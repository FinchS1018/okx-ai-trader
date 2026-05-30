# OKX AI 量化交易系统

AI-powered quantitative trading bot for OKX exchange with real-time web dashboard, multi-timeframe technical analysis, and paper/live trading modes.

## 功能特性

- **双模式运行**：模拟盘（Paper Trading）和实盘（Live Trading）一键切换
- **AI 智能决策**：接入 MiMo API / Anthropic API，基于多周期技术分析自动做交易决策
- **多周期分析**：15分钟 / 1小时 / 4小时 / 日K / 月线 五级 K 线 + RSI、MACD、布林带、EMA 指标
- **实时 Web 面板**：K 线图（含 B/S 买卖点标记）、持仓监控、实时盈亏、AI 决策日志
- **风控引擎**：硬编码安全护栏，单笔金额限制、止损止盈自动触发
- **模拟盘存盘**：模拟交易状态自动持久化，重启后恢复

## 模式说明

### 模拟盘（paper）

| 项目 | 说明 |
|---|---|
| 行情数据 | OKX 实时行情（真实价格、K线、指标） |
| 账户余额 | 本地模拟，默认 200 USDT |
| 下单 | 本地模拟成交，不涉及真实资金 |
| 持仓 | 虚拟持仓，重启后自动恢复（持久化到 `logs/paper_state.json`） |
| 用途 | 测试策略、验证系统、学习量化交易 |

### 实盘（live）

| 项目 | 说明 |
|---|---|
| 行情数据 | OKX 实时行情 |
| 账户余额 | OKX 账户真实余额 |
| 下单 | 通过 OKX API 实际下单，**真金白银** |
| 持仓 | OKX 账户真实持仓 |
| 用途 | 正式量化交易 |

> **⚠️ 重要**：模拟盘修复的所有 Bug（K线时区、资金余额、USDT 单位换算、仓位限制等）同样适用于实盘。模拟盘验证通过的策略可直接切换到实盘运行。

### 模拟盘（demo）

OKX 官方提供的模拟交易环境，需要 OKX 模拟盘 API 密钥。**推荐优先使用 `paper` 模式**，体验更好且无网络延迟。

## 快速开始

### 环境要求

- Python 3.10+
- 依赖安装：
```bash
pip install -r requirements.txt
```

### 配置

复制环境变量文件并填入密钥：

```bash
cp config/.env.example config/.env
```

编辑 `config/.env`：

```ini
# OKX API 配置（必填，模拟盘也需要行情数据）
OKX_API_KEY=your_okx_api_key_here
OKX_SECRET_KEY=your_okx_secret_key_here
OKX_PASSPHRASE=your_okx_passphrase_here

# AI 提供商配置（mimo 或 anthropic）
AI_PROVIDER=mimo
MIMO_API_KEY=your_mimo_api_key_here
MIMO_MODEL=mimo-v2-flash

# 运行模式: paper | live | demo
TRADING_MODE=paper
```

### 启动

```bash
python main.py
```

访问 `http://127.0.0.1:8080` 打开监控面板。

### 启动不同模式

#### 模拟盘（推荐）
```
# 编辑 config/.env
TRADING_MODE=paper
# 然后启动
python main.py
```

#### 实盘
```
# 编辑 config/.env
TRADING_MODE=live
# 然后启动
python main.py
```

#### 仅启动 Web 面板（不启动交易引擎）
```bash
python main.py --web-only
```

#### 干跑模式（AI 做决策但不实际下单）
```bash
python main.py --dry-run
```

## Web 面板

启动后打开 `http://127.0.0.1:8080`：

| 区域 | 内容 |
|---|---|
| K 线图 | BTC/USDT 切换、多周期切换（15m/1H/4H/日K/月线）、B/S 买卖点标记 |
| 账户 | 总资产、可用 USDT |
| 持仓 | 持仓数量、市值、实时盈亏（百分比 + 绝对值 USDT） |
| 风控 | 今日盈亏、亏损上限、熔断状态 |
| AI 引擎 | AI 在线状态、调用次数、成本统计 |
| AI 决策 | 最新 AI 决策历史 |
| 快速交易 | 手动买入/卖出（输入 USDT 金额） |

