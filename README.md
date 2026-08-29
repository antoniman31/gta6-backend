# GTA6_WATCH

Veille automatisée de l'actualité GTA 6 : un robot interroge 49 sources en
parallèle toutes les 30 minutes, décode les vrais liens Google News, récupère
de vraies miniatures, notifie sur Discord et par notification push, et publie
tout dans une app installable sur Android.

Un passage complet dure **environ une minute**.

**App en ligne :** https://antoniman31.github.io/gta6-backend/

## Architecture

Trois briques, aucun serveur à gérer :

```
cron-job.org (toutes les 30 min)          ← horloge principale
        │  POST /dispatches
        ▼
GitHub Actions  ◄── cron GitHub "7,37"    ← filet de secours, best-effort
        │
        ▼
  fetch_feeds.py  ──►  docs/feed.json          ──►  GitHub Pages  ──►  docs/index.html (PWA)
        │          └─►  docs/feed-recent.json  ──►
        │                    ▲
        │                    │ merge_feed.py (fusion si push concurrent)
        ▼
  discord_notify.py + push_notify.py (APRÈS publication réussie)
        │
        ▼
  healthchecks.io (signal de vie ; l'absence de signal déclenche l'alerte)
```

Le robot Python tourne côté GitHub, écrit un fichier JSON statique, et
GitHub Pages le sert directement — aucune base de données, aucun serveur à
maintenir, hébergement gratuit et illimité pour ce volume.

## Le robot — `fetch_feeds.py`

Tourne toutes les 30 minutes, déclenché par un planificateur **externe**
(cron-job.org) — voir la section dédiée. Le `cron` de GitHub reste déclaré
comme filet de secours, et le déclenchement manuel reste possible via
l'onglet Actions → "Mise à jour des flux GTA 6" → Run workflow, ou depuis
l'app.

Pourquoi un planificateur externe : le déclencheur `schedule` de GitHub est
*best-effort*. Les runs partent avec 10 à 35 minutes de retard et sont
purement abandonnés en période de charge. Fin août 2026 la cadence réelle
était tombée à un run toutes les 3 à 9 heures ; décaler le cron à `7,37`
n'a rien changé — sur les 12 heures suivant ce changement, **zéro**
exécution planifiée n'est partie. C'est un contournement qui ne marche pas,
d'où le planificateur externe.

**Ce qu'il fait, dans l'ordre :**

1. **Charge l'historique existant** depuis `docs/feed.json` — le robot ne
   repart jamais de zéro, il ajoute au fil du temps.
2. **Récupère les 49 sources** (liste `FEEDS`) **en parallèle**, avec
   gestion d'erreur par source : si une source échoue, les 34 autres
   continuent normalement. Le détail du parallélisme est décrit plus bas
   (« Récupération en parallèle ») ; en séquentiel cette étape prenait
   3 min 04, elle prend maintenant ~35 s.
   La requête est **conditionnelle** : le robot renvoie l'`ETag` et le
   `Last-Modified` reçus au passage précédent, et le serveur répond `304`
   (quelques octets, sans corps) si rien n'a changé. Sans ça il
   retéléchargerait 35 flux entiers 48 fois par jour ; la documentation de
   feedparser prévient qu'un client qui ignore ces en-têtes peut se faire
   bannir par l'éditeur. Les validateurs sont conservés dans
   `feed_http_state` de `feed.json`, faute d'autre stockage persistant.
3. **La chaîne YouTube de Rockstar est la source primaire.** Un trailer sort
   là ; la presse en parle dix à trente minutes plus tard. Sans elle, le
   robot apprend l'événement par ceux qui le commentent. Deux réglages
   propres à cette source, sans lesquels elle serait inutile :
   - `official_domains` — ses liens pointent vers `youtube.com`, pas vers
     `rockstargames.com`. Avec la liste par défaut, la vérification de
     domaine (voir ci-dessous) lui retirerait son statut officiel **à
     chaque passage**, et les vidéos n'apparaîtraient jamais dans l'onglet
     Rockstar de l'app — qui filtre précisément sur ce statut.
   - `official_keywords_extra` — « trailer » s'ajoute aux mots-clés GTA 6.
     Une vidéo intitulée simplement « Trailer 3 » n'en contient aucun et
     serait rejetée : précisément le jour qui compte. Contrepartie assumée,
     quelques bandes-annonces GTA Online passeront aussi. Le filtre reste
     actif malgré tout (contrairement à RockstarMag) : la chaîne publie
     régulièrement du Red Dead et du GTA Online.
