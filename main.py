import asyncio
import logging
import os
import re
import base64
import io
import json
import aiohttp
import aiosqlite
import datetime
from github import Github
from docx import Document
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BufferedInputFile
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
GH_TOKEN = os.getenv("GH_TOKEN")
GH_REPO = os.getenv("GH_REPO")
DB_FILE = "bot_memory.db"

if not BOT_TOKEN or not XEROSHARC_API_KEY:
    raise ValueError("Не заданы BOT_TOKEN или XEROSHARC_API_KEY!")

router = Router()
client = AsyncOpenAI(api_key=XEROSHARC_API_KEY, base_url=XEROSHARC_BASE_URL, timeout=120.0, max_retries=0)

class BotStates(StatesGroup):
    waiting_for_chat_name = State()
    waiting_for_github_token = State()
    waiting_for_github_repo = State()

# --- КОНВЕРТАЦИЯ В HTML (ЧТОБЫ НЕ БЫЛО ЗВЕЗДОЧЕК) ---
def convert_md_to_html(text):
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
    text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
    text = re.sub(r'```(?:\w+)?\n(.*?)```', r'<pre>\1</pre>', text, flags=re.DOTALL)
    return text

# --- СИСТЕМНЫЙ ПРОМПТ (С РЕЖИМАМИ) ---
def get_base_prompt(mode="Обычный"):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    prompts = {
        "Обычный": "Ты — умный ИИ-ассистент. Отвечай дружелюбно и по делу.",
        "Сеньор": "Ты — строгий Senior-разработчик. Пиши только чистый код, без лишней воды и сюсюканий.",
        "Учитель": "Ты — терпеливый учитель. Объясняй каждую строчку так, будто я полный новичок."
    }
    return (
        f"Сейчас: {now}.\n"
        f"{prompts.get(mode, prompts['Обычный'])}\n"
        "ПРАВИЛА:\n"
        "1. Если просят код, оборачивай его: ```python filename=\"main.py\"\n...\n```.\n"
        "2. Если просят напомнить, пиши тег: [REMIND: YYYY-MM-DD HH:MM | текст].\n"
        "3. Если просят поискать в интернете: [SEARCH: запрос].\n"
        "4. Если просят сделать документ (Word), напиши тег [MAKE_DOC: название.docx | текст документа].\n"
        "5. Отвечай на русском."
    )

