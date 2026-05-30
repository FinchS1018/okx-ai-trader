"""
风控引擎 — 硬编码安全护栏
AI 的所有决策必须经过此引擎检查，不可绕过

设计原则：
  - 风控逻辑是硬编码的，不依赖任何 AI
  - check_decision() 返回 approved=True/False
  - 被拒绝的决策记录日志并通知
"""

import time
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

from config.settings import (
    INITIAL_CAPITAL,
    MAX_POSITION_RATIO,
    MAX_SINGLE_ORDER_RATIO,
    MIN_ORDER_USDT,
    STOP_LOSS_RATIO,
    TAKE_PROFIT_RATIO,
    DAILY_LOSS_LIMIT_RATIO,
    COOLDOWN_SECONDS,
    MAX_LEVERAGE,
    SYMBOLS,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    """风控检查结果"""
    approved: bool
    reason: str = ""                     # 拒绝原因（通过为空）
    adjusted_amount: Optional[float] = None  # 调整后的金额（如果原金额超限）
    adjusted_stop_loss: Optional[float] = None
    adjusted_take_profit: Optional[float] = None


class RiskManager:
    """
    风控引擎

    每次 AI 决策后，调用 check_decision() 进行风控审批。
    风控可以：
      1. 完全拒绝（approved=False）
      2. 调整金额（超过限额时自动裁剪）
      3. 收窄止损（AI 设太宽时收紧）
    """

    def __init__(self):
        # 每日盈亏追踪
        self._today_date = datetime.now().date()
        self._daily_realized_pnl = 0.0      # 今日已实现盈亏
        self._daily_loss_limit = INITIAL_CAPITAL * DAILY_LOSS_LIMIT_RATIO
        self._daily_remaining_loss = self._daily_loss_limit  # 剩余可亏额度

        # 冷却控制
        self._last_trade_time: Optional[float] = None

        # 当前持仓成本（外部更新）
        self._position_cost: Dict[str, float] = {}  # symbol → avg_cost

    # ================================================================
    # 主入口：AI 决策风控检查
    # ================================================================

    def check_decision(
        self,
        decision: Dict[str, Any],
        total_equity: float,
        current_positions: Dict[str, Dict],
    ) -> RiskCheckResult:
        """
        对 AI 决策进行完整风控检查

        Args:
            decision: AI 输出的决策 dict
                {decision, symbol, amount, price_type, limit_price, confidence, reasoning, stop_loss, take_profit}
            total_equity: 当前总资产
            current_positions: 当前持仓 {symbol: {amount, avg_cost, current_price}}

        Returns:
            RiskCheckResult(approved, reason, adjustments...)
        """
        decision_type = decision.get("decision", "hold")

        # hold 不需要风控
        if decision_type in ("hold", "adjust_grid", "pause_grid"):
            return RiskCheckResult(approved=True)

        symbol = decision.get("symbol", "")

        # 1. 日亏损熔断检查
        result = self._check_daily_loss_limit()
        if not result.approved:
            return result

        # 2. 交易冷却检查
        result = self._check_cooldown()
        if not result.approved:
            return result

        # 3. 仅现货检查
        result = self._check_spot_only()
        if not result.approved:
            return result

        # 4. 交易对白名单
        result = self._check_symbol_whitelist(symbol)
        if not result.approved:
            return result

        # 5. 金额检查（buy 和 sell 逻辑不同）
        if decision_type == "buy":
            result = self._check_buy(
                decision, total_equity, current_positions
            )
        elif decision_type == "sell":
            result = self._check_sell(
                decision, total_equity, current_positions
            )
        elif decision_type == "emergency_exit":
            # 紧急清仓直接通过
            return RiskCheckResult(approved=True)

        if not result.approved:
            return result

        # 6. 止损/止盈合理性
        result = self._validate_stop_profit(decision)

        return result

    # ================================================================
    # 各项检查
    # ================================================================

    def _check_daily_loss_limit(self) -> RiskCheckResult:
        """检查日亏损熔断"""
        self._reset_daily_if_new_day()
        if self._daily_remaining_loss <= 0:
            return RiskCheckResult(
                approved=False,
                reason=f"日亏损熔断已触发（今日已亏 {self._daily_realized_pnl:.2f} USDT，上限 {self._daily_loss_limit:.2f}）"
            )
        return RiskCheckResult(approved=True)

    def _check_cooldown(self) -> RiskCheckResult:
        """检查交易冷却"""
        if self._last_trade_time is None:
            return RiskCheckResult(approved=True)
        elapsed = time.time() - self._last_trade_time
        if elapsed < COOLDOWN_SECONDS:
            remaining = int(COOLDOWN_SECONDS - elapsed)
            return RiskCheckResult(
                approved=False,
                reason=f"交易冷却中，还需等待 {remaining} 秒"
            )
        return RiskCheckResult(approved=True)

    def _check_spot_only(self) -> RiskCheckResult:
        """确保不使用杠杆"""
        if MAX_LEVERAGE > 1:
            return RiskCheckResult(
                approved=False,
                reason=f"不允许使用杠杆（当前设置 MAX_LEVERAGE={MAX_LEVERAGE}，要求为 1）"
            )
        return RiskCheckResult(approved=True)

    def _check_symbol_whitelist(self, symbol: str) -> RiskCheckResult:
        """交易对白名单"""
        if symbol not in SYMBOLS:
            return RiskCheckResult(
                approved=False,
                reason=f"{symbol} 不在允许列表（仅允许: {SYMBOLS}）"
            )
        return RiskCheckResult(approved=True)

    def _check_buy(
        self,
        decision: Dict[str, Any],
        total_equity: float,
        current_positions: Dict[str, Dict],
    ) -> RiskCheckResult:
        """买入检查"""
        symbol = decision.get("symbol")
        amount = decision.get("amount", 0)
        limit_price = decision.get("limit_price")
        price_type = decision.get("price_type", "limit")

        # 计算本次买入的 USDT 金额
        if price_type == "market":
            buy_usdt = amount  # 市价单 amount 直接是 USDT 金额
        else:
            if limit_price and limit_price > 0:
                buy_usdt = amount * limit_price
            else:
                return RiskCheckResult(
                    approved=False,
                    reason="限价单缺少有效价格"
                )

        # 检查单笔最小金额
        if buy_usdt < MIN_ORDER_USDT:
            return RiskCheckResult(
                approved=False,
                reason=f"单笔金额 {buy_usdt:.2f} USDT 低于最低限额 {MIN_ORDER_USDT} USDT"
            )

        # 检查单笔最大金额
        max_single = total_equity * MAX_SINGLE_ORDER_RATIO
        adjusted_amount = amount
        if buy_usdt > max_single:
            adjusted_amount = amount * (max_single / buy_usdt)
            logger.warning(f"买入金额 {buy_usdt:.2f} 超过单笔上限 {max_single:.2f}，自动缩减")

        return RiskCheckResult(
            approved=True,
            adjusted_amount=adjusted_amount if adjusted_amount != amount else None,
        )

    def _check_sell(
        self,
        decision: Dict[str, Any],
        total_equity: float,
        current_positions: Dict[str, Dict],
    ) -> RiskCheckResult:
        """卖出检查（amount 是 USDT 金额）"""
        symbol = decision.get("symbol")
        amount = decision.get("amount", 0)

        # 检查是否有持仓可卖
        if symbol not in current_positions:
            return RiskCheckResult(
                approved=False,
                reason=f"没有 {symbol} 持仓，无法卖出"
            )

        pos = current_positions[symbol]
        pos_amount = pos.get("amount", 0)
        pos_usdt = pos.get("usdt_value", 0)

        if amount <= 0:
            return RiskCheckResult(approved=False, reason=f"卖出金额 <= 0")

        # USDT 金额不能超过持仓市值，超了则全仓卖出
        if amount >= pos_usdt:
            logger.warning(f"卖出 USDT {amount:.2f} 超过持仓市值 {pos_usdt:.2f}，将全部卖出")
            adjusted_usdt = pos_usdt
        else:
            adjusted_usdt = amount

        return RiskCheckResult(
            approved=True,
            adjusted_amount=adjusted_usdt,
        )

    def _validate_stop_profit(self, decision: Dict[str, Any]) -> RiskCheckResult:
        """验证止损止盈"""
        decision_type = decision.get("decision")
        if decision_type != "buy":
            return RiskCheckResult(approved=True)

        stop_loss = decision.get("stop_loss")
        take_profit = decision.get("take_profit")
        limit_price = decision.get("limit_price", 0)
        # 如果没有入场价就用限价
        entry_price = limit_price or 0

        if entry_price <= 0:
            return RiskCheckResult(approved=True)

        # 止损不能太松（最多亏 2%）
        max_stop = entry_price * (1 - STOP_LOSS_RATIO)
        adjusted_stop = None
        if stop_loss and stop_loss < max_stop:
            # AI 设的止损太宽，收紧
            adjusted_stop = max_stop
            logger.info(f"止损从 {stop_loss} 收紧到 {adjusted_stop}（-2%）")

        # 止盈不能太近（至少 1%）
        min_tp = entry_price * 1.01
        adjusted_tp = None
        if take_profit and take_profit < min_tp:
            adjusted_tp = entry_price * (1 + TAKE_PROFIT_RATIO)
            logger.info(f"止盈从 {take_profit} 调整到 {adjusted_tp}（+4%）")

        return RiskCheckResult(
            approved=True,
            adjusted_stop_loss=adjusted_stop,
            adjusted_take_profit=adjusted_tp,
        )

    # ================================================================
    # 状态管理
    # ================================================================

    def record_trade(self, realized_pnl: float):
        """记录一笔已成交的交易"""
        self._reset_daily_if_new_day()
        self._daily_realized_pnl += realized_pnl
        self._daily_remaining_loss = self._daily_loss_limit - abs(min(0, self._daily_realized_pnl))
        self._last_trade_time = time.time()
        logger.info(
            f"交易记录: PnL={realized_pnl:+.2f} USDT, "
            f"今日累计={self._daily_realized_pnl:+.2f}, "
            f"剩余可亏={self._daily_remaining_loss:.2f}"
        )

    def update_position_cost(self, symbol: str, avg_cost: float):
        """更新持仓成本（用于止损计算）"""
        self._position_cost[symbol] = avg_cost

    def get_stop_loss_price(self, symbol: str, current_price: float, custom_stop: Optional[float] = None) -> float:
        """
        计算止损价格
        取 (AI 自定义止损, 硬编码 -2%, current_price -2%) 中最紧的
        """
        hard_stop = current_price * (1 - STOP_LOSS_RATIO)
        candidates = [hard_stop]
        if custom_stop:
            candidates.append(custom_stop)
        # 返回最高的止损价（最紧的止损）
        return max(candidates)

    def _reset_daily_if_new_day(self):
        """跨日重置日亏损计数器"""
        today = datetime.now().date()
        if today != self._today_date:
            logger.info(f"新的一天，重置日亏损计数器（昨日 PnL: {self._daily_realized_pnl:+.2f}）")
            self._today_date = today
            self._daily_realized_pnl = 0.0
            self._daily_remaining_loss = self._daily_loss_limit

    # ================================================================
    # 状态查询（给 Web 面板用）
    # ================================================================

    def get_status(self) -> Dict[str, Any]:
        """返回风控状态摘要"""
        self._reset_daily_if_new_day()
        in_cooldown = False
        cooldown_remaining = 0
        if self._last_trade_time:
            elapsed = time.time() - self._last_trade_time
            if elapsed < COOLDOWN_SECONDS:
                in_cooldown = True
                cooldown_remaining = int(COOLDOWN_SECONDS - elapsed)

        return {
            "daily_realized_pnl": round(self._daily_realized_pnl, 2),
            "daily_loss_limit": round(self._daily_loss_limit, 2),
            "daily_remaining_loss": round(self._daily_remaining_loss, 2),
            "meltdown_triggered": self._daily_remaining_loss <= 0,
            "in_cooldown": in_cooldown,
            "cooldown_remaining_sec": cooldown_remaining,
            "last_trade_time": self._last_trade_time,
        }
