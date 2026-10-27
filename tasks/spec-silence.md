# Spec : « Ne pas déranger » propre à l'app

Approuvée par Antoni le 27/10/2026. **Une seule durée : 2 heures.**

## Objectif

Rendre les notifications de l'app silencieuses pendant 2 h, sans perdre
l'abonnement push.

## Mécanique

- L'app écrit « silencieux jusqu'à telle heure » dans un stockage que le
  service worker sait lire (il ne lit pas `localStorage`) : le Cache API,
  dans un cache à part, `gta6watch-prefs`.
- **`activate` de `sw.js` supprime tous les caches sauf `CACHE_NAME`** : sans
  exemption pour `gta6watch-prefs`, chaque déploiement effacerait le réglage.
- Une fonction pure `optionsNotification(contenu, jusqua, maintenant)`, dans
  `docs/notif-options.js`, compose les options. En silence : `silent: true`,
  **pas de `vibrate`** (les deux ensemble donnent une `TypeError`), pas de
  `requireInteraction`, `renotify` coupé.
- La notification reste **visible** : le navigateur l'exige, sinon il révoque
  l'abonnement (voir le commentaire du `push` dans `sw.js`).
- Réglage : bouton « Silence 2 h » dans les réglages push, et pastille 😴 dans
  l'en-tête avec l'heure de fin, qu'un appui annule.

## Réussite

- Tests de la fonction pure : réglage actif, expiré, absent ; officiel et
  routine ; jamais `vibrate` avec `silent` ; l'échéance à la seconde.
- Le réglage survit à un déploiement : retirer l'exemption fait échouer le test.
- Essai réel sur l'Android d'Antoni (Chrome) pendant le silence : notification
  visible, ni son ni vibration ; normale après l'échéance.

## Limites

- **Toujours** : la notification reste visible.
- **Demander d'abord** : envoyer un push de test (`test-push.yml`) sur son téléphone.
- **Jamais** : retenir une notification ; toucher aux abonnements ou à la
  pause nocturne côté robot.

## Non vérifié

MDN est injoignable depuis le conteneur : l'option `silent` n'a été lue que
dans des extraits de recherche, et le comportement de `renotify` combiné à
`silent` est inconnu. L'essai réel tranche.

## Risque

Un silence oublié la nuit de la sortie : une seule durée, et une pastille
visible avec l'heure de fin.
