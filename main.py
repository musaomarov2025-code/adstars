import os
import time
import base64
import json
import sqlite3
import httpx
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message
import uvicorn
from pypdf import PdfReader
from docx import Document
import io

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
AI_API_KEY = os.getenv("AI_API_KEY")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://xerosharc.site/v1")
GH_TOKEN = os.getenv("GITHUB_TOKEN")
GH_USER = os.getenv("GITHUB_USERNAME")
GH_REPO = os.getenv("GITHUB_REPO")

WEB_APP_URL = "https://bot-1793277514-5190-savik.bothost.tech" # Твой домен

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

# --- БАЗА ДАННЫХ (SQLite) ---
DB_FILE = "bot.db"
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Таблица чатов
    c.execute('''CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    # Таблица сообщений
    c.execute('''CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, role TEXT, content TEXT, time_spent REAL, tokens INTEGER)''')
    # Таблица памяти (факты о пользователе)
    c.execute('''CREATE TABLE IF NOT EXISTS memory (user_id INTEGER PRIMARY KEY, facts TEXT)''')
    # Таблица статистики токенов
    c.execute('''CREATE TABLE IF NOT EXISTS stats (user_id INTEGER PRIMARY KEY, total_tokens INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# --- ИНСТРУМЕНТЫ ИИ ---
tools = [
    {"type": "function", "function": {"name": "list_github_files", "description": "Показать список файлов в репозитории GitHub", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "delete_github_file", "description": "Удалить файл", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "upload_github_file", "description": "Загрузить или обновить файл", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_code_piston", "description": "Запустить код на Python, JS и т.д.", "parameters": {"type": "object", "properties": {"language": {"type": "string"}, "code": {"type": "string"}}, "required": ["language", "code"]}}},
    {"type": "function", "function": {"name": "search_internet", "description": "Поиск информации в интернете", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "save_memory", "description": "Сохранить факт о пользователе", "parameters": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}}}
]

async def execute_tool(tool_name, args, user_id):
    try:
        async with httpx.AsyncClient() as client:
            if tool_name == "list_github_files":
                headers = {"Authorization": f"token {GH_TOKEN}"}
                res = await client.get(f"https://api.github.com/repos/{GH_USER}/{GH_REPO}/contents", headers=headers)
                return f"Файлы: {', '.join([f['name'] for f in res.json()])}" if res.status_code == 200 else f"Ошибка GitHub: {res.status_code}"
            
            elif tool_name == "upload_github_file":
                headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
                content_b64 = base64.b64encode(args["content"].encode()).decode()
                res = await client.put(f"https://api.github.com/repos/{GH_USER}/{GH_REPO}/contents/{args['path']}", headers=headers, json={"message": "AI Upload", "content": content_b64})
                return "Файл загружен." if res.status_code in [200, 201] else "Ошибка загрузки."
            
            elif tool_name == "run_code_piston":
                res = await client.post("https://emkc.org/api/v2/piston/execute", json={"language": args["language"], "version": "*", "files": [{"content": args["code"]}]})
                run = res.json().get('run', {})
                return f"STDOUT:\n{run.get('stdout')}\nSTDERR:\n{run.get('stderr')}"
            
            elif tool_name == "search_internet":
                # Простой парсер DuckDuckGo
                headers = {"User-Agent": "Mozilla/5.0"}
                res = await client.get(f"https://html.duckduckgo.com/html/?q={args['query']}", headers=headers)
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(res.text, 'html.parser')
                results = [a.text for a in soup.find_all('a', class_='result__snippet')[:3]]
                return "Результаты поиска:\n" + "\n".join(results)
            
            elif tool_name == "save_memory":
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT facts FROM memory WHERE user_id = ?", (user_id,))
                row = c.fetchone()
                facts = row['facts'] + "\n" + args['fact'] if row else args['fact']
                c.execute("INSERT OR REPLACE INTO memory (user_id, facts) VALUES (?, ?)", (user_id, facts))
                conn.commit()
                conn.close()
                return "Факт сохранен в память."
    except Exception as e:
        return f"Ошибка: {e}"

# --- ЛОГИКА ИИ ---
async def ask_ai(user_id, chat_id, text, model="claude-sonnet-5-5"):
    conn = get_db()
    c = conn.cursor()
    
    # Получаем память пользователя
    c.execute("SELECT facts FROM memory WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    memory_text = f"\n[ТЫ ЗНАЕШЬ О ПОЛЬЗОВАТЕЛЕ]: {row['facts']}" if row else ""
    
    # Получаем историю текущего чата
    c.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
    history = [{"role": r["role"], "content": r["content"]} for r in c.fetchall()]
    
    system_prompt = {"role": "system", "content": f"Ты ИИ-ассистент. Ты умеешь писать код, искать в интернете и управлять GitHub. Если нужно запомнить факт, используй save_memory. {memory_text}"}
    
    messages = [system_prompt] + history + [{"role": "user", "content": text}]
    
    start_time = time.perf_counter()
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    
    async with httpx.AsyncClient() as client:
        res = await client.post(f"{AI_BASE_URL}/chat/completions", headers=headers, json={"model": model, "messages": messages, "tools": tools}, timeout=120)
        res_data = res.json()
        
        if "error" in res_data: return f"Ошибка API: {res_data['error'].get('message')}", 0, 0
        
        response_msg = res_data["choices"][0]["message"]
        
        if response_msg.get("tool_calls"):
            messages.append(response_msg)
            for tc in response_msg["tool_calls"]:
                args = json.loads(tc["function"]["arguments"])
                result = await execute_tool(tc["function"]["name"], args, user_id)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"], "content": result})
            
            res2 = await client.post(f"{AI_BASE_URL}/chat/completions", headers=headers, json={"model": model, "messages": messages}, timeout=120)
            res_data = res2.json()
            response_msg = res_data["choices"][0]["message"]
            
        elapsed = round(time.perf_counter() - start_time, 2)
        tokens = res_data.get("usage", {}).get("total_tokens", 0)
        answer = response_msg.get("content") or "Готово."
        
        # Сохраняем в БД
        c.execute("INSERT INTO messages (chat_id, role, content, time_spent, tokens) VALUES (?, ?, ?, ?, ?)", (chat_id, "user", text, 0, 0))
        c.execute("INSERT INTO messages (chat_id, role, content, time_spent, tokens) VALUES (?, ?, ?, ?, ?)", (chat_id, "assistant", answer, elapsed, tokens))
        c.execute("INSERT INTO stats (user_id, total_tokens) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET total_tokens = total_tokens + ?", (user_id, tokens, tokens))
        conn.commit()
        conn.close()
        
        return answer, elapsed, tokens

# --- БОТ TELEGRAM ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть ИИ", web_app=WebAppInfo(url=WEB_APP_URL))]])
    await message.answer("Жми кнопку:", reply_markup=kb)

@dp.message(F.document)
async def handle_document(message: types.Message):
    file = await bot.get_file(message.document.file_id)
    downloaded = await bot.download_file(file.file_path)
    text = ""
    
    if message.document.file_name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(downloaded.read()))
        text = "\n".join([page.extract_text() for page in reader.pages])
    elif message.document.file_name.endswith(".docx"):
        doc = Document(io.BytesIO(downloaded.read()))
        text = "\n".join([p.text for p in doc.paragraphs])
    else:
        text = downloaded.read().decode('utf-8', errors='ignore')
        
    await message.answer(f"📄 Файл прочитан! Отправь его в Mini App для анализа (или напиши вопрос).\n\nПервые 500 символов:\n{text[:500]}...")

# --- API ДЛЯ MINI APP ---
@app.get("/api/chats")
async def get_chats(user_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, title FROM chats WHERE user_id = ? ORDER BY id DESC", (user_id,))
    chats = [{"id": r["id"], "title": r["title"]} for r in c.fetchall()]
    conn.close()
    return chats

@app.post("/api/new_chat")
async def new_chat(request: Request):
    data = await request.json()
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO chats (user_id, title) VALUES (?, ?)", (data['user_id'], "Новый диалог"))
    conn.commit()
    chat_id = c.lastrowid
    conn.close()
    return {"chat_id": chat_id}

@app.get("/api/history")
async def get_history(chat_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT role, content, time_spent, tokens FROM messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
    hist = [dict(r) for r in c.fetchall()]
    conn.close()
    return hist

@app.get("/api/profile")
async def get_profile(user_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT total_tokens FROM stats WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    tokens = row["total_tokens"] if row else 0
    conn.close()
    # Лимит 100 млн
    limit = 100_000_000
    percent = max(0, round((limit - tokens) / limit * 100))
    return {"used": tokens, "limit": limit, "percent": percent}

@app.post("/api/chat")
async def api_chat(request: Request):
    data = await request.json()
    answer, time_spent, tokens = await ask_ai(data.get("user_id"), data.get("chat_id"), data.get("text"), data.get("model"))
    return {"answer": answer, "time": time_spent, "tokens": tokens}

@app.get("/")
async def serve_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

# --- ЗАПУСК ---
async def main():
    port = int(os.getenv("PORT", 3000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(asyncio.create_task(server.serve()), asyncio.create_task(dp.start_polling(bot)))

if __name__ == "__main__":
    asyncio.run(main())
