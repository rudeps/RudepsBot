# -*- coding: utf-8 -*-
"""
Общие обработчики для пользователей
"""

from telebot import types
import globals
from utils import (
    send_main_menu, get_main_keyboard, is_admin, get_admin_keyboard,
    get_user_display_name
)
from config import MIN_WITHDRAW_CARD, MIN_WITHDRAW_PHONE, WEEKLY_COMMENT_DECREMENT, BOT_NAME, COMMENT_THRESHOLD

bot = globals.bot
db = globals.db
user_state = globals.user_state
logger = globals.logger


@bot.message_handler(commands=['start'])
def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name

    user = db.get_user(user_id)

    if user:
        if user['accepted_rules']:
            db.update_user_activity(user_id)
            # Проверяем статус блокировки
            if user['is_blocked']:
                # Если заблокирован - показываем только кнопку проверки
                markup = get_main_keyboard(True)
                bot.send_message(
                    message.chat.id,
                    "🔒 Доступ заблокирован. Требуется 10 комментариев для разблокировки.",
                    reply_markup=markup
                )
            else:
                send_main_menu(message.chat.id, user_id, bot, db)
        else:
            show_rules(message.chat.id)
    else:
        db.create_user(user_id, username, first_name, last_name)
        show_rules(message.chat.id)


def show_rules(chat_id: int):
    """Показать правила и запросить согласие"""
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Принимаю", callback_data="accept_rules"),
        types.InlineKeyboardButton("❌ Отказываюсь", callback_data="reject_rules")
    )
    
    # Расширенное сообщение с функционалом и заработками
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
    
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data in ["accept_rules", "reject_rules"])
def callback_rules(call: types.CallbackQuery):
    """Обработчик согласия/отказа с правилами"""
    user_id = call.from_user.id

    if call.data == "accept_rules":
        db.set_accepted_rules(user_id)
        bot.answer_callback_query(call.id, "Спасибо! Добро пожаловать.")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        
        # Отправляем сообщение о необходимости рекламы и разблокировки
        markup = get_main_keyboard(True)  # Только кнопка проверки, т.к. заблокирован
        
        promo_text = (
            "📢 *Для получения спонсорских предложений вы должны предварительно прорекламировать бота!*\n\n"
            f"Чтобы разблокировать доступ ко всем функциям бота, необходимо набрать {COMMENT_THRESHOLD} комментариев.\n\n"
            "Как это работает:\n"
            "1️⃣ Отправляйте коментарии в TikTok по типу \"Бригада: Waossx выдал\" или на ваше усмотрение.\n"
            "2️⃣ Отправьте скриншот через кнопку ниже\n"
            "3️⃣ Получите +1 комментарий к балансу\n\n"
            f"После набора {COMMENT_THRESHOLD} комментариев доступ будет автоматически разблокирован!\n\n"
            "👇 *Нажмите кнопку ниже, чтобы начать*"
        )
        
        bot.send_message(
            call.message.chat.id, 
            promo_text, 
            parse_mode="Markdown",
            reply_markup=markup
        )
    else:
        bot.answer_callback_query(call.id, "Доступ закрыт.")
        bot.send_message(call.message.chat.id, "Доступ закрыт.")
        bot.delete_message(call.message.chat.id, call.message.message_id)


@bot.message_handler(commands=['admin'])
def cmd_admin(message: types.Message):
    """Обработчик команды /admin"""
    user_id = message.from_user.id

    if is_admin(user_id, db):
        bot.send_message(message.chat.id, "🔧 Админ-панель", reply_markup=get_admin_keyboard())
    else:
        bot.send_message(message.chat.id, "У вас нет прав администратора.")


@bot.message_handler(commands=['stats'])
def cmd_stats(message: types.Message):
    """Обработчик команды /stats - личная статистика"""
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

    bot.send_message(message.chat.id, text, parse_mode="Markdown")


@bot.message_handler(commands=['help'])
def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    send_help(message)


@bot.message_handler(func=lambda message: message.text in [
    "📝 Проверить комментарий", "💰 Мой баланс", "💎 Вывод средств",
    "📊 Статистика", "❓ Помощь"
])
def handle_menu_buttons(message: types.Message):
    """Обработчик кнопок главного меню"""
    user_id = message.from_user.id

    # Проверяем, принял ли пользователь правила
    user = db.get_user(user_id)
    if not user or not user['accepted_rules']:
        bot.send_message(message.chat.id, "Пожалуйста, используйте /start для начала.")
        return

    db.update_user_activity(user_id)

    # Проверяем блокировку
    if db.is_user_blocked(user_id):
        if message.text == "📝 Проверить комментарий":
            from handlers.comment import handle_check_comment
            handle_check_comment(message)
        else:
            # Показываем сколько осталось для разблокировки
            remaining = max(0, COMMENT_THRESHOLD - user['comment_balance'])
            bot.send_message(
                message.chat.id,
                f"⛔ Доступ заблокирован. Требуется {COMMENT_THRESHOLD} комментариев.\n"
                f"📝 Текущий баланс: {user['comment_balance']}\n"
                f"⏳ Осталось: {remaining}",
                reply_markup=get_main_keyboard(True)
            )
        return

    # Обработка кнопок для разблокированных пользователей
    if message.text == "📝 Проверить комментарий":
        from handlers.comment import handle_check_comment
        handle_check_comment(message)
    elif message.text == "💰 Мой баланс":
        show_balance(message)
    elif message.text == "💎 Вывод средств":
        from handlers.withdraw import start_withdrawal
        start_withdrawal(message)
    elif message.text == "📊 Статистика":
        cmd_stats(message)
    elif message.text == "❓ Помощь":
        send_help(message)


def show_balance(message: types.Message):
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

    bot.send_message(message.chat.id, text, parse_mode="Markdown")


def send_help(message: types.Message):
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
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")