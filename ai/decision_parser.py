"""
解析 Claude API 返回的 JSON 决策
容错处理 — AI 输出不一定完美，需要多层防护
"""

import json
import re
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# 默认决策（当 AI 返回不可解析时）
FALLBACK_DECISION = {
    "decision": "hold",
    "symbol": None,
    "amount": 0,
    "price_type": "limit",
    "limit_price": None,
    "confidence": 0,
    "reasoning": "AI 输出解析失败，降级为 hold",
    "stop_loss": None,
    "take_profit": None,
    "new_grid_low": None,
    "new_grid_high": None,
    "new_grid_levels": None,
}

# 有效决策类型
VALID_DECISIONS = {"buy", "sell", "hold", "adjust_grid", "pause_grid", "emergency_exit"}
VALID_SYMBOLS = {"BTC-USDT", "ETH-USDT"}


def parse_decision(raw_api_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    从 Claude API 返回的原始结果中解析交易决策

    Args:
        raw_api_response: {"raw": "...", "cost": ..., "tokens": ...}

    Returns:
        标准化决策 dict
    """
    raw_text = raw_api_response.get("raw", "")

    # Step 1: 尝试从文本中提取 JSON
    decision = _extract_json(raw_text)
    if decision is None:
        logger.warning(f"无法从 AI 输出中提取 JSON，使用 fallback。原始输出: {raw_text[:200]}")
        decision = dict(FALLBACK_DECISION)
    else:
        # Step 2: 验证和修正
        decision = _validate_and_fix(decision)

    # 附加元数据
    decision["_meta"] = {
        "cost": raw_api_response.get("cost", 0),
        "tokens": raw_api_response.get("tokens", 0),
        "raw_response": raw_text[:500],
    }

    return decision


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从 AI 输出文本中提取 JSON 对象"""
    if not text:
        return None

    # 尝试 1: 直接解析整个文本
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 尝试 2: 提取 ```json ... ``` 代码块
    code_block_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 尝试 3: 提取第一个 { ... } 对象
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _validate_and_fix(decision: Dict[str, Any]) -> Dict[str, Any]:
    """验证并修正 AI 决策中的字段"""

    # decision 类型校验
    d = decision.get("decision", "hold")
    if d not in VALID_DECISIONS:
        logger.warning(f"无效决策类型 '{d}'，降级为 hold")
        d = "hold"

    # symbol 校验
    symbol = decision.get("symbol")
    if symbol and symbol not in VALID_SYMBOLS:
        logger.warning(f"无效交易对 '{symbol}'，忽略")
        symbol = None

    # amount 校验
    amount = decision.get("amount", 0) or 0
    if not isinstance(amount, (int, float)):
        amount = 0
    if amount < 0:
        amount = 0

    # confidence 校验
    confidence = decision.get("confidence", 5) or 5
    if not isinstance(confidence, (int, float)):
        confidence = 5
    confidence = max(1, min(10, int(confidence)))

    # price_type 校验
    price_type = decision.get("price_type", "limit")
    if price_type not in ("limit", "market"):
        price_type = "limit"

    # stop_loss / take_profit 校验
    stop_loss = decision.get("stop_loss")
    take_profit = decision.get("take_profit")
    if stop_loss and not isinstance(stop_loss, (int, float)):
        stop_loss = None
    if take_profit and not isinstance(take_profit, (int, float)):
        take_profit = None

    # hold 时清空不重要字段
    if d == "hold":
        amount = 0
        stop_loss = None
        take_profit = None

    return {
        "decision": d,
        "symbol": symbol,
        "amount": amount,
        "price_type": price_type,
        "limit_price": decision.get("limit_price"),
        "confidence": confidence,
        "reasoning": decision.get("reasoning", ""),
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "new_grid_low": decision.get("new_grid_low"),
        "new_grid_high": decision.get("new_grid_high"),
        "new_grid_levels": decision.get("new_grid_levels"),
    }


def decision_to_summary(decision: Dict[str, Any]) -> str:
    """将决策转为一行摘要（用于日志和 Web 面板）"""
    d = decision.get("decision", "?")
    symbol = decision.get("symbol", "-")
    amount = decision.get("amount", 0)
    confidence = decision.get("confidence", "?")
    reasoning = decision.get("reasoning", "")

    if d == "hold":
        return f"🤚 HOLD (置信度 {confidence}/10) — {reasoning[:80]}"
    elif d == "buy":
        return f"🟢 BUY {symbol} x{amount} (置信度 {confidence}/10) — {reasoning[:80]}"
    elif d == "sell":
        return f"🔴 SELL {symbol} x{amount} (置信度 {confidence}/10) — {reasoning[:80]}"
    elif d == "emergency_exit":
        return f"🚨 EMERGENCY EXIT (置信度 {confidence}/10) — {reasoning[:80]}"
    elif d == "adjust_grid":
        return f"🔧 ADJUST_GRID (置信度 {confidence}/10) — {reasoning[:80]}"
    elif d == "pause_grid":
        return f"⏸️ PAUSE_GRID (置信度 {confidence}/10) — {reasoning[:80]}"
    return f"{d} {symbol} — {reasoning[:80]}"
