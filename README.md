# GTA6_WATCH

Veille automatisée de l'actualité GTA 6 : un robot interroge 60 sources en
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
2. **Récupère les 60 sources** (liste `FEEDS`) **en parallèle**, avec
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
10. **Plafonne l'historique à une PROFONDEUR** de 15 jours
   (`MAX_HISTORY_DAYS`), bornée entre 1 500 et 4 000 articles — au-delà, les
   plus anciens sont retirés. Ce n'est donc pas un historique complet et
   permanent, mais un historique glissant.

   **Une profondeur et non un nombre, depuis le 22/09/2026.** 1 500 valait
   dix-sept jours à 74 articles/jour — le régime ordinaire — mais seulement
   **six** à 249/jour, celui d'une journée d'annonce. Le jour de la sortie du
   jeu, un nombre fixe n'aurait plus tenu qu'un jour ou deux. Ce que le
   fichier sert, c'est la **recherche** : ce qui compte est de remonter
   toujours aussi loin, pas de garder toujours autant d'articles.

   Les deux bornes ne sont pas décoratives. Sans plancher, une semaine creuse
   réduirait l'historique à peau de chagrin ; sans plafond dur, quinze jours à
   1 000 articles/jour feraient 15 000 articles et une douzaine de Mo — le
   problème qu'on venait justement de régler. Le plafond de 4 000 (~3,4 Mo)
   est un arbitrage sur la recherche : une dizaine de secondes de
   téléchargement sur un téléphone, la première fois qu'on cherche.

   | régime | visé | retenu | profondeur |
   |---|---|---|---|
   | 74/jour (ordinaire) | 1 110 | **1 500** (plancher) | ~20 j |
   | 249/jour (journée chargée) | 3 735 | **3 735** | 15 j |
   | 1 000/jour (sortie) | 15 000 | **4 000** (plafond) | ~3 j |

   Les articles protégés ne comptent pas dans la fenêtre : les inclure la
   ferait rétrécir à mesure qu'ils s'accumulent. Une date illisible non plus —
   elle est par construction plus vieille que tout, et la laisser peser
   réduirait la profondeur à cause d'un seul article mal daté.

   **Sauf trois familles, qui ne se retirent jamais** — les publications de
   Rockstar (depuis le 02/09/2026), celles de RockstarMag et les actualités
   majeures (depuis le 18/09/2026). Même raison pour les trois : on ne les
   retrouve pas ailleurs. La reprise d'un site d'actu existe sur dix sites,
   le billet officiel non ; RockstarMag est suivi pour lui-même ; et trois
   rédactions sur un même sujet, c'est un évènement. Les officiels sont
   aussi les plus anciens du fil — l'annonce, le premier trailer — donc
   exactement ceux qu'une troncature par la fin emporte en premier.

   Elles pèsent **128 articles sur 3 202** au 18/09/2026, soit 4 % : la
   protection ne coûte presque rien en place.

   Le plafond reste un vrai plafond : ce qui est épargné à un protégé est
   pris sur un article ordinaire plus ancien. Un seul cas le dépasse — pas
   assez d'articles ordinaires à retirer — et la liste reste alors plus
   longue que 1 500 plutôt que de jeter ce qu'on a promis de garder ; le cas
   est testé.

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
du robot le 28/08, zéro exécution de contrôle. Le `paths-ignore` est une
ceinture en plus de ces bretelles, et il liste bien les **deux** fichiers que
le robot pousse (`docs/feed.json` et `docs/feed-recent.json`) : GitHub ne
saute un push que si TOUS les chemins modifiés y figurent, si bien que n'en
lister qu'un rendait le filtre inopérant. C'était le cas jusqu'au 16/09. Ce
paragraphe a lui-même décrit le trou pendant six jours après sa réparation —
un rappel que la documentation d'un défaut doit mourir avec lui.

`test_pipeline.py` n'a besoin ni de réseau ni de dépendance : la
récupération est injectable (paramètre `collecte` de `fetch_all_feeds`), ce
qui permet de tester tout le pipeline sans sortir de la machine. **1538
vérifications** couvrant les dates (les trois formats présents dans
l'historique, et le refus de l'époque Unix), le tri, le plafonnement
adaptatif, le plancher de rétention et les familles qu'il épargne,
l'archive mensuelle et ses tranches, le fait que tout ce que le robot
écrit soit bien commité ET ignoré par la CI, la repasse rétroactive, le
nettoyage des liens, le cache de décodage, la validation du champ VAPID
`sub` contre le vrai validateur de `py_vapid`, le masquage des URL dans les
messages d'erreur, l'équivalence entre récupération séquentielle et
parallèle, le plafond de requêtes par domaine, la déduplication à
l'intérieur d'un même passage, la promotion d'un sujet entre deux passages,
le suivi des sources muettes, le garde-fou contre les archives, l'unicité
des identifiants de source — et surtout la
fusion, c'est elle qui décide si des articles sont perdus quand deux
exécutions se chevauchent. Le dernier bloc rejoue ces règles sur le vrai
`docs/feed.json` du dépôt.

`check_sources_sync.py` compare `FEEDS` et les 35 mots-clés de
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
(instantané), l'autre fait travailler le robot sur les 60 sources
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

**Une faiblesse relevée au passage le 10/09, comblée le 15.** Les deux
*courbes* étaient verrouillées par un test, les quatre *rayons* et les deux
*durées* ne l'étaient pas : leur conformité avait été vérifiée à la main, rien
n'empêchait une dérive ensuite. On pouvait passer `--r-md` de 12 à 10 px, ou
`--t-moyen` de 250 à 300 ms, suite verte.

`test_echelle_m3_verrouillee` comble le trou en **seize vérifications**, et
l'essentiel n'est pas là où on l'attend :

- les six jetons valent exactement leur cran M3 (4/8/12/9999 px,
  150/250 ms). Six vérifications qui ne font que **nommer la référence** —
  elles ne coûtent rien et n'attrapent qu'une modification directe ;
- **aucun rayon de la feuille n'échappe à l'échelle** : les 47 `border-radius`
  passent tous par `var(--r-*)`, à deux exceptions près et deux seulement,
  `50%` pour ce qui est réellement un disque et `0` pour une remise à plat
  explicite. C'est **ce** contrôle qui a du mordant : sans lui on aurait six
  jetons parfaitement conformes et un cinquième rayon sauvage écrit en dur
  dans une règle ajoutée trois mois plus tard ;
- même chose pour les 14 transitions, dont aucune ne porte de durée en dur ;
- chaque jeton doit **servir** quelque part. Un jeton déclaré et jamais employé
  donne l'illusion d'une échelle tenue alors que la feuille s'en passe ;
- le long commentaire qui surplombe l'échelle cite les valeurs en toutes
  lettres ; le test vérifie qu'il dit bien ce que le code fait. Un commentaire
  qui dérive de son code est pire que pas de commentaire — celui-là sert de
  référence quand on se demande d'où sort un 12.

Les trois contrôles qui comptent ont été éprouvés en les cassant : rayon dévié
à 10 px, `border-radius:6px` ajouté, `transition:opacity 300ms linear` ajoutée
— chacun fait tomber sa vérification, et elle nomme la valeur fautive.

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

### Le panneau Paramètres, relu contre Material 3 et les lois de l'UX — 15/09/2026

Relecture du panneau, ligne à ligne, avec les deux références en main.
Dix-sept remarques, dont **six défauts réels** — le premier étant que le
bouton principal du panneau ne faisait pas ce que son nom promettait.

**« Appliquer » n'appliquait rien.** `saveSettings()` écrivait dans
`localStorage` puis appelait `updateSourcesLine()` et `updateFooterNote()`.
Jamais `applyFilters()`. Or c'est `applyFilters()` qui redessine le fil, et
qui lit le plafond d'affichage, les mots-clés d'exclusion et la liste des
sources actives. Couper une source, presser Appliquer : rien ne bougeait à
l'écran. Le réglage était pourtant bien enregistré — il ne se voyait qu'au
rafraîchissement suivant. Un bouton qui enregistre sans appliquer, sous un
libellé qui dit l'inverse, est plus trompeur qu'un bouton absent.

**Quatre réglages, trois comportements.** Le thème, le plafond et chaque
bascule de source s'enregistraient à la volée. Le curseur anti-doublon, lui,
n'était commité par rien : `oninput` ne mettait à jour que son étiquette. Il
partait donc dans la sauvegarde de l'action *suivante*, quelle qu'elle soit.
Concrètement : bouger le curseur puis « Fermer » le perdait ; bouger le
curseur puis basculer une source l'enregistrait sans qu'on l'ait demandé.
Deux sorties du panneau, deux résultats, aucun indice pour les distinguer.

Le panneau applique désormais **tout, immédiatement**, par une seule
fonction, `appliqueReglages()`, qui enregistre puis redessine. « Appliquer »
a disparu avec sa fonction, et le panneau écrit noir sur blanc que les
changements sont immédiats — il ne reste en bas qu'une action rare et
destructive, « Réinitialiser », cernée de rouge et seule.

**Le champ annonçait `max="1000"` et acceptait 20000.** Sur un
`<input type="number"` hors formulaire, `max` ne bloque rien : il n'est
consulté qu'à la validation d'un `<form>`, et il n'y en a pas. Pire, la
lecture était `parseInt(...) || 500` : « 0 » retombait à 500 parce que zéro
est faux, « abc » aussi parce que `NaN` l'est également. Trois corrections
muettes pour un champ qui prétendait avoir des bornes.

Les bornes sont maintenant deux constantes, `MIN_DISPLAY` et `MAX_DISPLAY`,
**lues par le code et annoncées par le balisage** — un test vérifie que les
deux disent la même chose, puisque c'est leur divergence qui avait produit le
défaut. Le plafond est monté à 20000 plutôt qu'appliqué à 1000 : couper à
1000 aurait retiré des articles réellement présents. Et chaque correction est
dite, avec le nombre d'articles disponibles en regard, pour que « 20000 » ait
un sens.

**Les bascules de source faisaient 19 px de haut.** `.switch` mesurait
32 × 19 px, et le `<label>` n'enveloppait que l'interrupteur : le nom de la
source, juste à côté, ne répondait pas. WCAG 2.5.8 demande 24 px, Material 3
en demande 48. C'était le contrôle **le plus nombreux du panneau** —
cinquante-neuf exemplaires — et le seul que la passe « 44 px par défaut »
avait oublié, alors que son propre commentaire revendiquait d'avoir rattrapé
tout l'intérieur des panneaux.

La ligne entière est devenue le `<label>`, et l'interrupteur un `<span>` —
un `<label>` dans un `<label>` étant invalide, c'est bien l'un ou l'autre.
La cible fait maintenant toute la largeur sur 44 px de haut.

**Le « ? » dérogeait aux 44 px sur un motif inexistant.** La règle
d'exemption expliquait que ces boutons « étendent déjà leur zone de clic par
un pseudo-élément ». Vérification faite, `.aide` n'en avait aucun : dix-huit
pixels de côté, dérogation accordée sur une phrase. Le commentaire s'était
périmé sans que personne le relise. Le bouton fait désormais 24 px visibles
et 44 px cliquables, et **un test lit le CSS pour exiger, de chaque
sélecteur de la liste d'exemption, le mécanisme que la phrase revendique** —
pseudo-élément avec débordement, ou position absolue. Une phrase ne se
vérifie pas ; un sélecteur, si.

**Aucun champ n'avait de nom.** `grep -c 'label for='` sur tout le fichier
renvoyait zéro. `<label>Articles affichés max</label>` n'était lié à rien :
le toucher ne donnait pas le focus au champ, et un lecteur d'écran annonçait
« champ numérique » sans plus. Trois autres champs n'avaient qu'un
`placeholder`, qui n'est pas une étiquette — il disparaît à la première
frappe. Et `aria-selected`, `aria-pressed`, `role="tab"` : zéro occurrence
dans le fichier. Les onglets du panneau et les trois boutons de thème
disaient leur sélection **uniquement par la couleur de fond**.

Les champs portent maintenant un nom, les boutons bascules un `aria-pressed`
tenu à jour par le JS. `aria-pressed` et non `role="tab"` : de vrais onglets
ARIA exigent la navigation aux flèches et le retrait des boutons du parcours
de tabulation. Trois boutons dont un seul est enfoncé disent la même chose et
se comportent comme ce qu'ils sont.

**Deux défilements imbriqués.** La liste des sources défilait chez elle
(`max-height:34vh`) à l'intérieur d'un panneau qui défilait déjà — le piège
classique au doigt. Son motif était de garder les boutons du bas
atteignables. L'entête est devenue collante (titre + onglets), ce qui rend le
prétexte caduc : la liste ne défile plus dans son coin, et elle est passée
**en dernier dans son onglet** pour que les deux zones de mots-clés ne soient
plus enterrées derrière cinquante-neuf lignes.

**L'entête collante a d'abord recouvert le contenu.** Premier jet : un
`margin-top` négatif pour manger le rembourrage du panneau. Un élément
`sticky` ne peut pas remonter au-dessus du bloc qui le contient — il se
faisait repousser vingt pixels plus bas que sa place tandis que le contenu,
lui, restait disposé comme si l'entête était remontée. Résultat : quatre
pixels de chaque onglet passaient sous la barre, et le haut des lettres de
« Notifications » et de « Mots-clés » était coupé. Visible seulement sur
capture d'écran, invisible à la lecture du CSS. Le rembourrage du haut est
maintenant confié à l'entête plutôt que repris deux fois.

**Le reste, plus petit mais réel.** Un onglet « Affichage » qui contenait un
groupe « Affichage » — le même mot à deux niveaux de hiérarchie. « Avancé »
empilait six sujets sans rapport, désormais découpés en trois intertitres
(Notifications, Connexion GitHub, Entretien) plutôt qu'en un quatrième
onglet que la largeur d'un téléphone ne permet pas. « Tout désactiver »
effaçait cinquante-neuf bascules sans confirmation ni retour possible, alors
que « Réinitialiser », juste à côté, en demandait une. « Oublier ce token »
côtoyait « Jeton enregistré » à vingt pixels d'écart. Les actions
destructives se déguisaient en boutons ordinaires. Et le « ? » du groupe des
notifications ouvrait une explication **enfermée dans un bloc lui-même
masqué** : il ne révélait rien.

**Deux défauts trouvés par les captures, pas par les tests.** Le compteur de
mots-clés affichait « 42 mot-clés » et « aucun exclusion » : un pluriel
français ne se fabrique pas en collant un « s » au dernier mot, et « aucun »
a un genre. Les trois formes sont passées en toutes lettres, données par
l'appelant. C'est aussi une capture qui a montré l'entête recouvrant le
contenu. Toute la suite était verte dans les deux cas.

**Verrouillé par 72 nouvelles vérifications** réparties en cinq tests, plus
un contrôle de bout en bout dans un vrai Chromium à 390 px : l'entête reste
en place après 1200 px de défilement, cliquer le nom d'une source la bascule,
un mot-clé d'exclusion retire vraiment des articles du fil, une valeur hors
bornes est ramenée **et annoncée**, la zone de clic du « ? » atteint 44 px,
exactement un onglet est annoncé enfoncé.

**Le libellé remontait en haut du bouton — sur deux boutons seulement.**
Signalé sur capture : « les boutons aussi ça va pas, les textes sont trop
collés au bouton ». Mesure faite sur chaque bouton du panneau, deux défauts
distincts se cachaient derrière la remarque.

Le premier est un vrai bug, et c'est **le même que celui déjà corrigé une
fois dans ce fichier**. `refreshPushUI()` montrait ses boutons par
`style.display = "inline-flex"`. Un `display` posé en style inline l'emporte
sur la feuille de style et fait du bouton un **conteneur flex** : son unique
enfant, le texte, cesse d'être centré et remonte en haut de la boîte. Avec
`min-height:44px`, cela fait 8,5 px de décalage. Le commentaire de
`refreshTokenUI()` décrit exactement ce piège et explique pourquoi il utilise
la chaîne vide — le correctif n'avait simplement jamais été reporté sur les
deux boutons du groupe push. Ils s'affichent désormais par `""`, et la règle
`button` déclare `align-items:center` en filet, sans effet sur un bouton
ordinaire mais salvateur le jour où quelqu'un repose un `display` de ce type.

Le second est de l'esthétique mesurable. `button.small` n'avait que **11 px
de marge latérale** autour de son texte, la plus étroite du panneau, quand un
bouton ordinaire en a 15 et un onglet de 18 à 29. Le bouton « Fermer », lui,
en avait 9. Tous à 15 px désormais.

Élargir les boutons a cassé le retour à la ligne : « Tester un envoi réel »
passait de 159 à 167 px, la rangée débordait de 10 px et le bouton partait à
la ligne avec « Désactiver ». Réglé par le libellé plutôt que par de la
gymnastique CSS — **« Tester l'envoi »**, qui tient en 126 px et qui est de
surcroît parallèle à son voisin « Tester l'affichage », ce que l'ancien
n'était pas. Les deux groupes d'actions du panneau ont maintenant la même
forme : les actions inoffensives sur la première rangée, la destructive seule
en dessous.

**Trois demandes, trois mesures.** « Chaque groupe de boutons doit être sur
la même ligne », « les textes doivent être centrés parfaitement », « il n'y a
pas d'espace entre les boutons et les textes » — avec, en appui, une capture
entourant au feutre bleu les deux endroits fautifs.

Le troisième point était le plus net : `.token-status` n'avait **aucune
marge**. La ligne de bilan se posait au ras du bouton au-dessus, et comme ce
bouton est rouge dans les deux cas — « Désactiver », « Oublier » — elle
paraissait lui appartenir. 10 px désormais, mesurés à 10 px dans les deux
groupes.

Le premier a demandé un arbitrage. Des boutons dimensionnés par leur texte ne
tiennent pas à trois sur 316 px : la rangée débordait, et pas au même endroit
selon le groupe — « Désactiver » seul en dessous ici, « Oublier ce jeton »
seul là. Deux mises en page pour deux groupes de trois boutons.
`flex:1 1 0` les met à largeur égale et remplit la rangée ; l'air autour du
libellé vient alors de l'étirement et non du rembourrage, qui n'est plus
qu'un plancher. Restait que les libellés longs ne rentraient pas dans un
tiers de rangée : « Tester l'affichage » et « Tester un envoi réel »
deviennent **« Aperçu »** et **« Envoi réel »**. Le raccourci dit d'ailleurs
mieux la différence que les anciens, qui commençaient tous deux par
« Tester » — l'un ne fait que MONTRER une notification fabriquée sur place,
l'autre en fait PARTIR une vraie.

**Une limite, assumée et chiffrée.** Sous 380 px, trois boutons ne tiennent
plus côte à côte sans qu'un libellé sorte de sa boîte : « Enregistrer »
mesure 75 px et il lui faut 97 px de boîte, soit 377 px de fenêtre pour
trois. Le retour à la ligne reprend donc ses droits sous ce seuil. Il est
calculé, pas choisi, et un test vérifie qu'il existe — mieux vaut une rangée
de plus qu'un texte qui déborde.

Le deuxième point, enfin, était déjà acquis sans qu'on puisse le voir :
l'écart entre le centre du texte et le centre de la boîte mesure **0,01 px**
horizontalement et **0,00 px** verticalement. Le contrôle navigateur, qui
tolérait 1,5 px, est descendu à 0,1 px.

**Une fausse alerte, dite comme telle.** La même capture laissait croire que
« Tester l'affichage » et « Tester un envoi réel » n'étaient pas alignés.
Mesure : `h=44` pour les trois boutons, même `y` pour les deux premiers.
Artefact de rendu du ×2, pas un défaut — et il valait mieux le mesurer que le
corriger.

**Une réserve de méthode.** `m3.material.io` et `lawsofux.com` sont tous deux
bloqués par le proxy de sortie de l'environnement où cette relecture a été
faite. Les principes ont été appliqués de mémoire et recoupés sur des sources
secondaires, pas relus à la source. Les chiffres cités — 24 px pour WCAG
2.5.8, 48 dp pour Material 3 — méritent d'être revérifiés sur les pages
elles-mêmes. Les défauts, eux, ont tous été constatés dans le code et dans le
navigateur.

## Notifications push natives

Discord fonctionne, mais taper une notification Discord ouvre Discord,
jamais l'article. Une notification push native ouvre directement le site.
Les deux coexistent : chacune s'active par la présence de ses secrets, et
se désactive par leur absence.

**Les deux annoncent mot pour mot la même chose**, et ne peuvent pas
diverger : le texte est écrit une seule fois dans
`feed_store.libelle_recap()`, appelé par les deux canaux.

### L'icône : « VI WATCH », dans la continuité du badge

Une fois le badge corrigé, l'icône de l'app disait encore « GTA 6 » là où la
notification affichait « VI ». Refaite le 12/09/2026 pour reprendre le même
glyphe : un **VI dominant, « WATCH » en capitales grasses dessous**.

**Quatre fichiers, pas un**, et ils ne sont pas identiques : `icon-192`,
`icon-512` et leurs deux variantes `-maskable`. La distinction n'est pas
cosmétique — Android recadre les maskable en cercle, en squircle ou en carré
arrondi selon le lanceur, et ne garantit que le **disque central de 80 % du
côté**. Tout ce qui dépasse peut être rogné. Les deux maskable sont donc les
mêmes que les autres, réduites à **95 %** : le contenu tombe à 75,8 px du
centre pour 76,8 de zone sûre en 192, et 201,8 pour 204,8 en 512.

