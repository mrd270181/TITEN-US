// TITEN US - service worker: aplikasi & data tetap bisa dibuka saat offline.
// Selalu coba jaringan dulu (supaya pembaruan langsung terlihat), simpan salinannya, pakai salinan bila offline.
const SIMPAN = "titen-us-v1";
const INTI = ["./", "index.html", "manifest.webmanifest", "ikon.svg", "ikon-192.png", "ikon-512.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(SIMPAN).then(c => c.addAll(INTI)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(k => Promise.all(k.filter(n => n !== SIMPAN).map(n => caches.delete(n)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;
  const kunci = u.pathname.includes("/data/") ? u.origin + u.pathname : e.request;   // data: abaikan ?v=
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok) { const salin = r.clone(); caches.open(SIMPAN).then(c => c.put(kunci, salin)); }
    return r;
  }).catch(() => caches.match(kunci).then(r => r || caches.match("index.html"))));
});
