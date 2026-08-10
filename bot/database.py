import aiosqlite
import asyncio
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
                    is_active INTEGER DEFAULT 1
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
                    problem TEXT,
                    media_id TEXT,
                    city TEXT,
                    inn_contract TEXT,
                    contact TEXT,
                    status TEXT DEFAULT 'open',
                    engineer_id INTEGER NULL
                )
            """)
            # Indexes
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_client ON tickets(client_id)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_engineer ON tickets(engineer_id)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
            
            await self.conn.commit()

    async def migrate(self):
        async with self.lock:
            # Check if close_comment exists, if not add it
            cursor = await self.conn.execute("PRAGMA table_info(tickets)")
            columns = [row['name'] for row in await cursor.fetchall()]
            if 'close_comment' not in columns:
                await self.conn.execute("ALTER TABLE tickets ADD COLUMN close_comment TEXT")
                await self.conn.commit()

    # Engineer CRUD
    async def add_engineer(self, user_id: int, name: str):
        async with self.lock:
            await self.conn.execute(
                "INSERT OR REPLACE INTO engineers (user_id, name, is_active) VALUES (?, ?, 1)",
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

    async def is_engineer(self, user_id: int) -> bool:
        async with self.lock:
            cursor = await self.conn.execute("SELECT 1 FROM engineers WHERE user_id = ? AND is_active = 1", (user_id,))
            return await cursor.fetchone() is not None

    # Admin CRUD
    async def add_admin(self, user_id: int):
        async with self.lock:
            await self.conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
            await self.conn.commit()

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
        contact: str
    ) -> int:
        async with self.lock:
            cursor = await self.conn.execute(
                """INSERT INTO tickets (
                    client_id, client_name, company, equipment_type, brand,
                    cnc_model, problem, media_id, city, inn_contract, contact
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    client_id, client_name, company, equipment_type, brand,
                    cnc_model, problem, media_id, city, inn_contract, contact
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
            await self.conn.execute(
                "UPDATE tickets SET status = ?, close_comment = ? WHERE id = ?",
                (status, comment, ticket_id)
            )
            await self.conn.commit()
            return True

    async def get_active_ticket_for_client(self, client_id: int) -> Optional[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE client_id = ? AND status = 'in_progress' ORDER BY id DESC LIMIT 1",
                (client_id,)
            )
            return await cursor.fetchone()

    async def get_active_tickets_for_engineer(self, engineer_id: int) -> List[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE engineer_id = ? AND status = 'in_progress' ORDER BY id DESC",
                (engineer_id,)
            )
            return await cursor.fetchall()

    async def get_all_tickets(self) -> List[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM tickets")
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
                    SUM(CASE WHEN t.status IN ('completed', 'canceled') AND t.engineer_id = e.user_id THEN 1 ELSE 0 END) as closed_count
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
