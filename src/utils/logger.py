"""
日志工具模块
"""
import logging
import sys
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

# 尝试导入 loguru
try:
    from loguru import logger
    LOGURU_AVAILABLE = True
except ImportError:
    LOGURU_AVAILABLE = False
    logger = logging.getLogger(__name__)


class InterceptHandler(logging.Handler):
    """
    把标准 logging 的输出桥接到 loguru。
    所有用 logging.getLogger(__name__) 的模块都会通过这里写入 loguru 的 sink。
    """
    def emit(self, record: logging.LogRecord):
        from loguru import logger as _logger
        try:
            level = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 直接用 record 里的模块信息，不依赖 depth 计算
        _logger.patch(lambda r: r.update(
            name=record.name,
            function=record.funcName,
            line=record.lineno,
        )).log(level, record.getMessage())


def setup_logging(
    log_dir: str = None,
    log_level: str = "INFO",
    console_output: bool = True,
    rotation: str = "1 day",
    retention: str = "7 days"
) -> logging.Logger:
    """
    设置日志
    
    Args:
        log_dir: 日志目录
        log_level: 日志级别
        console_output: 是否输出到控制台
        rotation: 日志轮转
        retention: 日志保留时间
    """
    if LOGURU_AVAILABLE:
        # 使用 loguru
        from loguru import logger
        
        # 移除默认处理器
        logger.remove()
        
        # 日志格式
        log_format = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        )
        
        # 添加控制台输出
        if console_output and sys.stdout is not None:
            logger.add(
                sys.stdout,
                format=log_format,
                level=log_level,
                colorize=True
            )
        
        # 桥接标准 logging → loguru（所有业务模块用 logging.getLogger 的输出都会进来）
        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

        # 添加 GUI sink：所有 loguru 日志同步写入全局 LogManager，供 GUI 日志窗口显示
        def _gui_sink(message):
            record = message.record
            ts = record["time"].strftime("%H:%M:%S")
            level = record["level"].name
            text = f"[{ts}] [{level}] {record['message']}"
            log_manager.add_log(text, level)

        logger.add(_gui_sink, level=log_level, format="{message}")

        # 添加文件输出
        if log_dir:
            try:
                log_path = Path(log_dir)
                log_path.mkdir(parents=True, exist_ok=True)
                
                logger.add(
                    log_path / "checkin_{time:YYYY-MM-DD}.log",
                    format=log_format,
                    level=log_level,
                    rotation=rotation,
                    retention=retention,
                    encoding="utf-8"
                )
            except Exception as e:
                if sys.stderr is not None:
                    print(f"警告: 无法创建日志文件: {e}", file=sys.stderr)
        
        return logger
    
    else:
        # 使用标准 logging
        log = logging.getLogger("jiantong")
        log.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        
        # 格式
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        # 控制台处理器
        if console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            log.addHandler(console_handler)
        
        # 文件处理器
        if log_dir:
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)
            
            file_handler = logging.FileHandler(
                log_path / f"checkin_{datetime.now().strftime('%Y-%m-%d')}.log",
                encoding='utf-8'
            )
            file_handler.setFormatter(formatter)
            log.addHandler(file_handler)
        
        return log


class LogManager:
    """日志管理器 - 用于 GUI 获取日志"""

    def __init__(self, max_lines: int = 1000):
        self.max_lines = max_lines
        self._logs: list[str] = []
        self._callbacks: list = []
        self._lock = threading.Lock()

    def add_log(self, message: str, level: str = "INFO"):
        """添加日志"""
        level_upper = level.upper()
        with self._lock:
            self._logs.append((message, level_upper))
            if len(self._logs) > self.max_lines:
                self._logs = self._logs[-self.max_lines:]
            callbacks = list(self._callbacks)

        for callback in callbacks:
            try:
                callback(message, level_upper)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"日志回调异常: {e}", exc_info=True)

    def get_logs(self, lines: int = None) -> list[str]:
        """获取日志"""
        with self._lock:
            if lines:
                return self._logs[-lines:]
            return self._logs.copy()

    def clear(self):
        """清空日志"""
        with self._lock:
            self._logs.clear()

    def add_callback(self, callback):
        """添加日志回调"""
        with self._lock:
            self._callbacks.append(callback)

    def remove_callback(self, callback):
        """移除日志回调"""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)


# 全局日志管理器
log_manager = LogManager()


def get_log_manager() -> LogManager:
    """获取日志管理器"""
    return log_manager