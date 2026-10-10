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
from fastapi.responses import HTMLResponse, FileResponse
from fastapi import Response

# --- HTML Mini App (встроенный) ---
@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Control Panel</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
body { font-family: -apple-system, sans-serif; background: #17212b; color: #fff; padding: 12px 12px 40px; font-size: 15px; line-height: 1.4; }
#loader { text-align: center; padding: 40px 0; opacity: 0.6; }
header { background: #232e3c; padding: 12px; border-radius: 10px; margin-bottom: 14px; }
#acc-info { font-weight: 600; margin-bottom: 4px; }
#counter { font-size: 13px; opacity: 0.7; }
.screen { display: none; } .screen.active { display: block; }
button { width: 100%; padding: 12px; margin: 6px 0; background: #5288c1; color: #fff; border: none; border-radius: 10px; font-size: 15px; font-weight: 500; cursor: pointer; }
button:active { opacity: 0.7; }
button.menu-btn { text-align: left; padding: 16px; font-size: 16px; }
button.back { background: transparent; color: #6ab2f2; text-align: left; padding: 8px 0; margin-bottom: 8px; font-size: 15px; }
button.green { background: #2ea6ff; } button.red { background: #e74c3c; }
input, textarea { width: 100%; padding: 10px; margin: 4px 0; background: #232e3c; color: #fff; border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; font-size: 15px; font-family: inherit; outline: none; }
textarea { min-height: 70px; resize: vertical; }
label { display: block; margin-top: 12px; margin-bottom: 4px; font-size: 14px; opacity: 0.85; }
.row { display: flex; gap: 8px; } .row input, .row button { flex: 1; }
.list { margin-top: 8px; }
.list-item { display: flex; justify-content: space-between; align-items: center; background: #232e3c; padding: 10px 12px; border-radius: 8px; margin: 6px 0; font-size: 14px; word-break: break-all; }
.list-item .del { color: #e74c3c; cursor: pointer; padding: 0 6px; font-weight: bold; background: none; border: none; width: auto; margin: 0; }
#logs-list .list-item { font-size: 12px; font-family: monospace; }
.log-info { border-left: 3px solid #2ea6ff; } .log-warn { border-left: 3px solid #f39c12; }
.log-error { border-left: 3px solid #e74c3c; } .log-restrict { border-left: 3px solid #e74c3c; background: rgba(231,76,60,0.15); }
h3 { font-size: 18px; margin-bottom: 10px; }
</style>
</head>
<body>
<div id="loader">Загрузка...</div>
<div id="app" style="display:none">
  <header>
    <div id="acc-info">Проверка...</div>
    <div id="counter">Отправлено: 0 / 300</div>
  </header>
  <section id="screen-home" class="screen active">
    <button class="menu-btn" onclick="go('screen-bc')">📤 Рассылка</button>
    <button class="menu-btn" onclick="go('screen-aj')">🔗 Автоподписка</button>
    <button class="menu-btn" onclick="go('screen-tr')">💬 Автотриггер</button>
    <button class="menu-btn" onclick="go('screen-acc')">👤 Аккаунт</button>
    <button class="menu-btn" onclick="go('screen-logs')">📊 Логи</button>
  </section>
  <section id="screen-acc" class="screen">
    <button class="back" onclick="go('screen-home')">← Назад</button>
    <h3>Аккаунт</h3>
    <div id="acc-status">Проверка...</div>
    <div id="acc-login" style="display:none">
      <input id="phone" placeholder="+7..." type="tel">
      <button onclick="loginPhone()">Отправить код</button>
      <input id="code" placeholder="Код" style="display:none">
      <button onclick="loginCode()" id="codeBtn" style="display:none">Войти</button>
      <input id="pass2fa" placeholder="Пароль 2FA" style="display:none">
      <button onclick="login2FA()" id="pass2faBtn" style="display:none">Подтвердить</button>
    </div>
  </section>
  <section id="screen-bc" class="screen">
    <button class="back" onclick="go('screen-home')">← Назад</button>
    <h3>Рассылка</h3>
    <label>Цели (через запятую):</label>
    <textarea id="bc-targets"></textarea>
    <button onclick="bcAddTargets()">Добавить</button>
    <div id="bc-targets-list" class="list"></div>
    <label>Тексты:</label>
    <textarea id="bc-text"></textarea>
    <button onclick="bcAddText()">Добавить текст</button>
    <div id="bc-texts-list" class="list"></div>
    <label>Задержка (сек):</label>
    <div class="row"><input id="bc-dmin" type="number" placeholder="30"><input id="bc-dmax" type="number" placeholder="90"></div>
    <label>КД (сек):</label><input id="bc-cooldown" type="number">
    <label>Повторов:</label><input id="bc-repeats" type="number">
    <label>Лимит/день:</label><input id="bc-limit" type="number">
    <button onclick="bcSaveSettings()">Сохранить</button>
    <div class="row"><button class="green" onclick="bcStart()">▶ Старт</button><button class="red" onclick="bcStop()">⏸ Стоп</button></div>
    <div id="bc-status"></div>
  </section>
  <section id="screen-aj" class="screen">
    <button class="back" onclick="go('screen-home')">← Назад</button>
    <h3>Автоподписка</h3>
    <button onclick="ajToggle()" id="aj-toggle-btn">ВКЛ/ВЫКЛ</button>
    <label>Боты-админы:</label><input id="aj-admin" placeholder="@bot">
    <button onclick="ajAddAdmin()">Добавить</button>
    <div id="aj-admins-list" class="list"></div>
    <label>Лимит/день:</label><input id="aj-limit" type="number">
    <label>Задержка (сек):</label>
    <div class="row"><input id="aj-dmin" type="number"><input id="aj-dmax" type="number"></div>
    <button onclick="ajSave()">Сохранить</button>
  </section>
  <section id="screen-tr" class="screen">
    <button class="back" onclick="go('screen-home')">← Назад</button>
    <h3>Автотриггер</h3>
    <button onclick="trToggle()" id="tr-toggle-btn">ВКЛ/ВЫКЛ</button>
    <label>Слова:</label><input id="tr-word">
    <button onclick="trAddWord()">Добавить слово</button>
    <div id="tr-words-list" class="list"></div>
    <label>Ответы:</label><textarea id="tr-reply"></textarea>
    <button onclick="trAddReply()">Добавить ответ</button>
    <div id="tr-replies-list" class="list"></div>
    <label><input type="checkbox" id="tr-add-contact" checked> Добавлять в контакты</label>
    <label>Задержка (сек):</label>
    <div class="row"><input id="tr-dmin" type="number"><input id="tr-dmax" type="number"></div>
    <button onclick="trSave()">Сохранить</button>
  </section>
  <section id="screen-logs" class="screen">
    <button class="back" onclick="go('screen-home')">← Назад</button>
    <h3>Логи</h3>
    <button onclick="loadLogs()">Обновить</button>
    <div id="logs-list" class="list"></div>
  </section>
</div>
<script>
const tg = window.Telegram.WebApp; tg.ready(); tg.expand();
const INIT_DATA = tg.initData;
async function api(path, body = {}) {
  try {
    const r = await fetch("/api/" + path, { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({initData: INIT_DATA, ...body}) });
    return await r.json();
  } catch (e) { return {error: "Сеть"}; }
}
function go(id) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  document.getElementById(id).classList.add("active");
  if (id === "screen-bc") loadBroadcast();
  if (id === "screen-aj") loadAutojoin();
  if (id === "screen-tr") loadTrigger();
  if (id === "screen-logs") loadLogs();
  if (id === "screen-acc") loadAccount();
}
async function refreshStatus() {
  const s = await api("status");
  const info = document.getElementById("acc-info");
  const counter = document.getElementById("counter");
  if (s.authorized && s.me) info.innerText = `✅ @${s.me.username || s.me.first_name}`;
  else info.innerText = "⚠️ Не привязан";
  counter.innerText = `Отправлено: ${s.sent_today} / ${s.daily_limit}`;
}
async function loadAccount() {
  const s = await api("status");
  const st = document.getElementById("acc-status");
  const login = document.getElementById("acc-login");
  if (s.authorized) { st.innerText = `✅ @${s.me.username || s.me.first_name}`; login.style.display = "none"; }
  else { st.innerText = "⚠️ Не авторизован"; login.style.display = "block"; }
}
async function loginPhone() {
  const phone = document.getElementById("phone").value.trim();
  const r = await api("login/phone", { phone });
  if (r.step === "code") { document.getElementById("code").style.display = "block"; document.getElementById("codeBtn").style.display = "block"; }
  else alert(r.error || "Ошибка");
}
async function loginCode() {
  const code = document.getElementById("code").value.trim();
  const r = await api("login/code", { code });
  if (r.step === "done") { alert("✅"); loadAccount(); refreshStatus(); }
  else if (r.step === "2fa") { document.getElementById("pass2fa").style.display = "block"; document.getElementById("pass2faBtn").style.display = "block"; }
  else alert(r.error || "Ошибка");
}
async function login2FA() {
  const password = document.getElementById("pass2fa").value;
  const r = await api("login/2fa", { password });
  if (r.step === "done") { alert("✅"); loadAccount(); refreshStatus(); }
  else alert(r.error || "Ошибка");
}
async function loadBroadcast() {
  const t = await api("targets/list"); renderList("bc-targets-list", t.targets || [], "targets/del", "chat_id");
  const txt = await api("texts/list"); renderList("bc-texts-list", txt.texts || [], "texts/del", "content");
  const s = await api("broadcast/settings/get");
  document.getElementById("bc-dmin").value = s.bc_delay_min;
  document.getElementById("bc-dmax").value = s.bc_delay_max;
  document.getElementById("bc-cooldown").value = s.bc_cooldown;
  document.getElementById("bc-repeats").value = s.bc_repeats;
  document.getElementById("bc-limit").value = s.bc_daily_limit;
}
async function bcAddTargets() {
  const raw = document.getElementById("bc-targets").value;
  const items = raw.split(",").map(x => x.trim()).filter(Boolean);
  if (!items.length) return;
  await api("targets/add", { items });
  document.getElementById("bc-targets").value = ""; loadBroadcast();
}
async function bcAddText() {
  const content = document.getElementById("bc-text").value.trim();
  if (!content) return;
  await api("texts/add", { content });
  document.getElementById("bc-text").value = ""; loadBroadcast();
}
async function bcSaveSettings() {
  await api("broadcast/settings", { bc_delay_min: document.getElementById("bc-dmin").value, bc_delay_max: document.getElementById("bc-dmax").value, bc_cooldown: document.getElementById("bc-cooldown").value, bc_repeats: document.getElementById("bc-repeats").value, bc_daily_limit: document.getElementById("bc-limit").value });
  alert("✅");
}
async function bcStart() { const r = await api("broadcast/start"); document.getElementById("bc-status").innerText = r.ok ? "▶ Запущено" : (r.error || ""); }
async function bcStop() { await api("broadcast/stop"); document.getElementById("bc-status").innerText = "⏸ Стоп"; }
async function loadAutojoin() {
  const d = await api("autojoin/list");
  renderList("aj-admins-list", d.admins || [], "autojoin/admins/del", "bot_username");
  document.getElementById("aj-limit").value = d.limit;
  document.getElementById("aj-dmin").value = d.delay_min;
  document.getElementById("aj-dmax").value = d.delay_max;
  document.getElementById("aj-toggle-btn").innerText = d.enabled ? "🟢 ВКЛ" : "🔴 ВЫКЛ";
}
async function ajAddAdmin() {
  const username = document.getElementById("aj-admin").value.trim().replace("@",tr "");
  if (!username) return;
  await api-add("autojoin/admins/add", { username }-contact); document.getElementById("aj-admin").value = """).; loadAutojoin();
}
async function ajToggle()checked { await api("autojoin/toggle ="); loadAutojoin(); }
async function ajSave() {
  await api("autojoin/settings", { autojoin_limit: document.getElementById("aj-limit").value, autojoin_delay_min: document.getElementById("aj-dmin").value, autojoin_delay_max: document.getElementById("aj-dmax").value });
  alert("✅");
}
async function loadTrigger() {
  const d = await api("trigger/list");
  renderList("tr-words-list", d.words || [], "trigger/words/del", "word");
  renderList("tr-replies-list", d.replies || [], "trigger/replies/del", "content");
  document.getElementById(" d.add_contact === "1";
  document.getElementById("tr-dmin").value = d.delay_min;
  document.getElementById("tr-dmax").value = d.delay_max;
  document.getElementById("tr-toggle-btn").innerText = d.enabled ? "🟢 ВКЛ" : "🔴 ВЫКЛ";
}
async function trAddWord() { const word = document.getElementById("tr-word").value.trim(); if (!word) return; await api("trigger/words/add", { word }); document.getElementById("tr-word").value = ""; loadTrigger(); }
async function trAddReply() { const content = document.getElementById("tr-reply").value.trim(); if (!content) return; await api("trigger/replies/add", { content }); document.getElementById("tr-reply").value = ""; loadTrigger(); }
async function trToggle() { await api("trigger/toggle"); loadTrigger(); }
async function trSave() {
  await api("trigger/settings", { trigger_add_contact: document.getElementById("tr-add-contact").checked ? "1" : "0", trigger_delay_min: document.getElementById("tr-dmin").value, trigger_delay_max: document.getElementById("tr-dmax").value });
  alert("✅");
}
async function loadLogs() {
  const d = await api("logs");
  const box = document.getElementById("logs-list"); box.innerHTML = "";
  (d.logs || []).forEach(l => {
    const div = document.createElement("div");
    div.className = "list-item log-" + l.level;
    div.innerText = `${l.time} ${l.text}`;
    box.appendChild(div);
  });
}
function renderList(elId, items, delPath, keyField) {
  const box = document.getElementById(elId); box.innerHTML = "";
  items.forEach(item => {
    const div = document.createElement("div"); div.className = "list-item";
    const span = document.createElement("span"); span.innerText = item;
    const btn = document.createElement("button"); btn.className = "del"; btn.innerText = "✕";
    btn.onclick = async () => {
      await api(delPath, { [keyField]: item });
      if (elId.includes("targets")) loadBroadcast();
      if (elId.includes("texts")) loadBroadcast();
      if (elId.includes("admins")) loadAutojoin();
      if (elId.includes("words") || elId.includes("replies")) loadTrigger();
    };
    div.appendChild(span); div.appendChild(btn); box.appendChild(div);
  });
}
window.addEventListener("load", async () => {
  document.getElementById("loader").style.display = "none";
  document.getElementById("app").style.display = "block";
  await refreshStatus();
  setInterval(refreshStatus, 10000);
});
</script>
</body>
</html>"""
if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port)
