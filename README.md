# GTA6_WATCH — Backend

Ce dossier contient un robot qui va chercher toutes tes sources GTA 6 toutes
les 3 heures, décode les vrais liens Google News, récupère de vraies
miniatures partout, garde un historique complet qui ne perd jamais rien,
peut t'envoyer une vraie notification Android (via ntfy.sh) ou Discord
pour chaque nouvel article, et publie le résultat dans un fichier
`feed.json` que ton tracker HTML peut lire directement — sans passer par
des proxys CORS.

## Ce que fait exactement chaque fichier

- **`fetch_feeds.py`** — le robot principal, tourne automatiquement toutes
  les 3h. Récupère tes 35 sources, décode les liens Google News, va
  chercher les vraies miniatures, déduplique, et **conserve l'historique**
  au lieu de tout régénérer à chaque fois (donc un article ne disparaît
  jamais juste parce qu'une source a publié 30 nouveaux articles depuis).
  L'historique est plafonné à 2000 articles pour rester rapide.
- **`fetch_wayback_history.py`** — script à part, à lancer **une seule
  fois manuellement** (pas de planning automatique). Va chercher dans les
  archives Wayback Machine les annonces Rockstar publiées depuis février
  2022 (date de l'annonce officielle du développement), pour peupler
  l'historique avec du contenu que le RSS ne peut structurellement pas
  couvrir. Voir la section dédiée plus bas.
- **`requirements.txt`** — la liste des dépendances Python, installée
  automatiquement par GitHub Actions.
- **`docs/feed.json`** — le fichier final, régénéré à chaque passage du
  robot, servi par GitHub Pages. C'est ce fichier que ton tracker HTML lit.
- **`.github/workflows/update-feeds.yml`** — la configuration qui dit à
  GitHub "lance fetch_feeds.py toutes les 3h".

## Installation (une seule fois, ~10 minutes)

### 1. Créer un compte GitHub (si tu n'en as pas)
Va sur github.com, inscription gratuite.

### 2. Créer un nouveau dépôt (repository)
- Clique sur le **+** en haut à droite → **New repository**
- Nom : `gta6-backend` (garde ce nom exact — les URLs plus bas dans ce
  guide sont déjà pré-remplies pour ton pseudo GitHub avec ce nom précis)
- Coche **Public** (obligatoire pour le quota gratuit illimité)
- Ne coche aucune case d'initialisation (pas de README, pas de .gitignore)
- Clique **Create repository**

### 3. Uploader les fichiers
Sur la page de ton nouveau dépôt vide, GitHub propose un lien
**"uploading an existing file"** — clique dessus.

Glisse-dépose TOUS ces fichiers, en gardant leurs dossiers :
```
fetch_feeds.py
fetch_wayback_history.py
requirements.txt
docs/feed.json
.github/workflows/update-feeds.yml
.github/workflows/wayback-history.yml
```

Important : ton navigateur/gestionnaire de fichiers doit recréer les
sous-dossiers `docs/` et `.github/workflows/` automatiquement si tu
glisses-déposes le dossier entier. Si l'upload web ne garde pas la
structure, utilise plutôt GitHub Desktop (app gratuite) ou demande à
quelqu'un de t'aider pour cette étape précise — c'est la seule qui peut
coincer sur mobile.

Valide avec **Commit changes**.

### 4. Activer GitHub Pages
- Dans ton dépôt, va dans **Settings** (onglet en haut)
- Menu de gauche : **Pages**
- Sous "Build and deployment" → Source : **Deploy from a branch**
- Branch : **main**, dossier : **/docs**
- Clique **Save**

Après ~1 minute, GitHub affiche l'URL de ton site, du genre :
`https://antoniman31.github.io/gta6-backend/`

### 5. Lancer le robot une première fois manuellement
- Onglet **Actions** en haut du dépôt
- Clique sur **"Mise à jour des flux GTA 6"** dans la liste à gauche
- Bouton **Run workflow** → **Run workflow** (confirmer)
- Attends 2-3 minutes, actualise la page — un ✅ vert apparaît quand c'est fini

Après ça, le robot se relance automatiquement toutes les 3 heures, pour
toujours. Tu n'as plus rien à faire.

### 6. Brancher le tracker HTML dessus
- Ouvre ton fichier `gta6-watch.html` (celui que tu as déjà)
- Ouvre ⚙ Paramètres
- Dans le champ **"Backend GitHub (optionnel)"**, colle :
  `https://antoniman31.github.io/gta6-backend/feed.json`
- Clique Appliquer
- Clique "Vérifier maintenant"

Si tout marche, les logs afficheront "Lecture directe du backend" au lieu
de la longue liste de tentatives par proxy. Vraies miniatures partout,
chargement quasi instantané.

## Notifications Discord (optionnel)

Dès qu'un nouvel article officiel Rockstar ou d'une source spécialiste GTA 6
tombe, tu peux recevoir un message Discord automatiquement — sans avoir
l'app ouverte, sans souci du blocage HTTPS qu'on avait identifié sur
navigateur (ici ça part du serveur, jamais bloqué).

### Créer un webhook Discord
1. Dans Discord, va dans le serveur/salon où tu veux recevoir les alertes
   (ça peut être un serveur perso rien que pour toi, avec un seul salon)
2. Clique sur l'engrenage ⚙ à côté du nom du salon → **Intégrations**
3. **Créer un Webhook** → donne-lui un nom (ex. "GTA6 Watch") → **Copier l'URL du webhook**

### Ajouter l'URL comme secret GitHub (jamais en clair dans le code)
1. Dans ton dépôt GitHub → **Settings** → menu de gauche **Secrets and
   variables** → **Actions**
2. **New repository secret**
3. Nom : `DISCORD_WEBHOOK_URL` (exactement ça, respecte les majuscules)
4. Valeur : colle l'URL copiée depuis Discord
5. **Add secret**

C'est tout — au prochain passage du robot (ou en le relançant
manuellement), les nouveaux articles enverront un message Discord avec
titre, miniature, et lien direct. Limité à 5 notifications par passage
pour ne pas spammer le salon si beaucoup d'articles arrivent d'un coup ;
les officiels/spécialistes GTA 6 sont toujours prioritaires.

Si tu ne configures pas ce secret, tout continue de marcher normalement,
juste sans notifications.

## Notifications Android directes — ntfy.sh (optionnel, plus simple que Discord)

Si tu veux juste une vraie notification classique sur ton téléphone, sans
passer par une app de discussion, **ntfy.sh** est plus direct : gratuit,
open source, sans compte, une simple app légère à installer une fois.

### Installer l'app ntfy
- Sur ton téléphone Android : installe **ntfy** depuis le Google Play
  Store (ou F-Droid si tu préfères une version sans Google)
- Ouvre l'app, appuie sur **+** pour t'abonner à un topic
- Choisis un nom de topic **difficile à deviner** (ex.
  `antoni-gta6-alerts-8k2j`) — n'importe qui connaissant ce nom pourrait
  s'y abonner aussi, puisque ntfy.sh est un service public sans mot de
  passe. Pas de données sensibles ici (juste des titres d'articles publics),
  donc le risque est minime, mais autant choisir un nom peu évident.

### Ajouter le même nom comme secret GitHub
1. Dans ton dépôt GitHub → **Settings** → **Secrets and variables** → **Actions**
2. **New repository secret**
3. Nom : `NTFY_TOPIC`
4. Valeur : le même nom de topic que celui choisi dans l'app (ex.
   `antoni-gta6-alerts-8k2j`)
5. **Add secret**

C'est tout — dès le prochain passage du robot, une vraie notification
Android apparaît pour chaque nouvel article (max 5 par passage), avec la
miniature en pièce jointe et un lien direct vers l'article au clic.

Tu peux activer Discord, ntfy, les deux, ou aucun des deux — ce sont deux
systèmes indépendants qui ne se gênent pas.

## Historique depuis 2022 (Wayback Machine) — optionnel, à faire une fois

Le RSS classique ne peut structurellement pas remonter avant quelques
semaines/mois. Ce script va chercher dans les archives publiques
d'archive.org les annonces Rockstar publiées depuis février 2022.

**À lancer une seule fois**, depuis l'onglet Actions :
- Onglet **Actions** → clique sur **"Récupérer l'historique depuis 2022
  (Wayback Machine)"** dans la liste à gauche
- Bouton **Run workflow** → **Run workflow** (confirmer)
- Ce script est plus long que le robot habituel (peut prendre 10-20
  minutes selon le nombre d'archives trouvées) — laisse tourner, tu peux
  fermer l'onglet et revenir plus tard vérifier le ✅

Limites honnêtes : peut prendre plusieurs minutes (interroge archive.org
poliment, avec des pauses), la couverture n'est jamais garantie à 100%
(seules les pages que quelqu'un a pensé à archiver existent), et cible
spécifiquement le Newswire Rockstar — pas la presse tierce de l'époque.

## Si tu veux ajuster quelque chose plus tard

- **Changer la fréquence** : ouvre `.github/workflows/update-feeds.yml`,
  modifie la ligne `cron: "0 */3 * * *"` (ex: `*/1` pour toutes les heures)
- **Ajouter/retirer une source** : modifie la liste `FEEDS` dans
  `fetch_feeds.py`, en copiant le même format que les autres lignes
- **Changer le nombre max d'articles conservés** : modifie
  `MAX_HISTORY_SIZE` en haut de `fetch_feeds.py` (2000 par défaut)
- **Changer le nombre de notifs Discord/ntfy par passage** : modifie
  `NOTIFY_MAX_PER_RUN` en haut de `fetch_feeds.py` (5 par défaut)
- **Revenir au mode direct** : vide juste le champ backend dans les
  paramètres du tracker, tout redevient comme avant

## En cas de souci

Onglet **Actions** de ton dépôt → clique sur la dernière exécution → tu
vois le détail de ce qui s'est passé, ligne par ligne, si jamais une
étape échoue.

## Ce qui n'a pas pu être ajouté (honnêteté)

GTAForums et GTA Base ont été testés pour ce projet, dans l'idée d'élargir
encore les sources — les deux bloquent activement les accès automatisés
(pas seulement depuis un navigateur, aussi depuis un script serveur), donc
ils ne sont pas inclus. Rien à faire de ton côté, ce n'est pas un réglage
manquant — c'est une vraie limite de ces deux sites.
