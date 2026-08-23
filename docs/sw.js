// Service worker minimal — sa seule vraie utilité ici est de satisfaire la
// condition technique de Chrome pour proposer "Installer" au lieu de la
// griser. Le tracker a besoin d'internet à chaque vérification (proxys,
// backend GitHub), donc pas de vraie logique de cache hors-ligne ici —
// juste une installation basique et transparente.

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Laisse passer toutes les requêtes normalement, sans interception —
// le fetch en direct reste le comportement voulu.
self.addEventListener("fetch", (event) => {
  // Intentionnellement vide : pas d'interception, pas de cache.
});
