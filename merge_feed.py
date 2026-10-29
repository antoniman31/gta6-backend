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
        fusionnee = {
            cle: max(_entier(a_nous.get(cle)), _entier(au_loin.get(cle)))
            for cle in ("articles", "officiels", "sommet")
        }
        # Les aperçus, eux, se RÉUNISSENT au lieu de se comparer : ce ne sont
        # pas des cumuls mais des articles, et apercus_recap déduplique sur le
        # lien puis reclasse le tout avant de garder les cinq premiers. Prendre
        # « le plus long des deux » ferait perdre au récapitulatif du matin
        # exactement les articles que l'autre côté avait vus.
        fusionnee["apercus"] = feed_store.apercus_recap(
            feed_store.apercus_assainis(a_nous.get("apercus"))
            + feed_store.apercus_assainis(au_loin.get("apercus")))
        merged["attente_recap"] = fusionnee

    _fusionne_cumulatifs(merged, ours, remote)
    return merged, recovered, removed


# Les clés CUMULATIVES ajoutées après l'écriture de ce script. Le même
# raisonnement que pour `attente_recap` s'y applique mot pour mot : notre
# côté les a calculées à partir d'un feed.json que le distant a dépassé
# entre-temps, donc `merged = dict(ours)` perd silencieusement sa
# contribution. Elles n'ont pas la même forme, donc pas la même règle.
def _fusionne_cumulatifs(merged, ours, remote):
    # Les jalons annoncés sont un ENSEMBLE : l'union, jamais l'un des deux.
    # Garder seulement notre côté pourrait « oublier » un jalon que le distant
    # a déjà annoncé, et le faire repartir une seconde fois.
    a_nous = ours.get("jalons_envoyes") or []
    au_loin = remote.get("jalons_envoyes") or []
    if isinstance(a_nous, list) and isinstance(au_loin, list) and (a_nous or au_loin):
        merged["jalons_envoyes"] = list(dict.fromkeys(
            [x for x in list(au_loin) + list(a_nous) if isinstance(x, str)]))

    # Le journal des incidents est une LISTE. Union par (source, début) :
    # deux côtés qui ont vu la même panne en écrivent la même entrée, et
    # celui qui en a vu une de plus ne doit pas la perdre.
    a_nous = ours.get("sources_incidents") or []
    au_loin = remote.get("sources_incidents") or []
    if isinstance(a_nous, list) and isinstance(au_loin, list):
        vus = {}
        for inc in list(au_loin) + list(a_nous):   # le nôtre écrase, il est plus frais
            if isinstance(inc, dict):
                vus[(inc.get("source"), inc.get("debut"))] = inc
        merged["sources_incidents"] = sorted(
            vus.values(), key=lambda i: str(i.get("debut") or ""), reverse=True)

    # Le compteur est un dict {mois: {source: {nom, n, heures}}}. Maximum
    # entrée par entrée, pour la raison exacte d'attente_recap : les deux
    # côtés partent du même arriéré, les additionner compterait double.
    a_nous = ours.get("sources_incidents_compte") or {}
    au_loin = remote.get("sources_incidents_compte") or {}
    if isinstance(a_nous, dict) and isinstance(au_loin, dict):
        compte = {}
        for source in (au_loin, a_nous):
            for mois, par_source in (source or {}).items():
                if not isinstance(par_source, dict):
                    continue
                cible = compte.setdefault(mois, {})
                for sid, e in par_source.items():
                    if not isinstance(e, dict):
                        continue
                    avant = cible.get(sid) or {}
                    cible[sid] = {
                        "nom": e.get("nom") or avant.get("nom"),
                        "n": max(_entier(e.get("n")), _entier(avant.get("n"))),
                        "heures": max(_reel(e.get("heures")), _reel(avant.get("heures"))),
                    }
        merged["sources_incidents_compte"] = compte

    # L'historique des volumes est un dict {source: "100,100,98,…"}, une
    # fenêtre glissante. On garde la chaîne la PLUS LONGUE : c'est celle qui
    # a vu le plus de passages, et la fenêtre se recoupera d'elle-même.
    a_nous = ours.get("sources_entries_history") or {}
    au_loin = remote.get("sources_entries_history") or {}
    if isinstance(a_nous, dict) and isinstance(au_loin, dict):
        hist = dict(au_loin)
        for sid, v in a_nous.items():
            avant = hist.get(sid)
            if not isinstance(avant, str) or not isinstance(v, str) or \
               len(v.split(",")) >= len(avant.split(",")):
                hist[sid] = v
        merged["sources_entries_history"] = hist

    # Les chronomètres de silence ne sont PAS cumulatifs au même sens : ce
    # sont des états vivants, reconstruits à chaque passage à partir des 63
    # sources réellement contactées. Notre côté est donc le plus frais, et
    # c'est lui qui fait foi — à deux détails près, qui sont justement ceux
    # qu'une fusion naïve casserait :
    #
    #   - `depuis` prend le PLUS ANCIEN des deux. Reprendre le nôtre
    #     remettrait le chronomètre à zéro, et une panne de six heures
    #     repartirait à zéro heure ;
    #   - `alertee` est un OU. Ce drapeau existe pour ne pas réalerter à
    #     chaque passage ; le perdre renverrait l'alerte Discord.
    #
    # Une entrée que seul le distant possède est reprise : il a vu une panne
    # que nous avons manquée. Ressusciter un chronomètre déjà clos coûte un
    # passage ; en perdre un retarde une alerte de plusieurs heures.
    a_nous = ours.get("sources_silence") or {}
    au_loin = remote.get("sources_silence") or {}
    if isinstance(a_nous, dict) and isinstance(au_loin, dict):
        silence = {sid: e for sid, e in au_loin.items() if isinstance(e, dict)}
        for sid, e in a_nous.items():
            if not isinstance(e, dict):
                continue
            avant = silence.get(sid)
            fusion = dict(e)
            if isinstance(avant, dict):
                depuis = [d for d in (e.get("depuis"), avant.get("depuis"))
                          if isinstance(d, str) and d]
                if depuis:
                    fusion["depuis"] = min(depuis)
                fusion["alertee"] = bool(e.get("alertee")) or bool(avant.get("alertee"))
            silence[sid] = fusion
        merged["sources_silence"] = silence


