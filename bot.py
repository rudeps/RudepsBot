# -*- coding: utf-8 -*-
"""
RudepsBot v4.0 - Максимально простой бот: фото -> +1 коммент, админу лог
"""

import asyncio
import logging
import sqlite3
import os
import time
import hashlib
import aiofiles
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any, Union
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import (
    ParseMode, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.dispatcher.filters import Text
import aioschedule

# ==================== КОНФИГУРАЦИЯ ====================

@dataclass
class Config:
    """Конфигурация бота"""
    BOT_TOKEN: str = "8287158555:AAGFJPPnaA9pRnicmQRJG6_jO63GWNfCvAk"
    ADMIN_IDS: List[int] = field(default_factory=lambda: [8286237801])
    BOT_NAME: str = "RudepsBot"
    DATABASE_FILE: str = "bot_database.db"
    LOG_FILE: str = "bot.log"
    MIN_WITHDRAW_CARD: int = 150
    MIN_WITHDRAW_PHONE: int = 100
    WEEKLY_COMMENT_DECREMENT: int = 10
    COMMENT_THRESHOLD: int = 10
    ANTIFLOOD_SECONDS: int = 10
    SCHEDULE_TIME: str = "00:00"
    MAX_PHOTO_SIZE_MB: int = 20
    MAX_PHOTO_SIZE: int = 20 * 1024 * 1024

    def __post_init__(self):
        if not self.BOT_TOKEN:
            raise ValueError("BOT_TOKEN не может быть пустым")

# ==================== СОСТОЯНИЯ ====================

class UserState(Enum):
    """Состояния пользователя для FSM"""
    IDLE = "idle"
    WAITING_PHOTO = "waiting_photo"
    WAITING_WITHDRAW_AMOUNT = "waiting_withdraw_amount"
    WAITING_WITHDRAW_DETAILS = "waiting_withdraw_details"
    BROADCAST_TARGET_TYPE = "broadcast_target_type"
    BROADCAST_COUNT = "broadcast_count"
    BROADCAST_SORT = "broadcast_sort"
    BROADCAST_TEXT = "broadcast_text"
    BROADCAST_LINK = "broadcast_link"
    BROADCAST_REWARD = "broadcast_reward"
    MANAGE_BALANCES_SEARCH = "manage_balances_search"
    MANAGE_BALANCES_ACTIONS = "manage_balances_actions"
    WAITING_REJECT_REASON = "waiting_reject_reason"

# ==================== БАЗА ДАННЫХ ====================

class Database:
    """Класс для работы с БД"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.executor = ThreadPoolExecutor(max_workers=4)
        self._cache: Dict[str, tuple] = {}
        self._cache_time: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._init_db_sync()

    def _init_db_sync(self):
        """Синхронная инициализация БД"""
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)

        with self._get_conn_sync() as conn:
            cur = conn.cursor()

            # Таблицы
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    registration_date TIMESTAMP,
                    last_activity TIMESTAMP,
                    comment_balance INTEGER DEFAULT 0,
                    money_balance INTEGER DEFAULT 0,
                    tasks_completed INTEGER DEFAULT 0,
                    total_comments_ever INTEGER DEFAULT 0,
                    is_blocked BOOLEAN DEFAULT FALSE,
                    is_admin BOOLEAN DEFAULT FALSE,
                    accepted_rules BOOLEAN DEFAULT FALSE,
                    last_task_date TIMESTAMP,
                    is_permanently_banned BOOLEAN DEFAULT FALSE
                )
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS used_photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    photo_hash TEXT UNIQUE,
                    timestamp TIMESTAMP
                )
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS comments_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    timestamp TIMESTAMP,
                    week_number INTEGER,
                    month_number INTEGER
                )
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    method TEXT,
                    details TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP,
                    processed_at TIMESTAMP,
                    reject_reason TEXT
                )
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER,
                    target_type TEXT,
                    target_count INTEGER,
                    message_text TEXT,
                    link TEXT,
                    reward_amount INTEGER,
                    sent_count INTEGER,
                    error_count INTEGER,
                    created_at TIMESTAMP
                )
            ''')

            # Индексы
            cur.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_users_is_permanently_banned ON users(is_permanently_banned)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_used_photos_hash ON used_photos(photo_hash)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals(status)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_comments_log_user ON comments_log(user_id)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_comments_log_week ON comments_log(week_number)')
            
            conn.commit()

    @contextmanager
    def _get_conn_sync(self):
        """Синхронный контекстный менеджер соединения"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    async def _execute(self, query: str, params: tuple = (), fetch_one: bool = False,
                       fetch_all: bool = False, commit: bool = True) -> Any:
        """Асинхронное выполнение запроса"""
        loop = asyncio.get_event_loop()

        def sync_execute():
            with self._get_conn_sync() as conn:
                cur = conn.cursor()
                cur.execute(query, params)
                if commit:
                    conn.commit()
                if fetch_one:
                    return cur.fetchone()
                if fetch_all:
                    return cur.fetchall()
                return None

        return await loop.run_in_executor(self.executor, sync_execute)

    async def _execute_many(self, queries: List[tuple]) -> None:
        """Асинхронное выполнение нескольких запросов в транзакции"""
        loop = asyncio.get_event_loop()

        def sync_execute_many():
            with self._get_conn_sync() as conn:
                cur = conn.cursor()
                for query, params in queries:
                    cur.execute(query, params)
                conn.commit()

        await loop.run_in_executor(self.executor, sync_execute_many)

    # ===== Пользователи =====

    async def get_user(self, user_id: int) -> Optional[Dict]:
        """Получить пользователя"""
        row = await self._execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,),
            fetch_one=True
        )
        return dict(row) if row else None

    async def create_user(self, user_id: int, username: str, first_name: str, last_name: str) -> None:
        """Создать пользователя"""
        now = datetime.now()
        is_admin = user_id in Config.ADMIN_IDS

        await self._execute('''
            INSERT OR IGNORE INTO users
            (user_id, username, first_name, last_name, registration_date, last_activity, is_admin, is_blocked, is_permanently_banned)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name, now, now, is_admin, True, False), commit=True)

    async def update_user_activity(self, user_id: int) -> None:
        """Обновить активность"""
        await self._execute(
            "UPDATE users SET last_activity = ? WHERE user_id = ?",
            (datetime.now(), user_id),
            commit=True
        )

    async def set_accepted_rules(self, user_id: int) -> None:
        """Принять правила"""
        await self._execute(
            "UPDATE users SET accepted_rules = 1 WHERE user_id = ?",
            (user_id,),
            commit=True
        )

    async def set_user_blocked(self, user_id: int, blocked: bool = True) -> None:
        """Временная блокировка/разблокировка"""
        await self._execute(
            "UPDATE users SET is_blocked = ? WHERE user_id = ?",
            (blocked, user_id),
            commit=True
        )

    async def ban_user_permanently(self, user_id: int) -> None:
        """Пожизненный бан пользователя"""
        await self._execute(
            "UPDATE users SET is_permanently_banned = 1, is_blocked = 1 WHERE user_id = ?",
            (user_id,),
            commit=True
        )

    async def is_permanently_banned(self, user_id: int) -> bool:
        """Проверить, забанен ли пользователь навсегда"""
        user = await self.get_user(user_id)
        return user and user['is_permanently_banned']

    async def update_user_admin_status(self, user_id: int, is_admin: bool) -> None:
        """Обновить статус админа"""
        await self._execute(
            "UPDATE users SET is_admin = ? WHERE user_id = ?",
            (is_admin, user_id),
            commit=True
        )

    # ===== Комментарии и фото =====

    async def check_photo_hash(self, photo_hash: str) -> bool:
        """Проверить, использовался ли хэш фото ранее"""
        row = await self._execute(
            "SELECT id FROM used_photos WHERE photo_hash = ?",
            (photo_hash,),
            fetch_one=True
        )
        return row is not None

    async def save_photo_hash(self, user_id: int, photo_hash: str) -> None:
        """Сохранить хэш фото"""
        await self._execute(
            "INSERT INTO used_photos (user_id, photo_hash, timestamp) VALUES (?, ?, ?)",
            (user_id, photo_hash, datetime.now()),
            commit=True
        )

    async def add_comment(self, user_id: int) -> int:
        """Добавить комментарий (увеличить баланс)"""
        now = datetime.now()
        week = now.isocalendar()[1]
        month = now.month

        queries = [
            ('''
                UPDATE users
                SET comment_balance = comment_balance + 1,
                    total_comments_ever = total_comments_ever + 1
                WHERE user_id = ?
            ''', (user_id,)),
            ('''
                INSERT INTO comments_log (user_id, timestamp, week_number, month_number)
                VALUES (?, ?, ?, ?)
            ''', (user_id, now, week, month))
        ]

        await self._execute_many(queries)

        # Получаем новый баланс
        row = await self._execute(
            "SELECT comment_balance FROM users WHERE user_id = ?",
            (user_id,),
            fetch_one=True
        )
        new_balance = row[0] if row else 0

        # Обновляем временную блокировку (если баланс ниже порога)
        new_blocked = new_balance < Config.COMMENT_THRESHOLD
        await self._execute(
            "UPDATE users SET is_blocked = ? WHERE user_id = ?",
            (new_blocked, user_id),
            commit=True
        )

        return new_balance

    async def get_comment_balance(self, user_id: int) -> int:
        """Получить баланс комментариев"""
        user = await self.get_user(user_id)
        return user['comment_balance'] if user else 0

    # ===== Деньги =====

    async def add_money(self, user_id: int, amount: int) -> None:
        """Начислить деньги"""
        await self._execute(
            "UPDATE users SET money_balance = money_balance + ? WHERE user_id = ?",
            (amount, user_id),
            commit=True
        )

    async def deduct_money(self, user_id: int, amount: int) -> None:
        """Списать деньги"""
        await self._execute(
            "UPDATE users SET money_balance = money_balance - ? WHERE user_id = ?",
            (amount, user_id),
            commit=True
        )

    async def get_money_balance(self, user_id: int) -> int:
        """Получить денежный баланс"""
        user = await self.get_user(user_id)
        return user['money_balance'] if user else 0

    async def increment_tasks_completed(self, user_id: int, reward: int) -> None:
        """Увеличить счетчик заданий"""
        await self._execute('''
            UPDATE users
            SET tasks_completed = tasks_completed + 1,
                money_balance = money_balance + ?,
                last_task_date = ?
            WHERE user_id = ?
        ''', (reward, datetime.now(), user_id), commit=True)

    # ===== Выводы =====

    async def create_withdrawal(self, user_id: int, amount: int, method: str, details: str) -> None:
        """Создать заявку на вывод"""
        await self._execute('''
            INSERT INTO withdrawals (user_id, amount, method, details, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, amount, method, details, datetime.now()), commit=True)

    async def get_pending_withdrawals(self) -> List[Dict]:
        """Получить ожидающие заявки"""
        rows = await self._execute(
            "SELECT * FROM withdrawals WHERE status = 'pending' ORDER BY created_at",
            fetch_all=True
        )
        return [dict(row) for row in rows] if rows else []

    async def get_withdrawal(self, withdrawal_id: int) -> Optional[Dict]:
        """Получить заявку по ID"""
        row = await self._execute(
            "SELECT * FROM withdrawals WHERE id = ?",
            (withdrawal_id,),
            fetch_one=True
        )
        return dict(row) if row else None

    async def update_withdrawal_status(self, withdrawal_id: int, status: str,
                                       reject_reason: str = None) -> None:
        """Обновить статус заявки"""
        if reject_reason:
            await self._execute('''
                UPDATE withdrawals
                SET status = ?, processed_at = ?, reject_reason = ?
                WHERE id = ?
            ''', (status, datetime.now(), reject_reason, withdrawal_id), commit=True)
        else:
            await self._execute('''
                UPDATE withdrawals
                SET status = ?, processed_at = ?
                WHERE id = ?
            ''', (status, datetime.now(), withdrawal_id), commit=True)

    # ===== Статистика =====

    async def get_total_users(self) -> int:
        """Всего пользователей (не забаненных навсегда)"""
        row = await self._execute(
            "SELECT COUNT(*) FROM users WHERE is_permanently_banned = 0",
            fetch_one=True
        )
        return row[0] if row else 0

    async def get_active_users(self) -> int:
        """Активных пользователей"""
        row = await self._execute(
            "SELECT COUNT(*) FROM users WHERE comment_balance >= ? AND is_permanently_banned = 0",
            (Config.COMMENT_THRESHOLD,),
            fetch_one=True
        )
        return row[0] if row else 0

    async def get_blocked_users(self) -> int:
        """Временно заблокированных"""
        row = await self._execute(
            "SELECT COUNT(*) FROM users WHERE is_blocked = 1 AND is_permanently_banned = 0",
            fetch_one=True
        )
        return row[0] if row else 0

    async def get_permanently_banned_users(self) -> int:
        """Пожизненно забаненных"""
        row = await self._execute(
            "SELECT COUNT(*) FROM users WHERE is_permanently_banned = 1",
            fetch_one=True
        )
        return row[0] if row else 0

    async def get_total_unique_photos(self) -> int:
        """Всего уникальных фото"""
        row = await self._execute("SELECT COUNT(*) FROM used_photos", fetch_one=True)
        return row[0] if row else 0

    async def get_withdrawal_stats(self) -> Dict:
        """Статистика по заявкам"""
        rows = await self._execute(
            "SELECT status, COUNT(*) FROM withdrawals GROUP BY status",
            fetch_all=True
        )
        return {row[0]: row[1] for row in rows} if rows else {}

    async def get_top_comment_balance(self, limit: int = 10) -> List[Tuple]:
        """Топ по комментариям"""
        rows = await self._execute('''
            SELECT user_id, comment_balance, username, first_name, last_name
            FROM users
            WHERE is_permanently_banned = 0
            ORDER BY comment_balance DESC
            LIMIT ?
        ''', (limit,), fetch_all=True)
        return rows or []

    async def get_top_tasks_completed(self, limit: int = 10) -> List[Tuple]:
        """Топ по заданиям"""
        rows = await self._execute('''
            SELECT user_id, tasks_completed, username, first_name, last_name
            FROM users
            WHERE is_permanently_banned = 0
            ORDER BY tasks_completed DESC
            LIMIT ?
        ''', (limit,), fetch_all=True)
        return rows or []

    async def get_all_user_ids(self) -> List[int]:
        """Все ID пользователей, не забаненных навсегда"""
        rows = await self._execute(
            "SELECT user_id FROM users WHERE accepted_rules = 1 AND is_permanently_banned = 0",
            fetch_all=True
        )
        return [row[0] for row in rows] if rows else []

    async def get_users_for_broadcast(self, target_type: str, count: int = 0) -> List[int]:
        """Получить пользователей для рассылки"""
        base_condition = "accepted_rules = 1 AND is_permanently_banned = 0"
        if target_type == 'all':
            rows = await self._execute(
                f"SELECT user_id FROM users WHERE {base_condition}",
                fetch_all=True
            )
        elif target_type == 'top_active':
            rows = await self._execute(f'''
                SELECT user_id FROM users
                WHERE {base_condition}
                ORDER BY tasks_completed DESC
                LIMIT ?
            ''', (count,), fetch_all=True)
        elif target_type == 'top_inactive':
            rows = await self._execute(f'''
                SELECT user_id FROM users
                WHERE {base_condition}
                ORDER BY tasks_completed ASC, last_activity ASC
                LIMIT ?
            ''', (count,), fetch_all=True)
        elif target_type == 'random':
            rows = await self._execute(f'''
                SELECT user_id FROM users
                WHERE {base_condition}
                ORDER BY RANDOM()
                LIMIT ?
            ''', (count,), fetch_all=True)
        elif target_type == 'blocked':
            rows = await self._execute(f'''
                SELECT user_id FROM users
                WHERE {base_condition} AND is_blocked = 1
            ''', fetch_all=True)
        elif target_type == 'unblocked':
            rows = await self._execute(f'''
                SELECT user_id FROM users
                WHERE {base_condition} AND is_blocked = 0
            ''', fetch_all=True)
        else:
            return []

        return [row[0] for row in rows] if rows else []

    async def search_users(self, query: str, limit: int = 20) -> List[Dict]:
        """Поиск пользователей"""
        if query.isdigit():
            rows = await self._execute('''
                SELECT * FROM users
                WHERE user_id = ?
                OR username LIKE ?
                OR first_name LIKE ?
                OR last_name LIKE ?
                LIMIT ?
            ''', (int(query), f'%{query}%', f'%{query}%', f'%{query}%', limit), fetch_all=True)
        else:
            rows = await self._execute('''
                SELECT * FROM users
                WHERE username LIKE ?
                OR first_name LIKE ?
                OR last_name LIKE ?
                LIMIT ?
            ''', (f'%{query}%', f'%{query}%', f'%{query}%', limit), fetch_all=True)

        return [dict(row) for row in rows] if rows else []

    async def weekly_decrement_comments(self) -> List[Tuple[int, int]]:
        """Еженедельное списание"""
        rows = await self._execute(
            "SELECT user_id, comment_balance FROM users WHERE is_permanently_banned = 0",
            fetch_all=True
        )

        newly_blocked = []
        threshold = Config.COMMENT_THRESHOLD
        decrement = Config.WEEKLY_COMMENT_DECREMENT

        queries = []
        for user_id, balance in rows:
            new_balance = max(0, balance - decrement)
            queries.append((
                "UPDATE users SET comment_balance = ? WHERE user_id = ?",
                (new_balance, user_id)
            ))

            new_blocked = new_balance < threshold
            queries.append((
                "UPDATE users SET is_blocked = ? WHERE user_id = ?",
                (new_blocked, user_id)
            ))

            if new_blocked and balance >= threshold:
                newly_blocked.append((user_id, new_balance))

        if queries:
            await self._execute_many(queries)

        return newly_blocked

