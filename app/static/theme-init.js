// Läuft im <head> vor dem ersten Zeichnen. Eigene Datei statt Inline-Skript wegen der Content-Security-Policy.
// Gewählte Darstellung vor dem ersten Zeichnen setzen (kein Aufblitzen im falschen Modus)
try { const t = localStorage.getItem("theme"); if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; } catch (e) {}

// Systemfarben (Android 12+): Die App liefert sie beim Start im #-Teil der Adresse. Reihenfolge: Hell dann Dunkel,
// je Akzent, heller Akzent, Schrift auf Akzent. Hier statt in app.js, damit das Bissfest-Grün nicht aufblitzt.
(function () {
  const root = document.documentElement, names = ["--accent", "--accent-soft", "--on-accent"];
  const get = (key) => { try { return localStorage.getItem(key); } catch (e) { return null; } };
  function readHash() {
    const m = /^#syscolors=([0-9a-f]{6}(?:\.[0-9a-f]{6}){5})$/i.exec(location.hash);
    if (!m) return;
    try { localStorage.setItem("system-colors", m[1]); } catch (e) {}
    history.replaceState(null, "", location.pathname + location.search);
  }
  window.applyColors = function () {
    const raw = get("system-colors"), on = raw && get("color-source") === "system", theme = get("theme");
    const colors = raw ? raw.split(".") : [];
    const dark = theme === "dark" || (theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
    names.forEach((name, i) => {
      if (on) root.style.setProperty(name, "#" + colors[(dark ? 3 : 0) + i]);
      else root.style.removeProperty(name);
    });
  };
  readHash();
  window.applyColors();
  window.addEventListener("hashchange", () => { readHash(); window.applyColors(); });
})();
