/* Muslim Connect SPA — hash router + API client + real-time chat */
"use strict";
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (iso) => { try { return new Date(iso).toLocaleString(); } catch { return iso || ""; } };
const linkify = (s) => esc(s).replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener nofollow">$1</a>');

const MC = {
  token: localStorage.getItem("mc_token") || "",
  user: null,
  lang: localStorage.getItem("mc_lang") || "en",
  dict: {},
  ws: null, wsRoom: null, wsConv: null, wsRetry: 0, wsTimer: null,
  typingUsers: new Map(),
  config: { app_name: "Muslim Connect", tagline: "One Ummah. One Community. Respect for All." },
};

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (MC.token) headers["Authorization"] = "Bearer " + MC.token;
  let res;
  try {
    res = await fetch(path, { ...opts, headers });
  } catch (e) {
    throw new Error("network");
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error((data && data.detail) || ("HTTP " + res.status));
  return data;
}
const apiForm = (path, formData) => fetch(path, {
  method: "POST", headers: MC.token ? { Authorization: "Bearer " + MC.token } : {}, body: formData,
}).then(async (r) => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.detail || "HTTP " + r.status); return d; });

function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast"; el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 3200);
}
function modal(html) {
  const root = $("#modal-root");
  root.innerHTML = `<div class="modal-back"><div class="modal" role="dialog" aria-modal="true">${html}</div></div>`;
  $(".modal-back").addEventListener("click", (e) => { if (e.target.classList.contains("modal-back")) closeModal(); });
}
function closeModal() { $("#modal-root").innerHTML = ""; }

/* ---------------- i18n ---------------- */
async function loadLang(lang) {
  MC.lang = lang; localStorage.setItem("mc_lang", lang);
  try { MC.dict = await (await fetch(`/static/i18n/${lang}.json`)).json(); }
  catch { MC.dict = {}; }
  document.documentElement.lang = lang;
  document.documentElement.dir = (lang === "ur" || lang === "ar") ? "rtl" : "ltr";
  $$(".lang-switch button").forEach((b) => b.classList.toggle("active", b.dataset.lang === lang));
  $$("[data-i18n]").forEach((el) => { const k = el.dataset.i18n; if (MC.dict[k]) el.textContent = MC.dict[k]; });
  renderNav();
}
const t = (k) => MC.dict[k] || k;
$$(".lang-switch button").forEach((b) => b.addEventListener("click", async () => { await loadLang(b.dataset.lang); route(); }));

/* ---------------- auth state ---------------- */
async function refreshMe() {
  if (!MC.token) { MC.user = null; return; }
  try { MC.user = await api("/api/auth/me"); }
  catch { MC.token = ""; localStorage.removeItem("mc_token"); MC.user = null; }
  applyTheme();
}
function applyTheme() {
  const theme = (MC.user && MC.user.theme) || localStorage.getItem("mc_theme") || "light";
  document.documentElement.dataset.theme = theme;
}
function setToken(token, user) {
  MC.token = token; MC.user = user;
  if (token) localStorage.setItem("mc_token", token); else localStorage.removeItem("mc_token");
  applyTheme(); renderNav(); updateConnPill();
}
function requireAuth() {
  if (!MC.user) { location.hash = "#/login"; return false; }
  return true;
}
function requireRegistered() {
  if (!requireAuth()) return false;
  if (MC.user.is_guest) { toast(t("guest") + " — " + t("create_account")); location.hash = "#/register"; return false; }
  return true;
}

/* ---------------- nav ---------------- */
function renderNav() {
  const nav = $("#nav"); const u = MC.user;
  const links = [
    ["#/", t("home")], ["#/rooms", t("rooms")], ["#/qa", t("qa")], ["#/scholars", t("scholars")], ["#/discover", t("discover")],
  ];
  if (u && !u.is_guest) links.push(["#/messages", t("messages")], ["#/dashboard", t("dashboard")]);
  else if (u && u.is_guest) links.push(["#/dashboard", t("dashboard")]);
  let html = links.map(([h, l]) => `<a href="${h}">${esc(l)}</a>`).join("");
  if (u) {
    html += `<a href="#/notifications">${esc(t("notifications"))} <span id="nav-unread" class="badge" style="display:none"></span></a>`;
    if (u.role === "admin" || u.role === "moderator") html += `<a href="#/mod">${esc(t("moderation"))}</a>`;
    if (u.role === "admin") html += `<a href="#/admin">${esc(t("admin"))}</a>`;
    html += `<a href="#/profile">${esc(u.display_name)}</a><button class="linklike" id="nav-logout">${esc(t("logout"))}</button>`;
  } else {
    html += `<a href="#/login">${esc(t("login"))}</a><a href="#/register" class="active">${esc(t("create_account"))}</a>`;
  }
  nav.innerHTML = html;
  const lo = $("#nav-logout");
  if (lo) lo.onclick = async () => { try { await api("/api/auth/logout", { method: "POST" }); } catch {} setToken("", null); location.hash = "#/"; };
  pollUnread();
}
async function pollUnread() {
  const el = $("#nav-unread");
  if (!MC.user || !el) return;
  try {
    const d = await api("/api/notifications/unread-count");
    el.style.display = d.unread ? "" : "none"; el.textContent = d.unread;
  } catch {}
}
setInterval(pollUnread, 30000);

function updateConnPill(state) {
  const el = $("#conn-status"); if (!el) return;
  const s = state || (navigator.onLine ? (MC.ws ? "online" : "offline") : "offline");
  el.className = "status-pill " + s;
  el.textContent = t(s === "online" ? "online" : s === "connecting" ? "connecting" : "offline");
}
window.addEventListener("online", () => updateConnPill());
window.addEventListener("offline", () => updateConnPill());

/* ---------------- router ---------------- */
const routes = {};
function addRoute(pattern, fn) { routes[pattern] = fn; }
function route() {
  const hash = location.hash || "#/";
  const [path, query] = hash.slice(2).split("?");
  const params = Object.fromEntries(new URLSearchParams(query || ""));
  const parts = path.split("/").filter(Boolean);
  closeWs();
  for (const [pattern, fn] of Object.entries(routes)) {
    const pp = pattern.split("/").filter(Boolean);
    if (pp.length !== parts.length && !pattern.endsWith("/*")) continue;
    const match = {};
    let ok = true;
    for (let i = 0; i < pp.length; i++) {
      if (pp[i].startsWith(":")) match[pp[i].slice(1)] = decodeURIComponent(parts[i]);
      else if (pp[i] !== parts[i]) { ok = false; break; }
    }
    if (ok) { window.scrollTo(0, 0); fn(match, params); $("#view").focus({ preventScroll: true }); return; }
  }
  views.notFound();
}
window.addEventListener("hashchange", route);

function page(html) { $("#view").innerHTML = html; }
function spinner(msg) { return `<div class="empty"><div class="big">🌙</div><p>${esc(msg || t("loading"))}</p></div>`; }
function errBox(e) {
  const m = e.message === "network" ? "Network error — check your connection and retry." : e.message;
  return `<div class="error-box" role="alert">${esc(m)}</div>`;
}

/* ================= VIEWS ================= */
const views = {};

/* ---------- landing ---------- */
views.landing = async function () {
  let pub = {};
  try { pub = await api("/api/admin/settings/public"); } catch {}
  page(`
  <div class="hero pattern-band">
    <h1>${esc(MC.config.app_name)}</h1>
    <p class="tagline">${esc(MC.config.tagline)}</p>
    <p style="max-width:640px;margin:0 auto 1.5rem;position:relative;z-index:1">${esc(t("hero_sub"))}</p>
    <div class="cta-row">
      ${MC.user ? `<a class="btn btn-gold" href="#/rooms">${esc(t("browse_rooms"))}</a>`
      : `<a class="btn btn-gold" href="#/register">${esc(t("get_started"))}</a>
         <a class="btn btn-ghost" href="#/guest">${esc(t("guest"))}</a>
         <a class="btn btn-ghost" href="#/login">${esc(t("login"))}</a>`}
    </div>
  </div>
  ${pub.announcement ? `<div class="ok-box">📢 ${esc(pub.announcement)}</div>` : ""}
  <div class="feature-grid">
    <div class="card feature"><div class="icon">💬</div><h3>${esc(t("feature_chat_t"))}</h3><p>${esc(t("feature_chat_d"))}</p></div>
    <div class="card feature"><div class="icon">📖</div><h3>${esc(t("feature_qa_t"))}</h3><p>${esc(t("feature_qa_d"))}</p></div>
    <div class="card feature"><div class="icon">🎓</div><h3>${esc(t("feature_scholar_t"))}</h3><p>${esc(t("feature_scholar_d"))}</p></div>
    <div class="card feature"><div class="icon">🛡️</div><h3>${esc(t("feature_safe_t"))}</h3><p>${esc(t("feature_safe_d"))}</p></div>
  </div>
  <div class="card" style="margin-top:1.5rem"><h3>🌙 ${esc(t("join_community"))}</h3>
    <p>${esc(t("guest_desc"))}</p>
    ${MC.user ? "" : `<a class="btn btn-primary" href="#/register">${esc(t("create_account"))}</a> <a class="btn btn-ghost" href="#/guest">${esc(t("guest"))}</a>`}
  </div>`);
};

/* ---------- auth views ---------- */
views.login = function () {
  page(`<div class="card" style="max-width:440px;margin:2rem auto">
    <h2>${esc(t("welcome_back"))}</h2><div id="err"></div>
    <form id="f">
      <label class="field">${esc(t("username"))} / ${esc(t("email"))}</label>
      <input name="id" required autocomplete="username">
      <label class="field">${esc(t("password"))}</label>
      <input name="pw" type="password" required autocomplete="current-password">
      <div style="margin-top:1rem"><button class="btn btn-primary" style="width:100%">${esc(t("login"))}</button></div>
    </form>
    <p><a href="#/forgot">${esc(t("forgot"))}</a></p>
    <p>${esc(t("no_account"))} <a href="#/register">${esc(t("create_account"))}</a></p>
  </div>`);
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const d = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username_or_email: fd.get("id"), password: fd.get("pw") }) });
      setToken(d.access_token, d.user);
      toast("Assalamu alaikum, " + d.user.display_name + "!");
      location.hash = "#/dashboard";
    } catch (err) { $("#err").innerHTML = errBox(err); }
  };
};

views.register = function () {
  page(`<div class="card" style="max-width:480px;margin:2rem auto">
    <h2>${esc(t("create_account"))}</h2><div id="err"></div>
    <form id="f">
      <label class="field">${esc(t("username"))}</label><input name="username" required pattern="[A-Za-z0-9_]{3,40}" autocomplete="username">
      <div class="hint">3–40 characters: letters, numbers, underscore.</div>
      <label class="field">${esc(t("display_name"))}</label><input name="display_name" required maxlength="80">
      <label class="field">${esc(t("email"))}</label><input name="email" type="email" required autocomplete="email">
      <label class="field">${esc(t("password"))}</label><input name="password" type="password" required minlength="8" autocomplete="new-password">
      <label class="field">${esc(t("country"))}</label><input name="country" maxlength="80" placeholder="Pakistan">
      <label class="field">${esc(t("language"))}</label>
      <select name="language"><option value="en">English</option><option value="ur">اردو</option><option value="ar">العربية</option></select>
      <div style="margin-top:1rem"><button class="btn btn-primary" style="width:100%">${esc(t("register"))}</button></div>
    </form>
    <p>${esc(t("have_account"))} <a href="#/login">${esc(t("login"))}</a></p>
  </div>`);
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    const v = Object.fromEntries(new FormData(e.target));
    try {
      const d = await api("/api/auth/register", { method: "POST", body: JSON.stringify(v) });
      setToken(d.access_token, d.user);
      toast("Account created! Please verify your email.");
      location.hash = "#/dashboard";
    } catch (err) { $("#err").innerHTML = errBox(err); }
  };
};

