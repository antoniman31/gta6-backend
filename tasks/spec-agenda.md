# Spec : l'agenda « Prochains rendez-vous »

Approuvée par Antoni le 27/10/2026. Trois entrées validées une à une.

## Objectif

En ouvrant le compteur, Antoni lit en cinq lignes au plus les prochaines
échéances datées du jeu, chacune avec sa fiabilité.

## Données : `docs/agenda.json`, tenu à la main

Une entrée : `date` (jour entier, `AAAA-MM-JJ`), `libelle`, `statut`
(`annonce` ou `presse`), `source` (`media`, `titre`, `lien`), `verifie_le`,
et `nb_titres` (combien de titres du fil citaient la date). Aucune heure :
aucune des trois dates n'en a une connue.

## Affichage

Dans le panneau du compteur, sous les dodos : « J-16 · jeu. 12 nov. ·
Précharge (≈ 200 Go) · selon la presse ». Le « J-N » se calcule en **jours
civils à Paris**, jamais en divisant par 24 h (le piège des dodos). Une
entrée passée reste grisée un jour, puis disparaît. Non vérifiée depuis
plus de 14 jours : « à revérifier ».

## Réussite

- Toutes les entrées sont validées par Antoni.
- Aucune entrée sans lien, sans statut ni date de vérification (contrôle).
- `J-N` juste à minuit à Paris et autour des deux changements d'heure.
- Un fichier absent, en 404 ou corrompu n'empêche pas le compteur de s'ouvrir.

## Limites

- **Toujours** : un lien et un statut sur chaque entrée.
- **Demander d'abord** : ajouter ou modifier une entrée, changer le format.
- **Jamais** : inventer une date ou une heure ; extraire une date par
  expression régulière ; écrire `annonce` sans source sur un domaine officiel
  (le contrôle le refuse mécaniquement, via `fetch_feeds.OFFICIAL_DOMAINS`).

## Entrées validées le 27/10/2026

| Date | Libellé | Statut et source |
|---|---|---|
| 29/10 | Prochaine actualité officielle | presse · Game Rant · 1 titre |
| 12/11 | Début de la précharge (jusqu'à 200 Go) | presse · GameGPU · 6 titres |
| 18/11 | Levée de l'embargo des tests | presse · Insider Gaming · 4 titres |

Aucune des trois n'a de source officielle dans le fil : la précharge repose
sur un site de benchmarks matériel, pas sur Rockstar.
