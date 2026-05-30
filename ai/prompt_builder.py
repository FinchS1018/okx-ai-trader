"""
Prompt 构建器
将市场数据和技术指标格式化为 AI 可读的文本

这是整个系统的"翻译器"——把数字翻译成 AI 能理解的语言。
输入：MarketSnapshot + AccountState + 交易记录
输出：结构化的文本 prompt
"""

from typing import Dict, List, Any, Optional
from datetime import datetime

from core.data_fetcher import MarketSnapshot, IndicatorSnapshot, format_klines_for_ai
from core.okx_client import AccountState, PositionInfo


def build_market_prompt(snapshot: MarketSnapshot) -> str:
    """构建市场数据部分的 prompt"""
    symbol = snapshot.symbol
    price = snapshot.current_price

    lines = [
        f"## {symbol} 行情快照",
        f"当前价格: {price:.2f} USDT",
        "",
    ]

    for interval in ["1D", "4H", "1H", "15m"]:
        indicator = snapshot.get(interval)
        if indicator is None:
            continue

        lines.append(f"### {interval} K 线")
        lines.append(format_klines_for_ai(indicator.klines))
        lines.append("")

        # 技术指标
        lines.append(f"**RSI({14}):** {indicator.rsi:.1f}" if indicator.rsi else f"**RSI({14}):** 无数据")
        macd = indicator.macd
        lines.append(f"**MACD:** DIF={macd['dif']}, DEA={macd['dea']}, 柱={macd['histogram']}")
        bb = indicator.bollinger
        lines.append(f"**布林带:** 上轨={bb['upper']}, 中轨={bb['middle']}, 下轨={bb['lower']}")

        ema_parts = [f"EMA{period}={value:.2f}" for period, value in sorted(indicator.ema.items())]
        lines.append(f"**EMA:** {', '.join(ema_parts)}")

        if indicator.volume_change_ratio != 0:
            direction = "放量" if indicator.volume_change_ratio > 0 else "缩量"
            lines.append(f"**成交量变化:** {direction} {abs(indicator.volume_change_ratio)*100:.1f}%")

        lines.append("")

    return "\n".join(lines)


def build_account_prompt(
    state: AccountState,
    risk_status: Dict[str, Any],
    recent_trades: List[Dict[str, Any]],
    open_orders: List[Dict[str, Any]],
) -> str:
    """构建账户状态部分的 prompt"""
    lines = [
        f"总资产: {state.total_equity_usdt:.2f} USDT",
        f"可用: {state.available_usdt:.2f} USDT",
        f"今日已实现盈亏: {risk_status.get('daily_realized_pnl', 0):+.2f} USDT",
        f"今日剩余可亏额度: {risk_status.get('daily_remaining_loss', 0):.2f} USDT",
    ]

    if risk_status.get("meltdown_triggered"):
        lines.append("⚠️ 日亏损熔断已触发！今日禁止开仓！")

    # 持仓
    if state.positions:
        lines.append("\n## 当前持仓")
        for symbol, pos in state.positions.items():
            pnl_str = f"{pos.pnl_ratio*100:+.2f}%" if pos.pnl_ratio != 0 else "N/A"
            lines.append(
                f"- {symbol}: {pos.amount} (成本 {pos.avg_cost:.2f}, "
                f"现价 {pos.current_price:.2f}, 市值 {pos.usdt_value:.2f} USDT, "
                f"盈亏 {pnl_str})"
            )
    else:
        lines.append("\n## 当前持仓")
        lines.append("- 无持仓")

    # 最近交易
    if recent_trades:
        lines.append("\n## 近期交易")
        for trade in recent_trades[-5:]:
            direction = "买入" if trade.get("side") == "buy" else "卖出"
            lines.append(
                f"- {trade.get('time', '?')} {direction} {trade.get('amount', '?')} "
                f"{trade.get('symbol', '?')} @ {trade.get('price', '?')}"
            )
    else:
        lines.append("\n## 近期交易")
        lines.append("- 无交易记录")

    # 未成交挂单
    if open_orders:
        lines.append("\n## 当前挂单")
        for order in open_orders:
            lines.append(
                f"- {order.get('side', '?')} {order.get('sz', '?')} {order.get('instId', '?')} "
                f"@ {order.get('px', '市价')} (订单ID: {order.get('ordId', '?')[:8]}...)"
            )
    else:
        lines.append("\n## 当前挂单")
        lines.append("- 无挂单")

    return "\n".join(lines)


def build_full_prompt(
    btc_snapshot: MarketSnapshot,
    eth_snapshot: MarketSnapshot,
    state: AccountState,
    risk_status: Dict[str, Any],
    recent_trades: List[Dict[str, Any]],
    open_orders: List[Dict[str, Any]],
) -> str:
    """构建完整的 AI prompt"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    parts = [
        f"## 时间\n{now}",
        "",
        build_account_prompt(state, risk_status, recent_trades, open_orders),
        "",
        "---",
        "",
        build_market_prompt(btc_snapshot),
        "",
        "---",
        "",
        build_market_prompt(eth_snapshot),
    ]

    return "\n".join(parts)
