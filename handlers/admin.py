# -*- coding: utf-8 -*-
"""
Админ-панель: рассылки, управление балансами, тикеты на выплату, экспорт ID, статистика
"""

import os
import time
import sqlite3
from datetime import datetime
from aiogram import types, Dispatcher
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

from utils import (
    is_admin, get_admin_keyboard, send_main_menu,
    get_user_display_name, export_all_user_ids_to_file
)
from config import ADMIN_IDS
import asyncio


def register_handlers(dp: Dispatcher, bot, db, user_state):
    """Регистрация обработчиков админ-панели"""
    
    @dp.message_handler(lambda message: message.text in [
        "👥 Рассылка", "💰 Управление балансами", "📊 Статистика",
        "📤 Экспорт ID", "🔧 Тикеты на выплату", "🔙 Назад в меню"
    ] and is_admin(message.from_user.id, db))
    async def handle_admin_buttons(message: types.Message):
        """Обработчик кнопок админ-панели"""
        user_id = message.from_user.id
        text = message.text

        if text == "👥 Рассылка":
            await start_broadcast(message, bot, user_state)
        elif text == "💰 Управление балансами":
            await start_balance_management(message, bot, user_state)
        elif text == "📊 Статистика":
            await show_admin_stats(message, bot, db)
        elif text == "📤 Экспорт ID":
            await export_user_ids(message, bot, db)
        elif text == "🔧 Тикеты на выплату":
            await show_pending_withdrawals(message, bot, db)
        elif text == "🔙 Назад в меню":
            await send_main_menu(message.chat.id, user_id, bot, db)

    # ===== Рассылка =====

    async def start_broadcast(message: types.Message, bot, user_state):
        """Начать создание рассылки"""
        user_id = message.from_user.id
        user_state.set_state(user_id, 'broadcast_target_type')

        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add("1️⃣ Все пользователи", "2️⃣ Своё количество")
        await bot.send_message(message.chat.id, "Выбери тип аудитории:", reply_markup=markup)

    @dp.message_handler(lambda message: user_state.has_state(message.from_user.id, 'broadcast_target_type'))
    async def handle_broadcast_target_type(message: types.Message, bot, user_state):
        """Обработка выбора типа аудитории"""
        user_id = message.from_user.id
        text = message.text

        if text == "1️⃣ Все пользователи":
            user_state.update_data(user_id, target_type='all')
            user_state.set_state(user_id, 'broadcast_text', **user_state.get_data(user_id))
            await bot.send_message(message.chat.id, "Введите текст сообщения для рассылки:")
        elif text == "2️⃣ Своё количество":
            user_state.set_state(user_id, 'broadcast_count')
            await bot.send_message(message.chat.id, "Введите количество пользователей для выборки:")
        else:
            await bot.send_message(message.chat.id, "Пожалуйста, выберите пункт меню.")

    @dp.message_handler(lambda message: user_state.has_state(message.from_user.id, 'broadcast_count'))
    async def handle_broadcast_count(message: types.Message, bot, user_state):
        """Обработка ввода количества пользователей"""
        user_id = message.from_user.id

        try:
            count = int(message.text)
            if count <= 0:
                raise ValueError
        except ValueError:
            await bot.send_message(message.chat.id, "Введите положительное целое число.")
            return

        user_state.update_data(user_id, count=count)
        user_state.set_state(user_id, 'broadcast_sort', **user_state.get_data(user_id))

        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add("1️⃣ Самые активные", "2️⃣ Самые неактивные", "3️⃣ Случайные")
        await bot.send_message(message.chat.id, "Выберите сортировку:", reply_markup=markup)

    @dp.message_handler(lambda message: user_state.has_state(message.from_user.id, 'broadcast_sort'))
    async def handle_broadcast_sort(message: types.Message, bot, user_state):
        """Обработка выбора сортировки"""
        user_id = message.from_user.id
        text = message.text

        sort_map = {
            "1️⃣ Самые активные": "top_active",
            "2️⃣ Самые неактивные": "top_inactive",
            "3️⃣ Случайные": "random"
        }

        if text not in sort_map:
            await bot.send_message(message.chat.id, "Пожалуйста, выберите пункт меню.")
            return

        user_state.update_data(user_id, target_type=sort_map[text])
        user_state.set_state(user_id, 'broadcast_text', **user_state.get_data(user_id))
        await bot.send_message(message.chat.id, "Введите текст сообщения для рассылки:")

    @dp.message_handler(lambda message: user_state.has_state(message.from_user.id, 'broadcast_text'))
    async def handle_broadcast_text(message: types.Message, bot, user_state):
        """Обработка ввода текста рассылки"""
        user_id = message.from_user.id
        user_state.update_data(user_id, message_text=message.text)
        user_state.set_state(user_id, 'broadcast_link', **user_state.get_data(user_id))
        await bot.send_message(
            message.chat.id,
            "Введите ссылку для кнопки (или отправьте '-' если ссылки не будет):"
        )

    @dp.message_handler(lambda message: user_state.has_state(message.from_user.id, 'broadcast_link'))
    async def handle_broadcast_link(message: types.Message, bot, db, user_state):
        """Обработка ввода ссылки"""
        user_id = message.from_user.id
        link = message.text if message.text != '-' else None
        user_state.update_data(user_id, link=link)
        user_state.set_state(user_id, 'broadcast_reward', **user_state.get_data(user_id))
        await bot.send_message(
            message.chat.id,
            "Введите сумму награды за выполнение задания (целое число рублей):"
        )

    @dp.message_handler(lambda message: user_state.has_state(message.from_user.id, 'broadcast_reward'))
    async def handle_broadcast_reward(message: types.Message, bot, db, user_state):
        """Обработка ввода награды и запуск рассылки"""
        user_id = message.from_user.id

        try:
            reward = int(message.text)
            if reward < 0:
                raise ValueError
        except ValueError:
            await bot.send_message(message.chat.id, "Введите целое неотрицательное число.")
            return

        data = user_state.get_data(user_id)
        user_state.clear_state(user_id)

        # Получаем список получателей
        if data['target_type'] == 'all':
            user_ids = db.get_users_for_broadcast('all')
        else:
            user_ids = db.get_users_for_broadcast(data['target_type'], data.get('count', 0))

        if not user_ids:
            await bot.send_message(message.chat.id, "Нет пользователей для рассылки.")
            return

        # Создаем уникальный ID для этой рассылки
        broadcast_id = int(time.time())
        
        # Сохраняем информацию о рассылке в БД
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO broadcasts 
                (admin_id, target_type, target_count, message_text, link, reward_amount, sent_count, error_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_id, data['target_type'], data.get('count', 0),
                data['message_text'], data.get('link'), reward, 0, 0,
                datetime.now()
            ))
            broadcast_db_id = cur.lastrowid
            conn.commit()

        # Создаем кнопку
        markup = None
        if data.get('link'):
            markup = InlineKeyboardMarkup()
            callback_data = f"complete_{broadcast_db_id}_{reward}"
            markup.add(InlineKeyboardButton("✅ Выполнить", callback_data=callback_data))

        # Отправляем сообщения
        sent = 0
        errors = 0
        error_list = []

        for uid in user_ids:
            try:
                await bot.send_message(uid, data['message_text'], reply_markup=markup)
                sent += 1
                await asyncio.sleep(0.05)  # небольшая задержка
            except Exception as e:
                print(f"Ошибка отправки пользователю {uid}: {e}")
                errors += 1
                error_list.append(str(uid))

        # Обновляем статистику рассылки
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                UPDATE broadcasts 
                SET sent_count = ?, error_count = ?
                WHERE id = ?
            ''', (sent, errors, broadcast_db_id))
            conn.commit()

        # Если были ошибки, сохраняем список проблемных ID
        if errors > 0:
            error_filename = f"broadcast_errors_{broadcast_db_id}.txt"
            with open(error_filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(error_list))
            
            with open(error_filename, 'rb') as f:
                await bot.send_document(
                    message.chat.id,
                    types.InputFile(f),
                    caption=f"❌ Список пользователей с ошибками ({errors})"
                )
            os.remove(error_filename)

        await bot.send_message(
            message.chat.id,
            f"✅ Рассылка завершена.\n"
            f"📨 Отправлено: {sent}\n"
            f"❌ Ошибок: {errors}"
        )

    @dp.callback_query_handler(lambda call: call.data.startswith('complete_'))
    async def callback_complete_task(call: types.CallbackQuery, bot, db):
        """Обработчик нажатия на кнопку выполнения задания"""
        user_id = call.from_user.id
        
        # Парсим данные: complete_broadcast_id_reward
        parts = call.data.split('_')
        if len(parts) >= 3:
            try:
                broadcast_id = int(parts[1])
                reward = int(parts[2])
            except:
                broadcast_id = 0
                reward = 0
        else:
            broadcast_id = 0
            reward = 0

        # Начисляем награду
        db.increment_tasks_completed(user_id, reward)
        
        # Получаем ссылку из базы данных
        link = None
        if broadcast_id > 0:
            with db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT link FROM broadcasts WHERE id = ?", (broadcast_id,))
                row = cur.fetchone()
                if row:
                    link = row['link']
        
        await bot.answer_callback_query(call.id, "Задание выполнено! Награда начислена.")
        
        # Отправляем сообщение с наградой
        await bot.send_message(
            call.message.chat.id, 
            f"✅ Спасибо за выполнение! Начислено {reward}₽ на ваш баланс."
        )
        
        # Если есть ссылка, отправляем её отдельно
        if link:
            await bot.send_message(
                call.message.chat.id,
                f"🔗 Ваша ссылка для перехода: {link}"
            )

    # ===== Управление балансами =====

    async def start_balance_management(message: types.Message, bot, user_state):
        """Начать управление балансами"""
        user_id = message.from_user.id
        user_state.set_state(user_id, 'manage_balances_search')
        await bot.send_message(
            message.chat.id,
            "Введите ID пользователя или username (без @) для поиска:"
        )

    @dp.message_handler(lambda message: user_state.has_state(message.from_user.id, 'manage_balances_search'))
    async def handle_balance_search(message: types.Message, bot, db, user_state):
        """Поиск пользователя для управления балансом"""
        admin_id = message.from_user.id
        query = message.text.strip()

        # Ищем пользователя
        with db.get_connection() as conn:
            cur = conn.cursor()
            if query.isdigit():
                cur.execute("SELECT * FROM users WHERE user_id = ?", (int(query),))
            else:
                cur.execute("SELECT * FROM users WHERE username = ?", (query,))
            user = cur.fetchone()

        if not user:
            await bot.send_message(message.chat.id, "Пользователь не найден.")
            user_state.clear_state(admin_id)
            return

        user = dict(user)
        user_state.set_state(admin_id, 'manage_balances_actions', target_user=user)

        text = (
            f"👤 Пользователь: {get_user_display_name(user)} (ID: {user['user_id']})\n"
            f"📝 Комментариев: {user['comment_balance']}\n"
            f"💰 Денег: {user['money_balance']} руб.\n"
            f"✅ Заданий выполнено: {user['tasks_completed']}\n"
            f"🔒 Заблокирован: {'Да' if user['is_blocked'] else 'Нет'}"
        )

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("➕ Начислить комментарии", callback_data="mod_comment_add"),
            InlineKeyboardButton("➖ Списать комментарии", callback_data="mod_comment_sub"),
            InlineKeyboardButton("➕ Начислить деньги", callback_data="mod_money_add"),
            InlineKeyboardButton("➖ Списать деньги", callback_data="mod_money_sub"),
            InlineKeyboardButton("🔄 Заблокировать/Разблокировать", callback_data="mod_toggle_block"),
            InlineKeyboardButton("🔙 Завершить", callback_data="mod_finish")
        )

        await bot.send_message(message.chat.id, text, reply_markup=markup)

    @dp.callback_query_handler(lambda call: call.data.startswith('mod_'))
    async def callback_balance_modification(call: types.CallbackQuery, bot, db, user_state):
        """Обработка действий с балансом"""
        admin_id = call.from_user.id
        data = call.data

        if not user_state.has_state(admin_id, 'manage_balances_actions'):
            await bot.answer_callback_query(call.id, "Сессия устарела. Начните заново.")
            return

        state_data = user_state.get_data(admin_id)
        target_user = state_data['target_user']
        user_id = target_user['user_id']

        if data == 'mod_comment_add':
            user_state.set_state(admin_id, 'manage_balances_comment_add', target_user=target_user)
            await bot.answer_callback_query(call.id)
            await bot.send_message(call.message.chat.id, "Введите количество комментариев для начисления:")
        elif data == 'mod_comment_sub':
            user_state.set_state(admin_id, 'manage_balances_comment_sub', target_user=target_user)
            await bot.answer_callback_query(call.id)
            await bot.send_message(call.message.chat.id, "Введите количество комментариев для списания:")
        elif data == 'mod_money_add':
            user_state.set_state(admin_id, 'manage_balances_money_add', target_user=target_user)
            await bot.answer_callback_query(call.id)
            await bot.send_message(call.message.chat.id, "Введите сумму рублей для начисления:")
        elif data == 'mod_money_sub':
            user_state.set_state(admin_id, 'manage_balances_money_sub', target_user=target_user)
            await bot.answer_callback_query(call.id)
            await bot.send_message(call.message.chat.id, "Введите сумму рублей для списания:")
        elif data == 'mod_toggle_block':
            new_blocked = not target_user['is_blocked']
            db.set_user_blocked(user_id, new_blocked)
            await bot.answer_callback_query(call.id, f"Блокировка изменена: {'включена' if new_blocked else 'выключена'}")
            # Обновляем данные в состоянии
            target_user['is_blocked'] = new_blocked
            user_state.update_data(admin_id, target_user=target_user)
            # Обновляем сообщение (убираем клавиатуру)
            await bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            await bot.send_message(call.message.chat.id, f"✅ Статус блокировки изменен.")
            # Возвращаемся к меню
            await start_balance_management(call.message, bot, user_state)
        elif data == 'mod_finish':
            user_state.clear_state(admin_id)
            await bot.answer_callback_query(call.id, "Готово.")
            await bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            await send_main_menu(call.message.chat.id, admin_id, bot, db)

    @dp.message_handler(lambda message: user_state.has_state(message.from_user.id, [
        'manage_balances_comment_add', 'manage_balances_comment_sub',
        'manage_balances_money_add', 'manage_balances_money_sub'
    ]))
    async def handle_balance_change(message: types.Message, bot, db, user_state):
        """Обработка изменения баланса"""
        admin_id = message.from_user.id
        state = user_state.get_state(admin_id)
        data = user_state.get_data(admin_id)
        target_user = data['target_user']
        user_id = target_user['user_id']

        try:
            amount = int(message.text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await bot.send_message(message.chat.id, "Введите положительное целое число.")
            return

        with db.get_connection() as conn:
            cur = conn.cursor()

            if state == 'manage_balances_comment_add':
                cur.execute("UPDATE users SET comment_balance = comment_balance + ? WHERE user_id = ?",
                           (amount, user_id))
                msg = f"✅ Начислено {amount} комментариев пользователю {user_id}"
            elif state == 'manage_balances_comment_sub':
                cur.execute("UPDATE users SET comment_balance = comment_balance - ? WHERE user_id = ?",
                           (amount, user_id))
                msg = f"✅ Списано {amount} комментариев у пользователя {user_id}"
            elif state == 'manage_balances_money_add':
                cur.execute("UPDATE users SET money_balance = money_balance + ? WHERE user_id = ?",
                           (amount, user_id))
                msg = f"✅ Начислено {amount} руб. пользователю {user_id}"
            elif state == 'manage_balances_money_sub':
                cur.execute("UPDATE users SET money_balance = money_balance - ? WHERE user_id = ?",
                           (amount, user_id))
                msg = f"✅ Списано {amount} руб. у пользователя {user_id}"

            conn.commit()

        await bot.send_message(message.chat.id, msg)
        user_state.clear_state(admin_id)
        await start_balance_management(message, bot, user_state)

    # ===== Статистика для админа =====

    async def show_admin_stats(message: types.Message, bot, db):
        """Показать расширенную статистику для админа"""
        total_users = db.get_total_users()
        active = db.get_active_users()
        blocked = db.get_blocked_users()
        avg_comments = db.get_avg_comment_balance()
        total_photos = db.get_total_unique_photos()
        withdrawal_stats = db.get_withdrawal_stats()

        top_comments = db.get_top_comment_balance(10)
        top_tasks = db.get_top_tasks_completed(10)

        text = (
            f"📊 Общая статистика:\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"✅ Активных (comment_balance>0): {active}\n"
            f"🔒 Заблокированных: {blocked}\n"
            f"📊 Средний comment_balance: {avg_comments:.2f}\n"
            f"📸 Всего уникальных фото: {total_photos}\n"
            f"💳 Заявки на вывод: "
            f"pending: {withdrawal_stats.get('pending', 0)}, "
            f"approved: {withdrawal_stats.get('approved', 0)}, "
            f"rejected: {withdrawal_stats.get('rejected', 0)}\n\n"
            f"🏆 Топ-10 по comment_balance:\n"
        )

        for row in top_comments:
            uid, bal = row[0], row[1]
            user = db.get_user(uid)
            name = get_user_display_name(user) if user else str(uid)
            text += f"{name}: {bal}\n"

        text += "\n🎯 Топ-10 по tasks_completed:\n"
        for row in top_tasks:
            uid, tasks = row[0], row[1]
            user = db.get_user(uid)
            name = get_user_display_name(user) if user else str(uid)
            text += f"{name}: {tasks}\n"

        await bot.send_message(message.chat.id, text)

    # ===== Экспорт ID =====

    async def export_user_ids(message: types.Message, bot, db):
        """Экспорт ID пользователей в файл и отправка"""
        filename = export_all_user_ids_to_file(db, "user_ids.txt")

        try:
            with open(filename, 'rb') as f:
                await bot.send_document(
                    message.chat.id,
                    types.InputFile(f),
                    caption=f"📤 Экспортировано ID пользователей"
                )
        finally:
            if os.path.exists(filename):
                os.remove(filename)

    # ===== Тикеты на выплату =====

    async def show_pending_withdrawals(message: types.Message, bot, db):
        """Показать ожидающие заявки на вывод"""
        withdrawals = db.get_pending_withdrawals()

        if not withdrawals:
            await bot.send_message(message.chat.id, "Нет ожидающих заявок.")
            return

        for w in withdrawals:
            user = db.get_user(w['user_id'])
            username = get_user_display_name(user) if user else "Неизвестно"

            text = (
                f"🆔 Заявка #{w['id']}\n"
                f"📅 Дата: {w['created_at']}\n"
                f"👤 Пользователь: {username} (ID: {w['user_id']})\n"
                f"💰 Сумма: {w['amount']} руб.\n"
                f"💳 Способ: {w['method']}\n"
                f"📝 Реквизиты: {w['details']}"
            )

            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(
                InlineKeyboardButton("✅ Принять", callback_data=f"approve_{w['id']}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{w['id']}")
            )

            await bot.send_message(message.chat.id, text, reply_markup=markup)

    @dp.callback_query_handler(lambda call: call.data.startswith(('approve_', 'reject_')))
    async def callback_withdrawal_action(call: types.CallbackQuery, bot, db, user_state):
        """Обработка действий с заявкой на вывод"""
        admin_id = call.from_user.id

        if not is_admin(admin_id, db):
            await bot.answer_callback_query(call.id, "Нет прав.")
            return

        action, withdraw_id = call.data.split('_')
        withdraw_id = int(withdraw_id)

        if action == 'approve':
            # Получаем информацию о заявке
            with db.get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT * FROM withdrawals WHERE id = ?", (withdraw_id,))
                row = cur.fetchone()
                if row:
                    w = dict(row)
                else:
                    w = None

            if not w:
                await bot.answer_callback_query(call.id, "Заявка не найдена.")
                return

            # Обновляем статус
            db.update_withdrawal_status(withdraw_id, 'approved')

            # Списываем деньги
            db.deduct_money(w['user_id'], w['amount'])

            # Уведомляем пользователя
            try:
                await bot.send_message(
                    w['user_id'],
                    f"✅ Ваша заявка на вывод {w['amount']}₽ принята. Ожидайте поступления в течение часа."
                )
            except Exception as e:
                print(f"Не удалось уведомить пользователя {w['user_id']}: {e}")

            await bot.answer_callback_query(call.id, "Заявка принята.")
            await bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)

        elif action == 'reject':
            user_state.set_state(admin_id, 'waiting_reject_reason', withdraw_id=withdraw_id, msg=call.message)
            await bot.answer_callback_query(call.id, "Введите причину отказа.")
            await bot.send_message(admin_id, "Напишите причину отказа:")

    @dp.message_handler(lambda message: user_state.has_state(message.from_user.id, 'waiting_reject_reason'))
    async def handle_reject_reason(message: types.Message, bot, db, user_state):
        """Обработка причины отказа"""
        admin_id = message.from_user.id
        data = user_state.get_data(admin_id)
        withdraw_id = data['withdraw_id']
        reason = message.text

        # Получаем информацию о заявке
        with db.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM withdrawals WHERE id = ?", (withdraw_id,))
            row = cur.fetchone()
            if row:
                w = dict(row)
            else:
                w = None

        if not w:
            await bot.send_message(admin_id, "Заявка не найдена.")
            user_state.clear_state(admin_id)
            return

        # Обновляем статус
        db.update_withdrawal_status(withdraw_id, 'rejected', reason)

        # Уведомляем пользователя
        try:
            await bot.send_message(
                w['user_id'],
                f"❌ Заявка на вывод {w['amount']}₽ отклонена.\nПричина: {reason}"
            )
        except Exception as e:
            print(f"Не удалось уведомить пользователя {w['user_id']}: {e}")

        # Удаляем сообщение с кнопками (если оно ещё существует)
        try:
            await bot.delete_message(data['msg'].chat.id, data['msg'].message_id)
        except:
            pass

        user_state.clear_state(admin_id)

        await bot.send_message(admin_id, "✅ Заявка отклонена.")

