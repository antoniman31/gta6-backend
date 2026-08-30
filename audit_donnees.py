#!/usr/bin/env python3
"""Audit de l'historique publié — cherche les incohérences dans les DONNÉES.

Pourquoi un fichier permanent plutôt qu'un script jeté après usage : chacune
des vérifications ci-dessous a été écrite à l'arrache un jour où quelque
chose clochait, utilisée une fois, puis perdue. Le comptage de couverture
gonflé, les doublons de titre, l'attribution croisée entre deux articles
Mashable : à chaque fois le même réflexe, le même code réécrit, et rien qui
reste pour la fois suivante. Ce fichier arrête ce gaspillage.

Ce n'est PAS un test. `test_pipeline.py` vérifie des invariants : s'il
échoue, le code est faux. Ici on signale des symptômes, qui peuvent être
légitimes ou connus — un même article republié sous deux URL par un site
n'est pas un bug du robot. D'où le comportement par défaut : on rapporte,
on ne fait pas échouer. `--strict` inverse ce choix pour une exécution
manuelle où l'on veut un code de retour.

    python audit_donnees.py
    python audit_donnees.py --strict
    python audit_donnees.py --json     # pour comparer deux exécutions
"""

import collections
import json
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feed_store
import fetch_feeds

# Marge avant de considérer une date comme aberrante. Les flux annoncent
# parfois une publication quelques heures en avance (fuseau mal déclaré,
# article programmé) ; ce n'est pas une anomalie. Au-delà d'un jour, si.
TOLERANCE_FUTUR = timedelta(days=1)


def _domaine(url):
    try:
        return urlparse(url or "").netloc.lower()
    except ValueError:
        return ""


