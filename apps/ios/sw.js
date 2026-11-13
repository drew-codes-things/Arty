const CACHE = "arty-ios-v11";
const SHELL = [
  "./", "index.html", "app.css", "manifest.webmanifest",
  "js/app.js", "js/answerguard.js", "js/api.js", "js/authorise.js", "js/chat.js", "js/computers.js", "js/conversations.js", "js/dom.js", "js/links.js", "js/manage.js",
  "js/markdown.js", "js/notifications.js", "js/phone.js", "js/qr.js", "js/remote.js", "js/scanner.js", "js/settings.js", "js/setup.js", "js/setupcode.js", "js/tasks.js", "js/updates.js",
  "fonts/plex_sans_regular.ttf", "fonts/plex_sans_medium.ttf", "fonts/plex_sans_semibold.ttf", "fonts/plex_mono_regular.ttf",
  "icons/icon-180.png", "icons/icon-192.png", "icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((names) => Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name))))
    .then(() => self.clients.claim()));
});

function isAppFile(request) {
  const url = new URL(request.url);
  return request.method === "GET" && url.origin === self.location.origin && url.pathname.startsWith(new URL("./", self.location).pathname);
}

function withoutQuery(request) {
  const url = new URL(request.url);
  url.search = "";
  return url.href;
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(withoutQuery(request), response.clone());
    return response;
  } catch (error) {
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  if (isAppFile(event.request)) event.respondWith(networkFirst(event.request));
});
