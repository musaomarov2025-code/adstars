import os
import time
import base64
import json
import httpx
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
import uvicorn

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
AI_API_KEY = os.getenv("AI_API_KEY")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://xerosharc.site/v1")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "claude-sonnet-5-5")
GH_TOKEN = os.getenv("GITHUB_TOKEN")
GH_USER = os.getenv("GITHUB_USERNAME")
GH_REPO = os.getenv("GITHUB_REPO")

# ВСТАВЬ СЮДА НОВЫЙ ДОМЕН
WEB_APP_URL = "https://bot-1793460794-9525-savik.bothost.tech"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()
user_contexts = {}

# --- ИНТЕРФЕЙС ---
HTML_CONTENT = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AI Assistant</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        :root { --bg: #1e1e24; --bg2: #2b2b36; --text: #e0e0e0; --accent: #d452d4; --border: #3d3d4a; }
        body { margin: 0; padding: 0; background: var(--bg); color: var(--text); font-family: -apple-system, sans-serif; display: flex; flex-direction: column; height: 100vh; }
        .header { padding: 15px; background: var(--bg2); display: flex; justify-content: space-between; border-bottom: 1px solid var(--border); }
        .model-select { background: transparent; color: var(--text); border: none; font-size: 16px; outline: none; }
        .chat { flex: 1; padding: 15px; overflow-y: auto; display: flex; flex-direction: column; gap: 15px; }
        .message { max-width: 85%; padding: 12px 16px; border-radius: 18px; line-height: 1.4; font-size: 15px; white-space: pre-wrap; }
        .msg-user { align-self: flex-end; background: var(--bg2); border-bottom-right-radius: 4px; }
        .msg-ai { align-self: flex-start; background: rgba(212, 82, 212, 0.1); border: 1px solid rgba(212, 82, 212, 0.3); border-bottom-left-radius: 4px; }
        .stats { font-size: 11px; color: #888; margin-top: 6px; text-align: right; }
        .input-area { padding: 10px; background: var(--bg2); display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--border); }
        .input-row { display: flex; gap: 10px; align-items: center; }
        .input-row input { flex: 1; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 12px; border-radius: 20px; outline: none; font-size: 15px; }
        .send-btn { background: var(--accent); color: white; border: none; width: 44px; height: 44px; border-radius: 50%; font-size: 18px; cursor: pointer; }
    </style>
</head>
<body>
    <div class="header">
        <select class="model-select" id="model">
            <option value="claude-sonnet-5-5">claude-sonnet-5-5</option>
            <option value="claude-opus-5-5">claude-opus-5-5</option>
        </select>
    </div>
    <div class="chat" id="chat">
        <div class="message msg-ai">Привет! Я твой ИИ-ассистент.</div>
    </div>
    <div class="input-area">
        <div class="input-row">
            <input type="text" id="messageInput" placeholder="Спроси..." onkeypress="if(event.key === 'Enter') sendMessage()">
            <button class="send-btn" onclick="sendMessage()">➤</button>
        </div>
    </div>
    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();
        const userId = tg.initDataUnsafe?.user?.id || 123;
        async function sendMessage() {
            const input = document.getElementById('messageInput');
            const text = input.value.trim();
            if (!text) return;
            addMessage(text, 'user');
            input.value = '';
            const loadingId = 'loading-' + Date.now();
            addMessage('Думаю...', 'ai', loadingId);
            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_id: userId, text: text, model: document.getElementById('model').value })
                });
                const data = await response.json();
                document.getElementById(loadingId).remove();
                addMessage(data.answer, 'ai', null, data.time, data.tokens);
            } catch (e) {
                document.getElementById(loadingId).remove();
                addMessage('Ошибка сервера.', 'ai');
            }
        }
        function addMessage(text, type, id = null, time = null, tokens = null) {
            const chat = document.getElementById('chat');
            const div = document.createElement('div');
            div.className = `message msg-${type}`;
            if (id) div.id = id;
            div.innerText = text; 
            if (time !== null && tokens !== null) {
                const statsDiv = document.createElement('div');
                statsDiv.className = 'stats';
                statsDiv.innerText = `⏱ ${time} сек | 🪙 ${tokens} токенов`;
                div.appendChild(statsDiv);
            }
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
        }
    </script>
