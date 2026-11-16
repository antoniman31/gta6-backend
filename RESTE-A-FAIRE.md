# Ce qu'il reste à faire

Mis à jour le 11/11/2026. **Rien n'est cassé.** Ce fichier ne garde que ce qui reste ; l'historique (audits, lots faits, anciennes notes) est dans `JOURNAL.md`,
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

- **`source_link` : fait le 10/11/2026** (trois jours au lieu de sept ; la suppression complète du 27/10 est abandonnée). Le fil publié ne s'allège qu'au passage suivant du robot : mesuré avec la vraie fonction, `feed.json` compressé de 1 044 à 876 Ko (−16,1 %). Reste à constater après un passage du robot que `feed.json` a bien maigri.
- **Le mode sans spoiler** après la sortie : lui demander s'il veut l'éteindre.

## 2 bis. Lot du 11/11/2026 : effets du jour J (codé, à regarder sur le téléphone)

- **Codé** : cadre autour de l'écran (avec cycle), libellé « GTA VI est sorti · J+N », barre d'état, écran de chargement du jour J, cases et curseur, halo de minuit, une case par effet dans Réglages + bouton « Lancer le halo », couleur Discord rose après la sortie.
- **À regarder sur ton téléphone** : la barre d'état (peut rester noire selon l'appareil), le carré sombre autour du logo rose de l'écran de chargement, le rayon des coins du cadre : 32 px, confirmé par toi sur ton S26 Ultra, est le défaut.
- **Lot 2a (11/11/2026, codé)** : chevron « Par source », loupe, titres de cartes et de jour, fondu du bas.
- **Lot 2b (11/11/2026, codé)** : bordures en dégradé des cartes et des tuiles (2 px, même épaisseur que le cadre), case globale et retour à l'automatique, repère « nouveaux », chiffres de Stats en tuiles. Les squelettes de chargement existaient déjà.

- **Grand moment de minuit (codé)** : à essayer avec la ligne « Répétition du jour J » dans Réglages (remplace l'ancien bouton « Lancer le halo », 14/11/2026) ; la vibration dépend du téléphone (Android, après un premier appui) : depuis le 14/11/2026 elle suit les canons (deux battements par salve, plus léger sur l'écran de fin) ; durées de départ jamais senties par moi, à régler d'après ta répétition (dis « trop faible » ou « trop fort »).
- **Minuit, suite (10/11/2026, codé)** : pluie de confettis continue d'environ 7 s qui s'accumule en bas jusqu'à remplir l'écran, puis écran noir « GTA VI EST DISPO ! » jusqu'au toucher ; à essayer avec la ligne « Répétition du jour J » dans Réglages (remplace l'ancien bouton « Lancer le halo », 14/11/2026) ; **fluidité vue sur le téléphone par Antoni le 11/11/2026 : « fluide, très bien ».**
- **Frise des jalons (10/11/2026, codée)** : visible seulement après le 19/11 ; pour la voir avant, aucun réglage (elle n'existe qu'après la sortie) : à regarder le jour même ou en simulant la date dans un navigateur de test.
- **Tout le texte dans le cycle (10/11/2026, codé)** : tous les textes suivent le cycle SAUF les titres d'articles (blancs) ; champ de recherche, loupe, « Filtres » et textes hérités compris. À regarder sur le téléphone : la lisibilité de longs textes en couleur (descriptions, Réglages) ; si c'est trop, on peut garder le blanc pour les paragraphes.
- **Réglages › Couleurs par catégories (11/11/2026, codé)** : quatre familles × quatre zones en pastilles (une ligne « Toute la famille » + une par zone, sans rien à déplier) et un aperçu resserré (310 px de haut à 390 px, contre 620). **Vu sur le téléphone par Antoni le 11/11/2026 : pastilles « nickel », aperçu « assez lisible », rien à changer.** Les préréglages sont trois : « Jour J complet », « Aucun », « Automatique » (« Discret » supprimé le 11/11/2026).

## 3. À regarder quand ça arrive

