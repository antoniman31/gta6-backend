# GTA6_WATCH — Backend + App

Un robot qui va chercher toutes les news GTA 6 sur 35 sources toutes les
heures, décode les vrais liens Google News, récupère de vraies miniatures
partout, garde un historique complet qui ne perd jamais rien, t'envoie une
notification Discord pour chaque nouvel article, et publie tout dans une
vraie app installable sur ton téléphone.

## Vue d'ensemble

- **`fetch_feeds.py`** — le robot principal, tourne automatiquement toutes
  les heures via GitHub Actions. Récupère les 35 sources, décode les liens
  Google News, va chercher les vraies miniatures (en parallèle, 5 à la
  fois), déduplique, et conserve l'historique (plafonné à 2000 articles).
  Envoie une notification Discord pour chaque nouvel article trouvé.
- **`requirements.txt`** — dépendances Python, installées automatiquement.
- **`docs/feed.json`** — le fichier de données, régénéré à chaque passage
  du robot.
- **`docs/index.html`** — l'app elle-même : une vraie PWA installable,
  affiche les articles, badges (officiel, spécialiste, leak, vidéo, FR),
  recherche, filtres, mode dense, compte à rebours jusqu'à la sortie du jeu.
- **`docs/manifest.json`, `docs/sw.js`, `docs/icon-*.png`** — fichiers
  requis pour que l'app soit installable comme une vraie PWA sur Android.
  4 icônes : standard + maskable (dédiée, texte resserré pour ne pas être
  coupée si Android rogne en cercle) en 192px et 512px. Texte "GTA 6
  WATCH" avec la police Bebas Neue (libre de droits, Open Font License) —
  pas le logo officiel Rockstar, qui est une marque déposée.
- **`.github/workflows/update-feeds.yml`** — programme le robot pour
  tourner chaque heure, avec un mécanisme de nouvelle tentative en cas de
  conflit de publication (plusieurs exécutions qui se chevauchent) et un
  timeout de 20 minutes pour le job entier.

## Accès à l'app

Une fois installé (voir ci-dessous), le site est accessible directement à :
```
https://antoniman31.github.io/gta6-backend/
```
Installable comme une vraie app sur Android (icône G6 sur l'écran
d'accueil, plein écran, sans barre d'adresse).

## Installation (déjà faite — pour référence si besoin de tout refaire)

### 1. Créer le dépôt
Dépôt GitHub public nommé `gta6-backend`, tous les fichiers uploadés en
gardant la structure des dossiers (`.github/workflows/`, `docs/`).

### 2. Activer GitHub Pages
Settings → Pages → Source : Deploy from a branch → main → `/docs`.

### 3. Premier lancement
Actions → "Mise à jour des flux GTA 6" → Run workflow. Après ça, le robot
tourne tout seul chaque heure, pour toujours.

### 4. Installer la PWA sur le téléphone
Ouvrir `https://antoniman31.github.io/gta6-backend/` dans Chrome → menu
"..." → Installer l'application (ou "Créer un raccourci" si l'option
Installer est grisée).

## Notifications Discord

Actives et fonctionnelles, avec nouvelle tentative automatique en cas
d'erreur temporaire (429 rate-limit, respecte le délai indiqué par
Discord ; 5xx serveur, backoff progressif) — jusqu'à 3 tentatives par
article avant d'abandonner. Pour les reconfigurer si besoin :
1. Discord → salon → ⚙ Intégrations → Créer un Webhook → copier l'URL
2. GitHub → Settings → Secrets and variables → Actions → New repository
   secret → nom `DISCORD_WEBHOOK_URL` → coller l'URL
3. **Ne jamais partager cette URL en clair** — si elle fuite, la régénérer
   immédiatement côté Discord.

## Notifications ntfy — abandonnées

Tentées puis désactivées : le serveur `ntfy.sh` confirmait l'envoi (200 OK)
mais ne relayait jamais les messages jusqu'au téléphone, même avec un
payload minimal. Cause probable, documentée par ntfy lui-même : fail2ban
ou rate-limit silencieux sur les adresses IP partagées des runners GitHub
Actions. Le code a été entièrement retiré de `fetch_feeds.py` (plus de
trace, ni actif ni en commentaire) — Discord est le seul système de
notification, avec retry automatique en cas d'erreur temporaire (voir
ci-dessous).

## Si tu veux ajuster quelque chose

- **Changer la fréquence** : `.github/workflows/update-feeds.yml`, ligne
  `cron: "0 * * * *"` (actuellement toutes les heures)
- **Ajouter/retirer une source** : liste `FEEDS` dans `fetch_feeds.py`
- **Nombre max d'articles conservés** : `MAX_HISTORY_SIZE` dans
  `fetch_feeds.py` (2000 par défaut)
- **Revenir au mode direct (sans backend)** dans le tracker : vider le
  champ backend dans les paramètres de l'app

## En cas de conflit de publication (push rejeté)

Si deux exécutions du robot se chevauchent (ex: relance manuelle pendant
qu'une automatique tourne), l'étape "Publier le résultat" retente
automatiquement jusqu'à 3 fois en récupérant les changements distants
avant de réessayer. Si ça échoue quand même après 3 tentatives, relance le
workflow une fois manuellement pour rattraper.

## Limites connues

- **GTAForums et GTA Base** — jamais intégrés, ces deux sites bloquent
  activement les accès automatisés (confirmé même depuis un vrai serveur,
  pas juste un navigateur).
- **Miniatures Google News** — un léger pourcentage d'articles n'a pas de
  miniature si le site source bloque les robots ou n'a pas de balise
  `og:image`/`twitter:image` exploitable. Normal, pas un bug.
- **PWA et cache** — le service worker met en cache le squelette de l'app
  (HTML) pour qu'elle s'ouvre même hors-ligne, en affichant les données
  déjà connues via `localStorage`. `feed.json` (les vraies données
  d'actualité), lui, n'est jamais mis en cache — toujours 100% réseau,
  pour ne jamais afficher une actu périmée en silence.
