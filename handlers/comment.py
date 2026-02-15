# -*- coding: utf-8 -*-
"""
Обработчики для проверки комментариев
"""

import os
import time
import hashlib
import aiofiles
from aiogram import types, Dispatcher
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from utils import check_flood, extract_text_from_image, get_main_keyboard
from config import BOT_NAME, COMMENT_THRESHOLD, ANTIFLOOD_SECONDS, MAX_PHOTO_SIZE_MB


# Константы
MAX_PHOTO_SIZE = MAX_PHOTO_SIZE_MB * 1024 * 1024  # в байтах


async def handle_check_comment(message: types.Message, bot, db, user_state, reader, last_photo_time):
    """
    Начать проверку комментария
    """
    user_id = message.from_user.id
    
    try:
        print(f"Пользователь {user_id} нажал кнопку проверки комментария")
        
        # Проверяем, существует ли пользователь в БД
        user = db.get_user(user_id)
        if not user:
            await bot.send_message(
                message.chat.id,
                "❌ Ошибка: пользователь не найден. Используйте /start для регистрации."
            )
            return
        
        # Проверяем, принял ли пользователь правила
        if not user.get('accepted_rules', False):
            await bot.send_message(
                message.chat.id,
                "❌ Сначала примите правила использования бота через /start"
            )
            return
        
        # Проверка антифлуд
        if not check_flood(user_id, last_photo_time, ANTIFLOOD_SECONDS):
            remaining_time = int(ANTIFLOOD_SECONDS - (time.time() - last_photo_time.get(user_id, 0)))
            await bot.send_message(
                message.chat.id,
                f"⏳ Слишком часто. Подождите {remaining_time} секунд."
            )
            return
        
        # Устанавливаем состояние ожидания фото
        user_state.set_state(user_id, 'waiting_photo')
        
        # Отправляем сообщение с инструкцией
        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(KeyboardButton("❌ Отмена"))
        
        await bot.send_message(
            message.chat.id,
            f"📸 Отправьте скриншот вашего комментария в TikTok, содержащего слово '{BOT_NAME}'.\n\n"
            f"Требования к фото:\n"
            f"• Формат: JPG, PNG, GIF, BMP, TIFF\n"
            f"• Максимальный размер: {MAX_PHOTO_SIZE_MB} MB\n"
            f"• Комментарий должен быть четко виден\n\n"
            f"Для отмены нажмите кнопку '❌ Отмена'",
            reply_markup=markup
        )
        
    except Exception as e:
        print(f"Ошибка в handle_check_comment для пользователя {user_id}: {e}")
        await bot.send_message(
            message.chat.id,
            "❌ Произошла ошибка. Пожалуйста, попробуйте позже."
        )


async def handle_cancel_photo(message: types.Message, bot, db, user_state):
    """
    Обработчик отмены отправки фото
    """
    user_id = message.from_user.id
    
    try:
        user_state.clear_state(user_id)
        print(f"Пользователь {user_id} отменил отправку фото")
        
        # Возвращаем главное меню
        user = db.get_user(user_id)
        blocked = user['is_blocked'] if user else True
        markup = get_main_keyboard(blocked)
        
        await bot.send_message(
            message.chat.id,
            "❌ Отправка фото отменена. Возврат в главное меню.",
            reply_markup=markup
        )
        
    except Exception as e:
        print(f"Ошибка в handle_cancel_photo для пользователя {user_id}: {e}")
        await bot.send_message(
            message.chat.id,
            "❌ Произошла ошибка. Пожалуйста, попробуйте позже."
        )