**L'erreur qui a coûté une version : juger une icône à la mauvaise taille.**
La version précédente mettait « WATCH » dans un bandeau bleu plein allant d'un
bord à l'autre. Sur les planches de comparaison, affichées à 192 px, c'était
franchement la plus belle. Sur l'écran d'accueil d'un vrai téléphone, où la
tuile fait environ **56 px**, le bandeau n'était plus qu'une barre bleue
illisible, tronquée en biais par l'arrondi. Antoni l'a vue avant moi, en
photo : « Ça va pas ». Toutes les propositions suivantes ont été rendues à
56 px d'abord, et à 192 seulement ensuite.

**Ce que le bandeau imposait, et qui a disparu avec lui.** Un élément à bord
perdu est par construction incompatible avec une zone sûre : le bandeau étant
en bas, celle-ci n'y mesurait plus qu'une vingtaine de pixels de large. Le
test devait alors se rabattre sur le masque circulaire réellement appliqué
(rayon 50 %), plus large que la garantie d'Android — un compromis assumé, mais
un compromis. Le dessin actuel n'a plus rien à bord perdu, donc plus rien à
concéder : **tout** le contenu tient dans la zone sûre conservatrice, et le
test l'exige littéralement.

**Dix-huit vérifications.** Chaque icône déclarée existe et a la taille
annoncée. Sur les maskable, tout le contenu tient dans la zone sûre — et
comme une icône vide passerait ce test-là sans effort, un second contrôle
compte les pixels des deux encres pour s'assurer que le VI et le WATCH sont
bien dessinés. Sur les deux autres, qui ne sont jamais recadrées, c'est la
contrainte inverse : le dessin doit **occuper** la tuile (rayon ≥ 35 % du
côté), sinon l'app a l'air perdue au milieu de son fond.

Les quatre sont enfin vérifiées **opaques**. C'est l'exacte réciproque du test
du badge, qui lui exige de la transparence : une icône d'app transparente
laisserait voir le fond du lanceur, un badge opaque redonne un carré blanc.
Les confondre est facile, d'où deux tests qui se contredisent volontairement.

**Android fige l'icône d'une PWA à l'installation.** Remplacer les fichiers ne
change rien à la tuile déjà posée sur l'écran d'accueil : il faut désinstaller
l'app et la réinstaller. Le badge de notification, lui, se met à jour tout
seul au prochain envoi.


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

C'est typiquement le défaut qu'aucun audit du site ne pouvait trouver : il ne
se voit ni dans le navigateur, ni dans le HTML, seulement sur un vrai
téléphone Android qui reçoit une vraie notification.

**Et il a survécu trois jours à son propre correctif.** Signalé de nouveau le
15/09 : toujours un carré blanc. Le service worker était pourtant juste, le
fichier aussi, et les sept vérifications étaient vertes.

Parce qu'il y a **deux** endroits qui affichent une notification, pas un :
`sw.js` pour les vraies, et `testPushDisplay()` dans `index.html` pour le
bouton « tester l'affichage » des réglages. Seul le premier avait été corrigé.
Le second est resté sur `badge: "icon-192.png"` — et c'est exactement celui
qu'on presse pour vérifier que le carré blanc a disparu. Le bouton fait pour
constater la correction était le seul à ne pas l'avoir reçue.

Le test ne lisait que `sw.js`. Une vérification écrite autour du fichier qu'on
venait de corriger, pas autour du défaut : elle ne pouvait pas voir un second
appel ailleurs, et sa ligne verte disait quand même « le badge n'est pas un
carré blanc ».

**Douze vérifications maintenant**, et elles ne regardent plus un fichier mais
tous les appels : chaque `showNotification` de `sw.js` **et** d'`index.html`
doit déclarer un badge, tous doivent déclarer **le même** (sinon le bouton de
test montre autre chose que ce que le robot enverra), et aucun ne peut être
une icône du manifeste — celles-ci sont opaques par obligation, donc carrées
et blanches une fois réduites à leur alpha. C'est l'erreur commise deux fois,
nommée dans le message du test. Le badge lui-même est ensuite contrôlé comme
avant : PNG, canal alpha, carré, fond réellement transparent, cache au-delà
de v4.

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

**Le titre dit combien ; le corps dit quoi.** Pendant longtemps le
récapitulatif n'affichait aucun titre d'article, et l'argument était juste :
reprendre celui du *premier* article ne veut rien dire, « premier » étant
l'ordre de `FEEDS` et pas une importance. Un titre tiré au hasard parmi dix
donne une idée fausse du lot.

L'argument ne visait que le tirage au hasard, pas le fait de citer. Depuis
le 22/09/2026, les deux canaux citent — chacun selon ce qu'il sait afficher,
et jamais en tirant au sort :

- **Notification push** : le titre reste le compte, le corps porte l'article
  le plus notable (`corps_recap`), suivi de « · et N autres ».
- **Discord** : sous le même titre, les **cinq derniers articles**,
  cliquables. Un téléphone ne sait pas afficher cinq liens dans une
  notification ; Discord, si.

Le classement n'est pas inventé pour l'occasion, c'est celui que l'app
utilise déjà : un article **officiel** de Rockstar passe devant tout, puis
viennent les plus **récents**. `article_le_plus_notable()` y ajoute le
nombre de rédactions pour désigner *le* plus notable quand il n'y en a
qu'un à citer.

```
🎮 12 nouveaux articles GTA 6
─────────────────────────────────────────────
⭐ [Grand Theft Auto VI: An Extended Look](…) — Rockstar Games
🔥 [Rockstar resserre les règles de modding](…) — IGN · 5 sources
• [Morgan Wallen dévoile un titre pour la B.O.](…) — Backstage Country
• [Pourquoi GTA 6 n'est pas aux Golden Joystick](…) — GamesRadar+
• [Take-Two confirme la fenêtre de sortie](…) — VGTimes
+ 7 autres dans l'app

[Ouvrir GTA6_WATCH](…)
```

**Le titre de l'embed n'a pas bougé d'un caractère.** C'est le texte partagé
mot pour mot avec le push (`libelle_recap`), et l'invariant « les deux
canaux disent la même chose » tient toujours : seul le corps du message
Discord s'enrichit. La règle « un seul message par passage » tient aussi —
cinq liens dans un même embed, ce ne sont pas cinq messages.

**Le nom du média est extrait du titre.** Les titres venus de Google News
finissent par « - IGN », « - Frandroid », « - ixbt.games », et la source
stockée est « Google News (EN) » : affichés ensemble, ils donnaient des
lignes comme « … - GamesRadar+ — Google News (EN) ».
`separe_titre_et_media()` coupe cette queue et s'en sert comme libellé de
source. Mesuré sur les 1 811 articles du fil le 22/09/2026 : **1 437 titres
contiennent « - », dont 1 411 dont la queue est bien un média ou un
domaine**. Les 26 autres sont de vrais bouts de titre, tous écartés par
trois bornes — 32 caractères, 4 mots, pas de ponctuation finale. Le doute
profite toujours au titre : si la queue ne ressemble pas franchement à un
média, rien n'est coupé. Le nettoyage est **d'affichage seulement** :
`feed.json` et les cartes de l'app gardent le titre brut.

**Le récapitulatif du matin cite lui aussi.** C'est le passage qui porte le
plus gros lot — et le seul dont les articles ne sont plus « nouveaux » au
moment où il parle : à 5h, ceux de la nuit sont publiés depuis des heures.
Seuls leurs *comptes* survivaient dans `attente_recap`, trois entiers.
Depuis le 22/09/2026, `attente_recap` porte en plus **jusqu'à cinq aperçus**
de six champs (~800 octets), et `write_feed_pair()` les **retire du fichier
allégé** : l'app le retélécharge à chaque ouverture, elle ne doit pas payer
pour une donnée qu'aucune ligne d'`index.html` ne lit. Le coût réel est donc
~800 octets dans `feed.json`, et uniquement entre minuit et 5h.

**Un défaut trouvé en écrivant les tests.** `apercu_de()` ne calculait le
nombre de rédactions que depuis `extraSources`. Un aperçu *relu* après la
nuit ne porte plus `extraSources` — il porte le compte déjà fait — et
retombait donc à « 1 source », perdant son 🔥 sur exactement le
récapitulatif qui en a besoin. Un contrôle vérifie désormais que l'aller-
retour par `feed.json` rend un aperçu **identique**.

**Et un trou de couverture refermé au passage.** Toute cette arithmétique
vivait dans `main()`, que rien dans la suite ne lance : elle était donc
invérifiable — le trou exact par lequel était passée la profondeur
d'historique restée inerte. Elle est extraite en `reporte_ou_annonce()`, et
un contrôle lit la table des noms du code compilé de `main()` pour garantir
qu'il l'appelle réellement au lieu d'en garder une copie.

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

### Rien n'arrivait quand le téléphone était verrouillé

Signalé le 16/09/2026 : les notifications passaient écran allumé, jamais
autrement. Défaut présent depuis l'ajout des push.

**`pywebpush` envoie `TTL: 0` quand on ne lui dit rien.** Ce n'est pas une
déduction, c'est écrit dans sa propre documentation, `pywebpush/__init__.py`
ligne 318 : *« The Time To Live in seconds for this message if the recipient
is not online. Defaults to "0", which discards the message immediately if the
recipient is unavailable. »* Et ligne 353, `headers["ttl"] = str(ttl or 0)`
s'applique à tous les envois.

Ce que `TTL: 0` veut dire dans la norme (RFC 8030 §5.2) : le service de push
tente la livraison **à cet instant précis** et, si l'appareil ne répond pas,
**jette le message**. Pas de file d'attente, pas de seconde tentative.

Un téléphone verrouillé depuis un moment n'est justement pas joignable :
Android suspend la connexion pour économiser la batterie (Doze). Le message
arrivait, ne trouvait personne, et disparaissait. Au déverrouillage il n'y
avait rien à rattraper — il n'existait plus.

**Deux durées, pas une.** Un récapitulatif est périmé au passage suivant :
au-delà d'une heure il annoncerait un compte que le passage d'après a déjà
corrigé, d'où `TTL_RECAP = 3600`. Une annonce de Rockstar ne se périme pas de
la même façon et mérite d'arriver en retard plutôt que jamais, d'où
`TTL_OFFICIEL = 86400`.

**`Urgency` seulement pour Rockstar.** L'en-tête `Urgency` (RFC 8030 §5.3)
dit au service de push si le message vaut la peine de réveiller un appareil
endormi ; `pywebpush` n'en envoie aucun, donc il vaut `normal` par défaut.
Seules les annonces officielles partent en `high`. Tout marquer urgent est
exactement l'abus que les services de push finissent par sanctionner, et
transformerait le réglage en bruit de fond.

**Pourquoi le bouton « tester » restait vert.** Parce qu'on teste toujours
écran allumé — le seul cas où un `TTL: 0` passe. Le test empruntait un chemin
plus favorable que la vraie notification, donc il ne testait pas la vraie
notification. Il envoie désormais avec exactement les mêmes durée de vie et
urgence que le récapitulatif, et un contrôle l'exige.

**Confirmé sur l'appareil, le 16/09/2026.** Téléphone laissé verrouillé
longtemps : la notification était là au déverrouillage. C'est la seule preuve
qui compte ici, et elle ne pouvait pas venir des tests. Ceux-ci vérifient que
`ttl` et `Urgency` sont bien transmis à `pywebpush` — pas qu'un vrai service
de push, sur un vrai téléphone endormi, en fait ce que la norme annonce. Sans
ce relevé, la section ne dirait que « on a corrigé ce qu'on croyait être la
cause » ; avec lui, elle dit que c'était la bonne.

**Ce que cette correction ne peut pas réparer.** L'optimisation de batterie
appliquée au navigateur, le mode « Ne pas déranger » et les réglages du canal
de notification sont côté téléphone. Le code peut faire en sorte que le
message attende ; il ne peut pas obtenir le droit de s'afficher.

**Et ce qui n'était pas un défaut.** Entre minuit et 5h heure de Paris, seules
les annonces officielles notifient — c'est la pause nocturne, voulue. Un test
nocturne resté silencieux ne prouvait rien.

## Une seconde de coupure devenait « backend inaccessible »

Signalé le 17/09/2026, capture à l'appui : l'app affichait **MODE DIRECT** et
ne voyait plus le backend. L'hypothèse de départ — « `feed.json` est devenu
trop gros pour GitHub Pages » — était fausse, et c'est la façon dont elle a
été écartée qui vaut d'être notée.

### Trois mesures contre une intuition

**La limite de Pages est de 1 Go pour tout le site.** Le site fait 3,4 Mo.
2,8 Mo pour un fichier n'approche rien.

**Le message aurait été différent.** Le code fait
`throw new Error("HTTP " + res.status)` : un refus du serveur s'écrirait
« HTTP 404 ». Le journal disait « Failed to fetch » — une requête qui n'est
jamais partie.

**Le chronomètre a tranché.** Les quatre lignes du journal portaient la même
seconde, **22:26:43**. Un fichier trop lourd aurait mis plusieurs secondes à
échouer. Un échec instantané ne parle pas de taille, il parle de réseau.

Et l'app, rejouée dans un vrai Chromium avec ce `feed.json` de 2,8 Mo :
300 articles lus, 300 affichés, zéro erreur.

### Le vrai défaut, dans un commentaire trop confiant

La lecture tente d'abord `feed-recent.json` (332 Ko) et ne prend
`feed.json` (2,8 Mo) qu'en repli. Le journal montrait le **gros** fichier.

La tentative sur le fichier allégé était enveloppée dans un `try/catch`
annoté « pas de fichier allégé ». Ce commentaire disait la seule cause
d'échec que son auteur avait en tête — le fichier absent, sur un backend
d'une version antérieure. **Une coupure réseau tombe dans le même `catch`**,
et l'app allait alors réclamer huit fois plus de données sur la connexion qui
venait précisément de flancher.

Les deux échecs se ressemblent dans le code et n'ont rien à voir :

| ce qui se passe | bon repli |
|---|---|
| le serveur répond **404** | le fichier complet — il n'y a pas d'allégé |
| la requête **n'arrive pas** | surtout pas le fichier complet |

Désormais les deux sont distingués. Sur une panne réseau, le fichier allégé
est **retenté une fois** après 1,2 s ; si ça échoue encore, l'échec remonte et
le mode direct prend la main — au lieu d'aller chercher 2,8 Mo pour rien.

### Un délai maximal, et un vrai

Aucune des deux lectures n'était bornée : une connexion qui répond au ralenti
laissait l'app suspendue sans fin. Elles passent maintenant par un
`AbortController` à **15 s**.

`AbortController` et pas une course de promesses : lui **annule** la requête.
Une course rendrait la main au bout du délai mais laisserait le téléchargement
se poursuivre en arrière-plan, sur la connexion qu'on cherche justement à
ménager.

### Ce que les contrôles voient maintenant

**Six contrôles rendus**, qui fabriquent la panne avec `page.route()` — donc
sans le moindre accès réseau, et parfaitement déterministes :

| scénario | attendu |
|---|---|
| nominal | seul le fichier allégé est demandé |
| coupure réseau | l'allégé est retenté une fois, le complet **jamais** demandé |
| 404 sur l'allégé | on passe bien au complet, 3145 articles chargés |

L'ancien code a été remis en place pour vérifier qu'ils le prennent : sur la
coupure, il réclamait bien `feed.json`. **Huit contrôles de lecture** s'y
ajoutent dans `test_pipeline.py` — dont un qui interdit le retour de la phrase
« pas de fichier allégé » sur ce `catch`.

Celui-là a d'abord échoué sur sa propre documentation, le commentaire qui
explique le défaut citant forcément la phrase interdite. Même piège que le
16/09 avec `|| ""`, même correctif : retirer les commentaires avant de
chercher.

### Ce qui n'était pas un défaut

**La pointe du 17/09.** Le fil a pris 227 articles en dix heures, contre 67 par
jour d'habitude. Ce n'est pas la déduplication qui a lâché : **222 des 230
nouveaux liens sont datés du 17/09**, de la vraie actualité — Rockstar a
annoncé *GTA 6: The Album*. La moyenne des sept jours pleins est de
**74/jour**, à peine au-dessus de l'estimation de l'audit, et le plafond reste
à environ huit mois. La déduplication a d'ailleurs fusionné 18 articles ce
jour-là, et ne laisse que 7 paires proches sur 26 000 comparaisons.

**Les « 0 source(s) active(s) »** du journal. Elles avaient été désactivées à
la main, pour une capture d'écran plus lisible. Le mode direct fonctionne.

### Le coupable était un DNS privé, et le journal ne le disait pas

Résolu le 18/09/2026. Après deux jours de recherche côté serveur, la cause
était sur le téléphone : un **DNS privé** bloquait `github.io`. Mis en liste
blanche, tout est reparti.

**Trois hypothèses côté serveur, toutes fausses, toutes écartées par le même
détail.** Fichier trop gros pour Pages, quota dépassé, bridage : elles ont un
point commun, le serveur *répond*. Il répond « non », mais il répond — avec un
code. Et l'app écrit ce code, puisqu'elle fait
`throw new Error("HTTP " + res.status)`.

Le journal disait **« Failed to fetch »**. Pas de code : la requête n'a jamais
atteint le serveur. L'échec est en dessous du niveau HTTP. Aucun quota, aucune
limite de taille, aucun bridage ne peut produire ça.

Deux mesures l'ont confirmé avant qu'on regarde ailleurs :

| mesure | résultat |
|---|---|
| Sonde depuis un runner GitHub | **HTTP 200** sur `feed.json`, `feed-recent.json` et `index.html` |
| Bande passante consommée | **2,2 Go/mois** au pire, pour une limite indicative de 100 Go |

**Et un détail rétrospectivement parlant.** Pages avait déployé la correction
de la veille à 08h33min36s ; la capture du symptôme date de 08h34min57s, et
montrait pourtant l'ancien code. Ce n'était pas un hasard : le service worker
sert le squelette **réseau d'abord, cache en secours**. L'app tournait sur la
version en cache *parce que* github.io était injoignable. Elle avait l'air
parfaitement vivante — servie par le téléphone, pas par le serveur.

**Le journal le dit maintenant.** Quand l'échec ne porte pas de code HTTP,
l'app ajoute une ligne : *la requête n'a pas atteint le serveur, regarde du
côté DNS privé, VPN ou bloqueur de pub*. `github.io` figure sur certaines
listes de blocage parce qu'il héberge aussi des pages de phishing. Cette ligne
fait dire à l'app ce qu'il a fallu deux jours pour déduire.

### 300 « nouveaux » par heure pour 2 articles réels

Signalé le 22/09/2026, capture Discord à l'appui : une notification par heure
annonçant *« 301 nouveaux articles GTA 6 »*, *« 298 »*, *« 296 »*… et **1887**
au récapitulatif du matin. Le rythme réel est de 74 par **jour**.

**Régression introduite quatre jours plus tôt, par l'abaissement du plafond.**
Les chiffres la datent à l'heure près :

| | articles | durée | « nouveaux » annoncés |
|---|---|---|---|
| 18/09 06h02, avant le plafond | 3202 | 92 s | **2** |
| 18/09 11h43, après | 1500 | 134 s | **259** |

**Deux fenêtres qui ne se parlaient pas.** Le plafond de 1500 couvre environ
dix-sept jours ; `MAX_ARTICLE_AGE_DAYS` en accepte quarante-cinq à l'entrée.
Chaque passage réabsorbait donc les articles de 17 à 45 jours que les flux
resservent — absents de l'historique élagué, ils passaient pour neufs — puis
`cap_items` les rejetait aussitôt.

Mesuré sur deux passages consécutifs : **2 articles réellement entrés,
293 annoncés**. Le fichier n'a jamais été faux ; c'est le compte qui l'était,
parce qu'il était arrêté avant le plafond.

**Le correctif est un ordre, pas une règle de plus.** Le plafonnement passe
maintenant AVANT tout ce qui exploite `newly_added`, et les articles entrés
puis élagués dans le même passage sont retirés des nouveautés — et des
compteurs par source, sinon la somme par source contredirait le total.

Ce qui se répare en partie du même coup : `fetch_missing_images`
téléchargeait les miniatures des 270 articles déjà condamnés.

**Une estimation corrigée par la mesure.** Cette section annonçait d'abord
« 45 secondes par passage » et « la durée revient à son niveau d'avant ».
Les deux étaient faux, et c'est le premier passage en production qui l'a dit :
**142 s → 116 s, soit 26 secondes**, et non un retour aux 92 s d'avant le
plafond. L'écart s'explique — le correctif retire le TÉLÉCHARGEMENT des
miniatures, pas la réabsorption elle-même : décoder et dédupliquer ces
articles coûte toujours la vingtaine de secondes qui reste.

Ces 24 secondes n'ont volontairement pas été poursuivies. Le dépôt est public
donc les minutes GitHub Actions sont gratuites, et le délai médian qui sépare
d'un article est de 54 minutes dont 47 de cadence : gagner 24 secondes
là-dessus ne se voit pas. Les deux façons de le faire ont chacune un défaut —
un `MAX_ARTICLE_AGE_DAYS` fixe redeviendrait faux au premier changement de
volume, et déduire le plancher de l'historique toucherait le filtre d'entrée,
l'endroit où une erreur ne se voit pas puisqu'on ne remarque pas un article
qui n'est jamais arrivé.

**L'invariant, désormais tenu par un test :** on n'annonce jamais ce qu'on n'a
pas gardé. Quatre contrôles vérifient l'ORDRE des étapes — plafond, puis
réconciliation, puis miniatures, actus majeures et compte publié — parce que
c'est l'ordre, et non la logique, qui était faux.