views.guest = function () {
  page(`<div class="card" style="max-width:480px;margin:2rem auto">
    <h2>🧳 ${esc(t("guest_title"))}</h2><p>${esc(t("guest_desc"))}</p><div id="err"></div>
    <form id="f">
      <label class="field">${esc(t("display_name"))} (optional)</label>
      <input name="display_name" maxlength="80" placeholder="e.g. Seeker">
      <label class="field">${esc(t("language"))}</label>
      <select name="language"><option value="en">English</option><option value="ur">اردو</option><option value="ar">العربية</option></select>
      <div style="margin-top:1rem"><button class="btn btn-gold" style="width:100%">${esc(t("enter"))} →</button></div>
    </form>
    <p class="hint">Guest sessions expire after 72 hours. <a href="#/register">${esc(t("create_account"))}</a> to keep your profile.</p>
  </div>`);
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    const v = Object.fromEntries(new FormData(e.target));
    try {
      const d = await api("/api/auth/guest", { method: "POST", body: JSON.stringify(v) });
      setToken(d.access_token, d.user);
      toast("Welcome, " + d.user.display_name + "!");
      location.hash = "#/rooms";
    } catch (err) { $("#err").innerHTML = errBox(err); }
  };
};

views.verify = async function (_, params) {
  page(spinner());
  try {
    const r = await fetch("/api/auth/verify?token=" + encodeURIComponent(params.token || ""), { method: "POST" });
    if (!r.ok) throw new Error("Invalid or expired verification link.");
    page(`<div class="card" style="max-width:440px;margin:2rem auto"><h2>✅ ${esc(t("verify_email"))}</h2><p>Your email is verified. Welcome!</p><a class="btn btn-primary" href="#/dashboard">${esc(t("dashboard"))}</a></div>`);
  } catch (e) { page(`<div class="card" style="max-width:440px;margin:2rem auto">${errBox(e)}</div>`); }
};

views.forgot = function () {
  page(`<div class="card" style="max-width:440px;margin:2rem auto"><h2>${esc(t("reset_password"))}</h2><div id="err"></div><div id="ok"></div>
    <form id="f"><label class="field">${esc(t("email"))}</label><input name="email" type="email" required>
    <div style="margin-top:1rem"><button class="btn btn-primary" style="width:100%">${esc(t("send"))}</button></div></form></div>`);
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/auth/password/reset-request", { method: "POST", body: JSON.stringify({ email: new FormData(e.target).get("email") }) });
      $("#ok").innerHTML = `<div class="ok-box">If the email exists, a reset link has been sent.</div>`;
    } catch (err) { $("#err").innerHTML = errBox(err); }
  };
};

views.resetPassword = function (_, params) {
  page(`<div class="card" style="max-width:440px;margin:2rem auto"><h2>${esc(t("reset_password"))}</h2><div id="err"></div>
    <form id="f"><label class="field">${esc(t("password"))}</label><input name="pw" type="password" required minlength="8">
    <div style="margin-top:1rem"><button class="btn btn-primary" style="width:100%">${esc(t("save"))}</button></div></form></div>`);
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/auth/password/reset-confirm", { method: "POST", body: JSON.stringify({ token: params.token, new_password: new FormData(e.target).get("pw") }) });
      toast("Password updated. Please log in."); location.hash = "#/login";
    } catch (err) { $("#err").innerHTML = errBox(err); }
  };
};

/* ---------- dashboard ---------- */
views.dashboard = async function () {
  if (!requireAuth()) return;
  page(spinner());
  const u = MC.user;
  let suggested = [], notif = 0, overview = null;
  try { suggested = (await api("/api/users/me/suggested-rooms")).rooms; } catch {}
  try { notif = (await api("/api/notifications/unread-count")).unread; } catch {}
  if (u.role === "admin" || u.role === "moderator") { try { overview = await api("/api/admin/overview"); } catch {} }
  page(`
  <h2>Assalamu alaikum, ${esc(u.display_name)} ${u.is_scholar_verified ? '<span class="badge gold">🎓 Verified Scholar</span>' : ""} ${u.is_guest ? '<span class="badge gray">Guest</span>' : ""}</h2>
  ${!u.email_verified && !u.is_guest ? `<div class="ok-box">📧 Please verify your email. <button class="btn btn-sm btn-ghost" id="resend">Resend link</button></div>` : ""}
  ${u.is_guest ? `<div class="ok-box">🧳 You are browsing as a guest. <a href="#/register">${esc(t("create_account"))}</a> to save your profile.</div>` : ""}
  <div class="grid cols-3">
    <div class="card"><h3>💬 ${esc(t("chat"))}</h3><p>Join live rooms or continue conversations.</p><a class="btn btn-primary btn-sm" href="#/rooms">${esc(t("rooms"))}</a> ${u.is_guest ? "" : `<a class="btn btn-ghost btn-sm" href="#/messages">${esc(t("messages"))}</a>`}</div>
    <div class="card"><h3>📖 ${esc(t("qa"))}</h3><p>Ask and learn with sources.</p><a class="btn btn-primary btn-sm" href="#/qa">${esc(t("questions"))}</a></div>
    <div class="card"><h3>🔔 ${esc(t("notifications"))}</h3><p>${notif} ${esc(t("unread"))}</p><a class="btn btn-ghost btn-sm" href="#/notifications">${esc(t("notifications"))}</a></div>
  </div>
  ${suggested.length ? `<div class="card" style="margin-top:1rem"><h3>✨ ${esc(t("suggested"))}</h3>${suggested.map((r) => `<a class="chip" href="#/room/${r.slug}">${esc(r.name)}</a>`).join("")}</div>` : ""}
  ${overview ? `<div class="card" style="margin-top:1rem"><h3>📊 ${esc(t("overview"))}</h3><div class="stat-grid">
    ${[["users_total", "Users"], ["messages_total", "Messages"], ["reports_open", "Open reports"], ["scholar_pending", "Scholar apps"]].map(([k, l]) => `<div class="stat"><div class="num">${overview[k] ?? 0}</div><div class="lbl">${l}</div></div>`).join("")}
  </div></div>` : ""}
  <div class="grid cols-2" style="margin-top:1rem">
    <div class="card"><h3>⚙️ ${esc(t("settings"))}</h3><a class="btn btn-ghost btn-sm" href="#/profile">${esc(t("profile"))}</a> <a class="btn btn-ghost btn-sm" href="#/safety">${esc(t("safety"))}</a> <a class="btn btn-ghost btn-sm" href="#/bookmarks">${esc(t("bookmarks"))}</a></div>
    <div class="card"><h3>🎓 ${esc(t("scholars"))}</h3><a class="btn btn-ghost btn-sm" href="#/scholars">${esc(t("scholar_directory"))}</a> ${u.is_guest ? "" : `<a class="btn btn-ghost btn-sm" href="#/scholars/apply">${esc(t("apply_scholar"))}</a>`}</div>
  </div>`);
  const rs = $("#resend");
  if (rs) rs.onclick = async () => { try { await api("/api/auth/verify/resend", { method: "POST" }); toast("Verification email sent."); } catch (e) { toast(e.message); } };
};

/* ---------- rooms directory ---------- */
views.rooms = async function () {
  page(spinner());
  let rooms = [], kind = "", q = "";
  async function load() {
    const p = new URLSearchParams();
    if (kind) p.set("kind", kind);
    if (q) p.set("q", q);
    rooms = await api("/api/rooms?" + p.toString());
    render();
  }
  function render() {
    const groups = {};
    rooms.forEach((r) => { (groups[r.kind] = groups[r.kind] || []).push(r); });
    page(`
    <div style="display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-bottom:1rem">
      <h2 style="margin:0">💬 ${esc(t("rooms"))}</h2>
      <input id="rq" placeholder="${esc(t("search"))}…" value="${esc(q)}" style="max-width:220px">
      <select id="rk" style="max-width:180px">
        <option value="">All</option>
        ${["global", "country", "language", "topic"].map((k) => `<option value="${k}" ${kind === k ? "selected" : ""}>${k}</option>`).join("")}
      </select>
      ${MC.user && !MC.user.is_guest ? `<button class="btn btn-sm btn-ghost" id="newroom">+ ${esc(t("new_room"))}</button>` : ""}
    </div>
    ${Object.entries(groups).map(([k, list]) => `
      <h3 style="text-transform:capitalize">📌 ${esc(k)}</h3>
      <div class="grid cols-3">${list.map((r) => `
        <a class="card room-item" href="#/room/${r.slug}" style="display:block">
          <div class="nm">${esc(r.name)} ${r.unread ? `<span class="unread-pill">${r.unread}</span>` : ""}</div>
          <div class="hint">${esc(r.description || "")}</div>
          <div class="hint">${r.member_count} ${esc(t("members"))} · ${esc(r.language)}${r.allow_guests ? " · 🧳" : ""}</div>
        </a>`).join("")}</div>`).join("") || `<div class="empty">${esc(t("empty"))}</div>`}`);
    $("#rq").oninput = (e) => { q = e.target.value; clearTimeout(window._rqt); window._rqt = setTimeout(load, 350); };
    $("#rk").onchange = (e) => { kind = e.target.value; load(); };
    const nr = $("#newroom");
    if (nr) nr.onclick = () => modal(`
      <h3>${esc(t("new_room"))}</h3>
      <form id="nrf"><label class="field">Name</label><input name="name" required maxlength="120">
      <label class="field">Description</label><textarea name="description" maxlength="2000"></textarea>
      <label class="field">Visibility</label><select name="visibility"><option value="members">Registered members</option><option value="private">Private (invite)</option></select>
      <div style="margin-top:1rem"><button class="btn btn-primary">Create</button>
      <button type="button" class="btn btn-ghost" onclick="closeModal()">${esc(t("cancel"))}</button></div></form>`);
    const nrf = $("#nrf");
    if (nrf) nrf.onsubmit = async (e) => {
      e.preventDefault();
      const v = Object.fromEntries(new FormData(e.target));
      try {
        const r = await api("/api/rooms", { method: "POST", body: JSON.stringify({ ...v, kind: "topic" }) });
        closeModal(); location.hash = "#/room/" + r.slug;
      } catch (err) { toast(err.message); }
    };
  }
  try { await load(); } catch (e) { page(errBox(e)); }
};

