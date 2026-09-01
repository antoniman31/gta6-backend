"""
Tests du pipeline de données — aucune dépendance, aucun réseau.

    python test_pipeline.py

Couvre les règles dont une régression corromprait l'historique en silence :
l'interprétation des dates, l'ordre de tri, le plafonnement, et surtout la
fusion après conflit de push (c'est elle qui décide si des articles sont
perdus quand deux exécutions se chevauchent).

Volontairement écrit sans pytest : le workflow n'installe que les
dépendances de production, et ces tests doivent pouvoir tourner partout.
"""

import collections
import json
import os
import sys
import tempfile
import time
import types
from datetime import datetime, timezone

import feed_store
import merge_feed

FAILURES = []
CHECKS = 0


def check(condition, label):
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  ÉCHEC {label}")
        FAILURES.append(label)


def article(link, date, title=None, **extra):
    item = {"link": link, "date": date, "title": title or f"Article {link}", "source": "Test"}
    item.update(extra)
    return item


# ---------------------------------------------------------------------------
def test_parse_date_key():
    print("\n[dates] les trois formats présents dans l'historique")
    iso = feed_store.parse_date_key("2026-08-28T12:45:37+00:00")
    rfc = feed_store.parse_date_key("Wed, 29 Jul 2026 20:05:05 GMT")
    check(iso == datetime(2026, 8, 28, 12, 45, 37, tzinfo=timezone.utc), "ISO 8601 lu correctement")
    check(rfc == datetime(2026, 7, 29, 20, 5, 5, tzinfo=timezone.utc), "RFC 822 lu correctement")
    check(feed_store.parse_date_key("2026-08-28T12:45:37Z").tzinfo is not None, "suffixe Z accepté")
    check(feed_store.parse_date_key("") == feed_store.DATE_FLOOR, "date vide -> plancher")
    check(feed_store.parse_date_key("pas une date") == feed_store.DATE_FLOOR, "date illisible -> plancher")
    check(feed_store.parse_date_key("2026-08-28T12:45:37").tzinfo is not None,
          "date naïve rendue aware (sinon le tri lève un TypeError)")

    # Le bug d'origine : comparées comme du TEXTE, les dates RFC 822 passent
    # avant les dates ISO ("W" > "2" en ASCII).
    check("Wed, 29 Jul 2026 20:05:05 GMT" > "2026-08-28T12:45:37+00:00",
          "reproduction du bug : en texte brut, juillet passe avant août")
    check(rfc < iso, "corrigé : comparées en datetime, les dates s'ordonnent bien")


def test_sort_and_cap():
    print("\n[tri] ordre et plafonnement")
    items = [
        article("a", "2026-01-01T00:00:00+00:00"),
        article("b", "Wed, 29 Jul 2026 20:05:05 GMT"),
        article("c", "2026-08-28T12:00:00+00:00"),
        article("d", ""),
    ]
    ordered = feed_store.sort_items(items)
    check([i["link"] for i in ordered] == ["c", "b", "a", "d"],
          "tri décroissant tous formats confondus, date manquante en dernier")

    capped, dropped = feed_store.cap_items(ordered, max_size=2)
    check(dropped == 2 and [i["link"] for i in capped] == ["c", "b"],
          "le plafond retire les plus anciens, jamais les plus récents")
    unchanged, none_dropped = feed_store.cap_items(ordered, max_size=99)
    check(none_dropped == 0 and len(unchanged) == 4, "sous le plafond, rien n'est retiré")


def test_normalize_stored_dates():
    print("\n[repasse rétroactive] conversion des dates héritées")
    items = [
        article("a", "Wed, 29 Jul 2026 20:05:05 GMT"),
        article("b", "2026-08-28T12:45:37+00:00"),
        article("c", "pas une date"),
    ]
    fixed = feed_store.normalize_stored_dates(items)
    check(fixed == 1, "seule la date RFC 822 est convertie")
    check(items[0]["date"] == "2026-07-29T20:05:05+00:00", "conversion en ISO correcte")
    check(items[1]["date"] == "2026-08-28T12:45:37+00:00", "une date déjà ISO n'est pas touchée")
    check(items[2]["date"] == "pas une date", "une date illisible est laissée telle quelle, pas inventée")
    check(feed_store.normalize_stored_dates(items) == 0, "idempotente : le second passage ne corrige rien")


def test_merge_no_loss():
    print("\n[fusion] aucune perte d'article après conflit de push")
    commun = [article(f"commun-{i}", f"2026-08-2{i % 9}T10:00:00+00:00") for i in range(10)]
    distant = {"generated_at": "2026-08-28T13:00:00+00:00", "new_this_run": 3, "sources": ["x"],
               "items": commun + [article("seul-distant-1", "2026-08-28T11:00:00+00:00"),
                                  article("seul-distant-2", "2026-08-28T11:30:00+00:00")]}
    local = {"generated_at": "2026-08-28T13:05:00+00:00", "new_this_run": 2, "sources": ["x"],
             "items": commun + [article("seul-local-1", "2026-08-28T12:00:00+00:00")]}

    merged, recovered, _ = merge_feed.merge_feeds(distant, local)
    links = {i["link"] for i in merged["items"]}
    attendu = {i["link"] for i in distant["items"]} | {i["link"] for i in local["items"]}

    check(links == attendu, "le résultat est exactement l'union des deux côtés")
    check(recovered == 2, "les articles trouvés uniquement par l'autre run sont récupérés")
    check(merged["generated_at"] == "2026-08-28T13:05:00+00:00",
          "generated_at vient du local (indicateur de fraîcheur préservé)")
    check(merged["total_articles"] == len(merged["items"]), "total_articles recalculé")
    dates = [feed_store.parse_date_key(i["date"]) for i in merged["items"]]
    check(dates == sorted(dates, reverse=True), "le résultat fusionné est trié")


def test_merge_keeps_our_version():
    print("\n[fusion] arbitrage sur un article présent des deux côtés")
    distant = {"items": [article("x", "2026-08-28T10:00:00+00:00", image=None)]}
    local = {"items": [article("x", "2026-08-28T10:00:00+00:00", image="https://exemple/img.jpg")]}
    merged, recovered, _ = merge_feed.merge_feeds(distant, local)
    check(len(merged["items"]) == 1 and recovered == 0, "pas de doublon sur un lien commun")
    check(merged["items"][0]["image"] == "https://exemple/img.jpg",
          "notre version est conservée (miniature récupérée pendant ce run)")


def test_merge_normalizes_and_caps():
    print("\n[fusion] la fusion applique les mêmes règles que le robot")
    distant = {"items": [article("vieux", "Mon, 06 Jan 2025 08:00:00 GMT")]}
    local = {"items": [article("neuf", "2026-08-28T12:00:00+00:00")]}
    merged, _, _ = merge_feed.merge_feeds(distant, local)
    check(all(feed_store.is_iso_date(i["date"]) for i in merged["items"]),
          "les dates héritées du distant sont normalisées elles aussi")
    check(merged["items"][0]["link"] == "neuf", "et le tri s'applique au résultat")

    gros = {"items": [article(f"a{i}", f"2026-0{1 + i % 8}-01T00:00:00+00:00")
                      for i in range(feed_store.MAX_HISTORY_SIZE + 50)]}
    merged, _, removed = merge_feed.merge_feeds({"items": []}, gros)
    check(len(merged["items"]) == feed_store.MAX_HISTORY_SIZE and removed == 50,
          "le plafond s'applique aussi après fusion")


def test_merge_refuses_empty_local():
    print("\n[fusion] garde-fou contre l'écrasement du distant")
    with tempfile.TemporaryDirectory() as d:
        distant, local, out = (os.path.join(d, n) for n in ("r.json", "o.json", "m.json"))
        with open(distant, "w") as f:
            json.dump({"items": [article("precieux", "2026-08-28T10:00:00+00:00")]}, f)
        with open(local, "w") as f:
            json.dump({"items": []}, f)
        sys.argv = ["merge_feed.py", distant, local, out]
        code = merge_feed.main()
        check(code == 1, "un local vide fait échouer la fusion au lieu d'écraser le distant")
        check(not os.path.exists(out), "aucun fichier de sortie n'est produit dans ce cas")


def test_feed_store_io():
    print("\n[io] lecture tolérante, écriture relisible")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "sous-dossier", "feed.json")
        feed_store.write_feed({"generated_at": "x", "items": [article("a", "2026-08-28T10:00:00+00:00")]}, path)
        check(os.path.exists(path), "le dossier de destination est créé si besoin")
        check(len(feed_store.load_items(path)) == 1, "relecture correcte")

        casse = os.path.join(d, "casse.json")
        with open(casse, "w") as f:
            f.write("{ ceci n'est pas du json")
        check(feed_store.load_items(casse) == [], "un fichier corrompu ne fait pas planter le robot")
        check(feed_store.load_items(os.path.join(d, "absent.json")) == [], "un fichier absent non plus")


def test_real_history():
    print("\n[données réelles] docs/feed.json du dépôt")
    items = feed_store.load_items()
    if not items:
        print("  (ignoré : docs/feed.json introuvable)")
        return
    heritees = [i for i in items if not feed_store.is_iso_date(i.get("date"))]
    copie = json.loads(json.dumps(items))
    feed_store.normalize_stored_dates(copie)
    ordered = feed_store.sort_items(copie)
    dates = [feed_store.parse_date_key(i["date"]) for i in ordered]
    check(dates == sorted(dates, reverse=True), f"les {len(items)} articles réels se trient correctement")
    check(all(feed_store.is_iso_date(i["date"]) for i in copie if i.get("date")),
          f"les {len(heritees)} date(s) héritée(s) sont toutes converties")
    check(len({i["link"] for i in items}) == len(items), "aucun lien dupliqué dans l'historique publié")


def test_canonical_link():
    print("\n[liens] nettoyage des paramètres de pistage")
    c = feed_store.canonical_link
    check(c("https://x.fr/a?utm_source=twitter&id=42") == "https://x.fr/a?id=42",
          "pistage retiré, paramètre fonctionnel conservé")
    check(c("https://x.fr/a?utm_source=x&utm_medium=y&fbclid=z") == "https://x.fr/a",
          "tous les paramètres de pistage connus sont retirés")
    check(c("https://x.fr/a#section") == "https://x.fr/a",
          "l'ancre est retirée (elle désigne une position, jamais un autre article)")
    check(c("https://x.fr/a?p=123") == "https://x.fr/a?p=123",
          "un identifiant d'article n'est pas retiré")
    check(c("pas une url") == "pas une url", "valeur illisible renvoyée telle quelle")
    check(c("") == "", "valeur vide tolérée")
    check(c("https://x.fr/a?UTM_SOURCE=X") == "https://x.fr/a",
          "insensible à la casse du nom de paramètre")


def test_canonicalize_stored_links():
    print("\n[liens] repasse rétroactive sur l'historique")
    items = [
        article("https://x.fr/a?utm_source=rss", "2026-08-28T10:00:00+00:00", "Article A"),
        article("https://x.fr/a", "2026-08-28T09:00:00+00:00", "Le même, partagé autrement"),
        article("https://x.fr/b", "2026-08-28T08:00:00+00:00", "Article B"),
    ]
    nettoyes, modifies, doublons = feed_store.canonicalize_stored_links(items)
    check(modifies == 1, "un seul lien portait un paramètre de pistage")
    check(doublons == 1, "le doublon ainsi révélé est retiré")
    check(len(nettoyes) == 2, "il reste deux articles distincts")
    check(nettoyes[0]["title"] == "Article A",
          "c'est la première occurrence qui est conservée (la plus récente après tri)")

    encore, m2, d2 = feed_store.canonicalize_stored_links(nettoyes)
    check(m2 == 0 and d2 == 0, "idempotente : le second passage ne change rien")


def test_push_payload():
    print("\n[push] contenu de la notification")
    import push_notify

    un = push_notify.build_payload([{"title": "Rockstar annonce la date de sortie",
                                     "official": True}])
    check("1 nouvel article" in un["title"], "singulier correct pour un seul article")
    check("officiel" in un["title"], "les articles officiels sont signalés")

    # Ce que l'utilisateur a demandé le 29/08 : le récapitulatif annonce
    # COMBIEN, jamais QUOI. Un titre d'article choisi parmi plusieurs donne
    # une idée fausse du lot ("premier" = ordre de FEEDS, pas importance).
    check("Rockstar annonce" not in un["title"] and "Rockstar annonce" not in un["body"],
          "aucun titre d'article dans la notification, ni en titre ni en corps")

    trois = push_notify.build_payload([
        {"title": "Un trailer inattendu", "official": False},
        {"title": "Autre chose", "official": False},
        {"title": "Encore autre chose", "official": False}])
    check("3 nouveaux articles" in trois["title"], "pluriel correct")
    check("officiel" not in trois["title"], "rien d'officiel : pas de mention parasite")
    check("trailer" not in trois["body"] and "Autre chose" not in trois["body"],
          "trois articles : toujours aucun titre repris")
    check(trois["tag"] == un["tag"], "tag identique : une notification remplace la précédente")

    # L'exigence de fond : Discord et le push disent MOT POUR MOT la même
    # chose. Garanti par construction (un seul libellé), vérifié ici pour
    # que la garantie ne saute pas en silence si quelqu'un la contourne.
    for lot in ([{"title": "a", "official": True}],
                [{"title": "a", "official": False}, {"title": "b", "official": True}],
                [{"title": str(i), "official": False} for i in range(7)]):
        check(push_notify.build_payload(lot)["title"] == feed_store.libelle_recap(lot),
              f"push et Discord annoncent le même texte ({len(lot)} article(s))")

    check(feed_store.libelle_recap([]) .startswith("🎮 0 nouvel"),
          "un lot vide ne fait pas planter le libellé (jamais envoyé, mais jamais d'exception)")
    check("Rockstar" in feed_store.libelle_recap([{"official": True}]),
          "un article sans clé 'title' ne fait pas planter le libellé")


def test_push_vapid_subject():
    print("\n[push] identifiant de contact VAPID (le champ 'sub')")
    import importlib
    import os
    import push_notify

    def avec(valeur):
        if valeur is None:
            os.environ.pop("VAPID_SUBJECT", None)
        else:
            os.environ["VAPID_SUBJECT"] = valeur
        importlib.reload(push_notify)
        return push_notify.VAPID_SUBJECT

    # Le cas qui a réellement échoué en production : un secret GitHub non
    # configuré n'est pas absent, il est défini À VIDE. La valeur par
    # défaut de os.environ.get ne s'applique alors pas, et le champ
    # obligatoire "sub" partait vide — le service de push rejetait l'envoi
    # avec « Missing 'sub' from claims ».
    check(avec("") != "", "un secret VIDE ne laisse pas 'sub' vide (le bug du 28/08)")
    check(avec("   ") != "", "un secret composé d'espaces non plus")
    check(avec(None) != "", "une variable absente donne aussi une valeur")
    check(avec("mailto:moi@exemple.fr") == "mailto:moi@exemple.fr",
          "un secret rempli est respecté")

    # LE test qui manquait. La première correction produisait un repli
    # syntaxiquement plausible mais refusé par la bibliothèque : elle
    # n'accepte qu'un schéma et un hôte, pas de chemin. Et son message
    # d'erreur est le même que pour un champ absent (« Missing 'sub' from
    # claims »), donc rien ne le distinguait dans les logs.
    #
    # Se fier à sa propre lecture de la spécification ne suffit pas : on
    # confronte la valeur au validateur réel.
    defaut = avec("")
    try:
        from py_vapid import _check_sub
    except ImportError:
        print("    (py_vapid absent — validation réelle non exécutée ici, "
              "elle tourne en CI où pywebpush est installé)")
    else:
        check(bool(_check_sub(defaut)),
              f"le repli est accepté par le validateur de py_vapid ({defaut})")
        check(not _check_sub("https://exemple.github.io/projet/"),
              "reproduction du bug : une URL AVEC chemin est bien refusée")
        check(bool(_check_sub("mailto:moi@exemple.fr")),
              "une adresse mailto: reste acceptée")

    check(push_notify.check_subject(defaut), "check_subject accepte le repli")
    check(not push_notify.check_subject("https://exemple.com/avec/chemin"),
          "check_subject refuse une URL avec chemin, avec un message clair")

    os.environ.pop("VAPID_SUBJECT", None)
    importlib.reload(push_notify)


def test_push_subscriptions():
    print("\n[push] lecture du secret d'abonnements")
    import os
    import push_notify

    def avec(valeur):
        os.environ["PUSH_SUBSCRIPTIONS"] = valeur
        return push_notify.load_subscriptions()

    valide = '{"endpoint":"https://fcm.example/abc","keys":{"p256dh":"x","auth":"y"}}'
    check(len(avec("[" + valide + "]")) == 1, "tableau JSON accepté")
    check(len(avec(valide)) == 1, "abonnement seul accepté (collé à la main depuis l'app)")
    check(len(avec("[" + valide + "," + valide + "]")) == 2, "plusieurs appareils acceptés")
    check(avec("") == [], "secret vide -> aucun abonnement, pas d'erreur")
    check(avec("pas du json") == [], "secret illisible -> aucun abonnement, pas de plantage")
    check(avec('[{"endpoint":"https://x"}]') == [], "abonnement sans clés rejeté")
    os.environ.pop("PUSH_SUBSCRIPTIONS", None)


def test_push_masquage_endpoint():
    print("\n[push] les journaux ne doivent jamais laisser fuiter un endpoint")
    import push_notify

    jeton = "cAAAAAAAAAAAAAAA_jeton_unique_de_l_appareil_xyz789"
    endpoint = "https://fcm.googleapis.com/fcm/send/" + jeton
    abonnements = [{"endpoint": endpoint, "keys": {"p256dh": "x", "auth": "y"}}]

    # Message réellement produit par urllib3 : l'hôte en préfixe, et le
    # CHEMIN SEUL après "with url:". C'est cette forme-là qui fuitait —
    # masquer uniquement l'URL complète ne l'aurait pas attrapée.
    reel = ("HTTPSConnectionPool(host='fcm.googleapis.com', port=443): "
            "Max retries exceeded with url: /fcm/send/" + jeton +
            " (Caused by ConnectTimeoutError(...))")
    propre = push_notify.masquer_endpoints(reel, abonnements)
    check(jeton not in propre, "le jeton de l'appareil disparaît du message urllib3")
    check("chemin masqué" in propre, "le chemin est remplacé par un marqueur explicite")
    check("fcm.googleapis.com" in propre,
          "l'hôte reste visible : il aide au diagnostic et n'identifie personne")

    complet = "Push failed for " + endpoint + " : 500"
    check(jeton not in push_notify.masquer_endpoints(complet, abonnements),
          "l'URL complète est masquée aussi")

    # Robustesse : le masquage tourne dans un gestionnaire d'exception, il ne
    # doit jamais devenir lui-même la cause d'un plantage.
    for bancal in ([None], [{}], ["pas un dict"], [{"endpoint": None}],
                   [{"endpoint": ""}], [{"endpoint": "https://x"}]):
        push_notify.masquer_endpoints("un message", bancal)
    check(True, "un abonnement mal formé ne fait pas planter le masquage")

    intact = push_notify.masquer_endpoints("erreur sans URL", abonnements)
    check(intact == "erreur sans URL", "un message sans URL n'est pas abîmé")

    # Même défaut, même correctif : le webhook Discord est un secret de la
    # même nature (qui l'a peut publier sur le salon).
    webhook = "https://discord.com/api/webhooks/123456789/cLE_JETON_SECRET_DU_SALON"
    reel_discord = ("HTTPSConnectionPool(host='discord.com', port=443): "
                    "Max retries exceeded with url: /api/webhooks/123456789/"
                    "cLE_JETON_SECRET_DU_SALON (Caused by ...)")
    propre_d = feed_store.masquer_urls(reel_discord, [webhook])
    check("cLE_JETON_SECRET_DU_SALON" not in propre_d,
          "le jeton du webhook Discord disparaît lui aussi")
    check("discord.com" in propre_d, "l'hôte Discord reste visible")
    check(feed_store.masquer_urls("rien", [None, "", 42]) == "rien",
          "des URL absentes ou mal typées ne font pas planter le masquage")
    check(feed_store.masquer_urls("a/b", ["https://h/"]) == "a/b",
          "un chemin réduit à « / » n'est pas remplacé (illisibilité pour rien)")


