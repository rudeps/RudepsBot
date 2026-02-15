# -*- coding: utf-8 -*-
"""
Конфигурационный файл бота RudepsBot
"""
import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Токен бота (получить у @BotFather)
BOT_TOKEN = os.getenv('BOT_TOKEN', "8526526327:AAF0FHqly8li_q6YDH36ilhSsDhUz5_fCl0")

# Список ID администраторов (можно добавить несколько)
ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '8286237801').split(',')]

# Название бота (используется для поиска в комментариях)
BOT_NAME = "RudepsBot"

# Файлы
DATABASE_FILE = "bot_database.db"
LOG_FILE = "bot.log"
USER_IDS_FILE = "user_ids.txt"  # Файл для экспорта ID

# Настройки вывода средств
MIN_WITHDRAW_CARD = 150      # Минимум на карту
MIN_WITHDRAW_PHONE = 100     # Минимум на телефон

# Еженедельное списание комментариев
WEEKLY_COMMENT_DECREMENT = 10
COMMENT_THRESHOLD = 10       # Порог для разблокировки

# OCR
OCR_LANGUAGES = ['ru', 'en']  # Языки для распознавания

# Антифлуд (секунды между отправкой фото)
ANTIFLOOD_SECONDS = 10

# Настройки планировщика
SCHEDULE_TIME = "00:00"      # Время еженедельной проверки (понедельник)

MAX_PHOTO_SIZE_MB = 20  # Максимальный размер фото в MB
