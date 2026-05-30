"""
OKX API 客户端封装
基于 python-okx SDK，统一封装所有交易接口
"""

import time
import logging
from typing import Optional, Dict, List, Any
from dataclasses import dataclass

import okx.MarketData as MarketData
import okx.Trade as Trade
import okx.Account as Account

from config.settings import (
    OKX_API_KEY,
    OKX_SECRET_KEY,
    OKX_PASSPHRASE,
    TRADING_MODE,
)

logger = logging.getLogger(__name__)


@dataclass
class KlineData:
    """单根 K 线数据"""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_okx(cls, row: List[str]) -> "KlineData":
        """从 OKX API 返回格式转换"""
        return cls(
            timestamp=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )


@dataclass
class OrderResult:
    """下单结果"""
    success: bool
    order_id: Optional[str] = None
    error_msg: Optional[str] = None


@dataclass
class AccountState:
    """账户状态快照"""
    total_equity_usdt: float = 0.0
    available_usdt: float = 0.0
    positions: Dict[str, "PositionInfo"] = None

    def __post_init__(self):
        if self.positions is None:
            self.positions = {}


@dataclass
class PositionInfo:
    """单个持仓信息"""
    symbol: str
    amount: float           # 持仓数量
    avg_cost: float         # 平均成本价
    current_price: float    # 当前价格
    pnl_ratio: float        # 盈亏比例
    usdt_value: float       # USDT 市值


