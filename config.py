import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", "8742164697").replace(" ", "").split(",") if x
]
DB_PATH = os.getenv("DB_PATH", "/app/data/joinbot.db")

DEFAULT_WELCOME = "👋 Привет! Спасибо за заявку, ты принят(а) в канал."
SEND_DELAY = 0.05  # пауза между сообщениями при рассылке
REPORT_HOUR = 9  # утренний отчёт, время по Москве
