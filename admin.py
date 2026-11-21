import asyncio
import logging
import os
import sqlite3
import tempfile
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import BaseFilter, Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import database as db
from config import ADMIN_IDS, DB_PATH, DEFAULT_WELCOME, SEND_DELAY

log = logging.getLogger("joinbot.admin")
router = Router()

ADMINS = set(ADMIN_IDS)
waiting = {}  # admin_id -> режим ожидания ввода
pending = {}  # admin_id -> (chat_id, message_id) поста для рассылки
running = {"bc": False}
tasks = set()

PANEL = "⚙️ Админ-панель"


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        user = event.from_user
        if user is None or user.id not in ADMINS:
            return False
        if isinstance(event, Message) and event.chat.type != "private":
            return False
        return True


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def load_admins():
    ADMINS.update(await db.get_admins())


def kb(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows
        ]
    )


BACK = [("◀️ Назад", "menu")]


async def main_menu():
    ap = await db.get_setting("auto_approve", "1") == "1"
    rp = await db.get_setting("report_on", "1") == "1"
    return kb(
        [
            [("📊 Статистика", "stats"), ("📨 Рассылка", "bc")],
            [("👋 Приветствие", "wl")],
            [(f"✅ Автоодобрение: {'ВКЛ' if ap else 'ВЫКЛ'}", "t_approve")],
            [(f"📰 Утренний отчёт: {'ВКЛ' if rp else 'ВЫКЛ'}", "t_report")],
            [("💾 База", "dbm"), ("👥 Админы", "adm")],
        ]
    )


async def show(c: CallbackQuery, text, markup):
    waiting.pop(c.from_user.id, None)
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass
    await c.answer()


# ---------- Приветствие ----------
async def send_welcome(bot: Bot, chat_id: int):
    wchat = await db.get_setting("welcome_chat")
    wmsg = await db.get_setting("welcome_msg")
    try:
        if wchat and wmsg:
            try:
                await bot.copy_message(chat_id, int(wchat), int(wmsg))
                return
            except TelegramBadRequest as e:
                log.warning("welcome copy failed: %s", e)
        await bot.send_message(chat_id, DEFAULT_WELCOME)
    except TelegramForbiddenError:
        pass
    except Exception as e:
        log.warning("welcome failed: %s", e)


# ---------- Тексты статистики ----------
def fmt_stats(s, title):
    lines = [
        title,
        "",
        f"Всего людей: {s['total']}",
        f"Активных: {s['total'] - s['blocked']}",
        f"Заблокировали бота: {s['blocked']}",
        f"Новых за 24 часа: {s['new24']}",
        f"Новых за 7 дней: {s['new7']}",
    ]
    if s["channels"]:
        lines += ["", "Заявки по каналам (всего / за 24ч):"]
        lines += [f"• {t}: {n} / {n24 or 0}" for t, n, n24 in s["channels"]]
    return "\n".join(lines)


def fmt_report(s):
    lines = [
        "☀️ Утренний отчёт",
        "",
        f"Новых людей за 24 часа: {s['new24']}",
        f"Всего: {s['total']}",
        f"Заблокировали бота: {s['blocked']}",
    ]
    ch = [(t, n24) for t, n, n24 in s["channels"] if n24]
    if ch:
        lines += ["", "Заявки по каналам за 24ч:"]
        lines += [f"• {t}: {n24}" for t, n24 in ch]
    return "\n".join(lines)


# ---------- Команды ----------
@router.message(Command("admin", "start"))
async def cmd_admin(m: Message):
    waiting.pop(m.from_user.id, None)
    await m.answer(PANEL, reply_markup=await main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(m: Message):
    waiting.pop(m.from_user.id, None)
    pending.pop(m.from_user.id, None)
    await m.answer("Отменено.", reply_markup=await main_menu())


# ---------- Меню ----------
@router.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery):
    await show(c, PANEL, await main_menu())


@router.callback_query(F.data == "stats")
async def cb_stats(c: CallbackQuery):
    s = await db.get_stats()
    await show(c, fmt_stats(s, "📊 Статистика"), kb([[("🔄 Обновить", "stats")], BACK]))


@router.callback_query(F.data == "t_approve")
async def cb_t_approve(c: CallbackQuery):
    cur = await db.get_setting("auto_approve", "1")
    await db.set_setting("auto_approve", "0" if cur == "1" else "1")
    await show(c, PANEL, await main_menu())


@router.callback_query(F.data == "t_report")
async def cb_t_report(c: CallbackQuery):
    cur = await db.get_setting("report_on", "1")
    await db.set_setting("report_on", "0" if cur == "1" else "1")
    await show(c, PANEL, await main_menu())


