import asyncio
import io
import logging
import os
import re
import aiohttp
from bs4 import BeautifulSoup
from collections import defaultdict

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
XEROSHARC_API_KEY = os.getenv("XEROSHARC_API_KEY")
XEROSHARC_BASE_URL = os.getenv("XEROSHARC_BASE_URL", "https://xerosharc.site/v1")

if not BOT_TOKEN or not XEROSHARC_API_KEY:
    raise ValueError("Не заданы переменные окружения!")

MODELS = {
    "claude-sonnet": {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
    "claude-opus": {"id": "claude-opus-5-5", "name": "Claude Opus 5.5 (Умная)"},
    "gpt-6": {"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol"},
}

DEFAULT_MODEL_KEY = "claude-sonnet"

# История хранится в памяти (сбрасывается при перезапуске BotHost)
user_histories = defaultdict(list)
user_models = {}
MAX_HISTORY_LENGTH = 50  # Помним 50 последних сообщений

router = Router()
client = AsyncOpenAI(
    api_key=XEROSHARC_API_KEY,
    base_url=XEROSHARC_BASE_URL,
)

SYSTEM_PROMPT = (
    "Ты senior-разработчик. Отвечай кратко, без воды и извинений. "
    "Если просят код, выводи его в блоках markdown. "
    "КРИТИЧЕСКИ ВАЖНО: ВСЕГДА указывай имя файла в заголовке блока кода, используя синтаксис: "
    "```python filename=\"main.py\". "
    "Если файлов несколько, используй отдельные блоки для каждого. "
    "Никогда не пиши 'я не могу создать файл', просто выдай код."
)

class ModelCallback(CallbackData, prefix="model"):
    key: str

def get_model_name(key: str) -> str:
    return MODELS.get(key, {}).get("name", key)

def get_model_key(user_id: int) -> str:
    return user_models.get(user_id, DEFAULT_MODEL_KEY)

def model_keyboard(current_key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, info in MODELS.items():
        text = f"✅ {info['name']}" if key == current_key else info["name"]
        builder.button(text=text, callback_data=ModelCallback(key=key).pack())
    builder.adjust(1)
    return builder.as_markup()

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ЧТЕНИЯ ССЫЛОК ---
async def fetch_url_text(url: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                # Удаляем скрипты и стили, оставляем только текст
                for script in soup(["script", "style"]):
                    script.extract()
                text = soup.get_text(separator=' ', strip=True)
                return text[:4000] # Ограничиваем размер текста
    except Exception as e:
        return f"[Не удалось прочитать ссылку: {e}]"

# --- КОМАНДЫ ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я senior-разработчик.\n"
        "Кидай мне ссылку на документацию или статью, или просто пиши задачу — я выдам код в файлах.\n\n"
        "Команды:\n"
        "/model — сменить модель\n"
        "/clear — очистить историю диалога"
    )

@router.message(Command("clear"))
async def cmd_clear(message: Message):
    user_id = message.from_user.id
    user_histories[user_id].clear()
    await message.answer("🧹 История диалога полностью очищена.")

@router.message(Command("model"))
async def cmd_model(message: Message):
    current_key = get_model_key(message.from_user.id)
    await message.answer(
        f"Текущая модель: **{get_model_name(current_key)}**",
        reply_markup=model_keyboard(current_key),
        parse_mode="Markdown",
    )

@router.callback_query(ModelCallback.filter())
async def process_model_choice(callback: CallbackQuery, callback_data: ModelCallback):
    user_id = callback.from_user.id
    new_key = callback_data.key
    user_models[user_id] = new_key
    await callback.message.edit_text(f"Модель изменена на **{get_model_name(new_key)}**.", parse_mode="Markdown")
    await callback.answer()

# --- ОСНОВНОЙ ОБРАБОТЧИК ---
@router.message(F.text)
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_text = message.text
    model_id = MODELS[get_model_key(user_id)]["id"]

    # 1. Проверяем, есть ли ссылка в сообщении
    url_match = re.search(r'(https?://[^\s]+)', user_text)
    if url_match:
        url = url_match.group(1)
        status_msg = await message.answer("🔗 Читаю ссылку, подожди...")
        url_content = await fetch_url_text(url)
        await status_msg.delete()
        # Заменяем ссылку в тексте на её содержимое
        user_text = user_text.replace(url, f"\n[Содержимое ссылки]:\n{url_content}\n")

    # 2. Добавляем в историю
    user_histories[user_id].append({"role": "user", "content": user_text})
    if len(user_histories[user_id]) > MAX_HISTORY_LENGTH:
        user_histories[user_id] = user_histories[user_id][-MAX_HISTORY_LENGTH:]

    # 3. Показываем статус "Печатает..."
    status_msg = await message.answer("⏳ Думаю над кодом...")
    
    try:
        messages_for_api = [{"role": "system", "content": SYSTEM_PROMPT}] + user_histories[user_id]

        response = await client.chat.completions.create(
            model=model_id,
            messages=messages_for_api,
            max_tokens=3000, # Увеличено для больших файлов
        )
        answer = response.choices[0].message.content
        user_histories[user_id].append({"role": "assistant", "content": answer})
        
        # Удаляем статус "Думаю..."
        await status_msg.delete()
        
        # 4. Вытаскиваем файлы (ищем ```язык filename="имя" \n код ```)
        # Регулярка ищет язык, имя файла и сам код
        code_blocks = re.findall(r"```(\w+)?(?:\s+filename=[\"']?(.*?)[\"']?)?\n(.*?)```", answer, re.DOTALL)
        
        if code_blocks:
            # Проверяем, есть ли осмысленный текст вокруг кода
            clean_text = re.sub(r"```.*?```", "", answer, flags=re.DOTALL).strip()
            if clean_text:
                await message.answer(clean_text)
                
            for lang, filename, code in code_blocks:
                lang = lang or "txt"
                # Если нейросеть забыла имя файла, делаем fallback
                if not filename or filename.strip() == "":
                    filename = f"snippet.{lang}"
                else:
                    # Чистим имя от кавычек, если ИИ накосячил
                    filename = filename.replace('"', '').replace("'", "").strip()

                # Отправляем файл
                file_bytes = code.strip().encode('utf-8')
                file_to_send = BufferedInputFile(file=file_bytes, filename=filename)
                await message.answer_document(document=file_to_send)
        else:
            # Если это просто текстовый ответ (без кода)
            await message.answer(answer)
            
    except Exception as e:
        await status_msg.delete()
        logging.exception("Ошибка API")
        if "503" in str(e):
            await message.answer("⚠️ **Сервера провайдера перегружены (503).** Подожди пару минут или смени модель через /model.")
        else:
            await message.answer(f"Ошибка: {e}")

async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