# ---------------------------------------------------------------------------
# Récupération parallèle des sources
#
# La parallélisation ne doit RIEN changer au fichier produit : c'est l'ordre
# de FEEDS, et lui seul, qui décide quelle source possède un article et dans
# quel ordre les sources supplémentaires s'empilent derrière. Ces tests
# comparent donc le nouveau chemin à une exécution séquentielle de
# référence, sur les mêmes données.
# ---------------------------------------------------------------------------

def _fausses_sources():
    """35 sources réparties comme les vraies : un domaine à 10 flux, un à 6,
    et 19 domaines à 1 flux."""
    sources = []
    for i in range(10):
        sources.append({"id": f"rssapp{i}", "name": f"RSSApp {i}",
                        "url": f"https://rss.app/feeds/{i}.xml"})
    for i in range(6):
        sources.append({"id": f"gnews{i}", "name": f"Google News {i}",
                        "url": f"https://news.google.com/rss/search?q={i}"})
    for i in range(19):
        sources.append({"id": f"site{i}", "name": f"Site {i}",
                        "url": f"https://site{i}.example/rss"})
    return sources


def _article(source, titre, lien, date="2026-08-28T10:00:00+00:00"):
    return {"title": titre, "link": lien, "source_link": None, "date": date,
            "source": source, "official": False, "rockstarmag": False,
            "specialist": False, "lang": "en", "image": None, "description": ""}


def _fausse_collecte(feed, decoded_cache=None, http_state=None):
    """Récupération factice, déterministe et sans réseau.

    Chaque source renvoie un article qui lui est propre, plus un article
    COMMUN à toutes : c'est lui qui met à l'épreuve la déduplication et
    l'ordre d'empilement des sources supplémentaires.

    Le sujet commun porte le même titre mais une URL par source, parce que
    c'est ce que fait le monde réel : trente-cinq rédactions publient
    trente-cinq pages sur la même annonce. Le même lien répété trente-cinq
    fois modéliserait nos propres requêtes qui se recoupent — et depuis que
    la couverture se compte par lien, ça n'ajoute justement aucune source.
    """
    items = [
        _article(feed["name"], f"Exclu {feed['id']}", f"https://exemple.fr/{feed['id']}"),
        _article(feed["name"], "Le meme article partout", f"https://{feed['id']}.exemple.fr/commun"),
    ]
    return items, {"raw_count": 2, "not_modified": False}, [f"[{feed['name']}] ok"]


def test_fetch_parallele_identique():
    print("\n== Récupération parallèle : résultat identique au séquentiel ==")
    import fetch_feeds

    sources = _fausses_sources()

    def parcours(resultats):
        """Rejoue la fusion et renvoie l'état final, comparable."""
        all_items, links_index, newly = [], {}, []
        infos, counts, inchanges = fetch_feeds.merge_results(
            sources, resultats, all_items, links_index, newly,
            decoded_cache={}, afficher=False)
        return all_items, newly, infos, counts, inchanges

    # Référence : récupération strictement séquentielle, comme avant.
    sequentiel = {f["id"]: _fausse_collecte(f) for f in sources}
    attendu = parcours(sequentiel)

    # Nouveau chemin : récupération parallèle.
    parallele = fetch_feeds.fetch_all_feeds(sources, {}, {}, collecte=_fausse_collecte)
    obtenu = parcours(parallele)

    check(set(parallele) == set(sequentiel), "toutes les sources sont revenues, aucune perdue")
    check(obtenu[0] == attendu[0], "liste finale des articles identique (contenu ET ordre)")
    check(obtenu[1] == attendu[1], "liste des nouveautés identique")
    check(obtenu[2] == attendu[2], "état HTTP par source identique")
    check(obtenu[3] == attendu[3], "compteurs de nouveautés par source identiques")
    check(obtenu[4] == attendu[4], "nombre de sources inchangées identique")

    # Le cas piégeux : l'article commun n'est gardé qu'une fois, et les 34
    # autres sources doivent s'empiler derrière dans l'ordre de FEEDS.
    communs = [i for i in obtenu[0] if i["title"] == "Le meme article partout"]
    check(len(communs) == 1, "l'article publié par les 35 sources n'est stocké qu'une fois")
    check(communs[0]["source"] == sources[0]["name"],
          "il est attribué à la PREMIÈRE source de la liste, pas à la plus rapide")
    empile = [s["source"] for s in (communs[0].get("extraSources") or [])]
    check(empile == [f["name"] for f in sources[1:]],
          "les 34 autres sources s'empilent derrière dans l'ordre de FEEDS")


def test_libelle_actu_majeure():
    print("\n[notif] le ton change quand plusieurs rédactions couvrent le même sujet")
    import push_notify

    def art(sources, officiel=False):
        return {"title": "peu importe", "official": officiel,
                "extraSources": [{"source": f"src{i}"} for i in range(sources - 1)]}

    seuil = feed_store.HOT_SOURCE_THRESHOLD
    sous = feed_store.libelle_recap([art(seuil - 1), art(1)])
    sur = feed_store.libelle_recap([art(seuil), art(1)])

    check(not feed_store.est_actu_majeure([art(seuil - 1)]),
          f"{seuil - 1} sources : ce n'est pas encore une actu majeure")
    check(feed_store.est_actu_majeure([art(seuil)]),
          f"{seuil} sources : c'en est une")
    check("Actu majeure" not in sous, "sous le seuil, le libellé reste celui de routine")
    check("Actu majeure" in sur and str(seuil) in sur,
          "au seuil, le libellé annonce l'alerte et le nombre de rédactions")
    check("2 nouveaux articles" in sur,
          "le décompte habituel reste présent dans l'alerte")

    # Aucun titre d'article, y compris en alerte — la règle posée le 29/08.
    check("peu importe" not in sur, "toujours aucun titre d'article, même en alerte")

    # Push et Discord doivent rester mot pour mot identiques (règle du 29/08).
    for lot in ([art(1)], [art(seuil)], [art(seuil + 3), art(1, True)]):
        check(push_notify.build_payload(lot)["title"] == feed_store.libelle_recap(lot),
              f"push et Discord annoncent le même texte ({feed_store.nb_sources_max(lot)} sources)")

    # Le tag distingue l'alerte : sinon le récapitulatif de routine du
    # passage suivant l'effacerait en silence une demi-heure plus tard.
    routine = push_notify.build_payload([art(1)])["tag"]
    alerte = push_notify.build_payload([art(seuil)])["tag"]
    check(routine != alerte, "une actu majeure a son propre tag de notification")
    check(push_notify.build_payload([art(seuil)])["tag"] == alerte,
          "deux alertes partagent le même tag : la seconde remplace la première")

    check(feed_store.nb_sources_max([]) == 0, "un lot vide ne fait pas planter le comptage")
    check(feed_store.nb_sources_max([{"title": "x"}]) == 1,
          "un article sans extraSources compte pour une source")


def test_promotion_entre_passages():
    print("\n[notif] un sujet qui devient majeur au fil des passages")
    import fetch_feeds

    seuil = feed_store.HOT_SOURCE_THRESHOLD
    titre = "Rockstar annonce la date de sortie de GTA 6"

    def art(source, lien, date="2026-08-29T08:00:00+00:00"):
        return {"title": titre, "link": lien, "date": date, "source": source,
                "official": False, "rockstarmag": False, "specialist": False,
                "lang": "en", "image": None, "description": "", "source_link": None}

    # L'article existe déjà, repris par seuil-1 rédactions. Une de plus le
    # fait basculer — mais elle arrive dans un passage ULTÉRIEUR, donc c'est
    # un doublon : rien de « nouveau » à annoncer. Sans le signal de
    # promotion, l'utilisateur ne serait jamais prévenu.
    connu = art("IGN", "https://ign.com/a")
    connu["extraSources"] = [{"source": f"Redac{i}"} for i in range(seuil - 2)]
    # Les distracteurs sont PLUS ANCIENS : l'article connu doit rester dans
    # la fenêtre de comparaison, sinon on testerait le bug déjà corrigé
    # ailleurs plutôt que la promotion.
    historique = [connu] + [art("Vieux", f"https://v/{i}", "2026-08-01T00:00:00+00:00")
                            for i in range(300)]
    for vieux in historique[1:]:
        vieux["title"] = f"Sujet sans rapport {vieux['link']}"
    index = {i["link"]: i for i in historique}

    feeds = [{"id": "z", "name": "GameSpot", "url": "https://z.test/rss"}]
    resultats = {"z": ([art("GameSpot", "https://gamespot.com/a")],
                       {"raw_count": 1, "not_modified": False}, [])}
    neufs, promus = [], []
    fetch_feeds.merge_results(feeds, resultats, historique, index, neufs, {},
                              afficher=False, promus=promus)

    check(neufs == [], "la reprise est bien un doublon : aucun article nouveau")
    check(len(promus) == 1, "le sujet qui franchit le seuil est signalé comme promu")
    check(1 + len(promus[0].get("extraSources") or []) == seuil,
          f"il compte désormais {seuil} rédactions")

    # Sans promotion, il n'y aurait rien à dire. Avec, l'alerte part quand
    # même — c'est tout l'intérêt.
    check(feed_store.est_actu_majeure([], promus),
          "l'alerte se déclenche sur la seule promotion, sans article nouveau")
    libelle = feed_store.libelle_recap([], promus)
    check("Actu majeure" in libelle, "le libellé annonce bien l'alerte")
    check("0 nouvel" not in libelle,
          "sans article nouveau, on n'annonce pas « 0 nouvel article »")

    import push_notify
    check(push_notify.build_payload([], promus)["title"] == libelle,
          "push et Discord restent identiques sur une promotion seule")

    # Une reprise DE PLUS ne doit pas réalerter : le sujet est déjà majeur.
    resultats2 = {"z": ([art("Kotaku", "https://kotaku.com/a")],
                        {"raw_count": 1, "not_modified": False}, [])}
    promus2 = []
    fetch_feeds.merge_results(feeds, resultats2, historique, index, [], {},
                              afficher=False, promus=promus2)
    check(promus2 == [], "un sujet déjà majeur ne réalerte pas à chaque reprise")


def test_recap_hebdomadaire():
    print("\n[hebdo] récapitulatif du dimanche")
    import weekly_digest
    from datetime import datetime, timedelta, timezone

    maintenant = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)

    def art(titre, jours, sources=1, officiel=False):
        return {"title": titre, "link": f"https://x/{titre}",
                "date": (maintenant - timedelta(days=jours)).isoformat(),
                "official": officiel,
                "extraSources": [{"source": str(i)} for i in range(sources - 1)]}

    items = [
        art("Sujet très repris", 2, sources=5),
        art("Sujet moyennement repris", 1, sources=2),
        art("Sujet isolé mais frais", 0),
        art("Sujet de la semaine dernière", 9, sources=9),
    ]
    recents = weekly_digest.articles_de_la_semaine(items, maintenant)
    check(len(recents) == 3, "les articles de plus de 7 jours sont écartés")
    check(all("semaine dernière" not in i["title"] for i in recents),
          "même très repris, un vieux sujet n'entre pas dans la semaine")

    classe = weekly_digest.classer(recents)
    check(classe[0]["title"] == "Sujet très repris",
          "le classement est piloté par le nombre de rédactions, pas par la date")
    check(classe[-1]["title"] == "Sujet isolé mais frais",
          "l'article le moins repris ferme la marche malgré sa fraîcheur")

    embed = weekly_digest.construire_embed(items, maintenant)
    check(embed is not None, "un embed est produit")
    check("3 articles" in embed["description"], "le décompte porte sur la semaine seule")
    check("🔥" in embed["description"], "le sujet au-delà du seuil porte la flamme")
    check("https://x/Sujet très repris" in embed["description"],
          "les liens sont présents — c'est un récapitulatif qu'on lit, pas une bannière")

    check(weekly_digest.construire_embed([], maintenant) is None,
          "aucun article : aucun message (pas d'embed vide)")
    check(weekly_digest.construire_embed([art("Vieux", 30)], maintenant) is None,
          "rien cette semaine : aucun message non plus")

    # Le Markdown de Discord est fragile aux deux bouts : un crochet dans le
    # titre ferme le libellé trop tôt, une parenthèse dans l'URL ferme la
    # cible trop tôt. Les deux produiraient un message cassé.
    lien = weekly_digest.lien_markdown("Titre [avec] crochets", "https://x/page")
    check("[avec]" not in lien, "les crochets d'un titre sont neutralisés")
    check(lien.startswith("[Titre (avec) crochets]"), "le titre reste lisible")

    lien2 = weekly_digest.lien_markdown("Normal", "https://x/GTA_(serie)")
    check("(serie)" not in lien2.split("](")[1],
          "les parenthèses d'une URL sont encodées (sinon le lien Discord casse)")
    check("%28serie%29" in lien2, "elles sont encodées, pas supprimées")

    long_titre = weekly_digest.lien_markdown("x" * 300, "https://x/y")
    check(len(long_titre.split("](")[0]) <= 92, "un titre à rallonge est tronqué")
    check(weekly_digest.lien_markdown(None, None).startswith("[sans titre]"),
          "un article sans titre ni lien ne fait pas planter le récapitulatif")


def test_suivi_sources_muettes():
    print("\n[sources] alerte après une panne de 24 h, pas après 6 passages")
    import fetch_feeds
    from datetime import timedelta

    heures = fetch_feeds.DEAD_SOURCE_HOURS
    t0 = datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc)

    def sante(statut):
        return [{"id": "a", "name": "VG247", "status": statut}]

    def passage(suivi, statut, apres_heures):
        return fetch_feeds.suivre_sources_muettes(
            sante(statut), suivi, maintenant=t0 + timedelta(hours=apres_heures))

    # Une panne courte ne déclenche rien, quel que soit le NOMBRE de
    # passages : c'est tout l'intérêt de compter en heures. Dix passages en
    # deux heures restent deux heures de panne.
    suivi = {}
    for i in range(10):
        suivi, alertes = passage(suivi, "muette", i * 0.2)
        check(alertes == [], f"panne de {i * 0.2:.1f} h sur {i + 1} passages : rien")
    check("a" in suivi, "la source est suivie, avec la date de son premier échec")
    check(suivi["a"]["depuis"] == t0.isoformat(),
          "la date retenue est celle du PREMIER échec, pas du dernier")

    # Juste avant le seuil : toujours rien.
    suivi, alertes = passage(suivi, "muette", heures - 0.1)
    check(alertes == [], f"à {heures - 0.1} h : toujours rien")

    # Au seuil : une alerte, une seule.
    suivi, alertes = passage(suivi, "muette", heures)
    check(len(alertes) == 1 and alertes[0]["type"] == "tombee",
          f"à {heures} h de panne continue, une alerte « tombée » part")
    check(alertes[0]["name"] == "VG247", "l'alerte nomme la source")
    check(alertes[0]["heures"] >= heures,
          "et donne la durée en heures, pas un nombre de passages")

    for h in (heures + 1, heures + 5, heures + 30):
        suivi, alertes = passage(suivi, "muette", h)
        check(alertes == [],
              f"panne prolongée à {h} h : aucune répétition "
              f"(sinon 48 messages par jour)")

    # Le retour demande DEUX passages réussis d'affilée.
    suivi, alertes = passage(suivi, "ok", heures + 31)
    check(alertes == [], "une seule réussite ne suffit pas à annoncer le retour")
    check("a" in suivi, "le chronomètre tourne encore, la reprise n'est pas confirmée")
    suivi, alertes = passage(suivi, "ok", heures + 32)
    check(len(alertes) == 1 and alertes[0]["type"] == "retour",
          "au second passage réussi d'affilée, le retour est annoncé")
    check(suivi == {},
          "et la source sort du suivi, pas conservée pour rien dans feed.json")

    suivi, alertes = passage(suivi, "ok", heures + 33)
    check(alertes == [], "une source qui va bien ne dit rien")

    # LE CAS IGN. Une source qui alterne réussite et échec ne doit pas
    # échapper à la détection : sans la reprise confirmée, la moindre
    # réussite remettrait le chronomètre à zéro et l'alerte ne partirait
    # jamais, alors que la source est cassée la moitié du temps.
    suivi = {}
    alertes = []
    for i in range(60):
        suivi, alertes = passage(suivi, "muette" if i % 2 == 0 else "ok", i)
        if alertes:
            break
    check(any(a["type"] == "tombee" for a in alertes),
          "une source qui alterne un passage sur deux finit par être signalée")
    check(i >= heures,
          f"et pas avant le seuil : signalée à {i} h de clignotement")

    # Le pendant : une source vraiment rétablie ne traîne pas dans le suivi.
    suivi = {}
    for statut, h in (("muette", 0), ("muette", 1), ("ok", 2), ("ok", 3)):
        suivi, alertes = passage(suivi, statut, h)
        check(alertes == [], f"hoquet court ({statut}) : silence radio")
    check(suivi == {}, "et le chronomètre est bien effacé après deux réussites")

    # Un flux qui répond 304 est vivant : build_sources_health le classe
    # « ok », donc il ne doit jamais entrer dans le comptage.
    suivi, alertes = fetch_feeds.suivre_sources_muettes(sante("tarie"), {})
    check(alertes == [] and suivi == {},
          "une source « tarie » (vivante mais sans actu) n'est pas une panne")

    # Migration : l'ancien format était un simple nombre de passages, sans
    # date. On repart de maintenant plutôt que d'inventer une ancienneté —
    # ça retarde une alerte, ça n'en fabrique jamais une fausse.
    suivi, alertes = passage({"a": 5}, "muette", 0)
    check(alertes == [], "un ancien compteur ne déclenche pas d'alerte immédiate")
    check(suivi["a"]["depuis"] == t0.isoformat(),
          "il est converti en chronomètre démarré maintenant")


def test_chaine_youtube_rockstar():
    print("\n[sources] la chaîne YouTube de Rockstar dans l'onglet Rockstar")
    import fetch_feeds

    yt = next(f for f in fetch_feeds.FEEDS if f["id"] == "rockstar-youtube")
    check(yt.get("official") is True, "la chaîne est déclarée officielle")

    # Sans official_domains, la vérification de domaine retirerait ce statut
    # à chaque passage (les liens pointent vers youtube.com, pas
    # rockstargames.com) et les vidéos n'apparaîtraient JAMAIS dans l'onglet
    # Rockstar de l'app, qui filtre précisément sur ce statut.
    check(fetch_feeds.lien_officiel("https://www.youtube.com/watch?v=abc",
                                    fetch_feeds.domaines_officiels(yt)),
          "un lien YouTube conserve le statut officiel pour CETTE source")
    check(not fetch_feeds.lien_officiel("https://www.youtube.com/watch?v=abc",
                                        fetch_feeds.OFFICIAL_DOMAINS),
          "mais pas avec les domaines par défaut : la règle reste stricte ailleurs")

    # La passe rétroactive doit respecter la même règle, sinon elle
    # déclasserait les vidéos à chaque exécution.
    videos = [{"official": True, "source": "Rockstar Games (YouTube)",
               "link": "https://www.youtube.com/watch?v=abc"},
              {"official": True, "source": "Rockstar Games (officiel EN)",
               "link": "https://www.youtube.com/watch?v=xyz"}]
    fetch_feeds.recheck_official_status(videos)
    check(videos[0]["official"] is True,
          "la vidéo de la chaîne garde son statut à la repasse rétroactive")
    check(videos[1]["official"] is False,
          "un lien YouTube venu d'une AUTRE source officielle est bien déclassé")

    # Le titre : une vidéo « Trailer 3 » ne contient aucun mot-clé GTA 6.
    # Sans le supplément, elle serait rejetée le jour qui compte.
    check(fetch_feeds.passe_le_filtre(yt, "Trailer 3", ""),
          "« Trailer 3 » est retenu (c'est tout l'objet du mot-clé ajouté)")
    check(fetch_feeds.passe_le_filtre(yt, "Grand Theft Auto VI: Trailer 3", ""),
          "un titre explicite passe aussi")
    check(not fetch_feeds.passe_le_filtre(yt, "Red Dead Online: Blood Money", ""),
          "le contenu Red Dead reste écarté : le filtre n'est pas désactivé")
    check(not fetch_feeds.passe_le_filtre(yt, "GTA Online Weekly Update", ""),
          "les mises à jour GTA Online sans « trailer » restent écartées")

    # Aucune contamination des autres sources officielles.
    rs = next(f for f in fetch_feeds.FEEDS if f["id"] == "rockstar-en")
    check(fetch_feeds.mots_cles_officiels(rs) == fetch_feeds.OFFICIAL_KEYWORDS,
          "les autres sources officielles gardent les mots-clés d'origine")
    check(not fetch_feeds.passe_le_filtre(rs, "Trailer 3", ""),
          "« Trailer 3 » reste rejeté partout ailleurs")
    check(tuple(fetch_feeds.domaines_officiels(rs)) == fetch_feeds.OFFICIAL_DOMAINS,
          "et leurs domaines d'origine")