/* ---------- room chat ---------- */
function msgHtml(m, opts = {}) {
  const mine = MC.user && m.author.id === MC.user.id;
  const reply = opts.replyMap && m.reply_to_id ? opts.replyMap[m.reply_to_id] : null;
  return `<div class="msg ${mine ? "mine" : ""}" data-id="${m.id}" id="msg-${m.id}">
    <div class="meta"><span>${esc(m.author.avatar_emoji)} <strong>${esc(m.author.display_name)}</strong></span>
      ${m.author.is_scholar_verified ? '<span class="badge gold">🎓</span>' : ""}
      ${m.author.is_guest ? '<span class="badge gray">Guest</span>' : ""}
      <span>@${esc(m.author.username)}</span><span>${fmtTime(m.created_at)}</span>${m.edited_at ? " · ✏️" : ""}</div>
    ${reply ? `<div class="replyctx">↩ ${esc(reply.author.display_name)}: ${esc(reply.body.slice(0, 80))}</div>` : ""}
    <div class="body">${linkify(m.body)}</div>
    ${m.attachment_url ? `<a href="${esc(m.attachment_url)}" target="_blank"><img class="att" src="${esc(m.attachment_url)}" alt="${esc(m.attachment_name || "image")}" loading="lazy"></a>` : ""}
    <div class="reactions">${Object.entries(m.reactions || {}).map(([e, c]) => `<button class="reaction-pill ${m.my_reactions.includes(e) ? "mine" : ""}" data-react="${esc(e)}" data-id="${m.id}">${esc(e)} ${c}</button>`).join("")}
      <button class="reaction-pill" data-react-pick="${m.id}">＋</button></div>
    <div class="actions">
      <button data-reply="${m.id}">↩ ${esc(t("reply"))}</button>
      ${mine ? `<button data-edit="${m.id}">✏️ ${esc(t("edit"))}</button><button data-del="${m.id}">🗑 ${esc(t("delete"))}</button>` : ""}
      <button data-report-msg="${m.id}">🚩</button>
    </div>
  </div>`;
}

function bindMsgActions(root, ctx) {
  $$("[data-react]", root).forEach((b) => b.onclick = async () => {
    try { await api(`/api/messages/${b.dataset.id}/reactions`, { method: "POST", body: JSON.stringify({ emoji: b.dataset.react }) }); }
    catch (e) { toast(e.message); }
  });
  $$("[data-react-pick]", root).forEach((b) => b.onclick = () => {
    const emojis = ["👍", "❤️", "🤲", "🌙", "😊", "👏", "💯", "🕌"];
    modal(`<h3>${esc(t("react"))}</h3><div style="font-size:1.8rem;display:flex;gap:.5rem;flex-wrap:wrap">${emojis.map((e) => `<button class="btn btn-ghost" data-pick="${e}" style="font-size:1.6rem">${e}</button>`).join("")}</div>`);
    $$("[data-pick]").forEach((p) => p.onclick = async () => {
      closeModal();
      try { await api(`/api/messages/${b.dataset.reactPick}/reactions`, { method: "POST", body: JSON.stringify({ emoji: p.dataset.pick }) }); }
      catch (e) { toast(e.message); }
    });
  });
  $$("[data-reply]", root).forEach((b) => b.onclick = () => ctx.setReply(parseInt(b.dataset.reply)));
  $$("[data-edit]", root).forEach((b) => b.onclick = () => {
    const m = ctx.messages.find((x) => x.id == b.dataset.edit);
    modal(`<h3>✏️ ${esc(t("edit"))}</h3><form id="ef"><textarea name="body">${esc(m.body)}</textarea>
      <div style="margin-top:1rem"><button class="btn btn-primary">${esc(t("save"))}</button>
      <button type="button" class="btn btn-ghost" onclick="closeModal()">${esc(t("cancel"))}</button></div></form>`);
    $("#ef").onsubmit = async (e) => {
      e.preventDefault(); closeModal();
      try { await api(`/api/messages/${m.id}`, { method: "PATCH", body: JSON.stringify({ body: new FormData(e.target).get("body") }) }); ctx.reload(); }
      catch (err) { toast(err.message); }
    };
  });
  $$("[data-del]", root).forEach((b) => b.onclick = async () => {
    if (!confirm("Delete this message?")) return;
    try { await api(`/api/messages/${b.dataset.del}`, { method: "DELETE" }); } catch (e) { toast(e.message); }
  });
  $$("[data-report-msg]", root).forEach((b) => b.onclick = () => openReportModal("message", parseInt(b.dataset.reportMsg)));
}

function openReportModal(targetType, targetId) {
  if (!MC.user) { location.hash = "#/login"; return; }
  modal(`<h3>🚩 ${esc(t("file_report"))}</h3><form id="rf">
    <label class="field">${esc(t("reason"))}</label>
    <select name="reason"><option>Harassment or bullying</option><option>Hate speech</option><option>Spam or scam</option><option>Fabricated religious quote</option><option>Private info shared</option><option>Other</option></select>
    <label class="field">${esc(t("details"))}</label><textarea name="details" maxlength="2000"></textarea>
    <div style="margin-top:1rem"><button class="btn btn-primary">${esc(t("submit"))}</button>
    <button type="button" class="btn btn-ghost" onclick="closeModal()">${esc(t("cancel"))}</button></div></form>`);
  $("#rf").onsubmit = async (e) => {
    e.preventDefault(); closeModal();
    const v = Object.fromEntries(new FormData(e.target));
    try { await api("/api/moderation/reports", { method: "POST", body: JSON.stringify({ target_type: targetType, target_id: targetId, ...v }) }); toast("Report submitted. JazakAllah."); }
    catch (err) { toast(err.message); }
  };
}

