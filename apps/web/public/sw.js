// Service worker: guarda a "casca" do app para abrir rápido e sem rede.
// Dados da API nunca vão para o cache (são autenticados e mudam o tempo todo).
// Ao mudar qualquer arquivo da lista, suba a VERSION: é ela que força a atualização.

const VERSION = "alerta-v0.1.0";
const SHELL = [
  "/",
  "/index.html",
  "/styles.css",
  "/js/app.js",
  "/js/api.js",
  "/js/charts.js",
  "/js/format.js",
  "/manifest.webmanifest",
  "/fonts/inter.woff2",
  "/fonts/space-grotesk.woff2",
  "/icons/icon.svg",
  "/icons/icon-192.png",
  "/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== VERSION).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) {
    return; // rede direto
  }
  if (event.request.mode === "navigate") {
    // HTML: rede primeiro (pega versão nova), cache se estiver offline
    event.respondWith(fetch(event.request).catch(() => caches.match("/index.html")));
    return;
  }
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
});
