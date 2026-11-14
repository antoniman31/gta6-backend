# GTA6_WATCH

Veille automatisée de l'actualité GTA VI. Un robot Python interroge des dizaines de sources toutes les heures, écrit un fichier JSON, et une application
web installable (PWA, un seul fichier HTML) l'affiche. Les nouveautés arrivent aussi sur Discord. GTA VI sort le **19 novembre 2026 à 00:00, heure de Paris**.

**App en ligne :** https://antoniman31.github.io/gta6-backend/

Quatre suites de contrôle gardent le tout. La principale compte **2260 vérifications** (nombre tenu à jour par un test, voir *Tests*).

- Le contenu historique, daté lot par lot, est dans **`JOURNAL.md`**.
- Ce qui reste à faire est dans **`RESTE-A-FAIRE.md`**.
- Les règles de travail (français, heure de Paris, git, pièges de mesure) sont dans **`CLAUDE.md`**.

## Sommaire

1. [Architecture](#architecture) · 2. [Les fichiers](#les-fichiers) · 3. [Le robot](#le-robot) · 4. [Publication](#publication-et-fusion)
5. [Discord](#discord) · 6. [L'app](#lapp) · 7. [Les données](#les-données) · 8. [Tests](#tests) · 9. [Règles d'exploitation](#règles-dexploitation)
10. [Limites connues](#limites-connues) · 11. [Où chercher](#où-chercher)

## Architecture

Aucun serveur à gérer, aucune base de données : un robot sur GitHub Actions, un fichier statique, GitHub Pages.

```
cron-job.org (toutes les heures)           ← horloge principale
        │  POST /dispatches
        ▼
GitHub Actions  ◄── cron GitHub « 37 */3 »  ← filet de secours, best-effort
        │
        ├─ Fenêtre de veille : 00h–05h Paris → tourne et publie, mais ne notifie
        │                      que les annonces officielles Rockstar
        ▼
  fetch_feeds.py ──► docs/feed.json, docs/feed-recent.json, docs/archives/*.json
        │                      │
        │                      └─► GitHub Pages ──► docs/index.html (PWA)
        │              merge_feed.py : fusion des données si deux passages se chevauchent
        ▼
  attente de la page en ligne → discord_notify.py → tableau_discord.py
        ▼
  healthchecks.io (signal de vie : son absence déclenche l'alerte)
```

Un passage notifie **par passage ayant trouvé du neuf**, jamais par article. Un déclenchement manuel (onglet Actions, ou bouton de l'app) passe à toute heure,
pause nocturne comprise.

## Les fichiers

| Fichier | Rôle |
|---|---|
| `fetch_feeds.py` | Le robot : sources (`FEEDS`), filtre, déduplication, décodage Google News, miniatures, santé des sources, écriture des JSON. |
| `feed_store.py` | Socle sans réseau : dates, tri, plafonds, lecture/écriture de `feed.json`, règles communes au robot et à la fusion, calendrier des jalons, bilan du soir. |
| `merge_feed.py` | Fusionne deux versions de `feed.json` au niveau des données quand une publication est rejetée. |
| `discord_notify.py` | Envoi Discord après publication : message du passage, jalons, alertes de report et de sources, bilan du soir. |
| `tableau_discord.py` | Le tableau du jour : un message Discord créé le matin, modifié à chaque passage. |
| `audit_donnees.py` | Audit des **données publiées** (incohérences qu'aucun test de code ne voit). Tourne en CI sans `--strict`. |
| `check_sources_sync.py` | Compare `FEEDS` (Python) et `DEFAULT_FEEDS` (JS) ainsi que les mots-clés. |
| `test_pipeline.py`, `test_discord.py`, `test_navigateur.py` | Les suites de test. |
| `mesure_actu_majeure.py` | Lecture seule : combien de sites distincts se cachent derrière une « actu majeure ». |
| `docs/index.html` | Toute l'app : HTML, CSS et JS dans un seul fichier. |
| `docs/sw.js`, `docs/manifest.json` | Service worker (cache de l'app) et manifeste PWA. |
| `docs/agenda.json` | L'agenda du compteur, tenu **à la main**. |
| `decode-cache.json`, `tableau-discord.json` | Cache de décodage des liens Google News ; identifiant du message du tableau du jour. |
| `.github/workflows/` | `update-feeds.yml` (le robot), `checks.yml` (les contrôles), `sonde.yml` (tester des URL avant de les ajouter), `test-discord.yml` (message de TEST à la demande). |

## Le robot

**Sources.** `FEEDS` dans `fetch_feeds.py` est la source de vérité ; le nombre courant est `sources_count` dans `feed.json`. Beaucoup passent par une recherche
Google News restreinte à un site ou à un sujet, les autres par le flux natif du site. Six sont officielles (le Newswire Rockstar en deux langues, ses annonces, Take-Two, sa chaîne YouTube et son assistance).
`DEFAULT_FEEDS` dans `docs/index.html` en est la copie pour le mode de secours : `check_sources_sync.py` refuse toute divergence.

**Filtre.** Un article entre si son titre ou son résumé contient un mot-clé GTA VI (sans tenir compte de la casse). Les sources officielles ont un filtre de titre strict ;
RockstarMag n'a aucun filtre (choix assumé).

**Déduplication.** Par lien, puis par ressemblance de titre (`SIMILARITY_THRESHOLD = 0.72`). Les liens Google News sont décodés vers l'adresse réelle ; le résultat est mémorisé dans
`decode-cache.json`. Les articles publiés ne gardent leur `source_link` que **trois jours** (`feed_store.JOURS_SOURCE_LINK_PUBLIE`, 10/11/2026) : c'est ce qui permet à l'app de suivre un lien corrigé par le robot ; un téléphone resté fermé plus de trois jours verrait l'article repasser « non lu ». Si le décodeur est absent (`HAS_DECODER`), le robot le signale au lieu de publier des liens opaques.

**Récupération.** En parallèle, avec un plafond de requêtes simultanées par domaine. Les miniatures viennent de la balise `og:image` de la page.

**Santé des sources.** `sources_health` suit chaque source ; une source muette ou qui chute déclenche une alerte Discord (une source est dite morte après
`DEAD_SOURCE_HOURS = 12` heures sans rien). `sources_incidents` garde les coupures de plus de trois heures sur 30 jours.

**Historique.** Une profondeur de 30 jours (`MAX_HISTORY_DAYS`), bornée entre 1 500 et 12 000 articles (`MIN_HISTORY_SIZE`, `MAX_HISTORY_SIZE`). Les officiels, RockstarMag et les
actualités majeures échappent à la coupe. Ce que la coupe écarte va dans `docs/archives/AAAA-MM.json`, un fichier par mois, que l'app charge mois par mois.
`docs/feed-recent.json` ne garde que les `RECENT_FEED_SIZE = 300` derniers articles : c'est lui que l'app ouvre au démarrage.

**Actu majeure.** Un sujet repris par au moins trois rédactions (`HOT_SOURCE_THRESHOLD`) est marqué majeur (onglet dédié dans l'app). Un sujet promu entre deux passages est suivi.

## Publication et fusion

- **Une seule exécution à la fois** (`concurrency`), délai maximal de 45 minutes ; un passage coupé perd tout son travail, d'où la marge.
- **Commit à chaque passage**, même sans nouvel article : `generated_at` doit avancer pour distinguer « le robot ne trouve rien » de « le robot est en panne ».
- **Publication fusionnante.** Si le push est rejeté, `merge_feed.py` fusionne les deux versions au niveau des données (union par lien) et réessaie, jusqu'à cinq fois. Aucun article n'est
  perdu, et les compteurs cumulatifs (jalons envoyés, bilan du soir, file du récapitulatif) sont réunis, jamais écrasés.
- **Notification après publication seulement** : l'étape Discord attend que la page soit réellement en ligne.
- **Fenêtre de veille.** De 00h à 05h (Paris, changement d'heure compris), les articles ordinaires sont mis de côté dans `attente_recap` et annoncés au premier passage de la journée.
  Du 18 au 20/11/2026 la pause est levée (dates écrites dans l'étape « Fenêtre de veille » du workflow).
- **Surveillance.** healthchecks.io reçoit un signal à chaque passage réussi.

## Discord

Un message par passage (`discord_notify.py`), jamais un par article. Il commence par une ligne d'aperçu pour la notification du téléphone, puis des sections dans cet ordre :

1. **⭐ Officiel** (orange) : les annonces Rockstar et Take-Two, avec leur type (déduit du domaine) ; les versions française et anglaise d'une même annonce sont fusionnées.
2. **🚨 Actu majeure**, 3. **📰 RockstarMag**, 4. **🎮 Les autres**.

Cinq lignes au plus par section, avec « + N autres dans l'app ». Quand le message dépasse la limite de Discord, on coupe d'abord « Les autres », puis RockstarMag, puis l'actu majeure, **jamais l'officiel**.
Un article n'apparaît qu'une fois. Un bouton ouvre l'app (retombe sur un lien texte si Discord refuse les boutons). Le nombre d'annonces officielles notifiées par passage est plafonné
à `NOTIFS_OFFICIELLES_MAX = 10`.

À part : les **jalons** du compte à rebours (`JALONS_JOURS` avant la sortie, puis `JALONS_HEURES`), le **bilan du soir** (premier passage après `BILAN_HEURE_PARIS = 22` h, un seul par jour : médias les plus actifs, sujets les plus couverts,
et « cette semaine » le dimanche), le **tableau du jour** (message créé le matin et modifié sur place, sans nouvelle notification) et les alertes de sources. Après la sortie, les messages
ordinaires continuent (« Sorti il y a… »).

Le webhook est le secret `DISCORD_WEBHOOK_URL` ; `HEALTHCHECK_URL` porte le signal de vie. Ni l'un ni l'autre ne figure dans le dépôt, la page ou la sauvegarde.
Les notifications push natives ont été retirées le 03/11/2026 ; le service worker ferme l'ancien abonnement une dernière fois.

## L'app

Une PWA dans `docs/index.html`, pensée pour le téléphone (rendu vérifié à 320 et 390 px).

- **Fil** : onglets Tous les articles, Actu majeure, Non Rockstar, Rockstar, RockstarMag, Toutes langues, News FR, News EN ; recherche sans accents ; filtres qui survivent à la fermeture.
- **Lecture** : lu/non-lu, mis de côté, reprise là où on s'est arrêté, aperçu d'article. Balayage d'une carte : vers la droite lu / non lu, vers la gauche mettre de côté / retirer (comme l'étoile). Mots à exclure (article masqué) et mots de spoiler (article flouté, mode actif par défaut).
- **Compteur** : décompte jusqu'à la sortie, dodos, barre de progression, agenda en frise (jour, rail, J-N, statut « annoncé » ou « selon la presse »), **un événement passé disparaît dès le lendemain** (il reste « Aujourd'hui » jusqu'à minuit à Paris ; plus de ligne « Hier » grisée depuis le 11/11/2026), puis, après la sortie, le temps écoulé (mois et années en tuiles quand il y en a), sans dodos, barre ni agenda. **Palette du jour J** : dès le 19/11/2026 00:00, l'accent passe du bleu au rose et les chiffres du compteur au cyan (classe `apres-sortie`, posée par `updateCountdown`). **Cycle de couleurs** : l'accent (en-tête, barres, onglets, recherche, pages) et les chiffres du compteur passent en boucle du cyan au violet puis au rose (4 s, 12 pas par seconde, tuiles décalées en vague). Actif d'office pendant les 24 h qui suivent la sortie, allumable un autre jour et coupable, par la case « Couleurs animées » des Réglages (`settings.cycleCouleurs` : `null` automatique, `true` toujours, `false` jamais) ; à l'arrêt si le téléphone réduit les animations. La liste d'articles et les couleurs de statut ne bougent pas : animer l'accent sur toute la page coûtait 75 % du processeur à la mesure, une animation par bloc environ 11 % d'un cœur avec 30 cartes affichées et 21 % avec 500, mesuré le 11/11/2026 dans le navigateur de test (les bordures en dégradé des cartes à l'écran ajoutent environ 2 points). **Effets du jour J** (11/11/2026), chacun avec sa case dans « Couleurs » (même règle que le cycle : `null` automatique pendant les 24 h de la sortie, `true`, `false` ; `settings.coulCadre`, `coulBarreEtat`, `coulChargement`, `coulControles`, posés par `appliqueEffetsJourJ`, écrits par `basculeEffet`) : un **cadre** de 3 px en dégradé cyan, violet, rose autour de tout l'écran, à coins arrondis EN BAS seulement, ceux du haut restant droits (`#cadreEcran`, par-dessus l'écran de chargement, qui tourne en 24 pas avec le cycle ; rayon `--rayon-cadre`, 32 px par défaut, confirmé par Antoni sur son S26 Ultra, réglable au curseur « Coins du bas de l'écran » : `settings.rayonCadre`), le libellé « GTA VI est sorti · J+N » du panneau (« depuis la sortie · J+N » sans la case), la **barre d'état** du téléphone teintée à 16 % de la couleur du cycle (ou d'un rose fixe), l'**écran de chargement** aux couleurs du jour J, les **cases à cocher et le curseur de saisie** à la couleur d'accent, et le **grand moment de minuit** de six secondes quand le compteur passe à zéro sous les yeux (un flash, deux ondes de choc parties du compteur, le halo des bords qui respire trois fois, une **pluie de confettis continue** d'environ 7 s : chaque confetti reste où il tombe et le tas monte du bas jusqu'à remplir l'écran (deux calques canvas, un pour le tas, un pour la chute, une carte de hauteurs d'une colonne tous les 4 px ; `lanceConfettis`), une vibration courte, puis, l'écran plein, un **écran noir « GTA VI EST DISPO ! »** dont le texte suit le cycle de couleur et qui reste jusqu'à un appui, Échap ou Entrée ; `lanceHalo`, `lanceFete`, `afficheEcranFinal`, bouton « Lancer le halo » pour l'essayer ; rien quand le téléphone réduit les animations, rien qui prenne un appui). Après la sortie, les messages Discord prennent le rose du site (`feed_store.COULEUR_COMPTEUR_APRES`). **Bordures** (11/11/2026, case `coulBordures`) : les tuiles du compteur (celle des jours grisée comprise, son texte reste gris) et les cartes d'article non officielles ont une bordure en dégradé conique, tournant avec le cycle ; seules les cartes à l'écran tournent (`observeCartes`, classe `dans-ecran`). **Tout dans le cycle** (11/11/2026) : avec le cycle, les statuts vert et jaune (`--ok`, `--warn`), les textes gris (`--text-dim` teinté à 55 % de l'accent), les badges de catégorie et de langue suivent l'accent ; l'orange (bordure et badge des officiels, texte d'« Actu majeure ») a son propre dégradé blanc, jaune, orange (en clair : ambres et bruns qui tiennent le contraste) ; **les erreurs rouges (`--danger`) restent rouges**. Les titres de jour et le repère « nouveaux » sont animés quand ils sont à l'écran (comme les cartes), la ligne « N affichés » est un bloc animé, et le sous-titre « GTA VI est sorti » devient un aplat à l'accent avec le cycle . **Tout texte suit le cycle sauf les titres d'articles**, qui restent blancs (Antoni, 10/11/2026) : avec le cycle, `--text` devient l'accent sur les blocs animés (jeton `--text-base` pour le blanc d'origine, gardé par `.card-title a`, la liste des statistiques et le titre de l'aperçu), la couleur héritée est reposée sur ces blocs (elle se transmet calculée depuis le body) et le texte d'un champ vide (`::placeholder`) prend le gris teinté ; un contrôle balaie neuf vues dans les deux thèmes (textes, champs vides, boutons-icônes, loupe) et refuse tout ce qui reste figé. Les jetons sont posés sur les mêmes blocs que l'accent et sur les cartes à l'écran. **Une seule épaisseur** pour toutes les bordures en dégradé (cadre, cartes, tuiles) : le jeton `--bord-ep`, 2 px. Réglages › Couleurs (rangement du 11/11/2026, refait le 10/11/2026 par catégories) : une **maquette fidèle et resserrée** de l'écran (en-tête et tuiles, recherche, onglets, deux cartes dont une officielle, barre du bas, cadre ; sans la frise des jalons, le sous-titre, le jour, le repère « nouveaux », les dates ni les boutons des cartes ; 74 % de la largeur, environ 245 px de haut à 320 px et 310 px à 390 px, contre 620 avant : les VRAIS éléments de la page copiés par `construitMaquette`, sans identifiants, donc elle suit chaque case sans règle à part), trois **préréglages** (« Jour J complet » sur toute la largeur, « Aucun » et « Automatique » côte à côte dessous, une ligne de texte chacun ; « Discret » supprimé le 11/11/2026) qui ÉCRIVENT chaque réglage et remettent les zones allumées, puis sous « Personnaliser » (`N sur 9 allumés`) : **Mouvement** (« Couleurs animées », la case maîtresse du cycle), **Ce qui est coloré** en QUATRE FAMILLES (textes et icônes, fonds-boutons-onglets, bordures, badges et statuts ; `settings.coulTypes`, `null` automatique / `true` / `false` comme les effets ; les bordures gardent `coulBordures`), chacune coupable dans QUATRE ZONES (en-tête et compteur, fil d'articles, barres du haut et du bas, réglages-statistiques-fenêtres), le tout en **pastilles** (disposition 3 d'Antoni : une ligne « Toute la famille » et une ligne par zone, quatre pastilles chacune, de vraies cases à cocher cachées sous leur pastille ; 407 px de haut mesurés à 320 et 390 px, sans rien à déplier ; allumer une pastille d'une famille éteinte n'allume que celle-là ; « Toute la famille » est à moitié quand certaines zones seulement sont allumées ; `settings.coulZones` : seuls les couples coupés sont écrits, `{ "texte:fil": false }`), et **Écran du téléphone** (cadre, barre d'état, écran de chargement). Mécanique : les usages de l'accent passent par des jetons typés `--c-texte`, `--c-fond`, `--c-bord` ; une famille coupée dans une zone (classe `sans-FAMILLE-ZONE` sur `<html>`, posée par `appliqueCouleursZones`) ramène ses jetons à `--accent-stat`, l'accent fixe, et les statuts à leurs valeurs `-base` ; les bordures en dégradé ont une classe par zone (`bordures-tete`, `bordures-fil`). Le grand moment de minuit garde son bouton « Lancer le halo » hors du détail. **Frise des jalons** (10/11/2026) : après la sortie, une barre sous le compteur va de J+0 à J+30, remplie en continu, avec les jalons J+1, J+7 et J+30 posés à leur place (`friseJalons`, `JALONS_APRES`) ; les passés sont cochés et suivent l'accent (donc le cycle), une ligne dit le prochain jalon ; case « Frise des jalons après la sortie » dans le groupe « Écran » des Réglages (`settings.friseJalons`, allumée d'office) ; rien avant la sortie. Le curseur des coins du bas a son propre groupe « Écran ».
- **Fil, lot du 11/11/2026** : les trois sources (Non Rockstar, Rockstar, RockstarMag) sont repliées derrière un chevron ⌄ sur la ligne de comptage, qui ne passe JAMAIS à la ligne (`.comptage-ligne`) : le chevron est réduit à son icône, et à l'abréviation du filtre actif (NR, R, RM ; le nom complet reste pour les lecteurs d'écran), et le texte de comptage se raccourcit tout seul (`ajusteComptage` : phrase complète, sans « masqués », « N non lus ») avant de recourir à l'ellipse ; zone de toucher de 44 px sans grossir la ligne ; avec un filtre de source actif la rangée s'ouvre seule ; fermé à chaque ouverture, jamais mémorisé : `sourcesOuvert`, `majSources`). Repère « ▲ N nouveaux · déjà vus ▼ » posé une fois entre les articles neufs et ceux déjà vus ; chiffres de Stats en tuiles (30 px, police du compteur). Loupe dans le champ de recherche, titres de cartes à 16 px en gras (13 px en mode compact), titres de jour en bandeau collant flouté avec un trait en dégradé, fondu de 96 px sous la barre du bas (`body::after`, z-index 59 sous la barre à 60, sans prendre d'appui).
- **Typographie et coins** : aucun texte sous 12 px ; les boîtes qui portent du texte ont des coins de 8 ou 12 px (les 4 px ne servent qu'aux favicons, barres et pastilles de légende des graphiques). Un test parcourt les dix surfaces à 320 et 390 px.
- **Stats et Réglages sans l'en-tête déplié** (10/11/2026) : sur ces deux pages la ligne réduite (titre, point vert, compteur) remplace l'en-tête, qui prenait environ 268 px sur 844 ; « Dans le fil » range ses chiffres sur deux colonnes ; la phrase « Touche une colonne » ne s'écrit qu'une fois par page. Le ↻ de l'en-tête n'y est plus : on actualise depuis le fil.
- **Statistiques et journal** : volume par jour et par heure, sources actives et en baisse, médias, délais, incidents du robot ; journal des événements de l'app.
- **Archive** chargée mois par mois ; **sauvegarde** exportable et importable (réglages, sources, articles lus) ; export OPML des sources.
- **Mode de secours** : si `feed.json` est injoignable, l'app interroge elle-même les flux (`DEFAULT_FEEDS`) via des proxys, avec sa propre déduplication.
- **Navigation** : barre flottante en bas — Fil, Stats, Réglages. Le détail du compteur s'ouvre en touchant la tuile de l'entête. L'entête : une rangée (titre, pastille « Backend », ▶ « Relancer le robot » avec un jeton, ↻) puis QUATRE TUILES jours / heures / minutes / secondes avec le libellé « avant la sortie » (« depuis la sortie » après) ; en défilant, une ligne réduite de 52 px se colle en haut (titre, point vert en mode backend, compteur à précision croissante). La fenêtre du compteur (centrée, quatre coins arrondis, barre de progression rectangulaire) reprend les MÊMES quatre tuiles que l'en-tête : un seul balisage, une seule fonction d'écriture, un seul style, au même instant, donc jamais de divergence. Le Journal des passages s'ouvre depuis Statistiques › Robot.
- **Réglages** : une PAGE (plus un pop-up) sous l'entête de l'app, ouverte par « Réglages » dans la barre du bas, qui reste visible ; les quatre rubriques sont collées en haut de la page, en cartes groupées — Apparence (thème, écran de chargement, liste des articles), Filtres (mots-clés, exclusions, mode sans spoiler), Sources, Données (backend, jeton, export, sauvegarde, « Zone sensible » avec Réinitialiser). Détail : thème, mots-clés, seuil de doublon, nombre d'articles, sources (les interrupteurs n'agissent qu'en mode de secours), adresse du backend, déclenchement du robot depuis le téléphone
  (jeton GitHub à portée minimale, gardé dans ce navigateur seulement).
- **Écran de chargement** : blanc en thème clair, noir en thème sombre (« système » suit le téléphone), avec le logo et le décompte du jour (« J-12 »). Il reste `SPLASH_DUREE_MIN_MS` après le premier
  affichage (1,5 s), bloque les touches, et un appui ou une touche le ferme. Le réglage « Écran de chargement » (Réglages › Apparence) le coupe ; piloté par un test, il est coupé tant que le réglage n'est pas allumé. Le petit script d'avant l'affichage, dans l'en-tête de la page, pose le thème pour que cet écran ne passe jamais par le sombre en thème clair.
- **Icônes** : Material Design Icons de Google (style Outlined, licence Apache-2.0, texte dans `LICENSE-material-icons`), en SVG embarqués dans la page (sprite après `<body>`, aide `ic()`), plus aucun emoji dans l'interface.
- **PWA** : installable, utilisable hors ligne pour la coque ; `docs/sw.js` ne gère que l'installation, l'activation et le cache. **Le nom du cache de `docs/sw.js` se bumpe à chaque changement de `docs/index.html`.**

L'heure de sortie est la constante `GTA6_RELEASE` de l'app ; un test vérifie qu'elle ne diverge pas du workflow.

## Les données

`docs/feed.json` (complet), `docs/feed-recent.json` (allégé) et `docs/archives/` sont écrits par le robot, jamais à la main. Parmi les champs de `feed.json` :

- `items` : les articles ; `total_articles`, `new_this_run`, `hot_count`.
- `generated_at`, `duration_seconds` : fraîcheur et durée du passage.
- `sources_health`, `sources_incidents`, `sources_silence`, `sources_declining` : santé des sources.
- `attente_recap`, `jalons_envoyes`, `rockstarmag_envoyes`, `bilan_envoye_le` : état des notifications (cumulatif, fusionné au lieu d'être écrasé).
- `decodage_etat`, `decode_failures` : suivi du décodage Google News.

`docs/agenda.json` est **écrit à la main**. Un statut `annonce` exige un lien sur un domaine officiel, sinon l'entrée est `presse` ; un test refuse le contraire. Chaque entrée porte une date de vérification ;
au-delà de 14 jours, l'app affiche « à revérifier ».

## Tests

```
python3 test_pipeline.py        # le pipeline, la fusion, les notifications, la doc
python3 check_sources_sync.py   # FEEDS (Python) contre DEFAULT_FEEDS (JS)
python3 audit_donnees.py        # cohérence des données publiées
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
CHROMIUM_PATH="$(ls /opt/pw-browsers/chromium_headless_shell-*/*/chrome-headless-shell)" \
python3 test_navigateur.py      # l'app dans un vrai navigateur, 320 et 390 px
```

`test_pipeline.py` n'a besoin ni de réseau ni de dépendance : la récupération est injectable. La suite navigateur dure environ 8 minutes seule et environ 3 minutes avec `--workers 3` (mesuré le 14/11/2026 ; `--seulement NOM` rejoue un contrôle). Les quatre doivent être vertes avant chaque commit ;
`checks.yml` les rejoue sur chaque pull request.

- **Un contrôle doit être prouvé capable d'échouer** : on le rejoue contre l'ancien code (`git archive HEAD` plus le nouveau test) avant de l'annoncer vert.
- **Aucun nombre recopié dans un test** : il lit la liste ou le code. Un nombre écrit à la main finit faux.
- **La documentation est testée** : l'en-tête de ce fichier annonce un nombre de vérifications que `test_pipeline.py` refuse s'il est faux ; une constante citée entre accents graves doit exister dans le code,
  et sa valeur, quand elle est écrite, doit être la bonne (ce fichier, `JOURNAL.md` et `RESTE-A-FAIRE.md`).
- **Dépendances** : épinglées au numéro exact ; Dependabot ouvre une pull request par mise à jour. Une CI verte ne prouve pas que le robot tourne encore : le réseau n'est jamais touché par les tests.

## Règles d'exploitation

- **Sources** : on n'en ajoute ni n'en retire jamais sans instruction explicite. Aucune URL de remplacement n'est devinée : elle vient de `sonde.yml`, du journal du robot ou du champ `redirect` de `sources_health`.
- **Agenda** : aucune entrée n'y entre ni n'en sort sans validation.
- **Secrets** dans les secrets GitHub (`DISCORD_WEBHOOK_URL`, `HEALTHCHECK_URL`), jamais dans le dépôt ni dans la sauvegarde exportée. Aucun jeton dans le User-Agent, aucun pseudo.
- **Rien de payant** (pas de résumé par IA).
- **Un contrôle ne passe jamais en baissant un seuil** sans dire pourquoi.
- **Heures** : toujours en heure de Paris, dans l'app, dans Discord et dans la documentation.
- **Le proxy du conteneur de développement bloque** `github.io`, YouTube et la plupart des sites de presse : une sonde ou un `curl` qui échoue n'y prouve rien. On lit le journal du robot.

## Limites connues

- **Historique glissant, pas permanent** : les articles écartés vivent dans l'archive mensuelle, que l'app charge à la demande.
- **Poids de `feed.json`** : borné par le plafond dur de l'historique. L'ouverture de l'app n'est pas concernée (elle charge `feed-recent.json`), mais une recherche télécharge l'historique complet.
- **Deux définitions de sources** (`FEEDS` et `DEFAULT_FEEDS`) à garder identiques ; impossible à fusionner sans casser l'autonomie du mode de secours. `check_sources_sync.py` empêche la dérive silencieuse.
- **Deux algorithmes de déduplication** (`SequenceMatcher` côté robot, jetons côté mode de secours). Le robot est la source principale ; l'écart n'a pas d'effet pratique.
- **Dépendance forte à Google News** pour une grande part des sources : un rate-limit s'y verrait en premier. Le plafond de requêtes simultanées par domaine la limite.
- **Une source sans article n'est pas forcément cassée** : un flux généraliste filtré par les mots-clés peut ne rien retenir. On compare `entries_fetched` au nombre d'articles retenus avant de conclure.
- **Un fichier HTML monolithique** : un seul fichier à déployer, au prix d'un fichier long à parcourir.
- **Miniatures** : une minorité d'articles n'en a pas quand le site bloque les robots.

## Où chercher

- **`JOURNAL.md`** : le journal. Cherche le titre d'un lot, ou un mot de la décision (« mur du jour J », « décodage », « seuil de ressemblance »…).
- **`RESTE-A-FAIRE.md`** : ce qui reste, avec les mesures déjà faites pour ne pas les refaire.
- **`PLAN-journal-incidents.md`** : le plan du journal des incidents de sources.
- **`tasks/`** : les spécifications (notifications Discord, agenda, silence…) et la liste de la répétition générale du 10/11.
- **`captures/`** : les captures d'écran mesurées (320, 360, 390 px).
- Quand le code cite une décision datée (« La dérive documentaire », « Planificateur externe »…), il renvoie à `JOURNAL.md`.
