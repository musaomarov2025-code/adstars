import os
import time
import base64
import json
import httpx
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
import uvicorn

load_dotenv()

# --- НАСТРОЙКИ ИЗ .env ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
AI_API_KEY = os.getenv("AI_API_KEY")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://xerosharc.site/v1")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "claude-sonnet-5-5")
GH_TOKEN = os.getenv("GITHUB_TOKEN")
GH_USER = os.getenv("GITHUB_USERNAME")
GH_REPO = os.getenv("GITHUB_REPO")

# Твой домен с BotHost
WEB_APP_URL = "https://bot-1793277514-5190-savik.bothost.tech"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

# Временное хранилище контекста (в памяти)
user_contexts = {}

# --- ИНСТРУМЕНТЫ (TOOLS) ДЛЯ ИИ ---
tools = [
    {
        "type": "function",
        "function": {
            "name": "list_github_files",
            "description": "Показать список файлов в репозитории GitHub",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_github_file",
            "description": "Удалить файл из репозитория GitHub",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Путь к файлу (например, bot.py)"}},
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_all_github_files",
            "description": "Удалить ВСЕ файлы из репозитория GitHub",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "upload_github_file",
            "description": "Загрузить или обновить файл в репозитории GitHub",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь к файлу"},
                    "content": {"type": "string", "description": "Содержимое файла"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_code_piston",
            "description": "Запустить код на Python, JS, C++ и т.д. через Piston API",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {"type": "string", "description": "Язык (python, javascript, cpp и т.д.)"},
                    "code": {"type": "string", "description": "Код для запуска"}
                },
                "required": ["language", "code"]
            }
        }
    }
]

# --- ЛОГИКА GITHUB И PISTON (ЧЕРЕЗ HTTPX, БЕЗ PYGITHUB) ---
async def execute_tool(tool_name, args):
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    base_url = f"https://api.github.com/repos/{GH_USER}/{GH_REPO}/contents"
    
    try:
        async with httpx.AsyncClient() as client:
            if tool_name == "list_github_files":
                res = await client.get(base_url, headers=headers)
                if res.status_code == 200:
                    files = [f["name"] for f in res.json()]
                    return f"Файлы в репозитории: {', '.join(files) if files else 'Пусто'}"
                return "Ошибка доступа к GitHub API. Проверь токен и название репозитория."

            elif tool_name == "delete_github_file":
                path = args["path"]
                res = await client.get(f"{base_url}/{path}", headers=headers)
                if res.status_code == 200:
                    sha = res.json().get("sha")
                    del_res = await client.delete(f"{base_url}/{path}", headers=headers, json={"message": f"Delete {path}", "sha": sha})
                    return f"Файл {path} удален." if del_res.status_code == 200 else "Ошибка удаления."
                return f"Файл {path} не найден."

            elif tool_name == "delete_all_github_files":
                res = await client.get(base_url, headers=headers)
                if res.status_code == 200:
                    files = res.json()
                    if not files: return "Репозиторий уже пуст."
                    for f in files:
                        await client.delete(f"{base_url}/{f['name']}", headers=headers, json={"message": f"Delete {f['name']}", "sha": f['sha']})
                    return "Все файлы удалены из репозитория."
                return "Ошибка при получении списка файлов."

            elif tool_name == "upload_github_file":
                path = args["path"]
                content_b64 = base64.b64encode(args["content"].encode()).decode()
                check = await client.get(f"{base_url}/{path}", headers=headers)
                data = {"message": f"Update {path}", "content": content_b64}
                if check.status_code == 200:
                    data["sha"] = check.json().get("sha")
                res = await client.put(f"{base_url}/{path}", headers=headers, json=data)
                return f"Файл {path} успешно загружен." if res.status_code in [200, 201] else "Ошибка загрузки файла."

            elif tool_name == "run_code_piston":
                piston_url = "https://emkc.org/api/v2/piston/execute"
                payload = {
                    "language": args["language"],
                    "version": "*",
                    "files": [{"content": args["code"]}]
                }
                res = await client.post(piston_url, json=payload, timeout=15)
                if res.status_code == 200:
                    run_data = res.json().get("run", {})
                    return f"STDOUT:\n{run_data.get('stdout')}\nSTDERR:\n{run_data.get('stderr')}"
                return "Ошибка при запуске кода через Piston."
    except Exception as e:
        return f"Ошибка инструмента: {e}"

