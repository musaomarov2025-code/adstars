import sqlite3
from config import DB_PATH


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    c = conn()
    cur = c.cursor()

    # Настройки (лимиты, задержки, флаги вкл/выкл)
    cur.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")

    # Цели для рассылки (чаты, каналы)
    cur.execute("""CREATE TABLE IF NOT EXISTS targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT UNIQUE,
        title TEXT,
        folder TEXT
    )""")

    # Тексты для рассылки
    cur.execute("""CREATE TABLE IF NOT EXISTS texts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT
    )""")

    # Боты-админы для автоподписки
    cur.execute("""CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_username TEXT UNIQUE
    )""")

    # Триггерные слова
    cur.execute("""CREATE TABLE IF NOT EXISTS triggers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        word TEXT UNIQUE
    )""")

    # Ответы на триггеры
    cur.execute("""CREATE TABLE IF NOT EXISTS replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT
    )""")

    # Контакты, которые уже добавлены (чтобы не добавлять повторно)
    cur.execute("""CREATE TABLE IF NOT EXISTS added_contacts (
        user_id INTEGER PRIMARY KEY
    )""")

    # Логи
    cur.execute("""CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT,
        level TEXT,
        text TEXT
    )""")

    # Счётчик за день
    cur.execute("""CREATE TABLE IF NOT EXISTS daily_counter (
        date TEXT PRIMARY KEY,
        sent INTEGER DEFAULT 0
    )""")

    c.commit()
    c.close()


# --- settings ---
def set(key, value):
    c = conn()
    c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))
    c.commit(); c.close()


def get(key, default=None):
    c = conn()
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    return r["value"] if r else default


# --- targets ---
def add_target(chat_id, title="", folder=""):
    c = conn()
    try:
        c.execute("INSERT INTO targets (chat_id, title, folder) VALUES (?,?,?)",
                  (str(chat_id), title, folder))
        c.commit()
    except sqlite3.IntegrityError:
        pass
    c.close()


def del_target(chat_id):
    c = conn()
    c.execute("DELETE FROM targets WHERE chat_id=?", (str(chat_id),))
    c.commit(); c.close()


def all_targets():
    c = conn()
    r = c.execute("SELECT chat_id FROM targets").fetchall()
    c.close()
    return [row["chat_id"] for row in r]


# --- texts ---
def add_text(content):
    c = conn()
    c.execute("INSERT INTO texts (content) VALUES (?)", (content,))
    c.commit(); c.close()


def all_texts():
    c = conn()
    r = c.execute("SELECT content FROM texts").fetchall()
    c.close()
    return [row["content"] for row in r]


def del_text(tid):
    c = conn()
    c.execute("DELETE FROM texts WHERE id=?", (tid,))
    c.commit(); c.close()


# --- admins (автоподписка) ---
def add_admin(username):
    c = conn()
    try:
        c.execute("INSERT INTO admins (bot_username) VALUES (?)", (username,))
        c.commit()
    except sqlite3.IntegrityError:
        pass
    c.close()


def all_admins():
    c = conn()
    r = c.execute("SELECT bot_username FROM admins").fetchall()
    c.close()
    return [row["bot_username"] for row in r]


def del_admin(aid):
    c = conn()
    c.execute("DELETE FROM admins WHERE id=?", (aid,))
    c.commit(); c.close()


# --- triggers ---
def add_trigger(word):
    c = conn()
    try:
        c.execute("INSERT INTO triggers (word) VALUES (?)", (word.lower(),))
        c.commit()
    except sqlite3.IntegrityError:
        pass
    c.close()


def all_triggers():
    c = conn()
    r = c.execute("SELECT word FROM triggers").fetchall()
    c.close()
    return [row["word"] for row in r]


def del_trigger(tid):
    c = conn()
    c.execute("DELETE FROM triggers WHERE id=?", (tid,))
    c.commit(); c.close()


# --- replies ---
def add_reply(content):
    c = conn()
    c.execute("INSERT INTO replies (content) VALUES (?)", (content,))
    c.commit(); c.close()


def all_replies():
    c = conn()
    r = c.execute("SELECT content FROM replies").fetchall()
    c.close()
    return [row["content"] for row in r]


def del_reply(rid):
    c = conn()
    c.execute("DELETE FROM replies WHERE id=?", (rid,))
    c.commit(); c.close()


# --- added contacts ---
def is_contact_added(user_id):
    c = conn()
    r = c.execute("SELECT 1 FROM added_contacts WHERE user_id=?", (user_id,)).fetchone()
    c.close()
    return r is not None


def mark_contact(user_id):
    c = conn()
    c.execute("INSERT OR IGNORE INTO added_contacts (user_id) VALUES (?)", (user_id,))
    c.commit(); c.close()


# --- logs ---
def log(level, text):
    from datetime import datetime
    c = conn()
    c.execute("INSERT INTO logs (time, level, text) VALUES (?,?,?)",
              (datetime.now().strftime("%H:%M:%S"), level, text))
    c.commit(); c.close()


def get_logs(limit=50):
    c = conn()
    r = c.execute("SELECT time, level, text FROM logs ORDER BY id DESC LIMIT ?",
                  (limit,)).fetchall()
    c.close()
    return [dict(row) for row in r]


# --- daily counter ---
def today():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def inc_sent():
    c = conn()
    d = today()
    c.execute("INSERT OR IGNORE INTO daily_counter (date, sent) VALUES (?,0)", (d,))
    c.execute("UPDATE daily_counter SET sent = sent + 1 WHERE date=?", (d,))
    c.commit(); c.close()


def get_sent_today():
    c = conn()
    r = c.execute("SELECT sent FROM daily_counter WHERE date=?", (today(),)).fetchone()
    c.close()
    return r["sent"] if r else 0
