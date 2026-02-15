# -*- coding: utf-8 -*-
"""
Вспомогательные функции и классы
"""

import time
import os
import asyncio
import aioschedule
from datetime import datetime
from typing import Optional, Dict

from aiogram import types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    ANTIFLOOD_SECONDS, BOT_NAME, WEEKLY_COMMENT_DECREMENT,
    COMMENT_THRESHOLD, SCHEDULE_TIME
)


class UserState:
    """Класс для управления состояниями пользователей"""

    def __init__(self):
        self.states = {}  # user_id -> {'state': state_name, 'data': {...}}

    def set_state(self, user_id: int, state: str, **data):
        """Установить состояние пользователя"""
        self.states[user_id] = {'state': state, 'data': data}

    def get_state(self, user_id: int) -> Optional[str]:
        """Получить текущее состояние"""
        if user_id in self.states:
            return self.states[user_id]['state']
        return None

    def get_data(self, user_id: int) -> Dict:
        """Получить данные состояния"""
        if user_id in self.states:
            return self.states[user_id]['data']
        return {}

    def update_data(self, user_id: int, **data):
        """Обновить данные состояния"""
        if user_id in self.states:
            self.states[user_id]['data'].update(data)

    def clear_state(self, user_id: int):
        """Очистить состояние пользователя"""
        if user_id in self.states:
            del self.states[user_id]

    def has_state(self, user_id: int, state: str) -> bool:
        """Проверить, находится ли пользователь в указанном состоянии"""
        return user_id in self.states and self.states[user_id]['state'] == state


def is_admin(user_id: int, db) -> bool:
    """Проверка, является ли пользователь администратором"""
    user = db.get_user(user_id)
    return user and user['is_admin']


def extract_text_from_image(image_path: str, reader) -> str:
    """Извлечение текста из изображения с помощью easyocr"""
    try:
        result = reader.readtext(image_path, detail=0, paragraph=True)
        return ' '.join(result).lower()
    except Exception as e:
        print(f"Ошибка OCR: {e}")
        return ""


def check_flood(user_id: int, last_photo_time: dict, antiflood_seconds: int) -> bool:
    """Проверка антифлуда (не чаще 1 раза в ANTIFLOOD_SECONDS)"""
    last = last_photo_time.get(user_id, 0)
    now = time.time()
    if now - last < antiflood_seconds:
        return False
    last_photo_time[user_id] = now
    return True


def get_main_keyboard(is_blocked: bool = False) -> ReplyKeyboardMarkup:
    """
    Создание клавиатуры главного меню
    
    Args:
        is_blocked: заблокирован ли пользователь
    """
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)

    if is_blocked:
        # Для заблокированных - только кнопка проверки комментария
        markup.add(KeyboardButton("📝 Проверить комментарий"))
    else:
        # Для разблокированных - полное меню
        buttons = [
            KeyboardButton("📝 Проверить комментарий"),
            KeyboardButton("💰 Мой баланс"),
            KeyboardButton("💎 Вывод средств"),
            KeyboardButton("📊 Статистика"),
            KeyboardButton("❓ Помощь")
        ]
        markup.add(*buttons)

    return markup


def get_admin_keyboard() -> ReplyKeyboardMarkup:
    """Создание клавиатуры админ-панели"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [
        KeyboardButton("👥 Рассылка"),
        KeyboardButton("💰 Управление балансами"),
        KeyboardButton("📊 Статистика"),
        KeyboardButton("📤 Экспорт ID"),
        KeyboardButton("🔧 Тикеты на выплату"),
        KeyboardButton("🔙 Назад в меню")
    ]
    markup.add(*buttons)
    return markup


async def send_main_menu(chat_id: int, user_id: int, bot, db):
    """Отправка главного меню"""
    blocked = db.is_user_blocked(user_id)
    markup = get_main_keyboard(blocked)
    
    if blocked:
        user = db.get_user(user_id)
        remaining = max(0, COMMENT_THRESHOLD - user['comment_balance'])
        text = (
            f"🔒 *Доступ заблокирован*\n\n"
            f"📝 Текущий баланс: {user['comment_balance']}\n"
            f"⏳ Осталось для разблокировки: {remaining}\n\n"
            f"Отправляйте скриншоты с упоминанием @{BOT_NAME} чтобы получить комментарии!"
        )
        await bot.send_message(chat_id, text, reply_markup=markup)
    else:
        await bot.send_message(chat_id, "Главное меню:", reply_markup=markup)


def get_user_display_name(user: Dict) -> str:
    """Получить отображаемое имя пользователя"""
    if user.get('username'):
        return f"@{user['username']}"
    parts = []
    if user.get('first_name'):
        parts.append(user['first_name'])
    if user.get('last_name'):
        parts.append(user['last_name'])
    if parts:
        return ' '.join(parts)
    return "Неизвестно"


# ===== Планировщик =====

async def weekly_check(bot, db):
    """Еженедельная проверка и списание комментариев"""
    from logger import setup_logging
    logger = setup_logging()
    
    logger.info("Запуск еженедельного списания комментариев")

    blocked_users = db.weekly_decrement_comments()

    # Отправляем уведомления заблокированным пользователям
    for user_id, new_balance in blocked_users:
        try:
            await bot.send_message(
                user_id,
                f"⛔ *ВНИМАНИЕ: доступ заблокирован!*\n\n"
                f"Произошло еженедельное списание {WEEKLY_COMMENT_DECREMENT} комментариев.\n"
                f"Ваш баланс стал {new_balance}.\n\n"
                f"Чтобы разблокировать доступ, наберите {COMMENT_THRESHOLD} комментариев "
                f"через кнопку '📝 Проверить комментарий'.",
                reply_markup=get_main_keyboard(True)
            )
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

    logger.info(f"Еженедельное списание завершено. Заблокировано: {len(blocked_users)}")


async def run_schedule_async(bot, db):
    """Запуск планировщика"""
    import aioschedule
    import asyncio
    
    aioschedule.every().monday.at(SCHEDULE_TIME).do(weekly_check, bot, db)
    
    while True:
        await aioschedule.run_pending()
        await asyncio.sleep(60)


def export_all_user_ids_to_file(db, filename: str = "user_ids.txt") -> str:
    """Экспортирует все ID пользователей в текстовый файл"""
    ids = db.get_all_user_ids()
    with open(filename, 'w', encoding='utf-8') as f:
        for uid in ids:
            f.write(str(uid) + '\n')
    return filename
