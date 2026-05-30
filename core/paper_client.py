"""
Paper Trading 模拟客户端
继承 OkxClient — 行情走真实 OKX API，交易/账户在本地模拟
"""

import json
import os
import threading
import time
import logging
from typing import Optional, Dict, List

from core.okx_client import (
    OkxClient,
    PositionInfo,
    OrderResult,
    AccountState,
)

logger = logging.getLogger(__name__)

from config.settings import PAPER_TRADING_FEE, LOG_DIR

# 各币种数量精度（对标 OKX 现货最小下单量）
_COIN_PRECISION = {"BTC-USDT": 6, "ETH-USDT": 4}

# 状态持久化文件
_STATE_FILE = os.path.join(LOG_DIR, "paper_state.json")


class PaperOkxClient(OkxClient):
    """模拟交易客户端 — 行情真实，账户/交易本地模拟"""

    def __init__(self, initial_capital: float = 200.0):
        # 调用父类初始化（建立真实行情 API 连接）
        super().__init__()

        self._lock = threading.Lock()

        # 虚拟账户
        self._paper_balances: Dict[str, float] = {"USDT": initial_capital}
        self._paper_positions: Dict[str, dict] = {}  # symbol → {amount, avg_cost}
        self._paper_pending: List[dict] = []           # 挂单
        self._paper_history: List[dict] = []            # 成交记录
        self._order_seq = 0

        self._initial_capital = initial_capital

        # 尝试从文件恢复之前的状态
        if self._load_state():
            logger.info(f"📂 已恢复模拟交易状态 (资金: {self._paper_balances.get('USDT', 0):.2f} USDT)")
        else:
            logger.info(f"📝 模拟交易新账户 (初始资金: {initial_capital} USDT)")

    @property
    def mode(self) -> str:
        return "paper"

    def is_demo(self) -> bool:
        return False

    def get_trade_history(self) -> List[Dict]:
        """返回模拟成交记录（含时间戳）"""
        with self._lock:
            return list(self._paper_history)

    # ================================================================
    # 账户（本地模拟）
    # ================================================================

    def get_balance(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._paper_balances)

    def get_usdt_balance(self) -> float:
        with self._lock:
            return self._paper_balances.get("USDT", 0.0)

    def get_positions(self) -> List[PositionInfo]:
        with self._lock:
            positions = []
            for symbol, pos in self._paper_positions.items():
                price = self.get_current_price(symbol) or 0
                amount = pos["amount"]
                avg_cost = pos["avg_cost"]
                value = amount * price
                pnl = (price - avg_cost) / avg_cost if avg_cost > 0 else 0
                positions.append(PositionInfo(
                    symbol=symbol,
                    amount=amount,
                    avg_cost=avg_cost,
                    current_price=price,
                    pnl_ratio=pnl,
                    usdt_value=value,
                ))
            return positions

    def get_account_state(self) -> AccountState:
        with self._lock:
            usdt = self._paper_balances.get("USDT", 0.0)

        positions = {}
        total_coin_value = 0.0

        # 锁外获取价格（避免长时间持锁）
        pos_snapshot = {}
        with self._lock:
            pos_snapshot = dict(self._paper_positions)

        for symbol, pos in pos_snapshot.items():
            price = self.get_current_price(symbol) or 0
            amount = pos["amount"]
            value = amount * price
            total_coin_value += value
            avg_cost = pos["avg_cost"]
            pnl = (price - avg_cost) / avg_cost if avg_cost > 0 else 0
            positions[symbol] = PositionInfo(
                symbol=symbol,
                amount=amount,
                avg_cost=avg_cost,
                current_price=price,
                pnl_ratio=pnl,
                usdt_value=value,
            )

        return AccountState(
            total_equity_usdt=usdt + total_coin_value,
            available_usdt=usdt,
            positions=positions,
        )

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        # 先检查挂单是否可成交
        self._check_pending()

        with self._lock:
            pending = list(self._paper_pending)

        if symbol:
            pending = [o for o in pending if o.get("instId") == symbol]
        return pending

    # ================================================================
    # 交易（本地模拟）
    # ================================================================

    def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "limit",
        price: Optional[float] = None,
    ) -> OrderResult:
        """模拟下单"""
        current_price = self.get_current_price(symbol) or 0
        if current_price <= 0:
            return OrderResult(success=False, error_msg="无法获取当前价格")

        # 决定成交价
        if order_type == "market":
            fill_price = current_price
            # 市价买入：amount 是 USDT，需换算为币数量
            if side == "buy":
                amount = amount / fill_price
                precision = _COIN_PRECISION.get(symbol, 4)
                amount = round(amount, precision)
        elif price is not None and price > 0:
            if side == "buy" and price >= current_price:
                fill_price = current_price  # 限价买单，用市价成交
            elif side == "sell" and price <= current_price:
                fill_price = current_price  # 限价卖单，用市价成交
            else:
                # 挂单
                return self._add_pending_order(symbol, side, amount, order_type, price)
        else:
            return OrderResult(success=False, error_msg="限价单需要有效价格")

        # 执行成交
        return self._fill_order(symbol, side, amount, fill_price)

    def place_order_usdt(
        self,
        symbol: str,
        side: str,
        usdt_amount: float,
        order_type: str = "limit",
        price: Optional[float] = None,
    ) -> OrderResult:
        """用 USDT 金额下市价单"""
        if order_type == "market":
            return self.place_order(symbol, side, usdt_amount, "market")
        else:
            if not price or price <= 0:
                return OrderResult(success=False, error_msg="限价单需要有效价格")
            amount = usdt_amount / price
            precision = _COIN_PRECISION.get(symbol, 4)
            amount = round(amount, precision)
            return self.place_order(symbol, side, amount, "limit", price)

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        with self._lock:
            before = len(self._paper_pending)
            self._paper_pending = [
                o for o in self._paper_pending
                if not (o.get("ordId") == order_id and o.get("instId") == symbol)
            ]
            if len(self._paper_pending) < before:
                logger.info(f"[PAPER] 撤单: {symbol} {order_id}")
                self._save_state()
                return True
        return False

    def cancel_all_orders(self, symbol: Optional[str] = None):
        with self._lock:
            if symbol:
                self._paper_pending = [
                    o for o in self._paper_pending if o.get("instId") != symbol
                ]
            else:
                self._paper_pending.clear()
        self._save_state()

    # ================================================================
    # 状态持久化
    # ================================================================

    def _save_state(self):
        """将当前状态保存到 JSON 文件（服务重启后可恢复）"""
        try:
            os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
            with self._lock:
                state = {
                    "balances": dict(self._paper_balances),
                    "positions": {k: dict(v) for k, v in self._paper_positions.items()},
                    "pending": list(self._paper_pending),
                    "history": list(self._paper_history),
                    "order_seq": self._order_seq,
                }
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"状态保存失败: {e}")

    def _load_state(self) -> bool:
        """从文件恢复之前的状态，成功返回 True"""
        if not os.path.exists(_STATE_FILE):
            return False
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            with self._lock:
                self._paper_balances = state.get("balances", {"USDT": self._initial_capital})
                self._paper_positions = state.get("positions", {})
                self._paper_pending = state.get("pending", [])
                self._paper_history = state.get("history", [])
                self._order_seq = state.get("order_seq", 0)
            return True
        except Exception as e:
            logger.warning(f"状态恢复失败（将使用初始资金）: {e}")
            return False

    def reset_state(self):
        """重置模拟账户到初始状态"""
        with self._lock:
            self._paper_balances = {"USDT": self._initial_capital}
            self._paper_positions = {}
            self._paper_pending = []
            self._paper_history = []
            self._order_seq = 0
        # 删除状态文件
        try:
            if os.path.exists(_STATE_FILE):
                os.remove(_STATE_FILE)
        except Exception as e:
            logger.warning(f"删除状态文件失败: {e}")
        logger.info(f"🔄 模拟账户已重置 (资金: {self._initial_capital} USDT)")

    # ================================================================
    # 内部方法
    # ================================================================

    def _gen_order_id(self) -> str:
        self._order_seq += 1
        return f"paper-{self._order_seq:06d}"

    def _fill_order(
        self, symbol: str, side: str, coin_amount: float, fill_price: float
    ) -> OrderResult:
        """执行成交"""
        precision = _COIN_PRECISION.get(symbol, 4)
        coin_amount = round(coin_amount, precision)

        if coin_amount <= 0:
            return OrderResult(success=False, error_msg=f"数量过小: {coin_amount}")

        usdt_value = coin_amount * fill_price
        fee = usdt_value * PAPER_TRADING_FEE

        with self._lock:
            if side == "buy":
                total_cost = usdt_value + fee
                usdt_balance = self._paper_balances.get("USDT", 0)
                if total_cost > usdt_balance:
                    return OrderResult(
                        success=False,
                        error_msg=f"余额不足: 需 {total_cost:.4f} USDT, 可用 {usdt_balance:.4f} USDT",
                    )

                # 扣除 USDT
                self._paper_balances["USDT"] = usdt_balance - total_cost

                # 增加币（扣除手续费对应的币量）
                received = coin_amount - (fee / fill_price)
                received = round(received, precision)

                # 更新持仓
                if symbol in self._paper_positions:
                    pos = self._paper_positions[symbol]
                    old_amount = pos["amount"]
                    old_cost = pos["avg_cost"]
                    new_amount = old_amount + received
                    new_cost = ((old_cost * old_amount) + (fill_price * received)) / new_amount
                    pos["amount"] = round(new_amount, precision)
                    pos["avg_cost"] = round(new_cost, 8)
                else:
                    self._paper_positions[symbol] = {
                        "amount": received,
                        "avg_cost": fill_price,
                    }

                # 更新余额里的币（如果之前持有）
                coin_ccy = symbol.split("-")[0]
                self._paper_balances[coin_ccy] = self._paper_balances.get(coin_ccy, 0) + received

                logger.info(
                    f"[PAPER] 买入 {symbol} {received} 枚 @ {fill_price:.4f} "
                    f"(手续费 {fee:.6f} USDT)"
                )

            else:  # sell
                coin_ccy = symbol.split("-")[0]
                pos = self._paper_positions.get(symbol)
                if not pos or pos["amount"] < coin_amount:
                    held = pos["amount"] if pos else 0
                    return OrderResult(
                        success=False,
                        error_msg=f"持仓不足: 要卖 {coin_amount}, 持有 {held}",
                    )

                revenue = usdt_value - fee

                # 增加 USDT
                self._paper_balances["USDT"] = self._paper_balances.get("USDT", 0) + revenue

                # 减少币
                new_amount = pos["amount"] - coin_amount
                if new_amount < 1e-10:
                    del self._paper_positions[symbol]
                else:
                    pos["amount"] = round(new_amount, precision)

                # 余额
                self._paper_balances[coin_ccy] = self._paper_balances.get(coin_ccy, 0) - coin_amount
                if self._paper_balances.get(coin_ccy, 0) < 1e-10:
                    self._paper_balances.pop(coin_ccy, None)

                logger.info(
                    f"[PAPER] 卖出 {symbol} {coin_amount} 枚 @ {fill_price:.4f} "
                    f"(手续费 {fee:.6f} USDT)"
                )

        # 记录成交
        order_id = self._gen_order_id()
        trade_record = {
            "ordId": order_id,
            "instId": symbol,
            "side": side,
            "amount": coin_amount,
            "price": fill_price,
            "fee": fee,
            "timestamp": int(time.time() * 1000),
            "status": "filled",
        }
        with self._lock:
            self._paper_history.append(trade_record)

        self._save_state()
        return OrderResult(success=True, order_id=order_id)

    def _add_pending_order(
        self, symbol: str, side: str, amount: float, order_type: str, limit_price: float
    ) -> OrderResult:
        """挂限价单"""
        order_id = self._gen_order_id()
        pending = {
            "ordId": order_id,
            "instId": symbol,
            "side": side,
            "sz": str(amount),
            "px": str(limit_price),
            "ordType": order_type,
            "state": "live",
        }
        with self._lock:
            self._paper_pending.append(pending)

        self._save_state()
        logger.info(
            f"[PAPER] 挂单: {side} {symbol} {amount} @ {limit_price} → {order_id}"
        )
        return OrderResult(success=True, order_id=order_id)

    def _check_pending(self):
        """检查挂单是否可成交"""
        to_fill = []
        with self._lock:
            for order in list(self._paper_pending):
                symbol = order["instId"]
                side = order["side"]
                limit_price = float(order["px"])
                current_price = self.get_current_price(symbol) or 0

                if current_price <= 0:
                    continue

                if side == "buy" and limit_price >= current_price:
                    to_fill.append(order)
                elif side == "sell" and limit_price <= current_price:
                    to_fill.append(order)

        for order in to_fill:
            symbol = order["instId"]
            side = order["side"]
            amount = float(order["sz"])
            # 把这个挂单从 pending 移除
            with self._lock:
                self._paper_pending = [
                    o for o in self._paper_pending if o["ordId"] != order["ordId"]
                ]
            # 执行成交
            current_price = self.get_current_price(symbol) or 0
            self._fill_order(symbol, side, amount, current_price)
