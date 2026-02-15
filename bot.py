# -*- coding: utf-8 -*-
"""
Основной файл бота RudepsBot
Запуск бота, регистрация обработчиков, планировщик
"""

import asyncio
import logging
import sys
import os
from aiogram import Bot, Dispatcher
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import ParseMode

# Добавляем текущую директорию в путь Python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import BOT_TOKEN
from database import Database
from logger import setup_logging
from utils import UserState, run_schedule_async
import handlers

# Настройка логирования
logger = setup_logging()

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.MARKDOWN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Инициализация БД и user_state
db = Database()
user_state = UserState()

# Инициализация OCR
import easyocr
from config import OCR_LANGUAGES
logger.info("Загрузка моделей OCR...")
try:
    reader = easyocr.Reader(OCR_LANGUAGES, gpu=False)
    logger.info("Модели OCR успешно загружены")
except Exception as e:
    logger.error(f"Ошибка загрузки OCR моделей: {e}")
    raise

# Словарь для антифлуда
last_photo_time = {}

# Регистрация обработчиков
def register_handlers():
    """Регистрация всех обработчиков"""
    handlers.common.register_handlers(dp, bot, db, user_state, reader, last_photo_time)
    handlers.comment.register_handlers(dp, bot, db, user_state, reader, last_photo_time)
    handlers.withdraw.register_handlers(dp, bot, db, user_state)
    handlers.admin.register_handlers(dp, bot, db, user_state)

async def on_startup(dp):
    """Действия при запуске бота"""
    logger.info("=" * 50)
    logger.info("Запуск бота RudepsBot")
    logger.info("=" * 50)
    
    # Запуск планировщика
    asyncio.create_task(run_schedule_async(bot, db))

async def on_shutdown(dp):
    """Действия при остановке бота"""
    logger.info("Бот остановлен")
    await dp.storage.close()
    await dp.storage.wait_closed()

async def main():
    """Основная функция запуска"""
    register_handlers()
    
    # Запуск поллинга
    try:
        await on_startup(dp)
        await dp.start_polling()
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")
        raise
    finally:
        await on_shutdown(dp)

if __name__ == "__main__":
    asyncio.run(main())
