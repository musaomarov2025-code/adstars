import asyncio
import base64
import io
import logging
import os
import re
import aiohttp
import aiosqlite
from bs4 import BeautifulSoup
from pypdf import PdfReader

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
    raise ValueError("Не заданы переменные окружения!")

MODEL_ID = "claude-sonnet-4-6" 

router = Router()
client = AsyncOpenAI(
    api_key=XEROSHARC_API_KEY, 
    base_url=XEROSHARC_BASE_URL,
    timeout=180.0 
)

# --- СОСТОЯНИЯ (ДЛЯ ДОБАВЛЕНИЯ ПРАВИЛ) ---
class BotStates(StatesGroup):
    waiting_for_rule = State()

# --- ЖЕСТКИЙ ПРОМПТ ---
BASE_SYSTEM_PROMPT = (
    "Ты — профессиональный программист и ИИ-ассистент. "
    "ВНИМАНИЕ: Твой бэкенд (бот) уже скачал текст по ссылке и передал его тебе. "
    "ГЛАВНОЕ ПРАВИЛО: Если пользователь прислал текст или ссылку, КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выводить этот текст обратно или пересказывать его. "
    "Ты должен ответить ОДНОЙ короткой фразой, например: 'Я прочитал этот материал. Что нужно с ним сделать?' или 'Изучил ТЗ. Приступаем к написанию кода?'. "
    "Если пользователь просит нарисовать фото, ответь: 'Я текстовая модель и не умею генерировать изображения'. "
    "ФОРМАТИРОВАНИЕ: Отвечай с использованием Markdown. Для жирного текста используй **текст**. "
    "КРИТИЧЕСКИ ВАЖНО: Если ты пишешь код целого файла, ВСЕГДА указывай его имя и язык в начале блока. "
    "Пример: ```python filename=\"main.py\"\nprint('hi')\n```. "
    "Если это просто короткий пример (1-5 строк), пиши без имени файла. "
    "Пиши на русском, кратко и по делу."
)

# --- КЛАВИАТУРЫ ---
def get_main_menu() -> ReplyKeyboardMarkup:
    """Главное меню внизу экрана"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧠 Мои правила"), KeyboardButton(text="🧹 Очистить память")],
            [KeyboardButton(text="❓ Помощь")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Отправь текст, файл или ссылку..."
    )
    return keyboard

def get_rules_menu() -> InlineKeyboardMarkup:
    """Кнопки под списком правил"""
    builder = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить правило", callback_data="add_rule")],
        [InlineKeyboardButton(text="🗑 Удалить все правила", callback_data="clear_rules")]
    ])
    return builder

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                rule_text TEXT
            )
        """)
        await db.commit()

async def add_to_history(user_id: int, role: str, content: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
        await db.commit()

async def get_history(user_id: int, limit: int = 50):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
        rows = await cursor.fetchall()
        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]

