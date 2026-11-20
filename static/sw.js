// Service worker for Tattoo Studio Pro PWA
const CACHE_NAME = 'tattoo-studio-v1';

self.addEventListener('install', (e) => {
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (e) => {
  // Network first for all requests
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});

