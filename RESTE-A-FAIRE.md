# Ce qu'il reste à faire

**Rien n'est cassé.** Ce fichier existe pour qu'on reprenne plus tard sans
redériver ce qui a déjà été mesuré.

Deux choses ne sont plus « sans urgence » depuis l'audit du 08/10, et les
deux se corrigent en quelques lignes : **4.1** (un mot-clé manquant au mode
de secours, sur le scénario exact du jour de la sortie) et **4.2** (le
message de quota conseille de détruire l'état de lecture). Tout le reste
attend.

Écrit le 06/10/2026 au soir, complété le 07/10, le 08/10 par un audit
complet des 46 fichiers (section 4), et le 09/10 (3.6 annulée, 3.7 et 3.8
ajoutées, onglet « Actu majeure » livré).
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

**MESURÉ LE 09/10/2026 — ET LE SEUIL TIENT.** 4 203 articles uniques, fil
et archive, sur les 32 jours de couverture complète.

| journées | nb | part de majeurs, médiane | max |
|---|---|---|---|
| moins de 100 articles | 17 | 0,0 % | 2,4 % |
| 100 à 149 | 8 | 1,3 % | 3,4 % |
| **150 et plus** | 7 | **1,4 %** | **3,8 %** |

Corrélation entre le volume du jour et la part de majeurs : **r = +0,32**.
Faible. En appliquant la pire part observée au volume d'un jour de sortie :

```
 300 articles/jour ->  3 majeurs (médiane) ...  11 (au pire observé)
1000 articles/jour ->  9 majeurs            ...  38
2000 articles/jour -> 19 majeurs            ...  75
```

Même au pire, une liste de 38 reste lisible — vingt-cinq fois plus courte
que le fil. **`HOT_SOURCE_THRESHOLD` reste à 3, et la question est close.**

**Ce que la mesure a sorti d'autre, et qui compte plus.** Le taux de
regroupement (2 rédactions ou plus) **ne suit pas le volume** : 6,4 % en
médiane les petites journées, 7,0 % les grosses. Ce qui le fait bouger,
c'est l'ÉVÉNEMENT, pas le nombre d'articles :

```
17/09   315 articles   13,7 % regroupés   (Twitch CEO)
24/09   212 articles   19,3 %             (coffret 400 $)
02/10   200 articles   15,5 %             (Miami HEAT)
18/09   182 articles    4,4 %             rien de fédérateur
07/10   188 articles    4,8 %             idem
08/10   285 articles    7,0 %             idem
```

**Le badge mesure donc la bonne chose** : la convergence éditoriale, pas le
volume. C'est précisément ce qu'on lui demande.

**Mais le rappel de la déduplication est le vrai plafond.** Le 08/10, 265
des 285 articles sont restés seuls, et parmi eux **24 paires se recouvrent
à 50 % ou plus**. Deux vraies reprises manquées, vérifiées à la main :

```
0,717  « GTA VI to include Metal themed radio station hosted by SLAYER's… »
       « 'Grand Theft Auto VI' To Feature An In-Game Metal Radio Station… »
```

**0,717 pour un seuil à 0,72.** Trois millièmes.

La cause n'est pas le seuil, c'est que **le nom du jeu n'est pas ramené à
une forme unique avant la comparaison** : « GTA VI » et « Grand Theft Auto
VI » font diverger deux titres qui disent la même chose. `_MOTS_SANS_VALEUR`
le fait déjà, mais seulement pour l'appariement par mot rare, pas pour la
similarité elle-même. Mesuré sur les deux paires manquées : unifier le nom
en ferait passer **une** (0,717 → 0,769, fusionnée), pas l'autre (0,595 →
0,705).

**Piste pour 3.1, pas pour le seuil.** Et elle exige le même rejeu complet
des deux corpus que le passage à 0,72 : améliorer le signal n'est pas
abaisser la barre, mais ça déplace quand même ce qui fusionne.

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

**Le dernier point est CLOS le 20/10/2026.** Antoni a ouvert
`github.com/settings/personal-access-tokens` et montré les deux jetons à
portée fine. Les deux sont limités au seul dépôt `antoniman31/gta6-backend`,
sans aucune permission utilisateur :

| jeton | expire | permissions dépôt |
|---|---|---|
| `horloge-gta6` (cron-job.org) | 28/08/2027 | métadonnées en lecture + **code en lecture-écriture** |
| `gta6watch mobile` (bouton « Relancer le robot ») | **26/11/2026** | métadonnées en lecture + **actions en lecture-écriture** |

`horloge-gta6` est exactement ce que cette revue demandait. Le second
n'était pas couvert par la revue et se révèle plus serré encore : il
déclenche le workflow mais **ne peut pas écrire de code**, ce qui est le
minimum pour ce qu'il fait.

> **À surveiller** : `gta6watch mobile` expire le **26 novembre 2026**,
> soit une semaine après la sortie. La nuit du 19 est couverte ; le bouton
> « Relancer le robot » cessera de fonctionner huit jours plus tard, sans
> avertissement.

**Deux durcissements à faire** (voir 4.10 et 4.11) : `.gitignore` ne couvre
pas `.env`, et les Actions sont épinglées au tag majeur alors que PyPI est
épinglé à l'exact.

---

## 3. Interface et expérience — huit pistes, par ordre d'intérêt

Relevé le 07/10/2026 en relisant `docs/index.html` en entier.

**D'abord, ce qui existe DÉJÀ** et qu'il ne faut pas reproposer : le
balayage sur les cartes (`swipe-wrap`, `surTouchStart`), le tirage pour
rafraîchir, le chip « Reprendre où j'en étais », le mode compact, les trois
modes de thème, l'avertissement quand une recherche porte sur un
historique incomplet (`historyPartial`), et l'onglet Rockstar qui filtre
bien sur `item.official` — le « fil officiel » existe comme filtre.

---

### 3.1 — Le mur du jour J

> **FAIT le 13/10/2026 — et deux des trois pistes ci-dessous sont mortes à la mesure.** Replier les reprises n'absorbe que 10,6 % du volume (91 % des articles n'ont qu'une rédaction) : le pire jour passe de 290 à 247. La barre de pic annoncerait « 0 sujet majeur » un jour de pic, « majeur » étant à 0 % pendant trois jours. Et les tranches horaires FIXES donnent 23 en-têtes sur une journée normale tout en laissant 177 articles dans un palier le 19 novembre. Livré : un découpage **adaptatif à la minute**, cible 30, seuil 60, sur « Aujourd'hui » et « Hier ». Le pire jour réel (290) devient 10 en-têtes, aucun groupe au-dessus de 30, la nuit en un bloc de dix heures et le pic en tranches de vingt minutes. 11 contrôles de navigateur. Voir le README, « Le mur du jour J ».

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

> **FAIT le 13/10/2026, mais PAS comme décrit ci-dessous.** La mesure a contredit le plan : « majeur » est à 0 % pendant trois jours (signal rétrospectif), les articles ne se regroupent pas en sujets (119 → 117 groupes), et un top 5 par score servait quatre fois la même fuite avec 10 à 23 articles à égalité avec le cinquième. Le bloc livré est donc un **bilan chiffré toujours vrai** (minoré explicitement quand l'historique partiel ne couvre pas l'absence) suivi de **jusqu'à quatre lignes des sources suivies** — officiels, spécialistes, RockstarMag. Il rétrécit les jours creux au lieu de se remplir, et n'apparaît pas à la première visite. Nouvel horodatage `derniere-visite-v1`. 12 contrôles de navigateur. Voir le README, « Ce que j'ai raté, et les trois idées que la mesure a tuées ».

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

> **CLOSE SANS TRAVAUX le 13/10/2026 — la fiche était fausse.** Mesuré sur 3 511 articles : **52 % des cartes n'affichent AUCUN badge**, 40 % en affichent un, et **3,1 % seulement en portent trois ou plus** (maximum observé : quatre, jamais six). Le badge de langue, désigné comme fautif, n'apparaît que pour le français et il est **déjà** en gris muet (`.tag-lang` : fond transparent, `--text-dim`). Toucher à la hiérarchie visuelle pour 3 % des cartes serait du risque sans bénéfice.

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

**Légèrement aggravé le 09/10** : le badge est passé de « 🔥 3 SOURCES » à
« 🔥 ACTU MAJEURE · 3 SOURCES » pour s'aligner sur l'onglet. Sur une carte
qui cumule majeur + officiel, les badges tiennent maintenant sur deux
rangs au lieu d'un. Vérifié au navigateur : ça reste lisible, et la
hiérarchie se lit même mieux — mais c'est une ligne de plus, et ce point-ci
est celui qui en comptait déjà sept.

