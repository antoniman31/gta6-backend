# Ce qu'il reste à faire

Mis à jour le 10/11/2026. **Rien n'est cassé.** Ce fichier ne garde que ce qui reste ; l'historique (audits, lots faits, anciennes notes) est dans `JOURNAL.md`,
sous « Ancien RESTE-A-FAIRE.md ». GTA VI sort le **19/11/2026 à 00:00, heure de Paris**. Tous les chiffres ci-dessous ont été mesurés le 04/11/2026.

## 1. Le calendrier jusqu'au 24/11

| Quand (Paris) | Quoi | Qui |
|---|---|---|
| 05/11, 09:00 | Jalon J-14 dans le salon Discord (journal du robot : « jalon j14 »). `jalons_envoyes` est vide aujourd'hui. | à regarder |
| 08/11 | Premier dimanche : le bilan du soir porte la carte « 📅 Cette semaine ». | à regarder |
| 10/11, 09:00 | **Répétition générale** (tâche planifiée) et **reconfirmation de l'agenda**. Liste à cocher : `tasks/repetition-10-11.md`. Antoni confirme chaque date, puis on met la date du jour sur `verifie_le`. | Antoni |
| 11/11 | L'entrée du 18/11 (vérifiée le 27/10) passe « à revérifier » si elle n'est pas reconfirmée. Celles du 10/11, 12/11 et 19/11 (vérifiées le 03/11) : dès le 18/11. L'entrée du 9/11 a été retirée le 07/11 sur instruction d'Antoni. | |
| 12/11, 16/11, 18/11 | Jalons J-7 (09:00), J-3 (09:00), J-1 (09:00). Le 18/11 aussi : H-12 (12:00) et H-1 (23:00). | à regarder |
| 15/11, 10:00 | Rappel : exporter une sauvegarde depuis l'app. | Antoni |
| 18/11, 22:30 | Veille de la sortie (tâche planifiée). La pause nocturne est levée du 18 au 20/11. | |
| 19/11, 00:30, 02:00, 09:00 | Surveillance de la nuit, puis bilan (tâches planifiées). | |
| 22/11, 10:00 | Remesure d'après-sortie (tâche planifiée) : catégories, seuil de l'actu majeure, sources, mode sans spoiler, notes de la presse. | |
| 24/11, 10:00 | Rappel : le jeton mobile expire le 26/11. | Antoni |

Les tâches planifiées vivent dans le compte, pas dans le dépôt : `list_triggers`.

## 2. À trancher

- **Après la sortie, l'agenda est caché** (07/11/2026, choix d'Antoni) : son code et `docs/agenda.json` restent pour de futurs DLC ou mises à jour, quand on aura des dates officielles. À la sortie, c'est aussi la fin des dodos et de la barre de progression de la fenêtre du compteur ; les compteurs repartent à l'endroit (mois, puis années quand il y en a besoin). Rien à faire, à vérifier le 19/11 à 00:00.

