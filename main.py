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
client = AsyncOpenAI(api_key=XEROSHARC_API_KEY, base_url=XEROSHARC_BASE_URL, timeout=120.0, max_retries=0)

class BotStates(StatesGroup):
    waiting_for_rule = State()
    waiting_for_chat_name = State()
    waiting_for_reminder_text = State()
    waiting_for_github_token = State()
    waiting_for_github_repo = State()

ROLES = {
    "Сеньор": "Ты — строгий Senior-разработчик и Tech Lead. Пиши только код и краткие пояснения.",
    "Учитель": "Ты — терпеливый учитель. Объясняй каждую строчку так, будто я новичок.",
    "Архитектор": "Ты — IT-Архитектор. Предлагай лучшие паттерны и структуру проекта.",
    "Обычный": "Ты — умный ассистент. Отвечай кратко, по делу."
}

BASE_SYSTEM_PROMPT = (
    "{role_prompt}\n"
    "Ты не просто исполнитель. Ты — опытный Tech Lead. Твои правила:\n"
    "1. НИКОГДА не пиши код слепо. Если задача требует уточнений — сначала задай вопросы, предложи варианты.\n"
    "2. Предлагай улучшения. Если пользователь просит сделать что-то неэффективно, предложи лучший вариант.\n"
    "3. Пиши Production-ready код: с обработкой ошибок, логированием и комментариями.\n"
    "4. У тебя ЕСТЬ доступ к GitHub через бэкенд. Если просят загрузить или удалить файлы — просто ответь 'Загружаю...' или 'Удаляю...', бэкенд сам всё сделает.\n"
    "5. Если нужны свежие данные, напиши СТРОГО тег [SEARCH: твой запрос].\n"
    "6. Если пишешь код для проекта, ВСЕГДА указывай имя файла: ```python filename=\"main.py\"\n...\n```.\n"
    "Отвечай на русском."
)

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📂 Мои диалоги"), KeyboardButton(text="➕ Новый диалог")],
        [KeyboardButton(text="🎭 Сменить роль"), KeyboardButton(text="📁 Файлы проекта")],
        [KeyboardButton(text="⏰ Напоминания"), KeyboardButton(text="🐙 GitHub Деплой")],
        [KeyboardButton(text="🧹 Очистить чат"), KeyboardButton(text="🧠 Правила"), KeyboardButton(text="❓ Помощь")]
    ], resize_keyboard=True)

def get_chats_menu(chats):
    kb = [[InlineKeyboardButton(text=title, callback_data=f"switch_{cid}")] for cid, title in chats]
    kb.append([InlineKeyboardButton(text="❌ Удалить все", callback_data="del_chats")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_roles_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=r, callback_data=f"role_{r}")] for r in ROLES.keys()])

