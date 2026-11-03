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

WEB_APP_URL = "https://bot-1793277514-5190-savik.bothost.tech"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()
user_contexts = {}

# Читаем HTML файл, если он есть, иначе отдаем заглушку
def get_html():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Ошибка: Файл index.html не найден в репозитории!</h1>"

tools = [
    {"type": "function", "function": {"name": "list_github_files", "description": "Показать список файлов в репозитории GitHub", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "delete_github_file", "description": "Удалить файл из репозитория", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "delete_all_github_files", "description": "Удалить ВСЕ файлы из репозитория", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "upload_github_file", "description": "Загрузить или обновить файл", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_code_piston", "description": "Запустить код на Python, JS, C++ и т.д.", "parameters": {"type": "object", "properties": {"language": {"type": "string"}, "code": {"type": "string"}}, "required": ["language", "code"]}}}
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
                if res.status_code == 401: return "ОШИБКА 401: Неверный GITHUB_TOKEN."
                if res.status_code == 404: return "ОШИБКА 404: Репозиторий не найден. Проверь GITHUB_USERNAME и GITHUB_REPO."
                if res.status_code == 200:
                    files = [f["name"] for f in res.json()]
                    return f"Файлы в репозитории: {', '.join(files) if files else 'Пусто'}"
                return f"Ошибка GitHub: {res.text}"
            
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
                return f"Результат выполнения:\n{res.json().get('run', {}).get('stdout')}" if res.status_code == 200 else "Ошибка запуска."
    except Exception as e: 
        return f"Системная ошибка: {e}"

async def ask_ai(user_id, text, model=DEFAULT_MODEL):
    if user_id not in user_contexts:
        user_contexts[user_id] = [{
            "role": "system", 
            "content": "Ты продвинутый ИИ-ассистент. Ты умеешь управлять GitHub (удалять, загружать, показывать файлы) и запускать код с помощью инструментов. Если пользователь просит файлы, ОБЯЗАТЕЛЬНО вызывай функцию list_github_files. Если инструмент возвращает ошибку, сообщи её пользователю. Никогда не груби."
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

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть ИИ", web_app=WebAppInfo(url=WEB_APP_URL))]])
    await message.answer("Жми кнопку, чтобы открыть Mini App:", reply_markup=kb)

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

@app.get("/api/history")
async def api_history(user_id: int):
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
    return HTMLResponse(content=get_html())

@app.get("/ping")
async def ping():
    return {"status": "ok"}

async def main():
    port = int(os.getenv("PORT", 3000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(asyncio.create_task(server.serve()), asyncio.create_task(dp.start_polling(bot)))

if __name__ == "__main__":
    asyncio.run(main())
