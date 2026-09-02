/**
 * JuziGenius service worker — deliberately network-only.
 *
 * It exists for exactly one reason: a registered worker with a fetch handler
 * is part of the browser's criteria for offering "Install app", which is what
 * gives JuziGenius a home-screen icon and its own window instead of a browser
 * tab.
 *
 * It caches nothing, ever. JuziGenius is a hosted service — practice data
 * lives in the account's brain.json on the server, so an app that "worked"
 * without reaching the server could only ever show a shell with no sentences
 * in it, and would additionally risk serving stale code after a deploy. An
 * earlier version of this file precached the app shell; that offline
 * capability was removed on purpose. If you are reintroducing caching here,
 * that is a product decision, not a performance tweak.
 *
 * Every request goes straight to the network and any failure propagates
 * untouched, so the page's own error handling reports it.
 */

self.addEventListener("install", () => {
    // Nothing to precache. Take over immediately so a previously installed
    // caching worker is replaced on the next load rather than lingering.
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        // Delete every cache this origin holds, including the shell and stroke
        // caches written by the previous version of this worker. Without this,
        // anyone who loaded the app while that version was live would keep a
        // working offline copy indefinitely.
        caches.keys()
            .then(keys => Promise.all(keys.map(key => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

// A fetch handler is required for installability. This one adds no behaviour:
// it is the browser's own default, spelled out.
self.addEventListener("fetch", (event) => {
    event.respondWith(fetch(event.request));
});
