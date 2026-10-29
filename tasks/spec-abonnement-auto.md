# Spec — mise à jour automatique du secret PUSH_SUBSCRIPTIONS

Statut : **proposition, rien n'est codé.** Écrite le 29/10/2026 à la demande
d'Antoni (« Préparer l'automatisation »). Elle attend sa validation, et un jeton
qu'**il** crée lui-même, avant la première ligne de code.

## Pourquoi

Les abonnements push meurent seuls et sans prévenir (mesuré le 29/10/2026) :

| abonnement | dernier signe de vie | mort constatée (Paris) |
|---|---|---|
| (premier) | 27/10 13:03 | 28/10 22:03, soit ≤ 33 h |
| `42310181` | envoi accepté 09:29 | 410 à 10:00, soit < 35 min |
| `052d3adf` | présent dans l'app à 12:03:37 | 410 à 13:03:43 ; absent de l'app à 13:04:08 |

Pour `052d3adf`, le journal du téléphone montre l'abonnement **présent** puis
**ABSENT**, la permission restant « granted », sans « pushsubscriptionchange » et
sans « push NON affiché ». Le code de l'app n'a rien désabonné (seul « Désactiver »
le fait). Chaque mort oblige aujourd'hui Antoni à copier un bloc JSON dans le secret
GitHub à la main.

**Ce que l'automatisation ne fait pas** : elle ne guérit pas la mort de
l'abonnement, et les notifications envoyées entre la mort et la prochaine ouverture
de l'app sont perdues (Discord les porte toujours). Elle supprime la corvée du
secret et raccourcit la panne : sans elle, la panne dure jusqu'à ce qu'Antoni
remarque ET recolle ; avec elle, jusqu'à la prochaine ouverture de l'app.

## Ce que fait le flux

1. **L'app** détecte « permission accordée, abonnement ABSENT » (c'est exactement la
   ligne `ouverture : abonnement ABSENT` du journal), se réabonne, **chiffre** le
   nouvel abonnement avec une clé publique, et déclenche le workflow
   `abonnement.yml` avec le paquet chiffré en entrée.
2. **Le workflow** déchiffre avec la clé privée (un secret), vérifie l'abonnement,
   écrit le secret `PUSH_SUBSCRIPTIONS` par l'API GitHub, et ne journalise que
   l'empreinte à huit caractères (`push_notify.empreinte_abonnement`).
3. **Le robot** utilise le nouveau secret dès son passage horaire suivant.

## Pourquoi chiffrer côté app

Le dépôt est **public** : les entrées d'un `workflow_dispatch` sont lisibles dans
l'interface des runs. Un endpoint d'abonnement est un secret (quiconque le possède
peut notifier l'appareil, voir `masquer_endpoints`). Le paquet est donc chiffré
**avant** de quitter le téléphone : AES-GCM pour le contenu, clé AES enveloppée en
RSA-OAEP (SHA-256) ; un RSA seul n'accepte que 190 octets en clair avec une clé de
2048 bits, et un abonnement JSON en fait davantage.

## Secrets et jetons (tous créés par Antoni, jamais par moi)

| nom | contenu | où il vit |
|---|---|---|
| `ABONNEMENT_CLE_PRIVEE` | clé RSA privée | secret GitHub, lu par `abonnement.yml` seul |
| `ABONNEMENT_CLE_PUBLIQUE` | clé RSA publique | secret GitHub, publiée par le robot dans `feed.json` comme `vapid_public_key` |
| `JETON_ECRITURE_SECRETS` | jeton fine-grained, ce seul dépôt, **Secrets : lecture et écriture, rien d'autre**, expiration courte | secret GitHub, lu par `abonnement.yml` seul |

Aucun de ces trois ne figure dans le dépôt, dans la page ni dans l'export de
sauvegarde. La paire RSA se génère dans l'app, comme `generateVapidKeys`
(Web Crypto, rien ne quitte l'appareil). Le jeton d'écriture des secrets n'est
**jamais** dans l'app : seul le workflow le lit.

L'app déclenche le workflow avec le jeton « Actions » qu'elle a déjà
(`gta6watch mobile`, qui expire le 26/11/2026 : même rappel, déjà planifié).

## Nouveautés de code (aucune non épinglée)

- `.github/workflows/abonnement.yml` : `workflow_dispatch` avec une seule entrée,
  `permissions: contents: read`, `timeout-minutes: 5`, actions épinglées par SHA
  comme `test-push.yml`.
- `abonnement_secret.py` : déchiffrer, valider, chiffrer pour GitHub, écrire. Testable
  sans réseau (l'appel d'API est injectable, comme `collecte` dans `fetch_all_feeds`).
- **PyNaCl** à ajouter, épinglé, dans `requirements.txt` : l'API des secrets GitHub
  exige une « sealed box » libsodium (X25519 + XSalsa20-Poly1305), que
  `cryptography` (déjà présent) ne fournit pas. Dependabot le suivra.
- Dans l'app : le chiffrement, le déclenchement, et un état visible
  (« abonnement envoyé », « échec : … »).

## Validation côté workflow (avant d'écrire quoi que ce soit)

- l'abonnement est un objet avec `endpoint` en **https**, `keys.p256dh` et
  `keys.auth` non vides ;
- l'hôte de l'endpoint est dans une liste blanche de services de push (FCM, y compris
  les hôtes en `google.com` comme `jmt17.google.com` vu dans les journaux, Mozilla,
  Windows, Apple) — **à construire à partir des hôtes réellement observés, pas
  devinée** ; un hôte inconnu fait échouer le workflow sans rien écrire ;
- le secret n'est remplacé que par une liste d'**un** abonnement (un seul appareil
  aujourd'hui) ; une taille anormale est refusée.

## Risques et décisions pour Antoni

1. **Qui peut déclencher le workflow** : quiconque détient le jeton « Actions » de
   l'app. Il pourrait y envoyer *son* paquet (la clé publique est publique) et
   rediriger les notifications vers un autre appareil. Impact : on perd les
   notifications, rien d'autre ; il peut déjà lancer le robot. À accepter, ou à
   refuser.
2. **Un jeton qui écrit des secrets est le plus sensible du dépôt.** Il ne vaut que
   pour ce dépôt et que « Secrets », mais il peut les modifier tous (VAPID,
   Discord…). Le garder dans un secret, avec expiration courte, et ne jamais le
   coller dans une conversation.
3. **Réabonnement automatique à l'ouverture** : il se fait sans demander. C'est le
   comportement voulu, mais il doit être **désactivable** par « Désactiver » (qui
   garde la décision d'Antoni), sinon l'app se réabonnerait contre sa volonté.
4. **Un seul appareil** : la liste est remplacée, pas complétée. À changer si un
   deuxième appareil arrive.

## Contrôles prévus (chacun prouvé capable d'échouer)

- aller-retour de chiffrement : ce que l'app chiffre, Python le déchiffre ;
- un paquet altéré d'un bit est refusé ;
- un hôte hors liste blanche, un endpoint en http, des clés vides : refusés, rien
  n'est écrit ;
- la clé privée, le jeton et l'endpoint n'apparaissent dans aucune sortie ;
- le réabonnement automatique ne se déclenche ni après « Désactiver », ni sans
  permission, ni quand l'abonnement est présent ;
- `abonnement.yml` ne déclare que `contents: read` et n'utilise le jeton d'écriture
  que dans l'étape qui écrit.

## Ce qui n'est pas dans cette spec

Une surveillance qui détecte la mort côté robot et remplace sans ouvrir l'app : elle
est impossible, le robot ne peut pas se réabonner à la place du téléphone.
