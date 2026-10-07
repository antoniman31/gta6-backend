# Ce qu'il reste à faire

**Rien n'est cassé, rien n'est urgent.** Ce fichier existe pour qu'on reprenne
plus tard sans redériver ce qui a déjà été mesuré.

Écrit le 06/10/2026 au soir. Base : `main` après la PR #139.
**GTA 6 sort le 19 novembre 2026, soit dans 43 jours.**

---

## 1. Le badge « actu majeure » ne survivra pas au jour de la sortie

**La seule chose qui ait une échéance.**

`HOT_SOURCE_THRESHOLD = 3` : un sujet est « majeur » quand trois rédactions
le couvrent. Aujourd'hui c'est un bon signal — 287 articles sur 3 232
regroupent plusieurs sources, et un seul en réunit cinq.

Le 19 novembre, **tout** sera couvert par trois sources ou plus. Le badge
cessera de distinguer quoi que ce soit, exactement au moment où tu en auras
le plus besoin.

**Piste** : un seuil adaptatif plutôt qu'un nombre fixe — par exemple le top
5 % du jour, ou un seuil qui suit le volume. À mesurer sur l'historique avant
de choisir, comme pour le seuil de ressemblance.

**Non mesuré.** Il faudrait rejouer la distribution du nombre de sources par
sujet, jour par jour, pour voir où placer la bascule.

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

## 3. Trois idées de confort, par ordre d'intérêt

### « Ce que j'ai raté »

Tu as 3 232 articles et un état lu/non-lu. Rien ne te dit « voici les cinq
sujets importants que tu n'as pas ouverts depuis ta dernière visite ».

Toutes les données existent déjà — `readSet`, le nombre de sources par
sujet, le drapeau officiel, l'heure de première vue. **C'est de l'assemblage,
pas de la collecte.** C'est l'idée qui changerait le plus ta lecture
quotidienne.

### Un fil « officiel seulement », lisible d'un coup

50 articles officiels sur 3 232 : le cœur de la veille, noyé dans le reste.
Le filtre existe déjà, mais rien ne présente ces cinquante-là comme une
chronologie courte et complète de ce que Rockstar a dit depuis le début.

### Une page de santé

`sources_health`, `decodage_etat`, `sources_incidents`, les durées de
passage : tout est dans `feed.json` mais ne se lit que dans l'onglet
Statistiques. Un badge d'entête ou une page séparée dirait « le robot va
bien » sans avoir à ouvrir les stats.

---

## Ce qui est RÉGLÉ et ne doit pas être rouvert

### Le mode hors-ligne : vérifié, il marche

Mesuré le 06/10 : réseau coupé, rechargement, **la page s'ouvre, 30 cartes
affichées, zéro erreur JavaScript**. Pas de page blanche dans le métro.

300 articles disponibles hors ligne au lieu de 3 232, et c'est voulu :
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

## Chiffres de référence au 06/10/2026, 23h30 Paris

| | |
|---|---|
| articles dans le fil | 3 232 |
| articles dans l'archive | 3 637 |
| durée d'un passage | **22 s** (56 s avant les correctifs du jour) |
| chargement complet sur téléphone | **1,49 Mo** gzip (2,29 Mo avant) |
| liens dupliqués | 0 |
| articles regroupant plusieurs rédactions | 287 (jusqu'à 5 sources) |
| articles officiels | 50 |
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