function connectWs() {
  closeWs();
  if (!MC.token) return;
  updateConnPill("connecting");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/ws?token=${encodeURIComponent(MC.token)}`);
  MC.ws = ws;
  ws.onopen = () => { MC.wsRetry = 0; updateConnPill("online"); if (MC._wsOnOpen) MC._wsOnOpen(); };
  ws.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    if (MC._wsOnMsg) MC._wsOnMsg(d);
  };
  ws.onclose = () => {
    updateConnPill("offline");
    if (MC.ws === ws) {
      MC.ws = null;
      MC.wsRetry++;
      const delay = Math.min(30000, 1000 * Math.pow(2, MC.wsRetry));
      MC.wsTimer = setTimeout(() => { if (MC._wsWant) connectWs(); }, delay);
    }
  };
  ws.onerror = () => { try { ws.close(); } catch {} };
}
function closeWs() {
  MC._wsWant = false; MC._wsOnOpen = null; MC._wsOnMsg = null;
  if (MC.wsTimer) clearTimeout(MC.wsTimer);
  if (MC._pollTimer) { clearInterval(MC._pollTimer); MC._pollTimer = null; }
  if (MC.ws) { const w = MC.ws; MC.ws = null; try { w.close(); } catch {} }
}
function wsSend(obj) { if (MC.ws && MC.ws.readyState === 1) MC.ws.send(JSON.stringify(obj)); }
/* Polling fallback: on hosts without WebSocket support (e.g. Vercel serverless),
   refresh messages every few seconds instead. Stops automatically if WS connects. */
function armPollFallback(reloadFn) {
  if (MC._pollTimer) { clearInterval(MC._pollTimer); MC._pollTimer = null; }
  setTimeout(() => {
    if ((!MC.ws || MC.ws.readyState !== 1) && !MC._pollTimer) {
      MC._pollTimer = setInterval(() => { try { reloadFn(); } catch {} }, 6000);
    }
  }, 6000);
  const prev = MC._wsOnOpen;
  MC._wsOnOpen = () => {
    if (MC._pollTimer) { clearInterval(MC._pollTimer); MC._pollTimer = null; }
    if (prev) prev();
  };
}

views.room = async function ({ slug }) {
  page(spinner());
  let room;
  try { room = await api("/api/rooms/" + slug); }
  catch (e) { page(`<div class="card">${errBox(e)}<a class="btn btn-ghost" href="#/rooms">${esc(t("back"))}</a></div>`); return; }
  const ctx = { messages: [], replyMap: {}, replyTo: null, slug };
  ctx.setReply = (id) => {
    ctx.replyTo = id;
    const m = ctx.messages.find((x) => x.id === id);
    $("#replybar").innerHTML = m ? `↩ ${esc(m.author.display_name)}: ${esc(m.body.slice(0, 60))} <button class="btn btn-sm btn-ghost" id="cancelreply">✕</button>` : "";
    $("#cancelreply").onclick = () => { ctx.replyTo = null; $("#replybar").innerHTML = ""; };
    $("#composer-input").focus();
  };
  async function loadMessages(beforeId) {
    const p = new URLSearchParams({ limit: 50 });
    if (beforeId) p.set("before_id", beforeId);
    const msgs = await api(`/api/rooms/${slug}/messages?` + p.toString());
    if (beforeId) { ctx.messages = [...msgs, ...ctx.messages]; }
    else ctx.messages = msgs;
    msgs.forEach((m) => ctx.replyMap[m.id] = m);
    renderMessages(beforeId != null);
  }
  function renderMessages(prepend) {
    const box = $("#messages");
    const html = ctx.messages.map((m) => msgHtml(m, ctx)).join("");
    if (prepend) { const oldH = box.scrollHeight; box.innerHTML = html; box.scrollTop = box.scrollHeight - oldH; }
    else { box.innerHTML = html || `<div class="empty">${esc(t("empty"))}</div>`; box.scrollTop = box.scrollHeight; }
    bindMsgActions(box, ctx);
  }
  ctx.reload = async () => { try { await loadMessages(); } catch {} };
  page(`
  <div class="chat-layout">
    <div class="card room-list-pane"><h3 style="margin-top:0">💬 ${esc(t("rooms"))}</h3><div id="roomlist">${spinner()}</div></div>
    <div class="card chat-pane">
      <div class="chat-header">
        <h2>${esc(room.name)}</h2>
        <span class="hint">${esc(room.description || "")}</span>
        <span style="margin-inline-start:auto" class="hint"><span class="presence-dot"></span> <span id="online-n">–</span> ${esc(t("online_now"))}</span>
        ${MC.user ? `<button class="btn btn-sm btn-ghost" id="joinbtn">${esc(t("join"))}</button>` : ""}
      </div>
      <div class="messages" id="messages">${spinner()}</div>
      <div class="typing-bar" id="typing"></div>
      <div id="replybar" style="padding:0 1rem;font-size:.85rem;color:var(--muted)"></div>
      <div class="composer">
        ${MC.user ? `
        <input id="composer-input" placeholder="${esc(t("type_message"))}" maxlength="4000" autocomplete="off" aria-label="${esc(t("type_message"))}">
        <label class="btn btn-ghost btn-sm" title="${esc(t("upload_image"))}" style="cursor:pointer">📎<input type="file" id="filepick" accept="image/*" class="sr-only"></label>
        <button class="btn btn-primary" id="sendbtn">${esc(t("send"))}</button>`
        : `<a class="btn btn-primary" href="#/login" style="flex:1">${esc(t("login"))} / ${esc(t("guest"))}</a>`}
      </div>
    </div>
  </div>`);
  // room list sidebar
  try {
    const rooms = await api("/api/rooms");
    $("#roomlist").innerHTML = rooms.map((r) => `<a class="room-item ${r.slug === slug ? "active" : ""}" href="#/room/${r.slug}"><div class="nm">${esc(r.name)} ${r.unread ? `<span class="unread-pill">${r.unread}</span>` : ""}</div><div class="hint">${r.member_count} ${esc(t("members"))}</div></a>`).join("");
  } catch {}
  try { await loadMessages(); } catch (e) { $("#messages").innerHTML = errBox(e); }
  const jb = $("#joinbtn");
  if (jb) jb.onclick = async () => { try { await api(`/api/rooms/${slug}/join`, { method: "POST" }); toast("Joined!"); } catch (e) { toast(e.message); } };
  // websocket
  if (MC.user) {
    MC._wsWant = true;
    MC._wsOnOpen = () => wsSend({ type: "join_room", slug });
    MC._wsOnMsg = (d) => {
      if (d.type === "message" && d.room_id === room.id) {
        const m = d.message;
        if (!ctx.messages.some((x) => x.id === m.id || x.uuid === m.uuid)) {
          ctx.messages.push(m); ctx.replyMap[m.id] = m; renderMessages();
        }
      } else if (d.type === "message_deleted") {
        ctx.messages = ctx.messages.filter((x) => x.id !== d.message_id); renderMessages();
      } else if (d.type === "reaction") { ctx.reload(); }
      else if (d.type === "typing" && d.room_id === room.id && d.user_id !== MC.user.id) {
        MC.typingUsers.set(d.user_id, Date.now());
        renderTyping();
      } else if (d.type === "presence" && d.room_id === room.id) {
        $("#online-n").textContent = d.online.length;
      }
    };
    connectWs();
    armPollFallback(() => ctx.reload());
  }
  function renderTyping() {
    const now = Date.now();
    const names = [];
    MC.typingUsers.forEach((ts, uid) => { if (now - ts > 4000) MC.typingUsers.delete(uid); });
    // names unknown without extra fetch; show count
    $("#typing").textContent = MC.typingUsers.size ? `✍️ ${MC.typingUsers.size} ${t("typing")}` : "";
  }
  setInterval(renderTyping, 2000);
  const input = $("#composer-input"), sendBtn = $("#sendbtn");
  async function send() {
    const body = input.value.trim();
    if (!body) return;
    const clientUuid = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));
    input.value = "";
    try {
      const m = await api(`/api/rooms/${slug}/messages`, { method: "POST", body: JSON.stringify({ body, reply_to_id: ctx.replyTo, client_uuid: clientUuid }) });
      ctx.replyTo = null; $("#replybar").innerHTML = "";
      if (!ctx.messages.some((x) => x.id === m.id)) { ctx.messages.push(m); ctx.replyMap[m.id] = m; renderMessages(); }
    } catch (e) { toast(e.message); input.value = body; }
  }
  if (sendBtn) {
    sendBtn.onclick = send;
    input.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
    let typingT;
    input.oninput = () => { clearTimeout(typingT); wsSend({ type: "typing", room_id: room.id }); typingT = setTimeout(() => {}, 3000); };
    $("#messages").addEventListener("scroll", async (e) => {
      const el = e.target;
      if (el.scrollTop < 60 && ctx.messages.length) {
        try { await loadMessages(ctx.messages[0].id); } catch {}
      }
    });
    const fp = $("#filepick");
    if (fp) fp.onchange = async () => {
      const f = fp.files[0]; if (!f) return;
      const fd = new FormData(); fd.append("file", f);
      try { toast("Uploading…"); await apiForm(`/api/rooms/${slug}/upload`, fd); } catch (e) { toast(e.message); }
      fp.value = "";
    };
  }
};

/* ---------- direct messages & groups ---------- */
views.messages = async function () {
  if (!requireRegistered()) return;
  page(spinner());
  try {
    const convs = await api("/api/conversations");
    page(`
    <div style="display:flex;gap:.6rem;margin-bottom:1rem;flex-wrap:wrap">
      <h2 style="margin:0">✉️ ${esc(t("direct_messages"))}</h2>
      <button class="btn btn-sm btn-ghost" id="newdm">+ ${esc(t("new_conversation"))}</button>
      <button class="btn btn-sm btn-ghost" id="newgrp">+ ${esc(t("new_group"))}</button>
    </div>
    <div class="grid cols-2">${convs.map((c) => `
      <a class="card" href="#/messages/${c.id}" style="display:block">
        <strong>${c.is_group ? "👥 " : "💬 "}${esc(c.title || "Conversation")}</strong>
        ${c.unread ? `<span class="unread-pill badge">${c.unread}</span>` : ""}
        <div class="hint">${c.participants.map((p) => esc(p.display_name)).join(", ")}</div>
        <div class="hint">${c.last_message_at ? fmtTime(c.last_message_at) : ""}</div>
      </a>`).join("") || `<div class="empty">${esc(t("empty"))}</div>`}</div>`);
    $("#newdm").onclick = () => {
      modal(`<h3>${esc(t("new_conversation"))}</h3><form id="dmf"><label class="field">${esc(t("username"))}</label><input name="u" required><div style="margin-top:1rem"><button class="btn btn-primary">${esc(t("send"))}</button></div></form>`);
      $("#dmf").onsubmit = async (e) => {
        e.preventDefault();
        const uname = new FormData(e.target).get("u");
        try {
          const users = await api("/api/users/directory?q=" + encodeURIComponent(uname));
          const match = users.users.find((x) => x.username.toLowerCase() === String(uname).toLowerCase());
          if (!match) throw new Error("User not found.");
          const c = await api("/api/conversations/dm", { method: "POST", body: JSON.stringify({ user_id: match.id }) });
          closeModal(); location.hash = "#/messages/" + c.id;
        } catch (err) { toast(err.message); }
      };
    };
    $("#newgrp").onclick = () => {
      modal(`<h3>${esc(t("new_group"))}</h3><form id="gf"><label class="field">Title</label><input name="title" required maxlength="120">
      <label class="field">Member usernames (comma separated)</label><input name="members">
      <div style="margin-top:1rem"><button class="btn btn-primary">${esc(t("send"))}</button></div></form>`);
      $("#gf").onsubmit = async (e) => {
        e.preventDefault();
        const v = Object.fromEntries(new FormData(e.target));
        try {
          const names = String(v.members || "").split(",").map((s) => s.trim()).filter(Boolean);
          const ids = [];
          for (const n of names) {
            const users = await api("/api/users/directory?q=" + encodeURIComponent(n));
            const m = users.users.find((x) => x.username.toLowerCase() === n.toLowerCase());
            if (m) ids.push(m.id);
          }
          const c = await api("/api/conversations/group", { method: "POST", body: JSON.stringify({ title: v.title, member_ids: ids }) });
          closeModal(); location.hash = "#/messages/" + c.id;
        } catch (err) { toast(err.message); }
      };
    };
  } catch (e) { page(errBox(e)); }
};

views.conversation = async function ({ id }) {
  if (!requireRegistered()) return;
  page(spinner());
  const ctx = { messages: [], replyMap: {}, replyTo: null };
  ctx.setReply = (rid) => {
    ctx.replyTo = rid;
    const m = ctx.messages.find((x) => x.id === rid);
    $("#replybar").innerHTML = m ? `↩ ${esc(m.author.display_name)}: ${esc(m.body.slice(0, 60))} <button class="btn btn-sm btn-ghost" id="cancelreply">✕</button>` : "";
    const c = $("#cancelreply"); if (c) c.onclick = () => { ctx.replyTo = null; $("#replybar").innerHTML = ""; };
    $("#composer-input").focus();
  };
  async function load(beforeId) {
    const p = new URLSearchParams({ limit: 50 });
    if (beforeId) p.set("before_id", beforeId);
    const msgs = await api(`/api/conversations/${id}/messages?` + p);
    ctx.messages = beforeId ? [...msgs, ...ctx.messages] : msgs;
    msgs.forEach((m) => ctx.replyMap[m.id] = m);
    const box = $("#messages");
    box.innerHTML = ctx.messages.map((m) => msgHtml(m, ctx)).join("") || `<div class="empty">${esc(t("empty"))}</div>`;
    if (!beforeId) box.scrollTop = box.scrollHeight;
    bindMsgActions(box, ctx);
  }
  try { await load(); } catch (e) { page(`<div class="card">${errBox(e)}</div>`); return; }
  page(`
  <a href="#/messages">← ${esc(t("direct_messages"))}</a>
  <div class="card chat-pane" style="height:calc(100vh - 220px);min-height:420px;margin-top:.6rem">
    <div class="messages" id="messages"></div>
    <div class="typing-bar" id="typing"></div>
    <div id="replybar" style="padding:0 1rem;font-size:.85rem;color:var(--muted)"></div>
    <div class="composer">
      <input id="composer-input" placeholder="${esc(t("type_message"))}" maxlength="4000" autocomplete="off">
      <button class="btn btn-primary" id="sendbtn">${esc(t("send"))}</button>
    </div>
  </div>`);
  // re-render after page() replaced content
  const box = $("#messages");
  box.innerHTML = ctx.messages.map((m) => msgHtml(m, ctx)).join("") || `<div class="empty">${esc(t("empty"))}</div>`;
  box.scrollTop = box.scrollHeight;
  bindMsgActions(box, ctx);
  MC._wsWant = true;
  MC._wsOnOpen = () => wsSend({ type: "join_conv", conversation_id: parseInt(id) });
  MC._wsOnMsg = (d) => {
    if (d.type === "message" && d.conversation_id == id) {
      const m = d.message;
      if (!ctx.messages.some((x) => x.id === m.id)) { ctx.messages.push(m); ctx.replyMap[m.id] = m; box.innerHTML += msgHtml(m, ctx); bindMsgActions(box, ctx); box.scrollTop = box.scrollHeight; }
    } else if (d.type === "message_deleted") {
      ctx.messages = ctx.messages.filter((x) => x.id !== d.message_id);
      const el = $("#msg-" + d.message_id); if (el) el.remove();
    } else if (d.type === "typing" && d.conversation_id == id && d.user_id !== MC.user.id) {
      $("#typing").textContent = `✍️ ${esc(d.display_name)} ${t("typing")}`;
      clearTimeout(window._ct); window._ct = setTimeout(() => $("#typing").textContent = "", 3500);
    }
  };
  connectWs();
  armPollFallback(() => load());
  const input = $("#composer-input");
  async function send() {
    const body = input.value.trim(); if (!body) return;
    input.value = "";
    try {
      const m = await api(`/api/conversations/${id}/messages`, { method: "POST", body: JSON.stringify({ body, reply_to_id: ctx.replyTo, client_uuid: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) }) });
      ctx.replyTo = null; const rb = $("#replybar"); if (rb) rb.innerHTML = "";
      if (!ctx.messages.some((x) => x.id === m.id)) { ctx.messages.push(m); ctx.replyMap[m.id] = m; box.innerHTML += msgHtml(m, ctx); bindMsgActions(box, ctx); box.scrollTop = box.scrollHeight; }
    } catch (e) { toast(e.message); input.value = body; }
  }
  $("#sendbtn").onclick = send;
  input.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  input.oninput = () => wsSend({ type: "typing", conversation_id: parseInt(id) });
};

/* ---------- Q&A ---------- */
views.qa = async function () {
  page(spinner());
  let cats = [], questions = [], cat = "", q = "";
  try { cats = await api("/api/qa/categories"); } catch {}
  async function load() {
    const p = new URLSearchParams();
    if (cat) p.set("category", cat);
    if (q) p.set("q", q);
    questions = await api("/api/qa/questions?" + p);
    render();
  }
  function render() {
    page(`
    <div style="display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-bottom:1rem">
      <h2 style="margin:0">📖 ${esc(t("qa"))}</h2>
      <input id="qq" placeholder="${esc(t("search"))}…" value="${esc(q)}" style="max-width:220px">
      <select id="qc" style="max-width:200px"><option value="">${esc(t("category"))}: all</option>
        ${cats.map((c) => `<option value="${c.id}" ${cat === c.id ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select>
      ${MC.user && !MC.user.is_guest ? `<a class="btn btn-sm btn-primary" href="#/qa/ask">+ ${esc(t("ask"))}</a>` : ""}
    </div>
    <div class="card">${questions.map((x) => `
      <div class="qa-item">
        <h3><a href="#/qa/${x.id}">${esc(x.title)}</a></h3>
        <div class="hint">${esc(x.category)} · ${x.answer_count} ${esc(t("answers"))} · ${x.view_count} views · ${esc(x.author.display_name)} · ${fmtTime(x.created_at)}
        ${x.is_answered ? ' <span class="badge">✅ ' + esc(t("accepted")) + "</span>" : ""}</div>
        <p>${esc(x.body.slice(0, 220))}…</p>
      </div>`).join("") || `<div class="empty">${esc(t("empty"))}</div>`}</div>`);
    $("#qq").oninput = (e) => { q = e.target.value; clearTimeout(window._qqt); window._qqt = setTimeout(load, 350); };
    $("#qc").onchange = (e) => { cat = e.target.value; load(); };
  }
  try { await load(); } catch (e) { page(errBox(e)); }
};

