import asyncio
import base64
import io
import logging
import os
import re
import zipfile
import aiohttp
import aiosqlite
import datetime
from bs4 import BeautifulSoup
from pypdf import PdfReader
from github import Github

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    Message, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
XEROSHARC_API_KEY = os.getenv("XEROSHARC_API_KEY")
XEROSHARC_BASE_URL = os.getenv("XEROSHARC_BASE_URL", "https://xerosharc.site/v1")
DB_FILE = "bot_memory.db"

if not BOT_TOKEN or not XEROSHARC_API_KEY:
    raise ValueError("Не заданы BOT_TOKEN или XEROSHARC_API_KEY!")

router = Router()
client = AsyncOpenAI(api_key=XEROSHARC_API_KEY, base_url=XEROSHARC_BASE_URL, timeout=180.0, max_retries=0)

class BotStates(StatesGroup):
    waiting_for_chat_name = State()
    waiting_for_github_token = State()
    waiting_for_github_repo = State()

def get_base_prompt():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        f"Сейчас: {now}.\n"
        "Ты — умный ИИ-ассистент. Общайся свободно.\n"
        "ПРАВИЛА:\n"
        "1. Если просят код, оборачивай его: ```python filename=\"main.py\"\n...\n```.\n"
        "2. Если просят напомнить, пиши тег: [REMIND: YYYY-MM-DD HH:MM | текст].\n"
        "3. Если просят поискать в интернете: [SEARCH: запрос].\n"
        "4. АВТОПАМЯТЬ: Если пользователь сообщает важный факт о себе, напиши тег [MEMORIZE: факт].\n"
        "5. Отвечай на русском."
    )

async def safe_edit(message: Message, text: str, parse_mode: str = None, reply_markup=None):
    try:
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        if "message is not modified" not in str(e) and "message to edit not found" not in str(e):
            pass

def get_main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📂 Мои диалоги"), KeyboardButton(text="➕ Новый диалог")],
        [KeyboardButton(text="🧠 Что ты обо мне знаешь?"), KeyboardButton(text="🐙 GitHub Деплой")],
        [KeyboardButton(text="🧹 Очистить чат"), KeyboardButton(text="❓ Помощь")]
    ], resize_keyboard=True)

def get_github_action_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Загрузить в GitHub", callback_data="do_push_github")],
        [InlineKeyboardButton(text="❌ Не надо", callback_data="cancel_push")]
    ])

