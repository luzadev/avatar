// Service worker minimo: rende l'app installabile; la pagina resta sempre aggiornata dalla rete.
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => self.clients.claim());
self.addEventListener('fetch', e => {});