**Confirmé en production le 22/09/2026.** Premier passage après la fusion :
`new_this_run` annonce 0, et 0 lien est réellement entré — ils coïncident pour
la première fois. Le journal du robot dit `[discord] aucun nouvel article à
annoncer` et `[push] aucun nouvel article à annoncer` : avant, ce même passage
aurait envoyé « ~270 nouveaux articles » sur les deux canaux.

### La vignette ouvre l'article

Demandé le 22/09/2026. C'est la plus grande zone de la carte, et ne rien faire
au clic la faisait passer pour décorative : il fallait viser le titre.

**Le piège n'était pas de poser le lien, mais de ne pas en créer un second.**
L'image est en `alt=""`. Une ancre focalisable autour d'elle serait un lien
**sans intitulé** vers la même page que le titre : un lecteur d'écran
annoncerait « lien » et rien d'autre, et le clavier gagnerait un arrêt inutile
par carte. D'où `aria-hidden="true"` et `tabindex="-1"` — la souris et le
doigt gagnent la cible, le clavier garde le seul lien qui se nomme.

**`display:contents` sur l'enveloppe.** Sans lui, l'ancre deviendrait l'élément
flex de `.card-top` et emporterait `align-self:stretch`, les marges négatives
et toutes les surcharges du mode compact : il aurait fallu recopier
`.card-thumb` sur deux sélecteurs, qui auraient divergé au premier ajustement.
`display:contents` retire la boîte, pas l'élément — le clic fonctionne, la
géométrie ne bouge pas. Un contrôle mesure le ratio 16/9 pour l'exiger.

Sept contrôles rendus : l'image est dans un lien, il mène au même article que
le titre, il est hors de l'arbre d'accessibilité, **la carte garde un seul
lien focalisable**, la géométrie est intacte, le clic ouvre l'onglet, et
l'article est marqué comme lu.

## Récapitulatif hebdomadaire — supprimé le 15/09/2026

Un message Discord partait le dimanche soir avec les sujets les plus repris
des sept derniers jours, classés par nombre de rédactions les ayant couverts.

**Il a été retiré parce qu'il ne servait plus.** Les notifications push
natives annoncent chaque article au moment où il paraît : le dimanche soir,
la semaine avait déjà été lue article par article. Restait un message, sur
Discord — le canal que ce projet considère lui-même comme le plus faible,
puisque c'est précisément pour ça que les push ont été construites (« taper
une notification Discord ouvre Discord, jamais l'article »).

Le seul apport propre du récapitulatif était son classement par nombre de
rédactions. L'app le fait déjà en direct, avec le badge « N SOURCES » et le
marqueur « actu majeure ».

**À ne pas confondre avec le récapitulatif du matin**, qui reste en place et
qui, lui, est indispensable : c'est celui qui rattrape la pause nocturne
(`attente_recap` dans `feed.json`, `libelle_recap()` dans `feed_store.py`).
Les deux portaient le même mot et n'ont jamais partagé une ligne de code —
`weekly_digest.py` n'était importé par rien d'autre que ses propres tests,
ce qui a été vérifié avant de le supprimer.

Sont partis avec lui : `weekly_digest.py`, `.github/workflows/weekly-digest.yml`
et ses 16 vérifications. Le test qui vérifiait qu'il épinglait ses
dépendances, lui, **est resté** — mais renommé
`test_les_workflows_epinglent_leurs_dependances` : la règle qu'il portait
vaut pour les trois workflows restants et ne dépend plus du cas qui l'avait
révélée.

**Un défaut ouvert s'est refermé avec lui.** Le récapitulatif était déclenché
par le `schedule` de GitHub, best-effort : programmé à 20h00, il partait en
réalité à **22h30, 22h10 et 22h46** les trois derniers dimanches. Le remède
était prêt — le workflow acceptait déjà un `repository_dispatch`, il ne
manquait qu'une tâche dans cron-job.org. Supprimer le récapitulatif a réglé
la question autrement, et sans rien à configurer : pas de tâche à ajouter,
pas de tâche à retirer, cron-job.org n'en a jamais eu pour lui.

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

### Retirer une source, et ce qu'elle laisse derrière elle

**VG247 et Xbox Wire sont parties le 15/09/2026**, après trois mesures
concordantes et non pas sur une impression.

| | entrées par passage | articles retenus, depuis l'ajout | dernier |
|---|---|---|---|
| VG247 | 100 | 8 | 26/07/2024 (781 j) |
| Xbox Wire | 6 | **0** | jamais |

Les deux flux **répondaient**. Ce n'était pas une panne, et c'est ce qui rend
le cas intéressant : ce sont des recherches Google News restreintes à un
domaine, et une recherche `site:` classe par **pertinence, pas par date**.
Elle ressert donc indéfiniment la couverture *historique* du média. Le
garde-fou `MAX_ARTICLE_AGE_DAYS` les écartait à chaque passage, correctement.
Le verdict `tarie` était juste : le flux marche, le site ne publie pas sur le
sujet.

Xbox Wire est le fil d'annonces de Microsoft — s'il parle de GTA 6 un jour,
Pure Xbox, TrueAchievements et les deux Google News l'auront relayé dans
l'heure. La redondance était déjà là.

**Ars Technica a d'abord été gardée, puis retirée le jour même.** Le pari
défendu ici était qu'un site tech généraliste écrirait sur GTA 6 le jour d'un
sujet sécurité ou industrie. Il tenait, mais il restait un pari — contre un
fait mesuré : **zéro article depuis l'ajout**. Entre les deux, Antoni a
tranché pour le fait. Et la couverture « industrie » est de toute façon
tenue : The Verge, Engadget, Tom's Hardware, Take-Two IR et le fil de Jason
Schreier sont là pour ça, avec un historique, eux.

**Le piège : retirer la source n'enlève pas ses articles.** La fusion
conserve tout ce qui est déjà stocké — c'est même son unique travail, et le
garde-fou qui refuse une fusion vide existe pour ça. Les 8 articles VG247,
datés de 2022 à 2024, seraient donc restés dans le flux sous un nom qui
n'existe plus nulle part dans le code.

Ce sont les pires à débusquer : vieux, donc tout en bas du tri par date, donc
invisibles à l'usage. Rien ne les signale. Ils ont d'ailleurs une origine
précise — ce sont exactement les 8 archives remontées le 29/08/2026 par la
recherche `site:` avant que le garde-fou d'âge n'existe. La fuite avait été
bouchée le jour même ; ce qui était déjà entré n'avait jamais été retiré.

**Dix-huit vérifications** verrouillent maintenant l'ensemble, et elles ne
regardent pas la liste des sources mais le flux produit : aucun article ne
peut venir d'une source absente de `FEEDS` ; aucun des cinq journaux de santé
(`sources_health`, `sources_silence`, `sources_entries_history`,
`sources_declining`, `feed_http_state`) ne peut parler d'une source qui
n'existe plus — sinon le bandeau compterait éternellement une ligne tarie
pour une source qu'il n'interroge plus ; et les compteurs annoncés doivent
valoir ce qu'il y a réellement.

**Dix-huit et non neuf, parce qu'il y a deux fichiers.** `feed-recent.json`
est l'extrait de 300 articles que l'app télécharge **en premier** — ne
verrouiller que `feed.json` laissait hors de portée le seul fichier
réellement lu au démarrage. Le trou s'est vu le jour même, en résolvant le
conflit du retrait : l'extrait annonçait encore 50 sources et 2 580 articles
pendant que le fichier complet en annonçait 48 et 2 572.

Un piège dans le piège : dans l'extrait, `total_articles` et `hot_count`
décrivent le **flux entier**, pas les 300 lignes présentes — c'est bien
« 2 572 articles » que doit afficher le bandeau. Les recalculer depuis
l'extrait donne 300 et 0, deux valeurs fausses. Le test les compare donc au
fichier complet, jamais à `len(items)`. J'ai fait l'erreur avant de l'écrire.

Éprouvé sur l'état d'avant la purge, fichier par fichier : quatre
vérifications tombent sur `feed.json` en nommant `{'VG247': 8}` et
`['vg247', 'xboxwire']`, quatre autres sur `feed-recent.json`.

**Le même comptage révélait 32 autres articles** antérieurs au garde-fou et
venant de sources qui n'ont pas le droit de garder leurs archives —
RockstarINTEL de mars 2026, GTA6 Times de juin, des annonces de précommande.
Contrairement aux 8 de VG247, ceux-là étaient du contenu réel et récent au
moment de leur import : les jeter était une décision de fond, pas un
nettoyage. Elle a été prise le soir même — voir ci-dessous.

### Doubler une source au lieu de la remplacer

Troisième liste de candidates soumise le 15/09/2026, 50 adresses, cette fois
avec les URL de flux. Douze sondées, **trois retenues** — et la décision
intéressante n'est pas laquelle, c'est **comment**.

| candidate | entrées | dont GTA 6 | plus récente |
|---|---|---|---|
| Journal du Geek (tag natif) | 30 | **27** | le jour même |
| Frandroid (tag natif) | 15 | **14** | 5 j |
| Reddit — recherche r/GamingLeaksAndRumours | 25 | **12** | 11 j |

**Les deux premières existaient déjà**, mais par une recherche Google News.
Ces flux-ci sont les flux natifs des mêmes médias, restreints au tag
`gta-6`. Bien plus denses. La tentation était de remplacer.

**Elles ont été AJOUTÉES, pas substituées, et c'est le point.** Un flux par
tag dépend du balisage du média : si un journaliste oublie le tag, l'article
n'existe pas pour le robot — et **rien ne le signale**. La source a
simplement l'air calme. C'est le pire type de panne : silencieuse et
indétectable après coup. Le tri via Google News, lui, ne dépend pas de leur
rigueur.

Deux requêtes de plus sur un passage de 50 secondes contre le risque de
perdre un article sans le savoir : pour une veille dont le but est de ne rien
rater, l'asymétrie tranche seule. La déduplication par lien fait le ménage,
et dans une semaine on saura, en comparant, si l'un des deux attrape ce que
l'autre manque.

**La recherche Reddit, et la nuance qui change tout.** Reddit avait été
écarté deux fois dans la journée, à raison : `/r/GTA6/new/.rss` déverse les
mèmes du subreddit à la journée. Mais `search.rss` sur
r/GamingLeaksAndRumours est une **recherche** filtrée sur « GTA 6 », et ce
qu'elle remonte est du suivi de fuites — *« GTA 6 Leaks Could Be Over As
Cyberleek Withdraws $250,000 »*, *« GTA 6 Will Be 30FPS at Launch »*. C'est
le seul angle leak-tracking de toute la liste, et le seul moyen propre
d'atteindre Reddit. Écarter une plateforme entière sur la foi d'une seule de
ses adresses était une erreur de raccourci.

**Le Rockstar Newswire natif, lui, a été écarté — et le README avait prévenu.**
Les deux adresses (`rockstargames.com/newswire.rss` et sa version FR)
répondent **HTTP 500**, pas 404. Or c'est exactement le piège consigné au
30/08 : six adresses candidates sur ce domaine avaient toutes renvoyé 500,
**y compris une inventée de toutes pièces**. Ce serveur répond 500 là où un
autre répondrait 404, donc **un 500 n'y prouve rien**. Les deux sources
officielles restent sur Google News : moins élégant, mais éprouvé depuis
trois semaines.

Écartées aussi, mesures à l'appui : Take-Two press-releases (pas un flux),
Gamergen tag (pas un flux), Neowin (403), Attack of the Fanboy et
GamesIndustry tag `take-two` (404 — tags inventés, les sites ne déclarent que
des flux génériques), Rockstar Universe (10 entrées, 0 retenue, 7 archives),
et PlayStation Blog FR — flux **valide mais totalement vide**, le tag existe
et personne n'a jamais rien publié dessous.

### Cinquante noms proposés, une seule source retenue

Le 15/09/2026 au soir, une liste de « 50 sources spécialisées pour ne rien
rater sur GTA 6 » a été soumise au projet. Elle avait tout d'une liste
exhaustive. Passée au crible, il en est sorti **une** source.

**Vingt-quatre y étaient déjà**, dont les quatre officielles au complet. Et
l'inverse valait aussi : la liste ignorait **26 sources de `FEEDS`** —
GTA BOOM, GTA6 Times, RockstarINTEL, RockstarMag, VGTimes, PCGamesN, les deux
Google News… Ce n'était pas une liste plus complète, c'était une liste
différente et plus courte.

**Onze étaient hors de portée par construction.** Le robot lit du RSS ; X
(Twitter) n'en sert plus depuis 2023, Discord n'en a jamais eu, GTAForums
bloque les robots — c'est déjà documenté ici. Aucune insistance ne changera
ça. Reddit fait exception (`/r/GTA6/.rss` existe), mais c'est un fil de mèmes
et de théories : du bruit à la journée.

**Les dix restantes ont été sondées.** Une seule a tenu.

| candidate | entrées | dont GTA 6 | verdict |
|---|---|---|---|
| **GamesIndustry.biz** | 100 | **2** | retenue, la plus récente du jour |
| MP1st | 20 | 0 | flux vivant, rien sur GTA 6 |
| Digital Foundry | 100 | 0 | c'est la rubrique *tech* d'Eurogamer, déjà présente |
| Tez2 (Google News) | 50 | 0 | voir ci-dessous |
| GTA6Guide, GrandTheftAuto5.fr, Les Numériques, PhonAndroid | — | — | **aucun flux RSS** |
| GTANF | — | — | flux déclaré mais mal formé (`undefined entity`) |
| GTA6France | 10 | 10 | voir ci-dessous |

**Le piège du pseudonyme.** L'idée semblait excellente : ce dépôt suit déjà
**Jason Schreier** par une recherche Google News sur son nom, donc pourquoi
pas **Tez2**, l'insider Rockstar le plus respecté ? Deux requêtes testées,
stricte puis large : 50 entrées chacune, **zéro retenue**, dont 15 à 22
archives de plus de 45 jours. La raison tient à une différence qui ne saute
pas aux yeux — « Jason Schreier » est un nom de journaliste qui figure **dans
les titres**, « Tez2 » est un pseudonyme cité **dans le corps du texte**.
Google News n'en fait pas un sujet. Le pattern ne se transpose pas d'un nom
à l'autre.

**Et le piège inverse : 100 % de pertinence comme signal d'alarme.**
GTA6France affiche le meilleur score de la journée — 10 entrées, 10 retenues,
la plus récente de la veille. Sauf que ses titres sont :

> *Guide : comment semer la police et réduire la heat dans GTA 6*
> *Guide : comment changer entre Jason et Lucia dans GTA 6 ?*

Des guides de jeu pour un jeu qui **n'est pas sorti**. Personne n'y a joué.
Ce ne peut être que du remplissage écrit pour le référencement. Le filtre par
mots-clés ne pouvait pas le voir : il compte les mots, pas la valeur. Un taux
de rétention parfait sur un site inconnu mérite d'être regardé de plus près,
pas célébré.

### Les 33 vieux articles, et pourquoi aucun test ne les remplace

Décision d'Antoni, 15/09/2026 : purger tout article de plus de
`MAX_ARTICLE_AGE_DAYS` (45 j) venant d'une source sans `garder_les_archives`.
**33 partis** (32 le matin, un de plus ayant franchi la ligne dans la
journée), de 46 à 313 jours. 2 586 → 2 553 articles.

**Un seul était marqué `official`** et il a été vérifié avant d'être jeté :
« Grand Theft Auto VI Pre-Orders Begin on June 25 », arrivé par Google News.
C'est un **relais** d'une annonce que Rockstar porte elle-même sur ses fils
archivés — en anglais (*Pre-Order Grand Theft Auto VI on June 25*), en
français (*Précommandez Grand Theft Auto VI le 25 juin*) et chez Take-Two
(*Rockstar Games Announces Pre-Orders*). Les trois sont conservées, elles
appartiennent à des sources à archives. Rien ne s'est perdu. Aucun article
« actu majeure » n'était concerné.

**Et aucun test ne verrouille ce nettoyage, délibérément.** La règle évidente
— « aucun article de plus de 45 jours » — serait un piège : les articles
vieillissent tout seuls. Celui qui entre aujourd'hui à 0 jour en aura 46 dans
un mois et demi, et ferait échouer la suite sans qu'aucun défaut n'existe. Un
test qui tombe au rouge par le seul passage du temps est pire qu'absent : on
finit par le débrancher, et on débranche ce qu'il protégeait avec lui.

La variante « rien d'antérieur au premier passage du robot » a été examinée et
écartée pour la même raison : un article vieux de 45 jours à son import est
légitime, et sa date peut déjà tomber avant cette borne.

**Conséquence assumée : le fil dérivera de nouveau.** C'est un nettoyage
ponctuel, pas un mécanisme. Le seul remède durable serait que le robot élague
à l'écriture, ce qui est un changement de conception — et pas ce qui a été
demandé.

### Trois sources retirées, trois ajoutées — et la sonde entre les deux

Le 15/09/2026, la liste passe de 50 à 47 puis revient à 50, mais ce n'est pas
un aller-retour : ce qui est entré n'a rien à voir avec ce qui est sorti.

**Ce qui est sorti** : VG247, Xbox Wire, Ars Technica — trois recherches
Google News `site:domaine`, zéro ou presque zéro article retenu depuis leur
ajout. **Ce qui est entré** : trois **flux RSS natifs**.

| entrée | flux | entrées | dont GTA 6 | plus récente |
|---|---|---|---|---|
| Rockstar Actu 🇫🇷 | `rockstaractu.com/feed/` | 20 | **9** | 11 j |
| TweakTown 🇬🇧 | `/feeds/news-mf.xml` | 15 | 2 | le jour même |
| Dexerto FR 🇫🇷 | `/feed/` | 50 | 2 | 4 j |

**Natif plutôt que Google News, et c'est tout le sujet.** Une recherche
`site:` classe par PERTINENCE : elle ressert indéfiniment la couverture
historique d'un média, que `MAX_ARTICLE_AGE_DAYS` écarte à chaque passage —
d'où trois sources éternellement « taries ». Un flux natif est
chronologique : quand le site publie, l'article arrive en tête, point. C'est
la leçon de VG247 appliquée à l'endroit, pas seulement constatée.

Neuf sur vingt pour Rockstar Actu, le meilleur ratio de toute la liste. C'est
un média 100 % Rockstar, comme RockstarMag — il n'y en avait qu'un seul en
français, pour un sujet qui est le cœur de cette veille.

**Rien n'a été ajouté sans sonde.** Les neuf candidates ont été passées au
workflow `sonde.yml`, depuis un runner GitHub, avec le même agent utilisateur
et le même filtre que le robot. Trois tours, vingt adresses. Écartées faute
de flux : **Millenium** (404), **GTABase** (404), **Leonidaverse** (aucun flux
déclaré, même en suivant sa redirection) — inexploitables, quelle que soit
leur qualité éditoriale. **Clubic** a un flux valide mais zéro entrée GTA 6
sur ses 50 dernières ; écartée pour l'instant, à revoir, car son flux étant
chronologique un article y arriverait normalement.

La sonde a aussi servi à trouver les adresses elles-mêmes : passer la page
d'accueil suffit, elle lit les `<link rel="alternate">` de la page et
annonce les flux déclarés. C'est ainsi que les adresses de Clubic et de
TweakTown sont sorties — aucune n'a été devinée.

**Ce qu'elles ont réellement produit, trois passages plus tard.** La sonde
annonce ce qu'un flux contient ; elle ne dit pas ce qu'il APPORTE, parce que
la déduplication passe après elle. Mesuré le 15/09/2026 à 15h01 :

| source | entrées reçues | articles en propre | en renfort | liens vers son domaine |
|---|---|---|---|---|
| Rockstar Actu | 20 | **7** | 0 | 8 |
| TweakTown | 15 | 0 | **2** | 21 |
| Dexerto FR | 50 | 0 | 0 | 9 |

Trois résultats, trois lectures différentes, et c'est la troisième colonne
qui les sépare :

- **Rockstar Actu apporte du neuf.** Sept articles sous son propre nom, et
  chacun ne compte qu'UNE source — donc aucun des 49 autres flux ne les
  avait. C'est exactement le trou visé : de la couverture GTA francophone
  que personne d'autre ne portait.
- **TweakTown confirme.** Ses deux articles existaient déjà, apportés par un
  autre média ; ils ont été fusionnés et comptent désormais **deux
  rédactions** au lieu d'une. Ce n'est pas rien — c'est ce compte qui pilote
  le badge « N SOURCES » et le marqueur « actu majeure ».
- **Dexerto FR n'a rien apporté**, et le verdict `tarie` est exact. Mais neuf
  articles du fil pointent déjà vers dexerto.fr : son contenu arrive, sous le
  nom de Google News qui la double systématiquement. À surveiller — si elle
  reste à zéro, elle ne sert qu'à payer une requête par passage.

La dernière colonne est l'indicateur à retenir quand on juge une source : un
grand nombre de liens vers son domaine, apportés par d'AUTRES flux, signifie
que le sujet est déjà couvert et que la source ajoutée fera doublon. TweakTown
à 21 et Dexerto FR à 9 étaient prévisibles ; Rockstar Actu à 8, dont 7 qu'elle
a apportés elle-même, ne l'était pas.

**Et le premier ajout a cassé le test écrit le matin même.** `sources_count`
y était comparé à `len(FEEDS)` par égalité stricte. À l'ajout, les fichiers
annonçaient 47 pendant que `FEEDS` en déclarait 50 : parfaitement normal, le
robot n'avait pas encore tourné. L'égalité est devenue une **inégalité**, et
l'asymétrie est le fond du sujet — un fichier en RETARD sur `FEEDS` se
corrige tout seul au passage suivant, un fichier en AVANCE compte une source
qui n'existe plus et ne se corrige jamais. Seul le second est un défaut. Les
contrôles de fantômes, eux, n'ont pas bougé : ils nomment toujours la source
disparue et le fichier fautif.

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

