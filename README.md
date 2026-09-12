# GTA6_WATCH

Veille automatisée de l'actualité GTA 6 : un robot interroge 50 sources en
parallèle toutes les heures, décode les vrais liens Google News, récupère
de vraies miniatures, notifie sur Discord et par notification push, et publie
tout dans une app installable sur Android.

Un passage complet dure **environ une minute**.

**App en ligne :** https://antoniman31.github.io/gta6-backend/

## Architecture

Trois briques, aucun serveur à gérer :

```
cron-job.org (toutes les heures)          ← horloge principale
        │  POST /dispatches
        ▼
GitHub Actions  ◄── cron GitHub "37 */3"  ← filet de secours, best-effort
        │
        ├─ 00h-05h (Paris) ? ─→ tourne et publie, mais ne notifie QUE
        │                       pour une annonce officielle Rockstar
        │                       (une demande manuelle notifie tout)
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

Tourne toutes les heures, déclenché par un planificateur **externe**
(cron-job.org) — voir la section dédiée. Entre minuit et 5h il tourne et
publie mais ne notifie que pour une annonce officielle de Rockstar : voir
*Pause nocturne*. Le `cron` de GitHub
reste déclaré comme filet de secours, et le déclenchement manuel reste
possible via l'onglet Actions → "Mise à jour des flux GTA 6" → Run workflow,
ou depuis l'app — **une demande manuelle passe à toute heure**, pause
comprise.

**La cadence est passée de 30 min à 1 h le 02/09/2026**, pour réduire le
nombre de notifications. Une notification part par passage AYANT trouvé du
neuf, jamais par article : espacer les passages ne fait donc pas rater
d'articles, il les regroupe. Le filet GitHub est passé au même moment de
toutes les heures à toutes les 3 heures — best-effort, il tombait à des
moments quelconques ENTRE deux passages de cron-job.org, et chaque
intercalaire envoyait sa propre notification.

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
2. **Récupère les 50 sources** (liste `FEEDS`) **en parallèle**, avec
   gestion d'erreur par source : si une source échoue, les 49 autres
   continuent normalement. Le détail du parallélisme est décrit plus bas
   (« Récupération en parallèle ») ; en séquentiel cette étape prenait
   3 min 04, elle prend maintenant ~35 s.
   La requête est **conditionnelle** : le robot renvoie l'`ETag` et le
   `Last-Modified` reçus au passage précédent, et le serveur répond `304`
   (quelques octets, sans corps) si rien n'a changé. Sans ça il
   retéléchargerait 50 flux entiers 24 fois par jour ; la documentation de
   feedparser prévient qu'un client qui ignore ces en-têtes peut se faire
   bannir par l'éditeur. Les validateurs sont conservés dans
   `feed_http_state` de `feed.json`, faute d'autre stockage persistant.

   **Délai maximal : 20 s par opération réseau** (`FETCH_TIMEOUT`, posé par
   `socket.setdefaulttimeout`). `feedparser.parse()` n'accepte aucun
   paramètre de timeout — il passe par urllib, qui suit le défaut des
   sockets, et ce défaut est `None`, c'est-à-dire une attente infinie. Une
   source qui accepte la connexion puis ne répond jamais bloquait son fil
   sans fin : les autres sources continuaient, mais le passage ne se
   terminait pas et rien n'était publié, jusqu'à ce que le
   `timeout-minutes` du workflow tue le job vingt minutes plus tard. Un
   seul site qui traîne coûtait le run entier.

   Le timeout se manifeste comme une **exception levée**, pas comme un
   `bozo` : `collect_feed_items` l'attrape, trace « échec réseau » et
   abandonne la source proprement.
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

   La **chaîne YouTube de RockstarMag** (`rockstarmag-youtube`) suit le même
   principe pour l'autre onglet. Son lien pointe vers youtube.com, donc le
   classement par domaine ne peut pas la reconnaître : c'est le chemin « la
   source le déclare » de `statut_rockstarmag()` qui la range. Elle n'a
   **pas** `no_filter_at_all`, contrairement au flux d'articles du même
   média — la chaîne couvre toute la production Rockstar, et tout accepter y
   noierait GTA 6. Le filtre porte sur le titre **et** la description, que
   le flux Atom de YouTube fournit tous les deux : une vidéo au titre
   elliptique passe donc si sa description parle du sujet.
4. **Ne lit que les 30 premières entrées de chaque flux**
   (`parsed.entries[:30]` dans `fetch_feeds.py`). Les flux ne sont pas de
   la même profondeur : RockstarMag en publie 10, Eurogamer et Rock Paper
   Shotgun 100. Au-delà de 30, ce sont des articles déjà vus aux passages
   précédents — à un passage par heure, jour et nuit, aucun site suivi ne
   publie 30 articles dans l'intervalle (le fil entier tourne autour de 105
   articles par jour, toutes sources confondues).

   **Ce plafond se lit dans les chiffres** et il faut y penser avant de
   comparer un flux à ce qu'il rapporte. Mesuré le 29/08/2026, entrées
   retenues par le filtre GTA 6 sur le flux entier contre les 30 premières
   seulement : GamesRadar+ 13 sur 50 → **6**, Rock Paper Shotgun 16 sur
   100 → **7**, Eurogamer 24 sur 100 → **18**. Les flux courts ne sont pas
   concernés (RockstarMag 10/10, GameSpot 20 sur 30, IGN 6 sur 20).
5. **Filtre par mots-clés** — les sources officielles (Rockstar, Take-Two)
   exigent un mot-clé GTA 6 dans le titre. Les sources "spécialistes"
   (`specialist_source: True` — RockstarMag, RockstarINTEL, GTA6 Times,
   GTA6 x Netflix) appliquent le même filtre que les sources normales :
   ce ne sont pas des flux sans filtre, juste des sites plus ciblés sur la
   série GTA en général (donc encore susceptibles de publier du contenu
   GTA Online/FiveM/RP qu'il faut écarter).
6. **Écarte les archives** — un article jamais vu dont la date dépasse
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
7. **Décode les liens Google News** — ces flux renvoient normalement des
   liens de redirection chiffrés (`news.google.com/rss/articles/...`),
   inutilisables pour aller chercher une vraie miniature. Le module
   `googlenewsdecoder` résout le vrai lien de l'article.
8. **Récupère les miniatures manquantes** en parallèle (`IMAGE_WORKERS`,
   8 requêtes simultanées via `ThreadPoolExecutor`) — d'abord depuis le flux RSS
   lui-même si présente, sinon en allant chercher la balise `og:image` ou
   `twitter:image` sur la vraie page de l'article.
9. **Compte les sources et déduplique** — quand plusieurs rédactions
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

   **Composition de la fenêtre** : les articles publiés dans les
   dernières **72 heures** (`FENETRE_HEURES`), **plus ceux ajoutés pendant
   le passage en cours**.

   Elle se comptait en ARTICLES jusqu'au 30/08/2026, et se refermait alors
   exactement quand il aurait fallu qu'elle s'ouvre : 200 articles valaient
   50 h en régime normal, mais 16 h le 27/08 — jour à 293 articles. Le robot
   voyait donc le moins loin les jours où il se passait quelque chose. Un
   plancher de 200 articles garantit qu'on ne compare jamais à moins
   qu'avant, un plafond de **5000** (`FENETRE_MAX`) borne le coût.

   Le second point — les articles du passage en cours — manquait jusqu'au
   29/08/2026 : ils étaient ajoutés à la *fin* de la liste, donc hors des
   200 premiers, et deux rédactions publiant le même sujet dans le même
   passage n'étaient jamais rapprochées. Mesuré
   avant correctif sur le vrai `feed.json` : 13 doublons manifestes dans
   les 400 articles les plus récents (dont des titres strictement
   identiques entre IGN et IGN France), et un compteur de sources plafonné
   à 3 — donc un badge 🔥 alors réglé sur 4 qui n'a jamais pu s'afficher une
   seule fois en 1 211 articles ; c'est ce constat qui a fait descendre le
   seuil à 3 (voir plus bas).

   **Le plafond était à 1500 jusqu'au 02/09/2026.** Il tenait large en
   régime courant — au plus gros jour observé (296 articles le 27/08), les
   72 h ne contenaient que 709 articles, 47 % du plafond — mais il mord
   précisément le jour où la fenêtre sert le plus : à 1500, une journée de
   sortie à 1000 articles ramènerait les 72 h demandées à 36 h effectives.
   5000 couvre 72 h jusqu'à 1600 articles par jour, cinq fois le pic connu.

   **Ce que cette marge a coûté : rien.** `peut_atteindre_le_seuil()`
   écarte une paire AVANT de construire le `SequenceMatcher` — c'est lui la
   partie chère — en calculant deux bornes SUPÉRIEURES du score : les
   longueurs (un titre de 20 caractères et un de 90 plafonnent à 0,36) et
   les caractères en commun. Ce sont exactement les bornes que difflib
   expose sous `real_quick_ratio()` et `quick_ratio()`, mais les appeler
   supposerait d'avoir déjà construit l'objet, donc d'avoir déjà payé.

   Une borne supérieure ne peut répondre que « le seuil est hors
   d'atteinte », jamais « c'est un doublon » : aucun vrai doublon ne peut
   lui échapper. Vérifié en force brute sur 175 980 paires de titres réels
   (0 décision différente), puis par un rejeu de tout l'historique — 1 632
   articles, 27 rapprochements de part et d'autre, 0 écart — qui donne au
   passage la mesure de bout en bout : **270,1 s avant, 52,6 s après**.

   Deux fausses pistes écartées en chemin, mesure à l'appui. Précalculer
   `titre_comparable()` pour toute la fenêtre ne gagne que 10 % : ce n'est
   pas le nettoyage des titres qui coûte, c'est l'alignement. Et réutiliser
   l'index interne de difflib en inversant les deux séquences aurait changé
   les résultats — `ratio()` n'est pas symétrique, 28 052 paires sur 33 670
   donnent un score différent selon l'ordre.
10. **Plafonne l'historique à 20 000 articles** (`MAX_HISTORY_SIZE`) — au-delà,
   les plus anciens sont retirés. Ce n'est donc pas un historique complet et
   permanent, mais un historique glissant très large.

   **Sauf les publications de Rockstar, qui ne se retirent jamais** (depuis
   le 02/09/2026). Ce sont les plus anciennes du fil — l'annonce, le premier
   trailer, toute la période d'attente : 32 articles dont le plus vieux date
   du 04/02/2022 — donc exactement celles qu'une troncature par la fin
   emporte en premier. Ce sont aussi les seules irremplaçables : la reprise
   d'un site d'actu se retrouve ailleurs, le billet officiel non.

   Le plafond reste un vrai plafond : ce qui est épargné à un officiel est
   pris sur un article ordinaire plus ancien. Un seul cas le dépasse — pas
   assez d'articles ordinaires à retirer — et la liste reste alors plus
   longue que 20 000 plutôt que de jeter ce qu'on a promis de garder. Il
   faudrait 20 000 publications de Rockstar pour y arriver ; le cas est
   testé quand même.

   **Échéance** (mesurée le 04/09/2026) : au rythme des deux dernières
   semaines — **116 articles/jour**, en hausse continue à l'approche de la
   sortie — le plafond tombe dans **environ 5 mois**, et `feed.json` pèsera
   alors ~15 Mo. `audit_donnees.py` recalcule cette projection à chaque
   passage de CI, pour qu'on la voie venir au lieu de la découvrir. Voir les
   Limites pour la conséquence sur le poids du fichier.

   Le dépôt git, lui, n'est pas un souci : entre deux passages presque rien
   ne change dans le fichier, donc git compresse en conséquence — 186
   versions d'un fichier d'un Mo tenaient dans 2,4 Mo, mesuré après un
   `git gc`.
11. **Dépose les nouveaux articles** dans le fichier désigné par
   `$NEW_ITEMS_FILE` (hors du dépôt), à destination de
   `discord_notify.py`. Le robot n'envoie plus lui-même la notification :
   voir la section Notifications. Rien n'est déposé au tout premier
   lancement (l'historique est vide, donc "tout" serait considéré comme
   nouveau).
12. **Écrit aussi `docs/feed-recent.json`** — les 300 articles les plus
   récents (`RECENT_FEED_SIZE`). Mesuré le 04/09/2026 sur 1 895 articles :
   304 Ko bruts / 92 Ko compressés, contre 1 526 Ko / 410 Ko pour
   l'historique complet.
   C'est ce fichier que l'app charge à l'ouverture ; elle télécharge le
   complet à la demande, et automatiquement dès qu'une recherche est
   lancée pour ne jamais renvoyer de résultats tronqués sans le dire. Les
   deux fichiers sont toujours écrits ensemble, y compris après une
   fusion de conflit.
13. **Distingue une source vide d'une source cassée.** `feedparser` avale
   une page HTML sans protester : `bozo` reste faux et la liste d'entrées
   est vide — **exactement comme un flux valide mais sans article**. Seul le
   champ `version` les sépare (renseigné uniquement quand le document est un
   flux). Sans ce test, une page de blocage anti-robot et un site qui ne
   publie rien produisent la même ligne « 0 entrée » : arrivé le 30/08/2026
   sur IGN et Kotaku, sans qu'on puisse trancher depuis le journal. Le code
   HTTP est tracé pour la même raison — un 403 déguisé en page HTML se lit
   alors d'un coup d'œil.

   D'où un statut `cassee`, distinct de `muette` : une source muette peut
   revenir seule, une URL qui ne renvoie plus de flux demande d'aller voir.

   **Une panne serveur n'est donc PAS une source cassée** (depuis le
   04/09/2026). Un 5xx ou un 429 revient tout seul : `panne_de_serveur()`
   les range en `muette`, et le journal dit « serveur en panne (HTTP 503) —
   repassera seul » au lieu d'accuser l'URL. Seuls les codes qui désignent
   l'adresse — 404, 403, 410 — et les réponses qui ne sont pas un flux
   restent `cassee`.

   Ce que ça corrigeait : le 04/09/2026, Google News a répondu 503 sur ses
   vingt flux d'un coup. Les vingt ont été publiées « cassées », l'app a
   affiché huit lignes de noms en orange, et il n'y avait **rien à
   réparer** — le passage suivant est reparti normalement. Quatre épisodes
   de ce genre sur 400 passages du 25/08 au 04/09, tous résorbés seuls.

   Le comptage, lui, n'a pas bougé : les deux statuts comptent toujours
   comme « ne rapporte rien », et une panne serveur qui dure finit donc
   quand même par déclencher l'alerte au bout de `DEAD_SOURCE_HOURS`.
   **Les deux comptent comme « ne rapporte rien »** (`ne_rapporte_rien()`) —
   les séparer ferait repartir à zéro le compteur de passages muets le jour
   où une muette devient cassée, et enverrait une fausse alerte de
   rétablissement sur Discord.
14. **Dresse l'état de chaque source** (`sources_health` dans `feed.json`) —
   une source « muette » n'a renvoyé aucune entrée brute, signe net d'un
   flux cassé ; une source « tarie » répond mais n'a rien publié depuis
   plus de 30 jours, ce qui peut être parfaitement normal (Rockstar et
   Take-Two communiquent peu).
   **Et alerte sur Discord quand une source tombe** — voir la section
   dédiée plus bas. L'état seul ne dit que « muette maintenant » ; c'est le
   cumul (`sources_silence`) qui distingue une panne d'un hoquet.
15. **Écrit `docs/feed.json`** avec l'historique complet, les métadonnées
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
news.google    →  file 1 ─┐
(20 flux)         file 2 ─┤   jusqu'à FETCH_WORKERS (8)
                  file 3 ─┤   files en vol simultanément
youtube.com    →  file 4 ─┤
(2 flux)          file 5 ─┤
28 autres      →  1 file ─┘
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

**Résultat mesuré** (passage n°167, 29/08/2026), la liste comptant alors
35 sources :

| | Avant | Après |
|---|---|---|
| Récupération des 35 sources | 3 min 04 | **35 s** |
| Passage complet | 3 min 10 | **1 min 03** |

Depuis, la liste est passée à 50 sources. Mesuré sur le passage n°207 du
29/08/2026 : **1 min 25** de bout en bout, dont **72 s** de récupération et
de traitement. Le surcoût des 15 sources supplémentaires reste donc très
en deçà du parallélisme gagné.

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
- **`audit_donnees.py`** — audit des **données publiées**, pas du code.
  Cherche les incohérences que `test_pipeline.py` ne peut pas voir parce
  qu'elles ne violent aucun invariant : une source supplémentaire dont le
  lien est celui d'un AUTRE article, un titre en double resté après
  déduplication, un article rattaché à une source disparue de `FEEDS`, une
  date dans le futur, un statut « officiel » hors domaine officiel, le
  fichier allégé désynchronisé du complet. Tourne en CI **sans `--strict`**
  : ces anomalies sont des symptômes, parfois légitimes, et bloquer les PR
  dessus rendrait l'outil insupportable. Il est là pour être lu.

  Il existe parce que chacune de ces vérifications avait déjà été écrite à
  la main un jour de panne, utilisée une fois, puis perdue.
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
qui permet de tester tout le pipeline sans sortir de la machine. **965
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

### Échelle de formes et de mouvement

Neuf rayons d'angle cohabitaient dans la feuille de style — 2, 3, 4, 5, 6, 8,
10, 19 et 20 px — et cinq durées d'animation : 150, 180, 200, 250 et 400 ms.
Aucune règle ne disait lequel prendre, donc deux boutons voisins ne
s'arrondissaient pas pareil et deux transitions équivalentes ne duraient pas
pareil.

Six jetons, indépendants du thème, partagés par les deux palettes :

```
--r-xs    4px     pastilles, favicon, surlignage, barres
--r-sm    8px     boutons, champs, onglets, vignettes
--r-md   12px     cartes, bandeaux, panneaux, feuille
--r-full 9999px   interrupteur, compteurs — les formes réellement en gélule

