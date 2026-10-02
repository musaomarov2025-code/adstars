import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = 8742164697  # твой ID (для /addchat и уведомлений)
DB = "/app/data/adbot.db"

DEFAULTS = {
    "autopost_enabled": "0",
    "autopost_interval": "30",       # секунд между чатами
    "autopost_texts": "[]",           # JSON-массив
    "autopost_chats": "[]",           # JSON-массив chat_id
    "autopost_media_type": "",        # "" / "photo" / "video" / "animation" / "sticker"
    "autopost_media_id": "",          # file_id
    "autopost_buttons": "",           # формат: Текст - https://... \n Текст2 - https://...
}