### 3.4 — Les 51 officiels méritent une forme, pas qu'un filtre

> **FAIT le 13/10/2026.** Ils sont 77 aujourd'hui. L'onglet Rockstar se rend en colonne datée, séparée par année (2022 → 2026), sans pagination : une histoire se lit d'un bout à l'autre. Aucun nouvel onglet. **Signalé au passage, non tranché** : 28 des 77 (36 %) sont des pages `store.rockstargames.com` — porte-clés, t-shirts — et 5 des pages de support. Les écarter serait une décision de contenu, et une page boutique a déjà été gardée délibérément le 09/10.

L'onglet Rockstar filtre déjà sur `official`, et ça marche. Mais il rend la
**même liste de cartes**, dans le même ordre, à la même densité. Or 51
articles depuis le début, c'est court : ça se lit comme une **chronologie**,
pas comme un fil. Une colonne verticale datée, du premier teaser à
aujourd'hui, serait la page qu'on montre à quelqu'un.

### 3.5 — La recherche n'a pas de mémoire

> **FAIT le 13/10/2026.** Quatre puces sous le champ, remplies après le débounce (pas à chaque frappe), insensibles à la casse, chacune effaçable. Moins de trois caractères n'est pas mémorisé : c'est une frappe en cours.

Champ, mot-clé, résultats. Pas d'historique, pas de recherche enregistrée.
Surveiller « date de sortie » ou « précommande » chaque semaine veut dire
le retaper chaque semaine. Trois ou quatre puces de recherches récentes
sous le champ coûtent peu.

### 3.6 — ~~Une pastille de santé dans l'entête~~ — ELLE EXISTE DÉJÀ

**Annulée le 09/10/2026. C'est moi qui avais écrit cette idée, et elle
partait d'une affirmation fausse** : « tout est dans `feed.json`, mais ne se
lit qu'en ouvrant Statistiques → Sources ». Non. L'entête le dit déjà, et
plutôt bien :

| ce qui existe | où | quand |
|---|---|---|
| `● Backend GitHub` / `● Mode direct` | entête, à droite | toujours |
| `26 s · 9 nouveaux · 63/63 sources` | sous les boutons | toujours |
| le compteur devient un **bouton rouge dépliable** | même ligne | dès qu'un souci apparaît |
| `N muettes · N cassées : IGN, Kotaku… · N en forte baisse · N décodages en échec` | ligne du dessous | seulement s'il y a un souci, et il se souvient si on l'avait déplié, par *signature* du problème |
| bandeau pleine largeur « aucune mise à jour depuis N h » | haut de page | si `generated_at` dépasse **4 h** |

Le seuil de 4 h porte même trois calibrations documentées (2 h → 7 h → 4 h).
Et le jour de la panne du décodeur, la ligne des soucis aurait affiché
« 126 décodages Google News en échec » : la donnée y était.

**Rien à construire.** La leçon est pour moi : c'est la deuxième idée de ce
fichier à ne pas survivre à la lecture du code — après « un fil officiel
seulement », qui était déjà l'onglet Rockstar. Les deux ont été écrites sans
avoir ouvert `docs/index.html` assez loin.

---

### 3.7 — Les 63 interrupteurs de source ne font RIEN en mode backend

> **FAIT le 13/10/2026.** La section est repliée et étiquetée « Sources — n'agit qu'en mode de secours » quand le robot répond, dépliée et pleinement utile en mode direct ; la ligne du panneau Informations annonce désormais les 63 sources que le ROBOT interroge, lues dans `sources_count`, au lieu des cases cochées ici. Rien n'est effacé. Un juge unique, `sourcesAgissent()`, décide pour les trois endroits. 15 contrôles de navigateur. Voir le README, « Les interrupteurs de source disent enfin la vérité ».

**Trouvé le 09/10/2026. Ce n'est pas une idée, c'est une dette : l'app
affiche une fonction qui n'existe pas.**

Le chemin backend construit le fil depuis `feed.json` et ne consulte
**jamais** `settings.feeds[].enabled`. La chaîne d'affichage
(`articlesAffiches`) filtre sur les mots-clés exclus, l'onglet, la langue,
l'état lu, la recherche et le plafond — **jamais sur la source**. Décocher
« IGN » dans Paramètres ne retire pas un seul article.

Ils ne servent qu'au **mode de secours**, où l'app va chercher les flux
elle-même (`activeFeeds` dans `checkNow`).

Et ce n'est pas un oubli discret. Un commentaire du 22/09 affirme le
contraire, noir sur blanc :

> « le plafond, les mots-clés d'exclusion et **les sources actives**
> décident pourtant de ce qu'affiche `applyFilters()`, qui n'était jamais
> rappelé. Couper une source puis presser "Appliquer" ne changeait donc
> rien à l'écran. »

Le correctif de l'époque a ajouté le **redessinage**. Le filtre que ce
redessinage devait déclencher n'a jamais existé côté backend.

**DÉCISION PRISE (Antoni, 09/10)** : ni les faire marcher, ni les masquer —
**garder la section, repliée, sous un titre qui dit la vérité** (« Sources
— n'agit qu'en mode de secours »). Masquer aurait eu un revers : on ne
pourrait plus préparer le réglage à froid, seulement pendant une panne,
c'est-à-dire au pire moment.

À faire avec :

- la même correction sur le panneau Informations, qui affiche
  `sources : A · B · C` construit depuis les sources actives — trompeur
  pour exactement la même raison ;
- **ne rien effacer** : `enabled` reste en mémoire, dans `localStorage` et
  dans la sauvegarde, pour que le mode de secours continue de le respecter ;
- des contrôles : section repliée et étiquetée quand le backend répond,
  pleinement utile en mode direct, et l'état survit à l'aller-retour.

---

### 3.8 — Trois badges affichés, aucun moyen de filtrer dessus

> **FAIT le 13/10/2026.** La regex VIDÉO d'abord : **199 → 148**, les 51 écartés vérifiés un par un, aucune vraie vidéo perdue. Puis les badges deviennent cliquables — pas de nouvel onglet, la rangée en compte déjà cinq et déborde à 320 px. Un seul juge par badge, partagé par la carte et le filtre.

C'est le profil exact qui a fait marcher l'onglet « Actu majeure » : un
signal **déjà visible sur la carte**, sur lequel on ne peut pas agir.
Mesuré le 09/10/2026 :

| badge | articles | % du fil | médiane/jour | jours à 0 sur 30 |
|---|---|---|---|---|
| **SPÉCIALISTE GTA 6** | 251 | 7,6 % | 3 | 1 |
| **VIDÉO** | 183 | 5,5 % | 3 | 5 |
| **LEAK** | 162 | 4,9 % | 2 | 9 |

**Mais deux des trois ont un défaut de qualité, et il faut le régler AVANT
de les exposer comme filtre.**

- **VIDÉO est faux à 25 %.** La regex contient `\bvideo\b`, qui attrape
  « video game », « jeux vidéo » et le nom du média *Video Games
  Chronicle*. 45 des 183 n'ont rien d'une vidéo : « GTA 6 On Track To
  Overthrow The Best-Selling **Video Game** Of All-Time », « Faut-il
  craindre une hausse du prix des **jeux vidéo** ». Sur une carte, un badge
  de travers fait hausser les épaules ; **comme filtre, un quart de faux
  serait inutilisable**. Resserrer ramène à 138 — et corrige du même coup
  45 cartes mal badgées aujourd'hui, ce qui vaut d'être fait même sans
  filtre.
- **SPÉCIALISTE est à 38 % du Netflix.** 95 des 251 viennent de « Google
  News (Netflix) », qui est une recherche thématique et non une rédaction
  spécialiste GTA 6. Les vrais spécialistes (RockstarMag, RockstarINTEL,
  GTA6 Times) font 156. Un filtre rendrait ce mélange visible d'un coup :
  c'est peut-être très bien, mais c'est une décision à prendre.
- **LEAK est propre** : 8 sur 8 à la vérification à l'aveugle.

**Où les mettre : PAS en onglet.** Mesuré en livrant « Actu majeure » : la
rangée du haut est à sa limite (sous 479 px ses deux boutons doivent déjà
rétrécir et passer à la ligne) et celle du bas aussi (deux media queries
rien que pour « RockstarMag »). Un sixième bouton ne rentre pas. La feuille
**Filtres** a la place, quatre sections déjà, et une pastille qui compte
les filtres actifs.

---

### 3.9 — Sept badges de plus