### Deux sites voisins, deux chemins opposés

Le 15/09/2026, quatre candidates sont sondées : GTA Base, MGG (Millenium),
Destructoid, Dexerto.com. **Trois entrent, et aucune par le même chemin.**
La liste passe de 54 à 57.

Premier tour, les pages d'accueil : les quatre rendent `PAS UN FLUX`, et la
découverte automatique ne trouve **aucun** `<link rel="alternate">`. Ce n'est
pas une panne, c'est l'époque : ces sites sont en React ou Next.js et ont
laissé tomber la balise d'auto-découverte que les navigateurs lisent depuis
vingt ans. Une page d'accueil muette ne prouve donc plus rien.

Second tour, chemins standards et repli Google News `site:` :

| candidate | flux natif | Google News `site:` | retenu |
|---|---|---|---|
| **Destructoid** | 200 — 30 entrées, **1** (0 j) | 100 entrées, **0** | le flux natif |
| **Dexerto.com** | 200 — 50 entrées, **1** (0 j) | 100 entrées, **6** (0 j) | Google News |
| **MGG (Millenium)** | 404 sur `/rss` et `/feed` | 100 entrées, **3** (4 j) | Google News |
| GTA Base | 404 sur `/feed/` et `/gta-6/news/feed/` | 100 entrées, 3 (2 j) | **écartée** |

**Destructoid et Dexerto se contredisent, et c'est le point.** Les deux sont
des sites de jeu vidéo anglophones de taille comparable, et pourtant le
chemin qui marche pour l'un est précisément celui qui échoue pour l'autre.
Destructoid n'existe que par son flux natif — Google News n'en ressort rien,
zéro sur cent entrées, alors que le flux sort un article GTA 6 du jour.
Dexerto, lui, n'existe que par Google News : son flux natif répond
parfaitement mais ne retient qu'un article sur cinquante, parce que c'est un
flux généraliste esport où GTA 6 se noie. Choisir un chemin par principe —
« le natif est toujours meilleur » — aurait perdu l'un des deux. Il n'y a pas
de règle, il n'y a que la mesure.

**Un 403 sur la page d'accueil ne condamne pas le site.** Destructoid répond
403 à un runner GitHub sur `destructoid.com`, et 200 sur
`destructoid.com/feed/`. Le pare-feu protège les pages, pas le flux. Conclure
« site bloqué » après le premier tour aurait fait perdre la seule source des
trois qu'aucun autre chemin ne peut atteindre.

**GTA Base a été écartée, et c'était la favorite.** Fansite Rockstar
historique, rubrique GTA 6 dédiée, exactement le profil de RockstarINTEL ou
GTA6 Times qui fonctionnent bien ici. Elle sort pourtant 3 résultats retenus,
et ce sont :

> *GTA 6 Cars & Vehicles Database: Full Confirmed List & Stats*
> *GTA 6 Map: Full Leonida Map, Vice City & All Locations*
> *GTA 6 Characters Guide: Main Protagonists & Full List*

Des pages de guide **permanentes**, re-datées à chaque mise à jour. Elles
remonteraient dans le fil comme des nouveautés alors qu'il ne s'est rien
passé. C'est le même piège que GTA6France plus haut, sous un meilleur
déguisement : là-bas des guides pour un jeu non sorti, ici des bases de
données qui vieillissent en restant au présent. Une réputation n'est pas une
mesure.

**L'indicateur de redondance a servi en amont.** Avant de sonder quoi que ce
soit, on compte combien de liens vers le domaine candidat arrivent **déjà**
dans `feed.json`, `extraSources` compris. Sur 2 594 articles : GTA Base 0,
Destructoid 0, Dexerto.com 0, millenium.org 3. Les quatre étaient de vrais
trous. C'est ce même comptage qui a fait écarter une douzaine d'autres noms
sans sonde — Notebookcheck (42 liens, dont 41 sur 30 jours), GamingBible (32),
Forbes (16), TechRadar (15) : ils arrivent déjà en abondance par les Google
News généralistes, les ajouter aurait coûté des requêtes pour rien.

**Et une nuée de faux candidats.** La recherche a fait remonter
`gta6.news`, `gtavice.net`, `gta6og.com`, `leonidaverse.com`, `gta6base.net`,
`wikigta6.com`, `gta6hub.fr`, `igrandtheftauto.com` — toutes annoncées « #1
source, mise à jour quotidienne », aucune avec de rédaction identifiable, et
l'une poussée par un **communiqué de presse payant** sur un site de bourse.
Elles n'ont pas été sondées : le tri s'arrête avant, sur ce que le site dit
de lui-même.

### Compter les articles exclusifs, pas les jours de silence

Le 15/09/2026 au soir, question posée au projet : chaque source a-t-elle un
meilleur chemin que celui qu'elle emprunte ? Les 57 ont été passées au
crible — l'équivalent Google News `site:` sondé pour les 32 sources en flux
natif, le flux natif sondé pour les 13 sources en Google News.

**La réponse est non, partout.** Et le chemin pour y arriver a d'abord été
faux.

#### La mesure trompeuse

Premier verdict, tiré en comparant le compteur `days_since_last_article` du
journal de santé à la fraîcheur rendue par la sonde : cinq sources
(Xboxygen, Numerama, Push Square, Pure Xbox, Gamergen) semblaient avoir un
flux natif **nettement plus frais** que leur Google News — 0 jour contre 8,
0 contre 6, 0 contre 7, 1 contre 11. De quoi conclure que Google News,
classant par pertinence et non par date, enterrait les articles récents
au-delà du centième résultat.

C'était faux, et la vérification tient en une ligne : l'article que la sonde
présentait comme un manque pour Numerama — *« PS5 Slim, FAT ou PS5 Pro :
laquelle acheter en 2026 avant GTA 6 ? »* — **était déjà dans le fil**,
apporté le jour même par Google News (FR).

`days_since_last_article` est indexé **par nom de source**. Quand l'article
d'IGN arrive par le flux large *Google News (EN)*, il est classé sous
« Google News (EN) ». Le compteur d'IGN reste donc vieux de 4 jours alors
qu'un article d'IGN du jour est dans le fichier. Ce compteur ne répond pas à
la question « rate-t-on les articles de ce site ? » ; il répond à « quand ce
flux précis a-t-il été le premier à rapporter quelque chose ? ». Les deux
sont sans rapport dès qu'une source généraliste couvre le même domaine.

Vérifié sur les cinq : le plus récent article déjà présent était à 0 j pour
numerama.com, 1 j pour xboxygen.com et gamergen.com, 5 j pour pushsquare.com
et 7 j pour purexbox.com — presque toujours via un Google News large. Doubler
ces cinq sources n'aurait ajouté que deux choses : la page-guide permanente
*« Carte GTA 6 : map interactive »* d'Xboxygen, et un article Rayman de
Pure Xbox retenu sur une mention de GTA 6 dans son corps de texte. La
proposition a été retirée avant d'écrire la moindre ligne de code.

#### La bonne mesure

Ce qu'il faut compter, c'est **combien d'articles disparaîtraient du fil si
on retirait la source** : les articles dont elle est la seule porteuse.
Sur 2 594 articles :

| source | exclusifs | dont 30 j |
|---|---|---|
| Google News (FR) | 628 | 628 |
| Google News (EN) | 514 | 512 |
| GTA 6 x Netflix | 234 | 231 |
| Rockstar Games (annonces) | 111 | 102 |
| GTA BOOM | 93 | 90 |
| VGTimes | 67 | 67 |
| Game Rant | 57 | 57 |
| IGN | 56 | 56 |
| RockstarINTEL | 54 | 46 |
| … | | |
| Journal du Geek (tag) | 3 | **0** |
| TweakTown · Dexerto FR · Frandroid (tag) | **0** | **0** |

Les deux Google News larges portent **46 % du fil à eux seuls** (1 142
articles sur 2 469 exclusifs). C'est la colonne vertébrale, et c'est
précisément pour ça qu'un `site:` posé par-dessus fait doublon : le
généraliste a déjà pris l'article.

#### Deux exceptions, qui confirment l'existant

- **RockstarINTEL** : sa recherche Google News `site:` ne rend **qu'un**
  article, vieux de 40 jours, là où son flux natif a apporté **54 articles
  exclusifs**. Google News n'indexe quasiment pas ce site.
- **PCGamesN** et **Rockstar Actu** : leur recherche `site:` rend **zéro**
  article retenu sur 100 entrées. Le flux natif est le seul chemin.

Dans les deux cas le transport en place est déjà le bon.

#### Le recouvrement est plus faible qu'il n'y paraît

**Seuls 135 articles sur 2 594 sont arrivés par plus d'une source.** La
déduplication par lien ne se déclenche presque jamais : chaque source
apporte ses propres URLs, et deux sites qui couvrent la même histoire
produisent deux liens différents, donc deux articles. L'argument « doubler
une source pour ne rien rater » en sort affaibli : ce qu'on gagne, ce n'est
pas un article manquant, c'est une deuxième version du même événement.

#### Ce qu'il reste à surveiller

Quatre sources sont à zéro article exclusif. Trois ont été ajoutées la
veille ou l'avant-veille (Dexerto FR, Frandroid (tag), Journal du Geek
(tag)) et TweakTown quelques heures plus tôt : trop tôt pour conclure. Le
calcul est à refaire dans quelques jours — celles toujours à zéro ne servent
à rien.

À l'inverse, quatre sources à faible volume sont **à garder malgré leurs
chiffres** : Take-Two IR (1 exclusif, flux trimestriel), Rockstar officiel
EN et FR (18 et 9), Jason Schreier (13, un seul sur 30 jours). Elles
rapportent peu, mais ce qu'elles rapportent est officiel ou de première
main. Le volume n'est pas la valeur.

### Six écritures du nom, dont deux acceptées les yeux ouverts

Le 15/09/2026 au soir, question posée : les requêtes Google News couvrent-elles
toutes les façons d'écrire le nom du jeu ? Non. Et l'écart était au pire
endroit.

**Les deux flux larges cherchaient `"GTA 6"` et rien d'autre**, alors que les
treize flux `site:` cherchaient déjà `("GTA 6" OR "Grand Theft Auto VI")`.
Or ce sont ces deux flux larges qui portent **46 % du fil**. Un article
intitulé *« Grand Theft Auto VI … »* ou *« GTA VI … »* — les formes que la
presse sérieuse et Rockstar emploient — ne leur parvenait pas. L'incohérence
datait du 29/08 et personne ne l'avait vue.

Distinction qui rend le problème invisible : **les 35 sources en flux natif
n'étaient pas concernées**. Leur flux arrive entier et c'est la liste des mots-clés
qui trie — or elle contient déjà `gta 6`, `gta6`, `gta vi`, `gtavi`,
`grand theft auto 6`, `grand theft auto vi`, `gta-6`, `gta_vi`… Chez Google
News au contraire, le tri se fait **avant nous** : ce que la requête ne demande
pas n'existe pas.

#### Ce que chaque écriture rapporte réellement

Chaque variante a été sondée **à l'exclusion des autres**, seule façon de
savoir ce qu'elle ajoute plutôt que ce qu'elle recoupe :

| écriture | EN | FR | total |
|---|---|---|---|
| `"GTA VI"` | 28 | 30 | **58** |
| `"Grand Theft Auto VI"` | 26 | 26 | **52** |
| `"Grand Theft Auto 6"` | 19 | 7 | **26** |
| `"GTA6"` attaché | 10 | 1 | 11 |
| `"GTAVI"` attaché | 0 | 0 | 0 |

#### Les deux dernières ont d'abord été écartées, puis remises

Sur ces chiffres, `"GTA6"` et `"GTAVI"` attachés avaient été retirés de la
formule. **Décision d'Antoni : les six y sont.** La mesure reste consignée
ici, parce qu'elle ne dit plus ce qu'il faut faire mais ce qu'on a accepté.

`"GTAVI"` attaché ne coûte rien d'autre qu'une requête un peu plus longue :
il n'avait rien rapporté, il ne rapportera probablement rien.

`"GTA6"` attaché, lui, porte un vrai risque, et il est identifié :

> *GTA6 $0.0002399 | Live GTA6 Price Chart Today, Swap on USDT — MEXC*
> *#gta6 — Xboxlive.fr*

**Une cryptomonnaie porte le nom du jeu.** Le fil en a d'ailleurs déjà parlé
(*« GTA 6 Leaker Is Making Millions on a Memecoin »*). Et le filtre par
mots-clés ne la rattrapera pas : la liste contient `gta6`, donc ces
articles passent. Si du cours de crypto apparaît dans le fil, c'est de là
qu'il vient, et c'est la formule qu'il faudra réduire — pas la liste de
mots-clés.

#### La formule retenue

```
("GTA 6" OR "GTA6" OR "GTA VI" OR "GTAVI" OR "Grand Theft Auto 6" OR "Grand Theft Auto VI")
```

Appliquée à **17 requêtes** : les deux flux larges, les douze `site:`,
`gta6-netflix`, `schreier`, et `rockstar-announce` — cette dernière portant
l'ancienne formule **dans l'ordre inverse**, qu'un remplacement global aurait
manquée.

**Et Reddit, oublié au premier passage.** Les dix-sept requêtes Google News
avaient été élargies, `reddit-leaks` était resté à `q=GTA 6` tout court. Le
test ne l'a pas vu parce qu'il ne regardait que Google News : il décrivait le
geste accompli, pas la règle. Mesuré avant correction, **12 articles retenus
sur 25** ; après, **23 sur 25**. Le subreddit de fuites écrit « GTA VI », pas
« GTA 6 » — *« 5th GTA VI clip has been leaked »*, *« 11th GTA VI leak is out
(nudist town) »*. Près de la moitié de cette source nous échappait. Le test
couvre désormais **toute source dont l'URL porte un paramètre de recherche**,
quel que soit le service. Soit **18 requêtes** au total.

**Deux sources restent volontairement sans filtre de jeu.** `rockstar-en` et
`rockstar-fr` interrogent `site:rockstargames.com` : tout ce qu'elles rendent
vient déjà de Rockstar. Y ajouter les écritures ne les élargirait pas, ça les
**rétrécirait** — on perdrait les articles du Newswire qui ne nomment pas le
jeu dans leur titre. C'est le seul endroit où ajouter une variante retire
quelque chose.

Gain attendu : **environ 147 articles** hors de portée jusque-là. Le nombre de
sources ne bouge pas (57), ni le nombre de requêtes, ni la cadence — seule la
question posée à Google change.

**Effet de bord assumé.** Ces flux rendent 100 entrées classées par
pertinence : élargir fait entrer d'un coup plusieurs dizaines d'articles de
l'arrière-catalogue. `MAX_ARTICLE_AGE_DAYS` écarte les archives, le reste
arrive en une salve. Le premier passage est donc à lancer **pendant la pause
nocturne**, où le robot publie sans notifier.

### Cent trente-neuf mots-clés, dont quatre-vingt-dix-sept inatteignables

`matches_keywords` fait `k in texte` : une **sous-chaîne**, pas un mot. Cette
ligne décide du sort de toute la liste.

Dès que `gta 6` correspond, `gta 6 news`, `gta 6 trailer`, `gta 6 leaked map`,
`rockstar gta 6`, `gta 6 discord` ne peuvent **rien** attraper que `gta 6`
n'ait déjà pris. Ils sont inatteignables par construction, quel que soit
l'article. La liste fournie en comptait **139 ; 97 étaient dans ce cas**.

#### Mesuré, pas supposé

Les deux listes ont été rejouées sur les **2 657 articles** du fil, en
comparant le verdict de chacune article par article :

```
articles testés                          2 657
verdict différent entre les deux listes      0
```

Zéro. Ce n'était pas un pari sur l'avenir mais une propriété démontrable :
retirer un mot-clé dont un autre est déjà une sous-chaîne ne peut pas changer
ce qui entre. **139 → 42.**

#### Ce qui travaille vraiment

Parmi les 42 restants, cinq portent l'essentiel — la colonne « seul » compte
les articles retenus par ce mot et aucun autre :

| mot-clé | présent dans | seul à retenir |
|---|---|---|
| `gta 6` | 2 210 | **1 566** |
| `gta vi` | 196 | 121 |
| `grand theft auto vi` | 221 | 80 |
| `grand theft auto 6` | 207 | 57 |
| `rockstar games` | 216 | 18 |

Le reste du travail se répartit sur huit mots qui retiennent entre 1 et 6
articles chacun.

#### Dix-huit mots n'ont jamais rien attrapé, et restent

