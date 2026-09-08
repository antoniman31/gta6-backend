"""
Fusionne deux versions de docs/feed.json au niveau des DONNÉES.

Pourquoi ce script existe
-------------------------
docs/feed.json est un fichier entièrement régénéré à chaque exécution. Quand
deux exécutions se chevauchent (typiquement un run planifié et un
déclenchement manuel), la seconde se fait rejeter au push, et la reprise
classique par `git pull --rebase` échoue systématiquement : les deux côtés
ont réécrit les mêmes lignes, Git ne peut pas trancher, le rebase s'arrête
en conflit et le run entier meurt en emportant tout son travail.

C'est exactement ce qui s'est produit le 28/08 à 07:23 :

    CONFLICT (content): Merge conflict in docs/feed.json
    error: could not apply b9c681a... Mise à jour automatique des flux

Or le conflit n'est qu'apparent : deux listes d'articles se fusionnent très
bien, il suffit de le faire au niveau du JSON et non des lignes de texte.
C'est ce que fait ce script — aucun article n'est perdu, quel que soit le
côté qui l'a trouvé.

Usage :
    python merge_feed.py <distant.json> <local.json> <sortie.json>
"""

import sys

import feed_store


def merge_feeds(remote, ours):
    """Fusionne deux structures feed.json complètes.

    Règles :
      - les articles sont l'UNION des deux côtés, dédupliqués par lien ;
      - en cas de lien présent des deux côtés, on garde notre version (elle
        peut avoir une miniature récupérée que l'autre n'a pas) ;
      - les métadonnées (generated_at, sources, new_this_run) viennent de
        NOTRE côté : c'est notre exécution qui est en train de publier, et
        c'est son horodatage qui doit faire foi pour l'indicateur de
        fraîcheur du tracker ;
      - total_articles est recalculé sur le résultat fusionné ;
      - attente_recap prend le MAXIMUM des deux côtés, jamais la somme
        (voir plus bas).
    """
    merged = dict(ours)

    ours_items = ours.get("items", []) or []
    remote_items = remote.get("items", []) or []

    items = list(ours_items)
    known_links = {item.get("link") for item in items if item.get("link")}

    recovered = 0
    for item in remote_items:
        link = item.get("link")
        if not link or link in known_links:
            continue
        items.append(item)
        known_links.add(link)
        recovered += 1

    feed_store.normalize_stored_dates(items)
    items = feed_store.sort_items(items)
    items, removed = feed_store.cap_items(items)

    merged["items"] = items
    merged["total_articles"] = len(items)

    # Les comptes mis de côté pendant la pause nocturne sont la seule
    # métadonnée qu'on ne peut pas simplement prendre de notre côté : c'est
    # un CUMUL, et le nôtre a été calculé à partir d'un feed.json que le
    # distant a entre-temps dépassé. On garderait sinon un total inférieur
    # au sien, en perdant sa contribution.
    #
    # Maximum et non somme : les deux côtés partent du même arriéré, les
    # additionner le compterait deux fois. Le maximum peut sous-estimer d'un
    # passage — dans un message qui annonce un nombre, mieux vaut annoncer un
    # peu moins que d'inventer. Le cas reste théorique : le workflow sérialise
    # ses exécutions (concurrency: update-feeds) et le checkout force `ref:
    # main`, donc deux passages ne lisent presque jamais le même état.
    a_nous = ours.get("attente_recap") or {}
    au_loin = remote.get("attente_recap") or {}
    if isinstance(a_nous, dict) and isinstance(au_loin, dict) and (a_nous or au_loin):
        merged["attente_recap"] = {
            cle: max(_entier(a_nous.get(cle)), _entier(au_loin.get(cle)))
            for cle in ("articles", "officiels", "sommet")
        }

    return merged, recovered, removed


def _entier(v):
    """Un entier positif, ou zéro pour tout le reste."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 2

    remote_path, ours_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

    remote = feed_store.load_feed(remote_path)
    ours = feed_store.load_feed(ours_path)

    if not ours.get("items"):
        # Notre côté est vide ou illisible : republier ça écraserait
        # l'historique distant. On refuse plutôt que de détruire des données.
        print("[merge] ABANDON : le fichier local est vide ou illisible, "
              "fusion refusée pour ne pas écraser l'historique distant.")
        return 1

    merged, recovered, removed = merge_feeds(remote, ours)

    # Les deux fichiers sont réécrits : republier le complet sans
    # régénérer l'allégé laisserait l'app sur un état périmé.
    # Même contrôle qu'à l'écriture normale : une fusion ratée après
    # conflit de push est précisément le moment où un fichier abîmé
    # pourrait être publié.
    feed_store.valide_avant_ecriture(merged, remote)

    feed_store.write_feed_pair(merged, out_path)

    print(f"[merge] local {len(ours.get('items', []))} article(s) + distant "
          f"{len(remote.get('items', []))} article(s) "
          f"-> {merged['total_articles']} après fusion "
          f"({recovered} récupéré(s) du distant"
          + (f", {removed} retiré(s) par le plafond" if removed else "")
          + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
