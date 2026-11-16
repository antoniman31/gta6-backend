# GTA6_WATCH

## Règles communes (le même bloc dans les 8 dépôts d'Antoni : le modifier ici, c'est le modifier partout)

Décidées par Antoni le 16/11/2026. Les règles propres à ce dépôt suivent ce bloc ; en cas de conflit, ce bloc l'emporte.

- **Avec Antoni** : français, tutoiement, réponses courtes, franches, sans flatterie. Il est seul utilisateur (Galaxy S26 Ultra, PC Windows).
- **Proposer d'abord** : décrire le changement, attendre un « ok » explicite avant de toucher quoi que ce soit, ne jamais régénérer un fichier de son propre chef. « Pose des questions » veut dire `AskUserQuestion`, avec des choix concrets et un recommandé.
- **Fusion** : PR en brouillon, fusion par squash **seulement sur le « go » d'Antoni**, CI verte. Jamais de fusion de sa propre initiative, même verte.
- **Branche** : celle que la session impose. Aucun nom de branche n'est écrit dans ce dépôt : l'environnement le change à chaque session.
- **Identité de commit** : `AntoniMan31 <268696974+antoniman31@users.noreply.github.com>`. Jamais l'adresse personnelle d'Antoni : GitHub refuse le push qui l'expose.
- **Noms de modèle** : autorisés (ligne de co-auteur des commits comprise).
- **Secrets** : jamais de clé, de jeton ni de mot de passe dans le dépôt ou la conversation ; ils se saisissent dans l'application ou dans les secrets GitHub/Cloudflare. Jamais d'export de données personnelles ni de `.env` commité.
- **Tests** : le lint et les tests du dépôt passent avant chaque commit. Un test ne compte qu'après l'avoir **vu échouer** sur le code d'avant. Jamais affaiblir un contrôle pour obtenir du vert (test sauté, seuil baissé, exception sans raison écrite).
- **Interface** : de vrais boutons (jamais un `div` cliquable) ; dans une appli utilisée au doigt, **zones tactiles d'au moins 48 px** (48 dp sous Android) ; contraste et focus vérifiés par l'outil du dépôt.
- **Robustesse** : une écriture qui peut échouer (stockage, réseau) se voit à l'écran ; toute donnée venue d'un fichier, du stockage ou du réseau est une entrée non fiable. Exporter les données avant toute opération destructive.
- **Écrit** : commentaires en français qui disent **pourquoi** ; aucun chiffre écrit sans l'avoir mesuré ; ne jamais écrire « ça marche » pour ce qui n'a pas tourné sur l'appareil réel.
- **Suivi, trois fichiers à la racine** : `README.md` dit l'**état actuel** ; `JOURNAL.md` garde l'**histoire** (chaque lot s'y ajoute en fin de fichier, sous `### Titre — jj/mm/aaaa`) ; `RESTE-A-FAIRE.md` dit **ce qui reste**, mis à jour à chaque lot. Un fichier de suivi retiré part en entier dans `JOURNAL.md`, sous « Ancien X.md ».

## Règles propres à ce dépôt

Une PWA d'un seul fichier (`docs/index.html`, GitHub Pages, **aucun build**) et un
robot Python (`fetch_feeds.py`, `feed_store.py`, `merge_feed.py`, `discord_notify.py`,
`tableau_discord.py`) lancé toutes les heures par GitHub Actions. GTA VI sort le
**19 novembre 2026 à 00:00, heure de Paris**.

**Réponds en français, et toujours en heure de Paris.** Antoni est seul utilisateur,
sur téléphone : le rendu à 320 et 390 px passe avant tout autre.

## Les quatre suites (toutes vertes avant chaque commit, sauf la dernière quand rien de visuel ne change)

```
python3 test_pipeline.py
python3 check_sources_sync.py
python3 audit_donnees.py
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers CHROMIUM_PATH="$(ls /opt/pw-browsers/chromium_headless_shell-*/*/chrome-headless-shell)" python3 test_navigateur.py
```

Le dossier du navigateur change avec l'image du conteneur (`-1194` puis `-1243` : mesuré le 07/11/2026) : la commande le cherche au lieu de le figer.

**La suite navigateur en parallèle** (choix d'Antoni, 14/11/2026 : « c'est trop long ») : ajoute `--workers 3` à la commande. Mesuré le 14/11/2026 : 180 s au lieu de 501 s, le même 1749/1749 ;
six passages sur six verts à trois processus, un sur six rouge à quatre (une attente de 20 s dépassée sous charge) : **ne monte pas à quatre**. La CI l'utilise aussi. Lance-la en arrière-plan et attends sa fin
avec un moniteur, sans enchaîner de `sleep`.

**Quand la lancer** : en cours de mise au point, rejoue seulement les contrôles concernés (`python3 test_navigateur.py --seulement test_frise_des_jalons ...`) ; la suite complète une seule fois avant le commit.
**Pas de suite navigateur locale quand rien de visuel ne change** (documentation, dépendances, robot Python, agenda) : pipeline, sources et audit suffisent, la CI lance la suite navigateur de toute façon.

Un contrôle que tu ajoutes doit être **prouvé capable d'échouer** : rejoue-le contre l'ancien code
(`git archive HEAD` dans un dossier à part, plus tes tests) avant de l'annoncer vert.

## Règles qui ont déjà coûté un cycle

- **`docs/sw.js`** : bumpe `CACHE_NAME` dès que `docs/index.html` change.
- **README** : décrit l'**état actuel** et son en-tête annonce « **N vérifications** » (`test_pipeline.py`
  refuse qu'il soit faux). L'**histoire** vit dans `JOURNAL.md` : chaque lot s'y ajoute en fin de fichier,
  sous `### Titre — jj/mm/aaaa`. Le README ne change que si l'état change (architecture, règle, commande).
  `RESTE-A-FAIRE.md` se met à jour à chaque lot.
- **Sources** : n'en ajoute ni n'en retire **jamais** sans instruction explicite.
  `FEEDS` (Python) et `DEFAULT_FEEDS` (JS) doivent rester identiques
  (`check_sources_sync.py`), et aucun commentaire ne va *dans* le tableau JS.
- **Agenda** (`docs/agenda.json`) : aucune entrée n'y entre ni n'en sort sans la
  validation d'Antoni. Un statut `annonce` exige un lien officiel ; tout le reste
  est `presse`.
- **Jamais d'URL de remplacement devinée** : elle vient de `--sonde`, d'une ligne
  « redirigé vers » du journal du robot, ou du champ `redirect` de `sources_health`.
- **Le proxy du conteneur bloque** `github.io`, YouTube et la plupart des sites de
  presse. Une sonde ou un `curl` qui échoue ne prouve rien sur le site : lis plutôt le
  journal du robot (`mcp__github__get_job_logs`, sur le dernier passage).
- **Secrets** : `DISCORD_WEBHOOK_URL`, `HEALTHCHECK_URL` (et les anciens secrets VAPID,
  gardés par Antoni mais inutilisés depuis le retrait du push, le 03/11/2026) vivent dans les secrets GitHub, jamais dans le dépôt ni dans la
  page. Aucun jeton dans l'export de sauvegarde.
- **Rien de payant** (pas de résumé par IA). Le User-Agent Reddit ne porte aucun pseudo.
- **Jamais faire passer un contrôle en baissant un seuil** sans dire pourquoi.
- **Aucun chiffre écrit sans l'avoir mesuré**, et aucun nombre recopié dans un test
  (« 63 sources » a fait tomber deux contrôles quand la liste a changé) : le test lit
  la liste.

## Git

- L'identité de commit, la branche et la fusion suivent le bloc commun (fusion par squash sur le « go » d'Antoni).
- Le robot pousse directement sur `main` à chaque passage (`update-feeds.yml`) : repartir de `origin/main` juste avant
  de commencer, et ne jamais modifier à la main les fichiers qu'il écrit (`docs/feed*.json`, `docs/archives/`,
  `decode-cache.json`, `elagues-recents.json`).

