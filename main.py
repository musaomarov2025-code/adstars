import asyncio
import base64
import logging
import os
import re
import aiohttp
from bs4 import BeautifulSoup
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

# Используем модель, которая лучше всего работает с картинками и кодом
MODEL_ID = "claude-sonnet-4-6" 

user_histories = defaultdict(list)
MAX_HISTORY_LENGTH = 20 # Ограничиваем историю, чтобы не превышать лимит токенов с картинками

router = Router()
client = AsyncOpenAI(api_key=XEROSHARC_API_KEY, base_url=XEROSHARC_BASE_URL)

SYSTEM_PROMPT = (
    "Ты senior-разработчик и эксперт. Отвечай кратко, без воды. "
    "Если пользователь присылает код или картинку — анализируй их. "
    "Если просят написать код, ВСЕГДА выдавай его в Markdown-блоках с указанием имени файла. "
    "Пример: ```python filename=\"main.py\"\nprint('hi')\n```. "
    "Если файлов несколько — каждый в отдельном блоке."
)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def fetch_url_text(url: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                soup = BeautifulSoup(await response.text(), 'html.parser')
                for script in soup(["script", "style"]): script.extract()
                return soup.get_text(separator=' ', strip=True)[:4000]
    except Exception:
        return "[Не удалось прочитать ссылку]"

async def send_code_files(message: Message, answer: str):
    """Ищет код в ответе и отправляет его файлами."""
    pattern = r"```(?:(\w+)\s+filename=[\"'](.*?)[\"']|(\w+))\n(.*?)```"
    matches = re.findall(pattern, answer, re.DOTALL)
    
    # Отправляем текст без кода (если есть)
    clean_text = re.sub(r"```.*?```", "", answer, flags=re.DOTALL).strip()
    if clean_text:
        await message.answer(clean_text)

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
        await message.answer(answer) # Если кода нет, шлем просто текст

# --- ОБРАБОТЧИКИ ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет! Я твой ИИ-ассистент. Могу читать текст, анализировать фото (скриншоты кода) и присылать готовые файлы. Просто напиши мне!")

@router.message(Command("clear"))
async def cmd_clear(message: Message):
    user_histories[message.from_user.id].clear()
    await message.answer("🧹 История диалога очищена.")

@router.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    status_msg = await message.answer("📸 Анализирую изображение...")
    
    try:
        # Скачиваем фото в максимальном разрешении
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file_info.file_path)
        
        # Кодируем в base64 для API
        base64_image = base64.b64encode(file_bytes.read()).decode('utf-8')
        
        content = [
            {"type": "text", "text": message.caption or "Проанализируй это изображение."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
        ]
        
        user_histories[user_id].append({"role": "user", "content": content})
        
        # Отправляем в API
        response = await client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + user_histories[user_id][-MAX_HISTORY_LENGTH:],
            max_tokens=3000,
        )
        
        answer = response.choices[0].message.content
        user_histories[user_id].append({"role": "assistant", "content": answer})
        
        await status_msg.delete()
        await send_code_files(message, answer)

    except Exception as e:
        await status_msg.delete()
        if "503" in str(e):
            await message.answer("⚠️ Сервера перегружены. Попробуй позже.")
        else:
            await message.answer(f"Ошибка: {e}")

@router.message(F.document)
async def handle_document(message: Message):
    user_id = message.from_user.id
    document = message.document
    
    # Проверяем размер (не более 2 МБ для текста)
    if document.file_size > 2 * 1024 * 1024:
        await message.answer("Файл слишком большой (макс. 2 МБ).")
        return

    status_msg = await message.answer("📄 Читаю^\ файл...")
    
    try:
s        file_info = await]+ message.bot.get_file(document)', user.file_id)
        file_bytes = await message.bot.download_file(file_info.file_path)
        
        # Пытаемся прочитать как текст
        try:
            text_content = file_bytes.read().decode('utf-8')
            prompt = f"Вот содержимое файла `{document.file_name}`:\n\n```\n{text_content}\n```\n\n{message.caption or 'Проанализируй этот код/файл.'}"
        except UnicodeDecodeError:
            await status_msg.delete()
            await message.answer("Не могу прочитать этот файл как текст. Пришли фото или текстовый файл (.py, .txt, .md).")
            return

        user_histories[user_id].append({"role": "user", "content": prompt})
        
        response = await client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + user_histories[user_id][-MAX_HISTORY_LENGTH:],
            max_tokens=3000,
        )
        
        answer = response.choices[0].message.content
        user_histories[user_id].append({"role": "assistant", "content": answer})
        
        await status_msg.delete()
        await send_code_files(message, answer)

    except Exception as e:
        await status_msg.delete()
        await message.answer(f"Ошибка при чтении файла: {e}")

@router.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    user_text = message.text
    
    # Чтение ссылок
    url_match = re.search(r'(https?://[_text)
    if url_match:
        url = url_match.group(1)
        status = await message.answer("🔗 Читаю ссылку...")
        content = await fetch_url_text(url)
        await status.delete()
        user_text = user_text.replace(url, f"\n[Текст по ссылке]:\n{content}\n")

    user_histories[user_id].append({"role": "user", "content": user_text})
    
    # aiogram будет автоматически показывать "Печатает..." пока выполняется код
    async with message.bot.send_chat_action(chat_id=message.chat.id, action="typing"):
        try:
            response = await client.chat.completions.create(
                model=MODEL_ID,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + user_histories[user_id][-MAX_HISTORY_LENGTH:],
                max_tokens=3000,
            )
            answer = response.choices[0].message.content
            user_histories[user_id].append({"role": "assistant", "content": answer})
            await send_code_files(message, answer)
            
        except Exception as e:
            logging.exception("Ошибка API")
            if "503" in str(e):
                await message.answer("⚠️ **Сервера провайдера перегружены (503).** Подожди пару минут.")
            else:
                await message.answer(f"Произошла ошибка: {e}")

async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот-мультимодал запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
