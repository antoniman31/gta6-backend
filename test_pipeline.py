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

import json
import os
import sys
import tempfile
import time
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
    import discord_notify
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
    import discord_notify
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
    """
    items = [
        _article(feed["name"], f"Exclu {feed['id']}", f"https://exemple.fr/{feed['id']}"),
        _article(feed["name"], "Le meme article partout", "https://exemple.fr/commun"),
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
    communs = [i for i in obtenu[0] if i["link"] == "https://exemple.fr/commun"]
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
    print("\n[sources] alerte quand une source tombe, et quand elle revient")
    import fetch_feeds

    seuil = fetch_feeds.DEAD_SOURCE_RUNS

    def sante(statut):
        return [{"id": "a", "name": "VG247", "status": statut}]

    # Une source muette pendant seuil-1 passages ne doit RIEN déclencher :
    # un 503 passager ou une coupure réseau ne sont pas une panne.
    compteurs = {}
    for passage in range(1, seuil):
        compteurs, alertes = fetch_feeds.suivre_sources_muettes(sante("muette"), compteurs)
        check(alertes == [], f"passage {passage}/{seuil} muet : pas encore d'alerte")
    check(compteurs.get("a") == seuil - 1, "le compteur suit les passages muets")

    # Le passage qui franchit le seuil alerte, une seule fois.
    compteurs, alertes = fetch_feeds.suivre_sources_muettes(sante("muette"), compteurs)
    check(len(alertes) == 1 and alertes[0]["type"] == "tombee",
          f"au {seuil}e passage muet, une alerte « tombée » part")
    check(alertes[0]["name"] == "VG247", "l'alerte nomme la source")

    for suite in range(2):
        compteurs, alertes = fetch_feeds.suivre_sources_muettes(sante("muette"), compteurs)
        check(alertes == [],
              "la panne se prolonge : aucune répétition (sinon 48 messages par jour)")

    # Le retour est annoncé, une fois.
    compteurs, alertes = fetch_feeds.suivre_sources_muettes(sante("ok"), compteurs)
    check(len(alertes) == 1 and alertes[0]["type"] == "retour", "le retour est annoncé")
    check(compteurs == {}, "compteur remis à zéro, et pas conservé pour rien dans feed.json")

    compteurs, alertes = fetch_feeds.suivre_sources_muettes(sante("ok"), compteurs)
    check(alertes == [], "une source qui va bien ne dit rien")

    # Une source qui hoquette sans jamais atteindre le seuil ne doit ni
    # alerter à la panne, ni alerter au retour.
    c = {}
    for statut in ("muette", "muette", "ok"):
        c, alertes = fetch_feeds.suivre_sources_muettes(sante(statut), c)
        check(alertes == [], f"hoquet ({statut}) : silence radio")

    # Un flux qui répond 304 est vivant : build_sources_health le classe
    # « ok », donc il ne doit jamais entrer dans le comptage.
    c, alertes = fetch_feeds.suivre_sources_muettes(sante("tarie"), {})
    check(alertes == [] and c == {},
          "une source « tarie » (vivante mais sans actu) n'est pas une panne")


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

for fn in (test_parse_date_key, test_sort_and_cap, test_normalize_stored_dates,
           test_merge_no_loss, test_merge_keeps_our_version, test_merge_normalizes_and_caps,
           test_merge_refuses_empty_local, test_feed_store_io,
           test_canonical_link, test_canonicalize_stored_links,
           test_push_payload, test_push_subscriptions, test_push_vapid_subject,
           test_push_masquage_endpoint,
           test_real_history,
           test_fetch_parallele_identique, test_garde_fou_archives,
           test_dedup_meme_passage,
           test_libelle_actu_majeure, test_promotion_entre_passages,
           test_recap_hebdomadaire, test_suivi_sources_muettes,
           test_identifiants_de_sources_uniques,
           test_chaines_par_hote,
           test_plafond_par_domaine, test_source_qui_plante,
           test_predecode_google_news):
    fn()

print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} vérifications passées")
if FAILURES:
    print("ÉCHECS :")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("Tout est vert.")