def test_garde_fou_archives():
    print("\n[collecte] les archives ne sont pas des nouvelles")
    import fetch_feeds
    from datetime import datetime, timedelta, timezone

    maintenant = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    plafond = fetch_feeds.MAX_ARTICLE_AGE_DAYS

    def il_y_a(jours):
        return (maintenant - timedelta(days=jours)).isoformat()

    check(not fetch_feeds.trop_vieux(il_y_a(0), maintenant), "un article du jour passe")
    check(not fetch_feeds.trop_vieux(il_y_a(plafond), maintenant),
          f"un article de {plafond} jours passe encore (le seuil est un « au-delà »)")
    check(fetch_feeds.trop_vieux(il_y_a(plafond + 1), maintenant),
          f"un article de {plafond + 1} jours est écarté")

    # Le cas réel du 29/08 : la recherche Google News restreinte au domaine
    # VG247 a remonté 8 articles de 2022 à 2024, annoncés comme « nouveaux ».
    check(fetch_feeds.trop_vieux("2024-07-26T00:00:00+00:00", maintenant),
          "reproduction : l'archive VG247 de 2024 aurait été écartée")
    check(fetch_feeds.trop_vieux("2022-02-04T00:00:00+00:00", maintenant),
          "reproduction : celle de 2022 aussi")

    # LE piège : une date absente ou illisible ne doit PAS valoir 1970.
    # normalize_date renvoie "" quand le flux ne fournit aucune date, et
    # parse_date_key retombe sur DATE_FLOOR — traiter ça comme « vieux de
    # 56 ans » viderait tout flux mal daté.
    check(not fetch_feeds.trop_vieux("", maintenant), "sans date : on garde (dans le doute)")
    check(not fetch_feeds.trop_vieux(None, maintenant), "date absente : on garde")
    check(not fetch_feeds.trop_vieux("pas une date", maintenant), "date illisible : on garde")

    # Une date dans le futur (horloge d'éditeur mal réglée) ne doit pas non
    # plus faire disparaître l'article.
    futur = (maintenant + timedelta(days=3)).isoformat()
    check(not fetch_feeds.trop_vieux(futur, maintenant), "date future : on garde")


def test_dedup_meme_passage():
    print("\n[dedup] deux rédactions, même sujet, même passage")
    import fetch_feeds

    # LE bug : les nouveaux articles étaient ajoutés à la FIN de all_items,
    # donc hors des `all_items[:TITLE_SIMILARITY_WINDOW]` que consultait
    # find_duplicate. Dès que l'historique dépassait la taille de la
    # fenêtre, deux sources publiant le même sujet dans le même passage
    # n'étaient plus jamais rapprochées. Conséquence mesurée sur le vrai
    # feed.json avant correctif : 13 doublons manifestes dans les 400
    # articles les plus récents, et le badge « N SOURCES » plafonné à 3
    # (donc jamais affiché, le seuil étant à 4).
    #
    # Le test balaie de part et d'autre de la fenêtre : c'est exactement
    # là que le comportement basculait.
    titre = "GTA 6 First Gameplay Details Reveal the Return of RPG Mechanics"
    redactions = [
        {"id": "a", "name": "IGN", "url": "https://a.test/rss"},
        {"id": "b", "name": "IGN France", "url": "https://b.test/rss"},
        {"id": "c", "name": "GameSpot", "url": "https://c.test/rss"},
    ]

    def art(source, titre_, lien, date="2026-08-29T08:00:00+00:00"):
        return {"title": titre_, "link": lien, "date": date, "source": source,
                "official": False, "rockstarmag": False, "specialist": False,
                "lang": "en", "image": None, "description": "", "source_link": None}

    def passage(nb_vieux):
        resultats = {
            f["id"]: ([art(f["name"], titre, f"https://{f['id']}.test/article")],
                      {"raw_count": 1, "not_modified": False}, [])
            for f in redactions
        }
        # Les vieux articles sont DEVANT (plus récents dans le tri) pour
        # reproduire l'historique réel, où les nouveaux arrivants sont
        # repoussés hors de la fenêtre.
        historique = [art("Ancienne source", f"Sujet sans rapport numero {i}",
                          f"https://vieux.test/{i}", "2026-08-30T00:00:00+00:00")
                      for i in range(nb_vieux)]
        index = {i["link"]: i for i in historique}
        neufs = []
        fetch_feeds.merge_results(redactions, resultats, historique, index,
                                  neufs, {}, afficher=False)
        garde = [i for i in historique if i["source"] != "Ancienne source"]
        sources = 1 + len(garde[0].get("extraSources") or []) if garde else 0
        return len(neufs), sources

    fenetre = fetch_feeds.TITLE_SIMILARITY_WINDOW
    for nb in (0, fenetre - 1, fenetre, fenetre + 1, fenetre * 6):
        ajoutes, sources = passage(nb)
        check(ajoutes == 1,
              f"{nb} articles en base : un seul gardé sur trois (obtenu : {ajoutes})")
        check(sources == 3,
              f"{nb} articles en base : les 3 rédactions sont comptées (obtenu : {sources})")

    # Le seuil « actu majeure » doit rester atteignable : c'était le fond du
    # problème, un compteur plafonné à 3 avec un seuil à 4.
    check(fetch_feeds.HOT_SOURCE_THRESHOLD <= len(redactions) + 1,
          "le seuil d'actu majeure reste atteignable par le comptage réel")


def test_identifiants_de_sources_uniques():
    print("\n== Identifiants des vraies sources ==")
    import fetch_feeds
    ids = [f["id"] for f in fetch_feeds.FEEDS]
    doublons = sorted({i for i in ids if ids.count(i) > 1})
    # Les résultats de la récupération parallèle sont rangés par identifiant :
    # deux sources partageant le même id verraient l'une écraser l'autre, et
    # la seconde serait traitée deux fois. En séquentiel c'était sans effet,
    # d'où ce garde-fou explicite.
    check(not doublons, f"les {len(ids)} identifiants de sources sont uniques"
                        + (f" — DOUBLONS : {doublons}" if doublons else ""))
    check(all(f.get("id") for f in fetch_feeds.FEEDS), "aucune source sans identifiant")


def test_chaines_par_hote():
    print("\n== Découpage en files par domaine ==")
    import fetch_feeds

    sources = _fausses_sources()
    chaines = fetch_feeds.chaines_par_hote(sources, par_hote=3)

    plat = [f["id"] for c in chaines for f in c]
    check(sorted(plat) == sorted(f["id"] for f in sources),
          "chaque source apparaît une fois et une seule")

    from urllib.parse import urlparse
    par_domaine = {}
    for chaine in chaines:
        domaines = {urlparse(f["url"]).netloc for f in chaine}
        check(len(domaines) == 1, f"une file ne mélange jamais deux domaines ({domaines})")
        par_domaine.setdefault(domaines.pop(), []).append(chaine)

    check(len(par_domaine["rss.app"]) == 3, "les 10 flux rss.app tiennent en 3 files, pas 10")
    check(len(par_domaine["news.google.com"]) == 3, "les 6 flux Google News tiennent en 3 files")
    check(len(par_domaine["site0.example"]) == 1, "un domaine à flux unique n'a qu'une file")

    check(fetch_feeds.chaines_par_hote(sources, 3) == chaines,
          "le découpage est déterministe : mêmes sources, mêmes files")


def test_plafond_par_domaine():
    print("\n== Politesse : plafond de requêtes simultanées par domaine ==")
    import fetch_feeds
    import threading
    from urllib.parse import urlparse

    verrou = threading.Lock()
    en_cours = {}
    maxi = {}

    def collecte_observee(feed, decoded_cache=None, http_state=None):
        hote = urlparse(feed["url"]).netloc
        with verrou:
            en_cours[hote] = en_cours.get(hote, 0) + 1
            maxi[hote] = max(maxi.get(hote, 0), en_cours[hote])
        time.sleep(0.05)  # laisse le temps aux autres fils de se chevaucher
        with verrou:
            en_cours[hote] -= 1
        return [], {"raw_count": 0, "not_modified": False}, []

    fetch_feeds.fetch_all_feeds(_fausses_sources(), {}, {}, collecte=collecte_observee)

    check(maxi.get("rss.app", 0) <= fetch_feeds.PER_HOST_LIMIT,
          f"rss.app n'a jamais reçu plus de {fetch_feeds.PER_HOST_LIMIT} requêtes à la fois "
          f"(observé : {maxi.get('rss.app')})")
    check(maxi.get("news.google.com", 0) <= fetch_feeds.PER_HOST_LIMIT,
          f"news.google.com non plus (observé : {maxi.get('news.google.com')})")
    check(max(maxi.values()) > 1, "mais plusieurs sources tournent bien en parallèle")


def test_source_qui_plante():
    print("\n== Une source qui casse n'emporte pas les autres ==")
    import fetch_feeds

    sources = _fausses_sources()
    cassee = sources[4]["id"]

    def collecte_capricieuse(feed, decoded_cache=None, http_state=None):
        if feed["id"] == cassee:
            raise RuntimeError("le serveur a renvoyé n'importe quoi")
        return _fausse_collecte(feed)

    resultats = fetch_feeds.fetch_all_feeds(sources, {}, {}, collecte=collecte_capricieuse)

    check(len(resultats) == len(sources), "toutes les sources ont une entrée, y compris celle qui a cassé")
    items, info, journal = resultats[cassee]
    check(items == [], "la source cassée ne renvoie aucun article")
    check(info["not_modified"] is False, "elle n'est pas comptée comme « inchangée »")
    check(any("échec inattendu" in l for l in journal), "l'échec est tracé dans le journal")
    check(resultats[sources[5]["id"]][0] != [], "les autres sources ont bien été récupérées")


def test_reprise_apres_echec_passager():
    print("\n== Une source qui échoue une fois est réessayée, une seule fois ==")
    import fetch_feeds

    sources = _fausses_sources()
    capricieuse = sources[3]["id"]
    appels = collections.Counter()
    etats_vus = {}

    def collecte(feed, decoded_cache=None, http_state=None):
        appels[feed["id"]] += 1
        etats_vus.setdefault(feed["id"], []).append(http_state)
        if feed["id"] == capricieuse and appels[feed["id"]] == 1:
            # Exactement le symptôme du 30/08/2026 : YouTube renvoie 500,
            # puis répond normalement un instant plus tard.
            return [], {"raw_count": 0, "not_modified": False,
                        "http_status": 500, "not_a_feed": True}, [
                f"[{feed['name']}] récupération...", "  PAS UN FLUX — HTTP 500"]
        return _fausse_collecte(feed)

    pause = fetch_feeds.REPRISE_PAUSE
    fetch_feeds.REPRISE_PAUSE = 0
    try:
        resultats = fetch_feeds.fetch_all_feeds(
            sources, {}, {"peu importe": {}}, collecte=collecte)
    finally:
        fetch_feeds.REPRISE_PAUSE = pause

    items, info, journal = resultats[capricieuse]
    check(appels[capricieuse] == 2, "la source en échec a été interrogée deux fois")
    check(all(appels[f["id"]] == 1 for f in sources if f["id"] != capricieuse),
          "aucune des sources saines n'a été redemandée")
    check(items != [], "le second essai a rattrapé les articles")
    check(info.get("raw_count") == 2, "c'est le résultat du second essai qui fait foi")
    check(any("HTTP 500" in l for l in journal),
          "le journal garde la trace du premier échec, sinon la panne rattrapée "
          "disparaîtrait des logs")
    check(any("seconde tentative" in l for l in journal),
          "et dit explicitement qu'il y a eu une reprise")
    check(etats_vus[capricieuse][1] is None,
          "la reprise part sans validateurs : on veut une réponse complète, "
          "pas un « rien n'a changé » portant sur un contenu qu'on n'a pas")

    # Une source qui échoue TOUJOURS ne doit pas déclencher de troisième essai.
    def toujours_cassee(feed, decoded_cache=None, http_state=None):
        appels[feed["id"]] += 1
        return [], {"raw_count": 0, "not_modified": False,
                    "http_status": 404, "not_a_feed": True}, [f"[{feed['name']}] ko"]

    appels.clear()
    fetch_feeds.REPRISE_PAUSE = 0
    try:
        fetch_feeds.fetch_all_feeds(sources[:2], {}, {}, collecte=toujours_cassee)
    finally:
        fetch_feeds.REPRISE_PAUSE = pause
    check(all(n == 2 for n in appels.values()),
          "une source durablement cassée est réessayée une fois, pas en boucle")


def test_reprise_choix_des_cas():
    print("\n== Ce qui mérite une reprise, et ce qui n'en mérite pas ==")
    import fetch_feeds

    merite = fetch_feeds.merite_reprise
    check(merite({"raw_count": 0, "http_status": 500}) is True,
          "500 : erreur du serveur, réessayable")
    check(merite({"raw_count": 0, "http_status": 404, "not_a_feed": True}) is True,
          "404 : réessayé quand même — les deux chaînes YouTube de Rockstar en ont "
          "renvoyé un le 30/08 entre 61 passages normaux et un retour à 200")
    check(merite({"raw_count": 0, "http_status": 403, "not_a_feed": True}) is True,
          "403 : blocage anti-robot, souvent intermittent")
    check(merite({"raw_count": 0, "injoignable": True}) is True,
          "panne réseau sans code HTTP : réessayable")
    check(merite({"raw_count": 0, "http_status": 200, "not_a_feed": True}) is True,
          "200 mais ce n'est pas un flux : page de blocage déguisée, réessayable")

    check(merite({"raw_count": 0, "not_modified": True}) is False,
          "304 : le serveur a répondu, rien à rattraper")
    check(merite({"raw_count": 12, "http_status": 200}) is False,
          "une source qui a rapporté des entrées n'est pas réessayée")
    check(merite({"raw_count": 0, "http_status": 301,
                  "redirect": "https://ailleurs.example/feed"}) is False,
          "une redirection obtenue sans validateurs renverra la même au second "
          "essai — c'est l'URL dans FEEDS qu'il faut corriger, et la réessayer "
          "masquerait le déménagement")

    # Le cas IGN. Une redirection obtenue AVEC validateurs n'est pas la même
    # mesure : la reprise part sans eux, donc ce n'est pas la même requête.
    # Sans cette nuance, IGN resterait cassée un passage sur deux — la règle
    # « une redirection ne se réessaie pas » l'aurait écartée pile dans le
    # cas où la reprise la répare.
    check(merite({"raw_count": 0, "http_status": 302, "conditionnelle": True,
                  "not_a_feed": True,
                  "redirect": "https://www.ign.com/rss/articles/feed"}) is True,
          "une redirection obtenue AVEC validateurs se réessaie : la reprise "
          "est inconditionnelle, ce n'est pas la même requête")
    check(merite({"raw_count": 0, "http_status": 200}) is False,
          "un flux valide mais vide n'est pas une panne : ne pas le redemander "
          "à chaque passage")
    check(merite({"raw_count": 0, "http_status": 302, "conditionnelle": True,
                  "redirect": "https://ailleurs.example/feed"}) is False,
          "et une redirection qui aboutit à un flux valide mais vide reste "
          "hors reprise, conditionnelle ou non")


def test_validateurs_lies_a_leur_url():
    print("\n== Les validateurs HTTP appartiennent à une URL, pas à une source ==")
    import fetch_feeds

    # Le piège : feed_http_state est indexé par identifiant de source. Quand
    # l'URL change (Kotaku /rss -> /feed le 30/08/2026), l'etag de l'ancienne
    # adresse serait envoyé à la nouvelle. Deux chemins d'un même site
    # partagent souvent le même backend : le serveur peut répondre 304, et le
    # robot noterait « inchangé » pour un flux qu'il n'a jamais lu.
    feed = {"id": "kotaku", "name": "Kotaku", "url": "https://kotaku.com/feed",
            "official": False}
    vus = {}

    def faux_parse(url, agent=None, etag=None, modified=None):
        vus["etag"] = etag
        vus["modified"] = modified
        return types.SimpleNamespace(status=200, bozo=False, entries=[],
                                     version="rss20", href=url,
                                     etag="neuf", modified="demain")

    vrai = fetch_feeds.feedparser.parse
    fetch_feeds.feedparser.parse = faux_parse
    try:
        perime = {"kotaku": {"url": "https://kotaku.com/rss",
                             "etag": "ancien", "modified": "hier"}}
        _, info, journal = fetch_feeds.collect_feed_items(feed, {}, perime)
        check(vus["etag"] is None and vus["modified"] is None,
              "un validateur obtenu pour une autre adresse n'est pas renvoyé")
        check(any("autre adresse" in l for l in journal),
              "et le journal dit pourquoi le flux est redemandé en entier")
        check(info.get("url") == feed["url"],
              "l'état enregistré retient l'adresse à laquelle il se rapporte")

        check(info.get("conditionnelle") is False,
              "et note que la requête est partie sans validateurs — c'est ce que "
              "la reprise lit pour décider si une redirection vaut un second essai")

        a_jour = {"kotaku": {"url": "https://kotaku.com/feed",
                             "etag": "bon", "modified": "hier"}}
        _, info, _ = fetch_feeds.collect_feed_items(feed, {}, a_jour)
        check(vus["etag"] == "bon",
              "quand l'adresse correspond, la requête conditionnelle est bien faite")
        check(info.get("conditionnelle") is True,
              "et la réponse est marquée comme obtenue avec validateurs")

        sans_url = {"kotaku": {"etag": "legs", "modified": "hier"}}
        fetch_feeds.collect_feed_items(feed, {}, sans_url)
        check(vus["etag"] is None,
              "un état enregistré avant ce champ est écarté : un téléchargement "
              "complet une fois vaut mieux qu'un 304 sur un contenu inconnu")
    finally:
        fetch_feeds.feedparser.parse = vrai


def test_miniature_youtube():
    print("\n[images] la vignette d'une vidéo YouTube, et pas le lecteur Flash")
    import fetch_feeds

    # Le contenu réel du flux Atom de YouTube, tel qu'il arrivait le
    # 30/08/2026 : media:content EXISTE mais c'est l'ancienne URL du
    # lecteur Flash, pas une image. Comme il était pris en premier sans
    # regarder ce qu'il annonçait, les 17 vidéos du fil enregistraient
    # cette adresse — et l'app, qui masque une image cassée, ne montrait
    # aucune vignette.
    class Entree:
        media_content = [{"url": "https://www.youtube.com/v/0H94XV8aPVY?version=3",
                          "medium": "video",
                          "type": "application/x-shockwave-flash"}]
        media_thumbnail = [{"url": "https://i.ytimg.com/vi/0H94XV8aPVY/hqdefault.jpg",
                            "width": "480", "height": "360"}]

    trouvee = fetch_feeds.image_du_flux(Entree())
    check(trouvee == "https://i.ytimg.com/vi/0H94XV8aPVY/hqdefault.jpg",
          "c'est media:thumbnail qui est retenu, pas le lecteur vidéo")
    check("youtube.com/v/" not in (trouvee or ""),
          "l'URL du lecteur Flash n'est plus jamais enregistrée comme image")

    # La ceinture : même sans media:thumbnail, l'adresse reste calculable.
    class SansVignette:
        media_content = Entree.media_content
        media_thumbnail = []

    check(fetch_feeds.image_du_flux(SansVignette()) is None,
          "sans vignette déclarée, le flux ne fournit rien plutôt qu'un faux")
    check(fetch_feeds.vignette_youtube("https://www.youtube.com/watch?v=0H94XV8aPVY")
          == "https://i.ytimg.com/vi/0H94XV8aPVY/hqdefault.jpg",
          "et la vignette se déduit du lien, sans appel réseau")
    for lien in ("https://youtu.be/0H94XV8aPVY",
                 "https://www.youtube.com/embed/0H94XV8aPVY",
                 "https://www.youtube.com/shorts/0H94XV8aPVY",
                 "https://www.youtube.com/watch?list=PL1&v=0H94XV8aPVY"):
        check(fetch_feeds.vignette_youtube(lien) is not None,
              f"identifiant reconnu dans {lien[:44]}")
    check(fetch_feeds.vignette_youtube("https://kotaku.com/un-article") is None,
          "et rien n'est inventé pour un lien qui n'est pas une vidéo")


