# GTA6_WATCH

Une PWA d'un seul fichier (`docs/index.html`, GitHub Pages, **aucun build**) et un
robot Python (`fetch_feeds.py`, `feed_store.py`, `merge_feed.py`, `discord_notify.py`,
`tableau_discord.py`) lancé toutes les heures par GitHub Actions. GTA VI sort le
**19 novembre 2026 à 00:00, heure de Paris**.

**Réponds en français, et toujours en heure de Paris.** Antoni est seul utilisateur,
sur téléphone : le rendu à 320 et 390 px passe avant tout autre.

## Les quatre suites (toutes vertes avant chaque commit)

```
python3 test_pipeline.py
python3 check_sources_sync.py
python3 audit_donnees.py
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers CHROMIUM_PATH="$(ls /opt/pw-browsers/chromium_headless_shell-*/*/chrome-headless-shell)" python3 test_navigateur.py
```

Le dossier du navigateur change avec l'image du conteneur (`-1194` puis `-1243` : mesuré le 07/11/2026) : la commande le cherche au lieu de le figer. La dernière dure plusieurs minutes : lance-la en arrière-plan et attends sa fin
avec un moniteur, sans enchaîner de `sleep`. Un contrôle que tu
ajoutes doit être **prouvé capable d'échouer** : rejoue-le contre l'ancien code
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
- **Aucun nom de modèle** dans un commit, une PR, un commentaire ou un fichier.

## Git

- **Identité de commit** : `AntoniMan31 <268696974+antoniman31@users.noreply.github.com>`.
  Toute autre adresse fait refuser le push (« email privacy restrictions »).
- Développe sur `claude/new-session-hai5yt`. Une fusion *squash* orpheline la branche ;
  pour la réaligner : `git fetch origin main`, `git checkout -B claude/new-session-hai5yt
  origin/main`, puis `git push --force-with-lease=claude/new-session-hai5yt:<SHA>` avec
  le SHA lu par `git ls-remote origin refs/heads/claude/new-session-hai5yt`. **Ne
  l'invente jamais.**
- PR en brouillon ; sors-la du brouillon et fusionne (squash) **seulement quand Antoni
  dit « go »**. `gh api -X POST repos/antoniman31/gta6-backend/pulls/N/ccr/ready_for_review`
  puis `gh api -X PUT .../pulls/N/merge -f merge_method=squash`.

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

- « **Propose, ne code pas** » : propose avec des chiffres mesurés et attends.
- « **Pose tes questions** » : `AskUserQuestion`, des choix concrets, un recommandé.
- Les commentaires du code disent *pourquoi*, avec la mesure et la date : garde cette
  densité. Une erreur de ta part se dit, se corrige et s'écrit dans `JOURNAL.md`.
- Où chercher : `README.md` (l'état), `JOURNAL.md` (l'histoire, cherche le titre d'un lot), `RESTE-A-FAIRE.md`
  (ce qui reste), `PLAN-journal-incidents.md`. Les tâches planifiées de novembre vivent
  dans le compte (`list_triggers`), pas dans le dépôt.
