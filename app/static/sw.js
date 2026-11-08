// Minimaler Service Worker: App-Hülle offline verfügbar, API-Daten immer frisch vom Server.
const CACHE = "bissfest-v54";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/static/logo.png", "/static/favicon-64.png", "/static/manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // API, Bestätigungslinks und Reset-Links (Token in der Adresse) nie anfassen oder cachen
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/verify") || url.searchParams.has("reset")) return;
  // Netzwerk zuerst, Cache als Fallback
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});

// Push-Erinnerungen anzeigen
self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch { data = { body: e.data && e.data.text() }; }
  e.waitUntil(
    self.registration.showNotification(data.title || "Bissfest", {
      body: data.body || "",
      tag: data.tag,
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png",
      data: { url: data.url || "/" },
    })
  );
});

// Tippen auf die Nachricht öffnet die App (oder holt ein offenes Fenster nach vorn)
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) if ("focus" in c) return c.focus();
      return self.clients.openWindow(url);
    })
  );
});
