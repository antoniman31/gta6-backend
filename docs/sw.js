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
const CACHE_NAME = "gta6watch-shell-v90";
const SHELL_URL = "./index.html";

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
    caches.open(CACHE_NAME).then((cache) => cache.add(SHELL_URL))
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
