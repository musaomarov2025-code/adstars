import asyncio
import logging
import os
from collections import defaultdict

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
XEROSHARC_API_KEY = os.getenv("XEROSHARC_API_KEY")
XEROSHARC_BASE_URL = os.getenv("XEROSHARC_BASE_URL", "https://xerosharc.site/v1")

if not BOT_TOKEN or not XEROSHARC_API_KEY:
    raise ValueError("Не заданы BOT_TOKEN или XEROSHARC_API_KEY")

# Реестр моделей: ключ для кнопки -> ID модели для API
# ВАЖНО: Названия моделей могут отличаться! Уточните их в личном кабинете xerosharc.site.
MODELS = {
    "claude-sonnet": {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet"},
    "claude-haiku": {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku"},
    "gpt-4o": {"id": "gpt-4o", "name": "GPT-4o"},
    "gpt-4o-mini": {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
}

DEFAULT_MODEL_KEY = "claude-sonnet"

user_histories = defaultdict(list)
user_models = {}
MAX_HISTORY_LENGTH = 10

router = Router()
client = AsyncOpenAI(
    api_key=XEROSHARC_API_KEY,
    base_url=XEROSHARC_BASE_URL,
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
        builder.button(
            text=text,
            callback_data=ModelCallback(key=key).pack(),
        )
    builder.adjust(1)
    return builder.as_markup()


@router.message(Command("model"))
async def cmd_model(message: Message):
    user_id = message.from_user.id
    current_key = get_model_key(user_id)
    await message.answer(
        f"Текущая модель: **{get_model_name(current_key)}**\n\nВыберите модель:",
        reply_markup=model_keyboard(current_key),
        parse_mode="Markdown",
    )


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
        response = await client.chat.completions.create(
            model=model_id,
            messages=user_histories[user_id],
            max_tokens=1024,
        )
        answer = response.choices[0].message.content
        user_histories[user_id].append({"role": "assistant", "content": answer})
        await message.answer(answer)
    except Exception as e:
        logging.exception("Ошибка при запросе к API")
        await message.answer(f"Произошла ошибка: {e}")


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