# ==================== ЛОГГЕР ====================

class Logger:
    """Логгер"""
    def __init__(self, log_file: str):
        self.logger = logging.getLogger('RudepsBot')
        self.logger.setLevel(logging.INFO)

        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def info(self, msg: str):
        self.logger.info(msg)

    def error(self, msg: str):
        self.logger.error(msg)

    def warning(self, msg: str):
        self.logger.warning(msg)

    def critical(self, msg: str):
        self.logger.critical(msg)

# ==================== УТИЛИТЫ ====================

class UserStateManager:
    """Менеджер состояний"""
    def __init__(self):
        self._states: Dict[int, Dict] = {}
        self._lock = asyncio.Lock()

    async def set_state(self, user_id: int, state: UserState, **data):
        async with self._lock:
            self._states[user_id] = {'state': state, 'data': data}

    async def get_state(self, user_id: int) -> Optional[UserState]:
        async with self._lock:
            if user_id in self._states:
                return self._states[user_id]['state']
            return None

    async def get_data(self, user_id: int) -> Dict:
        async with self._lock:
            if user_id in self._states:
                return self._states[user_id]['data'].copy()
            return {}

    async def update_data(self, user_id: int, **data):
        async with self._lock:
            if user_id in self._states:
                self._states[user_id]['data'].update(data)

    async def clear_state(self, user_id: int):
        async with self._lock:
            self._states.pop(user_id, None)

    async def has_state(self, user_id: int, state: Union[UserState, List[UserState]]) -> bool:
        async with self._lock:
            current = self._states.get(user_id, {}).get('state')
            if isinstance(state, list):
                return current in state
            return current == state