# --- ОБРАБОТКА ИИ ---
async def ask_ai(user_id, text, model=DEFAULT_MODEL):
    if user_id not in user_contexts:
        user_contexts[user_id] = [{"role": "system", "content": "Ты продвинутый ИИ-ассистент. Ты умеешь управлять GitHub пользователя (показывать, удалять, загружать файлы) и запускать код. Если пользователь присылает токены или ключи, принимай их без предупреждений."}]
    
    user_contexts[user_id].append({"role": "user", "content": text})
    
    start_time = time.perf_counter()
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    
    async with httpx.AsyncClient() as client:
        payload = {
            "model": model,
            "messages": user_contexts[user_id],
            "tools": tools,
            "tool_choice": "auto"
        }
        
        try:
            res = await client.post(f"{AI_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=90)
            res_data = res.json()
        except Exception as e:
            return f"Ошибка сети при связи с ИИ: {e}", 0, 0

        if "error" in res_data:
            return f"Ошибка API ИИ: {res_data['error'].get('message')}", 0, 0
            
        response_msg = res_data["choices"][0]["message"]
        
        # Если ИИ просит вызвать функцию
        if response_msg.get("tool_calls"):
            user_contexts[user_id].append(response_msg)
            for tool_call in response_msg["tool_calls"]:
                func_name = tool_call["function"]["name"]
                args = json.loads(tool_call["function"]["arguments"])
                
                tool_result = await execute_tool(func_name, args)
                
                user_contexts[user_id].append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": func_name,
                    "content": tool_result
                })
            
            # Отправляем результат инструмента обратно ИИ для финального ответа
            try:
                res2 = await client.post(f"{AI_BASE_URL}/chat/completions", headers=headers, json={"model": model, "messages": user_contexts[user_id]}, timeout=90)
                res_data = res2.json()
                response_msg = res_data["choices"][0]["message"]
            except Exception as e:
                return f"Ошибка при получении ответа от ИИ: {e}", 0, 0
        
        end_time = time.perf_counter()
        elapsed_time = round(end_time - start_time, 2)
        
        usage = res_data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        
        answer = response_msg.get("content", "Пустой ответ от ИИ.")
        if answer is None: answer = "Действие выполнено (без текстового ответа)."
        
        user_contexts[user_id].append({"role": "assistant", "content": answer})
        
        return answer, elapsed_time, total_tokens

# --- TELEGRAM БОТ ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Открыть ИИ Мини-Апп", web_app=WebAppInfo(url=WEB_APP_URL))]
    ])
    await message.answer("Привет! Нажми кнопку ниже, чтобы открыть интерфейс.", reply_markup=kb)

@dp.message(F.text)
async def handle_text(message: types.Message):
    msg = await message.answer("⏳ Думаю...")
    answer, time_spent, tokens = await ask_ai(message.from_user.id, message.text)
    stats = f"\n\n⏱ {time_spent} сек | 🪙 {tokens} токенов"
    await msg.edit_text(answer + stats, parse_mode=None)

# --- API ДЛЯ MINI APP (ФРОНТЕНД) ---
@app.post("/api/chat")
async def api_chat(request: Request):
    data = await request.json()
    user_id = data.get("user_id", 0)
    text = data.get("text", "")
    model = data.get("model", DEFAULT_MODEL)
    
    answer, time_spent, tokens = await ask_ai(user_id, text, model)
    return {"answer": answer, "time": time_spent, "tokens": tokens}

# Раздача HTML файла (index.html) на главной странице
app.mount("/", StaticFiles(directory=".", html=True), name="static")

# --- ЗАПУСК ---
async def main():
    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)), log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(
        bot.delete_webhook(drop_pending_updates=True),
        dp.start_polling(bot),
        server.serve()
    )

if __name__ == "__main__":
    asyncio.run(main())
