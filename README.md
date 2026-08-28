# GTA6_WATCH

Veille automatisée de l'actualité GTA 6 : un robot va chercher les news sur
35 sources deux fois par heure, décode les vrais liens Google News, récupère
de vraies miniatures, notifie sur Discord, et publie tout dans une app
installable sur Android.

**App en ligne :** https://antoniman31.github.io/gta6-backend/

## Architecture

Trois briques, aucun serveur à gérer :

```
GitHub Actions (cron : 2 passages/heure)
        │
        ▼
  fetch_feeds.py  ──►  docs/feed.json  ──►  GitHub Pages  ──►  docs/index.html (PWA)
        │                    ▲
        │                    │ merge_feed.py (fusion si push concurrent)
        ▼
  discord_notify.py (récapitulatif, APRÈS publication réussie)
```

Le robot Python tourne côté GitHub, écrit un fichier JSON statique, et
GitHub Pages le sert directement — aucune base de données, aucun serveur à
maintenir, hébergement gratuit et illimité pour ce volume.

## Le robot — `fetch_feeds.py`

Tourne automatiquement deux fois par heure (`cron: "7,37 * * * *"`), ou
manuellement via l'onglet Actions → "Mise à jour des flux GTA 6" → Run
workflow.

Le déclencheur `schedule` de GitHub est *best-effort* : les runs partent
avec 10 à 35 minutes de retard et sont purement abandonnés en période de
charge — la minute 0 étant la plus congestionnée. Fin août 2026 la cadence
réelle était tombée à un run toutes les 3 à 9 heures. Deux tentatives par
heure à des minutes creuses ne suppriment pas les abandons (c'est un
contournement côté GitHub, pas un correctif) mais ramènent la cadence
effective près de l'heure.

**Ce qu'il fait, dans l'ordre :**

1. **Charge l'historique existant** depuis `docs/feed.json` — le robot ne
   repart jamais de zéro, il ajoute au fil du temps.
2. **Récupère les 35 sources** (liste `FEEDS`), avec gestion d'erreur par
   source : si une source échoue, les 34 autres continuent normalement.
