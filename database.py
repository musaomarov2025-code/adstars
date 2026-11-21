import os

import aiosqlite

from config import DB_PATH


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                chat_id INTEGER,
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                blocked INTEGER DEFAULT 0
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS joins (
                user_id INTEGER,
                chat_id INTEGER,
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, chat_id)
            )"""
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS channels (chat_id INTEGER PRIMARY KEY, title TEXT)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        await db.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)")
        await db.commit()


# ---------- Люди ----------
async def add_join(user_id, username, first_name, chat_id, title):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users (user_id, username, first_name, chat_id)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username,
                 first_name=excluded.first_name,
                 chat_id=excluded.chat_id""",
            (user_id, username, first_name, chat_id),
        )
        await db.execute(
            "INSERT OR IGNORE INTO joins (user_id, chat_id) VALUES (?, ?)",
            (user_id, chat_id),
        )
        await db.execute(
            """INSERT INTO channels (chat_id, title) VALUES (?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title""",
            (chat_id, title or str(chat_id)),
        )
        await db.commit()


async def set_blocked(user_id, blocked: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET blocked=? WHERE user_id=?", (1 if blocked else 0, user_id)
        )
        await db.commit()


async def get_active_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE blocked=0")
        return [r[0] for r in await cur.fetchall()]


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:

        async def one(q):
            cur = await db.execute(q)
            return (await cur.fetchone())[0]

        total = await one("SELECT COUNT(*) FROM users")
        blocked = await one("SELECT COUNT(*) FROM users WHERE blocked=1")
        new24 = await one(
            "SELECT COUNT(*) FROM users WHERE joined_at >= datetime('now','-1 day')"
        )
        new7 = await one(
            "SELECT COUNT(*) FROM users WHERE joined_at >= datetime('now','-7 day')"
        )
        cur = await db.execute(
            """SELECT COALESCE(c.title, CAST(j.chat_id AS TEXT)),
                      COUNT(*),
                      SUM(CASE WHEN j.joined_at >= datetime('now','-1 day') THEN 1 ELSE 0 END)
               FROM joins j LEFT JOIN channels c ON c.chat_id = j.chat_id
               GROUP BY j.chat_id ORDER BY COUNT(*) DESC"""
        )
        channels = await cur.fetchall()
    return {
        "total": total,
        "blocked": blocked,
        "new24": new24,
        "new7": new7,
        "channels": channels,
    }


# ---------- Настройки ----------
async def set_setting(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        await db.commit()


async def get_setting(key, default=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


async def del_setting(key):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM settings WHERE key=?", (key,))
        await db.commit()


# ---------- Админы ----------
async def get_admins():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM admins ORDER BY user_id")
        return [r[0] for r in await cur.fetchall()]


async def add_admin(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
        await db.commit()


async def del_admin(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
        await db.commit()


# ---------- Импорт базы ----------
async def import_db(path):
    """Добавляет людей из чужого файла .db к текущей базе, ничего не удаляя."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        before = (await cur.fetchone())[0]
        await db.execute("ATTACH DATABASE ? AS src", (path,))
        await db.execute(
            """INSERT OR IGNORE INTO users
               (user_id, username, first_name, chat_id, joined_at, blocked)
               SELECT user_id, username, first_name, chat_id, joined_at, blocked
               FROM src.users"""
        )
        for q in (
            "INSERT OR IGNORE INTO joins (user_id, chat_id, joined_at) "
            "SELECT user_id, chat_id, joined_at FROM src.joins",
            "INSERT OR IGNORE INTO channels (chat_id, title) "
            "SELECT chat_id, title FROM src.channels",
        ):
            try:
                await db.execute(q)
            except Exception:
                pass
        await db.commit()
        cur = await db.execute("SELECT COUNT(*) FROM users")
        after = (await cur.fetchone())[0]
        await db.execute("DETACH DATABASE src")
    return after - before