def test_reparation_vignettes_stockees():
    print("\n[images] les vidéos déjà publiées récupèrent leur vignette")
    import fetch_feeds

    # Corriger la collecte ne suffit pas : un article déjà connu n'y
    # repasse jamais. Sans cette passe rétroactive, les 17 vidéos du fil
    # garderaient l'URL du lecteur Flash indéfiniment — c'est-à-dire que
    # rien n'aurait changé à l'écran, qui est le seul endroit qui compte.
    items = [
        {"link": "https://www.youtube.com/watch?v=TXsd53UiklU",
         "image": "https://www.youtube.com/v/TXsd53UiklU?version=3"},
        {"link": "https://kotaku.com/article",
         "image": "https://pic.clubic.com/v1/images/2178880/raw"},
        {"link": "https://gta6times.com/news/machin",
         "image": "https://gta6times.com/news/machin/opengraph-image"},
        {"link": "https://exemple.fr/a", "image": None},
    ]
    fetch_feeds.repare_vignettes_stockees(items)

    check(items[0]["image"] == "https://i.ytimg.com/vi/TXsd53UiklU/hqdefault.jpg",
          "la vidéo retrouve sa vraie miniature")
    check(items[1]["image"] == "https://pic.clubic.com/v1/images/2178880/raw",
          "une image de CDN sans extension n'est pas touchée")
    check(items[2]["image"] == "https://gta6times.com/news/machin/opengraph-image",
          "une route qui génère une image Open Graph non plus "
          "(c'est du Next.js, pas un défaut)")
    check(items[3]["image"] is None,
          "un article sans image reste sans image, le scraping s'en charge")

    # Idempotence : la passe tourne à chaque passage, elle ne doit pas
    # dériver au fil des exécutions.
    avant = [i["image"] for i in items]
    fetch_feeds.repare_vignettes_stockees(items)
    check([i["image"] for i in items] == avant,
          "rejouer la réparation ne change plus rien")


def test_media_content_non_declare_reste_accepte():
    print("\n[images] on n'écarte que ce qui s'annonce comme n'étant pas une image")
    import fetch_feeds

    # Le risque de la correction : beaucoup de CDN servent de vraies images
    # depuis des chemins sans extension et sans rien déclarer. Rejeter par
    # défaut ferait perdre des dizaines de vignettes valides — vérifié sur
    # les 1269 images publiées le 30/08/2026 (Clubic, Jerusalem Post,
    # Unsplash). On ne rejette donc que sur une déclaration explicite.
    def entree(media):
        return type("E", (), {"media_content": [media], "media_thumbnail": []})()

    cdn = "https://pic.clubic.com/v1/images/2178880/raw"
    check(fetch_feeds.image_du_flux(entree({"url": cdn})) == cdn,
          "un média sans medium ni type est gardé : c'est le cas des CDN")
    check(fetch_feeds.image_du_flux(entree({"url": cdn, "medium": "image"})) == cdn,
          "un média déclaré image est gardé")
    check(fetch_feeds.image_du_flux(entree({"url": cdn, "type": "image/jpeg"})) == cdn,
          "un type image/* aussi")
    check(fetch_feeds.image_du_flux(entree({"url": cdn, "medium": "video"})) is None,
          "un medium video est écarté")
    check(fetch_feeds.image_du_flux(entree({"url": cdn, "type": "video/mp4"})) is None,
          "un type video/* aussi")
    check(fetch_feeds.image_du_flux(entree({"url": cdn, "type": "audio/mpeg"})) is None,
          "et un podcast n'est pas une vignette")

    # Une entrée sans aucun média ne doit pas casser : c'est le cas le plus
    # fréquent, et le scraping og:image prend le relais plus tard.
    check(fetch_feeds.image_du_flux(type("E", (), {})()) is None,
          "une entrée sans média ne lève pas d'erreur")


def test_sonde_decouvre_les_flux_declares():
    print("\n[sonde] quand ce n'est pas un flux, demander à la page où est le sien")
    import fetch_feeds

    # Essayer des adresses au hasard n'apprend rien sur un site qui répond
    # 500 pour tout chemin inconnu — mesuré le 30/08/2026 sur
    # rockstargames.com, où une adresse inventée de toutes pièces renvoyait
    # 500 comme les six candidates. La page, elle, déclare ses flux.
    html = """<html><head>
      <link rel="alternate" type="application/rss+xml" href="/newswire/feed.rss">
      <link rel="alternate" type="application/atom+xml" href="https://ailleurs.fr/atom">
      <link rel="alternate" type="text/html" href="/version-imprimable">
      <link rel="stylesheet" href="/style.css">
    </head><body>rien</body></html>"""

    class Reponse:
        text = html

    vrai = fetch_feeds.requests.get
    fetch_feeds.requests.get = lambda *a, **k: Reponse()
    try:
        trouves = fetch_feeds.flux_declares("https://exemple.fr/newswire")
    finally:
        fetch_feeds.requests.get = vrai

    check("https://exemple.fr/newswire/feed.rss" in trouves,
          "une adresse relative est résolue contre celle de la page")
    check("https://ailleurs.fr/atom" in trouves,
          "une adresse absolue est gardée telle quelle")
    check(not any("imprimable" in t for t in trouves),
          "un rel=alternate qui n'est pas un flux est ignoré")
    check(not any("style" in t for t in trouves),
          "et une feuille de style n'est pas un flux non plus")

    # Une page illisible ne doit pas faire exploser la sonde : elle est là
    # pour diagnostiquer, pas pour ajouter une panne de plus.
    def boum(*a, **k):
        raise RuntimeError("réseau coupé")
    fetch_feeds.requests.get = boum
    try:
        check(fetch_feeds.flux_declares("https://exemple.fr/x") == [],
              "une page injoignable renvoie une liste vide, sans lever")
    finally:
        fetch_feeds.requests.get = vrai


def test_titres_numerotes_pas_fusionnes():
    print("\n[doublons] « Trailer 1 » et « Trailer 2 » sont deux vidéos")
    import fetch_feeds

    # 0,966 de similarité pour un chiffre d'écart : très au-dessus du seuil
    # de 0,75, alors que ce sont deux vidéos séparées d'un an et demi.
    # Constaté en versant les bandes-annonces dans l'historique — le
    # Trailer 2 disparaissait, absorbé par le Trailer 1, et se retrouvait
    # crédité comme « autre source » de celui-ci. Le Trailer 3 sortira
    # avant novembre.
    t1 = "Grand Theft Auto VI Trailer 1"
    t2 = "Grand Theft Auto VI Trailer 2"
    check(fetch_feeds.title_similarity(t1, t2) >= fetch_feeds.SIMILARITY_THRESHOLD,
          "les deux titres passent le seuil de similarité — d'où le piège")
    check(fetch_feeds.titres_dune_meme_serie(t1, t2),
          "mais ils sont reconnus comme deux numéros d'une même série")

    for a, b in (("Extended Look Part 3", "Extended Look Part 4"),
                 ("GTA 6 sort en 2026", "GTA 6 sort en 2027"),
                 ("GTA 6 repoussé", "GTA 5 repoussé")):
        check(fetch_feeds.titres_dune_meme_serie(a, b),
              f"« {a} » et « {b} » ne sont pas le même sujet")

    # Et l'inverse : le garde-fou ne doit pas empêcher les vraies fusions.
    for a, b in (("GTA 6 Trailer 2", "GTA 6 Trailer 2"),
                 ("GTA 6 : la map dévoilée", "GTA 6 : la map devoilee"),
                 ("GTA 6 arrive", "GTA 6 arrive enfin")):
        check(not fetch_feeds.titres_dune_meme_serie(a, b),
              f"« {a} » et « {b} » restent fusionnables")

    # Le garde-fou agit bien DANS find_duplicate, pas seulement en théorie.
    existant = [{"title": t1, "link": "https://youtu.be/aaaaaaaaaaa"}]
    item = {"title": t2, "link": "https://youtu.be/bbbbbbbbbbb"}
    check(fetch_feeds.find_duplicate(item, existant, {}, existant, {}) is None,
          "le Trailer 2 n'est plus absorbé par le Trailer 1")
    meme = {"title": t1, "link": "https://youtu.be/ccccccccccc"}
    check(fetch_feeds.find_duplicate(meme, existant, {}, existant, {}) is not None,
          "mais deux fois le même titre restent bien un doublon")


def test_description_video_youtube():
    print("\n[archive] lire titre et date d'une vidéo sans clé d'API")
    import fetch_feeds

    class FausseReponse:
        def __init__(self, texte="", data=None, code=200):
            self.text = texte
            self._data = data
            self.status_code = code
            self.ok = code == 200

        def json(self):
            return self._data

    vrai = fetch_feeds.requests.get
    try:
        page = {"html": ""}

        def faux_get(url, **kw):
            if "oembed" in url:
                return FausseReponse(data={"title": "Grand Theft Auto VI Trailer 2"})
            return FausseReponse(texte=page["html"])

        fetch_feeds.requests.get = faux_get

        check(fetch_feeds.decris_video_youtube("pas une url") is None,
              "un lien qui n'est pas une vidéo est refusé")

        # Cinq écritures possibles : la page d'une vidéo ne rend pas le même
        # balisage selon qu'elle sert une fiche complète ou une version
        # allégée. Le premier essai n'avait trouvé la date que sur 1 vidéo
        # sur 5 — chercher une seule forme ne suffit pas.
        for balise in ('<meta itemprop="datePublished" content="2025-05-06">',
                       '<meta itemprop="uploadDate" content="2025-05-06">',
                       '"datePublished":"2025-05-06"',
                       '"uploadDate":"2025-05-06"',
                       '"publishDate":"2025-05-06"'):
            page["html"] = balise
            infos = fetch_feeds.decris_video_youtube(
                "https://youtu.be/VQRLujxTm3c")
            check(infos["date"] == "2025-05-06",
                  f"date lue dans {balise[:34]}…")

        check(infos["id"] == "VQRLujxTm3c", "l'identifiant est extrait du lien")
        check(infos["title"] == "Grand Theft Auto VI Trailer 2",
              "le titre vient d'oEmbed")
        # image attend une URL, pas l'identifiant nu : lui passer `vid`
        # renvoyait None sans broncher.
        check(infos["image"] and "VQRLujxTm3c" in infos["image"],
              "la miniature est déduite du lien complet")

        # LE piège : `publishedTimeText` porte un texte relatif, et peut
        # appartenir à une vidéo recommandée dans la marge. Le 30/08/2026 il
        # a rendu « 2 days ago » pour le Trailer 2, sorti en 2025. Une date
        # fausse est PIRE que pas de date : elle range la vidéo au mauvais
        # endroit du fil et plus rien ne vient la corriger.
        page["html"] = '"publishedTimeText":{"simpleText":"2 days ago"}'
        infos = fetch_feeds.decris_video_youtube("https://youtu.be/VQRLujxTm3c")
        check(infos["date"] is None,
              "une date relative est refusée, jamais repêchée")

        # Réseau en panne : on rend ce qu'on a, on ne lève pas.
        def get_qui_explose(url, **kw):
            raise RuntimeError("réseau coupé")

        fetch_feeds.requests.get = get_qui_explose
        infos = fetch_feeds.decris_video_youtube("https://youtu.be/VQRLujxTm3c")
        check(infos and infos["id"] == "VQRLujxTm3c" and infos["date"] is None,
              "réseau coupé : la fonction rend l'identifiant sans planter")
    finally:
        fetch_feeds.requests.get = vrai


def test_miniatures_seulement_sur_les_nouveaux():
    print("\n[miniatures] la recherche ne porte que sur les articles du passage")
    html = open("fetch_feeds.py", encoding="utf-8").read()

    # LE fait que j'avais supposé au lieu de le lire. Voyant 76 articles sans
    # miniature dans le fil, j'ai déduit qu'ils étaient redemandés à chaque
    # passage — 3 648 requêtes par jour — et livré un garde-fou contre une
    # répétition qui n'existait pas. Le journal disait « 1 article(s) ».
    #
    # La fonction ne reçoit que newly_added : un article n'y passe qu'une
    # fois dans sa vie. Verrouillé ici pour que la même erreur ne se refasse
    # pas, dans un sens ou dans l'autre.
    check("fetch_missing_images(newly_added)" in html,
          "la recherche de miniatures ne porte que sur les nouveaux articles")
    check(html.count("fetch_missing_images(") == 2,
          "et elle n'est appelée qu'à cet endroit — définition comprise")

    # Le garde-fou retiré ne doit pas revenir sans preuve : son abandon après
    # 7 jours privait de miniature les archives, qui arrivent justement avec
    # une date ancienne.
    for mort in ("og_absente", "OG_ABANDON_JOURS", "merite_une_miniature"):
        check(mort not in html, f"le garde-fou inutile n'est pas revenu ({mort})")


def test_recuperation_des_miniatures():
    print("\n[miniatures] la récupération en parallèle")
    import fetch_feeds

    vrai = fetch_feeds.fetch_og_image
    try:
        appels = []

        def faux(url, timeout=8):
            appels.append(url)
            return None if "muet" in url else f"{url}/og.jpg"

        fetch_feeds.fetch_og_image = faux

        # On ne va chercher que ce qui manque : re-télécharger une image
        # déjà connue coûterait une requête par article à chaque passage.
        items = [
            {"title": "A", "link": "https://a.tld/1", "image": "deja.jpg"},
            {"title": "B", "link": "https://b.tld/2", "image": None},
            {"title": "C", "link": "https://muet.tld/3", "image": ""},
            {"title": "D", "link": None, "image": None},
        ]
        fetch_feeds.fetch_missing_images(items)
        check(sorted(appels) == ["https://b.tld/2", "https://muet.tld/3"],
              "on n'interroge que les articles sans image ET avec un lien")
        check(items[0]["image"] == "deja.jpg", "une image déjà là n'est pas touchée")
        check(items[1]["image"] == "https://b.tld/2/og.jpg", "celle trouvée est posée")
        check(not items[2].get("image"),
              "une page sans og:image laisse l'article sans image, sans planter")
        check(items[3].get("image") is None, "un article sans lien est ignoré")

        appels.clear()
        fetch_feeds.fetch_missing_images([{"title": "E", "link": "x", "image": "y"}])
        check(appels == [], "rien à chercher → aucune requête")

        # Une page qui explose ne doit pas emporter le passage entier : les
        # autres miniatures doivent quand même arriver.
        def explose(url, timeout=8):
            if "boom" in url:
                raise RuntimeError("page cassée")
            return f"{url}/og.jpg"

        fetch_feeds.fetch_og_image = explose
        mixte = [{"title": "F", "link": "https://boom.tld/1", "image": None},
                 {"title": "G", "link": "https://ok.tld/2", "image": None}]
        fetch_feeds.fetch_missing_images(mixte)
        check(mixte[1]["image"] == "https://ok.tld/2/og.jpg",
              "une page qui plante n'empêche pas les autres d'aboutir")
    finally:
        fetch_feeds.fetch_og_image = vrai


def test_fenetre_en_heures():
    print("\n[doublons] la fenêtre se compte en heures, pas en articles")
    import fetch_feeds
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

    def art(heures, n):
        return {"title": f"Article {n}", "link": f"https://ex.tld/{n}",
                "date": (base - timedelta(hours=heures)).isoformat()}

    # Comptée en articles, la fenêtre se refermait exactement quand il aurait
    # fallu qu'elle s'ouvre : 200 articles valaient 50 h en régime normal
    # mais 16 h le 27/08/2026, jour à 293 articles. Le robot voyait donc le
    # moins loin quand il se passait quelque chose.
    dense = [art(h * 0.05, n) for n, h in enumerate(range(600))]  # 30 h serrées
    fen = fetch_feeds.fenetre_recente(dense, heures=24)
    check(all(f in dense for f in fen), "la fenêtre ne contient que des articles du fil")
    plus_vieux = min(fetch_feeds.feed_store.parse_date_key(f["date"]) for f in fen)
    check((base - plus_vieux).total_seconds() / 3600 <= 24.01,
          "aucun article plus vieux que la durée demandée")

    # Plancher : trois jours creux ne doivent pas réduire la fenêtre à rien.
    # Sans lui, une accalmie rendrait la déduplication myope.
    creux = [art(h * 12, n) for n, h in enumerate(range(400))]
    fen = fetch_feeds.fenetre_recente(creux, heures=24)
    check(len(fen) == fetch_feeds.TITLE_SIMILARITY_WINDOW,
          f"sur un fil calme, on compare quand même aux "
          f"{fetch_feeds.TITLE_SIMILARITY_WINDOW} derniers")

    # Plafond : rien n'interdit à un événement futur de produire des
    # milliers d'articles en trois jours ; le coût doit rester borné.
    deluge = [art(h * 0.001, n) for n, h in enumerate(range(3000))]
    check(len(fetch_feeds.fenetre_recente(deluge, heures=72))
          == fetch_feeds.FENETRE_MAX,
          f"et jamais plus de {fetch_feeds.FENETRE_MAX}, quel que soit le pic")

    check(fetch_feeds.fenetre_recente([]) == [], "un fil vide ne casse rien")

    # Le tri est refait dans la fonction : lui passer une liste en désordre
    # ne doit pas lui faire prendre les mauvais articles.
    desordre = [art(200, 0), art(1, 1), art(100, 2), art(2, 3)]
    fen = fetch_feeds.fenetre_recente(desordre, heures=24)
    check(fen[0]["link"].endswith("/1"),
          "la fenêtre trie elle-même, elle ne suppose pas l'ordre")


def test_source_renommee():
    print("\n[données] une source débaptisée retrouve son nom")
    import fetch_feeds

    # Neuf articles portaient « RockstarMag.fr » au 30/08/2026, un nom
    # absent de FEEDS : ils ne comptaient plus pour la santé de leur source
    # et l'audit les signalait sans fin.
    ancien, (nouveau, domaine) = next(iter(fetch_feeds.SOURCES_RENOMMEES.items()))
    items = [{"title": "A", "source": ancien,
              "link": f"https://www.{domaine}/gta6-article"}]
    fetch_feeds.repare_noms_de_sources(items)
    check(items[0]["source"] == nouveau,
          f"« {ancien} » devient « {nouveau} »")

    # Le domaine doit confirmer : ce qui compte est QUI PUBLIE. Un article
    # d'ailleurs qui porterait ce nom par accident n'est pas rebranché.
    ailleurs = [{"title": "B", "source": ancien, "link": "https://autre.tld/x"}]
    fetch_feeds.repare_noms_de_sources(ailleurs)
    check(ailleurs[0]["source"] == ancien,
          "un lien sur un autre domaine n'est pas rebranché")

    intact = [{"title": "C", "source": "IGN", "link": "https://ign.com/x"}]
    fetch_feeds.repare_noms_de_sources(intact)
    check(intact[0]["source"] == "IGN", "les autres sources ne bougent pas")

    # Idempotente, comme toutes les réparations rétroactives.
    fetch_feeds.repare_noms_de_sources(items)
    check(items[0]["source"] == nouveau, "un second passage ne change rien")


