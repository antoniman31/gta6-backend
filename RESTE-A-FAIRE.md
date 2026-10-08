# Ce qu'il reste à faire

**Rien n'est cassé.** Ce fichier existe pour qu'on reprenne plus tard sans
redériver ce qui a déjà été mesuré.

Deux choses ne sont plus « sans urgence » depuis l'audit du 08/10, et les
deux se corrigent en quelques lignes : **4.1** (un mot-clé manquant au mode
de secours, sur le scénario exact du jour de la sortie) et **4.2** (le
message de quota conseille de détruire l'état de lecture). Tout le reste
attend.

Écrit le 06/10/2026 au soir, complété le 07/10, puis le 08/10 par un audit
complet des 46 fichiers (section 4).
Base : `main` après la PR #142.
**GTA 6 sort le 19 novembre 2026, soit dans 42 jours.**

---

## 1. Le badge « actu majeure » — j'avais tort, mesure à l'appui

**Ce que j'ai écrit ici le 06/10 au soir** : « Le 19 novembre, *tout* sera
couvert par trois sources ou plus. Le badge cessera de distinguer quoi que
ce soit. » C'était affirmé comme une certitude. Ça n'en était pas une.

**Mesuré le 07/10 sur le fil réel :**

| jour | articles | « majeurs » (3 rédactions ou plus) |
|---|---|---|
| **17/09**, le pic absolu | **309** | 6 — **2 %** |
| 06/10, une journée normale | 113 | 2 — **2 %** |

Le 17 septembre, l'app a encaissé **309 articles dans la journée** et la
proportion de majeurs n'a pas bougé d'un point. La déduplication fait son
travail : 309 articles, mais répartis sur beaucoup de sujets distincts, et
seuls six réunissaient vraiment trois rédactions.

C'est la seule expérience naturelle qu'on ait, et elle dit **le contraire**
de ce que j'avais affirmé.

**Ce qui reste vrai** : le 19 novembre est différent en nature, pas
seulement en volume — tout parlera du même sujet, et la convergence sera
plus forte qu'un jour de rumeur. Mais c'est une hypothèse. `HOT_SOURCE_THRESHOLD`
reste à 3 et il n'y a **aucune raison mesurée** d'y toucher avant la sortie.

**Ce qu'il faudrait pour trancher** : rejouer la distribution du nombre de
rédactions par sujet, jour par jour, et regarder comment elle se déforme
les jours de forte convergence (les trois pics de septembre). Si elle tient
à 2 % sur les pics, le seuil fixe tiendra aussi le 19 novembre.

**Le vrai problème du jour J n'est pas le badge : c'est le mur.** Voir la
rubrique interface ci-dessous, point 3.1.

---

## 2. La revue de sécurité — FAITE le 08/10/2026

Elle était la rubrique « jamais faite formellement » de ce fichier. L'audit
du 08/10 l'a couverte, sauf un point. Le dépôt est **public** et manipule un
PAT GitHub (cron-job.org), les clés VAPID (la privée en secret, la publique
dans `feed.json`), `PUSH_SUBSCRIPTIONS`, `DISCORD_WEBHOOK_URL` et
`HEALTHCHECK_URL`.

**Ce qui est vérifié, et comment :**

| surface | résultat |
|---|---|
| secrets dans le dépôt | **aucun**, ni dans les fichiers suivis ni dans **tout l'historique Git** (`.py`, `.yml`, `.html`, `.md`). Le seul « webhook » du dépôt est une valeur de test. |
| injection XSS | **close.** Les 7 interpolations `href`/`src` passent par `safeUrl()` (http(s) seulement) ; les deux `a.href = url` sont des `blob:` locaux. `escapeAttr` échappe l'antislash **puis** l'apostrophe **avant** l'échappement HTML — l'ordre correct pour un gestionnaire `onclick` à guillemets simples. |
| permissions des workflows | minimales : `contents: read` partout, `contents: write` pour le seul robot. |
| PR de fork | `checks.yml` tourne sur `pull_request` et **non** `pull_request_target` : jeton en lecture seule, aucun secret atteignable. |
| injection de script Actions | **aucune surface.** Zéro `${{ }}` dans un bloc `run:` — tout passe par `env:`, et `sonde.yml` documente explicitement pourquoi. |
| service worker | n'intercepte que les navigations (jamais `feed.json`). La charge utile du push est chiffrée VAPID ; son `url` vient de `item.link`, mais `statut_officiel` compare le **nom d'hôte** et `urlparse("javascript:…").hostname` vaut `None` — un lien à schéma douteux ne peut donc pas devenir officiel ni atteindre une notification. |
| ce que `feed.json` expose | 21 clés passées en revue, **rien de sensible**. `vapid_public_key` est publique par construction. `feed_http_state` publie les ETags par source : de la plomberie interne servie à tous les visiteurs, 3,3 ko, sans conséquence. |

**Ce qui reste, et que je ne peux PAS vérifier d'ici** : la portée réelle du
PAT cron-job.org. Elle se lit sur
`github.com/settings/personal-access-tokens` — à confirmer à l'œil :
dépôt `gta6-backend` uniquement, « Contents: Read and write » uniquement.

**Deux durcissements à faire** (voir 4.10 et 4.11) : `.gitignore` ne couvre
pas `.env`, et les Actions sont épinglées au tag majeur alors que PyPI est
épinglé à l'exact.

---

## 3. Interface et expérience — six pistes, par ordre d'intérêt

Relevé le 07/10/2026 en relisant `docs/index.html` en entier.

**D'abord, ce qui existe DÉJÀ** et qu'il ne faut pas reproposer : le
balayage sur les cartes (`swipe-wrap`, `surTouchStart`), le tirage pour
rafraîchir, le chip « Reprendre où j'en étais », le mode compact, les trois
modes de thème, l'avertissement quand une recherche porte sur un
historique incomplet (`historyPartial`), et l'onglet Rockstar qui filtre
bien sur `item.official` — le « fil officiel » existe comme filtre.

---

### 3.1 — Le mur du jour J

**Le point le plus important de cette rubrique, et le seul qui ait une
échéance.**

Le 17/09, **309 articles sont tombés dans un seul palier « Aujourd'hui »**.
Les paliers `Aujourd'hui / Hier / Cette semaine / Plus ancien` sont bien
pensés pour la médiane de 13 articles par jour. À 309 ils ne séparent plus
rien : tu fais défiler, tu charges 20 de plus, tu recommences.

Le 19 novembre sera pire, et c'est **déjà arrivé une fois sans le 19
novembre**.

Trois pistes, de la moins à la plus ambitieuse :

- **Des paliers horaires qui apparaissent tout seuls** au-delà d'un certain
  volume quotidien : `Ce matin`, `Cette nuit`, `14 h – 18 h`. La mécanique
  des paliers existe (`bucketOrder`), il s'agit de la subdiviser sous
  condition. Le seuil de bascule est **à mesurer**.
- **Une barre de pic en tête de fil** les jours chargés : « 309 articles
  aujourd'hui, 6 sujets majeurs », avec un bouton qui saute aux six. Le
  jour où il s'est passé quelque chose, c'est la seule chose qu'on veut.
- **Replier les reprises par sujet.** 289 articles regroupent déjà
  plusieurs rédactions, et la carte le dit en bas en petit (« + 3 autres
  sources »). Les jours de pic, l'inverse aiderait : une carte par sujet,
  les reprises pliées dessous.

### 3.2 — « Ce que j'ai raté » : le point d'entrée qui manque

**La meilleure idée de la liste, et le meilleur amortisseur pour le jour J.**

Chaque ouverture te pose en haut d'une liste de 3 266 articles. Le chip
« Reprendre où j'en étais » restaure une *position* ; il ne dit pas ce qui
s'est passé.

Un bloc de cinq lignes en tête : les sujets importants parus depuis la
dernière visite et non ouverts. Toutes les données existent déjà —
`readSet`, le nombre de rédactions, le drapeau officiel, l'heure de
première vue. **C'est de l'assemblage, pas de la collecte.**

Un bon point d'entrée rend le mur de 1 000 articles supportable même sans
toucher aux paliers. C'est pour ça qu'il passe avant 3.1 dans l'ordre de
réalisation, alors que 3.1 est le problème plus grave.

### 3.3 — La carte porte sept informations à plat

Une carte peut afficher : vignette, favicon, nom de source, **jusqu'à six
badges** (majeur, officiel, spécialiste, fuite, vidéo, langue), titre, date,
ligne « + N sources », et **quatre boutons** d'action.

2 851 articles sur 3 266 ont une vignette : c'est le cas courant, pas
l'exception. Et rien ne hiérarchise — le badge de langue a le même poids
visuel que le badge « officiel », qui ne concerne que 51 articles sur
3 266.

**Pistes** : réserver la couleur pleine à l'officiel et au majeur, passer
langue et vidéo en gris muet ou en icône seule ; ne révéler les quatre
boutons qu'au survol ou à l'appui long, comme le fait déjà le balayage.

### 3.4 — Les 51 officiels méritent une forme, pas qu'un filtre

L'onglet Rockstar filtre déjà sur `official`, et ça marche. Mais il rend la
**même liste de cartes**, dans le même ordre, à la même densité. Or 51
articles depuis le début, c'est court : ça se lit comme une **chronologie**,
pas comme un fil. Une colonne verticale datée, du premier teaser à
aujourd'hui, serait la page qu'on montre à quelqu'un.

### 3.5 — La recherche n'a pas de mémoire

Champ, mot-clé, résultats. Pas d'historique, pas de recherche enregistrée.
Surveiller « date de sortie » ou « précommande » chaque semaine veut dire
le retaper chaque semaine. Trois ou quatre puces de recherches récentes
sous le champ coûtent peu.

### 3.6 — Une pastille de santé dans l'entête

`sources_health`, `sources_incidents`, `sources_incidents_compte`,
`decodage_etat`, les durées de passage : tout est dans `feed.json`, mais ne
se lit qu'en ouvrant Statistiques → Sources. Un point vert ou orange dans
l'entête dirait « le robot va bien » sans détour.

C'est exactement ce qui a manqué le 05/10, le soir où j'ai raconté
n'importe quoi sur une panne de `reddit-leaks` faute de savoir la lire.

---

### Ce que je recommanderais de faire en premier

**3.2, puis 3.1.** 3.2 est de l'assemblage pur, sert tous les jours, et
prépare le jour J mieux que n'importe quoi d'autre. 3.1 est le problème
plus grave mais demande une mesure avant de choisir les seuils.

3.3 à 3.6 sont du confort réel, sans échéance.

---

## 4. Ce que l'audit complet du 08/10/2026 a trouvé

Les 46 fichiers suivis, ~24 000 lignes. Analyse statique (AST Python,
extraction des 225 fonctions JS), relecture des chemins à risque, et
**mesure sur les données réelles** : 3 270 articles, 2 835 entrées de cache,
46 jours d'historique Git.

### Ce qui est sain — et c'est l'essentiel du résultat

**Code mort : zéro.** 225 fonctions JS, 88 fonctions Python de premier
niveau, toutes référencées. Aucun import inutilisé, aucun `except` nu,
aucun défaut mutable, aucun `TODO`.

**Intégrité des données : zéro anomalie** sur six contrôles indépendants de
`audit_donnees.py` — aucun lien hors http(s), **aucune collision de
`safeId`** sur 3 270 liens, aucune date aberrante, aucune entité HTML ni
mojibake dans les titres, aucune source orpheline, `extraSources` sain.
L'élagage de `source_link` est exact (0 article hors fenêtre n'en garde un),
et le cache de décodage est propre (0 valeur restée sur `news.google.com`).

