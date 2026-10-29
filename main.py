import asyncio
import logging
import os
from collections import defaultdict

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

if not BOT_TOKEN or not ANTHROPIC_API_KEY:
    raise ValueError("Не заданы BOT_TOKEN или ANTHROPIC_API_KEY")

# Реестр доступных моделей: ключ — короткое имя для кнопки, значение — ID для API
MODELS = {
    "haiku": {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5"},
    "sonnet": {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
    "opus": {"id": "claude-opus-4-6", "name": "Claude Opus 4.6"},
}

DEFAULT_MODEL_KEY = "haiku"

user_histories = defaultdict(list)
user_models = {}  # user_id -> ключ модели (например, "sonnet")
MAX_HISTORY_LENGTH = 10

router = Router()
client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# --- Callback data для выбора модели ---
class ModelCallback(CallbackData, prefix="model"):
    key: str


def get_model_name(key: str) -> str:
    return MODELS.get(key, {}).get("name", key)


def get_model_key(user_id: int) -> str:
    return user_models.get(user_id, DEFAULT_MODEL_KEY)


# --- Клавиатура выбора модели ---
def model_keyboard(current_key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, info in MODELS.items():
        # Отмечаем текущую модель галочкой
        text = f"✅ {info['name']}" if key == current_key else info["name"]
        builder.button(
            text=text,
            callback_data=ModelCallback(key=key).pack(),
        )
    builder.adjust(1)  # по одной кнопке в ряд
    return builder.as_markup()


# --- Команда /model ---
@router.message(Command("model"))
async def cmd_model(message: Message):
    user_id = message.from_user.id
    current_key = get_model_key(user_id)
    await message.answer(
        f"Текущая модель: **{get_model_name(current_key)}**\n\nВыберите модель:",
        reply_markup=model_keyboard(current_key),
        parse_mode="Markdown",
    )


# --- Обработка нажатия кнопки выбора модели ---
@router.callback_query(ModelCallback.filter())
async def process_model_choice(callback: CallbackQuery, callback_data: ModelCallback):
    user_id = callback.from_user.id
    new_key = callback_data.key

    if new_key not in MODELS:
        await callback.answer("Неизвестная модель.", show_alert=True)
        return

    user_models[user_id] = new_key
    await callback.message.edit_text(
        f"Модель изменена на **{get_model_name(new_key)}**.",
        parse_mode="Markdown",
        reply_markup=None,
    )
    await callback.answer(f"Выбрана модель: {get_model_name(new_key)}")


# --- Обработка обычных сообщений ---
@router.message(F.text)
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_text = message.text
    model_key = get_model_key(user_id)
    model_id = MODELS[model_key]["id"]

    user_histories[user_id].append({"role": "user", "content": user_text})
    if len(user_histories[user_id]) > MAX_HISTORY_LENGTH:
        user_histories[user_id] = user_histories[user_id][-MAX_HISTORY_LENGTH:]

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        response = await client.messages.create(
            model=model_id,
            max_tokens=1024,
            messages=user_histories[user_id],
            system="Ты полезный ассистент. Отвечай кратко и по делу на русском языке.",
        )
        answer = response.content[0].text
        user_histories[user_id].append({"role": "assistant", "content": answer})
        await message.answer(answer)
    except Exception as e:
        logging.exception("Ошибка при запросе к Claude")
        await message.answer(f"Произошла ошибка: {e}")


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