class OkxClient:
    """OKX API 客户端 — 统一入口"""

    def __init__(self):
        self._is_demo = TRADING_MODE == "demo"
        # flag: "1" = 模拟盘, "0" = 实盘
        self._flag = "1" if self._is_demo else "0"

        logger.info(f"OKX 客户端模式: {'模拟盘' if self._is_demo else '实盘'} (flag={self._flag})")

        self._market_api = MarketData.MarketAPI(
            api_key=OKX_API_KEY,
            api_secret_key=OKX_SECRET_KEY,
            passphrase=OKX_PASSPHRASE,
            flag=self._flag,
            debug=False,
        )
        self._trade_api = Trade.TradeAPI(
            api_key=OKX_API_KEY,
            api_secret_key=OKX_SECRET_KEY,
            passphrase=OKX_PASSPHRASE,
            flag=self._flag,
            debug=False,
        )
        self._account_api = Account.AccountAPI(
            api_key=OKX_API_KEY,
            api_secret_key=OKX_SECRET_KEY,
            passphrase=OKX_PASSPHRASE,
            flag=self._flag,
            debug=False,
        )

        # 资金账户 API（缓存复用，避免每次查询都创建新实例）
        import okx.Funding as Funding
        self._funding_api = Funding.FundingAPI(
            api_key=OKX_API_KEY,
            api_secret_key=OKX_SECRET_KEY,
            passphrase=OKX_PASSPHRASE,
            flag=self._flag,
            debug=False,
        )

        self._last_request_time = 0
        self._min_interval = 0.1  # 最小请求间隔 100ms
        self._trading_usdt = 0.0   # 交易账户 USDT 余额，由 get_balance() 更新

    @property
    def mode(self) -> str:
        return TRADING_MODE

    # ---------- 频率控制 ----------

    def _rate_limit(self):
        """请求频率控制：确保两次请求间隔 ≥ 100ms"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    def _call_with_retry(self, func, *args, max_retries=3, **kwargs):
        """
        带重试的 API 调用
        指数退避：1s → 2s → 4s
        """
        self._rate_limit()
        last_error = None
        for attempt in range(max_retries):
            try:
                result = func(*args, **kwargs)
                if isinstance(result, dict) and result.get("code") != "0":
                    error_msg = result.get("msg", "Unknown OKX error")
                    logger.warning(f"OKX API error (attempt {attempt+1}): {error_msg}")
                    # 某些错误不需要重试（如余额不足）
                    if result.get("code") in ["51115", "51008"]:
                        return result
                    last_error = error_msg
                else:
                    return result
            except Exception as e:
                logger.warning(f"OKX API exception (attempt {attempt+1}): {e}")
                last_error = str(e)

            if attempt < max_retries - 1:
                wait = 2 ** attempt
                time.sleep(wait)

        logger.error(f"OKX API call failed after {max_retries} retries: {last_error}")
        return {"code": "-1", "msg": str(last_error)}

    # ================================================================
    # 行情数据
    # ================================================================

    def get_klines(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 20,
    ) -> List[KlineData]:
        """
        获取 K 线数据
        interval: 15m | 1H | 4H
        """
        # OKX API 格式转换
        bar_map = {"15m": "15m", "1H": "1H", "4H": "4H", "1D": "1D", "1Y": "1M"}
        bar = bar_map.get(interval, "15m")

        result = self._call_with_retry(
            self._market_api.get_candlesticks,
            instId=symbol,
            bar=bar,
            limit=str(limit),
        )

        if result.get("code") != "0":
            return []

        data = result.get("data", [])
        klines = [KlineData.from_okx(row) for row in reversed(data)]
        return klines

    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """获取最新 ticker"""
        result = self._call_with_retry(
            self._market_api.get_ticker,
            instId=symbol,
        )
        if result.get("code") != "0":
            return None
        data = result.get("data", [])
        return data[0] if data else None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """获取当前最新价格"""
        ticker = self.get_ticker(symbol)
        if ticker:
            return float(ticker.get("last", 0))
        return None

    # ================================================================
    # 账户
    # ================================================================

    def get_balance(self) -> Dict[str, float]:
        """
        获取账户余额（交易账户 + 资金账户）
        Returns: {"USDT": 500.0, "BTC": 0.01, ...}
        """
        trading_balances: Dict[str, float] = {}
        funding_balances: Dict[str, float] = {}

        # 1) 交易账户
        result = self._call_with_retry(
            self._account_api.get_account_balance,
        )
        if result.get("code") == "0":
            for item in result.get("data", []):
                for detail in item.get("details", []):
                    currency = detail.get("ccy", "")
                    eq = float(detail.get("eq", 0) or 0)
                    if eq > 0:
                        trading_balances[currency] = trading_balances.get(currency, 0) + eq

        # 2) 资金账户
        try:
            fresult = self._funding_api.get_balances()
            if fresult.get("code") == "0":
                for item in fresult.get("data", []):
                    currency = item.get("ccy", "")
                    bal = float(item.get("bal", 0) or 0)
                    if bal > 0:
                        funding_balances[currency] = funding_balances.get(currency, 0) + bal
        except Exception as e:
            logger.warning(f"资金账户查询失败: {e}")

        # 合并：交易 + 资金
        merged: Dict[str, float] = {}
        all_currencies = set(trading_balances.keys()) | set(funding_balances.keys())
        for ccy in all_currencies:
            merged[ccy] = trading_balances.get(ccy, 0) + funding_balances.get(ccy, 0)

        # 同时缓存交易账户可用余额，供 get_account_state 使用
        self._trading_usdt = trading_balances.get("USDT", 0.0)

        return merged

    def get_usdt_balance(self) -> float:
        """获取交易账户 USDT 可用余额（每次调用刷新）"""
        self.get_balance()  # 触发余额刷新，更新 self._trading_usdt
        return self._trading_usdt

    def get_positions(self) -> List[PositionInfo]:
        """获取当前所有现货持仓"""
        result = self._call_with_retry(
            self._account_api.get_account_balance,
        )
        positions = []
        if result.get("code") != "0":
            return positions

        for item in result.get("data", []):
            for detail in item.get("details", []):
                currency = detail.get("ccy", "")
                amount = float(detail.get("availBal", 0))
                frozen = float(detail.get("frozenBal", 0))
                total = amount + frozen
                if currency in ("USDT",):
                    continue
                if total <= 0:
                    continue

                symbol = f"{currency}-USDT"
                current_price = self.get_current_price(symbol) or 0
                # 成本价从持仓记录估算（简化：用最近成交价）
                # 更精确的做法需要查询成交历史
                positions.append(PositionInfo(
                    symbol=symbol,
                    amount=total,
                    avg_cost=0,  # 需要从成交记录获取
                    current_price=current_price,
                    pnl_ratio=0,
                    usdt_value=total * current_price,
                ))
        return positions

    def get_account_state(self) -> AccountState:
        """获取完整账户状态快照"""
        balances = self.get_balance()
        usdt_total = balances.get("USDT", 0.0)

        # 计算非 USDT 资产市值
        positions = {}
        total_non_usdt_value = 0.0
        for currency, amount in balances.items():
            if currency == "USDT":
                continue
            symbol = f"{currency}-USDT"
            price = self.get_current_price(symbol) or 0
            value = amount * price
            total_non_usdt_value += value
            positions[symbol] = PositionInfo(
                symbol=symbol,
                amount=amount,
                avg_cost=0,
                current_price=price,
                pnl_ratio=0,
                usdt_value=value,
            )

        state = AccountState(
            total_equity_usdt=usdt_total + total_non_usdt_value,
            available_usdt=self._trading_usdt,
            positions=positions,
        )
        return state

    # ================================================================
    # 交易
    # ================================================================

    def place_order(
        self,
        symbol: str,
        side: str,          # "buy" | "sell"
        amount: float,      # 币数量（不是 USDT 金额）
        order_type: str = "limit",  # "limit" | "market"
        price: Optional[float] = None,
    ) -> OrderResult:
        """
        下单
        - symbol: BTC-USDT
        - side: buy / sell
        - amount: 币的数量
        - order_type: limit / market
        - price: 限价单价格（市价单可为 None）
        """
        td_mode = "cash"  # 现货模式

        if order_type == "market":
            result = self._call_with_retry(
                self._trade_api.place_order,
                instId=symbol,
                tdMode=td_mode,
                side=side,
                ordType="market",
                sz=str(amount),
            )
        else:
            if price is None:
                return OrderResult(success=False, error_msg="限价单需要指定价格")
            result = self._call_with_retry(
                self._trade_api.place_order,
                instId=symbol,
                tdMode=td_mode,
                side=side,
                ordType="limit",
                sz=str(amount),
                px=str(price),
            )

        if result.get("code") == "0":
            order_id = result.get("data", [{}])[0].get("ordId", "")
            side_cn = "买入" if side == "buy" else "卖出"
            logger.info(f"{side_cn} {symbol} {amount} @ {price or '市价'} → 订单 {order_id}")
            return OrderResult(success=True, order_id=order_id)
        else:
            error_msg = result.get("msg", "Unknown error")
            logger.error(f"下单失败: {error_msg}")
            return OrderResult(success=False, error_msg=error_msg)

    def place_order_usdt(
        self,
        symbol: str,
        side: str,
        usdt_amount: float,
        order_type: str = "limit",
        price: Optional[float] = None,
    ) -> OrderResult:
        """
        用 USDT 金额下市价单（自动换算币数量）
        """
        if order_type == "market":
            return self.place_order(
                symbol=symbol,
                side=side,
                amount=usdt_amount,
                order_type="market",
            )
        else:
            if price is None:
                return OrderResult(success=False, error_msg="限价单需要指定价格")
            amount = usdt_amount / price
            # 截断到合理精度
            if "BTC" in symbol:
                amount = round(amount, 6)
            else:
                amount = round(amount, 4)
            return self.place_order(symbol, side, amount, "limit", price)

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """撤销订单"""
        result = self._call_with_retry(
            self._trade_api.cancel_order,
            instId=symbol,
            ordId=order_id,
        )
        success = result.get("code") == "0"
        if success:
            logger.info(f"撤单成功: {symbol} {order_id}")
        return success

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """获取未成交订单"""
        params = {}
        if symbol:
            params["instId"] = symbol
        result = self._call_with_retry(
            self._trade_api.get_order_list,
            **params,
        )
        if result.get("code") != "0":
            return []
        return result.get("data", [])

    def cancel_all_orders(self, symbol: Optional[str] = None):
        """撤销所有未成交订单"""
        orders = self.get_open_orders(symbol)
        for order in orders:
            self.cancel_order(order["instId"], order["ordId"])
        logger.info(f"已撤销 {len(orders)} 个挂单")

    def is_demo(self) -> bool:
        return self._is_demo