# --- БЕЗОПАСНАЯ ОТПРАВКА ---
async def safe_edit(message: Message, text: str, reply_markup=None):
    try:
        await message.edit_text(convert_md_to_html(text), parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        if "message is not modified" not in str(e) and "message to edit not found" not in str(e):
            try: await message.edit_text(text, reply_markup=reply_markup)
            except: pass

async def safe_answer(message: Message, text: str, reply_markup=None):
    if not text.strip(): return
    try:
        await message.answer(convert_md_to_html(text), parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        await message.answer(text, reply_markup=reply_markup)

# --- СИНХРОНИЗАЦИЯ БД С GITHUB ---
async def restore_db_from_github():
    if not GH_TOKEN or not GH_REPO: return
    if os.path.exists(DB_FILE) and os.path.getsize(DB_FILE) > 0: return
    try:
        def download():
            g = Github(GH_TOKEN); repo = g.get_repo(GH_REPO)
            contents = repo.get_contents(DB_FILE)
            with open(DB_FILE, "wb") as f: f.write(base64.b64decode(contents.content))
        await asyncio.to_thread(download)
    except Exception: pass

async def backup_db_to_github():
    while True:
        await asyncio.sleep(300)
        if not GH_TOKEN or not GH_REPO or not os.path.exists(DB_FILE): continue
        try:
            def upload():
                g = Github(GH_TOKEN); repo = g.get_repo(GH_REPO)
                with open(DB_FILE, "rb") as f: content = f.read()
                try:
                    contents = repo.get_contents(DB_FILE)
                    repo.update_file(contents.path, "Auto-backup DB", content, contents.sha)
                except: repo.create_file(DB_FILE, "Create DB backup", content)
            await asyncio.to_thread(upload)
        except Exception: pass

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, summary TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, role TEXT, content TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS project_files (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, filename TEXT, content TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, remind_time TEXT, text TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, fact_text TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS settings (user_id INTEGER PRIMARY KEY, gh_token TEXT, gh_repo TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, selected_model TEXT DEFAULT 'claude-sonnet-4-6', mode TEXT DEFAULT 'Обычный')")
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
    if GH_TOKEN and GH_REPO: return (GH_TOKEN, GH_REPO)
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT gh_token, gh_repo FROM settings WHERE user_id = ?", (user_id,))
        return await cursor.fetchone()

async def get_user_data(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT selected_model, mode FROM user_settings WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row if row else ("claude-sonnet-4-6", "Обычный")

async def set_user_data(user_id, field, value):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(f"INSERT OR IGNORE INTO user_settings (user_id, {field}) VALUES (?, ?)", (user_id, value))
        await db.execute(f"UPDATE user_settings SET {field} = ? WHERE user_id = ?", (value, user_id))
        await db.commit()

async def get_facts(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT fact_text FROM facts WHERE user_id = ?", (user_id,))
        rows = await cursor.fetchall()
        return "\n".join([f"- {r[0]}" for r in rows]) if rows else ""

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
                    model="claude-haiku-4-5", messages=[{"role": "user", "content": f"Сожми диалог в саммари:\n{text}"}], max_tokens=1000
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
            except Exception: pass 

# --- НАПОМИНАНИЯ ---
async def reminder_worker(bot: Bot):
    while True:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("SELECT id, user_id, text FROM reminders WHERE remind_time <= ?", (now,))
            due = await cursor.fetchall()
            for r in due:
                try: await bot.send_message(r[1], f"⏰ <b>НАПОМИНАНИЕ:</b>\n{r[2]}", parse_mode="HTML")
                except: pass
                await db.execute("DELETE FROM reminders WHERE id = ?", (r[0],))
            await db.commit()
        await asyncio.sleep(30)

# --- ЗАПУСК КОДА (PISTON API) ---
async def execute_code(code: str, lang: str):
    url = "https://emkc.org/api/v2/piston/execute"
    payload = {"language": lang, "version": "*", "files": [{"content": code}]}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if "run" in data: return data["run"].get("output", "Нет вывода")
                return "Ошибка выполнения"
    except Exception as e:
        return f"Ошибка API: {e}"

# --- ГЛАВНАЯ ЛОГИКА ИИ ---
async def ai_request_stream(message: Message, user_id: int, chat_id: int):
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT summary FROM chats WHERE id = ?", (chat_id,))
        row = await cursor.fetchone()
        summary = row[0] if row and row[0] else ""
        cursor = await db.execute("SELECT role, content FROM history WHERE chat_id = ? ORDER BY id DESC LIMIT 7", (chat_id,))
        history = [{"role": r[0], "content": r[1]} for r in reversed(await cursor.fetchall())]
        
    facts = await get_facts(user_id)
    model, mode = await get_user_data(user_id)
    
    sys_prompt = get_base_prompt(mode)
    if summary: sys_prompt += f"\n\nСАММАРИ:\n{summary}"
    if facts: sys_prompt += f"\n\nФАКТЫ О ПОЛЬЗОВАТЕЛЕ:\n{facts}"
    
    msgs = [{"role": "system", "content": sys_prompt}] + history
    full_text = ""
    msg = await message.answer("⏳ Думаю...")
    
    models_to_try = list(dict.fromkeys([model, "claude-sonnet-4-6", "gpt-6.1-sol"]))
    
    for m in models_to_try:
        try:
            stream = await client.chat.completions.create(
                model=m, messages=msgs, max_tokens=4000, stream=True 
            )
            
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    full_text += chunk.choices[0].delta.content
                    if len(full_text) % 30 == 0:
                        display_text = re.sub(r'\[(REMIND|SEARCH|MAKE_DOC).*?\]', '', full_text)
                        await safe_edit(msg, display_text + " █") 
                        
            display_text = re.sub(r'\[(REMIND|SEARCH|MAKE_DOC).*?\]', '', full_text).strip()
            await safe_edit(msg, display_text if display_text else "Готово.")
            
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", full_text))
                await db.commit()

            # --- ОБРАБОТКА ТЕГОВ ---
            remind_match = re.search(r'\[REMIND:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*\|\s*(.*?)\]', full_text)
            if remind_match:
                dt_str, rem_text = remind_match.groups()
                async with aiosqlite.connect(DB_FILE) as db:
                    await db.execute("INSERT INTO reminders (user_id, remind_time, text) VALUES (?, ?, ?)", (user_id, dt_str, rem_text))
                    await db.commit()
                await message.answer(f"⏰ Ок, напомню в {dt_str}:\n<i>{rem_text}</i>", parse_mode="HTML")

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

            doc_match = re.search(r'\[MAKE_DOC:\s*(.*?)\s*\|\s*(.*?)\]', full_text, re.DOTALL)
            if doc_match:
                fname, content = doc_match.groups()
                doc = Document()
                doc.add_paragraph(content.strip())
                file_stream = io.BytesIO()
                doc.save(file_stream)
                file_stream.seek(0)
                file_to_send = BufferedInputFile(file_stream.read(), filename=fname)
                await message.answer_document(document=file_to_send, caption=f"📄 Документ `{fname}` готов!")
                return

            # Кнопки управления
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Заново", callback_data="regen_text"),
                 InlineKeyboardButton(text="➡️ Продолжить", callback_data="continue_text")]
            ])
            await message.answer("👇", reply_markup=kb)

            # Проверка на код
            pattern = r"```(?:\w+)?\s*filename=[\"'](.*?)[\"']\n(.*?)```"
            matches = re.findall(pattern, full_text, re.DOTALL)
            if matches:
                async with aiosqlite.connect(DB_FILE) as db:
                    for fname, code in matches:
                        await db.execute("INSERT INTO project_files (chat_id, filename, content) VALUES (?, ?, ?)", (chat_id, fname, code))
                    await db.commit()
                    
                kb_code = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Загрузить в GitHub", callback_data="do_push_github")],
                    [InlineKeyboardButton(text="▶️ Запустить код (Python)", callback_data="exec_py")]
                ])
                await message.answer("👇 Я написал код. Что сделать?", reply_markup=kb_code)

            asyncio.create_task(summarize_chat_async(chat_id))
            return

        except Exception as e:
            if m == models_to_try[-1]:
                try: await msg.delete()
                except: pass
                await message.answer("⚠️ **Сервера xerosharc сейчас лежат.**\nПопробуй через 5 минут.")
                return
            else:
                await safe_edit(msg, f"⏳ {m} перегружен. Пробую резервную...")