`gta_vi`, `gtaonline6`, `rockstar next game`, `gta sequel`, `vicecity`,
`taketwo`, `rockstar san diego`… : zéro correspondance sur 2 657 articles.
Ils sont **conservés** — le filtre s'arrête au premier succès, donc ils ne
coûtent rien, et ils couvrent un vocabulaire qui pourrait apparaître (un
report, un spin-off, un nom de studio qui entre dans l'actualité).

Une faute de frappe a été corrigée au passage : `cyber leek` (le légume) est
devenu `cyber leak`. Ce qui a mécaniquement tué `cyber leak gta`, qui le
contient désormais — d'où **97 retirés et non 96**.

#### Trois mots élargissent au-delà de GTA 6, et c'est voulu

`rockstar games`, `new gta` et `take-two` font entrer des articles sur
GTA Online, GTA V et l'industrie — 26 au total. La colonne « seul à retenir »
le montre sans ambiguïté :

> `rockstar games` → *« How To Get $1.5M In GTA Online For Free This GTA RP Week »*
> `new gta` → *« A GTA 5 modder has just transformed Los Santos… »*
> `take-two` → *« How does Epic Games CEO Tim Sweeney reckon we can fight… »*

**Décision d'Antoni : on les garde.** L'écosystème Rockstar fait partie de la
veille. C'est consigné ici pour que le prochain qui trouvera un article
GTA Online dans le fil sache que ce n'est pas un défaut.

#### Un test empêche la liste de regonfler

Ajouter « gta 6 quelque-chose » alors que « gta 6 » est déjà là donne
l'illusion d'élargir la veille sans rien changer du tout. Le test vérifie
qu'aucun mot-clé n'en contient un autre, sur `KEYWORDS` comme sur
`OFFICIAL_KEYWORDS`.

### On jetait soixante-dix pour cent de ce que Google donnait

`MAX_ENTREES = 100` est le plafond par défaut : le robot ne regarde que
`parsed.entries[:30]`. Seules `rockstar-en` et `rockstar-fr` avaient été
relevées à 100, le jour où quelqu'un s'est aperçu que des pages de Rockstar
n'apparaissaient nulle part.

Or une recherche Google News rend **100 entrées**, classées par
**pertinence** et non par date. On gardait donc les 30 « plus pertinentes de
tous les temps » et on jetait les 70 autres — dont les articles récents mal
classés — **avant même de les filtrer**.

#### Ce qui l'a rendu visible

Une sonde sur la même requête bornée à sept jours par l'opérateur `when:7d` :

```
when:7d FR : 100 entrées →  30 retenues sur les 30 examinées
when:7d EN : 100 entrées →  29 retenues sur les 30 examinées
```

**Trente sur trente.** Ce n'était pas le filtre qui limitait, c'était le
plafond. Les 17 recherches Google News sont passées à `max_entrees: 100`.
Aucune requête supplémentaire : c'est la même réponse HTTP, on cesse
simplement de la tronquer.

#### Deux jumeaux `when:7d`, ajoutés à côté et non à la place

Même avec 100 entrées, le classement reste celui de la pertinence. `when:7d`
force les 100 créneaux à être récents. Les deux flux larges ont donc chacun
un jumeau borné à sept jours — **ajouté à côté**, selon la règle établie avec
Journal du Geek. La déduplication par lien fera le ménage ; ce qu'on saura
dans une semaine, en comparant leurs articles exclusifs, c'est lequel attrape
ce que l'autre manque. **57 → 59 sources.**

### Compter ce qu'une source apporte, à chaque passage

`articles_exclusifs` entre dans le journal de santé : le nombre d'articles
dont une source est la **seule porteuse**, ceux qui disparaîtraient si on la
retirait. C'est la seule mesure qui dise si une source mérite sa place, et
elle manquait — d'où l'erreur de méthode documentée plus haut, où
`days_since_last_article` avait été lu comme « on rate les articles de ce
site » alors qu'il ne répond pas à cette question.

### Un compteur faux se dénonce désormais lui-même

Le 15/09/2026, un passage a ajouté **10 articles en n'en annonçant que 3** :
Dexerto (6) et MGG (1) revenaient d'un HTTP 503 et leurs articles sont entrés
sans être comptés par `new_this_run`. Le mécanisme **n'a pas été élucidé** —
la piste est la reprise, qui remplace `resultats[fid]` après coup.

Plutôt que de deviner, l'écart est rendu bruyant : le robot compare ce qui
est réellement entré à ce qu'il annonce, et **nomme les sources en cause**
quand les deux divergent. Un compteur silencieusement faux finit par tromper
un diagnostic — c'est déjà arrivé avec `hot_count`, remis à zéro deux fois
sans que rien ne le signale.

### Quatre sources à fenêtre courte, dont une à surveiller

Certains flux natifs ne servent que dix entrées. Le robot passant toutes les
heures, un site qui publie plus de dix articles par heure, **tous sujets
confondus**, en perdrait avant qu'on les voie.

| source | fenêtre | articles GTA 6 / 30 j |
|---|---|---|
| **Game Rant** | 10 | **77** |
| Polygon | 10 | 41 |
| DualShockers | 10 | 15 |
| ActuGaming | 10 | 15 |

**Game Rant est le cas qui inquiétait** : c'est la 5ᵉ source du classement
des domaines, et sa fenêtre est la plus étroite. On ne peut pas élargir un
flux que l'éditeur sert à dix entrées ; le remède était un jumeau Google
News `site:gamerant.com`, qui rend 100 entrées.

**Fait depuis** — la source `gamerant-gnews` existe, et le relevé du
22/09/2026 la crédite de **17 articles** là où le flux natif en apporte 37.
Un tiers du Game Rant du fil passe donc par le jumeau : sans lui, il
manquerait. Cette ligne a dit « pas encore appliqué » pendant plusieurs
jours après l'avoir été — même défaut que le `paths-ignore` plus haut, et
même leçon : **la documentation d'un manque doit mourir avec le manque.**

### Publier n'est pas servir

Antoni, le 15/09/2026 : *« quand je reçois une notif il faut toujours que
j'attende que ça soit publié par GitHub pour pouvoir les voir »*.

La notification partait pourtant déjà **après** le `git push` — cet ordre-là
était bon, et corrigé depuis longtemps. Le trou était ailleurs : entre le
push et le moment où GitHub Pages sert réellement le fichier.

```
21:02:14  le robot pousse feed.json
21:02:17  ⚡ notification envoyée
21:02:15  GitHub Pages commence à construire
21:03:25  le fichier est enfin servi
```

**Jusqu'à 70 secondes d'avance sur le contenu.** Cinq déploiements mesurés
le même soir : 36, 36, 40, 42 et 70 secondes. Variable, donc un délai fixe
serait soit trop court, soit du temps perdu.

#### Deux fausses pistes, écartées par la mesure

Ce n'était **pas un cache CDN** : l'app ajoute déjà `?_t=Date.now()` à chaque
requête. Ce n'était **pas le service worker** : il n'intercepte que les
navigations, jamais les JSON. Le fichier n'était réellement pas encore servi.

#### Qui était concerné, et qui ne l'était pas

Les notifications ont deux formes, et une seule souffrait du problème :

| forme | lien | concernée |
|---|---|---|
| annonce officielle (Rockstar) | vers **l'article** | non — on lit sans passer par le site |
| récapitulatif (« 12 nouveaux articles ») | vers **l'app** | **oui** |

C'est donc la forme la plus fréquente qui envoyait sur un site périmé.

#### Ce qui a été fait

Une étape s'intercale entre la publication et les notifications : elle
interroge l'**URL publique** jusqu'à ce que `generated_at` corresponde à ce
qui vient d'être poussé.

**L'URL publique et non l'API Pages** : l'API dit « le build est fini »,
l'URL dit « le contenu est servi », et c'est la seconde qui décide de ce que
verra le téléphone. Cela n'exige d'ailleurs aucune permission
supplémentaire — le workflow reste à `contents: write`, là où l'API aurait
demandé `pages: read`.

**`feed-recent.json` et non `feed.json`** : c'est l'extrait que l'app charge
en premier. Les deux sont poussés ensemble, mais c'est celui-là que le
téléphone demande.

**Jamais bloquant.** Au bout de 180 secondes, on notifie quand même. Une
notification en retard vaut infiniment mieux qu'une notification perdue, et
c'est exactement le comportement d'avant : l'étape ne peut qu'améliorer,
jamais régresser.

#### Un test qui désignait une position plutôt qu'une chose

L'insertion a fait échouer une assertion sans rapport — « Notifier Discord
reçoit le drapeau de silence nocturne » — parce qu'elle lisait `blocs[6]` et
`blocs[7]`, des index en dur. Déplacer une étape annonçait donc un défaut
inexistant sur une autre.

Les blocs sont désormais indexés **par nom**. Un test doit désigner ce qu'il
vérifie, pas l'endroit où il se trouvait ce jour-là. Et l'ordre de la
nouvelle étape est lui-même verrouillé : après la publication, avant les deux
notifications — sinon elle attend une version qu'on n'a pas encore poussée,
ou elle ne sert à rien.

### Le push est mort en silence, et rien ne l'a dit

Le 15/09/2026 au soir : *« pourquoi je reçois plus la notif push sur mon
tel »*. Le journal du passage répondait en trois lignes :

```
[push] envoi à 1 appareil(s) : 🎮 3 nouveaux articles GTA 6
[push] abonnement #1 expiré (HTTP 410)
[push] 0/1 notification(s) envoyée(s), 1 abonnement(s) expiré(s)
```

L'abonnement Web Push avait expiré — très probablement lors de la
réinstallation de l'app le soir même, pour la nouvelle icône. Le robot
envoyait correctement ; c'est le destinataire qui n'existait plus.

**Et le job est resté vert.** Le signal de vie ne couvre pas ce cas : il dit
« le robot tourne », pas « les notifications arrivent ». Le défaut est resté
invisible trois heures, jusqu'à ce qu'on le demande.

#### Une alerte sur un canal qui, lui, fonctionne encore

Quand **tous** les abonnements sont expirés, le robot le dit maintenant sur
Discord. Seulement quand tous : avec plusieurs appareils, en perdre un est
banal. Discord marche quand le push est mort — c'est donc le bon endroit
pour annoncer que le push est mort.

#### Tester un VRAI envoi, depuis l'app

Le bouton « Tester l'affichage » n'affiche qu'une notification **locale** :
il montre à quoi ça ressemble, il ne prouve rien sur la chaîne d'envoi. Et
il ne peut pas faire mieux — une vraie push doit être signée avec la **clé
privée VAPID**, qui vit dans un secret et doit y rester. Dans la page,
n'importe qui pourrait notifier l'appareil.

Le seul chemin honnête est donc :

```
app  →  GitHub (test-push.yml)  →  push_notify.py --test
     →  signature VAPID  →  service de push  →  téléphone
```

Un second bouton, **« Tester l'envoi »**, déclenche ce workflow par la
même API que « Relancer le robot ». Il n'apparaît que si un jeton GitHub est
enregistré : proposer un bouton qui ne peut pas marcher, c'est promettre un
test et rendre une erreur.

**Trois choix qui font la différence :**

- **Un workflow séparé, pas une option du robot.** Il tourne en quelques
  secondes au lieu de trois minutes, et il est en `contents: read` — tester
  ne doit rien publier.
- **Le run ÉCHOUE quand aucune notification ne part.** C'est tout l'intérêt :
  un test qui reste vert alors que rien n'arrive ne teste rien. C'est
  exactement le défaut qu'on venait de vivre, et le voilà transformé en
  signal rouge.
- **L'app ne prétend pas savoir si c'est arrivé.** Seul le téléphone le
  sait. Le message dit donc : *« si rien n'arrive d'ici une minute,
  l'abonnement est expiré »* — plutôt que d'annoncer un succès non constaté.

#### Ce que ça ne règle pas

Le secret `PUSH_SUBSCRIPTIONS` doit toujours être **mis à jour à la main**
après chaque réabonnement : l'app ne peut pas écrire un secret GitHub, ça
demande des droits d'administration et un chiffrement côté client. Le bouton
ne supprime pas cette friction — il dit seulement tout de suite quand il faut
s'y coller, au lieu de le laisser découvrir trois jours plus tard.

### Les flux RSS de YouTube ne répondent plus — six sondes pour l'établir — 16/09/2026

Le journal de santé a montré deux sources `cassee` le même matin :
`rockstar-youtube` et `rockstarmag-youtube`, toutes deux en **HTTP 404**,
tombées **à la même seconde** (`02:02:27`) d'après le journal de silence.
Deux flux qui meurent au même passage, ce n'est pas deux identifiants de
chaîne devenus invalides.

**Le diagnostic est différentiel, pas une intuition.** Première sonde : les
deux URL en panne, plus la chaîne YouTube **officielle de YouTube**
(`UCBR8-60-B28hp2BmDPdntcQ`), pour distinguer « ces deux chaînes sont
mortes » de « l'endpoint ne répond plus ». Les trois : 404. Deuxième sonde,
sur la variante documentée `playlist_id=UU…` — l'identifiant de chaîne dont
le préfixe `UC` devient `UU`, qui désigne la playlist des mises en ligne.
Les trois : 404 aussi.

Six sondes, trois chaînes, deux formes d'URL, zéro réponse.
`youtube.com/feeds/videos.xml` est injoignable depuis le runner. Ce n'est
pas notre configuration : les rapports publics de 404 et 500 sur ces flux
s'accumulent depuis des mois. **Aucune URL de remplacement ne sera donc
proposée** — la règle vaut ici plus que jamais : on ne devine pas une
adresse, et il n'y en a aucune à valider.

**Ce que ça coûte réellement, et ce que ça ne coûte pas.** Un trailer GTA 6
ne sera pas manqué : il paraît toujours sur le Newswire de Rockstar, couvert
par `rockstar-en` et `rockstar-fr` dont les liens sont sur un domaine
officiel, et toute la presse le reprend dans la minute. Ce qui est perdu,
c'est le lien YouTube *lui-même* — cinq des trente-huit articles officiels
du fil. Les deux sources restent en place, marquées `cassee` : elles ont
alerté une fois sur Discord et ne le répéteront pas, `alertee` empêchant la
répétition.

### Le rendement réel des sources, mesuré plutôt que supposé

Relevé du 16/09/2026, 2786 articles :

```
2638 articles exclusifs
├─ 1771 (67 %) via Google News   ← 21 sources sur 59
│   └─ 1191 (45 %) par gnews-fr + gnews-en SEULES
└─  867 (33 %) via les 38 sources natives
```

Les trente-huit flux natifs ressemblent à l'ossature du projet ; ils en
portent un tiers. Deux URL Google News en portent presque la moitié. Ce
n'est pas un défaut à corriger — c'est le rapport de force à connaître, et
il justifie de garder les natives même peu productives : elles sont
l'assurance contre le jour où Google News étrangle le robot, ce qui est
déjà arrivé.

**Le doublage a rendu son verdict.** Deux sites sont interrogés deux fois,
en natif et par une recherche Google News : `jdg` apporte 14 articles
exclusifs contre 3 pour `jdg-natif`, `frandroid` 16 contre **0** pour
`frandroid-natif`. Le jumeau Google News gagne les deux fois. Trois sources
n'apportent aucun article exclusif — `dexerto-fr`, `frandroid-natif`,
`tweaktown` — et une quatrième, `take2-ir`, se tait depuis 83 jours, ce qui
est normal : ce sont des communiqués d'investisseurs, un par trimestre.
Rien n'est retiré : les chiffres sont posés, la décision appartient au
propriétaire du fil.

### Deux requêtes qui s'annonçaient ciblées et ne l'étaient pas

`Rockstar Games (annonces)` et `GTA 6 x Netflix` portaient des noms
promettant un flux précis. Ce n'en étaient pas : Google News ne traite ni
`Netflix` ni `(announce OR reveals OR confirms)` comme un filtre, seulement
comme un poids de pertinence. Au relevé, elles apportaient **261 et 127**
articles exclusifs — la 3ᵉ et la 5ᵉ source du fil, ce qu'aucune requête
réellement ciblée ne pourrait faire. Elles s'appellent désormais
`Google News (Netflix)` et `Google News (annonces Rockstar)`, comme les
quatre autres recherches.

**Une crainte vérifiée, et écartée.** `rockstar-announce` porte
`official: True`, et l'on pouvait redouter que 127 articles quelconques
entrent marqués « officiels » — de quoi lever la pause nocturne pour rien.
C'est faux : `statut_officiel()` exige que **le lien** soit sur un domaine
Rockstar ou Take-Two, la déclaration de source ne suffit jamais. Au
comptage : 38 articles officiels dans le fil, tous sur `rockstargames.com`,
`support.`, `store.`, `youtube.com` ou `ir.take2games.com`, et **aucun**
venu de cette source. Le drapeau ne fait là que durcir son filtre de titres.

**Renommer une source casse ses archives — le test l'a dit avant le
lecteur.** Les 421 articles déjà stockés portaient l'ancien nom et
devenaient orphelins : plus reliés à leur source, ni pour l'onglet, ni pour
le journal de santé. La mécanique existait (`SOURCES_RENOMMEES`) mais
exigeait un **domaine de preuve**, ce qui identifie une source-éditeur comme
RockstarMag et ne peut rien identifier pour une recherche d'agrégateur, qui
renvoie vers cinquante domaines. Le domaine accepte maintenant `None`, avec
sa justification : ce qui fait preuve alors, c'est que l'ancien nom
n'appartenait qu'à cette source et n'existe plus nulle part.

Et la fonction avait un angle mort : elle corrigeait `source` mais pas les
`extraSources`, qui portent le même nom. Vingt-cinq reprises seraient
restées étiquetées à l'ancien, sous un article dont le nom, lui, aurait été
corrigé. Deux tests verrouillent désormais les deux sens, plus un troisième
qui exige que chaque renommage déclaré vise une source existante — une table
pointant vers un nom absent de `FEEDS` ne rebrancherait rien, en silence,
tout en faisant passer le contrôle d'orphelins.

### Deux trous dans les workflows

`sonde.yml` était le seul des quatre sans `timeout-minutes`. Sans, GitHub
applique son défaut de **360 minutes** : une sonde qui pend sur un site qui
accepte la connexion et ne répond jamais brûle six heures de quota pour
rien. `FETCH_TIMEOUT` valant 20 s par adresse, dix minutes tiennent très
large.

`checks.yml` n'avait pas de `concurrency`. Deux pushes rapprochés lançaient
deux suites complètes en parallèle, la première portant déjà sur du code
remplacé. Elle est maintenant annulée — l'inverse exact du choix fait pour
`update-feeds`, et pour la raison inverse : là-bas une exécution abandonnée
perdrait des articles, ici elle ne perd qu'un verdict périmé.

**Ce qui a été vérifié et tient.** Les quatre workflows installent depuis
`requirements.txt`, qui épingle les versions à l'exact. Les permissions sont
déclarées explicitement et au minimum — `contents: read` sur trois, `write`
seulement sur `update-feeds`. `sonde.yml` passe la saisie utilisateur par
l'environnement et jamais interpolée dans le `run:`, donc pas d'injection
shell. Le signal de vie est en `if: always()` et ne peut pas faire échouer
le job. La fenêtre nocturne est calculée en heure de Paris et **laisse
passer** si elle échoue. Les actions restent épinglées au tag majeur et non
au SHA : ce sont des actions GitHub first-party, le compromis est assumé et
noté ici pour qu'il soit un choix et non un oubli.

### Un plafond qui ne protégeait plus rien — 16/09/2026

`MAX_ENTREES` valait 30. Le commentaire qui l'accompagnait disait pourquoi :
le plafond existait « pour le coût, pas pour la pertinence », le coût étant
le décodage des liens Google News — 51 s sur un passage de 74 s.

Sauf que `decode_google_news_link()` rend la main immédiatement quand l'URL
ne contient pas `news.google.com` :

```python
if not HAS_DECODER or "news.google.com" not in url:
    return url
```

**Ce coût ne concerne donc que les flux Google News** — lesquels déclarent
tous `max_entrees` explicitement et ne dépendaient déjà plus de ce défaut.
Sur les 38 sources natives, le plafond ne faisait économiser *rien* : leurs
entrées sont déjà téléchargées et analysées dans la même réponse HTTP, et
leurs liens n'ont aucun décodage à subir. On jetait du contenu déjà payé.

Relevé sur les douze derniers passages, douze fois sur douze, **18 sources
étaient tronquées** :

| Source | Exclusifs | Offertes | Examinées | Perdues/passage |
|---|---|---|---|---|
| `eurogamer` | 33 | 100 | 30 | **70** |
| `rps` | 14 | 100 | 30 | **70** |
| `pcgamesn` | 4 | 75 | 30 | 45 |
| `pcgamer`, `gamesradar`, `ginjfo`, `gamekult`, `tomshw`, `gameinformer`, `dexerto-fr` | — | 50 | 30 | 20 |
| `vgtimes` | 68 | 40 | 30 | 10 |
| `ignfr` | 33 | 40 | 30 | 10 |
| `gta6times` | 21 | 39 | 30 | 9 |

**Le gain n'est pas quotidien, et il faut le dire.** À un passage par heure,
aucune de ces sources ne publie trente articles entre deux passages : la
troncature ne coûtait presque rien en régime normal. Le gain est en cas de
PANNE — si le planificateur externe tombe et que le filet de 3 h prend le
relais, une source active peut avoir dépassé trente, et ces articles-là sont
perdus pour de bon puisqu'un flux n'expose qu'une fenêtre récente.

L'autre justification — « les entrées 30 à 100 sont du bruit » — vaut pour un
flux d'actualité générale et tombe pour `gta6times` et `gtaboom`, qui ne
parlent que de GTA 6 : chez eux, l'entrée 35 vaut l'entrée 3.

Le plafond garde son utilité de garde-fou — un flux malformé annonçant cent
mille entrées ne fera pas exploser le passage — mais son défaut vaut
désormais 100, comme les flux Google News.

**Mesuré au premier passage, et la prédiction était fausse.** On annonçait un
rattrapage massif : jusqu'à 219 entrées de plus examinées d'un coup, donc une
notification anormalement grosse. **Elle n'est jamais venue.** Le premier
passage avec le plafond relevé a rapporté **+6 articles**, au milieu d'une
nuit qui allait de +1 à +7. Au passage suivant, les dix sources natives
concernées ont examiné **119 entrées de plus** et en ont tiré **zéro** article.

Le raisonnement était faux sur un point : les entrées 30 à 100 d'un flux ne
contiennent pas des articles jamais vus, mais surtout des articles **déjà
dans le fil** — attrapés quand ils étaient en position 1 à 30 aux passages
précédents, ou remontés par Google News entre-temps. La déduplication les
écarte.

Le changement reste justifié, mais pour la seule raison invoquée plus haut :
**le cas de panne**. En régime normal il ne rapporte rien, et c'est ce qu'il
fallait annoncer plutôt qu'une avalanche.

### L'accordéon des soucis, et le piège qu'il a révélé

Deux sources cassées pour de bon — les flux RSS de YouTube — affichaient
**deux lignes orange permanentes** sous la console : `57/59 sources` puis
`2 cassées : Rockstar Games (YouTube), RockstarMag (YouTube)`. Un signal qui
ne s'éteint jamais cesse d'être un signal : on finit par ne plus le voir, et
la vraie panne du jour s'y noie.

Le compteur de sources devient donc le bouton qui replie la ligne du détail.
Il n'est un bouton **que** s'il y a quelque chose à déplier — quand tout
répond, il reste du texte, sans chevron ni soulignement : un chevron qui
n'ouvre rien est une promesse vide. Dix-sept pixels visibles, quarante-sept
cliquables par pseudo-élément, comme le « ? » des paramètres : la ligne fait
10 px de police, l'épaissir pour atteindre 44 px déformerait la console.

**Le comportement retenu : replié, mais rouvert tout seul dès que la liste
des soucis change.** Les deux YouTube restent muettes une fois acquittées ;
le jour où une troisième source tombe, la signature change et la ligne se
rouvre d'elle-même. On se débarrasse du bruit sans devenir aveugle au neuf.
Une seule valeur stockée — la signature acquittée — plutôt qu'un booléen de
plus à tenir en cohérence avec elle : replier acquitte, déplier efface
l'acquittement.

**Le piège, invisible à la lecture.** `storageGet` ne renvoie pas la chaîne
stockée mais `{ value }` ou `null` — c'est ce qui permet de distinguer « rien
de stocké » de « chaîne vide stockée ». Le premier jet écrivait
`storageGet(...) || ""`, qui rend donc un **objet**, jamais égal à une
signature : l'accordéon se rouvrait à chaque rechargement. Le code se lisait
parfaitement ; seul le contrôle navigateur l'a vu, en rechargeant la page
après avoir replié.

**Et un test qui échouait sur sa propre documentation.** Le contrôle qui
interdit ce `|| ""` le trouvait dans le commentaire expliquant justement
pourquoi il est interdit. Les commentaires sont retirés avant la recherche —
même travers que le mot « muette » sur la ligne du haut, rencontré plus tôt.

Dix-huit vérifications dans un vrai Chromium, à 320 et 390 px : ouvert au
premier affichage, la ligne du haut qui ne déborde pas, la zone de clic à
47 px, le repli qui tient au rechargement, et la réouverture automatique
quand une source de plus tombe.

### Les flux YouTube tombent toutes les nuits — 16/09/2026

Remarque du propriétaire du fil : *« j'ai l'impression que YouTube ça plante
tt les nuits en ce moment et sa revient dans la matinée »*. C'est exact, et
c'est plus régulier que « en ce moment » : **seize nuits d'affilée** depuis
le 1ᵉʳ septembre.

Chaque passage du robot étant un commit de `feed.json`, l'historique donne
l'état heure par heure. Relevé sur les 770 passages remontant au 25/08 :

| | |
|---|---|
| Première panne | entre **04h01 et 08h01**, jamais avant 04h |
| Retour | au passage de **09h**, tous les jours |
| Amplitude typique | **3 à 4,5 h** |
| Journées touchées | **17 sur 19** observées |

Répartition par heure de Paris : `04h→10 · 05h→14 · 06h→20 · 07h→26 ·
08h→22`, et **zéro panne entre 09h et 03h**.

**Ce sont bien les flux YouTube, pas le runner** — c'est la vérification qui
comptait, puisqu'une panne réseau du runner ferait tomber tout le monde
ensemble. Dans cette fenêtre, sur 197 pannes de sources relevées, **172 sont
les deux flux YouTube**. Les 25 restantes se concentrent sur trois dates —
30/08, 03/09, 04/09 — où une vingtaine de sources Google News tombent d'un
coup sur un seul passage : le profil d'un throttling ponctuel, pas d'une
panne nocturne récurrente.

**Ce que ça coûte : rien, ou presque.** Les flux YouTube renvoient les quinze
dernières vidéos. Une vidéo publiée à 5h du matin est donc toujours dans le
flux au passage de 9h : elle arrive en retard de quelques heures, jamais
perdue. Il faudrait que Rockstar publie seize vidéos entre 4h et 9h pour
qu'une seule tombe hors fenêtre.

**Et aucune alerte ne part, ce qui est correct.** `DEAD_SOURCE_HOURS` vaut
24, les pannes durent 3 à 4 h : le seuil n'est jamais atteint. Seize nuits
sans une seule alerte Discord. C'est précisément ce que ce seuil sert à
éviter — être réveillé pour un incident passager qui se répare seul. Le seul
indice reste la console, entre 4h et 9h, d'où le fait que ça se remarque le
matin.

**Rien n'a été codé pour ça, délibérément.** Le problème est cosmétique, se
répare seul et ne perd aucun article. Toute machinerie qui apprendrait à
« connaître » cette fenêtre nocturne risquerait de masquer une vraie panne le
jour où elle arriverait au même moment. Cette section existe pour qu'on ne
réenquête pas dans trois semaines en croyant découvrir quelque chose.

**Une limite de l'accordéon, notée au passage.** Il se replie et ne se rouvre
que si la LISTE des soucis change. Or certaines nuits une seule des deux
sources tombe, et certaines passent par un HTTP 500 qui les classe « muette »
avant de repasser « cassée ». La signature varie donc d'une nuit à l'autre :
l'accordéon se rouvrira certains matins, pas tous. Il n'épargne pas
complètement ce bruit-là.

**Deux erreurs de méthode, parce qu'elles se reproduiront.** Le premier
calcul de fenêtre annonçait 23 h de panne par jour — il cherchait
`"status": "ok"` avec une espace que le JSON n'écrit pas, si bien que tout
paraissait cassé. Le second ne trouvait plus rien du tout : il cherchait le
bloc `rockstar-youtube` dans TOUT le fichier et tombait sur le premier, celui
de la liste `sources`, qui décrit la source et ne porte aucun `status`. Les
deux fois, c'est la contradiction avec un relevé déjà fait qui a alerté —
pas la relecture du code. **Extraire d'un JSON à la regex demande de viser la
zone, ici `sources_health`, jamais le fichier entier.**

### Cinq chantiers d'un coup — 16/09/2026

**Le sous-comptage de `new_this_run` : ce n'était pas un bug.** Soupçon
ouvert depuis des jours, instrumenté sans jamais crier. Plutôt que de lire
des journaux, on a mesuré le SYMPTÔME sur les 770 passages de l'historique :
un passage où le total d'articles grimpe de plus que le nombre annoncé de
nouveaux est nécessairement un sous-comptage. **Cinq cas sur 769.** Et les
cinq ont un écart de **0,0 minute** avec le passage précédent : deux commits
portent le même `generated_at`. C'est la signature du chemin de fusion — le
push est rejeté, `merge_feed.py` unit les deux résultats, et le second commit
porte l'union des articles mais le `new_this_run` de son seul passage. Aucun
article perdu, aucun défaut de comptage.

Corollaire à retenir : **l'instrumentation posée pour ça cherche au mauvais
endroit.** Elle vérifie la cohérence à l'intérieur d'un passage, alors que
l'écart n'apparaît qu'entre deux commits, à la fusion. Elle ne criera jamais
pour ce motif.

**Sept mots-clés sur quarante-deux étaient des formes d'URL.** Le filtre lit
le titre et le résumé. Or `gta-6` apparaît dans **2110 liens** et **zéro**
titre ; idem pour `gta-vi`, `grand-theft-auto-6`, `gta_6`, `gta_vi`,
`taketwo`, `take2`. On écrit « GTA 6 » et « Take-Two » dans une phrase,
jamais `gta_6`. Même défaut que les 97 mots retirés plus tôt, en plus
discret : **42 → 35**.

Les quinze autres qui n'attrapent rien — `gta sequel`, `cyber leak`,
`rockstar san diego` — sont **gardés**. Ils sont spéculatifs mais plausibles
dans un titre ; les retirer risquerait un article pour aucun gain.

**Game Rant est doublé.** 4ᵉ source native du fil en articles exclusifs (59),
mais son flux n'expose que **dix entrées** pour environ 77 articles publiés
sur trente jours — relever `MAX_ENTREES` n'y change rien, c'est le flux
lui-même qui est court. Sondé avant d'être ajouté : 100 entrées,
16 pertinentes, la plus récente d'un jour. Doublé et non remplacé, le flux
natif restant plus rapide. **59 → 60 sources.**

**Les onglets d'articles disaient leur sélection par la seule couleur.** Même
défaut que ceux du panneau Paramètres, corrigés la veille — et pas repris
avec eux. Un helper `marqueActif()` pose maintenant la classe ET
`aria-pressed`, pour les onglets, les langues et les filtres. `aria-pressed`
et non `role="tab"`, pour la même raison qu'au panneau : de vrais onglets
ARIA exigent la navigation aux flèches.

**Les soixante bascules sont rangées en cinq familles repliées.** Officielles,
Spécialisées GTA, Recherches Google News, Presse francophone, Presse
anglophone — par ordre de PRIORITÉ, puisqu'une source peut cocher plusieurs
cases (RockstarMag est spécialisée et francophone). Le filtre commande les
familles : il **ouvre** celles où il trouve, masque les autres, et referme
tout quand on le vide. Chercher dans des sections repliées sans les ouvrir
ne montrerait rien, et le filtre aurait l'air cassé.

**Le piège, invisible dans le HTML.** L'attribut `hidden` ne vaut qu'un
`display:none` de la feuille du **navigateur**, que n'importe quelle règle
d'auteur bat. Avec `display:grid` sur le corps de famille, les sections se
rendaient **dépliées** malgré leur `hidden` : la liste faisait **3244 px**,
soit pire qu'avant le regroupement. Une ligne — `.src-famille-corps[hidden]
{display:none;}` — et elle retombe à **244 px**. Le HTML se lisait
parfaitement ; seul le contrôle navigateur l'a vu.

### Où part vraiment le délai, et pourquoi on n'y touche pas — 16/09/2026

Question posée : « il y a rien qui pourrait accélérer et améliorer la
récupération des articles ». La réponse tient dans une mesure, faite sur les
soixante derniers passages en confrontant la date de publication de chaque
article à l'heure du passage qui l'a vu en premier.

```
Délai médian publication → vu par le robot : 54 min
  ├─ cadence entre deux passages ......... 47 min (médiane)
  ├─ durée du passage ..................... 1 min 38
  └─ attente que GitHub Pages serve ....... 36 s
```

**Le code pèse 3 % du délai.** Optimiser la collecte — plus de fils, moins de
décodages, un cache — gagnerait quelques secondes sur cinquante-quatre
minutes. C'est le genre de travail qui a l'air sérieux et ne sert à rien : il
n'a pas été proposé.

Et les flux natifs comme les recherches Google News ont la **même médiane**
(~80 min sur une fenêtre de mesure plus large). Ce n'est donc pas un type de
source qui traîne : la cadence commande les deux.

**Le seul levier réel sur la vitesse coûte cher.** Passer la cadence de ~1 h
à 30 min couperait le délai de moitié, mécaniquement. Mais sur les quatorze
derniers passages, **treize avaient du nouveau** : une notification part donc
presque à chaque fois, de l'ordre de vingt-cinq à trente par jour. Doubler la
cadence double ce nombre. L'arbitrage — voir les articles vingt-cinq minutes
plus tôt contre deux fois plus de notifications — appartient au propriétaire
du fil, qui a tranché : **on ne touche à rien.**

**Un levier qui marche, lui, et qui est prouvé.** Le jumeau Google News de
Game Rant a rapporté **cinq articles exclusifs à son premier passage**. Huit
autres sources natives ont le même profil — fenêtre courte, forte production
— dont `ign` (56 exclusifs, 20 entrées), `rockstarintel` (55, 10 entrées),
`polygon` (36, 10 entrées) et `insider` (26, 10 entrées).

Elles ne sont **pas classables sans sonder** : une fenêtre de dix entrées ne
coûte rien si le site publie peu, et cher s'il publie beaucoup comme Game
Rant. La donnée qui distingue les deux n'est pas dans le journal de santé.
La marche à suivre reste celle qui a servi pour Game Rant — sonder, puis
n'ajouter que ce qui rapporte. Non fait, en attente d'une décision.

**Ce qu'aucun réglage ne rattrapera.** La queue des délais est longue : 5 h au
75ᵉ centile, 8 h au 90ᵉ. Ces articles-là ne sont pas en retard chez nous —
ce sont des sites qui publient tard dans leur propre flux, ou Google News qui
les indexe tard.

**Une mesure jetée en chemin, parce qu'elle ne valait rien.** Un classement
des sources natives « les plus lentes » avait été produit : effectifs de un ou
deux articles, et deux sources renommées mal classées parce que les anciens
articles portent l'ancien nom. Une médiane sur un article n'est pas une
médiane. Elle n'a pas été présentée comme un résultat.

### Les contrôles rendus entrent dans le dépôt — 16/09/2026

Jusqu'ici, deux façons de vérifier coexistaient sans être logées à la même
enseigne. `test_pipeline.py` **lit les fichiers** et vérifie que ce qui est
écrit est cohérent : 1250 vérifications, versionnées, exécutées à chaque
pull request. Les contrôles qui **ouvrent l'app dans un vrai navigateur**,
eux, n'existaient que le temps d'une session de travail, lancés à la main,
puis perdus.

Or ils ont trouvé, en une seule journée, **quatre défauts que la lecture du
code ne pouvait pas voir** :

| Défaut | Ce que le code disait | Ce que l'écran montrait |
|---|---|---|
| `storageGet(...) \|\| ""` | comparaison de chaînes | comparaison d'un **objet** — l'accordéon se rouvrait à chaque rechargement |
| `display:grid` contre `hidden` | « cache cette section » | section **dépliée**, liste à 3244 px au lieu de 244 |
| entête `sticky` à marge négative | entête en haut | entête **recouvrant** 4 px de chaque onglet |
| `display` inline sur un bouton | libellé centré | libellé **8,5 px trop haut** |

**Deux de ces défauts rendaient l'app pire qu'avant** le changement censé
l'améliorer. Aucun n'était visible autrement qu'en ouvrant un navigateur.

`test_navigateur.py` entre donc dans le dépôt, et `checks.yml` l'exécute.
**37 contrôles**, à 320 et 390 px.

**Ce qui y entre, et ce qui n'y entre pas.** Un contrôle qui échoue au hasard
est pire que pas de contrôle : on apprend à ignorer le rouge, et le vrai
défaut passe avec le reste. N'y entrent donc que des mesures
**déterministes** — géométries, présences, attributs :

- tailles de cible au doigt, **zone** comprise et non boîte visible ;
- rien ne déborde, ni de la page ni d'un bouton ;
- ce qui est annoncé replié l'est vraiment ;
- libellés centrés au pixel, avec de l'air autour ;
- exactement une bascule enfoncée par groupe, chaque champ nommé ;
- trois gestes choisis **parce qu'ils ont déjà cassé**.

Pas de comparaison d'images, pas d'animations, pas de délais, pas de parcours
à plusieurs étapes, rien qui dépende du réseau — le fil est servi par un
serveur local jetable.

**Playwright vit dans `requirements-dev.txt`, pas dans `requirements.txt`.**
Ce dernier est installé par les quatre workflows, dont celui du robot qui
tourne vingt-quatre à quarante-huit fois par jour : lui faire télécharger un
navigateur à chaque passage aurait été un coût quotidien pour un usage
limité aux pull requests. La règle « tout workflow installe depuis un fichier
de dépendances » est du coup étendue : elle vérifie maintenant qu'**aucun**
`pip install` ne cite un paquet en clair, et que les trois autres workflows
n'installent pas les dépendances de test.

**Deux critères recadrés en écrivant ces contrôles, et la nuance compte.**
Le premier jet refusait le bouton « ? » parce que son texte « sortait de sa
boîte » : c'est un rond de 24 px contenant un caractère, la notion ne s'y
applique pas. Le second exigeait 13 px d'air autour de chaque libellé, et
butait sur les onglets du panneau, qui n'en ont que **6 à 320 px**. Ce n'est
pas un défaut : un onglet est un tiers de rangée, son air dépend de la
largeur de l'écran et non d'un choix de rembourrage — rien ne déborde ni ne
se décentre. Le seuil d'air ne vaut donc que pour les boutons dimensionnés
par leur contenu ; le contrôle de débordement, lui, continue de surveiller
les onglets.

Recadrer un critère n'est pas l'affaiblir quand on peut dire **pourquoi** il
ne s'appliquait pas. Le dire est la condition.

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
dans les quatre workflows d'alors — trois depuis la suppression du
récapitulatif hebdomadaire, la propriété tient toujours (`contents: read`
partout sauf le robot) — et le fichier de totaux est écrit dans
`$RUNNER_TEMP`, hors du dépôt.

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

  Quand la réponse est **PAS UN FLUX**, la sonde va plus loin : elle relit la
  page et en extrait les `<link rel="alternate">`, c'est-à-dire les flux que
  le site **déclare lui-même**. Lui passer une page d'accueil suffit donc à
  obtenir la bonne adresse, au lieu d'essayer des chemins au hasard. C'est
  ainsi qu'ont été trouvées celles de Clubic et de TweakTown le 15/09/2026.

- **Le workflow `sonde.yml`** — la même sonde, mais lancée depuis un runner
  GitHub : onglet Actions → *Sonder des flux candidats* → *Run workflow*, on
  colle les adresses séparées par des espaces.

  **C'est souvent le seul chemin praticable, et l'oublier coûte cher.** Le
  15/09/2026, trois sources devaient être diagnostiquées depuis une machine
  dont le réseau bloquait `news.google.com` : la sonde en ligne de commande y
  répondait INJOIGNABLE pour une raison qui n'avait rien à voir avec les
  sources. Le diagnostic a donc été **déduit** du code et des données
  enregistrées, alors qu'il pouvait être **mesuré** — ce workflow existait
  déjà. La conclusion s'est trouvée juste ; la méthode ne l'était pas.

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

- **Historique glissant, pas permanent** — une profondeur de 15 jours
  (`MAX_HISTORY_DAYS` dans `feed_store.py`), bornée entre 1 500 et 4 000
  articles, pas un vrai historique complet depuis toujours. Les officiels
  Rockstar, RockstarMag et les actualités majeures y échappent.
- **Poids de `feed.json`** — 1,31 Mo aujourd'hui, 3,4 Mo au plafond dur, et
  jamais au-delà. L'ouverture de l'app n'est de toute façon pas concernée
  (elle charge `feed-recent.json`), mais toute recherche déclenche le
  téléchargement de l'historique complet.
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
  GTA 6 » se confirme donc sur deux formats de flux différents.

  **La source a d'abord été gardée, puis retirée le 15/09/2026.** L'argument
  « elle ne coûte qu'une requête » tenait tant que le doute portait sur le
  flux. Il ne tenait plus après une troisième mesure, 781 jours après le
  dernier article vg247.com : ce n'est plus une source qui dort, c'est une
  source qui ne couvre pas le sujet. Xbox Wire est partie avec elle, pour
  la raison inverse et aussi nette — **zéro article depuis son ajout**,
  jamais un seul. Voir *Retirer une source, et ce qu'elle laisse derrière
  elle*.
- **Miniatures Google News** — un léger pourcentage d'articles n'a pas de
  miniature si le site source bloque les robots ou n'a pas de balise
  exploitable. Comportement normal, pas un bug.
- **Fichier HTML monolithique** — `index.html` regroupe CSS, HTML et JS
  dans un seul fichier de ~3900 lignes plutôt que d'être séparé en
  plusieurs fichiers. Choix assumé : ça simplifie l'upload manuel (un seul
  fichier à remplacer au lieu de plusieurs à garder synchronisés), au
  prix d'un fichier plus long à parcourir si besoin d'y retoucher.

## Décisions prises, et ce qu'elles engagent — 22/09/2026

Trois décisions issues d'un audit complet. Elles sont ici plutôt que dans un
fil de discussion parce que c'est le seul endroit où elles survivent.

### Le projet continue après la sortie — c'est une veille, pas un compte à rebours

GTA6_WATCH ne s'arrête pas le 19/11/2026 : il devient une veille du jeu
(mises à jour, DLC, GTA Online).

**Cela renverse un arbitrage.** Le DÉCOUPAGE de l'historique en fichiers
mensuels, plutôt qu'un seul `feed.json` — à ne pas confondre avec la
pagination de l'affichage, qui existe déjà et sert tout autre chose — avait
été écarté comme trop lourd pour un projet visant une date. Ce motif tombe.
Un fil qui tourne pendant des années finira par rendre le plafond glissant
insatisfaisant quel que soit son réglage : chercher ce qui s'est dit d'un DLC
six mois plus tôt ne rentrera jamais dans quinze jours. Ce n'est pas décidé,
mais ce n'est plus « trop gros pour ce projet ».

Deux conséquences à prévoir. **Les sources devront être revues** : celles qui
couvrent une attente ne sont pas celles qui couvriront des correctifs et du
multijoueur. Et **la profondeur de quinze jours** sera à reconsidérer, le
rythme d'une veille n'ayant rien à voir avec celui d'un compte à rebours.

### Fait le 22/09 : le filtre d'entrée est aligné sur la fenêtre gardée

**Le problème.** `MAX_ARTICLE_AGE_DAYS` acceptait quarante-cinq jours à
l'entrée, alors que l'historique n'en garde que la profondeur visée. Tout
article situé entre les deux était réabsorbé à chaque passage, pris pour
neuf, puis élagué aussitôt. Le correctif du matin l'avait fait taire — il
n'était plus ni annoncé ni compté — mais il était toujours décodé et
dédupliqué pour rien.

**Le correctif.** `feed_store.plancher_de_retention()` calcule la date du
plus vieil article ordinaire que l'historique garde effectivement, et
`merge_results` refuse à l'entrée tout ce qui est plus ancien. Le plancher
ne se calcule que si l'historique est plein (`len(items) >=
taille_historique_visee(items)`) : tant qu'il ne l'est pas, rien n'est
élagué, donc rien ne doit être refusé. Une première version appelait
`cap_items` pour voir ce qui serait coupé — elle ne s'est jamais déclenchée,
puisque l'historique stocké est déjà plafonné : il n'y avait jamais rien à
couper.

**Les trois familles protégées passent toujours**, quelle que soit leur
date : les articles officiels de Rockstar, ceux de RockstarMag, et les
sujets chauds (au moins trois sources). Le filtre appelle exactement le même
`item_protege()` que l'élagage — c'est ce qui garantit que les deux ne
peuvent pas diverger.

**Vérifié par équivalence, pas par raisonnement.** Le vrai historique a été
rejoué deux fois, avec et sans le filtre :

```
sans filtre : 1500 articles publiés
avec filtre : 1500 articles publiés, 150 refusés à l'entrée
FICHIER PUBLIÉ IDENTIQUE : True
  l'officiel vieux est conservé  : True
  le RockstarMag vieux conservé  : True
  les 20 récents sont conservés  : 20/20
```

**Ce que ça rapporte, honnêtement.** Pas les vingt secondes annoncées plus
haut : le travail évité est du décodage de texte, pas du réseau, et il se
compte en quelques secondes par passage. Le vrai bénéfice est ailleurs — le
pipeline arrête de faire un travail qu'il défait aussitôt, et le nombre
d'entrées refusées est désormais imprimé dans le journal, ce qui rend le
gaspillage visible au lieu d'être à déduire. Ce gain grandit avec le volume :
le plafond étant adaptatif, la fenêtre rétrécit le jour de la sortie, et ce
sont les sources LENTES qui alimentent l'écart — **29 sources sur 60**
produisent dix articles ou moins sur la fenêtre, donc leurs cent entrées
couvrent des mois.

**Pourquoi maintenant et pas en octobre**, comme il était écrit ici le matin
même : attendre n'achète rien. Le correctif touche le filtre d'entrée,
l'endroit où une erreur ne se voit pas — un article qui n'arrive jamais ne
manque à personne. C'est précisément un argument pour le poser tôt : poser
aujourd'hui donne huit semaines d'observation avant la sortie, poser en
octobre en donne quatre. La version d'origine de ce paragraphe se
contredisait.

### Fait le 22/09 : un article ne peut plus être daté de 1970

L'historique contenait un article daté du 01/01/1970 — une date d'époque
Unix produite par un flux dont le champ `date` était vide ou illisible, et
que `normalize_date` convertissait fidèlement. Un tel article tombe tout au
fond du tri et n'en remonte jamais ; avec le plancher de rétention il aurait
en plus été refusé à l'entrée à chaque passage.

Trois verrous, parce qu'un seul n'aurait traité qu'un des trois cas :

1. `normalize_date` refuse désormais `DATE_FLOOR` et renvoie `None` plutôt
   qu'une date fausse ;
2. `date_ou_premiere_vue()` donne à l'article sans date exploitable
   l'instant où le robot l'a vu pour la première fois — ce qui est faux de
   quelques heures au pire, au lieu de faux de cinquante-six ans ;
3. `repare_dates_epoque()` fait la même chose rétroactivement sur
   l'historique déjà écrit. La passe est idempotente : vérifié sur le vrai
   fichier, 1 article corrigé au premier passage, 0 au second.

### Fait le 22/09 : les deux lecteurs de liaison sont mutualisés

`lire_liste()` et `lire_totaux_recap()` existaient en double, à l'identique,
dans `push_notify.py` et `discord_notify.py`. Ils vivent maintenant dans
`feed_store.py` et les deux modules les y prennent. Ce n'est pas une
économie de lignes : c'est la garantie que le compte annoncé sur Discord et
le compte annoncé en notification push ne peuvent plus diverger par
divergence de code. Un test vérifie que les quatre références pointent bien
vers la même implémentation.

### La cadence reste horaire, sortie comprise

Pas d'accélération pour le 19/11. Décision cohérente avec ce qui a été
mesuré : le délai médian de 54 minutes vient à 47 minutes de la cadence, et
le code n'y pèse que 3 %. Une cadence à quinze minutes diviserait le délai
par sept, mais multiplierait par quatre les commits, les déploiements et les
notifications — pour une veille qu'on consulte quand on y pense, pas en
direct.

## Les trois chantiers de la veille durable — 22/09/2026

Les décisions ci-dessus laissaient trois sujets ouverts : la profondeur de
l'historique, la révision des sources, le découpage de l'archive. Les trois
ont été mesurés le même jour, parce qu'ils se décident sur les mêmes
chiffres — et la mesure a renversé l'ordre dans lequel je comptais les
prendre.

### Ce que l'historique contient vraiment

Avant tout arbitrage, le décompte sur les 1500 articles en fenêtre :

| ancienneté | articles |
|---|---|
| moins de 24 h | 80 |
| 1 à 3 jours | 122 |
| 3 à 7 jours | 669 |
| 7 à 15 jours | 520 |
| plus de 15 jours | 109 |

Les 109 derniers sont exactement les familles protégées — officiels,
RockstarMag, sujets chauds — que l'élagage épargne. La fenêtre de quinze
jours, elle, était pleine à ras bord : **79 articles par jour en médiane,
94 en moyenne**, soit ~1400 ordinaires sur quinze jours pour un plancher à
1500. Autrement dit le plancher commandait et la profondeur visée n'avait
jamais eu l'occasion de servir.

### Sauf que ça ne marchait pas : le calcul était circulaire

**Le passage de 15 à 30 jours n'a rien changé du tout.** Constaté le jour
même, après la fusion, en vérifiant la sortie du robot en production plutôt
qu'en se fiant à la suite de tests : la fenêtre gardait ses 1500 articles et
ses 14 jours, exactement comme avant.

**La cause.** `taille_historique_visee` comptait les articles DÉJÀ STOCKÉS
qui tombaient dans la fenêtre. Or cette liste est elle-même plafonnée par le
nombre que la fonction rend. Le calcul se mordait la queue. Quarante-cinq
jours de robot simulés sur le vrai historique, avec l'ancien calcul :

| débit | gardés | profondeur obtenue |
|---|---|---|
| 40/jour | 1500 | 33 j |
| **94/jour** (le régime réel) | **1500** | **14 j** |
| 142/jour | 1500 | 9 j |
| 200/jour | 4000 | 19 j |

Le seuil de bascule se calcule : la visée ne dépassait 1500 que si l'apport
d'un passage suffisait à faire franchir ce nombre aux articles ordinaires
stockés — soit un débit supérieur au nombre de protégés, **142 aujourd'hui**.
En dessous, la fenêtre était gelée au plancher et `MAX_HISTORY_DAYS` n'avait
**aucun effet, quelle que soit sa valeur**. Le réglage était décoratif depuis
le jour de sa création.

**Le correctif.** La visée part maintenant du **débit observé** — la médiane
des articles ordinaires par jour sur les sept jours RÉVOLUS — multiplié par
la profondeur voulue. Le débit, lui, ne dépend pas de ce qu'on garde.

Deux détails qui comptent :

- **le jour en cours est exclu.** Il est incomplet par construction : à midi
  il ne porte que la moitié de ses articles, et le compter tirerait la
  mesure vers le bas à chaque passage de la matinée ;
- **la médiane, pas la moyenne.** Le 18/09 a produit 300 articles quand la
  médiane de la semaine est à 94. Une moyenne aurait laissé ce seul jour
  élargir la fenêtre d'un tiers, puis la laisser rétrécir dès qu'il sort de
  la mesure — l'historique respirerait au rythme des journées d'annonce au
  lieu de suivre le régime de fond.

**Et les DEUX estimations sont gardées, la plus généreuse gagne.** Le débit
dit ce que la fenêtre *devrait* contenir, le comptage ce qu'elle contient
*déjà*. Le débit seul jetterait ce qu'on a la place de garder : un afflux
massif sur un historique jeune n'a aucun jour révolu à mesurer, le débit
vaut zéro, et il faudrait couper au plancher des milliers d'articles tous
publiés dans la fenêtre. **C'est un test de la suite qui l'a dit**, pas une
relecture — celui du plafond après fusion, qui a viré au rouge dès la
première version du correctif.

La même simulation, avec le nouveau calcul :

| débit | avant | après |
|---|---|---|
| 40/jour | 1500 / 33 j | 1500 / 33 j |
| **94/jour** | 1500 / **14 j** | 2820 / **28 j** |
| 142/jour | 1500 / **9 j** | 4000 / **27 j** |
| 200/jour | 4000 / 19 j | 4000 / 19 j |
| 500/jour | 4000 / 7 j | 4000 / 7 j |

Les gros débits sont inchangés — le plafond dur reprend la main et la fenêtre
rétrécit, ce qui protège le fichier le 19/11.

**Ce qui manquait pour l'attraper.** La suite vérifiait la FONCTION de calcul,
et elle la vérifiait correctement. Personne ne vérifiait que le robot, en la
rejouant passage après passage, arrivait quelque part. Le nouveau contrôle
`test_la_profondeur_est_reellement_atteinte` rejoue quarante-cinq jours de
cap et regarde la profondeur obtenue — c'est la seule forme de test qui
pouvait voir une boucle fermée sur elle-même.

**Conséquence immédiate, à ne pas prendre pour une panne.** La visée est
passée à ~2790 alors que l'historique n'en contient que 1500 : il n'est donc
plus « plein », le plancher de rétention se met en retrait (`None`) et les
~300 refus par passage tombent à zéro le temps que la fenêtre se remplisse.
Les deux mécanismes se composent comme prévu — le plancher ne refuse que ce
qui serait élagué, et pour l'instant rien ne l'est.

### Le raisonnement qui a mené au réglage — et qui, lui, tenait

Je m'attendais à devoir arbitrer. La mesure dit qu'il n'y avait pas
d'arbitrage à faire, pour une raison que j'avais sous les yeux sans la
voir : **l'ouverture de l'app ne lit pas `feed.json`**. Elle lit
`feed-recent.json`, 300 articles, dont la taille ne dépend pas de la
profondeur. Le fichier complet ne part que sur la recherche et sur « Tout
charger ».

Les trois coûts que je redoutais ont été vérifiés un par un, et aucun ne
tient :

- **le dépôt git** : un commit du robot pèse son *delta*, et le delta suit
  le nombre d'articles qui ont changé, pas la profondeur de l'historique.
  Mesuré : 1028 passages, 1,5 Mo de `feed.json` réécrit à chaque fois, et le
  pack entier fait **7,2 Mo** ;
- **la déduplication** : ses deux passes sont déjà bornées indépendamment de
  l'historique — `FENETRE_MAX` à 5000 et `FUSION_RETRO_MAX` à 3000 ;
- **le téléphone** : inchangé à l'ouverture, par ce qui précède.

Reste le seul coût réel, le téléchargement du fichier complet à la
recherche : ~1,5 Mo devient ~2,6 Mo bruts, 465 Ko devient ~800 Ko une fois
compressés. Sur une action explicite.

`MAX_HISTORY_DAYS` passe donc de 15 à **30**, les deux bornes inchangées.
Le plafond dur de 4000 devient mordant à partir de 134 articles/jour au lieu
de 267 — c'est voulu : c'est lui qui protège le fichier le jour de la sortie.
Le test du plafond, qui n'exerçait que deux de ses trois branches, les
exerce maintenant toutes les trois, et **calcule ses débits à partir de
`MAX_HISTORY_DAYS`** au lieu de les coder en dur — sans quoi il aurait
recommencé à mentir au prochain réglage.

### Les sources : la mesure a surtout confirmé l'existant

Le décompte d'abord, parce qu'il est plus dur qu'attendu :

- **1118 articles sur 1500 viennent de Google News**, soit 75 % ;
- **22 des 63 sources passent par `news.google.com`** — pas seulement les
  six qui portent ce nom, mais aussi The Verge, Engadget, Numerama,
  Frandroid, Dexerto, MGG, Pure Xbox, TrueAchievements et d'autres, qui sont
  des recherches `site:` déguisées sous le nom du média ;
- **13 sources sur 63 n'ont produit aucun article** sur la fenêtre, dont 7
  qui n'en ont jamais produit un seul.

Une panne de ce seul domaine éteindrait donc un tiers de la liste d'un coup.
Douze flux natifs ont été sondés pour voir ce qui pouvait s'en détacher.
**Le résultat est surtout négatif, et c'est le résultat :**

| candidat | verdict |
|---|---|
| The Verge, Engadget, Numerama, Push Square | 200, mais **0 retenue** — flux généralistes courts où GTA 6 ne passe pas |
| TrueAchievements | 403 |
| millenium.org/rss.xml | 404 |
| Dexerto natif | déjà écarté le 15/09 pour la même raison, re-confirmé |
| **xboxygen.com** | 301 → `/feed`, 50 entrées, **3 retenues du jour** |
| **journaldugeek.com/feed/** | 30 entrées, **1 retenue du jour** |

Deux ajouts seulement, et ce sont des **ajouts, pas des substitutions** —
même raison que pour les flux par tag plus haut : un flux maison peut cesser
de couvrir un sujet sans que rien ne le signale. Le cas du Journal du Geek
le prouve sur pièce : ses deux accès existants ne rapportent rien (la
recherche Google News n'a rien sorti depuis 22 jours, le flux par tag
« gta-6 » revient vide) alors que son flux de site avait un article du jour.
C'est exactement le défaut de balisage annoncé en septembre, pris sur le
fait.

Un mot sur un faux défaut. Le flux de Pure Xbox a laissé passer deux
articles dont le titre ne parle pas de GTA 6 — « Final Fantasy VII
Revelation… », « Upcoming Xbox Game Pass Title… ». Vérification faite,
aucun mot-clé ne correspond dans le titre : c'est la *description* qui
mentionne GTA 6. Le filtre lit titre + description, délibérément. Ce n'est
pas un bogue, c'est le compromis assumé entre rappel et précision — mais
c'est un coût des flux natifs que les recherches `site:` ne font pas payer,
puisque Google a déjà trié.

### Une source pour l'après-sortie, sur sept sondées

Toute la liste avait été choisie pour couvrir une attente. Sept candidates
ont été sondées sur l'angle correctifs / DLC / GTA Online :

| candidat | verdict |
|---|---|
| `rockstargames.com/newswire.rss` et `/newswire/rss` | **HTTP 500, deux fois** — le Newswire n'a pas de flux natif |
| `support.rockstargames.com/rss` | aucune réponse |
| gtaforums.com | 403 |
| gtabase.com | 404, comme au 15/09 |
| gtanet.com | 200, 0 retenue sur 10 |
| r/gtaonline (recherche « GTA 6 ») | 200, 2 retenues, la plus récente à **29 jours** — le sujet n'y existe pas encore, à re-sonder après le 19/11 |
| **r/GTA6 (recherche update/patch/DLC/online)** | 25 entrées, **3 retenues**, la plus récente à 4 jours |

La dernière est retenue. Elle cherche les mots qui *manquent* plutôt que
« GTA 6 », qui va de soi sur ce subreddit — d'où « GTA 6 Online en 2027 »
dans ses résultats. Réserve dite plutôt que tue : c'est la deuxième source
sur `reddit.com`, et une première sonde groupée s'est fait renvoyer un 429.

#### Une cause, et une faute sans rapport

La réserve ci-dessus disait aussi que « `PER_HOST_LIMIT` et `HOST_PAUSE`
espacent déjà les requêtes d'un même domaine ». **C'était faux**, et la
source l'a payé. Mesuré sur les 12 derniers passages du 22/09/2026 :

| Source | 12 derniers passages | Articles apportés |
|---|---|---|
| `reddit-leaks` | `25,25,25,25,25,25,25,25,25,25,25,25` | 24 |
| `reddit-gta6-suivi` | `25,25,25,25,0,0,0,0,25` | 3 |

Toujours la même des deux qui tombe, jamais l'autre.

**La cause : les deux requêtes partaient en même temps.**
`chaines_par_hote()` fait `min(PER_HOST_LIMIT, len(liste))` files par
domaine : avec deux sources `reddit.com` et `PER_HOST_LIMIT = 3`, ça donne
**deux files d'une source chacune**. Or `HOST_PAUSE` ne s'applique
qu'*entre deux sources d'une même file*. Les deux requêtes partaient donc à
~0 seconde d'écart. Le garde-fou existait, il ne couvrait simplement pas ce
cas — et le commentaire qui affirmait le contraire a survécu trois semaines.

**La sonde du 22/09 à 20h29 le prouve, et corrige au passage ce que ce
paragraphe affirmait d'abord.** Les deux URL sondées l'une après l'autre,
dans la même seconde :

```
r/GTA6 (reddit-gta6-suivi)              → HTTP 200, 25 entrées, 3 pertinentes
r/GamingLeaksAndRumours (reddit-leaks)  → HTTP 429
```

L'ordre était **inversé** par rapport à la production — et le perdant a
changé avec lui. `reddit-leaks`, qui n'avait pas raté un passage en douze,
prend le 429 dès qu'elle passe en second. Le 429 ne dépend donc ni du
subreddit, ni de la requête, ni de la source : **la seconde requête vers
`reddit.com` dans la même seconde se fait jeter, quelle qu'elle soit.** La
limite d'environ une requête par minute et par IP, rapportée depuis juin
2026, est mesurée chez nous.

Accessoirement : `reddit-gta6-suivi` a rendu 25 entrées dont 3 pertinentes,
la plus récente à 5 jours. Elle n'a jamais été une mauvaise source — elle
perdait la course. L'idée de la retirer, envisagée le matin même faute de
rendement, reposait sur un symptôme.

**Et une faute sans rapport avec le débit : on mentait sur le
`User-Agent`.** Les règles de l'API Reddit sont explicites :

> *NEVER lie about your user-agent. This includes spoofing popular browsers
> […] We will ban liars with extreme prejudice.*
>
> *Many default User-Agents […] are drastically limited to encourage unique
> and descriptive user-agent strings.*

Le robot envoyait un Chrome falsifié depuis une IP de runner GitHub,
partagée et de datacenter. C'est le profil exact qu'ils étranglent — et
c'est une violation qui expose à un bannissement, pas à un ralentissement.

Ce paragraphe a d'abord annoncé « deux causes ». **C'était trop généreux
pour celle-ci, et c'est corrigé ici plutôt que discrètement réécrit** :
avant le correctif, la *première* requête passait déjà en 200 avec le Chrome
falsifié, et la sonde ci-dessus montre que le rang dans la file suffit à
tout expliquer. Rien ne permet de dire que l'agent coûtait du débit. Le
changer était nécessaire ; ce n'est pas lui qui a débloqué la source.

#### Ce qui a été fait le 22/09/2026

**Un `User-Agent` honnête pour `reddit.com`, et lui seul.** `USER_AGENT_REDDIT`
vaut `python:gta6-watch:v1.0`, au format que Reddit documente
(`<plateforme>:<identifiant>:<version>`). Le pseudo que leur format prévoit
est volontairement absent : ils le demandent sans l'imposer, ce dépôt est
public, et le cœur de la règle est de ne pas se faire passer pour un
navigateur. Les 61 autres sources gardent l'agent d'avant.

Le choix passe par `agent_pour(url)` et non par deux constantes recopiées :
**cinq** endroits envoient un `User-Agent` (le flux, l'image de
prévisualisation, la découverte de flux de la sonde, et les deux appels
YouTube). En corriger quatre, c'est continuer de mentir une fois sur cinq
sans que rien ne le dise. Un contrôle intercepte `feedparser` et lit l'agent
*réellement émis* — vérifier la fonction n'aurait rien dit de ses appelants.

**Un tour de rôle.** `ROTATION_REDDIT` fait se relayer les deux sources, sur
la parité de l'heure UTC. Aucun état stocké : rien à fusionner après un
conflit de push, et deux exécutions de la même heure font le même choix. Les
passages tournent toutes les ~30 min, donc chaque source est interrogée au
moins une fois par heure — assez pour du suivi de fuites.

L'alternative écartée : forcer `reddit.com` dans une file unique avec une
longue pause. Il faudrait ~65 s d'attente à l'intérieur d'un passage qui en
dure 40, soit tripler sa durée pour économiser une requête.

#### Le vrai travail n'était pas l'alternance

**Une source non interrogée n'est pas une source muette**, et tout le suivi
de santé raisonne sur « ce passage n'a rien rapporté ». Sans un troisième
état, la rotation aurait produit deux fausses alertes symétriques :

- au bout de `DEAD_SOURCE_HOURS`, une alerte Discord « **source tombée** »
  pour une source qu'on n'avait simplement pas appelée ;
- et pire, deux passages plus tard, ses tours de repos comptés comme des
  réussites par `REPRISE_CONFIRMEE` auraient annoncé le « **retour** » d'une
  source jamais rappelée. La fausse bonne nouvelle est celle qui coûte le
  plus cher : elle clôt le dossier.

D'où le statut `en_attente`, tenu **hors** de `STATUTS_SANS_ARTICLE`, et trois
comportements :

| Mécanisme | Ce qu'il fait d'un repos |
|---|---|
| `sources_health` | statut `en_attente`, ni muette ni cassée ni tarie |
| `maj_historique_entrees` | n'empile **rien** — un `0` ferait voir la source « en forte baisse » un passage sur deux |
| `suivre_sources_muettes` | reconduit le chronomètre **à l'identique** : ni échec, ni réussite |

Le contrôle qui le prouve rejoue **deux jours de rotation à deux passages par
heure**, une fois sur des sources saines (zéro alerte) et une fois avec une
source réellement muette : la panne est bien signalée une fois, au bout de
24 h, et aucun faux retour ne part. Vérifier les trois fonctions séparément
n'aurait pas attrapé l'interaction.

Côté app, une source `en_attente` compte comme répondante dans le compteur
`62/63 sources` — volontairement : le compteur dit « combien ne posent pas
de problème », et une source au repos n'en pose aucun. La déduire ferait
clignoter le compteur toutes les heures pour rien.

#### Ce qui n'a pas été fait

**OAuth « app-only ».** Reddit accorde 60 requêtes/minute à un client OAuth
enregistré, contre ~1/minute par IP en anonyme depuis juin 2026, et le palier
gratuit couvre l'usage non commercial. Le flux est simple (`POST
https://www.reddit.com/api/v1/access_token`, HTTP Basic avec le couple
client, `grant_type=client_credentials`). Deux raisons de ne pas l'avoir
fait tout de suite : `oauth.reddit.com` rend du **JSON et non du RSS**, donc
il faudrait un lecteur Reddit à côté du chemin `feedparser` — le seul endroit
du robot qui ne serait plus « une URL, un flux » ; et le tour de rôle ramène
déjà le robot à UNE requête `reddit.com` par passage, toutes les 30 min, très
loin de la limite. À mesurer une semaine avant d'en refaire un sujet.

