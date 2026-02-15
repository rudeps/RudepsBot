# -*- coding: utf-8 -*-
"""
Общие обработчики для пользователей
"""

from aiogram import types, Dispatcher
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.types import ParseMode, InlineKeyboardMarkup, InlineKeyboardButton

from utils import (
    send_main_menu, get_main_keyboard, is_admin, get_admin_keyboard,
    get_user_display_name
)
from config import MIN_WITHDRAW_CARD, MIN_WITHDRAW_PHONE, WEEKLY_COMMENT_DECREMENT, BOT_NAME, COMMENT_THRESHOLD


def register_handlers(dp: Dispatcher, bot, db, user_state, reader, last_photo_time):
    """Регистрация обработчиков"""
    
    @dp.message_handler(commands=['start'])
    async def cmd_start(message: types.Message):
        user_id = message.from_user.id
        username = message.from_user.username
        first_name = message.from_user.first_name
        last_name = message.from_user.last_name

        user = db.get_user(user_id)

        if user:
            if user['accepted_rules']:
                db.update_user_activity(user_id)
                if user['is_blocked']:
                    markup = get_main_keyboard(True)
                    await bot.send_message(
                        message.chat.id,
                        "🔒 Доступ заблокирован. Требуется 10 комментариев для разблокировки.",
                        reply_markup=markup
                    )
                else:
                    await send_main_menu(message.chat.id, user_id, bot, db)
            else:
                await show_rules(message.chat.id, bot)
        else:
            db.create_user(user_id, username, first_name, last_name)
            await show_rules(message.chat.id, bot)

    @dp.message_handler(commands=['admin'])
    async def cmd_admin(message: types.Message):
        user_id = message.from_user.id

        if is_admin(user_id, db):
            await bot.send_message(message.chat.id, "🔧 Админ-панель", reply_markup=get_admin_keyboard())
        else:
            await bot.send_message(message.chat.id, "У вас нет прав администратора.")

    @dp.message_handler(commands=['stats'])
    async def cmd_stats(message: types.Message):
        user_id = message.from_user.id
        user = db.get_user(user_id)

        if user:
            status = "🔒 Заблокирован" if user['is_blocked'] else "✅ Разблокирован"
            remaining = max(0, COMMENT_THRESHOLD - user['comment_balance']) if user['is_blocked'] else 0
            
            text = (
                f"📊 *Твоя статистика:*\n"
                f"📅 Дата регистрации: {user['registration_date']}\n"
                f"💬 Всего комментариев: {user['total_comments_ever']}\n"
                f"📝 Текущий баланс комментариев: {user['comment_balance']}\n"
                f"🔒 Статус доступа: {status}\n"
            )
            
            if user['is_blocked']:
                text += f"⏳ Осталось для разблокировки: {remaining}\n"
            
            text += (
                f"✅ Выполнено заданий: {user['tasks_completed']}\n"
                f"💰 Заработано денег: {user['money_balance']} руб."
            )
        else:
            text = "Статистика недоступна."

        await bot.send_message(message.chat.id, text, parse_mode=ParseMode.MARKDOWN)

    @dp.message_handler(commands=['help'])
    async def cmd_help(message: types.Message):
        await send_help(message, bot)

    @dp.message_handler(lambda message: message.text in [
        "📝 Проверить комментарий", "💰 Мой баланс", "💎 Вывод средств",
        "📊 Статистика", "❓ Помощь"
    ])
    async def handle_menu_buttons(message: types.Message):
        user_id = message.from_user.id

        user = db.get_user(user_id)
        if not user or not user['accepted_rules']:
            await bot.send_message(message.chat.id, "Пожалуйста, используйте /start для начала.")
            return

        db.update_user_activity(user_id)

        if db.is_user_blocked(user_id):
            if message.text == "📝 Проверить комментарий":
                from handlers.comment import handle_check_comment
                await handle_check_comment(message, bot, db, user_state, reader, last_photo_time)
            else:
                remaining = max(0, COMMENT_THRESHOLD - user['comment_balance'])
                await bot.send_message(
                    message.chat.id,
                    f"⛔ Доступ заблокирован. Требуется {COMMENT_THRESHOLD} комментариев.\n"
                    f"📝 Текущий баланс: {user['comment_balance']}\n"
                    f"⏳ Осталось: {remaining}",
                    reply_markup=get_main_keyboard(True)
                )
            return

        if message.text == "📝 Проверить комментарий":
            from handlers.comment import handle_check_comment
            await handle_check_comment(message, bot, db, user_state, reader, last_photo_time)
        elif message.text == "💰 Мой баланс":
            await show_balance(message, bot, db)
        elif message.text == "💎 Вывод средств":
            from handlers.withdraw import start_withdrawal
            await start_withdrawal(message, bot, db, user_state)
        elif message.text == "📊 Статистика":
            await cmd_stats(message)
        elif message.text == "❓ Помощь":
            await send_help(message, bot)


