import asyncio
import hmac
import hashlib
from urllib.parse import parse_qsl
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from config import BOT_TOKEN, WEBAPP_URL
import database as db
import telethon_client as tc
from bot import webhook_handler, setup_webhook, remove_webhook


# ==========================================================
#  ПРОВЕРКА initData
# ==========================================================
def check_init_data(init_data: str) -> dict | None:
    if not init_data:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

        if calc != received_hash:
            return None
        return parsed
    except Exception:
        return None


async def auth(request: Request):
    body = await request.json()
    user = check_init_data(body.get("initData", ""))
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")
    return user


# ==========================================================
#  LIFESPAN
# ==========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    await tc.client.connect()
    if await tc.client.is_user_authorized():
        print("✅ Telethon: авторизован")
    else:
        print("⚠️ Telethon: НЕ авторизован")

    # Установка webhook бота
    if WEBAPP_URL:
        try:
            await setup_webhook(WEBAPP_URL)
        except Exception as e:
            print(f"Webhook setup error: {e}")

    yield

    await remove_webhook()
    await tc.client.disconnect()


app = FastAPI(lifespan=lifespan)


# ==========================================================
#  WEBHOOK (Telegram)
# ==========================================================
app.post("/webhook")(webhook_handler)


# ==========================================================
#  СТАТУС
# ==========================================================
@app.post("/api/status")
async def api_status(request: Request):
    await auth(request)
    authorized = False
    if tc.client.is_connected():
        authorized = await tc.client.is_user_authorized()

    me = None
    if authorized:
        try:
            u = await tc.client.get_me()
            me = {"id": u.id, "username": u.username, "first_name": u.first_name}
        except Exception:
            pass

    return {
        "connected": tc.client.is_connected(),
        "authorized": authorized,
        "me": me,
        "sent_today": db.get_sent_today(),
        "daily_limit": int(db.get("bc_daily_limit", 300)),
        "broadcast_running": tc.broadcast_running,
        "autojoin_running": tc.autojoin_running,
        "autotrigger_running": tc.autotrigger_running,
    }