Ce qui le rendrait nécessaire, en revanche, est net depuis la sonde : **une
troisième source Reddit est impossible sans lui.** Le tour de rôle fait déjà
tomber chaque source à une interrogation par heure ; à trois, ce serait une
toutes les 90 minutes, et à quatre, deux heures. OAuth est la seule façon
d'en avoir plus de deux. C'est aussi l'assurance du jour où Reddit fermera
le RSS public, ce qu'ils ont laissé entendre.

### L'archive mensuelle : ce qui sort de la fenêtre ne sort plus du projet

C'est le chantier qui change le plus, et celui dont j'avais mal jugé la
place. J'avais dit le matin qu'il fallait régler la profondeur en premier
« parce qu'elle conditionne les deux autres ». C'est l'inverse : une fenêtre,
si profonde soit-elle, ne répondra jamais à « qu'est-ce qui s'est dit de ce
DLC il y a six mois ». Trente jours ou quatre-vingt-dix ne change pas la
nature du problème — il faut *garder*, pas *élargir*.

`docs/archives/` contient désormais **un fichier par mois**, plus un
`index.json`. Le robot y range **tout ce qu'il publie, à chaque passage** —
et non « ce qu'il s'apprête à jeter ». La nuance porte tout le dispositif :

- si l'archive rate un article une fois (passage interrompu, conflit de
  push, bogue), le passage suivant le remet tant qu'il est encore dans la
  fenêtre. N'archiver que les élagués n'offrirait **aucun rattrapage** :
  l'article serait perdu des deux côtés et rien ne le dirait ;
