/* Network-only workspace: never persist authenticated HTML, APIs, or personal data.
 * Only a generic offline document is cached. Updates wait for open clients to close.
 */
const CACHE = 'dear-diane-offline-v1';
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.add('/app/offline.html')));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith('dear-diane-offline-') && key !== CACHE)
      .map(key => caches.delete(key)),
  )));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || event.request.mode !== 'navigate'
      || url.origin !== self.location.origin || !['/', '/index.html'].includes(url.pathname)) return;
  event.respondWith(fetch(event.request).catch(async () =>
    (await caches.match('/app/offline.html')) || Response.error(),
  ));
});
