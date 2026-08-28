// Service worker — condition technique requise par Chrome pour proposer
// "Installer" au lieu de la griser. Stratégie : toujours essayer le réseau
// en premier (pour avoir la dernière version du squelette de l'app), mais
// se replier sur une copie en cache si le réseau échoue — pour que l'app
// s'ouvre au moins hors-ligne et affiche ce que localStorage contient déjà,
// plutôt qu'un échec de chargement total. IMPORTANT : ce cache ne concerne
// QUE le fichier HTML lui-même (le squelette de l'app), jamais feed.json —
// les données d'actualité doivent toujours venir du réseau, sinon l'app
// afficherait silencieusement une actu périmée en la faisant passer pour
// à jour.

// v2 : bumpé après une refonte importante d'index.html (lots B à E).
// Le service worker sert le squelette en réseau-d'abord, donc l'app se met
// déjà à jour dès qu'il y a du réseau — mais changer le nom du cache force
// le remplacement de l'ancien service worker et de la copie hors-ligne, ce
// qui évite de revivre l'incident de cache tenace jamais élucidé.
const CACHE_NAME = "gta6watch-shell-v2";
const SHELL_URL = "./index.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.add(SHELL_URL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  // Seulement pour les requêtes de navigation (ouverture/rechargement de la
  // page elle-même) : réseau en priorité, cache en secours si hors-ligne.
  if(event.request.mode === "navigate"){
    event.respondWith(
      fetch(event.request, { cache: "no-store" })
        .then((response) => {
          // Ne met en cache que les réponses réussies (200-299) — une
          // erreur serveur (404, 500...) ne doit jamais devenir la version
          // servie hors-ligne au prochain accès sans réseau.
          if(response.ok){
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(SHELL_URL, copy));
          }
          return response;
        })
        .catch(() => caches.match(SHELL_URL))
    );
  }
  // Toutes les autres requêtes (feed.json, favicons, etc.) passent
  // normalement, sans interception ni mise en cache de notre part — les
  // données d'actualité ne doivent jamais être servies depuis un cache.
});
