from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)


def main_menu():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="📢 Автопост")],
        [KeyboardButton(text="📊 Отчёт"), KeyboardButton(text="⚙️ Настройки")],
    ])


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Автопост", callback_data="ap_menu")],
        [InlineKeyboardButton(text="✏️ Тексты", callback_data="ap_texts"),
         InlineKeyboardButton(text="🆔 Чаты", callback_data="ap_chats")],
        [InlineKeyboardButton(text="📎 Медиа", callback_data="ap_media"),
         InlineKeyboardButton(text="🔗 Кнопки", callback_data="ap_buttons")],
        [InlineKeyboardButton(text="⏱ Интервал", callback_data="ap_interval")],
        [InlineKeyboardButton(text="📊 Отчёт", callback_data="report_now")],
    ])


def back_admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="ap_menu")],
    ])


def ap_menu_kb(enabled):
    status = "🔴 Выключить" if enabled else "🟢 Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=status, callback_data="ap_toggle")],
        [InlineKeyboardButton(text="✏️ Тексты", callback_data="ap_texts"),
         InlineKeyboardButton(text="🆔 Чаты", callback_data="ap_chats")],
        [InlineKeyboardButton(text="📎 Медиа", callback_data="ap_media"),
         InlineKeyboardButton(text="🔗 Кнопки", callback_data="ap_buttons")],
        [InlineKeyboardButton(text="⏱ Интервал", callback_data="ap_interval")],
        [InlineKeyboardButton(text="📊 Отчёт", callback_data="report_now")],
    ])


def texts_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data="ap_text_add")],
        [InlineKeyboardButton(text="📜 Список", callback_data="ap_text_show")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data="ap_text_del")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="ap_menu")],
    ])


def chats_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data="ap_chat_add")],
        [InlineKeyboardButton(text="📜 Список", callback_data="ap_chat_show")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data="ap_chat_del")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="ap_menu")],
    ])


def media_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Загрузить", callback_data="ap_media_add")],
        [InlineKeyboardButton(text="🗑 Убрать", callback_data="ap_media_del")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="ap_menu")],
    ])