async def handle_photo_message(message: types.Message, bot, db, user_state, reader, last_photo_time):
    """
    Основной обработчик получения фото
    """
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    try:
        print(f"Получено фото от пользователя {user_id}")
        
        # Проверяем, находится ли пользователь в состоянии ожидания фото
        current_state = user_state.get_state(user_id)
        
        if current_state != 'waiting_photo':
            print(f"Пользователь {user_id} отправил фото не в состоянии ожидания")
            await bot.send_message(
                chat_id,
                "❌ Сначала нажмите кнопку '📝 Проверить комментарий' в меню.",
                reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
            )
            return
        
        # Очищаем состояние
        user_state.clear_state(user_id)
        
        # Проверка антифлуд
        if not check_flood(user_id, last_photo_time, ANTIFLOOD_SECONDS):
            remaining_time = int(ANTIFLOOD_SECONDS - (time.time() - last_photo_time.get(user_id, 0)))
            await bot.send_message(
                chat_id,
                f"⏳ Слишком часто. Подождите {remaining_time} секунд.",
                reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
            )
            return
        
        # Получаем фото
        if not message.photo:
            await bot.send_message(
                chat_id,
                "❌ Ошибка: фото не обнаружено.",
                reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
            )
            return
        
        photo = message.photo[-1]
        file_id = photo.file_id
        file_size = photo.file_size if hasattr(photo, 'file_size') else 0
        
        # Проверка размера файла
        if file_size > MAX_PHOTO_SIZE:
            await bot.send_message(
                chat_id,
                f"❌ Файл слишком большой. Максимальный размер: {MAX_PHOTO_SIZE_MB} MB.",
                reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
            )
            return
        
        # Отправляем статус обработки
        await bot.send_chat_action(chat_id, 'typing')
        processing_msg = await bot.send_message(
            chat_id,
            "⏳ Обрабатываю фото, пожалуйста, подождите..."
        )
        
        # Скачиваем файл
        try:
            file_info = await bot.get_file(file_id)
            downloaded_file = await bot.download_file(file_info.file_path)
            downloaded_file_bytes = downloaded_file.getvalue()
        except Exception as e:
            print(f"Ошибка скачивания файла: {e}")
            await bot.edit_message_text(
                "❌ Ошибка при скачивании файла.",
                chat_id,
                processing_msg.message_id
            )
            await bot.send_message(
                chat_id,
                "Главное меню:",
                reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
            )
            return
        
        # Вычисляем хэш
        try:
            photo_hash = hashlib.sha256(downloaded_file_bytes).hexdigest()
        except Exception as e:
            print(f"Ошибка вычисления хэша: {e}")
            await bot.edit_message_text(
                "❌ Ошибка при обработке фото.",
                chat_id,
                processing_msg.message_id
            )
            await bot.send_message(
                chat_id,
                "Главное меню:",
                reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
            )
            return
        
        # Проверяем уникальность фото
        try:
            if db.check_photo_hash(photo_hash):
                print(f"Пользователь {user_id} отправил уже использованное фото")
                await bot.edit_message_text(
                    "❌ Этот скриншот уже использовался ранее.",
                    chat_id,
                    processing_msg.message_id
                )
                await bot.send_message(
                    chat_id,
                    "Главное меню:",
                    reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
                )
                return
        except Exception as e:
            print(f"Ошибка проверки хэша: {e}")
        
        # Сохраняем временный файл
        temp_filename = f"temp_{user_id}_{int(time.time())}.jpg"
        
        try:
            async with aiofiles.open(temp_filename, 'wb') as f:
                await f.write(downloaded_file_bytes)
            
            await bot.edit_message_text(
                "⏳ Распознаю текст на фото...",
                chat_id,
                processing_msg.message_id
            )
            
            # Извлекаем текст
            text = extract_text_from_image(temp_filename, reader)
            print(f"Распознанный текст: {text[:100]}...")
            
            if not text:
                await bot.edit_message_text(
                    f"❌ Не удалось распознать текст на фото.",
                    chat_id,
                    processing_msg.message_id
                )
                await bot.send_message(
                    chat_id,
                    "Главное меню:",
                    reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
                )
                return
            
            # Ищем ключевое слово
            if BOT_NAME.lower() in text.lower():
                print(f"Ключевое слово найдено")
                
                # Добавляем комментарий
                try:
                    new_balance = db.add_comment(user_id)
                    db.save_photo_hash(user_id, photo_hash)
                    
                    # Получаем обновленный статус
                    user = db.get_user(user_id)
                    
                    if user['is_blocked']:
                        # Всё ещё заблокирован (меньше 10)
                        remaining = COMMENT_THRESHOLD - new_balance
                        await bot.edit_message_text(
                            f"✅ Комментарий засчитан!\n\n"
                            f"📝 Текущий баланс: {new_balance}\n"
                            f"🔒 СТАТУС: ЗАБЛОКИРОВАН\n"
                            f"⏳ Осталось до разблокировки: {remaining} комментариев",
                            chat_id,
                            processing_msg.message_id
                        )
                    else:
                        # Разблокирован (10+)
                        await bot.edit_message_text(
                            f"✅ Комментарий засчитан!\n\n"
                            f"📝 Текущий баланс: {new_balance}\n"
                            f"🎉 СТАТУС: РАЗБЛОКИРОВАН\n"
                            f"💫 Теперь вам доступны все функции бота!",
                            chat_id,
                            processing_msg.message_id
                        )
                    
                    # Отправляем главное меню
                    await bot.send_message(
                        chat_id,
                        "Главное меню:",
                        reply_markup=get_main_keyboard(user['is_blocked'])
                    )
                    
                except Exception as e:
                    print(f"Ошибка обновления баланса: {e}")
                    await bot.edit_message_text(
                        "❌ Ошибка при начислении комментария.",
                        chat_id,
                        processing_msg.message_id
                    )
                    await bot.send_message(
                        chat_id,
                        "Главное меню:",
                        reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
                    )
            else:
                print(f"Ключевое слово НЕ найдено")
                await bot.edit_message_text(
                    f"❌ На фото не найдено слово '{BOT_NAME}'.\n\n"
                    f"Убедитесь, что комментарий содержит '{BOT_NAME}'",
                    chat_id,
                    processing_msg.message_id
                )
                await bot.send_message(
                    chat_id,
                    "Главное меню:",
                    reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
                )
        
        except Exception as e:
            print(f"Ошибка при обработке фото: {e}")
            await bot.edit_message_text(
                "❌ Произошла ошибка при обработке фото.",
                chat_id,
                processing_msg.message_id
            )
            await bot.send_message(
                chat_id,
                "Главное меню:",
                reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
            )
        
        finally:
            # Удаляем временный файл
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except Exception as e:
                    print(f"Ошибка удаления временного файла: {e}")
    
    except Exception as e:
        print(f"Критическая ошибка: {e}")
        try:
            await bot.send_message(
                chat_id,
                "❌ Произошла критическая ошибка.",
                reply_markup=get_main_keyboard(db.is_user_blocked(user_id))
            )
        except:
            pass


async def handle_unexpected_message(message: types.Message, bot, db, user_state):
    """
    Обработчик любых сообщений, кроме фото, когда ожидается фото
    """
    await bot.send_message(
        message.chat.id,
        "❌ Пожалуйста, отправьте ФОТО (изображение).\n\n"
        "Для отмены нажмите кнопку '❌ Отмена' в меню."
    )


def register_handlers(dp: Dispatcher, bot, db, user_state, reader, last_photo_time):
    """Регистрация обработчиков для комментариев"""
    
    @dp.message_handler(lambda message: message.text == "❌ Отмена", state=None)
    async def cancel_photo(message: types.Message):
        if user_state.has_state(message.from_user.id, 'waiting_photo'):
            await handle_cancel_photo(message, bot, db, user_state)
    
    @dp.message_handler(content_types=['photo'])
    async def photo_message(message: types.Message):
        await handle_photo_message(message, bot, db, user_state, reader, last_photo_time)
    
    @dp.message_handler(lambda message: user_state.has_state(message.from_user.id, 'waiting_photo'))
    async def unexpected_message(message: types.Message):
        await handle_unexpected_message(message, bot, db, user_state)
