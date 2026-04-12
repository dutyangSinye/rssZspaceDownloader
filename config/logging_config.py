# 日志配置模块
import os
import logging
from logging.handlers import RotatingFileHandler
from config.settings import Settings


def setup_logging(name: str = "mteam_downloader") -> logging.Logger:
    """配置日志系统"""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, Settings.LOG_LEVEL.upper(), logging.INFO))

    # 日志格式
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件输出（带轮转）
    log_file = Settings.BASE_DIR / Settings.LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