> 数据每 3 秒刷新，K 线每 10 秒刷新。按 Ctrl+Shift+R 强制刷新浏览器缓存。

## 系统架构

```
Scheduler ──▶ AIEngine ──▶ DataFetcher ──▶ OKX API（行情）
                  │
                  ├──▶ ClaudeClient ──▶ AI API（决策）
                  │
                  ├──▶ RiskManager ──▶ 风控检查
                  │
                  └──▶ OrderExecutor ──▶ OkxClient / PaperOkxClient（交易）
```

### 目录结构

```
├── main.py                    # 主入口（启动引擎 + Web）
├── config/
│   ├── settings.py            # 全局配置
│   └── .env.example           # 环境变量模板
├── core/
│   ├── okx_client.py          # OKX API 封装（行情/账户/交易）
│   ├── paper_client.py        # 模拟交易客户端（PaperOkxClient）
│   ├── risk_manager.py        # 风控引擎
│   ├── order_executor.py      # 订单执行
│   └── data_fetcher.py        # 数据获取 + 技术指标
├── ai/
│   ├── claude_client.py       # AI 决策客户端
│   ├── ai_engine.py           # AI 引擎（决策主循环）
│   ├── prompt_builder.py      # Prompt 构建
│   └── decision_parser.py     # 决策解析
├── engine/
│   └── scheduler.py           # 调度器
├── web/
│   └── app.py                 # Web 监控面板（Flask）
└── logs/
    ├── trading.log            # 运行日志
    ├── decisions/             # AI 决策审计日志
    └── paper_state.json       # 模拟盘状态持久化
```

## 技术栈

- **语言**：Python 3.10+
- **框架**：Flask（Web 面板）、Lightweight Charts（K 线图）
- **交易所 API**：OKX V5 API（python-okx SDK）
- **AI API**：MiMo API（OpenAI 兼容）/ Anthropic API
- **技术指标**：pandas、numpy

## 风控规则

所有风控规则硬编码在 `config/settings.py` 和 `core/risk_manager.py` 中，AI 不可绕过：

| 规则 | 参数 | 默认值 |
|---|---|---|
| 单笔最小 USDT | `MIN_ORDER_USDT` | 15 USDT |
| 单笔最大占比 | `MAX_SINGLE_ORDER_RATIO` | 20% |
| 止损比例 | `STOP_LOSS_RATIO` | -2% |
| 止盈比例 | `TAKE_PROFIT_RATIO` | +4% |
| 日亏损熔断 | `DAILY_LOSS_LIMIT_RATIO` | 5% |
| 交易冷却 | `COOLDOWN_SECONDS` | 300 秒 |

## 常见问题

**Q：只有 75 USDT 可以跑吗？**
A：可以，系统最低要求 75 USDT（需满足单笔最小 15 USDT + 单笔最大占比 20%），但推荐至少 200 USDT 以获得更好的策略空间。

**Q：AI 为什么一直 HOLD？**
A：AI 按照量化规则分析市场，只在出现明确信号时交易。当前无持仓时每 1 分钟检查一次，有持仓时每 3 分钟检查一次。HOLD 也是有效的量化决策。

**Q：模拟盘重启后数据还在吗？**
A：模拟盘的余额、持仓、成交记录会自动保存到 `logs/paper_state.json`，重启后自动恢复。

**Q：如何验证系统是否能正常交易？**
A：
1. 切换为 paper 模式
2. 在 Web 面板「快速交易」手动下一笔买入
3. 观察持仓、余额、盈亏是否正常变化
4. 再手动卖出，验证完整交易流程
5. 以上流程验证通过后，即可切换到实盘

## License

MIT