`audit_donnees.py`, lui, a trouvé un vrai défaut que mes contrôles ne
cherchaient pas — voir **4.13**. C'est exactement ce pour quoi il existe.

**Trois pistes explorées qui ne sont PAS des problèmes**, à ne pas rouvrir :

- un lien `javascript:` ne peut pas devenir officiel (comparaison sur le
  nom d'hôte) ;
- un article promu majeur ne peut pas être élagué puis annoncé quand même
  (`item_protege` protège exactement la condition qui le rend majeur) ;
- `build_sources_health` et `echecs_decodage` ne rendent jamais `None` —
  faux positifs de mon analyseur (`return` nu dans une fonction imbriquée,
  `return` dans un `with`).

---

### 4.1 — Le filtre officiel diverge en mode de secours, sur LE mot qui compte

**Le constat le plus sérieux de l'audit, et le seul qui ait une échéance.**

`FEEDS` donne à la chaîne YouTube de Rockstar un mot-clé supplémentaire :

```python
"official_keywords_extra": ["trailer"],
```

Le commentaire juste au-dessus dit pourquoi : « Une vidéo intitulée
simplement *Trailer 3* ne contient aucun de ces mots-clés et serait
rejetée : **précisément le jour qui compte.** »

Le mode de secours de l'app (`filterFeedItems`, docs/index.html) n'a
**aucun mécanisme équivalent** : sa liste de six mots-clés officiels est
codée en dur, sans extension par source.

Le reste concorde — j'ai comparé : les six mots-clés de base sont
**identiques**, et les deux côtés filtrent sur le **titre seul**. L'écart se
réduit à ce seul mot, sur cette seule source. Mais c'est celui qui a été
ajouté exprès.

**Conséquence** : backend injoignable + vidéo appelée « Trailer 3 » =
l'app ne l'affiche pas. Deux conditions rares, que le mois de la sortie
maximise toutes les deux.

**Et rien ne le dirait.** `check_sources_sync.py`, dont c'est toute la
raison d'être, ne compare pas ce champ. Quatre champs de `FEEDS` lui
échappent : `garder_les_archives`, `max_entrees`, `tombe_la_nuit` (les trois
sans équivalent JS, légitimement) et `official_keywords_extra`.

**Correctif** : la donnée côté JS, **plus** la comparaison du champ dans
`check_sources_sync.py` pour que ça ne redérive pas.

---

### 4.2 — `readSet` n'a pas de plafond, et le message d'erreur conseille de détruire l'irremplaçable

`seenMap` est plafonné à 2 000 entrées, `lastItems` à 300, dans les deux cas
avec un commentaire citant la limite de 5 Mo. **`readSet` a échappé au même
traitement** : c'est la seule structure qui croît sans borne, et la seule
dont le contenu ne se reconstitue pas.

Mesuré, en UTF-16 (ce que compte réellement le navigateur) :

```
seenMap    plafonné à 2000   0,47 Mo
lastItems  plafonné à  300   0,53 Mo
readSet    NON PLAFONNÉ      1,67 Mo aujourd'hui (3 270 liens)
                             -> il reste 4,00 Mo, soit ~19 600 liens
```

| échéance | liens lus | empreinte |
|---|---|---|
| aujourd'hui | 3 270 | 1,67 Mo |
| 19 novembre | 7 932 | 2,62 Mo |
| 31 décembre | 12 594 | 3,57 Mo |
| 1er mars 2027 | 19 254 | **4,93 Mo** |

À 111 articles/jour le quota tombe vers **début mars**. Mais si la sortie
fait monter le volume : à 600/jour, **27 jours** ; à 1 000/jour, **16
jours**. Le pire cas n'est pas théorique — « Tout marquer lu » ajoute
jusqu'à 500 liens d'un geste (`maxDisplay` vaut 500 par défaut et monte à
20 000).

**Le vrai problème est ce qui se passe ensuite.** Quand l'écriture échoue,
`storageSet` affiche : « Stockage local plein — […] **Vide les données du
site** pour repartir sur une base saine. » Suivre ce conseil détruit
`readSet` définitivement. La sauvegarde existe et contient bien `lus` (et
exclut correctement le jeton GitHub) — mais **le message ne la mentionne
pas**, et elle est dans un autre panneau. Au moment précis où il faut
exporter, l'app dit de supprimer.

**Correctif immédiat** : une phrase dans ce message. Le plafonnement de
`readSet` lui-même demande une mesure — borner sans faire réapparaître des
articles comme non lus, par exemple en ne gardant que les liens encore
présents dans le fil ou l'archive.

---

### 4.3 — `merge_feed.py` ne fusionne pas les champs cumulatifs ajoutés depuis

Le script de reprise après conflit de push fait `merged = dict(ours)` et ne
traite explicitement que **trois clés** : `items`, `total_articles`,
`attente_recap`.

Pour `attente_recap`, le piège est identifié et commenté sur place : « c'est
un CUMUL, et le nôtre a été calculé à partir d'un feed.json que le distant a
entre-temps dépassé ». Le même raisonnement s'applique maintenant à quatre
clés postérieures au script :

```
sources_incidents           cumulatif — le distant est perdu
sources_incidents_compte    cumulatif — le distant est perdu   (livré le 07/10)
sources_entries_history     cumulatif — le distant est perdu
sources_silence             cumulatif — le distant est perdu
```

Gravité faible : le workflow sérialise ses exécutions (`concurrency`) et le
checkout force `ref: main`. Mais c'est le même angle mort, laissé ouvert
pour les champs d'après.

---

### 4.4 — Le cache de décodage est perdu après un conflit de push

Dans la boucle de reprise de `update-feeds.yml` :

```bash
git reset --hard origin/main        # efface tout ce que le passage a écrit
python merge_feed.py ...            # réécrit feed.json, feed-recent.json, docs/archives
git add docs/feed.json docs/feed-recent.json docs/archives decode-cache.json
```

Le commentaire raisonne explicitement sur les archives effacées par le
`reset` (« ici elle compte double ») — mais `decode-cache.json`, ajouté à
cette ligne plus tard, n'est **pas** réécrit par `merge_feed.py`. Le
`git add` porte donc sur la version de `origin/main` : **c'est un
non-opérant**, et les liens décodés pendant le passage sont perdus.

Sans gravité pour le fil publié (les liens décodés sont déjà dans les
articles) : le passage suivant re-décode. Mais c'est du réseau payé deux
fois, sur le composant qui est précisément tombé le 3 octobre.

Deux autres asymétries du même chemin, auto-réparées au passage suivant :
`merge_feed.py` archive **sans** le `exclure=` que `main()` applique (pages
Rockstar hors langue, cotations crypto), et n'élague pas `source_link`.

---

### 4.5 — `main()` : 493 lignes, complexité ≈ 82, jamais exécutée par un test

De loin la plus grosse fonction du dépôt — trois fois la suivante
(`collect_feed_items`, 302 lignes). Elle décide de tout ce qui est publié.

**Aucun test ne l'exécute.** Deux tests ouvrent `fetch_feeds.py`, découpent
le texte à partir de `def main(` et vérifient que des chaînes s'y trouvent :

```python
check('"sources_incidents": journal_incidents' in corps, ...)
```

Tout ce qu'elle orchestre est bien testé **isolément** ; l'orchestration,
non. Un test qui lit du texte ne peut attraper ni un ordre d'opérations
inversé, ni une mauvaise variable passée, ni un chemin d'exception.

La fragilité est vécue, pas théorique : le 07/10, renommer une variable
locale de `compte_incidents` en `compteur_incidents` a cassé un test alors
que le comportement était rigoureusement identique.

`main()` prend ses entrées de `load_feed()` et du réseau, et
`fetch_all_feeds` a déjà un paramètre `collecte` injectable : **un test qui
l'exécute de bout en bout sur un faux fil est à portée.**

---

### 4.6 — 55 assertions portent sur du texte source

38 tests sur 123 inspectent du texte plutôt que du comportement. Une partie
est légitime — le README ne citant que des constantes réelles, les workflows
épinglant leurs dépendances, les contrastes CSS : là, **le texte EST
l'artefact**.

Mais une bonne part pose des assertions sur le JS
(`"demandeConfirmation(" in corps`, `"readSet.has(i.link)" in corps.split(…)`)
alors qu'une suite navigateur de 331 contrôles existe et pourrait les
vérifier en les exécutant. Coût : un renommage anodin casse un test vert, et
une régression qui préserve le texte passe.

---

### 4.7 — `hot_count` est publié et lu par personne

Écrit une fois (`fetch_feeds.py:4195`), **relu nulle part** : ni le robot,
ni l'app, ni Discord, ni le push, ni l'audit, ni un test. Vestige de la
ligne d'état, qui a fini par n'afficher que durée / nouveaux / sources.

Deux octets : le coût n'est pas la place. Le coût est que le README
(ligne 2278) décrit un test qui « compare `total_articles` **et
`hot_count`** au fichier complet » — or **ce test ne couvre que
`total_articles`**. La documentation décrit une protection à moitié
existante.

---

### 4.8 — Dérive documentaire

La docstring de `fetch_feeds.py`, les vingt premières lignes du fichier
principal, celles qu'on lit en premier :

| écrit | réel |
|---|---|
| « les mêmes **35** que dans le tracker HTML » | **63** sources (35 est le nombre de *mots-clés*) |
| « contacter **34** flux » | **63** |

Et `.github/dependabot.yml` : « une montée de version qui casserait les
**130** vérifications » → **1 709**.

Le garde-fou contre ce genre de dérive existe
(`test_readme_ne_cite_que_des_constantes_reelles`) mais sa portée s'arrête
aux **constantes du README** : ni les nombres, ni les docstrings des autres
fichiers.

---

### 4.9 — 21 vignettes ne s'afficheront jamais

21 articles portent une image en `http://`. L'app est servie en HTTPS :
c'est du **contenu mixte**, le navigateur les bloque. `onerror` les masque
proprement, donc ça dégrade bien — mais ce sont 21 cartes sans vignette pour
une raison purement mécanique. Presque toutes viennent de deux domaines
(`gamekyo.com`, `geeknplay.fr`) et se réduisent à ~5 URL distinctes.

Cinq articles ont aussi un **lien** en `http://`, dont deux sur
`store.rockstargames.com` — correctement reconnus officiels (la comparaison
porte sur l'hôte), donc sans incidence.

---

### 4.10 — `.gitignore` ne couvre pas `.env`

Deux lignes : `__pycache__/` et `*.pyc`. Rien n'empêche un `.env` local de
partir dans un dépôt **public**. Rien n'a jamais fuité — vérifié sur tout
l'historique — mais c'est la protection la moins chère du dépôt, et elle
manque.

---

### 4.11 — PyPI est épinglé à l'exact, les Actions au tag majeur

`requirements.txt` fige **tout le graphe**, avec trente lignes de
commentaire expliquant pourquoi (l'incident `selectolax` du 03/10). Les
workflows, eux, utilisent `actions/checkout@v7` — un tag mobile. Dependabot
surveille les deux, mais il signale une *nouvelle version*, pas un *retag*
de `v7`. Seule asymétrie avec la politique affichée du dépôt.

---

### 4.12 — Le garde-fou de publication ne protège que les articles

`valide_avant_ecriture` vérifie la structure, les liens, les titres, les
doublons et la perte massive — **sur `items` uniquement**. Un bug qui
viderait `sources_incidents` ou ferait reculer le compteur publierait en
silence. Choix de portée défendable (les articles sont le produit), mais il
mérite d'être nommé.

---

### 4.13 — Trois articles vivent dans deux tranches d'archive à la fois

**Le seul défaut VIVANT trouvé par l'audit** — et c'est `audit_donnees.py`
qui l'a signalé tout seul, ce qui est plutôt une bonne nouvelle :

```
⚠ L'index annonce un total différent de ce qu'il contient
     index : 4123 articles
     fichiers : 4120 articles
```

Trois liens apparaissent dans **deux tranches mensuelles**, avec **deux
dates différentes** :

| lien | tranche | date |
|---|---|---|
| `lacremedugaming.fr/…-boite-v` | `2026-09.json` | 10/09 |
| | `2026-10.json` | 07/10 |
| `lacremedugaming.fr/…-7-nouvelle` | `2026-09.json` | 10/09 |
| | `2026-10.json` | 07/10 |
| `mashable.com/…netflix-extended-look` | `2026-08.json` | 27/08 |
| | `2026-10.json` | 07/10 |

**Mécanisme** : la date de l'article a changé entre deux archivages (la
source a republié avec un nouveau `pubDate`). `archiver` l'écrit dans
`mois_de(item)`, donc dans la tranche du **nouveau** mois, et **rien ne le
retire de l'ancienne** : son `exclure=` « ne s'applique qu'aux mois que
`items` touche », et l'ancien mois n'est plus jamais relu.

**Ce que ça fait vraiment** : l'app déduplique sur le lien au chargement
(`connus.has(item.link)`), donc **aucune carte en double à l'écran**. Mais
elle garde la **première** occurrence rencontrée : pour ces trois articles,
la date affichée dépend de l'ordre de chargement des mois. Et l'index
annonce 3 articles et un poids qui n'existent pas.

3 sur 4 120, soit 0,07 % — mais ça **s'accumule**, et c'est apparu entre le
07 et le 08/10.

---

### 4.14 — Une décision à prendre : les pages Rockstar en italien et en espagnol

`audit_donnees.py` signale deux préfixes de langue jamais vus, avec la
consigne « à ajouter à `LANGUES_ROCKSTAR_ECARTEES` si ce n'est ni de
l'anglais ni du français » :

```
/it/ : 1 lien   store.rockstargames.com/it/…/buy-gta-vi-album-standard-vinyl
/es/ : 1 lien   rockstargames.com/es/newswire/…/the-music-of-grand-theft-auto-vi…
```

**Suivre cette consigne aurait coûté du contenu.** J'ai vérifié : les
équivalents anglais de ces deux annonces **ne sont PAS dans le fil**. Ces
pages localisées en sont la seule copie. `LANGUES_ROCKSTAR_ECARTEES` ne
vaut que `('de', 'mx')` — c'est une liste de REFUS, pas une liste
d'autorisation, et c'est précisément ce qui a évité la perte.

**Le vrai risque est en novembre.** Rockstar publiera en huit langues, et
chaque page localisée est `official: True`, donc une notification. Le
`NOTIFS_OFFICIELLES_MAX = 5` plafonne les notifications, mais rien ne
regrouperait les huit pages dans le fil : les URL diffèrent, et la fusion
par similarité de titre ne les attrapera que si le titre reste en anglais
(ce qui est le cas de ces deux-là, pas forcément des suivantes).

**Décision à prendre, pas un bug** : ne rien faire (on garde tout, au risque
de voir la même annonce huit fois), ou regrouper les pages Rockstar sur
l'identifiant d'article plutôt que sur l'URL complète. À mesurer sur
l'historique avant de choisir.

---

### L'ordre dans lequel je ferais ça

1. **4.1 — le mot `trailer` en mode secours.** Petit, daté, et c'est le
   scénario du jour de la sortie.
2. **4.2 — la phrase du message de quota.** Ajouter « exporte d'abord ta
   sauvegarde » avant « vide les données du site ». Le plafonnement de
   `readSet` demande une mesure et peut attendre ; la phrase, non.
3. **4.8 et 4.10** — dix minutes à deux : la docstring, le
   `dependabot.yml`, et `.env` dans `.gitignore`.
4. **4.5** — le test qui exécute `main()`. Le plus gros chantier, et le seul
   qui change durablement la confiance qu'on peut avoir dans un passage.

Puis **4.13** (trois articles en double dans l'archive) et **4.14** (la
décision sur les pages Rockstar localisées, à mesurer avant novembre).

4.3, 4.4, 4.6, 4.7, 4.9, 4.11 et 4.12 sont réels mais sans urgence.

---

## Ce qui est RÉGLÉ et ne doit pas être rouvert

### Le mode hors-ligne : vérifié, il marche

Mesuré le 06/10 : réseau coupé, rechargement, **la page s'ouvre, 30 cartes
affichées, zéro erreur JavaScript**. Pas de page blanche dans le métro.

300 articles disponibles hors ligne au lieu de 3 266, et c'est voulu :
`MAX_PERSISTED_ITEMS = 300`. Sans ce plafond, localStorage dépasserait son
quota de 5 Mo et une écriture sur deux échouerait en silence. **Ne pas y
toucher.**

### La croissance du dépôt : fausse alerte, puis mauvais thermomètre

**Premier temps.** J'avais annoncé le 06/10 au matin « 68 Mo en 21 jours,
~97 Mo par mois ». C'était faux : je mesurais `.git` en incluant les objets
non compactés, que Git rassemble ensuite. J'ai corrigé en me reportant au
chiffre de l'API GitHub — 35 Mo — en écrivant ici qu'il était « **la seule
qui compte** ».

**Second temps, l'audit du 08/10.** Ce chiffre-là ne compte pas non plus :
l'API annonçait **53 Mo** deux jours plus tard, soit +50 % en 48 h, pendant
que le contenu réel n'avait pas bougé. C'est de la comptabilité
serveur avant ramassage, pas de la croissance.

**Le bon thermomètre est le paquet local**, et il est stable :

```
git count-objects -vH  ->  size-pack: 28,44 Mio   (46 jours)
```

Croissance réelle mesurée sur cinq jours : **~130 Mo de blobs bruts par
jour**, que la compression delta ramène à **~0,6 Mo/jour** empaqueté — ce
qui confirme l'ordre de grandeur annoncé le 06/10 (0,7 Mo/jour). `feed.json`
est réécrit en entier à chaque passage mais change très peu : Git ne stocke
que l'écart.

À la sortie : ~40 Mo empaquetés. GitHub est à l'aise jusqu'à 1 Go. **Rien à
faire** — et si on regarde un jour, c'est `size-pack` qu'on lit, pas l'API.

### Reddit : on n'y touche plus (décision du 06/10)

Deux sources, zéro 429 sur 120 passages, 22 articles exclusifs. Trois portes
fermées, et pour de bonnes raisons :

- **élargir les mots-clés de r/GTA6** — la sonde a tranché le 05/10 ;
- **une troisième source Reddit** — impossible sans OAuth : le tour de rôle
  ferait déjà tomber chaque source à une interrogation par heure, à trois ce
  serait une toutes les 90 minutes ;
- **OAuth « app-only »** — `oauth.reddit.com` rend du JSON et non du RSS, il
  faudrait un lecteur Reddit à côté de `feedparser`. À rouvrir seulement si
  Reddit ferme le RSS public, ou si tu veux plus de deux sources.

### Le chargement automatique de l'archive : choix assumé

`rattrapeToutEnArrierePlan()` télécharge l'archive **en entier, à chaque
ouverture de l'app**. Antoni a choisi de ne pas le borner, en connaissance
du revers : en décembre, l'app prendra plusieurs mégaoctets toute seule à
chaque ouverture.

Le jour où ça gêne, c'est **`rattrapeToutEnArrierePlan` qu'il faut borner**,
pas la ligne d'archive — celle-ci a déjà ses boutons par mois.

### Le graphique mensuel

Apparaîtra tout seul le 1er novembre, quand il y aura deux mois complets à
comparer. **Rien à faire.**

---

## Ce que la mesure a fait ABANDONNER

À ne pas reproposer sans chiffres nouveaux :

| idée | pourquoi non |
|---|---|
| Ne plus stocker les booléens `false` | 166 Ko bruts, mais **6 Ko gzippés (0,6 %)**. La compression efface déjà les booléens répétés. |
| Couper tout suffixe « - X » des titres | 60 paires au lieu de 43, mais **cinq vrais contenus détruits** — dont la série RockstarMag que `titres_dune_meme_serie` existe pour tenir séparée. |
| Filtrer la crypto par mots-clés | 43 titres contiennent « price / charts / marketcap », **41 sont légitimes**. Le filtre est sur le domaine. |
| Descendre le seuil de ressemblance sous 0,72 | 0,72 est la limite de ce qui a été **relu article par article**. En dessous, personne ne peut dire. |
| Élargir les mots-clés de r/GTA6 | sonde du 05/10. |

---

## Chiffres de référence au 08/10/2026, 06h00 Paris

Le fil bouge d'heure en heure : ces chiffres sont un instantané, pas des
constantes. Ceux cités dans la section 4 sont ceux mesurés **au moment de
l'audit**, et ne bougent plus.

| le fil | |
|---|---|
| articles dans le fil | 3 222 |
| articles dans l'archive | 4 123 |
| **jour le plus chargé jamais vu** | **309 articles le 17/09**, dont 6 majeurs (2 %) |
| durée d'un passage | 34,6 s |
| chargement complet sur téléphone | **1,49 Mo** gzip (2,29 Mo avant le 06/10) |
| liens dupliqués | 0 |
| articles regroupant plusieurs rédactions | 291 (jusqu'à 5 sources) |
| articles officiels | 62 |
| articles avec vignette | 2 840 |
| sources | 63 |
| mots-clés | 35 |

| le dépôt | |
|---|---|
| `size-pack` (**la mesure à suivre**) | **28,44 Mio** — ~0,6 Mo/jour |
| taille annoncée par l'API GitHub | 53 Mo — *instable, à ne pas suivre* |
| fichiers suivis / lignes | 46 / ~24 000 |
| fonctions JS / mortes | 225 / **0** |
| fonctions Python de premier niveau (`fetch_feeds`) | 88 |
| `main()` | 493 lignes, complexité ≈ 82, **0 test l'exécute** |

| les contrôles | |
|---|---|
| `test_pipeline` | 123 tests, 1 709 vérifications |
| `test_navigateur` | 331 contrôles |
| tests inspectant du texte source | 38 (55 assertions) |

| les constantes | |
|---|---|
| `CACHE_NAME` | `gta6watch-shell-v18` |
| `SIMILARITY_THRESHOLD` | 0,72 |
| `HOT_SOURCE_THRESHOLD` | 3 |
| `MAX_PERSISTED_ITEMS` | 300 |
| `MAX_SEEN_ENTRIES` | 2 000 |
| `readSet` | **aucun plafond** — voir 4.2 |
| `DEAD_SOURCE_HOURS` | **12** (24 jusqu'au 07/10) |
| `INCIDENTS_DUREE_MIN_H` | 3,0 |
| `JOURS_SOURCE_LINK_PUBLIE` | 7 |

---

## Ce qui a été livré le 06/10/2026

Sept fusions : #132, #133 (plan), #134, #135, #136, #137, #138, #139.

1. **Réparation rétroactive** des 851 articles abîmés par la panne du
   décodeur — 0 lien `news.google.com` restant, 251 doublons cachés retirés.
2. **Le cache de décodage sort du réseau** — chargement complet 2,29 → 1,49 Mo.
3. **L'archive se charge mois par mois.**
4. **Journal des incidents** — « aucun incident depuis 30 jours » s'affiche
   même quand il est vide, et c'est tout son intérêt.
5. **La déduplication passe de 20,1 s à 2,4 s** par passage.
6. **Les reprises d'une même dépêche se rejoignent** — 22 paires → 43.
7. **Le seuil descend à 0,72**, après rejeu complet des deux corpus.

---

## Ce qui a été livré le 07/10/2026

**Le journal des incidents, repris le lendemain de sa livraison.** Antoni a
demandé pourquoi la panne du décodeur n'y figurait pas. Réponse : le journal
est chronologique, il n'inscrit un incident qu'à sa clôture, et le décodeur
s'était rétabli quinze heures avant que le journal n'existe.

La question en a ouvert une autre. Les **630 versions de `feed.json`** ont
été rejouées : **319 pannes terminées en 21 jours**, soit **449 par mois
pour un plafond de 100**. Le journal livré la veille se serait rempli en une
semaine et « aucun incident depuis 30 jours » ne se serait plus jamais
affiché — la fonctionnalité s'annulait elle-même.

1. **La liste ne retient que les incidents de plus de 3 h**, et jamais les
   nuits YouTube, déclarées `tombe_la_nuit` dans `FEEDS`. Il en reste 8 par
   mois au lieu de 449.
2. **Un compteur par source et par mois** prend tout le reste, hoquets
   compris — sinon une source qui tombe onze fois par jour sans jamais
   passer trois heures redeviendrait invisible.
3. **Les sept incidents notables d'avant le journal ont été semés**, dont la
   panne du décodeur, redatée sur `decode_failures` : 61,6 h et non 62,2.
4. **`DEAD_SOURCE_HOURS` passe de 24 à 12 h.** La plus longue des 319 pannes
   dure 8 h 30 : le seuil de 24 h ne pouvait pas se déclencher.

---

## Ce qui a été fait le 08/10/2026

**L'audit complet** des 46 fichiers suivis — aucune ligne de code modifiée.
Le résultat est la section 4 ci-dessus : quatorze constats, et surtout la
confirmation mesurée que l'essentiel est sain (zéro code mort, zéro anomalie
de données, surface XSS close, aucun secret dans tout l'historique).

Il clôt au passage la **revue de sécurité** (section 2), qui était ouverte
depuis la création de ce fichier. Il reste un seul point qui ne se vérifie
pas depuis le dépôt : la portée réelle du PAT cron-job.org.

Deux chiffres de ce fichier étaient faux et sont corrigés : la taille du
dépôt (l'API GitHub n'est pas un thermomètre) et le nombre de contrôles
navigateur.