> **FAIT le 13/10/2026.** Sondé sur 8 378 titres avant d'écrire une ligne. Sept retenus : PC, LUCIA / JASON, PRÉCOMMANDE, PRIX, TAKE-TWO, RUMEUR, DATE DE SORTIE. Quatre écartés à la mesure : CARTE (« Vice City » attrape les maillots des Miami Heat), DÉMENTI (29 jours à zéro sur 31), BANDE-ANNONCE (déjà couvert par vidéo), RETARD (« report » est ambigu en français). Cliquables comme les trois autres, une couleur par badge, les neuf contrastes calculés avant le CSS.

**La densité, que j'avais annoncée en l'air.** J'avais dit qu'à quatre
badges on retomberait dans le problème de 3.3. Mesuré sur 3 589 articles :
les cartes sans badge passent de 52,5 % à 42,2 %, celles à trois ou plus de
3,0 % à 5,1 %, maximum six sur deux cartes. 1,3 point de coût, pas un
problème.

**Une seule boucle.** La carte et la chronologie parcourent `ORDRE_BADGES`
avec le même code. Neuf lignes recopiées auraient garanti qu'un ajout en
oublie une — ce qui était déjà arrivé à la vidéo.

---

### Ce que je recommanderais de faire en premier

**3.7 d'abord** — ce n'est pas une idée mais une dette, la décision est
prise, et c'est petit. Puis **3.2**, de l'assemblage pur qui sert tous les
jours et prépare le jour J mieux que n'importe quoi d'autre. **3.1** est le
problème plus grave mais demande une mesure avant de choisir les seuils.

3.3 à 3.5, 3.8 et 3.9 sont du confort réel, sans échéance. **3.6 est
annulée.**

Dans 3.8, le resserrage de la regex VIDÉO se tient tout seul : il corrige
45 cartes mal badgées aujourd'hui, sans attendre qu'on construise un
filtre.

---

### Ce qui a été LIVRÉ de cette rubrique

**L'onglet « 🔥 Actu majeure » (09/10/2026, PR #144).** Chronologie
complète des sujets couverts par trois rédactions ou plus, à côté de
« Tous les articles ». 50 articles, deux mois d'histoire, pastille de
non-lus.

Trois choses à en retenir pour les suivantes :

1. **Le profil qui marche** : le signal existait déjà à l'écran (le badge
   🔥) sans qu'on puisse agir dessus, la donnée était déjà publiée
   (`extraSources`), et c'était mesurable avant de construire. C'est ce
   dernier point qui a choisi la chronologie plutôt que le filtre du jour —
   12 jours vides sur 30.
2. **Un seul juge.** Le badge calculait « est-ce majeur » en ligne dans
   `renderCard`. Deux calculs séparés peuvent diverger sans qu'aucun
   contrôle ne le voie. `estMajeur()` est maintenant le seul à décider, et
   lit `hotThreshold` publié par le backend.
3. **La capture a servi à quelque chose.** À 320 px, « Actu majeure » était
   coupé de 50 px — et le texte ne débordait d'aucun bouton : `.tab` est en
   `white-space:nowrap` et `flex-shrink:0`, donc c'est la RANGÉE qui
   débordait. Le contrôle de débordement existant serait passé au vert. Il
   en mesure deux maintenant.

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

> **FAIT le 14/10/2026.** L'app a le même mécanisme que le robot : la source déclare un SUPPLÉMENT de mots-clés officiels, jamais une liste complète. `check_sources_sync.py` compare désormais ce champ source par source — vérifié capable d'échouer. Mesuré : la chaîne a cinq articles conservés dans tout le dépôt, tous déjà retenus par les six mots-clés de base, donc le supplément n'en fait entrer aucun de plus aujourd'hui. Il n'existe que pour celui qui n'est pas encore publié.

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

> **FAIT le 14/10/2026.** Plafond à 8 000 liens, mesuré (300 octets par lien au neuvième décile, soit 2,3 Mo) — **relevé à 20 000 le 20/10/2026**, la mesure d'origine étant deux fois trop pessimiste : 149 octets au neuvième décile sur la sauvegarde réelle. Trois garde-fous : il ne s'applique qu'à la copie écrite sur l'appareil — la sauvegarde exportée garde tout ; on ne jette que les liens absents à la fois du fil et de l'archive, et seulement si les deux sont en mémoire ; et un lien encore affichable n'est jamais jeté, même au-dessus du plafond. Le message de quota ne conseille plus d'effacer les données du site, il renvoie vers l'export.

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

> **FAIT le 13/10/2026.** Les quatre clés ont chacune leur règle : union par (source, début) pour le journal, maximum entrée par entrée pour le compteur, la chaîne la plus longue pour l'historique, et pour les chronomètres de silence le nôtre mais avec le `depuis` le plus ancien et `alertee` en OU.

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

> **FAIT le 13/10/2026.** Le cache est mis à l'abri avant le `git reset --hard` et remis après. Les deux asymétries sont corrigées aussi : la fusion archive avec le même `exclure=` et allège `source_link`.

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

### 4.5 — ~~`main()` jamais exécutée par un test~~ — RÉGLÉ le 09/10/2026

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

**C'est fait.** `test_un_passage_complet_du_robot`, 18 vérifications, moins
d'une seconde. Trois choses seulement sont neutralisées — `collect_feed_items`
(la feuille réseau), `fetch_missing_images`, et `HOST_PAUSE` (une seconde de
politesse par requête, 8,5 s par passage pour ne rien vérifier). Tout le
reste s'exécute, dans un répertoire jetable.

Ce que seul le passage complet montre : la déduplication de bout en bout
(deux reprises entrent par deux sources, ressortent en une, la perdante
gardée en source supplémentaire — c'est l'ordre de `FEEDS` qui décide), le
report de l'état cumulatif, l'exactitude de `new_this_run`, l'écriture
simultanée des quatre fichiers, le fait que l'archive n'hérite pas de ce
que le fil écarte, et **l'idempotence** : un second passage n'ajoute ni ne
perd rien.

Les deux contrôles sur le texte de `main()` restent : ils vérifient un
ORDRE d'instructions que le résultat seul ne montre pas. **4.6 reste donc
ouvert**, mais avec un argument de moins.

---

### 4.6 — 55 assertions portent sur du texte source

> **NON FAIT, délibérément (13/10/2026).** Le constat est juste, mais convertir 38 tests verts est un gros remaniement à faible gain — la suite navigateur couvre déjà ces comportements, et réécrire des tests verts est le meilleur moyen d'en casser un sans s'en apercevoir.

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

> **FAIT le 13/10/2026.** Le test compare désormais `hot_count` au fichier complet, comme le README l'affirmait déjà — la phrase devient vraie au lieu d'être affaiblie.

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

> **FAIT le 13/10/2026.** Les nombres sont RETIRÉS plutôt que corrigés : ils vivent dans `len(FEEDS)` et `sources_count`. Un test interdit qu'un compte de sources revienne dans la docstring.

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

> **FAIT le 13/10/2026** (26 au moment du correctif). `https_les_vignettes()` les repasse en https à chaque passage. On ne perd rien à essayer : l'image est déjà bloquée comme contenu mixte.

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

> **FAIT le 13/10/2026.**

Deux lignes : `__pycache__/` et `*.pyc`. Rien n'empêche un `.env` local de
partir dans un dépôt **public**. Rien n'a jamais fuité — vérifié sur tout
l'historique — mais c'est la protection la moins chère du dépôt, et elle
manque.

---

### 4.11 — PyPI est épinglé à l'exact, les Actions au tag majeur

> **FAIT le 13/10/2026.** Toutes les Actions sont épinglées au SHA de commit, avec `# v7` pour que Dependabot continue de les suivre. Un test refuse toute référence qui ne soit pas 40 caractères hexadécimaux.

