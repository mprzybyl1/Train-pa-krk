/* Service worker: offline shell, ostatnie odjazdy z cache przy braku sieci, Web Push. */
const VERSION = "v1";
const SHELL = `shell-${VERSION}`;
const DATA = `data-${VERSION}`;
const SHELL_FILES = [
  "/", "/index.html", "/styles.css", "/app.js", "/manifest.webmanifest",
  "/icons/icon-192.png", "/icons/icon-512.png", "/icons/apple-touch-icon.png", "/icons/favicon-32.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key !== SHELL && key !== DATA) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

// odjazdy: najpierw sieć, przy błędzie ostatnia zapisana odpowiedź (z nagłówkiem X-From-Cache)
async function departures(request) {
  const cache = await caches.open(DATA);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request.url, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(request.url);
    if (!cached) throw err;
    const headers = new Headers(cached.headers);
    headers.set("X-From-Cache", "1");
    return new Response(await cached.blob(), { status: 200, headers });
  }
}

// powłoka aplikacji: stale-while-revalidate
async function shell(request) {
  const cache = await caches.open(SHELL);
  const cached = await cache.match(request, { ignoreSearch: request.mode === "navigate" });
  const network = fetch(request).then((r) => {
    if (r.ok) cache.put(request, r.clone());
    return r;
  }).catch(() => null);
  return cached || (await network) || (request.mode === "navigate" ? cache.match("/") : Response.error());
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname === "/api/departures") {
    event.respondWith(departures(event.request));
  } else if (!url.pathname.startsWith("/api/")) {
    event.respondWith(shell(event.request));
  }
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "Pociągi PZ ⇄ KRK", {
    body: data.body || "",
    tag: data.tag,
    renotify: true,
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    data: { url: data.url || "/" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of all) {
      if ("focus" in c) {
        await c.navigate(target).catch(() => {});
        return c.focus();
      }
    }
    return self.clients.openWindow(target);
  })());
});
