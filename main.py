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

# Твой домен
WEB_APP_URL = "https://bot-1793277514-5190-savik.bothost.tech"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()
user_contexts = {}

# --- ИНТЕРФЕЙС С ЗАГРУЗКОЙ ИСТОРИИ И ВСЕМИ МОДЕЛЯМИ ---
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
        .header { padding: 15px; background: var(--bg2); display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); }
        .model-select { background: transparent; color: var(--text); border: none; font-size: 15px; font-weight: 500; outline: none; max-width: 60%; }
        .new-chat-btn { background: transparent; color: var(--accent); border: 1px solid var(--accent); padding: 5px 10px; border-radius: 10px; cursor: pointer; font-size: 13px; }
        .chat { flex: 1; padding: 15px; overflow-y: auto; display: flex; flex-direction: column; gap: 15px; }
        .message { max-width: 85%; padding: 12px 16px; border-radius: 18px; line-height: 1.4; font-size: 15px; white-space: pre-wrap; }
        .msg-user { align-self: flex-end; background: var(--bg2); border-bottom-right-radius: 4px; }
        .msg-ai { align-self: flex-start; background: rgba(212, 82, 212, 0.1); border: 1px solid rgba(212, 82, 212, 0.3); border-bottom-left-radius: 4px; }
        .stats { font-size: 11px; color: #888; margin-top: 6px; text-align: right; }
        .input-area { padding: 10px; background: var(--bg2); display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--border); }
        .input-row { display: flex; gap: 10px; align-items: center; }
        .input-row input { flex: 1; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 12px; border-radius: 20px; outline: none; font-size: 15px; }
        .send-btn { background: var(--accent); color: white; border: none; width: 44px; height: 44px; border-radius: 50%; font-size: 18px; cursor: pointer; display: flex; justify-content: center; align-items: center; }
    </style>