--t-court .15s    changement d'état sur place (survol, focus, bascule)
--t-moyen .25s    un élément entre, sort ou parcourt une distance

--courbe        cubic-bezier(0.2, 0, 0, 1)   changement d'état sur place
--courbe-entree cubic-bezier(0, 0, 0, 1)     un élément entre ou se déplace
```

**Les valeurs viennent des fichiers de jetons que Google génère lui-même**
(`md-sys-shape` et `md-sys-motion` v0.192), pas d'une paraphrase du site :
4/8/12/9999 sont exactement les crans *extra-small*, *small*, *medium* et
*full* de Material 3, et 150/250 ms ses durées *short3* et *medium1*.

**L'affectation, en revanche, diverge volontairement.** Google met ses boutons
et ses pastilles en gélule et ses boîtes de dialogue à 28 px. L'identité de
l'app est monospace et anguleuse ; des boutons entièrement arrondis la
casseraient. On emprunte l'échelle, pas le style — et c'est écrit dans le code
aussi, pour que personne n'y lise plus tard une conformité qui n'existe pas.

**Les courbes ont suivi le 08/09/2026.** Tout était en `ease`, la valeur par
défaut du navigateur, alors que le reste de l'échelle venait des jetons
Material 3 — une incohérence qui ne se voyait pas mais qui était bien là. Ce
sont maintenant `easing-standard` et `easing-standard-decelerate`, des mêmes
fichiers. La différence est subtile et réelle : un élément qui entre arrive
vite puis se pose, au lieu de freiner à mi-course comme le fait le `ease-out`
du navigateur.

Les pulsations décoratives infinies gardent leur rythme **et leur
`ease-in-out`** : ce n'est pas du retour d'interaction, et une respiration
doit être symétrique — les deux courbes ci-dessus ne le sont pas. Un test
vérifie qu'aucune *transition* ne reste sur une courbe par défaut, et que les
cinq *animations* infinies gardent la leur.

**Mouvement réduit.** Trois blocs `prefers-reduced-motion` coupaient déjà les
animations d'*entrée* (carte qui apparaît, retour de glissement, refermeture
du tiroir). Restaient les quatre pulsations **infinies** — compte à rebours
urgent, compte à rebours final, squelettes de chargement, point du run en
cours — c'est-à-dire précisément celles que le réglage vise : un clignotement
qui ne s'arrête jamais, sur une page qu'on garde ouverte. Elles sont
maintenant couvertes, et le mouvement s'arrête sans que le signal se perde :
le rouge, la graisse et les majuscules du palier final restent, et son halo
devient fixe au lieu de disparaître avec l'animation qui le portait.

Mesuré dans Chromium à 390 px sur les deux thèmes : 5 valeurs de rayon
distinctes à l'écran ramenées à 4, 3 durées ramenées à 2, rendu identique à
l'œil.

### Contraste : les deux thèmes tiennent WCAG AA

Toutes les couleurs sont des **jetons CSS** basculés par `data-theme` sur
`<html>`, et aucune couleur de texte n'est écrite en dur — un jeton se
corrige par thème, un `#ffffff` non.

Le piège, et il a coûté cher : **le texte posé SUR un aplat de couleur a une
contrainte opposée à celle du texte posé DEVANT.** Plus le bleu est clair,
mieux il se lit sur un fond noir, moins il peut porter du blanc. D'où
`--accent-contrast`, qui bascule avec le thème — presque noir sur le bleu
clair du sombre (8,74:1), blanc sur le bleu profond du clair (6,70:1).

Ce que ça corrigeait, mesuré le 04/09/2026 dans le navigateur, élément par
élément :

| | avant | après |
|---|---|---|
| « Actualiser », thème sombre | **2,14:1** | 8,74:1 |
| « Actualiser », thème clair | **3,00:1** | 6,70:1 |
| `--accent` (logo, compte à rebours, libellés de jour), clair | **2,79:1** | 6,23:1 |
| `--ok` (badge « backend »), clair | **3,07:1** | 5,76:1 |
| `--danger`, clair | **4,49:1** | 6,02:1 |
| pastille sur `--warn`, sombre | **1,17:1** | 15,4:1 |
| **pire rapport de l'app** | **2,14:1** | **4,95:1** |

Le thème sombre ne change qu'à un endroit visible : le bouton primaire passe
du texte blanc au texte presque noir, sur le même bleu.

### Ergonomie tactile

Mesuré le 04/09/2026 contre les standards mobiles (Material 3 : 48×48 dp,
Apple HIG : 44×44 pt, règle de séparation : 8 px entre deux cibles) :

| | avant | après |
|---|---|---|
| zone de clic de la coche ✓ | 78×**30** | 75×**44** |
| lien « + autre source » | 83×**17** | 83×~~43~~ **25** (voir plus bas : les 43 px étaient faux **et** nuisibles) |
| bouton « Tout charger » | 93×**19** | 97×**44** |
| écart entre les boutons d'une carte | **4 px** | **8 px** |
| champ de recherche | **13 px** | **16 px** |

**Deuxième passe, le 08/09/2026.** La première n'avait traité que l'intérieur
des cartes : toute la barre de commandes de l'application était restée sous le
seuil, y compris son action principale.

| | avant | après |
|---|---|---|
| « Charger N de plus » | **27 px** | **44 px** |
| les 4 boutons d'entête | **34×34** | **44×44** (en `::after`) |
| **« Actualiser »** | **34 px** | **44 px** |
| champ de recherche (hauteur) | **34 px** | **44 px** |
| bouton « Filtres » | **34 px** | **44 px** |
| onglets | **36 px** | **44 px** |
| bouton d'information | **39 px** | **44 px** |

Les boutons d'entête gardent 34 px à l'œil : les agrandir vraiment portait la
rangée à 194 px, soit 22 px de plus que la place disponible à côté du logo sur
un écran de 320 px. Ils gagnent leurs 44 px en `::after`, et leur écart passe
de 6 à 10 px — à 6 px les deux zones élargies se seraient chevauchées de 4 px
et un appui entre deux icônes serait parti sur la mauvaise.

Coût vertical à 390 px : la première carte descend de 523 à 551 px, soit
28 px, **sans changer le nombre de cartes visibles** au premier écran.

Cette passe a aussi mis au jour un débordement qui lui était antérieur : à
320 px, `.header-actions` porte `flex-shrink:0` et refusait de se comprimer,
si bien que le badge de mode se faisait trancher et que le dernier bouton
touchait le bord de la carte. L'entête passe en `flex-wrap` et son bloc de
droite en `margin-left:auto` : sous ~370 px il descend sur sa propre ligne, à
droite, au lieu d'être coupé.

**Troisième passe, le 08/09/2026 : les deux derniers reports sont levés.**
Ils avaient été écartés avec des raisons — l'une bonne mais incomplète,
l'autre carrément fausse.

**Les liens de titre d'article** (36 px sur deux lignes, ~18 sur une) étaient
laissés de côté au motif que c'est du texte en ligne, le cas explicitement
excepté par WCAG 2.5.5 et 2.5.8. L'exception vaut pour un lien *au milieu
d'une phrase*, dont on ne peut pas grossir la zone sans casser l'interligne
du texte autour. Le titre d'article est seul sur ses lignes : elle ne
s'applique pas vraiment. Le lien est devenu un bloc avec `min-height:44px`.

**La coche des cartes en mode dense** (38 px) était écartée parce que
l'élargir « ferait empiéter sa zone sur la carte voisine ». **C'était faux, et
personne ne l'avait mesuré.** Sous la coche il y a 42 px de libre jusqu'au
premier élément *cliquable* de la carte suivante, et 20 px au-dessus jusqu'au
lien de titre. Les 5 px qu'on croyait bloquants sont ceux de la date, qui
n'est pas une cible. Il reste 10 px de marge de chaque côté après
l'élargissement, et un contrôle automatique compare désormais **toutes les
paires** de zones cliquables du fil : 134 zones, aucun chevauchement, dans
les deux densités.

