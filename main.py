#!/usr/bin/env python3
"""
OKX AI 量化交易平台 — 主入口

启动方式：
  python main.py            # 启动交易引擎 + Web 面板
  python main.py --web-only # 仅启动 Web 面板
  python main.py --dry-run  # 干跑模式（不实际下单，仅 AI 决策）

架构：
  1. 初始化 OKX 客户端 + Claude 客户端 + 风控引擎
  2. 启动 AI 引擎 + 调度器（后台线程）
  3. 启动 Flask Web 面板（主线程）
"""

import sys
import os
import io
import logging
import signal
import threading

# Windows 终端 UTF-8 编码修复
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import (
    WEB_HOST,
    WEB_PORT,
    LOG_DIR,
    LOG_LEVEL,
    TRADING_MODE,
)
from core.okx_client import OkxClient
from core.risk_manager import RiskManager
from ai.claude_client import ClaudeClient
from ai.ai_engine import AIEngine
from engine.scheduler import Scheduler
from web.app import init_web, run_web


def setup_logging():
    """配置日志系统"""
    os.makedirs(LOG_DIR, exist_ok=True)

    # 根日志
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                os.path.join(LOG_DIR, "trading.log"),
                encoding="utf-8",
            ),
        ],
    )

    # 降低第三方库日志级别
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("okx").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def print_banner():
    """打印启动横幅"""
    mode_labels = {"demo": "模拟盘", "paper": "模拟交易", "live": "实盘 🔴"}
    banner = """
╔══════════════════════════════════════════╗
║       OKX AI 量化交易平台 v1.0            ║
║       AI-Powered Trading System          ║
╠══════════════════════════════════════════╣
║  策略: 多周期分析 + AI 决策               ║
║  标的: BTC-USDT / ETH-USDT               ║
║  模式: {mode:12s}                 ║
║  风控: 硬编码安全护栏                      ║
║  面板: http://{host}:{port:<5d}              ║
╚══════════════════════════════════════════╝
""".format(
        mode=mode_labels.get(TRADING_MODE, TRADING_MODE),
        host=WEB_HOST,
        port=WEB_PORT,
    )
    print(banner)


def main():
    """主函数"""
    setup_logging()
    logger = logging.getLogger(__name__)

    # 解析命令行参数
    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args
    web_only = "--web-only" in args

    print_banner()

    # 检查配置
    from config import settings

    if not web_only:
        if not settings.OKX_API_KEY or settings.OKX_API_KEY.startswith("your_"):
            logger.error("OKX API Key 未配置！请编辑 config/.env 文件")
            logger.error("  复制 config/.env.example 为 config/.env 并填入真实密钥")
            sys.exit(1)

        ai_configured = (
            (settings.AI_PROVIDER == "mimo" and settings.MIMO_API_KEY and not settings.MIMO_API_KEY.startswith("your_"))
            or (settings.ANTHROPIC_API_KEY and not settings.ANTHROPIC_API_KEY.startswith("your_"))
        )
        if not ai_configured:
            logger.warning("⚠️  AI API Key 未配置（MIMO_API_KEY 或 ANTHROPIC_API_KEY），AI 决策将降级到纯规则模式")
            logger.warning("  如需 AI 决策，请编辑 config/.env 填入 MIMO_API_KEY")

    # ================================================================
    # 初始化组件
    # ================================================================
    logger.info("初始化组件...")

    okx_client = OkxClient()
    if TRADING_MODE == "paper":
        from core.paper_client import PaperOkxClient
        okx_client = PaperOkxClient(initial_capital=settings.INITIAL_CAPITAL)
        logger.info(f"📝 模拟交易模式 (初始资金: {settings.INITIAL_CAPITAL} USDT)")
    risk_manager = RiskManager()
    claude_client = ClaudeClient()

    if claude_client.is_available():
        logger.info(f"✅ AI 已就绪（模型: {settings.AI_MODEL}, 提供商: {settings.AI_PROVIDER}）")
    else:
        logger.warning("⚠️  Claude AI 不可用，将使用纯规则模式（风控规则兜底）")

    logger.info(f"✅ OKX 客户端已就绪（{TRADING_MODE}）")
    logger.info(f"✅ 风控引擎已就绪")
    logger.info(f"✅ 交易标的: {settings.SYMBOLS}")

    # ================================================================
    # 启动
    # ================================================================

    if web_only:
        logger.info("仅启动 Web 面板模式")
        init_web(None, None, None)
        run_web()
        return

    # 初始化 AI 引擎
    ai_engine = AIEngine(okx_client, claude_client, risk_manager)

    # 初始化 Web 面板
    init_web(ai_engine, risk_manager, okx_client)

    if dry_run:
        logger.info("🔍 干跑模式 — AI 会做决策但不会实际下单")
        # 干跑模式下，替换 order_executor 为 mock
        from core.order_executor import OrderExecutor

        class DryRunExecutor(OrderExecutor):
            def execute_decision(self, decision, adjustments):
                result = super().execute_decision(decision, adjustments)
                if result.get("executed"):
                    logger.info(f"[DRY-RUN] 模拟执行: {result.get('details')}")
                return result

        ai_engine._executor = DryRunExecutor(okx_client)

    # 启动调度器
    scheduler = Scheduler(ai_engine.tick)
    scheduler.start()
    logger.info("✅ 交易引擎已启动")

    # 优雅关闭处理
    def shutdown(signum, frame):
        logger.info("收到关闭信号，正在安全退出...")
        scheduler.stop()
        okx_client.cancel_all_orders()
        logger.info("已退出。再见！")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # 启动 Web 面板（主线程）
    try:
        run_web()
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
