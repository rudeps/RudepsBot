# -*- coding: utf-8 -*-
"""
Глобальные объекты бота
ВНИМАНИЕ: Этот модуль НЕ должен импортировать другие наши модули!
"""

import telebot
import easyocr
from logger import setup_logging

# Логгер (не зависит от других модулей)
logger = setup_logging()

# Инициализация бота (не зависит от других модулей)
from config import BOT_TOKEN
bot = telebot.TeleBot(BOT_TOKEN)

# Инициализация OCR (не зависит от других модулей)
from config import OCR_LANGUAGES
logger.info("Загрузка моделей OCR...")
try:
    reader = easyocr.Reader(OCR_LANGUAGES, gpu=False)
    logger.info("Модели OCR успешно загружены")
except Exception as e:
    logger.error(f"Ошибка загрузки OCR моделей: {e}")
    raise

# База данных будет инициализирована после создания всех объектов
db = None  # Заполнится позже

# Словарь для антифлуда
last_photo_time = {}

# UserState будет определен в другом модуле и импортирован туда, где нужен