# ---------- Рассылка ----------
@router.callback_query(F.data == "bc")
async def cb_bc(c: CallbackQuery):
    await show(
        c,
        "📨 Пришли пост для рассылки (текст, фото, видео, гиф).\n"
        "Я покажу предпросмотр, потом можно сделать тест или отправить всем.",
        kb([BACK]),
    )
    waiting[c.from_user.id] = "bc"


async def copy_safe(bot: Bot, uid: int, from_chat: int, msg_id: int) -> str:
    try:
        await bot.copy_message(uid, from_chat, msg_id)
        return "ok"
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await copy_safe(bot, uid, from_chat, msg_id)
    except TelegramForbiddenError:
        await db.set_blocked(uid, True)
        return "blocked"
    except Exception as e:
        log.warning("send to %s failed: %s", uid, e)
        return "fail"


async def run_broadcast(bot: Bot, admin_id: int, from_chat: int, msg_id: int):
    ok = blocked = fail = 0
    try:
        for uid in await db.get_active_ids():
            res = await copy_safe(bot, uid, from_chat, msg_id)
            if res == "ok":
                ok += 1
            elif res == "blocked":
                blocked += 1
            else:
                fail += 1
            await asyncio.sleep(SEND_DELAY)
    finally:
        running["bc"] = False
    await bot.send_message(
        admin_id,
        f"✅ Рассылка завершена\n\nДоставлено: {ok}\nЗаблокировали бота: {blocked}\nОшибок: {fail}",
    )


def bc_kb():
    return kb(
        [
            [("✅ Отправить всем", "bc_send")],
            [("🧪 Тест (отправить админам)", "bc_test")],
            [("❌ Отмена", "bc_cancel")],
        ]
    )


@router.callback_query(F.data == "bc_test")
async def cb_bc_test(c: CallbackQuery, bot: Bot):
    data = pending.get(c.from_user.id)
    if not data:
        await c.answer("Пост не найден, пришли заново.", show_alert=True)
        return
    for aid in ADMINS:
        await copy_safe(bot, aid, data[0], data[1])
    await c.answer("Тест отправлен админам.")


@router.callback_query(F.data == "bc_send")
async def cb_bc_send(c: CallbackQuery, bot: Bot):
    if running["bc"]:
        await c.answer("Рассылка уже идёт, дождись итога.", show_alert=True)
        return
    data = pending.pop(c.from_user.id, None)
    if not data:
        await c.answer("Пост не найден, пришли заново.", show_alert=True)
        return
    running["bc"] = True
    await c.message.edit_text("⏳ Рассылка запущена. Итог пришлю сюда.")
    await c.answer()
    t = asyncio.create_task(run_broadcast(bot, c.from_user.id, data[0], data[1]))
    tasks.add(t)
    t.add_done_callback(tasks.discard)


@router.callback_query(F.data == "bc_cancel")
async def cb_bc_cancel(c: CallbackQuery):
    pending.pop(c.from_user.id, None)
    await show(c, PANEL, await main_menu())


# ---------- Приветствие ----------
@router.callback_query(F.data == "wl")
async def cb_wl(c: CallbackQuery):
    custom = bool(await db.get_setting("welcome_msg"))
    delay = await db.get_setting("welcome_delay", "0")
    text = (
        "👋 Приветствие после заявки\n\n"
        f"Сейчас: {'твой пост' if custom else 'стандартный текст'}\n"
        f"Задержка: {delay} сек"
    )
    await show(
        c,
        text,
        kb(
            [
                [("✏️ Изменить", "wl_set"), ("👁 Посмотреть", "wl_view")],
                [("⏱ Задержка", "wl_delay"), ("♻️ Сбросить", "wl_reset")],
                BACK,
            ]
        ),
    )


@router.callback_query(F.data == "wl_set")
async def cb_wl_set(c: CallbackQuery):
    await show(c, "Пришли пост, который бот будет отправлять после заявки.", kb([BACK]))
    waiting[c.from_user.id] = "welcome"


@router.callback_query(F.data == "wl_view")
async def cb_wl_view(c: CallbackQuery, bot: Bot):
    await c.answer()
    await send_welcome(bot, c.from_user.id)


@router.callback_query(F.data == "wl_delay")
async def cb_wl_delay(c: CallbackQuery):
    await show(
        c,
        "Через сколько секунд писать после заявки? Пришли число от 0 до 300.\n"
        "0 — сразу.",
        kb([BACK]),
    )
    waiting[c.from_user.id] = "delay"


@router.callback_query(F.data == "wl_reset")
async def cb_wl_reset(c: CallbackQuery):
    await db.del_setting("welcome_chat")
    await db.del_setting("welcome_msg")
    await c.answer("Сброшено на стандартный текст.", show_alert=True)


# ---------- База ----------
@router.callback_query(F.data == "dbm")
async def cb_dbm(c: CallbackQuery):
    await show(
        c,
        "💾 База\n\nСкачать — получишь файл-бэкап.\n"
        "Загрузить — людей из файла добавлю к текущим, ничего не удаляя.",
        kb([[("⬇️ Скачать БД", "db_dl")], [("⬆️ Загрузить БД", "db_ul")], BACK]),
    )