def audite(data):
    """Renvoie une liste d'anomalies : (gravité, code, message, exemples)."""
    items = data.get("items") or []
    anomalies = []

    def signale(gravite, code, message, exemples=()):
        anomalies.append({"gravite": gravite, "code": code,
                          "message": message, "exemples": list(exemples)[:5]})

    # ---- Structure : ce qui rendrait le fichier inexploitable -------------
    sans_lien = [i for i in items if not i.get("link")]
    if sans_lien:
        signale("grave", "sans-lien", f"{len(sans_lien)} article(s) sans lien",
                (i.get("title", "?") for i in sans_lien))

    sans_titre = [i for i in items if not i.get("title")]
    if sans_titre:
        signale("grave", "sans-titre", f"{len(sans_titre)} article(s) sans titre",
                (i.get("link", "?") for i in sans_titre))

    liens = [i["link"] for i in items if i.get("link")]
    doubles = [l for l, n in collections.Counter(liens).items() if n > 1]
    if doubles:
        signale("grave", "lien-double",
                f"{len(doubles)} lien(s) présent(s) plusieurs fois", doubles)

    annonce = data.get("total_articles")
    if annonce is not None and annonce != len(items):
        signale("grave", "compte-faux",
                f"total_articles annonce {annonce}, il y a {len(items)} articles")

    # ---- Attribution croisée ---------------------------------------------
    # Une « source supplémentaire » dont le lien est le lien principal d'un
    # AUTRE article : ce n'est pas la même actu vue deux fois, c'est un
    # rapprochement erroné. Le lecteur qui clique sur « autre source »
    # atterrit sur un sujet différent. Trouvé le 30/08/2026 entre deux
    # récapitulatifs quotidiens de Mashable.
    par_lien = {i["link"]: i for i in items if i.get("link")}
    croisees = []
    for i in items:
        for s in (i.get("extraSources") or []):
            autre = par_lien.get(s.get("link"))
            if autre is not None and autre is not i:
                croisees.append(f"« {i.get('title','?')[:45]} » créditée de "
                                f"« {autre.get('title','?')[:45]} »")
    if croisees:
        signale("attention", "attribution-croisee",
                f"{len(croisees)} source(s) supplémentaire(s) pointant vers "
                f"un autre article de l'historique", croisees)

    # ---- Doublons de titre restants --------------------------------------
    par_titre = collections.defaultdict(list)
    for i in items:
        if i.get("title"):
            par_titre[fetch_feeds.normalize_title(i["title"])].append(i)
    restants = {t: v for t, v in par_titre.items() if len(v) > 1}
    if restants:
        signale("attention", "titre-double",
                f"{len(restants)} titre(s) normalisé(s) apparaissant plusieurs fois",
                (f"{len(v)}× « {v[0]['title'][:55]} »" for v in restants.values()))

    # ---- Cohérence avec la liste des sources ------------------------------
    connus = {f["name"] for f in fetch_feeds.FEEDS}
    orphelins = collections.Counter(i.get("source", "") for i in items
                                    if i.get("source") not in connus)
    if orphelins:
        signale("info", "source-orpheline",
                f"{sum(orphelins.values())} article(s) rattaché(s) à "
                f"{len(orphelins)} source(s) absente(s) de FEEDS",
                (f"{n} × {s}" for s, n in orphelins.most_common()))

    # ---- Dates ------------------------------------------------------------
    limite = datetime.now(timezone.utc) + TOLERANCE_FUTUR
    futurs = [i for i in items
              if i.get("date") and feed_store.parse_date_key(i["date"]) > limite]
    if futurs:
        signale("attention", "date-future",
                f"{len(futurs)} article(s) daté(s) dans le futur — ils "
                f"resteront en tête du fil",
                (f"{i['date'][:16]} — {i.get('title','?')[:45]}" for i in futurs))

    dates = [feed_store.parse_date_key(i.get("date")) for i in items]
    if dates != sorted(dates, reverse=True):
        signale("grave", "tri-casse",
                "l'historique publié n'est pas trié du plus récent au plus ancien")

    # ---- Statuts d'onglet -------------------------------------------------
    # Un article « officiel » dont le lien ne pointe vers aucun domaine
    # officiel n'a rien à faire dans l'onglet Rockstar.
    #
    # Le feed d'origine DOIT être passé à statut_officiel : certaines
    # sources déclarent leurs propres domaines officiels (la chaîne YouTube
    # de Rockstar pointe vers youtube.com, pas rockstargames.com). Sans lui,
    # l'audit signalait cette chaîne comme une anomalie — un outil de
    # diagnostic qui crie au loup est pire que pas d'outil du tout.
    par_nom = {f["name"]: f for f in fetch_feeds.FEEDS}
    officiels_hors = [
        i for i in items if i.get("official")
        and not fetch_feeds.statut_officiel(i.get("link", ""),
                                            par_nom.get(i.get("source")))]
    if officiels_hors:
        signale("attention", "officiel-hors-domaine",
                f"{len(officiels_hors)} article(s) marqué(s) officiels hors "
                f"domaine officiel",
                (f"{_domaine(i.get('link'))} — {i.get('title','?')[:40]}"
                 for i in officiels_hors))

    # ---- Cohérence des deux fichiers publiés ------------------------------
    try:
        allege = feed_store.load_feed(feed_store.recent_path_for(feed_store.FEED_PATH))
    except Exception:
        allege = None
    if allege:
        liens_recents = {i.get("link") for i in (allege.get("items") or [])}
        manquants = liens_recents - set(liens)
        if manquants:
            signale("grave", "allege-desynchro",
                    f"{len(manquants)} article(s) du fichier allégé absent(s) "
                    f"de l'historique complet", manquants)

    return anomalies


def rapporte(anomalies, sortie_json=False):
    if sortie_json:
        print(json.dumps(anomalies, ensure_ascii=False, indent=2))
        return

    if not anomalies:
        print("Audit des données : aucune anomalie.")
        return

    ordre = {"grave": 0, "attention": 1, "info": 2}
    symbole = {"grave": "✗", "attention": "⚠", "info": "·"}
    print("Audit des données\n")
    for a in sorted(anomalies, key=lambda x: ordre[x["gravite"]]):
        print(f"  {symbole[a['gravite']]} {a['message']}")
        for ex in a["exemples"]:
            print(f"       {ex}")
        print()

    compte = collections.Counter(a["gravite"] for a in anomalies)
    print("  " + " · ".join(f"{n} {g}" for g, n in compte.most_common()))


def main(argv):
    strict = "--strict" in argv
    data = feed_store.load_feed()
    anomalies = audite(data)
    rapporte(anomalies, sortie_json="--json" in argv)

    # Par défaut on ne fait jamais échouer : ces anomalies sont des
    # symptômes, pas des invariants, et bloquer toutes les PR sur des cas
    # connus rendrait l'outil inutilisable — on finirait par l'ignorer.
    if strict and any(a["gravite"] == "grave" for a in anomalies):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