#### L'invariant qui a coûté une passe

En mode dense le titre est coupé à deux lignes. Rendre le lien bloc a cassé
cette coupure — le `-webkit-line-clamp` du titre ne s'applique plus à un
enfant bloc — et la hauteur imposée de 44 px valait alors **2,42 lignes** de
18,2 px : un bout de troisième ligne apparaissait tranché en son milieu, sur
13 titres sur 30.

La coupure a donc été déplacée sur le lien, et son interligne porté à 22 px
pour que **deux lignes fassent exactement les 44 px** de la zone de clic. Une
hauteur imposée et une coupure au nombre de lignes ne cohabitent que si l'une
tombe juste sur l'autre ; un test vérifie cette égalité plutôt que les deux
valeurs séparément.

Ce défaut n'a pas été trouvé par la mesure automatique, qui se contentait de
relire la propriété CSS `-webkit-line-clamp` — toujours à 2, donc toujours
« correcte ». Il a été trouvé **en regardant la capture d'écran**. Le contrôle
compare maintenant la hauteur du texte rendu à celle de sa boîte, ce qui est
le seul symptôme réel.

Coût mesuré à 390 px : la carte moyenne passe de 150 à 153 px en mode normal
et de 111 à 118 px en mode dense, **sans changer le nombre de cartes visibles
au premier écran**.

**Il ne reste plus aucune cible sous 44 px dans le fil**, dans les deux
densités.

Deux principes tiennent tout ça :

- **La zone de clic n'est pas la boîte visuelle.** La coche ✓ et le lien de
  source étendent leur surface réactive par un `::after` en `position:
  absolute` : le bouton garde sa taille à l'œil, la carte garde sa hauteur,
  seul le doigt y gagne. Grossir les boutons aurait cassé la densité
  assumée de l'app.
- **44 px et non 48.** C'est le minimum d'Apple HIG et de WCAG 2.5.5,
  atteignable ici sans voler les clics du voisin — vérifié qu'aucun élément
  interactif ne se trouve dans les bandes ainsi couvertes. Material 3 demande
  48 ; l'app ne le suit délibérément pas, sa densité étant un choix assumé
  jusqu'au mode dense en option.
- **16 px sur un champ de saisie, jamais moins.** En dessous, Safari sur iOS
  ZOOME la page tout seul quand le doigt entre dans le champ, et il faut
  dézoomer à la main après chaque recherche. Le rembourrage vertical
  compense pour que la rangée garde sa hauteur.

Rien ne descend plus sous **10 px** : le plus petit rôle typographique que
Material 3 définisse est 11sp, en dessous il n'y a plus de barème. Cinq
badges y étaient (« SPÉCIALISTE GTA 6 », « VIDÉO », « FR », « OFFICIEL »,
« NOUVEAU ») plus les messages d'erreur de source — c'est-à-dire exactement
le texte qu'il ne faut pas rendre difficile à lire.

*(Les dos d'âne sont réservés aux identifiants du code : le test qui compare
les constantes du README au code a pris « OFFICIEL » pour une constante
inventée, et il avait raison de le faire.)*

**Ce qui a été vérifié et trouvé conforme**, sans rien changer : le zoom
pincé reste autorisé (pas de `user-scalable=no`), la pagination est un
bouton explicite et non un défilement infini, les boutons désactivés le
sont temporairement pendant une action et jamais en permanence, la largeur
de colonne tient **43 caractères** en médiane (la plage visée sur mobile est
35-45), et le seuil de Doherty est tenu largement — 129 ms pour changer
d'onglet, 115 ms pour marquer lu, 48 ms pour une recherche, là où 400 ms
est la limite.

**Trois leçons de méthode**, chacune payée par un cas réel :

- **Mesurer contre les trois fonds**, pas seulement `--bg`. Les pastilles
  d'onglet tenaient 4,64:1 sur la page mais 4,47 sur `--bg-elevated` : seule
  une mesure élément par élément dans le navigateur l'a montré.
- **Le thème clair recopiait les couleurs du sombre.** Son `--accent` était
  exactement le bleu du thème sombre, à 2,79:1. Un thème clair n'est pas un
  thème sombre avec un fond blanc.
- **Un test verrouille les ratios** (`test_contraste_des_deux_themes`) : il
  lit les jetons des deux thèmes, calcule les contrastes contre les trois
  fonds, et refuse tout texte de couleur écrit en dur. Vérifié capable
  d'échouer avant d'être retenu.

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
(instantané), l'autre fait travailler le robot sur les 50 sources
(~1 min 25).

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

**La détection de doublons passe d'abord par le titre exact.** Un
dictionnaire titre normalisé -> article, sans limite d'ancienneté, avant la
comparaison floue. Le motif : la fenêtre floue a un horizon, un
dictionnaire non. Quand elle se comptait en **articles** (200), un pic à
288 articles par jour la réduisait à douze heures, et onze paires de
doublons parfaits lui avaient échappé au 29/08 — le même article sous deux
URL (`ign.com` et `fr.ign.com`, `bbc.com` et `bbc.co.uk`). Elle se compte
en heures depuis le 30/08 (voir plus haut), mais l'argument tient toujours :
élargir une fenêtre ne fait que déplacer sa limite, là où un dictionnaire
n'en a aucune.

`fusionne_doublons_de_titre()` reprend l'historique déjà stocké, comme les
deux autres passes rétroactives. Elle s'en tient au **titre exact** : rejouer
le seuil de 0,75 sur tout l'historique fusionnerait des articles réellement
distincts — « Our GTA 6 Extended Look Predictions » et « How Our GTA 6
Extended Look Predictions Held Up » passent le seuil alors que l'un annonce
ce que l'autre conclut. Le doute profite à la séparation.

**« N sources » compte des rédactions, pas des flux.** `record_coverage()`
n'ajoute une source supplémentaire que si elle apporte un **lien différent**.
Le nom du flux ne suffit pas : quatre requêtes Google News distinctes
remontent souvent la même page, et les compter comme quatre sources gonflait
le badge « actu majeure » sans qu'aucune rédaction de plus n'ait rien publié.

Mesuré sur l'historique du 29/08 avant correctif : **136 des 168 articles
dits « croisés » n'étaient qu'un seul lien recompté**, et le premier badge 🔥
était un article du Newswire trouvé par quatre de nos propres requêtes. Après
correctif : 32 articles réellement croisés (2,5 %), maximum 3 sources, aucun
à 4 — 36 (2,8 %) au 29/08 après la bascule des flux rss.app, toujours 3 au
maximum.

**Le seuil est donc passé de 4 à 3** (`HOT_SOURCE_THRESHOLD`, le 29/08/2026).
À 4, il était devenu inatteignable : un badge qu'aucun article ne peut
déclencher n'est pas une garantie de rigueur, c'est une fonction morte. Le
seuil vaut ce que vaut le comptage, et le comptage est désormais juste.

`deduplique_couverture()` reprend l'historique déjà stocké, comme
`recheck_official_status()` : sans elle, les 136 articles gonflés avant le
correctif garderaient leur compte faux indéfiniment.

**Les onglets classent par éditeur, pas par source.** Un article est
« Rockstar » si son lien est sur `rockstargames.com` ou `take2games.com`,
« RockstarMag » s'il est sur `rockstarmag.fr` — quelle que soit la source qui
l'a trouvé. La déclaration de la source reste honorée en complément, ce qui
couvre la chaîne YouTube de Rockstar : ses liens pointent légitimement vers
youtube.com, domaine impossible à mettre dans la liste globale sans rendre
officielle n'importe quelle vidéo.

Avant, le drapeau était recopié depuis le flux : un article du Newswire ou de
Rockstar Mag remonté par Google News atterrissait dans « Non Rockstar ». Comme
la déduplication garde le premier trouvé, **le même article changeait d'onglet
selon le flux qui gagnait la course**. Cinq articles étaient concernés dans
l'historique.

La repasse rétroactive `recheck_official_status()` corrige désormais **dans
les deux sens**. Ne rétrograder que les faux officiels laissait le défaut
inverse à l'abandon : l'historique n'est jamais rejoué dans le pipeline de
collecte, donc rien ne serait jamais venu chercher les articles mal classés.

**Le badge du bouton « Filtres ».** Il dit qu'un filtre est appliqué sans
avoir à ouvrir le panneau. La langue s'y affiche par son **drapeau** plutôt
que comptée — un « 1 » dit qu'il se passe quelque chose, un drapeau dit quoi.
Les autres filtres restent un nombre, faute d'un symbole aussi parlant, et
les deux coexistent (`🇫🇷 1`) : n'afficher que le drapeau masquerait un
« Non lus » posé par-dessus. Le mode dense ne compte pas : c'est un mode
d'affichage, et « Réinitialiser les filtres » ne le touche pas.

**Ce que `localStorage` conserve.** La copie locale des articles est
plafonnée à **300**, la taille du fichier allégé. Elle ne sert qu'à afficher
quelque chose à l'ouverture avant que le réseau réponde ; sans plafond elle
suivait l'historique — 1,25 Mo pour 1 637 articles, donc environ 15 Mo aux
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
ancien système de récupération directe des 50 sources via des proxys CORS
publics (CodeTabs, allorigins, corsproxy.io, whateverorigin, feed2json,
rss2json). C'est redondant avec le backend, mais volontaire : sans ce
filet de sécurité, l'app serait totalement inutilisable si le backend
tombait, ce qui serait une vraie régression de fiabilité pour un gain de
simplicité qui n'en vaut pas la peine.

### La vignette : quatre essais avant le bandeau

Point de départ, le 08/09/2026 : les vignettes de 36 px du mode compact
étaient trop petites pour qu'on distingue l'image.

**Essai 1 — agrandir le carré du compact.** La place était déjà là : la
colonne de texte fait 105 px de haut, la vignette 36, soit **69 px de vide** à
côté d'elle. Passée à 80 px, la hauteur de carte ne bouge pas d'un pixel.
80 est un maximum mesuré : le côté se prend sur la colonne de texte, donc sur
les quatre boutons qui la partagent, et à 88 px ce sont 80 cibles qui
repassent sous leur seuil à 320 px.

**Essai 2 — la même chose en mode normal.** Refusé après capture : étirée sur
la hauteur, la vignette donnait une bande verticale dont la taille changeait
d'une carte à l'autre — 119 à 215 px à 320 px selon la longueur du titre — et
rognait durement des images qui sont presque toutes en 16:9.

**Essai 3 — un carré, plus grand en normal qu'en compact.** Remarque juste au
passage : le mode aéré avait la **plus petite** vignette (64 contre 80), parce
que chaque mode avait été réglé isolément sans jamais les comparer. Mais le
carré plafonne à **68 px à 320 px** — trop petit pour qu'on distingue l'image,
ce qui était toute la demande.

**Le carré à la hauteur de la carte ne converge pas.** La hauteur de la carte
dépend de la largeur de la colonne de texte (titre plus étroit = plus de
lignes = carte plus haute). Si le carré doit faire la hauteur de la carte, il
doit être large ; large, il rétrécit la colonne, donc la carte grandit, donc
le carré doit grandir. La seule sortie serait de **figer** la hauteur des
cartes en coupant les titres à 3 lignes — mesuré comme faisable à partir de
360 px, impossible à 320.

**Essai 4, retenu — le bandeau.** La vignette passe au-dessus du texte, sur
toute la largeur de la carte. Elle ne prend plus rien à la colonne de texte,
donc plus rien aux boutons : le problème disparaît au lieu d'être arbitré.

| | avant | bandeau |
|---|---|---|
| vignette à 390 px | 64×64 | **356×200** |
| plus petit bouton | 57 px | **75 px** |
| hauteur de carte | 167 px | 348 px |
| **articles par écran** | **4,7** | **2,3** |

Le prix est là et il est assumé : **on voit deux fois moins d'articles d'un
coup d'œil**. Les formats plus plats ont été mesurés — 2:1 donne 2,5 articles,
21:9 en donne 2,7, 3:1 en donne 3,0 — et le 16:9 a été gardé parce que c'est
le format natif des images des sources : il ne rogne rien.

**Aucun HTML n'a été déplacé.** Le `<img>` reste le premier enfant de
`.card-top` ; c'est la direction du flex qui passe en colonne. Vérifié
qu'aucun JS ne cible `.card-thumb`, `.card-top` ni `.card-body`, mais ne pas
toucher au balisage supprime la question.

**Le compact garde la vignette à côté du texte**, en carré de 80 px, et c'est
là tout l'intérêt d'avoir deux modes : le normal montre les images, le compact
montre le plus d'articles possible. Un bandeau y doublerait la hauteur des
cartes et supprimerait la raison d'être du mode. Il doit donc défaire *chacune*
des propriétés du bandeau — direction du flex, format, marges négatives,
bordures : un `aspect-ratio` resté en 16:9 sur un carré de 80 px, et la
vignette redevient une bande. Un test verrouille les quatre séparément.

