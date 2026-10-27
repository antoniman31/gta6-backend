# Plan : agenda et silence, avant le 19 novembre

Décidé avec Antoni le 27/10/2026. Les specs sont dans `tasks/spec-agenda.md`
et `tasks/spec-silence.md`. Troisième idée retenue, **sans code** : « Notes de
la presse », reportée à la mesure du 22/11 (point 5 de la tâche planifiée).

Écartées avec leurs chiffres : partager le décompte ; « À la une » en tête de
fil (0 à 4 actus majeures par jour) ; « Masquer ce média » (déjà faisable par
les mots à exclure) ; un push « plus qu'N dodos » (s'arrête dans 23 jours).

## Découpage en trois PR

| PR | Tâches |
|---|---|
| 1 · agenda | A1, A2, A3 |
| 2 · silence | B1, B2 |
| 3 · essai réel | B3 (du code seulement si l'essai révèle un défaut) |

## Tâches

### A1 — les données et la fonction pure (S)
`docs/agenda.json`, `rendezVousAVenir(agenda, maintenant)`, sa validation.
- [ ] le contrôle refuse : entrée sans lien, sans statut ou sans date de
      vérification ; `annonce` sans domaine officiel ; plus de 8 entrées ;
      entrées mal triées ; doublon
- [ ] `J-N` juste à minuit à Paris (12/11 = 23:00 UTC la veille) et autour des
      deux changements d'heure
- [ ] une entrée vieille de plus d'un jour disparaît ; plus de 14 jours sans
      vérification : « à revérifier » ; un statut inconnu vaut `presse`
- Fichiers : `docs/agenda.json`, `docs/index.html`, `test_pipeline.py`,
  `test_navigateur.py`

### A2 — l'affichage (S)
- [ ] dans le panneau du compteur, sous les dodos
- [ ] fichier absent, 404 ou JSON corrompu : le compteur s'ouvre, sans erreur
- [ ] aucune ligne ne sort du panneau à 320 px (mesurée ligne par ligne)
- [ ] lisible dans les deux thèmes (contraste mesuré)
- Fichiers : `docs/index.html`, `docs/sw.js` (bump), `test_navigateur.py`

### A3 — la documentation (XS)
- [ ] `CLAUDE.md` : aucune entrée d'agenda sans validation d'Antoni
- [ ] `README.md`, `RESTE-A-FAIRE.md`

### B1 — les options de notification (S)
- [ ] `optionsNotification` pure, dans `docs/notif-options.js`, chargée par
      `sw.js` (`importScripts`) et par les tests
- [ ] jamais `vibrate` avec `silent` ; échéance à la seconde

### B2 — le réglage (S)
- [ ] stockage lisible par le service worker, avec l'exemption dans `activate`
- [ ] bouton « Silence 2 h » dans les réglages push ; pastille 😴 dans l'en-tête
- [ ] le réglage survit à un déploiement (le test échoue sans l'exemption)

### B3 — l'essai réel (XS)
- [ ] avec l'accord d'Antoni : `test-push.yml` pendant le silence

## Points de contrôle
- Après A1 + A2 : quatre suites vertes, captures à 320 et 390 px dans les deux
  thèmes, Antoni voit le résultat sur son téléphone.
- Après B1 + B2 : quatre suites vertes.
- Après B3 : le constat d'Antoni sur son Android.

## Risques
| Risque | Parade |
|---|---|
| `activate` efface le réglage au déploiement | exemption + test qui échoue sans elle |
| `silent` / `renotify` autrement sur son Chrome | `renotify` coupé ; B3 tranche |
| Silence oublié la nuit de la sortie | une durée, 2 h ; pastille avec l'heure de fin |
| Agenda périmé | date de vérification affichée ; « à revérifier » après 14 jours |
