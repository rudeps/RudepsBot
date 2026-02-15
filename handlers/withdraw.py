# -*- coding: utf-8 -*-
"""
Обработчики для вывода средств
"""

from telebot import types
import globals
from config import MIN_WITHDRAW_CARD, MIN_WITHDRAW_PHONE, ADMIN_IDS

bot = globals.bot
db = globals.db
user_state = globals.user_state
logger = globals.logger


def start_withdrawal(message: types.Message):
    """Начать процесс вывода средств"""
    user_id = message.from_user.id
    money = db.get_money_balance(user_id)

    if money < MIN_WITHDRAW_CARD:
        bot.send_message(
            message.chat.id,
            f"💤 Минимальная сумма вывода — {MIN_WITHDRAW_CARD}₽. Твой баланс: {money}₽"
        )
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("1️⃣ На карту", callback_data="withdraw_card"),
        types.InlineKeyboardButton("2️⃣ На баланс телефона", callback_data="withdraw_phone")
    )
    bot.send_message(message.chat.id, "Выберите способ вывода:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("withdraw_"))
def callback_withdraw_method(call: types.CallbackQuery):
    """Выбор способа вывода"""
    user_id = call.from_user.id
    method = call.data.split('_')[1]

    user_state.set_state(user_id, 'waiting_withdraw_amount', method=method)
    bot.answer_callback_query(call.id)

    min_amount = MIN_WITHDRAW_CARD if method == 'card' else MIN_WITHDRAW_PHONE
    bot.send_message(
        call.message.chat.id,
        f"Введите сумму для вывода (минимум {min_amount}₽, целое число):"
    )


@bot.message_handler(func=lambda message: user_state.has_state(message.from_user.id, 'waiting_withdraw_amount'))
def handle_withdraw_amount(message: types.Message):
    """Обработка ввода суммы вывода"""
    user_id = message.from_user.id
    data = user_state.get_data(user_id)
    method = data.get('method')

    try:
        amount = int(message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(message.chat.id, "Пожалуйста, введите положительное целое число.")
        return

    min_amount = MIN_WITHDRAW_CARD if method == 'card' else MIN_WITHDRAW_PHONE
    if amount < min_amount:
        bot.send_message(message.chat.id, f"Сумма должна быть не меньше {min_amount}₽.")
        return

    money = db.get_money_balance(user_id)
    if amount > money:
        bot.send_message(message.chat.id, f"Недостаточно средств. Ваш баланс: {money}₽.")
        return

    user_state.update_data(user_id, amount=amount)
    user_state.set_state(user_id, 'waiting_withdraw_details', **user_state.get_data(user_id))

    if method == 'card':
        bot.send_message(message.chat.id, "Введите номер карты (16 цифр):")
    else:
        bot.send_message(message.chat.id, "Введите номер телефона (в любом формате):")


@bot.message_handler(func=lambda message: user_state.has_state(message.from_user.id, 'waiting_withdraw_details'))
def handle_withdraw_details(message: types.Message):
    """Обработка ввода реквизитов для вывода"""
    user_id = message.from_user.id
    data = user_state.get_data(user_id)
    method = data.get('method')
    amount = data.get('amount')
    details = message.text.strip()

    # Валидация (простая)
    if method == 'card':
        # Удаляем возможные пробелы и проверяем, что остались только цифры и длина 16
        card_number = ''.join(filter(str.isdigit, details))
        if len(card_number) != 16:
            bot.send_message(
                message.chat.id,
                "Некорректный номер карты. Введите 16 цифр без пробелов."
            )
            return
        details = card_number
    else:
        # Для телефона просто проверяем, что есть хотя бы одна цифра
        if not any(c.isdigit() for c in details):
            bot.send_message(message.chat.id, "Пожалуйста, введите номер телефона.")
            return

    # Создаём заявку
    db.create_withdrawal(user_id, amount, method, details)
    user_state.clear_state(user_id)

    bot.send_message(
        message.chat.id,
        "✅ Заявка на вывод создана. Ожидайте решения администратора."
    )

    # Уведомляем админов
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                admin_id,
                f"🔔 Новая заявка на вывод!\n"
                f"Пользователь: {user_id}\n"
                f"Сумма: {amount}₽\n"
                f"Способ: {method}"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить админа {admin_id}: {e}")