Un autre test verrouille le lien invisible entre deux règles éloignées : le
débordement du bandeau (`margin:-14px -16px 0`) doit valoir exactement le
rembourrage de la carte (`padding:14px 16px`), sinon il s'arrête avant le bord
ou dépasse. Rien d'autre ne relie ces deux valeurs.

Vérifié sur 16 configurations (320/360/390/412 px × sombre/clair ×
normal/compact) : 0 cible hors de son seuil, 0 chevauchement, 0 débordement
horizontal, 0 erreur JavaScript.

### Le défaut que trois audits avaient manqué

En vérifiant les vignettes, le navigateur a signalé une cible sous le seuil et
deux zones qui se recouvraient, en mode **normal**, à toutes les largeurs.
Rien à voir avec le changement en cours : un défaut présent depuis la première
passe tactile du 04/09.

Le commentaire écrit au-dessus du code affirmait deux choses, toutes les deux
fausses :

> `-13 px` : porte la zone à 44 px […] Vérifié qu'aucun élément interactif ne
> se trouve dans la bande ainsi couverte, au-dessus comme en dessous.

1. **17 + 2×13 = 43**, pas 44. Il en manquait un.
2. Il y a **8 px** entre le bas de ce lien et la rangée de boutons, et la coche
   **remonte elle-même de 7 px**. Les 13 px de descente traversaient les deux :
   un appui juste sous « + 1 autre source » partait sur ✅.

**Pourquoi personne ne l'avait vu.** La ligne « + autre source » n'apparaît que
sur les articles repris par plusieurs rédactions. Il n'y en avait aucun à
l'écran lors des audits précédents — le robot venait d'en publier un.

**Pourquoi les tests ne l'avaient pas vu non plus**, et c'est plus grave :

- le seuil était écrit `atteinte >= 43` alors que le message annonçait
  « 44 visé ». 43 passait donc en se faisant passer pour conforme ;
- le calcul lisait `inset:-13px 0` en supposant le débordement **symétrique**,
  et ne vérifiait les recouvrements qu'entre voisins **de même type**. Deux
  éléments différents qui se font face verticalement étaient l'angle mort.

**Corrigé.** La zone ne descend plus du tout (1 px de marge sous elle) et monte
de 8 px : 17 + 8 = **25 px**. Au-dessus des 24 px de WCAG 2.5.8, sous les 44 px
de WCAG 2.5.5, et c'est le maximum atteignable : il n'y a que 21 px au-dessus
jusqu'au titre et 8 en dessous dont 7 sont déjà pris. Entre une cible de 25 px
et une cible de 43 px qui déclenche le mauvais bouton, la première est la
bonne. Agrandir vraiment ce lien demanderait d'écarter la rangée de boutons,
donc de rallonger toutes les cartes pour un lien secondaire.

Trois tests nouveaux ferment l'angle mort : un lecteur d'`inset` qui lit les
quatre côtés, l'inégalité entre le bas du lien et le haut de la coche, et le
vrai minimum par élément au lieu d'un seuil unique rabaissé à 43.

**Et le lecteur d'`inset` était faux à sa première écriture** : il cherchait
`-?(\d+)px` et perdait donc tous les zéros sans unité, si bien que sur
`-8px 0 0` il ne voyait qu'une valeur, la recopiait sur les quatre côtés, et
annonçait 33 px de zone au lieu de 25. Le script d'audit navigateur avait
exactement le même défaut et fabriquait un chevauchement inexistant. Les deux
sont corrigés, et quatre cas de lecture d'`inset` sont maintenant vérifiés par
la suite elle-même — un analyseur faux est pire qu'une valeur écrite en dur,
parce qu'il donne des chiffres crédibles.

**Vérification finale**, 16 configurations (320/360/390/412 px × sombre/clair ×
normal/compact) : 0 cible hors de son seuil, 0 chevauchement, 0 débordement
horizontal, 0 erreur JavaScript. Aucune couleur n'a été touchée, le contraste
est donc inchangé.

### Audit de conformité du 10/09/2026 — et deux fausses alertes

Vérification complète de l'app contre ses références, sans toucher une ligne
de code. **Rien à corriger.**

**L'échelle Material 3, valeur par valeur.** `m3.material.io` est inaccessible
depuis l'environnement de travail, mais les fichiers de jetons de Google sont
publics : `md-sys-shape` et `md-sys-motion`. Les 4 rayons, les 2 durées et les
2 courbes correspondent exactement, `cubic-bezier(0.2, 0, 0, 1)` compris.

Et surtout, l'échelle est **réellement utilisée** : les 12 transitions du
fichier passent toutes par `var(--t-*)` et `var(--courbe*)`, aucune valeur en
dur. Les seules exceptions sont justifiées — `50%` pour des cercles, `0` pour
deux éléments qui débordent volontairement, `ease-in-out` pour les pulsations
infinies.

**Une faiblesse relevée au passage, et non corrigée.** Les deux *courbes* sont
verrouillées par un test (`--courbe` et `--courbe-entree` sont comparées aux
valeurs Material 3, et un autre test interdit qu'une transition retombe sur
une courbe par défaut du navigateur). Les quatre *rayons* et les deux *durées*
ne le sont pas : leur conformité a été vérifiée à la main le 10/09, rien
n'empêche une dérive ensuite. Le test manquant serait de comparer
`--r-xs/--r-sm/--r-md/--r-full` à 4/8/12/9999 px et `--t-court/--t-moyen` à
150/250 ms, comme cela se fait déjà pour les courbes.

**Vingt configurations** (320/360/390/412/768 px × clair/sombre ×
normal/compact) : 0 cible sous son seuil, 0 chevauchement, 0 débordement
horizontal, 0 erreur JavaScript, 0 saut de niveau de titre, zoom non bloqué,
focus visible partout. Dialogues **5/5**. En mouvement réduit : 0 animation et
**0 transition produisant un déplacement** — les 9 restantes sont des fondus
de couleur, sans effet vestibulaire.

#### Les deux défauts trouvés étaient dans les instruments, pas dans l'app

C'est la partie qui mérite d'être retenue.

**« 31 échecs de contraste en thème clair ».** Faux. Le détecteur remontait le
DOM jusqu'à un ancêtre opaque et ignorait le fond semi-transparent de
l'élément lui-même. Il a produit **3,39 puis 4,38** — deux chiffres crédibles
et faux, obtenus par deux versions du même script. Tranché en lisant les
**pixels réellement affichés** : **4,98:1** en clair, **7,89:1** en sombre, les
deux au-dessus du seuil de 4,5. Le fond composé mesuré (`rgb(220,234,225)`)
correspond au calcul à la main, ce qui confirme la mesure.

**« Défilement perdu à l'ouverture des Paramètres ».** Faux aussi. Playwright
fait défiler la page pour amener sa cible à l'écran avant de cliquer : le
bouton étant dans l'entête, la page remontait en haut *avant* que le gel de
fond ne s'active. Prouvé en lisant `body.style.top` pendant l'ouverture —
`0px` avec un clic Playwright, `-700px` avec un clic déclenché en JavaScript,
et dans ce second cas le défilement est bien restauré à 700.

Le seul vrai défaut trouvé était dans l'inventaire : 📋 avait été compté comme
un dialogue alors que c'est un onglet (`setTab('logs')`).

**La leçon, la troisième de cette série :** après l'analyseur d'`inset` qui
perdait les zéros et le script d'audit qui supposait l'inset symétrique, c'est
la troisième fois qu'un outil de mesure produit un chiffre faux et plausible.
Un instrument qui se trompe coûte plus cher qu'une absence de mesure, parce
qu'on lui fait confiance. La parade est toujours la même : quand deux méthodes
divergent, descendre d'un cran vers ce qui est le plus proche du réel — ici,
les pixels.

### Les filtres survivent à la fermeture

L'onglet, la langue et l'état (Tout / Non lus) sont retenus d'une ouverture à
l'autre. Trois décisions valent d'être expliquées.

**Une clé séparée, pas un champ de plus dans `settings`.** `saveSettings()`
lit les champs du panneau de paramètres *dans le DOM* — l'appeler depuis un
clic sur un onglet écraserait l'URL du backend et la liste de mots-clés avec
des champs éventuellement jamais remplis, et casserait l'app. Deux besoins,
deux clés : `settings-v1` d'un côté, `filtres-v1` de l'autre. Un test vérifie
que les trois fonctions de filtre ne touchent jamais à `saveSettings`.

**Deux états ne sont volontairement pas restaurés :**

- **« Nouveaux »** s'appuie sur la liste des articles nouveaux du dernier
  passage, reconstruite de zéro à chaque ouverture et **vide** tant qu'aucun
  rafraîchissement n'a eu lieu. Le restaurer ferait rouvrir l'app sur un fil
  vide sans que rien n'explique pourquoi.
- **L'onglet du journal** : rouvrir directement sur les logs du robot plutôt
  que sur les actualités n'est jamais ce qu'on veut.

Dans les deux cas on retombe sur le dernier état mémorisable connu — et non
sur le défaut. Sans cette nuance, un simple coup d'œil au journal effaçait un
« Rockstar » installé depuis des jours, et un aller-retour par « Nouveaux »
effaçait « Non lus ».

**La recherche n'est pas mémorisée.** Un mot-clé oublié dans la barre filtre
le fil sans qu'on s'en aperçoive — c'est bien moins visible qu'une pastille
d'onglet allumée. La barre repart vide à chaque ouverture.

Toute valeur lue est validée contre une liste blanche : une clé corrompue, ou
écrite par une version antérieure, retombe sur le défaut plutôt que de coincer
l'app dans un état qu'aucun bouton ne saurait défaire. Vérifié dans le
navigateur en injectant `{"onglet":"n_importe_quoi","langue":42,"etat":null}` :
ouverture propre, aucune erreur.

La restauration ne dessine qu'une fois. Les trois fonctions acceptent un mode
silencieux qui pose l'état et allume les boutons sans redessiner ; le rendu
unique de `loadState` suit. Sans ça, ouvrir l'app enchaînait trois rendus
successifs de trois cents articles.

### Icônes : des emojis, sauf là où la couleur porte du sens

Les quatre boutons d'entête et les commandes de carte utilisaient des glyphes
Unicode choisis à la main — des demi-cercles pour le thème, un carré hachuré
pour le journal, un i cerclé pour les informations. Remplacés par des emojis,
plus immédiatement reconnaissables :

| | avant | après |
|---|---|---|
| bascule de thème | demi-cercles | 🌙 en clair, ☀️ en sombre |
| paramètres | engrenage texte | ⚙️ |
| journal des passages | carré hachuré | 📋 |
| informations | i cerclé | ℹ️ |
| marquer lu / non lu | coche / flèche | ✅ / ↩️ |
| copier le lien | carrés superposés | 🔗 |
| aperçu | œil | 👁️ |

Deux détails qui ne se voient pas dans un tableau :

- **L'icône de thème annonce désormais l'action, pas l'état.** En thème clair
  elle montre 🌙 — « clique pour passer au sombre ». Les deux demi-cercles
  d'avant décrivaient l'état courant, ce qui laissait deviner dans quel sens
  le clic allait. L'étiquette d'accessibilité, « Changer de thème », était
  déjà une action ; l'icône la rejoint.
- **La confirmation de copie reste un glyphe texte.** C'est la seule des trois
  icônes de carte qui soit teintée par CSS : quand un lien est copié, le
  bouton passe en vert une seconde et demie, et c'est ce vert qui la
  distingue du bouton « marquer lu » juste à côté. La couleur d'un emoji ne
  se pilote pas — en emoji, la confirmation serait devenue le sosie exact de
  son voisin. Un test verrouille ce point précis, parce que c'est exactement
  le genre de détail qu'une passe d'harmonisation ultérieure « corrigerait »
  de bonne foi.

Le sélecteur de variante `U+FE0F` est obligatoire sur ⚙️ et ℹ️ : sans lui,
certains systèmes les rendent en noir et blanc façon glyphe texte, soit
exactement ce qu'on cherchait à quitter. Un test compte les occurrences nues.

Mesuré dans Chromium : les quatre emojis d'entête occupent tous 19×18 px dans
un bouton de 34×34, ceux des cartes 16,5×15 — aucun débordement, aucune
rangée déplacée, la rangée d'entête tient toujours ses 166 px.

### Les panneaux sont de vrais dialogues

Audité le 08/09/2026. Le contraste y était déjà bon — zéro élément sous le
seuil, deux thèmes, tous les panneaux. L'accessibilité, elle, ne l'était pas.

