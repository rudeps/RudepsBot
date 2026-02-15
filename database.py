# -*- coding: utf-8 -*-
"""
Класс для работы с базой данных SQLite
"""

import sqlite3
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any
import os

# Импортируем только конфиг и логгер (без циклических зависимостей)
from config import DATABASE_FILE, WEEKLY_COMMENT_DECREMENT, COMMENT_THRESHOLD
from logger import setup_logging

logger = setup_logging()


class Database:
    """Класс для работы с базой данных"""

    def __init__(self, db_file: str = DATABASE_FILE):
        self.db_file = db_file
        # Создаем директорию для БД, если нужно
        db_dir = os.path.dirname(db_file)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
        self.init_db()

    def get_connection(self):
        """Получить соединение с БД"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """Инициализация таблиц базы данных"""
        with self.get_connection() as conn:
            cur = conn.cursor()

            # Таблица пользователей
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
                    last_task_date TIMESTAMP
                )
            ''')

            # Таблица использованных хэшей фото
            cur.execute('''
                CREATE TABLE IF NOT EXISTS used_photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    photo_hash TEXT UNIQUE,
                    timestamp TIMESTAMP
                )
            ''')

            # Таблица логов комментариев
            cur.execute('''
                CREATE TABLE IF NOT EXISTS comments_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    timestamp TIMESTAMP,
                    week_number INTEGER,
                    month_number INTEGER
                )
            ''')

            # Таблица заявок на вывод
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

            # Таблица истории рассылок
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

            # Индексы для ускорения запросов
            cur.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_users_is_blocked ON users(is_blocked)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_users_comment_balance ON users(comment_balance)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_used_photos_hash ON used_photos(photo_hash)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals(status)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_comments_log_user ON comments_log(user_id)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_comments_log_week ON comments_log(week_number)')

            conn.commit()
            logger.info("База данных инициализирована")

    # ===== Работа с пользователями =====

    def get_user(self, user_id: int) -> Optional[Dict]:
        """Получить данные пользователя"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def create_user(self, user_id: int, username: str, first_name: str, last_name: str) -> None:
        """Создать нового пользователя"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            now = datetime.now()
            # Определяем, является ли пользователь администратором (из config)
            from config import ADMIN_IDS
            is_admin = user_id in ADMIN_IDS
            
            # Новый пользователь сразу заблокирован (баланс 0 < 10)
            cur.execute('''
                INSERT OR IGNORE INTO users 
                (user_id, username, first_name, last_name, registration_date, last_activity, is_admin, is_blocked)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name, now, now, is_admin, True))
            conn.commit()
            logger.info(f"Создан новый пользователь {user_id} ({username}) - заблокирован")

    def update_user_activity(self, user_id: int) -> None:
        """Обновить время последней активности"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET last_activity = ? WHERE user_id = ?",
                       (datetime.now(), user_id))
            conn.commit()

    def set_accepted_rules(self, user_id: int) -> None:
        """Отметить, что пользователь принял правила"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET accepted_rules = 1 WHERE user_id = ?", (user_id,))
            conn.commit()

    def set_user_blocked(self, user_id: int, blocked: bool = True) -> None:
        """Заблокировать/разблокировать пользователя"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_blocked = ? WHERE user_id = ?", (blocked, user_id))
            conn.commit()
            status = "заблокирован" if blocked else "разблокирован"
            logger.info(f"Пользователь {user_id} {status}")

    def is_user_blocked(self, user_id: int) -> bool:
        """Проверить, заблокирован ли пользователь"""
        user = self.get_user(user_id)
        return user and user['is_blocked']

    def update_user_admin_status(self, user_id: int, is_admin: bool) -> None:
        """Обновить статус администратора"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_admin = ? WHERE user_id = ?", (is_admin, user_id))
            conn.commit()
            logger.info(f"Пользователь {user_id} admin={is_admin}")

    # ===== Работа с комментариями =====

    def check_photo_hash(self, photo_hash: str) -> bool:
        """Проверить, существует ли хэш фото"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM used_photos WHERE photo_hash = ?", (photo_hash,))
            return cur.fetchone() is not None

    def save_photo_hash(self, user_id: int, photo_hash: str) -> None:
        """Сохранить хэш фото"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO used_photos (user_id, photo_hash, timestamp) VALUES (?, ?, ?)",
                       (user_id, photo_hash, datetime.now()))
            conn.commit()

    def add_comment(self, user_id: int) -> int:
        """Добавить комментарий пользователю, вернуть новый баланс и обновить статус блокировки"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            now = datetime.now()
            week = now.isocalendar()[1]
            month = now.month

            # Обновляем баланс
            cur.execute('''
                UPDATE users 
                SET comment_balance = comment_balance + 1,
                    total_comments_ever = total_comments_ever + 1
                WHERE user_id = ?
            ''', (user_id,))

            # Добавляем в лог
            cur.execute('''
                INSERT INTO comments_log (user_id, timestamp, week_number, month_number)
                VALUES (?, ?, ?, ?)
            ''', (user_id, now, week, month))

            # Получаем новый баланс
            cur.execute("SELECT comment_balance FROM users WHERE user_id = ?", (user_id,))
            new_balance = cur.fetchone()[0]

            # Обновляем статус блокировки: если баланс >= 10 - разблокирован, иначе - заблокирован
            if new_balance >= COMMENT_THRESHOLD:
                cur.execute("UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,))
            else:
                cur.execute("UPDATE users SET is_blocked = 1 WHERE user_id = ?", (user_id,))

            conn.commit()
            return new_balance

    def get_comment_balance(self, user_id: int) -> int:
        """Получить баланс комментариев"""
        user = self.get_user(user_id)
        return user['comment_balance'] if user else 0

    def get_user_comments_stats(self, user_id: int) -> Dict:
        """Получить статистику комментариев пользователя"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT 
                    COUNT(*) as total_comments,
                    COUNT(DISTINCT week_number) as weeks_active,
                    COUNT(DISTINCT month_number) as months_active,
                    MAX(timestamp) as last_comment_date
                FROM comments_log 
                WHERE user_id = ?
            ''', (user_id,))
            row = cur.fetchone()
            return dict(row) if row else {
                'total_comments': 0,
                'weeks_active': 0,
                'months_active': 0,
                'last_comment_date': None
            }

    # ===== Работа с деньгами =====

    def add_money(self, user_id: int, amount: int) -> None:
        """Начислить деньги"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET money_balance = money_balance + ? WHERE user_id = ?",
                       (amount, user_id))
            conn.commit()

    def deduct_money(self, user_id: int, amount: int) -> None:
        """Списать деньги"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET money_balance = money_balance - ? WHERE user_id = ?",
                       (amount, user_id))
            conn.commit()

    def get_money_balance(self, user_id: int) -> int:
        """Получить денежный баланс"""
        user = self.get_user(user_id)
        return user['money_balance'] if user else 0

    def increment_tasks_completed(self, user_id: int, reward: int) -> None:
        """Увеличить счетчик выполненных заданий и начислить награду"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                UPDATE users 
                SET tasks_completed = tasks_completed + 1,
                    money_balance = money_balance + ?,
                    last_task_date = ?
                WHERE user_id = ?
            ''', (reward, datetime.now(), user_id))
            conn.commit()

    def get_user_tasks_stats(self, user_id: int) -> Dict:
        """Получить статистику заданий пользователя"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT 
                    tasks_completed,
                    money_balance,
                    last_task_date
                FROM users 
                WHERE user_id = ?
            ''', (user_id,))
            row = cur.fetchone()
            return dict(row) if row else {
                'tasks_completed': 0,
                'money_balance': 0,
                'last_task_date': None
            }

    # ===== Работа с выводами =====

    def create_withdrawal(self, user_id: int, amount: int, method: str, details: str) -> None:
        """Создать заявку на вывод"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO withdrawals (user_id, amount, method, details, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, amount, method, details, datetime.now()))
            conn.commit()
            logger.info(f"Создана заявка на вывод: user={user_id}, amount={amount}, method={method}")

    def get_pending_withdrawals(self) -> List[Dict]:
        """Получить все ожидающие заявки"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM withdrawals WHERE status = 'pending' ORDER BY created_at")
            rows = cur.fetchall()
            return [dict(row) for row in rows]

    def get_withdrawal(self, withdrawal_id: int) -> Optional[Dict]:
        """Получить заявку по ID"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def update_withdrawal_status(self, withdrawal_id: int, status: str,
                                reject_reason: str = None) -> None:
        """Обновить статус заявки"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            if reject_reason:
                cur.execute('''
                    UPDATE withdrawals 
                    SET status = ?, processed_at = ?, reject_reason = ?
                    WHERE id = ?
                ''', (status, datetime.now(), reject_reason, withdrawal_id))
            else:
                cur.execute('''
                    UPDATE withdrawals 
                    SET status = ?, processed_at = ?
                    WHERE id = ?
                ''', (status, datetime.now(), withdrawal_id))
            conn.commit()
            logger.info(f"Заявка {withdrawal_id} обновлена: status={status}")

    def get_user_withdrawals(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Получить историю выводов пользователя"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT * FROM withdrawals 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (user_id, limit))
            rows = cur.fetchall()
            return [dict(row) for row in rows]

    # ===== Статистика =====

    def get_total_users(self) -> int:
        """Общее количество пользователей"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            return cur.fetchone()[0]

    def get_active_users(self) -> int:
        """Количество пользователей с comment_balance >= COMMENT_THRESHOLD"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users WHERE comment_balance >= ?", (COMMENT_THRESHOLD,))
            return cur.fetchone()[0]

    def get_blocked_users(self) -> int:
        """Количество заблокированных пользователей"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
            return cur.fetchone()[0]

    def get_users_with_accepted_rules(self) -> int:
        """Количество пользователей, принявших правила"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users WHERE accepted_rules = 1")
            return cur.fetchone()[0]

    def get_avg_comment_balance(self) -> float:
        """Средний баланс комментариев"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT AVG(comment_balance) FROM users")
            avg = cur.fetchone()[0]
            return avg if avg else 0

    def get_total_comments_ever(self) -> int:
        """Общее количество комментариев за всё время"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT SUM(total_comments_ever) FROM users")
            total = cur.fetchone()[0]
            return total if total else 0

    def get_total_tasks_completed(self) -> int:
        """Общее количество выполненных заданий"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT SUM(tasks_completed) FROM users")
            total = cur.fetchone()[0]
            return total if total else 0

    def get_total_money_earned(self) -> int:
        """Общая сумма заработанных денег"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT SUM(money_balance) FROM users")
            total = cur.fetchone()[0]
            return total if total else 0

    def get_top_comment_balance(self, limit: int = 10) -> List[Tuple]:
        """Топ пользователей по комментариям"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT user_id, comment_balance, username, first_name, last_name 
                FROM users 
                ORDER BY comment_balance DESC 
                LIMIT ?
            ''', (limit,))
            return cur.fetchall()

    def get_top_tasks_completed(self, limit: int = 10) -> List[Tuple]:
        """Топ пользователей по выполненным заданиям"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT user_id, tasks_completed, username, first_name, last_name 
                FROM users 
                ORDER BY tasks_completed DESC 
                LIMIT ?
            ''', (limit,))
            return cur.fetchall()

    def get_top_money_balance(self, limit: int = 10) -> List[Tuple]:
        """Топ пользователей по денежному балансу"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT user_id, money_balance, username, first_name, last_name 
                FROM users 
                ORDER BY money_balance DESC 
                LIMIT ?
            ''', (limit,))
            return cur.fetchall()

    def get_total_unique_photos(self) -> int:
        """Общее количество уникальных фото"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM used_photos")
            return cur.fetchone()[0]

    def get_photos_by_user(self, user_id: int) -> int:
        """Количество фото, загруженных пользователем"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM used_photos WHERE user_id = ?", (user_id,))
            return cur.fetchone()[0]

    def get_withdrawal_stats(self) -> Dict:
        """Статистика по заявкам на вывод"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT status, COUNT(*) FROM withdrawals GROUP BY status")
            return dict(cur.fetchall())

    def get_withdrawal_total_amount(self) -> Dict:
        """Общая сумма выведенных средств по статусам"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT status, SUM(amount) as total 
                FROM withdrawals 
                GROUP BY status
            ''')
            rows = cur.fetchall()
            return {row['status']: row['total'] for row in rows}

    def get_daily_stats(self, days: int = 7) -> List[Dict]:
        """Статистика за последние N дней"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT 
                    DATE(registration_date) as date,
                    COUNT(*) as new_users
                FROM users 
                WHERE registration_date >= datetime('now', ?)
                GROUP BY DATE(registration_date)
                ORDER BY date DESC
            ''', (f'-{days} days',))
            return [dict(row) for row in cur.fetchall()]

    # ===== Работа с пользователями для рассылок =====

    def get_all_user_ids(self) -> List[int]:
        """Все ID пользователей для экспорта"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM users WHERE accepted_rules = 1")
            return [row[0] for row in cur.fetchall()]

    def get_users_for_broadcast(self, target_type: str, count: int = 0) -> List[int]:
        """Получить список пользователей для рассылки"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            if target_type == 'all':
                cur.execute("SELECT user_id FROM users WHERE accepted_rules = 1")
            elif target_type == 'top_active':
                cur.execute('''
                    SELECT user_id FROM users 
                    WHERE accepted_rules = 1 
                    ORDER BY tasks_completed DESC 
                    LIMIT ?
                ''', (count,))
            elif target_type == 'top_inactive':
                cur.execute('''
                    SELECT user_id FROM users 
                    WHERE accepted_rules = 1 
                    ORDER BY tasks_completed ASC, last_activity ASC 
                    LIMIT ?
                ''', (count,))
            elif target_type == 'random':
                cur.execute('''
                    SELECT user_id FROM users 
                    WHERE accepted_rules = 1 
                    ORDER BY RANDOM() 
                    LIMIT ?
                ''', (count,))
            elif target_type == 'blocked':
                cur.execute('''
                    SELECT user_id FROM users 
                    WHERE accepted_rules = 1 AND is_blocked = 1
                ''')
            elif target_type == 'unblocked':
                cur.execute('''
                    SELECT user_id FROM users 
                    WHERE accepted_rules = 1 AND is_blocked = 0
                ''')
            else:
                return []
            return [row[0] for row in cur.fetchall()]

    def get_users_count_by_criteria(self, target_type: str) -> int:
        """Получить количество пользователей по критерию"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            if target_type == 'all':
                cur.execute("SELECT COUNT(*) FROM users WHERE accepted_rules = 1")
            elif target_type == 'blocked':
                cur.execute("SELECT COUNT(*) FROM users WHERE accepted_rules = 1 AND is_blocked = 1")
            elif target_type == 'unblocked':
                cur.execute("SELECT COUNT(*) FROM users WHERE accepted_rules = 1 AND is_blocked = 0")
            else:
                return 0
            return cur.fetchone()[0]

    # ===== Еженедельное списание =====

    def weekly_decrement_comments(self) -> List[Tuple[int, int]]:
        """
        Еженедельное списание комментариев
        Списываем WEEKLY_COMMENT_DECREMENT, но не уходим в минус (минимум 0)
        После списания: если баланс < 10 - блокируем, если >= 10 - разблокируем
        Возвращает список (user_id, новый_баланс) для тех, кто был заблокирован
        """
        with self.get_connection() as conn:
            cur = conn.cursor()

            # Получаем всех пользователей
            cur.execute("SELECT user_id, comment_balance FROM users")
            users = cur.fetchall()

            newly_blocked = []

            for user_id, balance in users:
                # Списываем, но не уходим в минус
                new_balance = max(0, balance - WEEKLY_COMMENT_DECREMENT)
                
                # Обновляем баланс
                cur.execute("UPDATE users SET comment_balance = ? WHERE user_id = ?",
                           (new_balance, user_id))

                # Обновляем статус блокировки по правилу: <10 - блокирован, >=10 - разблокирован
                if new_balance < COMMENT_THRESHOLD:
                    cur.execute("UPDATE users SET is_blocked = 1 WHERE user_id = ?", (user_id,))
                    if balance >= COMMENT_THRESHOLD:  # Если раньше был разблокирован
                        newly_blocked.append((user_id, new_balance))
                        logger.info(f"Пользователь {user_id} ЗАБЛОКИРОВАН (баланс: {new_balance} < 10)")
                else:  # new_balance >= 10
                    cur.execute("UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,))
                    if balance < COMMENT_THRESHOLD:  # Если раньше был заблокирован
                        logger.info(f"Пользователь {user_id} РАЗБЛОКИРОВАН (баланс: {new_balance} >= 10)")

            conn.commit()
            return newly_blocked

    # ===== Очистка старых данных =====

    def clean_old_data(self, days: int = 30):
        """Очистка старых данных (для экономии места)"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            
            # Очищаем старые логи комментариев
            cur.execute('''
                DELETE FROM comments_log 
                WHERE timestamp < datetime('now', ?)
            ''', (f'-{days} days',))
            deleted_logs = cur.rowcount
            
            # Очищаем старые обработанные заявки
            cur.execute('''
                DELETE FROM withdrawals 
                WHERE status != 'pending' 
                AND processed_at < datetime('now', ?)
            ''', (f'-{days} days',))
            deleted_withdrawals = cur.rowcount
            
            conn.commit()
            logger.info(f"Очистка данных: удалено {deleted_logs} логов, {deleted_withdrawals} заявок")

    # ===== Бэкап базы данных =====

    def backup_database(self, backup_file: str = None) -> str:
        """Создать бэкап базы данных"""
        if not backup_file:
            backup_file = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        
        import shutil
        shutil.copy2(self.db_file, backup_file)
        logger.info(f"Создан бэкап БД: {backup_file}")
        return backup_file

    # ===== Поиск пользователей =====

    def search_users(self, query: str, limit: int = 20) -> List[Dict]:
        """Поиск пользователей по ID, username, имени"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            
            # Пробуем разные варианты поиска
            if query.isdigit():
                cur.execute('''
                    SELECT * FROM users 
                    WHERE user_id = ? 
                    OR username LIKE ? 
                    OR first_name LIKE ? 
                    OR last_name LIKE ?
                    LIMIT ?
                ''', (int(query), f'%{query}%', f'%{query}%', f'%{query}%', limit))
            else:
                cur.execute('''
                    SELECT * FROM users 
                    WHERE username LIKE ? 
                    OR first_name LIKE ? 
                    OR last_name LIKE ?
                    LIMIT ?
                ''', (f'%{query}%', f'%{query}%', f'%{query}%', limit))
            
            rows = cur.fetchall()
            return [dict(row) for row in rows]

    # ===== Получение статистики по периодам =====

    def get_comments_by_period(self, period: str = 'week') -> int:
        """Количество комментариев за период (day, week, month)"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            
            if period == 'day':
                cur.execute('''
                    SELECT COUNT(*) FROM comments_log 
                    WHERE timestamp >= datetime('now', 'start of day')
                ''')
            elif period == 'week':
                cur.execute('''
                    SELECT COUNT(*) FROM comments_log 
                    WHERE timestamp >= datetime('now', 'weekday 0', '-7 days')
                ''')
            elif period == 'month':
                cur.execute('''
                    SELECT COUNT(*) FROM comments_log 
                    WHERE timestamp >= datetime('now', 'start of month')
                ''')
            else:
                return 0
            
            return cur.fetchone()[0]

    def get_new_users_by_period(self, period: str = 'week') -> int:
        """Новых пользователей за период"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            
            if period == 'day':
                cur.execute('''
                    SELECT COUNT(*) FROM users 
                    WHERE registration_date >= datetime('now', 'start of day')
                ''')
            elif period == 'week':
                cur.execute('''
                    SELECT COUNT(*) FROM users 
                    WHERE registration_date >= datetime('now', 'weekday 0', '-7 days')
                ''')
            elif period == 'month':
                cur.execute('''
                    SELECT COUNT(*) FROM users 
                    WHERE registration_date >= datetime('now', 'start of month')
                ''')
            else:
                return 0
            
            return cur.fetchone()[0]