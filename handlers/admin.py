from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_ID
from database import (
    get_all_chats, get_all_ads, add_ad, delete_ad,
    get_next_ad, log_ad, get_stats, toggle_chat_ad
)

router = Router()


class AdStates(StatesGroup):
    waiting_text = State()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Реклама", callback_data="ad_menu")],
        [InlineKeyboardButton(text="💬 Группы", callback_data="chats_menu")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
    ])


# ==== /admin ====
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🔧 <b>АДМИН-ПАНЕЛЬ</b>", reply_markup=admin_menu())


# ==== Назад в меню ====
@router.callback_query(F.data == "main_menu")
async def back_to_main(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    await call.message.edit_text("🔧 <b>АДМИН-ПАНЕЛЬ</b>", reply_markup=admin_menu())


# ==== Меню рекламы ====
@router.callback_query(F.data == "ad_menu")
async def ad_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    ads = await get_all_ads()

    text = "📢 <b>УПРАВЛЕНИЕ РЕКЛАМОЙ</b>\n\n"
    if ads:
        for i, ad in enumerate(ads, 1):
            text += f"{i}. {ad[1][:50]}...\n"
    else:
        text += "Рекламы пока нет."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить рекламу", callback_data="ad_add")],
        [InlineKeyboardButton(text="🗑 Удалить последнюю", callback_data="ad_del")],
        [InlineKeyboardButton(text="▶️ Отправить сейчас (чередование)", callback_data="ad_send")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])
    await call.message.edit_text(text, reply_markup=kb)


# ==== Добавить рекламу ====
@router.callback_query(F.data == "ad_add")
async def ad_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await call.message.edit_text(
        "✏️ Напиши текст рекламы одним сообщением.\n\n"
        "Можно с HTML (<b>, <i>, <a href='...'>)."
    )
    await state.set_state(AdStates.waiting_text)


@router.message(AdStates.waiting_text)
async def ad_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await add_ad(message.html_text)
    await state.clear()
    await message.answer("✅ Реклама добавлена!", reply_markup=admin_menu())


# ==== Удалить последнюю рекламу ====
@router.callback_query(F.data == "ad_del")
async def ad_del(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    ads = await get_all_ads()
    if not ads:
        await call.answer("Рекламы нет")
        return
    last_id = ads[-1][0]
    await delete_ad(last_id)
    await call.answer("Удалено")
    await ad_menu(call)


# ==== Отправить сейчас ====
@router.callback_query(F.data == "ad_send")
async def ad_send(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    ad = await get_next_ad()
    if not ad:
        await call.answer("Рекламы нет")
        return

    ad_id, text, media_type, media_id = ad
    chats = await get_all_chats()
    if not chats:
        await call.answer("Нет чатов")
        return

    await call.message.edit_text(f"⏳ Отправляю в {len(chats)} чатов...")

    sent = 0
    failed = 0
    for chat in chats:
        chat_id, title, ad_enabled, mat_enabled = chat
        if not ad_enabled:
            continue
        try:
            await call.bot.send_message(chat_id, text)
            await log_ad(ad_id, chat_id, "ok")
            sent += 1
        except Exception as e:
            await log_ad(ad_id, chat_id, f"err: {e}")
            failed += 1

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="ad_menu")]
    ])
    await call.message.edit_text(
        f"✅ Отправлено: {sent}\n❌ Ошибок: {failed}",
        reply_markup=kb
    )


# ==== Меню групп ====
@router.callback_query(F.data == "chats_menu")
async def chats_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    chats = await get_all_chats()

    if not chats:
        text = "💬 <b>ГРУППЫ</b>\n\nБот пока нигде не сидит."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
    else:
        text = f"💬 <b>ГРУППЫ ({len(chats)})</b>\n\n"
        buttons = []
        for chat in chats:
            chat_id, title, ad_enabled, mat_enabled = chat
            mark = "✅" if ad_enabled else "⏸"
            text += f"{mark} {title} (<code>{chat_id}</code>)\n"
            buttons.append([InlineKeyboardButton(
                text=f"{mark} {title[:20]}",
                callback_data=f"toggle_{chat_id}"
            )])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await call.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("toggle_"))
async def toggle_chat(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    chat_id = int(call.data.split("_")[1])
    await toggle_chat_ad(chat_id)
    await chats_menu(call)


# ==== Статистика ====
@router.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    s = await get_stats()
    text = (
        "📊 <b>СТАТИСТИКА</b>\n\n"
        f"💬 Чатов: {s['chats']}\n"
        f"👤 Юзеров: {s['users']}\n"
        f"📢 Реклам: {s['ads']}\n"
        f"✅ Отправок: {s['sent']}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
