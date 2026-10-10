const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

const INIT_DATA = tg.initData;


// ==========================================================
//  API
// ==========================================================
async function api(path, body = {}) {
  try {
    const r = await fetch("/api/" + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: INIT_DATA, ...body })
    });
    return await r.json();
  } catch (e) {
    console.error("API error:", e);
    return { error: "Сеть недоступна" };
  }
}


// ==========================================================
//  НАВИГАЦИЯ
// ==========================================================
function go(id) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  document.getElementById(id).classList.add("active");
  if (id === "screen-bc") loadBroadcast();
  if (id === "screen-aj") loadAutojoin();
  if (id === "screen-tr") loadTrigger();
  if (id === "screen-logs") loadLogs();
  if (id === "screen-acc") loadAccount();
}


// ==========================================================
//  СТАТУС (главная)
// ==========================================================
async function refreshStatus() {
  const s = await api("status");
  const info = document.getElementById("acc-info");
  const counter = document.getElementById("counter");

  if (s.authorized && s.me) {
    info.innerText = `✅ @${s.me.username || s.me.first_name} (ID: ${s.me.id})`;
  } else {
    info.innerText = "⚠️ Аккаунт не привязан";
  }
  counter.innerText = `Отправлено сегодня: ${s.sent_today} / ${s.daily_limit}`;
}


// ==========================================================
//  АККАУНТ
// ==========================================================
async function loadAccount() {
  const s = await api("status");
  const st = document.getElementById("acc-status");
  const login = document.getElementById("acc-login");

  if (s.authorized) {
    st.innerText = `✅ Авторизован: @${s.me.username || s.me.first_name}`;
    login.style.display = "none";
  } else {
    st.innerText = "⚠️ Не авторизован";
    login.style.display = "block";
  }
}

async function loginPhone() {
  const phone = document.getElementById("phone").value.trim();
  if (!phone) return alert("Введи номер");
  const r = await api("login/phone", { phone });
  if (r.step === "code") {
    document.getElementById("code").style.display = "block";
    document.getElementById("codeBtn").style.display = "block";
  } else {
    alert(r.error || "Ошибка");
  }
}

async function loginCode() {
  const code = document.getElementById("code").value.trim();
  const r = await api("login/code", { code });
  if (r.step === "done") {
    alert("✅ Аккаунт привязан");
    loadAccount();
    refreshStatus();
  } else if (r.step === "2fa") {
    document.getElementById("pass2fa").style.display = "block";
    document.getElementById("pass2faBtn").style.display = "block";
  } else {
    alert(r.error || "Ошибка");
  }
}

async function login2FA() {
  const password = document.getElementById("pass2fa").value;
  const r = await api("login/2fa", { password });
  if (r.step === "done") {
    alert("✅ Аккаунт привязан");
    loadAccount();
    refreshStatus();
  } else {
    alert(r.error || "Ошибка");
  }
}


// ==========================================================
//  РАССЫЛКА
// ==========================================================
async function loadBroadcast() {
  const t = await api("targets/list");
  renderList("bc-targets-list", t.targets || [], "targets/del", "chat_id");

  const txt = await api("texts/list");
  renderList("bc-texts-list", txt.texts || [], "texts/del", "content");

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
  document.getElementById("bc-targets").value = "";
  loadBroadcast();
}

async function bcAddText() {
  const content = document.getElementById("bc-text").value.trim();
  if (!content) return;
  await api("texts/add", { content });
  document.getElementById("bc-text").value = "";
  loadBroadcast();
}

async function bcSaveSettings() {
  await api("broadcast/settings", {
    bc_delay_min: document.getElementById("bc-dmin").value,
    bc_delay_max: document.getElementById("bc-dmax").value,
    bc_cooldown: document.getElementById("bc-cooldown").value,
    bc_repeats: document.getElementById("bc-repeats").value,
    bc_daily_limit: document.getElementById("bc-limit").value
  });
  alert("✅ Сохранено");
}

async function bcStart() {
  const r = await api("broadcast/start");
  document.getElementById("bc-status").innerText = r.ok ? "▶ Запущено" : (r.error || "");
}

async function bcStop() {
  await api("broadcast/stop");
  document.getElementById("bc-status").innerText = "⏸ Остановлено";
}