`requirements.txt` fige **tout le graphe**, avec trente lignes de
commentaire expliquant pourquoi (l'incident `selectolax` du 03/10). Les
workflows, eux, utilisent `actions/checkout@v7` — un tag mobile. Dependabot
surveille les deux, mais il signale une *nouvelle version*, pas un *retag*
de `v7`. Seule asymétrie avec la politique affichée du dépôt.

---

### 4.12 — Le garde-fou de publication ne protège que les articles

> **FAIT le 13/10/2026.** Deux règles grossières : un champ cumulatif ne peut pas disparaître, et le journal des incidents ne peut pas tomber de moitié d'un coup.

`valide_avant_ecriture` vérifie la structure, les liens, les titres, les
doublons et la perte massive — **sur `items` uniquement**. Un bug qui
viderait `sources_incidents` ou ferait reculer le compteur publierait en
silence. Choix de portée défendable (les articles sont le produit), mais il
mérite d'être nommé.

---

### 4.13 — Trois articles vivent dans deux tranches d'archive à la fois

> **RÉGLÉ le 13/10/2026.** Ils étaient **six** au moment du correctif, contre trois le 09/10 : l'écart grandissait tout seul. `feed_store.dedoublonne_archives()` fait désormais respecter l'invariant « un lien n'est rangé que dans un mois » sur toute l'archive, à chaque passage, entre `archiver` et l'écriture de l'index — la copie la plus récente gagne. Mesuré sur l'archive réelle : 4647 → 4641, deux mois réécrits, rejeu à zéro. Voir le README, « Un article republié restait dans deux mois ».

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

> **TRANCHÉ le 09/10/2026, et reconduit le 13/10.** `it`, `es`, `br` puis `ru` ont rejoint `LANGUES_ROCKSTAR_ECARTEES` le 09/10 ; `zh`, signalé par l'audit quatre jours plus tard, l'a rejointe le 13/10 — c'était la **sixième** copie du même article des stations de radio. L'analyse ci-dessous reste lisible pour la trace, mais sa conclusion (« les équivalents anglais ne sont pas dans le fil ») s'est révélée fausse à la vérification du 09/10. Le mécanisme tient : l'audit nomme la langue, elle coûte une ligne.

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
`NOTIFS_OFFICIELLES_MAX` plafonne les notifications (5 à l'écriture de
cette fiche, **10** depuis la première répétition générale), mais rien ne
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
4. ~~**4.5** — le test qui exécute `main()`.~~ **Fait le 09/10/2026.**

~~Puis **4.13** (trois articles en double dans l'archive) et **4.14** (la
décision sur les pages Rockstar localisées, à mesurer avant novembre).~~
**Les deux sont faits le 13/10/2026.**

4.3, 4.4, 4.6, 4.7, 4.9, 4.11 et 4.12 sont réels mais sans urgence.

---

## La deuxième répétition générale — 14/10/2026

Rejouée parce que presque tout ce qui touche au volume avait changé depuis
la première : plafond d'historique à douze mille, plafond de notifications
à dix, neuf badges calculés par article, découpage adaptatif du fil,
chronologie des officiels, plafond des articles lus.

**Rien n'a cassé.** Aucune erreur JavaScript, aucun garde-fou franchi,
aucune notification en trop. Les quatre chiffres à retenir :

- le robot reste **sous cinq minutes** dans toutes les configurations
  éprouvées à 1 800 articles/jour, et à 17,9 s avec un fil réaliste ; le
  seul cas qui dépasse est le pire cas théorique (3 600 articles reçus,
  sujets presque tous distincts) à **12 min 29**, toujours très en dessous
  d'un cron horaire ;
- l'app ouvre en **173 ms** même avec douze mille articles chargés, et les
  neuf juges de badge coûtent **27 ms** sur tout le fil ;
- le stockage local plafonne à **3,26 Mo sur ~5** au pire cas honnête ;
- les notifications plafonnent à **douze messages**, quelle que soit la
  rafale.

**Ce qui n'a PAS été atteint** : le plafond de douze mille articles. Un jour
de pic comprime le fil au lieu de le gonfler — la fusion rétroactive
absorbe les reprises. C'est la fenêtre de trente jours qui lie.

**Le seul coût notable** : « Tout charger » demande **9,3 Mo** au plafond,
contre 2,8 aujourd'hui, et fige l'interface quatre secondes. Rien n'est
cassé, mais c'est le chiffre à connaître avant de presser le bouton en
données mobiles le 19 novembre.

---

## Ce qui a été fait le 14/10/2026

Six lots, et le gel décidé en cours de route.

1. **4.1 et 4.2**, les deux derniers points de l'audit : le mode de secours
   sait étendre ses mots-clés officiels, et la liste des articles lus a un
   plafond.
2. **La deuxième répétition générale.** Rien n'a cassé. Le plafond de douze
   mille n'est jamais atteint : un jour de pic comprime le fil au lieu de
   le gonfler.
3. **Deux durcissements**, puis **gel** : garde-temps du robot à 45 min
   (12 min 29 mesurées au pire cas), et « Tout charger » annonce son poids.
4. **Un bouton mort pendant 8 à 23 secondes**, trouvé en chronométrant à la
   demande d'Antoni. Trois endroits écrivaient dans la même ligne ; le
   rendu du fil passait toujours en dernier.
5. **La relecture à froid des chemins du jour J.** Un défaut :
   `discord_notify` pouvait emporter la notification push, les deux canaux
   n'étant indépendants qu'en intention.
6. **Le README annonçait une fenêtre qui n'existe plus** — quatre chiffres
   faux, dont trois dans « Limites connues et assumées ». Un contrôle les
   compare désormais au code.

**Rien n'était ouvert au soir du 14/10** — voir plus bas le 15/10, qui a
rouvert un point. Les neuf pistes d'interface (3.1 à 3.9) et
les quatorze points de l'audit sont livrés ou clos à la mesure. Le code est
gelé : correctifs seulement jusqu'au 19 novembre.

**Ce qui reste, et qui n'est pas du code** : vérifier la portée réelle du
jeton d'accès personnel (`gta6-backend` seul, Contents en lecture-écriture
et rien d'autre), et une troisième répétition vers le 10 novembre, avec les
données d'alors.

---

## Ce qui a été fait le 15/10/2026

**Un seul lot, parti d'une question d'Antoni** : « Pourquoi j'ai 10 articles
non lus, ça me dit que depuis la dernière visite il y a que 5 nouveaux ? »

Ni l'un ni l'autre des deux compteurs n'était faux : le fil rejouait les
mêmes articles. Relu dans les 29 versions successives de `docs/feed.json`
du 14/10 — les vrais commits, pas une simulation — **337 articles sont
sortis de l'historique dans la journée et 113 sont revenus**, par paquets,
chacun juste après une descente de la fenêtre.

La cause : le débit se mesurait par **âge glissant** depuis l'instant du
passage, donc la cible bougeait avec l'heure (3 390 → 4 830, 14 descentes
dans la journée). Chaque descente élaguait *et* relevait le plancher
d'entrée — même calcul — donc refusait des articles du jour même.

Livré :

1. **Le débit se mesure en journées civiles**, fuseau de Paris. Sur les
   mêmes 29 passages réels : cible **4 170 partout, zéro descente**.
2. **Une mémoire des élagués** (`elagues-recents.json`, 15 jours) : un
   article resservi par sa source reprend sa place sans recompter comme
   neuf. Filet, au cas où la fenêtre bouge pour une vraie raison.
3. **Un contrôle qui verrouille l'amplitude**, vérifié capable d'échouer.
4. Au passage : la suite navigateur plantait sur une trace de pile au lieu
   de rendre son verdict quand le fil servi datait de la veille.

**NON LIVRÉ, et c'était validé** : le troisième morceau prévu — faire
rétrécir la fenêtre par petits pas. Écrit deux fois, retiré deux fois. Un
pas fixe dépend de la fréquence des passages, réglée hors du dépôt (30
jours demandés, 39 obtenus) ; un pas proportionnel casse l'équivalence qui
porte le filtre d'entrée (le plancher et le plafond doivent donner le même
résultat). Cinq contrôles l'ont dit. La correction du point 1 supprime la
cause ; ce pas-là n'aurait été qu'une ceinture, et une ceinture qui casse
autre chose ne se met pas.

**Ce qui restait ouvert au soir du 15/10** : le bloc « Ce que j'ai raté »
comptait les articles par leur **date de publication**, pas par le moment
où ils sont **apparus** dans le fil. **Livré le 16/10**, voir la section
suivante — et par un champ publié par le robot plutôt que par une trace
locale, la mesure ayant montré que la trace locale est vide exactement
quand elle sert : appareil neuf, cache vidé, navigation privée.

---

## Ce qui a été fait le 16/10/2026

**Le dernier point ouvert du dépôt est livré** : le bloc « Ce que j'ai
raté » compte désormais ce qui est **apparu**, et non ce qui a été publié.

Le robot écrit `vu_le` — l'instant où il découvre l'article — au seul
endroit où un article est reconnu réellement neuf. Les articles déjà
publiés n'auront jamais ce champ ; l'app retombe sur leur date et le fil se
remplit en quelques jours.

**Un chiffre que j'avais annoncé et qui était faux.** J'avais dit « 10 %
des arrivées ont plus de 24 h de retard, soit ~16 par jour ». La
quasi-totalité de ces 10 % sont des articles que l'app ne charge jamais :
`feed-recent.json` garde les 300 plus récents **par date**, et ces
arrivants tardifs ont 35 jours d'âge médian. Dans la fenêtre réellement
chargée, le retard concerne **25 articles par semaine au-delà de 12 h et 7
au-delà de 24 h** — un à quatre par jour selon le rythme de visite. Le lot
reste justifié, mais pour ce gain-là, pas pour celui que j'avais annoncé.

