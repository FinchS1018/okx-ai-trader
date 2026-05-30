"""
AI 决策客户端 — 支持 OpenAI 兼容接口和 Anthropic 原生接口
支持 MiMo、Anthropic 官方等多家 AI 提供商
"""

import json
import logging
import time
from typing import Optional, Dict, Any

from openai import OpenAI

from config.settings import (
    AI_PROVIDER,
    MIMO_API_KEY,
    MIMO_BASE_URL,
    MIMO_MODEL,
    ANTHROPIC_API_KEY,
    AI_MODEL,
    AI_MAX_TOKENS,
    AI_TEMPERATURE,
)

logger = logging.getLogger(__name__)

# ================================================================
# 系统提示（会被缓存，几乎不变）
# ================================================================

SYSTEM_PROMPT = """你是一个加密货币短线交易 AI 助手，专门在 OKX 交易所进行 BTC-USDT 和 ETH-USDT 的现货交易。

## 你的职责
每 15-30 分钟，你会收到一份市场数据报告（包含 K 线、技术指标、账户状态），你需要做出一个交易决策。

## 交易哲学（最重要）
- **安全第一**：我们只做现货，不碰合约，不追涨杀跌
- **不追求暴利**：目标是稳定小收益，不是赌博
- **寻找确定性机会**：多周期共振时果断进场，信号不明确时等待
- **控制回撤比追求收益更重要**

## 交易指南（重要）
- **不要只选 hold**：如果技术指标给出明确信号（如 RSI < 30 超卖且有底背离、MACD 金叉、价格在布林下轨），应该果断买入
- **趋势是你的朋友**：4H 和 1D 趋势一致时，顺势操作赢面更大
- **成交量验证**：突破必须有成交量配合，缩量反弹不可靠
- **仓位管理**：信号越强仓位越大，信号一般时小仓位试探

## 决策类型
你可以选择以下决策之一：
- "buy"：买入（开仓或加仓）
- "sell"：卖出（平仓或减仓）
- "hold"：不操作
- "adjust_grid"：调整网格参数（需要提供 new_grid_low, new_grid_high, new_grid_levels）
- "pause_grid"：暂停网格交易（单边行情时）
- "emergency_exit"：紧急清仓（仅在极端情况下使用）

## 决策应考虑的因素
1. **多周期确认**：15m RSI 超卖但 4H 趋势向下 → 可能是陷阱，谨慎买入
2. **成交量验证**：价格突破但缩量 → 可能是假突破
3. **布林带位置**：价格在布林带外 → 大概率回归，但也可能趋势很强
4. **MACD 柱状线变化**：柱状线从负变正在收窄 → 下跌动能减弱，可能反弹
5. **仓位管理**：已有持仓时慎重加仓，不要把所有资金一次用完
6. **今日盈亏**：如果今天已经亏了不少，应该更保守

## 风控红线（绝对不能违反）
- 永远不会建议做空（现货不做空）
- 永远不会建议使用合约或杠杆
- 止损必须 <= 入场价的 2%（系统会自动执行硬止损）
- 单笔买入金额不超过总资产的 20%（系统会强制执行）

## 输出格式（严格 JSON）
你必须只输出一个 JSON 对象，不要有任何额外文字：

{
  "decision": "buy|sell|hold|adjust_grid|pause_grid|emergency_exit",
  "symbol": "BTC-USDT",
  "amount": 0.001,
  "price_type": "limit|market",
  "limit_price": 50000.0,
  "confidence": 7,
  "reasoning": "你的推理过程（1-3句话，解释为什么做这个决定）",
  "stop_loss": null,
  "take_profit": null,
  "new_grid_low": null,
  "new_grid_high": null,
  "new_grid_levels": null
}

- 如果 decision 是 "buy"，必须提供 amount, price_type, stop_loss, take_profit
- 如果 decision 是 "sell"，必须提供 amount
- 如果 decision 是 "hold"，其他字段可为 null
- 如果 decision 是 "adjust_grid"，必须提供 new_grid_low, new_grid_high, new_grid_levels
- confidence 是 1-10 的整数，表示你对自己决策的把握程度
"""