- la première exécution remplit l'archive avec l'historique entier au lieu
  de partir de zéro. Essai à blanc sur les vraies données : **1500 articles
  répartis sur 10 mois**, de décembre 2023 à septembre 2026, aucun perdu,
  aucun inventé.

Pourquoi le mois. Un mois **révolu ne change plus jamais** : son fichier est
écrit une fois, plus aucun commit du robot ne le touche, et le navigateur
peut le garder en cache indéfiniment. Un fichier unique serait réécrit
vingt-quatre fois par jour ; un fichier par jour demanderait des centaines
de requêtes pour couvrir une recherche d'un an.

**Les tranches existent avant d'en avoir besoin.** Au-delà de 2500 articles,
un mois est coupé en `2026-11.json`, `2026-11.2.json`, etc. — la première
garde le nom nu, celui qu'on tape à la main pour vérifier. Un mois ordinaire
tient en une tranche. Le mois de la sortie n'en fera pas 2800 mais plusieurs
dizaines de milliers, et un fichier de 25 Mo ne se télécharge pas depuis un
téléphone. Poser le découpage en novembre voudrait dire changer le format
publié pendant le mois le plus chargé de la vie du projet : c'est le même
argument qui a fait avancer le plancher de rétention au 22/09 plutôt qu'en
octobre, et il vaut ici aussi.