- **Minuit du 19/11 (réponse d'Antoni, 11/11/2026)** : rien à préparer à la main, « tout est prêt » ; les tâches planifiées surveillent la nuit. Dépendances remontées le 11/11 (googlenewsdecoder 0.2.2, selectolax 1.0.2, playwright 1.64.1) : à vérifier dans le journal du robot que le décodage Google News tient.

- **Retard des officiels** : chercher `[officiel] retard` dans le journal du robot ; après **dix alertes** mesurées, décider d'un éventuel second déclencheur sur les flux officiels. Avant, rien à conclure.
- **Les premiers messages Discord** sont partis (premier bilan du soir le 03/11, tableau du jour du 04/11, `rockstarmag_envoyes` rempli). Reste à voir sur le téléphone **l'écran verrouillé** :
  la ligne d'aperçu est-elle utile, et le bouton « Ouvrir GTA6_WATCH » est-il là ? S'il manque, le journal du robot dit « nouvel essai sans les boutons ».
- **Un second message du tableau du jour** dans la même journée : lire le journal de l'étape « Tableau du jour Discord » (« modifié », « créé », « échec transitoire »).
- **Après la sortie**, les messages ordinaires continuent (« Sorti il y a… »).
- **Agenda reconfirmé le 10/11/2026** par Antoni, les quatre dates (`verifie_le` au 2026-11-10, valable jusqu'au 24/11). Doute sur le 19/11 LEVÉ par Antoni après lecture de l'article officiel : la carte de Leonida est gratuite en téléchargement, l'affiche recto-verso livrée coûte 0,99 $ ; le libellé de l'agenda est corrigé en conséquence. Reste le point d'Amazon : la livraison des exemplaires Xbox repoussée au 28/02/2027 (GameGPU, 09/11), qui ne touche pas la date du 12/11 chez les autres revendeurs. Répétition générale du 10/11 faite, « tout va bien » (liste `tasks/repetition-10-11.md` mise à jour). Rappel créé pour le 20/11 à 10h : le cycle de couleurs doit s'être arrêté.
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
- **Répétition de minuit (14/11/2026, codée, à essayer sur ton téléphone)** : ouvre « Répétition du jour J » dans Réglages (ou `repetition.html`) (https://antoniman31.github.io/gta6-backend/repetition.html), touche « Minuit dans 30 secondes » et regarde. Rien n'est enregistré. À regarder : confettis, texte, vibration, couleurs d'après la sortie ; dis-moi ce qui cloche avant le 19/11.
  Validée par Antoni le 14/11/2026 : « ça marche bien, à minuit tout se déclenche ».
- **Confettis version 6 dans l'app (14/11/2026, à regarder sur le téléphone)** : validée par Antoni sur la page d'essai (« Ok ajoute »), mise dans `lanceConfettis` : canons, tas en rangées, écran plein en 4 s à 4,5 s, canons réduits sous un gros texte « GTA VI / EST / DISPO ! », plus de consigne « touche ». Regarde-la avec la répétition (`repetition.html`, « Minuit dans 30 secondes ») et dis-moi ce qui cloche avant le 19/11. La page d'essai reste en ligne (non liée).
- **Agenda : la sortie du 19/11 (14/11/2026, codée)** : entrée « Sortie de GTA VI sur XSX et PS5 », statut « annoncé », source l'article Rockstar « Grand Theft Auto VI is Now Set to Launch November 19, 2026 » (domaine officiel), `verifie_le` au 14/11, au-dessus de la carte de Leonida. Sans heure : le lien lu ne donne que la date. À toi : si tu me colles l'adresse complète de la page du magasin Rockstar (`store.rockstargames.com`), je peux la mettre à la place de l'article.
- **Mode sans spoiler : ta liste (16/11/2026)** : liste nettoyée (178 mots, sans `rat`, `tue`, `tué`, `tuer`, `indic`, `spoil`, `script`, `kill`, `kills`) livrée en fichier ; à coller dans Réglages, « Mots-clés de spoiler ». PR « début de mot » ouverte (la recherche ne trouve plus un mot au milieu d'un autre : « trending », « studies ») : à fusionner par toi avec « go ». Mesuré : 691 articles voilés sur 7 619 (9,1 %) avec cette liste, contre 64 (0,8 %) avec les 22 mots d'origine.
- **Hors ligne et erreur 5xx : corrigé le 16/11/2026** (service worker v117, PR à fusionner par Antoni). Mesuré sur le vrai service worker : hors ligne l'app s'ouvre avec ses articles mémorisés (30 cartes) ; une réponse 503 remplaçait l'app par la page d'erreur ; ouvrir `repetition.html` écrasait la copie de l'app (hors ligne, l'app s'ouvrait alors sur la répétition). Les trois sont réglés. Reste non mesuré : un réseau très lent (premier affichage 2,0 s à 1 Mbit/s et 5,9 s à 300 kbit/s, mesuré le 14/11/2026) : l'app attend toujours le réseau avant de retomber sur la copie.
- **Liste de contrôle de la veille (16/11/2026, codée)** : Réglages › « Liste de contrôle du jour J » (ou `liste-jour-j.html`), 17 cases dont les quatre points fragiles mesurés (app au premier plan, premier appui pour la vibration, animations non réduites, écran allumé). Une seule case dépend de toi seul : « le canal d'alerte du signal de vie est confirmé » (je ne vois pas où le service externe te prévient).
- **Alerte Discord de panne (14/11/2026, codée)** : « Le robot a échoué » / « est reparti », une fois par panne. À essayer une fois : lancer « Tester les notifications Discord » (workflow manuel), un message TEST arrive dans ton salon.
- **Lenteur corrigée le 14/11/2026 (à regarder sur le téléphone)** : l'historique complet qui arrive et la vue Stats ramaient (20 s et 4,8 s de blocage mesurés dans le navigateur de test) ; mesuré après : 0,9 s et 0,3 s. Dis-moi si ça te paraît fluide sur ton S26 ; sinon, prochaine étape : découper le calcul en tranches.
- **Interface du 14/11/2026 (codée, à regarder sur le téléphone)** : icône « Mode compact » sur la ligne de comptage, ligne des soucis repliée au premier affichage (chevron pour tout voir), photo des cartes en 21:9. À regarder : l'icône est-elle trouvable, la photo rognée te va-t-elle ? Restent des idées non retenues : bouton flottant (A2), tuiles du compteur plus petites (B2 : trop petites sous la règle des 12 px), photo 2:1 (C1).

## 6. Mémos

- Quatre suites vertes avant chaque commit (commandes dans `README.md`).
- Le jeton « gta6watch mobile » expire le **26/11/2026** ; l'horloge cron-job.org (« horloge-gta6 ») le **28/08/2027**.
- Antoni est seul utilisateur, sur téléphone : le rendu à 320 et 390 px passe avant tout autre.