def get_chats_menu(chats):
    kb = [[InlineKeyboardButton(text=title, callback_data=f"switch_{cid}")] for cid, title in chats]
    kb.append([InlineKeyboardButton(text="❌ Удалить все", callback_data="del_chats")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_cancel_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]])

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, summary TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, role TEXT, content TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS project_files (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, filename TEXT, content TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, remind_time TEXT, text TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, fact_text TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS settings (user_id INTEGER PRIMARY KEY, gh_token TEXT, gh_repo TEXT)")
        await db.commit()

async def get_chat_id(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT id FROM chats WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
        row = await cursor.fetchone()
        if row: return row[0]
        cursor = await db.execute("INSERT INTO chats (user_id, title) VALUES (?, ?)", (user_id, "Основной"))
        await db.commit()
        return cursor.lastrowid

async def get_settings(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT gh_token, gh_repo FROM settings WHERE user_id = ?", (user_id,))
        return await cursor.fetchone()

async def get_facts(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT fact_text FROM facts WHERE user_id = ?", (user_id,))
        rows = await cursor.fetchall()
        return "\n".join([f"- {r[0]}" for r in rows]) if rows else ""

async def summarize_chat_async(chat_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT count(*) FROM history WHERE chat_id = ?", (chat_id,))
        count = (await cursor.fetchone())[0]
        
        if count > 50:
            cursor = await db.execute("SELECT id, role, content FROM history WHERE chat_id = ? ORDER BY id ASC LIMIT 20", (chat_id,))
            old_msgs = await cursor.fetchall()
            text = "\n".join([f"{m[1]}: {m[2]}" for m in old_msgs])
            
            try:
                resp = await client.chat.completions.create(
                    model="claude-haiku-4-5", 
                    messages=[{"role": "user", "content": f"Сожми этот диалог в саммари (сохрани суть и код):\n{text}"}],
                    max_tokens=1000
                )
                # ЗАЩИТА ОТ ПУСТОГО ОТВЕТА
                if not resp.choices:
                    return
                new_summary = resp.choices[0].message.content
                
                cursor = await db.execute("SELECT summary FROM chats WHERE id = ?", (chat_id,))
                row = await cursor.fetchone()
                old_summary = row[0] if row and row[0] else ""
                combined = old_summary + "\n" + new_summary
                
                await db.execute("UPDATE chats SET summary = ? WHERE id = ?", (combined[:4000], chat_id))
                
                ids = [str(m[0]) for m in old_msgs]
                await db.execute(f"DELETE FROM history WHERE id IN ({','.join(ids)})")
                await db.commit()
            except Exception as e:
                logging.error(f"Ошибка сжатия: {e}")

async def reminder_worker(bot: Bot):
    while True:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("SELECT id, user_id, text FROM reminders WHERE remind_time <= ?", (now,))
            due = await cursor.fetchall()
            for r in due:
                try: await bot.send_message(r[1], f"⏰ **НАПОМИНАНИЕ:**\n{r[2]}", parse_mode="Markdown")
                except: pass
                await db.execute("DELETE FROM reminders WHERE id = ?", (r[0],))
            await db.commit()
        await asyncio.sleep(30)

async def ai_request_stream(message: Message, user_id: int, chat_id: int):
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT summary FROM chats WHERE id = ?", (chat_id,))
        row = await cursor.fetchone()
        summary = row[0] if row and row[0] else ""
        
        cursor = await db.execute("SELECT role, content FROM history WHERE chat_id = ? ORDER BY id DESC LIMIT 50", (chat_id,))
        history = [{"role": r[0], "content": r[1]} for r in reversed(await cursor.fetchall())]
        
    facts = await get_facts(user_id)
    sys_prompt = get_base_prompt()
    if summary: sys_prompt += f"\n\nКРАТКОЕ СОДЕРЖАНИЕ СТАРОГО ДИАЛОГА:\n{summary}"
    if facts: sys_prompt += f"\n\nЧТО ТЫ ЗНАЕШЬ О ПОЛЬЗОВАТЕЛЕ:\n{facts}"
    
    msgs = [{"role": "system", "content": sys_prompt}] + history
    full_text = ""
    
    msg = await message.answer("⏳ Думаю...")
    
    try:
        stream = await client.chat.completions.create(
            model="claude-sonnet-4-6", 
            messages=msgs,
            max_tokens=4000,
            stream=True 
        )
        
        async for chunk in stream:
            # ГЛАВНАЯ ЗАЩИТА ОТ ОШИБКИ list index out of range
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                full_text += chunk.choices[0].delta.content
                if len(full_text) % 30 == 0:
                    display_text = re.sub(r'\[(REMIND|SEARCH|MEMORIZE).*?\]', '', full_text)
                    await safe_edit(msg, display_text + " █") 
                    
        display_text = re.sub(r'\[(REMIND|SEARCH|MEMORIZE).*?\]', '', full_text).strip()
        await safe_edit(msg, display_text)
        
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", full_text))
            await db.commit()

        # --- ТЕГИ ---
        memorize_matches = re.findall(r'\[MEMORIZE:\s*(.*?)\]', full_text)
        for fact in memorize_matches:
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("INSERT INTO facts (user_id, fact_text) VALUES (?, ?)", (user_id, fact))
                await db.commit()

        remind_match = re.search(r'\[REMIND:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*\|\s*(.*?)\]', full_text)
        if remind_match:
            dt_str, rem_text = remind_match.groups()
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("INSERT INTO reminders (user_id, remind_time, text) VALUES (?, ?, ?)", (user_id, dt_str, rem_text))
                await db.commit()
            await message.answer(f"⏰ Ок, напомню в {dt_str}:\n_{rem_text}_", parse_mode="Markdown")

        search_match = re.search(r'\[SEARCH:\s*(.*?)\]', full_text)
        if search_match:
            query = search_match.group(1)
            await message.answer(f"🌐 Ищу: {query}...")
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://s.jina.ai/{query}", timeout=60) as r:
                    res = (await r.text())[:20000]
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", f"[Результат поиска '{query}']:\n{res}"))
                await db.commit()
            await ai_request_stream(message, user_id, chat_id)
            return

        pattern = r"```(?:\w+)?\s*filename=[\"'](.*?)[\"']\n(.*?)```"
        matches = re.findall(pattern, full_text, re.DOTALL)
        if matches:
            async with aiosqlite.connect(DB_FILE) as db:
                for fname, code in matches:
                    await db.execute("INSERT INTO project_files (chat_id, filename, content) VALUES (?, ?, ?)", (chat_id, fname, code))
                await db.commit()
            await message.answer("👇 Я написал код. Загрузить в GitHub?", reply_markup=get_github_action_kb())

        asyncio.create_task(summarize_chat_async(chat_id))

    except Exception as e:
        await safe_edit(msg, f"⚠️ Ошибка: {e}")

@router.message(Command("start"))
async def cmd_start(message: Message):
    await get_chat_id(message.from_user.id)
    await message.answer("👋 Привет! Я твой умный ИИ-ассистент.", reply_markup=get_main_menu())

@router.message(F.text == "🧠 Что ты обо мне знаешь?")
async def btn_facts(message: Message):
    facts = await get_facts(message.from_user.id)
    if facts: await message.answer(f"📋 **Мои знания о тебе:**\n\n{facts}", parse_mode="Markdown")
    else: await message.answer("Я пока ничего о тебе не знаю.")

@router.message(F.text == "🐙 GitHub Деплой")
async def btn_github(message: Message, state: FSMContext):
    settings = await get_settings(message.from_user.id)
    if settings and settings[0] and settings[1]:
        await message.answer(f"✅ GitHub подключен: `{settings[1]}`", parse_mode="Markdown")
    else:
        await message.answer("🔑 Отправь GitHub Token:", reply_markup=get_cancel_menu())
        await state.set_state(BotStates.waiting_for_github_token)

@router.message(BotStates.waiting_for_github_token)
async def save_gh_token(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO settings (user_id, gh_token) VALUES (?, ?)", (message.from_user.id, message.text.strip()))
        await db.execute("UPDATE settings SET gh_token = ? WHERE user_id = ?", (message.text.strip(), message.from_user.id))
        await db.commit()
    await message.answer("📂 Отправь `логин/репозиторий`:", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.waiting_for_github_repo)

@router.message(BotStates.waiting_for_github_repo)
async def save_gh_repo(message: Message, state: FSMContext):
    if "/" not in message.text:
        await message.answer("❌ Формат: `логин/название`")
        return
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE settings SET gh_repo = ? WHERE user_id = ?", (message.text.strip(), message.from_user.id))
        await db.commit()
    await state.clear()
    await message.answer("✅ GitHub подключен!", reply_markup=get_main_menu())

@router.callback_query(F.data == "do_push_github")
async def cb_push_github(callback: CallbackQuery):
    chat_id = await get_chat_id(callback.from_user.id)
    settings = await get_settings(callback.from_user.id)
    if not settings: return
    await callback.message.edit_text("🐙 Пушу в GitHub...")
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT filename, content FROM project_files WHERE chat_id = ?", (chat_id,))
        files = await cursor.fetchall()
    def push():
        g = Github(settings[0]); repo = g.get_repo(settings[1])
        for fname, code in files:
            try:
                contents = repo.get_contents(fname); repo.update_file(contents.path, f"Update {fname}", code, contents.sha)
            except: repo.create_file(fname, f"Create {fname}", code)
        return "✅ Загружено!"
    try:
        res = await asyncio.to_thread(push)
        await callback.message.edit_text(res)
    except Exception as e: await callback.message.edit_text(f"❌ {e}")

@router.callback_query(F.data == "cancel_push")
async def cb_cancel_push(callback: CallbackQuery):
    await callback.message.edit_text("Ок, оставил код в чате.")

@router.message(F.text == "🧹 Очистить чат")
async def btn_clear(message: Message):
    chat_id = await get_chat_id(message.from_user.id)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM history WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM project_files WHERE chat_id = ?", (chat_id,))
        await db.execute("UPDATE chats SET summary = '' WHERE id = ?", (chat_id,))
        await db.commit()
    await message.answer("🧹 Чат очищен. (Факты о тебе сохранены).", reply_markup=get_main_menu())

@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear(); await callback.message.edit_text("❌ Отменено."); await callback.answer()

@router.message(F.text)
async def handle_text(message: Message):
    chat_id = await get_chat_id(message.from_user.id)
    text = message.text
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", text))
        await db.commit()
    await ai_request_stream(message, message.from_user.id, chat_id)

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    asyncio.create_task(reminder_worker(bot))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    print("МЕГА-БОТ ЗАПУЩЕН!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
