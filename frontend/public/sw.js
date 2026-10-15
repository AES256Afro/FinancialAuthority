// The shell, offline: the app's own files are kept so the page opens on the phone without the server;
// every call to the server itself goes to the network, never to a cache, so no figure is ever stale.
const SHELL = "fa-shell-v2";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(["/", "/favicon.svg", "/icon.svg", "/icon-192.png", "/apple-touch-icon.png", "/manifest.webmanifest"]).catch(() => undefined)));
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

// Push from the app itself: show it, and open the page it points at when tapped.
self.addEventListener("push", (event) => {
  let data = { title: "FinancialAuthority", body: "", link: "/today", tag: "fa" };
  try {
    data = { ...data, ...(event.data ? event.data.json() : {}) };
  } catch {
    data.body = event.data ? event.data.text() : "";
  }
  event.waitUntil(self.registration.showNotification(data.title, { body: data.body, tag: data.tag, icon: "/icon-192.png", badge: "/icon-192.png", data: { link: data.link } }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.link) || "/today", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    const open = wins.find((w) => w.url.startsWith(self.location.origin));
    if (open) return open.navigate(target).then((w) => (w || open).focus());
    return self.clients.openWindow(target);
  }));
});
