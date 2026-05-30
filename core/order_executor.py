"""
订单执行器
接收风控审批后的决策，执行实际下单 + 止损止盈挂单
"""

import logging
from typing import Dict, Any, Optional

from core.okx_client import OkxClient, OrderResult

logger = logging.getLogger(__name__)


class OrderExecutor:
    """订单执行器 — AI 决策的最终执行者"""

    def __init__(self, client: OkxClient):
        self._client = client

    def execute_decision(
        self,
        decision: Dict[str, Any],
        risk_adjustments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        执行风控审批后的决策

        Args:
            decision: AI 原始决策
            risk_adjustments: 风控调整（adjusted_amount, adjusted_stop_loss, adjusted_take_profit）

        Returns:
            {"executed": bool, "order_id": str, "details": str}
        """
        d_type = decision.get("decision", "hold")
        symbol = decision.get("symbol", "BTC-USDT")

        # 应用风控调整
        adj_amount = risk_adjustments.get("adjusted_amount")
        amount = adj_amount if adj_amount is not None else decision.get("amount", 0)
        stop_loss = risk_adjustments.get("adjusted_stop_loss") or decision.get("stop_loss")
        take_profit = risk_adjustments.get("adjusted_take_profit") or decision.get("take_profit")

        if d_type == "hold":
            return {"executed": True, "order_id": None, "details": "HOLD — 不操作"}

        elif d_type == "buy":
            return self._execute_buy(symbol, amount, decision, stop_loss, take_profit)

        elif d_type == "sell":
            return self._execute_sell(symbol, amount, decision)

        elif d_type == "emergency_exit":
            return self._execute_emergency_exit()

        elif d_type == "adjust_grid":
            return self._execute_grid_adjust(decision)

        elif d_type == "pause_grid":
            return self._execute_pause_grid()

        return {"executed": False, "order_id": None, "details": f"未知决策类型: {d_type}"}

    def _execute_buy(
        self,
        symbol: str,
        amount: float,
        decision: Dict[str, Any],
        stop_loss: Optional[float],
        take_profit: Optional[float],
    ) -> Dict[str, Any]:
        """执行买入"""
        price_type = decision.get("price_type", "limit")
        limit_price = decision.get("limit_price")

        if price_type == "market":
            # 市价买：amount 是 USDT 金额
            result = self._client.place_order_usdt(
                symbol=symbol,
                side="buy",
                usdt_amount=amount,
                order_type="market",
            )
        else:
            # 限价买
            if not limit_price or limit_price <= 0:
                return {"executed": False, "order_id": None, "details": "限价单缺少有效价格"}
            result = self._client.place_order(
                symbol=symbol,
                side="buy",
                amount=amount,
                order_type="limit",
                price=limit_price,
            )

        if result.success:
            details = f"买入 {symbol} {amount} @ {limit_price or '市价'}"
            if stop_loss:
                details += f" (止损: {stop_loss})"
            if take_profit:
                details += f" (止盈: {take_profit})"

            # 注意：OKX 现货不支持自动止损止盈挂单（合约才支持）
            # 止损止盈由 trading_loop 监控价格来执行
            return {"executed": True, "order_id": result.order_id, "details": details}
        else:
            return {"executed": False, "order_id": None, "details": f"买入失败: {result.error_msg}"}

    def _execute_sell(
        self,
        symbol: str,
        amount: float,
        decision: Dict[str, Any],
    ) -> Dict[str, Any]:
        """执行卖出（amount 是 USDT 金额，自动换算为币数量）"""
        price_type = decision.get("price_type", "limit")
        limit_price = decision.get("limit_price")

        # 获取当前价格换算 USDT → 币数量
        conversion_price = limit_price if limit_price and limit_price > 0 else self._client.get_current_price(symbol)
        if not conversion_price or conversion_price <= 0:
            return {"executed": False, "order_id": None, "details": "无法获取当前价格，无法换算卖出数量"}
        coin_amount = amount / conversion_price

        if price_type == "market":
            result = self._client.place_order(
                symbol=symbol,
                side="sell",
                amount=coin_amount,
                order_type="market",
            )
        else:
            if not limit_price or limit_price <= 0:
                return {"executed": False, "order_id": None, "details": "限价单缺少有效价格"}
            result = self._client.place_order(
                symbol=symbol,
                side="sell",
                amount=coin_amount,
                order_type="limit",
                price=limit_price,
            )

        if result.success:
            return {"executed": True, "order_id": result.order_id, "details": f"卖出 {symbol} {amount} @ {limit_price or '市价'}"}
        else:
            return {"executed": False, "order_id": None, "details": f"卖出失败: {result.error_msg}"}

    def _execute_emergency_exit(self) -> Dict[str, Any]:
        """紧急清仓：市价卖出所有持仓"""
        positions = self._client.get_positions()
        results = []
        for pos in positions:
            result = self._client.place_order(
                symbol=pos.symbol,
                side="sell",
                amount=pos.amount,
                order_type="market",
            )
            results.append(f"{pos.symbol}: {'成功' if result.success else '失败'}")

        return {"executed": True, "order_id": None, "details": "紧急清仓: " + ", ".join(results)}

    def _execute_grid_adjust(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """调整网格参数"""
        low = decision.get("new_grid_low")
        high = decision.get("new_grid_high")
        levels = decision.get("new_grid_levels")
        return {
            "executed": True,
            "order_id": None,
            "details": f"网格调整: {low}-{high}, {levels}层 (由 AI 引擎处理)"
        }

    def _execute_pause_grid(self) -> Dict[str, Any]:
        """暂停网格"""
        return {"executed": True, "order_id": None, "details": "网格已暂停"}


def check_stop_loss_take_profit(
    client: OkxClient,
    positions: Dict[str, Any],
    stop_loss_map: Dict[str, float],
    take_profit_map: Dict[str, float],
) -> list[Dict[str, Any]]:
    """
    检查持仓的止损止盈触发条件
    由 trading_loop 每轮调用

    Returns:
        触发的操作列表
    """
    triggered = []
    for symbol, pos in positions.items():
        current_price = client.get_current_price(symbol)
        if not current_price:
            continue

        # 止损检查
        if symbol in stop_loss_map and current_price <= stop_loss_map[symbol]:
            triggered.append({
                "type": "stop_loss",
                "symbol": symbol,
                "price": current_price,
                "trigger_price": stop_loss_map[symbol],
            })

        # 止盈检查
        if symbol in take_profit_map and current_price >= take_profit_map[symbol]:
            triggered.append({
                "type": "take_profit",
                "symbol": symbol,
                "price": current_price,
                "trigger_price": take_profit_map[symbol],
            })

    return triggered
