import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.types import ChatJoinRequest, ChatMemberUpdated

import admin
import database as db
from config import ADMIN_IDS, BOT_TOKEN, REPORT_HOUR

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("joinbot")

dp = Dispatcher()
dp.include_router(admin.router)
bg_tasks = set()


def spawn(coro):
    t = asyncio.create_task(coro)
    bg_tasks.add(t)
    t.add_done_callback(bg_tasks.discard)


async def delayed_welcome(bot: Bot, chat_id: int, delay: int):
    await asyncio.sleep(delay)
    await admin.send_welcome(bot, chat_id)


@dp.chat_join_request()
async def on_join(req: ChatJoinRequest, bot: Bot):
    u = req.from_user
    await db.add_join(u.id, u.username, u.first_name, req.chat.id, req.chat.title)

    if await db.get_setting("auto_approve", "1") == "1":
        try:
            await req.approve()
        except Exception as e:
            log.warning("approve failed: %s", e)

    try:
        delay = int(await db.get_setting("welcome_delay", "0") or 0)
    except ValueError:
        delay = 0
    if delay > 0:
        spawn(delayed_welcome(bot, req.user_chat_id, delay))
    else:
        await admin.send_welcome(bot, req.user_chat_id)


@dp.my_chat_member(F.chat.type == "private")
async def on_block(event: ChatMemberUpdated):
    status = event.new_chat_member.status
    await db.set_blocked(event.from_user.id, status in ("kicked", "left"))


async def report_loop(bot: Bot):
    tz = ZoneInfo("Europe/Moscow")
    while True:
        now = datetime.now(tz)
        nxt = now.replace(hour=REPORT_HOUR, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            if await db.get_setting("report_on", "1") == "1":
                text = admin.fmt_report(await db.get_stats())
                for aid in admin.ADMINS:
                    try:
                        await bot.send_message(aid, text)
                    except Exception as e:
                        log.warning("report to %s failed: %s", aid, e)
        except Exception as e:
            log.warning("report failed: %s", e)


async def main():
    if not BOT_TOKEN:
        raise SystemExit("Задай переменную окружения BOT_TOKEN")
    await db.init_db()
    await admin.load_admins()
    bot = Bot(BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=False)
    spawn(report_loop(bot))
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "chat_join_request", "my_chat_member"],
    )


if __name__ == "__main__":
    asyncio.run(main())
