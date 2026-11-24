# Ce qu'il reste à faire

Mis à jour le 16/11/2026. **Rien n'est cassé.** Ce fichier ne garde que ce qui reste ; l'historique (audits, lots faits, anciennes notes) est dans `JOURNAL.md`,
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

- **`source_link` : fait le 10/11/2026** (trois jours au lieu de sept ; la suppression complète du 27/10 est abandonnée). Le fil publié ne s'allège qu'au passage suivant du robot : mesuré avec la vraie fonction, `feed.json` compressé de 1 044 à 876 Ko (−16,1 %). **Constaté le 16/11/2026** : 1 191 Ko compressés pour 7 707 articles, soit 155 octets par article contre 181 avant (1 044 Ko pour 5 754 articles le 10/11) : −15 %, l'allègement tient.
- **CI de `main` rouge depuis la sortie : réparée (21/11/2026, PR de réparation, suite navigateur complète 2647/2647 en local)** : les contrôles du compteur tournaient à l'heure réelle (horloge figée au 12/11 pour l'avant-sortie, bloc ajouté pour l'après-sortie), la frise des jalons passe de 11 à 12 px, et deux contrôles lisaient le vrai fil (carte NOUVEAU, contraste d'un article lu : voir `JOURNAL.md`).
- **Trou dans le journal des incidents, du 18/11 au 21/11/2026** : le bug de la PR M a fait repartir le suivi des sources de zéro à chaque passage. Rejoué sur les 74 passages : 275 épisodes clos (médiane 2 h, plus long 8 h, aucun alerté) dont 90 auraient été retenus par le journal ; aucune alerte « tombée » ou « retour » perdue. Non reconstitué à la main.

## 2 bis. Lot du 11/11/2026 : effets du jour J (codé, à regarder sur le téléphone)