class KeyboardFactory:
    """Фабрика клавиатур"""

    @staticmethod
    def main(is_blocked: bool = False, is_banned: bool = False) -> ReplyKeyboardMarkup:
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        if is_banned:
            return markup
        if is_blocked:
            markup.add(KeyboardButton("📝 Проверить комментарий"))
        else:
            markup.add(
                KeyboardButton("📝 Проверить комментарий"),
                KeyboardButton("💰 Мой баланс"),
                KeyboardButton("💎 Вывод средств"),
                KeyboardButton("📊 Статистика"),
                KeyboardButton("❓ Помощь")
            )
        return markup

    @staticmethod
    def admin() -> ReplyKeyboardMarkup:
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(
            KeyboardButton("👥 Рассылка"),
            KeyboardButton("💰 Управление балансами"),
            KeyboardButton("📊 Статистика"),
            KeyboardButton("📤 Экспорт ID"),
            KeyboardButton("🔧 Тикеты на выплату"),
            KeyboardButton("🔙 Назад в меню")
        )
        return markup

    @staticmethod
    def cancel() -> ReplyKeyboardMarkup:
        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(KeyboardButton("❌ Отмена"))
        return markup

# ==================== ПЛАНИРОВЩИК ====================

class Scheduler:
    """Планировщик задач"""
    def __init__(self, bot: Bot, db: Database, logger: Logger):
        self.bot = bot
        self.db = db
        self.logger = logger
        self._running = False

    async def start(self):
        self._running = True
        aioschedule.every().monday.at(Config.SCHEDULE_TIME).do(self.weekly_check)

        while self._running:
            await aioschedule.run_pending()
            await asyncio.sleep(60)

    async def stop(self):
        self._running = False

    async def weekly_check(self):
        self.logger.info("Запуск еженедельного списания")
        blocked_users = await self.db.weekly_decrement_comments()

        tasks = []
        for user_id, new_balance in blocked_users:
            tasks.append(self._notify_user(user_id, new_balance))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self.logger.info(f"Списание завершено. Заблокировано: {len(blocked_users)}")

    async def _notify_user(self, user_id: int, new_balance: int):
        try:
            await self.bot.send_message(
                user_id,
                f"⛔ *ВНИМАНИЕ: доступ заблокирован!*\n\n"
                f"Произошло еженедельное списание {Config.WEEKLY_COMMENT_DECREMENT} комментариев.\n"
                f"Ваш баланс стал {new_balance}.\n\n"
                f"Чтобы разблокировать доступ, наберите {Config.COMMENT_THRESHOLD} комментариев "
                f"через кнопку '📝 Проверить комментарий'.",
                reply_markup=KeyboardFactory.main(True)
            )
        except Exception as e:
            self.logger.error(f"Не удалось отправить уведомление {user_id}: {e}")

# ==================== ОБРАБОТЧИКИ ====================

