import asyncio
import logging
import os
import re
import base64
import io
import aiohttp
import aiosqlite
import datetime
from github import Github
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, 
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
GH_TOKEN = os.getenv("GH_TOKEN") # Берем токен из переменных BotHost
GH_REPO = os.getenv("GH_REPO")   # Берем репо из переменных BotHost
DB_FILE = "bot_memory.db"

if not BOT_TOKEN or not XEROSHARC_API_KEY:
    raise ValueError("Не заданы BOT_TOKEN или XEROSHARC_API_KEY!")

router = Router()
client = AsyncOpenAI(api_key=XEROSHARC_API_KEY, base_url=XEROSHARC_BASE_URL, timeout=120.0, max_retries=0)

class BotStates(StatesGroup):
    waiting_for_chat_name = State()
    waiting_for_github_token = State()
    waiting_for_github_repo = State()

def get_base_prompt():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        f"Сейчас: {now}.\n"
        "Ты — умный ИИ-ассистент с доступом к GitHub пользователя через бэкенд. "
        "Если пользователь присылает фото, внимательно изучи его. "
        "Если пользователь спрашивает про файлы, репозиторий, или говорит 'там', 'тут', 'в гитхабе' — ты знаешь, что речь идет о его GitHub-репозитории.\n"
        "ПРАВИЛА:\n"
        "1. Если просят код, оборачивай его: ```python filename=\"main.py\"\n...\n```.\n"
        "2. Если просят напомнить, пиши тег: [REMIND: YYYY-MM-DD HH:MM | текст].\n"
        "3. Если просят поискать в интернете: [SEARCH: запрос].\n"
        "4. Отвечай на русском."
    )

async def safe_edit(message: Message, text: str, parse_mode: str = None, reply_markup=None):
    try:
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        if "message is not modified" not in str(e) and "message to edit not found" not in str(e):
            pass

# --- СИНХРОНИЗАЦИЯ БАЗЫ ДАННЫХ С GITHUB ---
async def restore_db_from_github():
    """Скачивает базу из GitHub при запуске, если локальной нет"""
    if not GH_TOKEN or not GH_REPO: return
    if os.path.exists(DB_FILE) and os.path.getsize(DB_FILE) > 0:
        return # База уже есть, не перезаписываем
        
    try:
        def download():
            g = Github(GH_TOKEN)
            repo = g.get_repo(GH_REPO)
            contents = repo.get_contents(DB_FILE)
            file_data = base64.b64decode(contents.content)
            with open(DB_FILE, "wb") as f:
                f.write(file_data)
        await asyncio.to_thread(download)
        print("✅ База данных успешно восстановлена из GitHub!")
    except Exception as e:
        print(f"⚠️ Не удалось восстановить БД из GitHub (возможно, это первый запуск): {e}")

async def backup_db_to_github():
    """Фоновая задача для сохранения базы в GitHub каждые 5 минут"""
    while True:
        await asyncio.sleep(300) # 5 минут
        if not GH_TOKEN or not GH_REPO or not os.path.exists(DB_FILE): continue
        
        try:
            def upload():
                g = Github(GH_TOKEN)
                repo = g.get_repo(GH_REPO)
                with open(DB_FILE, "rb") as f:
                    content = f.read()
                try:
                    contents = repo.get_contents(DB_FILE)
                    repo.update_file(contents.path, "Auto-backup DB", content, contents.sha)
                except:
                    repo.create_file(DB_FILE, "Create DB backup", content)
            await asyncio.to_thread(upload)
            print("💾 База данных сохранена в GitHub (Auto-backup).")
        except Exception as e:
            print(f"❌ Ошибка автосохранения в GitHub: {e}")

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📂 Мои диалоги"), KeyboardButton(text="➕ Новый диалог")],
        [KeyboardButton(text="🤖 Выбрать модель"), KeyboardButton(text="🐙 GitHub Деплой")],
        [KeyboardButton(text="🧠 Что ты обо мне знаешь?"), KeyboardButton(text="🧹 Очистить чат")],
        [KeyboardButton(text="❓ Помощь")]
    ], resize_keyboard=True)

