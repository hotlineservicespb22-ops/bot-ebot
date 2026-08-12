import aiosqlite
import asyncio
import datetime
from typing import List, Optional, Tuple
from bot.config import DB_PATH

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None
        self.lock = asyncio.Lock()

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row

    async def close(self):
        if self.conn:
            await self.conn.close()

    async def init_db(self):
        async with self.lock:
            # Engineers table
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS engineers (
                    user_id INTEGER PRIMARY KEY,
                    name TEXT,
                    is_active INTEGER DEFAULT 1,
                    bitrix_user_id INTEGER
                )
            """)
            # Admins table (optional but good for /add_admin)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY
                )
            """)
            # Tickets table
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER,
                    client_name TEXT,
                    company TEXT,
                    equipment_type TEXT,
                    brand TEXT,
                    cnc_model TEXT,
                    machine_info TEXT,
                    company_city TEXT,
                    problem TEXT,
                    media_id TEXT,
                    city TEXT,
                    inn_contract TEXT,
                    contact TEXT,
                    status TEXT DEFAULT 'open',
                    engineer_id INTEGER NULL
                )
            """)
            # Messages table (история переписки)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER,
                    sender_id INTEGER,
                    sender_role TEXT,
                    text TEXT,
                    media_type TEXT,
                    created_at TEXT,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
                )
            """)
            # Ratings table (оценки клиентов)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER UNIQUE,
                    client_id INTEGER,
                    rating INTEGER,
                    comment TEXT,
                    created_at TEXT,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
                )
            """)
            # Media table (сохранённые фото/видео/документы заявки)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER,
                    file_id TEXT,
                    file_type TEXT,
                    file_path TEXT,
                    sender_id INTEGER,
                    sender_role TEXT,
                    created_at TEXT,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
                )
            """)
            # Ticket notifications table (message_id уведомлений, отправленных инженерам)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS ticket_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER,
                    engineer_id INTEGER,
                    message_id INTEGER,
                    created_at TEXT,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
                )
            """)
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_ticket_notifications_ticket ON ticket_notifications(ticket_id)")
            # Indexes
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_client ON tickets(client_id)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_engineer ON tickets(engineer_id)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_ticket ON messages(ticket_id)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_media_ticket ON media(ticket_id)")
            
            await self.conn.commit()

    async def migrate(self):
        async with self.lock:
            cursor = await self.conn.execute("PRAGMA table_info(tickets)")
            columns = set(row['name'] for row in await cursor.fetchall())

            # Колонки, которые нужно добавить (если их ещё нет)
            columns_to_add = [
                ('close_comment', 'TEXT'),
                ('machine_info', 'TEXT'),
                ('company_city', 'TEXT'),
                ('created_at', 'TEXT'),
                ('closed_at', 'TEXT'),
                ('machine_media_id', 'TEXT'),
            ]
            for col_name, col_type in columns_to_add:
                if col_name not in columns:
                    await self.conn.execute(f"ALTER TABLE tickets ADD COLUMN {col_name} {col_type}")
                    await self.conn.commit()
                    columns.add(col_name)

            # Миграция для таблицы engineers: добавляем bitrix_user_id
            cursor = await self.conn.execute("PRAGMA table_info(engineers)")
            eng_columns = set(row['name'] for row in await cursor.fetchall())
            if 'bitrix_user_id' not in eng_columns:
                await self.conn.execute("ALTER TABLE engineers ADD COLUMN bitrix_user_id INTEGER")
                await self.conn.commit()

    # Engineer CRUD
    async def add_engineer(self, user_id: int, name: str):
        async with self.lock:
            # Используем ON CONFLICT DO UPDATE, чтобы НЕ сбрасывать bitrix_user_id
            # при повторном добавлении инженера.
            await self.conn.execute(
                """
                INSERT INTO engineers (user_id, name, is_active)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    name = excluded.name,
                    is_active = 1
                """,
                (user_id, name)
            )
            await self.conn.commit()

    async def delete_engineer(self, user_id: int):
        async with self.lock:
            await self.conn.execute("DELETE FROM engineers WHERE user_id = ?", (user_id,))
            await self.conn.commit()

    async def get_engineers(self) -> List[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM engineers WHERE is_active = 1")
            return await cursor.fetchall()

    async def get_all_engineers(self) -> List[aiosqlite.Row]:
        """Возвращает всех инженеров (включая неактивных)."""
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM engineers ORDER BY name")
            return await cursor.fetchall()

    async def set_engineer_active(self, user_id: int, is_active: int):
        """Включает/выключает инженера как дежурного (is_active: 1 или 0)."""
        async with self.lock:
            await self.conn.execute(
                "UPDATE engineers SET is_active = ? WHERE user_id = ?",
                (is_active, user_id)
            )
            await self.conn.commit()

    async def is_engineer(self, user_id: int) -> bool:
        async with self.lock:
            cursor = await self.conn.execute("SELECT 1 FROM engineers WHERE user_id = ? AND is_active = 1", (user_id,))
            return await cursor.fetchone() is not None

    async def set_bitrix_user_id(self, user_id: int, bitrix_user_id: int):
        """Устанавливает соответствие инженера бота пользователю Битрикс24."""
        async with self.lock:
            await self.conn.execute(
                "UPDATE engineers SET bitrix_user_id = ? WHERE user_id = ?",
                (bitrix_user_id, user_id)
            )
            await self.conn.commit()

    async def get_bitrix_user_id(self, user_id: int) -> Optional[int]:
        """Возвращает ID пользователя Битрикс24 для инженера (или None)."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT bitrix_user_id FROM engineers WHERE user_id = ? AND is_active = 1",
                (user_id,)
            )
            row = await cursor.fetchone()
            return row['bitrix_user_id'] if row and row['bitrix_user_id'] is not None else None

    async def get_engineer_name(self, user_id: int) -> Optional[str]:
        """Возвращает имя инженера по его Telegram ID (или None)."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT name FROM engineers WHERE user_id = ? AND is_active = 1",
                (user_id,)
            )
            row = await cursor.fetchone()
            return row['name'] if row and row['name'] else None

    # Admin CRUD
    async def add_admin(self, user_id: int):
        async with self.lock:
            await self.conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
            await self.conn.commit()

    async def delete_admin(self, user_id: int):
        async with self.lock:
            await self.conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            await self.conn.commit()

    async def get_admins(self) -> List[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM admins")
            return await cursor.fetchall()

    async def is_admin(self, user_id: int) -> bool:
        # Check both hardcoded and DB
        from bot.config import ADMIN_IDS
        if user_id in ADMIN_IDS:
            return True
        async with self.lock:
            cursor = await self.conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
            return await cursor.fetchone() is not None

    # Ticket CRUD
    async def create_ticket(
        self,
        client_id: int,
        client_name: str,
        company: str,
        equipment_type: str,
        brand: str,
        cnc_model: str,
        problem: str,
        media_id: Optional[str],
        city: str,
        inn_contract: str,
        contact: str,
        machine_info: str = '',
        company_city: str = '',
        machine_media_id: Optional[str] = None
    ) -> int:
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cursor = await self.conn.execute(
                """INSERT INTO tickets (
                    client_id, client_name, company, equipment_type, brand,
                    cnc_model, machine_info, company_city, problem, media_id, city, inn_contract, contact, machine_media_id, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    client_id, client_name, company, equipment_type, brand,
                    cnc_model, machine_info, company_city, problem, media_id, city, inn_contract, contact, machine_media_id, now
                )
            )
            ticket_id = cursor.lastrowid
            await self.conn.commit()
            return ticket_id

    async def get_ticket(self, ticket_id: int) -> Optional[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
            return await cursor.fetchone()

    async def take_ticket(self, ticket_id: int, engineer_id: int) -> bool:
        async with self.lock:
            cursor = await self.conn.execute(
                "UPDATE tickets SET status = 'in_progress', engineer_id = ? WHERE id = ? AND status = 'open'",
                (engineer_id, ticket_id)
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def close_ticket(self, ticket_id: int, status: str, comment: str = None) -> bool:
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            await self.conn.execute(
                "UPDATE tickets SET status = ?, close_comment = ?, closed_at = ? WHERE id = ?",
                (status, comment, now, ticket_id)
            )
            await self.conn.commit()
            return True

    async def get_active_ticket_for_client(self, client_id: int) -> Optional[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE client_id = ? AND status IN ('open', 'in_progress') ORDER BY id DESC LIMIT 1",
                (client_id,)
            )
            return await cursor.fetchone()

    async def get_client_tickets(self, client_id: int, limit: int = 10) -> List[aiosqlite.Row]:
        """Возвращает историю заявок клиента (последние N)."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE client_id = ? ORDER BY id DESC LIMIT ?",
                (client_id, limit)
            )
            return await cursor.fetchall()

    async def get_active_tickets_for_engineer(self, engineer_id: int) -> List[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE engineer_id = ? AND status = 'in_progress' ORDER BY id DESC",
                (engineer_id,)
            )
            return await cursor.fetchall()

    async def get_open_tickets(self) -> List[aiosqlite.Row]:
        """Возвращает нераспределенные заявки (статус 'open', без инженера)."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE status = 'open' AND engineer_id IS NULL ORDER BY id DESC"
            )
            return await cursor.fetchall()

    async def get_all_tickets(self) -> List[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM tickets")
            return await cursor.fetchall()

    async def get_tickets_by_period(self, start_date: str, end_date: str) -> List[aiosqlite.Row]:
        """Возвращает заявки, созданные в заданном периоде (ISO-даты)."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE created_at >= ? AND created_at <= ? ORDER BY id DESC",
                (start_date, end_date)
            )
            return await cursor.fetchall()

    async def get_expired_open_tickets(self, timeout_seconds: int) -> List[aiosqlite.Row]:
        """
        Возвращает заявки в статусе 'open', которые висят дольше timeout_seconds.
        """
        async with self.lock:
            cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=timeout_seconds)).isoformat()
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE status = 'open' AND engineer_id IS NULL AND created_at < ?",
                (cutoff,)
            )
            return await cursor.fetchall()

    # Ratings CRUD
    async def save_rating(self, ticket_id: int, client_id: int, rating: int, comment: str = None):
        """Сохраняет оценку клиента по завершённой заявке.

        Используется INSERT OR IGNORE, чтобы повторная оценка не перезаписывала предыдущую.
        """
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            await self.conn.execute(
                "INSERT OR IGNORE INTO ratings (ticket_id, client_id, rating, comment, created_at) VALUES (?, ?, ?, ?, ?)",
                (ticket_id, client_id, rating, comment, now)
            )
            await self.conn.commit()

    async def update_rating_comment(self, ticket_id: int, comment: str):
        """Обновляет комментарий к оценке заявки."""
        async with self.lock:
            await self.conn.execute(
                "UPDATE ratings SET comment = ? WHERE ticket_id = ?",
                (comment, ticket_id)
            )
            await self.conn.commit()

    async def get_rating_for_ticket(self, ticket_id: int) -> Optional[aiosqlite.Row]:
        """Возвращает оценку по заявке."""
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM ratings WHERE ticket_id = ?", (ticket_id,))
            return await cursor.fetchone()

    async def get_avg_rating(self) -> Optional[float]:
        """Средняя оценка по всем заявкам."""
        async with self.lock:
            cursor = await self.conn.execute("SELECT AVG(rating) as avg_rating FROM ratings")
            row = await cursor.fetchone()
            return row['avg_rating'] if row else None

    async def get_avg_resolution_time(self) -> Optional[float]:
        """Среднее время решения заявки (в часах) для завершённых заявок."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT AVG((julianday(closed_at) - julianday(created_at)) * 24) as avg_hours "
                "FROM tickets WHERE status = 'completed' AND created_at IS NOT NULL AND closed_at IS NOT NULL"
            )
            row = await cursor.fetchone()
            return row['avg_hours'] if row else None

    async def get_tickets_by_city(self) -> List[aiosqlite.Row]:
        """Статистика заявок по городам."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT company_city, COUNT(*) as cnt FROM tickets WHERE company_city IS NOT NULL AND company_city != '' GROUP BY company_city ORDER BY cnt DESC LIMIT 10"
            )
            return await cursor.fetchall()

    # Messages CRUD
    async def save_message(self, ticket_id: int, sender_id: int, sender_role: str, text: str, media_type: str = None):
        """Сохраняет сообщение переписки в историю."""
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            await self.conn.execute(
                "INSERT INTO messages (ticket_id, sender_id, sender_role, text, media_type, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (ticket_id, sender_id, sender_role, text, media_type, now)
            )
            await self.conn.commit()

    async def get_messages_for_ticket(self, ticket_id: int) -> List[aiosqlite.Row]:
        """Возвращает историю переписки по заявке."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM messages WHERE ticket_id = ? ORDER BY created_at ASC",
                (ticket_id,)
            )
            return await cursor.fetchall()

    async def get_engineer_stats(self) -> List[aiosqlite.Row]:
        """
        Retrieves statistics for each engineer, including active and closed tickets.
        """
        async with self.lock:
            query = """
                SELECT
                    e.user_id,
                    e.name,
                    SUM(CASE WHEN t.status = 'in_progress' THEN 1 ELSE 0 END) as active_count,
                    SUM(CASE WHEN t.status IN ('completed', 'canceled') THEN 1 ELSE 0 END) as closed_count
                FROM
                    engineers e
                LEFT JOIN
                    tickets t ON e.user_id = t.engineer_id
                GROUP BY
                    e.user_id, e.name
                ORDER BY
                    e.name;
            """
            cursor = await self.conn.execute(query)
            return await cursor.fetchall()

    # Media CRUD
    async def save_media(self, ticket_id: int, file_id: str, file_type: str, file_path: str, sender_id: int, sender_role: str):
        """Сохраняет информацию о медиафайле заявки."""
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            await self.conn.execute(
                "INSERT INTO media (ticket_id, file_id, file_type, file_path, sender_id, sender_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticket_id, file_id, file_type, file_path, sender_id, sender_role, now)
            )
            await self.conn.commit()

    async def get_media_for_ticket(self, ticket_id: int) -> List[aiosqlite.Row]:
        """Возвращает список медиафайлов заявки."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM media WHERE ticket_id = ? ORDER BY created_at ASC, id ASC",
                (ticket_id,)
            )
            return await cursor.fetchall()

    # Ticket Notifications CRUD
    async def save_ticket_notification(self, ticket_id: int, engineer_id: int, message_id: int):
        """Сохраняет message_id уведомления о заявке, отправленного инженеру."""
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            await self.conn.execute(
                "INSERT INTO ticket_notifications (ticket_id, engineer_id, message_id, created_at) VALUES (?, ?, ?, ?)",
                (ticket_id, engineer_id, message_id, now)
            )
            await self.conn.commit()

    async def get_ticket_notifications(self, ticket_id: int) -> List[aiosqlite.Row]:
        """Возвращает список уведомлений о заявке, отправленных инженерам."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM ticket_notifications WHERE ticket_id = ?",
                (ticket_id,)
            )
            return await cursor.fetchall()

    async def delete_ticket_notifications(self, ticket_id: int):
        """Удаляет все уведомления о заявке (после того, как заявка взята/закрыта)."""
        async with self.lock:
            await self.conn.execute(
                "DELETE FROM ticket_notifications WHERE ticket_id = ?",
                (ticket_id,)
            )
            await self.conn.commit()