</head>
<body>
    <div class="header">
        <select class="model-select" id="model">
            <option value="claude-opus-5-5">claude-opus-5-5</option>
            <option value="claude-sonnet-5-5" selected>claude-sonnet-5-5</option>
            <option value="claude-haiku-5-5">claude-haiku-5-5</option>
            <option value="gpt-6.1-sol">gpt-6.1-sol</option>
            <option value="gpt-6-sol">gpt-6-sol</option>
            <option value="gpt-5.6-sol">gpt-5.6-sol</option>
            <option value="gpt-5.5-pro">gpt-5.5-pro</option>
            <option value="gpt-5.5">gpt-5.5</option>
        </select>
        <button class="new-chat-btn" onclick="resetChat()">➕ Новый диалог</button>
    </div>
    <div class="chat" id="chat"></div>
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
        const chat = document.getElementById('chat');

        // Загружаем историю при открытии
        window.onload = async () => {
            try {
                const res = await fetch(`/api/history?user_id=${userId}`);
                const history = await res.json();
                if (history.length === 0) {
                    addMessage('Привет! Я твой ИИ-ассистент. Могу управлять GitHub и запускать код.', 'ai');
                } else {
                    history.forEach(msg => addMessage(msg.content, msg.role === 'user' ? 'user' : 'ai'));
                }
            } catch (e) {
                addMessage('Привет! Я твой ИИ-ассистент.', 'ai');
            }
        };

        async function resetChat() {
            await fetch(`/api/reset?user_id=${userId}`, { method: 'POST' });
            chat.innerHTML = '';
            addMessage('Новый диалог начат. Чем займёмся?', 'ai');
        }

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
    {"type": "function", "function": {"name": "list_github_files", "description": "Показать список файлов в репозитории GitHub", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "delete_github_file", "description": "Удалить файл из репозитория", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "delete_all_github_files", "description": "Удалить все файлы из репозитория", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "upload_github_file", "description": "Загрузить файл в репозиторий", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_code_piston", "description": "Запустить код", "parameters": {"type": "object", "properties": {"language": {"type": "string"}, "code": {"type": "string"}}, "required": ["language", "code"]}}}
]

async def execute_tool(tool_name, args):
    if not GH_TOKEN or not GH_USER or not GH_REPO:
        return "ОШИБКА: Не настроены GITHUB_TOKEN, GITHUB_USERNAME или GITHUB_REPO в переменных окружения."

    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    base_url = f"https://api.github.com/repos/{GH_USER}/{GH_REPO}/contents"
    
    try:
        async with httpx.AsyncClient() as client:
            if tool_name == "list_github_files":
                res = await client.get(base_url, headers=headers)
                if res.status_code == 200:
                    files = [f["name"] for f in res.json()]
                    return f"Список файлов: {', '.join(files) if files else 'Пусто'}"
                return f"ОШИБКА GITHUB API: {res.status_code} {res.text}"
            
            elif tool_name == "delete_github_file":
                path = args["path"]
                res = await client.get(f"{base_url}/{path}", headers=headers)
                if res.status_code == 200:
                    await client.delete(f"{base_url}/{path}", headers=headers, json={"message": f"Delete {path}", "sha": res.json().get("sha")})
                    return f"Файл {path} успешно удален."
                return f"Файл {path} не найден."
            
            elif tool_name == "delete_all_github_files":
                res = await client.get(base_url, headers=headers)
                if res.status_code == 200:
                    files = res.json()
                    if not files: return "Репозиторий уже пуст."
                    for f in files:
                        await client.delete(f"{base_url}/{f['name']}", headers=headers, json={"message": f"Delete {f['name']}", "sha": f['sha']})
                    return "Все файлы удалены."
                return "Ошибка получения списка."
            
            elif tool_name == "upload_github_file":
                path = args["path"]
                content_b64 = base64.b64encode(args["content"].encode()).decode()
                check = await client.get(f"{base_url}/{path}", headers=headers)
                data = {"message": f"Update {path}", "content": content_b64}
                if check.status_code == 200: data["sha"] = check.json().get("sha")
                res = await client.put(f"{base_url}/{path}", headers=headers, json=data)
                return f"Файл {path} загружен." if res.status_code in [200, 201] else "Ошибка загрузки."
            
            elif tool_name == "run_code_piston":
                res = await client.post("https://emkc.org/api/v2/piston/execute", json={"language": args["language"], "version": "*", "files": [{"content": args["code"]}]}, timeout=15)
                return f"Результат:\n{res.json().get('run', {}).get('stdout')}" if res.status_code == 200 else "Ошибка запуска."
    except Exception as e: 
        return f"Системная ошибка: {e}"

async def ask_ai(user_id, text, model=DEFAULT_MODEL):
    if user_id not in user_contexts:
        user_contexts[user_id] = [{
            "role": "system", 
            "content": "Ты продвинутый ИИ-ассистент. Ты умеешь управлять GitHub (удалять, загружать, показывать файлы) с помощью инструментов. Если пользователь просит файлы, ОБЯЗАТЕЛЬНО вызывай функцию list_github_files. Если инструмент возвращает ошибку, сообщи пользователю: 'Ошибка доступа к GitHub. Проверь настройки токена'. Никогда не груби пользователю."
        }]
    
    user_contexts[user_id].append({"role": "user", "content": text})
    start_time = time.perf_counter()
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    
    async with httpx.AsyncClient() as client:
        res = await client.post(f"{AI_BASE_URL}/chat/completions", headers=headers, json={"model": model, "messages": user_contexts[user_id], "tools": tools}, timeout=90)
        res_data = res.json()
        
        if "error" in res_data: 
            return f"Ошибка API: {res_data['error'].get('message')}", 0, 0
        
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
        answer = response_msg.get("content") or "Действие выполнено (без текста)."
        
        user_contexts[user_id].append({"role": "assistant", "content": answer})
        return answer, elapsed_time, total_tokens

# --- ТЕЛЕГРАМ БОТ ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть ИИ", web_app=WebAppInfo(url=WEB_APP_URL))]])
    await message.answer("Жми кнопку:", reply_markup=kb)

@dp.message(F.text)
async def handle_text(message: types.Message):
    msg = await message.answer("⏳ Думаю...")
    answer, time_spent, tokens = await ask_ai(message.from_user.id, message.text)
    await msg.edit_text(f"{answer}\n\n⏱ {time_spent} сек | 🪙 {tokens} токенов", parse_mode=None)

# --- API ДЛЯ MINI APP ---
@app.post("/api/chat")
async def api_chat(request: Request):
    data = await request.json()
    answer, time_spent, tokens = await ask_ai(data.get("user_id", 0), data.get("text", ""), data.get("model", DEFAULT_MODEL))
    return {"answer": answer, "time": time_spent, "tokens": tokens}

@app.get("/api/history")
async def api_history(user_id: int):
    # Отдаем только сообщения пользователя и ИИ, скрывая системный промпт и вызовы функций
    history = [msg for msg in user_contexts.get(user_id, []) if msg["role"] in ["user", "assistant"]]
    return history

@app.post("/api/reset")
async def api_reset(request: Request):
    user_id = int(request.query_params.get("user_id", 0))
    if user_id in user_contexts:
        del user_contexts[user_id]
    return {"status": "ok"}

@app.get("/")
async def serve_index():
    return HTMLResponse(content=HTML_CONTENT)

@app.get("/ping")
async def ping():
    return {"status": "ok"}

async def main():
    port = int(os.getenv("PORT", 3000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    bot_task = asyncio.create_task(dp.start_polling(bot))
    await asyncio.gather(server_task, bot_task)

if __name__ == "__main__":
    asyncio.run(main())
