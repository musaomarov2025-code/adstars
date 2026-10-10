import asyncio
import random
from telethon import TelegramClient, events, functions
from telethon.errors import (
    FloodWaitError, PeerFloodError, UserAlreadyParticipantError,
    ChannelPrivateError, UsernameInvalidError, ContactNameEmptyError,
    ChatWriteForbiddenError
)
from config import API_ID, API_HASH, SESSION_PATH
import database as db

client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

# Флаги управления
broadcast_running = False
autojoin_running = True
autotrigger_running = True


# ==========================================================
#  УВЕДОМЛЕНИЯ (через bot.py)
# ==========================================================
async def notify(text, level="info"):
    from bot import send_notify
    db.log(level, text)
    emoji = {"info": "ℹ️", "warn": "⚠️", "error": "❌", "restrict": "🚫"}.get(level, "ℹ️")
    try:
        await send_notify(f"{emoji} {text}")
    except Exception as e:
        print(f"Notify error: {e}")


# ==========================================================
#  АВТОПОДПИСКА
# ==========================================================
@client.on(events.NewMessage(incoming=True))
async def autojoin_handler(event):
    if not autojoin_running:
        return
    try:
        sender = await event.get_sender()
        if not sender or not getattr(sender, "username", None):
            return

        admins = db.all_admins()
        if sender.username.lower() not in [a.lower() for a in admins]:
            return

        text = event.message.text or ""
        links = [w for w in text.split() if "t.me/" in w or "joinchat" in w]

        for link in links:
            limit = int(db.get("autojoin_limit", 5))
            if db.get_sent_today() >= limit:
                await notify(f"Автоподписка: лимит {limit} достигнут", "warn")
                return

            try:
                await client(functions.channels.JoinChannelRequest(link))
                db.inc_sent()
                await notify(f"Подписался: {link}", "info")
            except UserAlreadyParticipantError:
                pass
            except FloodWaitError as e:
                await notify(f"FloodWait {e.seconds}с (подписка)", "warn")
                await asyncio.sleep(e.seconds)
            except (ChannelPrivateError, UsernameInvalidError):
                await notify(f"Не смог подписаться: {link}", "error")

            dmin = int(db.get("autojoin_delay_min", 30))
            dmax = int(db.get("autojoin_delay_max", 90))
            await asyncio.sleep(random.randint(dmin, dmax))

    except Exception as e:
        await notify(f"Ошибка автоподписки: {e}", "error")


# ==========================================================
#  АВТОТРИГГЕР
# ==========================================================
@client.on(events.NewMessage(incoming=True))
async def autotrigger_handler(event):
    if not autotrigger_running:
        return
    if event.is_private:
        return  # только чаты

    try:
        text = (event.message.text or "").lower()
        if not text:
            return

        triggers = [t.lower() for t in db.all_triggers()]
        if not triggers or not any(t in text for t in triggers):
            return

        sender = await event.get_sender()
        if not sender:
            return

        dmin = int(db.get("trigger_delay_min", 5))
        dmax = int(db.get("trigger_delay_max", 30))
        await asyncio.sleep(random.randint(dmin, dmax))

        replies = db.all_replies()
        if not replies:
            return
        await event.reply(random.choice(replies))

        # Добавление в контакты
        if db.get("trigger_add_contact", "1") == "1":
            if not db.is_contact_added(sender.id):
                try:
                    await client(functions.contacts.AddContactRequest(
                        id=sender.id,
                        first_name=sender.first_name or "Lead",
                        last_name=sender.last_name or "",
                        phone="",
                        add_phone_privacy_exception=False
                    ))
                    db.mark_contact(sender.id)
                    await notify(f"Добавлен в контакты: {sender.first_name}", "info")
                except ContactNameEmptyError:
                    await notify("Контакты: пустое имя", "warn")
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    await notify(f"Ошибка добавления: {e}", "error")

    except Exception as e:
        await notify(f"Ошибка автотриггера: {e}", "error")


# ==========================================================
#  РАССЫЛКА
# ==========================================================
async def run_broadcast():
    global broadcast_running
    broadcast_running = True

    targets = db.all_targets()
    texts = db.all_texts()
    if not targets or not texts:
        await notify("Рассылка: нет целей или текстов", "warn")
        broadcast_running = False
        return

    repeats = int(db.get("bc_repeats", 1))
    dmin = int(db.get("bc_delay_min", 30))
    dmax = int(db.get("bc_delay_max", 90))
    cooldown = int(db.get("bc_cooldown", 60))
    daily_limit = int(db.get("bc_daily_limit", 300))

    await notify(f"Рассылка: {len(targets)} чатов × {repeats}", "info")

    try:
        for r in range(repeats):
            if not broadcast_running:
                break

            for chat_id in targets:
                if not broadcast_running:
                    break

                if db.get_sent_today() >= daily_limit:
                    await notify(f"Лимит {daily_limit} достигнут", "warn")
                    broadcast_running = False
                    return

                try:
                    await client.send_message(chat_id, random.choice(texts))
                    db.inc_sent()
                    db.log("info", f"Отправлено: {chat_id}")

                except FloodWaitError as e:
                    await notify(f"FloodWait {e.seconds}с", "warn")
                    await asyncio.sleep(e.seconds)
                except PeerFloodError:
                    await notify("🚫 Аккаунт получил ограничение! Стоп", "restrict")
                    broadcast_running = False
                    return
                except (ChatWriteForbiddenError, ChannelPrivateError):
                    db.del_target(chat_id)
                    await notify(f"Удалён (нет доступа): {chat_id}", "error")
                except Exception as e:
                    await notify(f"Ошибка {chat_id}: {e}", "error")

                await asyncio.sleep(random.randint(dmin, dmax))

            if r < repeats - 1:
                await asyncio.sleep(cooldown)

        await notify("Рассылка завершена", "info")

    except Exception as e:
        await notify(f"Критическая ошибка: {e}", "error")
    finally:
        broadcast_running = False


def stop_broadcast():
    global broadcast_running
    broadcast_running = False
