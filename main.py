import os
import time
import base64
import json
import sqlite3
import httpx
import asyncio
import io
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
import uvicorn
from pypdf import PdfReader

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
AI_API_KEY = os.getenv("AI_API_KEY")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://xerosharc.site/v1")
GH_TOKEN = os.getenv("GITHUB_TOKEN")
GH_USER = os.getenv("GITHUB_USERNAME")
GH_REPO = os.getenv("GITHUB_REPO")

WEB_APP_URL = "https://bot-1793277514-5190-savik.bothost.tech"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

# --- БАЗА ДАННЫХ ---
DB_FILE = "bot.db"
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, role TEXT, content TEXT, time_spent REAL, tokens INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS memory (user_id INTEGER PRIMARY KEY, facts TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS stats (user_id INTEGER PRIMARY KEY, total_tokens INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# --- ИИ И ИНСТРУМЕНТЫ ---
tools = [
    {"type": "function", "function": {"name": "run_code_piston", "description": "Запустить код", "parameters": {"type": "object", "properties": {"language": {"type": "string"}, "code": {"type": "string"}}, "required": ["language", "code"]}}},
    {"type": "function", "function": {"name": "search_internet", "description": "Поиск в интернете", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}
]

async def ask_ai(user_id, chat_id, text, model="claude-sonnet-5-5"):
    # Очистка ключей от пробелов
    api_key = AI_API_KEY.strip() if AI_API_KEY else ""
    base_url = AI_BASE_URL.strip() if AI_BASE_URL else ""
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
    history = [{"role": r["role"], "content": r["content"]} for r in c.fetchall()]
    
    system_prompt = {"role": "system", "content": "Ты продвинутый ИИ-ассистент. Отвечай кратко и по делу."}
    messages = [system_prompt] + history + [{"role": "user", "content": text}]
    
    start_time = time.perf_counter()
    
    # Двойные заголовки (поддержка и OpenAI, и Anthropic формата)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            # Отправляем на /v1/chat/completions
            url = f"{base_url}/chat/completions"
            res = await client.post(url, headers=headers, json={"model": model, "messages": messages, "tools": tools}, timeout=120)
            res_data = res.json()
            
            if "error" in res_data:
                return f"❌ Ошибка API: {res_data['error'].get('message')}", 0, 0
            
            response_msg = res_data["choices"][0]["message"]
            
            # Логика вызова инструментов
            if response_msg.get("tool_calls"):
                messages.append(response_msg)
                for tc in response_msg["tool_calls"]:
                    func_name = tc["function"]["name"]
                    args = json.loads(tc["function"]["arguments"])
                    result = "Ошибка"
                    
                    if func_name == "run_code_piston":
                        r = await client.post("https://emkc.org/api/v2/piston/execute", json={"language": args["language"], "version": "*", "files": [{"content": args["code"]}]})
                        result = r.json().get('run', {}).get('stdout', 'Ошибка выполнения')
                    elif func_name == "search_internet":
                        # Заглушка для поиска (в реальности нужно подключать парсер)
                        result = "Поиск выполнен. Результаты: (здесь будут данные из интернета)"
                        
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "name": func_name, "content": result})
                
                res2 = await client.post(url, headers=headers, json={"model": model, "messages": messages}, timeout=120)
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
        except Exception as e:
            return f"❌ Системная ошибка: {str(e)}", 0, 0

# --- TELEGRAM БОТ ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть ИИ", web_app=WebAppInfo(url=WEB_APP_URL))]])
    await message.answer("Жми кнопку:", reply_markup=kb)

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
    c.execute("INSERT INTO chats (user_id, title) VALUES (?, ?)", (data['user_id'], data.get('title', 'Новый диалог')))
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
    return {"used": tokens, "limit": 100_000_000, "percent": max(0, round((100_000_000 - tokens) / 1_000_000))}

@app.get("/api/memory")
async def get_memory(user_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT facts FROM memory WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return {"facts": row["facts"] if row else "Пока ничего не запомнил."}

@app.post("/api/chat")
async def api_chat(request: Request):
    data = await request.json()
    answer, time_spent, tokens = await ask_ai(data.get("user_id"), data.get("chat_id"), data.get("text"), data.get("model"))
    return {"answer": answer, "time": time_spent, "tokens": tokens}

@app.post("/api/upload")
async def upload_file(user_id: int = Form(...), file: UploadFile = File(...)):
    content = await file.read()
    text = ""
    if file.filename.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content))
        text = "\n".join([page.extract_text() for page in reader.pages])
    else:
        text = content.decode('utf-8', errors='ignore')
    return {"text": text[:1000] + "..." if len(text) > 1000 else text, "full_length": len(text)}

@app.get("/")
async def serve_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

async def main():
    config = uvicorn.Config(app, host="0.0.0.0", port=3000, log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(asyncio.create_task(server.serve()), asyncio.create_task(dp.start_polling(bot)))

if __name__ == "__main__":
    asyncio.run(main())
