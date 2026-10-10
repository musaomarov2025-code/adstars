# 🤖 Telegram Userbot Control Panel

Мини-приложение для управления юзерботом: рассылка, автоподписка, автотриггер.

## 📋 Возможности

- **Рассылка** — по чатам/группам с задержками, лимитами, повторами
- **Автоподписка** — подписка на ссылки от указанных ботов-админов
- **Автотриггер** — ответ на слова в чатах + авто-добавление в контакты
- **Уведомления** — в ЛС при ограничениях аккаунта
- **Логи** — все действия в реальном времени

## 🗂 Структура

```
.
├── main.py               # FastAPI + webhook
├── bot.py                # aiogram бот (webhook)
├── telethon_client.py    # юзербот (рассылка, триггеры, подписки)
├── database.py           # SQLite
├── config.py             # .env переменные
├── requirements.txt
├── .env.example
├── .gitignore
└── static/
    ├── index.html
    ├── style.css
    └── app.js
```

## 🚀 Деплой на BotHost

### 1. Создай бота
- Открой [@BotFather](https://t.me/BotFather) → `/newbot`
- Скопируй **токен**

### 2. Получи API_ID и API_HASH
- Зайди на [my.telegram.org](https://my.telegram.org) → API development tools
- Скопируй **api_id** и **api_hash**

### 3. Узнай свой Telegram ID
- Напиши [@userinfobot](https://t.me/userinfobot)
- Скопируй **Id**

### 4. Загрузи код на GitHub
```bash
git init
git add .
git commit -m "init"
git remote add origin https://github.com/твой-юзер/репо.git
git push -u origin main
```

### 5. Настрой BotHost
- Создай проект → **Python** → подключи GitHub-репо
- **Точка входа:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
- **Переменные окружения:**

| Переменная | Значение |
|---|---|
| `BOT_TOKEN` | токен от BotFather |
| `OWNER_ID` | твой ID |
| `API_ID` | с my.telegram.org |
| `API_HASH` | с my.telegram.org |
| `WEBAPP_URL` | `https://твой-домен.bothost.ru` |
| `DB_PATH` | `data.db` |
| `SESSION_PATH` | `sessions/user` |

### 6. Установи кнопку Mini App
- @BotFather → `/mybots` → твой бот → **Bot Settings** → **Menu Button** → **Edit menu button URL**
- Вставь: `https://твой-домен.bothost.ru`

### 7. Запусти
- BotHost сам запустит процесс
- Открой бота → `/start` → «🚀 Открыть панель»
- В Mini App → **Аккаунт** → введи номер → код → 2FA (если есть)

## ⚠️ Безопасность

- **НЕ** коммить `.env` и `sessions/` в GitHub
- `SESSION_PATH` = полный доступ к аккаунту
- Используй **твинк** для тестов
- Лимиты: **не более 300 сообщений/день** с прогретого аккаунта
- Задержки: **минимум 30 сек** между сообщениями в разные чаты

## 🛠 Решение проблем

**«initData invalid»**
→ Проверь `BOT_TOKEN` в `.env`, он должен быть от того же бота

**«Telethon не авторизован»**
→ Открой Mini App → **Аккаунт** → пройди вход

**Webhook не ставится**
→ Проверь `WEBAPP_URL`, должен быть HTTPS и без слэша в конце

**Аккаунт забанили**
→ Уменьши лимиты, увеличь задержки, используй прогретый аккаунт

## 📝 Лицензия

Только для личного использования. Автор не несёт ответственности за блокировки аккаунтов.
.