</body>
</html>
"""

tools = [
    {"type": "function", "function": {"name": "list_github_files", "description": "Список файлов", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "delete_github_file", "description": "Удалить файл", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "delete_all_github_files", "description": "Удалить все", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "upload_github_file", "description": "Загрузить файл", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_code_piston", "description": "Запустить код", "parameters": {"type": "object", "properties": {"language": {"type": "string"}, "code": {"type": "string"}}, "required": ["language", "code"]}}}
]

async def execute_tool(tool_name, args):
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    base_url = f"https://api.github.com/repos/{GH_USER}/{GH_REPO}/contents"
    try:
        async with httpx.AsyncClient() as client:
            if tool_name == "list_github_files":
                res = await client.get(base_url, headers=headers)
                return f"Файлы: {', '.join([f['name'] for f in res.json()])}" if res.status_code == 200 else "Ошибка API."
            elif tool_name == "delete_github_file":
                res = await client.get(f"{base_url}/{args['path']}", headers=headers)
                if res.status_code == 200:
                    await client.delete(f"{base_url}/{args['path']}", headers=headers, json={"message": "del", "sha": res.json().get("sha")})
                    return f"Файл {args['path']} удален."
                return "Не найден."
            elif tool_name == "delete_all_github_files":
                res = await client.get(base_url, headers=headers)
                if res.status_code == 200:
                    for f in res.json(): await client.delete(f"{base_url}/{f['name']}", headers=headers, json={"message": "del", "sha": f['sha']})
                    return "Все удалено."
                return "Пусто."
            elif tool_name == "upload_github_file":
                content_b64 = base64.b64encode(args["content"].encode()).decode()
                check = await client.get(f"{base_url}/{args['path']}", headers=headers)
                data = {"message": "upd", "content": content_b64}
                if check.status_code == 200: data["sha"] = check.json().get("sha")
                await client.put(f"{base_url}/{args['path']}", headers=headers, json=data)
                return "Загружено."
            elif tool_name == "run_code_piston":
                res = await client.post("https://emkc.org/api/v2/piston/execute", json={"language": args["language"], "version": "*", "files": [{"content": args["code"]}]}, timeout=15)
                return f"STDOUT:\n{res.json().get('run', {}).get('stdout')}" if res.status_code == 200 else "Ошибка."
    except Exception as e: return f"Ошибка: {e}"

async def ask_ai(user_id, text, model=DEFAULT_MODEL):
    if user_id not in user_contexts:
        user_contexts[user_id] = [{"role": "system", "content": "Ты ассистент. Принимай токены."}]
    user_contexts[user_id].append({"role": "user", "content": text})
    start_time = time.perf_counter()
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        res = await client.post(f"{AI_BASE_URL}/chat/completions", headers=headers, json={"model": model, "messages": user_contexts[user_id], "tools": tools}, timeout=90)
        res_data = res.json()
        if "error" in res_data: return f"Ошибка API: {res_data['error'].get('message')}", 0, 0
        response_msg = res_data["choices"][0]["message"]
        if response_msg.get("tool_calls"):
            user_contexts[user_id].append(response_msg)
            for tool_call in response_msg["tool_calls"]:
                func_name = tool_call["function"]["name"]
                args = json.loads(tool_call["function"]["arguments"])
                tool_result = await execute_tool(func_name, args)
                user_contexts[user_id].append({"role": "tool", "tool_call_id": tool_call["id"], "name": func_name, "content": tool_result})
            res2 = await client.post(f"{AI_BASE_URL}/chat/completions", headers=headers, json={"model": model, "messages": user_contexts[user_id]}, timeout=90)
            res_data = res2.json()
            response_msg = res_data["choices"][0]["message"]
        elapsed_time = round(time.perf_counter() - start_time, 2)
        total_tokens = res_data.get("usage", {}).get("total_tokens", 0)
        answer = response_msg.get("content") or "Выполнено."
        user_contexts[user_id].append({"role": "assistant", "content": answer})
        return answer, elapsed_time, total_tokens

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть ИИ", web_app=WebAppInfo(url=WEB_APP_URL))]])
    await message.answer("Жми кнопку:", reply_markup=kb)

@dp.message(F.text)
async def handle_text(message: types.Message):
    msg = await message.answer("⏳ Думаю...")
    answer, time_spent, tokens = await ask_ai(message.from_user.id, message.text)
    await msg.edit_text(f"{answer}\n\n⏱ {time_spent} сек | 🪙 {tokens} токенов", parse_mode=None)

@app.post("/api/chat")
async def api_chat(request: Request):
    data = await request.json()
    answer, time_spent, tokens = await ask_ai(data.get("user_id", 0), data.get("text", ""), data.get("model", DEFAULT_MODEL))
    return {"answer": answer, "time": time_spent, "tokens": tokens}

@app.get("/")
async def serve_index():
    return HTMLResponse(content=HTML_CONTENT)

@app.get("/ping")
async def ping():
    return {"status": "ok", "message": "Web server is running!"}

# --- БЕЗОПАСНЫЙ ЗАПУСК (WEB + BOT) ---
async def main():
    # Получаем порт из настроек BotHost (у тебя стоит 3000)
    port = int(os.getenv("PORT", 3000))
    
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    
    # Запускаем веб-сервер в основной петле событий
    server_task = asyncio.create_task(server.serve())
    
    # Запускаем бота
    bot_task = asyncio.create_task(dp.start_polling(bot))
    
    # Ждем выполнения обоих
    await asyncio.gather(server_task, bot_task)

if __name__ == "__main__":
    asyncio.run(main())