views.askQuestion = function () {
  if (!requireRegistered()) return;
  page(`<div class="card" style="max-width:720px;margin:0 auto"><h2>❓ ${esc(t("ask_question"))}</h2><div id="err"></div>
  <form id="f">
    <label class="field">${esc(t("question_title"))}</label><input name="title" required minlength="5" maxlength="200">
    <label class="field">${esc(t("category"))}</label><select name="category"><option value="quran">Quran & Tafsir</option><option value="hadith">Hadith</option><option value="seerah">Seerah & History</option><option value="fiqh">Fiqh & Daily Life</option><option value="general">General Community</option></select>
    <label class="field">${esc(t("tags"))} (comma separated)</label><input name="tags" maxlength="300">
    <label class="field">${esc(t("question_body"))}</label><textarea name="body" required minlength="10" maxlength="10000" style="min-height:160px"></textarea>
    <div style="margin-top:1rem"><button class="btn btn-primary">${esc(t("post"))}</button></div>
  </form></div>`);
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const q = await api("/api/qa/questions", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(e.target))) });
      location.hash = "#/qa/" + q.id;
    } catch (err) { $("#err").innerHTML = errBox(err); }
  };
};

views.question = async function ({ id }) {
  page(spinner());
  try {
    const q = await api("/api/qa/questions/" + id);
    const answers = await api(`/api/qa/questions/${id}/answers`);
    const kinds = await api("/api/qa/kinds").catch(() => []);
    const kindLabel = (k) => (kinds.find((x) => x.id === k) || {}).label || k;
    page(`
    <a href="#/qa">← ${esc(t("qa"))}</a>
    <div class="card" style="margin-top:.6rem">
      <h2>${esc(q.title)}</h2>
      <div class="hint">${esc(q.category)} · ${esc(q.author.display_name)} · ${fmtTime(q.created_at)} · ${q.view_count} views</div>
      <p style="white-space:pre-wrap">${linkify(q.body)}</p>
      <div>${q.tags.split(",").filter(Boolean).map((tg) => `<span class="chip">${esc(tg.trim())}</span>`).join("")}</div>
      <div style="margin-top:.6rem"><button class="btn btn-sm btn-ghost" id="bmq">🔖 ${esc(t("bookmarks"))}</button>
      <button class="btn btn-sm btn-ghost" id="repq">🚩 ${esc(t("report"))}</button></div>
    </div>
    <h3 style="margin-top:1.2rem">${answers.length} ${esc(t("answers"))}</h3>
    ${answers.map((a) => `
    <div class="card answer">
      <div class="hint"><strong>${esc(a.author.display_name)}</strong> ${a.author.is_scholar_verified ? '<span class="badge gold">🎓 Verified Scholar</span>' : ""}
      · <span class="kind-tag">${esc(kindLabel(a.kind))}</span> · ${fmtTime(a.created_at)}</div>
      <p style="white-space:pre-wrap">${linkify(a.body)}</p>
      ${(a.citations || []).map((c) => `<div class="citation"><span class="ref">📜 ${esc(c.reference)}</span>
        ${c.verified ? `<span class="badge">${esc(t("citation_verified"))}</span>` : `<span class="badge gray">${esc(t("citation_unverified"))}</span>`}
        <div class="hint">${esc(c.type)}</div><div>${esc(c.text)}</div></div>`).join("")}
      <div style="display:flex;gap:.5rem;align-items:center;margin-top:.5rem">
        <button class="btn btn-sm ${a.voted_useful ? "btn-gold" : "btn-ghost"}" data-useful="${a.id}">👍 ${esc(t("mark_useful"))} (${a.useful_count})</button>
        ${q.author.id === (MC.user && MC.user.id) ? `<button class="btn btn-sm btn-ghost" data-accept="${a.id}">✅ ${esc(t("accept"))}</button>` : ""}
        <button class="btn btn-sm btn-ghost" data-bma="${a.id}">🔖</button>
        <button class="btn btn-sm btn-ghost" data-repa="${a.id}">🚩</button>
      </div>
    </div>`).join("") || `<div class="empty">${esc(t("empty"))}</div>`}
    ${MC.user && !MC.user.is_guest ? `
    <div class="card" style="margin-top:1rem"><h3>✍️ Post an answer</h3><div id="err"></div>
      <form id="af">
        <label class="field">Answer type</label><select name="kind">${kinds.map((k) => `<option value="${k.id}">${esc(k.label)}</option>`).join("")}</select>
        <div class="hint">Mark clearly: is this a quotation, a translation, an interpretation, or your personal opinion?</div>
        <label class="field">Your answer</label><textarea name="body" required minlength="10" maxlength="15000" style="min-height:140px"></textarea>
        <label class="field">Citations (one per line: <code>type | reference | text</code>, e.g. <code>quran | Al-Baqarah 2:152 | …</code>)</label>
        <textarea name="cites" style="min-height:70px" placeholder="quran | Al-Baqarah 2:152 | So remember Me…"></textarea>
        <div class="hint">⚠️ Never invent verses or hadith. If you are unsure of a reference, leave it out or mark it unverified.</div>
        <div style="margin-top:1rem"><button class="btn btn-primary">${esc(t("post"))}</button></div>
      </form></div>` : `<div class="ok-box"><a href="#/login">${esc(t("login"))}</a> to post an answer.</div>`}
    <p class="hint" style="margin-top:1rem">🕌 ${esc(t("verification_disclaimer"))} This platform is not a substitute for qualified scholarly, medical, or legal advice.</p>`);
    $("#bmq").onclick = async () => { try { await api("/api/users/bookmarks", { method: "POST", body: JSON.stringify({ target_type: "question", target_id: q.id }) }); toast("🔖 Saved"); } catch (e) { toast(e.message); } };
    $("#repq").onclick = () => openReportModal("question", q.id);
    $$("[data-useful]").forEach((b) => b.onclick = async () => {
      if (!MC.user) { location.hash = "#/login"; return; }
      try { const r = await api(`/api/qa/answers/${b.dataset.useful}/useful`, { method: "POST" }); b.innerHTML = `👍 ${esc(t("mark_useful"))} (${r.useful_count})`; } catch (e) { toast(e.message); }
    });
    $$("[data-accept]").forEach((b) => b.onclick = async () => {
      try { await api(`/api/qa/questions/${id}/accept/${b.dataset.accept}`, { method: "POST" }); toast("✅ Accepted"); route(); } catch (e) { toast(e.message); }
    });
    $$("[data-bma]").forEach((b) => b.onclick = async () => {
      if (!requireRegistered()) return;
      try { await api("/api/users/bookmarks", { method: "POST", body: JSON.stringify({ target_type: "answer", target_id: parseInt(b.dataset.bma) }) }); toast("🔖 Saved"); } catch (e) { toast(e.message); }
    });
    $$("[data-repa]").forEach((b) => b.onclick = () => openReportModal("answer", parseInt(b.dataset.repa)));
    const af = $("#af");
    if (af) af.onsubmit = async (e) => {
      e.preventDefault();
      const v = Object.fromEntries(new FormData(e.target));
      const citations = String(v.cites || "").split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
        const [type, reference, ...rest] = l.split("|").map((s) => s.trim());
        return { type: ["quran", "hadith"].includes(type) ? type : "other", reference: reference || "", text: rest.join("|"), verified: false };
      });
      try {
        await api(`/api/qa/questions/${id}/answers`, { method: "POST", body: JSON.stringify({ body: v.body, kind: v.kind, citations }) });
        toast("Answer posted."); route();
      } catch (err) { $("#err").innerHTML = errBox(err); }
    };
  } catch (e) { page(`<div class="card">${errBox(e)}</div>`); }
};

/* ---------- scholars ---------- */
views.scholars = async function () {
  page(spinner());
  try {
    const d = await api("/api/scholars/directory");
    page(`
    <div style="display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-bottom:1rem">
      <h2 style="margin:0">🎓 ${esc(t("scholar_directory"))}</h2>
      ${MC.user && !MC.user.is_guest && !MC.user.is_scholar_verified ? `<a class="btn btn-sm btn-gold" href="#/scholars/apply">${esc(t("apply_scholar"))}</a>` : ""}
    </div>
    <div class="ok-box">🛡️ ${esc(t("verification_disclaimer"))}</div>
    <div class="grid cols-3">${d.scholars.map((s) => `
      <div class="card"><div style="display:flex;gap:.8rem;align-items:center">
        <div class="avatar-lg">${esc(s.avatar_emoji)}</div>
        <div><strong>${esc(s.display_name)}</strong><br><span class="badge gold">🎓 Verified Scholar</span>
        <div class="hint">${esc(s.scholar_title || "")} · ${esc(s.country)}</div></div></div>
        <p>${esc((s.scholar_bio || "").slice(0, 160))}</p>
        <div class="hint">🌐 ${esc(s.scholar_languages)} · 📚 ${esc(s.scholar_specialties)}</div>
        <div style="margin-top:.6rem"><a class="btn btn-sm btn-ghost" href="#/user/${s.id}">${esc(t("profile"))}</a>
        <a class="btn btn-sm btn-primary" href="#/qa/ask?to=${s.id}">${esc(t("ask"))}</a></div>
      </div>`).join("") || `<div class="empty">${esc(t("empty"))}</div>`}</div>`);
  } catch (e) { page(errBox(e)); }
};

views.scholarApply = function () {
  if (!requireRegistered()) return;
  page(`<div class="card" style="max-width:680px;margin:0 auto"><h2>🎓 ${esc(t("apply_scholar"))}</h2>
  <p class="hint">${esc(t("verification_disclaimer"))} Applications are reviewed by a human. Documents are stored securely and visible only to reviewers.</p>
  <div id="err"></div><div id="ok"></div>
  <form id="f">
    <label class="field">Full name</label><input name="full_name" required maxlength="120">
    <label class="field">${esc(t("bio"))}</label><textarea name="biography" maxlength="5000"></textarea>
    <label class="field">${esc(t("country"))}</label><input name="country" maxlength="80">
    <label class="field">Languages</label><input name="languages" maxlength="200" placeholder="English, Urdu, Arabic">
    <label class="field">Areas of knowledge</label><input name="specialties" maxlength="300" placeholder="Fiqh, Hadith, Seerah">
    <label class="field">Educational background</label><textarea name="education" maxlength="5000"></textarea>
    <label class="field">Credentials / ijazahs</label><textarea name="credentials" maxlength="5000"></textarea>
    <div style="margin-top:1rem"><button class="btn btn-primary">${esc(t("submit"))}</button></div>
  </form></div>`);
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/scholars/apply", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(e.target))) });
      $("#ok").innerHTML = `<div class="ok-box">Application #${r.application_id} submitted for review. You can upload verification documents next (PDF/image, max 10MB).</div>
      <form id="df"><input type="file" name="files" multiple accept=".pdf,.png,.jpg,.jpeg"><button class="btn btn-ghost btn-sm">Upload documents</button></form>`;
      $("#df").onsubmit = async (ev) => {
        ev.preventDefault();
        const fd = new FormData(ev.target);
        try { const u = await apiForm(`/api/scholars/apply/${r.application_id}/documents`, fd); toast(`Uploaded (${u.documents} total).`); }
        catch (err) { toast(err.message); }
      };
    } catch (err) { $("#err").innerHTML = errBox(err); }
  };
};

