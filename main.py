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
from aiogram.types import Message, BufferedInputFile
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
    timeout=120.0 
)

# Базовый промпт
BASE_SYSTEM_PROMPT = (
    "Ты — профессиональный программист и ИИ-ассистент. "
    "Отвечай кратко, без воды. "
    "ФОРМАТИРОВАНИЕ: ВСЕГДА оборачивай код в Markdown-блоки с указанием имени файла. Пример: ```python filename=\"main.py\"\nprint('hi')\n```. "
    "Если файлов несколько, используй отдельный блок для каждого. Никогда не пиши 'я не могу создать файл'. "
    "Пиши на русском."
)

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

async def get_history(user_id: int, limit: int = 20):
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
        rows = await cursor.fetchall()
        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]

async def clear_history(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        await db.commit()

# --- ПРАВИЛА (САМООБУЧЕНИЕ) ---
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
        return f"\n\nВАЖНЫЕ ПРАВИЛА ОТ ПОЛЬЗОВАТЕЛЯ (ОБЯЗАТЕЛЬНЫ К ИСПОЛНЕНИЮ):\n{rules}"

async def clear_rules(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM rules WHERE user_id = ?", (user_id,))
        await db.commit()

async def list_rules(user_id: int) -> list:
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT id, rule_text FROM rules WHERE user_id = ?", (user_id,))
        return await cursor.fetchall()

# --- ПАРСИНГ ССЫЛОК (ЧЕРЕЗ JINA AI) ---
async def fetch_url_text(url: str) -> str:
    try:
        # Jina AI превращает любую страницу в чистый текст (Markdown)
        jina_url = f"https://r.jina.ai/{url}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(jina_url, headers=headers, timeout=25) as response:
                if response.status == 200:
                    text = await response.text()
                    return text[:10000] # Ограничиваем 10к символов
                else:
                    return f"[Ошибка чтения сайта. Код: {response.status}. Скинь текст файлом]"
    except Exception as e:
        return f"[Не удалось прочитать ссылку: {e}]"

# --- ОТПРАВКА КОДА ---
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
    pattern = r"```(?:(\w+)\s+filename=[\"'](.*?)[\"']|(\w+))\n(.*?)```"
    matches = re.findall(pattern, answer, re.DOTALL)
    clean_text = re.sub(r"```.*?```", "", answer, flags=re.DOTALL).strip()
    
    if clean_text:
        for chunk in split_long_text(clean_text):
            await message.answer(chunk)

    if matches:
        for m in matches:
            lang, custom_filename, default_lang, code = m
            lang = lang or default_lang or "txt"
            filename = custom_filename or f"snippet.{lang}"
            filename = filename.replace('"', '').replace("'", "").strip()
            file_bytes = code.strip().encode('utf-8')
            doc = BufferedInputFile(file=file_bytes, filename=filename)
            await message.answer_document(document=doc)
    elif not clean_text:
        for chunk in split_long_text(answer):
            await message.answer(chunk)

# --- ЗАПРОС К НЕЙРОСЕТИ ---
async def process_ai_response(message: Message, user_id: int, status_msg: Message = None):
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except: pass

    try:
        user_rules = await get_rules(user_id)
        final_system_prompt = BASE_SYSTEM_PROMPT + user_rules
        
        history = await get_history(user_id, limit=20)
        messages = [{"role": "system", "content": final_system_prompt}] + history
        
        response = await client.chat.completions.create(
            model=MODEL_ID,
            messages=messages,
            max_tokens=4000,
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
            await message.answer("⚠️ Сервера провайдера перегружены. Подожди минуту.")
        else:
            await message.answer(f"Произошла ошибка: {e}")

# --- КОМАНДЫ ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я твой ИИ-разработчик.\n\n"
        "🧠 **Я умею учиться на ошибках.**\n"
        "Если я тупанул, напиши `/rule [как надо было сделать]`.\n"
        "Например: `/rule Всегда пиши код с комментариями`.\n\n"
        "Команды:\n"
        "/rules — мои правила\n"
        "/clear — очистить память\n"
        "/forget_rules — забыть правила"
    )

@router.message(Command("clear"))
async def cmd_clear(message: Message):
    await clear_history(message.from_user.id)
    await message.answer("🧹 История диалога очищена.")

@router.message(Command("rule"))
async def cmd_add_rule(message: Message):
    rule_text = message.text.replace("/rule", "").strip()
    if not rule_text:
        await message.answer("Напиши правило после команды. Пример: `/rule Всегда пиши код с комментариями`")
        return
    await add_rule(message.from_user.id, rule_text)
    await message.answer(f"🧠 Запомнил новое правило:\n_{rule_text}_", parse_mode="Markdown")

@router.message(Command("rules"))
async def cmd_list_rules(message: Message):
    rules = await list_rules(message.from_user.id)
    if not rules:
        await message.answer("У меня нет правил. Напиши `/rule [текст]`, чтобы обучить меня.")
        return
    text = "📋 **Твои правила:**\n\n"
    for r_id, r_text in rules:
        text += f"{r_id}. {r_text}\n"
    await message.answer(text, parse_mode="Markdown")

@router.message(Command("forget_rules"))
async def cmd_forget_rules(message: Message):
    await clear_rules(message.from_user.id)
    await message.answer("🧹 Я забыл все правила. Теперь я чистый лист.")

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
        final_system_prompt = BASE_SYSTEM_PROMPT + user_rules
        
        history = await get_history(user_id, limit=10)
        content = [{"type": "text", "text": caption}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]
        messages = [{"role": "system", "content": final_system_prompt}] + history + [{"role": "user", "content": content}]
        
        response = await client.chat.completions.create(model=MODEL_ID, messages=messages, max_tokens=4000)
        answer = response.choices[0].message.content
        await add_to_history(user_id, "assistant", answer)
        await status_msg.delete()
        await send_code_files(message, answer)
    except Exception as e:
        await status_msg.delete()
        await message.answer(f"Ошибка при чтении фото: {e}")

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
                for page in reader.pages[:20]:
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
        prompt = f"Файл `{document.file_name}`:\n\n```\n{extracted_text[:25000]}\n```\n\n{caption}"
        
        await add_to_history(user_id, "user", prompt)
        await process_ai_response(message, user_id, status_msg)
        
    except Exception as e:
        await status_msg.delete()
        await message.answer(f"Ошибка при обработке файла: {e}")

# --- ТЕКСТ И ССЫЛКИ ---
@router.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    user_text = message.text
    
    url_match = re.search(r'(https?://[^\s]+)', user_text)
    if url_match:
        url = url_match.group(1)
        status_msg = await message.answer("⏳ Читаю сайт (через Jina AI)...")
        content = await fetch_url_text(url)
        user_text = user_text.replace(url, f"\n[Текст по ссылке]:\n{content}\n")
    else:
        status_msg = await message.answer("⏳ Читаю и думаю...")

    await add_to_history(user_id, "user", user_text)
    await process_ai_response(message, user_id, status_msg)

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
