import aiosqlite
from config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY,
                title TEXT,
                ad_enabled INTEGER DEFAULT 1,
                mat_enabled INTEGER DEFAULT 1,
                link TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                name TEXT,
                gender TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT,
                media_type TEXT,
                media_id TEXT,
                order_num INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ad_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ad_id INTEGER,
                chat_id INTEGER,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Миграция: добавить колонку link, если её нет (для старых БД)
        try:
            await db.execute("ALTER TABLE chats ADD COLUMN link TEXT")
        except Exception:
            pass  # уже есть

        await db.commit()


# ==== ЧАТЫ ====
async def add_chat(chat_id: int, title: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO chats (id, title) VALUES (?, ?)",
            (chat_id, title)
        )
        await db.commit()


async def get_all_chats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, title, ad_enabled, mat_enabled FROM chats"
        ) as cur:
            return await cur.fetchall()


async def toggle_chat_ad(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE chats SET ad_enabled = 1 - ad_enabled WHERE id = ?",
            (chat_id,)
        )
        await db.commit()


async def set_chat_mat(chat_id: int, enabled: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE chats SET mat_enabled = ? WHERE id = ?",
            (1 if enabled else 0, chat_id)
        )
        await db.commit()


async def is_mat_enabled(chat_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT mat_enabled FROM chats WHERE id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else True


# ==== ССЫЛКИ НА ЧАТЫ ====
async def set_chat_link(chat_id: int, link: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE chats SET link = ? WHERE id = ?",
            (link, chat_id)
        )
        await db.commit()


async def get_chat_link(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT link FROM chats WHERE id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else None


# ==== ЮЗЕРЫ ====
async def save_user(user_id: int, name: str, gender: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (id, name, gender) VALUES (?, ?, ?)",
            (user_id, name, gender)
        )
        await db.commit()


async def get_user_gender(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT gender FROM users WHERE id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


# ==== РЕКЛАМА ====
async def add_ad(text: str, media_type: str = None, media_id: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT MAX(order_num) FROM ads") as cur:
            row = await cur.fetchone()
            next_num = (row[0] or 0) + 1
        await db.execute(
            "INSERT INTO ads (text, media_type, media_id, order_num) VALUES (?, ?, ?, ?)",
            (text, media_type, media_id, next_num)
        )
        await db.commit()


async def get_all_ads():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, text, media_type, media_id, order_num FROM ads ORDER BY order_num"
        ) as cur:
            return await cur.fetchall()


async def delete_ad(ad_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM ads WHERE id = ?", (ad_id,))
        await db.commit()


async def get_next_ad():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM ads") as cur:
            count = (await cur.fetchone())[0]
        if count == 0:
            return None

        async with db.execute(
            "SELECT value FROM settings WHERE key = 'last_ad_index'"
        ) as cur:
            row = await cur.fetchone()
            last = int(row[0]) if row else -1

        next_index = (last + 1) % count

        async with db.execute(
            "SELECT id, text, media_type, media_id FROM ads ORDER BY order_num LIMIT 1 OFFSET ?",
            (next_index,)
        ) as cur:
            ad = await cur.fetchone()

        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('last_ad_index', ?)",
            (str(next_index),)
        )
        await db.commit()
        return ad


async def log_ad(ad_id: int, chat_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO ad_log (ad_id, chat_id, status) VALUES (?, ?, ?)",
            (ad_id, chat_id, status)
        )
        await db.commit()


# ==== СТАТИСТИКА ====
async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM chats") as cur:
            chats = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            users = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM ads") as cur:
            ads = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM ad_log WHERE status = 'ok'"
        ) as cur:
            sent = (await cur.fetchone())[0]
        return {"chats": chats, "users": users, "ads": ads, "sent": sent}
