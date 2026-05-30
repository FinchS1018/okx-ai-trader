"""
数据获取 + 技术指标计算
从 OKX 获取 K 线数据，计算所有技术指标，统一输出给 AI
"""

import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import pandas as pd
import numpy as np

from core.okx_client import OkxClient, KlineData
from config.settings import (
    SYMBOLS,
    KLINE_INTERVALS,
    RSI_PERIOD,
    MACD_FAST,
    MACD_SLOW,
    MACD_SIGNAL,
    BOLLINGER_PERIOD,
    BOLLINGER_STD,
    EMA_PERIODS,
    KLINE_BARS_15M,
    KLINE_BARS_1H,
    KLINE_BARS_4H,
    KLINE_BARS_1D,
    KLINE_BARS_1Y,
)

logger = logging.getLogger(__name__)


@dataclass
class IndicatorSnapshot:
    """单个币种、单个时间级别的指标快照"""
    symbol: str
    interval: str
    klines: List[Dict[str, Any]] = field(default_factory=list)
    current_price: float = 0.0
    rsi: Optional[float] = None
    macd: Dict[str, float] = field(default_factory=lambda: {"dif": 0, "dea": 0, "histogram": 0})
    bollinger: Dict[str, float] = field(default_factory=lambda: {"upper": 0, "middle": 0, "lower": 0})
    ema: Dict[int, float] = field(default_factory=dict)
    volume_change_ratio: float = 0.0    # 较前一根 K 线的成交量变化
    price_change_15m: float = 0.0       # 15 分钟涨跌幅


@dataclass
class MarketSnapshot:
    """完整市场快照（包含一个币种所有时间级别）"""
    symbol: str
    intervals: Dict[str, IndicatorSnapshot] = field(default_factory=dict)
    current_price: float = 0.0

    def get(self, interval: str) -> Optional[IndicatorSnapshot]:
        return self.intervals.get(interval)


class DataFetcher:
    """数据获取器 — K 线 + 技术指标一站式"""

    def __init__(self, client: OkxClient):
        self._client = client

    def fetch_full_snapshot(self, symbol: str) -> MarketSnapshot:
        """获取一个币种的完整市场快照"""
        snapshot = MarketSnapshot(symbol=symbol)

        bars_map = {
            "15m": KLINE_BARS_15M,
            "1H": KLINE_BARS_1H,
            "4H": KLINE_BARS_4H,
            "1D": KLINE_BARS_1D,
            "1Y": KLINE_BARS_1Y,
        }

        current_price = self._client.get_current_price(symbol) or 0

        for interval in KLINE_INTERVALS:
            limit = bars_map.get(interval, 20)
            klines = self._client.get_klines(symbol, interval, limit)
            if not klines:
                logger.warning(f"无法获取 {symbol} {interval} K 线")
                continue

            indicator = self._calculate_indicators(symbol, interval, klines, current_price)
            snapshot.intervals[interval] = indicator

        snapshot.current_price = current_price
        return snapshot

    def _calculate_indicators(
        self,
        symbol: str,
        interval: str,
        klines: List[KlineData],
        current_price: float,
    ) -> IndicatorSnapshot:
        """计算所有技术指标"""
        snap = IndicatorSnapshot(
            symbol=symbol,
            interval=interval,
            current_price=current_price,
        )

        if len(klines) < BOLLINGER_PERIOD:
            return snap

        # 转 pandas DataFrame
        closes = np.array([k.close for k in klines], dtype=np.float64)
        highs = np.array([k.high for k in klines], dtype=np.float64)
        lows = np.array([k.low for k in klines], dtype=np.float64)
        volumes = np.array([k.volume for k in klines], dtype=np.float64)

        # RSI
        snap.rsi = self._calc_rsi(closes, RSI_PERIOD)

        # MACD
        snap.macd = self._calc_macd(closes)

        # 布林带
        snap.bollinger = self._calc_bollinger(closes)

        # EMA
        for period in EMA_PERIODS:
            snap.ema[period] = self._calc_ema(closes, period)

        # 成交量变化
        if len(volumes) >= 2:
            prev_vol = volumes[-2]
            curr_vol = volumes[-1]
            if prev_vol > 0:
                snap.volume_change_ratio = (curr_vol - prev_vol) / prev_vol

        # K 线列表（用于 AI prompt）
        snap.klines = [
            {
                "ts": k.timestamp,
                "o": k.open,
                "h": k.high,
                "l": k.low,
                "c": k.close,
                "v": k.volume,
            }
            for k in klines[-10:]  # 只取最近 10 根给 AI
        ]

        # 15 分钟涨跌幅
        if len(closes) >= 2:
            snap.price_change_15m = (closes[-1] - closes[-2]) / closes[-2]

        return snap

    # ================================================================
    # 技术指标计算（纯 numpy，无外部依赖）
    # ================================================================

    def _calc_rsi(self, closes: np.ndarray, period: int = 14) -> Optional[float]:
        """计算 RSI"""
        if len(closes) < period + 1:
            return None
        deltas = np.diff(closes[-period - 1:])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100 - 100 / (1 + rs))

    def _calc_macd(self, closes: np.ndarray) -> Dict[str, float]:
        """计算 MACD"""
        if len(closes) < MACD_SLOW + MACD_SIGNAL:
            return {"dif": 0, "dea": 0, "histogram": 0}

        ema_fast = self._calc_ema_array(closes, MACD_FAST)
        ema_slow = self._calc_ema_array(closes, MACD_SLOW)
        dif = ema_fast - ema_slow
        dea = pd.Series(dif).ewm(span=MACD_SIGNAL, adjust=False).mean().iloc[-1]
        histogram = dif[-1] - dea

        return {
            "dif": round(float(dif[-1]), 4),
            "dea": round(float(dea), 4),
            "histogram": round(float(histogram), 4),
        }

    def _calc_bollinger(self, closes: np.ndarray) -> Dict[str, float]:
        """计算布林带"""
        if len(closes) < BOLLINGER_PERIOD:
            return {"upper": 0, "middle": 0, "lower": 0}

        window = closes[-BOLLINGER_PERIOD:]
        middle = np.mean(window)
        std = np.std(window)
        return {
            "upper": round(float(middle + BOLLINGER_STD * std), 4),
            "middle": round(float(middle), 4),
            "lower": round(float(middle - BOLLINGER_STD * std), 4),
        }

    def _calc_ema(self, closes: np.ndarray, period: int) -> float:
        """计算单个 EMA 值"""
        if len(closes) < period:
            return float(closes[-1])
        return float(pd.Series(closes).ewm(span=period, adjust=False).mean().iloc[-1])

    def _calc_ema_array(self, closes: np.ndarray, period: int) -> np.ndarray:
        """计算 EMA 数组"""
        return pd.Series(closes).ewm(span=period, adjust=False).mean().values


def format_klines_for_ai(klines: List[Dict]) -> str:
    """将 K 线列表格式化为 AI 可读的文本表格"""
    if not klines:
        return "无数据"

    lines = ["| 时间 | 开 | 高 | 低 | 收 | 成交量 |",
             "|------|----|----|----|----|--------|"]
    for k in klines[-8:]:  # 显示最近 8 根
        from datetime import datetime
        ts = datetime.fromtimestamp(k["ts"] / 1000).strftime("%H:%M")
        lines.append(
            f"| {ts} | {k['o']:.2f} | {k['h']:.2f} | {k['l']:.2f} | {k['c']:.2f} | {k['v']:.0f} |"
        )
    return "\n".join(lines)
