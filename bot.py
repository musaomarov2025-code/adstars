from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from fastapi import Request, Response

from config import BOT_TOKEN, WEBAPP_URL, OWNER_ID

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ==========================================================
#  /start — кнопка Mini App
# ==========================================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Доступ только для владельца.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🚀 Открыть панель",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )]
    ])
    await message.answer(
        "🤖 <b>Control Panel</b>\n\n"
        "Управление:\n"
        "• Рассылка\n"
        "• Автоподписка\n"
        "• Автотриггер\n"
        "• Логи",
        reply_markup=kb,
        parse_mode="HTML"
    )


# ==========================================================
#  WEBHOOK — FastAPI route
# ==========================================================
async def webhook_handler(request: Request):
    """Telegram стучится сюда с апдейтами."""
    try:
        data = await request.json()
        update = types.Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        print(f"Webhook error: {e}")
    return Response(status_code=200)


# ==========================================================
#  Установка webhook при старте
# ==========================================================
async def setup_webhook(domain: str):
    url = f"{domain.rstrip('/')}/webhook"
    await bot.set_webhook(
        url=url,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"]
    )
    print(f"✅ Webhook установлен: {url}")


async def remove_webhook():
    await bot.delete_webhook()
    print("🔻 Webhook удалён")


# ==========================================================
#  Уведомления (для telethon_client)
# ==========================================================
async def send_notify(text: str):
    try:
        await bot.send_message(OWNER_ID, text)
    except Exception as e:
        print(f"Notify error: {e}")