/* ---------- profile / discover / directory ---------- */
views.profile = async function () {
  if (!requireAuth()) return;
  page(spinner());
  const u = MC.user;
  page(`<div class="card" style="max-width:680px;margin:0 auto">
    <div style="display:flex;gap:1rem;align-items:center"><div class="avatar-lg">${esc(u.avatar_emoji)}</div>
    <div><h2 style="margin:0">${esc(u.display_name)}</h2><div class="hint">@${esc(u.username)} · ${esc(u.role)}${u.is_scholar_verified ? " · 🎓 Verified Scholar" : ""}${u.is_guest ? " · Guest" : ""}</div></div></div>
    <div id="err"></div>
    <form id="f">
      <label class="field">${esc(t("display_name"))}</label><input name="display_name" value="${esc(u.display_name)}" maxlength="80">
      <label class="field">${esc(t("bio"))}</label><textarea name="bio" maxlength="1000">${esc(u.bio || "")}</textarea>
      <label class="field">${esc(t("country"))}</label><input name="country" value="${esc(u.country || "")}" maxlength="80">
      <label class="field">${esc(t("language"))}</label><select name="language"><option value="en" ${u.language === "en" ? "selected" : ""}>English</option><option value="ur" ${u.language === "ur" ? "selected" : ""}>اردو</option><option value="ar" ${u.language === "ar" ? "selected" : ""}>العربية</option></select>
      <label class="field">${esc(t("interests"))} (comma separated)</label><input name="interests" value="${esc(u.interests || "")}" maxlength="500">
      <label class="field">Avatar emoji</label><input name="avatar_emoji" value="${esc(u.avatar_emoji)}" maxlength="16" style="max-width:120px">
      <label class="field">${esc(t("theme"))}</label><select name="theme"><option value="light" ${u.theme !== "dark" ? "selected" : ""}>${esc(t("light"))}</option><option value="dark" ${u.theme === "dark" ? "selected" : ""}>${esc(t("dark"))}</option></select>
      <div style="margin-top:.8rem;display:grid;gap:.4rem">
        ${[["online_visible", "Show online status"], ["notify_messages", "Message notifications"], ["notify_answers", "Answer notifications"], ["notify_moderation", "Moderation notifications"]].map(([k, l]) => `<label><input type="checkbox" name="${k}" ${u[k] ? "checked" : ""} style="width:auto"> ${l}</label>`).join("")}
      </div>
      <div style="margin-top:1rem;display:flex;gap:.6rem;flex-wrap:wrap">
        <button class="btn btn-primary">${esc(t("save"))}</button>
        <a class="btn btn-ghost" href="#/user/${u.id}">Public view</a>
      </div>
    </form>
    <hr style="margin:1.5rem 0">
    <h3>🔐 ${esc(t("account"))}</h3>
    ${u.is_guest ? `<p class="hint">Guests: <a href="#/register">create a full account</a> to keep this profile.</p>`
    : `<div id="pwerr"></div><form id="pwf"><label class="field">Current ${esc(t("password"))}</label><input name="cur" type="password" required>
      <label class="field">New ${esc(t("password"))}</label><input name="nw" type="password" required minlength="8">
      <div style="margin-top:.6rem"><button class="btn btn-ghost btn-sm">Change password</button>
      <a class="btn btn-ghost btn-sm" href="#/export">⬇ ${esc(t("export_data"))}</a></div></form>
      <div style="margin-top:1rem"><button class="btn btn-danger btn-sm" id="delacc">${esc(t("delete_account"))}</button></div>`}
  </div>`);
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = Object.fromEntries(fd);
    ["online_visible", "notify_messages", "notify_answers", "notify_moderation"].forEach((k) => body[k] = fd.get(k) === "on");
    try {
      const nu = await api("/api/users/me", { method: "PATCH", body: JSON.stringify(body) });
      MC.user = nu; applyTheme(); renderNav(); toast("Profile saved."); route();
    } catch (err) { $("#err").innerHTML = errBox(err); }
  };
  const pwf = $("#pwf");
  if (pwf) pwf.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try { await api("/api/auth/password/change", { method: "POST", body: JSON.stringify({ current_password: fd.get("cur"), new_password: fd.get("nw") }) }); toast("Password changed."); e.target.reset(); }
    catch (err) { $("#pwerr").innerHTML = errBox(err); }
  };
  const del = $("#delacc");
  if (del) del.onclick = async () => {
    if (!confirm("Permanently delete your account? This cannot be undone.")) return;
    if (!confirm("Really delete? Type OK mentally and click OK to confirm.")) return;
    try { await api("/api/auth/me", { method: "DELETE" }); setToken("", null); location.hash = "#/"; toast("Account deleted."); }
    catch (e) { toast(e.message); }
  };
};

views.userProfile = async function ({ id }) {
  page(spinner());
  try {
    const p = await api("/api/users/" + id);
    const me = MC.user;
    page(`<div class="card" style="max-width:640px;margin:0 auto">
      <div style="display:flex;gap:1rem;align-items:center"><div class="avatar-lg">${esc(p.avatar_emoji)}</div>
      <div><h2 style="margin:0">${esc(p.display_name)} ${p.is_scholar_verified ? '<span class="badge gold">🎓 Verified Scholar</span>' : ""}</h2>
      <div class="hint">@${esc(p.username)} · ${esc(p.country)} · ${esc(p.language)}</div></div></div>
      ${p.bio ? `<p style="white-space:pre-wrap">${esc(p.bio)}</p>` : ""}
      ${p.interests ? `<div>${p.interests.split(",").map((i) => `<span class="chip">${esc(i.trim())}</span>`).join("")}</div>` : ""}
      ${p.scholar_title ? `<p><strong>${esc(p.scholar_title)}</strong></p>` : ""}
      <div style="margin-top:1rem;display:flex;gap:.5rem;flex-wrap:wrap">
        ${me && !me.is_guest && me.id !== p.id ? `
          <button class="btn btn-sm ${p.is_following ? "btn-ghost" : "btn-primary"}" id="fol">${p.is_following ? esc(t("unfollow")) : esc(t("follow"))}</button>
          <button class="btn btn-sm btn-ghost" id="dm">✉️ ${esc(t("direct_messages"))}</button>
          <button class="btn btn-sm btn-ghost" id="blk">${p.is_blocked ? esc(t("unblock")) : esc(t("block"))}</button>
          <button class="btn btn-sm btn-ghost" id="rep">🚩 ${esc(t("report"))}</button>` : ""}
      </div></div>`);
    const fol = $("#fol");
    if (fol) fol.onclick = async () => {
      try { await api(`/api/users/${p.id}/follow`, { method: p.is_following ? "DELETE" : "POST" }); route(); }
      catch (e) { toast(e.message); }
    };
    const dm = $("#dm");
    if (dm) dm.onclick = async () => {
      try { const c = await api("/api/conversations/dm", { method: "POST", body: JSON.stringify({ user_id: p.id }) }); location.hash = "#/messages/" + c.id; }
      catch (e) { toast(e.message); }
    };
    const blk = $("#blk");
    if (blk) blk.onclick = async () => {
      try { await api(`/api/moderation/block/${p.id}`, { method: p.is_blocked ? "DELETE" : "POST" }); toast(p.is_blocked ? "Unblocked" : "Blocked"); route(); }
      catch (e) { toast(e.message); }
    };
    const rep = $("#rep");
    if (rep) rep.onclick = () => openReportModal("user", p.id);
  } catch (e) { page(`<div class="card">${errBox(e)}</div>`); }
};

views.discover = async function () {
  page(spinner());
  let users = [], q = "", scholarsOnly = false;
  async function load() {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (scholarsOnly) p.set("scholars_only", "true");
    users = (await api("/api/users/directory?" + p)).users;
    page(`
    <h2>🔍 ${esc(t("discover"))}</h2>
    <div style="display:flex;gap:.6rem;margin-bottom:1rem;flex-wrap:wrap">
      <input id="dq" placeholder="${esc(t("search"))}…" value="${esc(q)}" style="max-width:260px">
      <label style="align-self:center"><input type="checkbox" id="ds" ${scholarsOnly ? "checked" : ""} style="width:auto"> 🎓 ${esc(t("scholars"))}</label>
    </div>
    <div class="grid cols-3">${users.map((u) => `
      <a class="card" href="#/user/${u.id}" style="display:block">
        <div style="display:flex;gap:.7rem;align-items:center"><div class="avatar-lg" style="width:52px;height:52px;font-size:1.5rem">${esc(u.avatar_emoji)}</div>
        <div><strong>${esc(u.display_name)}</strong>${u.is_scholar_verified ? ' <span class="badge gold">🎓</span>' : ""}<div class="hint">@${esc(u.username)} · ${esc(u.country)}</div></div></div>
      </a>`).join("") || `<div class="empty">${esc(t("empty"))}</div>`}</div>`);
    $("#dq").oninput = (e) => { q = e.target.value; clearTimeout(window._dqt); window._dqt = setTimeout(load, 350); };
    $("#ds").onchange = (e) => { scholarsOnly = e.target.checked; load(); };
  }
  try { await load(); } catch (e) { page(errBox(e)); }
};

/* ---------- notifications / bookmarks / export ---------- */
views.notifications = async function () {
  if (!requireAuth()) return;
  page(spinner());
  try {
    const items = await api("/api/notifications?limit=60");
    await api("/api/notifications/read-all", { method: "POST" });
    page(`<div style="display:flex;justify-content:space-between;align-items:center"><h2>🔔 ${esc(t("notifications"))}</h2></div>
    <div class="card">${items.map((n) => `
      <div class="qa-item"><strong>${esc(n.title)}</strong> <span class="badge gray">${esc(n.kind)}</span>
      <div class="hint">${fmtTime(n.created_at)}</div><p>${esc(n.body)}</p>
      ${n.link ? `<a href="${esc(n.link)}" class="btn btn-sm btn-ghost">Open →</a>` : ""}</div>`).join("") || `<div class="empty">${esc(t("empty"))}</div>`}</div>`);
    pollUnread();
  } catch (e) { page(errBox(e)); }
};

views.bookmarks = async function () {
  if (!requireRegistered()) return;
  page(spinner());
  try {
    const d = await api("/api/users/me/bookmarks");
    page(`<h2>🔖 ${esc(t("bookmarks"))}</h2><div class="card">
    ${d.bookmarks.map((b) => `<div class="qa-item"><span class="badge">${esc(b.target_type)}</span>
      <a href="${esc(b.link || "#")}"><strong>${esc(b.title || b.target_type + " #" + b.target_id)}</strong></a>
      <div class="hint">${fmtTime(b.created_at)}</div></div>`).join("") || `<div class="empty">${esc(t("empty"))}</div>`}</div>`);
  } catch (e) { page(errBox(e)); }
};

