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

// v66 : les notifications push sont retirées (03/11/2026, décision d'Antoni :
// trois abonnements morts en 33 h au plus, sans cause trouvée, et Discord porte
// les mêmes liens). Changer le nom du cache force le remplacement de l'ancien
// service worker sur les téléphones déjà installés : sans cela il resterait
// actif avec ses gestionnaires push, son « ne pas déranger » et son journal.
//
// Historique : v4 a corrigé le badge de notification (carré blanc dans la barre
// d'état Android) ; v3 a ajouté le push. Le nom change à chaque modification de
// docs/index.html (règle du dépôt) : le service worker sert le squelette en
// réseau-d'abord, donc l'app se met déjà à jour dès qu'il y a du réseau, mais un
// nouveau nom évite de revivre l'incident de cache tenace jamais élucidé.
// v117 (16/11/2026) : DEUX défauts mesurés le même jour sur le vrai service worker (voir le test « coque »).
// (1) Une erreur serveur 5xx sur la page de l'app remplaçait l'app par la page d'erreur (503 : « Service Unavailable », zéro carte) : seul
//     un échec RÉSEAU retombait sur la copie. Le soir du 19/11, un GitHub Pages saturé répond précisément par des 5xx.
// (2) TOUTE navigation de la portée était traitée comme la coque : ouvrir repetition.html (ou essai-confettis.html) écrasait la copie
//     d'index.html par cette page, et hors ligne l'app s'ouvrait sur la répétition, sans fil. Seule la page de l'app est désormais
//     interceptée ; les autres pages passent sans que le service worker y touche.
// Le changement de nom purge aussi, sur les téléphones, une copie déjà polluée.
// v118 (16/11/2026) : docs/index.html change (le lien « Liste de contrôle du jour J » des Réglages).
// v142 (21/11/2026) : les polices quittent Google Fonts pour docs/fonts/ ; le service worker les précache et les sert
// depuis le cache, pour que la page hors ligne ait enfin ses vraies polices (avant, elle retombait sur celles du système).
const CACHE_NAME = "gta6watch-shell-v158";
const SHELL_URL = "./index.html";
// Les noms ne changent pas d'une version à l'autre : une nouvelle police impose un nouveau CACHE_NAME, qui purge l'ancienne.
const POLICES = ["./fonts/inter-latin-wght-normal.woff2", "./fonts/jetbrains-mono-latin-wght-normal.woff2"];

// La requête vise-t-elle une police du site ? Comparée au chemin complet, dans la portée, jamais à une autre origine.
function estUnePolice(requete){
  try{
    const portee = (self.registration && self.registration.scope) || self.location.href;
    const u = new URL(requete.url);
    return POLICES.some((p) => { const v = new URL(p, portee); return u.origin === v.origin && u.pathname === v.pathname; });
  }catch(e){
    return false;
  }
}

// La navigation est-elle celle de l'APP (la racine de la portée ou index.html) ? Les autres pages du dossier (repetition.html,
// essai-confettis.html) ne sont ni mises en cache ni remplacées par la coque.
function estLaCoque(requete){
  try{
    const portee = (self.registration && self.registration.scope) || self.location.href;
    const u = new URL(requete.url), racine = new URL("./", portee), index = new URL(SHELL_URL, portee);
    return u.origin === racine.origin && (u.pathname === racine.pathname || u.pathname === index.pathname);
  }catch(e){
    return false;
  }
}

// Le cache des réglages d'avant le 03/11/2026 (« ne pas déranger » et journal de
// diagnostic du push) : il n'a plus de lecteur. L'activation le supprime, comme
// tout cache dont le nom n'est pas CACHE_NAME.

// Ferme l'ancien abonnement push s'il en reste un. Antoni en avait un, mort côté
// Google (HTTP 410) mais encore présent dans le navigateur : sans cela le
// téléphone garderait pour toujours une inscription que plus rien n'écoute.
// Sans effet quand il n'y en a pas, et jamais bloquant : une erreur est avalée.
// Ce nettoyage ne sert qu'une fois ; il peut être supprimé quand tous les
// appareils ont reçu cette version.
function retireAncienAbonnementPush(){
  try{
    if(!self.registration || !self.registration.pushManager) return Promise.resolve(false);
    return self.registration.pushManager.getSubscription()
      .then((abonnement) => (abonnement ? abonnement.unsubscribe() : false))
      .catch(() => false);
  }catch(e){
    return Promise.resolve(false);
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      // La coque d'abord, obligatoire. Les polices ensuite, sans bloquer l'installation si l'une manque : la page vit
      // très bien avec les polices du système.
      cache.add(SHELL_URL).then(() => cache.addAll(POLICES).catch(() => {}))
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    ).then(() => retireAncienAbonnementPush())
     .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  // Seulement pour les requêtes de navigation (ouverture/rechargement de la
  // page elle-même) : réseau en priorité, cache en secours si hors-ligne.
  if(event.request.mode === "navigate" && estLaCoque(event.request)){
    event.respondWith(
      fetch(event.request, { cache: "no-store" })
        .then((response) => {
          // Ne met en cache que les réponses réussies (200-299) — une
          // erreur serveur (404, 500...) ne doit jamais devenir la version
          // servie hors-ligne au prochain accès sans réseau.
          if(response.ok){
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(SHELL_URL, copy));
            return response;
          }
          // Une erreur SERVEUR (5xx) retombe aussi sur la copie, comme un échec réseau : mieux vaut l'app d'hier que la page d'erreur.
          // Sans copie, la réponse d'erreur passe telle quelle. Les 4xx (404…) ne retombent pas : ce n'est pas une panne.
          if(response.status >= 500){
            return caches.match(SHELL_URL).then((copie) => copie || response);
          }
          return response;
        })
        .catch(() => caches.match(SHELL_URL))
    );
  }
  // Les polices : le cache d'abord (elles ne changent qu'avec CACHE_NAME), le réseau en secours, mis en cache s'il répond.
  else if(estUnePolice(event.request)){
    event.respondWith(
      caches.match(event.request).then((copie) => copie || fetch(event.request).then((response) => {
        if(response.ok){
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        }
        return response;
      }))
    );
  }
  // Toutes les autres requêtes (feed.json, favicons, etc.) passent
  // normalement, sans interception ni mise en cache de notre part — les
  // données d'actualité ne doivent jamais être servies depuis un cache.
});