Le panneau de **confirmation** était le seul vrai dialogue. Les quatre autres
n'avaient ni rôle, ni `aria-modal`, ni nom : on ouvrait les paramètres et le
focus restait sur le bouton d'engrenage *derrière* le voile, la tabulation
promenait dans la page cachée, et Échap ne faisait rien. Et **aucun des cinq**,
celui de confirmation compris, ne piégeait le focus.

| | avant | après |
|---|---|---|
| panneaux avec un rôle et un nom | 1 / 5 | **5 / 5** |
| le focus entre dans le panneau | 1 / 5 | **5 / 5** |
| Échap ferme | 1 / 5 | **5 / 5** |
| focus piégé | **0 / 5** | **5 / 5** |
| le fond reste immobile | 0 / 5 | **5 / 5** |
| cibles sous 44 px | 8 | **0** |

**44 px est devenu le défaut**, pas une liste. La passe précédente avait
énuméré les commandes de la page et oublié tout l'intérieur des panneaux, où
« Fermer » tenait encore dans **23 px** — le plus petit élément de l'app. Le
bouton de base porte maintenant `min-height`, et les quatre boutons dont la
petitesse est structurelle y dérogent explicitement : icônes d'entête carrées,
coche des cartes et croix de recherche posées en absolu, bouton d'aide. Un
défaut se périme moins vite qu'une liste.

**Le titre de chaque panneau est un `h2`**, avec `font:inherit` pour que rien
ne bouge à l'œil. Le bouton « Fermer » reste *hors* du titre : dedans, il
serait lu comme faisant partie de son intitulé. La feuille de filtres n'avait
aucun titre — elle reçoit un `h2` invisible, sans quoi un lecteur d'écran
annonce « dialogue » et rien d'autre.

**Le fond est figé en `position:fixed`** et non en `overflow:hidden` : Safari
iOS ignore le second dès que le doigt arrive au bout du panneau et se met à
faire glisser la page derrière.

**Une pile, pas une variable.** Une confirmation peut s'ouvrir par-dessus les
paramètres : fermer celle du dessus rend le focus au panneau du dessous, et le
fond n'est libéré qu'au dernier fermé.

#### Deux bugs trouvés par la mesure, corrigés dans le même lot

**La position de défilement était perdue.** La libération la restituait
correctement — puis le retour du focus la défaisait aussitôt, parce que
focaliser un élément hors écran fait défiler la page jusqu'à lui, et
l'élément d'origine est presque toujours un bouton d'entête. Ouvrir un panneau
depuis le milieu du fil et le refermer renvoyait au sommet : 1500 px
redevenaient 0. Corrigé par `preventScroll`.

J'avais d'abord soupçonné un défaut de recalcul de mise en page et ajouté un
reflow forcé. C'était faux : la mesure pas à pas a montré que la libération
marchait déjà. Le reflow a été retiré. **Deux corrections plausibles, une
seule vraie — c'est la mesure qui a tranché, pas le raisonnement.**

**Un test existant est devenu faux sans devenir rouge.** Il vérifiait le focus
initial de la confirmation en cherchant un appel `.focus()` écrit à la main.
Ce code n'existe plus : le focus initial est désormais *déclaré* à la
mécanique commune. Le test vise maintenant l'intention déclarée.

#### Ce que les tests ne prouvent pas

Les quatre vérifications du piège de focus **lisent le code sans l'exécuter**.
Un `return` glissé au début du piège les laisse toutes passer — essayé, elles
passent. Elles constatent que le piège est *écrit*, pas qu'il fonctionne.

Son comportement est mesuré dans un vrai navigateur : un tour complet de
tabulation sur chacun des cinq panneaux, **zéro sortie**. Cette suite en
Python sans navigateur ne sait pas le faire, et la limite est écrite dans le
test plutôt que passée sous silence.

### Structure de la page et retour non visuel

Audité le 08/09/2026, à la demande, contre Material 3 et les lois d'UX. Deux
défauts qu'aucune mesure de couleur ou de taille ne pouvait révéler :

**La page n'avait aucun plan de titres.** Pas un seul `h1` : la marque, les
séparateurs de jour et les titres d'articles étaient tous des `div`, et les
deux seuls `h3` du fichier étaient enfermés dans des boîtes de dialogue. Au
lecteur d'écran, la page était un mur plat — impossible de sauter d'article
en article ou de jour en jour. Elle a maintenant un plan complet :

```
h1  GTA6_WATCH
  h2  AUJOURD'HUI
    h3  GTA 6 change totalement la gestion des armes
    h3  GTA 6 sortira à minuit dans chaque pays…
```

…plus un repère `main` autour de la recherche, des onglets, du fil et du
journal. L'entête et les bandeaux d'alerte restent en dehors : ce sont des
annonces, pas le contenu qu'on vient lire.

Le changement est purement sémantique. `.wrap` est un bloc simple et non un
conteneur flex, les classes portent déjà toute la typographie, et la remise à
zéro globale des marges neutralise les styles par défaut des titres : mesuré
avant/après, la première carte reste exactement à `y=551`.

**Rien n'était annoncé après une actualisation.** Quatre lignes se
réécrivaient — état du run, soucis, compteur, historique — sans un seul
`aria-live` dans le fichier. Visuellement la réponse arrive en 83 ms sous le
bouton ; sans les yeux, elle n'arrivait jamais.

Une seule région live y remédie, et pas une par ligne : les quatre se
réécrivent au même instant, quatre régions auraient produit quatre annonces
qui se coupent la parole. Elle compose une phrase à partir du texte
**réellement affiché**, ce qui garantit qu'annonce et écran ne divergeront
jamais :

> Actualisé. 300 affichés, 300 non lus. 54 s, 3 nouveaux, 48/50 sources.
> 2 cassées : Rockstar Games (YouTube), RockstarMag (YouTube).

Trois détails qui font la différence entre une région live qui marche et une
qui ne dit rien :

- **`sr-only` masque sans retirer de l'arbre.** `display:none` ou
  `visibility:hidden` rendraient la région définitivement muette ; il faut la
  sortir du flux en `position:absolute` sur 1×1 px.
- **On vide avant de réécrire.** Une région n'annonce que ce qui *change* :
  deux actualisations au résultat identique resteraient silencieuses alors que
  l'utilisateur attend confirmation de son geste.
- **Les deux parcours sont branchés.** Le mode backend et le mode direct
  finissent par des chemins différents ; n'en instrumenter qu'un laisserait
  l'autre muet. Un test compte les deux points d'appel.

**Ce qui a été mesuré et trouvé conforme**, sans rien changer : le seuil de
Doherty (83 ms entre le clic et le premier changement visible, pour une limite
à 400), les trois commandes de carte qui portent toutes `aria-label` et
`title`, `lang="fr"` sur le document, aucune image sans `alt`, et le
tirer-pour-rafraîchir qui rattrape la position haute du bouton principal —
la loi de Fitts pénalise le haut de l'écran, le geste rend l'action
accessible au pouce.

**Sur Material 3, une précision qui compte.** Les valeurs de l'échelle de
formes et de mouvement viennent des fichiers de jetons que Google génère
lui-même, pas du site : 4, 8, 12 et 9999 px sont exactement ses crans
extra-small, small, medium et full, et 150 et 250 ms ses durées short3 et
medium1. **L'affectation, elle, diverge volontairement** — Google met ses
boutons et ses pastilles en gélule et ses dialogues à 28 px, ce qu'une
identité monospace et anguleuse ne supporte pas. On emprunte l'échelle, pas
le style. C'est écrit dans le code aussi, pour que personne n'y lise plus
tard une conformité qui n'existe pas.

## Notifications push natives

Discord fonctionne, mais taper une notification Discord ouvre Discord,
jamais l'article. Une notification push native ouvre directement le site.
Les deux coexistent : chacune s'active par la présence de ses secrets, et
se désactive par leur absence.

**Les deux annoncent mot pour mot la même chose**, et ne peuvent pas
diverger : le texte est écrit une seule fois dans
`feed_store.libelle_recap()`, appelé par les deux canaux.

### Le carré blanc dans la barre d'état

Signalé le 12/09/2026 : l'icône de la notification push était un carré blanc
sur le téléphone. Défaut présent depuis l'ajout des push, jamais remarqué.

**`icon` et `badge` ne se comportent pas pareil, et c'est tout le piège.**
`icon` est la grande image affichée dans la notification dépliée : elle sort
en couleur, telle quelle. `badge` est la petite icône de la **barre d'état**,
et Android n'en garde que le **canal alpha** — il jette la couleur et repeint
la silhouette en blanc.

`sw.js` déclarait `badge: "icon-192.png"`. Or les quatre icônes de l'app sont
des PNG de **type 2 — RVB, sans aucun canal alpha** : tous les pixels sont
opaques. La silhouette obtenue est donc le carré entier. D'où le carré blanc,
et il ne pouvait pas en être autrement.

**Corrigé** par un fichier dédié, `docs/icon-badge.png` : 96×96 en RGBA, un
« VI » blanc sur fond entièrement transparent — **16 % de pixels opaques,
83 % transparents**. Le chiffre romain plutôt qu'un simple « 6 » parce qu'il
reste identifiable à 24 px, taille réelle d'affichage dans la barre.

**Le nom du cache est passé en `v4`, et ce n'est pas cosmétique.** Sans
remplacement du service worker, l'ancien resterait actif sur les téléphones
déjà installés et continuerait d'envoyer l'ancien badge : la correction ne
serait jamais parvenue à l'appareil. C'est la même raison qui avait fait
passer en `v3` à l'ajout des push.

**Sept vérifications verrouillent l'ensemble** : le badge existe, c'est un
PNG, il a un canal alpha, il est carré, son fond est *réellement* transparent
(un RGBA entièrement opaque redonnerait le carré blanc, avoir le canal ne
suffit pas), et le nom du cache est bien au-delà de v4.

C'est typiquement le défaut qu'aucun audit du site ne pouvait trouver : il ne
se voit ni dans le navigateur, ni dans le HTML, seulement sur un vrai
téléphone Android qui reçoit une vraie notification.

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

1. Ouvrir l'app → ⚙️ Paramètres. Tant que le dépôt n'a pas de clés, un bloc
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

6. ⚙️ Paramètres → *Notifications sur cet appareil* → **Activer les
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

Le robot chronomètre donc **depuis quand** chaque source ne renvoie plus la
moindre entrée brute (`sources_silence` dans `feed.json`, faute d'autre
stockage persistant) et envoie un message Discord **au moment où l'état
bascule** :

```
🔴 VG247 ne renvoie plus rien depuis 24 h.
🟢 VG247 est revenue.
```

- **`DEAD_SOURCE_HOURS = 24`** — en HEURES, pas en passages. Un passage n'est
  pas une unité de temps : l'écart entre deux va d'une heure à près de cinq
  selon que GitHub honore ou abandonne son exécution planifiée, donc
  « depuis 6 passages » ne disait rien d'exploitable. Vingt-quatre heures,
  c'est assez pour écarter une panne serveur passagère ou une coupure
  réseau, assez peu pour ne pas laisser un flux mort passer la semaine.

  *(Ce README a documenté une constante DEAD_SOURCE_RUNS valant 6 jusqu'au
  04/09/2026 — elle n'a jamais existé dans le code. Repérée en comparant une
  à une toutes les constantes citées ici aux valeurs réelles ; un test le
  fait désormais à chaque commit.)*
- **`REPRISE_CONFIRMEE = 2`** — le chronomètre ne repart à zéro qu'après deux
  passages réussis d'affilée. Une source qui alterne réussite et échec garde
  donc son chronomètre en marche, là où une seule réussite la rendrait
  invisible pour toujours.
- **Une alerte par bascule, jamais par passage.** Sans ça, une panne d'une
  journée produirait 48 messages identiques.
