"use strict";
// Bissfest Admin-Dashboard – nutzt die normale Bissfest-Anmeldung, Zugriff nur für ADMIN_USERS.

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 0) => Number(n).toLocaleString("de-DE", { maximumFractionDigits: d, minimumFractionDigits: d });
const fmtDate = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" }) : "–");
const fmtDateTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–");
const fmtBytes = (b) => (b > 1e9 ? `${fmt(b / 1e9, 1)} GB` : b > 1e6 ? `${fmt(b / 1e6, 1)} MB` : `${fmt(b / 1e3)} KB`);
const state = { feedback: [], feedbackFilter: "offen", foods: [] };

function ago(ts) {
  if (!ts) return "nie";
  const d = (Date.now() / 1000 - ts) / 86400;
  if (d < 1 / 24) return "gerade eben";
  if (d < 1) return `vor ${Math.round(d * 24)} Std.`;
  if (d < 2) return "gestern";
  return `vor ${Math.round(d)} Tagen`;
}

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail : "Fehler " + res.status);
    err.status = res.status;
    throw err;
  }
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), 2500);
}

function denied(title, text) {
  $$(".admin-tab").forEach((s) => s.classList.add("hidden"));
  $("#admin-nav").classList.add("hidden");
  $("#denied-title").textContent = title;
  $("#denied-text").textContent = text;
  $("#admin-denied").classList.remove("hidden");
}

/* ------------------------------------------------------------ Übersicht */

const kpi = (value, label, cls = "") => `<div class="kpi ${cls}"><strong>${value}</strong><span>${label}</span></div>`;

async function loadOverview() {
  const o = await api("/admin/overview");
  $("#kpis").innerHTML =
    kpi(fmt(o.users), `Nutzer · ${fmt(o.users_verified)} bestätigt`) +
    kpi(fmt(o.users_active_7d), "aktiv (7 Tage)") +
    kpi(fmt(o.users_new_7d), "neu (7 Tage)") +
    kpi(fmt(o.entries_today), `Einträge heute · ${fmt(o.entries_total)} gesamt`) +
    kpi(fmt(o.feedback_open), `offenes Feedback · ${fmt(o.bugs_open)} Bugs`, o.bugs_open ? "warn" : "") +
    kpi(fmt(o.custom_foods), "Lebensmittel in der Bissfest-DB") +
    kpi(fmt(o.templates), "gespeicherte Gerichte") +
    kpi(`${fmt(o.push_devices)} / ${fmt(o.api_tokens)}`, "Push-Geräte / API-Tokens");
  const badge = $("#badge-feedback");
  badge.textContent = o.feedback_open;
  badge.classList.toggle("hidden", !o.feedback_open);
  await loadActivity();
}

async function loadActivity() {
  const days = +$("#activity-range").value;
  const { rows } = await api("/admin/activity?days=" + days);
  const byDay = Object.fromEntries(rows.map((r) => [r.day, r]));
  const list = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    list.push({ day: iso, entries: byDay[iso]?.entries || 0, users: byDay[iso]?.users || 0 });
  }
  const W = 720, H = 200, P = { l: 34, r: 34, t: 10, b: 22 };
  const maxE = Math.max(5, ...list.map((r) => r.entries)), maxU = Math.max(2, ...list.map((r) => r.users));
  const bw = (W - P.l - P.r) / days;
  const Y = (v, m) => P.t + (1 - v / m) * (H - P.t - P.b);
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const v = (maxE * i) / 4;
    g += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${Y(v, maxE)}" y2="${Y(v, maxE)}"/><text x="${P.l - 4}" y="${Y(v, maxE) + 3}" text-anchor="end">${fmt(v)}</text>`;
  }
  g += `<text x="${W - P.r + 4}" y="${Y(maxU, maxU) + 3}">${maxU} N.</text>`;
  list.forEach((r, i) => {
    if (r.entries) g += `<rect class="barr" x="${P.l + i * bw + bw * 0.15}" y="${Y(r.entries, maxE)}" width="${bw * 0.7}" height="${Y(0, maxE) - Y(r.entries, maxE)}" rx="2"><title>${r.day}: ${r.entries} Einträge, ${r.users} Nutzer</title></rect>`;
  });
  g += `<path class="line users" d="${list.map((r, i) => `${i ? "L" : "M"}${(P.l + i * bw + bw / 2).toFixed(1)},${Y(r.users, maxU).toFixed(1)}`).join("")}"/>`;
  const label = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("de-DE", { day: "numeric", month: "numeric" });
  g += `<text x="${P.l}" y="${H - 6}">${label(list[0].day)}</text><text x="${W - P.r}" y="${H - 6}" text-anchor="end">${label(list[list.length - 1].day)}</text>`;
  $("#activity-chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}">${g}</svg>`;
}

/* ------------------------------------------------------------ Nutzer */

async function loadUsers() {
  const users = await api("/admin/users");
  $("#users-count").textContent = `${users.length} Konten`;
  $("#users-table").innerHTML = `<thead><tr><th>Nutzer</th><th>E-Mail</th><th>Registriert</th><th>Zuletzt aktiv</th>
    <th class="num">Einträge</th><th class="num">Push / Tokens</th><th></th></tr></thead><tbody>` +
    users.map((u) => `<tr data-id="${u.id}" data-name="${esc(u.username)}">
      <td><strong>${esc(u.username)}</strong>${u.is_admin ? ' <span class="pill">Admin</span>' : ""}</td>
      <td>${esc(u.email || "–")} ${u.email_verified ? "" : '<span class="pill warn">unbestätigt</span>'}</td>
      <td>${fmtDate(u.created)}</td>
      <td title="${fmtDateTime(u.last_active)}">${ago(u.last_active)}</td>
      <td class="num">${fmt(u.entries)}</td>
      <td class="num">${u.push_devices} / ${u.api_tokens}</td>
      <td class="actions">
        ${u.email_verified ? "" : `<button class="btn small" data-act="resend">Mail erneut</button><button class="btn small" data-act="verify">Bestätigen</button>`}
        ${u.is_admin ? "" : `<button class="btn small danger" data-act="delete">Löschen</button>`}
      </td></tr>`).join("") + "</tbody>";
  $$("#users-table [data-act]").forEach((b) => (b.onclick = () => userAction(b.dataset.act, b.closest("tr"))));
}

async function userAction(act, row) {
  const id = row.dataset.id, name = row.dataset.name;
  try {
    if (act === "delete") {
      const typed = prompt(`Konto „${name}“ mit ALLEN Einträgen endgültig löschen?\nZur Bestätigung den Benutzernamen eintippen:`);
      if (typed !== name) return typed !== null && toast("Name stimmt nicht – nichts gelöscht");
      await api(`/admin/users/${id}`, { method: "DELETE" });
      toast(`Konto „${name}“ gelöscht`);
    } else if (act === "verify") {
      await api(`/admin/users/${id}/verify`, { method: "POST" });
      toast(`„${name}“ manuell bestätigt`);
    } else if (act === "resend") {
      await api(`/admin/users/${id}/resend`, { method: "POST" });
      toast(`Bestätigungsmail an „${name}“ verschickt`);
    }
    loadUsers();
  } catch (err) { toast(err.message); }
}

/* ------------------------------------------------------------ Feedback */

async function loadFeedback() {
  state.feedback = await api("/admin/feedback");
  renderFeedback();
}

function renderFeedback() {
  const f = state.feedbackFilter;
  const items = state.feedback.filter((x) => f === "alle" || (f === "offen" ? x.status === "offen" : x.kind === f));
  const open = state.feedback.filter((x) => x.status === "offen").length;
  $("#badge-feedback").textContent = open;
  $("#badge-feedback").classList.toggle("hidden", !open);
  $("#feedback-list").innerHTML = items.length ? items.map((x) => `
    <article class="fb ${x.status === "erledigt" ? "done" : ""}" data-id="${x.id}">
      <header>
        <span class="pill ${x.kind === "bug" ? "warn" : ""}">${x.kind === "bug" ? "🐞 Bug" : "💡 Idee"} #${x.id}</span>
        <strong>${esc(x.username || "gelöschtes Konto")}</strong>
        <span class="muted tiny">${fmtDateTime(x.created)}${x.mailed ? "" : " · ⚠️ Mail nicht gesendet"}</span>
        <span class="pill ${x.status === "erledigt" ? "ok" : ""}">${x.status}</span>
      </header>
      <p class="fb-text">${esc(x.text)}</p>
      ${x.has_screenshot ? `<a href="/api/admin/feedback/${x.id}/screenshot" target="_blank" rel="noopener"><img class="fb-shot" loading="lazy" src="/api/admin/feedback/${x.id}/screenshot" alt="Screenshot zu #${x.id}"></a>` : ""}
      ${x.meta ? `<details><summary class="muted tiny">Technische Angaben</summary><pre class="bug-meta">${esc(Object.entries(x.meta).map(([k, v]) => `${k}: ${v}`).join("\n"))}</pre></details>` : ""}
      <div class="fb-actions">
        <button class="btn small ${x.status === "offen" ? "primary" : ""}" data-status="${x.status === "offen" ? "erledigt" : "offen"}">${x.status === "offen" ? "✓ Erledigt" : "Wieder öffnen"}</button>
        ${x.email ? `<a class="btn small" href="mailto:${esc(x.email)}?subject=${encodeURIComponent(`Re: Bissfest ${x.kind === "bug" ? "Bug Report" : "Feature Request"} #${x.id}`)}">Antworten</a>` : ""}
      </div>
    </article>`).join("") : `<p class="empty">Nichts in dieser Ansicht 🎉</p>`;
  $$("#feedback-list [data-status]").forEach((b) => (b.onclick = async () => {
    const id = +b.closest(".fb").dataset.id;
    try {
      await api(`/admin/feedback/${id}`, { method: "PATCH", body: { status: b.dataset.status } });
      state.feedback.find((x) => x.id === id).status = b.dataset.status;
      renderFeedback();
    } catch (err) { toast(err.message); }
  }));
}

/* ------------------------------------------------------------ Lebensmittel */

async function loadFoods() {
  state.foods = await api("/admin/foods");
  renderFoods();
}

function renderFoods() {
  const q = $("#foods-search").value.trim().toLowerCase();
  const items = state.foods.filter((f) => !q || [f.name, f.brand, f.manufacturer, f.code, f.created_by].join(" ").toLowerCase().includes(q));
  $("#foods-count").textContent = `${items.length} von ${state.foods.length}`;
  $("#foods-table").innerHTML = `<thead><tr><th>Lebensmittel</th><th>Marke · Hersteller</th><th>Barcode</th>
    <th class="num">kcal/100 g</th><th class="num">genutzt</th><th>Angelegt</th><th></th></tr></thead><tbody>` +
    items.map((f) => `<tr data-id="${f.id}" data-name="${esc(f.name)}">
      <td><strong>${esc(f.name)}</strong></td>
      <td>${esc([f.brand, f.manufacturer].filter(Boolean).join(" · ") || "–")}</td>
      <td>${esc(f.code || "–")}</td>
      <td class="num">${fmt(f.kcal_100g)}</td>
      <td class="num">${fmt(f.uses)}×</td>
      <td>${esc(f.created_by || "–")}<div class="muted tiny">${fmtDate(f.created)}</div></td>
      <td class="actions"><button class="btn small danger" data-del>Löschen</button></td></tr>`).join("") + "</tbody>";
  $$("#foods-table [data-del]").forEach((b) => (b.onclick = async () => {
    const row = b.closest("tr");
    if (!confirm(`„${row.dataset.name}“ aus der Bissfest-Datenbank löschen?\nBereits eingetragene Mahlzeiten bleiben erhalten.`)) return;
    try {
      await api(`/admin/foods/${row.dataset.id}`, { method: "DELETE" });
      toast("Gelöscht");
      loadFoods();
    } catch (err) { toast(err.message); }
  }));
}

/* ------------------------------------------------------------ System */

function renderSystem(s) {
  const r = s.registration;
  $("#reg-switch").checked = r.switch;
  $("#reg-hint").textContent = !r.configured ? "In der Server-Konfiguration gesperrt (ALLOW_REGISTRATION)."
    : !r.mail ? "Zugelassen, aber der Mailversand ist nicht eingerichtet – Registrierung daher zu."
      : r.open ? "Aktuell offen: jeder mit der Adresse kann sich (mit E-Mail-Bestätigung) registrieren." : "Aktuell gesperrt.";
  $("#reg-switch").disabled = !r.configured;
  const yes = (v) => (v ? "✅" : "❌");
  const rows = [
    ["Version", s.version],
    ["Admins", s.admins.join(", ")],
    ["Letztes Backup", s.backup.last || "noch keins / nicht lesbar"],
    ["Backup-Ziel", `${yes(s.backup.target_ok)} ${s.backup.target_ok ? "erreichbar" : "nicht gemountet!"} · täglich ${s.backup.time || "aus"} · ${s.backup.keep_days} Tage aufbewahrt`],
    ["Datenbank", fmtBytes(s.db_bytes)],
    ["Speicher frei", `${fmtBytes(s.disk_free_bytes)} von ${fmtBytes(s.disk_total_bytes)}`],
    ["Mailversand", `${yes(r.mail)} ${esc(s.mail_from || "nicht eingerichtet")}`],
    ["Feedback an", esc(s.feedback_to || "–")],
    ["Push-Schlüssel", yes(s.push_configured)],
    ["Android-APK", s.android_apk ? "✅ eigene Datei hinterlegt" : "offizielle APK (Download von bissfest.facgooder.de)"],
  ];
  $("#sysinfo").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

async function loadSystem() {
  renderSystem(await api("/admin/system"));
}

/* ------------------------------------------------------------ Start */

const LOADERS = { overview: loadOverview, users: loadUsers, feedback: loadFeedback, foods: loadFoods, system: loadSystem };

function showTab(tab) {
  $$("#admin-nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".admin-tab").forEach((s) => s.classList.toggle("hidden", s.id !== "tab-" + tab));
  history.replaceState(null, "", tab === "overview" ? "/admin" : "/admin#" + tab);
  LOADERS[tab]().catch((err) => toast(err.message));
}

(async function main() {
  $$("#admin-nav button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  $("#activity-range").onchange = () => loadActivity().catch((err) => toast(err.message));
  $$("#feedback-filter button").forEach((b) => (b.onclick = () => {
    state.feedbackFilter = b.dataset.f;
    $$("#feedback-filter button").forEach((x) => x.classList.toggle("active", x === b));
    renderFeedback();
  }));
  $("#foods-search").oninput = renderFoods;
  $("#reg-switch").onchange = async () => {
    try {
      renderSystem(await api("/admin/registration", { method: "PUT", body: { open: $("#reg-switch").checked } }));
      toast($("#reg-switch").checked ? "Registrierung geöffnet" : "Registrierung gesperrt");
    } catch (err) { toast(err.message); loadSystem(); }
  };
  try {
    await api("/admin/overview");
  } catch (err) {
    if (err.status === 401) return denied("Bitte anmelden", "Melde dich zuerst in Bissfest an und öffne dann /admin erneut.");
    if (err.status === 403) return denied("Kein Zugriff", "Dieses Konto ist kein Admin. Admins werden in der Server-Konfiguration (ADMIN_USERS) festgelegt.");
    return denied("Fehler", err.message);
  }
  const tab = location.hash.slice(1);
  showTab(LOADERS[tab] ? tab : "overview");
})();