def get_cancel_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]])

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, summary TEXT, role TEXT DEFAULT 'Сеньор')")
        await db.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, role TEXT, content TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS project_files (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, filename TEXT, content TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, chat_id INTEGER, remind_time TEXT, text TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS settings (user_id INTEGER PRIMARY KEY, gh_token TEXT, gh_repo TEXT)")
        await db.commit()

async def get_chat_id(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT id FROM chats WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
        row = await cursor.fetchone()
        if row: return row[0]
        cursor = await db.execute("INSERT INTO chats (user_id, title, summary, role) VALUES (?, ?, ?, ?)", (user_id, "Основной", "", "Сеньор"))
        await db.commit()
        return cursor.lastrowid

async def get_settings(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT gh_token, gh_repo FROM settings WHERE user_id = ?", (user_id,))
        return await cursor.fetchone()

# --- УМНАЯ ПАМЯТЬ (СЖАТИЕ) ---
async def check_and_summarize(chat_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT count(*) FROM history WHERE chat_id = ?", (chat_id,))
        count = (await cursor.fetchone())[0]
        
        # Если сообщений больше 25, сжимаем старые 10
        if count > 25: 
            cursor = await db.execute("SELECT id, role, content FROM history WHERE chat_id = ? ORDER BY id ASC LIMIT 10", (chat_id,))
            old_msgs = await cursor.fetchall()
            text_to_summarize = "\n".join([f"{m[1]}: {m[2]}" for m in old_msgs])
            
            try:
                response = await client.chat.completions.create(
                    model="claude-haiku-4-5", 
                    messages=[{"role": "user", "content": f"Сожми этот диалог в краткое саммари, сохранив технические детали, имена файлов и суть:\n\n{text_to_summarize}"}],
                    max_tokens=1000
                )
                new_summary = response.choices[0].message.content
                
                cursor = await db.execute("SELECT summary FROM chats WHERE id = ?", (chat_id,))
                old_summary = (await cursor.fetchone())[0] or ""
                combined_summary = old_summary + "\n" + new_summary
                
                await db.execute("UPDATE chats SET summary = ? WHERE id = ?", (combined_summary[:4000], chat_id))
                
                ids_to_delete = [str(m[0]) for m in old_msgs]
                await db.execute(f"DELETE FROM history WHERE id IN ({','.join(ids_to_delete)})")
                await db.commit()
            except Exception as e:
                logging.error(f"Ошибка сжатия памяти: {e}")

# --- ИНТЕРНЕТ И ПОИСК ---
async def fetch_url(url: str):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://r.jina.ai/{url}", timeout=60) as r:
                return (await r.text())[:40000] if r.status == 200 else "[Ошибка чтения]"
    except: return "[Ошибка]"

async def search_web(query: str):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://s.jina.ai/{query}", timeout=60) as r:
                return (await r.text())[:20000] if r.status == 200 else "[Ошибка поиска]"
    except: return "[Ошибка]"

# --- НАПОМИНАНИЯ ---
async def reminder_worker(bot: Bot):
    while True:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("SELECT id, user_id, chat_id, text FROM reminders WHERE remind_time <= ?", (now,))
            due = await cursor.fetchall()
            for r in due:
                try: await bot.send_message(r[1], f"⏰ **НАПОМИНАНИЕ:**\n{r[3]}", parse_mode="Markdown")
                except: pass
                await db.execute("DELETE FROM reminders WHERE id = ?", (r[0],))
            await db.commit()
        await asyncio.sleep(30)

# --- ОТПРАВКА ФАЙЛОВ ---
async def process_files(message: Message, answer: str):
    answer = re.sub(r"<thinking>.*?</thinking>", "", answer, flags=re.DOTALL).strip()
    
    mer = re.findall(r"```mermaid\n(.*?)```", answer, re.DOTALL)
    for graph in mer:
        b64 = base64.urlsafe_b64encode(graph.strip().encode('utf-8')).decode('utf-8')
        img_url = f"https://mermaid.ink/img/{b64}"
        try:
            await message.answer_photo(photo=img_url, caption="📊 Схема проекта")
            answer = answer.replace(f"```mermaid\n{graph}```", "")
        except: pass

    pattern = r"```(?:\w+)?\s*filename=[\"'](.*?)[\"']\n(.*?)```"
    matches = re.findall(pattern, answer, re.DOTALL)
    clean = re.sub(r"```.*?```", "", answer, flags=re.DOTALL).strip()
    
    if clean: await safe_answer(message, clean)

    if matches:
        if len(matches) > 1:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for fname, code in matches:
                    zf.writestr(fname.strip(), code.strip())
            zip_buffer.seek(0)
            doc = BufferedInputFile(file=zip_buffer.read(), filename="project.zip")
            await message.answer_document(document=doc, caption="📦 Все файлы проекта в ZIP")
        else:
            fname, code = matches[0]
            doc = BufferedInputFile(file=code.strip().encode('utf-8'), filename=fname.strip())
            await message.answer_document(document=doc)

async def safe_answer(message: Message, text: str):
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()
    if not text: return
    try: await message.answer(text, parse_mode="Markdown")
    except: await message.answer(text)

# --- ЗАПРОС К НЕЙРОСЕТИ ---
async def ai_request(message: Message, user_id: int, chat_id: int, status_msg: Message = None, extra: str = ""):
    try: await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except: pass
    
    try:
        await check_and_summarize(chat_id) # Проверяем, не надо ли сжать память
        
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("SELECT summary, role FROM chats WHERE id = ?", (chat_id,))
            summary, role = await cursor.fetchone()
            
            # Берем 15 последних сообщений
            cursor = await db.execute("SELECT role, content FROM history WHERE chat_id = ? ORDER BY id DESC LIMIT 15", (chat_id,))
            history = [{"role": r[0], "content": r[1]} for r in reversed(await cursor.fetchall())]
            
            cursor = await db.execute("SELECT filename, content FROM project_files WHERE chat_id = ?", (chat_id,))
            files = await cursor.fetchall()
            f_ctx = "\n".join([f"--- {f[0]} ---\n{f[1][:1500]}" for f in files]) if files else ""

        sys_prompt = BASE_SYSTEM_PROMPT.format(role_prompt=ROLES.get(role, ROLES["Сеньор"]))
        if summary: sys_prompt += f"\n\nСАММАРИ ПРЕДЫДУЩЕГО ДИАЛОГА:\n{summary}"
        if f_ctx: sys_prompt += f"\n\nФАЙЛЫ ПРОЕКТА:\n{f_ctx}"
        if extra: sys_prompt += f"\n\nКОНТЕКСТ:\n{extra}"

        msgs = [{"role": "system", "content": sys_prompt}] + history
        resp = None
        
        for model in ["claude-sonnet-4-6", "gpt-6.1-sol"]:
            try:
                if status_msg: await status_msg.edit_text(f"⏳ {role} думает...")
                resp = await client.chat.completions.create(model=model, messages=msgs, max_tokens=4000)
                break
            except Exception as e:
                if any(x in str(e) for x in ["429", "503", "404", "502"]) and model != "gpt-6.1-sol": continue
                else: raise e

        ans = resp.choices[0].message.content
        
        search_match = re.search(r'\[SEARCH:\s*(.*?)\]', ans)
        if search_match:
            query = search_match.group(1).strip()
            if status_msg: await status_msg.edit_text(f"🌐 Ищу в интернете: {query}...")
            search_results = await search_web(query)
            
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", f"[Результат поиска '{query}']:\n{search_results}"))
                await db.commit()
                
            await ai_request(message, user_id, chat_id, status_msg, extra=f"Результаты поиска '{query}':\n{search_results}\n\nОтветь на вопрос пользователя, используя эту информацию.")
            return

        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "assistant", ans))
            await db.commit()
        
        if status_msg:
            try: await status_msg.delete()
            except: pass
        await process_files(message, ans)
        
    except Exception as e:
        if status_msg:
            try: await status_msg.delete()
            except: pass
        
        error_str = str(e)
        if "400" in error_str:
            await message.answer("⚠️ **Контекст слишком большой!**\nПожалуйста, нажми кнопку «🧹 Очистить чат», чтобы я сбросил память.")
        else:
            await message.answer(f"⚠️ Ошибка: {e}")

# --- ОБРАБОТЧИКИ КОМАНД И КНОПОК ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    await get_chat_id(message.from_user.id)
    await message.answer("👋 Привет! Я твой продвинутый ИИ-ассистент.\n\nЯ умею искать в интернете, читать файлы, напоминать и пушить код в GitHub!", reply_markup=get_main_menu())

@router.message(F.text == "➕ Новый диалог")
async def btn_new_chat(message: Message, state: FSMContext):
    await message.answer("Напиши название для нового диалога (или нажми кнопку ниже):", reply_markup=get_cancel_menu())
    await state.set_state(BotStates.waiting_for_chat_name)

@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Действие отменено.")
    await callback.answer()

@router.message(BotStates.waiting_for_chat_name)
async def create_chat(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO chats (user_id, title, summary, role) VALUES (?, ?, ?, ?)", (message.from_user.id, message.text.strip(), "", "Сеньор"))
        await db.commit()
    await state.clear()
    await message.answer(f"✅ Создан диалог: **{message.text}**", reply_markup=get_main_menu(), parse_mode="Markdown")

@router.message(F.text == "📂 Мои диалоги")
async def btn_chats(message: Message):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT id, title FROM chats WHERE user_id = ? ORDER BY id DESC", (message.from_user.id,))
        chats = await cursor.fetchall()
    await message.answer("Выбери диалог:", reply_markup=get_chats_menu(chats))

@router.callback_query(F.data.startswith("switch_"))
async def switch_chat(callback: CallbackQuery):
    await callback.message.edit_text("✅ Диалог выбран. Пиши задачу.")
    await callback.answer()

@router.message(F.text == "🐙 GitHub Деплой")
async def btn_github(message: Message, state: FSMContext):
    settings = await get_settings(message.from_user.id)
    if settings and settings[0] and settings[1]:
        await message.answer(f"✅ GitHub подключен.\nРепозиторий: `{settings[1]}`\nПросто попроси меня написать код и скажи 'Загрузи в GitHub'.", parse_mode="Markdown")
    else:
        await message.answer("🔑 Отправь мне свой GitHub Token (с правами repo):", reply_markup=get_cancel_menu())
        await state.set_state(BotStates.waiting_for_github_token)

@router.message(BotStates.waiting_for_github_token)
async def save_gh_token(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO settings (user_id, gh_token) VALUES (?, ?)", (message.from_user.id, message.text.strip()))
        await db.execute("UPDATE settings SET gh_token = ? WHERE user_id = ?", (message.text.strip(), message.from_user.id))
        await db.commit()
    await message.answer("📂 Теперь отправь название репозитория в формате `логин/название`:", reply_markup=get_cancel_menu(), parse_mode="Markdown")
    await state.set_state(BotStates.waiting_for_github_repo)

@router.message(BotStates.waiting_for_github_repo)
async def save_gh_repo(message: Message, state: FSMContext):
    repo_name = message.text.strip()
    if "/" not in repo_name:
        await message.answer("❌ **Ошибка!**\nФормат: `логин/название`.\nНапример: `musaomarov2025-code/NoobGift`", reply_markup=get_cancel_menu())
        return
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE settings SET gh_repo = ? WHERE user_id = ?", (repo_name, message.from_user.id))
        await db.commit()
    await state.clear()
    await message.answer("✅ GitHub успешно подключен!", reply_markup=get_main_menu())

@router.message(F.text == "⏰ Напоминания")
async def btn_reminders(message: Message, state: FSMContext):
    await message.answer("⏰ Напиши время и текст в формате:\n`2026-10-30 18:00 Проверить сервер`", reply_markup=get_cancel_menu(), parse_mode="Markdown")
    await state.set_state(BotStates.waiting_for_reminder_text)

@router.message(BotStates.waiting_for_reminder_text)
async def set_reminder(message: Message, state: FSMContext):
    try:
        parts = message.text.split(" ", 2)
        dt_str = f"{parts[0]} {parts[1]}"
        text = parts[2]
        datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        chat_id = await get_chat_id(message.from_user.id)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO reminders (user_id, chat_id, remind_time, text) VALUES (?, ?, ?, ?)", (message.from_user.id, chat_id, dt_str, text))
            await db.commit()
        await message.answer(f"✅ Напоминание на {dt_str} установлено!", reply_markup=get_main_menu())
    except Exception:
        await message.answer("❌ Неверный формат. Напиши: `2026-10-30 18:00 Купить хлеб`", parse_mode="Markdown")
    await state.clear()

@router.message(F.text == "🎭 Сменить роль")
async def btn_role(message: Message):
    await message.answer("Выбери роль:", reply_markup=get_roles_menu())

@router.callback_query(F.data.startswith("role_"))
async def set_role(callback: CallbackQuery):
    role = callback.data.split("_")[1]
    chat_id = await get_chat_id(callback.from_user.id)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE chats SET role = ? WHERE id = ?", (role, chat_id))
        await db.commit()
    await callback.message.edit_text(f"✅ Роль: **{role}**", parse_mode="Markdown")
    await callback.answer()

# --- ФАЙЛЫ (ЧТЕНИЕ ДОКУМЕНТОВ) ---
@router.message(F.document)
async def handle_document(message: Message):
    chat_id = await get_chat_id(message.from_user.id)
    if message.document.file_size > 20 * 1024 * 1024:
        await message.answer("Файл слишком большой (макс 20 МБ).")
        return
    status = await message.answer("⏳ Читаю файл...")
    try:
        file_bytes = await message.bot.download_file((await message.bot.get_file(message.document.file_id)).file_path)
        raw_data = file_bytes.read()
        extracted_text = ""
        
        if message.document.file_name.lower().endswith('.pdf'):
            reader = PdfReader(io.BytesIO(raw_data))
            for page in reader.pages[:20]: 
                extracted_text += (page.extract_text() or "") + "\n"
        else:
            extracted_text = raw_data.decode('utf-8')

        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO project_files (chat_id, filename, content) VALUES (?, ?, ?)", (chat_id, message.document.file_name, extracted_text))
            await db.commit()
            
        await status.edit_text(f"✅ Файл `{message.document.file_name}` сохранен в проект!", parse_mode="Markdown")
    except Exception as e:
        await status.edit_text(f"Ошибка чтения файла: {e}")

# --- ОСНОВНОЙ ОБРАБОТЧИК ТЕКСТА (С GITHUB ДЕПЛОЕМ И УДАЛЕНИЕМ) ---
@router.message(F.text)
async def handle_text(message: Message):
    chat_id = await get_chat_id(message.from_user.id)
    text = message.text
    text_lower = text.lower()
    
    # --- ЛОГИКА РАБОТЫ С GITHUB (СРАЗУ, БЕЗ УЧАСТИЯ ИИ) ---
    is_delete = any(word in text_lower for word in ["удали", "удалить", "сотри", "очисти"])
    is_push = any(word in text_lower for word in ["загрузи", "запушь", "добавь в гит", "залей"])
    
    if is_delete or is_push:
        settings = await get_settings(message.from_user.id)
        if not settings or not settings[0] or not settings[1]:
            await message.answer("❌ Сначала настрой GitHub через кнопку '🐙 GitHub Деплой'")
            return

        status_msg = await message.answer("🐙 Подключаюсь к GitHub...")
        
        try:
            g = Github(settings[0])
            repo = g.get_repo(settings[1])
            
            # === УДАЛЕНИЕ ===
            if is_delete:
                if "все" in text_lower or "репо" in text_lower:
                    def clear_repo():
                        contents = repo.get_contents("")
                        for f in contents:
                            repo.delete_file(f.path, "Очистка репозитория", f.sha)
                        return "✅ Все файлы удалены из репозитория!"
                    
                    res = await asyncio.to_thread(clear_repo)
                    await status_msg.edit_text(res)
                    return
                else:
                    file_match = re.search(r'([\w\-]+\.\w+)', text)
                    if not file_match:
                        await status_msg.edit_text("❌ Укажи имя файла с расширением (например, `main.py`), который нужно удалить.")
                        return
                    
                    fname = file_match.group(1)
                    await status_msg.edit_text(f"🐙 Ищу и удаляю `{fname}`...")
                    
                    def delete_file():
                        try:
                            contents = repo.get_contents(fname)
                            repo.delete_file(contents.path, f"Удаление {fname}", contents.sha)
                            return f"✅ Файл `{fname}` успешно удален из GitHub!"
                        except Exception as e:
                            return f"❌ Ошибка (возможно файла нет): {e}"
                    
                    res = await asyncio.to_thread(delete_file)
                    await status_msg.edit_text(res)
                    return

            # === ЗАГРУЗКА ===
            if is_push:
                async with aiosqlite.connect(DB_FILE) as db:
                    cursor = await db.execute("SELECT content FROM history WHERE chat_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1", (chat_id,))
                    row = await cursor.fetchone()
                    
                if not row:
                    await status_msg.edit_text("❌ Нет кода для загрузки. Попроси меня сначала написать код.")
                    return
                    
                ans = row[0]
                pattern = r"```(?:\w+)?\s*filename=[\"'](.*?)[\"']\n(.*?)```"
                matches = re.findall(pattern, ans, re.DOTALL)
                
                if not matches:
                    await status_msg.edit_text("❌ Я не нашел в предыдущем сообщении кода с именем файла (формат ```python filename=\"main.py\").")
                    return
                    
                await status_msg.edit_text("🐙 Пушу код в GitHub...")
                
                def push_files():
                    for fname, code in matches:
                        try:
                            contents = repo.get_contents(fname)
                            repo.update_file(contents.path, f"Update {fname}", code, contents.sha)
                        except:
                            repo.create_file(fname, f"Create {fname}", code)
                    return f"✅ Код успешно загружен в `{settings[1]}`!"
                    
                res = await asyncio.to_thread(push_files)
                await status_msg.edit_text(res)
                return

        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка GitHub: {e}")
            return
    # --- КОНЕЦ ЛОГИКИ GITHUB ---

    # Если это не команда для GitHub, идем к нейросети
    status = await message.answer("⏳ Думаю...")
    
    url_match = re.search(r'(https?://[^\s]+)', text)
    if url_match:
        content = await fetch_url(url_match.group(1))
        text = f"[Ссылка]: {content}\n\n[Вопрос]: {text.replace(url_match.group(1), '')}"
        await status.edit_text("⏳ Прочитал ссылку, думаю...")

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, "user", text))
        await db.commit()

    await ai_request(message, message.from_user.id, chat_id, status)

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    asyncio.create_task(reminder_worker(bot))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    # Автоматический сброс вебхука
    await bot.delete_webhook(drop_pending_updates=True)
    
    print("МЕГА-БОТ ЗАПУЩЕН!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