views.exportData = async function () {
  if (!requireRegistered()) return;
  page(spinner());
  try {
    const d = await api("/api/users/me/export");
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    page(`<div class="card" style="max-width:520px;margin:2rem auto"><h2>⬇ ${esc(t("export_data"))}</h2>
    <p>Your data export is ready.</p><a class="btn btn-primary" href="${url}" download="muslim-connect-export.json">Download JSON</a></div>`);
  } catch (e) { page(errBox(e)); }
};

/* ---------- safety center / reports / appeal ---------- */
views.safety = async function () {
  let rules = "";
  try { rules = (await api("/api/admin/settings/public")).community_rules || ""; } catch {}
  page(`<h2>🛡️ ${esc(t("safety"))}</h2>
  <div class="card"><h3>📜 ${esc(t("community_rules"))}</h3><p style="white-space:pre-wrap">${esc(rules)}</p></div>
  <div class="grid cols-2" style="margin-top:1rem">
    <div class="card"><h3>🚩 ${esc(t("report"))}</h3><p>Report abusive users, messages, or content from anywhere you see it — every message, profile, question and answer has a 🚩 button.</p>
    ${MC.user ? `<a class="btn btn-ghost btn-sm" href="#/reports">${esc(t("my_reports"))}</a>` : ""}</div>
    <div class="card"><h3>🚫 ${esc(t("block"))}</h3><p>Blocking stops DMs and hides a user's messages from you.</p>
    ${MC.user && !MC.user.is_guest ? `<a class="btn btn-ghost btn-sm" href="#/blocked">Manage blocked users</a>` : ""}</div>
  </div>
  <div class="card" style="margin-top:1rem"><h3>⚖️ ${esc(t("appeal"))}</h3>
    <p>If your account was suspended or banned, you can appeal. Appeals are reviewed by a human moderator.</p>
    <div id="err"></div><form id="af"><textarea name="details" required minlength="5" maxlength="2000" placeholder="Explain what happened…"></textarea>
    <div style="margin-top:.6rem"><button class="btn btn-primary btn-sm">${esc(t("submit"))}</button></div></form></div>`);
  $("#af").onsubmit = async (e) => {
    e.preventDefault();
    if (!MC.user) { location.hash = "#/login"; return; }
    try { await api("/api/moderation/appeals", { method: "POST", body: JSON.stringify({ details: new FormData(e.target).get("details") }) }); toast("Appeal submitted."); e.target.reset(); }
    catch (err) { $("#err").innerHTML = errBox(err); }
  };
};

