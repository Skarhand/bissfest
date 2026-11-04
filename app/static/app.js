"use strict";

// Letzte JavaScript-Fehler für Bug Reports merken (so früh wie möglich registriert)
const recentErrors = [];
const rememberError = (msg) => { recentErrors.push(`${new Date().toLocaleTimeString("de-DE")} ${msg}`.slice(0, 300)); recentErrors.splice(0, recentErrors.length - 5); };
window.addEventListener("error", (e) => rememberError(`${e.message} (${(e.filename || "").split("/").pop()}:${e.lineno})`));
window.addEventListener("unhandledrejection", (e) => rememberError(`Promise: ${e.reason?.message || e.reason}`));
// Start aus der Android-App erkennen (der Referrer gilt nur beim ersten Laden)
try { if (document.referrer.startsWith("android-app://")) sessionStorage.setItem("twa", "1"); } catch {}

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
  state.version = cfg.version;
  if (cfg.version) {
    $("#app-version").textContent = `Bissfest v${cfg.version} · Mit Biss zum Wunschgewicht`;
    $("#settings-version").textContent = `Bissfest – Version ${cfg.version}`;
    $("#auth-version").textContent = `Version ${cfg.version} · `;
  }
  if (!cfg.allow_registration) $("#auth-toggle").classList.add("hidden");
  if (cfg.source_url) {
    $("#source-link a").href = cfg.source_url;
    $("#source-link").classList.remove("hidden");
  }
  // Android-App anbieten: nur im Android-Browser, nicht in der installierten App/PWA
  const standalone = matchMedia("(display-mode: standalone)").matches || document.referrer.startsWith("android-app://");
  $("#android-card").classList.toggle("hidden", !(cfg.android_apk_url && /Android/i.test(navigator.userAgent) && !standalone));
  if (cfg.android_apk_url) $("#android-download").href = cfg.android_apk_url;
  $("#android-server").textContent = location.origin;
  $("#switch-server").classList.toggle("hidden", appMode() !== "Android-App");
  // „Spendier mir einen Kaffee“ per Wero – Karte nur, wenn ein Link eingerichtet ist
  if (cfg.donate_wero) $("#donate-wero").href = cfg.donate_wero;
  $("#donate-card").classList.toggle("hidden", !cfg.donate_wero);
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
        showNotice("📬 Fast geschafft!", `Wir haben dir eine Mail an ${r.email} geschickt. Klicke auf den Link darin, um dein Konto zu bestätigen – danach bist du direkt angemeldet. Schau auch im Spam-Ordner nach.`, true, r.email);
        return;
      }
      await api("/login", { method: "POST", body: creds() });
      $("#auth-pass").value = "";
      await startApp();
    } catch (err) {
      if (err.status === 403 && !state.registerMode) showNotice("📬 E-Mail noch nicht bestätigt", err.message, true, err.message.match(/\S+@[^\s,]+[^\s.,]/)?.[0]);
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
      else showNotice("📬 Neue Mail ist unterwegs", `Wir haben den Bestätigungslink erneut an ${r.email} geschickt.`, true, r.email);
    } catch (err) { $("#auth-err").textContent = err.message; }
  };
  $("#auth-fix-open").onclick = () => {
    $("#auth-fix-open").classList.add("hidden");
    $("#auth-fix").classList.remove("hidden");
    $("#auth-fix-email").focus();
  };
  $("#auth-fix").onsubmit = async (e) => {
    e.preventDefault();
    $("#auth-err").textContent = "";
    if (!$("#auth-user").value.trim() || !$("#auth-pass").value) {
      $("#auth-err").textContent = "Bitte oben Benutzername und Passwort eingeben, dann erneut senden.";
      return;
    }
    try {
      const r = await api("/correct-email", { method: "POST", body: { ...creds(), email: $("#auth-fix-email").value.trim() } });
      showNotice("📬 Adresse korrigiert", `Wir haben einen neuen Bestätigungslink an ${r.email} geschickt. Der Link aus der vorherigen Mail gilt nicht mehr.`, true, r.email);
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

function showNotice(title, text, resend, email = "") {
  $("#auth-notice-title").textContent = title;
  $("#auth-notice-text").textContent = text;
  $("#auth-resend").classList.toggle("hidden", !resend);
  $("#auth-fix-open").classList.toggle("hidden", !resend);
  $("#auth-fix").classList.add("hidden");
  $("#auth-fix-email").value = email;
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
  maybeStartOnboarding();
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
  $("#goal-hint").classList.toggle("hidden", !needsGoalSetup());
  $("#goal-hint-kcal").textContent = fmt(u.kcal_goal);
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
  renderDrinks();

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
          <div><div class="n">${esc(f.name)}</div><div class="g">${f.portion_name ? esc(fmtPortion(f.portion_count, f.portion_name)) + " · " : ""}${fmt(f.grams)} g${f.brand ? " · " + esc(f.brand) : ""}</div></div>
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
    <li><div><div class="n">${esc(i.name)}</div><div class="b">${i.portion_name ? esc(fmtPortion(i.portion_count, i.portion_name)) + " · " : ""}${fmt(i.grams)} g${i.brand ? " · " + esc(i.brand) : ""}</div></div>
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

/* ------------------------------------------------------------ Menge & Portionen */

// "2 × Scheibe", "½ × Stück", "1½ × EL"
function fmtCount(n) {
  const whole = Math.floor(n), frac = Math.round((n - whole) * 100) / 100;
  if (frac === 0.5) return (whole ? whole : "") + "½";
  return fmt(n, n % 1 ? 1 : 0);
}
const fmtPortion = (count, name) => `${fmtCount(count)} × ${name}`;

function pickFood(f, grams, isEdit = false) {
  stopScan();
  state.pick = f;
  const seq = (state.pickSeq = (state.pickSeq || 0) + 1);
  $("#a-name").textContent = f.name;
  const macros = [f.protein_100g != null && `E ${fmt(f.protein_100g, 1)}`, f.carbs_100g != null && `KH ${fmt(f.carbs_100g, 1)}`,
    f.sugar_100g != null && `Zucker ${fmt(f.sugar_100g, 1)}`, f.fat_100g != null && `F ${fmt(f.fat_100g, 1)}`].filter(Boolean).join(" · ");
  $("#a-meta").textContent = [f.brand, `${fmt(f.kcal_100g)} kcal/100 g`, macros, f.source].filter(Boolean).join(" · ");
  $("#a-new-portion").classList.add("hidden");
  // Erst mit Gramm anzeigen, Portionen laden nach
  state.units = [{ name: null, grams: 1 }];
  selectUnit(0, grams ?? f.grams ?? 100);
  fillMealSelect(state.meal);
  $("#a-save").textContent = isEdit ? "Speichern" : "Eintragen";
  $("#a-delete").classList.toggle("hidden", !isEdit);
  $("#a-back").classList.toggle("hidden", isEdit);
  $("#a-delete-custom").classList.toggle("hidden", isEdit || !(f.custom_id && f.mine));
  state.lastTab = $("#sheet-tabs .active")?.dataset.tab || "search";
  showTab("amount");
  loadUnits(f).then((units) => {
    if (seq !== state.pickSeq) return; // inzwischen anderes Lebensmittel gewählt
    state.units = units;
    // Vorauswahl: zuletzt genutzte Portion, sonst erste Portion, sonst Gramm
    const last = f.portion_name && units.findIndex((u) => u.name && u.name.toLowerCase() === f.portion_name.toLowerCase());
    if (last > 0) selectUnit(last, isEdit ? f.portion_count : (f.portion_count || 1));
    else if (units.length > 1 && grams == null) selectUnit(1, 1);
    else selectUnit(0, grams ?? f.grams ?? 100);
  });
}

async function loadUnits(f) {
  const q = new URLSearchParams({ name: f.name, brand: f.brand || "", source: f.source || "", code: f.code || "" });
  const r = await api("/portions?" + q).catch(() => ({ saved: [], suggested: [] }));
  const units = [{ name: null, grams: 1 }], seen = new Set();
  const add = (p, kind) => {
    const k = p.name.toLowerCase();
    if (seen.has(k) || !(p.grams > 0)) return;
    seen.add(k);
    units.push({ ...p, kind });
  };
  r.saved.forEach((p) => add(p, "saved"));
  (f.source_portions || []).forEach((p) => add(p, "source"));
  // Portion aus einem früheren Eintrag (z. B. "Zuletzt"), falls sie nicht gespeichert ist
  if (f.portion_name && f.portion_count && f.grams) add({ name: f.portion_name, grams: Math.round((f.grams / f.portion_count) * 10) / 10 }, "entry");
  r.suggested.forEach((p) => add(p, "suggested"));
  return units;
}

function selectUnit(i, qty) {
  state.unit = i;
  const u = state.units[i];
  $("#a-units").innerHTML = state.units.map((x, j) => `
    <button type="button" data-u="${j}" class="${j === i ? "on" : ""}">${x.name
      ? `${esc(x.name)} · ${fmt(x.grams, x.grams % 1 ? 1 : 0)} g${x.kind === "suggested" ? " <small>(typisch)</small>" : ""}`
      : "Gramm"}</button>`).join("") + `<button type="button" class="add" id="a-add-portion">+ Portion</button>`;
  $$("#a-units [data-u]").forEach((b) => (b.onclick = () => selectUnit(+b.dataset.u, +b.dataset.u ? 1 : Math.round(currentGrams()) || 100)));
  $("#a-add-portion").onclick = () => {
    $("#a-new-portion").classList.toggle("hidden");
    $("#np-name").focus();
  };
  $("#a-qty-label").textContent = u.name ? `Anzahl ${u.name}` : "Menge (g / ml)";
  $("#a-grams").value = qty;
  const quick = u.name ? [[0.5, "½"], [1, "1"], [2, "2"], [3, "3"]] : [50, 100, 150, 200, 250].map((g) => [g, `${g} g`]);
  $("#a-chips").innerHTML = quick.map(([v, label]) => `<button type="button" data-q="${v}">${label}</button>`).join("");
  $$("#a-chips button").forEach((b) => (b.onclick = () => { $("#a-grams").value = b.dataset.q; updatePreview(); }));
  updatePreview();
}

function currentGrams() {
  const qty = parseFloat($("#a-grams").value.replace(",", ".")) || 0;
  return Math.round(qty * state.units[state.unit].grams * 10) / 10;
}

function updatePreview() {
  const g = currentGrams(), u = state.units[state.unit];
  $("#a-kcal").textContent = fmt((state.pick.kcal_100g * g) / 100);
  $("#a-grams-info").textContent = u.name ? `= ${fmt(g, g % 1 ? 1 : 0)} g` : "";
}

async function savePortion(f, name, grams) {
  return api("/portions", {
    method: "POST",
    body: { name: f.name, brand: f.brand || "", source: f.source || "", code: f.code || "", portion_name: name, grams },
  });
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
  const grams = currentGrams();
  const meal = +$("#a-meal").value;
  const f = state.pick, u = state.units[state.unit];
  const qty = parseFloat($("#a-grams").value.replace(",", "."));
  const portion = u.name ? { portion_name: u.name, portion_count: qty } : { portion_name: null, portion_count: null };
  if (!(grams > 0)) return toast("Bitte eine Menge eingeben");
  try {
    // Vorgeschlagene oder aus der Datenbank stammende Portion beim ersten Benutzen dauerhaft merken
    if (u.name && u.kind !== "saved") await savePortion(f, u.name, u.grams).catch(() => {});
    if (state.editId) {
      await api("/food/" + state.editId, { method: "PATCH", body: { grams, meal, ...portion } });
    } else {
      await api("/food", {
        method: "POST",
        body: {
          day: state.day, meal, grams, name: f.name, brand: f.brand || "", kcal_100g: f.kcal_100g,
          protein_100g: f.protein_100g ?? null, carbs_100g: f.carbs_100g ?? null, sugar_100g: f.sugar_100g ?? null,
          fat_100g: f.fat_100g ?? null,
          source: f.source || "manuell", code: f.code || "", ...portion,
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
      const pName = $("#m-portion-name").value.trim(), pGrams = num("#m-portion-grams");
      if (pName && pGrams > 0) await savePortion(food, pName, pGrams).catch(() => {});
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
  $("#np-save").onclick = async () => {
    const name = $("#np-name").value.trim(), grams = parseFloat($("#np-grams").value.replace(",", "."));
    if (!name || !(grams > 0)) return toast("Bitte Name und Gramm angeben");
    try {
      await savePortion(state.pick, name, grams);
      state.units = await loadUnits(state.pick);
      const i = state.units.findIndex((u) => u.name && u.name.toLowerCase() === name.toLowerCase());
      $("#np-name").value = $("#np-grams").value = "";
      $("#a-new-portion").classList.add("hidden");
      selectUnit(i > 0 ? i : 0, 1);
      toast(`Portion „${name}“ gespeichert`);
    } catch (err) { toast(err.message); }
  };
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
  drinksHistory(h);
}

/* ------------------------------------------------------------ Kaffee-Tracker (Extra) */

const CAFFEINE_LIMIT = 400; // mg pro Tag, Empfehlung der EFSA für gesunde Erwachsene

const CUP_ML = 150; // auf diese Menge bezieht sich "Koffein pro Tasse"
const DRINKS = {
  coffee: { name: "Kaffee", icon: "☕", enabled: "coffee_enabled", mg: "caffeine_mg" },
  tea: { name: "Tee", icon: "🍵", enabled: "tea_enabled", mg: "tea_caffeine_mg" },
};
const EMPTY_DRINK = { cups: 0, mugs: 0, ml: 0 };
const caffeineOf = (ml, kind) => (ml / CUP_ML) * state.user[DRINKS[kind].mg];
const enabledDrinks = () => Object.keys(DRINKS).filter((k) => state.user[DRINKS[k].enabled]);

function renderDrinks() {
  const active = enabledDrinks();
  // Koffein aus Kaffee und Tee zählt gemeinsam gegen die Tagesgrenze
  const totalMg = active.reduce((sum, k) => sum + caffeineOf((state.data[k] || EMPTY_DRINK).ml, k), 0);
  const over = totalMg > CAFFEINE_LIMIT;
  Object.entries(DRINKS).forEach(([kind, d]) => {
    const card = $(`#drink-${kind}`);
    card.classList.toggle("hidden", !active.includes(kind));
    if (!active.includes(kind)) return;
    const c = state.data[kind] || EMPTY_DRINK, mg = caffeineOf(c.ml, kind);
    const icons = d.icon.repeat(Math.min(c.cups, 8)) + `<span class="mug">${d.icon.repeat(Math.min(c.mugs, 6))}</span>`;
    const parts = [c.cups && `${c.cups} ${c.cups === 1 ? "Tasse" : "Tassen"}`, c.mugs && `${c.mugs} ${c.mugs === 1 ? "Pott" : "Pötte"}`].filter(Boolean);
    card.querySelector(".d-cups").innerHTML = c.ml
      ? `<span class="cup-icons">${icons}</span><strong>${parts.join(" + ")}</strong> <span class="muted">· ${fmt(c.ml)} ml</span>`
      : `<span class="muted">Noch kein ${d.name} heute.</span>`;
    const caf = card.querySelector(".d-caffeine");
    caf.textContent = `≈ ${fmt(mg)} mg Koffein`;
    caf.className = `d-caffeine ${over && mg ? "neg" : "muted"}`;
    // Hinweis nur einmal, auf der letzten sichtbaren Karte
    card.querySelector(".d-hint").textContent = over && kind === active[active.length - 1]
      ? `${active.length > 1 ? "Kaffee und Tee zusammen: " : ""}über ${CAFFEINE_LIMIT} mg Koffein – mehr empfiehlt die EFSA pro Tag nicht. Vielleicht ist jetzt ein Wasser dran? 💧`
      : "";
    card.querySelector(".d-undo").disabled = !c.ml;
  });
}

async function drinkAction(kind, req) {
  try {
    state.data[kind] = await req;
    renderDrinks();
  } catch (err) { toast(err.message); }
}

function initDrinks() {
  Object.keys(DRINKS).forEach((kind) => {
    const card = $(`#drink-${kind}`);
    card.querySelectorAll(".d-add").forEach((b) => (b.onclick = () => drinkAction(kind,
      api("/coffee", { method: "POST", body: { day: state.day, size: b.dataset.size, kind } }))));
    card.querySelector(".d-undo").onclick = () => drinkAction(kind,
      api(`/coffee/last?day=${state.day}&kind=${kind}`, { method: "DELETE" }));
  });
}

function drinksHistory(h) {
  const active = enabledDrinks().filter((k) => h[k].length);
  $("#drinks-history").classList.toggle("hidden", !active.length);
  if (!active.length) return;
  $("#drinks-history-title").textContent = active.map((k) => `${DRINKS[k].icon} ${DRINKS[k].name}`).join(" & ");
  // Koffein je Tag über alle aktiven Getränke
  const perDay = {};
  active.forEach((k) => h[k].forEach((r) => (perDay[r.day] = (perDay[r.day] || 0) + caffeineOf(r.ml, k))));
  const days = Object.keys(perDay).length;
  const over = Object.values(perDay).filter((mg) => mg > CAFFEINE_LIMIT).length;
  const avgMl = (k) => h[k].reduce((s, r) => s + r.ml, 0) / days;
  $("#drinks-stats").innerHTML =
    active.map((k) => stat(`${fmt(avgMl(k))} ml`, `Ø ${DRINKS[k].name}/Tag`)).join("") +
    stat(`${fmt(Object.values(perDay).reduce((a, b) => a + b, 0) / days)} mg`, "Ø Koffein") +
    stat(`${over}`, `Tage > ${CAFFEINE_LIMIT} mg`);
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
  $("#admin-link").classList.toggle("hidden", !u.is_admin);
  $("#s-kcal").value = u.kcal_goal;
  $("#s-water").value = u.water_goal_ml;
  $("#s-target").value = u.target_weight ?? "";
  $("#s-coffee").checked = u.coffee_enabled;
  $("#s-caffeine").value = u.caffeine_mg;
  $("#s-coffee-details").classList.toggle("hidden", !u.coffee_enabled);
  $("#s-tea").checked = u.tea_enabled;
  $("#s-tea-caffeine").value = u.tea_caffeine_mg;
  $("#s-tea-details").classList.toggle("hidden", !u.tea_enabled);
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
        coffee_enabled: $("#s-coffee").checked,
        caffeine_mg: Math.round(+$("#s-caffeine").value) || 80,
        tea_enabled: $("#s-tea").checked,
        tea_caffeine_mg: $("#s-tea-caffeine").value === "" ? 40 : Math.round(+$("#s-tea-caffeine").value),
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
  ["coffee", "tea"].forEach((k) => {
    $(`#s-${k}`).onchange = () => {
      $(`#s-${k}-details`).classList.toggle("hidden", !$(`#s-${k}`).checked);
      saveSettings("#extras-msg");
    };
  });
  $("#s-caffeine").onchange = () => saveSettings("#extras-msg");
  $("#s-tea-caffeine").onchange = () => saveSettings("#extras-msg");
  initDrinks();
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

/* ------------------------------------------------------------ Home Assistant (API-Tokens) */

const fmtDateTime = (ts) => new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });

async function loadTokens() {
  const list = $("#ha-tokens");
  const tokens = await api("/tokens").catch(() => []);
  list.innerHTML = tokens.length ? "" : `<li class="muted tiny">Noch kein Token erzeugt.</li>`;
  tokens.forEach((t) => {
    const li = document.createElement("li");
    li.innerHTML = `<div><strong>${esc(t.name)}</strong> <code>${esc(t.prefix)}…</code>
      <div class="muted tiny">erstellt ${fmtDateTime(t.created)} · ${t.last_used ? "zuletzt genutzt " + fmtDateTime(t.last_used) : "noch nicht genutzt"}</div></div>
      <button type="button" class="btn small danger">Widerrufen</button>`;
    li.querySelector("button").onclick = async () => {
      if (!confirm(`Token „${t.name}“ widerrufen? Home Assistant verliert dann den Zugriff.`)) return;
      await api("/tokens/" + t.id, { method: "DELETE" });
      $("#ha-new-token").classList.add("hidden");
      loadTokens();
    };
    list.appendChild(li);
  });
}

function initHomeAssistant() {
  $("#ha-url").textContent = location.origin;
  $("#ha-token-form").onsubmit = async (e) => {
    e.preventDefault();
    $("#ha-err").textContent = "";
    try {
      const r = await api("/tokens", { method: "POST", body: { name: $("#ha-token-name").value.trim() } });
      $("#ha-token-value").textContent = r.token;
      $("#ha-new-token").classList.remove("hidden");
      $("#ha-token-name").value = "";
      loadTokens();
    } catch (err) { $("#ha-err").textContent = err.message; }
  };
  $("#ha-token-copy").onclick = async () => {
    try {
      await navigator.clipboard.writeText($("#ha-token-value").textContent);
      toast("Token kopiert");
    } catch {
      // Fallback (z. B. ohne Clipboard-Berechtigung): Text markieren, damit man ihn manuell kopieren kann
      getSelection().selectAllChildren($("#ha-token-value"));
      toast("Bitte manuell kopieren");
    }
  };
}

/* ------------------------------------------------------------ Einrichtungs-Assistent (persönliches Kalorienziel) */

// Hinweis/Assistent, solange kein persönliches Ziel berechnet werden kann und der Nutzer ihn nicht weggeklickt hat
const needsGoalSetup = () => !energy(state.user) && !state.user.goal_hint_dismissed;
const onbKey = () => `bissfest-onboarding-${state.user.username}`;

function maybeStartOnboarding() {
  if (!needsGoalSetup()) return;
  try {
    if (localStorage.getItem(onbKey())) return; // automatisch nur einmal pro Gerät, danach über die Hinweiskarte
    localStorage.setItem(onbKey(), "1");
  } catch { return; }
  openOnboarding();
}

function onbProfile() {
  const num = (id) => { const v = $(id).value.replace(",", "."); return v === "" ? null : parseFloat(v); };
  return { sex: $("#onb-sex").value || null, birth_year: num("#onb-birth"), height_cm: num("#onb-height"),
    activity: num("#onb-activity"), weekly_loss: num("#onb-pace"), latest_weight: num("#onb-weight") };
}

function renderOnboarding() {
  const p = onbProfile(), e = energy(p);
  // Wasser: Faustregel 35 ml pro kg, auf 50 ml gerundet (nur vorschlagen, solange nicht selbst geändert)
  if (p.latest_weight && !$("#onb-water").dataset.touched) $("#onb-water").value = Math.round((p.latest_weight * 35) / 50) * 50;
  const box = $("#onb-result");
  if (!e) { box.innerHTML = `<div class="hint">Fülle alle Felder aus – dann siehst du hier dein Ziel.</div>`; return; }
  box.innerHTML = `<div class="line"><span>Gesamtumsatz (mit Aktivität)</span><strong>${fmt(e.tdee)} kcal</strong></div>
    <div class="line"><span>Tägliches Defizit</span><strong>${fmt(e.deficit)} kcal</strong></div>
    <div class="line goal-line"><span>Dein Kalorienziel</span><strong>${fmt(e.goal)} kcal</strong></div>` +
    (e.limit ? `<div class="note">Begrenzt (${e.limit}) – das entspricht ca. ${fmt(e.perWeek, 2)} kg pro Woche.</div>` : "");
}

function openOnboarding() {
  const u = state.user;
  $("#onb-sex").value = u.sex ?? "";
  $("#onb-birth").value = u.birth_year ?? "";
  $("#onb-height").value = u.height_cm ?? "";
  $("#onb-weight").value = u.latest_weight ?? "";
  $("#onb-activity").value = u.activity != null ? String(u.activity) : "";
  $("#onb-pace").value = String(u.weekly_loss ?? 0.5);
  $("#onb-water").value = u.water_goal_ml;
  delete $("#onb-water").dataset.touched;
  $("#onb-err").textContent = "";
  renderOnboarding();
  $("#onboarding").classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeOnboarding() {
  $("#onboarding").classList.add("hidden");
  document.body.style.overflow = "";
}

function initOnboarding() {
  ["#onb-sex", "#onb-birth", "#onb-height", "#onb-weight", "#onb-activity", "#onb-pace"].forEach((id) => ($(id).oninput = renderOnboarding));
  $("#onb-water").oninput = () => ($("#onb-water").dataset.touched = "1");
  $("#onb-close").onclick = closeOnboarding;
  $("#onb-later").onclick = closeOnboarding;
  $("#goal-hint-open").onclick = openOnboarding;
  $("#goal-hint-dismiss").onclick = async () => {
    if (!confirm(`Dein Kalorienziel von ${fmt(state.user.kcal_goal)} kcal beibehalten und den Hinweis nicht mehr anzeigen?`)) return;
    await api("/goal-hint/dismiss", { method: "POST" }).catch(() => {});
    state.user.goal_hint_dismissed = true;
    renderDay();
  };
  $("#onb-form").onsubmit = async (e) => {
    e.preventDefault();
    const p = onbProfile(), en = energy(p);
    if (!en) return ($("#onb-err").textContent = "Bitte alle Felder ausfüllen.");
    $("#onb-save").disabled = true;
    try {
      await api("/weight/" + localISO(new Date()), { method: "PUT", body: { kg: p.latest_weight } });
      // Werte in die Einstellungs-Felder übernehmen und über den normalen Speichern-Weg sichern
      $("#s-sex").value = p.sex;
      $("#s-birth").value = p.birth_year;
      $("#s-height").value = p.height_cm;
      $("#s-activity").value = String(p.activity);
      $("#s-pace").value = String(p.weekly_loss);
      $("#s-kcal").value = en.goal;
      $("#s-water").value = Math.round(+$("#onb-water").value) || state.user.water_goal_ml;
      await saveSettings("#onb-err");
      if (!energy(state.user)) throw new Error($("#onb-err").textContent || "Speichern fehlgeschlagen");
      closeOnboarding();
      toast(`Dein Ziel: ${fmt(en.goal)} kcal pro Tag 🎯`);
    } catch (err) {
      $("#onb-err").textContent = err.message;
    } finally {
      $("#onb-save").disabled = false;
    }
  };
}

/* ------------------------------------------------------------ Feature Request */

const FEEDBACK_KINDS = {
  feature: {
    title: "💡 Feature Request", intro: "Beschreibe kurz, was du dir wünschst.", placeholder: "z. B. Ich hätte gern …",
    thanks: "Danke! Deine Idee ist angekommen 💡",
  },
  bug: {
    title: "🐞 Fehler melden", intro: "Was ist passiert, und was hast du stattdessen erwartet? Ein Screenshot hilft sehr.",
    placeholder: "z. B. Ich habe auf „Eintragen“ getippt, aber der Eintrag ist nicht erschienen …",
    thanks: "Danke! Wir schauen uns den Fehler an 🐞",
  },
};

function appMode() {
  let twa = false;
  try { twa = sessionStorage.getItem("twa") === "1"; } catch {}
  if (twa) return "Android-App";
  return matchMedia("(display-mode: standalone)").matches || navigator.standalone ? "installierte Web-App" : "Browser";
}

// Technische Angaben für Bug Reports – werden im Dialog vor dem Senden angezeigt
function collectMeta() {
  const view = $$(".view").find((v) => !v.classList.contains("hidden"))?.id.replace("view-", "") || "?";
  return {
    version: state.version || "?",
    modus: appMode(),
    geraet: navigator.userAgent,
    bildschirm: `${screen.width}×${screen.height} (Fenster ${innerWidth}×${innerHeight}, ${devicePixelRatio}x)`,
    darstellung: `${getTheme()}${darkQuery.matches ? " (System dunkel)" : ""}`,
    ansicht: view === "today" ? `Tag ${state.day}` : view,
    sprache: navigator.language,
    online: navigator.onLine,
    benachrichtigungen: "Notification" in window ? Notification.permission : "nicht unterstützt",
    letzte_fehler: recentErrors.length ? recentErrors.join(" | ") : "keine",
  };
}

// Bild verkleinern (max. 1600 px) und als JPEG-Data-URL liefern
async function shrinkImage(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.82);
}

function closeFeedback() {
  $("#feedback-dialog").classList.add("hidden");
  document.body.style.overflow = "";
}

function clearShot() {
  state.screenshot = null;
  $("#bug-shot").value = "";
  $("#bug-shot-box").classList.add("hidden");
}

function openFeedback(kind) {
  const k = FEEDBACK_KINDS[kind];
  state.feedbackKind = kind;
  $("#feedback-title").textContent = k.title;
  $("#feedback-intro").textContent = k.intro;
  $("#feedback-text").placeholder = k.placeholder;
  $("#feedback-err").textContent = "";
  $("#feedback-reply").textContent = state.user.email
    ? `Antworten kommen an ${state.user.email}.`
    : "Du hast keine E-Mail-Adresse hinterlegt – wir können dir daher nicht direkt antworten.";
  $("#bug-extras").classList.toggle("hidden", kind !== "bug");
  if (kind === "bug") $("#bug-meta").textContent = Object.entries(collectMeta()).map(([key, v]) => `${key}: ${v}`).join("\n");
  $("#feedback-dialog").classList.remove("hidden");
  document.body.style.overflow = "hidden";
  $("#feedback-count").textContent = `${$("#feedback-text").value.length} / 2000`;
  setTimeout(() => $("#feedback-text").focus(), 50);
}

function initFeedback() {
  const text = $("#feedback-text");
  $$("[data-feedback]").forEach((b) => (b.onclick = () => openFeedback(b.dataset.feedback)));
  $("#feedback-close").onclick = closeFeedback;
  $("#feedback-dialog").onclick = (e) => { if (e.target.id === "feedback-dialog") closeFeedback(); };
  text.oninput = () => ($("#feedback-count").textContent = `${text.value.length} / 2000`);
  $("#bug-shot").onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      state.screenshot = await shrinkImage(file);
      $("#bug-shot-preview").src = state.screenshot;
      $("#bug-shot-box").classList.remove("hidden");
    } catch {
      clearShot();
      toast("Dieses Bild konnte nicht gelesen werden");
    }
  };
  $("#bug-shot-remove").onclick = clearShot;
  $("#feedback-form").onsubmit = async (e) => {
    e.preventDefault();
    const kind = state.feedbackKind || "feature";
    $("#feedback-err").textContent = "";
    $("#feedback-send").disabled = true;
    try {
      const body = { text: text.value.trim(), kind };
      if (kind === "bug") Object.assign(body, { meta: collectMeta(), screenshot: state.screenshot || null });
      await api("/feedback", { method: "POST", body });
      text.value = "";
      clearShot();
      closeFeedback();
      toast(FEEDBACK_KINDS[kind].thanks);
    } catch (err) {
      $("#feedback-err").textContent = err.message;
    } finally {
      $("#feedback-send").disabled = false;
    }
  };
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
  if (view === "settings") loadTokens();
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
  initFeedback();
  initOnboarding();
  initHomeAssistant();
  initNav();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  try { await startApp(); } catch { showAuth(); }
})();