## Pièges de mesure (cinq fois, l'instrument était faux et pas la chose mesurée)

- `scrollWidth` ne bouge pas quand une grille `1fr` déborde, et une grille est un bloc :
  ce sont ses *tuiles* qui sortent. Mesure chaque tuile.
- Un élément en `inset:0` épouse son parent par construction.
- Chromium rend `color-mix()` en `color(srgb 0.07 0.43 0.20 / 0.15)` : des flottants 0–1.
- Une carte mesurée pendant son animation d'entrée donne des opacités transitoires :
  `reduced_motion="reduce"`.
- Dans Playwright : `page.clock.install(time=…)` et `timezone_id="Europe/Paris"` ; attends
  `historyPartial === false` avant de compter les articles.

## Méthode de travail avec Antoni

- Une proposition porte des **chiffres mesurés** (règle commune : proposer d'abord, attendre l'« ok »).
- Les commentaires du code disent *pourquoi*, avec la mesure et la date : garde cette
  densité. Une erreur de ta part se dit, se corrige et s'écrit dans `JOURNAL.md`.
- Où chercher : `README.md` (l'état), `JOURNAL.md` (l'histoire, cherche le titre d'un lot), `RESTE-A-FAIRE.md`
  (ce qui reste), `PLAN-journal-incidents.md`. Les tâches planifiées de novembre vivent
  dans le compte (`list_triggers`), pas dans le dépôt.