class ClaudeClient:
    """AI 决策客户端 — 支持 OpenAI 兼容和 Anthropic 协议"""

    def __init__(self):
        self._client = None
        self._model = None
        self._call_count = 0
        self._total_cost_estimate = 0.0

        # 根据提供商初始化
        if AI_PROVIDER == "mimo" and MIMO_API_KEY and not MIMO_API_KEY.startswith("your_"):
            self._client = OpenAI(
                api_key=MIMO_API_KEY,
                base_url=MIMO_BASE_URL,
            )
            self._model = MIMO_MODEL
            logger.info(f"AI 客户端: MiMo (模型={self._model}, base_url={MIMO_BASE_URL})")

        elif ANTHROPIC_API_KEY and not ANTHROPIC_API_KEY.startswith("your_"):
            # Anthropic 官方 — 需要 anthropic SDK
            try:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=ANTHROPIC_API_KEY)
                self._model = AI_MODEL
                self._provider = "anthropic"
                logger.info(f"AI 客户端: Anthropic (模型={self._model})")
            except ImportError:
                logger.warning("anthropic SDK 未安装，降级: Anthropic key 不可用")

        if not self._client:
            logger.warning("AI 客户端初始化失败，AI 决策将降级到规则模式")

        # 判断 provider 类型
        self._provider = "openai"  # 默认 OpenAI 兼容（MiMo）
        try:
            if hasattr(self._client, 'messages'):
                self._provider = "anthropic"
        except Exception:
            pass

    def is_available(self) -> bool:
        """检查 AI 是否可用"""
        return self._client is not None

    def get_decision(
        self,
        market_data_text: str,
        account_text: str,
    ) -> Optional[Dict[str, Any]]:
        """
        调用 AI API 获取交易决策
        """
        if not self._client:
            return None

        user_message = f"""## 账户状态
{account_text}

## 市场数据
{market_data_text}

请基于以上数据做出交易决策。只输出 JSON。"""

        try:
            start_time = time.time()

            # 判断是 OpenAI 兼容还是 Anthropic 原生
            # 用 hasattr 检查
            if hasattr(self._client, 'chat'):
                # OpenAI 兼容（MiMo 等）
                response = self._client.chat.completions.create(
                    model=self._model,
                    max_tokens=AI_MAX_TOKENS,
                    temperature=AI_TEMPERATURE,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                )
                raw_text = response.choices[0].message.content
                input_tokens = response.usage.prompt_tokens if response.usage else 0
                output_tokens = response.usage.completion_tokens if response.usage else 0

                # MiMo 定价: $0.1/1M input, $0.3/1M output
                total_cost = (input_tokens * 0.10 + output_tokens * 0.30) / 1_000_000

            else:
                # Anthropic 原生
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=AI_MAX_TOKENS,
                    temperature=AI_TEMPERATURE,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                )
                raw_text = response.content[0].text
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                # Anthropic Haiku: $1/1M input, $5/1M output
                total_cost = (input_tokens * 1.0 + output_tokens * 5.0) / 1_000_000

            elapsed = time.time() - start_time
            self._call_count += 1
            self._total_cost_estimate += total_cost

            logger.info(
                f"AI API #{self._call_count}: "
                f"{input_tokens} in / {output_tokens} out, "
                f"耗时 {elapsed:.1f}s, "
                f"估算成本 ${total_cost:.6f}"
            )

            return {"raw": raw_text, "cost": total_cost, "tokens": input_tokens + output_tokens}

        except Exception as e:
            logger.error(f"AI API 调用失败: {e}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        """获取调用统计"""
        return {
            "call_count": self._call_count,
            "total_cost_estimate": round(self._total_cost_estimate, 6),
        }
