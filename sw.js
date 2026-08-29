/**
 * JuziGenius service worker.
 *
 * Two jobs, and it is deliberately conservative about both.
 *
 * 1. Make the app shell available with no network at all, so JuziGenius can be
 *    installed to a tablet home screen and opened on a train. The app was
 *    already offline-capable in the sense that it makes no external calls --
 *    but it still needed its own server reachable to load at all, which is a
 *    different thing from being installable.
 *
 * 2. Keep stroke data close at hand. /api/strokes is immutable per character
 *    (a character's strokes never change) and is the one request on the
 *    critical path of actually writing, so it is cached forever, cache-first.
 *
 * The shell is network-FIRST, not cache-first. This is a self-hosted app the
 * user updates by pulling and restarting; a cache-first shell would keep
 * serving yesterday's app.js after an update with nothing to explain why --
 * the same failure mode as the stale-cache problem the no-cache header fixed
 * on the server side. Network-first costs one conditional request per load on
 * a working connection and still works completely offline.
 *
 * Everything else -- the writable API (session, import, review, progress) --
 * is never cached. Those are per-account state; a stale answer would be worse
 * than an honest failure.
 */

const VERSION = "juzi-v1";
const SHELL_CACHE = `${VERSION}-shell`;
const STROKE_CACHE = `${VERSION}-strokes`;

// Kept in step with server.py's ALLOWED_STATIC_PATHS.
const SHELL_ASSETS = [
    "/",
    "/index.html",
    "/style.css",
    "/app.js",
    "/vendor/hanzi-writer.min.js",
    "/avatar-nobg-128.png",
    "/avatar-nobg.png",
    "/icon-192.png",
    "/icon-512.png",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(SHELL_CACHE)
            // addAll fails the whole install if any single asset 404s, which
            // would leave the app with no offline shell at all. Adding them
            // individually means one missing file costs only that file.
            .then(cache => Promise.all(
                SHELL_ASSETS.map(url => cache.add(url).catch(err => {
                    console.warn(`[sw] could not precache ${url}`, err);
                }))
            ))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(
                keys.filter(k => !k.startsWith(VERSION)).map(k => caches.delete(k))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") return;

    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;   // never touch the CDN fallback

    // Stroke data: immutable per character, and the one request standing
    // between the user and writing. Cache-first, kept indefinitely.
    if (url.pathname.endsWith("/api/strokes")) {
        event.respondWith(
            caches.open(STROKE_CACHE).then(cache =>
                cache.match(request).then(hit => hit || fetch(request).then(response => {
                    if (response.ok) cache.put(request, response.clone());
                    return response;
                }))
            )
        );
        return;
    }

    // Per-account state. Never cached: a stale session or progress reading
    // would be worse than a visible failure.
    if (url.pathname.includes("/api/")) return;

    // The shell: network-first so an update is picked up as soon as the server
    // is reachable, cache as the offline fallback.
    event.respondWith(
        fetch(request)
            .then(response => {
                if (response.ok) {
                    const copy = response.clone();
                    caches.open(SHELL_CACHE).then(cache => cache.put(request, copy));
                }
                return response;
            })
            .catch(() => caches.match(request).then(
                hit => hit || caches.match("/index.html")
            ))
    );
});
