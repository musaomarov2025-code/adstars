import asyncio
import base64
import io
import logging
import os
import re
import aiohttp
from bs4 import BeautifulSoup
from pypdf import PdfReader
from collections import defaultdict

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

if not BOT_TOKEN or not XEROSHARC_API_KEY:
    raise ValueError("Не заданы переменные окружения!")

MODEL_ID = "claude-sonnet-4-6" 
user_histories = defaultdict(list)
MAX_HISTORY_LENGTH = 30 

router = Router()
client = AsyncOpenAI(
    api_key=XEROSHARC_API_KEY, 
    base_url=XEROSHARC_BASE_URL,
    timeout=120.0 
)

SYSTEM_PROMPT = (
    "Ты — элитный senior-разработчик и эксперт по IT. Ты пишешь код на Python, JS/TS, Java, Kotlin, Swift, C#, C++, Go, Rust, PHP, SQL, Bash, HTML/CSS. "
    "Твои задачи: писать код с нуля, исправлять ошибки, объяснять код, писать тесты, переводить код с языка на язык. "
    "Ты помогаешь с Telegram-ботами (aiogram, telebot), парсерами, API, сайтами (Flask, FastAPI, Django, React, HTML/CSS/JS), базами данных и pandas. "
    "ВАЖНО: Ты не можешь запускать код в реальном времени (у тебя нет среды), но можешь давать готовые решения. Не ври, что запустил код. "
    "ФОРМАТИРОВАНИЕ: ВСЕГДА оборачивай код в Markdown-блоки с указанием имени файла. Пример: ```python filename=\"main.py\"\nprint('hi')\n```. "
    "Если файлов несколько, используй отдельный блок для каждого. Никогда не пиши 'я не могу создать файл'. "
    "Отвечай без воды, кратко и по делу. Пиши на русском."
)

async def fetch_url_text(url: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                soup = BeautifulSoup(await response.text(), 'html.parser')
                for script in soup(["script", "style"]): script.extract()
                return soup.get_text(separator=' ', strip=True)[:6000]
    except Exception as e:
        return f"[Не удалось прочитать ссылку: {e}]"

def split_long_text(text: str, max_length: int = 4000) -> list:
    parts = []
    while len(text) > max_length:
        split_idx = text.rfind('\n', 0, max_length)
        if split_idx == -1:
            split_idx = max_length
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

async def process_ai_response(message: Message, user_id: int, status_msg: Message = None):
    # ИСПРАВЛЕНА ОШИБКА: теперь просто вызываем await
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except:
        pass

    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + user_histories[user_id][-MAX_HISTORY_LENGTH:]
        
        response = await client.chat.completions.create(
            model=MODEL_ID,
            messages=messages,
            max_tokens=4000,
        )
        answer = response.choices[0].message.content
        user_histories[user_id].append({"role": "assistant", "content": answer})
        
        # Удаляем сообщение "⏳ Думаю...", если оно было
        if status_msg:
            try:
                await status_msg.delete()
            except:
                pass
                
        await send_code_files(message, answer)

    except Exception as e:
        if status_msg:
            try:
                await status_msg.delete()
            except:
                pass
        logging.exception("Ошибка API")
        if "503" in str(e) or "502" in str(e):
            await message.answer("⚠️ Сервера провайдера перегружены. Подожди минуту и попробуй снова.")
        else:
            await message.answer(f"Произошла ошибка: {e}")

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я твой ИИ-разработчик.\n\n"
        "Пиши код на Python, JS, C++, Go и других языках. "
        "Кидай ошибки, файлы и фото — я разберусь и пришлю готовое решение файлами."
    )

@router.message(Command("clear"))
async def cmd_clear(message: Message):
    user_histories[message.from_user.id].clear()
    await message.answer("🧹 Контекст очищен. Начинаем сначала.")

@router.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    status_msg = await message.answer("⏳ Смотрю скриншот...")
    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file_info.file_path)
        base64_image = base64.b64encode(file_bytes.read()).decode('utf-8')
        
        content = [
            {"type": "text", "text": message.caption or "Разбери этот скриншот (код, ошибка, схема)."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
        ]
        user_histories[user_id].append({"role": "user", "content": content})
        await process_ai_response(message, user_id, status_msg)
    except Exception as e:
        await status_msg.delete()
        await message.answer(f"Ошибка при чтении фото: {e}")

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
                await message.answer("Не удалось прочитать PDF (возможно, это сканы).")
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

        prompt = f"Файл `{document.file_name}`:\n\n```\n{extracted_text[:20000]}\n```\n\n{message.caption or 'Проанализируй/исправь.'}"
        user_histories[user_id].append({"role": "user", "content": prompt})
        await process_ai_response(message, user_id, status_msg)
    except Exception as e:
        await status_msg.delete()
        await message.answer(f"Ошибка при обработке файла: {e}")

@router.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    user_text = message.text
    
    url_match = re.search(r'(https?://[^\s]+)', user_text)
    if url_match:
        url = url_match.group(1)
        status_msg = await message.answer("⏳ Читаю сайт...")
        content = await fetch_url_text(url)
        user_text = user_text.replace(url, f"\n[Текст с сайта]:\n{content}\n")
    else:
        status_msg = await message.answer("⏳ Думаю...")

    user_histories[user_id].append({"role": "user", "content": user_text})
    await process_ai_response(message, user_id, status_msg)

async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот-разработчик запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