@router.callback_query(F.data == "db_dl")
async def cb_db_dl(c: CallbackQuery):
    await c.answer("Готовлю файл...")
    name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    tmp = os.path.join(tempfile.gettempdir(), name)

    def _backup():
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        dst.close()
        src.close()

    try:
        await asyncio.to_thread(_backup)
        await c.message.answer_document(FSInputFile(tmp), caption="💾 Бэкап базы")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@router.callback_query(F.data == "db_ul")
async def cb_db_ul(c: CallbackQuery):
    await show(c, "Пришли файл .db (бэкап бота).", kb([BACK]))
    waiting[c.from_user.id] = "upload_db"


# ---------- Админы ----------
async def adm_view(uid):
    extra = await db.get_admins()
    lines = ["👥 Админы", ""]
    lines += [f"• {i} (главный)" for i in ADMIN_IDS]
    lines += [f"• {i}" for i in extra if i not in ADMIN_IDS]
    rows = []
    if uid in ADMIN_IDS:
        rows.append([("➕ Добавить", "adm_add")])
        for i in extra:
            if i not in ADMIN_IDS:
                rows.append([(f"❌ Убрать {i}", f"adm_del:{i}")])
    rows.append(BACK)
    return "\n".join(lines), kb(rows)


@router.callback_query(F.data == "adm")
async def cb_adm(c: CallbackQuery):
    text, markup = await adm_view(c.from_user.id)
    await show(c, text, markup)


@router.callback_query(F.data == "adm_add")
async def cb_adm_add(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        await c.answer("Только главный админ может добавлять.", show_alert=True)
        return
    await show(
        c,
        "Пришли Telegram ID нового админа (число).\n"
        "Он должен сначала нажать /start у бота.",
        kb([BACK]),
    )
    waiting[c.from_user.id] = "add_admin"


@router.callback_query(F.data.startswith("adm_del:"))
async def cb_adm_del(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        await c.answer("Только главный админ может убирать.", show_alert=True)
        return
    uid = int(c.data.split(":")[1])
    await db.del_admin(uid)
    if uid not in ADMIN_IDS:
        ADMINS.discard(uid)
    text, markup = await adm_view(c.from_user.id)
    await show(c, text, markup)


# ---------- Любое сообщение админа ----------
@router.message()
async def on_message(m: Message):
    if m.text and m.text.startswith("/"):
        return
    uid = m.from_user.id
    mode = waiting.pop(uid, None)

    if mode == "welcome":
        await db.set_setting("welcome_chat", m.chat.id)
        await db.set_setting("welcome_msg", m.message_id)
        await m.answer(
            "✅ Приветствие сохранено. Так его увидят люди "
            "(не удаляй это сообщение из чата с ботом):"
        )
        await m.bot.copy_message(m.chat.id, m.chat.id, m.message_id)
        await m.answer(PANEL, reply_markup=await main_menu())
        return

    if mode == "delay":
        txt = (m.text or "").strip()
        if not txt.isdigit() or not 0 <= int(txt) <= 300:
            waiting[uid] = "delay"
            await m.answer("Пришли число от 0 до 300.")
            return
        await db.set_setting("welcome_delay", int(txt))
        await m.answer(f"✅ Задержка: {int(txt)} сек.", reply_markup=await main_menu())
        return

    if mode == "add_admin":
        txt = (m.text or "").strip()
        if not txt.isdigit() or int(txt) <= 0:
            waiting[uid] = "add_admin"
            await m.answer("Пришли Telegram ID числом.")
            return
        await db.add_admin(int(txt))
        ADMINS.add(int(txt))
        text, markup = await adm_view(uid)
        await m.answer(text, reply_markup=markup)
        return

    if mode == "upload_db":
        if not m.document:
            waiting[uid] = "upload_db"
            await m.answer("Пришли файл .db.")
            return
        tmp = os.path.join(tempfile.gettempdir(), f"import_{uid}.db")
        try:
            await m.bot.download(m.document, destination=tmp)
            added = await db.import_db(tmp)
            await m.answer(
                f"✅ Готово. Добавлено новых людей: {added}.",
                reply_markup=await main_menu(),
            )
        except Exception as e:
            log.warning("import failed: %s", e)
            await m.answer("❌ Не получилось. Похоже, это не база этого бота.")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return

    # иначе это пост для рассылки
    pending[uid] = (m.chat.id, m.message_id)
    s = await db.get_stats()
    await m.answer("👁 Предпросмотр. Так увидят люди:")
    await m.bot.copy_message(m.chat.id, m.chat.id, m.message_id)
    await m.answer(f"Отправить этот пост {s['total'] - s['blocked']} людям?", reply_markup=bc_kb())
