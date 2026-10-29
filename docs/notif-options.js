// Les options d'une notification push, en fonction du « ne pas déranger ».
// Fichier à part, chargé par sw.js (importScripts) ET par les tests : la
// fonction est pure, donc éprouvable sans service worker.
//
// Le silence dure 2 h, et une seule durée (Antoni, 27/10/2026) : un silence
// oublié la nuit de la sortie coûterait plus cher que le confort d'un choix.
const SILENCE_DUREE_MS = 2 * 3600 * 1000;

// Actif tant que `maintenant` est STRICTEMENT avant l'échéance. Une échéance
// plus lointaine que la durée permise est illisible ou corrompue : on
// l'ignore plutôt que de rendre le téléphone muet pour des années.
function silenceActif(jusqua, maintenant){
  if(typeof jusqua !== "number" || !isFinite(jusqua)) return false;
  if(typeof maintenant !== "number" || !isFinite(maintenant)) return false;
  return maintenant < jusqua && jusqua - maintenant <= SILENCE_DUREE_MS;
}

function optionsNotification(contenu, jusqua, maintenant){
  const options = {
    body: contenu.body,
    icon: "icon-192.png",
    // Le badge doit garder un canal alpha : voir icon-badge.png, et le test
    // qui le vérifie.
    badge: "icon-badge.png",
    // Un tag identique remplace la notification précédente au lieu
    // d'empiler douze bannières après une nuit sans regarder le téléphone.
    tag: contenu.tag,
    data: { url: contenu.url }
  };
  if(silenceActif(jusqua, maintenant)){
    // LA NOTIFICATION RESTE VISIBLE : le navigateur révoque l'abonnement d'un
    // push qui n'affiche rien. Seuls le son et la vibration sont coupés.
    // `silent` AVEC `vibrate` lève une TypeError : aucun des deux ne reste
    // en cas de silence, pas plus que requireInteraction ou renotify.
    options.silent = true;
    options.renotify = false;
    return options;
  }
  options.renotify = true;
  if(contenu.officiel){
    // Une annonce de Rockstar doit se reconnaître sans lire le texte
    // (Antoni, 22/09/2026) : vibration double, bannière qui reste.
    options.vibrate = [200, 100, 200];
    options.requireInteraction = true;
  }
  return options;
}

// Le journal de diagnostic du push (29/10/2026). L'abonnement d'Antoni est mort
// moins de 35 minutes après un envoi accepté (10:29 → 11:02 Paris), sans qu'il
// touche à rien : le robot sait QUAND Google répond 410, pas ce que le
// téléphone a vu. Le service worker note donc ce qu'il reçoit, et la page ce
// qu'elle trouve à l'ouverture. Quarante lignes au plus, les plus récentes.
const TRACE_MAX = 40;

// Les constats de la page (`unique`) ne sont pas répétés d'une ouverture à la
// suivante : trois ouvertures sans changement ne doivent pas chasser du
// journal l'événement utile. Deux push de suite, eux, sont deux événements.
// Un contenu illisible (autre chose qu'une liste) repart de zéro.
function ajouteTrace(liste, entree, maximum){
  const base = Array.isArray(liste)
    ? liste.filter((x) => x && typeof x.t === "number" && typeof x.e === "string")
    : [];
  const dernier = base[base.length - 1];
  if(entree.unique === true && dernier && dernier.e === entree.e) return base.slice(-(maximum || TRACE_MAX));
  base.push({ t: entree.t, e: entree.e });
  return base.slice(-(maximum || TRACE_MAX));
}

if(typeof module !== "undefined") module.exports = { silenceActif, optionsNotification, SILENCE_DUREE_MS, ajouteTrace, TRACE_MAX };
