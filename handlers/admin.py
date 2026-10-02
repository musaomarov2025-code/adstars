import os
import shutil
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_ID, DB_PATH
from database import (
    get_all_chats, get_all_ads, add_ad, delete_ad,
    get_next_ad, log_ad, get_stats, toggle_chat_ad
)

router = Router()


class AdStates(StatesGroup):
    waiting_text = State()


class DBStates(StatesGroup):
    waiting_file = State()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Реклама", callback_data="ad_menu")],
        [InlineKeyboardButton(text="💬 Группы", callback_data="chats_menu")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="🗄 База данных", callback_data="db_menu")],
    ])


def db_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать БД", callback_data="db_download")],
        [InlineKeyboardButton(text="📤 Загрузить БД", callback_data="db_upload")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])


# ==== /admin ====
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🔧 <b>АДМИН-ПАНЕЛЬ</b>", reply_markup=admin_menu())


@router.callback_query(F.data == "main_menu")
async def back_to_main(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.clear()
    await call.message.edit_text("🔧 <b>АДМИН-ПАНЕЛЬ</b>", reply_markup=admin_menu())


# ==== БАЗА ДАННЫХ ====
@router.callback_query(F.data == "db_menu")
async def db_menu_handler(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return

    size = "—"
    if os.path.exists(DB_PATH):
        size = f"{os.path.getsize(DB_PATH) / 1024:.1f} KB"

    text = (
        "🗄 <b>БАЗА ДАННЫХ</b>\n\n"
        f"Файл: <code>{DB_PATH}</code>\n"
        f"Размер: {size}\n\n"
        "📥 <b>Скачать</b> — получить текущую БД\n"
        "📤 <b>Загрузить</b> — заменить БД из файла\n\n"
        "⚠️ Перед загрузкой сделай резервную копию!"
    )
    await call.message.edit_text(text, reply_markup=db_menu())


# ==== Скачать БД ====
@router.callback_query(F.data == "db_download")
async def db_download(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return

    if not os.path.exists(DB_PATH):
        await call.answer("Файл БД не найден", show_alert=True)
        return

    try:
        file = FSInputFile(DB_PATH, filename="bot.db")
        await call.message.answer_document(file, caption="📥 База данных бота")
        await call.answer("Отправлено!")
    except Exception as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)


# ==== Загрузить БД ====
@router.callback_query(F.data == "db_upload")
async def db_upload(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return

    await call.message.edit_text(
        "📤 <b>ЗАГРУЗКА БД</b>\n\n"
        "Отправь мне файл <code>bot.db</code> одним сообщением.\n\n"
        "⚠️ Текущая БД будет заменена!\n"
        "Убедись, что у тебя есть резервная копия."
    )
    await state.set_state(DBStates.waiting_file)


@router.message(DBStates.waiting_file, F.document)
async def db_receive(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    doc = message.document
    if not doc.file_name.endswith(".db"):
        await message.answer("❌ Нужен файл с расширением <code>.db</code>")
        return

    # Делаем бэкап старой БД
    backup = DB_PATH + ".backup"
    try:
        if os.path.exists(DB_PATH):
            shutil.copy(DB_PATH, backup)
    except Exception as e:
        print(f"[backup error] {e}")

    # Сохраняем новую БД
    try:
        file = await message.bot.get_file(doc.file_id)
        await message.bot.download_file(file.file_path, DB_PATH)
        await message.answer(
            "✅ <b>БД загружена!</b>\n\n"
            f"Бэкап старой БД: <code>{backup}</code>\n\n"
            "Перезапусти бота, чтобы изменения точно применились.",
            reply_markup=admin_menu()
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка загрузки: {e}")

    await state.clear()


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