def test_titre_trop_court_n_attire_personne():
    print("\n[doublons] un titre réduit à deux mots ne fusionne plus")
    import fetch_feeds

    avant = fetch_feeds._SUFFIXES_MEDIAS
    try:
        fetch_feeds.memorise_suffixes_medias([])
        # Une fois le nom du jeu retiré, « extended look gta 6 - GamerGen »
        # ne pèse plus que « extended look » — soit exactement la page
        # officielle de Rockstar nettoyée. 1,00 de similarité pour deux
        # pages différentes : l'aimant sous une autre forme.
        court = [
            {"title": "Grand Theft Auto VI: An Extended Look",
             "source": "Rockstar Games (officiel EN)", "date": "2026-08-06T10:00:00+00:00",
             "link": "https://www.rockstargames.com/newswire/extended-look"},
            {"title": "extended look gta 6", "source": "Gamergen",
             "date": "2026-08-23T10:00:00+00:00", "link": "https://gamergen.tld/a"},
        ]
        check(len(fetch_feeds.fusionne_ressemblances_de_titre(list(court))) == 2,
              "deux mots génériques ne suffisent pas à confondre deux pages")

        # Mesuré : le minimum de 3 mots écarte ce cas-là et AUCUN autre. La
        # plus courte des fusions légitimes de l'historique en compte 3.
        trois = [
            {"title": "New Grand Theft Auto 6 Screenshots Revealed",
             "source": "VGTimes", "date": "2026-08-27T10:00:00+00:00",
             "link": "https://vgtimes.tld/a"},
            {"title": "20+ New GTA 6 Screenshots Released",
             "source": "RockstarINTEL", "date": "2026-08-27T12:00:00+00:00",
             "link": "https://rockstarintel.tld/b"},
        ]
        check(len(fetch_feeds.fusionne_ressemblances_de_titre(list(trois))) == 1,
              "trois mots suffisent, eux — la fusion légitime survit")
    finally:
        fetch_feeds._SUFFIXES_MEDIAS = avant