- Un flux qui répond `304` est vivant et n'entre pas dans le comptage ; une
  source « tarie » (elle répond, mais l'actualité est calme) non plus.
- Seules les sources en difficulté sont conservées dans `sources_silence` :
  inutile d'écrire 35 zéros dans `feed.json` à chaque passage.

### Éprouvé en conditions réelles, soirée du 09/09/2026

Quatre sources sont tombées le même soir, à une heure d'intervalle. Aucune
alerte n'est partie, et c'était le bon comportement.

| heure (Paris) | événement |
|---|---|
| 16h01 | **VGC** répond `403`. Chronomètre lancé. |
| 17h02 | VGC revient (`200`, 10 entrées). Au **même passage**, `Polygon`, `Game Rant` et `DualShockers` échouent. |
| 18h01 | les trois reviennent. |
| 18h51 | chronomètres effacés après 2 passages réussis d'affilée. |

L'erreur des trois, lue dans le journal, n'était pas un refus mais une
**connexion refermée sans réponse** — signature d'un filtre anti-robot, pas
d'une panne. Le robot avait déjà réessayé chacune sans requête conditionnelle,
et les deux tentatives ont échoué ; les 65 s du passage (contre 40 à 48
d'habitude) venaient de ces secondes tentatives, pas d'un réseau lent.

Trois domaines sans rapport — `polygon.com`, `gamerant.com`,
`dualshockers.com` — tombés à la même seconde, sur onze passages parfaits
suivis d'un seul à zéro : `10,10,10,10,10,10,10,10,10,10,10,0`.

**La tentation était de changer les URL.** C'est exactement ce qu'il ne fallait
pas faire : quatre sources qui fonctionnaient auraient été remplacées par des
inconnues, et le vrai problème aurait disparu tout seul entre-temps. La règle
« ne jamais deviner une URL de remplacement, elle doit venir d'une sonde ou
d'une ligne *redirigé vers* » a tenu.

Le seuil de 24 h a fait son travail : il a absorbé deux incidents d'un
passage chacun sans réveiller personne. C'est précisément ce pour quoi il vaut
24 h et non 6 passages.

**Exception assumée à la règle « un seul message Discord par passage ».**
Cette règle existe pour empêcher un message par *article*. Une alerte de
source est d'une autre nature, et surtout elle ne peut pas voyager dans le
récapitulatif, qui n'est pas envoyé quand il n'y a rien de neuf.

## Pause nocturne : rien entre 0h et 5h

Demandé le 08/09/2026 : les notifications réveillaient. Entre **00h00 et
05h00, heure de Paris**, le robot ne **notifie** plus — mais il continue de
récupérer et de publier, si bien que le fil est à jour au réveil.

> Cette phrase disait au départ « ne récupère rien, ne publie rien et ne
> notifie rien », ce qui décrivait le dessein initial et contredisait la suite
> de cette même section. L'exigence « les annonces de Rockstar sont
> prioritaires » l'a fait changer avant même la première mise en service : on
> ne peut pas savoir qu'une annonce est tombée sans aller la chercher.

### Éprouvé en conditions réelles, nuit du 08 au 09/09/2026

La première nuit sous le nouveau régime, vérifiée le lendemain matin par deux
chemins indépendants.

**Ce que disent les journaux.** Passage de 01h01, silence actif :

```
SEULEMENT_OFFICIELS: 1
Pause nocturne — 2 article(s) mis de côté, 4 en attente du récapitulatif du matin.
[discord] pause nocturne — 0 annonce(s) officielle(s) envoyée(s), le reste attend le matin.
[push]    pause nocturne et aucune annonce officielle — rien n'est envoyé.
```

Passage de 05h01, silence levé :

```
SEULEMENT_OFFICIELS: 0
[discord] envoi du récapitulatif (7 nouvel(le)(s) article(s))...
[push]    envoi à 1 appareil(s) : 🎮 7 nouveaux articles GTA 6
[push]    1/1 notification(s) envoyée(s)
```

**Ce que dit le dépôt, sans passer par les journaux.** En comparant les liens
présents dans `docs/feed.json` entre le dernier commit d'avant minuit et celui
de 05h01 : **7 articles ajoutés**. Le récapitulatif en a annoncé **7**.

Cette égalité est la vraie preuve, et elle est plus forte que la lecture d'un
journal : elle couvre les **cinq** passages nocturnes, pas seulement celui
qu'on a ouvert. Si l'un d'eux avait notifié, il aurait remis l'ardoise à zéro
et 05h01 aurait annoncé moins de 7.

| | résultat |
|---|---|
| passages pendant la pause | **6** (00h01, 01h01, 01h36, 02h01, 03h01, 04h01) |
| échecs | **aucun** |
| notifications envoyées entre 00h et 04h | **0** |
| articles publiés dans l'app pendant la nuit | **7**, lisibles dès le réveil |
| récapitulatif à 05h01 | **7 annoncés**, sur les deux canaux |
| signal de vie à healthchecks.io | envoyé à **chaque** passage |

**Ce que cette nuit n'a PAS testé.** Aucun article officiel de Rockstar n'est
tombé. L'alerte qui doit percer le silence n'a donc pas eu l'occasion de se
déclencher en vrai : elle reste couverte par les tests, pas par l'usage.

**Le garde est dans le workflow, pas dans le planificateur.** L'horloge réelle
est cron-job.org, qui n'est pas dans ce dépôt et qu'un changement
d'hébergeur remplacerait un jour. En plaçant la décision dans
`update-feeds.yml`, elle s'applique à tous les déclencheurs automatiques sans
rien à configurer ailleurs.

**Une demande à la main passe toujours, à n'importe quelle heure.** La pause
existe pour que le robot ne réveille personne *de lui-même*, pas pour refuser
un ordre explicite. Les deux se distinguent sans ambiguïté par
`github.event_name` :

| déclencheur | qui | pause ? |
|---|---|---|
| `workflow_dispatch` | le bouton « Relancer le robot » de l'app, et « Run workflow » sur GitHub | **jamais** |
| `repository_dispatch` | cron-job.org | oui |
| `schedule` | le filet de GitHub | oui |

Le bouton de l'app poste sur `/actions/workflows/…/dispatches` — c'est bien un
`workflow_dispatch`, pas le `repository_dispatch` qu'utilise cron-job.org. La
distinction est donc gratuite, il n'y a rien à changer côté app.

**Le passage tourne quand même, et publie.** C'est la *notification*, et elle
seule, qui se tait. Le robot récupère, déduplique et publie comme en pleine
journée : le fil est donc à jour au réveil, et healthchecks.io reçoit son
signal chaque heure — sa détection de panne n'est **pas dégradée d'une
minute**.

Ce n'était pas le dessein initial : la pause sautait tout le passage, ce qui
donnait un récapitulatif unique à 5h. C'est l'exigence « les annonces de
Rockstar sont prioritaires » qui l'a fait changer — on ne peut pas savoir
qu'une annonce est tombée sans aller la chercher. Voir *Une annonce de
Rockstar réveille* ci-dessous.

**Le fuseau est calculé, pas figé.** `TZ=Europe/Paris` et non un décalage UTC
en dur : la fenêtre reste 00h-05h locales des deux côtés du changement
d'heure. Un cron UTC figé l'aurait décalée d'une heure fin octobre. Un test
exécute le garde à des instants choisis en été **et** en hiver.

**Un garde en panne laisse passer.** Si l'heure de Paris est indéterminable
(zoneinfo absent, `date` en échec), le passage a lieu normalement. Rater une
pause est un désagrément ; bloquer le robot pour toujours est une panne.

**Exception le jour de la sortie.** La pause est levée du 18 au 20/11/2026
inclus — la veille, le jour même et le lendemain. GTA 6 sort à minuit, et
c'est précisément la nuit où il ne faut rien manquer. Cette date est écrite à
deux endroits (le workflow et `GTA6_RELEASE` dans l'app) : un test vérifie que
la fenêtre encadre bien la date annoncée par l'app, pour qu'elles ne divergent
pas si Rockstar décale encore.

**Le récapitulatif du matin est conservé**, et c'est le seul morceau de
mécanique qu'il a fallu construire pour ça.

Un article n'est « nouveau » que par comparaison avec le `feed.json` publié.
Dès qu'un passage nocturne publie à 2h, l'article n'est plus nouveau à 5h :
sans rien de plus, le récapitulatif du matin annoncerait « 0 nouveau » alors
que la nuit en a apporté douze. Et les articles ne portent **aucune date de
première vue** — seulement leur date de publication d'origine — donc rien ne
permet de le recalculer après coup.

`feed.json` porte donc un champ `attente_recap` : **trois entiers**, le nombre
d'articles, le nombre d'officiels, et le pic de sources sur un même sujet.
Chaque passage nocturne les cumule ; le premier passage de la journée les
ressort, les ajoute aux siens, annonce le tout et remet l'ardoise à zéro.

```
  00h  +3 articles              → en attente {articles:3,  officiels:0, sommet:1}
  01h  +2                       → en attente {articles:5,  officiels:0, sommet:2}
  02h  +4 (dont 1 officiel)     → en attente {articles:9,  officiels:1, sommet:4}
  03h  +0                       → inchangé
  04h  +3                       → en attente {articles:12, officiels:1, sommet:4}
  05h  +2  ON ANNONCE 14, l'ardoise est effacée

  🚨 Actu majeure — 4 sources sur le même sujet
     · 14 nouveaux articles GTA 6 (dont 1 officiel Rockstar)
```

**Trois entiers et non les articles eux-mêmes** : c'est tout ce dont le
libellé a besoin, et `feed.json` est téléchargé par l'app à chaque ouverture —
y stocker des articles en double se paierait à chaque visite.

**Le sommet est un maximum, pas une somme.** Trois passages à quatre sources
sur le même sujet, ça reste quatre sources. Une somme aurait fait passer une
nuit ordinaire pour une actu majeure.

**Le libellé est le même par les deux chemins.** `libelle_recap` compte les
articles puis appelle `libelle_recap_depuis_comptes` ; le récapitulatif du
matin appelle directement la seconde. Une formulation écrite deux fois aurait
dérivé au premier ajustement.

**À la fusion après conflit de push, l'arriéré prend le maximum des deux
côtés, jamais la somme** : les deux partent du même arriéré, les additionner
le compterait deux fois. Le maximum peut sous-estimer d'un passage — dans un
message qui annonce un nombre, mieux vaut annoncer un peu moins que d'inventer.
Le cas reste théorique : le workflow sérialise ses exécutions.

#### Le bug que le test a trouvé

`send_discord_notification` avait **sa propre** garde interne
« rien de neuf, on ne dit rien », en plus de celle de `main()`. N'ayant
corrigé que la seconde, Discord restait muet dans exactement le cas qui
justifie tout ce mécanisme : à 5h, aucun article neuf, mais douze en attente.
Push envoyait, Discord non.

Le test simule une nuit entière passage par passage et compte les envois sur
**les deux canaux** — c'est ce comptage qui a montré « 1 envoi » là où il en
fallait 2.

### Audit complet du 08/09/2026

Mené après une journée de gros changements — échelle de formes, cibles
tactiles, dialogues, filtres persistants, pause nocturne, alertes Rockstar.
Trois outils du dépôt au vert d'emblée (suite de tests, synchronisation des
sources, audit des données). Le navigateur et la relecture adverse ont trouvé
**deux vrais défauts**, tous deux dans du code écrit le jour même.

**1. L'alerte « actu majeure » de la nuit partait à la poubelle.** Si la nuit
n'apportait aucun article *neuf* mais qu'un sujet déjà connu devenait majeur —
repris par une quatrième rédaction — le robot déposait bien
`{articles:0, sommet:4}`, mais la garde des notificateurs ne regardait que le
nombre d'articles et se taisait. C'était précisément le cas pour lequel le
mécanisme `promus` avait été écrit, reproduit à l'identique sur le chemin
nocturne.

La règle « y a-t-il quelque chose à annoncer » vit désormais à **un seul
endroit** : le robot, qui ne dépose le fichier de totaux que dans ce cas. Les
notificateurs testent sa *présence*, jamais son contenu. Un test vérifie
qu'aucun des deux ne réinterprète les totaux.

**2. Quatre boutons à 39 px de large.** À 320 px, sur une carte **avec
vignette**, la rangée de commandes ne mesure que 180 px : les quatre boutons y
tombaient à 39 px de large (la hauteur, elle, était bonne). Invisible jusque-là
parce que les cartes *sans* vignette avaient 58 px par bouton — deux mesures
contradictoires qui décrivaient en réalité deux cartes différentes.

Il a fallu trois essais, et les deux premiers étaient pires que le défaut.

*Essai 1 — autoriser la rangée à passer à la ligne.* Chaque coche étend sa zone
de clic de 7 px au-dessus et en dessous, or l'écart entre rangées était de
8 px : les zones se recouvraient de 6 px et un appui dans cette bande partait
sur le mauvais bouton. **27 chevauchements mesurés.**

*Essai 2 — le même enroulement avec 16 px d'écart vertical.* Plus aucun
chevauchement, mais laid : le drapeau partait seul sur une deuxième rangée
étirée sur toute la largeur de la carte. Et seulement sur **8 cartes sur 30**,
**uniquement à 320 px** — dès 340 px l'enroulement ne se déclenche jamais.
Autrement dit, un défaut bien visible pour réparer un défaut que personne ne
voyait. C'est Antoni qui l'a arrêté, en demandant une capture d'écran avant de
croire la mesure.

*Essai 3, retenu — ne rien changer au dessin, élargir la zone.* La rangée
reste sur une seule ligne, le bouton fait toujours 39 px à l'œil, et son
`::after` déborde de **3 px à gauche et à droite** en plus des 7 px en haut et
en bas : 39 + 6 = 45 px de zone dans le cas le plus serré, 30 + 14 = 44 px en
hauteur. Pourquoi 3 et pas plus : deux boutons voisins sont séparés de 8 px,
donc 6 px consommés et 2 px de marge ; à 4 px les zones se toucheraient, à 5 px
elles se recouvriraient. Le test verrouille l'inégalité `écart > 2 × extension`
plutôt que les deux valeurs séparément.

Vérifié à 320, 360 et 390 px, en mode normal **et** dense : **0 cible sous
44 px, 0 chevauchement**, et le bouton visible mesurait toujours 39 px.

> **Périmé depuis le passage au bandeau** (voir *La vignette : quatre essais
> avant le bandeau*). La vignette ne partage plus la largeur avec la rangée de
> boutons en mode normal, donc le cas des 39 px n'existe plus : le plus petit
> bouton y mesure **58 px à 320 px** et 75 px à 390 px. L'extension latérale de
> 3 px reste en place mais n'y est plus déterminante. Elle l'est toujours en
> mode **compact**, où la vignette est restée à côté du texte : le bouton y
> tombe à 40 px, et 40 + 2×2 = 44. Le récit ci-dessus est conservé pour la
> leçon qu'il porte, pas pour ses chiffres.

La leçon : une mesure peut être juste et la correction quand même mauvaise.
« 36 cibles sous 44 px » était vrai ; ce que ce chiffre ne disait pas, c'est
que le défaut ne concernait qu'un huitième des cartes sur une largeur d'écran
que presque personne n'utilise, et que le remède se verrait, lui, tout le
temps.

**Ce qui a été mesuré et trouvé sain**, sur huit configurations (320 et
390 px × sombre et clair × normal et dense) :

| | résultat |
|---|---|
| contraste | 234 éléments mesurés, **0 sous le seuil** |
| cibles tactiles | 150 par configuration, **0 sous 44 px** |
| chevauchement de zones cliquables | **0** |
| débordement horizontal | **aucun** |
| erreurs JavaScript | **aucune** |
| dialogues | rôle, nom, focus piégé, Échap, fond figé : **5/5** |

Côté sécurité : aucun secret en clair, permissions minimales et explicites
dans les quatre workflows (`contents: read` partout sauf le robot), et le
fichier de totaux est écrit dans `$RUNNER_TEMP`, hors du dépôt.

**Une limite assumée, découverte au passage.** Le robot remet l'ardoise à zéro
*avant* que la notification soit confirmée : si la publication réussit mais que
Discord **et** le push échouent tous les deux, le récapitulatif est perdu. Les
articles, eux, sont publiés et visibles dans l'app — seule l'annonce manque.
Le cas existait déjà pour les articles d'un passage ordinaire ; la pause
nocturne en augmente seulement l'enjeu, puisqu'une nuit entière peut y passer.
Corriger demanderait une seconde écriture après notification réussie, donc un
commit de plus par passage : disproportionné pour une panne simultanée des deux
canaux.

### Une annonce de Rockstar réveille

La pause et « les actus Rockstar sont prioritaires » se contredisaient
précisément sur les cas qui comptent. Les dates le montrent sans appel — sur
les 34 articles officiels de l'historique, ramenés à l'heure de Paris :

```
  02h  █ 1          2023-12-05 02h24  la révélation de GTA 6
  03h  ██ 2         2026-02-16 03h48  Watch Trailer 1 Now
  05h  ██ 2         2026-08-28 03h00  An Extended Look
  09h  ████████████████ 16   ← le routinier du Newswire
  12h-15h ███████████ 11
  21h  ██ 2
```

**Les trois plus grosses annonces de l'histoire du jeu sont tombées entre 2h24
et 3h48.** Une pause qui les retiendrait jusqu'à 5h raterait exactement ce
pour quoi cette veille existe.

Pendant la pause, le robot tourne donc et publie, mais ne notifie **que** pour
un article marqué `official` — le Newswire de Rockstar, sa chaîne YouTube, les
relations investisseurs de Take-Two. Ce n'est pas une heuristique sur le
contenu : c'est l'émetteur, déclaré dans `FEEDS`.

**Le volume autorise une alerte par article.** Mesuré sur l'historique
complet : 34 articles officiels sur 2 183 (1,6 %), répartis sur **15 journées
en presque trois ans**, au pire 4 dans la même journée. C'est la deuxième
exception assumée à la règle « un seul message par passage », après les
alertes de source, et elle se justifie de la même façon.

Deux détails qui font la différence :

- **Le titre de l'article apparaît**, contrairement au récapitulatif. Celui-ci
  annonce un nombre parce qu'un lot de dix articles n'a pas de titre
  représentatif ; une annonce de Rockstar est un évènement unique, et c'est
  son contenu qu'on veut lire sans rien ouvrir.
- **Chaque alerte push a son propre `tag`**, dérivé du lien. Le récapitulatif
  utilise un tag commun qui *remplace* la notification précédente : sans tag
  propre, l'annonce d'un trailer serait effacée en silence par le
  récapitulatif du passage suivant, une demi-heure plus tard. Deux annonces du
  même passage ne s'écrasent pas non plus l'une l'autre.

Le récapitulatif continue de les **compter** (« dont 1 officiel Rockstar ») :
il annonce un volume, les alertes annoncent un contenu. L'amputer le ferait
mentir sur ses chiffres.

Le tri du fil, lui, n'a **pas** bougé : Rockstar a déjà son propre onglet.

**Le bandeau « robot en retard » a fait l'aller-retour.** Quand la pause
sautait les passages, l'écart atteignait légitimement 6h et le seuil de 4h
allumait le bandeau chaque nuit pour annoncer une panne inexistante : il avait
été porté à 7h. Depuis que le robot publie toute la nuit, `generated_at`
avance chaque heure sans interruption — il n'y a plus d'écart à tolérer, et le
seuil est **revenu à 4h**, ce qui rend une vraie panne visible trois heures
plus tôt.

`DEAD_SOURCE_HOURS` n'a pas bougé : il compte en **heures** et non en
passages, précisément pour être insensible à ce genre de changement de
cadence.

### Ce que la simulation a prouvé, et ce qu'elle a d'abord raté

Le garde a été rejoué **hors de GitHub**, en extrayant son script du workflow
et en lui faisant croire, via un faux `date`, qu'on était à un instant choisi.
Son verdict est comparé à la règle attendue, recalculée séparément :

| simulation | instants | divergences |
|---|---|---|
| une année entière, heure par heure, trois déclencheurs | 8 760 | 0 |
| un déclenchement **manuel** à chaque heure de l'année | 8 760 | 0 |
| minute par minute aux frontières 23h→01h et 04h→06h, été et hiver | 1 440 | 0 |
| les deux nuits de changement d'heure, minute par minute | 1 200 | 0 |

Et le décompte qui répond directement à la question posée — *est-ce qu'une
notification peut être bloquée en dehors de la plage ?* — sur les 5 840
passages automatiques d'une année :

```
  heures 00h-04h : 1 086 bloqués,     9 passés (fenêtre de sortie)
  heures 05h-23h :     0 bloqués, 4 754 passés
                       ^^^^^^^^^^
```

**Zéro.** Le total 1 086 + 4 754 = 5 840 recoupe le nombre de passages
automatiques, et la répartition inégale par heure s'explique : 210 jours en
CEST et 155 en CET envoient une même heure de Paris sur deux heures UTC
différentes.

**La première version de cette simulation était fausse et annonçait déjà
« 0 divergence ».** Le script de test lisait ses entrées ainsi :

```bash
while read -r instant declencheur; do   # découpe sur les ESPACES
# ligne lue : "2026-03-01 00:00 schedule"
#   instant     = "2026-03-01"      ← la date seule
#   declencheur = "00:00 schedule"  ← n'existe pas
```

Elle testait donc minuit en boucle avec un déclencheur inexistant, et n'a
jamais vu un seul déclenchement manuel. C'est l'histogramme par heure qui l'a
révélé : il rangeait 8 760 instants dans deux heures seulement, ce qui est
impossible.

**Un chiffre rassurant n'est pas une preuve.** C'est le résultat incohérent
posé à côté qui a montré que le rassurant ne valait rien. Les entrées sont
maintenant séparées par une barre verticale.

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
**toutes les heures** (c'était toutes les 30 min jusqu'au 02/09/2026, voir
plus haut). Il appelle 24 fois par jour, nuit comprise ; ce sont seulement
les *notifications* des cinq passages nocturnes que le workflow retient —
**rien n'a été modifié chez cron-job.org**, et c'est voulu : voir *Pause
nocturne*. La fiabilité a été vérifiée à l'époque de la demi-heure : sur la
nuit du 28 au 29 août, les 22 créneaux sont partis sans exception, à la
minute près. À comparer aux 12 créneaux consécutifs purement abandonnés par
le `schedule` de GitHub la veille.

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

Le `schedule` reste actif comme filet de sécurité, à **toutes les 3
heures** : si le planificateur externe tombe, GitHub prend le relais tant
bien que mal. Il était horaire jusqu'au 02/09/2026, mais comme il part à des
moments quelconques, ses passages s'intercalaient entre ceux de cron-job.org
et chacun envoyait sa propre notification. Les deux
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
5. Dans l'app : ⚙️ Paramètres → *Déclenchement à distance* → coller →
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

L'état des cinq derniers passages est visible dans la modale ℹ️. Le dépôt
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
- **Le panneau Paramètres en trois onglets.** Il faisait **2384 px**, soit
  2,8 écrans, dont **1075 px pour la seule liste des sources** : les réglages
  qu'on touche souvent étaient derrière ceux qu'on ne touche jamais, et
  « Appliquer » n'était atteignable qu'après avoir tout défilé. Découpé en
  **Affichage · Contenu · Avancé**, chaque onglet tient entre 362 et 878 px
  et les boutons restent visibles.

  **La barre réutilise la classe `.tab` du site**, pas une copie qui lui
  ressemble : le panneau suivra donc toute évolution des onglets d'articles
  sans qu'on y pense. C'est le point qui manquait — le panneau avait accumulé
  trois variantes de bouton à lui seul, et **dix attributs `style=` en ligne,
  plus que tout le reste du corps du document réuni**. Ils sont remplacés par
  trois classes. Le sélecteur de thème disait « sélectionné » en contour bleu
  quand les onglets le disent en aplat blanc sur accent : il adopte celui du
  site.

  La liste des sources défile **chez elle** (34 vh) au lieu d'allonger le
  panneau, avec filtre par nom, compteur et tout activer/désactiver. Une
  colonne sous 520 px : à 390 px, deux colonnes tronquaient « Rockstar Games
  (officiel EN) » en « (offi… », indistinguable de la version FR. Les
  explications passent derrière un « ? » — le texte reste dans le DOM, mais
  ne mange plus la moitié de l'écran une fois lu.

  **23 des 24 éléments du panneau sont pilotés par le JS**, et déplacer les
  blocs est exactement le geste qui en fait disparaître un en silence : un
  test vérifie que les 24 identifiants sont toujours présents, qu'aucun
  `getElementById` ne vise un élément disparu, qu'un seul onglet est visible
  au départ, que les boutons utilisent bien `.tab`, et qu'aucun style en
  ligne long n'est revenu.
- **L'état du dernier passage, dans l'app.** `feed.json` publiait déjà
  `generated_at`, `new_this_run`, `hot_count` et `sources_health` — l'app ne
  les regardait pas. Seule la durée manquait côté backend
  (`duration_seconds`). Une ligne sous les deux boutons dit maintenant
  `51 s · 5 nouveaux · 50/50 sources`. Placée **sous les boutons** :
  au-dessus, elle séparait le nombre d'articles des actions qui le
  modifient, alors que ce bloc existe pour les tenir ensemble. Masquée en
  mode de secours, où aucun robot ne tourne : y laisser l'état du dernier
  passage backend annoncerait un état qui n'a plus cours.

  **Une seule ligne, toujours ; une seconde seulement s'il y a un souci**
  (02/09/2026). La ligne du haut a une forme FIXE — durée, nouveaux,
  compteur de sources — dimensionnée pour tenir dans tous les cas. Tout ce
  qui peut s'allonger sans limite descend sur une deuxième ligne, qui
  n'existe pas du tout en régime normal : sources muettes, sources cassées
  avec leurs noms, sources en forte baisse, décodages Google News en échec.

  **Les noms des cassées sont plafonnés à cinq** (`CASSEES_NOMMEES`, depuis
  le 04/09/2026), au-delà elles se comptent : « … et 17 autres ». Le
  04/09 un incident a produit 22 cassées d'un coup et la deuxième ligne
  faisait alors **136 px**, soit huit lignes d'orange qui écrasaient la
  console — mesuré dans le navigateur. Nommer les premières garde ce qui
  sert (savoir LAQUELLE est tombée quand il y en a une ou deux) ; le détail
  complet reste dans `sources_health` et dans le journal du passage.

  Mesuré après correction, sur le cas réel du 04/09 : **136 px → 34 px**,
  deux lignes au lieu de huit, sur un écran de 390 px comme de 320 px.

  Deux morceaux ont dû maigrir pour que le haut tienne. « toutes les sources
  répondent » (28 caractères) est devenu le compteur `50/50 sources` (13),
  qui dit la même chose et continue de dire quelque chose quand il devient
  `46/50` — en orange à ce moment-là. Une source **tarie** répond
  parfaitement, elle n'a simplement rien publié depuis 30 jours : la compter
  en échec afficherait `46/50` en permanence pour un état sain. Et le
  préfixe « passage en » a disparu — 11 caractères, 66 px, c'est lui qui
  faisait déborder la ligne sur un écran de 320 px.

  Mesuré dans Chromium sur 320, 360, 375, 390, 412 et 430 px, avec le cas de
  production, un pire cas plausible et un cas extrême
  (`99 min 59 · 9999 nouveaux · 0/100 sources`) : hauteur de 17 px partout,
  soit une ligne exactement, marge la plus serrée +11 px. La ligne du haut
  est en `white-space:nowrap` — si un morceau s'allonge un jour, il débordera
  visiblement au lieu de repasser sournoisement sur deux lignes.

  **Les deux comptes viennent de `sources_health`, et de lui seul.** Une
  source y porte exactement UN statut, donc « muettes » et « cassées » ne
  peuvent pas se recouvrir. Les compter depuis `sources_silence` annonçait
  « 3 sources muettes · 3 cassées » pour trois sources en tout : **six
  problèmes affichés, trois réels**. Et `sources_silence` ne conviendrait
  plus de toute façon — il ne liste plus les sources muettes mais les
  CHRONOMÈTRES en cours, donc une source qui vient de répondre y reste tant
  que sa reprise n'est pas confirmée sur deux passages.
- **Confirmation avant les gestes sans retour.** Cinq actions n'avaient
  aucun garde-fou : « Tout marquer comme lu », « Oublier ce token »,
  « Réinitialiser les réglages », la régénération des clés VAPID et la
  désactivation du push. Un doigt qui ripe suffisait à effacer un token ou
  une paire de clés, sans annulation possible. Elles passent maintenant par
  `demandeConfirmation()`, qui renvoie une promesse : le focus part sur
  **Annuler**, Échap et un clic sur le fond répondent tous les deux non, et
  le bouton de validation est **souligné en rouge, pas rempli** — une
  confirmation ne doit pas se cliquer par réflexe.

  **La coche ✓ des cartes en est exemptée, volontairement.** C'est le geste
  le plus fréquent de l'app, il est réversible d'un second clic, et le faire
  passer par une fenêtre le rendrait insupportable. Un test verrouille cette
  exemption pour qu'on ne l'« harmonise » pas par distraction.

- **Un seul endroit décide ce qui est affiché.** `articlesAffiches()`
  applique dans l'ordre l'onglet, la langue, le filtre lu/nouveau, la
  recherche, puis le plafond d'affichage — et tout ce qui compte des
  articles part de là. La confirmation de « Tout marquer comme lu » annonçait
  auparavant **tous** les articles affichés au lieu des seuls non lus : sur
  l'onglet RockstarMag avec 29 non lus sur 247, elle proposait de marquer les
  247. Elle ne cible plus que les articles dont l'état change réellement, et
  annonce les deux nombres (« 29 non lus parmi les 247 affichés »).

- **Images différées et repères de jour collants.** Les vignettes et favicons
  portent `loading="lazy" decoding="async"` — le navigateur ne télécharge que
  ce qui approche de l'écran. Les libellés de jour restent collés en haut
  pendant le défilement (`position:sticky`), pour qu'on sache toujours de
  quelle journée on lit les articles. Vérifié dans un vrai navigateur, pas
  seulement dans le balisage : la première version du test passait sur une
  étiquette qui avait déjà quitté l'écran par le haut.

- **Recharger l'app sans la tuer.** Le service worker sert bien le squelette
  en réseau-d'abord, mais il n'intercepte que les requêtes de **navigation**
  — et une PWA installée n'en fait plus aucune après son lancement.
  « Actualiser » et le tirer-pour-rafraîchir vont chercher `feed.json`,
  jamais le HTML. Le
  code de l'app servi restait donc celui du démarrage jusqu'à ce qu'on tue
  l'app et qu'on la relance.

  Une requête **HEAD** sur `index.html` (quelques octets, pas les ~150 Ko du
  fichier) relève l'`ETag` au démarrage, puis le compare à chaque
  vérification — au plus une fois par minute, greffée sur « Actualiser ».
  S'il a changé, un bandeau « Nouvelle version disponible » propose de
  recharger ; le bouton existe aussi en permanence dans les Paramètres, pour
  forcer. `location.reload()` **est** une navigation : le service worker la
  voit passer et va chercher le HTML sur le réseau.

  L'`ETag` de GitHub Pages change à chaque déploiement : pas de numéro de
  version à incrémenter à la main, donc pas d'oubli possible. Repli sur
  `Last-Modified` puis `Content-Length` ; si le serveur n'envoie aucun des
  trois, ou si la requête échoue (hors-ligne), la détection reste
  **silencieuse** plutôt que de signaler à tort. Un rechargement ne coûte
  rien : réglages, articles, lu/non-lu et position de lecture vivent dans
  `localStorage`.

  Limite connue : GitHub Pages sert via un CDN dont le cache tient quelques
  minutes. Le `no-store` contourne le cache du navigateur, pas celui de
  GitHub — un rechargement lancé juste après un déploiement peut encore
  servir l'ancienne version. Le bandeau aide justement là : il n'apparaît
  qu'une fois le CDN réellement basculé.

## Diagnostiquer, sans réécrire l'outil à chaque fois

Deux outils sont nés d'une panne, ont servi une fois et ont été jetés — puis
il a fallu les regretter. Ils vivent désormais **dans** le projet, à
l'endroit qui les empêche de diverger ou de disparaître.

- **`python fetch_feeds.py --sonde <url>`** — dit ce qu'une URL renvoie
  réellement, sans rien écrire. C'est un **mode du robot**, pas un script à
  côté : elle emprunte `collect_feed_items`, donc le même agent utilisateur,
  le même délai maximal, le même filtre, les mêmes verdicts. Elle ne peut
  pas annoncer autre chose que ce que le robot verra au prochain passage.

  Cinq verdicts, là où « 0 entrée » ne disait rien : **OK**, **VIDE** (flux
  valide sans article), **PAS UN FLUX** (page de blocage, ou URL qui ne sert
  plus de RSS), **INJOIGNABLE** (aucune réponse HTTP), **INCHANGÉ** (304).

  **Et elle dit vers où le flux a déménagé.** feedparser suit les
  redirections et expose l'adresse finale : quand elle diffère de l'URL
  demandée, c'est exactement ce qu'il faut recopier dans `FEEDS`. Sans cette
  ligne, une redirection vers une page d'accueil ne produit qu'un code 301
  et « 0 entrée » — on sait que ça a bougé, pas où. C'est arrivé le
  30/08/2026 sur IGN (302) et Kotaku (301), et il a fallu un passage de plus
  pour l'apprendre.

  La distinction INJOIGNABLE / PAS UN FLUX n'est pas cosmétique :
  `feedparser` range une panne réseau au même endroit qu'un XML mal formé.
  Les confondre revient à accuser l'URL quand c'est le réseau qui n'a pas
  répondu.
- **`python audit_donnees.py`** — voir la liste des scripts plus haut.

**Détecter la dégradation, pas seulement la mort.** Une source qui passe de
30 entrées à 3 reste « ok » : elle répond, elle renvoie quelque chose. Elle a
pourtant perdu 90 % de sa couverture, et rien ne le signalait. Le volume des
**12 derniers passages** est donc conservé par source
(`sources_entries_history`, en chaîne compacte `"20,20,18"` — une liste JSON
mettrait une ligne par valeur dans un fichier committé à chaque passage). Une source est dite en baisse quand ses **3 derniers passages**
tombent sous **35 %** de sa médiane habituelle, et seulement si cette
médiane atteint **8 entrées** : sans ce plancher, une petite source qui
varie normalement déclencherait à tout bout de champ. Les réponses 304 ne
sont pas empilées — elles ne disent rien du volume, et les compter comme des
zéros ferait chuter la référence de toutes les sources bien élevées.

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
- **Poids de `feed.json` à terme** — 1 526 Ko aujourd'hui pour 1 895
  articles ; au plafond de 20 000 il approcherait 15 Mo (~4 Mo compressés).
  L'ouverture de l'app n'est pas concernée (elle charge `feed-recent.json`),
  mais toute recherche déclenche le téléchargement de l'historique complet.
  Côté dépôt en revanche il n'y a pas de problème : Git ne stocke que les
  lignes changées (~30 à 90 lignes par passage), et l'ensemble du dépôt
  tient dans **4,5 Mo compactés pour 540 commits** (mesuré le 04/09/2026).
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
  Contrepartie : Google News passe de 6 à 23 flux interrogés toutes les 60
  minutes — 20 aujourd'hui, après le retrait de Millenium, XboxEra et
  Xbox-Mag, soit 40 % des 50 sources sur un seul fournisseur — la plus
  grosse dépendance qui reste, désormais loin devant toutes les autres.
  Le plafond de 3 files simultanées par domaine tient (aucune requête
  parallèle supplémentaire vers Google), mais un éventuel rate-limit se
  verrait sur ces sources en premier.
- **rss.app : neuf sources dessus, épuisé d'un coup.** RockstarMag,
  RockstarINTEL, IGN, GameSpot, Polygon, Kotaku, GamesRadar+, Rock Paper
  Shotgun et Eurogamer y passaient — **18 % des sources sur un seul
  fournisseur gratuit**. Le 29/08/2026 vers 13h00 UTC, les neuf se sont
  tues simultanément : d'abord des flux valides mais vides, puis un franc
  **HTTP 402 Payment Required**. Quota épuisé, pas une panne : rien ne
  serait revenu.

  Les neuf sont passées aux **flux natifs des sites**, chaque URL vérifiée
  au préalable depuis un runner GitHub (une sonde jetable, 26 candidates
  testées, supprimée une fois le choix fait) plutôt que devinée. Deux pièges que ce sondage a évités :
  RockstarINTEL doit rester **sans `www`** (les variantes `www.` échouent
  au handshake TLS côté serveur), et GameSpot rapporte deux fois plus sur
  `/feeds/news/` que sur `/feeds/game-news/`. Aucun doublon à la bascule :
  rss.app relayait les liens d'origine des articles, donc l'index par lien
  a reconnu l'existant. Les 50 sources se répartissent maintenant sur
  **30 domaines distincts au lieu de 20**.

  La leçon vaut au-delà de rss.app : `feedparser` ne lève pas d'exception
  sur une panne réseau (il la range dans `bozo`, au même endroit qu'un XML
  mal formé) et avale une page HTML sans protester — `bozo` reste faux et
  la liste d'entrées est vide, **exactement comme un flux valide mais
  vide**. Seul le champ `version` les sépare. C'est cette distinction
  qu'une sonde doit faire, et le premier réflexe si une source se tait à
  nouveau : la refabriquer plutôt que deviner des URL de remplacement.
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

  **Le remplacement n'a rien changé, et c'est l'information utile.** Au
  passage n°207 du 29/08/2026 : 100 entrées récupérées, **zéro retenue**,
  et toujours aucun article vg247.com dans l'historique — 764 jours depuis
  le dernier. Le diagnostic « le flux marche, le site ne publie pas sur
  GTA 6 » se confirme donc sur deux formats de flux différents. La source
  reste en place : elle ne coûte qu'une requête, et le jour où VG247
  publiera, elle remontera.
- **Miniatures Google News** — un léger pourcentage d'articles n'a pas de
  miniature si le site source bloque les robots ou n'a pas de balise
  exploitable. Comportement normal, pas un bug.
- **Fichier HTML monolithique** — `index.html` regroupe CSS, HTML et JS
  dans un seul fichier de ~3900 lignes plutôt que d'être séparé en
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
- **Dépendances** : `requirements.txt`, épinglées à la version exacte. Les
  quatre workflows installent depuis ce fichier — `weekly-digest.yml` faisait
  `pip install requests` tout court jusqu'au 04/09/2026, donc le
  récapitulatif hebdomadaire tournait sur une version que rien n'avait
  testée. Un test interdit désormais qu'un workflow réinstalle sans passer
  par `requirements.txt`.
- **Taille max de l'historique** : `MAX_HISTORY_SIZE` dans `feed_store.py`
  (partagé par le robot et l'outil de fusion, pour que les deux appliquent
  exactement la même règle). Les articles marqués `official` y échappent,
  voir `cap_items`
- **Déduplication** : `SIMILARITY_THRESHOLD` (le seuil de 0,75),
  `FENETRE_HEURES`, `TITLE_SIMILARITY_WINDOW` (plancher) et `FENETRE_MAX`
  (plafond), en tête de `fetch_feeds.py`. Toucher au seuil se vérifie en
  rejouant l'historique, pas en raisonnant : c'est la mesure qui a montré
  qu'un titre réduit au seul nom du jeu se comportait en aimant
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