4. **Filtre par mots-clés** — les sources officielles (Rockstar, Take-Two)
   exigent un mot-clé GTA 6 dans le titre. Les sources "spécialistes"
   (`specialist_source: True` — RockstarMag, RockstarINTEL, GTA6 Times,
   GTA6 x Netflix) appliquent le même filtre que les sources normales :
   ce ne sont pas des flux sans filtre, juste des sites plus ciblés sur la
   série GTA en général (donc encore susceptibles de publier du contenu
   GTA Online/FiveM/RP qu'il faut écarter).
5. **Écarte les archives** — un article jamais vu dont la date dépasse
   `MAX_ARTICLE_AGE_DAYS` (45 jours) n'est pas importé. Un article de
   plusieurs mois découvert aujourd'hui n'est pas une nouvelle, et le robot
   l'annoncerait pourtant comme « nouvel article » sur Discord et sur le
   téléphone. Le cas s'est produit le 29/08/2026 en changeant le flux
   VG247 : une recherche Google News restreinte à un domaine classe par
   **pertinence, pas par date**, et a remonté 8 articles de 2022 à 2024,
   tous annoncés comme neufs. Sans ce garde-fou, ajouter une source revient
   à déverser ses archives.
   Ne s'applique qu'aux dates réellement lisibles : un flux sans date
   exploitable continue de passer, sinon on rejetterait tout son contenu en
   le prenant pour du 1ᵉʳ janvier 1970. Les archives écartées sont comptées
   dans le journal du passage — c'est le signe qu'une source est mal réglée.
6. **Décode les liens Google News** — ces flux renvoient normalement des
   liens de redirection chiffrés (`news.google.com/rss/articles/...`),
   inutilisables pour aller chercher une vraie miniature. Le module
   `googlenewsdecoder` résout le vrai lien de l'article.
7. **Récupère les miniatures manquantes** en parallèle (`IMAGE_WORKERS`,
   8 requêtes simultanées via `ThreadPoolExecutor`) — d'abord depuis le flux RSS
   lui-même si présente, sinon en allant chercher la balise `og:image` ou
   `twitter:image` sur la vraie page de l'article.
8. **Compte les sources et déduplique** — quand plusieurs rédactions
   couvrent la même actualité, les doublons ne sont plus jetés
   purement : la source supplémentaire est enregistrée dans
   `extraSources`. C'est la meilleure information disponible pour repérer
   une actualité majeure — un article isolé est en général une reprise ou
   de la supputation, quatre rédactions dans la foulée signalent un
   trailer, une date ou une annonce. L'app affiche un badge « 🔥 N
   SOURCES » au-delà de `hot_threshold` (4 par défaut).
   Les liens sont d'abord nettoyés de leurs paramètres de
   pistage (`utm_*`, `fbclid`, `gclid`…) et de leur ancre, qui ne changent
   jamais la page servie mais faisaient compter deux fois le même article
   partagé par deux canaux. Puis dédup par lien exact (lookup instantané
   via un `set`), puis par similarité de titre (`SequenceMatcher`, seuil
   75%) sur une **fenêtre** des articles les plus récents — comparer un
   nouvel article à un autre vieux de plusieurs mois n'a jamais de sens en
   pratique, et ça évite que le temps de calcul augmente indéfiniment avec
   l'historique.

   **Composition de la fenêtre** (corrigé le 29/08/2026) : les 200
   articles les plus récents de l'historique **plus ceux ajoutés pendant
   le passage en cours**. Ce second point manquait. Les nouveaux articles
   étaient ajoutés à la *fin* de la liste, donc hors des 200 premiers, et
   deux rédactions publiant le même sujet dans le même passage n'étaient
   jamais rapprochées dès que l'historique dépassait 200 articles. Mesuré
   avant correctif sur le vrai `feed.json` : 13 doublons manifestes dans
   les 400 articles les plus récents (dont des titres strictement
   identiques entre IGN et IGN France), et un compteur de sources plafonné
   à 3 — donc un badge 🔥 réglé sur 4 qui n'a jamais pu s'afficher une
   seule fois en 1 211 articles.
9. **Plafonne l'historique à 20 000 articles** (`MAX_HISTORY_SIZE`) — au-delà,
   les plus anciens sont retirés. Ce n'est donc pas un historique complet et
   permanent, mais un historique glissant très large. Au rythme observé
   (~50 articles/jour au maximum), le plafond ne sera pas atteint avant
   plus d'un an ; voir les Limites pour la conséquence sur le poids du
   fichier.
10. **Dépose les nouveaux articles** dans le fichier désigné par
   `$NEW_ITEMS_FILE` (hors du dépôt), à destination de
   `discord_notify.py`. Le robot n'envoie plus lui-même la notification :
   voir la section Notifications. Rien n'est déposé au tout premier
   lancement (l'historique est vide, donc "tout" serait considéré comme
   nouveau).
11. **Écrit aussi `docs/feed-recent.json`** — les 300 articles les plus
   récents (`RECENT_FEED_SIZE`). Mesuré le 29/08/2026 : 258 Ko bruts /
   65 Ko compressés, contre 896 Ko / 199 Ko pour l'historique complet.
   C'est ce fichier que l'app charge à l'ouverture ; elle télécharge le
   complet à la demande, et automatiquement dès qu'une recherche est
   lancée pour ne jamais renvoyer de résultats tronqués sans le dire. Les
   deux fichiers sont toujours écrits ensemble, y compris après une
   fusion de conflit.
12. **Dresse l'état de chaque source** (`sources_health` dans `feed.json`) —
   une source « muette » n'a renvoyé aucune entrée brute, signe net d'un
   flux cassé ; une source « tarie » répond mais n'a rien publié depuis
   plus de 30 jours, ce qui peut être parfaitement normal (Rockstar et
   Take-Two communiquent peu).
   **Et alerte sur Discord quand une source tombe** — voir la section
   dédiée plus bas. L'état seul ne dit que « muette maintenant » ; c'est le
   cumul (`sources_silence`) qui distingue une panne d'un hoquet.
13. **Écrit `docs/feed.json`** avec l'historique complet, les métadonnées
   (date de génération, nombre d'articles), et la liste des sources (pour
   que le tracker HTML puisse afficher leurs noms sans maintenir sa
   propre copie séparée — voir la limite ci-dessous sur cette
   synchronisation).

## Récupération en parallèle

Interroger 35 sources l'une après l'autre coûtait **3 min 04** par passage :
35 allers-retours réseau en file indienne, plus une seconde de pause entre
chaque source. C'était la quasi-totalité du temps d'exécution.

Les sources sont désormais réparties en **files d'attente par domaine**, et
ces files tournent en parallèle :

```
rss.app        →  file 1 ─┐
(10 flux)         file 2 ─┤
                  file 3 ─┤
news.google    →  file 4 ─┤   jusqu'à FETCH_WORKERS (8)
(6 flux)          file 5 ─┤   files en vol simultanément
                  file 6 ─┤
19 autres      →  1 file ─┘
domaines          chacun
```

- `PER_HOST_LIMIT` (3) — un même domaine n'est jamais interrogé par plus de
  3 files à la fois. La politesse due au serveur est tenue **par
  construction**, sans sémaphore et sans risque de famine.
- `HOST_PAUSE` (1 s) — la pause d'une seconde subsiste, mais uniquement
  entre deux requêtes d'une **même** file. Elle ne bloque plus les 34 autres
  sources.
- Le découpage est **déterministe** : à liste de sources identique, mêmes
  files.

**Ce qui n'a pas changé, et c'est le point important.** Seul le
*téléchargement* est parallélisé. La fusion dans l'historique
(`merge_results`) parcourt toujours `FEEDS` **dans l'ordre déclaré**, jamais
dans l'ordre d'arrivée des réponses. C'est cet ordre qui détermine quelle
source « possède » un article et dans quel ordre les sources supplémentaires
s'empilent derrière lui pour le badge « 🔥 N SOURCES ». Parcourir dans
l'ordre d'arrivée rendrait le fichier produit dépendant de la vitesse des
serveurs — donc différent d'un passage à l'autre. Un test dédié rejoue
l'ancienne boucle séquentielle et la nouvelle sur les mêmes données et
vérifie que le résultat est identique, ordre compris.

Le décodage Google News (`DECODE_WORKERS`, 4) et les miniatures
(`IMAGE_WORKERS`, 8) sont parallélisés selon le même principe.

**Résultat mesuré** (passage n°167, 29/08/2026) :

| | Avant | Après |
|---|---|---|
| Récupération des 35 sources | 3 min 04 | **35 s** |
| Passage complet | 3 min 10 | **1 min 03** |

## Le workflow — `.github/workflows/update-feeds.yml`

- **`concurrency: group: update-feeds, cancel-in-progress: false`** —
  empêche deux exécutions de tourner en même temps (ex: une automatique
  horaire qui démarre pendant qu'une relance manuelle tourne encore) ; la
  seconde attend en file d'attente au lieu de risquer un conflit de push.
- **`timeout-minutes: 20`** — le job dure normalement ~1 minute depuis la
  mise en parallèle ; la marge reste volontairement large si un site traîne
  anormalement, sans risquer qu'une exécution bloquée tourne indéfiniment.
- **`actions/checkout@v7` et `actions/setup-python@v7`** — passer de v4/v5
  à v5/v6 avait d'abord servi à faire taire l'avertissement de dépréciation
  de Node 20 ; les v7 sont arrivées ensuite par Dependabot. Leurs
  changements de rupture ont été lus avant d'accepter, et aucun ne
  s'applique ici : `checkout` v7 bloque la récupération des PR issues de
  forks, mais seulement sur les déclencheurs `pull_request_target` et
  `workflow_run`, dont aucun n'est utilisé ; `setup-python` v7 supprime
  l'option `pip-install`, que les deux workflows n'utilisent pas (ils ne
  passent que `python-version`).
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
  plafonnement, lecture/écriture de `docs/feed.json`, plus deux règles que
  Discord et le push doivent appliquer à l'identique — `masquer_urls()`
  (ne jamais laisser un secret dans un journal) et `libelle_recap()` (le
  texte de la notification). Aucun accès réseau, aucune dépendance externe. Ces trois règles doivent rester identiques
  entre le robot et l'outil de fusion, sous peine de corrompre
  l'historique — d'où le module commun.
- **`merge_feed.py`** — fusionne deux versions de `docs/feed.json` au
  niveau des données. Appelé par le workflow uniquement en cas de rejet
  de push.
- **`discord_notify.py`** — l'envoi Discord, appelé après publication.
- **`weekly_digest.py`** — le récapitulatif du dimanche (voir plus bas).
- **`dedupe_history.py`** — passage unique de nettoyage, gardé pour
  mémoire et reproductibilité. Voir « Récupération en parallèle » pour le
  bug qu'il a servi à réparer côté données.
- **`push_notify.py`** — les notifications push natives, appelées au même
  moment. N'importe pas `pywebpush` au niveau du module : la construction
  du message et la lecture des abonnements restent testables sans la
  dépendance.

### Tests et contrôles

```
python test_pipeline.py        # dates, tri, plafond, fusion
python check_sources_sync.py   # backend Python vs mode de secours JS
```

Aucun des deux n'a besoin de réseau ni des dépendances du robot : le
contrôle de synchronisation ne lit que des constantes et neutralise les
imports manquants, pour tourner sur n'importe quelle machine.

**Mises à jour de dépendances.** `.github/dependabot.yml` fait ouvrir une
pull request par GitHub quand une version corrigée sort, côté Python
(`requirements.txt`) comme côté actions de workflow. Rien n'est appliqué
tout seul : la PR passe par la CI comme n'importe quelle autre. C'est le
contrepoids nécessaire à l'épinglage au numéro exact, qui garantit qu'aucun
passage du robot ne change de comportement sans qu'on le décide — mais fige
aussi les correctifs de sécurité. C'est aussi ce qui aurait signalé la
dépréciation de Node 20 sans attendre qu'un avertissement jaune soit
remarqué à l'œil.

Attention en relisant ces PR : **une CI verte ne prouve pas que le robot
tourne encore.** `test_pipeline.py` ne touche jamais au réseau, donc une
rupture dans `feedparser`, `requests` ou `beautifulsoup4` n'y apparaîtrait
pas. Sur le premier lot (29/08/2026), les usages réels ont donc été rejoués
à part avec les nouvelles versions installées — extraction `og:image` et
repli `twitter:image`, lecture d'un flux RSS, `media:content`, conversion
d'une date RFC 822, paramètres `etag`/`modified` — avant d'accepter. Un
passage réel du robot après fusion reste la vérification qui compte.

Les deux tournent en CI (`.github/workflows/checks.yml`) sur chaque pull
request et sur `main`. Les commits du robot ne les déclenchent pas — non pas
grâce au `paths-ignore`, mais parce que GitHub ne déclenche aucun workflow
sur un push signé par le `GITHUB_TOKEN` d'un workflow. Vérifié : 65 commits
du robot le 28/08, zéro exécution de contrôle. Le `paths-ignore` sur
`docs/feed.json` est une ceinture en plus de ces bretelles — et il est
d'ailleurs incomplet, puisqu'il ne mentionne pas `docs/feed-recent.json`,
écrit par le même commit. Sans conséquence aujourd'hui, mais à corriger si
le robot venait à pousser avec un autre jeton.

`test_pipeline.py` n'a besoin ni de réseau ni de dépendance : la
récupération est injectable (paramètre `collecte` de `fetch_all_feeds`), ce
qui permet de tester tout le pipeline sans sortir de la machine. **224
vérifications** couvrant les dates (les trois formats présents dans
l'historique), le tri, le plafonnement, la repasse rétroactive, le
nettoyage des liens, le cache de décodage, la validation du champ VAPID
`sub` contre le vrai validateur de `py_vapid`, le masquage des URL dans les
messages d'erreur, l'équivalence entre récupération séquentielle et
parallèle, le plafond de requêtes par domaine, la déduplication à
l'intérieur d'un même passage, la promotion d'un sujet entre deux passages,
le suivi des sources muettes, le garde-fou contre les archives, l'unicité
des identifiants de source, le récapitulatif hebdomadaire — et surtout la
fusion, c'est elle qui décide si des articles sont perdus quand deux
exécutions se chevauchent. Le dernier bloc rejoue ces règles sur le vrai
`docs/feed.json` du dépôt.

`check_sources_sync.py` compare `FEEDS` et les 139 mots-clés de
`fetch_feeds.py` à leurs copies `DEFAULT_FEEDS` / `keywords` de
`docs/index.html`, et échoue en nommant chaque écart. La duplication reste
(voir Limites), mais elle ne peut plus dériver en silence : fin août 2026,
trois sources étaient marquées « sans filtre » côté JS alors que le backend
leur appliquait le filtre normal, et personne ne l'avait vu.

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

**Le bloc de contrôle.** L'état du fil, les deux boutons d'action, le message
de déclenchement et la barre de progression forment une seule carte. Ils
étaient cinq blocs empilés, dont deux annonçaient le même nombre d'articles
(« 300 articles affichés » suivi de « 300 articles récents chargés »).

Les deux boutons sont en **flex et non en grille** : « Relancer le robot »
est masqué tant qu'aucun jeton n'est enregistré, et une grille à deux
colonnes aurait laissé une demi-colonne vide à côté d'« Actualiser ». Leurs
noms disent ce qui les sépare — l'un retélécharge le fichier déjà publié
(instantané), l'autre fait travailler le robot sur les 49 sources (~1 min).

Onglets et boutons d'action partagent **une seule déclaration CSS** plutôt
que deux qui se ressemblent, ce qui garantit qu'ils ne divergeront pas à la
prochaine retouche. Le centrage y passe par flex et non par `text-align` :
`refreshTokenUI()` posait autrefois `display:inline-flex` en style inline
pour révéler le bouton, ce qui en faisait un conteneur flex — et
`text-align:center` n'a aucun effet sur un conteneur flex. Le texte était
décalé de 30 px vers la gauche et aucune règle CSS ne pouvait le rattraper,
un style inline l'emportant sur la feuille de style. Le JS rend maintenant
la main à la CSS.

La **précision du compte à rebours suit l'urgence** : jours et heures au
départ, les minutes à partir de 7 jours, les secondes dans les dernières
24 h. Des secondes qui défilent à 81 jours de la sortie attirent l'œil en
permanence sans rien apprendre.

Les actions d'un article (**marquer lu, copier, aperçu, traduire**) sont
une rangée d'icônes sous le titre. En colonne à droite, elles ramenaient le
titre à 164 px sur un écran de 390 px, soit huit lignes pour un titre long ;
il en fait 250 aujourd'hui. Chaque icône porte un `title` et un
`aria-label`.

**Gestes tactiles.** Balayer une carte vers la gauche ou la droite bascule
lu/non lu ; le seuil est de 64 px, en deçà la carte revient en place. Tirer
vers le bas en haut de page relance une actualisation. La direction du geste
est figée au premier mouvement franc (8 px) et ne change plus : sans ça, un
doigt qui dévie pendant un défilement déclencherait un balayage.

**Reprise de lecture.** L'app mémorise le *lien* de l'article regardé au
moment où on la quitte, jamais une position en pixels — au retour, de
nouveaux articles se sont insérés en haut du fil, donc le même nombre de
pixels ne désigne plus le même endroit. Un trait « lu jusqu'ici » se glisse
avant cet article, et une pastille propose d'y sauter tant qu'il est hors
écran (elle déplie la pagination si besoin).

**Mots-clés à exclure.** Symétrique de la liste des mots-clés qui font entrer
un article. Le masquage a lieu **à l'affichage** et non à la collecte :
`lastItems` garde tout, donc retirer un mot fait réapparaître les articles
aussitôt, sans relancer le robot. Tous les compteurs partent de
`articlesVisibles()`, jamais de `lastItems` — un badge qui compterait les
articles masqués annoncerait des non-lus introuvables.

**Ce que `localStorage` conserve.** La copie locale des articles est
plafonnée à **300**, la taille du fichier allégé. Elle ne sert qu'à afficher
quelque chose à l'ouverture avant que le réseau réponde ; sans plafond elle
suivait l'historique — 0,8 Mo pour 1260 articles, donc environ 13 Mo aux
20 000 du backend, très au-delà du quota de 5 Mo. Un drapeau accompagne la
troncature : sans lui, la liste restaurée au démarrage paraîtrait complète
et une recherche renverrait « aucun résultat » pour un article qui existe.
Un quota dépassé est désormais **signalé dans le journal** — il ne faisait
qu'échouer les écritures en silence.

**Badge sur l'icône de l'app.** `navigator.setAppBadge()` affiche le nombre
de non-lus sur l'icône de l'écran d'accueil. L'API n'existe que pour une PWA
installée et pas sur tous les navigateurs : absence, promesse rejetée et
exception synchrone sont toutes trois avalées, le badge étant un confort et
non une fonction de l'app.

**Mode de secours** : si `docs/feed.json` est inaccessible (backend en
panne, GitHub Pages indisponible), l'app bascule automatiquement sur un
ancien système de récupération directe des 49 sources via des proxys CORS
publics (CodeTabs, allorigins, corsproxy.io, whateverorigin, feed2json,
rss2json). C'est redondant avec le backend, mais volontaire : sans ce
filet de sécurité, l'app serait totalement inutilisable si le backend
tombait, ce qui serait une vraie régression de fiabilité pour un gain de
simplicité qui n'en vaut pas la peine.

## Notifications push natives

Discord fonctionne, mais taper une notification Discord ouvre Discord,
jamais l'article. Une notification push native ouvre directement le site.
Les deux coexistent : chacune s'active par la présence de ses secrets, et
se désactive par leur absence.

**Les deux annoncent mot pour mot la même chose**, et ne peuvent pas
diverger : le texte est écrit une seule fois dans
`feed_store.libelle_recap()`, appelé par les deux canaux.

```
🎮 3 nouveaux articles GTA 6 (dont 1 officiel Rockstar)
```

**Deux tons.** Quand un sujet est couvert par au moins
`HOT_SOURCE_THRESHOLD` rédactions (4), le libellé bascule en alerte :

```
🚨 Actu majeure — 5 sources sur le même sujet · 3 nouveaux articles GTA 6
```

C'est la différence entre être notifié d'une rumeur et être prévenu d'un
trailer, et c'est la seule information dont on dispose sans lire les
articles. Côté push, l'alerte reçoit en plus **son propre `tag`** : avec le
tag de routine, le récapitulatif du passage suivant l'effacerait en silence
une demi-heure plus tard — précisément celle qu'on ne veut pas rater.

**Y compris quand la couverture s'étale.** C'était la limite de la première
version : une annonce reprise progressivement par la presse ne déclenchait
rien, puisque chaque reprise est un doublon — donc « rien de neuf » à
annoncer, alors que c'est précisément le moment où le sujet devient
important. Le robot signale désormais les articles **déjà connus** qui
franchissent le seuil grâce à une reprise (`PROMOTED_ITEMS_FILE`), et une
notification part sur ce seul motif :

```
🚨 Actu majeure — 5 sources sur le même sujet
```

Le franchissement est détecté **au basculement uniquement** : un sujet déjà
majeur qui gagne une 6ᵉ puis une 7ᵉ reprise ne réalerte pas.

Aucun titre d'article n'y figure. Une version précédente reprenait celui du
premier article pour éviter d'avoir à ouvrir l'app — mais « premier » ne
veut rien dire ici : c'est l'ordre de `FEEDS`, pas une importance. Un titre
tiré au hasard parmi plusieurs donne une idée fausse de ce que contient le
lot. Un récapitulatif annonce combien ; le quoi est dans l'app, à un tap.

Le protocole Web Push ne demande **pas de serveur permanent** : il faut
une paire de clés VAPID et, par appareil, un abonnement créé par le
navigateur. L'envoi tient en quelques secondes dans une étape de workflow
(`push_notify.py`).

**Mise en place** (une fois pour le dépôt) :

1. Ouvrir l'app → ⚙ Paramètres. Tant que le dépôt n'a pas de clés, un bloc
   *Configuration initiale* propose de les générer.
2. Cliquer **Générer une paire de clés**. Elles sont créées dans le
   navigateur par Web Crypto et ne partent nulle part — inutile
   d'installer quoi que ce soit.
3. Créer deux secrets GitHub (Settings → Secrets and variables → Actions) :
   **`VAPID_PUBLIC_KEY`** et **`VAPID_PRIVATE_KEY`**.
   Ne pas conserver de capture d'écran de la privée.
4. Facultatif : **`VAPID_SUBJECT`**, une adresse `mailto:` que les services
   de push utilisent pour joindre l'expéditeur en cas d'abus. Jamais
   montrée à l'utilisateur.
5. Relancer le robot. Il recopie la clé **publique** dans `feed.json` — le
   bloc de configuration disparaît et l'abonnement devient possible.

**Puis, par appareil :**

6. ⚙ Paramètres → *Notifications sur cet appareil* → **Activer les
   notifications**, et accepter la demande du navigateur.
7. Copier le bloc d'abonnement affiché et le coller dans le secret
   **`PUSH_SUBSCRIPTIONS`**. Pour plusieurs appareils, mettre un tableau
   JSON : `[{...}, {...}]`.

C'est le seul geste manuel du dispositif, et il découle directement de
l'absence de backend : l'app ne peut pas écrire dans les secrets du dépôt
toute seule. Le bouton **Tester l'affichage** envoie une notification
locale — utile pour vérifier que l'appareil les affiche (mode silencieux,
Ne pas déranger…) avant de chercher pourquoi le robot n'envoie rien.

**Pourquoi les abonnements sont un secret et pas un fichier du dépôt :** un
abonnement rendu public permettrait à n'importe qui d'envoyer des
notifications sur l'appareil concerné.

**Et pourquoi les messages d'erreur sont nettoyés avant affichage.** Le même
raisonnement s'applique aux journaux d'exécution, publics puisque le dépôt
l'est. Les bibliothèques réseau recopient l'URL appelée dans leurs messages
d'erreur — `Max retries exceeded with url: /fcm/send/cXXXX…` — et GitHub ne
masque que la valeur *exacte* d'un secret, pas un fragment extrait du JSON
qui l'entoure. Une simple panne réseau publiait donc l'endpoint en clair.
`feed_store.masquer_urls()` retire l'URL complète **et son chemin seul**
(urllib3 n'affiche souvent que le chemin) avant tout affichage ; l'hôte est
conservé, il aide au diagnostic et n'identifie personne. Le webhook Discord
est un secret exactement de la même nature — qui le possède peut publier sur
le salon — et passe par le même filtre, d'où la fonction commune dans
`feed_store` plutôt qu'une copie dans chaque module.

**Abonnements expirés.** Quand un navigateur renouvelle son abonnement, le
service de push répond 404 ou 410. Le robot le signale explicitement dans
les logs du run : il faut alors retirer l'ancienne entrée du secret et
refaire l'abonnement depuis l'app.

**Support.** Complet sur Android. Sur iPhone, l'app doit être installée sur
l'écran d'accueil et le support y est plus restreint.

## Récapitulatif hebdomadaire

Un message Discord le dimanche soir (`weekly-digest.yml`, un workflow à
part), avec les sujets les plus repris des sept derniers jours.

Il répond à une autre question que le récapitulatif de passage. Celui-ci
dit « quoi de neuf dans la dernière demi-heure » et annonce un nombre, sans
titre : sur un téléphone, une notification se lit d'un coup d'œil. Le
récapitulatif hebdomadaire dit « qu'est-ce que j'ai raté cette semaine »,
on l'ouvre au lieu de le survoler — il porte donc bien des titres et des
liens, et c'est tout son intérêt.

- **Classé par nombre de rédactions**, pas par date : c'est le seul
  indicateur d'importance disponible sans lire les articles.
- **8 sujets maximum.** Au-delà, c'est un mur de texte que personne ne lit,
  soit l'inverse du but.
- **Discord seulement, pas de push** : la valeur est dans la liste
  cliquable, ce qu'une bannière de notification ne sait pas montrer.
- Aucun message s'il n'y a rien eu cette semaine.
- Les crochets d'un titre et les parenthèses d'une URL sont neutralisés :
  la syntaxe de lien Markdown casse aux deux bouts, et une URL parenthésée
  afficherait un lien tronqué suivi d'un bout d'adresse en texte brut.

⚠️ Le `schedule` de GitHub est best-effort : sur une tâche **hebdomadaire**,
un créneau abandonné = une semaine sautée. Le workflow accepte donc aussi
`repository_dispatch` avec `{"event_type": "weekly-digest"}` — un appel
hebdomadaire depuis cron-job.org le fiabilise, comme pour le robot.

## Alerte quand une source tombe

`sources_health` savait déjà repérer un flux mort, mais cette information
n'allait nulle part : il fallait ouvrir le site pour la voir. Or une source
morte se manifeste précisément les jours où rien n'arrive — donc où aucun
récapitulatif ne part.

Le robot compte donc les passages consécutifs sans la moindre entrée brute
(`sources_silence` dans `feed.json`, faute d'autre stockage persistant) et
envoie un message Discord **au moment où l'état bascule** :

```
🔴 VG247 ne renvoie plus rien depuis 6 passages.
🟢 VG247 est revenue.
```

- **`DEAD_SOURCE_RUNS = 6`** — à 30 minutes par passage, trois heures. Assez
  pour écarter un 503 passager ou une coupure réseau ; assez peu pour ne pas
  laisser un flux mort passer la journée inaperçu.
- **Une alerte par bascule, jamais par passage.** Sans ça, une panne d'une
  journée produirait 48 messages identiques.
- Un flux qui répond `304` est vivant et n'entre pas dans le comptage ; une
  source « tarie » (elle répond, mais l'actualité est calme) non plus.
- Seules les sources en difficulté sont conservées dans `sources_silence` :
  inutile d'écrire 35 zéros dans `feed.json` à chaque passage.

**Exception assumée à la règle « un seul message Discord par passage ».**
Cette règle existe pour empêcher un message par *article*. Une alerte de
source est d'une autre nature, et surtout elle ne peut pas voyager dans le
récapitulatif, qui n'est pas envoyé quand il n'y a rien de neuf.

## Surveillance : savoir quand le robot s'arrête

GitHub envoie un mail quand une exécution **échoue**. Il n'envoie rien
quand aucune exécution ne **part** — et c'est exactement ce qui s'est
produit fin août 2026 : le robot est resté muet des heures sans que rien
ne le signale. Le bandeau dans l'app ne prévient que si on ouvre l'app.

La parade est un *dead man's switch* : le robot envoie un signal de vie à
chaque passage, et c'est l'**absence** de signal qui déclenche l'alerte.

**Mise en place** (gratuit, une fois) :

1. Créer un compte sur [healthchecks.io](https://healthchecks.io) —
   gratuit jusqu'à 20 surveillances.
2. Créer un check, régler la période sur 1 heure et le délai de grâce sur
   3 heures (le planificateur de GitHub prend du retard, inutile de crier
   au loup au premier créneau manqué).
3. Copier l'URL de ping fournie (`https://hc-ping.com/…`).
4. Dans le dépôt : Settings → Secrets and variables → Actions → New
   repository secret, nommé **`HEALTHCHECK_URL`**.
5. Choisir le canal d'alerte dans healthchecks.io : mail, Discord, ou
   notification mobile.

Sans ce secret, l'étape ne fait rien et le robot fonctionne normalement.
En cas d'échec du job, le robot signale explicitement l'échec (`/fail`)
plutôt que d'attendre l'expiration du délai.

## Planificateur externe : réparer le cron plutôt que le contourner

**En place et vérifié depuis le 28/08/2026.** cron-job.org appelle le dépôt
toutes les 30 minutes ; sur la nuit du 28 au 29 août, les 22 créneaux sont
partis sans exception, à la minute près. À comparer aux 12 créneaux
consécutifs purement abandonnés par le `schedule` de GitHub la veille.

Le `schedule` de GitHub Actions est *best effort* par conception. GitHub
documente que les exécutions planifiées peuvent être retardées, et purement
abandonnées en période de charge. Le décalage à `7,37` n'a rien changé en
pratique.

Le workflow accepte donc aussi un déclenchement **externe** :

```
POST https://api.github.com/repos/antoniman31/gta6-backend/dispatches
Authorization: Bearer <jeton fine-grained, Contents: read and write>
Accept: application/vnd.github+json

{"event_type": "run-feeds"}
```

N'importe quel planificateur sait envoyer ça — [cron-job.org](https://cron-job.org)
est gratuit et suffit largement. À l'inverse du `schedule` de GitHub,
l'appel part à l'heure dite et l'exécution démarre immédiatement.

Le `schedule` reste actif comme filet de sécurité : si le planificateur
externe tombe, GitHub prend le relais tant bien que mal. Les deux
ensemble ne créent pas de doublon problématique — la file d'attente
(`concurrency`) sérialise les exécutions, et la publication fusionnante
absorbe les chevauchements.

Note : ce déclencheur demande un jeton avec la permission **Contents**,
plus large que celui du bouton dans l'app (Actions seul). À réserver au
planificateur, pas à mettre dans un navigateur.

## Déclenchement à distance depuis l'app

Le déclencheur planifié de GitHub étant best-effort, il arrive qu'un
créneau saute. L'app permet de relancer le robot depuis le téléphone sans
ouvrir l'onglet Actions.

**Créer le jeton** (à faire une fois, sur GitHub) :

1. Settings → Developer settings → Personal access tokens →
   **Fine-grained tokens** → Generate new token
2. **Repository access** : *Only select repositories* → `gta6-backend`
   uniquement
3. **Permissions** → Repository permissions → **Actions : Read and write**.
   Rien d'autre.
4. Choisir une **date d'expiration**, générer, copier le jeton
5. Dans l'app : ⚙ Paramètres → *Déclenchement à distance* → coller →
   Enregistrer. Le bouton « Relancer le robot » apparaît alors à côté
   d'« Actualiser ».

**Ce que le jeton peut et ne peut pas faire.** Avec la permission
ci-dessus, il ne sait que lister et lancer des exécutions du workflow. Il
**ne peut pas** modifier le code, lire les secrets, ni toucher au contenu
du dépôt. Au pire, quelqu'un qui le récupérerait pourrait déclencher des
mises à jour de flux.

**Où il est rangé.** Dans sa propre clé `localStorage`
(`gta6watch:github-token-v1`), jamais mélangé à `settings-v1` : il ne part
pas dans l'export OPML et n'est pas effacé par « Réinitialiser les
paramètres ». Le bouton « Oublier ce token » l'efface. Il est propre à cet
appareil et à ce navigateur — c'est aussi la limite du procédé : un jeton
stocké dans un navigateur est lisible par tout script s'exécutant sur la
page, d'où l'insistance sur une portée minimale et une expiration.

**Suivi.** Après déclenchement, l'app interroge GitHub toutes les 10 s
(pendant 10 min maximum) et affiche l'état — en file d'attente, en cours,
terminé — puis recharge les articles dès que l'exécution réussit. Un délai
de garde de 2 minutes empêche d'empiler les demandes : le workflow a de
toute façon une file d'attente côté GitHub.

L'état des cinq derniers passages est visible dans la modale ⓘ. Le dépôt
étant public, cette liste s'affiche même sans jeton (quota anonyme de
l'API GitHub : 60 requêtes/h par adresse IP).

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
lien cliquable vers le site), jamais un message par article, jamais de titre
d'article — même texte que la notification push, voir plus haut. Aucun envoi
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

- **Historique glissant, pas permanent** — plafonné à 20 000 articles
  (`MAX_HISTORY_SIZE` dans `feed_store.py`), pas un vrai historique complet
  depuis toujours.
- **Poids de `feed.json` à terme** — 896 Ko aujourd'hui pour 1 208 articles ;
  au plafond de 20 000 il approcherait 15 Mo (~3 Mo compressés). L'ouverture
  de l'app n'est pas concernée (elle charge `feed-recent.json`), mais toute
  recherche déclenche le téléchargement de l'historique complet. Côté dépôt
  en revanche il n'y a pas de problème : Git ne stocke que les lignes
  changées (~30 à 90 lignes par passage), et l'ensemble du dépôt tient
  aujourd'hui dans **676 Ko compactés pour 112 commits**.
- **Deux définitions de sources** — la liste `FEEDS` (Python, source de
  vérité) et `DEFAULT_FEEDS` (JS, utilisé uniquement par le mode de
  secours) doivent être synchronisées manuellement si une source est
  ajoutée ou retirée. Impossible à éliminer complètement sans casser
  l'autonomie du mode de secours, qui a justement besoin des vraies URLs
  même quand `feed.json` (qui pourrait autrement centraliser cette liste)
  est inaccessible. `check_sources_sync.py`, exécuté en CI, garantit au
  moins que les deux copies ne peuvent plus diverger sans que ça se voie.
- **Deux algorithmes de déduplication légèrement différents** — le
  backend Python utilise `SequenceMatcher`, le mode de secours JS une
  comparaison par tokens. Comme le backend est la source principale et le
  mode de secours n'intervient qu'en cas de panne, ce n'est pas un vrai
  risque pratique.
- **Reddit r/GTA6 : essayé, retiré.** Ajouté le 29/08/2026 en version
  « meilleurs posts du jour » plutôt que « tous les nouveaux », précisément
  pour limiter le bruit. Insuffisant : le premier passage a remonté 25
  publications, dont « Sums up people born after 2002 lmao ». Un forum
  communautaire n'a pas la même densité d'information qu'une rédaction, et
  aucun réglage de tri ne corrige ça. Les 25 articles importés ont été
  retirés avec la source.
- **Millenium, XboxEra et Xbox-Mag : retirées faute de résultats.** Leurs
  recherches Google News renvoyaient zéro entrée. Deux causes possibles,
  indiscernables depuis l'environnement d'ajout : domaine mal orthographié,
  ou absence d'articles GTA 6 indexés pour ce domaine. Retirées plutôt que
  laissées muettes — une source qui ne rapporte rien déclenche l'alerte de
  source morte à répétition et brouille le signal.
- **17 sources ajoutées le 29/08/2026 sans que leur URL ait pu être
  testée.** L'environnement depuis lequel elles ont été ajoutées n'avait
  accès à aucun de ces sites. Elles passent donc toutes par une recherche
  Google News restreinte au domaine — le seul format dont la validité était
  certaine, déjà éprouvé par sept sources en production — plutôt que par le
  flux RSS natif de chaque site, dont le chemin varie (`/feed`, `/rss`,
  `/rss.xml`…) et aurait été deviné. Une source dont le domaine serait
  erroné renvoie zéro entrée, bascule en « muette » et déclenche l'alerte
  de source morte sous trois heures : l'erreur se signale d'elle-même.
  Contrepartie : Google News passe de 6 à 23 flux interrogés toutes les 30
  minutes. Le plafond de 3 files simultanées par domaine tient (aucune
  requête parallèle supplémentaire vers Google), mais un éventuel
  rate-limit se verrait sur ces sources en premier.
- **GTAForums et GTA Base** — jamais intégrés, ces deux sites bloquent
  activement les accès automatisés, y compris depuis un vrai serveur (pas
  seulement un navigateur).
- **Diagnostiquer une source qui ne rapporte rien.** Le tableau
  `sources_health` de `feed.json` le permet sans toucher au code : comparer
  `entries_fetched` (ce que le flux renvoie vraiment) au nombre d'articles
  de cette source dans l'historique. Un flux qui renvoie beaucoup et ne
  produit rien n'est pas cassé — il est hors sujet.

  Cas d'école, VG247 le 29/08/2026 : **25 articles récupérés par passage,
  zéro retenu**. Son flux rss.app était un flux VG247 *généraliste* (tout
  le catalogue du site) ; le filtre faisait exactement son travail en
  rejetant tout. Retirer la source aurait été le mauvais geste : vérifié
  au passage, **aucun** article vg247.com n'arrivait non plus par les
  autres flux, donc la couverture manquait réellement. L'URL a été
  remplacée par une recherche Google News restreinte au domaine, sur le
  même modèle que les sources officielles.
- **Miniatures Google News** — un léger pourcentage d'articles n'a pas de
  miniature si le site source bloque les robots ou n'a pas de balise
  exploitable. Comportement normal, pas un bug.
- **Fichier HTML monolithique** — `index.html` regroupe CSS, HTML et JS
  dans un seul fichier de ~2700 lignes plutôt que d'être séparé en
  plusieurs fichiers. Choix assumé : ça simplifie l'upload manuel (un seul
  fichier à remplacer au lieu de plusieurs à garder synchronisés), au
  prix d'un fichier plus long à parcourir si besoin d'y retoucher.

## Ajuster quelque chose

- **Fréquence** : dans cron-job.org, l'horloge principale. La ligne `cron`
  de `.github/workflows/update-feeds.yml` n'est qu'un filet de secours
  best-effort — la changer n'a pratiquement aucun effet.
- **Parallélisme** : `FETCH_WORKERS`, `PER_HOST_LIMIT`, `HOST_PAUSE`,
  `DECODE_WORKERS`, `IMAGE_WORKERS` en tête de `fetch_feeds.py`
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

## Licence

[MIT](LICENSE) — réutilisation libre, y compris commerciale, à condition de
conserver l'avis de copyright. Aucune garantie.

Ne couvre que le code de ce dépôt. Les articles agrégés restent la propriété
de leurs éditeurs respectifs, et « Grand Theft Auto » est une marque déposée
de Take-Two Interactive : ce projet n'est ni affilié à Rockstar Games ni
approuvé par eux.