- **Codé** : cadre autour de l'écran (avec cycle), libellé « GTA VI est sorti · J+N », barre d'état, écran de chargement du jour J, cases et curseur, halo de minuit, une case par effet dans Réglages + bouton « Lancer le halo », couleur Discord rose après la sortie.
- **À regarder sur ton téléphone** : la barre d'état (peut rester noire selon l'appareil), le rayon des coins du cadre : 32 px, confirmé par toi sur ton S26 Ultra, est le défaut.
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
- **Logo et cycle bleu/rose/blanc (17/11/2026, codé, à regarder sur le S26)** : VI de l'en-tête à 320 et 390 px, écran de chargement avec GTA6_WATCH dessous, couleurs du cycle sur tout l'écran (sombre et clair), logo sous « GTA VI EST DISPO ! » (utilise la répétition de minuit). Le bleu du cycle est `#3380ff` et non `#2f7dff` du logo : ce dernier faisait 4,495:1 sur `--bg-elevated`, sous le seuil de 4,5.
- **Les trois couleurs du logo, la suite (validé par Antoni le 17/11/2026, trois PR)** : A (robot : paliers en dégradé, avatar, pied de message, message « sorti » en trois bandes) fusionnée ; **B (l'app, codée le 17/11/2026, à regarder sur le S26)** : paire français / anglais bleu et rose (écart de couleur plus faible qu'avec l'orange : 114 → 70 en sombre, protanope 97 → 49), « J-2 » du chargement en bleu, rose en deuxième accent (badge NOUVEAU, trait des nouveaux, étoile, compteur de l'actu majeure), trait en dégradé, ↻ bicolore, état vide avec le VI, frise des jalons en dégradé, chargement qui passe du bleu au rose, « À propos » avec le VI ; **C (hors app, codée le 17/11/2026)** : aperçu de lien `og:image` et image de couverture du dépôt ; **à faire par toi** : envoyer `docs/og-image.png` dans Settings › General › Social preview du dépôt GitHub (non faisable ici). À vérifier sur ton Discord : l'avatar n'apparaît qu'après la mise en ligne de la page et sur les nouveaux messages.
- **PR E (codée le 17/11/2026, à regarder sur le S26)** : le repère « lu jusqu'ici » suit le cycle ; les officielles aux couleurs du logo Rockstar (badge orange #FFAB00 fixe ; bordure orange, noir, blanc en clair, orange, orange foncé, blanc en sombre) ; « Actu majeure » en rouge fixe (texte #F06F7D en sombre, #B3202E en clair) ; RockstarMag : badge violet fixe, bordure de carte violette fixe puis bleu, violet, rose au jour J ; cartes Actu majeure : bordure rouge fixe puis rouge, rose, blanc au jour J ; voile violet sur RockstarMag ; onglet SÉLECTIONNÉ aux couleurs des cartes (bouton « Fil » bleu, pastilles et onglets non sélectionnés inchangés) ; onglet « Non Rockstar » renommé « Autres » (gris, puis gris clair et blanc au jour J, quand il est sélectionné ; bouton « A ») ; jauges des stats et section « Les autres » de Discord aux couleurs des onglets ; Discord RockstarMag en violet ; section « Officiel » de Discord en orange ; pages annexes à la palette. À vérifier sur le S26 : une carte officielle (badge orange, bordure qui tourne ; le blanc en clair y est presque invisible), le badge « Actu majeure » (rouge, lisible en thème clair), une carte RockstarMag (badge violet, voile, bordure qui tourne), les onglets Actu majeure / Rockstar / RockstarMag sélectionnés et le bouton « R / RM », le repère « lu jusqu'ici » qui change de couleur.
- **PR F (codée le 17/11/2026, à regarder sur le S26)** : titre de jour, ligne « n affichés » et bouton ↻ aux couleurs de l'onglet sélectionné (fixes, puis la famille de l'onglet au jour J) ; sélection de texte et curseur ; nom de source par type ; frise de l'onglet Rockstar par type ; barre d'état teintée de 16 % de la couleur de l'onglet (peut ne rien changer selon le téléphone).
- **PR G (codée le 17/11/2026, à regarder sur le S26)** : photo de fond (violette en sombre, orange en clair), éteinte par défaut : Réglages › Affichage › « Photo de fond », voile de 30 à 85 % (départ 55 %). À vérifier : le défilement reste fluide avec la photo (couche fixe), les textes se lisent partout (mesuré dans le navigateur de test, pas sur ton écran), la photo se charge sans à-coup en 4G (157 et 171 Ko) ; PR H : tout en forme de carte, avec ou sans photo (titre de jour collant, ligne « n affichés », repères « lu jusqu'ici » et « nouveaux · déjà vus », chronologie Rockstar, bandeau du bas) et interlignes resserrés (titre 1,2, date 1,25) ; la ligne réduite du haut en carte avec le logo VI ; l'aperçu des Réglages garde tous ses éléments quelle que soit l'option.\n- **Le contrôle « vignette » qui échouait au hasard en CI : cause trouvée et réparée (21/11/2026)** : une course entre le clic et l'arrivée de l'historique complet, qui redessinait le fil et faisait perdre son marqueur à la carte. `rattrapageLance = true` avant le chargement ; détail dans `JOURNAL.md`. Les « relances vertes » d'avant étaient une course gagnée.
- **Barres collées aux bords, opaques (18/11/2026, à regarder sur le S26)** : barre du haut et du bas touchent les bords, 100 % opaques, avec remplissage de la zone système (barre d'état, geste) ; le fondu du bas est gardé. À vérifier sur l'appareil : rien ne passe derrière les deux barres, la barre du haut couvre bien la barre d'état, le bouton « Fil » ne gêne pas le coin arrondi de l'écran (réglage « Coins du bas »). Même PR : le jour en cours (« Aujourd'hui », « Hier · 23h00 – 23h54 »…) est la deuxième ligne de la barre du haut (85 px), trait et texte à la couleur de l'onglet ; à regarder sur le S26 : le texte change bien au défilement et rien ne saute.
- **Espaces resserrés de deux crans (PR J, 18/11/2026, à regarder sur le S26)** : entre les blocs du haut et du fil, et entre les cartes (8 / 8 / 4 / 8 / 8 px, 6 px entre cartes) ; l'en-tête et l'intérieur des cartes n'ont pas bougé. Si c'est trop serré, le premier cran (10 / 10 / 6 / 12 / 12, 8 entre cartes) est dans l'historique de la PR.
- **Languette « Reprendre » posée sur la barre du bas (PR K, 18/11/2026, à regarder sur le S26)** : elle apparaît quand le repère de lecture est hors de vue ; collée au haut de la barre, angles de carte en haut, 36 px, fond de l'onglet sélectionné (cycle au jour J). À vérifier : elle ne gêne pas la lecture, et un appui dessus saute bien au repère.
- **Onglet Rockstar : chronologie / cartes et balayage (18/11/2026, fait, vérifié par Antoni sur son téléphone le 19/11/2026)** : bouton avant le mode compact, balayage des lignes, étoile, année entre les cartes. Rien à faire.
- **Message Discord « sorti » (19/11/2026, fait, vu par Antoni : « tout était bon »)** : envoyé vers 00h03 (le passage horaire démarre à 00h01), le message normal du robot 3 minutes après. Détail dans `JOURNAL.md`. Reste à supprimer à la main les messages TEST du salon, s'il en reste.
- **Frise Rockstar en barres de couleur (PR L, 18/11/2026, à regarder sur le S26)** : plus de trait ni de points ; barre de 4 px par carte (orange officiel, rouge Actu majeure, gris), année en carte pleine largeur. À vérifier : la barre se voit bien en clair avec la photo, et le cycle du jour J tourne sur les barres des annonces officielles et des actus majeures.
- **Le 19/11 à minuit, sur ton téléphone** : la palette du jour J (accent rose, chiffres bleus depuis le 17/11/2026, palette du 07/11/2026) est mesurée dans le navigateur de test, pas encore vue sur un vrai écran. Le texte à 12 px minimum et le balayage vers la gauche (mettre de côté) non plus : à regarder dès maintenant pour ces deux-là.

## 4. Décisions prises : ne pas rouvrir

- **Le push est retiré** (03/11/2026) et ne sera jamais réactivé ; le service worker ferme l'ancien abonnement. Les secrets `VAPID_*` restent chez GitHub, inutilisés.
- **Le gel de l'app est abandonné** (03/11/2026). On continue d'ajouter ; la répétition du 10/11 reste à faire.
- **Actu majeure : « trois flux »** (04/11/2026). Mesure du jour (`mesure_actu_majeure.py`) : 109 articles à trois flux ou plus sur 4 389, dont **44 à 54** avec au moins trois sites distincts.
  À reprendre après la remesure du 22/11.
- **Sources** : on n'en ajoute ni n'en retire jamais sans instruction. Les quatre requêtes du 03/11 (patchs EN, serveurs EN et FR, streamers) sont un filet pour le jour J : deux articles utiles en
  quatorze jours avant leur ajout. `rockstar-support` n'a apporté aucun article exclusif ; elle reste.
- **Connus, mesurés, sans suite** : les deux flux YouTube échouent ensemble environ 27 % des passages ; JeuxActu est en erreur depuis le 21/10 sans rien coûter ; Reddit r/GTA6 est retiré.
- **Le mode sans spoiler reste ALLUMÉ après la sortie ; c'est Antoni qui décide quand l'enlever** (décidé le 19/11/2026). Ne plus lui poser la question.
- **Rien de payant** (pas de résumé par IA). Les noms de modèle sont autorisés depuis le 16/11/2026 (règle commune des dépôts).

## 4 bis. Zones tactiles à 48 px et CI à trois niveaux (faits le 16/11/2026, avant la sortie, choix d'Antoni)

- [x] **Regardé sur le téléphone le 16/11/2026** (Antoni, sur le S26, après la mise en ligne de la #299) : boutons, filtres,
      pastilles des catégories et titres du mode compact à 48 px ; répétition de minuit rejouée avec.
- [ ] **CSP : écartée pour l'instant.** La page compte 122 gestionnaires d'événements écrits dans le HTML (`onclick=`…,
      dont ceux des cartes, fabriqués avec les données des articles) ; une CSP stricte demanderait de tous les réécrire en
      délégation d'événements. L'échappement de ces gestionnaires a été relu le 16/11 (`escapeAttr` : JavaScript puis HTML,
      dans le bon ordre) : pas de faille trouvée. Gros chantier, à décider après la sortie.

## 5. Idées non faites

- **Notes de la presse** : une pastille « 18/20 » sur la carte d'un test, et un panneau avec la moyenne et le nombre de tests. À mesurer **après** la sortie, sur le fil réel : précision de 95 % exigée,
  et on ne moyenne jamais des étoiles ni des mentions sans chiffre.
- **Bande « À la une »** en tête de fil : elle dépend de la mesure du seuil de l'actu majeure.
- **Pistes d'interface** (« Interface et expérience », huit pistes du 09/10) : l'état de chacune est dans `JOURNAL.md` ; à relire avant de s'y fier.
- **Répétition de minuit (14/11/2026, codée, à essayer sur ton téléphone)** : ouvre « Répétition du jour J » dans Réglages (ou `repetition.html`) (https://antoniman31.github.io/gta6-backend/repetition.html), touche « Minuit dans 30 secondes » et regarde. Rien n'est enregistré. À regarder : confettis, texte, vibration, couleurs d'après la sortie ; dis-moi ce qui cloche avant le 19/11.
  Validée par Antoni le 14/11/2026 : « ça marche bien, à minuit tout se déclenche ». Rejouée le 16/11, après le passage à 48 px.
- **Confettis version 6 dans l'app (14/11/2026, à regarder sur le téléphone)** : validée par Antoni sur la page d'essai (« Ok ajoute »), mise dans `lanceConfettis` : canons, tas en rangées, écran plein en 4 s à 4,5 s, canons réduits sous un gros texte « GTA VI / EST / DISPO ! », plus de consigne « touche ». Regarde-la avec la répétition (`repetition.html`, « Minuit dans 30 secondes ») et dis-moi ce qui cloche avant le 19/11. La page d'essai reste en ligne (non liée).
- **Agenda : la sortie du 19/11 (14/11/2026, codée)** : entrée « Sortie de GTA VI sur XSX et PS5 », statut « annoncé », source l'article Rockstar « Grand Theft Auto VI is Now Set to Launch November 19, 2026 » (domaine officiel), `verifie_le` au 14/11, au-dessus de la carte de Leonida. Sans heure : le lien lu ne donne que la date. À toi : si tu me colles l'adresse complète de la page du magasin Rockstar (`store.rockstargames.com`), je peux la mettre à la place de l'article.
- **Mode sans spoiler : ta liste (16/11/2026)** : liste nettoyée (178 mots, sans `rat`, `tue`, `tué`, `tuer`, `indic`, `spoil`, `script`, `kill`, `kills`) livrée en fichier ; à coller dans Réglages, « Mots-clés de spoiler ». PR « début de mot » ouverte (la recherche ne trouve plus un mot au milieu d'un autre : « trending », « studies ») : à fusionner par toi avec « go ». Mesuré : 691 articles voilés sur 7 619 (9,1 %) avec cette liste, contre 64 (0,8 %) avec les 22 mots d'origine.
- **Hors ligne et erreur 5xx : corrigé le 16/11/2026** (service worker v117, PR à fusionner par Antoni). Mesuré sur le vrai service worker : hors ligne l'app s'ouvre avec ses articles mémorisés (30 cartes) ; une réponse 503 remplaçait l'app par la page d'erreur ; ouvrir `repetition.html` écrasait la copie de l'app (hors ligne, l'app s'ouvrait alors sur la répétition). Les trois sont réglés. Reste non mesuré : un réseau très lent (premier affichage 2,0 s à 1 Mbit/s et 5,9 s à 300 kbit/s, mesuré le 14/11/2026) : l'app attend toujours le réseau avant de retomber sur la copie.
- **Liste de contrôle de la veille (16/11/2026, codée)** : Réglages › « Liste de contrôle du jour J » (ou `liste-jour-j.html`), 18 cases dont les quatre points fragiles mesurés (app au premier plan, premier appui pour la vibration, animations non réduites, écran allumé). Une seule case dépend de toi seul : « le canal d'alerte du signal de vie est confirmé » (je ne vois pas où le service externe te prévient).
- **Alerte Discord de panne (14/11/2026, codée)** : « Le robot a échoué » / « est reparti », une fois par panne. À essayer une fois : lancer « Tester les notifications Discord » (workflow manuel), un message TEST arrive dans ton salon.
- **Lenteur corrigée le 14/11/2026 (à regarder sur le téléphone)** : l'historique complet qui arrive et la vue Stats ramaient (20 s et 4,8 s de blocage mesurés dans le navigateur de test) ; mesuré après : 0,9 s et 0,3 s. Dis-moi si ça te paraît fluide sur ton S26 ; sinon, prochaine étape : découper le calcul en tranches.
- **Interface du 14/11/2026 (codée, à regarder sur le téléphone)** : icône « Mode compact » sur la ligne de comptage, ligne des soucis repliée au premier affichage (chevron pour tout voir), photo des cartes en 21:9. À regarder : l'icône est-elle trouvable, la photo rognée te va-t-elle ? Restent des idées non retenues : bouton flottant (A2), tuiles du compteur plus petites (B2 : trop petites sous la règle des 12 px), photo 2:1 (C1).

## 5 bis. Polices servies par le site (21/11/2026) : à vérifier sur le S26

- Les chiffres du compte à rebours sont bien en JetBrains Mono, le reste de la page en Inter, comme avant.
- En mode avion, une fois l'app ouverte au moins une fois en ligne : les mêmes polices, pas celles du système.

## 5 ter. Version PC (24/11/2026) : à vérifier sur le PC

- Au-dessus de 1100 px : le rail à gauche, le fil au centre, le panneau à droite ; l'en-tête reste collé en haut quand on fait défiler.
- Le panneau « Dernières actus majeures » : trois lignes cliquables, qui ouvrent l'article dans un nouvel onglet.
- Les raccourcis : `o`, `f`, `l` sur la carte sous la souris, `/` vers le filtre, `1` à `5` pour les onglets ; rien quand on écrit.
- Sous 1100 px (fenêtre réduite) : la disposition du téléphone, comme avant.
- Sur le S26 couché : faire défiler le panneau de droite avec le doigt ne lance plus l'actualisation et le panneau défile ; tirer vers le bas dans le fil (au centre) actualise toujours ; le bouton ↻ du panneau actualise.
- Pas encore fait, à décider : adapter le mode « Réglages » et la page « Stats » à la largeur PC (elles gardent leur colonne de 640 px).
- **Avant de regarder le paysage : désinstaller puis réinstaller l'app** (« Ajouter à l'écran d'accueil »). Le manifeste ne verrouille plus le portrait (24/11/2026), mais Android ne relit le manifeste d'une app installée qu'à sa mise à jour, qui peut prendre un jour ou plus. Dans un onglet Chrome normal, la rotation marche sans réinstaller ; vérifier aussi que la rotation automatique n'est pas verrouillée.
- Téléphone en paysage (à regarder sur le S26) : le rail à gauche sans passer sous l'encoche, le fil et le panneau côte à côte, le panneau qui défile jusqu'à la dernière actu majeure ; en portrait, rien n'a changé.

## 6. Mémos

- Quatre suites vertes avant chaque commit (commandes dans `README.md`).
- Le jeton « gta6watch mobile » expire le **26/11/2026** ; l'horloge cron-job.org (« horloge-gta6 ») le **28/08/2027**.
- Antoni est seul utilisateur, sur téléphone : le rendu à 320 et 390 px passe avant tout autre.
