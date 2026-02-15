# -*- coding: utf-8 -*-
"""
Настройка логирования для бота
"""

import logging
from logging.handlers import RotatingFileHandler
from config import LOG_FILE

def setup_logging():
    """Настройка системы логирования"""
    logger = logging.getLogger('RudepsBot')
    logger.setLevel(logging.INFO)

    # Форматтер для логов
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Файловый handler с ротацией
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)

    # Консольный handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger