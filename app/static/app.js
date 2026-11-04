"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 0) => Number(n).toLocaleString("de-DE", { maximumFractionDigits: d, minimumFractionDigits: d });

const state = { user: null, day: localISO(new Date()), data: null, pick: null, editId: null, meal: 0, registerMode: false };

function localISO(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function shiftDay(iso, n) {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + n);
  return localISO(d);
}

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401 && !path.startsWith("/login")) {
    showAuth();
    throw new Error("Nicht angemeldet");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    let msg = data.detail;
    if (Array.isArray(msg)) msg = "Bitte Eingaben prüfen";
    const err = new Error(msg || "Fehler " + res.status);
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

/* ------------------------------------------------------------ Energiebedarf */

const KCAL_PER_KG = 7700; // ≈ Energiegehalt von 1 kg Körperfett

// Grundumsatz nach Mifflin-St Jeor, Gesamtumsatz = Grundumsatz × Aktivitätsfaktor.
// Das Defizit wird auf 25 % des Gesamtumsatzes und eine Mindestzufuhr begrenzt.
function energy(p, kg = p.latest_weight) {
  if (!p.sex || !p.birth_year || !p.height_cm || !p.activity || !kg) return null;
  const age = new Date().getFullYear() - p.birth_year;
  const bmr = 10 * kg + 6.25 * p.height_cm - 5 * age + (p.sex === "m" ? 5 : -161);
  const tdee = bmr * p.activity;
  const wanted = ((p.weekly_loss ?? 0.5) * KCAL_PER_KG) / 7;
  const minIntake = p.sex === "m" ? 1500 : 1200;
  let goal = tdee - wanted, limit = null;
  if (goal < tdee * 0.75) { goal = tdee * 0.75; limit = "max. 25 % unter dem Gesamtumsatz"; }
  if (goal < minIntake) { goal = Math.min(minIntake, tdee); limit = `nicht unter ${fmt(minIntake)} kcal`; }
  goal = Math.round(goal / 10) * 10;
  return { age, kg, bmr, tdee, wanted, goal, deficit: tdee - goal, limit, perWeek: ((tdee - goal) * 7) / KCAL_PER_KG };
}

/* ------------------------------------------------------------ Anmeldung */

function showAuth() {
  $("#app").classList.add("hidden");
  $("#auth").classList.remove("hidden");
}

// Copyright-Hinweis (Jahr läuft automatisch mit: 2026, ab 2027 „2026–2027“ usw.)
const COPYRIGHT_OWNER = "Sebastian Frie";
function fillCopyright() {
  const year = new Date().getFullYear();
  const text = `© ${year > 2026 ? `2026–${year}` : 2026} ${COPYRIGHT_OWNER}`;
  $$("[data-copyright]").forEach((el) => (el.textContent = text));
}

async function initAuth() {
  fillCopyright();
  const cfg = await api("/config").catch(() => ({ allow_registration: true }));
  if (cfg.version) {
    $("#app-version").textContent = `Bissfest v${cfg.version} · Mit Biss zum Wunschgewicht`;
    $("#settings-version").textContent = `Bissfest – Version ${cfg.version}`;
  }
  if (!cfg.allow_registration) $("#auth-toggle").classList.add("hidden");
  $("#auth-toggle").onclick = () => {
    state.registerMode = !state.registerMode;
    $("#auth-email").classList.toggle("hidden", !state.registerMode);
    $("#auth-email").required = state.registerMode;
    hideNotice();
    $("#auth-submit").textContent = state.registerMode ? "Konto erstellen" : "Anmelden";
    $("#auth-toggle").textContent = state.registerMode ? "Schon ein Konto? Anmelden" : "Noch kein Konto? Registrieren";
    $("#auth-pass").autocomplete = state.registerMode ? "new-password" : "current-password";
    $("#auth-err").textContent = "";
  };
  const creds = () => ({ username: $("#auth-user").value.trim(), password: $("#auth-pass").value });
  $("#auth-form").onsubmit = async (e) => {
    e.preventDefault();
    $("#auth-err").textContent = "";
    hideNotice();
    try {
      if (state.registerMode) {
        const r = await api("/register", { method: "POST", body: { ...creds(), email: $("#auth-email").value.trim() } });
        $("#auth-toggle").click(); // zurück zur Anmeldung – nach dem Bestätigen geht es direkt los
        showNotice("📬 Fast geschafft!", `Wir haben dir eine Mail an ${r.email} geschickt. Klicke auf den Link darin, um dein Konto zu bestätigen – danach bist du direkt angemeldet. Schau auch im Spam-Ordner nach.`, true);
        return;
      }
      await api("/login", { method: "POST", body: creds() });
      $("#auth-pass").value = "";
      await startApp();
    } catch (err) {
      if (err.status === 403 && !state.registerMode) showNotice("📬 E-Mail noch nicht bestätigt", err.message, true);
      else $("#auth-err").textContent = err.message;
    }
  };
  $("#auth-resend").onclick = async () => {
    if (!$("#auth-user").value.trim() || !$("#auth-pass").value) {
      $("#auth-err").textContent = "Bitte Benutzername und Passwort eingeben, dann erneut senden.";
      return;
    }
    try {
      const r = await api("/resend-verification", { method: "POST", body: creds() });
      if (r.already) showNotice("✅ Schon bestätigt", "Deine E-Mail-Adresse ist bereits bestätigt – du kannst dich anmelden.", false);
      else showNotice("📬 Neue Mail ist unterwegs", `Wir haben den Bestätigungslink erneut an ${r.email} geschickt.`, true);
    } catch (err) { $("#auth-err").textContent = err.message; }
  };
  // Rückkehr vom Bestätigungslink (/verify leitet auf /?verified=1 bzw. 0 um)
  const verified = new URLSearchParams(location.search).get("verified");
  if (verified !== null) {
    history.replaceState(null, "", "/");
    if (verified === "1") setTimeout(() => toast("✅ E-Mail bestätigt – willkommen bei Bissfest!"), 600);
    else showNotice("⚠️ Link ungültig", "Der Bestätigungslink ist abgelaufen oder wurde schon benutzt. Melde dich an oder lass dir eine neue Mail schicken.", true);
  }
}

function showNotice(title, text, resend) {
  $("#auth-notice-title").textContent = title;
  $("#auth-notice-text").textContent = text;
  $("#auth-resend").classList.toggle("hidden", !resend);
  $("#auth-notice").classList.remove("hidden");
}

function hideNotice() {
  $("#auth-notice").classList.add("hidden");
}

async function startApp() {
  state.user = await api("/me");
  $("#auth").classList.add("hidden");
  $("#app").classList.remove("hidden");
  fillSettings();
  refreshPush();
  await loadDay();
}

/* ------------------------------------------------------------ Tagesansicht */

function dayTitle(iso) {
  const today = localISO(new Date());
  if (iso === today) return "Heute";
  if (iso === shiftDay(today, -1)) return "Gestern";
  if (iso === shiftDay(today, 1)) return "Morgen";
  return new Date(iso + "T12:00:00").toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

async function loadDay() {
  $("#day-label").textContent = dayTitle(state.day);
  state.data = await api("/day/" + state.day);
  renderDay();
  loadWeightDiff();
}

function renderDay() {
  const d = state.data, u = state.user;
  const eaten = Math.round(d.totals.kcal);
  const left = u.kcal_goal - eaten;
  $("#kcal-eaten").textContent = fmt(eaten);
  $("#kcal-goal").textContent = fmt(u.kcal_goal);
  $("#kcal-left").textContent = fmt(Math.abs(left));
  $("#kcal-left-label").textContent = left >= 0 ? "kcal übrig" : "kcal zu viel";
  const ring = $("#kcal-ring");
  const frac = Math.min(eaten / u.kcal_goal, 1);
  ring.style.strokeDashoffset = 326.7 * (1 - frac);
  ring.classList.toggle("over", left < 0);
  const e = energy(u);
  $("#deficit-row").classList.toggle("hidden", !e);
  if (e) {
    const def = Math.round(e.tdee - eaten);
    $("#deficit-row .muted").textContent = def >= 0 ? "Defizit" : "Überschuss";
    $("#kcal-deficit").textContent = fmt(Math.abs(def));
    $("#kcal-deficit").className = def >= 0 ? "pos" : "neg";
  }
  $("#macros").innerHTML = `<span>E ${fmt(d.totals.protein)} g</span><span>KH ${fmt(d.totals.carbs)} g</span><span>Zucker ${fmt(d.totals.sugar)} g</span><span>F ${fmt(d.totals.fat)} g</span>`;

  $("#water-text").textContent = `${fmt(d.water_ml / 1000, 2)} / ${fmt(u.water_goal_ml / 1000, 1)} l`;
  $("#water-bar").style.width = Math.min(100, (d.water_ml / u.water_goal_ml) * 100) + "%";

  const wi = $("#weight-input");
  if (document.activeElement !== wi) wi.value = d.weight ?? "";

  const meals = $("#meals");
  meals.innerHTML = "";
  u.meal_names.forEach((name, i) => {
    // Einträge aus Mahlzeiten, die nach Reduzieren der Anzahl wegfallen, landen in der letzten
    const items = d.foods.filter((f) => Math.min(f.meal, u.meal_names.length - 1) === i);
    const kcal = items.reduce((s, f) => s + f.kcal, 0);
    const card = document.createElement("div");
    card.className = "card meal";
    card.innerHTML = `
      <div class="card-head"><h2>${esc(name)}</h2><span class="muted">${fmt(kcal)} kcal</span></div>
      <ul>${items.map((f) => `
        <li data-id="${f.id}">
          <div><div class="n">${esc(f.name)}</div><div class="g">${fmt(f.grams)} g${f.brand ? " · " + esc(f.brand) : ""}</div></div>
          <div class="k">${fmt(f.kcal)} kcal</div>
        </li>`).join("")}</ul>
      <div class="btn-row">
        <button class="btn add-btn">+ Lebensmittel</button>
        ${items.length ? `<button class="btn tpl-btn" title="Diese Mahlzeit als Gericht speichern">⭐ Als Gericht</button>` : ""}
      </div>`;
    card.querySelector(".add-btn").onclick = () => openSheet(i);
    const tplBtn = card.querySelector(".tpl-btn");
    if (tplBtn) tplBtn.onclick = () => saveAsTemplate(i, name, items);
    card.querySelectorAll("li").forEach((li) => (li.onclick = () => editEntry(+li.dataset.id)));
    meals.appendChild(card);
  });
}

async function loadWeightDiff() {
  const h = await api("/history?days=365").catch(() => null);
  const el = $("#weight-diff");
  el.textContent = "";
  if (!h || !h.weights.length) return;
  const first = h.weights[0].kg, last = h.weights[h.weights.length - 1].kg;
  const diff = last - first;
  if (h.weights.length > 1) el.textContent = `${diff > 0 ? "+" : ""}${fmt(diff, 1)} kg`;
  if (state.user.target_weight) el.textContent += ` · Ziel ${fmt(state.user.target_weight, 1)}`;
}

function initDay() {
  $("#day-prev").onclick = () => { state.day = shiftDay(state.day, -1); loadDay(); };
  $("#day-next").onclick = () => { state.day = shiftDay(state.day, 1); loadDay(); };
  $("#day-label").onclick = () => {
    const p = $("#day-picker");
    p.value = state.day;
    if (p.showPicker) p.showPicker(); else p.click();
  };
  $("#day-picker").onchange = (e) => { if (e.target.value) { state.day = e.target.value; loadDay(); } };

  $$("[data-water]").forEach((b) => (b.onclick = async () => {
    const r = await api("/water", { method: "POST", body: { day: state.day, ml: +b.dataset.water } });
    state.data.water_ml = r.water_ml;
    renderDay();
  }));

  $("#weight-form").onsubmit = async (e) => {
    e.preventDefault();
    const v = $("#weight-input").value.replace(",", ".");
    const kg = v === "" ? null : parseFloat(v);
    try {
      await api("/weight/" + state.day, { method: "PUT", body: { kg } });
      $("#weight-input").blur();
      toast(kg === null ? "Gewicht gelöscht" : `${fmt(kg, 1)} kg gespeichert`);
      state.data.weight = kg;
      state.user = await api("/me");
      fillSettings();
      renderDay();
      loadWeightDiff();
    } catch (err) { toast(err.message); }
  };
}

/* ------------------------------------------------------------ Lebensmittel-Sheet */

function showTab(name) {
  $$("#sheet .tab").forEach((t) => t.classList.add("hidden"));
  $("#tab-" + name).classList.remove("hidden");
  $$("#sheet-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $("#sheet-tabs").classList.toggle("hidden", name === "amount" || name === "template");
  if (name === "recent") loadRecent();
  if (name === "templates") loadTemplates();
  if (name === "search") setTimeout(() => $("#search-q").focus(), 50);
}

function openSheet(meal) {
  state.meal = meal;
  state.editId = null;
  $("#sheet-title").textContent = state.user.meal_names[meal] + " – hinzufügen";
  $("#sheet").classList.remove("hidden");
  document.body.style.overflow = "hidden";
  showTab("search");
}

function closeSheet() {
  $("#sheet").classList.add("hidden");
  document.body.style.overflow = "";
  stopScan();
}

function resultItem(f) {
  const li = document.createElement("li");
  li.innerHTML = `
    <div><div class="n">${esc(f.name)}</div>
      <div class="b">${esc(f.brand || "")}<span class="src">${esc(f.source)}</span></div></div>
    <div class="k"><strong>${fmt(f.kcal_100g)}</strong><br><span class="b">kcal/100 g</span></div>`;
  li.onclick = () => pickFood(f);
  return li;
}

async function doSearch(e) {
  e.preventDefault();
  const q = $("#search-q").value.trim();
  if (q.length < 2) return;
  const list = $("#search-results");
  list.innerHTML = "";
  $("#search-info").textContent = "Suche in Open Food Facts und USDA …";
  try {
    const r = await api("/foods/search?q=" + encodeURIComponent(q));
    $("#search-info").textContent = (r.results.length ? `${r.results.length} Treffer` : "Keine Treffer – probiere einen anderen Begriff oder trage manuell ein.")
      + (r.errors.length ? " · " + r.errors.join(", ") : "");
    r.results.forEach((f) => list.appendChild(resultItem(f)));
  } catch (err) {
    $("#search-info").textContent = err.message;
  }
}

async function loadRecent() {
  const list = $("#recent-results");
  list.innerHTML = "";
  const items = await api("/foods/recent");
  if (!items.length) list.innerHTML = `<li class="empty">Noch keine Einträge</li>`;
  items.forEach((f) => list.appendChild(resultItem(f)));
}

async function lookupBarcode(code) {
  $("#barcode-info").textContent = "Suche Produkt …";
  try {
    const f = await api("/foods/barcode/" + encodeURIComponent(code));
    $("#barcode-info").textContent = "";
    pickFood(f);
  } catch (err) {
    $("#barcode-info").innerHTML = `${esc(err.message)}<br><button type="button" class="link" id="barcode-create">+ Als neues Lebensmittel anlegen</button>`;
    $("#barcode-create").onclick = () => { $("#tab-manual").reset(); $("#m-code").value = code; showTab("manual"); $("#m-name").focus(); };
  }
}

/* ------------------------------------------------------------ Barcode-Scanner */
// Android/Chrome: eingebauter BarcodeDetector. iPhone & Co.: ZXing (lokal unter /static/vendor, wird erst
// beim ersten Scan geladen). Beides braucht HTTPS, sonst gibt der Browser die Kamera nicht frei.

const SCAN_FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e"];
const CAMERA = { video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } } };
let activeScan = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.onload = resolve;
    el.onerror = () => reject(new Error("Scanner konnte nicht geladen werden"));
    document.head.appendChild(el);
  });
}

async function nativeDetector() {
  if (!("BarcodeDetector" in window)) return null;
  try {
    const supported = await BarcodeDetector.getSupportedFormats();
    const formats = SCAN_FORMATS.filter((f) => supported.includes(f));
    return formats.includes("ean_13") ? new BarcodeDetector({ formats }) : null;
  } catch { return null; }
}

function setupTorch(stream) {
  const btn = $("#scanner-torch");
  const track = stream && stream.getVideoTracks()[0];
  const caps = track && track.getCapabilities ? track.getCapabilities() : {};
  btn.classList.toggle("hidden", !caps.torch);
  let on = false;
  btn.onclick = async () => {
    on = !on;
    try { await track.applyConstraints({ advanced: [{ torch: on }] }); } catch {}
    btn.classList.toggle("primary", on);
  };
}

// ZXing-Decoder für Browser ohne BarcodeDetector. Liest nur den mittleren Streifen des Kamerabilds
// (dort sitzt der Rahmen) – das ist schneller und robuster als ZXings eigene Video-Schleife.
async function zxingDecoder(video) {
  if (!window.ZXing) await loadScript("/static/vendor/zxing-0.21.3.min.js");
  const reader = new ZXing.MultiFormatReader();
  reader.setHints(new Map([
    [ZXing.DecodeHintType.POSSIBLE_FORMATS, [ZXing.BarcodeFormat.EAN_13, ZXing.BarcodeFormat.EAN_8,
      ZXing.BarcodeFormat.UPC_A, ZXing.BarcodeFormat.UPC_E]],
    [ZXing.DecodeHintType.TRY_HARDER, true],
  ]));
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  return () => {
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh) return null;
    const sh = Math.round(vh * 0.6), sy = Math.round((vh - sh) / 2);
    canvas.width = vw;
    canvas.height = sh;
    ctx.drawImage(video, 0, sy, vw, sh, 0, 0, vw, sh);
    try {
      const bitmap = new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(new ZXing.HTMLCanvasElementLuminanceSource(canvas)));
      return reader.decode(bitmap).getText();
    } catch { return null; } // kein Barcode in diesem Bild
  };
}

// Öffnet den Vollbild-Scanner und liefert den erkannten Code (oder null bei Abbruch/Fehler)
function scanBarcode() {
  return new Promise(async (resolve) => {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      toast("Die Kamera funktioniert nur über eine HTTPS-Adresse. Tippe die Nummer bitte ein.");
      return resolve(null);
    }
    const overlay = $("#scanner"), video = $("#scanner-video"), hint = $("#scanner-hint");
    let stream = null, done = false;
    const finish = (code) => {
      if (done) return;
      done = true;
      if (stream) stream.getTracks().forEach((t) => t.stop());
      video.srcObject = null;
      overlay.classList.add("hidden");
      activeScan = null;
      if (code && navigator.vibrate) navigator.vibrate(60);
      resolve(code);
    };
    activeScan = { stop: () => finish(null) };
    $("#scanner-cancel").onclick = () => finish(null);
    $("#scanner-torch").classList.add("hidden");
    hint.textContent = "Kamera wird gestartet …";
    overlay.classList.remove("hidden");
    try {
      stream = await navigator.mediaDevices.getUserMedia(CAMERA);
      if (done) return stream.getTracks().forEach((t) => t.stop());
      video.srcObject = stream;
      await video.play();
      const detector = await nativeDetector();
      const decodeFrame = detector
        ? async () => { const codes = await detector.detect(video); return codes.length ? codes[0].rawValue : null; }
        : await zxingDecoder(video);
      if (done) return;
      setupTorch(stream);
      hint.textContent = "Halte den Barcode in den Rahmen";
      const tick = async () => {
        if (done) return;
        let code = null;
        try { code = await decodeFrame(); } catch {}
        if (code) return finish(code);
        setTimeout(tick, 80);
      };
      tick();
    } catch (err) {
      const denied = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
      finish(null);
      toast(denied ? "Kamerazugriff wurde nicht erlaubt – bitte in den Einstellungen freigeben." : "Kamera nicht verfügbar: " + (err.message || err));
    }
  });
}

function stopScan() {
  if (activeScan) activeScan.stop();
}

/* ------------------------------------------------------------ Gerichte (gespeicherte Mahlzeiten) */

const PORTIONS = [[0.5, "½"], [1, "1×"], [1.5, "1½"], [2, "2×"]];

async function saveAsTemplate(meal, mealName, items) {
  const name = prompt(`Name für dieses Gericht (${items.length} Zutat${items.length === 1 ? "" : "en"}):`, `Mein ${mealName}`);
  if (!name || !name.trim()) return;
  try {
    await api("/templates", { method: "POST", body: { name: name.trim(), day: state.day, meal } });
    toast(`Gericht „${name.trim()}“ gespeichert`);
  } catch (err) { toast(err.message); }
}

async function loadTemplates() {
  const list = $("#template-results");
  list.innerHTML = "";
  state.templates = await api("/templates");
  if (!state.templates.length) list.innerHTML = `<li class="empty">Noch keine Gerichte gespeichert</li>`;
  state.templates.forEach((t) => {
    const li = document.createElement("li");
    li.innerHTML = `
      <div><div class="n">⭐ ${esc(t.name)}</div>
        <div class="b">${t.items.map((i) => esc(i.name)).join(", ")}</div></div>
      <div class="k"><strong>${fmt(t.kcal)}</strong><br><span class="b">kcal</span></div>`;
    li.onclick = () => openTemplate(t);
    list.appendChild(li);
  });
}

function openTemplate(t) {
  state.template = t;
  state.factor = 1;
  $("#t-name").textContent = t.name;
  $("#t-meta").textContent = `${t.items.length} Zutat${t.items.length === 1 ? "" : "en"} · ${fmt(t.grams)} g`;
  $("#t-items").innerHTML = t.items.map((i) => `
    <li><div><div class="n">${esc(i.name)}</div><div class="b">${fmt(i.grams)} g${i.brand ? " · " + esc(i.brand) : ""}</div></div>
      <div class="k">${fmt(i.kcal)} kcal</div></li>`).join("");
  $("#t-factors").innerHTML = PORTIONS.map(([f, label]) =>
    `<button type="button" data-f="${f}" class="${f === 1 ? "on" : ""}">${label}</button>`).join("");
  $$("#t-factors button").forEach((b) => (b.onclick = () => {
    state.factor = +b.dataset.f;
    $$("#t-factors button").forEach((x) => x.classList.toggle("on", x === b));
    $("#t-kcal").textContent = fmt(t.kcal * state.factor);
  }));
  $("#t-meal").innerHTML = state.user.meal_names.map((n, i) => `<option value="${i}" ${i === state.meal ? "selected" : ""}>${esc(n)}</option>`).join("");
  $("#t-kcal").textContent = fmt(t.kcal);
  showTab("template");
}

function initTemplates() {
  $("#t-back").onclick = () => showTab("templates");
  $("#t-apply").onclick = async () => {
    try {
      const r = await api(`/templates/${state.template.id}/apply`, {
        method: "POST", body: { day: state.day, meal: +$("#t-meal").value, factor: state.factor },
      });
      closeSheet();
      toast(`${state.template.name}: ${r.added} Zutat${r.added === 1 ? "" : "en"} eingetragen`);
      loadDay();
    } catch (err) { toast(err.message); }
  };
  $("#t-delete").onclick = async () => {
    if (!confirm(`Gericht „${state.template.name}“ löschen? Bereits eingetragene Mahlzeiten bleiben erhalten.`)) return;
    await api("/templates/" + state.template.id, { method: "DELETE" });
    showTab("templates");
  };
}

function fillMealSelect(selected) {
  $("#a-meal").innerHTML = state.user.meal_names.map((n, i) => `<option value="${i}" ${i === selected ? "selected" : ""}>${esc(n)}</option>`).join("");
}

function pickFood(f, grams, isEdit = false) {
  stopScan();
  state.pick = f;
  $("#a-name").textContent = f.name;
  const macros = [f.protein_100g != null && `E ${fmt(f.protein_100g, 1)}`, f.carbs_100g != null && `KH ${fmt(f.carbs_100g, 1)}`,
    f.sugar_100g != null && `Zucker ${fmt(f.sugar_100g, 1)}`, f.fat_100g != null && `F ${fmt(f.fat_100g, 1)}`].filter(Boolean).join(" · ");
  $("#a-meta").textContent = [f.brand, `${fmt(f.kcal_100g)} kcal/100 g`, macros, f.source].filter(Boolean).join(" · ");
  $("#a-grams").value = grams ?? f.serving_g ?? f.grams ?? 100;
  const chips = [50, 100, 150, 200, 250];
  if (f.serving_g && !chips.includes(f.serving_g)) chips.unshift(f.serving_g);
  $("#a-chips").innerHTML = chips.map((g) => `<button type="button" data-g="${g}">${fmt(g)} g${g === f.serving_g ? " (Portion)" : ""}</button>`).join("");
  $$("#a-chips button").forEach((b) => (b.onclick = () => { $("#a-grams").value = b.dataset.g; updatePreview(); }));
  fillMealSelect(state.meal);
  $("#a-save").textContent = isEdit ? "Speichern" : "Eintragen";
  $("#a-delete").classList.toggle("hidden", !isEdit);
  $("#a-back").classList.toggle("hidden", isEdit);
  $("#a-delete-custom").classList.toggle("hidden", isEdit || !(f.custom_id && f.mine));
  state.lastTab = $("#sheet-tabs .active")?.dataset.tab || "search";
  showTab("amount");
  updatePreview();
}

function updatePreview() {
  const g = parseFloat($("#a-grams").value.replace(",", ".")) || 0;
  $("#a-kcal").textContent = fmt((state.pick.kcal_100g * g) / 100);
}

function editEntry(id) {
  const f = state.data.foods.find((x) => x.id === id);
  if (!f) return;
  state.meal = Math.min(f.meal, state.user.meal_names.length - 1);
  state.editId = id;
  $("#sheet-title").textContent = "Eintrag bearbeiten";
  $("#sheet").classList.remove("hidden");
  document.body.style.overflow = "hidden";
  pickFood({ ...f, serving_g: null }, f.grams, true);
}

async function saveAmount(e) {
  e.preventDefault();
  const grams = parseFloat($("#a-grams").value.replace(",", "."));
  const meal = +$("#a-meal").value;
  const f = state.pick;
  try {
    if (state.editId) {
      await api("/food/" + state.editId, { method: "PATCH", body: { grams, meal } });
    } else {
      await api("/food", {
        method: "POST",
        body: {
          day: state.day, meal, grams, name: f.name, brand: f.brand || "", kcal_100g: f.kcal_100g,
          protein_100g: f.protein_100g ?? null, carbs_100g: f.carbs_100g ?? null, sugar_100g: f.sugar_100g ?? null,
          fat_100g: f.fat_100g ?? null,
          source: f.source || "manuell", code: f.code || "",
        },
      });
    }
    closeSheet();
    toast("Gespeichert");
    loadDay();
  } catch (err) { toast(err.message); }
}

function initSheet() {
  $("#sheet-close").onclick = closeSheet;
  $("#sheet").onclick = (e) => { if (e.target.id === "sheet") closeSheet(); };
  $$("#sheet-tabs button").forEach((b) => (b.onclick = () => { stopScan(); showTab(b.dataset.tab); }));
  $("#search-form").onsubmit = doSearch;
  $("#barcode-form").onsubmit = (e) => { e.preventDefault(); lookupBarcode($("#barcode-q").value.trim()); };
  $("#barcode-scan").onclick = async () => {
    const code = await scanBarcode();
    if (!code) return;
    $("#barcode-q").value = code;
    lookupBarcode(code);
  };
  $("#m-code-scan").onclick = async () => {
    const code = await scanBarcode();
    if (code) $("#m-code").value = code;
  };
  $("#tab-manual").onsubmit = async (e) => {
    e.preventDefault();
    $("#m-err").textContent = "";
    const num = (id) => { const v = $(id).value.replace(",", "."); return v === "" ? null : parseFloat(v); };
    try {
      const food = await api("/foods/custom", {
        method: "POST",
        body: {
          name: $("#m-name").value.trim(), manufacturer: $("#m-manufacturer").value.trim(), brand: $("#m-brand").value.trim(),
          code: $("#m-code").value.trim(), kcal_100g: num("#m-kcal"), protein_100g: num("#m-protein"),
          carbs_100g: num("#m-carbs"), sugar_100g: num("#m-sugar"), fat_100g: num("#m-fat"),
        },
      });
      e.target.reset();
      toast("In der Bissfest-Datenbank gespeichert");
      pickFood(food);
    } catch (err) { $("#m-err").textContent = err.message; }
  };
  $("#a-delete-custom").onclick = async () => {
    if (!confirm(`„${state.pick.name}“ für alle Nutzer aus der Bissfest-Datenbank löschen? Bereits eingetragene Mahlzeiten bleiben erhalten.`)) return;
    try {
      await api("/foods/custom/" + state.pick.custom_id, { method: "DELETE" });
      toast("Gelöscht");
      showTab(state.lastTab || "search");
      if (state.lastTab === "search" && $("#search-q").value) $("#search-form").requestSubmit();
    } catch (err) { toast(err.message); }
  };
  initTemplates();
  $("#a-grams").oninput = updatePreview;
  $("#a-back").onclick = () => showTab(state.lastTab || "search");
  $("#tab-amount").onsubmit = saveAmount;
  $("#a-delete").onclick = async () => {
    if (!confirm("Eintrag löschen?")) return;
    await api("/food/" + state.editId, { method: "DELETE" });
    closeSheet();
    loadDay();
  };
}

/* ------------------------------------------------------------ Verlauf */

function lineChart(points, target) {
  if (points.length < 1) return `<p class="empty">Noch keine Gewichtsdaten</p>`;
  const W = 340, H = 180, P = { l: 34, r: 8, t: 10, b: 22 };
  const xs = points.map((p) => new Date(p.day + "T12:00:00").getTime());
  const ys = points.map((p) => p.kg).concat(target ? [target] : []);
  let ymin = Math.min(...ys), ymax = Math.max(...ys);
  if (ymax - ymin < 2) { ymin -= 1; ymax += 1; }
  const pad = (ymax - ymin) * 0.1; ymin -= pad; ymax += pad;
  const xmin = Math.min(...xs), xmax = Math.max(...xs, xmin + 86400000);
  const X = (t) => P.l + ((t - xmin) / (xmax - xmin)) * (W - P.l - P.r);
  const Y = (v) => P.t + (1 - (v - ymin) / (ymax - ymin)) * (H - P.t - P.b);
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const v = ymin + ((ymax - ymin) * i) / 4;
    g += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${P.l - 4}" y="${Y(v) + 3}" text-anchor="end">${fmt(v, 1)}</text>`;
  }
  const label = (t) => new Date(t).toLocaleDateString("de-DE", { day: "numeric", month: "numeric" });
  g += `<text x="${P.l}" y="${H - 6}">${label(xmin)}</text><text x="${W - P.r}" y="${H - 6}" text-anchor="end">${label(xmax)}</text>`;
  if (target) g += `<line class="goal" x1="${P.l}" x2="${W - P.r}" y1="${Y(target)}" y2="${Y(target)}"/>`;
  const path = points.map((p, i) => `${i ? "L" : "M"}${X(xs[i]).toFixed(1)},${Y(p.kg).toFixed(1)}`).join("");
  const dots = points.length <= 60 ? points.map((p, i) => `<circle class="dot" r="2.5" cx="${X(xs[i])}" cy="${Y(p.kg)}"/>`).join("") : "";
  return `<svg viewBox="0 0 ${W} ${H}">${g}<path class="line" d="${path}"/>${dots}</svg>`;
}

function barChart(rows, goal, days) {
  if (!rows.length) return `<p class="empty">Noch keine Mahlzeiten erfasst</p>`;
  const W = 340, H = 160, P = { l: 34, r: 8, t: 10, b: 22 };
  const byDay = Object.fromEntries(rows.map((r) => [r.day, r.kcal]));
  const n = Math.min(days, 60);
  const list = [];
  for (let i = n - 1; i >= 0; i--) { const d = shiftDay(localISO(new Date()), -i); list.push({ day: d, kcal: byDay[d] || 0 }); }
  const ymax = Math.max(goal * 1.3, ...list.map((r) => r.kcal));
  const bw = (W - P.l - P.r) / n;
  const Y = (v) => P.t + (1 - v / ymax) * (H - P.t - P.b);
  let g = "";
  for (let i = 0; i <= 3; i++) {
    const v = (ymax * i) / 3;
    g += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${P.l - 4}" y="${Y(v) + 3}" text-anchor="end">${fmt(v)}</text>`;
  }
  list.forEach((r, i) => {
    if (!r.kcal) return;
    g += `<rect class="barr ${r.kcal > goal ? "over" : ""}" x="${P.l + i * bw + bw * 0.15}" y="${Y(r.kcal)}" width="${bw * 0.7}" height="${Y(0) - Y(r.kcal)}" rx="2"/>`;
  });
  g += `<line class="goal" x1="${P.l}" x2="${W - P.r}" y1="${Y(goal)}" y2="${Y(goal)}"/>`;
  g += `<text x="${P.l}" y="${H - 6}">${n === days ? "" : "letzte "}${n} Tage</text>`;
  return `<svg viewBox="0 0 ${W} ${H}">${g}</svg>`;
}

const stat = (val, label) => `<div><strong>${val}</strong><span>${label}</span></div>`;

async function loadHistory() {
  const days = +$("#hist-range").value;
  const h = await api("/history?days=" + days);
  const u = state.user;
  $("#weight-chart").innerHTML = lineChart(h.weights, u.target_weight);
  const w = h.weights;
  if (w.length) {
    const cur = w[w.length - 1].kg, change = cur - w[0].kg;
    $("#weight-stats").innerHTML =
      stat(`${fmt(cur, 1)} kg`, "aktuell") +
      stat(`${change > 0 ? "+" : ""}${fmt(change, 1)} kg`, "Veränderung") +
      stat(u.target_weight ? `${fmt(Math.max(0, cur - u.target_weight), 1)} kg` : "–", "bis zum Ziel");
  } else $("#weight-stats").innerHTML = "";

  $("#kcal-chart").innerHTML = barChart(h.kcal, u.kcal_goal, days);
  if (h.kcal.length) {
    const avg = h.kcal.reduce((s, r) => s + r.kcal, 0) / h.kcal.length;
    const under = h.kcal.filter((r) => r.kcal <= u.kcal_goal).length;
    const wAvg = h.water.length ? h.water.reduce((s, r) => s + r.ml, 0) / h.water.length : 0;
    $("#kcal-stats").innerHTML =
      stat(fmt(avg), "Ø kcal/Tag") + stat(`${under}/${h.kcal.length}`, "Tage im Ziel") + stat(`${fmt(wAvg / 1000, 1)} l`, "Ø Wasser");
  } else $("#kcal-stats").innerHTML = "";

  // Defizit nur über abgeschlossene Tage (heute ist meist noch nicht fertig erfasst)
  const e = energy(u);
  const today = localISO(new Date());
  const done = h.kcal.filter((r) => r.day < today);
  if (e && done.length) {
    const total = done.reduce((s, r) => s + (e.tdee - r.kcal), 0);
    $("#deficit-stats").innerHTML =
      stat(fmt(total / done.length), "Ø Defizit/Tag") +
      stat(fmt(total), "Defizit gesamt") +
      stat(`${fmt(total / KCAL_PER_KG, 1)} kg`, "≈ Fett verbrannt");
    $("#deficit-hint").textContent = `Basis: Gesamtumsatz ${fmt(e.tdee)} kcal/Tag, ${done.length} erfasste Tage (ohne heute). Schätzwert – Wasser und Verdauung lassen die Waage täglich schwanken.`;
  } else {
    $("#deficit-stats").innerHTML = "";
    $("#deficit-hint").textContent = e ? "" : "Trage in den Einstellungen deine Körperdaten ein, um dein Kaloriendefizit zu sehen.";
  }
}

/* ------------------------------------------------------------ Einstellungen */

// Feste Mahlzeiten (Reihenfolge wie im Backend): je Mahlzeit eine Zeile mit Uhrzeit für die Push-Erinnerung
function renderMealNameInputs(count, times = []) {
  $("#s-meal-names").innerHTML = MEAL_NAMES.slice(0, count).map((name, i) => `
    <label class="meal-row">
      <span class="meal-row-name">${esc(name)}</span>
      <input type="time" data-meal-time value="${esc(times[i] || "")}" aria-label="Erinnerung ${esc(name)}">
    </label>`).join("");
  $("#s-meal-names").style.display = "grid";
  $("#s-meal-names").style.gap = "8px";
}

const MEAL_NAMES = ["Frühstück", "Mittagessen", "Abendessen", "Snack 1", "Snack 2", "Snack 3", "Snack 4"];

function fillSettings() {
  const u = state.user;
  $("#s-user").textContent = u.username;
  $("#s-kcal").value = u.kcal_goal;
  $("#s-water").value = u.water_goal_ml;
  $("#s-target").value = u.target_weight ?? "";
  $("#s-meals").value = u.meals_per_day;
  renderMealNameInputs(u.meals_per_day, u.meal_names.map((_, i) => u.reminders[i] || ""));
  $("#s-sex").value = u.sex ?? "";
  $("#s-birth").value = u.birth_year ?? "";
  $("#s-height").value = u.height_cm ?? "";
  $("#s-activity").value = u.activity != null ? String(u.activity) : "";
  $("#s-pace").value = String(u.weekly_loss ?? 0.5);
  $("#s-weight").value = u.latest_weight ? `${fmt(u.latest_weight, 1)} kg (zuletzt eingetragen)` : "";
  const e = energy(u);
  $("#body-summary").textContent = e
    ? `Gesamtumsatz ${fmt(e.tdee)} kcal · Empfehlung ${fmt(e.goal)} kcal`
    : "Noch nicht eingerichtet – jetzt dein Defizit berechnen";
  renderCalc();
}

function profileFromForm() {
  const num = (id) => { const v = $(id).value.replace(",", "."); return v === "" ? null : parseFloat(v); };
  return {
    sex: $("#s-sex").value || null,
    birth_year: num("#s-birth"),
    height_cm: num("#s-height"),
    activity: num("#s-activity"),
    weekly_loss: num("#s-pace"),
  };
}

function renderCalc() {
  const p = { ...profileFromForm(), latest_weight: state.user.latest_weight };
  const box = $("#calc-result");
  const e = energy(p);
  if (!e) {
    const missing = [!p.sex && "Geschlecht", !p.birth_year && "Geburtsjahr", !p.height_cm && "Größe",
      !p.activity && "Aktivität", !p.latest_weight && "ein Gewicht (unter „Tag“)"].filter(Boolean);
    box.innerHTML = `<div class="hint">Für die Berechnung fehlt noch: ${missing.join(", ")}.</div>`;
    return;
  }
  const line = (label, val) => `<div class="line"><span>${label}</span><strong>${val}</strong></div>`;
  box.innerHTML =
    line("Grundumsatz (in Ruhe)", `${fmt(e.bmr)} kcal`) +
    line("Gesamtumsatz (mit Aktivität)", `${fmt(e.tdee)} kcal`) +
    line("Tägliches Defizit", `${fmt(e.deficit)} kcal`) +
    `<div class="line goal-line"><span>Empfohlenes Kalorienziel</span><strong>${fmt(e.goal)} kcal</strong></div>` +
    (e.limit ? `<div class="note">Begrenzt (${e.limit}) – das entspricht ca. ${fmt(e.perWeek, 2)} kg pro Woche.</div>` : "") +
    `<div class="hint">Mifflin-St-Jeor-Formel, ${e.age} Jahre, ${fmt(e.kg, 1)} kg. Wird mit neuem Gewicht automatisch genauer.</div>` +
    (Math.round(+$("#s-kcal").value) === e.goal ? "" : `<button type="button" class="btn small" id="calc-apply">Als Kalorienziel übernehmen</button>`);
  const btn = $("#calc-apply");
  if (btn) btn.onclick = () => { $("#s-kcal").value = e.goal; saveSettings("#body-msg"); };
}

// Speichert alle Einstellungen (Hauptseite + Unterseite Körperdaten) und zeigt die Meldung in msgSel
async function saveSettings(msgSel) {
  const t = $("#s-target").value.replace(",", ".");
  try {
    state.user = await api("/settings", {
      method: "PUT",
      body: {
        meals_per_day: +$("#s-meals").value,
        kcal_goal: +$("#s-kcal").value,
        water_goal_ml: +$("#s-water").value,
        target_weight: t === "" ? null : parseFloat(t),
        ...profileFromForm(),
        reminders: Object.fromEntries($$("[data-meal-time]").map((inp, i) => [String(i), inp.value]).filter(([, t]) => t)),
      },
    });
    fillSettings();
    refreshPush();
    $(msgSel).textContent = "Gespeichert ✓";
    setTimeout(() => ($(msgSel).textContent = ""), 2000);
    loadDay();
  } catch (err) { $(msgSel).textContent = err.message; }
}

function initSettings() {
  ["#s-sex", "#s-birth", "#s-height", "#s-activity", "#s-pace", "#s-kcal"].forEach((id) => ($(id).oninput = renderCalc));
  $("#s-meals").onchange = () => {
    const times = $$("[data-meal-time]").map((i) => i.value);
    const n = +$("#s-meals").value;
    renderMealNameInputs(n, times);
  };
  $("#settings-form").onsubmit = (e) => { e.preventDefault(); saveSettings("#settings-msg"); };
  $("#body-form").onsubmit = (e) => { e.preventDefault(); saveSettings("#body-msg"); };
  $("#open-body").onclick = () => showView("body");
  $("#body-back").onclick = () => showView("settings");
  $("#pw-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/password", { method: "PUT", body: { old_password: $("#pw-old").value, new_password: $("#pw-new").value } });
      $("#pw-msg").textContent = "Passwort geändert ✓";
      e.target.reset();
    } catch (err) { $("#pw-msg").textContent = err.message; }
  };
  $("#logout").onclick = async () => {
    // Gerät vom Konto lösen, damit es nach dem Abmelden keine Erinnerungen dieses Benutzers mehr bekommt
    const sub = "serviceWorker" in navigator && "PushManager" in window ? await currentSubscription().catch(() => null) : null;
    if (sub) await api("/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } }).catch(() => {});
    await api("/logout", { method: "POST" }).catch(() => {});
    state.user = null;
    showAuth();
  };
}

/* ------------------------------------------------------------ Push-Benachrichtigungen */

function b64ToBytes(b64) {
  const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

// getRegistration statt .ready: .ready wartet ewig, wenn kein Service Worker registriert ist
async function currentSubscription() {
  const reg = await navigator.serviceWorker.getRegistration();
  return reg ? reg.pushManager.getSubscription() : null;
}

// Zeigt den passenden Status und meldet ein vorhandenes Abo erneut beim Server an
// (z. B. nach Benutzerwechsel auf demselben Gerät).
async function refreshPush() {
  const status = $("#push-status");
  const show = (enable, active) => {
    $("#push-enable").classList.toggle("hidden", !enable);
    $("#push-test").classList.toggle("hidden", !active);
    $("#push-disable").classList.toggle("hidden", !active);
  };
  show(false, false);
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  if (!window.isSecureContext) {
    status.textContent = "Push-Nachrichten funktionieren nur über eine HTTPS-Adresse (z. B. https://bissfest.deine-domain.de). Über eine http://-IP-Adresse lässt der Browser sie nicht zu.";
    return;
  }
  if (ios && !navigator.standalone) {
    status.textContent = "Auf dem iPhone: Öffne die App zuerst über Teilen → „Zum Home-Bildschirm“ und starte sie dann von dort (ab iOS 16.4).";
    return;
  }
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    status.textContent = "Dieser Browser unterstützt keine Push-Nachrichten.";
    return;
  }
  if (Notification.permission === "denied") {
    status.textContent = "Benachrichtigungen sind für diese App blockiert. Erlaube sie in den Browser- bzw. Systemeinstellungen.";
    return;
  }
  const sub = await currentSubscription();
  if (sub) {
    const r = await api("/push/subscribe", { method: "POST", body: sub.toJSON() }).catch(() => null);
    const n = r ? r.push_devices : state.user.push_devices;
    const anyTime = Object.keys(state.user.reminders || {}).length;
    status.textContent = `Aktiv auf diesem Gerät (${n} Gerät${n === 1 ? "" : "e"} insgesamt).` +
      (anyTime ? "" : " Trage oben bei den Mahlzeiten Uhrzeiten ein, damit Erinnerungen kommen.");
    show(false, true);
  } else {
    status.textContent = "Erhalte zu den eingestellten Uhrzeiten eine Erinnerung, deine Mahlzeit einzutragen.";
    show(true, false);
  }
}

async function enablePush() {
  try {
    if ((await Notification.requestPermission()) !== "granted") return refreshPush();
    const { key } = await api("/push/key");
    await navigator.serviceWorker.register("/sw.js");
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
    await api("/push/subscribe", { method: "POST", body: sub.toJSON() });
    toast("Erinnerungen aktiviert");
  } catch (err) {
    toast("Aktivieren fehlgeschlagen: " + err.message);
  }
  refreshPush();
}

async function disablePush() {
  const sub = await currentSubscription();
  if (sub) {
    await api("/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } }).catch(() => {});
    await sub.unsubscribe();
  }
  toast("Erinnerungen auf diesem Gerät deaktiviert");
  refreshPush();
}

function initPush() {
  $("#push-enable").onclick = enablePush;
  $("#push-disable").onclick = disablePush;
  $("#push-test").onclick = async () => {
    try { await api("/push/test", { method: "POST" }); toast("Test-Nachricht gesendet"); }
    catch (err) { toast(err.message); }
  };
}

/* ------------------------------------------------------------ Darstellung (Hell / Dunkel / System) */

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

function getTheme() {
  try { return localStorage.getItem("theme") || "system"; } catch { return "system"; }
}

function applyTheme(choice) {
  if (choice === "light" || choice === "dark") document.documentElement.dataset.theme = choice;
  else delete document.documentElement.dataset.theme;
  const dark = choice === "dark" || (choice === "system" && darkQuery.matches);
  // Farbe der Statusleiste / Browserleiste an den Hintergrund anpassen
  document.querySelector('meta[name="theme-color"]').content = dark ? "#0e1411" : "#fbf7ef";
  $$("[data-theme-choice]").forEach((b) => {
    const on = b.dataset.themeChoice === choice;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", on);
  });
}

function initTheme() {
  applyTheme(getTheme());
  $$("[data-theme-choice]").forEach((b) => (b.onclick = () => {
    const choice = b.dataset.themeChoice;
    try { localStorage.setItem("theme", choice); } catch {}
    applyTheme(choice);
  }));
  // Bei "System": Wechsel des Handys (z. B. abends automatisch dunkel) sofort übernehmen
  darkQuery.addEventListener("change", () => { if (getTheme() === "system") applyTheme("system"); });
}

/* ------------------------------------------------------------ Navigation */

const SUBVIEW_TAB = { body: "settings" };

function showView(view) {
  const tab = SUBVIEW_TAB[view] || view;
  $$(".tabbar button").forEach((x) => x.classList.toggle("active", x.dataset.view === tab));
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $("#view-" + view).classList.remove("hidden");
  $(".topbar").classList.toggle("hidden", view !== "today");
  if (view === "history") loadHistory();
  window.scrollTo(0, 0);
}

function initNav() {
  $$(".tabbar button").forEach((b) => (b.onclick = () => showView(b.dataset.view)));
  $("#hist-range").onchange = loadHistory;
  // Beim Zurückkehren in die App (z. B. am nächsten Morgen) auf "Heute" springen
  let lastToday = localISO(new Date());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || !state.user) return;
    const today = localISO(new Date());
    if (today !== lastToday && state.day === lastToday) state.day = today;
    lastToday = today;
    loadDay();
  });
}

(async function main() {
  initAuth();
  initDay();
  initSheet();
  initSettings();
  initPush();
  initTheme();
  initNav();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  try { await startApp(); } catch { showAuth(); }
})();