L'ordre d'écriture est réfléchi et le code le dit : l'archive part **après**
le garde-fou — un passage refusé ne doit pas laisser de trace, l'archive
n'ayant pas de second contrôle derrière elle — et **avant** le fichier
publié — si l'écriture s'interrompt entre les deux, une archive en avance
d'un passage ne demande aucune réparation, l'inverse ferait disparaître des
articles élagués.

L'index porte le **poids** de chaque fichier autant que son nombre
d'articles : sans lui, l'app ne pourrait pas prévenir avant de lancer un
téléchargement de plusieurs mégaoctets sur un forfait mobile — et c'est
précisément le mois de la sortie qui sera le plus lourd.

### Et l'app sait la lire

Une ligne apparaît sous le compteur, **une fois la fenêtre déjà complète** —
pas avant, sinon deux boutons concurrents proposeraient le même geste :

```
archive : 20 de plus, 2026-07 → 2026-08 (20 Ko)   [Charger l'archive]
```

Trois choix qui méritent d'être dits, parce qu'ils étaient tous les trois
faciles à rater :

- **elle annonce ce qu'elle AJOUTE, pas le total de l'archive.** L'archive
  est un sur-ensemble de la fenêtre : afficher son total promettrait des
  milliers d'articles pour n'en apporter que quelques dizaines ;
- **elle donne le poids** avant de télécharger. C'est à ça que sert le champ
  `octets` de l'index, et ce sera le mois de la sortie qui en aura besoin ;
- **un article d'archive n'est jamais une nouveauté.** Le chargement ne
  touche ni à `seenMap` ni à `lastNewLinks` — sans ça, un article de juillet
  arrivant aujourd'hui ferait sonner les pastilles de non-lus pour des
  centaines de vieux articles. C'est le défaut le plus probable de cette
  fonctionnalité, et c'est celui qui est verrouillé le plus explicitement.

L'index se charge **sans `await`** derrière l'affichage des articles : c'est
un confort, il ne doit pas retarder le fil d'une milliseconde. Un backend
d'une version antérieure n'a pas de répertoire `archives/` — dans ce cas la
ligne reste muette et **rien ne s'affiche comme une panne**, parce que c'est
une absence, pas une erreur.

Quatorze contrôles en vrai navigateur couvrent tout ça, en interceptant le
réseau plutôt qu'en écrivant de faux fichiers dans `docs/`.

### Un contrôle qui perdait une course, et qui ne vérifiait pas ce qu'il disait

La CI est passée au rouge le 22/09 sur `[vignette] le clic ouvre bien un
onglet sur l'article`, puis au vert au commit suivant sans qu'une ligne de
l'app ait changé. C'est le signalement d'un test fragile, pas un aléa
d'infrastructure, et il avait **deux** défauts :

- il **dormait 700 ms** en espérant que l'onglet soit apparu. Un runner
  chargé met parfois plus longtemps. Remplacé par `expect_page`, qui attend
  l'ÉVÉNEMENT avec une limite haute : le test est du coup plus sûr *et* plus
  rapide dans le cas normal ;
- son commentaire annonçait « on lit l'URL DEMANDÉE », et le code lisait
  `pg.url` — l'URL de l'onglet. Or la cible est un vrai site, injoignable
  depuis la CI, donc Chromium y met `chrome-error://chromewebdata/`.
  L'assertion était donc écrite `... or bool(demandees)` : **n'importe quel
  onglet** suffisait à la satisfaire. Elle lit maintenant l'adresse sur un
  écouteur de requêtes, qui enregistre ce qui a été TENTÉ, et compare à
  l'adresse attendue.

Le contrôle est donc plus strict qu'avant, pas plus indulgent. Suite rejouée
trois fois de suite pour vérifier qu'elle ne rebascule pas.

### L'audit regarde enfin l'archive

L'archive est écrite à chaque passage et **relue par personne**. Un fichier
tronqué, un index périmé, un mois qui se vide : rien ne l'aurait signalé, et
on s'en serait aperçu le jour où l'on aurait cherché quelque chose d'ancien
— c'est-à-dire trop tard, la fenêtre l'ayant depuis longtemps oublié. C'est
un trou que la livraison de l'archive avait creusé le matin même.

`audit_donnees.py` pose maintenant trois questions, par gravité décroissante :

1. **l'archive contient-elle TOUT ce que contient la fenêtre ?** C'est
   l'invariant qui justifie son existence. S'il tombe, des articles sont en
   train de disparaître pour de bon — signalé `grave` ;
2. **l'index dit-il la vérité ?** L'app ne lit que lui. Un index qui annonce
   un fichier absent l'envoie chercher dans le vide ; un fichier présent
   qu'il ne cite pas ne sera jamais demandé, et ses articles n'existent pour
   personne ;
3. **chaque article est-il dans le fichier de son mois ?** Un article d'août
   rangé en septembre ne se perd pas, mais il ne se retrouve pas non plus.

**Les six pannes sont provoquées exprès dans la suite**, parce qu'un audit
qu'on ne met jamais en échec ne prouve rien : article manquant, fichier
annoncé et absent, fichier présent hors index, index illisible, absence
totale d'archive — et le cas où cette absence est **normale**, un dépôt tout
neuf n'ayant rien à se reprocher.

L'audit affiche aussi, en temps normal, ce que l'archive garde **au-delà de
la fenêtre** : c'est le chiffre qui dira, dans quelques mois, si elle sert
vraiment à quelque chose.

### Une tranche abîmée effaçait 2500 articles, en silence

Trouvé en relisant à froid le code de l'archive quelques heures après
l'avoir livré. C'est le défaut le plus grave de la journée, et c'était
exactement la perte que l'archive existe pour empêcher.

**Le mécanisme.** `lire_mois` sautait une tranche illisible pour ne pas
faire échouer la lecture entière — raisonnable pour un LECTEUR, l'app
préférant la moitié d'un mois à rien. Mais `archiver` utilisait la même
fonction avant de **réécrire** le mois. Une tranche qu'on ne sait pas lire
devenait alors une tranche vide, et le mois était republié sans elle.

**Mesuré, pas supposé.** Un mois de 6000 articles en trois tranches, une
seule tronquée :

```
archive initiale : 6000 articles, 3 tranches
après corruption : 3500 articles lisibles
APRÈS le passage suivant : 3500 articles
articles perdus DÉFINITIVEMENT : 2500
```

Sans un mot dans le journal. Et l'archive étant le dernier endroit où
vivent les articles sortis de la fenêtre, « définitivement » est à prendre
au pied de la lettre.

**Le correctif.** `_lire_tranches()` rend les articles **et** la liste des
tranches qu'il n'a pas su lire. `lire_mois` reste au mieux — c'est ce que
veulent l'app et l'audit. `archiver`, lui, **refuse de toucher au mois**
dès qu'une tranche manque à l'appel, et le dit dans le journal.

Perdre la mise à jour d'un passage est sans conséquence : le suivant la
refera, tant que les articles sont encore dans la fenêtre. Réécrire est
irréversible. Entre les deux, le choix ne se discute pas.

**Le revers, assumé et surveillé.** Ce refus **gèle** le mois : il
n'accueillera plus rien tant que la tranche n'est pas réparée (l'historique
git la contient) ou retirée. Un gel silencieux serait pire que la panne,
donc l'audit le signale en **grave** avec le geste à faire.

C'est aussi la démonstration que l'audit de l'archive, écrit une heure plus
tôt, servait déjà : il a fallu l'étendre pour couvrir ce cas, mais sa
structure a rendu l'ajout immédiat.

## Ce qu'Antoni a demandé le 22/09, en fin de journée

### Actualiser ne doit plus jeter ce qu'on a chargé

*« Ça serait bien que l'historique et l'archive se chargent tout seuls suite
à une actualisation. »* Ce n'était pas une demande de confort, c'était un
**défaut**, reproduit dans un vrai navigateur avant d'écrire une ligne :

```
1. ouverture (fichier allégé)     :  300 articles
2. après « Tout charger »         : 1804 articles
3. APRÈS UNE SIMPLE ACTUALISATION :  300 articles
```

La cause tenait en une ligne, `lastItems = all`, où `all` est le contenu du
fichier qu'on vient de lire. Au rafraîchissement c'est le fichier **allégé**,
donc 300 articles écrasaient tout le reste, archive comprise. `partial`
décrivait ce fichier-là, et l'app le prenait pour son propre état.

**Deux correctifs, et le second ne coûte rien :**

- **la fusion.** Quand le fichier reçu est plus court que ce qu'on a déjà,
  on fusionne par lien au lieu de remplacer : l'article qui arrive gagne, ce
  qu'il ne mentionne pas reste en place. Un article que le backend a élagué
  survit ainsi côté app — c'est voulu, c'est justement ce qu'on a demandé à
  garder. Zéro octet de réseau ;
- **le rattrapage au démarrage.** L'historique complet puis l'archive se
  chargent seuls, **une fois par session**, sans `await` et **après**
  l'affichage du fil. Les 300 premiers articles s'affichent aussi vite
  qu'avant ; le reste arrive derrière.

Vérifié en vrai navigateur, huit contrôles : 300 affichés tout de suite,
1807 une fois le rattrapage fini, **1807 après un rafraîchissement et après
un second**, aucun doublon, tri intact — et **une actualisation ne
retélécharge pas le fichier complet**, ce que la fusion rend inutile.

Trois contrôles existants ont dû être ajustés, et pas parce qu'ils avaient
tort : le rattrapage automatique télécharge exprès le fichier complet, ce
qu'ils interdisaient. Ils arment maintenant `rattrapageLance` à la main pour
continuer de vérifier ce qu'ils vérifiaient — qu'une lecture normale
n'**escalade** pas vers le gros fichier faute de savoir lire un échec. Un
téléchargement voulu et une escalade subie ne sont pas la même chose, et les
confondre aurait rendu leur verdict illisible.

### Les notifications disent enfin quoi, et l'officiel se distingue

Deux demandes, deux réponses.

**« Elles ne disent pas assez. »** Le corps portait « Ouvrir GTA6_WATCH ».
Il porte maintenant un vrai titre d'article — et le commentaire d'août qui
refusait ce titre avait raison sur un point qu'il faut garder : *« premier »
ne veut rien dire, c'est l'ordre de FEEDS et pas une importance.* La
réponse n'est donc pas de prendre le premier, mais de **classer** :

1. un article **officiel** de Rockstar passe avant tout ;
2. puis le **nombre de rédactions** sur le même sujet — c'est déjà ce qui
   pilote le badge « actu majeure » ;
3. puis la date.

Le titre de la notification, lui, reste un **compte** : la règle du 29/08
tient, un lot de dix articles n'a pas de titre représentatif. C'est le corps
qui a changé, pas l'en-tête. Et quand il n'y a rien à citer — le
récapitulatif du matin ne travaille que sur des comptes reportés — le corps
retombe sur le texte neutre plutôt que d'inventer.

**« L'officiel devrait se distinguer. »** Il l'était déjà plus que je ne le
croyais : notification séparée, titre de l'article, tag propre, TTL d'un
jour, urgence haute, et elle réveille même pendant la pause nocturne. Ce qui
manquait est ailleurs — **les deux commençaient par le même emoji 🎮**, et
sur un téléphone c'est la première chose qu'on voit. Trois différences
maintenant :

| | routine | officiel Rockstar |
|---|---|---|
| emoji | 🎮 | **⭐** |
| vibration | celle du système | **pulsation double** |
| bannière | s'efface seule | **reste jusqu'à ce qu'on l'écarte** |

Les deux dernières sont ignorées sans risque là où elles ne sont pas gérées.

### Trois dossiers fermés

- **Les 8 sources muettes restent.** Une source qui n'apporte rien coûte une
  requête par passage et rien d'autre ; pour une veille dont le but est de
  ne rien rater, c'est une assurance bon marché. On n'y revient pas.
- **Clubic reste dehors**, et cette fois c'est mesuré une seconde fois : ses
  deux adresses redirigent vers le même flux, 50 entrées, **une** retenue —
  un article sur des manettes Xbox en promotion. ActuGaming en flux natif :
  zéro.
- **Les deux messages de fusion aux balises parasites ne seront pas
  réparés.** Réécrire l'historique de `main` en force pendant que le robot y
  pousse toutes les heures coûte plus que deux lignes moches.

### Et une règle de travail

Antoni, 22/09 : **je fusionne dès que les quatre suites sont vertes**, sans
demander. Ce qui touche au format publié ou aux notifications continue de
passer par lui.

## Ce que game-library a appris à GTA6_WATCH — 22/09/2026

Antoni a un second projet, [`game-library`](https://github.com/antoniman31/game-library) :
une bibliothèque de jeux en **React + Vite**, 33 modules, un Worker Cloudflare.
Question posée : qu'est-ce qui pourrait servir ici ?

**Aucune ligne n'est copiable.** GTA6_WATCH est un fichier HTML unique de
5 000 lignes, sans build, servi tel quel. Ce qui se transporte, ce sont des
décisions — et la lecture a surtout servi à trouver ce qui manquait vraiment,
en écartant tout ce que cette app faisait déjà, parfois mieux (le panneau
`Sheet` de game-library gère Échap et le verrou de défilement, mais pas le
focus ; les dialogues d'ici, si).

### La recherche ignorait les accents

`normalizeTitle()` vivait dans ce fichier depuis longtemps, avec la
normalisation NFD qu'il fallait — mais elle ne servait qu'à la **similarité
des titres**. La recherche, elle, se contentait d'un `toLowerCase()`.

Mesuré sur les 1 846 articles du fil :

```
523 titres portent un accent  (28 %)

  31× « crème »        22× « vidéo »        15× « détails »
  29× « dévoilé »      20× « précommande »  15× « édition »
  24× « déjà »         19× « aperçu »       12× « présentation »
```

Ce sont exactement les mots qu'on tape dans une barre de recherche, et sur un
téléphone on les tape **sans accent**. `precommande` ne renvoyait rien.

Le vrai travail n'était pas le filtre, qui tient en une ligne, mais le
**surlignage**. Une fois les accents retirés, la position d'une correspondance
ne désigne plus le texte d'origine : « é » compte pour un caractère avant
décomposition et deux après, donc `<mark>` se décalait d'un caractère par
accent qui précède. `indexSansAccents()` garde la correspondance entre les
deux textes, et le surlignage retrouve les bornes exactes.

Bénéfice de bord : la `RegExp` a disparu. L'ancienne version cherchait dans le
texte **déjà échappé**, ce qui obligeait à échapper aussi les caractères
spéciaux de la requête — deux échappements imbriqués pour un surlignage. On
cherche maintenant dans le texte aplati et on n'échappe qu'à l'écriture.

La liste noire a suivi, et **des deux côtés** : n'aplatir que le texte des
articles aurait rendu muet un mot déjà enregistré avec ses accents. Une liste
affinée pendant des semaines aurait cessé de filtrer sans que rien ne le dise.

### La barre d'état restait bleue

`<meta name="theme-color">` valait le bleu de l'accent depuis le premier jour,
et **aucune ligne ne la touchait**. En PWA installée, une bande bleue coiffait
donc en permanence une application noire — un bleu qui n'était le fond d'aucun
des deux thèmes. `COULEUR_BARRE` reprend les valeurs de `--bg`, et
`majCouleurBarre()` est appelée là où `data-theme` est posé, donc au démarrage,
au changement manuel, et quand le téléphone bascule le soir.

### Ce qui se défait tout seul ne demande plus la permission

game-library efface avec un toast « Annuler » de 5 s. Ici tout passait par une
confirmation modale. Le garde-fou suit désormais la **réversibilité** :

| Geste | Garde-fou | Pourquoi |
|---|---|---|
| Marquer N articles comme lus | **Toast « Annuler », 5 s** | état local, se rétablit à l'identique |
| Effacer le jeton GitHub | Confirmation | GitHub ne réaffiche jamais un jeton créé |
| Générer des clés VAPID | Confirmation | tous les appareils abonnés décrochent |
| Réinitialiser les réglages | Confirmation | sources et mots-clés ne se rendent pas |
| Tout désactiver | Confirmation | la sélection perdue ne se rend pas |

Le piège n'était pas le toast mais l'**annulation**. Elle doit défaire ce que
le geste a fait, pas rejouer l'inverse sur la liste affichée : un article déjà
lu avant le geste serait alors remarqué non lu, et l'annulation ferait plus
que défaire. `markAllRead` note donc les liens **réellement modifiés**, et ce
sont eux seuls qui reviennent en arrière. Un contrôle navigateur place un
article déjà lu au milieu du lot et vérifie qu'il l'est encore après retour.

Le toast ne prend pas le focus — le voler après un geste volontaire fait
perdre sa place dans la liste — mais son bouton reste atteignable au clavier,
et il s'annonce en `role="status"` et non `alert` : il accompagne une action
voulue, il ne l'interrompt pas.

### Sortir ses réglages de l'appareil

Tout vit dans le `localStorage` d'un navigateur : les réglages, les mots-clés
affinés pendant des semaines, les 63 sources, l'état de lecture. Vider les
données du site ou changer de téléphone efface le tout, sans retour.

**Deux boutons dans ⚙️, et rien à déployer.** Le fichier porte les réglages,
les articles lus et les articles déjà vus. Il ne porte **pas** :

- **le jeton GitHub** — leçon reprise telle quelle de game-library (« l'export
  contient les jeux mais jamais les clés »). Une sauvegarde doit pouvoir
  rester dans un dossier de téléchargements, être envoyée par message ou
  stockée ailleurs sans rien exposer. Le jeton se refabrique sur GitHub ; il
  n'a rien à faire dans un fichier qui voyage ;
- **le cache du fil** (`last-items-v1`), qui se reconstruit seul à la première
  ouverture. Le recopier alourdirait le fichier de centaines de kilo-octets
  d'articles qui reviennent tout seuls.

**Un seul mode : remplacer.** game-library propose aussi « fusionner », parce
qu'il réconcilie deux bibliothèques divergentes. Ici le besoin est un
téléphone unique qu'on restaure : sur un appareil neuf il n'y a rien à
fusionner, et sur l'appareil courant la question est justement « est-ce que je
remplace ». Le mode manquant s'ajoutera le jour où deux appareils existeront.

**La confirmation montre les chiffres des deux côtés** — `1 846 → 1 200`
articles lus, les sources, les mots-clés — et la date de la sauvegarde.
Importer par mégarde un fichier d'il y a un mois effacerait sinon un mois de
lecture en silence.

**Un fichier douteux est refusé EN BLOC**, jamais appliqué à moitié : un
import partiel laisse un état que personne ne sait décrire, qui n'est ni
l'ancien ni le nouveau. `valideSauvegarde()` rend une raison précise pour
chacun des sept cas — pas du JSON, une autre application, une version plus
récente, un champ absent, un champ du mauvais type — et sept contrôles
navigateur les rejouent.

Un détail qui aurait coûté un bug silencieux : le champ de fichier est **remis
à zéro** après lecture. Sans ça, réimporter deux fois le même fichier ne
déclenche pas de second `change`, et le second import semblerait ignoré sans
qu'on sache pourquoi.

Et le contrôle d'accessibilité du dépôt a attrapé un vrai défaut au passage :
le champ de fichier n'avait pas de nom. Masqué ou non, il en a un maintenant —
`display:none` est un détail de présentation, qui peut sauter.

### Ce qui n'avait rien à faire

**Nommer ce qu'on perd avant d'effacer.** C'était la proposition, et elle était
fondée sur une lecture trop rapide : les confirmations d'ici nomment déjà la
conséquence, mot pour mot dans l'esprit de `garde-fous.js`. Et le champ du
jeton n'est jamais pré-rempli — on ne peut pas écraser d'un doigt qui glisse ce
qu'on ne voit pas. Zéro ligne écrite, la proposition était en trop.

**La synchronisation entre appareils.** Écartée après une question : GTA6_WATCH
se consulte surtout depuis un téléphone, donc un Worker à déployer et un état
partagé qui peut diverger ne répondraient à rien. Le vrai risque restant — un
navigateur qui nettoie ses données, un changement de téléphone — se couvre par
un export de fichier, sans infrastructure.

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
  trois workflows installent depuis ce fichier. La règle vient d'un défaut
  du récapitulatif hebdomadaire, qui faisait `pip install requests` tout
  court jusqu'au 04/09/2026 et tournait donc sur une version que rien
  n'avait testée ; le récapitulatif a été supprimé depuis, la règle est
  restée et un test interdit qu'un workflow réinstalle sans passer par
  `requirements.txt`.
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
