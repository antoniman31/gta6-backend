# Spec : mesurer `og:description` pendant 24 h (proposition, rien n'est codé)

Rédigée le 27/10/2026. **En attente de la validation d'Antoni.**

## Objectif

Savoir, avant toute décision, **combien d'articles sans résumé en trouveraient un**
en lisant la balise `og:description` de leur page. Aujourd'hui 85,5 % des résumés
sont vides (2 746 articles sur 3 210, mesuré le 27/10 sur `docs/feed.json`), donc le
bouton d'aperçu n'existe que sur 14,5 % des cartes.

La mesure **ne change rien à l'app** : aucun champ ajouté à `feed.json`, aucun texte
conservé.

## Ce qui est mesuré aujourd'hui (pour dimensionner)

- 113 articles sont entrés dans les dernières 24 h ; 667 sur 7 jours, soit ~95 par jour.
- Sur les 24 dernières heures : 99 sans résumé, dont 15 sans image.
- Le robot ne télécharge une page d'article **que** pour les articles sans image
  (`fetch_missing_images`) : ~15 pages par jour. Les ~84 autres articles sans résumé
  n'ont jamais leur page lue.

## Mécanique proposée

1. `fetch_og_image` lit déjà la page des articles sans image : on y ajoute la **lecture**
   de `og:description` (et `meta name="description"` en repli), sans requête de plus.
2. Pour les articles avec image et sans résumé, une fonction d'échantillonnage lit leur page,
   **plafonnée à 8 pages par passage** (à ~3,5 articles par heure sans résumé, le plafond
   ne devrait pas jouer ; s'il joue, le journal le dit).
3. Chaque lecture ne produit que des **comptes agrégés par source** dans le journal du
   robot : présente / absente / page refusée, et la **longueur** (pas le texte).
   Une ligne de synthèse en fin de passage.
4. Mêmes en-têtes et même `User-Agent` que les miniatures (aucun pseudo), mêmes délais
   (`timeout=8`), mêmes 8 fils : aucun nouveau comportement réseau.

## Réussite

- Après 24 passages : pour chaque source, la part d'articles dont la page porte une
  description non vide, et sa longueur médiane. On en tire une décision chiffrée :
  vaut-il la peine de la conserver (et sous quelle limite), oui ou non.
- Le journal ne contient **aucun extrait de texte**, seulement des nombres.
- Test : la fonction de lecture rend la bonne longueur sur des pages fabriquées (balise
  présente, vide, absente, doublée) ; éprouvée en cassant la sélection de la balise.

## Limites

- **Toujours** : plafond de pages par passage ; mesure sans stockage.
- **Demander d'abord** : toute conservation de texte dans `feed.json` (poids, droit de
  citation) — c'est une autre décision, après la mesure.
- **Jamais** : conserver ou publier le texte complet d'une page ; résumer par IA.

## Non vérifié

- Combien de sites de presse refusent le robot : le proxy du conteneur bloque la plupart
  d'entre eux, la sonde ne dit donc rien. Seul le journal du robot, en CI, le dira.
- Le coût en durée d'un passage : on le lira dans les durées par source (`duree_ms`) et le
  temps total, avant et après.

## Risque

Ajouter ~84 requêtes par jour vers des sites de presse déjà interrogés pour leur flux ;
une hausse des refus serait un effet à surveiller dans `sources_health`.
