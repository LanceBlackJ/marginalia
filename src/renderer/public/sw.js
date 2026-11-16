// Marginalia 手机端使用 network-first，电脑端更新后不会长期卡在旧缓存。
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return
  const url = new URL(event.request.url)
  if (url.origin !== self.location.origin) return
  event.respondWith(fetch(event.request, { cache: 'no-store' }).catch(() => caches.match(event.request)))
})