# --- ОСНОВНОЕ МЕНЮ ---
def get_main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎭 Режимы"), KeyboardButton(text="🤖 Модель")],
        [KeyboardButton(text="📂 Мои диалоги"), KeyboardButton(text="➕ Новый диалог")],
        [KeyboardButton(text="🧠 Память"), KeyboardButton(text="🐙 GitHub")],
        [KeyboardButton(text="🧹 Очистить чат")]
    ], resize_keyboard=True)

def get_models_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Claude Haiku 4.5 (Быстрая)", callback_data="set_model_claude-haiku-4-5")],
        [InlineKeyboardButton(text="Claude Sonnet 4.6 (Средняя)", callback_data="set_model_claude-sonnet-4-6")],
        [InlineKeyboardButton(text="Claude Opus 5.5 (Умная)", callback_data="set_model_claude-opus-5-5")],
        [InlineKeyboardButton(text="GPT-6.1 Sol", callback_data="set_model_gpt-6.1-sol")]
    ])

# --- ХЕНДЛЕРЫ КОМАНД ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    await get_chat_id(message.from_user.id)
    await message.answer("👋 Привет! Я твой ТОП ИИ-ассистент.\nТеперь я понимаю голос, делаю документы и запускаю код!", reply_markup=get_main_menu())

@router.message(F.text == "🎭 Режимы")
async def btn_modes(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обычный", callback_data="mode_Обычный")],
        [InlineKeyboardButton(text="Сеньор (только код)", callback_data="mode_Сеньор")],
        [InlineKeyboardButton(text="Учитель (всё объясняет)", callback_data="mode_Учитель")]
    ])
    await message.answer("Выбери режим общения:", reply_markup=kb)

@router.callback_query(F.data.startswith("mode_"))
async def cb_mode(callback: CallbackQuery):
    mode = callback.data.replace("mode_", "")
    await set_user_data(callback.from_user.id, "mode", mode)
    await callback.message.edit_text(f"✅ Режим изменен на **{mode}**", parse_mode="Markdown")

