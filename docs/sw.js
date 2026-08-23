// Service worker — condition technique requise par Chrome pour proposer
// "Installer" au lieu de la griser. Intercepte volontairement les requêtes
// de navigation (ouverture de la page) pour forcer un rechargement réseau
// à chaque fois, plutôt que de risquer que Chrome serve une ancienne
// version en cache de index.html — le tracker doit toujours refléter les
// derniers articles, jamais une page figée.

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  // Seulement pour les requêtes de navigation (ouverture/rechargement de la
  // page elle-même) : on force un vrai aller-retour réseau, jamais de cache.
  if(event.request.mode === "navigate"){
    event.respondWith(
      fetch(event.request, { cache: "no-store" }).catch(() => fetch(event.request))
    );
  }
  // Toutes les autres requêtes (feed.json, favicons, etc.) passent
  // normalement, sans interception ni mise en cache de notre part.
});