async def show_rules(chat_id: int, bot):
    """Показать правила и запросить согласие"""
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Принимаю", callback_data="accept_rules"),
        InlineKeyboardButton("❌ Отказываюсь", callback_data="reject_rules")
    )
    
    text = (
        "🤖 *Добро пожаловать в RudepsBot!*\n\n"
        "📱 *Что умеет бот:*\n"
        "• Проверка комментариев с упоминанием @" + BOT_NAME + "\n"
        "• Накопление комментариев для доступа\n"
        "• Выполнение заданий с наградой\n"
        "• Вывод заработанных средств\n\n"
        
        "💰 *Примерные заработки:*\n"
        "• За каждое задание: от 5 до 50₽\n"
        "• В среднем: 500-1500₽ в неделю\n"
        "• Максимальный заработок: до 5000₽/неделю\n\n"
        
        "📊 *Система комментариев:*\n"
        f"• Для разблокировки нужно {COMMENT_THRESHOLD} комментариев\n"
        f"• Каждый понедельник списывается {WEEKLY_COMMENT_DECREMENT} комментариев\n"
        "• Если баланс станет 0 - доступ блокируется\n"
        "• Комментарии можно получать за скриншоты\n\n"
        
        "💳 *Вывод средств:*\n"
        f"• На карту: от {MIN_WITHDRAW_CARD}₽\n"
        f"• На телефон: от {MIN_WITHDRAW_PHONE}₽\n"
        "• Вывод в течение 24 часов\n\n"
        
        "⚠️ *Перед началом работы ознакомьтесь с условиями использования*"
    )
    
    await bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)


async def show_balance(message: types.Message, bot, db):
    """Показать баланс пользователя"""
    user_id = message.from_user.id
    user = db.get_user(user_id)

    if user:
        status = "🔒 Заблокирован" if user['is_blocked'] else "✅ Разблокирован"
        remaining = max(0, COMMENT_THRESHOLD - user['comment_balance']) if user['is_blocked'] else 0
        
        text = (
            f"💰 *Твой баланс:*\n"
            f"📝 Комментариев: {user['comment_balance']}\n"
            f"🔒 Статус: {status}\n"
        )
        
        if user['is_blocked']:
            text += f"⏳ До разблокировки: {remaining} комментариев\n"
        
        text += (
            f"💵 Денег: {user['money_balance']} руб.\n"
            f"✅ Всего выполнено заданий: {user['tasks_completed']}"
        )
    else:
        text = "Ошибка получения данных."

    await bot.send_message(message.chat.id, text, parse_mode=ParseMode.MARKDOWN)


async def send_help(message: types.Message, bot):
    """Отправить справку"""
    help_text = (
        f"❓ *Помощь по боту {BOT_NAME}:*\n\n"
        f"📝 *Проверить комментарий* — отправьте скриншот комментария с упоминанием @{BOT_NAME}, "
        f"чтобы получить +1 к балансу комментариев.\n"
        f"💰 *Мой баланс* — показывает текущие балансы и статус доступа.\n"
        f"💎 *Вывод средств* — создайте заявку на вывод денег "
        f"(минимум {MIN_WITHDRAW_CARD}₽ на карту, {MIN_WITHDRAW_PHONE}₽ на телефон).\n"
        f"📊 *Статистика* — ваша личная статистика.\n"
        f"❓ *Помощь* — это сообщение.\n\n"
        f"🔒 *Система блокировки:*\n"
        f"• Для разблокировки нужно {COMMENT_THRESHOLD} комментариев\n"
        f"• Каждый понедельник списывается {WEEKLY_COMMENT_DECREMENT} комментариев\n"
        f"• Если баланс станет 0 - доступ блокируется\n"
        f"• Баланс не может уйти в минус\n\n"
        f"📢 *Важно:* Для получения спонсорских предложений нужно "
        f"предварительно прорекламировать бота!"
    )
    await bot.send_message(message.chat.id, help_text, parse_mode=ParseMode.MARKDOWN)
