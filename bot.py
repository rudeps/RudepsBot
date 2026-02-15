# -*- coding: utf-8 -*-
"""
Основной файл бота RudepsBot
Запуск бота, регистрация обработчиков, планировщик
"""

import threading
import sys
import os

# Добавляем текущую директорию в путь Python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Сначала импортируем глобальные объекты (без циклических зависимостей)
from globals import bot, logger, reader, last_photo_time

# Инициализируем БД и user_state
from database import Database
from utils import UserState, run_schedule

# Создаем глобальные объекты
db = Database()
user_state = UserState()

# Сохраняем ссылки на них в globals для доступа из других модулей
import globals
globals.db = db
globals.user_state = user_state
globals.last_photo_time = last_photo_time

# Теперь импортируем обработчики (они будут использовать globals)
import handlers

# Запуск планировщика
scheduler_thread = threading.Thread(target=run_schedule, args=(bot, db), daemon=True)
scheduler_thread.start()
logger.info("Планировщик запущен")

# Запуск бота
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("Запуск бота RudepsBot")
    logger.info("=" * 50)
    
    try:
        logger.info("Бот начинает polling...")
        bot.infinity_polling()
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")
        raise