def test_audit_signale_la_croissance():
    print("\n[audit] l'échéance du plafond est visible, pas à découvrir")
    import audit_donnees
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc)
    items = [{"title": f"T{n}", "link": f"https://ex.tld/{n}",
              "date": (base - timedelta(days=n // 40)).isoformat()}
             for n in range(400)]
    codes = {a["code"]: a for a in audit_donnees.audite({"items": items})}
    check("croissance" in codes, "l'audit rend compte de la croissance")
    croissance = codes["croissance"]
    check(croissance["gravite"] == "info",
          "en info : c'est une échéance à voir venir, pas une anomalie")
    texte = " ".join(croissance["exemples"])
    check("o/article" in texte, "il donne le poids par article")
    check("plafond" in texte, "et la date d'échéance du plafond")

    # Un fil vide ne doit pas produire de division par zéro.
    check(all(a["code"] != "croissance" for a in audit_donnees.audite({"items": []})),
          "un fil vide ne déclenche aucun calcul de croissance")


def test_filtre_par_mots_cles():
    print("\n[filtre] la règle qui décide ce qui entre au fil")
    import fetch_feeds

    # C'est LA règle métier du robot : elle décide, article par article, de
    # ce qui atterrit sur le téléphone. Elle n'avait aucun test direct.

    check(fetch_feeds.matches_keywords("Le GTA 6 arrive", ["gta 6"]),
          "un mot-clé présent est reconnu")
    check(fetch_feeds.matches_keywords("LE GTA 6 ARRIVE", ["gta 6"]),
          "la casse n'a pas d'importance")
    check(not fetch_feeds.matches_keywords("Le nouveau Zelda", ["gta 6"]),
          "un texte hors sujet est refusé")
    check(not fetch_feeds.matches_keywords("GTA 6", []),
          "une liste vide ne laisse rien passer — jamais de tout-venant")
    # La recherche est une sous-chaîne, pas un mot entier : c'est voulu,
    # « gta6news » doit matcher « gta6 ». Le noter pour que personne ne
    # « corrige » ça un jour en croyant à un oubli.
    check(fetch_feeds.matches_keywords("voir gta6news.com", ["gta6"]),
          "la recherche porte sur la sous-chaîne, volontairement")

    # Les trois chemins de passe_le_filtre, un par type de source.
    normale = {"id": "x", "name": "X"}
    officielle = {"id": "o", "name": "O", "official": True}
    rockstarmag = {"id": "r", "name": "R", "no_filter_at_all": True}

    check(fetch_feeds.passe_le_filtre(rockstarmag, "Un tuto FiveM", ""),
          "RockstarMag passe tout — seule source sans filtre, choix explicite")

    check(fetch_feeds.passe_le_filtre(officielle, "Grand Theft Auto VI Trailer 2", ""),
          "une source officielle retient un titre qui nomme le jeu")
    check(not fetch_feeds.passe_le_filtre(officielle, "Red Dead Online update", ""),
          "et refuse un titre qui parle d'un autre jeu")
    # Le filtre officiel ne lit QUE le titre : un flux officiel est une
    # recherche Google News, sa description charrie n'importe quoi.
    check(not fetch_feeds.passe_le_filtre(officielle, "Nouveautés du mois",
                                          "on y parle aussi de GTA 6"),
          "le filtre officiel ignore la description, volontairement")

    # Une source normale lit titre ET description : beaucoup de flux
    # résument dans la description ce que le titre laisse deviner.
    check(fetch_feeds.passe_le_filtre(normale, "Le jeu le plus attendu",
                                      "Rockstar prépare GTA 6 pour novembre"),
          "une source normale accepte un mot-clé trouvé dans la description")
    check(not fetch_feeds.passe_le_filtre(normale, "Test du dernier Mario",
                                          "un excellent jeu de plateforme"),
          "et refuse ce qui ne parle pas du jeu")

    # Un supplément déclaré par la source s'AJOUTE aux six mots de base,
    # il ne les remplace pas.
    avec_extra = {"id": "o2", "name": "O2", "official": True,
                  "official_keywords_extra": ["leonida"]}
    check(fetch_feeds.passe_le_filtre(avec_extra, "Bienvenue en Leonida", ""),
          "le supplément de mots-clés d'une source est pris en compte")
    check(fetch_feeds.passe_le_filtre(avec_extra, "Grand Theft Auto 6", ""),
          "sans faire perdre les mots-clés de base")

    # Les 139 mots-clés doivent rester exploitables : aucun vide, aucune
    # majuscule (la comparaison se fait en minuscules), aucun doublon.
    mots = fetch_feeds.KEYWORDS
    check(all(m and m == m.lower().strip() for m in mots),
          f"les {len(mots)} mots-clés sont en minuscules, sans espace superflu")
    check(len(set(mots)) == len(mots), "et aucun n'est en double")
    check(all(m in mots for m in fetch_feeds.OFFICIAL_KEYWORDS),
          "les mots-clés officiels sont tous dans la liste générale")


def test_jours_depuis():
    print("\n[dates] l'âge d'un article")
    import fetch_feeds
    from datetime import datetime, timedelta, timezone

    check(fetch_feeds.jours_depuis(None) is None, "pas de date → pas d'âge")
    check(fetch_feeds.jours_depuis("") is None, "date vide → pas d'âge")
    # Une date illisible tombe sur DATE_FLOOR ; la rendre « vieille de
    # 700 000 jours » ferait passer la source pour morte.
    check(fetch_feeds.jours_depuis("pas une date") is None,
          "date illisible → pas d'âge, surtout pas un âge géant")
    hier = (datetime.now(timezone.utc) - timedelta(days=1, hours=1)).isoformat()
    check(fetch_feeds.jours_depuis(hier) == 1, "hier vaut 1 jour")


def test_depots_pour_les_notifications():
    print("\n[notifications] les fichiers déposés pour l'étape suivante")
    import fetch_feeds, json, os, tempfile

    dossier = tempfile.mkdtemp()
    alertes_src = fetch_feeds.SOURCE_ALERTS_FILE
    promus_src = fetch_feeds.PROMOTED_ITEMS_FILE
    try:
        # Rien à dire = aucun fichier. C'est l'étape suivante du workflow qui
        # décide de notifier ou non selon la présence du fichier : en écrire
        # un vide enverrait une notification pour rien.
        fetch_feeds.SOURCE_ALERTS_FILE = os.path.join(dossier, "a.json")
        fetch_feeds.PROMOTED_ITEMS_FILE = os.path.join(dossier, "p.json")
        fetch_feeds.write_source_alerts_file([])
        fetch_feeds.write_promoted_items_file([])
        check(not os.path.exists(fetch_feeds.SOURCE_ALERTS_FILE),
              "aucune alerte → aucun fichier déposé")
        check(not os.path.exists(fetch_feeds.PROMOTED_ITEMS_FILE),
              "aucune promotion → aucun fichier déposé")

        fetch_feeds.write_source_alerts_file([{"source": "X", "raison": "muette"}])
        with open(fetch_feeds.SOURCE_ALERTS_FILE, encoding="utf-8") as f:
            check(json.load(f)[0]["source"] == "X", "l'alerte déposée est relisible")

        fetch_feeds.write_promoted_items_file([{"title": "Ç a chauffe", "link": "u"}])
        with open(fetch_feeds.PROMOTED_ITEMS_FILE, encoding="utf-8") as f:
            check(json.load(f)[0]["title"] == "Ç a chauffe",
                  "les accents survivent au dépôt (ensure_ascii=False)")

        # Sans chemin configuré — le cas d'un lancement à la main — on
        # n'écrit nulle part et surtout on ne plante pas.
        fetch_feeds.SOURCE_ALERTS_FILE = None
        fetch_feeds.PROMOTED_ITEMS_FILE = None
        fetch_feeds.write_source_alerts_file([{"source": "X"}])
        fetch_feeds.write_promoted_items_file([{"title": "T"}])
        check(True, "sans chemin configuré, aucun dépôt et aucune erreur")
    finally:
        fetch_feeds.SOURCE_ALERTS_FILE = alertes_src
        fetch_feeds.PROMOTED_ITEMS_FILE = promus_src


def test_index_compte_les_sources_supplementaires():
    print("\n[doublons] un article fusionné ne peut pas rentrer une 2e fois")
    import fetch_feeds

    # Un article fusionné ne figure plus au fil sous son propre lien : il
    # n'y survit que comme source supplémentaire. Sans lui dans l'index, son
    # flux le rapporte au passage suivant et il RENTRE une seconde fois —
    # la fusion défaite, et une notification pour un article déjà lu.
    #
    # Constaté le 30/08/2026, juste après le premier rejeu de l'historique :
    # 5 des 21 articles fusionnés étaient revenus dans le même passage. La
    # 3e passe de find_duplicate ne rattrape pas le cas : elle ne compare
    # qu'aux 200 articles les plus récents, et le gardien peut être plus
    # vieux que ça.
    gardien = {"title": "Le gardien", "link": "https://a.tld/1",
               "source": "A",
               "extraSources": [{"source": "B", "link": "https://b.tld/2"}]}
    index = fetch_feeds.index_des_liens([gardien])
    check(index.get("https://b.tld/2") is gardien,
          "le lien d'une source supplémentaire mène à l'article qui la porte")

    revenant = {"title": "Un titre sans rapport", "link": "https://b.tld/2"}
    check(fetch_feeds.find_duplicate(revenant, [gardien], index, [], {}) is gardien,
          "le revenant est reconnu, même si son titre ne ressemble à rien")

    # Le lien principal l'emporte : un article présent en propre reste son
    # propre représentant, jamais celui d'un autre.
    propre = {"title": "B chez lui", "link": "https://b.tld/2", "source": "B"}
    index = fetch_feeds.index_des_liens([gardien, propre])
    check(index["https://b.tld/2"] is propre,
          "un article présent en propre reste son propre représentant")


def test_fusion_retroactive_des_ressemblances():
    print("\n[doublons] rejeu de la ressemblance sur l'historique")
    import fetch_feeds

    avant = fetch_feeds._SUFFIXES_MEDIAS
    try:
        fetch_feeds.memorise_suffixes_medias([])

        def art(titre, source, date, lien=None):
            return {"title": titre, "source": source, "date": date,
                    "link": lien or f"https://ex.com/{abs(hash(titre)) % 10**8}"}

        # Le cas que la fenêtre glissante laissait passer : deux rédactions
        # qui titrent la même actu, séparées dans l'historique.
        base = [art("GTA 6 Map Is 3X Bigger Than Red Dead Redemption 2's",
                    "GameSpot", "2026-08-27T10:00:00+00:00"),
                art("GTA 6 Map Is Three Times Bigger Than Red Dead Redemption 2",
                    "VGC", "2026-08-28T10:00:00+00:00")]
        sortie = fetch_feeds.fusionne_ressemblances_de_titre(list(base))
        check(len(sortie) == 1, "les deux reprises ne font plus qu'une carte")
        check(sortie[0]["source"] == "GameSpot",
              "le plus ancien est gardé — c'est lui qui situe l'actualité")
        check([a["source"] for a in sortie[0]["extraSources"]] == ["VGC"],
              "et l'autre est créditée en source supplémentaire, rien n'est perdu")

        # Idempotence : le passage suivant ne doit RIEN refaire, sinon le
        # robot grignote l'historique 48 fois par jour.
        check(len(fetch_feeds.fusionne_ressemblances_de_titre(list(sortie))) == 1,
              "un second passage ne fusionne rien de plus")

        # Jamais deux fois la même source : une rédaction ne republie pas le
        # même article, elle publie une suite. Cas réel de RockstarMag.
        suite = [art("GTA 6 : UN LARGE APERÇU - ON DÉCOUVRE CELA ENSEMBLE !",
                     "RockstarMag (YouTube)", "2026-08-27T10:00:00+00:00"),
                 art("GTA 6 : UN LARGE APERÇU - ON DÉCOUVRE CELA ENSEMBLE ! (SUITE)",
                     "RockstarMag (YouTube)", "2026-08-27T12:00:00+00:00")]
        check(len(fetch_feeds.fusionne_ressemblances_de_titre(list(suite))) == 2,
              "« (SUITE) » de la même chaîne reste une carte à part")

        # Le contre-exemple qui servait d'argument pour ne rien faire.
        gtaboom = [art("Our GTA 6 Extended Look Predictions",
                       "GTA BOOM", "2026-08-27T08:00:00+00:00"),
                   art("How Our GTA 6 Extended Look Predictions Held Up",
                       "GTA BOOM", "2026-08-27T20:00:00+00:00")]
        check(len(fetch_feeds.fusionne_ressemblances_de_titre(list(gtaboom))) == 2,
              "« Predictions » et « Predictions Held Up » restent séparés")

        # Une vidéo et un article ne sont pas le même contenu : l'un se
        # regarde, l'autre se lit. Sans cette règle, l'annonce du 6 août
        # avalait la vidéo du 27 et la carte perdait sa miniature.
        video = [art("Grand Theft Auto VI: An Extended Look",
                     "Rockstar Games (officiel EN)", "2026-08-06T10:00:00+00:00",
                     "https://www.rockstargames.com/newswire/extended-look"),
                 art("Grand Theft Auto VI: An Extended Look",
                     "Rockstar Games (YouTube)", "2026-08-27T10:00:00+00:00",
                     "https://www.youtube.com/watch?v=tJbzMqJGH4k")]
        check(len(fetch_feeds.fusionne_ressemblances_de_titre(list(video))) == 2,
              "la vidéo ne disparaît pas derrière la page qui l'annonce")

        # Pas de chaînage : chaque membre doit ressembler au GARDIEN, pas
        # seulement à celui qui l'a attiré. Sans ça, la transitivité
        # ressoudait le Trailer 1 et le Trailer 2 par le milieu.
        chaine = [art("Grand Theft Auto VI Trailer 1", "A", "2023-12-05T10:00:00+00:00"),
                  art("Grand Theft Auto VI Trailer 1 en ligne", "B", "2023-12-06T10:00:00+00:00"),
                  art("Grand Theft Auto VI Trailer 2", "C", "2025-05-06T10:00:00+00:00")]
        sortie = fetch_feeds.fusionne_ressemblances_de_titre(list(chaine))
        restants = {i["title"] for i in sortie}
        check("Grand Theft Auto VI Trailer 2" in restants,
              "le Trailer 2 n'est pas absorbé par ricochet")
    finally:
        fetch_feeds._SUFFIXES_MEDIAS = avant


def test_suffixe_du_media_appris():
    print("\n[doublons] le « - Nom du média » final ne sépare plus")
    import fetch_feeds

    S = fetch_feeds.SIMILARITY_THRESHOLD
    avant = fetch_feeds._SUFFIXES_MEDIAS
    try:
        # 60 % des titres du fil finissent par le nom de leur média. On ne
        # coupe pas à l'aveugle : couper TOUT suffixe court avait fait
        # PERDRE 2 fusions justes sur les 500 derniers titres, en mangeant
        # la vraie fin d'un titre. On n'apprend donc que ce qui REVIENT.
        historique = (
            [{"title": f"Sujet {n} - Kotaku"} for n in range(3)]
            + [{"title": f"Autre {n} - Rockstar Games"} for n in range(3)]
            + [{"title": "GTA 6 : UN LARGE APERÇU - ON DÉCOUVRE CELA ENSEMBLE !"}]
            + [{"title": "Une exclu - British GQ"}]
        )
        appris = fetch_feeds.memorise_suffixes_medias(historique)
        check("kotaku" in appris, "un suffixe vu 3 fois est un nom de média")
        check("rockstar games" in appris, "celui du studio aussi")
        check("british gq" not in appris,
              "un suffixe vu une seule fois n'est pas retenu")
        check("on découvre cela ensemble" not in appris
              and "on decouvre cela ensemble" not in appris,
              "la vraie fin d'un titre unique n'est pas prise pour un média")

        # Ce qu'on gagne : quatre reprises du même article étaient affichées
        # séparément le 30/08/2026 alors que seul le suffixe les distinguait.
        check(fetch_feeds.sans_suffixe_media("Sujet 9 - Kotaku") == "Sujet 9",
              "le suffixe connu est retiré avant comparaison")
        check(fetch_feeds.sans_suffixe_media(
                  "GTA 6 : UN LARGE APERÇU - ON DÉCOUVRE CELA ENSEMBLE !")
              == "GTA 6 : UN LARGE APERÇU - ON DÉCOUVRE CELA ENSEMBLE !",
              "un suffixe inconnu laisse le titre entier")

        fetch_feeds.memorise_suffixes_medias(
            [{"title": f"Sujet {n} - Push Square"} for n in range(3)]
            + [{"title": f"Divers {n} - GamesRadar"} for n in range(3)])
        check(fetch_feeds.title_similarity(
                  "GTA 6 Contains No Microtransactions or Generative AI, "
                  "Rockstar Says - Push Square",
                  "GTA 6 Contains No Microtransactions or Generative AI, "
                  "Rockstar Says - GamesRadar") == 1.0,
              "le même titre chez deux médias se rejoint enfin")

        # Vice est un média (vice.com), mais Vice City est la ville du jeu :
        # un titre qui finit par la ville doit rester entier.
        protege = fetch_feeds.apprend_suffixes_medias(
            [{"title": f"Balade {n} - Vice City"} for n in range(6)])
        check("vice city" not in protege,
              "« Vice City » n'est jamais pris pour un nom de média")

        # Sans historique appris, rien n'est coupé : le comportement d'avant.
        fetch_feeds.memorise_suffixes_medias([])
        check(fetch_feeds.sans_suffixe_media("Sujet 9 - Kotaku")
              == "Sujet 9 - Kotaku",
              "sans liste apprise on ne coupe rien")

        # Le garde-fou des numéros doit lire le titre NETTOYÉ, comme la
        # similarité. Sinon « Trailer 1 » et « Trailer 2 - Rockstar Games »
        # ne sont pas vus comme une même série — leurs formes sans chiffres
        # diffèrent par le suffixe — et se retrouvent à 0,889 sans filet.
        # Le Trailer 3 sort avant novembre : il serait absorbé.
        fetch_feeds.memorise_suffixes_medias(
            [{"title": f"X {n} - Rockstar Games"} for n in range(3)])
        for a, b in (("Grand Theft Auto VI Trailer 1 - Rockstar Games",
                      "Grand Theft Auto VI Trailer 2 - Rockstar Games"),
                     ("Grand Theft Auto VI Trailer 1",
                      "Grand Theft Auto VI Trailer 2 - Rockstar Games"),
                     ("Grand Theft Auto VI Trailer 2",
                      "Grand Theft Auto VI Trailer 3 - Rockstar Games")):
            check(fetch_feeds.titres_dune_meme_serie(a, b),
                  f"« {a[:34]} » et « {b[:34]} » restent séparés")
        check(not fetch_feeds.titres_dune_meme_serie(
                  "Grand Theft Auto VI Trailer 1 - Rockstar Games",
                  "Grand Theft Auto VI Trailer 1"),
              "mais la MÊME vidéo avec et sans suffixe reste fusionnable")
        _ = S
    finally:
        fetch_feeds._SUFFIXES_MEDIAS = avant


def test_similarite_ignore_le_nom_du_jeu():
    print("\n[doublons] le nom du jeu ne compte plus dans la comparaison")
    import fetch_feeds

    S = fetch_feeds.SIMILARITY_THRESHOLD

    # Il est dans TOUS les titres : le compter brouille la mesure dans les
    # deux sens, mesuré sur les 500 articles les plus récents.
    #
    # Sens 1 — l'aimant. « Grand Theft Auto VI - Rockstar Games » n'est que
    # le nom du jeu et celui du studio. Il atteignait 0,750, pile le seuil.
    # Le 30/08/2026 il a absorbé le Trailer 1 en production, et fait PERDRE
    # le Trailer 2 derrière lui — record_coverage refuse une seconde source
    # du même nom. Nettoyé, il ne reste rien de ce titre.
    aimant = "Grand Theft Auto VI - Rockstar Games"
    check(fetch_feeds.titre_comparable(aimant) == "",
          "un titre réduit au nom du jeu et du studio ne laisse rien")
    for autre in ("Grand Theft Auto VI Trailer 1",
                  "Grand Theft Auto VI Trailer 2",
                  "Grand Theft Auto VI: An Extended Look"):
        check(fetch_feeds.title_similarity(aimant, autre) == 0.0,
              f"il ne ressemble plus à « {autre[:38]} »")

    # Sens 2 — les vrais doublons que le nom du jeu séparait. Deux
    # graphies différentes comptaient comme une différence, alors que c'est
    # le même article montré deux fois dans le fil.
    for a, b in (("20+ New GTA 6 Screenshots Released",
                  "New Grand Theft Auto 6 Screenshots Revealed"),
                 ("Grand Theft Auto 6 Playthroughs Can Last 80 Hours",
                  "GTA 6 Playthrough Can Last Roughly 80 Hours")):
        check(fetch_feeds.title_similarity(a, b) >= S,
              f"« {a[:40]} » et « {b[:40]} » se rejoignent enfin")

    # Le suffixe de marque ne doit pas séparer non plus.
    check(fetch_feeds.title_similarity(
              "Grand Theft Auto VI - An Extended Look - Rockstar Games",
              "Grand Theft Auto VI: An Extended Look") == 1.0,
          "le même article avec et sans le suffixe du studio se rejoint")

    # Et deux sujets distincts restent distincts.
    check(fetch_feeds.title_similarity(
              "Grand Theft Auto VI - An Extended Look - Rockstar Games",
              "Grand Theft Auto VI Trailer 1 - Rockstar Games") < S,
          "« An Extended Look » et « Trailer 1 » ne se confondent pas")

    existant = [{"title": aimant, "link": "https://www.rockstargames.com/VI"}]
    item = {"title": "Grand Theft Auto VI Trailer 1",
            "link": "https://www.youtube.com/watch?v=QdBZY2fkU-0"}
    check(fetch_feeds.find_duplicate(item, existant, {}, existant, {}) is None,
          "le Trailer 1 n'est plus absorbé par la page générique")

    # Le garde-fou des numéros reste indispensable : nettoyés, « trailer 1 »
    # et « trailer 2 » se ressemblent encore à 0,89.
    check(fetch_feeds.title_similarity("Grand Theft Auto VI Trailer 1",
                                       "Grand Theft Auto VI Trailer 2") >= S,
          "le nettoyage seul ne sépare pas les numéros — d'où l'autre garde-fou")


def test_reparation_attributions_croisees():
    print("\n[données] une « autre source » ne renvoie pas vers un autre article")
    import fetch_feeds

    # Le lecteur qui clique sur « autre source » doit atterrir sur le même
    # sujet. Quand le lien est le lien PRINCIPAL d'un autre article du fil,
    # c'est un rapprochement erroné — audit_donnees.py les signalait sans
    # que rien ne vienne les corriger.
    items = [
        {"title": "A", "link": "https://a.fr/1",
         "extraSources": [{"source": "X", "link": "https://b.fr/2"},
                          {"source": "Y", "link": "https://legitime.fr/9"}]},
        {"title": "B", "link": "https://b.fr/2"},
        {"title": "C", "link": "https://c.fr/3",
         "extraSources": [{"source": "Z", "link": "https://b.fr/2"}]},
    ]
    fetch_feeds.repare_attributions_croisees(items)

    liens = [s["link"] for s in items[0].get("extraSources", [])]
    check(liens == ["https://legitime.fr/9"],
          "le renvoi vers l'article B est retiré, la source légitime reste")
    check("extraSources" not in items[2],
          "un article dont toutes les sources étaient fausses n'en garde aucune")
    check("extraSources" not in items[1],
          "un article sans source supplémentaire n'en gagne pas")

    # Idempotence : la passe tourne à chaque passage.
    avant = json.dumps(items, sort_keys=True)
    fetch_feeds.repare_attributions_croisees(items)
    check(json.dumps(items, sort_keys=True) == avant,
          "rejouer la réparation ne change plus rien")


def test_videos_archivees():
    print("\n[Rockstar] les vidéos trop anciennes pour le flux")
    import fetch_feeds

    par_id = {f["id"]: f for f in fetch_feeds.FEEDS}
    check(bool(fetch_feeds.VIDEOS_ARCHIVEES), "l'archive n'est pas vide")

    for v in fetch_feeds.VIDEOS_ARCHIVEES:
        check(v["source"] in par_id,
              f"{v['video']} est rattachée à une source qui existe encore")
        check(len(v["video"]) == 11,
              f"{v['video']} a la forme d'un identifiant YouTube")
        # Une date approximative rangerait la vidéo au mauvais endroit du
        # fil, et plus rien ne viendrait la corriger. Elles ont donc toutes
        # été relevées sur les fiches YouTube, pas devinées.
        quand = feed_store.parse_date_key(v["date"])
        check(quand != feed_store.DATE_FLOOR,
              f"{v['video']} porte une date lisible ({v['date'][:10]})")
        check(quand <= datetime.now(timezone.utc),
              f"{v['video']} n'est pas datée dans le futur")

    liens = [v["video"] for v in fetch_feeds.VIDEOS_ARCHIVEES]
    check(len(liens) == len(set(liens)), "aucune vidéo listée deux fois")

    # L'article produit doit être identique en forme à ceux du flux, sinon
    # il traverserait le pipeline différemment.
    v = fetch_feeds.VIDEOS_ARCHIVEES[0]
    item = fetch_feeds.item_video_archivee(v, par_id[v["source"]])
    for champ in ("title", "link", "date", "source", "official", "rockstarmag",
                  "specialist", "lang", "image", "description"):
        check(champ in item, f"l'article porte le champ « {champ} »")
    check(item["official"] is True,
          "une vidéo de la chaîne de Rockstar est marquée officielle")
    check(item["image"] == f"https://i.ytimg.com/vi/{v['video']}/hqdefault.jpg",
          "sa vignette se déduit de l'identifiant, sans appel réseau")
    check(item.get("archive") is True,
          "et elle est marquée archive : une bande-annonce de 2023 ne "
          "s'annonce pas comme une nouveauté")

    # Versées dans le résultat de leur source, pas importées à part : c'est
    # ce qui leur fait emprunter le même chemin que tout le reste.
    resultats = {v["source"]: ([], {"raw_count": 0}, [])}
    n = fetch_feeds.ajoute_videos_archivees(resultats)
    attendu = sum(1 for x in fetch_feeds.VIDEOS_ARCHIVEES
                  if x["source"] == v["source"])
    check(n == attendu and len(resultats[v["source"]][0]) == attendu,
          f"les {attendu} vidéos rejoignent la liste de leur source")

    # Une source absente du résultat (retirée de FEEDS, ou muette ce
    # passage) ne doit pas faire fabriquer un résultat de toutes pièces.
    check(fetch_feeds.ajoute_videos_archivees({}) == 0,
          "sans résultat pour la source, rien n'est ajouté")

    # Rejouées à chaque passage : la déduplication doit les absorber sans
    # gonfler les « sources supplémentaires » de l'article existant.
    deja = fetch_feeds.item_video_archivee(v, par_id[v["source"]])
    check(fetch_feeds.record_coverage(deja, dict(deja)) is False,
          "revoir la même vidéo n'ajoute pas une source supplémentaire")


def test_archives_ecartees_avant_le_decodage():
    print("\n[Google News] on ne décode pas ce qu'on jette")
    import fetch_feeds, types
    from datetime import timedelta

    vieux = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()

    # 3 récents, 7 archives — tous passent le filtre par mots-clés.
    entrees = [{"title": f"GTA 6 sujet {i}", "summary": "",
                "link": f"https://news.google.com/rss/articles/{i}",
                "published": recent if i < 3 else vieux} for i in range(10)]
    flux = types.SimpleNamespace(status=200, bozo=False, entries=entrees,
                                 version="rss20", href=None,
                                 etag=None, modified=None)

    def collecte(feed):
        """Renvoie (résultat, liens réellement envoyés au décodeur)."""
        vus = []
        vrai_parse = fetch_feeds.feedparser.parse
        vrai_pre = fetch_feeds.predecode_links
        fetch_feeds.feedparser.parse = lambda *a, **k: flux
        fetch_feeds.predecode_links = lambda liens, cache=None, journal=None: (
            vus.extend(liens) or {})
        try:
            return fetch_feeds.collect_feed_items(feed, {}, {}), vus
        finally:
            fetch_feeds.feedparser.parse = vrai_parse
            fetch_feeds.predecode_links = vrai_pre

    base = {"id": "g", "name": "Une recherche", "official": False,
            "url": "https://news.google.com/rss/search?q=gta6", "lang": "en"}

    # Le décodage coûte une seconde par lien. Le payer pour un article jeté
    # la ligne suivante, c'est du temps pur perdu : mesuré le 01/09/2026 sur
    # un vrai passage, 91 des 199 décodages étaient dans ce cas — 46 %.
    (items, _, _), decodes = collecte(dict(base))
    check(len(decodes) == 3,
          f"seuls les 3 articles gardés sont décodés, pas les 10 ({len(decodes)})")
    check(len(items) == 3, "et 3 articles ressortent")

    # Une source qui garde ses archives les décode toutes : c'est voulu, ce
    # sont justement les articles qu'on cherche.
    (items, _, _), decodes = collecte(dict(base, garder_les_archives=True))
    check(len(decodes) == 10,
          "une source en garder_les_archives décode tout, archives comprises")
    check(sum(1 for i in items if i.get("archive")) == 7,
          "et les 7 archives ressortent, marquées comme telles")

    # L'ordre lui-même : le commentaire du code disait déjà « écarté AVANT le
    # décodage », mais le décodage groupé avait été posé avant le filtre
    # d'âge. Verrouillé ici pour que la dérive ne se refasse pas.
    src = open("fetch_feeds.py", encoding="utf-8").read()
    corps = src[src.index("def collect_feed_items("):]
    corps = corps[:corps.index("\ndef ")]
    check(corps.index("trop_vieux(date)") < corps.index("predecode_links("),
          "le filtre d'âge est bien AVANT l'appel au décodage groupé")


def test_couverture_rockstar():
    print("\n[Rockstar] tout ce que publie Rockstar, archives comprises")
    import fetch_feeds
    from datetime import timedelta

    vieux = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()

    def faux_flux(nb):
        """nb entrées, toutes anciennes sauf la première."""
        entrees = [{"title": f"GTA 6 nouvelle {i}", "summary": "",
                    "link": f"https://www.rockstargames.com/n{i}",
                    "published": recent if i == 0 else vieux} for i in range(nb)]
        return types.SimpleNamespace(status=200, bozo=False, entries=entrees,
                                     version="rss20", href=None,
                                     etag=None, modified=None)

    def collecte(feed, entrees):
        vrai = fetch_feeds.feedparser.parse
        fetch_feeds.feedparser.parse = lambda *a, **k: faux_flux(entrees)
        try:
            return fetch_feeds.collect_feed_items(feed, {}, {})
        finally:
            fetch_feeds.feedparser.parse = vrai

    base = {"id": "x", "name": "Rockstar Games (officiel EN)",
            "url": "https://news.google.com/rss/search?q=site:rockstargames.com",
            "official": True}

    # Plafond de lecture : par défaut 30, même quand le flux en offre 100.
    items, _, _ = collecte({**base, "garder_les_archives": True}, 100)
    check(len(items) == fetch_feeds.MAX_ENTREES,
          f"sans réglage, seules {fetch_feeds.MAX_ENTREES} entrées sont lues "
          f"sur 100 (obtenu {len(items)})")

    items, _, _ = collecte({**base, "garder_les_archives": True,
                            "max_entrees": 100}, 100)
    check(len(items) == 100,
          f"avec max_entrees=100, les 100 sont lues (obtenu {len(items)}) — "
          f"c'est 70 pages de Rockstar qui n'entraient nulle part")

    # Le garde-fou d'âge, et son exemption.
    items, _, _ = collecte({**base, "max_entrees": 100}, 100)
    check(len(items) == 1,
          "sans exemption, tout ce qui dépasse 45 jours est écarté : "
          f"il ne reste que l'article récent (obtenu {len(items)})")

    items, _, _ = collecte({**base, "garder_les_archives": True,
                            "max_entrees": 100}, 100)
    archives = [i for i in items if i.get("archive")]
    check(len(archives) == 99,
          f"avec exemption, les anciennes reviennent (obtenu {len(archives)})")
    check(not items[0].get("archive"),
          "et l'article récent n'est PAS marqué archive")

    # Le drapeau se pose source par source, jamais déduit de `official` :
    # « Rockstar Games (annonces) » est officiel ET une recherche web
    # généraliste. L'exempter rouvrirait le déversement d'archives tierces
    # qui a motivé le garde-fou le 29/08/2026.
    par_id = {f["id"]: f for f in fetch_feeds.FEEDS}
    for fid in ("rockstar-en", "rockstar-fr", "rockstar-youtube", "take2-ir"):
        check(par_id[fid].get("garder_les_archives") is True,
              f"{fid} garde ses archives")
    check(not par_id["rockstar-announce"].get("garder_les_archives"),
          "mais PAS rockstar-announce, qui cherche sur tout le web")
    exemptees = [f["id"] for f in fetch_feeds.FEEDS if f.get("garder_les_archives")]
    check(all(f["id"].startswith(("rockstar", "take2")) for f in fetch_feeds.FEEDS
              if f.get("garder_les_archives")),
          f"l'exemption reste cantonnée aux canaux de Rockstar : {exemptees}")


def test_archives_ne_notifient_pas():
    print("\n[Rockstar] une archive rapatriée ne fait vibrer aucun téléphone")

    # Chaque notification fait sortir le téléphone. Cinquante publications
    # de 2025 annoncées d'un bloc, ce sont cinquante dérangements pour du
    # vieux — le contraire de ce qu'on cherche en rapatriant l'historique.
    nouveaux = [{"title": "Neuf", "link": "https://a.fr/1"},
                {"title": "Vieux", "link": "https://a.fr/2", "archive": True}]
    a_annoncer = [i for i in nouveaux if not i.get("archive")]
    check(len(a_annoncer) == 1 and a_annoncer[0]["title"] == "Neuf",
          "seul l'article réellement nouveau part en notification")

    src = open("fetch_feeds.py", encoding="utf-8").read()
    check('a_annoncer = [i for i in newly_added if not i.get("archive")]' in src,
          "le filtre est bien posé sur le chemin des notifications")
    check("write_new_items_file(a_annoncer)" in src,
          "et c'est la liste filtrée qui est déposée, pas newly_added")

    # Le piège indirect : rapatrier des archives fait gagner une reprise à
    # des sujets déjà connus. Sans garde, une vague de « sujet devenu
    # majeur » partirait par la bande alors que les archives elles-mêmes
    # sont silencieuses.
    check('not item.get("archive")' in src
          and "avant < HOT_SOURCE_THRESHOLD <= apres" in src,
          "une archive ne peut pas promouvoir un sujet en actu majeure")

    # Et le compte reste honnête : l'archive EST un nouvel article de
    # l'historique, elle doit être comptée comme tel.
    check("len(newly_added)" in src,
          "« N nouveaux » continue de compter les archives : elles entrent "
          "bien dans l'historique, c'est la notification qu'on retient")


def test_predecode_google_news():
    print("\n== Pré-décodage des liens Google News ==")
    import fetch_feeds

    appels = []

    def faux_decodeur(url):
        appels.append(url)
        return url.replace("news.google.com/rss/articles/", "vrai-site.fr/")

    vrai = fetch_feeds.decode_google_news_link
    fetch_feeds.decode_google_news_link = faux_decodeur
    try:
        cache = {}
        liens = [f"https://news.google.com/rss/articles/{i}" for i in range(5)]
        # Un lien en double dans la même fournée : il ne doit être décodé qu'une fois.
        resolus = fetch_feeds.predecode_links(liens + [liens[0]], cache)
        check(len(appels) == 5, f"5 liens distincts -> 5 décodages, pas 6 (obtenu : {len(appels)})")
        check(resolus[liens[0]] == "https://vrai-site.fr/0", "le lien est bien résolu")
        check(cache[liens[0]] == "https://vrai-site.fr/0", "le cache partagé est alimenté")

        # Deuxième fournée : tout est déjà en cache, plus aucun décodage.
        appels.clear()
        fetch_feeds.predecode_links(liens, cache)
        check(appels == [], "un lien déjà connu du cache n'est pas redécodé")

        # Un décodage qui échoue renvoie le lien inchangé : il ne doit PAS
        # entrer en cache, sinon un autre flux portant le même article ne
        # retenterait jamais, alors qu'en séquentiel il retentait.
        appels.clear()
        cache2 = {}
        rate = "https://news.google.com/rss/articles/casse"
        fetch_feeds.decode_google_news_link = lambda u: u
        fetch_feeds.predecode_links([rate], cache2)
        check(rate not in cache2, "un décodage raté n'est pas mis en cache")
    finally:
        fetch_feeds.decode_google_news_link = vrai


def test_onglets_par_domaine():
    print("\n[onglets] le classement suit l'éditeur, pas la source qui a trouvé")
    import fetch_feeds

    gnews_fr = next(f for f in fetch_feeds.FEEDS if f["id"] == "gnews-fr")
    gnews_en = next(f for f in fetch_feeds.FEEDS if f["id"] == "gnews-en")
    rmag = next(f for f in fetch_feeds.FEEDS if f["id"] == "rockstarmag")
    yt = next(f for f in fetch_feeds.FEEDS if f["id"] == "rockstar-youtube")

    # Le défaut d'origine : Google News trouve un article du Newswire, et il
    # atterrit dans « Non Rockstar » parce que la SOURCE n'est pas officielle.
    newswire = "https://www.rockstargames.com/newswire/article/517oa1/gta-vi-pre-orders"
    check(fetch_feeds.statut_officiel(newswire, gnews_en),
          "un lien Newswire trouvé par Google News est officiel")
    check(fetch_feeds.statut_officiel(newswire, None),
          "il l'est même sans source connue : le domaine suffit")
    check(fetch_feeds.statut_officiel("https://ir.take2games.com/news/x", gnews_en),
          "Take-Two aussi")

    # Symétrique pour Rockstar Mag.
    art_rmag = "https://www.rockstarmag.fr/gta-6-decouvrez-la-nouvelle-preview-du-jeu/"
    check(fetch_feeds.statut_rockstarmag(art_rmag, gnews_fr),
          "un article rockstarmag.fr trouvé par Google News va dans son onglet")
    check(fetch_feeds.statut_rockstarmag(art_rmag, None),
          "le domaine suffit là aussi")
    check(fetch_feeds.statut_rockstarmag("https://www.jeuxvideo.com/news/x", rmag),
          "la déclaration de source reste honorée si le lien sort du domaine")

    # Et surtout : aucune contamination.
    check(not fetch_feeds.statut_officiel("https://www.ign.com/articles/gta-6", gnews_en),
          "un article IGN ne devient pas officiel")
    check(not fetch_feeds.statut_rockstarmag("https://www.ign.com/articles/gta-6", gnews_en),
          "ni RockstarMag")
    check(not fetch_feeds.statut_officiel("https://www.youtube.com/watch?v=abc", gnews_en),
          "une vidéo YouTube trouvée par Google News n'est PAS officielle")
    check(fetch_feeds.statut_officiel("https://www.youtube.com/watch?v=abc", yt),
          "mais elle l'est venant de la chaîne de Rockstar, qui déclare ce domaine")
    check(not fetch_feeds.statut_officiel("", gnews_en),
          "un lien vide ne fait rien passer")

    # La repasse rétroactive corrige DANS LES DEUX SENS. Ne rétrograder que
    # les faux officiels laissait les vrais non détectés à l'abandon :
    # l'historique n'est jamais rejoué dans le pipeline de collecte.
    historique = [
        # à promouvoir : publiés par Rockstar / Rockstar Mag, trouvés ailleurs
        {"source": "Google News (EN)", "link": newswire, "official": False, "rockstarmag": False},
        {"source": "Google News (FR)", "link": art_rmag, "official": False, "rockstarmag": False},
        # à rétrograder : marqué officiel alors que le lien ne l'est pas
        {"source": "Rockstar Games (officiel EN)", "link": "https://www.ign.com/a",
         "official": True, "rockstarmag": False},
        # à laisser tel quel
        {"source": "Rockstar Games (YouTube)", "link": "https://www.youtube.com/watch?v=abc",
         "official": True, "rockstarmag": False},
        {"source": "PC Gamer", "link": "https://www.pcgamer.com/gta6",
         "official": False, "rockstarmag": False},
        # source disparue de FEEDS (ancien nom) : jugée sur son seul domaine
        {"source": "RockstarMag.fr", "link": art_rmag, "official": False, "rockstarmag": True},
    ]
    fetch_feeds.recheck_official_status(historique)
    check(historique[0]["official"] is True, "le Newswire est promu officiel rétroactivement")
    check(historique[1]["rockstarmag"] is True, "l'article Rockstar Mag est promu rétroactivement")
    check(historique[2]["official"] is False, "le faux officiel est toujours rétrogradé")
    check(historique[3]["official"] is True, "la vidéo de la chaîne garde son statut")
    check(historique[4]["official"] is False and historique[4]["rockstarmag"] is False,
          "un article tiers reste dans « Non Rockstar »")
    check(historique[5]["rockstarmag"] is True,
          "une source renommée garde son onglet grâce au domaine")

    # Un article ne doit jamais tomber dans deux onglets : les trois filtres
    # de l'app (official / rockstarmag / ni l'un ni l'autre) se partagent le
    # fil, et un doublon fausserait les compteurs.
    for item in historique:
        check(not (item["official"] and item["rockstarmag"]),
              f"pas de double appartenance : {item['source']}")



def test_couverture_par_lien():
    print("\n[couverture] « N sources » compte des rédactions, pas des flux")
    import fetch_feeds

    LIEN = "https://www.rockstargames.com/newswire/article/9k2k/extended-look"
    base = {"source": "Rockstar Games (officiel EN)", "link": LIEN, "title": "Extended Look"}

    # Le cas réel qui a produit le premier faux 🔥 : quatre requêtes Google
    # News différentes remontent la MÊME page du Newswire.
    for flux in ("Rockstar Games (annonces)", "Google News (EN)", "GTA 6 x Netflix"):
        fetch_feeds.record_coverage(base, {"source": flux, "link": LIEN})
    check(len(base.get("extraSources") or []) == 0,
          "le même lien trouvé par trois autres flux n'ajoute aucune source")
    check(not fetch_feeds.is_hot(base), "il ne devient donc pas une actu majeure")

    # Une vraie rédaction, avec sa propre URL, compte.
    fetch_feeds.record_coverage(base, {"source": "IGN", "link": "https://www.ign.com/a"})
    fetch_feeds.record_coverage(base, {"source": "Kotaku", "link": "https://kotaku.com/b"})
    check(len(base["extraSources"]) == 2, "deux rédactions distinctes sont comptées")

    # Deux flux différents rapportant la même URL tierce : un seul compte.
    fetch_feeds.record_coverage(base, {"source": "Google News (FR)", "link": "https://www.ign.com/a"})
    check(len(base["extraSources"]) == 2,
          "un autre flux sur une URL déjà connue n'ajoute rien")

    # La règle historique tient toujours : même nom de source, on ignore.
    fetch_feeds.record_coverage(base, {"source": "IGN", "link": "https://www.ign.com/autre"})
    check(len(base["extraSources"]) == 2, "la même source deux fois reste ignorée")

    check(1 + len(base["extraSources"]) == 3, "le compte affiché vaut 3, pas 6")

    # Un article sans lien ne doit pas faire exploser la fonction.
    vide = {"source": "A", "link": ""}
    fetch_feeds.record_coverage(vide, {"source": "B", "link": ""})
    check(len(vide.get("extraSources") or []) == 1,
          "des liens vides n'empêchent pas de compter deux sources nommées")

    # --- Reprise de l'historique déjà gonflé ---
    # Sans elle, les articles enregistrés avant le correctif garderaient leur
    # compte faux : l'historique n'est jamais rejoué dans la collecte.
    historique = [
        {"link": LIEN, "extraSources": [
            {"source": "Rockstar Games (annonces)", "link": LIEN},
            {"source": "Google News (EN)", "link": LIEN},
            {"source": "GTA 6 x Netflix", "link": LIEN},
        ]},
        {"link": "https://a.fr/1", "extraSources": [
            {"source": "IGN", "link": "https://www.ign.com/a"},
            {"source": "Google News (FR)", "link": "https://www.ign.com/a"},
            {"source": "Kotaku", "link": "https://kotaku.com/b"},
        ]},
        {"link": "https://b.fr/2", "extraSources": [{"source": "IGN", "link": "https://www.ign.com/c"}]},
        {"link": "https://c.fr/3"},
    ]
    fetch_feeds.deduplique_couverture(historique)
    check("extraSources" not in historique[0],
          "un article gonflé par un seul lien perd entièrement ses sources en trop")
    check(not fetch_feeds.is_hot(historique[0]), "et perd son badge d'actu majeure")
    check(len(historique[1]["extraSources"]) == 2,
          "un doublon est retiré, les deux vraies rédactions restent")
    check([a["source"] for a in historique[1]["extraSources"]] == ["IGN", "Kotaku"],
          "c'est la première occurrence qui est gardée, dans l'ordre")
    check(len(historique[2]["extraSources"]) == 1, "un article déjà sain n'est pas touché")
    check("extraSources" not in historique[3], "un article sans sources reste sans sources")

    # Idempotence : rejouer la passe ne doit plus rien changer.
    avant = [len(i.get("extraSources") or []) for i in historique]
    fetch_feeds.deduplique_couverture(historique)
    check([len(i.get("extraSources") or []) for i in historique] == avant,
          "rejouer la passe est sans effet")



def test_doublons_de_titre():
    print("\n[doublons] même titre exact, quelle que soit l'ancienneté")
    import fetch_feeds

    # Le cas réel : le même article sous deux URL, découvert par deux flux à
    # des heures différentes. La fenêtre floue se compte en ARTICLES, pas en
    # heures : lors d'un pic à 288 articles/jour elle ne couvre plus que
    # douze heures, et ces paires lui échappaient.
    a = {"title": "We've Seen GTA 6 Gameplay IRL", "link": "https://ign.com/a",
         "source": "IGN", "date": "2026-08-28T01:07:00+00:00"}
    b = {"title": "We’ve Seen GTA 6 Gameplay IRL", "link": "https://fr.ign.com/b",
         "source": "IGN France", "date": "2026-08-28T01:07:00+00:00"}
    index = {fetch_feeds.normalize_title(a["title"]): a}
    # Fenêtre VIDE : c'est tout l'intérêt, l'index n'a pas d'horizon.
    check(fetch_feeds.find_duplicate(b, [a], {a["link"]: a}, [], index) is a,
          "reconnu alors que la fenetre floue ne le voyait plus")
    check(fetch_feeds.find_duplicate(b, [a], {a["link"]: a}, []) is None,
          "sans l'index, il passait entre les mailles — le défaut d'origine")

    # Un titre vide ne prouve rien : deux articles sans titre ne sont pas le
    # même article, et les fusionner ferait disparaître le second.
    v1 = {"title": "", "link": "https://x.fr/1", "source": "A"}
    v2 = {"title": "", "link": "https://x.fr/2", "source": "B"}
    check(fetch_feeds.find_duplicate(v2, [v1], {v1["link"]: v1}, [], {"": v1}) is None,
          "deux titres vides ne fusionnent pas")

    # --- Reprise de l'historique ---
    hist = [
        {"title": "Même titre", "link": "https://a.fr/1", "source": "A", "date": "2026-08-02T00:00:00+00:00"},
        {"title": "MÊME TITRE !", "link": "https://b.fr/2", "source": "B", "date": "2026-08-01T00:00:00+00:00"},
        {"title": "Autre sujet", "link": "https://c.fr/3", "source": "C", "date": "2026-08-03T00:00:00+00:00"},
        {"title": "", "link": "https://d.fr/4", "source": "D", "date": "2026-08-04T00:00:00+00:00"},
        {"title": "", "link": "https://e.fr/5", "source": "E", "date": "2026-08-05T00:00:00+00:00"},
    ]
    r = fetch_feeds.fusionne_doublons_de_titre(list(hist))
    check(len(r) == 4, "une seule fusion sur cinq articles")
    garde = next(i for i in r if i["link"] == "https://b.fr/2")
    check(garde is not None, "c'est le PLUS ANCIEN qui est conservé")
    check([e["source"] for e in garde.get("extraSources") or []] == ["A"],
          "le plus récent devient une source supplémentaire")
    check(sum(1 for i in r if i["title"] == "") == 2,
          "les deux titres vides survivent tous les deux")

    # Idempotence : rejouer la passe ne doit plus rien changer.
    r2 = fetch_feeds.fusionne_doublons_de_titre(list(r))
    check(len(r2) == len(r), "rejouer la passe est sans effet")

    # Jamais de fusion floue rétroactive : deux titres proches mais distincts
    # doivent rester séparés, même au-dessus du seuil de similarité.
    proches = [
        {"title": "Our GTA 6 Extended Look Predictions", "link": "https://g.fr/1",
         "source": "GTA BOOM", "date": "2026-08-01T00:00:00+00:00"},
        {"title": "How Our GTA 6 Extended Look Predictions Held Up", "link": "https://g.fr/2",
         "source": "GTA BOOM", "date": "2026-08-02T00:00:00+00:00"},
    ]
    check(fetch_feeds.title_similarity(proches[0]["title"], proches[1]["title"])
          >= fetch_feeds.SIMILARITY_THRESHOLD,
          "ces deux titres passent pourtant le seuil de similarité")
    check(len(fetch_feeds.fusionne_doublons_de_titre(list(proches))) == 2,
          "et restent malgré tout séparés : le doute profite à la séparation")



def test_chaine_youtube_rockstarmag():
    print("\n[sources] la chaîne YouTube de RockstarMag dans l'onglet RockstarMag")
    import fetch_feeds

    yt = next(f for f in fetch_feeds.FEEDS if f["id"] == "rockstarmag-youtube")
    lien = "https://www.youtube.com/watch?v=abc"

    # Le classement par domaine ne peut RIEN pour elle : le lien pointe vers
    # youtube.com, pas rockstarmag.fr. C'est la déclaration de la source qui
    # doit prendre le relais — sans quoi les vidéos tomberaient dans
    # « Non Rockstar ».
    check(not fetch_feeds.statut_rockstarmag(lien, None),
          "un lien YouTube seul ne suffit pas à désigner RockstarMag")
    check(fetch_feeds.statut_rockstarmag(lien, yt),
          "mais la chaîne déclarée y range bien ses vidéos")
    check(not fetch_feeds.statut_officiel(lien, yt),
          "et elle n'est surtout pas officielle : ce n'est pas Rockstar")

    # Aucune contamination : une autre source sur YouTube reste où elle est.
    gnews = next(f for f in fetch_feeds.FEEDS if f["id"] == "gnews-fr")
    check(not fetch_feeds.statut_rockstarmag(lien, gnews),
          "une vidéo trouvée par Google News ne devient pas RockstarMag")

    # Pas de no_filter_at_all, contrairement au flux d'articles du même
    # média : la chaîne couvre toute la production Rockstar.
    check(not yt.get("no_filter_at_all"),
          "le filtre reste actif sur la chaîne")
    articles = next(f for f in fetch_feeds.FEEDS if f["id"] == "rockstarmag")
    check(articles.get("no_filter_at_all") is True,
          "alors que le flux d'articles garde son exception, elle est inchangée")
    check(sum(1 for f in fetch_feeds.FEEDS if f.get("no_filter_at_all")) == 1,
          "une seule source sans filtre dans tout FEEDS")

    # Le filtre porte sur le titre ET la description : une vidéo au titre
    # elliptique passe si sa description parle du sujet.
    check(fetch_feeds.passe_le_filtre(yt, "GTA 6 | Tout sur la bande-annonce", ""),
          "un titre explicite passe")
    check(fetch_feeds.passe_le_filtre(yt, "On En Parle #12",
                                      "Un point complet sur GTA 6 et Vice City"),
          "un titre elliptique passe grâce à sa description")
    check(not fetch_feeds.passe_le_filtre(yt, "Red Dead Redemption 2 : les secrets",
                                          "Notre documentaire sur RDR2"),
          "le contenu Red Dead reste écarté")

    # Un onglet, un seul : official et rockstarmag ne peuvent pas coexister.
    check(not (fetch_feeds.statut_officiel(lien, yt)
               and not fetch_feeds.statut_rockstarmag(lien, yt)),
          "pas de double appartenance possible")


def test_timeout_reseau():
    print("\n[réseau] un délai maximal existe et vaut ce qui est documenté")
    import socket
    import fetch_feeds

    # feedparser n'accepte aucun paramètre de timeout : il passe par urllib,
    # qui suit le défaut des sockets. Sans ce défaut, une source qui accepte
    # la connexion puis se tait bloque son fil indéfiniment et le passage ne
    # se termine jamais. Ce test garde le garde-fou en place.
    check(socket.getdefaulttimeout() == fetch_feeds.FETCH_TIMEOUT,
          f"importer fetch_feeds pose un timeout global de {fetch_feeds.FETCH_TIMEOUT} s")
    check(socket.getdefaulttimeout() is not None,
          "le timeout n'est pas None (attente infinie)")
    check(0 < fetch_feeds.FETCH_TIMEOUT <= 60,
          "le timeout est dans une plage raisonnable")


def test_source_cassee_vs_muette():
    print("\n[sources] une page HTML ne se confond plus avec un flux vide")
    import fetch_feeds

    # Cas réel du 30/08/2026 : IGN et Kotaku renvoyaient « 0 entrée » sans
    # qu'on puisse savoir si le flux était vide ou si la réponse n'était pas
    # un flux du tout. Les deux appellent des gestes différents.
    infos = {
        "bloquee": {"raw_count": 0, "not_modified": False,
                    "http_status": 403, "not_a_feed": True},
        "vide":    {"raw_count": 0, "not_modified": False, "http_status": 200},
        "trois_zero": {"raw_count": 0, "not_modified": True, "http_status": 304},
    }
    feeds_avant = fetch_feeds.FEEDS
    fetch_feeds.FEEDS = [{"id": k, "name": k, "url": "", "official": False}
                         for k in infos]
    try:
        sante = {s["id"]: s for s in fetch_feeds.build_sources_health([], infos, {})}
    finally:
        fetch_feeds.FEEDS = feeds_avant

    check(sante["bloquee"]["status"] == "cassee",
          "une réponse qui n'est pas un flux donne le statut « cassee »")
    check(sante["bloquee"]["http_status"] == 403,
          "le code HTTP est conservé pour le diagnostic")
    check(sante["vide"]["status"] == "muette",
          "un flux valide mais vide reste « muette »")
    check(sante["trois_zero"]["status"] != "muette",
          "une réponse 304 n'est jamais prise pour une panne")

    # Le point qui casse en silence si on l'oublie : « cassee » doit compter
    # comme « ne rapporte rien », sinon le compteur de passages muets repart
    # à zéro et une fausse alerte de rétablissement part sur Discord.
    check(fetch_feeds.ne_rapporte_rien(sante["bloquee"]),
          "une source cassée compte comme ne rapportant rien")
    check(fetch_feeds.ne_rapporte_rien(sante["vide"]),
          "une source muette aussi")
    check(not fetch_feeds.ne_rapporte_rien({"status": "ok"}),
          "une source qui répond, non")

    # « muette » et « cassée » décrivent la même conséquence. Une source qui
    # passe de l'une à l'autre ne doit PAS voir son chronomètre repartir de
    # zéro : ça retarderait l'alerte de 24 h et, au retour, déclencherait un
    # faux « source rétablie » pour une panne jamais signalée.
    from datetime import timedelta
    t0 = datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc)
    depart = (t0 - timedelta(hours=fetch_feeds.DEAD_SOURCE_HOURS)).isoformat()
    liste = [dict(sante["bloquee"])]
    suivi, alertes = fetch_feeds.suivre_sources_muettes(
        liste, {"bloquee": {"depuis": depart, "succes": 0, "alertee": False}},
        maintenant=t0)
    check(suivi["bloquee"]["depuis"] == depart,
          "le chronomètre continue de tourner quand une muette devient cassée")
    check(any(a["type"] == "tombee" for a in alertes),
          "et l'alerte part bien au franchissement des 24 h")


def test_compteur_echecs_decodage():
    print("\n[Google News] les décodages ratés sont comptés, pas seulement tracés")
    import fetch_feeds

    fetch_feeds.reinitialise_echecs_decodage()
    check(fetch_feeds.echecs_decodage() == 0, "compteur remis à zéro au début du passage")

    # decode_google_news_link renvoie le lien inchangé quand il échoue :
    # c'est ce que predecode_links compte, sans jamais perdre l'article.
    liens = ["https://news.google.com/rss/articles/AAA",
             "https://news.google.com/rss/articles/BBB"]
    reel = fetch_feeds.decode_google_news_link
    fetch_feeds.decode_google_news_link = lambda u: u  # échec pour les deux
    try:
        journal = []
        resolus = fetch_feeds.predecode_links(liens, {}, journal)
    finally:
        fetch_feeds.decode_google_news_link = reel

    check(fetch_feeds.echecs_decodage() == 2, "deux échecs comptés")
    check(all(resolus[l] == l for l in liens),
          "le lien d'origine est conservé : aucun article perdu")
    check(any("échec" in l for l in journal), "l'échec apparaît dans le journal")

    fetch_feeds.reinitialise_echecs_decodage()
    check(fetch_feeds.echecs_decodage() == 0, "et le compteur se réinitialise")


def test_historique_entrees():
    print("\n[sources] repérer une source qui se dégrade sans mourir")
    import fetch_feeds

    # Une réponse 304 ne dit rien du volume du flux : l'empiler comme un
    # zéro ferait chuter la référence de toutes les sources bien élevées.
    s = fetch_feeds.maj_historique_entrees(
        {"b": {"raw_count": 0, "not_modified": True}}, {"b": "30,30,30"})
    check(s["b"] == "30,30,30", "une réponse 304 n'ajoute pas de faux zéro")

    s = fetch_feeds.maj_historique_entrees(
        {"a": {"raw_count": 19, "not_modified": False}}, {"a": "20,20,18"})
    check(s["a"] == "20,20,18,19", "un passage normal est empilé")

    long = ",".join(["30"] * 30)
    s = fetch_feeds.maj_historique_entrees(
        {"a": {"raw_count": 30, "not_modified": False}}, {"a": long})
    check(len(fetch_feeds._serie(s["a"])) == fetch_feeds.HISTORIQUE_PASSAGES,
          f"la série est plafonnée à {fetch_feeds.HISTORIQUE_PASSAGES} passages")

    def baisse(serie):
        return "x" in fetch_feeds.sources_en_baisse({"x": serie})

    # Le cas qu'on cherche : la source répond toujours, mais amputée.
    check(baisse("30,28,31,29,30,32,30,2,1,2"),
          "un effondrement de 30 à 2 est signalé")
    # Et tout ce qui ne doit PAS déclencher, sous peine de crier au loup.
    check(not baisse("30,28,31,29,30,32,30,30,30,2"),
          "un creux d'un seul passage ne suffit pas")
    check(not baisse("3,2,4,1,3,2,0,0,0"),
          "une petite source qui varie n'est pas signalée")
    check(not baisse("30,28,31,29,30,32,30,29,31,30"),
          "un régime stable ne déclenche rien")
    check(not baisse("30,30"),
          "une série trop courte ne conclut pas")


def test_diagnostic_redirection():
    print("\n[sources] une redirection dit vers OÙ le flux a déménagé")
    import fetch_feeds

    # Le cas réel du 30/08/2026 : IGN répondait 302 et Kotaku 301, avec
    # « 0 entrée » pour tout diagnostic. On savait que ça avait bougé, pas
    # vers où — il a fallu un passage de plus pour l'apprendre.
    class FauxParse:
        bozo = False
        entries = []
        version = ""
        status = 301
        href = "https://exemple.com/nouveau-flux"

    info = {"http_status": 301, "redirect": FauxParse.href, "not_a_feed": True,
            "raw_count": 0, "not_modified": False}
    feeds_avant = fetch_feeds.FEEDS
    fetch_feeds.FEEDS = [{"id": "x", "name": "X", "url": "", "official": False}]
    try:
        sante = fetch_feeds.build_sources_health([], {"x": info}, {})[0]
    finally:
        fetch_feeds.FEEDS = feeds_avant

    check(sante["status"] == "cassee", "une redirection vers une page donne « cassee »")
    check(sante["http_status"] == 301, "le code de redirection est conservé")
    check(sante["redirect"] == FauxParse.href,
          "et surtout l'adresse d'arrivée, qui est la correction à appliquer")

    # Une redirection vers un vrai flux ne doit RIEN signaler : beaucoup de
    # sites redirigent http vers https ou ajoutent une barre oblique.
    sain = {"http_status": 301, "redirect": FauxParse.href, "raw_count": 20,
            "not_modified": False}
    fetch_feeds.FEEDS = [{"id": "x", "name": "X", "url": "", "official": False}]
    try:
        s2 = fetch_feeds.build_sources_health([], {"x": sain}, {})[0]
    finally:
        fetch_feeds.FEEDS = feeds_avant
    check(s2["status"] != "cassee",
          "une redirection qui aboutit sur un vrai flux n'est pas une panne")


def test_validation_avant_ecriture():
    print("\n[écriture] un flux abîmé n'est jamais publié")
    import feed_store

    bon = {"items": [{"link": "https://a/1", "title": "un"},
                     {"link": "https://a/2", "title": "deux"}]}
    check(feed_store.valide_avant_ecriture(bon) == 2, "un flux sain passe")

    def refuse(data, precedent=None):
        try:
            feed_store.valide_avant_ecriture(data, precedent)
            return False
        except feed_store.FeedInvalide:
            return True

    check(refuse({"items": [{"title": "sans lien"}]}), "un article sans lien est refusé")
    check(refuse({"items": [{"link": "https://a/1"}]}), "un article sans titre est refusé")
    check(refuse({"items": [{"link": "https://a/1", "title": "x"},
                            {"link": "https://a/1", "title": "y"}]}),
          "deux fois le même lien est refusé")
    check(refuse({"items": "pas une liste"}), "un « items » qui n'est pas une liste est refusé")

    # Une purge rétroactive retire légitimement quelques articles ; en perdre
    # un dixième d'un coup est un bug, pas un nettoyage.
    gros = {"items": [{"link": f"https://a/{i}", "title": str(i)} for i in range(1000)]}
    presque = {"items": gros["items"][:980]}
    maigre = {"items": gros["items"][:800]}
    check(not refuse(presque, gros), "perdre 2 % des articles reste toléré")
    check(refuse(maigre, gros), "en perdre 20 % arrête la publication")



def test_ligne_etat_sans_double_compte():
    print("\n[app] la ligne d'état ne compte pas deux fois la même source")
    html = open("docs/index.html", encoding="utf-8").read()
    fn = html[html.index("function majLigneRun("):]
    fn = fn[:fn.index("\n}")]

    # La garantie : « muettes » et « cassées » ne peuvent pas se recouvrir.
    # Elle tenait par soustraction d'ensembles, elle tient désormais par
    # construction — les deux comptes viennent de sources_health, où une
    # source porte exactement un statut. Le symptôme qu'on empêche reste le
    # même : « 3 sources muettes · 3 cassées » pour trois sources en tout.
    check('filter(s => s.status === "cassee")' in fn,
          "les cassées se comptent depuis sources_health")
    check('filter(s => s.status === "muette")' in fn,
          "les muettes aussi — donc les deux ensembles sont disjoints")

    # Et surtout PAS depuis sources_silence : ce dictionnaire ne liste plus
    # les sources muettes mais les chronomètres de panne en cours. Une
    # source qui vient de répondre y reste tant que sa reprise n'est pas
    # confirmée sur deux passages ; la compter ici l'afficherait muette
    # alors qu'elle rapporte.
    # L'accès à la propriété, pas le mot : les commentaires ci-dessus
    # expliquent justement pourquoi on ne s'en sert plus, et les chercher
    # littéralement ferait échouer le test sur sa propre documentation.
    check("data.sources_silence" not in fn,
          "la ligne d'état ne lit plus sources_silence, qui a changé de sens")

    # Le message « tout va bien » ne doit sortir que si les DEUX comptes sont
    # nuls, sinon il cohabiterait avec une alerte.
    check("if(!muettes && !cassees.length)" in fn,
          "« toutes les sources répondent » exige zéro muette ET zéro cassée")

    # La ligne se place sous les deux boutons : au-dessus, elle séparait le
    # nombre d'articles des actions qui le modifient.
    carte = html[html.index('<header class="console">'):]
    carte = carte[:carte.index("</header>")]
    check(carte.index('id="runLine"') > carte.index('class="controls"'),
          "la ligne d'état est placée après le bloc des boutons")


def test_confirmation_des_actions_sans_retour():
    print("\n[app] les actions sans retour demandent confirmation")
    html = open("docs/index.html", encoding="utf-8").read()

    # Aucune de ces actions n'avait de garde-fou, et « Oublier ce token »
    # est collé à « Enregistrer » et « Tester ».
    check('id="confirmOverlay"' in html, "le panneau de confirmation existe")
    check('role="alertdialog"' in html and 'aria-modal="true"' in html,
          "il s'annonce comme une alerte modale")

    # Au-dessus du panneau Paramètres (z-index 100), sinon la confirmation
    # se cacherait derrière son propre déclencheur.
    bloc = html[html.index(".overlay.confirm-overlay"):]
    bloc = bloc[:bloc.index("}")]
    check("z-index:200" in bloc,
          "il passe au-dessus du panneau Paramètres, d'où partent ces actions")

    # Chaque action irréversible passe par la confirmation, et AUCUNE ne
    # s'exécute avant la réponse.
    for nom in ("markAllRead", "forgetGithubToken", "resetSettings",
                "generateVapidKeys", "disablePush"):
        debut = html.index(f"function {nom}(")
        corps = html[debut:html.index("\n}", debut)]
        check("demandeConfirmation(" in corps,
              f"{nom} demande confirmation")
        check("if(!ok) return;" in corps,
              f"{nom} ne fait rien tant que ce n'est pas validé")
        check(corps.index("demandeConfirmation(") < corps.index("if(!ok) return;"),
              f"{nom} demande AVANT d'agir")

    # Le bouton ✓ des cartes en est volontairement exempté : on le clique des
    # dizaines de fois par jour, et un second clic l'annule déjà. Verrouillé
    # ici pour que personne ne l'y ajoute « par cohérence ».
    debut = html.index("function toggleRead(")
    check("demandeConfirmation" not in html[debut:html.index("\n}", debut)],
          "le ✓ d'une carte reste sans confirmation, c'est délibéré")

    # Les deux issues sûres : le focus part sur Annuler, Échap et le clic
    # à côté refusent. Une validation par mégarde doit être inoffensive.
    bloc = html[html.index("function demandeConfirmation("):
                html.index("function repondConfirmation(")]
    check('getElementById("confirmAnnuler")' in bloc and ".focus()" in bloc,
          "le focus arrive sur Annuler, pas sur l'action destructive")
    bloc = html[html.index("function _toucheConfirmation("):
                html.index("// Un clic à côté ferme")]
    check("repondConfirmation(false)" in bloc, "Échap refuse")
    bloc = html[html.index("function confirmationSurFond("):]
    bloc = bloc[:bloc.index("\n}")]
    check("repondConfirmation(false)" in bloc and "true" not in bloc,
          "un clic à côté refuse, jamais ne valide")

    # Le nombre annoncé doit être celui des articles qui vont VRAIMENT
    # changer d'état. Compter aussi les articles déjà lus annonçait « 247 »
    # sur un onglet où 29 seulement étaient non lus.
    debut = html.index("async function markAllRead(")
    corps = html[debut:html.index("\n}", debut)]
    check("articlesAffiches()" in corps,
          "le marquage en masse part de la MÊME liste que l'affichage")
    check("readSet.has(i.link)" in corps.split("demandeConfirmation(")[0],
          "et ne retient que les articles dont l'état va changer")
    check("vise.length" in corps.split("demandeConfirmation(")[1][:220],
          "c'est ce nombre-là qui est annoncé")
    check("affiches.length" in corps.split("demandeConfirmation(")[1][:320],
          "avec le total affiché en regard, pour repérer le mauvais onglet")
    check(corps.index("vise.length === 0") < corps.index("demandeConfirmation("),
          "et rien n'est demandé quand aucun article ne changerait")

    # La cause du défaut : markAllRead recopiait trois des six règles de
    # filtrage d'applyFilters, et les deux avaient dérivé. Une seule
    # définition, désormais — verrouillée ici.
    corps_af = html[html.index("function applyFilters("):
                    html.index("function applyFilters(") + 900]
    check("articlesAffiches()" in corps_af,
          "l'affichage passe par la même fonction, pas par une copie")
    filtre = html[html.index("function articlesAffiches("):]
    filtre = filtre[:filtre.index("\n}")]
    for regle, quoi in (("currentTab", "l'onglet"), ("currentLang", "la langue"),
                        ("currentFilter", "le filtre Non lus / Nouveaux"),
                        ("searchInput", "la recherche"),
                        ("settings.maxDisplay", "le plafond d'affichage")):
        check(regle in filtre, f"la liste affichée tient compte de {quoi}")


def test_haut_de_page_une_seule_carte():
    print("\n[app] une seule carte en haut, et le compteur sous les onglets")
    html = open("docs/index.html", encoding="utf-8").read()
    corps = html[html.index("<body>"):html.index('<div class="pull-zone"')]

    # Une seule carte : l'en-tête PORTE la classe .console au lieu d'être un
    # bloc distinct suivi d'un second. Deux cartes coûtaient une bordure, un
    # fond et deux rembourrages pour une frontière qui ne correspondait à
    # rien — les boutons agissent sur ce que le titre annonce.
    check('<header class="console">' in corps,
          "l'en-tête et la console ne forment plus qu'une carte")
    check(corps.count('class="console"') == 1,
          "et il n'y a bien qu'une seule carte en haut de page")
    for quoi in ('class="brand"', 'id="countdown"', 'id="modeIndicator"',
                 'class="controls"', 'id="runLine"', 'id="progressBar"'):
        check(corps.index(quoi) < corps.index("</header>"),
              f"{quoi} est dans la carte fusionnée")

    # Le compteur décrit la liste : il descend entre les onglets et elle.
    pos_onglets = corps.rindex('id="tabRockstarmag"')
    check(corps.index('id="countLine"') > pos_onglets,
          "le compteur est placé APRÈS la rangée d'onglets")
    check(corps.index('id="historyLine"') > pos_onglets,
          "« historique partiel » et son bouton le suivent — une seule phrase")
    check('id="countLine"' not in corps[:corps.index("</header>")],
          "et il ne reste rien de lui dans la carte du haut")

    # La pastille de « Tous les articles » est absorbée par la ligne, qui
    # dit explicitement ce que chaque nombre compte.
    check('id="badgeAll"' not in corps,
          "la pastille de « Tous les articles » a disparu du balisage")
    check("poseBadge(" in html and "if(el) el.textContent" in html,
          "et l'écriture des pastilles tolère son absence, sinon la première "
          "ligne aurait planté en emportant toutes les suivantes")
    check('" non lu"' in html or "non lu${" in html,
          "la ligne distingue les affichés des non-lus")

    # Piège CSS : la barre de progression est collée au bord de la carte par
    # des marges négatives calées sur son rembourrage. Désaccordées, elle
    # déborde ou laisse un liseré.
    import re
    pad = re.search(r"\.console\{[^}]*padding:(\d+)px", html)
    marge = re.search(r"\.console \.progress-bar\{margin:0 -(\d+)px -(\d+)px", html)
    check(pad and marge and pad.group(1) == marge.group(1) == marge.group(2),
          f"les marges de la barre de progression suivent le rembourrage de "
          f"la carte ({pad.group(1) if pad else '?'}px)")

    # Les deux bandeaux d'alerte étaient ENTRE les deux cartes. Ils ne
    # doivent pas se retrouver coincés dans l'en-tête fusionné.
    for banniere in ('id="staleBanner"', 'id="updateBanner"'):
        check(corps.index(banniere) > corps.index("</header>"),
              f"{banniere} est sorti de la carte, sous elle")


def test_panneau_parametres_intact():
    print("\n[app] réorganiser le panneau n'a perdu aucun élément piloté par le JS")
    import re
    html = open("docs/index.html", encoding="utf-8").read()
    deb = html.index("<!-- ---------- Panneau Paramètres ---------- -->")
    fin = html.index("<!-- ---------- Modale Aperçu article ---------- -->")
    panneau = html[deb:fin]
    js = html[html.index("<script>"):]

    # 23 des 24 éléments du panneau sont adressés par getElementById depuis le
    # JS. Déplacer les blocs entre onglets est exactement le geste qui en fait
    # disparaître un en silence : l'app continue de se charger, et le réglage
    # concerné ne répond simplement plus.
    ATTENDUS = [
        "settingsOverlay", "themeSystemBtn", "themeLightBtn", "themeDarkBtn",
        "backendUrlInput", "vapidSetupGroup", "vapidKeysBlock", "vapidPublicOut",
        "vapidPrivateOut", "pushGroup", "pushEnableBtn", "pushTestBtn",
        "pushDisableBtn", "pushStatusLine", "pushSubscriptionBlock",
        "pushSubscriptionText", "githubTokenInput", "tokenStatusLine",
        "keywordsInput", "excludeKeywordsInput", "sourceList", "maxDisplay",
        "simRange", "simValue",
    ]
    presents = set(re.findall(r'id="([^"]+)"', panneau))
    manquants = [i for i in ATTENDUS if i not in presents]
    check(not manquants, f"les {len(ATTENDUS)} identifiants du panneau sont toujours là"
                         + (f" — manquants : {manquants}" if manquants else ""))

    # Et qu'aucun ne soit référencé par le JS sans exister dans le balisage.
    orphelins = [i for i in re.findall(r'getElementById\("([^"]+)"\)', js)
                 if i in ATTENDUS and i not in presents]
    check(not orphelins, "aucun getElementById ne vise un élément disparu")

    # Chaque élément doit vivre dans exactement un onglet, sinon il serait
    # masqué en permanence ou affiché dans deux onglets à la fois.
    onglets = re.findall(r'data-onglet="([a-z]+)"', panneau)
    check(len(onglets) == 3, f"trois onglets déclarés (obtenu {len(onglets)})")
    check(len(set(onglets)) == 3, "leurs clés sont distinctes")
    cibles = set(re.findall(r'data-cible="([a-z]+)"', panneau))
    check(cibles == set(onglets),
          "chaque bouton d'onglet pointe vers un panneau existant, et réciproquement")

    # Un seul onglet visible au chargement, sinon deux se superposent.
    caches = len(re.findall(r'data-onglet="[a-z]+" hidden', panneau))
    check(caches == 2, f"deux onglets masqués au départ, un visible (obtenu {caches})")

    # La barre réutilise .tab : c'est ce qui garantit l'harmonie avec les
    # onglets d'articles. Une classe propre au panneau dériverait avec le temps.
    barre = re.search(r'<div class="panneau-tabs">(.*?)</div>', panneau, re.S).group(1)
    check(barre.count('class="tab') == 3,
          "les trois boutons utilisent la classe .tab du site")
    # Sur les boutons eux-mêmes, et non sur le conteneur .panneau-tabs qui,
    # lui, ne porte que la mise en page.
    boutons = re.findall(r'<button[^>]*>', barre)
    check(len(boutons) == 3, f"trois boutons dans la barre (obtenu {len(boutons)})")
    reinventes = [b for b in boutons
                  if not re.search(r'class="tab(?: active)?"', b)]
    check(not reinventes,
          "aucun bouton n'a de classe propre au panneau" +
          (f" — {reinventes}" if reinventes else ""))

    # Le balisage du panneau ne doit plus porter de style= long : il en avait
    # dix, plus que tout le reste du corps du document réuni.
    longs = [s for s in re.findall(r'style="([^"]*)"', panneau) if len(s) > 24]
    check(not longs, f"plus aucun style en ligne long dans le panneau (reste {len(longs)})")


for fn in (test_parse_date_key, test_sort_and_cap, test_normalize_stored_dates,
           test_merge_no_loss, test_merge_keeps_our_version, test_merge_normalizes_and_caps,
           test_merge_refuses_empty_local, test_feed_store_io,
           test_canonical_link, test_canonicalize_stored_links,
           test_push_payload, test_push_subscriptions, test_push_vapid_subject,
           test_push_masquage_endpoint,
           test_real_history,
           test_fetch_parallele_identique, test_chaine_youtube_rockstar,
           test_onglets_par_domaine, test_couverture_par_lien,
           test_chaine_youtube_rockstarmag,
           test_doublons_de_titre,
           test_garde_fou_archives,
           test_dedup_meme_passage,
           test_libelle_actu_majeure, test_promotion_entre_passages,
           test_recap_hebdomadaire, test_suivi_sources_muettes,
           test_identifiants_de_sources_uniques,
           test_chaines_par_hote,
           test_plafond_par_domaine, test_source_qui_plante,
           test_predecode_google_news,
           test_miniature_youtube, test_media_content_non_declare_reste_accepte,
           test_reparation_vignettes_stockees,
           test_sonde_decouvre_les_flux_declares,
           test_titres_numerotes_pas_fusionnes, test_similarite_ignore_le_nom_du_jeu,
           test_suffixe_du_media_appris, test_fusion_retroactive_des_ressemblances,
           test_index_compte_les_sources_supplementaires,
           test_miniatures_seulement_sur_les_nouveaux,
           test_recuperation_des_miniatures, test_description_video_youtube,
           test_fenetre_en_heures, test_source_renommee,
           test_titre_trop_court_n_attire_personne,
           test_audit_signale_la_croissance,
           test_filtre_par_mots_cles, test_jours_depuis,
           test_depots_pour_les_notifications,
           test_reparation_attributions_croisees, test_videos_archivees,
           test_archives_ecartees_avant_le_decodage,
           test_couverture_rockstar, test_archives_ne_notifient_pas,
           test_reprise_apres_echec_passager, test_reprise_choix_des_cas,
           test_validateurs_lies_a_leur_url,
           test_timeout_reseau, test_source_cassee_vs_muette,
           test_compteur_echecs_decodage,
           test_historique_entrees, test_diagnostic_redirection,
           test_panneau_parametres_intact, test_ligne_etat_sans_double_compte,
           test_confirmation_des_actions_sans_retour,
           test_haut_de_page_une_seule_carte,
           test_validation_avant_ecriture):
    fn()

print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} vérifications passées")
if FAILURES:
    print("ÉCHECS :")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("Tout est vert.")