def get_models_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Claude Haiku 4.5 (Супер-быстрая)", callback_data="set_model_claude-haiku-4-5")],
        [InlineKeyboardButton(text="Claude Sonnet 4.6 (Средняя)", callback_data="set_model_claude-sonnet-4-6")],
        [InlineKeyboardButton(text="Claude Opus 5.5 (Умная)", callback_data="set_model_claude-opus-5-5")],
        [InlineKeyboardButton(text="GPT-6.1 Sol (Альтернатива)", callback_data="set_model_gpt-6.1-sol")]
    ])

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

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, summary TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, role TEXT, content TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS project_files (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, filename TEXT, content TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, remind_time TEXT, text TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, fact_text TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS settings (user_id INTEGER PRIMARY KEY, gh_token TEXT, gh_repo TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, selected_model TEXT DEFAULT 'claude-sonnet-4-6')")
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
    # Если токен и репо есть в переменных окружения, используем их
    if GH_TOKEN and GH_REPO:
        return (GH_TOKEN, GH_REPO)
        
    # Иначе ищем в базе
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT gh_token, gh_repo FROM settings WHERE user_id = ?", (user_id,))
        return await cursor.fetchone()

async def get_facts(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT fact_text FROM facts WHERE user_id = ?", (user_id,))
        rows = await cursor.fetchall()
        return "\n".join([f"- {r[0]}" for r in rows]) if rows else ""

async def get_user_model(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT selected_model FROM user_settings WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else "claude-sonnet-4-6"

async def add_fact(user_id, fact):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO facts (user_id, fact_text) VALUES (?, ?)", (user_id, fact))
        await db.commit()

async def summarize_chat_async(chat_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT count(*) FROM history WHERE chat_id = ?", (chat_id,))
        count = (await cursor.fetchone())[0]
        if count > 20:
            cursor = await db.execute("SELECT id, role, content FROM history WHERE chat_id = ? ORDER BY id ASC LIMIT 10", (chat_id,))
            old_msgs = await cursor.fetchall()
            text = "\n".join([f"{m[1]}: {m[2]}" for m in old_msgs])
            try:
                resp = await client.chat.completions.create(
                    model="claude-haiku-4-5", 
                    messages=[{"role": "user", "content": f"Сожми диалог в саммари:\n{text}"}],
                    max_tokens=1000
                )
                if not resp.choices: return
                new_summary = resp.choices[0].message.content
                cursor = await db.execute("SELECT summary FROM chats WHERE id = ?", (chat_id,))
                row = await cursor.fetchone()
                old_summary = row[0] if row and row[0] else ""
                await db.execute("UPDATE chats SET summary = ? WHERE id = ?", ((old_summary + "\n" + new_summary)[:4000], chat_id))
                ids = [str(m[0]) for m in old_msgs]
                await db.execute(f"DELETE FROM history WHERE id IN ({','.join(ids)})")
                await db.commit()
            except Exception:
                pass 

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
        
        cursor = await db.execute("SELECT role, content FROM history WHERE chat_id = ? ORDER BY id DESC LIMIT 7", (chat_id,))
        history = [{"role": r[0], "content": r[1]} for r in reversed(await cursor.fetchall())]
        
    facts = await get_facts(user_id)
    sys_prompt = get_base_prompt()
    if summary: sys_prompt += f"\n\nСАММАРИ:\n{summary}"
    if facts: sys_prompt += f"\n\nФАКТЫ О ПОЛЬЗОВАТЕЛЕ:\n{facts}"
    
    msgs = [{"role": "system", "content": sys_prompt}] + history
    full_text = ""
    msg = await message.answer("⏳ Думаю...")
    
    user_model = await get_user_model(user_id)
    models_to_try = list(dict.fromkeys([user_model, "claude-sonnet-4-6", "gpt-6.1-sol"]))
    
    for model in models_to_try:
        try:
            stream = await client.chat.completions.create(
                model=model, 
                messages=msgs,
                max_tokens=4000,
                stream=True 
            )
            
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    full_text += chunk.choices[0].delta.content
                    if len(full_text) % 30 == 0:
                        display_text = re.sub(r'\[(REMIND|SEARCH).*?\]', '', full_text)
                        await safe_edit(msg, display_text + " █") 
                        
            display_text = re.sub(r'\[(REMIND|SEARCH).*?\]', '', full_text).strip()
            await safe_edit(msg, display_text if display_text else "Готово.")
            
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", full_text))
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
                    await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", f"[Поиск '{query}']:\n{res}"))
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
            return

        except Exception as e:
            if model == models_to_try[-1]:
                try: await msg.delete()
                except: pass
                await message.answer("⚠️ **Сервера xerosharc сейчас лежат.**\nПопробуй через 5 минут.")
                return
            else:
                await safe_edit(msg, f"⏳ {model} перегружен. Пробую резервную...")

# --- ОБРАБОТЧИКИ ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    await get_chat_id(message.from_user.id)
    await message.answer("👋 Привет! Я твой ИИ-ассистент.\nТеперь я автоматически сохраняю свои мозги в GitHub!", reply_markup=get_main_menu())

@router.message(F.text == "🤖 Выбрать модель")
async def btn_models(message: Message):
    current = await get_user_model(message.from_user.id)
    await message.answer(f"Текущая модель: `{current}`\n\nВыбери новую:", reply_markup=get_models_menu(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("set_model_"))
async def cb_set_model(callback: CallbackQuery):
    new_model = callback.data.replace("set_model_", "")
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO user_settings (user_id, selected_model) VALUES (?, ?)", (callback.from_user.id, new_model))
        await db.execute("UPDATE user_settings SET selected_model = ? WHERE user_id = ?", (new_model, callback.from_user.id))
        await db.commit()
    await callback.message.edit_text(f"✅ Модель изменена на `{new_model}`!", parse_mode="Markdown")
    await callback.answer()

@router.message(F.text == "📂 Мои диалоги")
async def btn_chats(message: Message):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT id, title FROM chats WHERE user_id = ? ORDER BY id DESC", (message.from_user.id,))
        chats = await cursor.fetchall()
    await message.answer("Выбери диалог:", reply_markup=get_chats_menu(chats))

@router.callback_query(F.data.startswith("switch_"))
async def switch_chat(callback: CallbackQuery):
    try: await callback.message.edit_text("✅ Диалог выбран.")
    except: pass
    await callback.answer()

@router.message(F.text == "➕ Новый диалог")
async def btn_new_chat(message: Message, state: FSMContext):
    await message.answer("Название нового диалога:", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.waiting_for_chat_name)

@router.message(BotStates.waiting_for_chat_name)
async def create_chat(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO chats (user_id, title) VALUES (?, ?)", (message.from_user.id, message.text.strip()))
        await db.commit()
    await state.clear()
    await message.answer(f"✅ Создан диалог: **{message.text}**", reply_markup=get_main_menu(), parse_mode="Markdown")

@router.message(F.text == "🧠 Что ты обо мне знаешь?")
async def btn_facts(message: Message):
    facts = await get_facts(message.from_user.id)
    if facts: await message.answer(f"📋 **Мои знания о тебе:**\n\n{facts}", parse_mode="Markdown")
    else: await message.answer("Я пока ничего о тебе не знаю. Напиши 'Запомни, что...' чтобы я запомнил.")

@router.message(F.text == "🧹 Очистить чат")
async def btn_clear(message: Message):
    chat_id = await get_chat_id(message.from_user.id)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM history WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM project_files WHERE chat_id = ?", (chat_id,))
        await db.execute("UPDATE chats SET summary = '' WHERE id = ?", (chat_id,))
        await db.commit()
    await message.answer("🧹 Чат очищен.", reply_markup=get_main_menu())

@router.message(F.text == "❓ Помощь")
async def btn_help(message: Message):
    await message.answer("Просто пиши задачи! Я ищу в интернете, помню контекст и управляю твоим GitHub.")

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
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", "[СИСТЕМА: Пользователь загрузил код в GitHub]"))
            await db.commit()
    except Exception as e: await callback.message.edit_text(f"❌ {e}")

@router.callback_query(F.data == "cancel_push")
async def cb_cancel_push(callback: CallbackQuery):
    await callback.message.edit_text("Ок, оставил код в чате.")

@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear(); await callback.message.edit_text("❌ Отменено."); await callback.answer()

# --- ФОТО ---
@router.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    chat_id = await get_chat_id(user_id)
    status_msg = await message.answer("⏳ Смотрю скриншот...")
    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file_info.file_path)
        base64_image = base64.b64encode(file_bytes.read()).decode('utf-8')
        caption = message.caption or "Разбери это изображение."
        
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", f"[Фото]: {caption}"))
            await db.commit()
        
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("SELECT summary FROM chats WHERE id = ?", (chat_id,))
            row = await cursor.fetchone()
            summary = row[0] if row and row[0] else ""
            cursor = await db.execute("SELECT role, content FROM history WHERE chat_id = ? ORDER BY id DESC LIMIT 7", (chat_id,))
            history = [{"role": r[0], "content": r[1]} for r in reversed(await cursor.fetchall())]
        
        sys_prompt = get_base_prompt()
        if summary: sys_prompt += f"\n\nСАММАРИ:\n{summary}"
        
        content = [{"type": "text", "text": caption}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]
        msgs = [{"role": "system", "content": sys_prompt}] + history[:-1] + [{"role": "user", "content": content}]
        
        response = await client.chat.completions.create(model="claude-sonnet-4-6", messages=msgs, max_tokens=4000)
        answer = response.choices[0].message.content
        
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", answer))
            await db.commit()
            
        await status_msg.delete()
        await safe_answer(message, answer)

    except Exception as e:
        await status_msg.delete()
        await message.answer(f"⚠️ Ошибка при чтении фото: {e}")

@router.message(F.document)
async def handle_document(message: Message):
    user_id = message.from_user.id
    chat_id = await get_chat_id(user_id)
    if message.document.file_size > 20 * 1024 * 1024:
        await message.answer("Файл слишком большой.")
        return
    status_msg = await message.answer("⏳ Читаю файл...")
    try:
        file_info = await message.bot.get_file(message.document.file_id)
        file_bytes = await message.bot.download_file(file_info.file_path)
        raw_data = file_bytes.read()
        try: extracted_text = raw_data.decode('utf-8')
        except:
            await status_msg.delete()
            await message.answer("Могу читать только текстовые файлы.")
            return
        prompt = f"Файл `{message.document.file_name}`:\n\n```\n{extracted_text[:20000]}\n```\n\n{message.caption or 'Проанализируй.'}"
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", prompt))
            await db.commit()
        await status_msg.delete()
        await ai_request_stream(message, user_id, chat_id)
    except Exception as e:
        await status_msg.delete()
        await message.answer(f"⚠️ Ошибка при чтении файла: {e}")

# --- ОСНОВНОЙ ХЕНДЛЕР ---
@router.message(F.text)
async def handle_text(message: Message):
    chat_id = await get_chat_id(message.from_user.id)
    text = message.text
    text_lower = text.lower()
    
    if text_lower.startswith("запомни") or text_lower.startswith("запомни,"):
        fact = text[7:].strip(" ,.")
        if fact:
            await add_fact(message.from_user.id, fact)
            await message.answer(f"🧠 Запомнил: *{fact}*", parse_mode="Markdown")
            return

    settings = await get_settings(message.from_user.id)
    
    if settings and settings[0] and settings[1]:
        if any(word in text_lower for word in ["удали", "удалить", "сотри", "очисти"]):
            status = await message.answer("⏳ Работаю с GitHub...")
            def process_deletion():
                g = Github(settings[0]); repo = g.get_repo(settings[1])
                if "все" in text_lower or "репо" in text_lower:
                    def delete_recursive(content):
                        count = 0
                        if isinstance(content, list):
                            for item in content: count += delete_recursive(item)
                        else:
                            if content.type == "dir": count += delete_recursive(repo.get_contents(content.path))
                            else:
                                repo.delete_file(content.path, "Очистка", content.sha)
                                count += 1
                        return count
                    total = delete_recursive(repo.get_contents(""))
                    return f"✅ Репозиторий очищен! Удалено: {total}."
                
                file_matches = re.findall(r'([a-zA-Z0-9_\-\.\/]+\.[a-zA-Z0-9]+)', text)
                if not file_matches: return "❌ Напиши точнее: 'Удали main.py'"
                deleted_count = 0; errors = []
                for fname in file_matches:
                    try:
                        contents = repo.get_contents(fname)
                        if isinstance(contents, list): errors.append(f"`{fname}` - папка.")
                        else:
                            repo.delete_file(contents.path, f"Удаление {fname}", contents.sha)
                            deleted_count += 1
                    except Exception as e: errors.append(f"`{fname}`: {e}")
                msg = f"✅ Успешно удалено: {deleted_count}.\n" if deleted_count > 0 else ""
                if errors: msg += "❌ Ошибки:\n" + "\n".join(errors)
                return msg if msg else "❌ Ничего не удалено."
            try:
                res = await asyncio.to_thread(process_deletion)
                await status.edit_text(res)
                async with aiosqlite.connect(DB_FILE) as db:
                    await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", text))
                    await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", f"[СИСТЕМА: Я выполнил удаление. {res}]"))
                    await db.commit()
            except Exception as e: await status.edit_text(f"❌ Ошибка GitHub: {e}")
            return 

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", text))
        await db.commit()
    await ai_request_stream(message, message.from_user.id, chat_id)

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # Восстанавливаем базу из GitHub ДО запуска бота
    await restore_db_from_github()
    await init_db()
    
    bot = Bot(token=BOT_TOKEN)
    asyncio.create_task(reminder_worker(bot))
    
    # Запускаем фоновое сохранение в GitHub
    if GH_TOKEN and GH_REPO:
        asyncio.create_task(backup_db_to_github())
    
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    print("БОТ ЗАПУЩЕН! (С авто-сохранением в GitHub)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