@router.message(F.text == "🤖 Модель")
async def btn_models(message: Message):
    model, _ = await get_user_data(message.from_user.id)
    await message.answer(f"Текущая модель: `{model}`\n\nВыбери новую:", reply_markup=get_models_menu(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("set_model_"))
async def cb_set_model(callback: CallbackQuery):
    new_model = callback.data.replace("set_model_", "")
    await set_user_data(callback.from_user.id, "selected_model", new_model)
    await callback.message.edit_text(f"✅ Модель изменена на `{new_model}`!", parse_mode="Markdown")
    await callback.answer()

@router.message(F.text == "🧠 Память")
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

@router.message(F.text == "➕ Новый диалог")
async def btn_new_chat(message: Message, state: FSMContext):
    await message.answer("Название нового диалога:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]))
    await state.set_state(BotStates.waiting_for_chat_name)

@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear(); await callback.message.edit_text("❌ Отменено."); await callback.answer()

@router.message(BotStates.waiting_for_chat_name)
async def create_chat(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO chats (user_id, title) VALUES (?, ?)", (message.from_user.id, message.text.strip()))
        await db.commit()
    await state.clear()
    await message.answer(f"✅ Создан диалог: **{message.text}**", reply_markup=get_main_menu(), parse_mode="Markdown")

@router.message(F.text == "📂 Мои диалоги")
async def btn_chats(message: Message):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT id, title FROM chats WHERE user_id = ? ORDER BY id DESC", (message.from_user.id,))
        chats = await cursor.fetchall()
    kb = [[InlineKeyboardButton(text=title, callback_data=f"switch_{cid}")] for cid, title in chats]
    await message.answer("Выбери диалог:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("switch_"))
async def switch_chat(callback: CallbackQuery):
    try: await callback.message.edit_text("✅ Диалог выбран.")
    except: pass
    await callback.answer()

# --- GITHUB ---
@router.message(F.text == "🐙 GitHub")
async def btn_github(message: Message, state: FSMContext):
    settings = await get_settings(message.from_user.id)
    if settings and settings[0] and settings[1]:
        await message.answer(f"✅ GitHub подключен: `{settings[1]}`", parse_mode="Markdown")
        chat_id = await get_chat_id(message.from_user.id)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", f"[СИСТЕМА: GitHub подключен. Репозиторий: {settings[1]}]"))
            await db.commit()
    else:
        await message.answer("🔑 Отправь GitHub Token:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]))
        await state.set_state(BotStates.waiting_for_github_token)

@router.message(BotStates.waiting_for_github_token)
async def save_gh_token(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO settings (user_id, gh_token) VALUES (?, ?)", (message.from_user.id, message.text.strip()))
        await db.execute("UPDATE settings SET gh_token = ? WHERE user_id = ?", (message.text.strip(), message.from_user.id))
        await db.commit()
    await message.answer("📂 Отправь `логин/репозиторий`:")
    await state.set_state(BotStates.waiting_for_github_repo)

@router.message(BotStates.waiting_for_github_repo)
async def save_gh_repo(message: Message, state: FSMContext):
    if "/" not in message.text: return await message.answer("❌ Формат: `логин/название`")
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

# --- ГОЛОСОВЫЕ ---
@router.message(F.voice)
async def handle_voice(message: Message):
    status_msg = await message.answer("🎙 Слушаю голосовое...")
    try:
        file_info = await message.bot.get_file(message.voice.file_id)
        file_bytes = await message.bot.download_file(file_info.file_path)
        file_obj = io.BytesIO(file_bytes.read())
        file_obj.name = "voice.ogg"
        
        response = await client.audio.transcriptions.create(model="whisper-1", file=file_obj)
        text = response.text
        await status_msg.edit_text(f"🎙 Ты сказал: <i>{text}</i>", parse_mode="HTML")
        
        chat_id = await get_chat_id(message.from_user.id)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", text))
            await db.commit()
        await ai_request_stream(message, message.from_user.id, chat_id)
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Ошибка распознавания (прокси не поддерживает Whisper):\n{e}")

# --- ЗАПУСК КОДА ---
@router.callback_query(F.data == "exec_py")
async def cb_exec_code(callback: CallbackQuery):
    chat_id = await get_chat_id(callback.from_user.id)
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT content FROM project_files WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,))
        row = await cursor.fetchone()
    if not row: return await callback.answer("Код не найден!", show_alert=True)
    await callback.message.edit_text("⏳ Запускаю код...")
    result = await execute_code(row[0], "python")
    await callback.message.answer(f"▶️ **Результат:**\n<pre>{result}</pre>", parse_mode="HTML")

# --- КНОПКИ ПЕРЕГЕНЕРАЦИИ ---
@router.callback_query(F.data == "regen_text")
async def cb_regen(callback: CallbackQuery):
    chat_id = await get_chat_id(callback.from_user.id)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM history WHERE chat_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1", (chat_id,))
        cursor = await db.execute("SELECT content FROM history WHERE chat_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1", (chat_id,))
        row = await cursor.fetchone()
        await db.commit()
    if row:
        await callback.message.delete()
        await ai_request_stream(callback.message, callback.from_user.id, chat_id)

@router.callback_query(F.data == "continue_text")
async def cb_continue(callback: CallbackQuery):
    chat_id = await get_chat_id(callback.from_user.id)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", "Продолжи ответ с того места, где ты остановился."))
        await db.commit()
    await callback.message.delete()
    await ai_request_stream(callback.message, callback.from_user.id, chat_id)

# --- ФОТО И ДОКУМЕНТЫ ---
@router.message(F.photo)
async def handle_photo(message: Message):
    chat_id = await get_chat_id(message.from_user.id)
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
        
        _, mode = await get_user_data(message.from_user.id)
        sys_prompt = get_base_prompt(mode)
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
    chat_id = await get_chat_id(message.from_user.id)
    if message.document.file_size > 20 * 1024 * 1024: return await message.answer("Файл слишком большой.")
    status_msg = await message.answer("⏳ Читаю файл...")
    try:
        file_info = await message.bot.get_file(message.document.file_id)
        file_bytes = await message.bot.download_file(file_info.file_path)
        raw_data = file_bytes.read()
        try: extracted_text = raw_data.decode('utf-8')
        except:
            await status_msg.delete()
            return await message.answer("Могу читать только текстовые файлы.")
        prompt = f"Файл `{message.document.file_name}`:\n\n```\n{extracted_text[:20000]}\n```\n\n{message.caption or 'Проанализируй.'}"
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", prompt))
            await db.commit()
        await status_msg.delete()
        await ai_request_stream(message, message.from_user.id, chat_id)
    except Exception as e:
        await status_msg.delete()
        await message.answer(f"⚠️ Ошибка при чтении файла: {e}")

# --- ОСНОВНОЙ ТЕКСТОВЫЙ ХЕНДЛЕР ---
@router.message(F.text)
async def handle_text(message: Message):
    chat_id = await get_chat_id(message.from_user.id)
    text = message.text
    text_lower = text.lower()
    
    if text_lower.startswith("запомни") or text_lower.startswith("запомни,"):
        fact = text[7:].strip(" ,.")
        if fact:
            await add_fact(message.from_user.id, fact)
            await message.answer(f"🧠 Запомнил: <i>{fact}</i>", parse_mode="HTML")
            return

    settings = await get_settings(message.from_user.id)
    
    if settings and settings[0] and settings[1]:
        # ПЕРЕХВАТ ФАЙЛОВ / GITHUB
        if re.search(r'(файл|репо|гит|шит|пусто|github|гитхаб)', text_lower):
            status = await message.answer("⏳ Смотрю файлы в GitHub...")
            def get_files():
                g = Github(settings[0]); repo = g.get_repo(settings[1])
                return [item.path for item in repo.get_contents("")]
            try:
                files = await asyncio.to_thread(get_files)
                if files: res = f"📁 **В репозитории `{settings[1]}`:**\n" + "\n".join([f"- `{f}`" for f in files])
                else: res = f"📁 **Репозиторий `{settings[1]}` пуст.**"
                await status.edit_text(res, parse_mode="Markdown")
                async with aiosqlite.connect(DB_FILE) as db:
                    await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", text))
                    await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", f"[СИСТЕМА: Я проверил GitHub. {res}]"))
                    await db.commit()
            except Exception as e: await status.edit_text(f"❌ Ошибка GitHub: {e}")
            return 

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
    await restore_db_from_github()
    await init_db()
    
    bot = Bot(token=BOT_TOKEN)
    asyncio.create_task(reminder_worker(bot))
    if GH_TOKEN and GH_REPO: asyncio.create_task(backup_db_to_github())
    
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    print("МЕГА-БОТ ЗАПУЩЕН!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