Livré avec :

1. **Les deux compteurs côte à côte** dans le bilan. « 10 non lus » contre
   « 5 nouveaux » : les deux avaient raison, et l'écart passait pour un
   bug. Il est maintenant lisible d'un coup d'œil.
2. **Un arrivant en retard dit son âge** — « paru le 6 octobre » — pour
   qu'on ne le prenne pas pour une dépêche du jour.
3. **Un bouton « J'ai tout vu »**, qui s'ajoute au repère posé à la
   fermeture au lieu de le remplacer.
4. **Le piège du revenant**, créé par le correctif de la veille : un
   article resservi aurait pris un `vu_le` tout neuf. Il prend la date de
   son élagage.

**Écarté, mesure à l'appui** : repêcher dans le fichier allégé les ~5
articles par jour qui arrivent hors fenêtre. Ce sont majoritairement des
fiches produit du Rockstar Store et des reprises Google News d'il y a un
mois, et il aurait fallu changer la règle du fichier que l'app charge.

### Et dans la foulée, les trois PR Dependabot

Antoni les a sorties du lot : trois PR ouvertes, trois sorts différents.

- **#157 pycparser 3.0 → 3.11** — fusionnée. Transitive de `cffi`, qui la
  requiert sans borne, quatre suites vertes. Rebasée d'abord sur le
  nouveau `main` pour que le garde-fou ci-dessous tourne dessus.
- **#156 multidict 6.9.1 → 7.1.0** — fermée. `aiohttp 3.14.4` exige
  `multidict<7.0,>=4.5` : le `pip install` échoue avant les tests. Ce
  n'est pas le code qui refuse, c'est la résolution des dépendances.
- **#150 selectolax 0.4.13 → 1.0.0** — fermée. C'est le paquet et la
  version exacts de la panne du 03/10. Vérifié le jour même :
  `googlenewsdecoder` est toujours en 0.2.1 (publiée le 20/09) et importe
  toujours `selectolax.parser`, sans borne haute de son côté.

**ET C'EST LA PR #150 QUI A RÉVÉLÉ LE VRAI DÉFAUT : elle était VERTE.**
Les quatre suites passaient avec le décodeur cassé. Le repli est
volontaire — sans décodeur le robot doit continuer de tourner, donc rien
ne plante — et les deux seules assertions qui parlaient du décodeur
portaient sur le TEXTE de `fetch_feeds.py`, pas sur le fait que l'import
réussisse. Un clic et la panne de trois jours revenait à l'identique.

`test_le_decodeur_google_news_est_vraiment_la` exige désormais
`HAS_DECODER is True`, que `selectolax.parser` réponde, et exerce deux
fonctions pures du décodeur **hors ligne** (la suite ne fait pas de
réseau). Vérifié capable d'échouer en simulant selectolax 1.0 par un faux
paquet n'exposant que `lexbor` : quatre contrôles au rouge, avec le
message mot pour mot du 03/10.

**Laissé de côté, et c'est une question ouverte pour Antoni** : Dependabot
réouvrira une PR selectolax à la prochaine version. On pourrait l'ignorer
dans `.github/dependabot.yml`. Pas fait : avec le contrôle, une telle PR
vire au rouge toute seule — c'est du bruit, plus un danger — et l'ignorer
ferait manquer le jour où le décodeur passera à `selectolax.lexbor`.

### Le compteur avait six heures de retard

Antoni, en passant : « le jeu sort le 19/11 à 00 h 00 heure de Paris ». La
constante valait minuit à **New York** — 6 h du matin à Paris — sur la foi
d'un commentaire disant « siège Rockstar/Take-Two », supposition jamais
sourcée et que rien ne vérifiait. Le 19 à 00 h 30, pendant qu'on y joue, le
compteur aurait annoncé « C'EST AUJOURD'HUI — 5h 29m ».

La réponse était dans le fil que l'app collecte elle-même depuis le
07/09/2026 : déverrouillage à **minuit heure locale**, par vagues, la
Nouvelle-Zélande à 11 h 00 UTC le 18 — chiffre qui recoupe exactement
UTC+13. Corrigé en `2026-11-19T00:00:00+01:00`, et le contrôle regarde
désormais la borne des deux côtés de minuit.

### L'après-19-novembre, décidé

Rien n'était prévu pour le lendemain de la sortie : le compteur affichait
« GTA 6 est sorti ! » et se figeait là pour toujours. Antoni a tranché
pour **un compteur à l'envers** — « Sorti depuis 3 jours ».

Écrit tout de suite bien que le code soit gelé, et c'est délibéré : le
bloc est inerte avant le 19 novembre, donc il ne touche à rien de ce qui
tourne d'ici là, et il évite d'improviser une modification le 20 au matin.

Trois autres décisions prises au passage, toutes dans le sens du statu
quo : **gel strict** d'ici la sortie (correctifs seulement, plus la
répétition du 10 novembre) ; **pause nocturne inchangée**, levée du 18 au
20 novembre seulement — les annonces Rockstar passent de toute façon la
nuit ; **Dependabot laissé libre** de reproposer selectolax, puisque le
contrôle du décodeur fait virer une telle PR au rouge et que le jour où
elle deviendra verte sera le signal que le verrou peut sauter.

### La carte nomme enfin l'éditeur — 16/10/2026

Antoni demandait des idées d'options. La mesure a répondu autre chose :
**87 % des cartes chargées disaient « Google News »** (262 sur 300, et les
dix premières toutes les dix) alors que l'app connaissait déjà le vrai
média. `mediaDe()` existait, mesurée sur 1 437 titres, et n'était appelée
que par les statistiques. Branchée sur la carte, l'aperçu et la
chronologie ; le titre perd son suffixe redondant ; la ligne « + N autres
sources » nomme le domaine au lieu de l'agrégateur.

Rien à inventer, rien côté robot, rien dans le format publié.

### Le panneau du compte à rebours — 16/10/2026

Demandé par Antoni : appuyer sur le compte à rebours ouvre son détail —
décomposition en mois / semaines / jours / heures / minutes / secondes,
équivalences, barre depuis l'annonce du jeu (04/02/2022) et heure exacte
de sortie. La Nouvelle-Zélande a été proposée et écartée.

Trois pièges nommés avant d'écrire, trois contrôles : le débordement de
`setMonth` (31 janvier + 1 mois = 3 mars), l'intervalle de la seconde qui
tourne et qu'il faut couper à la fermeture, et la cible tactile qui ne
peut PAS s'étendre vers le bas — un bouton de l'en-tête commence 14 px
plus bas.

Ajouté dans la foulée : un **compteur de dodos**, un délire d'Antoni avec
un ami. Un dodo est une NUIT, donc un minuit à traverser — et celui de la
sortie ne compte pas, puisqu'on joue au lieu de dormir. La version naïve
par division se trompe sur les cinq cas éprouvés, changement d'heure
compris.

### Les trois plafonds du stockage — 20/10/2026

Liens lus **8 000 -> 20 000**, articles déjà vus **2 000 -> 4 000**. Les
deux étaient pleins ou presque sur la sauvegarde réelle d'Antoni.

Deux mesures ont corrigé des chiffres faux : un lien lu pèse 149 octets
au neuvième décile et non 300 (comptage UTF-16), et une entrée « vue »
pèse 414 octets parce qu'elle porte tout le résumé de l'article, pas un
horodatage.

5 000 entrées vues avait été demandé d'abord : en additionnant les trois
réserves au pire cas, ça donne 5,12 Mo, au-delà du quota. 4 000 tient à
4,75 Mo, 0,25 de marge, assumé.

`test_budget_du_stockage` fait respecter la somme : il lit les trois
constantes et refuse qu'elles franchissent 5 Mo. Éprouvé en le cassant
des deux côtés.

### Revue d'améliorations du 24/10 — où elle en est (27/10/2026)