async def clear_history(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        await db.commit()

async def add_rule(user_id: int, rule_text: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO rules (user_id, rule_text) VALUES (?, ?)", (user_id, rule_text))
        await db.commit()

async def get_rules(user_id: int) -> str:
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT rule_text FROM rules WHERE user_id = ?", (user_id,))
        rows = await cursor.fetchall()
        if not rows: return ""
        rules = "\n".join([f"- {row[0]}" for row in rows])
        return f"\n\nВАЖНЫЕ ПРАВИЛА ОТ ПОЛЬЗОВАТЕЛЯ:\n{rules}"

async def clear_rules(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM rules WHERE user_id = ?", (user_id,))
        await db.commit()

async def list_rules(user_id: int) -> list:
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT rule_text FROM rules WHERE user_id = ?", (user_id,))
        return await cursor.fetchall()

# --- ПАРСИНГ И ОТПРАВКА ---
async def fetch_url_text(url: str) -> str:
    try:
        jina_url = f"https://r.jina.ai/{url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(jina_url, timeout=60) as response:
                if response.status == 200:
                    text = await response.text()
                    return text[:80000]
                return "[Ошибка чтения]"
    except Exception as e:
        return f"[Ошибка: {e}]"

async def safe_answer(message: Message, text: str):
    if not text.strip(): return
    try:
        await message.answer(text, parse_mode="Markdown")
    except Exception:
        await message.answer(text)

def split_long_text(text: str, max_length: int = 4000) -> list:
    parts = []
    while len(text) > max_length:
        split_idx = text.rfind('\n', 0, max_length)
        if split_idx == -1: split_idx = max_length
        parts.append(text[:split_idx])
        text = text[split_idx:].lstrip()
    parts.append(text)
    return parts

async def send_code_files(message: Message, answer: str):
    pattern = r"```(?:(\w+)?\s*(?:filename=[\"'](.*?)[\"'])?)\n(.*?)```"
    matches = re.findall(pattern, answer, re.DOTALL)
    clean_text = re.sub(r"```.*?```", "", answer, flags=re.DOTALL).strip()
    
    if clean_text:
        for chunk in split_long_text(clean_text):
            await safe_answer(message, chunk)

    if matches:
        for m in matches:
            lang, custom_filename, code = m
            lang = lang or "txt"
            code_stripped = code.strip()

            # Если имя файла указано ИЛИ код длинный (>150 символов) — шлем файлом
            if custom_filename or len(code_stripped) > 150:
                filename = custom_filename.replace('"', '').replace("'", "").strip() if custom_filename else f"snippet.{lang}"
                file_bytes = code_stripped.encode('utf-8')
                doc = BufferedInputFile(file=file_bytes, filename=filename)
                await message.answer_document(document=doc)
            else:
                # Короткий код шлем текстом
                await safe_answer(message, f"```{lang}\n{code_stripped}\n```")
    elif not clean_text:
        for chunk in split_long_text(answer):
            await safe_answer(message, chunk)

# --- ЗАПРОС К НЕЙРОСЕТИ ---
async def process_ai_response(message: Message, user_id: int, status_msg: Message = None):
    try: await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except: pass

    try:
        user_rules = await get_rules(user_id)
        final_system_prompt = BASE_SYSTEM_PROMPT + user_rules
        
        history = await get_history(user_id, limit=50)
        messages = [{"role": "system", "content": final_system_prompt}] + history
        
        response = await client.chat.completions.create(
            model=MODEL_ID,
            messages=messages,
            max_tokens=8000,
        )
        answer = response.choices[0].message.content
        await add_to_history(user_id, "assistant", answer)
        
        if status_msg:
            try: await status_msg.delete()
            except: pass
                
        await send_code_files(message, answer)

    except Exception as e:
        if status_msg:
            try: await status_msg.delete()
            except: pass
        logging.exception("Ошибка API")
        if "503" in str(e) or "502" in str(e):
            await message.answer("⚠️ Сервера перегружены. Подожди минуту.")
        else:
            await message.answer(f"Произошла ошибка: {e}")

# --- КОМАНДЫ И КНОПКИ ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я твой ИИ-разработчик.\n\n"
        "Пользуйся кнопками внизу экрана. Скидывай мне ТЗ, ссылки, фото или файлы — я всё прочитаю и выполню.",
        reply_markup=get_main_menu()
    )

@router.message(F.text == "🧹 Очистить память")
async def btn_clear(message: Message):
    await clear_history(message.from_user.id)
    await message.answer("🧹 Память диалога очищена.", reply_markup=get_main_menu())

@router.message(F.text == "❓ Помощь")
async def btn_help(message: Message):
    await message.answer(
        "Я умею:\n"
        "• Писать код и отправлять его файлами.\n"
        "• Читать ссылки (через Jina AI).\n"
        "• Читать PDF и фото.\n"
        "• Учиться на твоих правилах.\n\n"
        "Просто пиши мне задачу!",
        reply_markup=get_main_menu()
    )

@router.message(F.text == "🧠 Мои правила")
async def btn_rules(message: Message):
    rules = await list_rules(message.from_user.id)
    text = "📋 **Твои правила (чему я научился):**\n\n"
    if not rules:
        text = "У меня пока нет правил. Нажми «Добавить правило», чтобы обучить меня."
    else:
        for i, r in enumerate(rules, 1):
            text += f"{i}. {r[0]}\n"
    
    await message.answer(text, reply_markup=get_rules_menu())

# --- ИНЛАЙН-КНОПКИ (ДОБАВЛЕНИЕ ПРАВИЛ) ---
@router.callback_query(F.data == "add_rule")
async def cb_add_rule(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Напиши текст нового правила. Например:\n*Всегда пиши код с комментариями*", parse_mode="Markdown")
    await state.set_state(BotStates.waiting_for_rule)
    await callback.answer()

@router.callback_query(F.data == "clear_rules")
async def cb_clear_rules(callback: CallbackQuery):
    await clear_rules(callback.from_user.id)
    await callback.message.edit_text("🧹 Все правила удалены. Я снова чистый лист.")
    await callback.answer()

@router.message(BotStates.waiting_for_rule)
async def process_new_rule(message: Message, state: FSMContext):
    await add_rule(message.from_user.id, message.text)
    await state.clear()
    await message.answer("🧠 Запомнил новое правило!", reply_markup=get_main_menu())

# --- ФОТО ---
@router.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    status_msg = await message.answer("⏳ Смотрю скриншот...")
    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file_info.file_path)
        base64_image = base64.b64encode(file_bytes.read()).decode('utf-8')
        
        caption = message.caption or "Разбери этот скриншот."
        await add_to_history(user_id, "user", f"[Фото]: {caption}")
        
        user_rules = await get_rules(user_id)
        history = await get_history(user_id, limit=20)
        content = [{"type": "text", "text": caption}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]
        messages = [{"role": "system", "content": BASE_SYSTEM_PROMPT + user_rules}] + history + [{"role": "user", "content": content}]
        
        response = await client.chat.completions.create(model=MODEL_ID, messages=messages, max_tokens=8000)
        answer = response.choices[0].message.content
        await add_to_history(user_id, "assistant", answer)
        await status_msg.delete()
        await send_code_files(message, answer)
    except Exception as e:
        await status_msg.delete()
        await message.answer(f"Ошибка: {e}")

# --- ФАЙЛЫ ---
@router.message(F.document)
async def handle_document(message: Message):
    user_id = message.from_user.id
    document = message.document
    if document.file_size > 15 * 1024 * 1024:
        await message.answer("Файл слишком большой. Максимум 15 МБ.")
        return

    status_msg = await message.answer("⏳ Изучаю файл...")
    try:
        file_info = await message.bot.get_file(document.file_id)
        file_bytes = await message.bot.download_file(file_info.file_path)
        raw_data = file_bytes.read()
        extracted_text = ""
        
        if document.file_name.lower().endswith('.pdf'):
            try:
                reader = PdfReader(io.BytesIO(raw_data))
                for page in reader.pages[:30]:
                    text = page.extract_text()
                    if text: extracted_text += text + "\n"
            except:
                await status_msg.delete()
                await message.answer("Не удалось прочитать PDF.")
                return
        else:
            try:
                extracted_text = raw_data.decode('utf-8')
            except:
                await status_msg.delete()
                await message.answer("Не могу прочитать файл как текст.")
                return

        if not extracted_text.strip():
            await status_msg.delete()
            await message.answer("Файл пустой.")
            return

        caption = message.caption or 'Проанализируй этот файл/код.'
        prompt = f"Файл `{document.file_name}`:\n\n```\n{extracted_text[:80000]}\n```\n\n{caption}"
        
        await add_to_history(user_id, "user", prompt)
        await process_ai_response(message, user_id, status_msg)
        
    except Exception as e:
        await status_msg.delete()
        await message.answer(f"Ошибка: {e}")

# --- ТЕКСТ И ССЫЛКИ ---
@router.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    user_text = message.text
    
    url_match = re.search(r'(https?://[^\s]+)', user_text)
    if url_match:
        url = url_match.group(1)
        status_msg = await message.answer("⏳ Выкачиваю текст по ссылке...")
        content = await fetch_url_text(url)
        
        # Скрытый контекст: ИИ видит текст, но не пересказывает его
        hidden_context = f"[СИСТЕМНОЕ СООБЩЕНИЕ: Пользователь прислал ссылку {url}. Бот скачал текст. Вот он: {content}]\n\n"
        user_text = hidden_context + user_text.replace(url, "")
    else:
        status_msg = await message.answer("⏳ Читаю и думаю...")

    await add_to_history(user_id, "user", user_text)
    await process_ai_response(message, user_id, status_msg)

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    print("Бот запущен с кнопками!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