def _entier(v):
    """Un entier positif, ou zéro pour tout le reste."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _reel(v):
    """Un nombre positif, ou zéro. Les durées d'incident sont des flottants."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else 0.0


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

    # L'archive est réécrite ici aussi, et ce n'est pas du zèle : le
    # workflow fait `git reset --hard origin/main` avant d'appeler cette
    # fusion, ce qui efface les fichiers d'archive que le passage venait
    # d'écrire. Sans cette ligne, tout passage ayant subi un conflit de
    # push n'archiverait rien. L'archivage étant cumulatif et idempotent,
    # le passage suivant réparerait — mais seulement pour les articles
    # encore en fenêtre, et un conflit tombe justement les jours chargés.
    # Les MÊMES exclusions qu'à l'écriture normale. Sans le `exclure=`,
    # cette archive de secours réintroduisait les pages Rockstar hors langue
    # et les cotations crypto que main() venait d'écarter : un conflit de
    # push ramenait dans l'archive ce que le passage avait justement retiré.
    # Auto-réparé au passage suivant, mais seulement pour les mois que la
    # fenêtre touche encore.
    import fetch_feeds
    feed_store.archiver(
        merged.get("items") or [],
        exclure=lambda lien: (fetch_feeds.page_rockstar_hors_langue(lien)
                              or fetch_feeds.page_de_cotation_crypto(lien)))
    # Et le même allègement : sinon l'archive réécrite ici repart avec tous
    # ses source_link, c'est-à-dire la moitié de son poids.
    feed_store.elague_source_link_archives()
    feed_store.dedoublonne_archives()
    feed_store.ecrire_index_archives()

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