Antoni a choisi de tout faire. **Fait** : `CLAUDE.md` et le nom de média qui
était une URL (voir le README, « Revue d'améliorations, première tranche »).
**Reste, dans cet ordre** :

1. **Vecteurs de test partagés Python/JS** : `nettoie_resume` ↔
   `nettoieResume` et la regex du suffixe de média existent en double et
   seule leur présence est vérifiée. Un fichier d'entrées/sorties lu par les
   deux suites ferait échouer toute divergence. Reprend le cas tech-insider.
2. **Mesurer `og:description` sur 24 h** : une ligne de journal dans
   `fetch_og_image`. 85,5 % des résumés sont vides, donc le bouton d'aperçu
   n'existe que sur 14,5 % des cartes. On ne décide qu'après la mesure, et ce
   chantier veut une mini-spec (plafond de requêtes, extrait tronqué, jamais
   le texte complet).
3. **Temps par source dans `sources_health`** : le robot a doublé de durée
   les 22 et 23/10 (36 s → 70 s) puis est revenu à 36 s le 27 ; on ne peut
   pas dire pourquoi, aucun temps n'est enregistré.
4. **Retirer `source_link`** : 19 % du fil brut, 24,7 % servi, **zéro**
   décodage évité (950 sur 950 sont dans `decode-cache.json`).

**Non vérifié, et ça compte** : que GitHub Pages **compresse le JSON**. La
recherche n'a renvoyé que de la documentation GitLab. Sans compression le
fil complet pèse 3,3 Mo, pas 0,8 ; le premier écran n'a de toute façon besoin
que de `feed-recent.json` (0,33 Mo brut). Se vérifie en 30 secondes dans
l'onglet Réseau de Chrome, colonne « Content-Encoding ».

**Écartés, avec leurs chiffres** : découper `index.html` (459 Ko, 295
fonctions, mais 27 tests Python lisent son texte, plus `check_sources_sync`
et le précache du service worker) ; sortir les commits du robot de `main`
(déjà tranché) ; tags et versions sémantiques (un seul utilisateur, et
`CACHE_NAME vN` joue déjà ce rôle).

**Corrigé en route** : le badge « ACTU MAJEURE » à 1,92:1 en thème clair, qui
faisait passer la CI de toutes les PR au rouge dès que le fil contenait un
article à trois sources. #201 et #202 (Dependabot) étaient rouges pour cette
seule raison ; à relancer une fois cette PR fusionnée.

En attente aussi : trois PR Dependabot (#200 `certifi`, #201
`actions/setup-python`, #202 `soupsieve`).

### Les résumés d'articles — 20/10/2026

Signalé par Antoni : « ya des bugs dans les descriptions ». Deux défauts,
et une erreur à moi découverte en les mesurant.

| | |
|---|---|
| entités HTML affichées telles quelles | **3 700 / 4 235 · 87,4 %** — décodées à la collecte et rétroactivement |
| résumé = titre + nom du média | **3 626 / 4 235 · 85,6 %** — vidés, le bouton d'aperçu disparaît tout seul |

Le seuil des 25 caractères est **mesuré** : le plus long nom de média
collé du fil en fait exactement 25, et aucun des 609 résumés conservés ne
commence par son titre.

**Mon erreur** : j'avais écarté `support.rockstargames.com` le matin même
en affirmant que Google News n'indexe pas les pages d'assistance. Cinq
articles de ce domaine étaient déjà dans le fil. La source est ajoutée,
et un contrôle vérifie désormais la prémisse au lieu de me croire.

### Trois préparatifs pour l'après-sortie — 20/10/2026

Trois décisions d'Antoni, prises sur propositions chiffrées.

| | tranché |
|---|---|
| les titres vont raconter le jeu | **mode sans spoiler** : flouter, un geste révèle ; liste pré-remplie, **allumé par défaut** |
| le récap dira « +180 articles » toutes les heures | après la sortie, il ne part que s'il y a un **officiel** ou une **actu majeure** ; la DATE décide, pas un interrupteur |
| les soluces et les tests n'ont pas de source | **six requêtes** ajoutées maintenant, muettes jusqu'au 19 |

J'avais écarté `support.rockstargames.com` en affirmant que Google News
n'indexe pas les pages d'assistance. **C'était faux** — cinq articles de
ce domaine sont déjà dans le fil. Corrigé le soir même, la source est
ajoutée.

**Revirement du soir même sur le mode sans spoiler** : livré éteint, il
est allumé par défaut depuis. La liste voile déjà 41 articles sur 4 350
(0,9 %) et ce ne sont pas des faux positifs — les fuites racontent la
fin du jeu un mois avant la sortie.

Et une quatrième question, tranchée en « mesurer d'abord » : **le poids du
fil n'est pas un problème**. J'avais annoncé 9,6 Mo au plafond, c'était la
taille NON COMPRESSÉE ; servi en gzip, le fil fait 0,79 Mo aujourd'hui et
2,24 Mo au plafond de 12 000. `MAX_HISTORY_SIZE` ne bouge pas, le fil
n'est pas découpé par mois.

**Reste ouvert, non fait** : `source_link` pèse 11,5 % du fichier et ne
sert qu'au mode de secours — le retirer rendrait 0,27 Mo bruts. Proposé,
pas tranché.

**Toujours à remesurer après le 19** : les douze motifs de catégorie, et
le seuil de l'onglet « Actu majeure » (0 à 1,7 % des articles aujourd'hui
— après la sortie il peut aussi bien déborder que rester vide, et je ne
sais pas lequel).

### Douze catégories d'article pour l'après-sortie — 20/10/2026

Demandées par Antoni : « test, astuces, tuto, gameplay... ». Livrées en
badges, pas en onglets — les deux rangées sont pleines depuis le 16/10.
TEST, SOLUCE, ASTUCE, MISE À JOUR, BUG, MOD, ONLINE, PERFORMANCES,
TROPHÉES, VENTES, GAMEPLAY, SPEEDRUN.

**La limite est dite** : le fil est pré-sortie, donc les motifs n'ont pu
être éprouvés que sur leurs faux positifs d'aujourd'hui. J'avais proposé
d'en livrer trois maintenant et de mesurer les autres le 22 novembre sur
le corpus réel ; Antoni a tranché « tout maintenant, quitte à corriger ».
**À remesurer vers le 22/11** — c'est la seule chose que ce lot laisse
ouverte.

Trois choses trouvées en chemin, toutes mesurées :

- **le nom du média polluait les badges d'octobre** : 3 406 titres sur
  4 225 finissent par « - Nom du média », et le badge PC se posait sur
  21 articles à cause de *PC Gamer* et *PC Astuces*. Tous les juges
  lisent désormais le corps du titre ;
- **GAMEPLAY était un doublon total de VIDÉO** (97 sur 97). Je l'avais
  dit avant ; il a été demandé quand même. `\bgameplay\b` est donc sorti
  de VIDÉO, qui y gagne : 342 → 271, les 71 écartés relus, aucune vraie
  vidéo perdue ;
- **la boucle de rendu des badges existait en deux exemplaires**, carte
  et chronologie. Une seule fabrique désormais.

Deux familles visuelles, choix d'Antoni : alerte en aplat, catégorie au
trait. Densité mesurée avec les vingt et un badges : 67 % des cartes n'en
portent aucun, une seule en porte cinq.

**Et une couleur par catégorie**, demandée le jour même contre mon avis
(j'avais livré un trait neutre en écrivant qu'une douzième couleur ne se
retiendrait pas). Douze tons à 30° l'un de l'autre, déclinés PAR THÈME —
une catégorie est au trait, sa couleur s'écrit donc sur la carte, et
aucun ton unique ne tient 4,5:1 sur le blanc et sur le #10151a. Les deux
paires les plus proches font une cinquantaine d'unités RVB, et le
contrôle le dit plutôt que de le cacher.

### La répétition post-sortie, et ses trois défauts — 20/10/2026

Le panneau du compteur rendu à quatre instants simulés autour du
19 novembre. Rien ne casse. Trois défauts d'affichage, visibles seulement
ce soir-là ; Antoni a tranché sur chacun le jour même.

| ce qui n'allait pas | tranché |
|---|---|
| « Sortie le 19 novembre » sous un titre « Depuis la sortie » | « **Sortie depuis le** 19 novembre » |
| la barre figée à **100,0 %** pour toujours | pleine, mais le chiffre devient le mot « Sorti » |
| `0 mois 0 sem 0 j 0 h 30 min 14 s` la nuit de la sortie | sous 24 h, les unités de tête à zéro s'effacent |

Le troisième est la rançon des six tuiles toujours affichées, choisies le
16/10. **Un premier jet remplaçait tout par le total en secondes**, à
44 px ; refusé le jour même — « 1 799 secondes » ne se lit pas. Ne
s'effacent donc que les zéros **de tête** : ceux du milieu et de la fin
restent, sinon « 23 h 5 s » se lirait « 23 h 5 min ».

Le seuil est celui de `palierCompteur()`, pas un deuxième, et c'est un
**écart** : la coupe vaut aussi dans les 24 heures qui suivent minuit.

24 contrôles de navigateur de plus (627).

### Le plafond des liens lus passe à 20 000 — 20/10/2026

Antoni a exporté sa sauvegarde : **7 548 liens lus sur un plafond de
8 000**, soit 94 % après un mois d'usage. Il a demandé 20 000.

Remesuré sur ses données réelles avant de toucher à la constante, et la
mesure d'origine était **deux fois trop pessimiste** : 104 octets par lien
en moyenne, 149 au neuvième décile — pas les 300 annoncés, qui venaient
d'un comptage en UTF-16. Budget à 20 000 : liens lus 2,04 Mo (2,90 au pire
cas), fil en cache 0,25 Mo, dates de première vue 0,65 Mo, soit **2,94 Mo**
sur les 5 Mo accordés. C'est le plafond du FIL, à 300 articles, qui tient
ce budget.

