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
from fastapi.responses import HTMLResponse
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
    c.execute('''CREATE TABLE IF NOT EXISTS settings (user_id INTEGER PRIMARY KEY, persona TEXT DEFAULT 'Обычный ассистент', search_enabled INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()
init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# --- ИНСТРУМЕНТЫ (ВСЕ, ЧТО ТЫ ПРОСИЛ) ---
tools = [
    {"type": "function", "function": {"name": "list_github_files", "description": "Показать список файлов в репозитории", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "delete_github_file", "description": "Удалить файл из репозитория", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "delete_all_github_files", "description": "Удалить ВСЕ файлы из репозитория", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "upload_github_file", "description": "Загрузить или обновить файл в GitHub", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "read_github_file", "description": "Прочитать содержимое файла из GitHub", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "run_code_piston", "description": "Запустить код", "parameters": {"type": "object", "properties": {"language": {"type": "string"}, "code": {"type": "string"}}, "required": ["language", "code"]}}},
    {"type": "function", "function": {"name": "search_internet", "description": "Поиск в интернете", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "save_memory", "description": "Сохранить важный факт о пользователе", "parameters": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}}}
]

async def execute_tool(tool_name, args, user_id):
    gh_headers = {"Authorization": f"token {GH_TOKEN.strip()}", "Accept": "application/vnd.github.v3+json"}
    base_url = f"https://api.github.com/repos/{GH_USER.strip()}/{GH_REPO.strip()}/contents"
    
    try:
        async with httpx.AsyncClient() as client:
            if tool_name == "list_github_files":
                res = await client.get(base_url, headers=gh_headers)
                if res.status_code == 200:
                    files = [f["name"] for f in res.json()]
                    return f"Файлы: {', '.join(files) if files else 'Пусто'}"
                return f"Ошибка GitHub: {res.status_code}"

            elif tool_name == "read_github_file":
                res = await client.get(f"{base_url}/{args['path']}", headers=gh_headers)
                if res.status_code == 200:
                    content = base64.b64decode(res.json()["content"]).decode('utf-8')
                    return f"Содержимое {args['path']}:\n\n{content}"
                return "Файл не найден."

            elif tool_name == "delete_github_file":
                res = await client.get(f"{base_url}/{args['path']}", headers=gh_headers)
                if res.status_code == 200:
                    await client.delete(f"{base_url}/{args['path']}", headers=gh_headers, json={"message": "AI Delete", "sha": res.json()["sha"]})
                    return f"Файл {args['path']} удален."
                return "Файл не найден."

            elif tool_name == "delete_all_github_files":
                res = await client.get(base_url, headers=gh_headers)
                if res.status_code == 200:
                    for f in res.json():
                        await client.delete(f"{base_url}/{f['name']}", headers=gh_headers, json={"message": "AI Delete All", "sha": f["sha"]})
                    return "Все файлы удалены."
                return "Ошибка."

            elif tool_name == "upload_github_file":
                path = args["path"]
                content_b64 = base64.b64encode(args["content"].encode()).decode()
                check = await client.get(f"{base_url}/{path}", headers=gh_headers)
                data = {"message": "AI Update", "content": content_b64}
                if check.status_code == 200: data["sha"] = check.json()["sha"]
                res = await client.put(f"{base_url}/{path}", headers=gh_headers, json=data)
                return f"Файл {path} загружен." if res.status_code in [200, 201] else "Ошибка загрузки."

            elif tool_name == "run_code_piston":
                res = await client.post("https://emkc.org/api/v2/piston/execute", json={"language": args["language"], "version": "*", "files": [{"content": args["code"]}]}, timeout=20)
                if res.status_code == 200:
                    run = res.json().get("run", {})
                    return f"STDOUT:\n{run.get('stdout')}\nSTDERR:\n{run.get('stderr')}"
                return "Ошибка запуска."

            elif tool_name == "search_internet":
                from bs4 import BeautifulSoup
                res = await client.get(f"https://html.duckduckgo.com/html/?q={args['query']}", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                soup = BeautifulSoup(res.text, 'html.parser')
                results = [a.text for a in soup.find_all('a', class_='result__snippet')[:3]]
                return "Найдено в интернете:\n" + "\n".join(results)

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

async def ask_ai(user_id, chat_id, text, model):
    api_key = AI_API_KEY.strip()
    base_url = AI_BASE_URL.strip()
    
    conn = get_db()
    c = conn.cursor()
    
    # Память и Настройки
    c.execute("SELECT facts FROM memory WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    memory = f"\n[ПАМЯТЬ О ПОЛЬЗОВАТЕЛЕ]: {row['facts']}" if row else ""
    
    c.execute("SELECT persona FROM settings WHERE user_id = ?", (user_id,))
    p_row = c.fetchone()
    persona = p_row['persona'] if p_row else "Обычный ассистент"

    sys_prompt = f"""Ты ИИ-ассистент ({persona}). Ты умеешь управлять GitHub, запускать код и искать в интернете.
    Если тебя просят написать код, ОБЯЗАТЕЛЬНО проверь его через run_code_piston. Если есть ошибка - исправь и проверь снова. Если все ок - предложи загрузить в GitHub.
    Если код слишком длинный, пиши его частями.
    Ты можешь запоминать факты о пользователе через save_memory.
    {memory}"""
    
    c.execute("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
    history = [{"role": r["role"], "content": r["content"]} for r in c.fetchall()]
    messages = [{"role": "system", "content": sys_prompt}] + history + [{"role": "user", "content": text}]
    
    start_time = time.perf_counter()
    headers = {"Authorization": f"Bearer {api_key}", "x-api-key": api_key, "Content-Type": "application/json"}
    
    async with httpx.AsyncClient() as client:
        url = f"{base_url}/chat/completions"
        res = await client.post(url, headers=headers, json={"model": model, "messages": messages, "tools": tools}, timeout=120)
        res_data = res.json()
        
        if "error" in res_data: return f"Ошибка API: {res_data['error'].get('message')}", 0, 0
        
        msg = res_data["choices"][0]["message"]
        
        # Цикл авто-исправления и вызова инструментов (до 3 раз)
        for _ in range(3):
            if msg.get("tool_calls"):
                messages.append(msg)
                for tc in msg["tool_calls"]:
                    result = await execute_tool(tc["function"]["name"], json.loads(tc["function"]["arguments"]), user_id)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"], "content": result})
                
                res = await client.post(url, headers=headers, json={"model": model, "messages": messages, "tools": tools}, timeout=120)
                res_data = res.json()
                msg = res_data["choices"][0]["message"]
            else:
                break
                
        elapsed = round(time.perf_counter() - start_time, 2)
        tokens = res_data.get("usage", {}).get("total_tokens", 0)
        answer = msg.get("content") or "Выполнено."
        
        c.execute("INSERT INTO messages (chat_id, role, content, time_spent, tokens) VALUES (?, ?, ?, ?, ?)", (chat_id, "user", text, 0, 0))
        c.execute("INSERT INTO messages (chat_id, role, content, time_spent, tokens) VALUES (?, ?, ?, ?, ?)", (chat_id, "assistant", answer, elapsed, tokens))
        c.execute("INSERT INTO stats (user_id, total_tokens) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET total_tokens = total_tokens + ?", (user_id, tokens, tokens))
        conn.commit()
        conn.close()
        return answer, elapsed, tokens

# --- API ---
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
    c.execute("INSERT INTO chats (user_id, title) VALUES (?, ?)", (data['user_id'], data.get('title', 'Диалог')))
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

@app.get("/api/settings")
async def get_settings(user_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT persona, search_enabled FROM settings WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return {"persona": row["persona"] if row else "Обычный ассистент", "search_enabled": row["search_enabled"] if row else 0}

@app.post("/api/settings")
async def update_settings(request: Request):
    data = await request.json()
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO settings (user_id, persona, search_enabled) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET persona=excluded.persona, search_enabled=excluded.search_enabled", (data['user_id'], data['persona'], data['search_enabled']))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/profile")
async def get_profile(user_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT total_tokens FROM stats WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    tokens = row["total_tokens"] if row else 0
    c.execute("SELECT facts FROM memory WHERE user_id = ?", (user_id,))
    m_row = c.fetchone()
    conn.close()
    return {"used": tokens, "limit": 100_000_000, "percent": max(0, round((100_000_000 - tokens) / 1_000_000)), "memory": m_row["facts"] if m_row else "Пусто"}

@app.post("/api/chat")
async def api_chat(request: Request):
    data = await request.json()
    ans, tm, tk = await ask_ai(data["user_id"], data["chat_id"], data["text"], data["model"])
    return {"answer": ans, "time": tm, "tokens": tk}

@app.post("/api/upload")
async def upload_file(user_id: int = Form(...), file: UploadFile = File(...)):
    content = await file.read()
    text = ""
    if file.filename.endswith(".pdf"):
        text = "\n".join([p.extract_text() for p in PdfReader(io.BytesIO(content)).pages])
    else:
        text = content.decode('utf-8', errors='ignore')
    return {"text": text[:2000] + "..." if len(text) > 2000 else text}

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
