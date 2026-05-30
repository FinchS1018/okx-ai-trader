"""
OKX AI 量化交易平台 — 全局配置
所有可调参数集中管理在此文件
"""

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", ".env"))

# ============================================================
# API 密钥（从环境变量读取）
# ============================================================
OKX_API_KEY = os.getenv("OKX_API_KEY", "")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ============================================================
# 运行模式
# ============================================================
TRADING_MODE = os.getenv("TRADING_MODE", "demo")  # "demo" | "live"

# ============================================================
# 交易标的
# ============================================================
SYMBOLS = ["BTC-USDT", "ETH-USDT"]

# ============================================================
# 资金管理
# ============================================================
INITIAL_CAPITAL = 200.0          # 初始总资金 (USDT)
PAPER_TRADING_FEE = 0.001       # 模拟交易手续费率 (0.1%)
MAX_POSITION_RATIO = 0.50        # 单币种最大仓位占比
MAX_SINGLE_ORDER_RATIO = 0.20    # 单笔最大资金占比
MIN_ORDER_USDT = 15.0            # 单笔最低 USDT 金额

# ============================================================
# 风控参数（硬编码，AI 不可绕过）
# ============================================================
STOP_LOSS_RATIO = 0.02           # 止损：入场价 -2%
TAKE_PROFIT_RATIO = 0.04         # 止盈：入场价 +4%
DAILY_LOSS_LIMIT_RATIO = 0.05    # 日亏损熔断：总资金 5%
COOLDOWN_SECONDS = 300           # 交易冷却：5 分钟
MAX_LEVERAGE = 1                 # 杠杆：1x（永不使用杠杆）

# ============================================================
# AI 调用参数
# ============================================================
AI_PROVIDER = os.getenv("AI_PROVIDER", "mimo")  # "mimo" | "anthropic"
MIMO_API_KEY = os.getenv("MIMO_API_KEY", os.getenv("ANTHROPIC_API_KEY", ""))
MIMO_BASE_URL = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
MIMO_MODEL = os.getenv("MIMO_MODEL", "mimo-v2-flash")
# Anthropic 备用
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", MIMO_MODEL)
AI_MAX_TOKENS = 500              # AI 输出最大 token
AI_TEMPERATURE = 0.7             # 温度（0=保守 1=激进）

# AI 调用频率（秒）
AI_CALL_INTERVAL_NORMAL = 180    # 正常：3 分钟
AI_CALL_INTERVAL_LOSS = 120      # 浮亏时：2 分钟
AI_CALL_INTERVAL_IDLE = 60       # 无持仓无信号：1 分钟

# 极端波动阈值（触发立即评估）
EXTREME_VOLATILITY_THRESHOLD = 0.03  # 15 分钟内涨跌超过 3%

# ============================================================
# 技术指标参数
# ============================================================
KLINE_INTERVALS = ["15m", "1H", "4H", "1D", "1Y"]  # 使用的 K 线级别
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2
EMA_PERIODS = [20, 50]

# K 线数据条数（每次获取）
KLINE_BARS_15M = 50
KLINE_BARS_1H = 50
KLINE_BARS_4H = 50
KLINE_BARS_1D = 90
KLINE_BARS_1Y = 12

# ============================================================
# Web 面板
# ============================================================
WEB_HOST = "127.0.0.1"
WEB_PORT = 8080

# ============================================================
# 日志
# ============================================================
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
DECISION_LOG_DIR = os.path.join(LOG_DIR, "decisions")
LOG_LEVEL = "INFO"
