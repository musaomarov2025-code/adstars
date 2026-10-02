import random
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatType
from aiogram.filters import CommandStart

from config import CHIME_EVERY, FEMALE_NAMES, MALE_EXCEPTIONS
from phrases import (
    reaction_to_name,
    reply_to_babka,
    CHIME_PHRASES,
    CHIME_PHRASES_MAT,
    WELCOME_TO_GROUP,
)
from database import save_user, get_user_gender, is_mat_enabled, add_chat

router = Router()

message_counters: dict[int, int] = {}


def detect_gender(name: str) -> str:
    first_name = name.split()[0].lower()
    if first_name in FEMALE_NAMES:
        return "f"
    if first_name in MALE_EXCEPTIONS:
        return "m"
    if first_name.endswith(("а", "я", "ия")):
        return "f"
    return "m"


# ==== /start ====
@router.message(CommandStart())
async def cmd_start(message: Message):
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="➕ Добавить бабку в чат",
            url="https://t.me/BeBavbot?startgroup=true"
        )]
    ])

    await message.answer(
        "Привет! 👵\n\n"
        "Я — Пошлая бабка. Могу заменять оппонента в споре, "
        "поддерживать срачи, выносить мозг, насаждать добро, "
        "причинять пользу.\n\n"
        "Добавить бабку в чат можно так же, как и любого другого пользователя.\n\n"
        "Свойства чата — добавить пользователя, в поиске найти "
        "@BeBavbot или нажать кнопку ниже 👇",
        reply_markup=kb
    )


# ==== Приветствие ====
@router.message(F.new_chat_members)
async def on_add_to_group(message: Message):
    me = await message.bot.get_me()
    for member in message.new_chat_members:
        if member.id == me.id:
            try:
                await message.answer(random.choice(WELCOME_TO_GROUP))
            except Exception as e:
                print(f"[welcome error] {e}")
            try:
                await add_chat(message.chat.id, message.chat.title or "Без названия")
            except Exception as e:
                print(f"[add_chat error] {e}")
            return


# ==== Главный хендлер ====
@router.message(F.text)
async def handle_text(message: Message):
    if not message.text:
        return
    if message.text.startswith("/"):
        return

    text_lower = message.text.lower()
    user = message.from_user
    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    if is_group:
        try:
            await add_chat(message.chat.id, message.chat.title or "Без названия")
        except Exception as e:
            print(f"[track error] {e}")

    # ==== ТРИГГЕР 1: «бабка»/«бабуль» ====
    triggers = ["бабка", "бабуль", "бабуля", "бабушка", "бабке", "бабку", "бабки"]
    if any(w in text_lower for w in triggers):
        try:
            gender = await get_user_gender(user.id)
            if not gender:
                gender = detect_gender(user.full_name)
                await save_user(user.id, user.full_name, gender)
            await message.reply(reaction_to_name(gender))
        except Exception as e:
            print(f"[trigger1 error] {e}")
        return

    # ==== ТРИГГЕР 2: Reply ====
    if message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.is_bot:
            try:
                gender = await get_user_gender(user.id)
                if not gender:
                    gender = detect_gender(user.full_name)
                    await save_user(user.id, user.full_name, gender)
                await message.reply(reply_to_babka(gender))
            except Exception as e:
                print(f"[trigger2 error] {e}")
            return

    # ==== ТРИГГЕР 3: Влезает сама ====
    if is_group:
        counter = message_counters.get(message.chat.id, 0) + 1
        message_counters[message.chat.id] = counter

        if counter % CHIME_EVERY == 0:
            try:
                mat_on = await is_mat_enabled(message.chat.id)
            except Exception:
                mat_on = True
            pool = CHIME_PHRASES + (CHIME_PHRASES_MAT if mat_on else [])
            try:
                await message.reply(random.choice(pool))
            except Exception as e:
                print(f"[chime error] {e}")