- **`source_link` : passer de 7 à 3 jours** (choix d'Antoni, 10/11/2026, à la place de la suppression complète du 27/10). Mesuré le 10/11 sur `feed.json` compressé : 1040 Ko à 7 jours, 871 Ko à 3 jours (−16 %), 748 Ko sans le champ (−28 %) ; sur `feed-recent.json`, −52 % sans le champ. Le champ sert à l'app à suivre un article dont le robot corrige le lien (`migreLiensDecodes`) : le retirer rendrait possible un nouvel incident comme celui du 03 au 06/10 (453 articles repassés en « non lu »). **Pas codé : en attente du « go » d'Antoni** (la constante est `JOURS_SOURCE_LINK_PUBLIE` dans `feed_store.py`).
- **Le mode sans spoiler** après la sortie : lui demander s'il veut l'éteindre.

## 3. À regarder quand ça arrive

- **Retard des officiels** : chercher `[officiel] retard` dans le journal du robot ; après **dix alertes** mesurées, décider d'un éventuel second déclencheur sur les flux officiels. Avant, rien à conclure.
- **Les premiers messages Discord** sont partis (premier bilan du soir le 03/11, tableau du jour du 04/11, `rockstarmag_envoyes` rempli). Reste à voir sur le téléphone **l'écran verrouillé** :
  la ligne d'aperçu est-elle utile, et le bouton « Ouvrir GTA6_WATCH » est-il là ? S'il manque, le journal du robot dit « nouvel essai sans les boutons ».
- **Un second message du tableau du jour** dans la même journée : lire le journal de l'étape « Tableau du jour Discord » (« modifié », « créé », « échec transitoire »).
- **Après la sortie**, les messages ordinaires continuent (« Sorti il y a… »).
- **Agenda reconfirmé le 10/11/2026** par Antoni, les quatre dates (`verifie_le` au 2026-11-10, valable jusqu'au 24/11). Doute écrit sur le 19/11 : le mot « gratuit » de la carte n'est pas confirmé (RTBF parle d'une carte « à 1 dollar », Push Square dit que l'édition physique n'inclut pas la carte) ; l'article officiel Rockstar ne se lit pas depuis le conteneur (contenu chargé par JavaScript). Rappel créé pour le 20/11 à 10h : le cycle de couleurs doit s'être arrêté.
- **Le 19/11 à minuit, sur ton téléphone** : la palette du jour J (accent rose, chiffres cyan, 07/11/2026) est mesurée dans le navigateur de test, pas encore vue sur un vrai écran. Le texte à 12 px minimum et le balayage vers la gauche (mettre de côté) non plus : à regarder dès maintenant pour ces deux-là.

## 4. Décisions prises : ne pas rouvrir

- **Le push est retiré** (03/11/2026) et ne sera jamais réactivé ; le service worker ferme l'ancien abonnement. Les secrets `VAPID_*` restent chez GitHub, inutilisés.
- **Le gel de l'app est abandonné** (03/11/2026). On continue d'ajouter ; la répétition du 10/11 reste à faire.
- **Actu majeure : « trois flux »** (04/11/2026). Mesure du jour (`mesure_actu_majeure.py`) : 109 articles à trois flux ou plus sur 4 389, dont **44 à 54** avec au moins trois sites distincts.
  À reprendre après la remesure du 22/11.
- **Sources** : on n'en ajoute ni n'en retire jamais sans instruction. Les quatre requêtes du 03/11 (patchs EN, serveurs EN et FR, streamers) sont un filet pour le jour J : deux articles utiles en
  quatorze jours avant leur ajout. `rockstar-support` n'a apporté aucun article exclusif ; elle reste.
- **Connus, mesurés, sans suite** : les deux flux YouTube échouent ensemble environ 27 % des passages ; JeuxActu est en erreur depuis le 21/10 sans rien coûter ; Reddit r/GTA6 est retiré.
- **Rien de payant** (pas de résumé par IA). **Aucun nom de modèle** dans un commit, une PR ou un fichier.

## 5. Idées non faites

- **Notes de la presse** : une pastille « 18/20 » sur la carte d'un test, et un panneau avec la moyenne et le nombre de tests. À mesurer **après** la sortie, sur le fil réel : précision de 95 % exigée,
  et on ne moyenne jamais des étoiles ni des mentions sans chiffre.
- **Bande « À la une »** en tête de fil : elle dépend de la mesure du seuil de l'actu majeure.
- **Pistes d'interface** (« Interface et expérience », huit pistes du 09/10) : l'état de chacune est dans `JOURNAL.md` ; à relire avant de s'y fier.

## 6. Mémos

- Quatre suites vertes avant chaque commit (commandes dans `README.md`).
- Le jeton « gta6watch mobile » expire le **26/11/2026** ; l'horloge cron-job.org (« horloge-gta6 ») le **28/08/2027**.
- Antoni est seul utilisateur, sur téléphone : le rendu à 320 et 390 px passe avant tout autre.
