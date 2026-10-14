// The shell, offline: the app's own files are kept so the page opens on the phone without the server;
// every call to the server itself goes to the network, never to a cache, so no figure is ever stale.
const SHELL = "fa-shell-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(["/", "/favicon.svg", "/icon.svg", "/manifest.webmanifest"]).catch(() => undefined)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api") || url.pathname.startsWith("/mcp") || url.pathname.startsWith("/oauth")) return;
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(caches.open(SHELL).then(async (cache) => (await cache.match(event.request)) ?? fetch(event.request).then((response) => {
      if (response.ok) cache.put(event.request, response.clone());
      return response;
    })));
    return;
  }
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).then((response) => {
      caches.open(SHELL).then((cache) => cache.put("/", response.clone()));
      return response;
    }).catch(() => caches.match("/")));
  }
});
