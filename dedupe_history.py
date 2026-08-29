"""
Fusionne les doublons restés dans l'historique — passage unique.

Pourquoi ce script existe
-------------------------
Jusqu'au 29/08/2026, la déduplication ne comparait un nouvel article qu'aux
200 premiers de `all_items`, alors que les articles du passage en cours
étaient ajoutés à la FIN. Passé 200 articles d'historique, deux rédactions
publiant le même sujet dans le même passage n'étaient donc jamais
rapprochées. Le correctif empêche les NOUVEAUX doublons ; celui-ci nettoie
ceux qui se sont accumulés avant.

Ce n'est volontairement pas une passe automatique à chaque exécution :
- une fois l'historique propre, elle ne servirait plus qu'à consommer du
  temps à chaque passage ;
- surtout, supprimer un article le fait disparaître des appareils qui
  gardaient son état « lu ». Ça se décide, ça ne se subit pas.

Même règle que la déduplication en direct
-----------------------------------------
Même normalisation de titre, même seuil, même fenêtre, et surtout même
arbitrage : c'est l'article le PLUS ANCIEN qui est conservé, le plus récent
devient une source supplémentaire. Un script qui trancherait autrement
produirait un historique incohérent avec ce que fait le robot au quotidien.

Usage :
    python dedupe_history.py --essai     # montre ce qui serait fusionné
    python dedupe_history.py --appliquer # écrit docs/feed.json
"""

import sys

import feed_store
import fetch_feeds


def fusionner_doublons(items):
    """Renvoie (items nettoyés, liste des fusions effectuées).

    Parcourt du plus ancien au plus récent, comme le robot voit arriver les
    articles au fil du temps.
    """
    chronologique = sorted(items, key=lambda i: feed_store.parse_date_key(i.get("date")))
    gardes = []
    fusions = []
    for item in chronologique:
        # Même fenêtre que la déduplication en direct : les N derniers
        # articles conservés, c'est-à-dire les plus récents à cet instant.
        fenetre = gardes[-fetch_feeds.TITLE_SIMILARITY_WINDOW:]
        jumeau = None
        for autre in reversed(fenetre):
            if fetch_feeds.title_similarity(item.get("title", ""),
                                            autre.get("title", "")) >= fetch_feeds.SIMILARITY_THRESHOLD:
                jumeau = autre
                break
        if jumeau is None:
            gardes.append(item)
        else:
            fetch_feeds.record_coverage(jumeau, item)
            fusions.append((jumeau, item))
    return feed_store.sort_items(gardes), fusions


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--essai"
    if mode not in ("--essai", "--appliquer"):
        print(__doc__)
        return 2

    data = feed_store.load_feed()
    items = data.get("items", []) or []
    print(f"Historique : {len(items)} articles")

    nettoyes, fusions = fusionner_doublons(items)
    print(f"Doublons trouvés : {len(fusions)}")
    print()
    for garde, retire in fusions:
        n = 1 + len(garde.get("extraSources") or [])
        print(f"  gardé   [{garde.get('source')}] {garde.get('title','')[:66]}")
        print(f"  fusionné[{retire.get('source')}] {retire.get('title','')[:66]}")
        print(f"           -> {n} sources sur ce sujet")
        print()

    if not fusions:
        print("Rien à faire.")
        return 0

    if mode == "--essai":
        print(f"ESSAI : rien n'a été écrit. {len(items)} -> {len(nettoyes)} articles "
              "si appliqué.")
        return 0

    data["items"] = nettoyes
    data["total_articles"] = len(nettoyes)
    feed_store.write_feed_pair(data)
    print(f"Écrit : {len(items)} -> {len(nettoyes)} articles "
          f"(docs/feed.json et docs/feed-recent.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
