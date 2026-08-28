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

// v3 : bumpé à l'ajout des notifications push (lot H). Le service worker
// sert le squelette en réseau-d'abord, donc l'app se met déjà à jour dès
// qu'il y a du réseau — mais changer le nom du cache force le remplacement
// de l'ancien service worker, ce qui évite de revivre l'incident de cache
// tenace jamais élucidé. Ici c'est indispensable : sans remplacement,
// l'ancien service worker resterait actif et ignorerait les push.
const CACHE_NAME = "gta6watch-shell-v3";
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

// ---------------------------------------------------------------------------
// Notifications push
// ---------------------------------------------------------------------------
// Envoyées par le workflow GitHub Actions après chaque publication réussie
// (voir push_notify.py). Le service worker est le seul endroit où le
// navigateur accepte de les recevoir — y compris quand l'app est fermée.

self.addEventListener("push", (event) => {
  // Contenu par défaut si le message arrive vide ou illisible : le
  // navigateur EXIGE qu'un push affiche une notification visible, sous
  // peine de révoquer l'abonnement. Mieux vaut une notification vague
  // qu'aucune.
  let contenu = {
    title: "GTA6_WATCH",
    body: "De nouveaux articles sont disponibles.",
    url: "./index.html",
    tag: "gta6watch-nouveaux"
  };

  if (event.data) {
    try {
      contenu = Object.assign(contenu, event.data.json());
    } catch (e) {
      const texte = event.data.text();
      if (texte) contenu.body = texte;
    }
  }

  event.waitUntil(
    self.registration.showNotification(contenu.title, {
      body: contenu.body,
      icon: "icon-192.png",
      badge: "icon-192.png",
      // Un tag identique remplace la notification précédente au lieu
      // d'empiler douze bannières après une nuit sans regarder le téléphone.
      tag: contenu.tag,
      renotify: true,
      data: { url: contenu.url }
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const cible = (event.notification.data && event.notification.data.url) || "./index.html";

  // Si l'app est déjà ouverte quelque part, on la ramène au premier plan
  // plutôt que d'ouvrir un second onglet.
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url.includes("index.html") && "focus" in client) {
          return client.focus();
        }
      }
      return self.clients.openWindow(cible);
    })
  );
});
