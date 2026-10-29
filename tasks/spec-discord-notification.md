# Spec — la notification Discord : six améliorations

Statut : **proposition, rien n'est codé.** Écrite le 29/10/2026 à la demande
d'Antoni (« Améliorer la notification Discord, propose 7 idées, ne code pas »), puis
réduite à six idées par son choix : **1, 2, 3, 4, 5 et 7** (l'idée 6, le rappel
d'agenda, est écartée). Visuel final : `tasks/spec-discord-notification-final.png`.
Heures de Paris (UTC+1 depuis le 25/10).

![Visuel final](spec-discord-notification-final.png)

Tout ce qui suit touche `discord_notify.py`, `feed_store.py` et le robot, **pas
l'app** (`docs/`) : le gel de l'app ne s'applique pas. Le chiffre « J-21 » et les
couleurs reprennent pourtant des règles de l'app (voir 2).

## État actuel, mesuré

- Un embed : titre « 🎮 N nouveaux articles GTA 6 », au plus **5** lignes
  (`APERCU_RECAP_MAX`), « + N autres dans l'app », lien. Bleu `#5493FF`, rouge
  `#E04F5F` pour une actu majeure. Les annonces Rockstar ont leur propre embed orange.
- Passages du 29/10 : 9, 10, 11, 16, 24 et 31 articles. Cinq lignes en montrent donc
  entre 16 % et 56 %. Le fil produit de plus en plus : 55 articles le 25/10, 280 le 29/10 (jour en cours, mesuré à 19 h 25).
- Une ligne du récapitulatif mesure 200 caractères en médiane, 271 au 90ᵉ centile,
  343 au maximum (300 articles les plus récents). Les 10 plus longues font ensemble
  **3 195** caractères, sous les 3 900 gardés pour la description.
- Un article d'aperçu ne garde que six champs (`apercu_de`) : titre, lien, source,
  officiel, **nombre** de sources, date.

## Les six changements

### 1 · La vignette de l'article phare
Un `thumbnail` (petite image à droite) sur le récapitulatif, celle de la **première
ligne** (l'ordre existant : un officiel d'abord, sinon le plus récent).

- Mesuré : 261 des 300 articles récents ont une image (87 %), toutes en https, la
  plus longue fait 263 caractères (Discord accepte 2 048) ; 2 834 sur 3 300 sur tout le
  fil (86 %), dont 77 des 85 officiels. Hôtes variés (insider-gaming.com, sm.ign.com,
  cdn.mos.cms.futurecdn.net…).
- Il faut ajouter `image` à l'aperçu (septième champ). La docstring de `apercu_de`
  dit « six champs et pas un de plus » : la raison était le coût dans `feed.json`
  pendant la pause nocturne. Une URL de 263 caractères au plus, sur cinq à dix
  aperçus, reste négligeable ; **à mesurer** avant de livrer.
- Si l'image manque, n'est pas en https ou dépasse 2 048 caractères : pas de vignette,
  le message part comme aujourd'hui. Si l'hôte refuse le lien direct, Discord n'affiche
  rien et le message reste intact : ce n'est pas un échec du robot.
- **Risque spoiler, à trancher par Antoni.** L'app floute les articles à spoiler grâce
  à une liste de mots écrite en JavaScript (`MOTS_SPOILER`) ; le robot Python ne la
  connaît pas, et le récapitulatif affiche déjà les titres bruts. Une image est plus
  parlante qu'un titre. Options : (a) vignette pour tous, (b) vignette pour les seuls
  officiels Rockstar (91 % d'entre eux ont une image), (c) recopier la liste côté
  Python avec un contrôle de synchronisation comme `check_sources_sync.py`.

### 2 · Compte à rebours en pied de message, barre de couleur du compteur
Pied : « ⏳ J-21 · sortie le 19/11/2026 à 00:00, heure de Paris ». N est un nombre de
**jours civils à Paris** (même règle que l'agenda : un jour n'est pas 24 heures, le
25/10 en durait 25).

Couleur de la barre : les paliers de l'app (`palierCompteur`), recopiés côté Python :
moins de 24 h → rose `#ff2fa8` ; moins de 7 jours → orange `#ff8a00` ; moins de
30 jours → jaune `#f7ff00` ; sinon bleu `#5493FF` comme aujourd'hui. **Une actu
majeure garde son rouge** : l'information prime sur le décor.

Après la sortie : plus de pied ni de couleur de compteur (retour au bleu). Question
ouverte : veut-on « sorti depuis N jours » ?

Contrôle prévu : les seuils sont **lus dans `docs/index.html`** par le test (jamais
recopiés dans un test, règle du dépôt), des deux côtés de chacun, et à la seconde
près à 00:00 Paris ; même méthode que `SORTIE_GTA6`.

### 3 · Une ligne de chiffres
Champs en ligne : « 🇫🇷 / 🇬🇧 » (le nombre d'articles français et anglais du passage)
et « 📰 Médias » (le nombre de médias distincts). Mesuré le 29/10 : 100 articles en
français et 180 en anglais sur 280 ; 153 médias distincts.

- La langue vient du champ `lang` de chaque article ; il faut la garder dans l'aperçu
  et dans les comptes mis de côté pendant la pause nocturne (`attente_recap` ne
  conserve aujourd'hui que trois nombres : articles, officiels, sommet).
- « Médias » n'est un compte de **distincts** que sur un passage ordinaire. Le
  récapitulatif du matin additionne la nuit : on n'y affiche pas ce champ.

### 4 · Plus de lignes quand le passage est gros
Au lieu de 5 fixes : 5 lignes sous 10 articles, 7 de 10 à 19, **10** à partir de 20.
Mesuré : le pire cas de 10 lignes fait 3 195 caractères, et le titre (256 au plus),
les champs et le pied restent loin des 6 000 caractères que Discord accepte pour
l'ensemble d'un embed. `apercus_recap` et le stockage de nuit doivent suivre le même
plafond.

**Cela rouvre ton choix du 22/09 (« les 5 derniers »)** : à valider explicitement.

### 5 · Un sujet majeur, ses médias nommés
Sur une ligne 🔥, citer les médias : « — *IGN Nordic* · 3 sources : IGN Nordic, IGN ».

- Les noms existent dans l'article (`extraSources`, 328 articles sur 3 300) mais pas
  dans l'aperçu, qui ne garde que le nombre. Il faut y ajouter les noms, trois au
  plus, de 30 caractères au plus, protégés par `echappe_markdown`.
- **Constat qui change la question.** Les « sources » comptent des **flux**, pas des
  médias. Sur 93 articles à trois sources ou plus, **90 en ont moins de trois hors
  « Google News »** (0 : 20, 1 : 48, 2 : 22, 3 : 3). Les trois premiers du 29/10 le
  montrent : leurs « trois sources » sont deux flux Google News plus un média réel
  (par exemple « Google News (EN) », « IGN », « Google News (EN, 7 jours) »). Je ne
  sais **pas** combien de médias distincts cela représente : le média réel d'un
  article Google News est dans le suffixe de son titre (« … - IGN Nordic »), pas dans
  son nom de flux. Nommer les sources afficherait donc « Google News (EN, 7 jours) ».
- Règle proposée : ne nommer que les médias reconnaissables (le suffixe du titre, et
  les flux qui ne s'appellent pas « Google News »), jamais un nom de flux agrégateur.
- **Question distincte, à ne pas traiter ici sans décision :** le seuil de l'actu
  majeure (3) compte-t-il bien ce qu'on croit ? Une mesure des médias distincts
  d'abord, puis une décision d'Antoni.

### 7 · Un bilan du soir, une fois par jour
Un second message, au **premier passage dont l'heure de Paris est 22 h ou plus** :
nombre d'articles du jour civil, répartition FR/EN, médias distincts, **les cinq
médias les plus actifs** (cinq lignes) et **les trois sujets les plus couverts**.
Exemple réel du 29/10 à 19 h 25 : 280 articles, 153 médias, Insider Gaming 12, IGN
France 7, GamingBolt 7, Notebookcheck.fr 7, GamesRadar+ 6.

- Il faut retenir que le bilan est parti (`bilan_envoye_le: "2026-10-29"` dans
  `feed.json`, comme `attente_recap`) : un passage manuel à 22 h 30 ne le renvoie pas.
- Il s'oppose à la règle « un seul message par passage » : c'est un message de plus,
  une fois par jour. La règle existe contre un message par article ; à valider.
- Il tient dans la pause nocturne (22 h est avant minuit). **Après la sortie**, le
  récapitulatif horaire se tait sauf annonce ou actu majeure (règle du 20/10) :
  le bilan, lui, est un résumé, il n'a pas ce défaut. À confirmer : on le garde.
- Les « plus actifs » se comptent sur le **média du titre**, pas sur le nom du flux :
  compter les flux donnait « Google News (EN) — 124 », qui n'apprend rien.

## Décisions à prendre par Antoni

1. Vignette : pour tous, pour les seuls officiels, ou avec une liste de spoilers côté
   Python ? (1)
2. Après la sortie : un pied « sorti depuis N jours », ou rien ? (2)
3. Plafond de lignes : 5 / 7 / 10 selon le volume, contre les « 5 derniers » du
   22/09 ? (4)
4. Mesurer les médias distincts derrière les « 3 sources » avant de nommer quoi que
   ce soit ? (5)
5. Bilan du soir : un message de plus par jour, conservé après la sortie ? (7)

## Contrôles prévus (chacun prouvé capable d'échouer contre l'ancien code)

- 1 : pas de vignette sans https, ni au-delà de 2 048 caractères ; la vignette est
  celle de la première ligne ; le message reste valide sans image.
- 2 : seuils lus dans `docs/index.html`, de chaque côté, aux frontières de minuit à
  Paris et au passage à l'heure d'hiver ; rouge conservé pour l'actu majeure.
- 3 : langue comptée, y compris à travers la pause nocturne ; « Médias » absent du
  récapitulatif du matin.
- 4 : le plafond suit le volume ; l'embed entier reste sous 6 000 caractères avec dix
  lignes de 343 caractères, vignette, champs et pied.
- 5 : aucun nom « Google News… » affiché ; noms échappés ; trois noms au plus.
- 7 : un seul bilan par jour civil à Paris, même avec deux passages après 22 h ; aucun
  bilan avant 22 h ; heures testées des deux côtés du changement d'heure.
- Dans tous les cas : aucun nom de modèle, aucun secret, aucune URL de webhook dans un
  journal ; `DISCORD_WEBHOOK_URL` reste dans les secrets GitHub.

## Ce que cette spec ne fait pas

Elle ne touche pas aux alertes de source, ni aux annonces officielles (leur embed
orange reste tel quel), ni à l'app. Les maquettes sont des dessins : Discord sur
téléphone peut rendre un embed un peu différemment, et l'image de la vignette y est
un espace réservé.