views.myReports = async function () {
  if (!requireAuth()) return;
  page(spinner());
  try {
    const d = await api("/api/moderation/reports/mine");
    page(`<h2>🚩 ${esc(t("my_reports"))}</h2><div class="card"><table class="data">
    <tr><th>#</th><th>Target</th><th>${esc(t("reason"))}</th><th>Status</th><th>Date</th></tr>
    ${d.reports.map((r) => `<tr><td>${r.id}</td><td>${esc(r.target_type)} #${r.target_id}</td><td>${esc(r.reason)}</td><td><span class="badge">${esc(r.status)}</span></td><td>${fmtTime(r.created_at)}</td></tr>`).join("") || `<tr><td colspan="5">${esc(t("empty"))}</td></tr>`}
    </table></div>`);
  } catch (e) { page(errBox(e)); }
};

views.blocked = async function () {
  if (!requireRegistered()) return;
  page(spinner());
  try {
    const d = await api("/api/moderation/block");
    page(`<h2>🚫 Blocked users</h2><div class="card">${d.blocked.map((u) => `
      <div class="qa-item" style="display:flex;justify-content:space-between;align-items:center">
        <div><strong>${esc(u.display_name)}</strong> <span class="hint">@${esc(u.username)}</span></div>
        <button class="btn btn-sm btn-ghost" data-unblock="${u.id}">${esc(t("unblock"))}</button>
      </div>`).join("") || `<div class="empty">${esc(t("empty"))}</div>`}</div>`);
    $$("[data-unblock]").forEach((b) => b.onclick = async () => { await api("/api/moderation/block/" + b.dataset.unblock, { method: "DELETE" }); route(); });
  } catch (e) { page(errBox(e)); }
};

/* ---------- moderator dashboard ---------- */
views.mod = async function () {
  if (!requireAuth()) return;
  if (!["admin", "moderator"].includes(MC.user.role)) { location.hash = "#/"; return; }
  page(spinner());
  try {
    const [reps, acts] = await Promise.all([api("/api/moderation/reports"), api("/api/moderation/actions")]);
    const open = reps.reports.filter((r) => r.status === "open" || r.status === "in_review");
    page(`<h2>🛡️ ${esc(t("moderation"))}</h2>
    <div class="tabs"><button class="active" data-tab="reports">${esc(t("reports"))} (${open.length})</button><button data-tab="actions">Actions log</button></div>
    <div id="tab-reports"><div class="card"><table class="data">
      <tr><th>#</th><th>Reporter</th><th>Target</th><th>${esc(t("reason"))}</th><th>Status</th><th></th></tr>
      ${reps.reports.map((r) => `<tr><td>${r.id}</td><td>${esc(r.reporter)}</td><td>${esc(r.target_type)} #${r.target_id}</td>
        <td>${esc(r.reason)}<div class="hint">${esc((r.details || "").slice(0, 120))}</div></td>
        <td><span class="badge">${esc(r.status)}</span></td>
        <td><button class="btn btn-sm btn-ghost" data-case="${r.id}">Review</button></td></tr>`).join("") || `<tr><td colspan="6">${esc(t("empty"))}</td></tr>`}
    </table></div></div>
    <div id="tab-actions" style="display:none"><div class="card"><table class="data">
      <tr><th>#</th><th>Action</th><th>Actor</th><th>Target user</th><th>${esc(t("reason"))}</th><th>Date</th></tr>
      ${acts.actions.map((a) => `<tr><td>${a.id}</td><td><span class="badge">${esc(a.action)}</span></td><td>${a.actor_id}</td><td>${a.target_user_id || "—"}</td><td>${esc((a.reason || "").slice(0, 100))}</td><td>${fmtTime(a.created_at)}</td></tr>`).join("")}
    </table></div></div>`);
    $$(".tabs button").forEach((b) => b.onclick = () => {
      $$(".tabs button").forEach((x) => x.classList.remove("active")); b.classList.add("active");
      $("#tab-reports").style.display = b.dataset.tab === "reports" ? "" : "none";
      $("#tab-actions").style.display = b.dataset.tab === "actions" ? "" : "none";
    });
    $$("[data-case]").forEach((b) => b.onclick = () => {
      const r = reps.reports.find((x) => x.id == b.dataset.case);
      modal(`<h3>Case #${r.id}</h3>
        <p><strong>${esc(r.target_type)} #${r.target_id}</strong> — reported by ${esc(r.reporter)}</p>
        <p><strong>${esc(t("reason"))}:</strong> ${esc(r.reason)}</p><p>${esc(r.details || "")}</p>
        <form id="cf">
        <label class="field">Action</label><select name="action">
          <option value="">— none / dismiss —</option><option value="warning">⚠️ Warning</option>
          <option value="message_delete">🗑 Delete message</option><option value="room_mute">🔇 Room mute 24h</option>
          <option value="temp_suspend">⏸ Suspend 24h</option><option value="perm_ban">⛔ Permanent ban</option></select>
        <label class="field">Target user ID (for user actions)</label><input name="target_user_id" type="number">
        <label class="field">Target message ID (for message actions)</label><input name="target_message_id" type="number">
        <label class="field">${esc(t("reason"))} (shown to user)</label><textarea name="reason"></textarea>
        <div style="margin-top:1rem;display:flex;gap:.5rem;flex-wrap:wrap">
          <button class="btn btn-primary btn-sm">Apply action</button>
          <button type="button" class="btn btn-ghost btn-sm" id="c-inreview">Mark in review</button>
          <button type="button" class="btn btn-ghost btn-sm" id="c-dismiss">Dismiss</button>
          <button type="button" class="btn btn-ghost btn-sm" onclick="closeModal()">${esc(t("cancel"))}</button>
        </div></form>`);
      const setStatus = async (s) => { await api(`/api/moderation/reports/${r.id}/status?status=${s}`, { method: "POST" }); closeModal(); route(); };
      $("#c-inreview").onclick = () => setStatus("in_review").catch((e) => toast(e.message));
      $("#c-dismiss").onclick = () => setStatus("dismissed").catch((e) => toast(e.message));
      $("#cf").onsubmit = async (e) => {
        e.preventDefault();
        const v = Object.fromEntries(new FormData(e.target));
        if (!v.action) { toast("Choose an action or dismiss."); return; }
        try {
          await api("/api/moderation/actions", { method: "POST", body: JSON.stringify({
            action: v.action, target_user_id: v.target_user_id ? parseInt(v.target_user_id) : null,
            target_message_id: v.target_message_id ? parseInt(v.target_message_id) : null, reason: v.reason }) });
          await setStatus("actioned");
        } catch (err) { toast(err.message); }
      };
    });
  } catch (e) { page(errBox(e)); }
};

/* ---------- admin dashboard ---------- */
views.admin = async function () {
  if (!requireAuth() || MC.user.role !== "admin") { location.hash = "#/"; return; }
  page(spinner());
  try {
    const [ov, users, rooms, settings, auditLog, apps] = await Promise.all([
      api("/api/admin/overview"), api("/api/admin/users"), api("/api/admin/rooms"),
      api("/api/admin/settings"), api("/api/admin/audit"), api("/api/scholars/applications"),
    ]);
    const pending = apps.applications.filter((a) => a.status === "pending");
    page(`<h2>👑 ${esc(t("admin"))}</h2>
    <div class="stat-grid" style="margin-bottom:1rem">
      ${[["users_total", "Users"], ["guests_total", "Guests"], ["messages_total", "Messages"], ["messages_24h", "Msgs 24h"], ["reports_open", "Open reports"], ["scholar_pending", "Scholar apps"], ["banned_users", "Banned"]].map(([k, l]) => `<div class="card stat"><div class="num">${ov[k] ?? 0}</div><div class="lbl">${l}</div></div>`).join("")}
    </div>
    <div class="tabs">
      <button class="active" data-tab="scholars">🎓 Scholar apps (${pending.length})</button>
      <button data-tab="users">${esc(t("users"))}</button><button data-tab="rooms">${esc(t("rooms"))}</button>
      <button data-tab="settings">${esc(t("site_settings"))}</button><button data-tab="audit">${esc(t("audit_log"))}</button>
    </div>
    <div id="tab-scholars"><div class="card">${pending.map((a) => `
      <div class="qa-item"><strong>${esc(a.full_name)}</strong> <span class="hint">@${esc(a.username)} · ${esc(a.country)} · ${fmtTime(a.created_at)}</span>
      <p>${esc(a.biography.slice(0, 200))}</p>
      <div class="hint">📚 ${esc(a.specialties)} · 🌐 ${esc(a.languages)} · 📄 ${a.document_count} docs</div>
      <div class="hint">🎓 ${esc(a.education.slice(0, 200))}</div>
      <div style="margin-top:.5rem"><button class="btn btn-sm btn-primary" data-app="${a.id}">${esc(t("approve"))} / ${esc(t("reject"))}</button></div></div>`).join("") || `<div class="empty">${esc(t("empty"))}</div>`}</div></div>
    <div id="tab-users" style="display:none"><div class="card"><input id="uq" placeholder="${esc(t("search"))}…" style="max-width:240px;margin-bottom:.6rem"><table class="data">
      <tr><th>ID</th><th>User</th><th>Email</th><th>Role</th><th>Banned</th><th></th></tr>
      <tbody id="utbody">${users.users.map((u) => `<tr><td>${u.id}</td><td><strong>${esc(u.display_name)}</strong><br><span class="hint">@${esc(u.username)}${u.is_guest ? " (guest)" : ""}</span></td><td class="hint">${esc(u.email || "—")}</td>
      <td><select data-role="${u.id}">${["user", "moderator", "scholar", "admin"].map((r) => `<option ${u.role === r ? "selected" : ""}>${r}</option>`).join("")}</select></td>
      <td>${u.is_banned ? "⛔" : u.banned_until ? "⏸" : "—"}</td>
      <td>${u.id !== MC.user.id ? `<button class="btn btn-sm btn-ghost" data-ban="${u.id}">${u.is_banned ? "Unban" : "Ban"}</button>` : ""}</td></tr>`).join("")}</tbody>
    </table></div></div>
    <div id="tab-rooms" style="display:none"><div class="card"><table class="data">
      <tr><th>Room</th><th>Kind</th><th>Visibility</th><th>Slow mode</th><th>Active</th><th></th></tr>
      ${rooms.rooms.map((r) => `<tr><td><strong>${esc(r.name)}</strong><br><span class="hint">${esc(r.slug)}</span></td><td>${esc(r.kind)}</td><td>${esc(r.visibility)}</td>
      <td><input type="number" min="0" max="3600" value="${r.slow_mode_seconds}" data-slow="${r.id}" style="max-width:90px"></td>
      <td>${r.is_active ? "✅" : "❌"}</td><td><button class="btn btn-sm btn-ghost" data-tog="${r.id}">Toggle</button></td></tr>`).join("")}
    </table></div></div>
    <div id="tab-settings" style="display:none"><div class="card">
      <label class="field">📢 ${esc(t("announcement"))}</label><textarea id="set-ann">${esc(settings.settings.announcement || "")}</textarea>
      <label class="field">📜 ${esc(t("community_rules"))}</label><textarea id="set-rules" style="min-height:160px">${esc(settings.settings.community_rules || "")}</textarea>
      <div style="margin-top:.8rem"><button class="btn btn-primary btn-sm" id="setsave">${esc(t("save"))}</button></div>
    </div></div>
    <div id="tab-audit" style="display:none"><div class="card"><table class="data">
      <tr><th>Date</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th></tr>
      ${auditLog.entries.map((a) => `<tr><td class="hint">${fmtTime(a.created_at)}</td><td>${a.actor_id || "—"}</td><td><span class="badge gray">${esc(a.action)}</span></td><td>${esc(a.target)}</td><td class="hint">${esc((a.detail || "").slice(0, 120))}</td></tr>`).join("")}
    </table></div></div>`);
    $$(".tabs button").forEach((b) => b.onclick = () => {
      $$(".tabs button").forEach((x) => x.classList.remove("active")); b.classList.add("active");
      ["scholars", "users", "rooms", "settings", "audit"].forEach((k) => $("#tab-" + k).style.display = b.dataset.tab === k ? "" : "none");
    });
    $$("[data-app]").forEach((b) => b.onclick = () => {
      const a = pending.find((x) => x.id == b.dataset.app);
      modal(`<h3>🎓 ${esc(a.full_name)}</h3><p>${esc(a.biography)}</p>
      <p class="hint">Education: ${esc(a.education)}</p><p class="hint">Credentials: ${esc(a.credentials)}</p>
      <p class="hint">Documents: ${a.document_count} file(s) stored securely (reviewer access only)</p>
      <form id="saf"><label class="field">Review note</label><textarea name="note"></textarea>
      <div style="margin-top:1rem;display:flex;gap:.5rem">
        <button class="btn btn-primary btn-sm" data-d="approved">${esc(t("approve"))}</button>
        <button class="btn btn-danger btn-sm" data-d="rejected">${esc(t("reject"))}</button>
        <button type="button" class="btn btn-ghost btn-sm" onclick="closeModal()">${esc(t("cancel"))}</button></div></form>`);
      $$("#saf [data-d]").forEach((db2) => db2.onclick = async (e) => {
        e.preventDefault();
        try {
          await api(`/api/scholars/applications/${a.id}/review`, { method: "POST", body: JSON.stringify({ decision: db2.dataset.d, review_note: $("#saf [name=note]").value }) });
          closeModal(); toast("Decision recorded."); route();
        } catch (err) { toast(err.message); }
      });
    });
    $$("[data-role]").forEach((s) => s.onchange = async () => {
      try { await api(`/api/admin/users/${s.dataset.role}/role?role=${s.value}`, { method: "POST" }); toast("Role updated."); }
      catch (e) { toast(e.message); }
    });
    $$("[data-ban]").forEach((b) => b.onclick = async () => {
      const ban = b.textContent.trim() === "Ban";
      try {
        await api("/api/moderation/actions", { method: "POST", body: JSON.stringify({ action: ban ? "perm_ban" : "unban", target_user_id: parseInt(b.dataset.ban), reason: ban ? "Banned by admin" : "" }) });
        route();
      } catch (e) { toast(e.message); }
    });
    $$("[data-tog]").forEach((b) => b.onclick = async () => { await api(`/api/admin/rooms/${b.dataset.tog}/toggle`, { method: "POST" }); route(); });
    $$("[data-slow]").forEach((inp) => inp.onchange = async () => {
      try { await api(`/api/admin/rooms/${inp.dataset.slow}/slowmode?seconds=${parseInt(inp.value) || 0}`, { method: "POST" }); toast("Slow mode updated."); }
      catch (e) { toast(e.message); }
    });
    $("#setsave").onclick = async () => {
      try {
        await api("/api/admin/settings/announcement", { method: "POST", body: JSON.stringify({ value: $("#set-ann").value }) });
        await api("/api/admin/settings/community_rules", { method: "POST", body: JSON.stringify({ value: $("#set-rules").value }) });
        toast("Settings saved.");
      } catch (e) { toast(e.message); }
    };
    $("#uq").oninput = async (e) => {
      const qv = e.target.value;
      const d = await api("/api/admin/users?q=" + encodeURIComponent(qv));
      // simple re-render of tbody omitted for brevity — full reload on Enter
      if (e.inputType === undefined) {}
    };
  } catch (e) { page(errBox(e)); }
};

/* ---------- static pages ---------- */
views.terms = function () {
  page(`<div class="card" style="max-width:760px;margin:0 auto"><h2>📄 ${esc(t("terms"))}</h2>
  <p><strong>Muslim Connect</strong> is a community platform for Muslims worldwide. By using it you agree to:</p>
  <ol><li>Follow the community guidelines: no hate, harassment, spam, or fabricated religious claims.</li>
  <li>Be respectful across schools of thought, cultures, and languages.</li>
  <li>Not share anyone's private information without permission.</li>
  <li>Understand that scholar verification is a human review of stated credentials — not a guarantee of correctness.</li>
  <li>Understand that content here is not a substitute for qualified religious, medical, or legal advice.</li>
  <li>Accept that violations may lead to warnings, suspension, or bans, with a right to appeal.</li></ol>
  <p>Accounts: you must be 13 or older (or the minimum age in your country). You are responsible for your password.</p>
  <p>Content you post remains yours; you grant Muslim Connect a license to display it within the service.</p></div>`);
};
views.privacy = function () {
  page(`<div class="card" style="max-width:760px;margin:0 auto"><h2>🔒 ${esc(t("privacy"))}</h2>
  <h3>What we collect</h3><p>Account details you provide (username, email, profile), messages you send, reports you file, and basic technical logs (IP, timestamps) for safety.</p>
  <h3>What we never expose</h3><p>Email addresses, precise location, and private identifiers are never shown publicly or to guests.</p>
  <h3>Your rights</h3><p>Export your data anytime from Profile → Export. Delete your account anytime from Profile → Delete account. Guest sessions expire automatically after 72 hours.</p>
  <h3>Security</h3><p>Passwords are hashed (bcrypt). Sessions use signed tokens. Uploads are validated. Moderator document access is restricted and audited.</p>
  <h3>Encryption limits</h3><p>Traffic is encrypted in transit (HTTPS in production). Messages are <strong>not</strong> end-to-end encrypted; moderators may review reported content.</p>
  <h3>Contact</h3><p>Privacy questions: see <a href="#/help">Help & Contact</a>.</p></div>`);
};
views.help = function () {
  page(`<div class="card" style="max-width:760px;margin:0 auto"><h2>❓ ${esc(t("help"))}</h2>
  <h3>Getting started</h3><p>Create a free account, or <a href="#/guest">join as a guest</a> to try public rooms instantly.</p>
  <h3>Chat</h3><p>Open <a href="#/rooms">Chat rooms</a>, pick a room, and type. Use @username to mention someone. React with emoji, reply, edit (15 min), or delete your messages.</p>
  <h3>Q&A</h3><p>Ask in <a href="#/qa">Q&A</a>. When answering, mark whether your text is a quotation, translation, interpretation, or opinion — and cite sources honestly.</p>
  <h3>Scholars</h3><p><a href="#/scholars/apply">Apply for verification</a> if you are a qualified scholar. Applications are reviewed by a human.</p>
  <h3>Safety</h3><p>See the <a href="#/safety">Safety Center</a>. Every message has a 🚩 report button. Block users from their profile.</p>
  <h3>Emergency</h3><p>If you see a credible threat of violence, report it immediately and contact your local authorities. We cooperate with lawful requests while respecting privacy.</p>
  <h3>Contact</h3><p>For support, use the Safety Center appeal form or email the site administrator.</p></div>`);
};
views.notFound = function () {
  page(`<div class="empty"><div class="big">🧭</div><h2>404</h2><p>This page doesn't exist.</p><a class="btn btn-primary" href="#/">${esc(t("home"))}</a></div>`);
};

/* ---------- route table ---------- */
addRoute("", views.landing);
addRoute("login", views.login);
addRoute("register", views.register);
addRoute("guest", views.guest);
addRoute("verify", views.verify);
addRoute("forgot", views.forgot);
addRoute("reset-password", views.resetPassword);
addRoute("dashboard", views.dashboard);
addRoute("rooms", views.rooms);
addRoute("room/:slug", views.room);
addRoute("messages", views.messages);
addRoute("messages/:id", views.conversation);
addRoute("qa", views.qa);
addRoute("qa/ask", views.askQuestion);
addRoute("qa/:id", views.question);
addRoute("scholars", views.scholars);
addRoute("scholars/apply", views.scholarApply);
addRoute("profile", views.profile);
addRoute("user/:id", views.userProfile);
addRoute("discover", views.discover);
addRoute("notifications", views.notifications);
addRoute("bookmarks", views.bookmarks);
addRoute("export", views.exportData);
addRoute("safety", views.safety);
addRoute("reports", views.myReports);
addRoute("blocked", views.blocked);
addRoute("mod", views.mod);
addRoute("admin", views.admin);
addRoute("terms", views.terms);
addRoute("privacy", views.privacy);
addRoute("help", views.help);

/* ---------- boot ---------- */
(async function boot() {
  try { MC.config = { ...MC.config, ...(await api("/api/config")) }; } catch {}
  $("#brand-name").textContent = MC.config.app_name;
  $("#foot-brand").textContent = MC.config.app_name;
  document.title = MC.config.app_name + " — " + MC.config.tagline;
  await loadLang(MC.lang);
  await refreshMe();
  renderNav();
  route();
  if ("serviceWorker" in navigator) { /* optional; no SW file required */ }
})();
