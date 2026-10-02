import asyncio
import json
import time
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from config import BOT_TOKEN, OWNER_ID, DB
from database import init_db, get_setting, set_setting
from keyboards import (
    main_menu, admin_kb, back_admin_kb, ap_menu_kb, texts_kb, chats_kb, media_kb,
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ================== FSM ==================
class AddText(StatesGroup):
    waiting = State()

class DelText(StatesGroup):
    waiting = State()

class AddChat(StatesGroup):
    waiting = State()

class DelChat(StatesGroup):
    waiting = State()

class SetMedia(StatesGroup):
    waiting = State()

class SetButtons(StatesGroup):
    waiting = State()

class SetInterval(StatesGroup):
    waiting = State()


# ================== ХЕЛПЕРЫ ==================
def _load_json(key):
    raw = get_setting(key) or "[]"
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_json(key, data):
    set_setting(key, json.dumps(data, ensure_ascii=False))


def _build_buttons():
    raw = get_setting("autopost_buttons")
    if not raw:
        return None
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    rows = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        row = []
        for pair in line.split("&"):
            pair = pair.strip()
            if " - " not in pair:
                continue
            parts = pair.split(" - ", 1)
            text = parts[0].strip()
            url = parts[1].strip()
            if text and url:
                row.append(InlineKeyboardButton(text=text, url=url))
        if row:
            rows.append(row)
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ap_menu_text():
    enabled = get_setting("autopost_enabled") == "1"
    texts = _load_json("autopost_texts")
    chats = _load_json("autopost_chats")
    interval = get_setting("autopost_interval")
    media_type = get_setting("autopost_media_type") or "нет"
    return (
        f"📢 <b>Автопост</b>\n\n"
        f"Статус: {'🟢 включен' if enabled else '🔴 выключен'}\n"
        f"📝 Текстов: <b>{len(texts)}</b>\n"
        f"🆔 Чатов: <b>{len(chats)}</b>\n"
        f"⏱ Интервал: <b>{interval}</b> сек\n"
        f"📎 Медиа: <b>{media_type}</b>"
    )


# ================== /start ==================
@dp.message(CommandStart())
async def start(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ Бот только для владельца.")
        return
    await message.answer(
        f"👋 Привет!\n\n"
        f"Это рекламный бот. Управление через /admin.",
        reply_markup=main_menu())


# ================== /admin ==================
@dp.message(Command("admin"))
async def admin(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await message.answer(
        f"🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\n{ap_menu_text()}",
        reply_markup=ap_menu_kb(get_setting("autopost_enabled") == "1"),
        parse_mode="HTML")


@dp.callback_query(F.data == "ap_menu")
async def ap_menu_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    try:
        await call.message.edit_text(
            f"🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\n{ap_menu_text()}",
            reply_markup=ap_menu_kb(get_setting("autopost_enabled") == "1"),
            parse_mode="HTML")
    except Exception:
        await call.message.answer(
            f"🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\n{ap_menu_text()}",
            reply_markup=ap_menu_kb(get_setting("autopost_enabled") == "1"),
            parse_mode="HTML")


@dp.callback_query(F.data == "ap_toggle")
async def ap_toggle(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    current = get_setting("autopost_enabled") == "1"
    set_setting("autopost_enabled", "0" if current else "1")
    await call.answer("✅ Изменено")
    await call.message.edit_text(
        f"🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\n{ap_menu_text()}",
        reply_markup=ap_menu_kb(not current),
        parse_mode="HTML")


# ================== АВТО-ДОБАВЛЕНИЕ В ЧАТ ==================
@dp.my_chat_member()
async def on_chat_member_update(update: ChatMemberUpdated):
    """Срабатывает, когда бота добавляют/удаляют из чата."""
    chat = update.chat
    new_status = update.new_chat_member.status
    old_status = update.old_chat_member.status

    if chat.type not in ("group", "supergroup"):
        return

    chat_id = str(chat.id)
    chat_title = chat.title or "без названия"

    # === БОТА ДОБАВИЛИ ===
    if new_status in ("member", "administrator") and old_status in ("left", "kicked"):
        chats = _load_json("autopost_chats")

        if chat_id in chats:
            return

        if len(chats) >= 50:
            try:
                await bot.send_message(
                    OWNER_ID,
                    f"⚠️ <b>Лимит 50 чатов достигнут</b>\n"
                    f"Не могу добавить: <b>{chat_title}</b>\n"
                    f"<code>{chat_id}</code>",
                    parse_mode="HTML")
            except Exception:
                pass
            return

        chats.append(chat_id)
        _save_json("autopost_chats", chats)

        try:
            await bot.send_message(
                OWNER_ID,
                f"✅ <b>Бот добавлен в чат</b>\n\n"
                f"📌 <b>{chat_title}</b>\n"
                f"🆔 <code>{chat_id}</code>\n"
                f"📊 Всего чатов: <b>{len(chats)}</b>\n\n"
                f"<i>Если не хочешь постить здесь — удали через /admin → 🆔 Чаты.</i>",
                parse_mode="HTML")
        except Exception:
            pass

        try:
            await bot.send_message(
                chat_id,
                f"👋 Привет! Я добавлен для автопостов.\n\n"
                f"Писать буду редко и по делу. "
                f"Если что-то не понравится — просто удалите меня из группы.")
        except Exception:
            pass

        print(f"[adbot] + добавлен в {chat_id} ({chat_title})")

    # === БОТА УДАЛИЛИ / КИКНУЛИ ===
    elif new_status in ("left", "kicked") and old_status in ("member", "administrator"):
        chats = _load_json("autopost_chats")
        if chat_id in chats:
            chats.remove(chat_id)
            _save_json("autopost_chats", chats)

        try:
            await bot.send_message(
                OWNER_ID,
                f"🗑 <b>Бот удалён из чата</b>\n\n"
                f"📌 <b>{chat_title}</b>\n"
                f"🆔 <code>{chat_id}</code>\n"
                f"📊 Осталось чатов: <b>{len(chats)}</b>",
                parse_mode="HTML")
        except Exception:
            pass

        print(f"[adbot] - удалён из {chat_id} ({chat_title})")
        # ================== ТЕКСТЫ ==================
@dp.callback_query(F.data == "ap_texts")
async def ap_texts_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    texts = _load_json("autopost_texts")
    try:
        await call.message.edit_text(
            f"✏️ <b>Тексты поста</b>\n\nСохранено: <b>{len(texts)}</b>\n\n"
            f"Каждый пост бот берёт следующий по кругу.",
            reply_markup=texts_kb(), parse_mode="HTML")
    except Exception:
        await call.message.answer(
            f"✏️ <b>Тексты поста</b>\n\nСохранено: <b>{len(texts)}</b>",
            reply_markup=texts_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "ap_text_add")
async def ap_text_add(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await call.message.answer("✏️ Пришли текст поста (можно с форматированием):")
    await state.set_state(AddText.waiting)


@dp.message(AddText.waiting)
async def ap_text_save(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    texts = _load_json("autopost_texts")
    text = message.html_text or message.text or ""
    texts.append(text)
    _save_json("autopost_texts", texts)
    await state.clear()
    await message.answer(f"✅ Добавлено. Всего: <b>{len(texts)}</b>",
                         reply_markup=back_admin_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "ap_text_show")
async def ap_text_show(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    texts = _load_json("autopost_texts")
    if not texts:
        await call.answer("Пусто", show_alert=True)
        return
    text = "📜 <b>Тексты:</b>\n\n"
    for i, t in enumerate(texts, 1):
        preview = t[:100] + ("..." if len(t) > 100 else "")
        safe = preview.replace("<", "&lt;").replace(">", "&gt;")
        text += f"<b>{i}.</b> {safe}\n\n"
    if len(text) > 4000:
        text = text[:4000] + "\n...обрезано"
    await call.message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data == "ap_text_del")
async def ap_text_del_ask(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    texts = _load_json("autopost_texts")
    if not texts:
        await call.answer("Пусто", show_alert=True)
        return
    await call.message.answer(f"🗑 Пришли номер текста (1-{len(texts)}):")
    await state.set_state(DelText.waiting)


@dp.message(DelText.waiting)
async def ap_text_del(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    try:
        idx = int(message.text.strip()) - 1
    except Exception:
        await message.answer("⚠️ Нужно число.")
        return
    texts = _load_json("autopost_texts")
    if idx < 0 or idx >= len(texts):
        await message.answer("⚠️ Нет такого номера.")
        return
    texts.pop(idx)
    _save_json("autopost_texts", texts)
    await state.clear()
    await message.answer(f"🗑 Удалено. Осталось: <b>{len(texts)}</b>",
                         reply_markup=back_admin_kb(), parse_mode="HTML")


# ================== ЧАТЫ ==================
@dp.callback_query(F.data == "ap_chats")
async def ap_chats_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    chats = _load_json("autopost_chats")
    try:
        await call.message.edit_text(
            f"🆔 <b>Чаты</b>\n\nСохранено: <b>{len(chats)}</b>\n\n"
            f"Бот добавляет чаты <b>автоматически</b>, когда его туда добавляют.\n"
            f"Можно добавить вручную по ID.",
            reply_markup=chats_kb(), parse_mode="HTML")
    except Exception:
        await call.message.answer(
            f"🆔 <b>Чаты</b>\n\nСохранено: <b>{len(chats)}</b>",
            reply_markup=chats_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "ap_chat_add")
async def ap_chat_add(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await call.message.answer(
        "🆔 Пришли ID чата (например <code>-1001234567890</code>)\n\n"
        "Или добавь бота в чат — он сохранится автоматически.",
        parse_mode="HTML")
    await state.set_state(AddChat.waiting)


@dp.message(AddChat.waiting)
async def ap_chat_save(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    value = message.text.strip()
    if not value.lstrip("-").isdigit():
        await message.answer("⚠️ Нужен числовой ID.")
        return
    chats = _load_json("autopost_chats")
    if value in chats:
        await message.answer("⚠️ Уже есть.")
        return
    if len(chats) >= 50:
        await message.answer("⚠️ Лимит 50 чатов.")
        return
    chats.append(value)
    _save_json("autopost_chats", chats)
    await state.clear()
    await message.answer(f"✅ Добавлено. Всего: <b>{len(chats)}</b>",
                         reply_markup=back_admin_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "ap_chat_show")
async def ap_chat_show(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    chats = _load_json("autopost_chats")
    if not chats:
        await call.answer("Пусто", show_alert=True)
        return
    text = "🆔 <b>Чаты:</b>\n\n"
    for i, c in enumerate(chats, 1):
        text += f"<b>{i}.</b> <code>{c}</code>\n"
    if len(text) > 4000:
        text = text[:4000] + "\n...обрезано"
    await call.message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data == "ap_chat_del")
async def ap_chat_del_ask(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    chats = _load_json("autopost_chats")
    if not chats:
        await call.answer("Пусто", show_alert=True)
        return
    await call.message.answer(f"🗑 Номер чата (1-{len(chats)}):")
    await state.set_state(DelChat.waiting)


@dp.message(DelChat.waiting)
async def ap_chat_del(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    try:
        idx = int(message.text.strip()) - 1
    except Exception:
        await message.answer("⚠️ Нужно число.")
        return
    chats = _load_json("autopost_chats")
    if idx < 0 or idx >= len(chats):
        await message.answer("⚠️ Нет такого.")
        return
    chats.pop(idx)
    _save_json("autopost_chats", chats)
    await state.clear()
    await message.answer(f"🗑 Удалено. Осталось: <b>{len(chats)}</b>",
                         reply_markup=back_admin_kb(), parse_mode="HTML")


# ================== /addchat (запасной способ) ==================
@dp.message(Command("addchat"))
async def addchat_cmd(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("⚠️ Команда работает только в группе.")
        return
    chat_id = str(message.chat.id)
    chats = _load_json("autopost_chats")
    if chat_id in chats:
        await message.answer("⚠️ Этот чат уже в списке.")
        return
    if len(chats) >= 50:
        await message.answer("⚠️ Лимит 50 чатов.")
        return
    chats.append(chat_id)
    _save_json("autopost_chats", chats)
    await message.answer(f"✅ Чат добавлен. Всего: <b>{len(chats)}</b>",
                         parse_mode="HTML")


@dp.message(Command("delchat"))
async def delchat_cmd(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    chat_id = str(message.chat.id)
    chats = _load_json("autopost_chats")
    if chat_id not in chats:
        await message.answer("⚠️ Этого чата нет в списке.")
        return
    chats.remove(chat_id)
    _save_json("autopost_chats", chats)
    await message.answer(f"🗑 Чат удалён. Осталось: <b>{len(chats)}</b>",
                         parse_mode="HTML")


# ================== МЕДИА ==================
@dp.callback_query(F.data == "ap_media")
async def ap_media_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    mt = get_setting("autopost_media_type") or "нет"
    try:
        await call.message.edit_text(
            f"📎 <b>Медиа</b>\n\nТекущее: <b>{mt}</b>",
            reply_markup=media_kb(), parse_mode="HTML")
    except Exception:
        await call.message.answer(
            f"📎 <b>Медиа</b>\n\nТекущее: <b>{mt}</b>",
            reply_markup=media_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "ap_media_add")
async def ap_media_add(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await call.message.answer(
        "📎 Пришли фото / видео / GIF / стикер — бот сохранит как медиа.")
    await state.set_state(SetMedia.waiting)


@dp.message(SetMedia.waiting)
async def ap_media_save(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    if message.photo:
        set_setting("autopost_media_type", "photo")
        set_setting("autopost_media_id", message.photo[-1].file_id)
    elif message.video:
        set_setting("autopost_media_type", "video")
        set_setting("autopost_media_id", message.video.file_id)
    elif message.animation:
        set_setting("autopost_media_type", "animation")
        set_setting("autopost_media_id", message.animation.file_id)
    elif message.sticker:
        set_setting("autopost_media_type", "sticker")
        set_setting("autopost_media_id", message.sticker.file_id)
    else:
        await message.answer("⚠️ Нужно фото, видео, GIF или стикер.")
        return
    await state.clear()
    mt = get_setting("autopost_media_type")
    await message.answer(f"✅ Медиа сохранено: <b>{mt}</b>",
                         reply_markup=back_admin_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "ap_media_del")
async def ap_media_del(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return
    set_setting("autopost_media_type", "")
    set_setting("autopost_media_id", "")
    await call.answer("🗑 Убрано")
    try:
        await call.message.edit_text(
            "📎 <b>Медиа убрано</b>",
            reply_markup=media_kb(), parse_mode="HTML")
    except Exception:
        pass


# ================== КНОПКИ ==================
@dp.callback_query(F.data == "ap_buttons")
async def ap_buttons_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    await state.clear()
    cur = get_setting("autopost_buttons") or "нет"
    preview = cur[:200] if cur != "нет" else "нет"
    try:
        await call.message.edit_text(
            f"🔗 <b>Кнопки</b>\n\n"
            f"Формат: <code>Текст - https://ссылка</code>\n"
            f"Несколько в строку: через <code>&</code>\n"
            f"Очистить: пришли <code>-</code>\n\n"
            f"<b>Сейчас:</b>\n<pre>{preview}</pre>",
            reply_markup=back_admin_kb(), parse_mode="HTML")
    except Exception:
        await call.message.answer(f"🔗 Кнопки. Сейчас: {preview}")
    await state.set_state(SetButtons.waiting)


@dp.message(SetButtons.waiting)
async def ap_buttons_save(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    text = message.text.strip()
    if text == "-":
        set_setting("autopost_buttons", "")
        await state.clear()
        await message.answer("🗑 Очищено", reply_markup=back_admin_kb())
        return
    ok = False
    for line in text.split("\n"):
        for pair in line.split("&"):
            pair = pair.strip()
            if " - " in pair:
                parts = pair.split(" - ", 1)
                if len(parts) == 2 and parts[0].strip() and parts[1].strip().startswith("http"):
                    ok = True
    if not ok:
        await message.answer("⚠️ Неверный формат. Пример: <code>Кнопка - https://t.me/xxx</code>",
                             parse_mode="HTML")
        return
    set_setting("autopost_buttons", text)
    await state.clear()
    await message.answer("✅ Кнопки сохранены", reply_markup=back_admin_kb())


# ================== ИНТЕРВАЛ ==================
@dp.callback_query(F.data == "ap_interval")
async def ap_interval_cb(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return
    cur = get_setting("autopost_interval")
    await call.message.answer(
        f"⏱ Текущий интервал: <b>{cur}</b> сек.\n\n"
        f"Пришли новое значение в секундах (минимум 10):",
        parse_mode="HTML")
    await state.set_state(SetInterval.waiting)


@dp.message(SetInterval.waiting)
async def ap_interval_save(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    try:
        val = int(message.text.strip())
        if val < 10:
            raise ValueError
    except Exception:
        await message.answer("⚠️ Нужно число ≥ 10.")
        return
    set_setting("autopost_interval", val)
    await state.clear()
    await message.answer(f"✅ Интервал: <b>{val}</b> сек",
                         reply_markup=back_admin_kb(), parse_mode="HTML")


# ================== ОТЧЁТ ==================
REPORT_STATS = {
    "sent": 0,
    "errors": 0,
    "kicked": [],
}


def report_text():
    s = REPORT_STATS
    kicked_lines = ""
    if s["kicked"]:
        for cid, when in s["kicked"][-20:]:
            kicked_lines += f"• <code>{cid}</code> — {when}\n"
    else:
        kicked_lines = "• —\n"
    return (
        f"📊 <b>Отчёт по автопосту</b>\n\n"
        f"📤 Отправлено: <b>{s['sent']}</b>\n"
        f"❌ Ошибок: <b>{s['errors']}</b>\n"
        f"🗑 Изгнаний: <b>{len(s['kicked'])}</b>\n\n"
        f"<b>Изгнания:</b>\n{kicked_lines}"
    )


@dp.callback_query(F.data == "report_now")
async def report_now(cb: CallbackQuery):
    if cb.from_user.id != OWNER_ID:
        return
    await cb.answer()
    await cb.message.answer(report_text(), parse_mode="HTML")


# ================== АВТОПОСТ-ВОРКЕР ==================
async def autopost_worker():
    idx_text = 0
    while True:
        try:
            if get_setting("autopost_enabled") != "1":
                await asyncio.sleep(5)
                continue

            texts = _load_json("autopost_texts")
            chats = _load_json("autopost_chats")
            if not texts or not chats:
                await asyncio.sleep(10)
                continue

            try:
                interval = int(get_setting("autopost_interval") or 30)
            except Exception:
                interval = 30
            if interval < 10:
                interval = 10

            media_type = get_setting("autopost_media_type")
            media_id = get_setting("autopost_media_id")
            kb = _build_buttons()

            for chat_raw in list(chats):
                if get_setting("autopost_enabled") != "1":
                    break

                text = texts[idx_text % len(texts)]
                idx_text += 1

                try:
                    cid = int(chat_raw) if str(chat_raw).lstrip("-").isdigit() else chat_raw
                except Exception:
                    cid = chat_raw

                try:
                    try:
                        if media_type == "photo" and media_id:
                            await bot.send_photo(
                                chat_id=cid, photo=media_id,
                                caption=text or None, reply_markup=kb,
                                parse_mode="HTML" if text else None)
                        elif media_type == "video" and media_id:
                            await bot.send_video(
                                chat_id=cid, video=media_id,
                                caption=text or None, reply_markup=kb,
                                parse_mode="HTML" if text else None)
                        elif media_type == "animation" and media_id:
                            await bot.send_animation(
                                chat_id=cid, animation=media_id,
                                caption=text or None, reply_markup=kb,
                                parse_mode="HTML" if text else None)
                        elif media_type == "sticker" and media_id:
                            await bot.send_sticker(chat_id=cid, sticker=media_id)
                            if text:
                                await bot.send_message(
                                    chat_id=cid, text=text,
                                    reply_markup=kb, parse_mode="HTML")
                        else:
                            await bot.send_message(
                                chat_id=cid, text=text,
                                reply_markup=kb, parse_mode="HTML")
                        REPORT_STATS["sent"] += 1
                        print(f"[adbot] отправлено в {cid}")
                    except Exception as e_inner:
                        es = str(e_inner).lower()
                        if "parse entities" in es or "unclosed" in es or "can't parse" in es:
                            try:
                                if media_type == "photo" and media_id:
                                    await bot.send_photo(chat_id=cid, photo=media_id,
                                                         caption=text or None, reply_markup=kb)
                                elif media_type == "video" and media_id:
                                    await bot.send_video(chat_id=cid, video=media_id,
                                                         caption=text or None, reply_markup=kb)
                                elif media_type == "animation" and media_id:
                                    await bot.send_animation(chat_id=cid, animation=media_id,
                                                             caption=text or None, reply_markup=kb)
                                else:
                                    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
                                REPORT_STATS["sent"] += 1
                                continue
                            except Exception:
                                pass
                        if ("kicked" in es or "bot was blocked" in es
                                or "not enough rights" in es
                                or "chat not found" in es
                                or "bot is not a member" in es
                                or "have no rights" in es):
                            if chat_raw in chats:
                                chats.remove(chat_raw)
                                _save_json("autopost_chats", chats)
                            when = datetime.now().strftime("%d.%m %H:%M")
                            REPORT_STATS["kicked"].append((chat_raw, when))
                            try:
                                await bot.send_message(
                                    OWNER_ID,
                                    f"🗑 <b>Бот удалён из чата</b>\n"
                                    f"<code>{chat_raw}</code>\n"
                                    f"Ошибка: <i>{e_inner}</i>",
                                    parse_mode="HTML")
                            except Exception:
                                pass
                            print(f"[adbot] изгнан из {chat_raw}: {e_inner}")
                            continue
                        REPORT_STATS["errors"] += 1
                        print(f"[adbot] ошибка {chat_raw}: {e_inner}")
                except Exception as e:
                    REPORT_STATS["errors"] += 1
                    print(f"[adbot] крит. ошибка {chat_raw}: {e}")

                await asyncio.sleep(interval)

        except Exception as e:
            print("[adbot] worker error:", e)
            await asyncio.sleep(30)


# ================== ДНЕВНОЙ ОТЧЁТ ==================
async def daily_report():
    while True:
        try:
            now = datetime.now()
            target = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)
            wait = (target - now).total_seconds()
            await asyncio.sleep(wait)

            try:
                await bot.send_message(OWNER_ID, report_text(), parse_mode="HTML")
            except Exception as e:
                print("daily_report send error:", e)
            REPORT_STATS["sent"] = 0
            REPORT_STATS["errors"] = 0
            REPORT_STATS["kicked"] = []
        except Exception as e:
            print("daily_report error:", e)
            await asyncio.sleep(3600)


# ================== ЗАПУСК ==================
async def main():
    init_db()
    asyncio.create_task(autopost_worker())
    asyncio.create_task(daily_report())
    print("AdBot запущен")
    me = await bot.get_me()
    print(f"BOT: @{me.username}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
