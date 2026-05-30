"""
AI 引擎 — 核心编排器
负责：
  1. 调用时机控制（正常/浮亏/空闲/极端波动四种频率）
  2. 组装 prompt → 调用 Claude → 解析决策 → 风控检查 → 执行
  3. 每次决策的完整审计日志
"""

import json
import logging
import time
from typing import Dict, List, Any, Optional
from datetime import datetime

from core.okx_client import OkxClient
from core.data_fetcher import DataFetcher, MarketSnapshot
from core.risk_manager import RiskManager, RiskCheckResult
from core.order_executor import OrderExecutor, check_stop_loss_take_profit
from ai.claude_client import ClaudeClient
from ai.decision_parser import parse_decision, decision_to_summary
from ai.prompt_builder import build_full_prompt, build_market_prompt, build_account_prompt
from config.settings import (
    SYMBOLS,
    AI_CALL_INTERVAL_NORMAL,
    AI_CALL_INTERVAL_LOSS,
    AI_CALL_INTERVAL_IDLE,
    EXTREME_VOLATILITY_THRESHOLD,
    LOG_DIR,
)

logger = logging.getLogger(__name__)


class AIEngine:
    """AI 交易引擎"""

    def __init__(
        self,
        okx_client: OkxClient,
        claude_client: ClaudeClient,
        risk_manager: RiskManager,
    ):
        self._okx = okx_client
        self._claude = claude_client
        self._risk = risk_manager
        self._data_fetcher = DataFetcher(okx_client)
        self._executor = OrderExecutor(okx_client)

        # 状态追踪
        self._last_ai_call_time: Optional[float] = None
        self._last_prices: Dict[str, float] = {}  # symbol → last price（用于检测极端波动）
        self._recent_trades: List[Dict[str, Any]] = []  # 最近交易记录
        self._stop_loss_map: Dict[str, float] = {}  # symbol → stop_loss price
        self._take_profit_map: Dict[str, float] = {}  # symbol → take_profit price

        # 决策历史（给 Web 面板查看）
        self._decision_history: List[Dict[str, Any]] = []

    # ================================================================
    # 主循环 — 每轮 tick 调用
    # ================================================================

    def tick(self) -> Dict[str, Any]:
        """
        执行一轮完整的交易检查

        Returns:
            本轮执行摘要
        """
        summary = {
            "time": datetime.now().isoformat(),
            "ai_called": False,
            "decision": None,
            "executed": False,
            "stop_loss_triggered": [],
            "errors": [],
        }

        try:
            # 1. 获取当前账户状态
            state = self._okx.get_account_state()

            # 2. 检查止损止盈
            triggered = check_stop_loss_take_profit(
                self._okx,
                state.positions,
                self._stop_loss_map,
                self._take_profit_map,
            )
            for trigger in triggered:
                self._execute_stop_or_profit(trigger, state)
            summary["stop_loss_triggered"] = triggered

            # 3. 检测极端波动
            extreme_detected = self._check_extreme_volatility()

            # 4. 判断是否需要 AI 决策
            should_call = self._should_call_ai(state, extreme_detected)
            if not should_call:
                summary["reason"] = "跳过 AI 调用（冷却中或不满足调用条件）"
                return summary

            # 5. 获取市场数据
            market_snapshots = {}
            for symbol in SYMBOLS:
                snap = self._data_fetcher.fetch_full_snapshot(symbol)
                market_snapshots[symbol] = snap

            # 6. 构建 prompt 并调用 Claude
            btc_snap = market_snapshots.get("BTC-USDT")
            eth_snap = market_snapshots.get("ETH-USDT")
            if not btc_snap or not eth_snap:
                summary["errors"].append("无法获取市场数据")
                return summary

            risk_status = self._risk.get_status()
            open_orders = self._okx.get_open_orders()

            prompt = build_full_prompt(
                btc_snap, eth_snap, state,
                risk_status, self._recent_trades, open_orders,
            )

            raw_response = self._claude.get_decision(
                market_data_text=prompt,
                account_text="",  # 已包含在 prompt 中
            )

            if raw_response is None:
                summary["reason"] = "Claude API 不可用或调用失败"
                return summary

            summary["ai_called"] = True

            # 7. 解析 AI 决策
            decision = parse_decision(raw_response)
            summary["decision"] = decision

            # 记录到历史
            self._decision_history.append({
                "time": datetime.now().isoformat(),
                "decision": decision_to_summary(decision),
                "full": decision,
            })
            # 只保留最近 200 条
            if len(self._decision_history) > 200:
                self._decision_history = self._decision_history[-200:]

            logger.info(f"AI 决策: {decision_to_summary(decision)}")

            # 8. 风控检查
            current_positions = {}
            for sym, pos in state.positions.items():
                current_positions[sym] = {
                    "amount": pos.amount,
                    "avg_cost": pos.avg_cost,
                    "current_price": pos.current_price,
                    "usdt_value": pos.usdt_value,
                }

            risk_result = self._risk.check_decision(
                decision,
                state.total_equity_usdt,
                current_positions,
            )

            if not risk_result.approved:
                logger.warning(f"风控拒绝: {risk_result.reason}")
                summary["reason"] = f"风控拒绝: {risk_result.reason}"
                self._save_decision_log(prompt, decision, risk_result, None)
                return summary

            # 9. 执行
            risk_adjustments = {
                "adjusted_amount": risk_result.adjusted_amount,
                "adjusted_stop_loss": risk_result.adjusted_stop_loss,
                "adjusted_take_profit": risk_result.adjusted_take_profit,
            }

            result = self._executor.execute_decision(decision, risk_adjustments)
            summary["executed"] = result.get("executed", False)

            # 10. 更新止损止盈映射
            if decision.get("decision") == "buy" and result.get("executed"):
                symbol = decision.get("symbol")
                if symbol:
                    # 获取入场价
                    entry_price = decision.get("limit_price") or market_snapshots.get(symbol, MarketSnapshot(symbol)).current_price
                    # 设置止损
                    sl = risk_adjustments.get("adjusted_stop_loss") or decision.get("stop_loss")
                    if sl:
                        self._stop_loss_map[symbol] = sl
                    else:
                        self._stop_loss_map[symbol] = entry_price * 0.98  # -2%
                    # 设置止盈
                    tp = risk_adjustments.get("adjusted_take_profit") or decision.get("take_profit")
                    if tp:
                        self._take_profit_map[symbol] = tp

                    # 记录交易
                    self._recent_trades.append({
                        "time": datetime.now().strftime("%H:%M"),
                        "side": "buy",
                        "symbol": symbol,
                        "amount": decision.get("amount"),
                        "price": entry_price,
                    })
                    self._risk.record_trade(0)  # 先记录（盈亏待后续更新）

                    # 只保留最近 50 条
                    if len(self._recent_trades) > 50:
                        self._recent_trades = self._recent_trades[-50:]

            # 11. 记录日志
            self._save_decision_log(prompt, decision, risk_result, result)

            # 12. 更新上次调用时间
            self._last_ai_call_time = time.time()

            # 13. 更新价格缓存
            for symbol, snap in market_snapshots.items():
                self._last_prices[symbol] = snap.current_price

        except Exception as e:
            logger.error(f"Tick 异常: {e}", exc_info=True)
            summary["errors"].append(str(e))

        return summary

    # ================================================================
    # 调用时机控制
    # ================================================================

    def _should_call_ai(self, state, extreme_detected: bool) -> bool:
        """判断是否应该调用 AI"""
        if extreme_detected:
            return True

        if self._last_ai_call_time is None:
            return True

        elapsed = time.time() - self._last_ai_call_time

        # 有浮亏 → 更频繁
        has_unrealized_loss = False
        for symbol, pos in state.positions.items():
            if pos.pnl_ratio < -0.005:  # 浮亏超过 0.5%
                has_unrealized_loss = True
                break

        if has_unrealized_loss:
            return elapsed >= AI_CALL_INTERVAL_LOSS

        # 无持仓 → 放松
        if not state.positions:
            return elapsed >= AI_CALL_INTERVAL_IDLE

        # 正常
        return elapsed >= AI_CALL_INTERVAL_NORMAL

    def _check_extreme_volatility(self) -> bool:
        """检测极端波动"""
        if not self._last_prices:
            return False

        for symbol in SYMBOLS:
            current = self._okx.get_current_price(symbol)
            if not current or symbol not in self._last_prices:
                continue
            prev = self._last_prices[symbol]
            if prev > 0:
                change = abs(current - prev) / prev
                if change >= EXTREME_VOLATILITY_THRESHOLD:
                    logger.warning(f"{symbol} 极端波动: {change*100:.2f}%")
                    return True
        return False

    # ================================================================
    # 止损止盈执行
    # ================================================================

    def _execute_stop_or_profit(self, trigger: Dict, state):
        """执行止损或止盈"""
        symbol = trigger["symbol"]
        t_type = trigger["type"]
        pos = state.positions.get(symbol)
        if not pos:
            return

        # 市价卖出全部持仓
        result = self._okx.place_order(
            symbol=symbol,
            side="sell",
            amount=pos.amount,
            order_type="market",
        )

        # 清理映射
        self._stop_loss_map.pop(symbol, None)
        self._take_profit_map.pop(symbol, None)

        label = "止损" if t_type == "stop_loss" else "止盈"
        logger.warning(f"{label}触发: {symbol} @ {trigger['price']}")

        self._recent_trades.append({
            "time": datetime.now().strftime("%H:%M"),
            "side": "sell",
            "symbol": symbol,
            "amount": pos.amount,
            "price": trigger["price"],
            "type": t_type,
        })

        # 计算盈亏
        pnl = 0
        if pos.avg_cost and pos.avg_cost > 0:
            pnl = (trigger["price"] - pos.avg_cost) * pos.amount
        self._risk.record_trade(pnl)

    # ================================================================
    # 日志
    # ================================================================

    def _save_decision_log(
        self,
        prompt: str,
        decision: Dict[str, Any],
        risk_result: RiskCheckResult,
        exec_result: Optional[Dict],
    ):
        """保存完整的决策审计日志"""
        from config.settings import DECISION_LOG_DIR
        import os
        os.makedirs(DECISION_LOG_DIR, exist_ok=True)

        log_entry = {
            "time": datetime.now().isoformat(),
            "prompt": prompt[-500:],  # 截断 prompt
            "decision": decision,
            "risk_check": {
                "approved": risk_result.approved,
                "reason": risk_result.reason,
            },
            "execution": exec_result,
        }

        filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + f"_{decision.get('decision', 'unknown')}.json"
        filepath = os.path.join(DECISION_LOG_DIR, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(log_entry, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存决策日志失败: {e}")

    # ================================================================
    # 状态查询（给 Web 面板）
    # ================================================================

    def get_status(self) -> Dict[str, Any]:
        """返回引擎状态摘要"""
        ai_available = self._claude.is_available()
        next_call_in = 0
        if self._last_ai_call_time:
            elapsed = time.time() - self._last_ai_call_time
            next_call_in = max(0, AI_CALL_INTERVAL_NORMAL - int(elapsed))

        return {
            "ai_available": ai_available,
            "last_ai_call": self._last_ai_call_time,
            "next_ai_call_in_sec": next_call_in,
            "decision_history": self._decision_history[-20:],
            "stop_loss_map": self._stop_loss_map,
            "take_profit_map": self._take_profit_map,
            "recent_trades": self._recent_trades[-20:],
            "claude_stats": self._claude.get_stats(),
        }

    def emergency_stop(self):
        """紧急停止 — 撤销所有挂单"""
        self._okx.cancel_all_orders()
        logger.warning("紧急停止：已撤销所有挂单")