La sauvegarde a aussi été passée au peigne : **aucun secret**, sous aucune
forme — ni jeton, ni webhook, ni clé VAPID, ni URL de supervision.

Au passage, les 109 articles « masqués » qui l'inquiétaient sont ses six
mots d'exclusion (noms d'autres jeux) faisant leur travail. J'avais déduit
de cet écran qu'il n'avait que 109 articles chargés : c'était faux, les
comptes « affichés » et « masqués » sont indépendants.

### Le panneau prend les couleurs du compte à rebours — 20/10/2026

Demandé par Antoni. Le panneau avait deux états quand l'en-tête en a
quatre, et le seuil des 24 h était écrit deux fois — rien n'empêchait les
deux de diverger. Le calcul passe dans `palierCompteur()`, partagé, et la
disposition précise EST le palier `final`.

Seuls les gros chiffres prennent la teinte : la barre garde son bleu, les
zéros leur gris. Trois gardes éprouvés, dont un réécrit — il vérifiait
qu'une couleur existait, pas que c'était la bonne.

Au passage : le balayage marquait ses articles lus AVANT la fin du
rattrapage de l'historique, ce qui le faisait tomber en attente une fois
sur quelques-unes. Il attend maintenant `historyPartial === false`.

### Le balayage trouve son premier vrai défaut — 20/10/2026

Le compte à rebours est passé au palier « soon » dans la nuit : sa
couleur est à **2,16:1** en thème clair. Les trois paliers étaient en hex
en dur et avaient échappé au travail sur le thème clair ; `urgent` est à
2,32 et `final`, celui de la nuit de sortie, à 3,27. Ils deviennent des
jetons avec une gamme chaude surchargée en clair (4,99 / 5,02 / 6,47).
Le thème sombre était conforme et ne bouge pas.

Noté sans le corriger : la pulsation de `urgent` et `final` descend
l'opacité à 0,55, soit 2,29 et 2,82 au creux. Animation d'alerte, le
seuil ne s'y applique pas.

Au passage, le balayage avait bloqué trois PR Dependabot depuis le 19 :
sa liste d'exceptions pour les cartes lues était tirée de l'échantillon
du 16 octobre et ne prévoyait pas « + 1 autre source ». Elle devient
structurelle (`.card.read *`), et le titre d'un article lu — la seule
chose qu'on y relit — passe dans un contrôle à part.

### Un balayage du contraste sur toute l'app — 16/10/2026

Dix surfaces, deux thèmes, ~2 465 éléments par thème, seuil WCAG AA.
Coût 57 s : la suite passe de 81 à 143 s, choix d'Antoni (« un garde
qu'on doit penser à lancer ne garde rien »).

**Zéro défaut de couleur** dans toute l'app. Deux opacités en cause : la
ligne d'état (0,85 retiré, 4,01 -> 5,53) et les articles lus (0,45 ->
0,70, le titre passe de 2,80 à 6,05). Date et source d'un article lu
restent sous le seuil, exception nommée, décision d'Antoni.

QUATRE FOIS DANS LA JOURNÉE, un contrôle a rapporté du faux parce qu'il
mesurait à côté : scrollWidth sur une grille qui se serre, une boîte en
inset:0 qui épouse sa barre, une grille-bloc qui ne peut pas déborder,
et `color(srgb ...)` lu comme du rgb() — ce dernier a produit un faux
positif complet sur le badge vert, et une correction proposée pour rien.
Un cinquième : des cartes mesurées en pleine animation d'entrée.

Le profil de test n'ayant aucun article lu, `.card.read` n'était couvert
par rien ; le balayage en marque douze. Plancher de 900 éléments
examinés, et chaque exception doit encore désigner quelque chose.

### Le pourcentage de la barre, présent mais illisible — 16/10/2026

Signalé comme disparu ; il y était. Mesuré : **2,43:1** en thème clair et
**1,78:1** en sombre, parce qu'il était écrit en `--text` sur l'aplat
`--accent`. Le jeton `--accent-contrast` existe pour ce cas exact (6,70
et 8,74) et n'avait pas été utilisé.

Un seul exemplaire ne peut pas servir les deux fonds — le chiffre est à
cheval sur la limite du remplissage entre 83 et 97 %. La barre porte donc
deux exemplaires superposés, celui du dessus rogné par le remplissage.
Barre de 18 à 26 px, chiffre de 10 à 12.

Le contrôle CALCULE le rapport de contraste dans les deux thèmes au lieu
de le supposer.

### Le total en secondes revient — 16/10/2026

Perdu quand les équivalences sont passées de quatre à trois. Mesuré :
87 px aujourd'hui, 106 au pire cas, pour des colonnes de 55 — les quatre
ne tiennent pas sur une ligne. Grille à deux colonnes sur deux rangées,
comme avant la refonte.

Au passage, le garde de débordement s'est révélé faux pour la TROISIÈME
fois dans la journée, et toujours pour la même raison : il mesurait le
conteneur, qui ne peut pas déborder, au lieu du contenu. Il porte
maintenant sur chaque tuile, et le contrôle des équivalences est posé sur
l'instant qui exerce vraiment la grille — deux jours après l'annonce, où
le total en secondes fait onze caractères.

### Le même panneau, troisième passe : une seule ligne — 16/10/2026

« Tout tienne sur une ligne », et « c'est quand les mois partiront ». Les
six unités tiennent sur une rangée dès lors que les libellés sont abrégés
— mesuré : « secondes » fait 48 px à lui seul, et c'était ça qui forçait
le 3 + 2, pas la place. Les unités à zéro **restent affichées**, grisées,
pour que la ligne ne se redispose plus jamais.

Trois options rendues en images avant de coder ; Antoni a choisi celle-là.
Les rendus ont montré qu'il fallait des colonnes de largeur régulière,
sans quoi « 1 0 2 9 59 5 » se lit comme une suite de chiffres.

Le dodo passe sous la barre — « juste un truc en plus ». La bascule des
24 h ne pilote plus que la taille des tuiles.

Cinq gardes vérifiés capables d'échouer, dont un réécrit : il attendait
une liste de tuiles grisées **devinée** au lieu d'être calculée. Remplacé
par l'invariant « les grisées sont exactement celles qui valent zéro ».

### Le même panneau, refait en deux dispositions — 16/10/2026

« Tu peux pas faire plus joli ? » Le défaut n'était pas qu'esthétique : le
découpage était rendu en **phrase**, et une phrase se casse entre le
nombre et son unité — « 5 / minutes » sur l'écran d'Antoni. Tout est passé
en **tuiles**, sur le motif `.stats-chiffre` déjà en place dans les
Statistiques, que la première version avait ignoré.

Et le panneau a maintenant deux visages : **dodos en vedette** au-delà de
24 h, **tuiles précises** en dessous. Le seuil est le palier `final` de
l'en-tête, celui où elle affiche elle-même les secondes — pas un
cinquième seuil inventé. Il en découle que la vedette n'affiche jamais
« 0 dodo », ce que deux contrôles encadrent à une minute près.

Trois contrôles écrits pour l'occasion **ne prouvaient rien**, et c'est la
mesure qui l'a dit : `scrollWidth` ne bouge pas quand une grille `1fr`
sort du conteneur ; un `<i>` en `inset:0` épouse la barre par
construction ; et les deux instants testés, à un jour de la sortie,
n'affichaient que deux tuiles — le pire cas de la grille n'était jamais
joué. Corrigés par une mesure géométrique, un `Range` sur le texte, et un
troisième instant à six unités.

### Les deux options : une livrée, une abandonnée — 16/10/2026

Antoni a décidé d'avancer ces deux chantiers plutôt que d'attendre le
20 novembre. Résultat : **une livrée, une abandonnée sur mesure**.

