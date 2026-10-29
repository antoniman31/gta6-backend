"""Combien de sites se cachent derrière les « sources » d'une actu majeure ?

    python3 mesure_actu_majeure.py [docs/feed.json]

Lecture seule : ne modifie rien, n'envoie rien, ne touche à aucun réseau. Le
seuil de l'actu majeure (HOT_SOURCE_THRESHOLD) compte des FLUX ; ce script
encadre le nombre de SITES distincts derrière les liens :
  - borne basse : les sites des liens qu'on sait lire (feed_store.mesure_actu_majeure) ;
  - borne haute : la borne basse plus un site possible par lien resté sur
    l'agrégateur, dont on ne connaît pas le site.
Le vrai nombre de sites est entre les deux. Rien n'est décidé ici : c'est la
mesure sur laquelle Antoni tranchera le seuil.
"""

import collections
import json
import sys

import feed_store


def mesure(items):
    seuil = feed_store.HOT_SOURCE_THRESHOLD
    chauds = [i for i in items
              if isinstance(i, dict) and 1 + len(i.get("extraSources") or []) >= seuil]
    lignes = [feed_store.mesure_actu_majeure(i) for i in chauds]
    return {
        "seuil": seuil,
        "articles": len(items),
        "chauds": len(chauds),
        "bas": collections.Counter(l["sites"] for l in lignes),
        "borne_basse_ok": sum(1 for l in lignes if l["sites"] >= seuil),
        "borne_haute_ok": sum(1 for l in lignes if l["sites"] + l["non_decodes"] >= seuil),
        "avec_non_decode": sum(1 for l in lignes if l["non_decodes"]),
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    chemin = argv[0] if argv else "docs/feed.json"
    with open(chemin, encoding="utf-8") as f:
        data = json.load(f)
    items = data["items"] if isinstance(data, dict) else data
    m = mesure(items)
    n = m["chauds"]
    print(f"{n} article(s) à {m['seuil']} flux ou plus, sur {m['articles']}.")
    if not n:
        return 0
    print("Sites distincts (borne basse) :",
          ", ".join(f"{k} : {v}" for k, v in sorted(m["bas"].items())))
    print(f"Au moins {m['seuil']} sites lisibles (borne basse) : {m['borne_basse_ok']} sur {n}")
    print(f"Au moins {m['seuil']} sites possibles (borne haute) : {m['borne_haute_ok']} sur {n}")
    print(f"Avec au moins un lien non décodé : {m['avec_non_decode']} sur {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
