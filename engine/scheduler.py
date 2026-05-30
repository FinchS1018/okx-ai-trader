"""
调度器 — 定时触发 + 事件触发
管理 AI 引擎的调用节奏
"""

import time
import logging
import threading
from typing import Optional, Callable

from config.settings import (
    AI_CALL_INTERVAL_NORMAL,
)

logger = logging.getLogger(__name__)


class Scheduler:
    """简单调度器 — 定时循环 + 事件驱动"""

    def __init__(self, tick_callback: Callable):
        """
        Args:
            tick_callback: 每轮调用的函数，返回本轮摘要
        """
        self._tick = tick_callback
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._pause_between_ticks = 30  # 每 30 秒检查一次

    def start(self):
        """启动调度循环（后台线程）"""
        if self._running:
            logger.warning("调度器已在运行")
            return

        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="TradingScheduler")
        self._thread.start()
        logger.info("调度器已启动")

    def stop(self):
        """停止调度循环"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("调度器已停止")

    def _loop(self):
        """主循环"""
        logger.info("交易调度循环开始")
        while self._running:
            try:
                summary = self._tick()
                # 根据摘要决定等待时间
                wait = self._pause_between_ticks
                if summary.get("executed"):
                    wait = 10  # 有成交，更快检查
                time.sleep(wait)
            except Exception as e:
                logger.error(f"调度循环异常: {e}", exc_info=True)
                time.sleep(30)  # 出错后等久一点

    def is_running(self) -> bool:
        return self._running