class Handlers:
    """Обработчики команд"""

    def __init__(self, dp: Dispatcher, bot: Bot, db: Database,
                 state_manager: UserStateManager, logger: Logger):
        self.dp = dp
        self.bot = bot
        self.db = db
        self.state_manager = state_manager
        self.logger = logger
        self._last_photo_time: Dict[int, float] = {}

    def register_all(self):
        """Регистрация всех обработчиков"""
        self._register_common()
        self._register_comment()
        self._register_withdraw()
        self._register_admin()

    def _register_common(self):
        """Общие обработчики"""

        @self.dp.message_handler(commands=['start'])
        async def cmd_start(message: types.Message):
            user_id = message.from_user.id
            user = await self.db.get_user(user_id)

            # Проверка на пожизненный бан
            if user and user['is_permanently_banned']:
                await message.reply("⛔ Вы забанены навсегда. Доступ к боту закрыт.")
                return

            if user:
                if user['accepted_rules']:
                    await self.db.update_user_activity(user_id)
                    if user['is_blocked']:
                        await message.reply(
                            "🔒 Доступ заблокирован. Требуется 10 комментариев для разблокировки.",
                            reply_markup=KeyboardFactory.main(True)
                        )
                    else:
                        await self._send_main_menu(message.chat.id, user_id)
                else:
                    await self._show_rules(message.chat.id)
            else:
                await self.db.create_user(
                    user_id,
                    message.from_user.username or "",
                    message.from_user.first_name or "",
                    message.from_user.last_name or ""
                )
                await self._show_rules(message.chat.id)

        @self.dp.message_handler(commands=['admin'])
        async def cmd_admin(message: types.Message):
            user_id = message.from_user.id
            user = await self.db.get_user(user_id)
            if user and user['is_admin']:
                await message.reply("🔧 Админ-панель", reply_markup=KeyboardFactory.admin())
            else:
                await message.reply("У вас нет прав администратора.")

        @self.dp.message_handler(commands=['ban'])
        async def cmd_ban(message: types.Message):
            """Команда для пожизненного бана пользователя (только админ)"""
            user_id = message.from_user.id
            user = await self.db.get_user(user_id)
            if not user or not user['is_admin']:
                return

            args = message.get_args()
            if not args or not args.isdigit():
                await message.reply("Использование: /ban [user_id]")
                return

            target_id = int(args)
            target = await self.db.get_user(target_id)
            if not target:
                await message.reply("Пользователь не найден.")
                return

            if target.get('is_permanently_banned'):
                await message.reply("Пользователь уже забанен.")
                return

            await self.db.ban_user_permanently(target_id)
            await message.reply(f"✅ Пользователь {target_id} забанен навсегда.")

            # Уведомляем пользователя
            try:
                await self.bot.send_message(target_id, "⛔ Вы забанены администратором. Доступ к боту закрыт.")
            except:
                pass

        @self.dp.message_handler(commands=['stats'])
        async def cmd_stats(message: types.Message):
            user_id = message.from_user.id
            user = await self.db.get_user(user_id)

            if not user or user['is_permanently_banned']:
                await message.reply("Статистика недоступна.")
                return

            status = "🔒 Заблокирован" if user['is_blocked'] else "✅ Разблокирован"
            remaining = max(0, Config.COMMENT_THRESHOLD - user['comment_balance']) if user['is_blocked'] else 0

            text = (
                f"📊 *Твоя статистика:*\n"
                f"📅 Регистрация: {user['registration_date']}\n"
                f"💬 Всего комментариев: {user['total_comments_ever']}\n"
                f"📝 Текущий баланс: {user['comment_balance']}\n"
                f"🔒 Статус: {status}\n"
            )

            if user['is_blocked']:
                text += f"⏳ Осталось: {remaining}\n"

            text += f"✅ Заданий: {user['tasks_completed']}\n💰 Денег: {user['money_balance']} руб."

            await message.reply(text, parse_mode=ParseMode.MARKDOWN)

        @self.dp.message_handler(commands=['help'])
        async def cmd_help(message: types.Message):
            await self._send_help(message)

        @self.dp.message_handler(lambda m: m.text in [
            "📝 Проверить комментарий", "💰 Мой баланс", "💎 Вывод средств",
            "📊 Статистика", "❓ Помощь"
        ])
        async def handle_menu_buttons(message: types.Message):
            user_id = message.from_user.id
            user = await self.db.get_user(user_id)

            if not user or not user['accepted_rules']:
                await message.reply("Пожалуйста, используйте /start для начала.")
                return

            # Проверка на пожизненный бан
            if user['is_permanently_banned']:
                await message.reply("⛔ Вы забанены навсегда. Доступ к боту закрыт.")
                return

            await self.db.update_user_activity(user_id)

            if user['is_blocked'] and message.text != "📝 Проверить комментарий":
                remaining = max(0, Config.COMMENT_THRESHOLD - user['comment_balance'])
                await message.reply(
                    f"⛔ Доступ временно заблокирован. Требуется {Config.COMMENT_THRESHOLD} комментариев.\n"
                    f"📝 Баланс: {user['comment_balance']}\n⏳ Осталось: {remaining}",
                    reply_markup=KeyboardFactory.main(True)
                )
                return

            if message.text == "📝 Проверить комментарий":
                await self._handle_check_comment(message)
            elif message.text == "💰 Мой баланс":
                await self._show_balance(message)
            elif message.text == "💎 Вывод средств":
                await self._start_withdrawal(message)
            elif message.text == "📊 Статистика":
                await cmd_stats(message)
            elif message.text == "❓ Помощь":
                await self._send_help(message)

        @self.dp.callback_query_handler(lambda c: c.data == "accept_rules")
        async def accept_rules(call: types.CallbackQuery):
            user_id = call.from_user.id
            await self.db.set_accepted_rules(user_id)
            await call.answer("Правила приняты!")
            await call.message.delete()
            user = await self.db.get_user(user_id)
            if user['is_blocked']:
                await call.message.answer(
                    "🔒 Доступ заблокирован. Требуется 10 комментариев для разблокировки.",
                    reply_markup=KeyboardFactory.main(True)
                )
            else:
                await self._send_main_menu(call.message.chat.id, user_id)

        @self.dp.callback_query_handler(lambda c: c.data == "reject_rules")
        async def reject_rules(call: types.CallbackQuery):
            await call.answer("Вы не приняли правила. Бот не будет работать.")
            await call.message.delete()
            await call.message.answer("❌ Вы отказались от правил. Для начала работы используйте /start.")

    def _register_comment(self):
        """Обработчики комментариев"""

        @self.dp.message_handler(lambda m: m.text == "❌ Отмена")
        async def cancel_photo(message: types.Message):
            if await self.state_manager.has_state(message.from_user.id, UserState.WAITING_PHOTO):
                await self.state_manager.clear_state(message.from_user.id)
                user = await self.db.get_user(message.from_user.id)
                banned = user['is_permanently_banned'] if user else False
                blocked = user['is_blocked'] if user else True
                await message.reply(
                    "❌ Отправка фото отменена. Возврат в главное меню.",
                    reply_markup=KeyboardFactory.main(blocked, banned)
                )

        @self.dp.message_handler(content_types=['photo'])
        async def photo_message(message: types.Message):
            await self._handle_photo(message)

        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, UserState.WAITING_PHOTO)))
        async def unexpected_message(message: types.Message):
            await message.reply(
                "❌ Пожалуйста, отправьте ФОТО (изображение).\n\n"
                "Для отмены нажмите кнопку '❌ Отмена' в меню."
            )

    def _register_withdraw(self):
        """Обработчики вывода"""
        @self.dp.callback_query_handler(lambda c: c.data.startswith("withdraw_"))
        async def withdraw_method(call: types.CallbackQuery):
            if await self.db.is_permanently_banned(call.from_user.id):
                await call.answer("Вы забанены навсегда.")
                return
            await self._callback_withdraw_method(call)

        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, UserState.WAITING_WITHDRAW_AMOUNT)))
        async def withdraw_amount(message: types.Message):
            if await self.db.is_permanently_banned(message.from_user.id):
                await message.reply("Вы забанены навсегда.")
                return
            await self._handle_withdraw_amount(message)

        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, UserState.WAITING_WITHDRAW_DETAILS)))
        async def withdraw_details(message: types.Message):
            if await self.db.is_permanently_banned(message.from_user.id):
                await message.reply("Вы забанены навсегда.")
                return
            await self._handle_withdraw_details(message)

    def _register_admin(self):
        """Админ-панель"""
        @self.dp.message_handler(lambda m: m.text in [
            "👥 Рассылка", "💰 Управление балансами", "📊 Статистика",
            "📤 Экспорт ID", "🔧 Тикеты на выплату", "🔙 Назад в меню"
        ])
        async def handle_admin_buttons(message: types.Message):
            user_id = message.from_user.id
            user = await self.db.get_user(user_id)
            if not user or not user['is_admin']:
                return

            if message.text == "👥 Рассылка":
                await self._start_broadcast(message)
            elif message.text == "💰 Управление балансами":
                await self._start_balance_management(message)
            elif message.text == "📊 Статистика":
                await self._show_admin_stats(message)
            elif message.text == "📤 Экспорт ID":
                await self._export_user_ids(message)
            elif message.text == "🔧 Тикеты на выплату":
                await self._show_pending_withdrawals(message)
            elif message.text == "🔙 Назад в меню":
                await self._send_main_menu(message.chat.id, user_id)

        # Обработчики состояний рассылки
        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, UserState.BROADCAST_TARGET_TYPE)))
        async def handle_broadcast_target_type(message: types.Message):
            await self._handle_broadcast_target_type(message)

        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, UserState.BROADCAST_COUNT)))
        async def handle_broadcast_count(message: types.Message):
            await self._handle_broadcast_count(message)

        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, UserState.BROADCAST_SORT)))
        async def handle_broadcast_sort(message: types.Message):
            await self._handle_broadcast_sort(message)

        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, UserState.BROADCAST_TEXT)))
        async def handle_broadcast_text(message: types.Message):
            await self._handle_broadcast_text(message)

        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, UserState.BROADCAST_LINK)))
        async def handle_broadcast_link(message: types.Message):
            await self._handle_broadcast_link(message)

        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, UserState.BROADCAST_REWARD)))
        async def handle_broadcast_reward(message: types.Message):
            await self._handle_broadcast_reward(message)

        @self.dp.callback_query_handler(lambda c: c.data.startswith('complete_'))
        async def callback_complete_task(call: types.CallbackQuery):
            await self._callback_complete_task(call)

        # Обработчики управления балансами
        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, UserState.MANAGE_BALANCES_SEARCH)))
        async def handle_balance_search(message: types.Message):
            await self._handle_balance_search(message)

        @self.dp.callback_query_handler(lambda c: c.data.startswith('mod_'))
        async def callback_balance_modification(call: types.CallbackQuery):
            await self._callback_balance_modification(call)

        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, [
                UserState.MANAGE_BALANCES_ACTIONS,
                UserState.MANAGE_BALANCES_SEARCH
            ])))
        async def handle_balance_change(message: types.Message):
            await self._handle_balance_change(message)

        # Обработчики заявок на вывод
        @self.dp.callback_query_handler(lambda c: c.data.startswith(('approve_', 'reject_')))
        async def callback_withdrawal_action(call: types.CallbackQuery):
            await self._callback_withdrawal_action(call)

        @self.dp.message_handler(lambda m:
            asyncio.run(self.state_manager.has_state(m.from_user.id, UserState.WAITING_REJECT_REASON)))
        async def handle_reject_reason(message: types.Message):
            await self._handle_reject_reason(message)

    # ===== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ =====

    async def _send_main_menu(self, chat_id: int, user_id: int):
        user = await self.db.get_user(user_id)
        if not user:
            return
        banned = user['is_permanently_banned']
        blocked = user['is_blocked']

        if banned:
            await self.bot.send_message(chat_id, "⛔ Вы забанены навсегда.")
            return

        if blocked:
            remaining = max(0, Config.COMMENT_THRESHOLD - user['comment_balance'])
            text = (
                f"🔒 *Доступ заблокирован*\n\n"
                f"📝 Текущий баланс: {user['comment_balance']}\n"
                f"⏳ Осталось для разблокировки: {remaining}\n\n"
                f"Отправляйте скриншоты с упоминанием @{Config.BOT_NAME} чтобы получить комментарии!"
            )
            await self.bot.send_message(chat_id, text, reply_markup=KeyboardFactory.main(True))
        else:
            await self.bot.send_message(chat_id, "Главное меню:", reply_markup=KeyboardFactory.main())

    async def _show_rules(self, chat_id: int):
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✅ Принимаю", callback_data="accept_rules"),
            InlineKeyboardButton("❌ Отказываюсь", callback_data="reject_rules")
        )

        text = (
            f"🤖 *Добро пожаловать в RudepsBot!*\n\n"
            f"📱 *Что умеет бот:*\n"
            f"• Проверка комментариев с упоминанием @{Config.BOT_NAME}\n"
            f"• Накопление комментариев для доступа\n"
            f"• Выполнение заданий с наградой\n"
            f"• Вывод заработанных средств\n\n"
            f"💰 *Примерные заработки:*\n"
            f"• За каждое задание: от 5 до 50₽\n"
            f"• В среднем: 500-1500₽ в неделю\n\n"
            f"📊 *Система комментариев:*\n"
            f"• Для разблокировки нужно {Config.COMMENT_THRESHOLD} комментариев\n"
            f"• Каждый понедельник списывается {Config.WEEKLY_COMMENT_DECREMENT} комментариев\n"
            f"• Если баланс станет 0 - доступ блокируется\n\n"
            f"💳 *Вывод средств:*\n"
            f"• На карту: от {Config.MIN_WITHDRAW_CARD}₽\n"
            f"• На телефон: от {Config.MIN_WITHDRAW_PHONE}₽\n\n"
            f"⚠️ *За обман - пожизненный бан!*\n\n"
            f"❗️ Важно: любое отправленное фото сразу дает +1 комментарий, но если админ заметит обман - вы будете забанены навсегда."
        )

        await self.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    async def _show_balance(self, message: types.Message):
        user = await self.db.get_user(message.from_user.id)
        if not user:
            return
        status = "🔒 Заблокирован" if user['is_blocked'] else "✅ Разблокирован"
        remaining = max(0, Config.COMMENT_THRESHOLD - user['comment_balance']) if user['is_blocked'] else 0

        text = (
            f"💰 *Твой баланс:*\n"
            f"📝 Комментариев: {user['comment_balance']}\n"
            f"🔒 Статус: {status}\n"
        )
        if user['is_blocked']:
            text += f"⏳ До разблокировки: {remaining}\n"
        text += f"💵 Денег: {user['money_balance']} руб.\n✅ Всего выполнено заданий: {user['tasks_completed']}"

        await message.reply(text, parse_mode=ParseMode.MARKDOWN)

    async def _send_help(self, message: types.Message):
        help_text = (
            f"❓ *Помощь по боту {Config.BOT_NAME}:*\n\n"
            f"📝 *Проверить комментарий* — отправьте скриншот комментария с упоминанием @{Config.BOT_NAME}, "
            f"чтобы получить +1 к балансу комментариев.\n"
            f"💰 *Мой баланс* — показывает текущие балансы и статус доступа.\n"
            f"💎 *Вывод средств* — создайте заявку на вывод денег "
            f"(минимум {Config.MIN_WITHDRAW_CARD}₽ на карту, {Config.MIN_WITHDRAW_PHONE}₽ на телефон).\n"
            f"📊 *Статистика* — ваша личная статистика.\n"
            f"❓ *Помощь* — это сообщение.\n\n"
            f"🔒 *Система блокировки:*\n"
            f"• Для разблокировки нужно {Config.COMMENT_THRESHOLD} комментариев\n"
            f"• Каждый понедельник списывается {Config.WEEKLY_COMMENT_DECREMENT} комментариев\n"
            f"• Если баланс станет 0 - доступ блокируется\n\n"
            f"⚠️ *Важно:* За любое фото сразу начисляется +1 комментарий, но за обман - пожизненный бан!"
        )
        await message.reply(help_text, parse_mode=ParseMode.MARKDOWN)

    # ===== ОСНОВНАЯ ЛОГИКА ФОТО =====

    async def _handle_check_comment(self, message: types.Message):
        user_id = message.from_user.id

        # Антифлуд
        now = time.time()
        last = self._last_photo_time.get(user_id, 0)
        if now - last < Config.ANTIFLOOD_SECONDS:
            remaining = int(Config.ANTIFLOOD_SECONDS - (now - last))
            await message.reply(f"⏳ Слишком часто. Подождите {remaining} секунд.")
            return

        await self.state_manager.set_state(user_id, UserState.WAITING_PHOTO)

        await message.reply(
            f"📸 Отправьте скриншот вашего комментария, содержащего упоминание @{Config.BOT_NAME}.\n\n"
            f"⚠️ *ВАЖНО:* За любое фото сразу начисляется +1 комментарий!\n"
            f"Если админ заметит обман (повторные фото, не те комментарии) - вы будете забанены навсегда.\n\n"
            f"Требования к фото:\n"
            f"• Формат: JPG, PNG\n"
            f"• Максимальный размер: {Config.MAX_PHOTO_SIZE_MB} MB\n\n"
            f"Для отмены нажмите кнопку '❌ Отмена'",
            reply_markup=KeyboardFactory.cancel()
        )

    async def _handle_photo(self, message: types.Message):
        user_id = message.from_user.id

        # Проверка бана
        if await self.db.is_permanently_banned(user_id):
            await message.reply("⛔ Вы забанены навсегда. Доступ к боту закрыт.")
            return

        # Проверка состояния
        if not await self.state_manager.has_state(user_id, UserState.WAITING_PHOTO):
            await message.reply(
                "❌ Сначала нажмите кнопку '📝 Проверить комментарий' в меню.",
                reply_markup=KeyboardFactory.main(await self.db.get_user(user_id)['is_blocked'])
            )
            return

        await self.state_manager.clear_state(user_id)

        # Антифлуд
        now = time.time()
        last = self._last_photo_time.get(user_id, 0)
        if now - last < Config.ANTIFLOOD_SECONDS:
            remaining = int(Config.ANTIFLOOD_SECONDS - (now - last))
            await message.reply(
                f"⏳ Слишком часто. Подождите {remaining} секунд.",
                reply_markup=KeyboardFactory.main(await self.db.get_user(user_id)['is_blocked'])
            )
            return
        self._last_photo_time[user_id] = now

        # Проверка фото
        if not message.photo:
            await message.reply(
                "❌ Ошибка: фото не обнаружено.",
                reply_markup=KeyboardFactory.main(await self.db.get_user(user_id)['is_blocked'])
            )
            return

        photo = message.photo[-1]
        if photo.file_size > Config.MAX_PHOTO_SIZE:
            await message.reply(
                f"❌ Файл слишком большой. Максимальный размер: {Config.MAX_PHOTO_SIZE_MB} MB.",
                reply_markup=KeyboardFactory.main(await self.db.get_user(user_id)['is_blocked'])
            )
            return

        processing_msg = await message.reply("⏳ Обрабатываю фото, пожалуйста, подождите...")

        # Скачиваем фото
        try:
            file_info = await self.bot.get_file(photo.file_id)
            downloaded = await self.bot.download_file(file_info.file_path)
            data = downloaded.getvalue()
        except Exception as e:
            self.logger.error(f"Ошибка скачивания файла: {e}")
            await processing_msg.edit_text("❌ Ошибка при скачивании файла.")
            return

        # Хэш для защиты от повторов
        photo_hash = hashlib.sha256(data).hexdigest()
        if await self.db.check_photo_hash(photo_hash):
            await processing_msg.edit_text("❌ Этот скриншот уже использовался ранее.")
            return

        # Сохраняем хэш
        await self.db.save_photo_hash(user_id, photo_hash)

        # НАЧИСЛЯЕМ КОММЕНТАРИЙ СРАЗУ (без проверки)
        new_balance = await self.db.add_comment(user_id)

        # Получаем данные пользователя для лога
        user = await self.db.get_user(user_id)
        username = user.get('username') or f"{user['first_name']} {user['last_name']}".strip() or "Неизвестно"

        # Формируем сообщение для админа
        log_text = (
            f"📸 *НОВОЕ ФОТО (начислен комментарий)*\n"
            f"👤 Пользователь: {username}\n"
            f"🆔 ID: {user_id}\n"
            f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📝 Новый баланс комментариев: {new_balance}\n"
            f"💰 Денег: {user['money_balance']} руб.\n"
            f"🔒 Статус: {'Заблокирован' if user['is_blocked'] else 'Разблокирован'}"
        )

        # Отправляем фото админу с логом (БЕЗ КНОПОК - только для информации)
        for admin_id in Config.ADMIN_IDS:
            try:
                await self.bot.send_photo(admin_id, photo.file_id, caption=log_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                self.logger.error(f"Не удалось отправить фото админу {admin_id}: {e}")

        # Ответ пользователю
        if user['is_blocked']:
            remaining = Config.COMMENT_THRESHOLD - new_balance
            await processing_msg.edit_text(
                f"✅ Комментарий засчитан!\n\n"
                f"📝 Текущий баланс: {new_balance}\n"
                f"🔒 СТАТУС: ЗАБЛОКИРОВАН\n"
                f"⏳ Осталось до разблокировки: {remaining}"
            )
        else:
            await processing_msg.edit_text(
                f"✅ Комментарий засчитан!\n\n"
                f"📝 Текущий баланс: {new_balance}\n"
                f"🎉 СТАТУС: РАЗБЛОКИРОВАН\n"
                f"💫 Теперь вам доступны все функции бота!"
            )

        # Обновляем меню
        await self._send_main_menu(message.chat.id, user_id)

    # ===== МЕТОДЫ ДЛЯ ВЫВОДА =====

    async def _start_withdrawal(self, message: types.Message):
        """Начало процесса вывода"""
        user_id = message.from_user.id
        money = await self.db.get_money_balance(user_id)

        if money < Config.MIN_WITHDRAW_CARD:
            await message.reply(
                f"💤 Минимальная сумма вывода — {Config.MIN_WITHDRAW_CARD}₽. Твой баланс: {money}₽"
            )
            return

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("💳 На карту", callback_data="withdraw_card"),
            InlineKeyboardButton("📱 На телефон", callback_data="withdraw_phone")
        )
        await message.reply("Выберите способ вывода:", reply_markup=markup)

    async def _callback_withdraw_method(self, call: types.CallbackQuery):
        """Обработка выбора метода вывода"""
        user_id = call.from_user.id
        method = call.data.split('_')[1]

        await self.state_manager.set_state(user_id, UserState.WAITING_WITHDRAW_AMOUNT, method=method)
        await call.answer()

        min_amount = Config.MIN_WITHDRAW_CARD if method == 'card' else Config.MIN_WITHDRAW_PHONE
        await call.message.reply(
            f"Введите сумму для вывода (минимум {min_amount}₽, целое число):"
        )

    async def _handle_withdraw_amount(self, message: types.Message):
        """Обработка ввода суммы вывода"""
        user_id = message.from_user.id
        data = await self.state_manager.get_data(user_id)
        method = data.get('method')

        try:
            amount = int(message.text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await message.reply("Пожалуйста, введите положительное целое число.")
            return

        min_amount = Config.MIN_WITHDRAW_CARD if method == 'card' else Config.MIN_WITHDRAW_PHONE
        if amount < min_amount:
            await message.reply(f"Сумма должна быть не меньше {min_amount}₽.")
            return

        money = await self.db.get_money_balance(user_id)
        if amount > money:
            await message.reply(f"Недостаточно средств. Ваш баланс: {money}₽.")
            return

        await self.state_manager.update_data(user_id, amount=amount)
        await self.state_manager.set_state(user_id, UserState.WAITING_WITHDRAW_DETAILS, **data)

        if method == 'card':
            await message.reply("Введите номер карты (16 цифр):")
        else:
            await message.reply("Введите номер телефона (в любом формате):")

    async def _handle_withdraw_details(self, message: types.Message):
        """Обработка ввода реквизитов вывода"""
        user_id = message.from_user.id
        data = await self.state_manager.get_data(user_id)
        method = data.get('method')
        amount = data.get('amount')
        details = message.text.strip()

        if method == 'card':
            card = ''.join(filter(str.isdigit, details))
            if len(card) != 16:
                await message.reply("Некорректный номер карты. Введите 16 цифр без пробелов.")
                return
            details = card
        else:
            if not any(c.isdigit() for c in details):
                await message.reply("Пожалуйста, введите номер телефона.")
                return

        await self.db.create_withdrawal(user_id, amount, method, details)
        await self.state_manager.clear_state(user_id)

        await message.reply("✅ Заявка на вывод создана. Ожидайте решения администратора.")

        # Уведомляем админов
        tasks = []
        for admin_id in Config.ADMIN_IDS:
            tasks.append(self.bot.send_message(
                admin_id,
                f"🔔 Новая заявка на вывод!\n"
                f"Пользователь: {user_id}\n"
                f"Сумма: {amount}₽\n"
                f"Способ: {method}"
            ))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ===== МЕТОДЫ ДЛЯ РАССЫЛКИ =====

    async def _start_broadcast(self, message: types.Message):
        """Начало рассылки"""
        user_id = message.from_user.id
        await self.state_manager.set_state(user_id, UserState.BROADCAST_TARGET_TYPE)

        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add("1️⃣ Все пользователи", "2️⃣ Своё количество")
        await message.reply("Выберите тип аудитории:", reply_markup=markup)

    async def _handle_broadcast_target_type(self, message: types.Message):
        """Обработка выбора типа аудитории"""
        user_id = message.from_user.id

        if message.text == "1️⃣ Все пользователи":
            await self.state_manager.update_data(user_id, target_type='all')
            await self.state_manager.set_state(user_id, UserState.BROADCAST_TEXT)
            await message.reply("Введите текст сообщения для рассылки:")
        elif message.text == "2️⃣ Своё количество":
            await self.state_manager.set_state(user_id, UserState.BROADCAST_COUNT)
            await message.reply("Введите количество пользователей для выборки:")
        else:
            await message.reply("Пожалуйста, выберите пункт меню.")

    async def _handle_broadcast_count(self, message: types.Message):
        """Обработка ввода количества"""
        user_id = message.from_user.id

        try:
            count = int(message.text)
            if count <= 0:
                raise ValueError
        except ValueError:
            await message.reply("Введите положительное целое число.")
            return

        await self.state_manager.update_data(user_id, count=count)
        await self.state_manager.set_state(user_id, UserState.BROADCAST_SORT)

        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add("1️⃣ Самые активные", "2️⃣ Самые неактивные", "3️⃣ Случайные")
        await message.reply("Выберите сортировку:", reply_markup=markup)

    async def _handle_broadcast_sort(self, message: types.Message):
        """Обработка выбора сортировки"""
        user_id = message.from_user.id
        text = message.text

        sort_map = {
            "1️⃣ Самые активные": "top_active",
            "2️⃣ Самые неактивные": "top_inactive",
            "3️⃣ Случайные": "random"
        }

        if text not in sort_map:
            await message.reply("Пожалуйста, выберите пункт меню.")
            return

        await self.state_manager.update_data(user_id, target_type=sort_map[text])
        await self.state_manager.set_state(user_id, UserState.BROADCAST_TEXT)
        await message.reply("Введите текст сообщения для рассылки:")

    async def _handle_broadcast_text(self, message: types.Message):
        """Обработка ввода текста рассылки"""
        user_id = message.from_user.id
        await self.state_manager.update_data(user_id, message_text=message.text)
        await self.state_manager.set_state(user_id, UserState.BROADCAST_LINK)
        await message.reply(
            "Введите ссылку для кнопки (или отправьте '-' если ссылки не будет):"
        )

    async def _handle_broadcast_link(self, message: types.Message):
        """Обработка ввода ссылки"""
        user_id = message.from_user.id
        link = message.text if message.text != '-' else None
        await self.state_manager.update_data(user_id, link=link)
        await self.state_manager.set_state(user_id, UserState.BROADCAST_REWARD)
        await message.reply("Введите сумму награды за выполнение задания (целое число рублей):")

    async def _handle_broadcast_reward(self, message: types.Message):
        """Обработка ввода награды и запуск рассылки"""
        user_id = message.from_user.id

        try:
            reward = int(message.text)
            if reward < 0:
                raise ValueError
        except ValueError:
            await message.reply("Введите целое неотрицательное число.")
            return

        data = await self.state_manager.get_data(user_id)
        await self.state_manager.clear_state(user_id)

        # Получаем список пользователей
        if data['target_type'] == 'all':
            user_ids = await self.db.get_users_for_broadcast('all')
        else:
            user_ids = await self.db.get_users_for_broadcast(
                data['target_type'], data.get('count', 0)
            )

        if not user_ids:
            await message.reply("Нет пользователей для рассылки.")
            return

        # Создаем запись о рассылке
        broadcast_id = int(time.time())
        async with self.db._get_conn_sync() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO broadcasts 
                (admin_id, target_type, target_count, message_text, link, reward_amount, sent_count, error_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)
            ''', (
                user_id, data['target_type'], data.get('count', 0),
                data['message_text'], data.get('link'), reward, datetime.now()
            ))
            conn.commit()
            broadcast_db_id = cur.lastrowid

        # Создаем клавиатуру если есть ссылка
        markup = None
        if data.get('link'):
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(
                "✅ Выполнить",
                callback_data=f"complete_{broadcast_db_id}_{reward}"
            ))

        # Отправляем сообщения
        sent = 0
        errors = 0
        error_list = []

        for uid in user_ids:
            try:
                await self.bot.send_message(uid, data['message_text'], reply_markup=markup)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                errors += 1
                error_list.append(str(uid))

        # Обновляем статистику рассылки
        async with self.db._get_conn_sync() as conn:
            cur = conn.cursor()
            cur.execute('''
                UPDATE broadcasts 
                SET sent_count = ?, error_count = ?
                WHERE id = ?
            ''', (sent, errors, broadcast_db_id))
            conn.commit()

        # Отправляем отчет
        await message.reply(
            f"✅ Рассылка завершена.\n"
            f"📨 Отправлено: {sent}\n"
            f"❌ Ошибок: {errors}"
        )

    async def _callback_complete_task(self, call: types.CallbackQuery):
        """Обработка выполнения задания"""
        user_id = call.from_user.id
        parts = call.data.split('_')

        try:
            broadcast_id = int(parts[1])
            reward = int(parts[2])
        except:
            broadcast_id, reward = 0, 0

        # Начисляем награду
        await self.db.increment_tasks_completed(user_id, reward)

        # Получаем ссылку если есть
        link = None
        if broadcast_id:
            async with self.db._get_conn_sync() as conn:
                cur = conn.cursor()
                cur.execute("SELECT link FROM broadcasts WHERE id = ?", (broadcast_id,))
                row = cur.fetchone()
                if row:
                    link = row[0]

        await call.answer("Задание выполнено! Награда начислена.")
        await call.message.reply(f"✅ Спасибо за выполнение! Начислено {reward}₽ на ваш баланс.")

        if link:
            await call.message.reply(f"🔗 Ваша ссылка для перехода: {link}")

    # ===== МЕТОДЫ ДЛЯ УПРАВЛЕНИЯ БАЛАНСАМИ =====

    async def _start_balance_management(self, message: types.Message):
        """Начало управления балансами"""
        user_id = message.from_user.id
        await self.state_manager.set_state(user_id, UserState.MANAGE_BALANCES_SEARCH)
        await message.reply("Введите ID пользователя или username (без @) для поиска:")

    async def _handle_balance_search(self, message: types.Message):
        """Поиск пользователя для управления балансом"""
        admin_id = message.from_user.id
        query = message.text.strip()

        users = await self.db.search_users(query, limit=1)

        if not users:
            await message.reply("Пользователь не найден.")
            await self.state_manager.clear_state(admin_id)
            return

        user = users[0]
        await self.state_manager.set_state(
            admin_id, UserState.MANAGE_BALANCES_ACTIONS, target_user=user
        )

        name = user.get('username') or f"{user['first_name']} {user['last_name']}".strip() or "Неизвестно"
        text = (
            f"👤 Пользователь: {name} (ID: {user['user_id']})\n"
            f"📝 Комментариев: {user['comment_balance']}\n"
            f"💰 Денег: {user['money_balance']} руб.\n"
            f"✅ Заданий выполнено: {user['tasks_completed']}\n"
            f"🔒 Заблокирован: {'Да' if user['is_blocked'] else 'Нет'}\n"
            f"⛔ Забанен навсегда: {'Да' if user['is_permanently_banned'] else 'Нет'}"
        )

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("➕ Начислить комментарии", callback_data="mod_comment_add"),
            InlineKeyboardButton("➖ Списать комментарии", callback_data="mod_comment_sub"),
            InlineKeyboardButton("➕ Начислить деньги", callback_data="mod_money_add"),
            InlineKeyboardButton("➖ Списать деньги", callback_data="mod_money_sub"),
            InlineKeyboardButton("🔙 Завершить", callback_data="mod_finish")
        )

        await message.reply(text, reply_markup=markup)

    async def _callback_balance_modification(self, call: types.CallbackQuery):
        """Обработка выбора действия с балансом"""
        admin_id = call.from_user.id
        data = call.data

        if not await self.state_manager.has_state(admin_id, UserState.MANAGE_BALANCES_ACTIONS):
            await call.answer("Сессия устарела. Начните заново.")
            return

        state_data = await self.state_manager.get_data(admin_id)
        target_user = state_data['target_user']

        if data == 'mod_comment_add':
            await self.state_manager.update_data(admin_id, action='comment_add')
            await call.answer()
            await call.message.reply("Введите количество комментариев для начисления:")
        elif data == 'mod_comment_sub':
            await self.state_manager.update_data(admin_id, action='comment_sub')
            await call.answer()
            await call.message.reply("Введите количество комментариев для списания:")
        elif data == 'mod_money_add':
            await self.state_manager.update_data(admin_id, action='money_add')
            await call.answer()
            await call.message.reply("Введите сумму рублей для начисления:")
        elif data == 'mod_money_sub':
            await self.state_manager.update_data(admin_id, action='money_sub')
            await call.answer()
            await call.message.reply("Введите сумму рублей для списания:")
        elif data == 'mod_finish':
            await self.state_manager.clear_state(admin_id)
            await call.answer("Готово.")
            await call.message.edit_reply_markup(reply_markup=None)
            await self._send_main_menu(call.message.chat.id, admin_id)

    async def _handle_balance_change(self, message: types.Message):
        """Обработка изменения баланса"""
        admin_id = message.from_user.id
        state_data = await self.state_manager.get_data(admin_id)
        target_user = state_data.get('target_user')
        action = state_data.get('action')

        if not target_user or not action:
            await message.reply("Ошибка: данные не найдены.")
            await self.state_manager.clear_state(admin_id)
            return

        try:
            amount = int(message.text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await message.reply("Введите положительное целое число.")
            return

        user_id = target_user['user_id']

        if action == 'comment_add':
            await self.db._execute(
                "UPDATE users SET comment_balance = comment_balance + ? WHERE user_id = ?",
                (amount, user_id),
                commit=True
            )
            await message.reply(f"✅ Начислено {amount} комментариев пользователю {user_id}")
        elif action == 'comment_sub':
            await self.db._execute(
                "UPDATE users SET comment_balance = comment_balance - ? WHERE user_id = ?",
                (amount, user_id),
                commit=True
            )
            await message.reply(f"✅ Списано {amount} комментариев у пользователя {user_id}")
        elif action == 'money_add':
            await self.db.add_money(user_id, amount)
            await message.reply(f"✅ Начислено {amount}₽ пользователю {user_id}")
        elif action == 'money_sub':
            await self.db.deduct_money(user_id, amount)
            await message.reply(f"✅ Списано {amount}₽ у пользователя {user_id}")

        await self.state_manager.clear_state(admin_id)
        await self._start_balance_management(message)

    # ===== СТАТИСТИКА ДЛЯ АДМИНА =====

    async def _show_admin_stats(self, message: types.Message):
        """Показать статистику для админа"""
        total_users = await self.db.get_total_users()
        active = await self.db.get_active_users()
        blocked = await self.db.get_blocked_users()
        permanently_banned = await self.db.get_permanently_banned_users()
        total_photos = await self.db.get_total_unique_photos()
        withdrawal_stats = await self.db.get_withdrawal_stats()
        top_comments = await self.db.get_top_comment_balance(10)
        top_tasks = await self.db.get_top_tasks_completed(10)

        text = (
            f"📊 *Общая статистика:*\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"✅ Активных: {active}\n"
            f"🔒 Временно заблокированных: {blocked}\n"
            f"⛔ Забанено навсегда: {permanently_banned}\n"
            f"📸 Всего уникальных фото: {total_photos}\n"
            f"💳 Заявки на вывод:\n"
            f"  • Ожидают: {withdrawal_stats.get('pending', 0)}\n"
            f"  • Принято: {withdrawal_stats.get('approved', 0)}\n"
            f"  • Отклонено: {withdrawal_stats.get('rejected', 0)}\n\n"
            f"🏆 *Топ-10 по комментариям:*\n"
        )

        for row in top_comments:
            uid, bal, username, fn, ln = row[:5]
            name = f"@{username}" if username else f"{fn} {ln}".strip() or str(uid)
            text += f"{name}: {bal}\n"

        text += "\n🎯 *Топ-10 по заданиям:*\n"
        for row in top_tasks:
            uid, tasks, username, fn, ln = row[:5]
            name = f"@{username}" if username else f"{fn} {ln}".strip() or str(uid)
            text += f"{name}: {tasks}\n"

        await message.reply(text, parse_mode=ParseMode.MARKDOWN)

    async def _export_user_ids(self, message: types.Message):
        """Экспорт ID пользователей"""
        ids = await self.db.get_all_user_ids()
        filename = "user_ids.txt"

        async with aiofiles.open(filename, 'w') as f:
            await f.write('\n'.join(str(uid) for uid in ids))

        with open(filename, 'rb') as f:
            await self.bot.send_document(
                message.chat.id,
                types.InputFile(f),
                caption=f"📤 Экспортировано {len(ids)} ID пользователей"
            )
        os.remove(filename)

    # ===== ЗАЯВКИ НА ВЫВОД =====

    async def _show_pending_withdrawals(self, message: types.Message):
        """Показать ожидающие заявки на вывод"""
        withdrawals = await self.db.get_pending_withdrawals()

        if not withdrawals:
            await message.reply("Нет ожидающих заявок.")
            return

        for w in withdrawals:
            user = await self.db.get_user(w['user_id'])
            name = user.get('username') or f"{user['first_name']} {user['last_name']}".strip() or "Неизвестно"

            text = (
                f"🆔 Заявка #{w['id']}\n"
                f"📅 Дата: {w['created_at']}\n"
                f"👤 Пользователь: {name} (ID: {w['user_id']})\n"
                f"💰 Сумма: {w['amount']} руб.\n"
                f"💳 Способ: {w['method']}\n"
                f"📝 Реквизиты: {w['details']}"
            )

            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(
                InlineKeyboardButton("✅ Принять", callback_data=f"approve_{w['id']}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{w['id']}")
            )

            await message.reply(text, reply_markup=markup)

    async def _callback_withdrawal_action(self, call: types.CallbackQuery):
        """Обработка действий с заявкой на вывод"""
        admin_id = call.from_user.id
        user = await self.db.get_user(admin_id)

        if not user or not user['is_admin']:
            await call.answer("Нет прав.")
            return

        action, withdraw_id = call.data.split('_')
        withdraw_id = int(withdraw_id)

        if action == 'approve':
            w = await self.db.get_withdrawal(withdraw_id)
            if not w:
                await call.answer("Заявка не найдена.")
                return

            await self.db.update_withdrawal_status(withdraw_id, 'approved')
            await self.db.deduct_money(w['user_id'], w['amount'])

            try:
                await self.bot.send_message(
                    w['user_id'],
                    f"✅ Ваша заявка на вывод {w['amount']}₽ принята. Ожидайте поступления в течение часа."
                )
            except Exception as e:
                self.logger.error(f"Не удалось уведомить пользователя {w['user_id']}: {e}")

            await call.answer("Заявка принята.")
            await call.message.edit_reply_markup(reply_markup=None)

        elif action == 'reject':
            await self.state_manager.set_state(
                admin_id, UserState.WAITING_REJECT_REASON,
                withdraw_id=withdraw_id, msg=call.message
            )
            await call.answer("Введите причину отказа.")
            await self.bot.send_message(admin_id, "Напишите причину отказа:")

    async def _handle_reject_reason(self, message: types.Message):
        """Обработка причины отказа"""
        admin_id = message.from_user.id
        data = await self.state_manager.get_data(admin_id)
        withdraw_id = data['withdraw_id']
        reason = message.text

        w = await self.db.get_withdrawal(withdraw_id)

        if not w:
            await message.reply("Заявка не найдена.")
            await self.state_manager.clear_state(admin_id)
            return

        await self.db.update_withdrawal_status(withdraw_id, 'rejected', reason)

        try:
            await self.bot.send_message(
                w['user_id'],
                f"❌ Заявка на вывод {w['amount']}₽ отклонена.\nПричина: {reason}"
            )
        except Exception as e:
            self.logger.error(f"Не удалось уведомить пользователя {w['user_id']}: {e}")

        try:
            await self.bot.delete_message(data['msg'].chat.id, data['msg'].message_id)
        except:
            pass

        await self.state_manager.clear_state(admin_id)
        await message.reply("✅ Заявка отклонена.")

# ==================== ОСНОВНОЙ ЗАПУСК ====================

async def main():
    """Главная функция"""
    config = Config()
    logger = Logger(config.LOG_FILE)

    logger.info("=" * 50)
    logger.info("Запуск RudepsBot v4.0 (простая модерация)")
    logger.info("=" * 50)

    bot = Bot(token=config.BOT_TOKEN, parse_mode=ParseMode.MARKDOWN)
    storage = MemoryStorage()
    dp = Dispatcher(bot, storage=storage)

    db = Database(config.DATABASE_FILE)
    state_manager = UserStateManager()
    scheduler = Scheduler(bot, db, logger)

    handlers = Handlers(dp, bot, db, state_manager, logger)
    handlers.register_all()

    asyncio.create_task(scheduler.start())

    try:
        await dp.start_polling()
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")
        raise
    finally:
        await scheduler.stop()
        await dp.storage.close()
        await dp.storage.wait_closed()
        await bot.session.close()
        logger.info("Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())