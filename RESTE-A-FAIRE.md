# Ce qu'il reste à faire

**Rien n'est cassé, rien n'est urgent.** Ce fichier existe pour qu'on reprenne
plus tard sans redériver ce qui a déjà été mesuré.

Écrit le 06/10/2026 au soir, complété le 07/10 au matin.
Base : `main` après la PR #141.
**GTA 6 sort le 19 novembre 2026, soit dans 43 jours.**

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

## 2. La revue de sécurité

Jamais faite formellement. Le dépôt est **public** et manipule :

- un PAT GitHub (cron-job.org), censément limité à `gta6-backend` et à
  « Contents: Read and write » ;
- les clés VAPID (la privée en secret, la publique dans `feed.json`) ;
- `PUSH_SUBSCRIPTIONS` ;
- `DISCORD_WEBHOOK_URL`, qui doit rester masquée dans les journaux ;
- `HEALTHCHECK_URL`.

**Ce qui a déjà été vérifié au passage** (06/10, en analysant autre chose) :
aucun champ d'article n'est interpolé dans du HTML sans `escapeHtml`, et
`safeId` est du base64 filtré en alphanumérique. L'injection par un titre
d'article hostile est donc couverte.

**Ce qui ne l'a pas été** : les permissions déclarées des workflows, la
surface du service worker, la portée réelle du PAT, ce que `feed.json`
expose par inadvertance.

Il existe un outil de revue dédié dans l'environnement.

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

## Ce qui est RÉGLÉ et ne doit pas être rouvert

### Le mode hors-ligne : vérifié, il marche

Mesuré le 06/10 : réseau coupé, rechargement, **la page s'ouvre, 30 cartes
affichées, zéro erreur JavaScript**. Pas de page blanche dans le métro.

300 articles disponibles hors ligne au lieu de 3 266, et c'est voulu :
`MAX_PERSISTED_ITEMS = 300`. Sans ce plafond, localStorage dépasserait son
quota de 5 Mo et une écriture sur deux échouerait en silence. **Ne pas y
toucher.**

### La croissance du dépôt : fausse alerte, de ma part

J'avais annoncé le 06/10 au matin « 68 Mo en 21 jours, ~97 Mo par mois ».
**C'était faux** : je mesurais `.git` en incluant les objets non compactés,
que Git et GitHub rassemblent ensuite.

```
.git avant compactage        88 Mo
.git après compactage        25 Mo
taille déclarée par GitHub   35 Mo   ← la seule qui compte
```

44 jours d'existence, 35 Mo, soit **~24 Mo par mois**. Coût réel par
version : `feed.json` 10 ko, `feed-recent.json` 7 ko, `decode-cache.json`
**0 ko** (l'écriture triée fait son travail). Environ 0,7 Mo par jour.

À la sortie : ~55 Mo. GitHub est à l'aise jusqu'à 1 Go. **Rien à faire**,
juste à regarder une fois après novembre.

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

## Chiffres de référence au 07/10/2026, 06h00 Paris

| | |
|---|---|
| articles dans le fil | 3 266 |
| articles dans l'archive | 3 672 |
| **jour le plus chargé jamais vu** | **309 articles le 17/09**, dont 6 majeurs |
| articles par jour, en médiane | 13 |
| durée d'un passage | **22 s** (56 s avant les correctifs du jour) |
| chargement complet sur téléphone | **1,49 Mo** gzip (2,29 Mo avant) |
| liens dupliqués | 0 |
| articles regroupant plusieurs rédactions | 289 (jusqu'à 5 sources) |
| articles officiels | 51 |
| articles avec vignette | 2 851 |
| sources | 63 |
| `test_pipeline` | 1 709 vérifications |
| `test_navigateur` | 331 contrôles |
| taille du dépôt (GitHub) | 35 Mo |
| `CACHE_NAME` | `gta6watch-shell-v18` |
| `SIMILARITY_THRESHOLD` | 0,72 |
| `HOT_SOURCE_THRESHOLD` | 3 |
| `MAX_PERSISTED_ITEMS` | 300 |
| `DEAD_SOURCE_HOURS` | **12** (24 jusqu'au 07/10) |
| `INCIDENTS_DUREE_MIN_H` | 3,0 |

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