# ==========================================================
#  АВТОРИЗАЦИЯ АККАУНТА
# ==========================================================
@app.post("/api/login/phone")
async def login_phone(request: Request):
    await auth(request)
    body = await request.json()
    phone = body.get("phone", "").strip()
    if not phone:
        return {"error": "Укажи номер"}

    if not tc.client.is_connected():
        await tc.client.connect()

    try:
        await tc.client.send_code_request(phone)
        db.set("login_phone", phone)
        return {"step": "code"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/login/code")
async def login_code(request: Request):
    await auth(request)
    body = await request.json()
    code = body.get("code", "").strip()
    phone = db.get("login_phone")
    if not phone or not code:
        return {"error": "Нет номера или кода"}

    try:
        await tc.client.sign_in(phone, code)
        return {"step": "done"}
    except Exception as e:
        if "password" in str(e).lower():
            return {"step": "2fa"}
        return {"error": str(e)}


@app.post("/api/login/2fa")
async def login_2fa(request: Request):
    await auth(request)
    body = await request.json()
    password = body.get("password", "")
    try:
        await tc.client.sign_in(password=password)
        return {"step": "done"}
    except Exception as e:
        return {"error": str(e)}


# ==========================================================
#  РАССЫЛКА
# ==========================================================
@app.post("/api/broadcast/start")
async def bc_start(request: Request):
    await auth(request)
    if tc.broadcast_running:
        return {"error": "Уже запущено"}
    asyncio.create_task(tc.run_broadcast())
    return {"ok": True}


@app.post("/api/broadcast/stop")
async def bc_stop(request: Request):
    await auth(request)
    tc.stop_broadcast()
    return {"ok": True}


@app.post("/api/broadcast/settings")
async def bc_settings(request: Request):
    await auth(request)
    body = await request.json()
    for key in ["bc_delay_min", "bc_delay_max", "bc_cooldown",
                "bc_repeats", "bc_daily_limit"]:
        if key in body:
            db.set(key, body[key])
    return {"ok": True}


@app.post("/api/broadcast/settings/get")
async def bc_settings_get(request: Request):
    await auth(request)
    return {
        "bc_delay_min": db.get("bc_delay_min", 30),
        "bc_delay_max": db.get("bc_delay_max", 90),
        "bc_cooldown": db.get("bc_cooldown", 60),
        "bc_repeats": db.get("bc_repeats", 1),
        "bc_daily_limit": db.get("bc_daily_limit", 300),
    }


# ==========================================================
#  ЦЕЛИ
# ==========================================================
@app.post("/api/targets/add")
async def targets_add(request: Request):
    await auth(request)
    body = await request.json()
    for item in body.get("items", []):
        db.add_target(item)
    return {"ok": True, "targets": db.all_targets()}


@app.post("/api/targets/del")
async def targets_del(request: Request):
    await auth(request)
    body = await request.json()
    db.del_target(body.get("chat_id"))
    return {"ok": True, "targets": db.all_targets()}


@app.post("/api/targets/list")
async def targets_list(request: Request):
    await auth(request)
    return {"targets": db.all_targets()}


# ==========================================================
#  ТЕКСТЫ
# ==========================================================
@app.post("/api/texts/add")
async def texts_add(request: Request):
    await auth(request)
    body = await request.json()
    db.add_text(body.get("content", "").strip())
    return {"ok": True, "texts": db.all_texts()}


@app.post("/api/texts/del")
async def texts_del(request: Request):
    await auth(request)
    body = await request.json()
    db.del_text(body.get("id"))
    return {"ok": True, "texts": db.all_texts()}


@app.post("/api/texts/list")
async def texts_list(request: Request):
    await auth(request)
    return {"texts": db.all_texts()}


# ==========================================================
#  АВТОПОДПИСКА
# ==========================================================
@app.post("/api/autojoin/admins/add")
async def aj_add(request: Request):
    await auth(request)
    body = await request.json()
    db.add_admin(body.get("username", "").strip().lstrip("@"))
    return {"ok": True, "admins": db.all_admins()}


@app.post("/api/autojoin/admins/del")
async def aj_del(request: Request):
    await auth(request)
    body = await request.json()
    db.del_admin(body.get("id"))
    return {"ok": True, "admins": db.all_admins()}


@app.post("/api/autojoin/list")
async def aj_list(request: Request):
    await auth(request)
    return {
        "admins": db.all_admins(),
        "enabled": tc.autojoin_running,
        "limit": db.get("autojoin_limit", 5),
        "delay_min": db.get("autojoin_delay_min", 30),
        "delay_max": db.get("autojoin_delay_max", 90),
    }


@app.post("/api/autojoin/toggle")
async def aj_toggle(request: Request):
    await auth(request)
    tc.autojoin_running = not tc.autojoin_running
    return {"enabled": tc.autojoin_running}


@app.post("/api/autojoin/settings")
async def aj_settings(request: Request):
    await auth(request)
    body = await request.json()
    for key in ["autojoin_limit", "autojoin_delay_min", "autojoin_delay_max"]:
        if key in body:
            db.set(key, body[key])
    return {"ok": True}


# ==========================================================
#  АВТОТРИГГЕР
# ==========================================================
@app.post("/api/trigger/words/add")
async def tr_add(request: Request):
    await auth(request)
    body = await request.json()
    db.add_trigger(body.get("word", "").strip())
    return {"ok": True, "words": db.all_triggers()}


@app.post("/api/trigger/words/del")
async def tr_del(request: Request):
    await auth(request)
    body = await request.json()
    db.del_trigger(body.get("id"))
    return {"ok": True, "words": db.all_triggers()}


@app.post("/api/trigger/replies/add")
async def tr_reply_add(request: Request):
    await auth(request)
    body = await request.json()
    db.add_reply(body.get("content", "").strip())
    return {"ok": True, "replies": db.all_replies()}


@app.post("/api/trigger/replies/del")
async def tr_reply_del(request: Request):
    await auth(request)
    body = await request.json()
    db.del_reply(body.get("id"))
    return {"ok": True, "replies": db.all_replies()}


@app.post("/api/trigger/list")
async def tr_list(request: Request):
    await auth(request)
    return {
        "words": db.all_triggers(),
        "replies": db.all_replies(),
        "enabled": tc.autotrigger_running,
        "add_contact": db.get("trigger_add_contact", "1"),
        "delay_min": db.get("trigger_delay_min", 5),
        "delay_max": db.get("trigger_delay_max", 30),
    }


@app.post("/api/trigger/toggle")
async def tr_toggle(request: Request):
    await auth(request)
    tc.autotrigger_running = not tc.autotrigger_running
    return {"enabled": tc.autotrigger_running}


@app.post("/api/trigger/settings")
async def tr_settings(request: Request):
    await auth(request)
    body = await request.json()
    for key in ["trigger_add_contact", "trigger_delay_min", "trigger_delay_max"]:
        if key in body:
            db.set(key, body[key])
    return {"ok": True}


# ==========================================================
#  ЛОГИ
# ==========================================================
@app.post("/api/logs")
async def logs(request: Request):
    await auth(request)
    return {"logs": db.get_logs(50)}


# ==========================================================
#  СТАТИКА
# ==========================================================
app.mount("/", StaticFiles(directory="static", html=True), name="static")