3. **Filtre par mots-clés** — les sources officielles (Rockstar, Take-Two)
   exigent un mot-clé GTA 6 dans le titre. Les sources "spécialistes"
   (`specialist_source: True` — RockstarMag, RockstarINTEL, GTA6 Times,
   GTA6 x Netflix) appliquent le même filtre que les sources normales :
   ce ne sont pas des flux sans filtre, juste des sites plus ciblés sur la
   série GTA en général (donc encore susceptibles de publier du contenu
   GTA Online/FiveM/RP qu'il faut écarter).
4. **Décode les liens Google News** — ces flux renvoient normalement des
   liens de redirection chiffrés (`news.google.com/rss/articles/...`),
   inutilisables pour aller chercher une vraie miniature. Le module
   `googlenewsdecoder` résout le vrai lien de l'article.
5. **Récupère les miniatures manquantes** en parallèle (5 requêtes
   simultanées via `ThreadPoolExecutor`) — d'abord depuis le flux RSS
   lui-même si présente, sinon en allant chercher la balise `og:image` ou
   `twitter:image` sur la vraie page de l'article.
6. **Déduplique** — par lien exact (lookup instantané via un `set`), puis
   par similarité de titre (`SequenceMatcher`, seuil 75%) sur les 200
   articles les plus récents seulement — comparer un nouvel article à un
   autre vieux de plusieurs mois n'a jamais de sens en pratique, et ça
   évite que le temps de calcul augmente indéfiniment avec l'historique.
7. **Plafonne l'historique à 2000 articles** — au-delà, les plus anciens
   sont retirés. Ce n'est donc pas un historique complet et permanent,
   mais un historique glissant assez large pour ne jamais perdre l'actu
   récente ou moyennement récente.
8. **Dépose les nouveaux articles** dans le fichier désigné par
   `$NEW_ITEMS_FILE` (hors du dépôt), à destination de
   `discord_notify.py`. Le robot n'envoie plus lui-même la notification :
   voir la section Notifications. Rien n'est déposé au tout premier
   lancement (l'historique est vide, donc "tout" serait considéré comme
   nouveau).
9. **Écrit `docs/feed.json`** avec l'historique complet, les métadonnées
   (date de génération, nombre d'articles), et la liste des sources (pour
   que le tracker HTML puisse afficher leurs noms sans maintenir sa
   propre copie séparée — voir la limite ci-dessous sur cette
   synchronisation).

## Le workflow — `.github/workflows/update-feeds.yml`

- **`concurrency: group: update-feeds, cancel-in-progress: false`** —
  empêche deux exécutions de tourner en même temps (ex: une automatique
  horaire qui démarre pendant qu'une relance manuelle tourne encore) ; la
  seconde attend en file d'attente au lieu de risquer un conflit de push.
- **`timeout-minutes: 20`** — le job dure normalement ~5 minutes ; grande
  marge de sécurité si un site traîne anormalement, sans risquer qu'une
  exécution bloquée tourne indéfiniment.
- **`ref: main` au checkout** — un run planifié peut attendre 35 minutes
  en file avant de démarrer ; sans ça il repartirait du SHA figé au moment
  de son déclenchement, donc d'un dépôt périmé.
- **Publication fusionnante** — l'étape de publication retente jusqu'à 5
  fois, et sur rejet elle fusionne les deux versions **au niveau des
  données** via `merge_feed.py` (union des articles par lien) au lieu de
  tenter un `git pull --rebase`. C'est indispensable : `docs/feed.json`
  est entièrement régénéré à chaque run, donc un rebase conflicte
  systématiquement, s'arrête, et — le shell tournant sous `bash -e` —
  tuait tout le job dès la première tentative en emportant son travail
  (runs du 26/08 03:42 et du 28/08 07:17). Aucun article n'est perdu,
  quel que soit le run qui l'a trouvé.
- **Notification après publication** — l'étape Discord ne s'exécute
  qu'une fois le push réellement réussi.
- **Commit systématique** — même quand seul l'horodatage
  (`generated_at`) a changé sans nouvel article, un commit est fait à
  chaque exécution. C'est un choix assumé : Git compresse les diffs
  (delta), donc le coût réel en espace disque reste minime malgré les
  ~8760 commits/an que ça représente ; l'alternative (ignorer
  `generated_at` dans la comparaison pour éviter ces commits) casserait
  l'indicateur de fraîcheur du tracker, qui a justement besoin que ce
  champ avance à chaque passage pour distinguer "le robot tourne mais ne
  trouve rien de neuf" de "le robot est en panne".

## Les modules partagés

- **`feed_store.py`** — le socle : interprétation des dates, tri,
  plafonnement, lecture/écriture de `docs/feed.json`. Aucun accès réseau,
  aucune dépendance externe. Ces trois règles doivent rester identiques
  entre le robot et l'outil de fusion, sous peine de corrompre
  l'historique — d'où le module commun.
- **`merge_feed.py`** — fusionne deux versions de `docs/feed.json` au
  niveau des données. Appelé par le workflow uniquement en cas de rejet
  de push.
- **`discord_notify.py`** — l'envoi Discord, appelé après publication.

### Tests

```
python test_pipeline.py
```

Sans dépendance ni réseau. Couvre les dates (les trois formats présents
dans l'historique), le tri, le plafonnement, la repasse rétroactive, et
surtout la fusion — c'est elle qui décide si des articles sont perdus
quand deux exécutions se chevauchent. Le dernier bloc rejoue ces règles
sur le vrai `docs/feed.json` du dépôt.

## L'app — `docs/index.html`

Une PWA autonome (HTML/CSS/JS dans un seul fichier, volontairement — voir
plus bas) qui lit `docs/feed.json` en priorité, avec un mode de secours.

**Fonctionnalités :** recherche, filtres par source/lu-non lu/nouveauté,
mode dense, pagination progressive (30 articles à la fois, pas 500 d'un
coup), regroupement par jour, badges (officiel, spécialiste, leak, vidéo,
FR), compte à rebours jusqu'au 19 novembre 2026 avec 4 paliers visuels
d'intensité croissante (normal → teinte orangée dès 30 jours → pulsation
orange dès 7 jours → mode urgence rouge/majuscules dernières 24h),
persistance locale (lu/non-lu, paramètres, thème) via `localStorage`.

**Mode de secours** : si `docs/feed.json` est inaccessible (backend en
panne, GitHub Pages indisponible), l'app bascule automatiquement sur un
ancien système de récupération directe des 35 sources via des proxys CORS
publics (CodeTabs, allorigins, corsproxy.io, whateverorigin, feed2json,
rss2json). C'est redondant avec le backend, mais volontaire : sans ce
filet de sécurité, l'app serait totalement inutilisable si le backend
tombait, ce qui serait une vraie régression de fiabilité pour un gain de
simplicité qui n'en vaut pas la peine.

## PWA — installabilité

- **`manifest.json`** — 4 icônes déclarées (`icon-192.png`, `icon-512.png`
  en usage normal, `icon-192-maskable.png`, `icon-512-maskable.png` pour
  Android qui peut rogner l'icône en cercle ou autre forme — la version
  maskable a son texte resserré dans une zone sûre pour ne jamais être
  coupée). Texte "GTA 6 WATCH" avec la police Bebas Neue (libre de droits,
  Open Font License) — jamais le logo officiel Rockstar, qui est une
  marque déposée non réutilisable.
- **`sw.js`** — service worker minimal qui met en cache uniquement le
  squelette HTML (network-first, cache en secours si hors-ligne, jamais
  l'inverse), avec vérification que la réponse est bien valide
  (`response.ok`) avant de la mettre en cache. `feed.json` (les vraies
  données d'actualité) n'est jamais mis en cache — toujours 100% réseau,
  pour ne jamais afficher silencieusement une actu périmée en la faisant
  passer pour à jour.

## Notifications — historique des deux systèmes testés

**Discord** — actif, fonctionnel, confirmé en conditions réelles.

UN SEUL message récapitulatif par exécution (nombre de nouveaux articles +
lien cliquable vers le site), jamais un message par article. Aucun envoi
s'il n'y a rien de neuf, aucun envoi au tout premier lancement. Jusqu'à 3
tentatives en cas d'erreur temporaire (429 rate-limit : le délai indiqué
par Discord est respecté ; 5xx serveur : backoff 2s/4s/8s) ; les erreurs
définitives ne sont pas retentées.

L'envoi est fait par `discord_notify.py`, dans une étape de workflow
distincte qui ne s'exécute qu'**après une publication réussie**. Le robot
se contente de déposer la liste des nouveaux articles dans
`$NEW_ITEMS_FILE`. Auparavant l'envoi partait de `fetch_feeds.py`, donc
avant le push : quand la publication échouait, Discord annonçait des
articles jamais publiés, que l'exécution suivante redétectait et
réannonçait. Conséquence assumée : un `python fetch_feeds.py` lancé à la
main en local ne notifie plus.

**ntfy.sh** — testé puis abandonné. Le serveur confirmait systématiquement
l'envoi (200 OK) mais ne relayait jamais les messages jusqu'au téléphone,
même avec un payload minimal (juste topic + message, sans aucun champ
optionnel). Cause probable, documentée par ntfy lui-même : fail2ban ou
rate-limit silencieux sur les adresses IP partagées des runners GitHub
Actions — un problème structurel, pas un souci de configuration. Le code
correspondant a été entièrement retiré (aucune trace, ni active ni en
commentaire).

## Limites connues et assumées

- **Historique glissant, pas permanent** — plafonné à 2000 articles
  (`MAX_HISTORY_SIZE` dans `feed_store.py`), pas un vrai historique
  complet depuis toujours.
- **Deux définitions de sources** — la liste `FEEDS` (Python, source de
  vérité) et `DEFAULT_FEEDS` (JS, utilisé uniquement par le mode de
  secours) doivent être synchronisées manuellement si une source est
  ajoutée ou retirée. Impossible à éliminer complètement sans casser
  l'autonomie du mode de secours, qui a justement besoin des vraies URLs
  même quand `feed.json` (qui pourrait autrement centraliser cette liste)
  est inaccessible.
- **Deux algorithmes de déduplication légèrement différents** — le
  backend Python utilise `SequenceMatcher`, le mode de secours JS une
  comparaison par tokens. Comme le backend est la source principale et le
  mode de secours n'intervient qu'en cas de panne, ce n'est pas un vrai
  risque pratique.
- **GTAForums et GTA Base** — jamais intégrés, ces deux sites bloquent
  activement les accès automatisés, y compris depuis un vrai serveur (pas
  seulement un navigateur).
- **Miniatures Google News** — un léger pourcentage d'articles n'a pas de
  miniature si le site source bloque les robots ou n'a pas de balise
  exploitable. Comportement normal, pas un bug.
- **Fichier HTML monolithique** — `index.html` regroupe CSS, HTML et JS
  dans un seul fichier de ~1600 lignes plutôt que d'être séparé en
  plusieurs fichiers. Choix assumé : ça simplifie l'upload manuel (un seul
  fichier à remplacer au lieu de plusieurs à garder synchronisés), au
  prix d'un fichier plus long à parcourir si besoin d'y retoucher.

## Ajuster quelque chose

- **Fréquence** : `.github/workflows/update-feeds.yml`, ligne `cron`
  (éviter la minute 0, la plus congestionnée côté GitHub)
- **Sources** : liste `FEEDS` dans `fetch_feeds.py` (penser à reporter
  tout changement dans `DEFAULT_FEEDS` côté `index.html`, voir limite
  ci-dessus)
- **Taille max de l'historique** : `MAX_HISTORY_SIZE` dans `feed_store.py`
  (partagé par le robot et l'outil de fusion, pour que les deux appliquent
  exactement la même règle)
- **Webhook Discord** : secret GitHub `DISCORD_WEBHOOK_URL` (Settings →
  Secrets and variables → Actions) — ne jamais partager cette URL en
  clair ; si elle fuite, la régénérer immédiatement côté Discord
- **Revenir au mode direct sans backend** : vider le champ backend dans
  les paramètres de l'app
