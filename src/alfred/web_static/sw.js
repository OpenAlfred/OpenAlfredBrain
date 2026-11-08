// Service Worker: Die Oberfläche kommt auch ohne Netz, Daten immer frisch vom Server.
const CACHE = "alfred-v5";
const SHELL = [
  "./",
  "static/css/tokens.css", "static/css/base.css", "static/css/components.css", "static/css/views.css",
  "static/css/ernaehrung.css", "static/css/haushalt.css", "static/css/gedaechtnis.css", "static/css/system.css",
  "static/js/app.js", "static/js/core.js", "static/js/aufnahme.js", "static/js/design.js", "static/js/teile.js", "static/js/charts.js", "static/js/palette.js",
  "static/js/views/zentrale.js", "static/js/views/chat.js", "static/js/views/heute.js", "static/js/views/aufgaben.js",
  "static/js/views/auftraege.js", "static/js/views/einkauf.js", "static/js/views/ernaehrung.js", "static/js/views/haushalt.js",
  "static/js/views/faehigkeiten.js", "static/js/views/gedaechtnis.js", "static/js/views/system.js",
  "static/icon-192.png", "manifest.webmanifest",
];
// Eine neue Fassung übernimmt sofort, nicht erst, wenn alle Fenster der App zu sind.
self.addEventListener("install", (ev) => {
  self.skipWaiting();
  // Einzeln statt addAll: Fehlt eine Datei, soll nicht die ganze Installation scheitern.
  ev.waitUntil(caches.open(CACHE).then((c) => Promise.all(SHELL.map((url) => c.add(url).catch(() => {})))));
});
self.addEventListener("activate", (ev) => ev.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", (ev) => {
  const url = new URL(ev.request.url);
  if (url.pathname.includes("/api/") || ev.request.method !== "GET") return;
  // no-cache: beim Server nachfragen statt die Kopie des Browsers zu nehmen. Sonst lief nach
  // einem Update noch minutenlang das alte app.js.
  ev.respondWith(fetch(ev.request, { cache: "no-cache" }).then(async (antwort) => {
    if (antwort.ok) {
      const kopie = antwort.clone();
      caches.open(CACHE).then((c) => c.put(ev.request, kopie));
      return antwort;
    }
    // Ein Serverfehler (502 vom Proxy, während Alfred neu startet) soll die geladene Oberfläche nicht zerlegen: lieber die Kopie.
    if (antwort.status >= 500) {
      const kopie = await caches.match(ev.request);
      if (kopie) return kopie;
    }
    return antwort;
  }).catch(() => caches.match(ev.request)));
});
