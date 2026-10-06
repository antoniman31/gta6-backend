# Journal des incidents de source — plan

**État : proposé, en attente de validation. Rien n'est codé.**
Rédigé le 06/10/2026, après la réparation rétroactive du décodage (PR #132, fusionnée).
Base : `de21570`.

---

## 1. Pourquoi ce chantier existe

### Le point de départ : un faux diagnostic

Le 05/10 au soir, j'ai annoncé à Antoni que `reddit-leaks` renvoyait un flux
vide « depuis deux passages » et qu'une alerte Discord partirait le lendemain
vers 18h.

**Les deux affirmations étaient fausses.** Vérifié ce matin en rejouant
**120 versions de `docs/feed.json`** (du 01/10 22h52 au 06/10 06h28 UTC) :

| ce que j'ai dit | ce que disent les données |
|---|---|
| flux vide depuis deux passages | **un seul** passage muet sur 120 |
| c'était hier soir | c'était le **02/10 à 00h02 UTC**, trois jours plus tôt |
| une alerte va partir | `alertee: false` — aucune alerte, et aucune possible |

L'épisode : HTTP 200 avec zéro entrée, le chronomètre de silence s'est posé,
puis s'est effacé après les deux passages réussis d'affilée qu'exige
`REPRISE_CONFIRMEE`. Revenu à 02h02. Le robot a tout géré seul, exactement
comme prévu.

### Ce que ça révèle

Pour répondre à « est-ce que cette source va bien ces temps-ci ? », il faut
rejouer l'historique Git de `feed.json`. **Rien dans le projet ne garde la
trace d'une panne qui s'est réparée toute seule.** Le projet sait dire :

- « elle va mal **maintenant** » → `sources_health`
- « elle va mal **depuis** X heures » → `sources_silence`
- « elle rapporte ces volumes-ci » → `sources_entries_history` (12 relevés)

Il ne sait pas dire : « elle est tombée deux fois ce mois-ci, les deux fois
elle est revenue seule ». C'est précisément la question que je me suis posée,
et à laquelle j'ai répondu de travers.

### L'endroit exact où la mémoire se perd

`fetch_feeds.py`, dans `suivre_sources_muettes` :

```python
elif chrono is not None:
    chrono["succes"] += 1
    if chrono["succes"] >= REPRISE_CONFIRMEE:
        if chrono["alertee"]:
            ...
            alertes.append({"type": "retour", ...})
        # Rétablie : on cesse de la suivre. Une entrée par source en
        # bonne santé gonflerait feed.json de 50 lignes inutiles à
        # chaque passage.
        continue          # ←←← ICI l'incident disparaît pour toujours
    suivi[sid] = chrono
```

À cette ligne, le robot a tout sous la main : `chrono["depuis"]`,
`maintenant`, `chrono["alertee"]`, l'identité de la source. Puis il jette.
Le commentaire a raison sur le fond — il ne faut pas garder une entrée par
source en bonne santé — mais il jette aussi ce qui méritait d'être gardé.

---

## 2. Décisions déjà prises (06/10, Antoni)

| question | réponse |
|---|---|
| Reddit, suite ? | **On n'y touche plus.** Rien n'est cassé. |
| Journal d'incidents ? | **Oui, on le fait.** |
| Où l'afficher ? | **Dans les stats de l'app, rubrique Sources.** Pas dans Discord, pas seulement dans `feed.json`. |

---

## 3. Côté robot

### 3.1 Le journal

Nouvelle clé publiée dans `feed.json` : `sources_incidents`, une liste.
Une entrée est écrite **au moment précis du `continue` ci-dessus** :

```json
{
  "source": "reddit-leaks",
  "debut":  "2026-10-02T00:02:08.692181+00:00",
  "fin":    "2026-10-02T02:02:11.004512+00:00",
  "heures": 2.0,
  "alertee": false
}
```

- `source` : l'identifiant, pas le nom. L'app sait déjà faire la
  correspondance (`noms[s.id]` est construit dans le calcul des stats), et un
  identifiant ne change pas quand une source est rebaptisée.
- `heures` : arrondi à 0,1 h, comme les alertes existantes. Dérivable de
  `fin - debut`, mais stocké pour que l'app n'ait pas à reparser deux dates
  par ligne, et pour que la valeur affichée soit **exactement** celle qu'un
  message Discord aurait annoncée.
- `alertee` : la distinction qui compte à la relecture — « résolu seul, tu
  n'as rien eu à faire » contre « tu as reçu une alerte ».

### 3.2 Seulement les incidents TERMINÉS

Une panne **en cours** n'entre pas dans le journal. Elle est déjà dans
`sources_silence` (depuis quand) et dans `sources_health` (l'état courant).
L'écrire aussi dans le journal créerait deux sources de vérité qui finiraient
par se contredire, et obligerait à réécrire une entrée ouverte à chaque
passage.

**Règle : `sources_silence` est l'état, `sources_incidents` est la mémoire.**

### 3.3 Rétention : 30 jours, plafond 100 entrées

- **30 jours** parce que c'est déjà l'horizon du projet (`SILENT_SOURCE_DAYS
  = 30`), et parce que le bloc affiché annonce une période.
- **Plafond de 100** comme ceinture. Il ne devrait jamais servir : avec
  `REPRISE_CONFIRMEE = 2`, une source qui alterne échec/réussite **ne ferme
  jamais** son incident (`chrono["succes"]` retombe à 0 à chaque échec), donc
  le clignotement ne peut pas inonder le journal. Le pire cas réel est une
  source qui tombe une fois puis réussit deux fois, en boucle : ~16 entrées
  par jour. 100 couvre six jours de ce régime.

Mesure de référence : **1 incident sur 120 passages**. En régime normal le
journal pèse moins d'1 Ko.

### 3.4 Le décodage Google News comme pseudo-source — **à valider**

`suit_le_decodage` a déjà la même forme que `suivre_sources_muettes` : un
`depuis`, une alerte au basculement, un retour. L'ajouter au journal sous un
identifiant réservé (`__decodage__`) coûte environ cinq lignes.

**Pourquoi je le recommande :** la panne du 03/10 — trois jours, 851 articles
abîmés — est l'entrée la plus utile que ce journal puisse jamais contenir, et
elle n'y figurerait pas sans ça. Un journal d'incidents qui oublie le plus
gros incident du projet rate sa cible.

---

## 4. Côté format publié — **à valider**

`write_feed_pair` recopie toutes les clés de premier niveau dans le fichier
allégé : `sources_incidents` ira donc dans `docs/feed.json` **et** dans
`docs/feed-recent.json` (300 articles, c'est ce que l'app charge en premier).
C'est voulu — le bloc de stats doit s'afficher dès la première réponse, sans
attendre le fichier complet.

Coût : moins d'1 Ko en régime normal, 10 Ko au plafond absolu.

**C'est un changement du format publié, donc il passe par toi.**

---

## 5. Côté app

### 5.1 Emplacement

`renderStatsSources`, **juste après le bloc « État des sources »**. L'ordre
raconte quelque chose : l'état courant, puis l'histoire récente de cet état.

### 5.2 Forme : une liste, pas un graphique

À un ou deux incidents par mois, une barre ou une courbe serait du décor pour
deux valeurs. Je réutilise les classes existantes (même famille que « En forte
baisse » et « Les plus silencieuses »), donc **aucun CSS nouveau** — et donc
aucun risque sur les tests de rayon M3 ni sur ceux de contraste.

### 5.3 Le rendu

> **Incidents des 30 derniers jours** (1)
> Pannes de source qui se sont terminées. Une source en panne en ce moment
> figure dans le bloc du dessus.
>
> — Reddit — fuites et rumeurs · 2 oct., 2 h · résolu seul, sans alerte

### 5.4 Le bloc s'affiche AUSSI quand il est vide

> **Incidents des 30 derniers jours**
> Aucun incident depuis 30 jours.

C'est la ligne qui m'aurait évité ma bourde. L'absence d'incident est une
information — pas un vide à masquer. C'est même l'affichage le plus fréquent,
et celui qui a le plus de valeur : il répond « oui, tout va bien » sans avoir
à le déduire.

---

## 6. Pièges repérés d'avance

### 6.1 Le sélecteur des tests navigateur

Les tests repèrent les blocs par leur texte :

```python
etat = page.locator(".stats-bloc").filter(has_text="État des sources")
```

Si le nouveau bloc contenait la phrase « État des sources », le sélecteur
matcherait **deux** blocs et le test casserait en mode strict Playwright.
D'où la formulation « figure dans le bloc du dessus » au §5.3, qui évite
soigneusement de citer le titre voisin.

Vérifié par ailleurs : **aucun test ne compte globalement les `.stats-bloc`**
(`grep '\.stats-bloc.*count()'` ne renvoie rien). Tous les repérages sont
filtrés par texte. L'ajout d'un bloc est donc sans danger pour les 302
contrôles existants.

### 6.2 Références JS → DOM

Aucune. Le bloc est **ajouté**, rien n'est déplacé ni renommé dans
`renderStatsSources`. Aucun `getElementById` ni sélecteur existant n'est
touché.

### 6.3 Le cache du service worker

`docs/index.html` change → `CACHE_NAME` passe de `gta6watch-shell-v14` à
`v15`. Sans ça, les téléphones déjà installés ne verraient jamais le bloc.

---

## 7. Tests à écrire

### `test_pipeline.py` — `test_journal_des_incidents`

Rejoue `suivre_sources_muettes` sur une suite de passages :

1. une source tombe, reste muette, revient → **une** entrée, aux bonnes dates,
   avec la bonne durée et `alertee` correct ;
2. une panne de plus de 24 h → l'entrée porte `alertee: true`, et l'alerte
   Discord part toujours comme avant (non-régression) ;
3. une source qui clignote (échec/réussite alternés) → **aucune** entrée, elle
   ne ferme jamais son incident ;
4. un passage « au repos » (`en_attente`) ne ferme ni n'ouvre rien ;
5. rétention : une entrée de plus de 30 jours disparaît, le plafond de 100
   coupe les plus anciennes ;
6. idempotence : rejouer un passage identique n'ajoute pas de doublon.

### `test_navigateur.py` — `test_bloc_incidents`

1. avec des incidents : le bloc existe, les lignes sont dans le bon ordre
   (le plus récent d'abord), la mention « résolu seul » apparaît sur un
   incident non alerté ;
2. **sans incident** : le bloc existe quand même et dit « Aucun incident » ;
3. le sélecteur `has_text="État des sources"` continue de ne matcher qu'un
   seul bloc.

### Les quatre suites

`test_pipeline.py`, `check_sources_sync.py`, `test_navigateur.py`,
`audit_donnees.py` — toutes vertes avant la PR.
Penser au compteur de vérifications annoncé dans le README (1604 aujourd'hui).

---

## 8. Ordre de travail

1. `enregistre_incident()` + l'appel au `continue` de `suivre_sources_muettes`
2. rétention (30 j + plafond 100)
3. publication de `sources_incidents` dans `main`
4. le décodage comme pseudo-source *(si validé)*
5. tests pipeline, suite verte
6. le bloc dans `renderStatsSources`
7. `CACHE_NAME` v14 → v15
8. tests navigateur, suite verte
9. section README
10. les quatre suites, PR en brouillon, fusion sur vert, réalignement de
    branche, lancement du robot et vérification sur les vraies données

Estimation : deux à trois heures.

---

## 9. Ce qui n'est PAS dans ce lot

### Reddit — on n'y touche plus (décision du 06/10)

Les deux sources tournent, zéro 429 sur 120 passages, 22 articles exclusifs
dont 18 pour `reddit-leaks` seul. Il n'y a rien à réparer.

Trois portes restent fermées, et pour de bonnes raisons :

- **Élargir les mots-clés de r/GTA6** — la sonde a tranché le 05/10. Ne
  revient pas sur la table.
- **Une troisième source Reddit** — impossible sans OAuth. Le tour de rôle
  (`ROTATION_REDDIT`) fait déjà tomber chaque source à une interrogation par
  heure ; à trois, ce serait une toutes les 90 minutes.
- **OAuth « app-only »** — 60 requêtes/minute au lieu d'environ 1. Pas fait
  parce que `oauth.reddit.com` rend du **JSON et non du RSS** : il faudrait un
  lecteur Reddit à côté de `feedparser`, le seul endroit du robot qui cesserait
  d'être « une URL, un flux ». À rouvrir si Reddit ferme le RSS public, ou le
  jour où on veut plus de deux sources.

### Le journal dans Discord

Écarté par Antoni : l'affichage est dans les stats de l'app, et là seulement.

### Autre, sans rapport

Le graphique mensuel des statistiques apparaîtra tout seul le 1er novembre,
quand il y aura deux mois complets à comparer. Rien à faire.

---

## 10. Chiffres vérifiés, pour ne pas les redériver

| | |
|---|---|
| passages examinés | 120 (01/10 22h52 → 06/10 06h28 UTC) |
| incidents `reddit-leaks` | 1 — le 02/10 00h02, revenu à 02h02, jamais alerté |
| incidents `reddit-gta6-suivi` | 0 |
| 429 Reddit depuis le tour de rôle | 0 |
| `DEAD_SOURCE_HOURS` | 24 |
| `REPRISE_CONFIRMEE` | 2 |
| `SILENT_SOURCE_DAYS` | 30 |
| `HISTORIQUE_PASSAGES` | 12 |
| `RECENT_FEED_SIZE` | 300 |
| `CACHE_NAME` actuel | `gta6watch-shell-v14` |
| vérifications `test_pipeline` | 1604 |
| contrôles `test_navigateur` | 302 |