// ==========================================================
//  АВТОПОДПИСКА
// ==========================================================
async function loadAutojoin() {
  const d = await api("autojoin/list");
  renderList("aj-admins-list", d.admins || [], "autojoin/admins/del", "bot_username");
  document.getElementById("aj-limit").value = d.limit;
  document.getElementById("aj-dmin").value = d.delay_min;
  document.getElementById("aj-dmax").value = d.delay_max;
  document.getElementById("aj-toggle-btn").innerText = d.enabled ? "🟢 ВКЛ" : "🔴 ВЫКЛ";
}

async function ajAddAdmin() {
  const username = document.getElementById("aj-admin").value.trim().replace("@", "");
  if (!username) return;
  await api("autojoin/admins/add", { username });
  document.getElementById("aj-admin").value = "";
  loadAutojoin();
}

async function ajToggle() {
  await api("autojoin/toggle");
  loadAutojoin();
}

async function ajSave() {
  await api("autojoin/settings", {
    autojoin_limit: document.getElementById("aj-limit").value,
    autojoin_delay_min: document.getElementById("aj-dmin").value,
    autojoin_delay_max: document.getElementById("aj-dmax").value
  });
  alert("✅ Сохранено");
}


// ==========================================================
//  АВТОТРИГГЕР
// ==========================================================
async function loadTrigger() {
  const d = await api("trigger/list");
  renderList("tr-words-list", d.words || [], "trigger/words/del", "word");
  renderList("tr-replies-list", d.replies || [], "trigger/replies/del", "content");
  document.getElementById("tr-add-contact").checked = d.add_contact === "1";
  document.getElementById("tr-dmin").value = d.delay_min;
  document.getElementById("tr-dmax").value = d.delay_max;
  document.getElementById("tr-toggle-btn").innerText = d.enabled ? "🟢 ВКЛ" : "🔴 ВЫКЛ";
}

async function trAddWord() {
  const word = document.getElementById("tr-word").value.trim();
  if (!word) return;
  await api("trigger/words/add", { word });
  document.getElementById("tr-word").value = "";
  loadTrigger();
}

async function trAddReply() {
  const content = document.getElementById("tr-reply").value.trim();
  if (!content) return;
  await api("trigger/replies/add", { content });
  document.getElementById("tr-reply").value = "";
  loadTrigger();
}

async function trToggle() {
  await api("trigger/toggle");
  loadTrigger();
}

async function trSave() {
  await api("trigger/settings", {
    trigger_add_contact: document.getElementById("tr-add-contact").checked ? "1" : "0",
    trigger_delay_min: document.getElementById("tr-dmin").value,
    trigger_delay_max: document.getElementById("tr-dmax").value
  });
  alert("✅ Сохранено");
}


// ==========================================================
//  ЛОГИ
// ==========================================================
async function loadLogs() {
  const d = await api("logs");
  const box = document.getElementById("logs-list");
  box.innerHTML = "";
  (d.logs || []).forEach(l => {
    const div = document.createElement("div");
    div.className = "list-item log-" + l.level;
    div.innerText = `${l.time} ${l.text}`;
    box.appendChild(div);
  });
}


// ==========================================================
//  УТИЛИТА: рендер списка
// ==========================================================
function renderList(elId, items, delPath, keyField) {
  const box = document.getElementById(elId);
  box.innerHTML = "";
  items.forEach(item => {
    const div = document.createElement("div");
    div.className = "list-item";
    const span = document.createElement("span");
    span.innerText = item;
    const btn = document.createElement("button");
    btn.className = "del";
    btn.innerText = "✕";
    btn.onclick = async () => {
      await api(delPath, { [keyField]: item });
      if (elId === "bc-targets-list") loadBroadcast();
      if (elId === "bc-texts-list") loadBroadcast();
      if (elId === "aj-admins-list") loadAutojoin();
      if (elId === "tr-words-list") loadTrigger();
      if (elId === "tr-replies-list") loadTrigger();
    };
    div.appendChild(span);
    div.appendChild(btn);
    box.appendChild(div);
  });
}


// ==========================================================
//  СТАРТ
// ==========================================================
window.addEventListener("load", async () => {
  document.getElementById("loader").style.display = "none";
  document.getElementById("app").style.display = "block";
  await refreshStatus();
  setInterval(refreshStatus, 10000); // обновление каждые 10 сек
});