**Le filtre de notifications est ABANDONNÉ.** Mesuré avant de coder : le
récapitulatif est une notification par PASSAGE et non par article, tous
partagent le même `tag` donc le suivant remplace le précédent, et les
officielles sont à 4,3 par jour en moyenne — zéro sur six jours sur neuf —
déjà plafonnées à dix par passage. Mon « ça va sonner sans arrêt » de la
veille était faux. Ne pas reproposer sans chiffres nouveaux.

> Si on y revenait quand même, deux choses sont tranchées : le réglage
> vivrait dans une **variable de dépôt GitHub** (choix d'Antoni), et le
> filtre ne peut PAS vivre dans le service worker — `userVisibleOnly: true`
> fait révoquer l'abonnement si un push n'affiche rien.

**La mise de côté est LIVRÉE** : étoile sur la ligne de source, filtre
« Mis de côté », plafond de `readSet` repris tel quel, emportée par la
sauvegarde. Deux défauts trouvés en chemin par les contrôles ajoutés —
une zone tactile de 72 px qui aurait volé les clics du titre, et
« Nouveaux » qui débordait de son bouton depuis qu'ils sont quatre.

**La tâche planifiée du 20 novembre est donc sans objet** et peut être
supprimée.

### Ce qui était prévu — pour mémoire

Antoni les a choisies le 16/10 et a décidé qu'elles attendaient le
19 novembre. Une tâche planifiée au **20 novembre** les ramènera.

1. **Un filtre de notifications par importance** — trois crans : tout /
   officiels seulement / officiels + actus majeures. Aujourd'hui c'est
   tout ou rien par appareil. Deux implémentations possibles : le service
   worker refuse d'afficher (côté app, sans risque backend), ou le robot
   n'envoie pas (plus propre, mais touche la chaîne de push).
2. **Mettre un article de côté** — une étoile sur la carte, un filtre
   « Mis de côté », stocké en local comme `readSet` (qui a un plafond :
   prévoir le même).

Ni l'une ni l'autre n'est mesurée. Les proposer était une supposition
d'usage, et la dernière supposition d'usage a fini supprimée trois jours
plus tard — commencer par mesurer l'usage réel après la sortie.

### Le bloc « Ce que j'ai raté » est RETIRÉ — 16/10/2026

« Dégage-moi le truc de depuis ta dernière visite, ça ne sert à rien. »
Fin d'un dossier ouvert le 13/10 et travaillé trois jours : le bandeau,
son bilan, ses quatre lignes, le bouton « J'ai tout vu » et le repère de
dernière visite. Environ 13 000 caractères d'app et 17 000 de contrôles.

**Le champ `vu_le` part avec**, pour la raison qui l'aurait condamné de
toute façon : ajouté la veille uniquement pour ce bloc, il coûtait +3,7 %
sur le fichier que l'app télécharge à chaque ouverture et plus personne ne
le lisait. C'est le reproche du point 4.7 à `hot_count`, mot pour mot.

**Ce qui reste et qui sert toujours** : la mémoire des élagués et
`reprend_les_revenants` — un article resservi par sa source n'est pas
recompté neuf, ce qui vaut pour les notifications quel que soit
l'affichage. Et la correction de fond du 15/10, la mesure du débit en
journées civiles, qui n'avait jamais rien eu à voir avec ce bloc.

**Ce que ça règle** : les deux compteurs qui avaient lancé tout ce travail
ne peuvent plus se contredire, il n'en reste qu'un.

**Conséquence sur la piste 3.2** de ce fichier (« Ce que j'ai raté : le
point d'entrée qui manque ») : elle est close, par décision et non par
mesure. Ne pas la rouvrir sans qu'Antoni le demande.

### Quatre dernières décisions, et le calendrier qui les porte

Antoni a tranché quatre questions le 16/10 au soir :

| question | sa réponse |
|---|---|
| Une notification push à minuit pile le 19 ? | **Non.** Le compteur suffit — il saura que GTA 6 est sorti sans qu'une app le lui dise. Rien de codé. |
| Surveiller la nuit du 18 au 19 ? | **Oui**, des réveils automatiques, avec consigne de **silence si tout va bien**. |
| Revoir la couverture des sources avant la sortie ? | **Oui**, une revue en novembre — mesure et proposition, aucun changement sans son accord. |
| Sécuriser son état de lecture ? | **Rappel à la mi-novembre** : il exporte depuis les paramètres, dix secondes. |

### LE CALENDRIER EST PLANIFIÉ, PAS PROMIS

Les trois dernières réponses ci-dessus sont des rendez-vous en novembre.
Une promesse dans un fil de conversation ne vaut rien : la session qui
l'a faite n'existera plus. Sept **tâches planifiées** les portent donc, et
chacune se déclenche dans une session neuve avec sa consigne complète.

| quand (Paris) | ce qui se passe |
|---|---|
| **3 nov, 10 h** | Revue de couverture des 63 sources |
| **10 nov, 10 h** | Troisième répétition générale |
| **15 nov, 11 h** | Rappel : exporter la sauvegarde, vérifier la portée du jeton |
| **18 nov, 22 h 30** | Veille : robot, workflows, décodage, pause nocturne bien levée |
| **19 nov, 00 h 30** | Le passage de minuit — le plus chargé de la vie du projet |
| **19 nov, 02 h 00** | Volume, durée des passages, sources tombées |
| **19 nov, 09 h 00** | Bilan de la nuit, au réveil |

Les trois de la nuit se taisent si tout va bien — une ligne, pas un
rapport. Seul le bilan de 9 h s'écrit dans tous les cas, et il vérifiera
au passage le compteur « GTA 6 EST SORTI ! », livré le 16/10 et jamais
éprouvé en vrai. **Toutes ont pour instruction de ne RIEN déployer sans
l'accord d'Antoni** : le code est gelé et la nuit de la sortie est la pire
pour improviser.

**Une limite à connaître** : ces sessions tourneront sans les connecteurs
MCP, donc sans les outils `mcp__github__*`. Ce n'est pas bloquant —
`gh api` suffit à tout ce qui leur est demandé — mais pour leur donner
l'interface GitHub complète il faudrait les recréer depuis la page des
tâches planifiées sur claude.ai.

Si une tâche est supprimée par accident, ce tableau est la trace qui
permet de la refaire.

**Ce qui reste, et qui n'est pas du code** : vérifier la portée du jeton
d'accès personnel — c'est le seul point qui dépende du compte d'Antoni, et
le rappel du 15 novembre le redira.

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

## Chiffres de référence au 09/10/2026, 08h00 Paris

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
| `main()` | 493 lignes, complexité ≈ 82, **1 test l'exécute** |

| les contrôles | |
|---|---|
| `test_pipeline` | 126 tests, 1 743 vérifications |
| `test_navigateur` | 360 contrôles |
| articles « actu majeure » | 51 (1,5 %) |
| badge SPÉCIALISTE / VIDÉO / LEAK | 251 / 183 / 162 |
| tests inspectant du texte source | 38 (55 assertions) |

| les constantes | |
|---|---|
| `CACHE_NAME` | `gta6watch-shell-v19` |
| onglets du fil | 5 — **les deux rangées sont pleines** |
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

## Ce qui a été fait le 09/10/2026

**Livré : l'onglet « 🔥 Actu majeure »** (PR #144), avec son correctif de
mise en page trouvé à la capture — voir « Ce qui a été LIVRÉ de cette
rubrique », plus haut.

**Consigné, sans toucher au code** :

- **3.6 annulée.** La pastille de santé existe déjà dans l'entête, et mon
  affirmation du contraire était fausse.
- **3.7 ajoutée** — les 63 interrupteurs de source ne font rien en mode
  backend, et un commentaire du code affirme l'inverse. Décision prise :
  section repliée et étiquetée « n'agit qu'en mode de secours ».
- **3.8 ajoutée** — trois badges affichés sans filtre possible, dont deux à
  nettoyer avant de les exposer (VIDÉO faux à 25 %, SPÉCIALISTE à 38 % du
  Netflix).

**Les deux rangées d'onglets sont désormais pleines.** Toute vue
supplémentaire passera par la feuille Filtres, pas par un sixième bouton.

**Plus tard dans la journée** : la mesure du seuil (§1, question close — il
reste à 3), l'extension de `LANGUES_ROCKSTAR_ECARTEES` à br, es, it, ru, et
**le test qui exécute `main()`** (4.5, réglé).
`/ru/` est apparu PENDANT le changement, cinquième copie de la même annonce
Rockstar : la duplication multilingue n'était pas une projection pour
novembre. Et un contrôle verrouille enfin `"vi" not in` la liste — le
commentaire avertissait depuis le 23/09, rien ne l'empêchait.

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
