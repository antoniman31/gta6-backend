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


def test_plafond_epargne_rockstar():
    print("\n[tri] le plafond ne retire jamais une publication de Rockstar")

    def art(lien, jour, officiel=False, rockstarmag=False, extra=0):
        item = article(lien, "2026-%02d-%02dT00:00:00+00:00" % (1 + jour // 28, 1 + jour % 28))
        item["official"] = officiel
        if rockstarmag:
            item["rockstarmag"] = True
        if extra:
            item["extraSources"] = [{"source": "s%d" % n} for n in range(extra)]
        return item

    # Le plus ancien de la liste est officiel : c'est le cas qui comptait,
    # puisque la troncature part par la fin. Ce sont justement les articles
    # de Rockstar qui sont les plus vieux de l'historique — l'annonce, le
    # premier trailer — donc les premiers qu'un plafond emporterait.
    items = feed_store.sort_items([
        art("officiel-vieux", 0, officiel=True),
        art("banal-1", 1), art("banal-2", 2), art("banal-3", 3),
        art("officiel-recent", 4, officiel=True),
        art("banal-4", 5),
    ])
    gardes, retires = feed_store.cap_items(items, max_size=3)
    liens = [i["link"] for i in gardes]
    check(retires == 3 and len(gardes) == 3,
          "le plafond est bien atteint : trois articles retirés sur six")
    check("officiel-vieux" in liens and "officiel-recent" in liens,
          "les deux publications de Rockstar sont là, y compris la plus ancienne")
    check(liens == ["banal-4", "officiel-recent", "officiel-vieux"],
          "ce sont les articles ordinaires les plus anciens qui partent")

    # L'ordre du plus récent au plus ancien doit survivre au retrait : la
    # suite du passage (fenêtre de dédup, publication) le tient pour acquis.
    dates = [feed_store.parse_date_key(i["date"]) for i in gardes]
    check(dates == sorted(dates, reverse=True),
          "la liste reste triée du plus récent au plus ancien")

    # Cas limite : pas assez d'articles ordinaires à retirer. Dépasser le
    # plafond vaut mieux que jeter ce qu'on a promis de garder — et surtout
    # la fonction ne doit pas boucler ni renvoyer un compte faux.
    que_des_officiels = feed_store.sort_items(
        [art("off-%d" % n, n, officiel=True) for n in range(5)])
    gardes, retires = feed_store.cap_items(que_des_officiels, max_size=2)
    check(len(gardes) == 5 and retires == 0,
          "cinq officiels sous un plafond de deux : aucun n'est retiré")

    # Et le mélange : on retire tout ce qu'on peut, sans descendre au
    # plafond, et le compte annoncé est celui des retraits réels.
    mixte = feed_store.sort_items(
        [art("off-%d" % n, n, officiel=True) for n in range(4)]
        + [art("banal-%d" % n, 10 + n) for n in range(2)])
    gardes, retires = feed_store.cap_items(mixte, max_size=3)
    check(retires == 2 and len(gardes) == 4,
          "les deux ordinaires partent, les quatre officiels restent")
    check(all(i.get("official") for i in gardes),
          "il ne reste que du Rockstar")

    # --- Les deux familles protégées ajoutées le 18/09/2026 ---
    #
    # Le plafond est passé de 20000 à 1500, donc il MORD désormais pour de
    # bon : ce qui n'était qu'une précaution théorique décide maintenant, à
    # chaque passage, de ce qui disparaît du fil. Trois familles sont
    # gardées pour la même raison — on ne les retrouve pas ailleurs.
    check(feed_store.item_protege({"official": True}),
          "un article de Rockstar est protégé")
    check(feed_store.item_protege({"rockstarmag": True}),
          "un article de RockstarMag est protégé")
    seuil = feed_store.HOT_SOURCE_THRESHOLD
    chaud = {"extraSources": [{"source": "s%d" % n} for n in range(seuil - 1)]}
    check(feed_store.item_protege(chaud),
          "une actu majeure (%d rédactions) est protégée" % seuil)
    tiede = {"extraSources": [{"source": "s%d" % n} for n in range(seuil - 2)]}
    check(not feed_store.item_protege(tiede),
          "une reprise par %d rédactions ne l'est pas" % (seuil - 1))
    check(not feed_store.item_protege({"title": "x"}),
          "un article ordinaire ne l'est pas")

    # Le cas qui compte : les protégés sont les PLUS ANCIENS, donc ceux
    # qu'une troncature par la fin emporterait en premier.
    vieux_proteges = feed_store.sort_items([
        art("rmag-vieux", 0, rockstarmag=True),
        art("chaud-vieux", 1, extra=seuil - 1),
        art("banal-1", 2), art("banal-2", 3), art("banal-3", 4),
    ])
    gardes, retires = feed_store.cap_items(vieux_proteges, max_size=3)
    liens = [i["link"] for i in gardes]
    check(retires == 2 and len(gardes) == 3,
          "le plafond est atteint sans toucher aux protégés")
    check("rmag-vieux" in liens and "chaud-vieux" in liens,
          "RockstarMag et l'actu majeure survivent malgré leur âge")
    check("banal-1" not in liens and "banal-2" not in liens,
          "ce sont bien les ordinaires les plus anciens qui partent")


def test_elagage_declare_au_garde_fou():
    print("\n[feed] abaisser le plafond ne doit pas faire échouer le passage")
    import feed_store

    # Le piège, trouvé en lisant le code avant de l'écrire : le garde-fou
    # refuse toute écriture qui perd plus de 10 % ET plus de 50 articles.
    # Faire passer le plafond de 20000 à 1500 retire la moitié du fil d'un
    # coup — le premier passage aurait donc planté, et le robot serait
    # tombé en échec sur une purge parfaitement voulue.
    avant = {"items": [{"link": str(i), "title": "t"} for i in range(3202)]}
    apres = {"items": [{"link": str(i), "title": "t"} for i in range(1500)]}
    retires = 1702

    feed_store.valide_avant_ecriture(apres, avant, elagues=retires)
    check(True, "une purge déclarée au garde-fou est acceptée")

    # Et le garde-fou n'est pas affaibli pour autant : il ne pardonne que ce
    # qui a été délibérément retiré ET compté par cap_items.
    for annonce, cas in ((0, "non déclarée"), (retires - 700, "sous-déclarée")):
        try:
            feed_store.valide_avant_ecriture(apres, avant, elagues=annonce)
            check(False, "une perte %s doit être refusée" % cas)
        except feed_store.FeedInvalide:
            check(True, "une perte %s est toujours refusée" % cas)

    # Un élagage négatif ou absurde ne doit pas devenir un passe-droit.
    try:
        feed_store.valide_avant_ecriture(apres, avant, elagues=-5000)
        check(False, "un élagage négatif ne doit pas tout autoriser")
    except feed_store.FeedInvalide:
        check(True, "un élagage négatif n'ouvre aucune brèche")

    # Le paramètre est bien passé par l'appelant : sans ça, tout ce qui
    # précède ne vaudrait que pour un appel qui n'existe pas.
    src = open("fetch_feeds.py", encoding="utf-8").read()
    check("valide_avant_ecriture(output, stored, elagues=dropped)" in src,
          "fetch_feeds déclare au garde-fou ce que cap_items a retiré")


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

    # Le plafond n'est plus un nombre fixe mais une profondeur bornée, donc
    # ce test ne peut plus dater ses articles n'importe quand : la fenêtre
    # décide. Les deux bornes sont vérifiées, puisque ce sont elles qui
    # tiennent le fichier — et non plus une constante unique.
    from datetime import datetime, timedelta, timezone
    maintenant = datetime.now(timezone.utc)

    # Tous dans la fenêtre, et en surnombre : on bute sur le PLAFOND dur.
    recents = {"items": [article(f"a{i}", maintenant.isoformat())
                         for i in range(feed_store.MAX_HISTORY_SIZE + 50)]}
    merged, _, removed = merge_feed.merge_feeds({"items": []}, recents)
    check(len(merged["items"]) == feed_store.MAX_HISTORY_SIZE and removed == 50,
          "le plafond dur s'applique aussi après fusion")

    # Tous hors fenêtre : on retombe sur le PLANCHER, jamais en dessous.
    vieux_iso = (maintenant - timedelta(days=feed_store.MAX_HISTORY_DAYS + 30)).isoformat()
    vieux = {"items": [article(f"v{i}", vieux_iso)
                       for i in range(feed_store.MIN_HISTORY_SIZE + 200)]}
    merged, _, removed = merge_feed.merge_feeds({"items": []}, vieux)
    check(len(merged["items"]) == feed_store.MIN_HISTORY_SIZE and removed == 200,
          "et le plancher tient quand plus rien n'est dans la fenêtre")


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



def test_push_survit_a_un_telephone_verrouille():
    print("\n[push] une notification doit survivre à un téléphone endormi")
    import sys as _sys, types, re, inspect
    import push_notify

    # Le défaut d'origine : send_all n'indiquait ni durée de vie ni urgence.
    # pywebpush envoie alors TTL: 0, et sa propre documentation dit ce que
    # ça veut dire — « discards the message immediately if the recipient is
    # unavailable ». Téléphone verrouillé, connexion suspendue par Android :
    # le message est jeté, définitivement. Rien n'arrivait au déverrouillage
    # parce qu'il n'existait plus.
    check(push_notify.TTL_RECAP > 0 and push_notify.TTL_OFFICIEL > 0,
          "les deux durées de vie sont non nulles (TTL: 0 = message jeté)")
    check(push_notify.TTL_OFFICIEL > push_notify.TTL_RECAP,
          "une annonce Rockstar survit plus longtemps qu'un récapitulatif")
    # Un récapitulatif périmé annoncerait un compte que le passage suivant a
    # déjà corrigé : sa durée de vie ne doit pas dépasser la cadence du robot.
    check(push_notify.TTL_RECAP <= 3600,
          "le récapitulatif ne survit pas au-delà du passage suivant")

    # Ce que send_all transmet VRAIMENT à pywebpush. Un faux module suffit :
    # il n'est importé qu'au moment de l'appel, jamais au chargement.
    appels = []

    faux = types.ModuleType("pywebpush")
    faux.WebPushException = type("WebPushException", (Exception,), {})
    faux.webpush = lambda **kw: appels.append(kw)
    ancien = _sys.modules.get("pywebpush")
    _sys.modules["pywebpush"] = faux
    try:
        abo = [{"endpoint": "https://exemple.test/x"}]
        push_notify.send_all(abo, {"title": "t"}, "cle",
                             ttl=push_notify.TTL_OFFICIEL,
                             urgence=push_notify.URGENCE_HAUTE)
        push_notify.send_all(abo, {"title": "t"}, "cle")
    finally:
        if ancien is None:
            _sys.modules.pop("pywebpush", None)
        else:
            _sys.modules["pywebpush"] = ancien

    check(len(appels) == 2, "les deux envois ont bien atteint pywebpush")
    officiel, defaut = appels
    check(officiel.get("ttl") == push_notify.TTL_OFFICIEL,
          "la durée de vie demandée est transmise telle quelle")
    check(officiel.get("headers", {}).get("Urgency") == "high",
          "une annonce Rockstar part en Urgency: high")
    check(defaut.get("ttl") == push_notify.TTL_RECAP,
          "sans précision, on retombe sur la durée du récapitulatif")
    check(defaut.get("headers", {}).get("Urgency") == "normal",
          "et sur l'urgence normale — tout marquer urgent est un abus que "
          "les services de push finissent par sanctionner")

    # Le piège qui reviendra : ajouter un appel à send_all en oubliant ces
    # deux arguments. Les valeurs par défaut le rendraient silencieux — donc
    # chaque appel doit les écrire, y compris le mode test.
    corps = inspect.getsource(push_notify)
    corps = re.sub(r"^\s*#.*$", "", corps, flags=re.M)
    corps = re.sub(r'"""(?:.|\n)*?"""', "", corps)
    # (?<!def ) écarte la définition : elle s'écrit exactement comme l'appel
    # du récapitulatif, au mot « def » près.
    appels_ecrits = re.findall(r"(?<!def )send_all\((?:[^()]|\([^()]*\))*\)", corps)
    check(len(appels_ecrits) >= 3,
          "les appels à send_all sont bien repérés (%d)" % len(appels_ecrits))
    for a in appels_ecrits:
        court = " ".join(a.split())[:60]
        check("ttl=" in a and "urgence=" in a,
              "cet appel dit sa durée de vie et son urgence : %s" % court)

    # Le mode test doit emprunter le MÊME chemin que le récapitulatif. Un
    # test plus favorable que la vraie notification ne teste rien : c'est ce
    # qui a masqué le TTL: 0 pendant des semaines, le bouton restant vert
    # parce qu'on teste toujours écran allumé.
    source_test = inspect.getsource(push_notify.mode_test)
    check("ttl=TTL_RECAP" in source_test and "urgence=URGENCE_NORMALE" in source_test,
          "le bouton « tester » envoie exactement comme le récapitulatif")

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
        infos, counts, inchanges, _refuses = fetch_feeds.merge_results(
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


def test_retirer_une_source_ne_laisse_pas_ses_articles():
    print("\n[données réelles] une source retirée ne laisse rien derrière elle")
    import json, collections, fetch_feeds
    if not os.path.exists("docs/feed.json"):
        print("  (ignoré : docs/feed.json introuvable)")
        return

    # Les DEUX fichiers, pas seulement le gros. `feed-recent.json` est
    # l'extrait de 300 articles que l'app télécharge EN PREMIER (voir
    # `urlAllegee()` dans index.html) : ne verrouiller que `feed.json`
    # laisserait le seul fichier réellement lu au démarrage hors de portée.
    # Le trou a été constaté le 15/09/2026, en résolvant le conflit du
    # retrait de VG247 — l'extrait annonçait encore 50 sources.
    fichiers = [f for f in ("docs/feed.json", "docs/feed-recent.json")
                if os.path.exists(f)]
    for chemin in fichiers:
        _verifie_flux_sans_source_fantome(chemin, fetch_feeds)


def _verifie_flux_sans_source_fantome(chemin, fetch_feeds):
    import json, collections
    d = json.load(open(chemin, encoding="utf-8"))
    nom = os.path.basename(chemin)

    # Retirer une source de FEEDS arrête les NOUVEAUX articles, mais ne
    # touche pas à ceux déjà stockés : la fusion les conserve, c'est même
    # tout son travail. Conséquence : une source supprimée continue
    # d'apparaître dans le flux, sous un nom qui n'existe plus nulle part
    # dans le code. Constaté le 15/09/2026 en retirant VG247 — 8 articles
    # de 2022 à 2024 seraient restés à traîner en bas du fil.
    #
    # Ces articles-là sont les pires à débusquer : ils sont vieux, donc en
    # bas du tri par date, donc invisibles à l'usage. Rien ne les signale.
    #
    # Un nom qui figure dans SOURCES_RENOMMEES n'est pas orphelin : il a un
    # successeur déclaré, et repare_noms_de_sources() le rebranche au
    # prochain passage. Le fichier publié, lui, porte forcément encore
    # l'ancien nom entre le renommage et ce passage — l'exiger corrigé ici
    # reviendrait à demander au test de prédire l'avenir. Ce qu'on interdit,
    # c'est le nom qui ne mène à RIEN : ni à une source, ni à un successeur.
    connus = {f["name"] for f in fetch_feeds.FEEDS}
    rebranchables = set(fetch_feeds.SOURCES_RENOMMEES)
    orphelins = collections.Counter(
        i["source"] for i in d["items"]
        if i["source"] not in connus and i["source"] not in rebranchables)
    check(not orphelins,
          "%s : aucun article ne vient d'une source absente de FEEDS%s"
          % (nom, "" if not orphelins
             else " — %s" % dict(orphelins.most_common(3))))

    # Et le successeur déclaré doit exister : une table de renommage qui
    # pointe vers un nom absent de FEEDS ne rebrancherait rien du tout, en
    # silence, tout en faisant passer le contrôle ci-dessus.
    morts = sorted(n for n, (cible, _) in fetch_feeds.SOURCES_RENOMMEES.items()
                   if cible not in connus)
    check(not morts,
          "chaque renommage déclaré vise une source qui existe%s"
          % ("" if not morts else " — %s" % morts))

    # Les « autres sources » portent le même nom que l'article principal.
    # Les oublier laissait un article afficher, sous son nom corrigé, des
    # reprises encore étiquetées à l'ancien.
    reprises = collections.Counter(
        a["source"] for i in d["items"] for a in (i.get("extraSources") or [])
        if a.get("source") and a["source"] not in connus
        and a["source"] not in rebranchables)
    check(not reprises,
          "%s : aucune reprise ne cite une source absente de FEEDS%s"
          % (nom, "" if not reprises else " — %s" % dict(reprises.most_common(3))))

    # Même chose pour les journaux de santé : une source retirée qui reste
    # dans sources_health se compterait dans le « 48/48 » du bandeau, et
    # afficherait éternellement une ligne tarie pour une source qui n'est
    # plus interrogée.
    ids = {f["id"] for f in fetch_feeds.FEEDS}
    for cle in ("sources_health", "sources_silence", "sources_entries_history",
                "sources_declining", "feed_http_state"):
        valeurs = d.get(cle) or []
        restants = ([s["id"] for s in valeurs if s["id"] not in ids]
                    if isinstance(valeurs, list)
                    else [k for k in valeurs if k not in ids])
        check(not restants,
              "%s : %s ne parle que de sources existantes%s"
              % (nom, cle, "" if not restants else " — reste %s" % restants[:3]))

    # Le compteur peut être EN RETARD sur FEEDS, jamais en avance, et
    # l'asymétrie est le fond du sujet.
    #
    # Écrit d'abord en égalité stricte, cette vérification a échoué au
    # premier AJOUT de sources (15/09/2026) : les fichiers annonçaient 47,
    # FEEDS en déclarait 50, et c'était parfaitement normal — le robot
    # n'avait pas encore tourné. Un fichier qui ignore une source neuve se
    # corrige tout seul au passage suivant ; rien n'est faux, rien n'est
    # perdu, il n'y a rien à faire.
    #
    # Le retard est donc toléré. L'avance, non : un compteur SUPÉRIEUR à
    # FEEDS veut dire que le fichier compte une source qui n'existe plus,
    # et celui-là ne se corrige pas tout seul. C'est exactement le défaut
    # que ce test entier a été écrit pour attraper, et les contrôles de
    # fantômes ci-dessus le nomment déjà source par source — cette ligne
    # n'est que le filet.
    compteur = d.get("sources_count")
    check(isinstance(compteur, int) and compteur <= len(fetch_feeds.FEEDS),
          "%s : le compteur du bandeau annonce %s sources, FEEDS en déclare "
          "%d — un retard est normal (le robot n'a pas encore tourné), une "
          "avance signalerait une source fantôme"
          % (nom, compteur, len(fetch_feeds.FEEDS)))
    # `total_articles` compte le flux ENTIER dans les deux fichiers : dans
    # l'extrait il dépasse donc volontairement le nombre de lignes présentes,
    # c'est lui qu'affiche le bandeau « 2 572 articles ». On le compare au
    # fichier complet, jamais à len(items) de l'extrait.
    complet = json.load(open("docs/feed.json", encoding="utf-8"))
    attendu = len(complet["items"])
    check(d.get("total_articles") == attendu,
          "%s : le total annoncé (%s) vaut le nombre d'articles réellement "
          "présents dans le flux complet (%d)"
          % (nom, d.get("total_articles"), attendu))
    check(len(d["items"]) <= attendu,
          "%s : l'extrait (%d) ne dépasse pas le flux complet (%d)"
          % (nom, len(d["items"]), attendu))


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
    # Le déluge doit dépasser le plafond, sinon le test ne vérifie rien —
    # il est donc dimensionné SUR le plafond plutôt que sur une constante
    # recopiée, qui devenait fausse à la première remontée du plafond.
    deluge = [art(h * 0.001, n)
              for n, h in enumerate(range(fetch_feeds.FENETRE_MAX * 2))]
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

    # Les mots-clés doivent rester exploitables : aucun vide, aucune
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
    import fetch_feeds
    from datetime import datetime, timedelta, timezone
    maintenant = datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc)

    def art(nom, jours, **extra):
        item = {"title": nom, "link": "https://a.fr/" + nom,
                "date": (maintenant - timedelta(days=jours)).isoformat()}
        item.update(extra)
        return item

    # Ce contrôle interrogeait le TEXTE de fetch_feeds — il cherchait la
    # ligne du filtre mot pour mot. Il a viré au rouge le 22/09/2026 alors
    # que le filtre avait été RENFORCÉ, parce que la ligne avait changé de
    # forme. Un test qui décrit une ligne de code plutôt qu'une propriété se
    # met en travers de sa propre amélioration : il interroge maintenant le
    # prédicat lui-même.
    nouveaux = [art("neuf", 0), art("vieux", 0, archive=True)]
    a_notifier = [i for i in nouveaux if fetch_feeds.merite_notification(i, maintenant)]
    check([i["title"] for i in a_notifier] == ["neuf"],
          "seul l'article réellement nouveau part en notification")

    # Le cas général, trouvé après coup : un article peut arriver tard sans
    # porter le drapeau `archive` — un flux qui ressert son fond, une
    # fenêtre qui s'élargit, une source ajoutée aujourd'hui. Le 22/09, 268
    # articles sont entrés d'un coup et le téléphone a annoncé « 268
    # nouveaux articles GTA 6 ».
    check(not fetch_feeds.merite_notification(art("tardif", 30), maintenant),
          "un article de 30 jours entre sans faire vibrer le téléphone")
    check(fetch_feeds.merite_notification(art("frais", 2), maintenant),
          "un article de 2 jours reste une nouvelle : un flux lent n'est pas puni")
    check(not fetch_feeds.merite_notification(
              {"title": "sans date", "link": "https://a.fr/x"}, maintenant),
          "sans date exploitable on se tait : le doute ne fait sonner personne")

    src = open("fetch_feeds.py", encoding="utf-8").read()
    check("write_new_items_file(a_notifier)" in src,
          "et c'est la liste filtrée qui est déposée, pas newly_added")

    # Les DEUX canaux, pas un seul. Le 22/09 la liste du push était filtrée
    # et le COMPTE annoncé sur Discord ne l'était pas : le récapitulatif a
    # dit « 268 nouveaux » là où le push n'en listait aucun.
    check('"articles": attente["articles"] + len(a_notifier)' in src,
          "le compte du récapitulatif Discord lit la même liste que le push")
    check("feed_store.articles_officiels(a_notifier)" in src,
          "et le compte d'officiels aussi, sinon les deux divergeraient")

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

    # Le bilan des sources tient maintenant dans un compteur « 46/50 », et il
    # se calcule par SOUSTRACTION des deux mêmes ensembles disjoints. Une
    # source ne peut donc être comptée en échec qu'une fois, quel que soit
    # son statut.
    check("sante.length - muettes - cassees.length" in fn,
          "le compteur retire muettes et cassées, une source ne compte qu'une fois")

    # Et une source « tarie » n'est pas un échec : elle répond parfaitement,
    # elle n'a simplement rien publié depuis longtemps. Rockstar publie par
    # à-coups ; la compter en panne afficherait « 46/50 » en permanence pour
    # un état parfaitement sain. Le compteur ne doit donc jamais la voir.
    check('"tarie"' not in fn,
          "les sources taries ne sont pas comptées en échec")

    # La ligne se place sous les deux boutons : au-dessus, elle séparait le
    # nombre d'articles des actions qui le modifient.
    carte = html[html.index('<header class="console">'):]
    carte = carte[:carte.index("</header>")]
    check(carte.index('id="runLine"') > carte.index('class="controls"'),
          "la ligne d'état est placée après le bloc des boutons")


def _luminance(hexa):
    """Luminance relative WCAG d'une couleur #rrggbb."""
    c = hexa.lstrip("#")
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    def canal(v):
        v = int(v, 16) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * canal(c[0:2]) + 0.7152 * canal(c[2:4]) + 0.0722 * canal(c[4:6])


def _contraste(a, b):
    la, lb = _luminance(a), _luminance(b)
    haut, bas = max(la, lb), min(la, lb)
    return (haut + 0.05) / (bas + 0.05)


def test_ergonomie_tactile():
    print("\n[app] les cibles tactiles et la saisie tiennent les règles mobiles")
    import re
    html = open("docs/index.html", encoding="utf-8").read()

    # 16 px minimum sur un champ de saisie. En dessous, Safari sur iOS zoome
    # la page tout seul à l'entrée du doigt dans le champ, et l'utilisateur
    # doit dézoomer à la main après chaque recherche.
    bloc = re.search(r"\.search-row input\s*\{([^}]*)\}", html).group(1)
    taille = int(re.search(r"font-size:\s*(\d+)px", bloc).group(1))
    check(taille >= 16,
          "le champ de recherche est à %d px (16 minimum, sinon zoom iOS)" % taille)

    # La « separation rule » : 8 px entre deux cibles tactiles voisines. Sans
    # elle, quatre boutons de 30 px de haut collés à 4 px se prennent à deux
    # sous le pouce.
    for regle, minimum in ((r"\.card-actions\{([^}]*)\}", 8),
                           (r"\.feed\.dense \.card-actions\{([^}]*)\}", 6)):
        bloc = re.search(regle, html).group(1)
        ecart = int(re.search(r"gap:\s*(\d+)px", bloc).group(1))
        check(ecart >= minimum,
              "écart entre cibles : %d px (minimum %d)" % (ecart, minimum))

    # Les zones de clic étendues par pseudo-élément. Le bouton reste petit à
    # l'œil — c'est la surface réactive qui grandit, sans déplacer le texte.
    #
    # La cible visée est 44 px : le minimum d'Apple HIG et de WCAG 2.5.5,
    # atteignable ici sans voler les clics du voisin. Material 3 demande 48,
    # que l'app ne suit délibérément pas — sa densité est un choix, et
    # aligner ses 132 éléments interactifs sur 48 px la détruirait.
    # Le seuil était écrit « >= 43 » alors que le message annonçait « 44 visé » :
    # .card-extra a valait exactement 43 et passait donc en se faisant passer
    # pour conforme. Chaque élément porte maintenant son vrai minimum, et le
    # calcul lit les QUATRE côtés de l'inset au lieu de supposer qu'il est
    # symétrique — c'est cette supposition qui masquait le défaut.
    def _inset(selecteur):
        """Les quatre débordements d'un ::after, en pixels et comptés vers
        l'extérieur : inset:-8px 0 0 donne haut=8, droite=0, bas=0, gauche=0.

        Écrit à la main plutôt qu'avec une expression unique parce que la
        première version cherchait « -?(\\d+)px » et perdait donc tous les
        zéros sans unité : sur « -8px 0 0 » elle ne voyait qu'une valeur,
        la recopiait sur les quatre côtés et annonçait 33 px de zone au lieu
        de 25, et 8 px de descente au lieu de 0. Un analyseur faux est pire
        qu'une valeur écrite en dur : il donne des chiffres crédibles.
        """
        m = re.search(re.escape(selecteur) + r"::after\{[^}]*inset:([^;}]*)", html)
        if not m:
            return None
        n = []
        for mot in m.group(1).split():
            t = re.fullmatch(r"(-?\d+)(?:px)?", mot)
            if t is None:
                return None          # unité inattendue : on refuse de deviner
            n.append(int(t.group(1)))
        if len(n) == 1: n = n * 4
        elif len(n) == 2: n = [n[0], n[1], n[0], n[1]]
        elif len(n) == 3: n = [n[0], n[1], n[2], n[1]]
        elif len(n) != 4: return None
        return {"haut": -n[0], "droite": -n[1], "bas": -n[2], "gauche": -n[3]}

    # L'analyseur lui-même est vérifié, sinon il peut mentir en silence.
    for texte, attendu in (("-7px -3px", (7, 3, 7, 3)),
                           ("-8px 0 0", (8, 0, 0, 0)),
                           ("-10px -2px", (10, 2, 10, 2)),
                           ("-13px 0", (13, 0, 13, 0))):
        html_essai, vrai_html = html, html
        html = ".essai::after{content:\"\"; inset:%s;}" % texte
        r = _inset(".essai")
        html = vrai_html
        check(r is not None and (r["haut"], r["droite"], r["bas"], r["gauche"]) == attendu,
              "inset:%s se lit haut=%d droite=%d bas=%d gauche=%d"
              % ((texte,) + attendu))

    for selecteur, hauteurVisuelle, minimum, norme in (
            (".card-mark", 30, 44, "WCAG 2.5.5"),
            (".card-extra a", 17, 24, "WCAG 2.5.8")):
        ins = _inset(selecteur)
        check(ins is not None,
              "%s étend sa zone de clic par un pseudo-élément" % selecteur)
        if ins:
            atteinte = hauteurVisuelle + ins["haut"] + ins["bas"]
            check(atteinte >= minimum,
                  "%s : zone de %d px de haut (%d minimum, %s)"
                  % (selecteur, atteinte, minimum, norme))

    # L'invariant qui manquait, et le seul qui aurait attrapé le défaut : deux
    # éléments de types DIFFÉRENTS qui se font face verticalement. Le lien
    # « + autre source » descendait de 13 px, la coche sous lui remonte de 7,
    # et il n'y a que 8 px entre les deux : un appui dans la bande commune
    # partait sur le mauvais élément. Les tests d'avant ne comparaient que
    # des voisins de même type, d'où l'angle mort.
    ecart = int(re.search(r"\.card-actions\{[^}]*margin-top:\s*(\d+)px", html,
                          re.S).group(1))
    bas_lien = _inset(".card-extra a")["bas"]
    haut_coche = _inset(".card-mark")["haut"]
    check(bas_lien + haut_coche < ecart,
          "le lien « autre source » descend de %d px, la coche remonte de %d, "
          "et il y a %d px entre eux — pas de recouvrement"
          % (bas_lien, haut_coche, ecart))

    # L'invariant qui a coûté une correction en trois temps. À 320 px sur une
    # carte À VIGNETTE, quatre boutons tombent à 39 px de large : la hauteur
    # est bonne, pas la largeur. J'ai d'abord autorisé l'enroulement — la
    # première version fabriquait 27 zones de clic superposées, la seconde
    # était simplement laide (le drapeau seul sur une deuxième rangée étirée,
    # sur 8 cartes sur 30, et seulement à 320 px : dès 340 px l'enroulement
    # ne se déclenche jamais). La rangée reste donc sur une ligne et c'est le
    # ::after qui déborde latéralement, sans rien changer au dessin.
    # Contrainte : deux boutons voisins sont séparés de l'écart de la rangée,
    # et chacun déborde de N — il faut écart > 2N, sinon les zones se
    # recouvrent et un appui dans la bande commune part sur le mauvais
    # bouton. C'est la même inégalité que pour les boutons d'entête.
    bloc = re.search(r"\.card-actions\{([^}]*)\}", html, re.S).group(1)
    check("flex-wrap:nowrap" in bloc,
          "la rangée de commandes d'une carte tient sur une seule ligne")
    lateral = re.search(r"\.card-mark::after\{[^}]*inset:-\d+px\s+-(\d+)px", html)
    check(lateral is not None,
          "et la zone de clic de la coche déborde AUSSI latéralement")
    if lateral:
        marge = int(lateral.group(1))
        # 39 px est la largeur mesurée dans le cas le plus serré : carte à
        # vignette, quatre boutons, 320 px de large.
        check(39 + 2 * marge >= 44,
              "cas le plus serré : 39 + 2×%d = %d px de zone (44 visé)"
              % (marge, 39 + 2 * marge))
        ecart = int(re.search(r"gap:\s*(\d+)px", bloc).group(1))
        check(ecart > 2 * marge,
              "écart de %d px entre deux coches pour %d px d'extension de "
              "chaque côté — les zones ne se recouvrent pas" % (ecart, marge))

    # La vignette est un BANDEAU pleine largeur au-dessus du texte, en mode
    # normal. Les carrés posés à côté du titre ont été essayés d'abord et
    # plafonnaient à 68 px à 320 px, trop petit pour qu'on distingue l'image ;
    # un carré à la hauteur de la carte, lui, ne converge pas — la hauteur
    # dépend de la largeur de la colonne, qui dépend de la vignette.
    haut = re.search(r"\n  \.card-top\{([^}]*)\}", html).group(1)
    check("flex-direction:column" in haut,
          "en mode normal la carte s'empile : la vignette passe au-dessus du texte")
    base = re.search(r"\n  \.card-thumb\{([^}]*)\}", html, re.S).group(1)
    check("aspect-ratio:16/9" in base.replace(" ", ""),
          "le bandeau est en 16:9, le format des images des sources — rien n'est rogné")

    # Le débordement du bandeau doit valoir EXACTEMENT le rembourrage de la
    # carte, sinon il s'arrête avant le bord ou dépasse. Les deux valeurs
    # vivent dans deux règles différentes : rien ne les relie, sauf ce test.
    rembourrage = re.search(r"\n  \.card\{[^}]*padding:\s*(\d+)px\s+(\d+)px", html, re.S)
    marge = re.search(r"margin:\s*-(\d+)px\s+-(\d+)px\s+0", base)
    check(rembourrage is not None and marge is not None,
          "le bandeau déborde le rembourrage de la carte par des marges négatives")
    if rembourrage and marge:
        check((marge.group(1), marge.group(2)) == (rembourrage.group(1), rembourrage.group(2)),
              "le débordement (-%s/-%s) vaut le rembourrage de la carte (%s/%s) — "
              "le bandeau va bien d'un bord à l'autre"
              % (marge.group(1), marge.group(2),
                 rembourrage.group(1), rembourrage.group(2)))

    # Le compact garde la vignette à côté du texte : c'est la raison d'être du
    # mode. Il doit défaire CHACUNE des propriétés du bandeau — en oublier une
    # suffit, et un aspect-ratio resté en 16:9 sur un carré de 80 px redonne
    # une bande.
    hautD = re.search(r"\.feed\.dense \.card-top\{([^}]*)\}", html).group(1)
    check("flex-direction:row" in hautD,
          "le mode compact remet la vignette à CÔTÉ du texte")
    dense = re.search(r"\.feed\.dense \.card-thumb\{([^}]*)\}", html, re.S).group(1)
    for propriete, attendu, pourquoi in (
            ("aspect-ratio", "auto", "sinon le carré de 80 px redevient une bande 16:9"),
            ("margin", "0", "sinon la vignette déborde encore la carte"),
            ("align-self", "auto", "sinon elle s'étire sur la hauteur"),
            ("border-radius", "var(--r-sm)", "sinon elle garde les angles vifs du bandeau")):
        trouve = re.search(propriete + r":\s*([^;}]+)", dense)
        check(trouve is not None and trouve.group(1).strip() == attendu,
              "le compact remet %s à %s — %s" % (propriete, attendu, pourquoi))

    cote = re.search(r"width:\s*(\d+)px", dense)
    hauteur = re.search(r"height:\s*(\d+)px", dense)
    check(cote is not None and hauteur is not None and cote.group(1) == hauteur.group(1),
          "vignette compacte carrée : %s×%s"
          % (cote.group(1) if cote else "?", hauteur.group(1) if hauteur else "?"))
    if cote:
        # Mesuré à 320 px : 80 px laisse les boutons à 40 (44 avec les 2 px
        # d'extension latérale), 88 en fait tomber 80 sous le seuil.
        check(int(cote.group(1)) <= 80,
              "vignette compacte de %s px (80 maximum à 320 px)" % cote.group(1))

    bloc = re.search(r"\.history-line button\{([^}]*)\}", html).group(1)
    mh = re.search(r"min-height:\s*(\d+)px", bloc)
    check(mh is not None and int(mh.group(1)) >= 44,
          "« Tout charger » fait au moins 44 px de haut")

    # Les commandes de l'application elle-même, pas seulement celles des
    # cartes. La passe précédente n'avait traité que l'intérieur des cartes :
    # « Actualiser », l'action principale, mesurait encore 34 px de haut, et
    # « Charger N de plus » 27, le plus petit élément de toute l'interface.
    for regle, nom in ((r"\.tab, \.controls button\{([^}]*)\}", "« Actualiser » et les onglets"),
                       (r"\.filters-trigger\{([^}]*)\}", "le bouton « Filtres »"),
                       (r"\.search-row input\{([^}]*)\}", "le champ de recherche"),
                       (r"button\.small\{([^}]*)\}", "« Charger N de plus »"),
                       (r"\.info-btn\{([^}]*)\}", "le bouton d'information")):
        bloc = re.search(regle, html).group(1)
        mh = re.search(r"min-height:\s*(\d+)px", bloc)
        check(mh is not None and int(mh.group(1)) >= 44,
              "%s fait au moins 44 px de haut" % nom)

    # Les quatre boutons d'entête gardent 34 px à l'œil et gagnent leurs
    # 44 px en ::after : les agrandir vraiment poussait la rangée à 194 px,
    # soit plus que les 172 px disponibles à côté du logo sur un écran de
    # 320 px. L'écart doit valoir au moins le double de l'extension, sinon
    # les zones de deux icônes voisines se chevauchent et un appui entre les
    # deux part sur la mauvaise.
    inset = re.search(r"\.icon-btn::after\s*\{[^}]*inset:\s*-(\d+)px", html)
    check(inset is not None, ".icon-btn étend sa zone de clic par un pseudo-élément")
    bloc = re.search(r"\.icon-btn\{([^}]*)\}", html).group(1)
    cote = int(re.search(r"width:\s*(\d+)px", bloc).group(1))
    check("position:relative" in bloc,
          ".icon-btn ancre son pseudo-élément (position:relative)")
    if inset:
        marge = int(inset.group(1))
        check(cote + 2 * marge >= 44,
              "boutons d'entête : zone de %d px de côté (44 visé)" % (cote + 2 * marge))
        ecart = int(re.search(r"\.header-actions\{[^}]*gap:\s*(\d+)px", html).group(1))
        check(ecart >= 2 * marge,
              "écart de %d px entre les boutons d'entête pour %d px d'extension "
              "de chaque côté — pas de chevauchement" % (ecart, marge))

    # Le retour à la ligne de l'entête. Sans lui, .header-actions porte
    # flex-shrink:0, refuse de se comprimer, et le badge de mode comme le
    # dernier bouton se font trancher par le bord de la carte à 320 px.
    bloc = re.search(r"\.header-haut\{([^}]*)\}", html).group(1)
    check("flex-wrap:wrap" in bloc,
          "l'entête passe à la ligne au lieu de déborder sur écran étroit")
    bloc = re.search(r"\.header-right\{([^}]*)\}", html).group(1)
    check("margin-left:auto" in bloc,
          "et le bloc de droite reste à droite une fois passé à la ligne")

    # Anti-patterns qui se lisent dans le balisage.
    viewport = re.search(r'<meta name="viewport"[^>]*>', html).group(0)
    check("user-scalable=no" not in viewport and "maximum-scale" not in viewport,
          "le zoom pincé reste autorisé (WCAG 1.4.4)")
    check("loadMoreBtn" in html,
          "la pagination est un bouton explicite, pas un défilement infini")

    # Plus rien sous 10 px : le plus petit rôle typographique défini par
    # Material 3 est 11sp, en dessous il n'y a plus de barème du tout.
    petits = re.findall(r"font-size:\s*([0-9]+)px", html)
    trop = sorted({int(x) for x in petits if int(x) < 10})
    check(not trop, "aucun texte sous 10 px" + (" (trouvé : %s)" % trop if trop else ""))


def test_structure_et_annonces():
    print("\n[app] la page a un plan de titres et annonce ce qu'elle fait")
    import re
    html = open("docs/index.html", encoding="utf-8").read()
    # Les commentaires sont retirés AVANT de compter : celui qui explique le
    # repère <main> cite « <h2> » et « <h3> » en toutes lettres, et sans ce
    # nettoyage le comptage des paires les prenait pour du balisage. Un test
    # de structure doit lire la structure, pas la prose qui la commente.
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)

    # Un plan de titres, pour pouvoir naviguer autrement qu'en défilant. La
    # page n'avait AUCUN h1 : la marque, les jours et les titres d'articles
    # étaient tous des <div>, et les deux seuls <h3> du fichier étaient
    # enfermés dans des boîtes de dialogue.
    check('<h1 class="brand">' in html,
          "la marque est le titre de niveau 1 de la page")
    check('<h2 class="day-label">' in html,
          "chaque jour est un titre de niveau 2")
    check('<h3 class="card-title">' in html,
          "chaque article est un titre de niveau 3")
    check(html.count("<h1") == 1,
          "un seul h1 dans toute la page (%d trouvé(s))" % html.count("<h1"))

    # Les balises ouvrantes et fermantes vont par paires : une <h3 class=…>
    # laissée fermée par </div> passerait inaperçue à l'œil et casserait le
    # plan pour un lecteur d'écran.
    for niveau in ("h1", "h2", "h3"):
        ouvre = len(re.findall(r"<%s[ >]" % niveau, html))
        ferme = html.count("</%s>" % niveau)
        check(ouvre == ferme,
              "%s : %d ouvertures pour %d fermetures" % (niveau, ouvre, ferme))

    # Le repère de contenu principal, cible du « sauter au contenu ».
    check(html.count("<main>") == 1 and html.count("</main>") == 1,
          "un repère <main> encadre le contenu")
    # Il doit contenir le fil, sinon il ne sert à rien.
    corps = html[html.index("<main>"):html.index("</main>")]
    check('<div class="feed" id="feed">' in corps,
          "et le fil d'articles est bien dedans")
    check('<div class="search-row">' in corps,
          "avec la recherche et les filtres qui le pilotent")

    # La région live. Sans elle, quatre lignes d'état se réécrivaient après
    # chaque actualisation sans que rien ne soit annoncé : visuellement la
    # réponse arrive en 83 ms, à l'oreille elle n'arrivait jamais.
    region = re.search(r'<div id="annonce"[^>]*>', html)
    check(region is not None, "une région live existe")
    if region:
        balise = region.group(0)
        check('role="status"' in balise, "elle porte role=\"status\"")
        check('aria-live="polite"' in balise, "et aria-live=\"polite\"")
        check('class="sr-only"' in balise, "et la classe qui la sort de l'écran")

    # sr-only doit masquer SANS retirer de l'arbre d'accessibilité :
    # display:none et visibility:hidden rendraient la région muette.
    bloc = re.search(r"\.sr-only\{([^}]*)\}", html, re.S).group(1)
    check("display:none" not in bloc and "visibility:hidden" not in bloc,
          "sr-only masque sans retirer de l'arbre d'accessibilité")
    check("position:absolute" in bloc and "1px" in bloc,
          "sr-only sort bien l'élément du flux")

    # Et elle doit être alimentée aux DEUX fins de parcours : le mode backend
    # et le mode direct. N'en brancher qu'une laisserait l'autre silencieuse.
    check(html.count("annonceRafraichissement();") == 2,
          "l'annonce est déclenchée par les deux modes, backend et direct "
          "(%d point(s) d'appel)" % html.count("annonceRafraichissement();"))
    check("function annonceRafraichissement()" in html,
          "et la fonction qui la compose existe")


def test_derniers_reports_tactiles_et_courbes():
    print("\n[app] les trois points laissés de côté sont réglés")
    import re
    html = open("docs/index.html", encoding="utf-8").read()

    # 1. Le lien de titre d'article. Il était en TEXTE EN LIGNE — 36 px sur
    # deux lignes, ~18 sur une — et c'était le cas excepté par WCAG 2.5.5
    # et 2.5.8. L'exception vaut pour un lien AU MILIEU d'une phrase, dont on
    # ne peut pas grossir la zone sans casser l'interligne du texte autour.
    # Le titre est seul sur ses lignes : elle ne s'applique pas vraiment.
    bloc = re.search(r"\.card-title a\{([^}]*)\}", html, re.S).group(1)
    check("display:block" in bloc,
          "le lien de titre est un bloc, plus du texte en ligne")
    mh = re.search(r"min-height:\s*(\d+)px", bloc)
    check(mh is not None and int(mh.group(1)) >= 44,
          "et sa zone de clic fait au moins 44 px")
    hauteur = int(mh.group(1)) if mh else 0

    # L'INVARIANT qui a coûté une passe. En mode dense le titre est coupé à
    # deux lignes ; si la hauteur imposée ne tombe pas juste sur ces deux
    # lignes, la boîte laisse apparaître un bout de troisième ligne tranché
    # en son milieu. Constaté sur 13 titres sur 30 avec l'interligne
    # d'origine : 44 px valaient 2,42 lignes de 18,2.
    bloc = re.search(r"\.feed\.dense \.card-title a\{([^}]*)\}", html, re.S).group(1)
    check("-webkit-line-clamp" in bloc,
          "la coupure à deux lignes porte sur le LIEN (le bloc casse celle du parent)")
    lignes = int(re.search(r"-webkit-line-clamp:\s*(\d+)", bloc).group(1))
    inter = int(re.search(r"line-height:\s*(\d+)px", bloc).group(1))
    check(lignes * inter == hauteur,
          "%d lignes × %d px d'interligne = %d px, soit exactement la hauteur "
          "imposée (%d) — sinon une ligne apparaît tranchée"
          % (lignes, inter, lignes * inter, hauteur))

    # 2. La coche des cartes en mode dense. Elle tombe à 24 px, il faut donc
    # 10 px de chaque côté et non 7. Écarté d'abord par crainte d'empiéter
    # sur la carte voisine — mesure faite, il reste 42 px sous la coche
    # jusqu'au premier élément CLIQUABLE de la carte suivante, et 20 px
    # au-dessus jusqu'au lien de titre. Les 5 px qu'on croyait bloquants
    # étaient ceux de la date, qui n'est pas une cible.
    cote = int(re.search(r"\.feed\.dense \.card-mark\{[^}]*height:\s*(\d+)px", html).group(1))
    marge = int(re.search(r"\.feed\.dense \.card-mark::after\{inset:-(\d+)px", html).group(1))
    check(cote + 2 * marge >= 44,
          "coche en mode dense : %d px visibles + 2×%d = %d px cliquables"
          % (cote, marge, cote + 2 * marge))

    # 3. Les courbes. Elles étaient toutes en `ease`, la valeur par défaut du
    # navigateur, là où le reste de l'échelle vient des jetons Material 3.
    for jeton, valeur in (("--courbe", "cubic-bezier(0.2, 0, 0, 1)"),
                          ("--courbe-entree", "cubic-bezier(0, 0, 0, 1)")):
        check("%s: %s;" % (jeton, valeur) in html,
              "%s vaut la courbe Material 3 %s" % (jeton, valeur))

    # Plus aucune transition ne doit utiliser un mot-clé du navigateur. Les
    # animations décoratives infinies gardent leur ease-in-out : une
    # respiration doit être symétrique, ces courbes-ci ne le sont pas.
    restes = re.findall(r"transition:[^;]*\b(?:ease-out|ease-in|ease)\b[^;]*", html)
    check(not restes,
          "aucune transition ne reste sur une courbe par défaut du navigateur"
          + (" (trouvé : %s)" % restes[:2] if restes else ""))
    pulsations = re.findall(r"animation:[^;]*infinite", html)
    check(all("ease-in-out" in x for x in pulsations),
          "les %d pulsations infinies gardent leur courbe symétrique" % len(pulsations))


def test_echelle_m3_verrouillee():
    print("\n[app] l'échelle Material 3 ne peut pas déraper en silence")
    import re
    html = open("docs/index.html", encoding="utf-8").read()

    # Les deux courbes étaient verrouillées depuis le report tactile, mais
    # PAS les rayons ni les durées : on pouvait passer --r-md de 12 à 10 px,
    # ou --t-moyen de 250 à 300 ms, et la suite restait verte. Un audit l'a
    # relevé le 10/09/2026 sans que ce soit corrigé sur le moment. Ces six
    # valeurs sont des crans M3 réels, relevés dans les fichiers de jetons de
    # Google (md-sys-shape et md-sys-motion, v0.192) — pas des chiffres ronds
    # choisis au jugé. Les changer doit être un acte délibéré, pas un
    # glissement.
    JETONS = (("--r-xs", "4px", "corner-extra-small"),
              ("--r-sm", "8px", "corner-small"),
              ("--r-md", "12px", "corner-medium"),
              ("--r-full", "9999px", "corner-full"),
              ("--t-court", ".15s", "duration-short3, 150 ms"),
              ("--t-moyen", ".25s", "duration-medium1, 250 ms"))
    for jeton, valeur, cran in JETONS:
        declare = re.search(r"%s:\s*([^;]+);" % re.escape(jeton), html)
        trouve = declare.group(1).strip() if declare else "absent"
        check(trouve == valeur,
              "%s vaut %s — le cran M3 %s (trouvé : %s)"
              % (jeton, valeur, cran, trouve))

    # Verrouiller les six valeurs ne sert à rien si une nouvelle règle écrit
    # son rayon en dur à côté de l'échelle : on aurait six jetons corrects et
    # un cinquième rayon sauvage dans la feuille. C'est CE contrôle qui a du
    # mordant, les six ci-dessus ne font que nommer la référence.
    #
    # Deux exceptions, et seulement deux : 50 % pour ce qui est réellement un
    # disque (une pastille ronde n'est pas un cran de l'échelle), et 0 pour
    # une remise à plat explicite — la vignette en bandeau annule le sien.
    rayons = [v.strip() for v in re.findall(r"border-radius:\s*([^;}]+)", html)]
    hors = [v for v in rayons
            if "var(--r-" not in v and v not in ("50%", "0")]
    check(not hors,
          "les %d rayons de la feuille passent tous par l'échelle "
          "(exceptions admises : 50%% pour un disque, 0 pour une remise à "
          "plat)%s" % (len(rayons),
                       " — en dur : %s" % hors[:3] if hors else ""))

    # Même raisonnement pour les durées. Les animations décoratives infinies
    # ne sont pas concernées : ce n'est pas du retour d'interaction, elles
    # gardent leur rythme propre, c'est écrit dans la feuille elle-même.
    durees = re.findall(r"transition:\s*([^;}]+)", html)
    en_dur = [d.strip() for d in durees if re.search(r"[\d.]+m?s", d)]
    check(not en_dur,
          "les %d transitions prennent leur durée dans l'échelle%s"
          % (len(durees),
             " — en dur : %s" % en_dur[:3] if en_dur else ""))

    # Un jeton déclaré et jamais employé est un jeton mort : il donne
    # l'illusion d'une échelle tenue alors que la feuille s'en passe.
    for jeton, _, _ in JETONS:
        emplois = len(re.findall(r"var\(%s\)" % re.escape(jeton), html))
        check(emplois > 0, "%s sert quelque part (%d emploi(s))"
              % (jeton, emplois))

    # Enfin le commentaire qui surplombe l'échelle, et qui cite les valeurs
    # en toutes lettres. Un commentaire qui dérive de son code est pire que
    # pas de commentaire : celui-ci sert de référence quand on se demande
    # d'où sort un 12. On vérifie donc qu'il dit bien ce que le code fait.
    cite = re.search(r"extra-small (\d+), small (\d+), medium (\d+), "
                     r"full (\d+)", html)
    check(cite is not None and [int(x) for x in cite.groups()] == [4, 8, 12, 9999],
          "le commentaire cite la même échelle de rayons que les jetons")
    # Le commentaire passe à la ligne entre les deux : \s+ et pas un espace.
    cite = re.search(r"short3 = (\d+) ms,\s+medium1 = (\d+) ms", html)
    check(cite is not None and [int(x) for x in cite.groups()] == [150, 250],
          "le commentaire cite les mêmes durées que les jetons")


def test_panneaux_sont_de_vrais_dialogues():
    print("\n[app] les cinq panneaux sont de vrais dialogues")
    import re
    html = open("docs/index.html", encoding="utf-8").read()
    sans_com = re.sub(r"<!--.*?-->", "", html, flags=re.S)

    # Lot A — rôle, modalité, nom. Avant, SEUL le panneau de confirmation les
    # avait ; on ouvrait les paramètres et un lecteur d'écran n'annonçait rien.
    panneaux = {
        "confirm-panel":  "alertdialog",
        "sheet-panel":    "dialog",
        "settings-panel": "dialog",
        "previewPanel":   "dialog",
    }
    for classe, role in panneaux.items():
        motif = r'(?:class|id)="[^"]*' + re.escape(classe) + r'[^"]*"[^>]*'
        balises = [m.group(0) for m in re.finditer(motif, sans_com)
                   if "role=" in m.group(0)]
        check(balises, "le panneau %s déclare un rôle" % classe)
        for b in balises:
            check('role="%s"' % role in b,
                  "%s a le rôle %s" % (classe, role))
            check('aria-modal="true"' in b, "%s est modal" % classe)
            check("aria-labelledby=" in b, "%s porte un nom" % classe)

    # Chaque aria-labelledby doit pointer sur un élément QUI EXISTE : un
    # renvoi vers un id absent laisse le dialogue sans nom, en silence.
    for m in re.finditer(r'aria-labelledby="([^"]+)"', sans_com):
        cible = m.group(1)
        check(('id="%s"' % cible) in sans_com,
              "le nom du dialogue renvoie à un élément réel (%s)" % cible)

    # Lot D — les titres de panneau sont des titres.
    for tid, libelle in (("titreParametres", "Paramètres"), ("titreInfos", "Informations"),
                         ("titreFiltres", "Filtres"), ("previewSource", "Aperçu")):
        check(re.search(r'<h2 id="%s"' % tid, sans_com) is not None,
              "le titre du panneau %s est un h2" % libelle)
    check(".settings-title h2{font:inherit" in html,
          "et il hérite de la typographie du panneau — rien ne bouge à l'œil")

    # Lots A et B — la mécanique commune, écrite UNE fois pour les cinq.
    for nom in ("debutDialogue", "finDialogue", "figeLeFond",
                "libereLeFond", "_toucheDialogue", "focusablesDe"):
        check("function %s(" % nom in html, "la mécanique commune fournit %s" % nom)

    for ouvre, ferme, idFond in (("openSettings", "closeSettings", "settingsOverlay"),
                                 ("openInfo", "closeInfo", "infoOverlay"),
                                 ("openFiltersSheet", "closeFiltersSheet", "filtersOverlay"),
                                 ("openPreview", "closePreview", "previewOverlay")):
        corps = html[html.index("function %s(" % ouvre):]
        corps = corps[:corps.index("\n}")]
        check('debutDialogue("%s"' % idFond in corps,
              "%s branche le dialogue" % ouvre)
        corps = html[html.index("function %s(" % ferme):]
        corps = corps[:corps.index("\n}")]
        check('finDialogue("%s")' % idFond in corps,
              "%s le débranche" % ferme)
    check('debutDialogue("confirmOverlay"' in html and 'finDialogue("confirmOverlay")' in html,
          "la confirmation utilise la MÊME mécanique que les quatre autres")

    # Le piégeage du focus. Il manquait aux CINQ, celui de confirmation
    # compris : passé le dernier élément, la tabulation repartait dans le fil
    # d'articles caché sous le voile.
    #
    # LIMITE ASSUMÉE de ce qui suit : ces quatre vérifications lisent le code,
    # elles ne l'exécutent pas. Un `return` glissé au début du piège les
    # laisserait toutes passer — essayé, elles passent. Elles constatent donc
    # que le piège est ÉCRIT, pas qu'il fonctionne. Son COMPORTEMENT est
    # mesuré dans un vrai navigateur : un tour complet de tabulation sur
    # chacun des cinq panneaux, zéro sortie. Ça, cette suite en Python sans
    # navigateur ne sait pas le faire, et prétendre le contraire serait pire
    # que de l'écrire ici.
    piege = html[html.index("function _toucheDialogue("):html.index("function debutDialogue(")]
    check('e.key !== "Tab"' in piege, "le piège intercepte la tabulation")
    check("e.shiftKey" in piege, "et la tabulation arrière")
    check("dernier.focus()" in piege and "premier.focus()" in piege,
          "il boucle du dernier au premier et inversement")
    check("!haut.panneau.contains(actif)" in piege,
          "et rattrape un focus qui se serait échappé du panneau")

    # Lot B — le fond ne défile plus, et la position est RENDUE.
    fige = html[html.index("function figeLeFond("):html.index("function libereLeFond(")]
    check("_defilementFige = window.scrollY" in fige,
          "la position de défilement est mémorisée avant de figer")
    check('position = "fixed"' in fige,
          "le fond est figé en position:fixed (overflow:hidden ne tient pas sur Safari iOS)")
    libere = html[html.index("function libereLeFond("):html.index("function _toucheDialogue(")]
    check("window.scrollTo(0, y)" in libere, "et elle est restituée à la fermeture")

    # LE piège qui a coûté une passe : rendre le focus à un bouton hors écran
    # fait défiler la page jusqu'à lui, ce qui DÉFAIT la restitution qu'on
    # vient de faire. Mesuré : 1500 px redevenaient 0.
    fin = html[html.index("function finDialogue("):]
    fin = fin[:fin.index("\nfunction ")]
    check("preventScroll: true" in fin,
          "le focus rendu ne fait pas défiler la page (preventScroll) — "
          "sans quoi la position restituée est aussitôt perdue")

    # Une pile et non une variable : une confirmation peut s'ouvrir par-dessus
    # les paramètres, et la fermer doit rendre le focus au panneau du dessous.
    check("_pileDialogues" in html and "_pileDialogues.push" in html,
          "les dialogues s'empilent (confirmation par-dessus paramètres)")
    check("_pileDialogues.length === 0" in html,
          "et le fond n'est libéré qu'au dernier fermé")


def test_recap_du_matin_couvre_la_nuit():
    print("\n[notif] le récapitulatif du matin annonce toute la nuit")
    import io, json as _json, contextlib, tempfile, os as _os, shutil
    import discord_notify, push_notify

    # Le libellé doit être LE MÊME par les deux chemins, sinon le
    # récapitulatif du matin ne ressemblerait pas à celui de la journée.
    a = {"title": "A", "official": True, "extraSources": [1, 2, 3]}
    b = {"title": "B"}
    check(feed_store.libelle_recap([a, b])
          == feed_store.libelle_recap_depuis_comptes(2, 1, 4),
          "compter les articles ou lire trois entiers donne le même texte")
    check("12 nouveaux" in feed_store.libelle_recap_depuis_comptes(12, 0, 1),
          "et le texte sait annoncer un nombre venu de l'arriéré")

    # L'arriéré lu depuis feed.json doit résister à n'importe quoi : une
    # version antérieure n'a pas le champ, et une valeur aberrante ne doit
    # pas faire annoncer n'importe quel nombre.
    import fetch_feeds
    vide = {"articles": 0, "officiels": 0, "sommet": 0}
    check(fetch_feeds.attente_lue({}) == vide, "un feed sans le champ repart de zéro")
    check(fetch_feeds.attente_lue({"attente_recap": "n_importe_quoi"}) == vide,
          "un champ qui n'est pas un objet aussi")
    check(fetch_feeds.attente_lue(
              {"attente_recap": {"articles": -3, "officiels": "x", "sommet": True}}) == vide,
          "et toute valeur aberrante retombe à zéro")
    check(fetch_feeds.attente_lue(
              {"attente_recap": {"articles": 7, "officiels": 1, "sommet": 4}})
          == {"articles": 7, "officiels": 1, "sommet": 4},
          "un arriéré sain est lu tel quel")

    # La fusion après conflit de push : un MAXIMUM, jamais une somme — les
    # deux côtés partent du même arriéré, les additionner le compterait deux
    # fois.
    import merge_feed
    fusion, _, _ = merge_feed.merge_feeds(
        {"items": [], "attente_recap": {"articles": 9, "officiels": 1, "sommet": 4}},
        {"items": [], "attente_recap": {"articles": 5, "officiels": 0, "sommet": 2}})
    check(fusion["attente_recap"] == {"articles": 9, "officiels": 1, "sommet": 4},
          "à la fusion, l'arriéré le plus élevé l'emporte (jamais la somme)")

    # ---- La nuit entière, bout en bout ----
    envoyes = []
    vrais = (discord_notify.send_discord_with_retry, push_notify.send_all,
             push_notify.check_subject, push_notify.load_subscriptions,
             discord_notify.DISCORD_WEBHOOK_URL)
    discord_notify.send_discord_with_retry = lambda e, t, **k: envoyes.append(("discord", e)) or True
    push_notify.send_all = lambda s_, c, k, **kw: (envoyes.append(("push", c)), ([], []))[1]
    push_notify.check_subject = lambda s_: True
    push_notify.load_subscriptions = lambda: [{"endpoint": "https://exemple.test/x"}]
    discord_notify.DISCORD_WEBHOOK_URL = "https://exemple.test/webhook"

    tmp = tempfile.mkdtemp()
    try:
        def passage(nouveaux, totaux, nuit):
            """Rejoue un passage : le robot a déposé ses fichiers, on notifie."""
            envoyes.clear()
            chemin = _os.path.join(tmp, "new.json")
            with open(chemin, "w", encoding="utf-8") as f:
                _json.dump(nouveaux, f)
            ctot = _os.path.join(tmp, "totaux.json")
            if totaux is None:
                if _os.path.exists(ctot):
                    _os.remove(ctot)
            else:
                with open(ctot, "w", encoding="utf-8") as f:
                    _json.dump(totaux, f)
            env = dict(NEW_ITEMS_FILE=chemin, RECAP_TOTALS_FILE=ctot,
                       SEULEMENT_OFFICIELS="1" if nuit else "0",
                       VAPID_PRIVATE_KEY="factice")
            anciens = {k: _os.environ.get(k) for k in env}
            _os.environ.update(env)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    discord_notify.main()
                    push_notify.main()
            finally:
                for k, v in anciens.items():
                    if v is None: _os.environ.pop(k, None)
                    else: _os.environ[k] = v
            return list(envoyes)

        banal = {"title": "Une rumeur", "link": "https://ex.test/1", "official": False}

        # 4 passages nocturnes, 3 articles chacun, aucun Rockstar : silence
        # total, et le robot empile 12 dans attente_recap.
        muets = sum(len(passage([banal] * 3, None, nuit=True)) for _ in range(4))
        check(muets == 0, "quatre passages nocturnes sans Rockstar : aucun envoi (%d)" % muets)

        # 5h : le passage ne trouve QUE 2 articles neufs, mais le robot lui a
        # déposé le total 14 — les 12 de la nuit plus ces 2.
        matin = passage([banal] * 2, {"articles": 14, "officiels": 1, "sommet": 2}, nuit=False)
        textes = [str(e.get("title", "")) for _, e in matin]
        check(len(matin) == 2, "le matin, un envoi par canal (%d)" % len(matin))
        check(all("14 nouveaux" in t for t in textes),
              "et il annonce les 14 de la nuit, pas les 2 de ce passage : %s" % textes)
        check(all("officiel" in t for t in textes),
              "l'officiel mis de côté est mentionné lui aussi")

        # Le cas qui justifie tout : à 5h, le passage ne trouve RIEN de neuf,
        # mais douze articles attendent d'être annoncés.
        rien = passage([], {"articles": 12, "officiels": 0, "sommet": 1}, nuit=False)
        check(len(rien) == 2,
              "un passage sans rien de neuf annonce quand même l'arriéré (%d envoi(s))"
              % len(rien))
        check(all("12 nouveaux" in str(e.get("title", "")) for _, e in rien),
              "et il annonce bien les 12")

        # LE cas que la première version jetait à la poubelle. La nuit
        # n'apporte AUCUN article neuf, mais un sujet déjà connu est repris
        # par une quatrième rédaction : il devient majeur. Le robot dépose
        # {articles:0, sommet:4} et l'alerte doit partir — c'est exactement
        # ce pour quoi le mécanisme `promus` avait été écrit.
        majeure = passage([], {"articles": 0, "officiels": 0, "sommet": 4}, nuit=False)
        check(len(majeure) == 2,
              "une actu devenue majeure la nuit SANS article neuf est quand même "
              "annoncée le matin (%d envoi(s))" % len(majeure))
        check(all("majeure" in str(e.get("title", "")) for _, e in majeure),
              "et elle est annoncée COMME majeure")

        # La règle « y a-t-il quelque chose à annoncer » vit à UN SEUL
        # endroit : le robot, qui ne dépose le fichier que dans ce cas. Les
        # notificateurs ne la rejouent pas — la rejouer à moitié est
        # exactement ce qui avait fait disparaître l'alerte ci-dessus.
        for fichier in ("discord_notify.py", "push_notify.py"):
            src = open(fichier, encoding="utf-8").read()
            check("totaux[0]" not in src,
                  "%s ne réinterprète pas le contenu des totaux" % fichier)

        # Sans fichier de totaux du tout, et sans rien de neuf : on se tait.
        check(len(passage([], None, nuit=False)) == 0,
              "rien de neuf et aucun total déposé : aucun envoi")

        # Sans le fichier de totaux (lancement local), on retombe sur le
        # comptage direct de la liste.
        local = passage([banal] * 3, None, nuit=False)
        check(all("3 nouveaux" in str(e.get("title", "")) for _, e in local),
              "sans fichier de totaux, le comptage direct prend le relais")
    finally:
        (discord_notify.send_discord_with_retry, push_notify.send_all,
         push_notify.check_subject, push_notify.load_subscriptions,
         discord_notify.DISCORD_WEBHOOK_URL) = vrais
        shutil.rmtree(tmp, ignore_errors=True)


def test_alerte_officielle_rockstar():
    print("\n[notif] une annonce de Rockstar a son alerte à elle")
    import re, io, json as _json, contextlib, tempfile, os as _os
    import discord_notify, push_notify

    officiel = {"title": "Grand Theft Auto VI: An Extended Look",
                "link": "https://www.rockstargames.com/newswire/extended-look",
                "source": "Rockstar Games (officiel EN)", "official": True}
    banal = {"title": "Une rumeur de plus", "link": "https://exemple.test/1",
             "source": "Google News (EN)", "official": False}

    # Le tri repose sur le drapeau posé par la SOURCE dans FEEDS, pas sur une
    # heuristique de contenu : c'est l'émetteur qui fait l'officialité.
    tries = feed_store.articles_officiels([banal, officiel, banal])
    check(tries == [officiel], "seuls les articles des sources officielles sont retenus")
    check(feed_store.articles_officiels([]) == [], "un lot vide ne retient rien")
    check(feed_store.articles_officiels(None) == [], "et un lot absent non plus")

    # Le texte est écrit UNE fois et partagé, comme le récapitulatif : les
    # deux canaux ne peuvent pas diverger au premier ajustement.
    entete, titre = feed_store.libelle_officiel(officiel)
    check("Rockstar" in entete, "l'entête nomme Rockstar")
    check(titre == officiel["title"],
          "et le TITRE de l'article apparaît — contrairement au récapitulatif, "
          "qui n'annonce qu'un nombre")
    charge = push_notify.build_payload_officiel(officiel)
    check(charge["title"] == entete and charge["body"] == titre,
          "la notification push reprend exactement ce texte partagé")
    check(charge["url"] == officiel["link"],
          "et mène à l'ARTICLE, pas à l'accueil du site")

    # LE point qui rend l'alerte utile. Le récapitulatif utilise un tag
    # commun qui REMPLACE la notification précédente ; sans tag propre,
    # l'annonce d'un trailer serait effacée en silence par le récapitulatif
    # du passage suivant, une demi-heure plus tard.
    banal_charge = push_notify.build_payload([officiel, banal])
    check(charge["tag"] != banal_charge["tag"],
          "son tag diffère de celui du récapitulatif — sinon le récapitulatif "
          "suivant l'effacerait")
    autre = dict(officiel, link="https://www.rockstargames.com/newswire/autre")
    check(charge["tag"] != push_notify.build_payload_officiel(autre)["tag"],
          "et deux annonces du même passage ne s'écrasent pas l'une l'autre")
    check(charge["tag"] == push_notify.build_payload_officiel(dict(officiel))["tag"],
          "le tag est stable pour un même article")

    # Le récapitulatif continue de les COMPTER : il annonce un volume, les
    # alertes annoncent un contenu. L'amputer le ferait mentir.
    libelle = feed_store.libelle_recap([officiel, banal])
    check("officiel" in libelle,
          "le récapitulatif mentionne toujours les officiels (%s)" % libelle)

    # Le mode nuit, bout en bout : on capture ce que chaque script DÉCIDE
    # d'envoyer, sans réseau — les deux envoient via des fonctions qu'on
    # remplace le temps du test.
    envoyes = []
    vrai_discord = discord_notify.send_discord_with_retry
    vrai_push = push_notify.send_all
    vrai_check = push_notify.check_subject
    vraie_charge = push_notify.load_subscriptions
    discord_notify.send_discord_with_retry = lambda e, t, **k: envoyes.append(("discord", e)) or True
    push_notify.send_all = lambda subs, charge, cle, **kw: (envoyes.append(("push", charge)), ([], []))[1]
    push_notify.check_subject = lambda s_: True
    push_notify.load_subscriptions = lambda: [{"endpoint": "https://exemple.test/x"}]
    discord_notify.DISCORD_WEBHOOK_URL = "https://exemple.test/webhook"

    tmp = tempfile.mkdtemp()
    chemin = _os.path.join(tmp, "new.json")
    with open(chemin, "w", encoding="utf-8") as f:
        _json.dump([officiel, banal], f)

    def rejoue(nuit):
        envoyes.clear()
        env = dict(NEW_ITEMS_FILE=chemin, SEULEMENT_OFFICIELS="1" if nuit else "0",
                   VAPID_PRIVATE_KEY="factice")
        anciens = {k: _os.environ.get(k) for k in env}
        _os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                discord_notify.main()
                push_notify.main()
        finally:
            for k, v in anciens.items():
                if v is None: _os.environ.pop(k, None)
                else: _os.environ[k] = v
        return list(envoyes)

    try:
        nuit = rejoue(True)
        jour = rejoue(False)
    finally:
        discord_notify.send_discord_with_retry = vrai_discord
        push_notify.send_all = vrai_push
        push_notify.check_subject = vrai_check
        push_notify.load_subscriptions = vraie_charge
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    def textes(lot, canal):
        return [(e.get("title", "") + " " + str(e.get("description", "") or e.get("body", "")))
                for c, e in lot if c == canal]

    check(len(nuit) == 2,
          "la nuit, exactement 2 envois — un Discord, un push — et rien d'autre "
          "(obtenu : %d)" % len(nuit))
    check(all("Extended Look" in t for t in textes(nuit, "discord") + textes(nuit, "push")),
          "et les deux portent le titre de l'annonce Rockstar")
    check(not any("rumeur" in t.lower() for t in textes(nuit, "discord") + textes(nuit, "push")),
          "l'article ordinaire du même lot reste muet la nuit")

    check(len(jour) == 4,
          "le jour, 4 envois : l'alerte officielle ET le récapitulatif, sur les "
          "deux canaux (obtenu : %d)" % len(jour))
    check(any("nouv" in t for t in textes(jour, "discord")),
          "le récapitulatif part bien en journée")


def test_pause_nocturne():
    print("\n[workflow] le robot ne tourne pas la nuit, heure de Paris")
    import re, subprocess, tempfile, os, shutil
    brut = open(".github/workflows/update-feeds.yml", encoding="utf-8").read()

    # Lecture en TEXTE et non avec PyYAML : la CI n'installe que
    # requirements.txt, qui ne le contient pas. Un `import yaml` passerait
    # ici et ferait rougir la CI — c'est exactement ce qui a failli arriver.
    corps = brut[brut.index("    steps:"):]
    blocs = re.split(r"\n      - name: ", corps)[1:]
    etapes = []
    for b in blocs:
        nom = b.split("\n", 1)[0].strip()
        cond = re.search(r"^        if: (.+)$", b, re.M)
        etapes.append((nom, cond.group(1).strip() if cond else ""))
    parNom = dict(etapes)
    # Bloc complet indexé par NOM. Par POSITION jusqu'au 15/09/2026, et
    # c'était un piège : insérer une étape au milieu faisait échouer une
    # assertion qui n'avait rien à voir avec elle, en annonçant un défaut
    # inexistant sur « Notifier Discord ». Un test doit désigner ce qu'il
    # vérifie, pas l'endroit où il se trouvait ce jour-là.
    blocParNom = {b.split("\n", 1)[0].strip(): b for b in blocs}

    check(etapes[0][0] == "Fenêtre de veille",
          "la décision est prise AVANT tout le reste (première étape)")

    # Le passage TOURNE la nuit : depuis que les annonces officielles de
    # Rockstar doivent réveiller, on ne peut plus sauter la récupération —
    # il faut bien récupérer pour savoir s'il y en a une. C'est la
    # notification, et elle seule, qui se tait.
    for nom in ("Récupérer le dépôt", "Installer Python", "Installer les dépendances",
                "Récupérer et traiter les flux", "Publier le résultat"):
        check(parNom.get(nom, "") == "",
              "« %s » tourne aussi la nuit" % nom)

    check(parNom.get("Signaler que le robot est vivant") == "always()",
          "le signal de vie part quoi qu'il arrive")

    # Les deux notifications tournent toujours, et reçoivent le drapeau qui
    # les fait taire — sauf pour Rockstar.
    for nom in ("Notifier Discord", "Notifier par push"):
        check(parNom.get(nom, "") == "success()",
              "« %s » ne notifie qu'après une publication réussie" % nom)
    for nom in ("Notifier Discord", "Notifier par push"):
        bloc = blocParNom.get(nom, "")
        check("SEULEMENT_OFFICIELS" in bloc and "steps.veille.outputs.silence" in bloc,
              "« %s » reçoit le drapeau de silence nocturne" % nom)

    # L'attente de mise en ligne s'intercale entre la publication et les
    # notifications. L'ordre est tout : après le push, sinon on attend une
    # version qu'on n'a pas encore poussée ; avant les notifications, sinon
    # elle ne sert à rien.
    noms = [n for n, _ in etapes]
    attente = "Attendre que la page soit réellement en ligne"
    check(attente in noms, "l'attente de mise en ligne existe")
    if attente in noms:
        check(noms.index("Publier le résultat") < noms.index(attente) < noms.index("Notifier Discord")
              and noms.index(attente) < noms.index("Notifier par push"),
              "elle s'intercale entre la publication et les deux notifications")
        bloc = blocParNom[attente]
        check("feed-recent.json" in bloc,
              "elle interroge l'extrait que l'app charge en premier")
        check("generated_at" in bloc,
              "elle compare la VERSION servie, pas seulement que le fichier réponde")
        # Le plafond existe et l'étape sort en 0 : une mise en ligne lente
        # ne doit jamais faire perdre la notification.
        check("ATTENTE_MAX" in bloc and "exit 0" in bloc,
              "un plafond existe, et l'étape n'échoue jamais le passage")

    bloc_veille = blocs[0]
    check("TZ=Europe/Paris" in bloc_veille,
          "l'heure est calculée en Europe/Paris et non à un décalage fixe "
          "(sinon la fenêtre glisserait au changement d'heure)")
    apres_panne = bloc_veille.split("indéterminable")[1].split("exit 0")[0] \
        if "indéterminable" in bloc_veille else ""
    check("silence=false" in apres_panne,
          "un garde en panne laisse PASSER — il doit rater une pause, "
          "jamais bloquer le robot pour toujours")

    # La date de sortie est écrite à deux endroits. Elle ne doit pas diverger.
    debut_exc = re.search(r'EXCEPTION_DEBUT: "(\d{4}-\d{2}-\d{2})"', bloc_veille).group(1)
    fin_exc = re.search(r'EXCEPTION_FIN: "(\d{4}-\d{2}-\d{2})"', bloc_veille).group(1)
    app = open("docs/index.html", encoding="utf-8").read()
    sortie = re.search(r'GTA6_RELEASE = new Date\("(\d{4}-\d{2}-\d{2})', app).group(1)
    check(debut_exc <= sortie <= fin_exc,
          "la fenêtre d'exception (%s → %s) encadre la sortie annoncée par "
          "l'app (%s)" % (debut_exc, fin_exc, sortie))

    # Le bandeau « robot en retard » doit tolérer la pause, sinon il
    # s'allumerait chaque nuit pour annoncer une panne qui n'existe pas.
    # Le robot publiant désormais TOUTE la nuit, generated_at avance chaque
    # heure sans interruption : il n'y a plus d'écart nocturne à tolérer, et
    # laisser le seuil haut retarderait la détection d'une vraie panne pour
    # rien. Il était monté à 7h le temps que la pause saute les passages.
    seuil = int(re.search(r"const STALE_THRESHOLD_MS = (\d+) \* 60 \* 60 \* 1000", app).group(1))
    check(seuil <= 4,
          "le bandeau « robot en retard » est revenu à un seuil serré : %dh "
          "(la pause ne saute plus aucun passage)" % seuil)

    # Et on EXÉCUTE le garde, à des instants choisis, avec un faux `date`. Un
    # test qui lit le script sans le lancer ne prouve rien sur des
    # comparaisons de chaînes en shell.
    script_src = bloc_veille.split("        run: |\n", 1)[1]
    script = "\n".join(l[10:] if l.startswith(" " * 10) else l
                       for l in script_src.split("\n"))
    tmp = tempfile.mkdtemp()
    try:
        chemin = os.path.join(tmp, "garde.sh")
        open(chemin, "w", encoding="utf-8").write(script)
        faux = os.path.join(tmp, "bin")
        os.makedirs(faux)
        d = os.path.join(faux, "date")
        open(d, "w").write('#!/bin/bash\nexec /bin/date -d "$FAUX_INSTANT UTC" "$@"\n')
        os.chmod(d, 0o755)

        def verdict(instant, declencheur):
            sortieFic = os.path.join(tmp, "out")
            open(sortieFic, "w").close()
            env = dict(os.environ,
                       PATH=faux + os.pathsep + os.environ["PATH"],
                       FAUX_INSTANT=instant, GITHUB_OUTPUT=sortieFic,
                       DECLENCHEUR=declencheur,
                       EXCEPTION_DEBUT=debut_exc, EXCEPTION_FIN=fin_exc)
            subprocess.run(["bash", chemin], env=env, capture_output=True)
            return open(sortieFic, encoding="utf-8").read().strip()

        # Déclencheurs AUTOMATIQUES : cron-job.org (repository_dispatch) et le
        # filet de GitHub (schedule). Ce sont eux, et eux seuls, que la pause
        # concerne.
        cas = [
            ("2026-09-07 21:30", "false", "23h30 en été"),
            ("2026-09-07 22:00", "true",  "minuit pile en été"),
            ("2026-09-08 02:59", "true",  "4h59 en été"),
            ("2026-09-08 03:00", "false", "5h00 en été"),
            ("2026-11-14 23:30", "true",  "00h30 en HIVER — le décalage a changé"),
            ("2026-11-15 04:00", "false", "5h00 en hiver"),
            ("2026-11-19 02:00", "false", "nuit de la sortie : pause levée"),
            ("2026-11-21 01:00", "true",  "lendemain de la fenêtre : pause revenue"),
        ]
        for instant, attendu, libelle in cas:
            for declencheur in ("repository_dispatch", "schedule"):
                obtenu = verdict(instant, declencheur)
                check(obtenu == "silence=" + attendu,
                      "%s (%s) → %s%s" % (libelle, declencheur, "silence=" + attendu,
                                          "" if obtenu == "silence=" + attendu
                                          else "  OBTENU : " + (obtenu or "rien")))

        # UNE DEMANDE À LA MAIN PASSE TOUJOURS. Le bouton « Relancer le robot »
        # de l'app poste sur /actions/workflows/…/dispatches, donc
        # workflow_dispatch, exactement comme le bouton « Run workflow » de
        # GitHub. La pause existe pour que le robot ne réveille personne de
        # lui-même, pas pour refuser un ordre explicite.
        #
        # On balaie les VINGT-QUATRE heures, pas seulement quelques-unes :
        # c'est la garantie demandée, elle doit être vérifiée partout.
        rates = []
        for h in range(24):
            for jour, saison in (("2026-09-08", "été"), ("2026-12-08", "hiver")):
                instant = "%s %02d:30" % (jour, h)
                if verdict(instant, "workflow_dispatch") != "silence=false":
                    rates.append("%s %s" % (instant, saison))
        check(not rates,
              "un déclenchement manuel passe aux 24 heures, été comme hiver"
              + (" (bloqué à : %s)" % ", ".join(rates[:4]) if rates else ""))

        # Et le contrôle inverse : aux mêmes instants, un déclencheur
        # automatique DOIT être bloqué la nuit. Sans ça, le test ci-dessus
        # passerait tout aussi bien si la pause ne marchait plus du tout.
        bloques = sum(1 for h in range(5)
                      if verdict("2026-09-08 %02d:30" % ((h - 2) % 24), "schedule") == "silence=true")
        check(bloques == 5,
              "aux mêmes heures, l'automatique est bien mis en pause "
              "(%d/5 — sinon le test du manuel ne prouverait rien)" % bloques)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_filtres_persistants():
    print("\n[app] les filtres survivent à la fermeture de l'app")
    import re
    html = open("docs/index.html", encoding="utf-8").read()

    check('const CLE_FILTRES = "filtres-v1";' in html,
          "les filtres ont leur propre clé de stockage")

    # LE point de sécurité. saveSettings() lit les champs du panneau de
    # paramètres dans le DOM ; l'appeler depuis un clic sur un onglet
    # écraserait l'URL du backend et les mots-clés avec des champs
    # éventuellement vides. Les trois setters ne doivent JAMAIS y toucher.
    for nom in ("setTab", "setLang", "setFilter"):
        corps = re.search(r"function %s\([^)]*\)\{(.*?)\n\}" % nom, html, re.S).group(1)
        check("sauvegardeFiltres()" in corps,
              "%s sauvegarde les filtres" % nom)
        check("saveSettings" not in corps,
              "%s ne touche PAS à saveSettings (il écraserait l'URL du backend)" % nom)
        check("silencieux" in corps,
              "%s sait poser l'état sans redessiner (restauration en un seul rendu)" % nom)

    corps = re.search(r"async function loadState\(\)\{(.*?)\n\}", html, re.S).group(1)
    check("restaureFiltres()" in corps, "loadState restaure les filtres")
    check(corps.index("restaureFiltres()") < corps.index("applyFilters()"),
          "et il les restaure AVANT de dessiner le fil")

    # Les deux exclusions volontaires.
    etats = re.search(r"const ETATS_MEMORISES = \[([^\]]*)\]", html).group(1)
    check('"new"' not in etats,
          "« Nouveaux » n'est pas restauré : il s'appuie sur lastNewLinks, "
          "vidé à chaque ouverture, donc il rouvrirait sur un fil vide")
    onglets = re.search(r"const ONGLETS_MEMORISES = \[([^\]]*)\]", html).group(1)
    check('"logs"' not in onglets,
          "l'onglet du journal n'est pas restauré")
    check('"all"' in etats and '"unread"' in etats,
          "mais « Tout » et « Non lus » le sont")
    for t in ('"all"', '"non-rockstar"', '"rockstar"', '"rockstarmag"'):
        check(t in onglets, "onglet mémorisé : %s" % t)

    # Un détour par un état non mémorisable ne doit pas effacer le choix
    # d'avant : ouvrir le journal puis fermer l'app faisait sinon rouvrir sur
    # « Tous les articles » alors qu'on était sur « Rockstar ».
    corps = re.search(r"function sauvegardeFiltres\(\)\{(.*?)\n\}", html, re.S).group(1)
    check("ongletMemorise" in corps and "etatMemorise" in corps,
          "un détour par le journal ou par « Nouveaux » n'efface pas le choix précédent")
    check("restaurationFiltres" in corps,
          "et la restauration elle-même ne réécrit pas un état à moitié posé")

    # Rien ne doit être restauré qui ne soit pas dans les listes : une clé
    # corrompue ou écrite par une version antérieure retombe sur le défaut.
    corps = re.search(r"function restaureFiltres\(\)\{(.*?)\n\}", html, re.S).group(1)
    for liste in ("ONGLETS_MEMORISES", "LANGUES_MEMORISEES", "ETATS_MEMORISES"):
        check("%s.includes" % liste in corps,
              "restaureFiltres valide la valeur lue contre %s" % liste)
    check("try{" in corps and "catch" in corps,
          "et une clé illisible ne fait pas planter l'ouverture")

    # La recherche, elle, n'est PAS mémorisée : un mot-clé oublié dans la
    # barre filtre le fil sans qu'on s'en rende compte, bien moins
    # visiblement qu'une pastille d'onglet allumée.
    corps = re.search(r"function sauvegardeFiltres\(\)\{(.*?)\n\}", html, re.S).group(1)
    check("searchQuery" not in corps and "searchInput" not in corps,
          "le texte de recherche n'est pas mémorisé")


def test_icones_en_emoji():
    print("\n[app] les icônes sont des emojis, sauf là où la couleur porte du sens")
    import re
    html = open("docs/index.html", encoding="utf-8").read()
    sans_commentaires = re.sub(r"<!--.*?-->", "", html, flags=re.S)

    # Les glyphes Unicode d'origine ne doivent plus rien étiqueter. ◐/◑ pour le
    # thème, ⚙ nu (sans sélecteur de variante) pour les paramètres, ▤ pour le
    # journal, ⓘ pour les informations, ⧉ pour la copie.
    for glyphe, role in (("◐", "thème clair"), ("◑", "thème sombre"),
                         ("▤", "journal"), ("ⓘ", "informations"),
                         ("⧉", "copie du lien")):
        check(glyphe not in sans_commentaires,
              "plus de « %s » pour %s" % (glyphe, role))

    # ⚙ et ℹ doivent porter le sélecteur de variante U+FE0F, sans lequel
    # certains systèmes les rendent en glyphe texte noir et blanc au lieu de
    # l'emoji — c'est justement ce qu'on cherchait à quitter.
    for base, nom in (("\u2699", "l'engrenage des paramètres"),
                      ("\u2139", "le i d'informations")):
        nus = len(re.findall(base + r"(?!\uFE0F)", sans_commentaires))
        check(nus == 0,
              "%s force la présentation emoji (U+FE0F) — %d occurrence(s) nue(s)"
              % (nom, nus))

    for emoji, role in (("\U0001F319", "passer au thème sombre"),
                        ("\u2600\uFE0F", "passer au thème clair"),
                        ("\U0001F4CB", "le journal"),
                        ("\U0001F517", "copier le lien"),
                        ("\u2705", "marquer lu"),
                        ("\u21A9\uFE0F", "marquer non lu")):
        check(emoji in sans_commentaires, "« %s » : %s" % (emoji, role))

    # LE point subtil. La confirmation de copie est la seule des trois icônes
    # de carte qui soit TEINTÉE par CSS (.card-mark.done la passe en vert), et
    # la couleur d'un emoji ne se pilote pas. En emoji, cette confirmation
    # deviendrait le sosie exact du bouton « marquer lu » juste à côté —
    # précisément ce que ce vert sert à éviter. Elle doit rester un glyphe
    # texte, et ce test est là pour empêcher qu'on l'« harmonise » un jour.
    bloc = re.search(r"function copyLink\([^)]*\)\{(.*?)\n\}", html, re.S).group(1)
    check('btn.dataset.iconOnly ? "\u2713"' in bloc,
          "la confirmation de copie reste la coche TEXTE ✓, pas l'emoji ✅")
    check('classList.add("done")' in bloc,
          "et elle est bien teintée en vert par la classe done")
    regle = re.search(r"\.card-mark\.done\{([^}]*)\}", html).group(1)
    check("color:" in regle,
          "la règle .card-mark.done teinte bien le texte (ce qu'un emoji ignorerait)")


def test_contraste_des_deux_themes():
    print("\n[app] les deux thèmes tiennent le contraste WCAG AA")
    import re
    html = open("docs/index.html", encoding="utf-8").read()

    def jetons(motif):
        bloc = re.search(motif + r"\s*\{([^}]*)\}", html, re.S)
        return dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;", bloc.group(1)))

    sombre = jetons(r"\n  :root")
    clair = jetons(r'html\[data-theme="light"\]')
    check(len(sombre) >= 8 and len(clair) >= 8,
          "les deux jeux de jetons sont lus (%d sombre, %d clair)" % (len(sombre), len(clair)))

    # Le seuil de WCAG AA pour du texte courant. Ces couleurs ne décorent pas :
    # --warn dit qu'une source est tombée, --ok que le backend répond, --accent
    # porte le compte à rebours et les libellés de jour.
    SEUIL = 4.5
    for nom, jeu in (("sombre", sombre), ("clair", clair)):
        for jeton in ("text", "text-dim", "accent", "ok", "warn", "danger"):
            if jeton not in jeu:
                continue
            # Contre les trois fonds où ces jetons servent réellement. Ne
            # mesurer que --bg laissait passer les pastilles d'onglet, à
            # 4,47 sur --bg-elevated alors qu'elles tenaient 4,64 sur --bg.
            for fond in ("bg", "bg-panel", "bg-elevated"):
                if fond not in jeu:
                    continue
                r = _contraste(jeu[jeton], jeu[fond])
                check(r >= SEUIL,
                      "%s : --%s sur --%s = %.2f:1" % (nom, jeton, fond, r))

    # Le piège qui a coûté le plus cher : un aplat --accent portant du texte
    # écrit en dur. Le blanc tombait à 2,14:1 sur le bleu clair du thème
    # sombre — sur le bouton le plus utilisé de l'app.
    for nom, jeu in (("sombre", sombre), ("clair", clair)):
        r = _contraste(jeu["accent-contrast"], jeu["accent"])
        check(r >= SEUIL,
              "%s : --accent-contrast sur --accent = %.2f:1" % (nom, r))

    # Et la garde qui empêche le retour du problème : aucune couleur de texte
    # écrite en dur SUR UN JETON. Un jeton se corrige par thème, pas un #fff.
    #
    # La première version de ce test cherchait la chaîne « color:#ffffff ».
    # Elle serait passée à côté de .tag-leak et .tag-video, qui écrivent
    # « color:#fff » — trois caractères de moins, même défaut. Une garde qui
    # ne connaît qu'une orthographe du blanc n'en est pas une.
    for forme in ("#ffffff", "#fff;", "#FFFFFF", "#FFF;", "white;"):
        for regle in re.findall(r"\{[^}]*\}", html):
            if "color:" + forme in regle.replace(" ", "") and "var(--accent)" in regle:
                check(False, "couleur de texte en dur sur un aplat --accent : %s"
                      % regle.replace("\n", " ")[:70])
    check(True, "aucune couleur de texte en dur sur un aplat de jeton")

    # Les couleurs volontairement fixes (un badge « LEAK » est rouge dans les
    # deux thèmes) échappent aux jetons, donc au contrôle par jeton ci-dessus.
    # On les mesure directement : fixe ne veut pas dire dispensé.
    fixes = re.findall(r"background:\s*(#[0-9a-fA-F]{3,6})\s*;\s*color:\s*(#[0-9a-fA-F]{3,6})", html)
    check(len(fixes) >= 2, "les paires de couleurs fixes sont trouvées (%d)" % len(fixes))
    for fond, texte in fixes:
        r = _contraste(texte, fond)
        check(r >= SEUIL, "couleur fixe %s sur %s = %.2f:1" % (texte, fond, r))


def test_readme_ne_cite_que_des_constantes_reelles():
    print("\n[doc] le README ne décrit que des constantes qui existent")
    import re, glob

    # Ce test existe à cause d'un cas réel : le README a documenté pendant
    # dix jours un DEAD_SOURCE_RUNS valant 6 « passages » alors que le code
    # compte DEAD_SOURCE_HOURS = 24 heures. Personne ne l'a vu, parce que
    # rien ne reliait les deux fichiers. Maintenant si.
    code = {}
    for fichier in glob.glob("*.py"):
        if fichier.startswith("test_"):
            continue
        for m in re.finditer(r"^([A-Z][A-Z0-9_]{2,})\s*=\s*(.+?)\s*(?:#.*)?$",
                             open(fichier, encoding="utf-8").read(), re.M):
            code.setdefault(m.group(1), m.group(2).strip())

    # L'app est un fichier unique en JavaScript ; ses constantes sont aussi
    # citables que celles du Python. Elles étaient jusqu'ici EXEMPTÉES une par
    # une, ce qui laissait passer une constante JS inventée aussi facilement
    # qu'un nom au hasard. On les lit pour de vrai.
    js = open("docs/index.html", encoding="utf-8").read()
    for m in re.finditer(r"^\s*(?:const|let|var)\s+([A-Z][A-Z0-9_]{2,})\s*=\s*(.*)$",
                         js, re.M):
        # La valeur ne sert qu'aux constantes tenant sur une ligne ; celles
        # qui ouvrent un tableau ou un objet (DEFAULT_FEEDS, DEFAULT_SETTINGS)
        # sont enregistrées sans valeur — leur nom suffit à prouver qu'elles
        # existent, et le README ne cite pas leur contenu.
        valeur = m.group(2).strip()
        code.setdefault(m.group(1),
                        valeur[:-1].strip() if valeur.endswith(";") else None)

    # Ne restent en dehors que ce qui n'est une constante de code NULLE PART :
    # variables d'environnement et secrets GitHub.
    hors_sujet = {
        "HEALTHCHECK_URL", "DISCORD_WEBHOOK_URL", "VAPID_PRIVATE_KEY",
        "VAPID_PUBLIC_KEY", "VAPID_SUBJECT", "PUSH_SUBSCRIPTIONS",
        "NEW_ITEMS_FILE", "SOURCE_ALERTS_FILE", "PROMOTED_ITEMS_FILE",
        "GITHUB_TOKEN", "RUNNER_TEMP",
    }

    readme = open("README.md", encoding="utf-8").read()
    cites = set(re.findall(r"`([A-Z][A-Z0-9_]{2,})(?:\s*=\s*([^`]+))?`", readme))

    inconnues = sorted(n for n, _ in cites if n not in code and n not in hors_sujet)
    check(not inconnues,
          "aucune constante citée entre `dos d'âne` n'est inventée"
          + (" (fautives : %s)" % ", ".join(inconnues) if inconnues else ""))

    # Et quand le README annonce une VALEUR, elle doit être la bonne : une
    # constante qui existe mais dont le README ment sur le chiffre est
    # exactement aussi trompeuse qu'une constante inventée.
    faux = []
    for nom, valeur in cites:
        valeur = (valeur or "").strip()
        if not valeur or nom not in code or code[nom] is None:
            continue
        if valeur.rstrip(".") != code[nom].rstrip("."):
            faux.append("%s : README dit %s, code dit %s" % (nom, valeur, code[nom]))
    check(not faux, "les valeurs annoncées sont les vraies"
          + (" (fautives : %s)" % " ; ".join(faux) if faux else ""))

    # Contrôle du contrôle : le test doit vraiment voir les constantes,
    # sinon il passerait tout aussi bien sur un README vide.
    check("DEAD_SOURCE_HOURS" in code and "SIMILARITY_THRESHOLD" in code,
          "le test lit bien les constantes du code Python")
    check("GTA6_RELEASE" in code and "STORAGE_PREFIX" in code,
          "et celles du JavaScript de l'app")
    check(len([n for n, _ in cites if n in code]) >= 10,
          "et il en trouve au moins dix citées dans le README")


def test_panne_serveur_nest_pas_une_source_cassee():
    print("\n[sources] une panne serveur n'accuse pas l'URL")
    import fetch_feeds

    # La distinction qui compte : « cassée » veut dire « va voir », « muette »
    # veut dire « ça repassera ». Un 503 repasse tout seul — mesuré quatre
    # fois sur 400 passages, résorbé au passage suivant à chaque fois.
    for statut in (500, 502, 503, 504, 429):
        check(fetch_feeds.panne_de_serveur(statut),
              "HTTP %d est une panne passagère" % statut)
    # Ceux-là désignent l'adresse, pas le serveur : ils ne se répareront
    # jamais seuls, et doivent continuer d'appeler quelqu'un.
    for statut in (200, 301, 401, 403, 404, 410):
        check(not fetch_feeds.panne_de_serveur(statut),
              "HTTP %d n'est PAS une panne passagère" % statut)
    check(not fetch_feeds.panne_de_serveur(None),
          "aucune réponse n'est pas une panne serveur — c'est injoignable")

    # Et le bout par lequel ça se voit : le statut publié dans feed.json,
    # que l'app lit pour sa ligne d'état.
    def sante(infos):
        feeds = [{"id": i, "name": "S%d" % n, "url": "https://ex.tld/%d" % n,
                  "official": False}
                 for n, i in enumerate(infos)]
        vrais = fetch_feeds.FEEDS
        fetch_feeds.FEEDS = feeds
        try:
            return {h["name"]: h["status"]
                    for h in fetch_feeds.build_sources_health([], infos, {})}
        finally:
            fetch_feeds.FEEDS = vrais

    etats = sante({
        "a": {"raw_count": 0, "http_status": 503, "panne_serveur": True},
        "b": {"raw_count": 0, "http_status": 404, "not_a_feed": True},
        "c": {"raw_count": 0, "http_status": 200},
    })
    check(etats["S0"] == "muette", "un 503 est publié « muette », pas « cassee »")
    check(etats["S1"] == "cassee", "un 404 reste « cassee » — il faut aller voir")
    check(etats["S2"] == "muette", "un flux vide reste « muette »")


def test_les_workflows_epinglent_leurs_dependances():
    print("\n[workflows] chaque workflow installe depuis requirements.txt")
    import glob

    # Ce test s'appelait test_recap_hebdo_epingle_sa_dependance : il était né
    # d'un défaut du récapitulatif hebdomadaire, qui faisait `pip install
    # requests` tout court et tournait donc sur une version que rien n'avait
    # testée, pendant que les trois autres workflows tenaient celle de
    # requirements.txt.
    #
    # Le récapitulatif a été supprimé le 15/09/2026. La règle qu'il avait fait
    # naître, elle, vaut pour tous les workflows — d'où le renommage : elle ne
    # dépend plus du cas qui l'a révélée. C'est la seule partie du test qui
    # comptait vraiment, les trois vérifications propres au hebdo ne faisaient
    # que décrire son fichier.
    req = open("requirements.txt", encoding="utf-8").read()
    check("requests==" in req, "requirements.txt épingle bien requests")

    # Tout workflow qui installe quelque chose doit passer par
    # requirements.txt, sinon la prochaine divergence se réinstallera sans
    # bruit. Trié : sans ça l'ordre des messages dépend du système de
    # fichiers, et un échec ne se relit pas deux fois pareil.
    for chemin in sorted(glob.glob(".github/workflows/*.yml")):
        contenu = open(chemin, encoding="utf-8").read()
        if "pip install" in contenu:
            check("requirements.txt" in contenu,
                  "%s installe depuis requirements.txt" % chemin.split("/")[-1])
            # La règle vaut pour TOUT ce qu'un workflow installe, pas
            # seulement pour le premier fichier cité. Un `pip install truc`
            # glissé à côté réinstallerait la divergence que cette règle a
            # justement été écrite pour empêcher.
            import re as _re
            libres = [c for c in _re.findall(r"pip install ([^\n]+)", contenu)
                      if not c.strip().startswith("-r ")]
            check(not libres,
                  "%s n'installe rien hors d'un fichier de dépendances%s"
                  % (chemin.split("/")[-1],
                     "" if not libres else " — " + ", ".join(libres)))

    # Les dépendances des contrôles vivent à part : requirements.txt est
    # installé par le robot à chaque passage, vingt-quatre à quarante-huit
    # fois par jour, et un navigateur de test n'y a rien à faire.
    dev = open("requirements-dev.txt", encoding="utf-8").read()
    check("playwright==" in dev, "requirements-dev.txt épingle playwright")
    check("playwright" not in req,
          "et le robot ne se traîne pas un navigateur à chaque passage")
    controles = open(".github/workflows/checks.yml", encoding="utf-8").read()
    check("requirements-dev.txt" in controles,
          "les contrôles installent bien leurs propres dépendances")
    for autre in ("update-feeds.yml", "sonde.yml", "test-push.yml"):
        contenu = open(".github/workflows/" + autre, encoding="utf-8").read()
        check("requirements-dev.txt" not in contenu,
              "%s ne les installe pas, lui" % autre)

    # Le contrôle rendu doit être LANCÉ, pas seulement présent dans le dépôt.
    # Un fichier de tests que rien n'exécute est un fichier mort, et c'est
    # exactement le sort qui l'attendait avant d'être versé ici.
    check("python test_navigateur.py" in controles,
          "les contrôles rendus sont réellement exécutés par la CI")
    check("playwright install" in controles,
          "et le navigateur qu'ils pilotent est installé avant")


def test_envoi_push_reel_testable():
    print("\n[push] un vrai envoi est testable de bout en bout")
    import push_notify

    wf = open(".github/workflows/test-push.yml", encoding="utf-8").read()

    # Séparé du robot, et déclenché à la main seulement : tester ne doit
    # jamais publier, ni partir tout seul.
    check("workflow_dispatch" in wf, "le test s'appelle à la demande")
    check("schedule" not in wf and "repository_dispatch" not in wf,
          "il ne part jamais tout seul")
    check("contents: read" in wf,
          "il est en lecture seule — un test n'écrit rien dans docs/")
    check("push_notify.py --test" in wf,
          "il passe par le vrai chemin d'envoi, pas par un double")

    # Les trois secrets sans lesquels rien ne peut être signé ni adressé.
    for secret in ("VAPID_PRIVATE_KEY", "VAPID_SUBJECT", "PUSH_SUBSCRIPTIONS"):
        check(secret in wf, "le test reçoit %s" % secret)

    # LE point du test. Un run qui reste vert alors qu'aucune notification
    # n'arrive ne teste rien — c'est exactement ce qui s'est produit le
    # 15/09/2026 : « 0/1 notification(s) envoyée(s) », job vert, défaut
    # invisible pendant trois heures. mode_test rend donc un code d'erreur.
    check(hasattr(push_notify, "mode_test"), "le mode test existe")
    src = open("push_notify.py", encoding="utf-8").read()
    debut = src.index("def mode_test():")
    corps = src[debut:src.index("\ndef ", debut + 10)]
    check("return 1" in corps,
          "le mode test ÉCHOUE quand rien ne part (sinon il ne teste rien)")
    check("envoyes == 0" in corps,
          "l'échec est décidé sur le nombre réellement envoyé")

    # L'alerte de dernier recours : quand plus personne n'est joignable, le
    # dire sur un canal qui, lui, fonctionne encore.
    check(hasattr(push_notify, "alerte_discord_push_mort"),
          "une alerte existe quand tous les abonnements sont expirés")
    prod = open(".github/workflows/update-feeds.yml", encoding="utf-8").read()
    bloc_push = prod[prod.index("- name: Notifier par push"):]
    check("DISCORD_WEBHOOK_URL" in bloc_push.split("- name:")[1],
          "le robot peut alerter sur Discord quand le push est mort")

    # Côté app : un bouton qui déclenche CE workflow, et pas le robot.
    html = open("docs/index.html", encoding="utf-8").read()
    check("testPushReel" in html, "l'app propose un envoi réel")
    check('GITHUB_WORKFLOW_TEST_PUSH = "test-push.yml"' in html,
          "elle vise le workflow de test, pas celui du robot")
    check("pushTestRealBtn" in html and 'btnPush.style.display = token ? "" : "none"' in html,
          "le bouton n'apparaît que si un jeton peut le faire marcher")


def test_aucun_mot_cle_nen_contient_un_autre():
    print("\n[mots-clés] aucun mot-clé n'est rendu inatteignable par un autre")
    import fetch_feeds

    # matches_keywords fait `k in texte` : une SOUS-CHAÎNE, pas un mot. Donc
    # dès que « gta 6 » correspond, « gta 6 news », « gta 6 trailer »,
    # « rockstar gta 6 »… ne peuvent rien attraper de plus. Ils sont
    # inatteignables par construction, quel que soit l'article.
    #
    # La liste fournie en comptait 139, dont 97 dans ce cas. Retirés le
    # 15/09/2026 après avoir REJOUÉ les deux listes sur les 2 657 articles
    # du fil : zéro verdict différent. Ce n'était pas un pari sur l'avenir,
    # c'était une propriété démontrable.
    #
    # Ce test existe pour que la liste ne regonfle pas : ajouter « gta 6
    # quelque-chose » alors que « gta 6 » est déjà là donne l'illusion
    # d'élargir la veille sans rien changer du tout.
    K = fetch_feeds.KEYWORDS
    check(len(K) == len(set(K)), "les %d mots-clés sont tous distincts" % len(K))

    for k in sorted(K):
        couvreurs = sorted(j for j in K if j != k and j in k)
        check(not couvreurs,
              "« %s » est atteignable%s" % (k,
                  "" if not couvreurs
                  else " — inatteignable, déjà couvert par « %s »" % couvreurs[0]))

    # Même règle pour les mots-clés officiels, qui forment une liste à part
    # appliquée au seul titre des sources officielles.
    for k in sorted(fetch_feeds.OFFICIAL_KEYWORDS):
        couvreurs = sorted(j for j in fetch_feeds.OFFICIAL_KEYWORDS
                           if j != k and j in k)
        check(not couvreurs,
              "officiel « %s » est atteignable%s" % (k,
                  "" if not couvreurs
                  else " — déjà couvert par « %s »" % couvreurs[0]))


def test_variantes_du_nom_dans_les_requetes_google_news():
    print("\n[requêtes] chaque recherche Google News couvre les six écritures")
    import fetch_feeds
    from urllib.parse import unquote

    # Les six écritures du nom, décision d'Antoni du 15/09/2026.
    #
    # Chacune a d'abord été sondée À L'EXCLUSION des autres, pour mesurer ce
    # qu'elle ajoute et non ce qu'elle recoupe :
    #
    #   "GTA VI"               EN 28 · FR 30  =  58 articles
    #   "Grand Theft Auto VI"  EN 26 · FR 26  =  52
    #   "Grand Theft Auto 6"   EN 19 · FR  7  =  26
    #   "GTA6"  attaché        EN 10 · FR  1  =  11
    #   "GTAVI" attaché        EN  0 · FR  0  =   0
    #
    # Les deux dernières avaient été écartées sur ces chiffres, puis remises
    # sur décision explicite. La mesure reste ici parce qu'elle dit ce qu'on
    # a accepté en les remettant, et ce qu'il faudra regarder si le fil se
    # salit : « GTA6 » attaché fait entrer le cours d'une CRYPTOMONNAIE qui
    # porte le nom du jeu (« GTA6 $0.0002399 | Live GTA6 Price Chart Today,
    # Swap on USDT — MEXC ») et des pages de tag vides ; « GTAVI » attaché
    # n'avait rien rapporté du tout.
    #
    # Le filtre par mots-clés ne rattrapera pas la crypto : la liste des 139
    # contient déjà « gta6 », donc ces articles passent. Si le bruit devient
    # visible, c'est ici qu'il faudra revenir.
    SIX = ["GTA 6", "GTA6", "GTA VI", "GTAVI",
           "Grand Theft Auto 6", "Grand Theft Auto VI"]

    # Les deux sources sans filtre de jeu, et pourquoi elles n'en prennent
    # pas : elles interrogent `site:rockstargames.com`, donc tout ce qu'elles
    # rendent vient déjà de Rockstar. Y ajouter les écritures ne les
    # élargirait pas, ça les rétrécirait — on perdrait les articles du
    # Newswire qui ne nomment pas le jeu dans leur titre.
    SANS_FILTRE = {"rockstar-en", "rockstar-fr"}

    gnews = [f for f in fetch_feeds.FEEDS if "news.google.com" in f["url"]]
    check(len(gnews) >= 19,
          "%d sources passent par Google News" % len(gnews))

    couvertes = 0
    for feed in gnews:
        if feed["id"] in SANS_FILTRE:
            check("site:rockstargames.com" in feed["url"],
                  "%s reste sans filtre de jeu, sur le domaine de Rockstar"
                  % feed["id"])
            continue
        requete = unquote(feed["url"].split("q=")[1].split("&")[0]).replace("+", " ")
        manquantes = [v for v in SIX if '"%s"' % v not in requete]
        check(not manquantes,
              "%s : la requête couvre les six écritures%s"
              % (feed["id"],
                 "" if not manquantes else " — il manque %s" % ", ".join(manquantes)))
        couvertes += 1

    check(couvertes >= 17,
          "%d requêtes portent les six écritures (17 attendues au minimum)"
          % couvertes)

    # Google News n'est pas la seule source qui porte une requête, et c'est
    # exactement ce qui a été oublié le 15/09/2026 : les dix-sept requêtes
    # Google News avaient été élargies, Reddit était resté à `q=GTA+6` tout
    # court. Personne ne l'a vu parce que le test ne regardait que Google
    # News — il décrivait le geste accompli, pas la règle.
    #
    # Mesuré avant correction : 25 entrées, 12 retenues. Après : 25 entrées,
    # 23 retenues. Le subreddit de fuites écrit « GTA VI », pas « GTA 6 »
    # (« 5th GTA VI clip has been leaked », « 11th GTA VI leak is out »).
    # Près de la moitié de cette source nous échappait.
    #
    # La règle est donc : TOUTE source dont l'URL porte un paramètre de
    # recherche couvre les six écritures, quel que soit le service.
    #
    # UNE exception, et de la même nature que rockstar-en / rockstar-fr plus
    # haut : quand la PORTÉE de la recherche garantit déjà le sujet, ajouter
    # les écritures ne l'élargit pas, ça la rétrécit.
    #
    # reddit-gta6-suivi cherche `update OR patch OR DLC OR online` À
    # L'INTÉRIEUR de r/GTA6. Sur ce subreddit, nommer le jeu va de soi et
    # beaucoup de fils ne le font pas ; exiger une des six écritures en plus
    # ne garderait que les fils qui le nomment explicitement — soit
    # exactement l'inverse du but, qui est d'attraper l'angle correctifs /
    # DLC / GTA Online que le reste de la liste ne couvre pas.
    #
    # L'exception n'est pas accordée sur parole : le test VÉRIFIE que la
    # portée est bien ce qui la justifie. Si un jour cette source cessait
    # d'être restreinte à son subreddit, elle retomberait sous la règle
    # commune au lieu de garder une dérogation devenue fausse.
    PORTEE_VAUT_FILTRE = {"reddit-gta6-suivi"}

    for feed in fetch_feeds.FEEDS:
        if "news.google.com" in feed["url"] or "q=" not in feed["url"]:
            continue
        if feed["id"] in PORTEE_VAUT_FILTRE:
            check("/r/GTA6/" in feed["url"] and "restrict_sr=on" in feed["url"],
                  "%s : la portée (r/GTA6, restrict_sr) tient lieu de filtre de jeu"
                  % feed["id"])
            continue
        requete = unquote(feed["url"].split("q=")[1].split("&")[0]).replace("+", " ")
        manquantes = [v for v in SIX if '"%s"' % v not in requete]
        check(not manquantes,
              "%s (hors Google News) : la requête couvre les six écritures%s"
              % (feed["id"],
                 "" if not manquantes else " — il manque %s" % ", ".join(manquantes)))


def test_prefiltre_de_ressemblance():
    print("\n[doublons] le préfiltre ne peut pas écarter un vrai doublon")
    import fetch_feeds
    from difflib import SequenceMatcher

    S = fetch_feeds.SIMILARITY_THRESHOLD

    # La propriété qu'on verrouille, et la seule qui compte : le préfiltre
    # ne répond « non » que si le score est HORS D'ATTEINTE. Il ne peut donc
    # jamais faire rater un doublon — il ne fait qu'éviter de calculer.
    #
    # Vérifié en force brute sur toutes les paires d'un jeu de titres
    # réalistes, en comparant la décision AVEC et SANS préfiltre. Un seul
    # écart et la déduplication aurait changé de comportement.
    titres = [
        "GTA 6 : Rockstar dévoile enfin la date de sortie",
        "GTA 6 : Rockstar dévoile enfin la date de sortie officielle",
        "Rockstar dévoile la date de sortie de GTA 6",
        "GTA VI Trailer 2 est en ligne",
        "GTA VI Trailer 3 est en ligne",
        "Grand Theft Auto VI - Trailer 1",
        "20+ New GTA 6 Screenshots Released",
        "New Grand Theft Auto 6 Screenshots Revealed",
        "Take-Two confirme le report de GTA 6 à novembre 2026",
        "Take-Two confirms GTA 6 delay to November 2026",
        "Un fan recrée Vice City dans Minecraft",
        "Les actions de Take-Two grimpent après l'annonce",
        "a",
        "",
        "Grand Theft Auto VI - Rockstar Games",
        "GTA 6",
    ]

    ecarts = 0
    prefiltres = 0
    for a in titres:
        ta = fetch_feeds.titre_comparable(a)
        for b in titres:
            tb = fetch_feeds.titre_comparable(b)
            possible = fetch_feeds.peut_atteindre_le_seuil(ta, tb)
            reel = fetch_feeds.ressemblance_comparables(ta, tb)
            if not possible:
                prefiltres += 1
                if reel >= S:
                    ecarts += 1
    check(ecarts == 0,
          "aucune paire écartée par le préfiltre n'atteignait le seuil")
    check(prefiltres > 0,
          "et le préfiltre écarte bien quelque chose, sinon il ne sert à rien")

    # Le préfiltre est une BORNE SUPÉRIEURE, pas une décision : il doit
    # laisser passer des paires que le vrai calcul refuse ensuite. Un
    # préfiltre qui trancherait juste à tous les coups serait en train de
    # décider à la place de SequenceMatcher, et le seuil ne voudrait plus
    # rien dire.
    #
    # Le cas est construit plutôt que cherché dans un corpus : deux chaînes
    # faites des MÊMES caractères dans l'ordre inverse. Les deux bornes
    # valent 1,0 — mêmes longueurs, mêmes lettres — alors que l'alignement
    # réel ne trouve presque rien. Sur des titres réels la situation est la
    # règle, pas l'exception : 8 216 paires sur 89 700 passent le préfiltre,
    # dont 2 seulement sont de vrais doublons.
    endroit, envers = "abcdefghij", "jihgfedcba"
    check(fetch_feeds.peut_atteindre_le_seuil(endroit, envers),
          "deux chaînes aux mêmes lettres passent les deux bornes")
    check(fetch_feeds.ressemblance_comparables(endroit, envers) < S,
          "et le vrai calcul les refuse — le préfiltre ne décide pas à sa place")

    # Un titre vide n'est un doublon de rien, pas même d'un autre titre vide.
    check(not fetch_feeds.peut_atteindre_le_seuil("", ""),
          "deux titres vides ne se ressemblent pas")
    check(not fetch_feeds.peut_atteindre_le_seuil("", "gta 6 date de sortie"),
          "un titre vide ne ressemble à rien")

    # Les deux bornes prises séparément, sur des cas construits pour ça.
    check(not fetch_feeds.peut_atteindre_le_seuil("court", "x" * 200),
          "la borne des longueurs écarte deux titres de tailles incomparables")
    check(not fetch_feeds.peut_atteindre_le_seuil("abcdefghij", "klmnopqrst"),
          "la borne des caractères écarte deux titres sans lettre commune")
    check(fetch_feeds.peut_atteindre_le_seuil("abcdefghij", "abcdefghij"),
          "deux titres identiques passent le préfiltre")

    # Et le résultat de bout en bout : find_duplicate doit toujours trouver
    # le doublon évident, préfiltre ou pas.
    fenetre = [{"title": "GTA 6 : Rockstar dévoile enfin la date de sortie",
                "link": "https://a.tld/1"}]
    trouve = fetch_feeds.find_duplicate(
        {"title": "GTA 6 : Rockstar dévoile enfin la date de sortie officielle",
         "link": "https://b.tld/2"},
        fenetre, links_index={}, fenetre_titres=fenetre)
    check(trouve is not None and trouve["link"] == "https://a.tld/1",
          "find_duplicate reconnaît toujours une reprise du même titre")

    # Le garde-fou des numéros passe APRÈS le préfiltre maintenant. Deux
    # titres d'une même série se ressemblent beaucoup, donc le préfiltre les
    # laisse passer : c'est bien le garde-fou qui doit les séparer, sinon
    # « Trailer 2 » et « Trailer 3 » fusionneraient.
    serie = [{"title": "GTA VI Trailer 2 est en ligne", "link": "https://a.tld/t2"}]
    check(fetch_feeds.find_duplicate(
              {"title": "GTA VI Trailer 3 est en ligne", "link": "https://b.tld/t3"},
              serie, links_index={}, fenetre_titres=serie) is None,
          "le garde-fou des numéros agit toujours après le préfiltre")


def test_ligne_run_tient_sur_une_ligne():
    print("\n[app] le bilan du robot tient sur une ligne, sauf s'il y a des soucis")
    html = open("docs/index.html", encoding="utf-8").read()
    fn = html[html.index("function majLigneRun("):]
    fn = fn[:fn.index("\n}")]

    # Le partage est le cœur du changement : la ligne du haut a une forme
    # FIXE (durée · nouveaux · compteur), la ligne du bas une forme variable.
    # Tout ce qui peut s'allonger sans limite — le nom d'une source cassée
    # fait trente caractères — doit atterrir en bas, sinon la ligne du haut
    # repasse sur deux lignes et on revient au point de départ.
    # On lit les arguments réellement passés à chaque push, pas le texte du
    # fichier : « muette » apparaît aussi dans les commentaires qui expliquent
    # justement pourquoi le compte est disjoint, et chercher le mot ferait
    # passer le test sur sa propre documentation.
    def arguments_de(appel):
        rendus, depart = [], 0
        while True:
            i = fn.find(appel, depart)
            if i < 0:
                return rendus
            j, profondeur = i + len(appel), 1
            while profondeur:
                profondeur += {"(": 1, ")": -1}.get(fn[j], 0)
                j += 1
            rendus.append(fn[i + len(appel):j - 1])
            depart = j

    haut = arguments_de("etat.push(")
    bas = arguments_de("soucis.push(")
    check(haut, "la ligne du haut se construit dans son propre groupe")
    check(bas, "les soucis se construisent dans un groupe à part")

    for morceau in ("muette", "cassée", "en forte baisse", "Google News en échec"):
        check(any(morceau in a for a in bas) and not any(morceau in a for a in haut),
              "« %s » va sur la deuxième ligne, pas la première" % morceau)

    # Et la ligne du haut ne contient QUE les trois morceaux courts : durée,
    # nouveaux, compteur de sources. Un quatrième, et la garantie tombe.
    check(len(haut) == 3,
          "la ligne du haut n'a que trois morceaux, tous de longueur bornée")

    # L'accordéon. Deux sources cassées pour de bon — les flux RSS de YouTube,
    # que YouTube ne sert plus — donnaient deux lignes orange PERMANENTES sous
    # la console. Un signal qui ne s'éteint jamais cesse d'être un signal.
    check("bascule-soucis" in fn,
          "le compteur de sources porte l'accordéon quand il y a des soucis")
    check('aria-controls="runSoucis"' in fn and 'aria-expanded="' in fn,
          "il s'annonce comme un accordéon, pas comme du texte décoratif")
    # Bouton SEULEMENT s'il y a quelque chose à déplier : un chevron qui
    # n'ouvre rien est une promesse vide.
    compteur = fn[fn.index("const compteur ="):]
    compteur = compteur[:compteur.index("etat.push(") + 400]
    check("soucis.length" in compteur.split("etat.push(")[1][:60],
          "quand tout répond, le compteur reste du texte")

    # Replié par défaut MAIS rouvert dès que la liste change : c'est ce qui
    # évite de devenir aveugle à une nouvelle panne en se débarrassant du
    # bruit permanent. Une seule valeur stockée — la signature acquittée —
    # plutôt qu'un booléen à tenir en cohérence avec elle.
    corps = html[html.index("function signatureSoucis("):]
    corps = corps[:corps.index("\n}")]
    for ingredient in ("cassees.map", "muettes", "baisse", "decode_failures"):
        check(ingredient in corps,
              "la signature des soucis tient compte de %s" % ingredient)

    corps = html[html.index("function soucisOuverts("):]
    corps = corps[:corps.index("\n}")]
    # storageGet renvoie { value } ou null, jamais la chaîne nue. Un
    # `storageGet(...) || ""` rendait un OBJET, jamais égal à une signature :
    # l'accordéon se rouvrait à chaque rechargement. Invisible à la lecture,
    # attrapé par le contrôle navigateur.
    #
    # Les commentaires sont retirés AVANT de chercher : celui de la fonction
    # cite justement le piège qu'on interdit, et le test échouait sur sa
    # propre documentation. Même travers que « muette » sur la ligne du haut.
    code = "\n".join(l for l in corps.split("\n")
                     if not l.strip().startswith("//"))
    check(".value" in code,
          "soucisOuverts déballe storageGet au lieu de comparer un objet")
    check("storageGet(" in code and '|| ""' not in code,
          "et ne retombe pas dans le piège du repli sur un objet")

    # Le plus long des trois est le compteur, et il est borné par construction :
    # deux nombres et le mot « sources ». C'est ce qui remplace l'ancien
    # « toutes les sources répondent », vingt-huit caractères à lui seul.
    check(not any("répondent" in a for a in haut),
          "l'ancienne phrase longue a bien laissé la place au compteur")

    # La deuxième ligne n'existe que s'il y a quelque chose à dire. Une div
    # vide mais affichée occuperait quand même sa hauteur de ligne, ce qui
    # ferait exactement le décalage qu'on cherche à supprimer.
    # La condition s'est renforcée : la ligne se cache AUSSI quand l'accordéon
    # est replié. On vérifie les deux, pas une chaîne figée — sinon le test
    # interdit l'amélioration au lieu d'interdire la régression.
    import re
    cond = re.search(r"const ouvert = (soucis\.length[\s\S]{0,120}?);", fn)
    check(cond is not None, "l'affichage de la deuxième ligne a une condition lisible")
    check(cond is not None and "soucis.length" in cond.group(1),
          "la deuxième ligne disparaît quand il n'y a aucun souci")
    check(cond is not None and "soucisOuverts" in cond.group(1),
          "et quand l'accordéon est replié")
    check('elSoucis.style.display = ouvert ? "" : "none"' in fn,
          "c'est bien cette condition qui pilote son affichage")

    # Le nowrap est la ceinture : si un jour un morceau du haut s'allonge, il
    # débordera visiblement au lieu de repasser sournoisement sur deux lignes.
    css = html[html.index(".run-line{"):]
    css = css[:css.index(".history-line")]
    check("white-space:nowrap" in css, "la ligne du haut ne revient jamais à la ligne")
    check(".run-soucis{" in css, "la deuxième ligne a son propre style")

    # Le mode direct n'a pas de robot : les DEUX lignes doivent se taire.
    # Oublier la seconde y laisserait un bilan périmé affiché.
    check('getElementById("runSoucis")' in html[html.index('const ligneRun ='):
                                                html.index('const ligneRun =') + 400],
          "le mode direct masque aussi la deuxième ligne")


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
    # La confirmation passe désormais par la mécanique commune des dialogues,
    # à qui elle DÉCLARE son focus initial. On vérifie l'intention déclarée —
    # « confirmAnnuler » — et non plus un appel .focus() écrit à la main, qui
    # n'existe plus. Le comportement, lui, est vérifié dans le navigateur.
    check('focusInitial: "confirmAnnuler"' in bloc,
          "le focus arrive sur Annuler, pas sur l'action destructive")
    check('fermer: function(){ repondConfirmation(false); }' in bloc,
          "et Échap passe par le REFUS, donc la promesse se résout")
    # _toucheConfirmation n'existe plus : Échap est géré une seule fois, pour
    # les cinq dialogues, par la mécanique commune. On vérifie donc là-bas
    # qu'Échap appelle bien la fermeture DÉCLARÉE par le dialogue du dessus —
    # laquelle, pour la confirmation, est le refus (vérifié juste au-dessus).
    bloc = html[html.index("function _toucheDialogue("):
                html.index("function debutDialogue(")]
    check('e.key === "Escape"' in bloc and "haut.fermer()" in bloc,
          "Échap appelle la fermeture déclarée par le dialogue du dessus")
    check("_pileDialogues[_pileDialogues.length - 1]" in bloc,
          "et c'est bien celui du DESSUS, pas un autre de la pile")
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


def test_panneau_parametres_applique_vraiment():
    print("\n[app] le panneau applique ce qu'il enregistre")
    import re
    html = open("docs/index.html", encoding="utf-8").read()

    # LE défaut : saveSettings() écrivait dans localStorage et s'arrêtait là.
    # Le plafond d'affichage, les mots-clés d'exclusion et les sources actives
    # décident pourtant de ce que montre applyFilters(), qui n'était jamais
    # rappelé. Couper une source puis presser « Appliquer » ne changeait rien
    # à l'écran — le bouton portait un nom qu'il ne tenait pas.
    corps = html[html.index("function appliqueReglages("):]
    corps = corps[:corps.index("\n}")]
    check("saveSettings()" in corps and "applyFilters()" in corps,
          "appliqueReglages enregistre PUIS redessine")
    check(corps.index("saveSettings()") < corps.index("applyFilters()"),
          "et dans cet ordre : on redessine à partir de ce qui vient d'être écrit")

    # Chaque commande qui change ce qui s'affiche doit passer par là. Une qui
    # appellerait encore saveSettings() seule rejouerait le défaut.
    for quoi in ('id="maxDisplay"', 'id="simRange"',
                 'id="keywordsInput"', 'id="excludeKeywordsInput"'):
        deb = html.index(quoi)
        balise = html[html.rindex("<", 0, deb):html.index(">", deb) + 1]
        check("appliqueReglages()" in balise,
              "%s redessine le fil quand il change" % quoi)
    deb = html.index('id="src-${f.id}"')
    check("appliqueReglages()" in html[deb:deb + 200],
          "basculer une source redessine le fil")
    corps = html[html.index("async function resetSettings("):]
    corps = corps[:corps.index("\n}")]
    check("applyFilters()" in corps,
          "« Réinitialiser » aussi : sinon l'écran garde les anciens filtres")

    # Le curseur anti-doublon n'était commité par RIEN : oninput ne mettait à
    # jour que son étiquette. « Fermer » le perdait, basculer une source
    # l'enregistrait sans qu'on l'ait demandé. Un seul modèle pour tous.
    balise = html[html.rindex("<", 0, html.index('id="simRange"')):]
    balise = balise[:balise.index(">") + 1]
    check("oninput=" in balise and "onchange=" in balise,
          "le curseur met à jour son étiquette EN GLISSANT et s'enregistre au relâché")

    # Plus de bouton « Appliquer », donc plus de fonction pour le servir.
    deb = html.index("<!-- ---------- Panneau Paramètres")
    panneau = html[deb:html.index("<!-- ---------- Modale Aperçu article")]
    check(">Appliquer<" not in panneau,
          "le bouton « Appliquer » a disparu — tout s'applique à la frappe")
    check("saveSettingsAndClose" not in html,
          "et sa fonction avec lui, plutôt que de rester en code mort")
    check("s'appliquent immédiatement" in panneau,
          "le panneau DIT que les changements sont immédiats")


def test_plafond_daffichage_a_de_vraies_bornes():
    print("\n[app] le plafond d'affichage respecte les bornes qu'il annonce")
    import re
    html = open("docs/index.html", encoding="utf-8").read()

    # max="1000" sur un <input type=number> hors formulaire ne bloque rien :
    # 20000 y tenait sans un mot. Et parseInt(...) || 500 corrigeait « 0 » et
    # « abc » en silence, puisque zéro et NaN sont tous deux faux.
    check("|| 500" not in html,
          "plus de repli muet sur 500 par la fausseté de zéro")
    for nom in ("MIN_DISPLAY", "MAX_DISPLAY"):
        m = re.search(r"const %s = (\d+);" % nom, html)
        check(m is not None, "%s est déclarée" % nom)
    mini = int(re.search(r"const MIN_DISPLAY = (\d+);", html).group(1))
    maxi = int(re.search(r"const MAX_DISPLAY = (\d+);", html).group(1))

    corps = html[html.index("function litMaxDisplay("):]
    corps = corps[:corps.index("\nfunction ")]
    check("MIN_DISPLAY" in corps and "MAX_DISPLAY" in corps,
          "la lecture du champ s'appuie sur ces deux constantes")
    check("Number.isFinite" in corps,
          "une saisie illisible est reconnue comme telle, pas confondue avec zéro")
    check(corps.count("message =") >= 3,
          "les trois corrections possibles ont chacune leur message")
    check('note.textContent' in corps,
          "et ce message est affiché, pas seulement calculé")
    check("settings.maxDisplay = litMaxDisplay();" in html,
          "saveSettings passe par cette lecture bornée")

    # Le balisage doit annoncer EXACTEMENT les bornes que le code applique :
    # c'est la divergence entre les deux qui a produit le défaut.
    balise = html[html.rindex("<", 0, html.index('id="maxDisplay"')):]
    balise = balise[:balise.index(">") + 1]
    check('min="%d"' % mini in balise and 'max="%d"' % maxi in balise,
          "le champ annonce les bornes réellement appliquées (%d–%d)" % (mini, maxi))
    check('id="maxDisplayNote"' in html,
          "il a un endroit où dire qu'il a corrigé quelque chose")


def test_cibles_tactiles_du_panneau():
    print("\n[app] tout ce qui se touche dans le panneau fait 44 px")
    import re
    html = open("docs/index.html", encoding="utf-8").read()

    # L'interrupteur de source mesurait 32x19 px — sous le minimum de 24 px
    # de WCAG 2.5.8 — et seul l'interrupteur répondait, pas le nom à côté.
    # Cinquante-neuf exemplaires, tous oubliés par la passe qui avait porté
    # le reste de l'app à 44 px.
    bloc = re.search(r"\n  \.switch\{([^}]*)\}", html).group(1)
    for axe in ("width", "height"):
        v = int(re.search(r"%s:(\d+)px" % axe, bloc).group(1))
        check(v >= 24, "l'interrupteur fait au moins 24 px de %s (%d)" % (axe, v))

    bloc = re.search(r"\n  \.source-row\{([^}]*)\}", html).group(1)
    h = re.search(r"min-height:(\d+)px", bloc)
    check(h is not None and int(h.group(1)) >= 44,
          "la ligne de source fait au moins 44 px de haut")
    check("cursor:pointer" in bloc, "et se donne pour cliquable")

    # C'est la LIGNE qui est le <label>, donc tout le rectangle bascule. Un
    # <label> dans un <label> étant invalide, l'interrupteur doit être un
    # <span> : c'est l'inverse exact de l'ancien balisage.
    # Ancré sur le gabarit d'une LIGNE, pas sur la boucle qui l'appelle : la
    # liste est passée d'un map direct à un rendu par familles, et le test
    # visait la boucle. Ce qu'il vérifie — la ligne est un label, la bascule
    # un span — ne dépend pas de la façon dont les lignes sont assemblées.
    gabarit = html[html.index("const ligne = f => `"):]
    gabarit = gabarit[:gabarit.index("</label>`;")]
    check('<label class="source-row">' in gabarit,
          "la ligne entière est le label de la case")
    check('<span class="switch">' in gabarit and '<label class="switch">' not in gabarit,
          "et l'interrupteur n'est plus un label imbriqué")

    # Les soixante bascules ne font plus un mur : elles sont rangées en cinq
    # familles repliées. Chaque source doit tomber dans UNE famille et une
    # seule — d'où un ordre de priorité, RockstarMag étant à la fois
    # spécialisée et francophone.
    corps = html[html.index("function familleSource("):]
    corps = corps[:corps.index("\n}")]
    for critere in ("f.official", "f.specialist", "news.google.com", 'f.lang === "fr"'):
        check(critere in corps, "la famille se décide aussi sur %s" % critere)
    familles = re.search(r"const FAMILLES_SOURCES = \[([^\]]*)\]", html).group(1)
    check(familles.count('"') // 2 == 5, "cinq familles déclarées")

    # LE piège, et il ne se voit pas dans le HTML : l'attribut `hidden` ne
    # vaut qu'un display:none de la feuille du NAVIGATEUR, que toute règle
    # d'auteur bat. Avec display:grid sur le corps de famille et sans cette
    # règle, les familles se rendaient DÉPLIÉES malgré leur hidden — la
    # liste faisait 3244 px, soit pire qu'avant le regroupement.
    check(".src-famille-corps[hidden]{display:none;}" in html,
          "un corps de famille masqué l'est vraiment, malgré son display:grid")

    # Le filtre commande les familles : chercher dans des sections repliées
    # sans les ouvrir ne montrerait rien, et le filtre aurait l'air cassé.
    corps = html[html.index("function filtreSources("):]
    corps = corps[:corps.index("\n}")]
    check("basculeFamille(" in corps, "le filtre ouvre les familles où il trouve")
    check("fam.hidden" in corps, "et masque celles où il ne trouve rien")
    check("Boolean(q)" in corps,
          "vider le filtre referme tout, sinon l'onglet redevient un mur")

    # Le « ? » faisait 18 px, exempté du minimum par un commentaire qui lui
    # attribuait un pseudo-élément qu'il n'avait pas. On vérifie désormais le
    # mécanisme, pas la phrase.
    bloc = re.search(r"\n  \.aide\{([^}]*)\}", html).group(1)
    cote = int(re.search(r"width:(\d+)px", bloc).group(1))
    check(cote >= 24, "le « ? » fait au moins 24 px de côté (%d)" % cote)
    check("position:relative" in bloc, "et ancre son pseudo-élément")
    apres = re.search(r"\.aide::after\{([^}]*)\}", html)
    check(apres is not None, ".aide étend sa zone de clic par un pseudo-élément")
    debord = re.search(r"inset:-(\d+)px", apres.group(1))
    check(debord is not None and cote + 2 * int(debord.group(1)) >= 44,
          "sa zone de clic atteint 44 px (%d + 2x%s)"
          % (cote, debord.group(1) if debord else "?"))

    # Un display posé en style inline l'emporte sur la feuille de style et
    # fait du bouton un CONTENEUR FLEX : son libellé cesse d'être centré et
    # remonte en haut de la boîte, de 8,5 px sur un bouton .small. Le défaut
    # avait déjà été rencontré et corrigé sur triggerRunBtn — et jamais
    # reporté sur les deux boutons du groupe push, qui l'ont gardé.
    check('style.display = "inline-flex"' not in html,
          "aucun JS ne pose un display de type flex pour montrer un bouton")
    check(html.count('style.display = token ? "" : "none"') >= 1
          or '.style.display = "";' in html,
          "montrer un bouton rend la main à la feuille de style (chaîne vide)")

    # Le filet, pour que le piège ne puisse plus se refermer : même devenu
    # conteneur flex, le bouton centre son contenu.
    bloc = re.search(r"\n  button\{([^}]*)\}", html).group(1)
    check("align-items:center" in bloc and "justify-content:center" in bloc,
          "le bouton centre son libellé même s'il devient un conteneur flex")

    # « Les textes sont trop collés au bouton » : .small n'avait que 10 px de
    # rembourrage latéral, le plus étroit du panneau, là où le bouton
    # ordinaire en a 14. Le bouton « Fermer » en avait 8.
    ordinaire = int(re.search(r"padding:\d+px (\d+)px", bloc).group(1))
    for sel in ("button.small", r"\.settings-title button"):
        regle = re.search(r"%s\{([^}]*)\}" % sel, html).group(1)
        lat = int(re.search(r"padding:\d+px (\d+)px", regle).group(1))
        check(lat >= ordinaire,
              "%s laisse au moins autant d'air que le bouton ordinaire "
              "(%d px contre %d)" % (sel.replace("\\", ""), lat, ordinaire))

    # Un groupe d'actions tient sur UNE rangée, à largeurs égales. Avec des
    # boutons dimensionnés par leur texte, le dernier partait à la ligne, et
    # pas le même selon le groupe : « Désactiver » seul ici, « Oublier » seul
    # là. Deux mises en page pour deux groupes de trois boutons.
    bloc = re.search(r"\n  \.setting-buttons\{([^}]*)\}", html).group(1)
    check("flex-wrap:nowrap" in bloc, "un groupe d'actions ne se scinde pas")
    bloc = re.search(r"\.setting-buttons button\{([^}]*)\}", html).group(1)
    check("flex:1 1 0" in bloc, "ses boutons ont tous la même largeur")
    check("white-space:nowrap" in bloc, "et aucun libellé ne se coupe en deux")

    # Sous 380 px, trois boutons ne tiennent plus sans qu'un libellé déborde
    # de sa boîte. Le repli est explicite, et son seuil est calculé.
    media = re.search(r"@media \(max-width:(\d+)px\)\{\s*\.setting-buttons\{flex-wrap:wrap;\}", html)
    check(media is not None,
          "sous une largeur donnée, le retour à la ligne reprend ses droits")
    check(media is not None and 340 <= int(media.group(1)) <= 400,
          "et ce seuil est celui d'un téléphone étroit (%s px)"
          % (media.group(1) if media else "?"))

    # « Il n'y a pas d'espace entre les boutons et les textes » : la ligne de
    # bilan était collée au bas des boutons, et comme elle suit souvent un
    # bouton rouge, elle semblait en faire partie.
    bloc = re.search(r"\n  \.token-status\{([^}]*)\}", html).group(1)
    marge = re.search(r"margin-top:(\d+)px", bloc)
    check(marge is not None and int(marge.group(1)) >= 8,
          "le bilan respire sous les boutons (%s)"
          % (marge.group(1) + " px" if marge else "aucune marge"))

    # La dérogation elle-même : chaque sélecteur qui y figure doit tenir son
    # engagement, sinon c'est une porte ouverte à la prochaine régression.
    ligne = re.search(r"([^\n]*)\{min-height:0;\}", html).group(1)
    for sel in [s.strip() for s in ligne.split(",")]:
        nom = sel.lstrip(".")
        etendu = re.search(r"\%s::(?:after|before)\{[^}]*inset:" % sel, html) is not None
        absolu = re.search(r"\%s\{[^}]*position:absolute" % sel, html) is not None
        check(etendu or absolu,
              "%s déroge aux 44 px en tenant sa promesse (pseudo-élément ou position absolue)"
              % sel)


def test_panneau_parametres_accessible():
    print("\n[app] le panneau ne dit plus ses états par la seule couleur")
    import re
    html = open("docs/index.html", encoding="utf-8").read()
    deb = html.index("<!-- ---------- Panneau Paramètres")
    panneau = html[deb:html.index("<!-- ---------- Modale Aperçu article")]

    # La sélection ne vivait que dans une classe CSS : un lecteur d'écran
    # annonçait trois boutons identiques, pour les onglets comme pour le thème.
    for barre, combien in (("panneau-tabs", 3), ("theme-toggle", 3)):
        bloc = panneau[panneau.index(barre):]
        bloc = bloc[:bloc.index("</div>")]
        check(bloc.count('aria-pressed=') == combien,
              "les %d boutons de .%s annoncent leur état" % (combien, barre))
        check(bloc.count('aria-pressed="true"') <= 1,
              ".%s n'en déclare jamais deux enfoncés à la fois" % barre)

    # Et le JS doit les tenir à jour, sinon l'attribut ment dès le premier clic.
    for fn in ("updateThemeButtons", "ongletParam"):
        corps = html[html.index("function %s(" % fn):]
        corps = corps[:corps.index("\n}")]
        check('setAttribute("aria-pressed"' in corps,
              "%s met l'attribut à jour, il ne fait pas que poser une classe" % fn)

    # Les onglets d'ARTICLES avaient le même défaut que ceux du panneau, et
    # n'avaient pas été corrigés avec eux : ils annonçaient quatre boutons
    # identiques à un lecteur d'écran.
    corps = html[html.index("function marqueActif("):]
    corps = corps[:corps.index("\n}")]
    check('setAttribute("aria-pressed"' in corps and 'classList.toggle("active"' in corps,
          "une bascule dit son état par la couleur ET par aria-pressed")
    for fn in ("setTab", "setLang", "setFilter"):
        bloc = html[html.index("function %s(" % fn):]
        bloc = bloc[:bloc.index("\n}")]
        check('classList.toggle("active"' not in bloc,
              "%s passe par marqueActif au lieu de poser la classe seule" % fn)
        check("marqueActif(" in bloc, "%s marque bien ses boutons" % fn)
    for ident in ("tabAll", "tabRockstar", "filterAll", "tabLangAll"):
        bloc = html[html.rindex("<", 0, html.index('id="%s"' % ident)):]
        bloc = bloc[:bloc.index(">") + 1]
        check("aria-pressed=" in bloc,
              "%s déclare son état initial dans le balisage" % ident)

    # Aucun champ du panneau ne doit tenir son nom d'un seul placeholder :
    # il disparaît à la première frappe, et n'en est pas un pour les outils
    # d'assistance. Il n'y avait aucun label for= dans tout le fichier.
    for champ in re.finditer(r"<(input|textarea)\b[^>]*>", panneau):
        balise = champ.group(0)
        m = re.search(r'id="([^"]+)"', balise)
        if not m or balise.startswith("<input type=\"checkbox"):
            continue
        ident = m.group(1)
        nomme = ('aria-label=' in balise
                 or 'aria-labelledby=' in balise
                 or ('<label for="%s"' % ident) in panneau)
        check(nomme, "le champ %s porte un nom lisible par un lecteur d'écran" % ident)

    # Le « ? » se greffe sur la PREMIÈRE explication repliée du groupe. Si
    # celle-ci vit dans un bloc lui-même masqué, le bouton ne révèle rien :
    # c'était le cas du groupe des notifications, dont le « ? » ouvrait un
    # texte enfermé dans .bloc-replie.
    def dans_un_bloc_replie(fragment, pos):
        """Vrai si pos tombe À L'INTÉRIEUR d'un <div class="bloc-replie">.
        L'ordre d'apparition ne suffit pas : une explication peut très bien
        suivre le bloc replié sans être dedans."""
        profondeur, replie = 0, None
        for m in re.finditer(r"<div\b[^>]*>|</div>", fragment):
            if m.start() >= pos:
                break
            if m.group(0).startswith("</"):
                profondeur -= 1
                if replie is not None and profondeur <= replie:
                    replie = None
            else:
                if replie is None and "bloc-replie" in m.group(0):
                    replie = profondeur
                profondeur += 1
        return replie is not None

    for gid in ("pushGroup", "vapidSetupGroup"):
        groupe = panneau[panneau.index('id="%s"' % gid):]
        suivant = groupe.find('<div class="setting-group"', 10)
        if suivant != -1:
            groupe = groupe[:suivant]
        premiere = groupe.find('class="setting-desc" hidden')
        check(premiere != -1,
              "%s a bien une explication repliée à révéler" % gid)
        check(premiere != -1 and not dans_un_bloc_replie(groupe, premiere),
              "%s : le « ? » ouvre une explication visible, pas un texte "
              "enfermé dans un bloc lui-même masqué" % gid)

    # La cause de fond, prise à la racine plutôt qu'au cas par cas : le « ? »
    # ne vise qu'un enfant DIRECT du groupe. Sans cela il se greffait sur
    # n'importe quelle explication repliée, bloc annexe compris.
    corps = html[html.index("function greffeAides("):]
    corps = corps[:corps.index("\n}")]
    check(':scope > .setting-desc[hidden]' in corps,
          "le « ? » ne vise qu'une explication enfant direct du groupe")

    # Et réciproquement : une explication repliée à l'intérieur d'un bloc
    # annexe n'aurait plus aucun bouton pour l'ouvrir. Elle serait morte.
    for m in re.finditer(r'class="setting-desc" hidden', panneau):
        check(not dans_un_bloc_replie(panneau, m.start()),
              "aucune explication repliée n'est enfermée dans un bloc annexe, "
              "où plus rien ne pourrait l'ouvrir")

    # Le bloc de l'abonnement s'affiche EN PERMANENCE dès que l'appareil est
    # abonné. Le mode d'emploi complet y tenait quatre lignes : les
    # instructions d'une étape faite une fois restaient à l'écran pour
    # toujours. Une ligne, et le détail derrière le « ? ».
    bloc = panneau[panneau.index('id="pushSubscriptionBlock"'):]
    bloc = bloc[:bloc.index("<textarea")]
    visibles = re.findall(r'<div class="setting-desc">(.*?)</div>', bloc, re.S)
    for texte in visibles:
        nu = re.sub(r"<[^>]+>|\s+", " ", texte).strip()
        check(len(nu) <= 120,
              "le texte permanent du bloc d'abonnement tient en une ligne "
              "(%d caractères)" % len(nu))
    check("Dernière étape" not in panneau,
          "le pavé d'instructions ne campe plus dans le panneau")


def test_panneau_parametres_structure():
    print("\n[app] la structure du panneau ne piège plus le doigt")
    import re
    html = open("docs/index.html", encoding="utf-8").read()
    deb = html.index("<!-- ---------- Panneau Paramètres")
    panneau = html[deb:html.index("<!-- ---------- Modale Aperçu article")]

    # Le titre et les onglets défilaient avec le reste : sur l'onglet Contenu,
    # « Fermer » se retrouvait deux mille pixels plus haut.
    check('class="panneau-entete"' in panneau,
          "le titre et les onglets sont réunis dans une entête")
    bloc = re.search(r"#settingsOverlay \.panneau-entete\{([^}]*)\}", html).group(1)
    check("position:sticky" in bloc and "top:0" in bloc,
          "et cette entête reste collée en haut")
    # Un élément collant ne peut pas remonter au-dessus du bloc qui le
    # contient : avec un margin-top négatif pour manger le rembourrage du
    # panneau, il était repoussé plus bas que sa place et recouvrait les
    # premiers pixels de chaque onglet.
    check("margin:0 -20px" in bloc,
          "sans marge négative en haut, qui la ferait recouvrir le contenu")
    check(re.search(r"#settingsOverlay \.settings-panel\{[^}]*padding-top:0", html) is not None,
          "le rembourrage du haut est confié à l'entête, pas repris deux fois")

    # Deux zones de défilement imbriquées : on ne sait jamais laquelle on
    # pousse. La liste défilait chez elle pour garder les boutons du bas
    # atteignables — l'entête collante a rendu ce prétexte caduc.
    bloc = re.search(r"\.panneau-onglet #sourceList\{([^}]*)\}", html).group(1)
    check("overflow-y" not in bloc and "max-height" not in bloc,
          "la liste des sources ne défile plus dans son coin")

    # Et comme elle est longue, elle passe en DERNIER dans son onglet :
    # sinon les deux zones de mots-clés seraient enterrées derrière.
    onglet = panneau[panneau.index('data-onglet="src"'):]
    onglet = onglet[:onglet.index('data-onglet="adv"')]
    check(onglet.index('id="keywordsInput"') < onglet.index('id="sourceList"'),
          "les mots-clés viennent avant les cinquante-neuf bascules")

    # « Avancé » empilait six sujets sans rapport sans rien pour les séparer.
    onglet = panneau[panneau.index('data-onglet="adv"'):]
    check(onglet.count('class="panneau-section"') == 3,
          "« Avancé » est découpé en trois sujets nommés")

    # Un onglet « Affichage » qui contenait un groupe « Affichage » : le même
    # mot à deux niveaux de hiérarchie ne dit plus rien.
    onglet = panneau[panneau.index('data-onglet="aff"'):]
    onglet = onglet[:onglet.index('data-onglet="src"')]
    check('<span class="setting-label">Affichage</span>' not in onglet,
          "aucun groupe ne reprend le nom de l'onglet qui le contient")

    # Les actions destructives ne ressemblent plus à leurs voisines.
    # « Oublier » et non « Oublier ce jeton » : à trois boutons par rangée,
    # le libellé long ne tenait pas dans son tiers. Le groupe s'intitule
    # « Déclenchement à distance » et la ligne d'état juste en dessous parle
    # du jeton — le mot n'avait pas besoin d'être redit sur le bouton.
    for libelle in ("Réinitialiser", "Tout désactiver", "Oublier"):
        motif = r'<button[^>]*class="[^"]*danger[^"]*"[^>]*>%s<' % re.escape(libelle)
        check(re.search(motif, panneau) is not None,
              "« %s » est cerné de rouge, pas déguisé en bouton ordinaire" % libelle)
    check("Oublier ce token" not in panneau,
          "« token » et « jeton » ne cohabitent plus dans le même groupe")

    # Éteindre cinquante-neuf sources d'un geste efface une sélection que
    # « Tout activer » ne rend pas.
    corps = html[html.index("async function toutesSources("):]
    corps = corps[:corps.index("\n}")]
    check("demandeConfirmation(" in corps and "if(!ok) return;" in corps,
          "« Tout désactiver » demande confirmation")
    check(corps.index("if(!actif)") < corps.index("demandeConfirmation("),
          "mais « Tout activer » n'en demande pas : il se défait tout seul")

    # Un pluriel français ne se fabrique pas en collant un « s » : le premier
    # compteur affichait « 42 mot-clés » et « aucun exclusion ».
    corps = html[html.index("function majCompteurMots("):]
    corps = corps[:corps.index("\n}")]
    check('(uniques.size > 1 ? plusieurs : un)' in corps,
          "le compteur reçoit ses formes en toutes lettres")
    check('"aucun mot-clé"' in html and '"aucune exclusion"' in html,
          "singulier, pluriel et négation sont donnés par l'appelant")


def test_badge_de_notification_a_un_canal_alpha():
    print("\n[push] le badge de la barre d'état n'est pas un carré blanc")
    import json, re, struct
    sw = open("docs/sw.js", encoding="utf-8").read()
    page = open("docs/index.html", encoding="utf-8").read()

    # Il y a DEUX endroits qui affichent une notification, pas un : le
    # service worker pour les vraies, et le bouton « tester » des réglages.
    # La première version de ce test ne lisait que sw.js. Résultat : le badge
    # y a été corrigé, le bouton de test est resté sur icon-192.png, et c'est
    # précisément ce bouton qu'on presse pour vérifier la correction — le
    # carré blanc a donc survécu trois jours à son propre correctif, suite
    # verte. Le test balaie maintenant tout appel, où qu'il soit.
    declares = []
    for nom_source, texte in (("docs/sw.js", sw), ("docs/index.html", page)):
        appels = len(re.findall(r"\.showNotification\(", texte))
        badges = re.findall(r'badge:\s*"([^"]+)"', texte)
        check(appels > 0 and len(badges) == appels,
              "%s : les %d appel(s) à showNotification déclarent tous un badge "
              "(%d déclaré(s))" % (nom_source, appels, len(badges)))
        declares += [(nom_source, n) for n in badges]

    check(len(declares) >= 2,
          "les deux chemins d'affichage déclarent un badge (%d trouvé(s))"
          % len(declares))

    # Et ils doivent déclarer LE MÊME : sinon le bouton de test montre autre
    # chose que ce que le robot enverra, ce qui est pire qu'inutile.
    noms = {n for _, n in declares}
    check(len(noms) == 1,
          "tous les appels utilisent le même badge (%s)"
          % ", ".join(sorted(noms)))

    # Le piège, nommé. Les icônes du manifeste sont opaques par obligation
    # (test_icones_de_lapp le verrouille) ; en utiliser une comme badge donne
    # donc toujours un carré blanc. C'est l'erreur commise deux fois.
    icones = {e["src"] for e in json.load(
        open("docs/manifest.json", encoding="utf-8"))["icons"]}
    for nom_source, n in declares:
        check(n.lstrip("./") not in icones,
              "%s : le badge (%s) n'est pas une icône du manifeste — celles-ci "
              "sont opaques, donc carrées et blanches une fois réduites à leur "
              "alpha" % (nom_source, n))

    nom = sorted(noms)[0]
    chemin = "docs/" + nom.lstrip("./")
    check(os.path.exists(chemin), f"le fichier du badge existe ({nom})")
    if not os.path.exists(chemin):
        return

    # Le cœur du défaut. Android ne garde QUE le canal alpha du badge et
    # repeint la forme en blanc. Un PNG sans alpha (type 2 = RVB) a tous ses
    # pixels opaques : la silhouette est le carré entier, et l'utilisateur
    # voit un carré blanc dans sa barre d'état. C'est exactement ce qui se
    # passait avec badge:"icon-192.png" depuis l'ajout des push.
    donnees = open(chemin, "rb").read()
    check(donnees[:8] == b"\x89PNG\r\n\x1a\n", "le badge est bien un PNG")
    largeur, hauteur, _, type_couleur = struct.unpack(">IIBB", donnees[16:26])
    AVEC_ALPHA = (4, 6)   # 4 = gris+alpha, 6 = RVB+alpha
    check(type_couleur in AVEC_ALPHA,
          "le badge a un canal alpha (type PNG %d) — sans lui Android affiche "
          "un carré blanc" % type_couleur)
    check(largeur == hauteur,
          "le badge est carré (%dx%d)" % (largeur, hauteur))

    # Avoir un canal alpha ne suffit pas : encore faut-il qu'il serve. Un PNG
    # RGBA entièrement opaque redonnerait le même carré blanc.
    if type_couleur == 6:
        import zlib
        morceaux = b""
        i = 8
        while i < len(donnees):
            taille = struct.unpack(">I", donnees[i:i + 4])[0]
            if donnees[i + 4:i + 8] == b"IDAT":
                morceaux += donnees[i + 8:i + 8 + taille]
            i += 12 + taille
        brut = zlib.decompress(morceaux)
        bpp, pas = 4, largeur * 4
        transparents = 0
        precedente = bytearray(pas)
        position = 0
        for _ in range(hauteur):
            filtre = brut[position]; position += 1
            ligne = bytearray(brut[position:position + pas]); position += pas
            for x in range(pas):
                a = ligne[x - bpp] if x >= bpp else 0
                b = precedente[x]
                c = precedente[x - bpp] if x >= bpp else 0
                if filtre == 1: ligne[x] = (ligne[x] + a) & 255
                elif filtre == 2: ligne[x] = (ligne[x] + b) & 255
                elif filtre == 3: ligne[x] = (ligne[x] + (a + b) // 2) & 255
                elif filtre == 4:
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    ligne[x] = (ligne[x] + pr) & 255
            transparents += sum(1 for x in range(3, pas, bpp) if ligne[x] < 50)
            precedente = ligne
        part = 100 * transparents / (largeur * hauteur)
        check(part > 50,
              "le fond du badge est réellement transparent (%.0f %% des pixels) "
              "— un RGBA tout opaque redonnerait le carré blanc" % part)

    # Le service worker doit être remplacé, sinon la correction ne parvient
    # jamais aux téléphones déjà installés : c'est le nom du cache qui force
    # ce remplacement, et le fichier le documente lui-même.
    version = re.search(r'CACHE_NAME\s*=\s*"gta6watch-shell-v(\d+)"', sw)
    check(version is not None and int(version.group(1)) >= 4,
          "le nom du cache a été bumpé (v%s) pour remplacer l'ancien service "
          "worker" % (version.group(1) if version else "?"))


def _pixels_png(chemin):
    """Décode un PNG en lignes de pixels. Écrit à la main : le dépôt n'a pas
    Pillow, et l'ajouter pour vérifier quatre icônes serait disproportionné."""
    import struct, zlib
    d = open(chemin, "rb").read()
    largeur, hauteur, profondeur, couleur = struct.unpack(">IIBB", d[16:26])
    canaux = {0: 1, 2: 3, 4: 2, 6: 4}[couleur]
    bpp = canaux * profondeur // 8
    pas = largeur * bpp
    idat = b""
    i = 8
    while i < len(d):
        taille = struct.unpack(">I", d[i:i + 4])[0]
        if d[i + 4:i + 8] == b"IDAT":
            idat += d[i + 8:i + 8 + taille]
        i += 12 + taille
    brut = zlib.decompress(idat)
    lignes = []
    precedente = bytearray(pas)
    position = 0
    for _ in range(hauteur):
        filtre = brut[position]; position += 1
        ligne = bytearray(brut[position:position + pas]); position += pas
        if filtre:
            for x in range(pas):
                a = ligne[x - bpp] if x >= bpp else 0
                b = precedente[x]
                c = precedente[x - bpp] if x >= bpp else 0
                if filtre == 1: ligne[x] = (ligne[x] + a) & 255
                elif filtre == 2: ligne[x] = (ligne[x] + b) & 255
                elif filtre == 3: ligne[x] = (ligne[x] + (a + b) // 2) & 255
                else:
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    ligne[x] = (ligne[x] + pr) & 255
        lignes.append(bytes(ligne))
        precedente = ligne
    return largeur, hauteur, bpp, lignes



def test_archive_mensuelle():
    print("\n[archive] ce qui sort de la fenêtre ne sort pas du projet")
    import feed_store, json, os, shutil, tempfile
    from datetime import datetime, timedelta, timezone

    rep = tempfile.mkdtemp(prefix="archive-")
    try:
        base = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)

        def art(n, quand=None, titre=None):
            return {"link": "https://x.test/%d" % n,
                    "title": titre or "titre %d" % n,
                    "date": (quand or base - timedelta(minutes=n)).isoformat()}

        # Trois mois, pour vérifier que le rangement suit la DATE de
        # l'article et pas l'ordre dans lequel on les donne.
        melange = [art(1), art(2, base - timedelta(days=40)),
                   art(3), art(4, base - timedelta(days=80))]
        feed_store.archiver(melange, rep)
        check(sorted(n for n in os.listdir(rep)) == ["2026-08.json", "2026-10.json",
                                                     "2026-11.json"],
              "un fichier par mois, nommé d'après la date des articles")
        check(len(feed_store.lire_mois("2026-11", rep)) == 2,
              "et chaque article tombe dans le bon (2 en novembre)")

        # LE point de toute l'affaire : la fenêtre élague, l'archive garde.
        # Sans cette vérification, tout le reste ne serait que du rangement.
        fenetre = [art(1)]
        feed_store.archiver(fenetre, rep)
        garde = {i["link"] for i in feed_store.lire_mois("2026-11", rep)}
        check("https://x.test/3" in garde,
              "un article disparu de la fenêtre reste dans son mois")

        # Idempotence : rejouer le même passage ne doit produire AUCUNE
        # écriture. Sinon le robot signerait vingt-quatre commits par jour
        # sur des fichiers dont pas un octet n'a bougé.
        feed_store.archiver(melange, rep)
        check(feed_store.archiver(melange, rep) == {},
              "rejouer un passage identique ne réécrit rien")

        # Un article mis à jour (source supplémentaire, miniature trouvée)
        # DOIT être réécrit : c'est la version de la fenêtre qui fait foi.
        enrichi = dict(art(1))
        enrichi["extraSources"] = [{"source": "ailleurs"}]
        check(feed_store.archiver([enrichi], rep) != {},
              "un article enrichi depuis est bien réécrit")
        relu = {i["link"]: i for i in feed_store.lire_mois("2026-11", rep)}
        check(relu["https://x.test/1"].get("extraSources"),
              "et c'est la version enrichie qui est gardée")

        # Le mois de la sortie. 2500 est un plafond par FICHIER, pas par
        # mois : au-delà le mois est tranché, sinon novembre 2026 ferait un
        # seul fichier de plusieurs dizaines de Mo.
        gros = [art(n) for n in range(feed_store.ARCHIVE_MAX_PAR_FICHIER * 2 + 7)]
        feed_store.archiver(gros, rep)
        tranches = feed_store.tranches_du_mois("2026-11", rep)
        check(len(tranches) == 3,
              "un mois de sortie est coupé en tranches (%d ici)" % len(tranches))
        check(os.path.basename(tranches[0]) == "2026-11.json",
              "la première tranche garde le nom nu, celui qu'on tape à la main")
        check(len(feed_store.lire_mois("2026-11", rep)) == len(gros),
              "et la relecture les recolle sans en perdre un (%d)" % len(gros))

        # Un mois qui rétrécit ne doit pas laisser de tranche orpheline : le
        # site la servirait encore alors que l'index ne la cite plus.
        for nom in os.listdir(rep):
            os.remove(os.path.join(rep, nom))
        feed_store.archiver(gros, rep)
        feed_store.archiver([art(1)], rep)
        # rejouer depuis un sous-ensemble ne rétrécit RIEN (c'est voulu),
        # donc on force le cas en repartant d'un répertoire propre.
        petit = tempfile.mkdtemp(prefix="archive-petit-")
        try:
            feed_store.archiver(gros, petit)
            avant = len(feed_store.tranches_du_mois("2026-11", petit))
            for nom in os.listdir(petit):
                os.remove(os.path.join(petit, nom))
            feed_store.archiver([art(1)], petit)
            check(avant == 3 and len(feed_store.tranches_du_mois("2026-11", petit)) == 1,
                  "un mois qui rétrécit ne laisse pas de tranche orpheline")
        finally:
            shutil.rmtree(petit, ignore_errors=True)

        # L'index : c'est le seul fichier que l'app lit pour savoir quoi
        # demander. Il porte le POIDS autant que le nombre, sans quoi l'app
        # ne peut pas prévenir avant un téléchargement de plusieurs Mo.
        index = feed_store.ecrire_index_archives(rep)
        check(os.path.exists(os.path.join(rep, "index.json")),
              "l'index est écrit dans le répertoire des archives")
        check([e["mois"] for e in index["mois"]] ==
              sorted([e["mois"] for e in index["mois"]], reverse=True),
              "les mois y sont du plus récent au plus ancien")
        nov = next(e for e in index["mois"] if e["mois"] == "2026-11")
        check(nov["articles"] == len(gros),
              "l'index annonce le vrai nombre d'articles du mois")
        check(all(f["octets"] > 0 for f in nov["fichiers"]),
              "et le poids de chaque tranche, pour prévenir avant de télécharger")
        check(index["articles"] == sum(e["articles"] for e in index["mois"]),
              "le total de l'index est la somme de ses mois")
        check("index.json" not in [f["fichier"] for e in index["mois"]
                                   for f in e["fichiers"]],
              "l'index ne se liste pas lui-même comme un mois")

        # Un article sans lien n'est pas archivable : il n'a pas d'identité,
        # donc la fusion par lien le dupliquerait à chaque passage.
        avant = len(feed_store.lire_mois("2026-11", rep))
        feed_store.archiver([{"title": "sans lien", "date": base.isoformat()}], rep)
        check(len(feed_store.lire_mois("2026-11", rep)) == avant,
              "un article sans lien est ignoré plutôt que dupliqué sans fin")

        # Une tranche illisible ne doit pas emporter le mois entier.
        with open(os.path.join(rep, "2026-11.2.json"), "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du JSON")
        check(len(feed_store.lire_mois("2026-11", rep)) > 0,
              "une tranche corrompue ne fait pas disparaître tout le mois")
    finally:
        shutil.rmtree(rep, ignore_errors=True)




def test_laudit_surveille_larchive():
    print("\n[archive] l'audit voit les pannes de l'archive, pas seulement sa taille")
    import audit_donnees, feed_store
    import os, shutil, tempfile

    # L'archive est écrite à chaque passage et relue par personne. Un
    # fichier tronqué, un index périmé, un mois qui se vide : rien ne le
    # signalerait, et on s'en apercevrait le jour où l'on chercherait
    # quelque chose d'ancien — c'est-à-dire trop tard, la fenêtre l'ayant
    # depuis longtemps oublié. D'où cet audit, et d'où ce contrôle : un
    # audit qu'on ne met jamais en échec exprès ne prouve rien.
    rep = tempfile.mkdtemp(prefix="t-audit-arch-")
    autres = []
    try:
        fenetre = {"items": [
            {"link": "https://a.fr/%d" % n, "title": "t",
             "date": "2026-09-%02dT10:00:00+00:00" % (n % 9 + 1)}
            for n in range(20)]}

        def prepare():
            r = tempfile.mkdtemp(prefix="t-audit-arch-")
            autres.append(r)
            feed_store.archiver(fenetre["items"], r)
            feed_store.ecrire_index_archives(r)
            return r

        def codes(data=fenetre, r=None):
            return {a["code"] for a in audit_donnees.audite_archive(data, r or rep)}

        feed_store.archiver(fenetre["items"], rep)
        feed_store.ecrire_index_archives(rep)

        check(codes() == {"archive"},
              "une archive saine ne signale rien d'autre que son bilan")

        # LA panne qui compte : un article de la fenêtre absent de
        # l'archive. C'est l'invariant que l'archive existe pour tenir ;
        # s'il tombe, des articles disparaissent pour de bon.
        gonfle = {"items": fenetre["items"] + [
            {"link": "https://a.fr/perdu", "title": "t",
             "date": "2026-09-01T10:00:00+00:00"}]}
        check("archive-incomplete" in codes(gonfle),
              "un article de la fenêtre absent de l'archive est signalé GRAVE")
        check(all(a["gravite"] == "grave"
                  for a in audit_donnees.audite_archive(gonfle, rep)
                  if a["code"] == "archive-incomplete"),
              "et c'est bien la gravité maximale, pas un simple avertissement")

        # L'index est la seule chose que l'app lit. Un index qui annonce un
        # fichier absent l'envoie chercher dans le vide.
        r = prepare()
        nom = [n for n in os.listdir(r) if n != "index.json"][0]
        os.remove(os.path.join(r, nom))
        check("archive-fichier-manquant" in codes(r=r),
              "un fichier annoncé par l'index et absent du disque est signalé")

        # L'inverse : un fichier que l'index ne cite pas ne sera jamais
        # demandé. Ses articles existent et personne ne les verra.
        r = prepare()
        shutil.copy(os.path.join(r, nom if os.path.exists(os.path.join(r, nom))
                                 else [n for n in os.listdir(r) if n != "index.json"][0]),
                    os.path.join(r, "2025-01.json"))
        check("archive-fichier-orphelin" in codes(r=r),
              "un fichier présent mais absent de l'index est signalé")

        r = prepare()
        with open(os.path.join(r, "index.json"), "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du JSON")
        check("archive-index-illisible" in codes(r=r),
              "un index illisible est signalé plutôt que de faire planter l'audit")

        # Absence d'archive : anomalie SEULEMENT si la fenêtre a du contenu.
        # Un dépôt tout neuf n'a rien à se reprocher.
        vide = tempfile.mkdtemp(prefix="t-audit-vide-")
        autres.append(vide)
        check("archive-absente" in codes(r=vide),
              "pas d'archive alors que la fenêtre a des articles : signalé")
        check(codes({"items": []}, r=vide) == set(),
              "pas d'archive et pas d'articles : rien à signaler, c'est un début")
    finally:
        shutil.rmtree(rep, ignore_errors=True)
        for r in autres:
            shutil.rmtree(r, ignore_errors=True)


def test_ce_que_le_robot_publie_est_bien_commite():
    print("\n[workflow] tout ce que le robot écrit est commité, et ignoré par la CI")
    import re

    # Deux oublis du même genre ont déjà eu lieu : feed-recent.json absent du
    # paths-ignore (réparé le 16/09), et docs/archives/ qui aurait été écrit
    # à chaque passage puis jeté avec le runner, sans la moindre erreur.
    #
    # Le point commun : `git add` prend des CHEMINS, pas « ce qui a changé ».
    # Ajouter un fichier publié sans toucher au workflow ne casse rien, ne
    # fait rien, et ne dit rien. Ce test est le seul endroit où ça se voit.
    PUBLIES = ["docs/feed.json", "docs/feed-recent.json", "docs/archives"]

    wf = open(".github/workflows/update-feeds.yml", encoding="utf-8").read()
    adds = re.findall(r"git add ([^\n]+)", wf)
    check(len(adds) >= 2,
          "le workflow du robot a bien ses deux `git add` (%d trouvés)" % len(adds))
    # enumerate, et pas adds.index(ligne) : les deux lignes sont le MÊME
    # texte, donc index() rendrait 0 pour les deux et le second bloc
    # s'annoncerait sous le nom du premier. Un test qui ment sur ce qu'il
    # vérifie est pire qu'un test absent.
    for rang, ligne in enumerate(adds):
        ou = "publication" if rang == 0 else "après fusion"
        for chemin in PUBLIES:
            check(chemin in ligne,
                  "`git add` (%s) : %s est commité" % (ou, chemin))

    # Et l'autre moitié : ce que le robot pousse doit être ignoré par la CI,
    # SINON le filtre entier devient inopérant — GitHub ne saute un push que
    # si TOUS les chemins modifiés y figurent.
    checks = open(".github/workflows/checks.yml", encoding="utf-8").read()
    bloc = checks.split("paths-ignore:")[1].split("permissions:")[0]
    for chemin in PUBLIES:
        check(chemin in bloc,
              "paths-ignore couvre %s, sinon le filtre entier ne sert à rien"
              % chemin)

    # merge_feed.py réécrit l'archive après un conflit de push, parce que le
    # `git reset --hard` du workflow vient d'effacer celle du passage.
    mf = open("merge_feed.py", encoding="utf-8").read()
    check("archiver(" in mf and "ecrire_index_archives(" in mf,
          "merge_feed réarchive après fusion, le reset --hard ayant tout effacé")


def test_icones_de_lapp():
    print("\n[pwa] les icônes déclarées existent et tiennent dans la zone sûre")
    import json, math, struct
    manifeste = json.load(open("docs/manifest.json", encoding="utf-8"))

    for entree in manifeste["icons"]:
        chemin = "docs/" + entree["src"]
        check(os.path.exists(chemin), "%s existe" % entree["src"])
        if not os.path.exists(chemin):
            continue
        d = open(chemin, "rb").read()
        largeur, hauteur = struct.unpack(">II", d[16:24])
        attendu = int(entree["sizes"].split("x")[0])
        check((largeur, hauteur) == (attendu, attendu),
              "%s fait bien %dx%d" % (entree["src"], attendu, attendu))

    # Les icônes maskable et la zone sûre d'Android.
    #
    # Android recadre ces icônes en cercle, squircle ou carré arrondi selon
    # le lanceur. Il ne garantit que le disque central de 80 % du côté
    # (rayon 40 %) ; tout ce qui déborde peut être rogné.
    #
    # La version précédente de l'icône avait un bandeau à bord perdu, donc
    # incompatible par construction avec cette garantie — le test devait
    # alors se rabattre sur le masque circulaire réel (rayon 50 %), plus
    # large que la garantie. Vu sur un vrai écran d'accueil à 56 px, le
    # bandeau n'était qu'une barre bleue illisible tronquée par l'arrondi.
    #
    # Le dessin actuel — « VI » dominant, « WATCH » dessous — n'a plus
    # aucun élément à bord perdu. Le compromis n'a donc plus lieu d'être :
    # on exige que TOUT le contenu tienne dans la zone sûre conservatrice.
    # Si un futur dessin recommence à déborder, ce test le dira.
    for entree in manifeste["icons"]:
        if entree.get("purpose") != "maskable":
            continue
        chemin = "docs/" + entree["src"]
        if not os.path.exists(chemin):
            continue
        largeur, hauteur, bpp, lignes = _pixels_png(chemin)
        fond = lignes[0][0:3]
        BLEU = (88, 185, 255)    # --accent, la couleur du « VI »
        CLAIR = (228, 236, 232)  # --text, la couleur de « WATCH »

        def proche(p, q, tolerance=20):
            return max(abs(p[0] - q[0]), abs(p[1] - q[1]),
                       abs(p[2] - q[2])) <= tolerance

        centre = largeur / 2
        rayon = 0.0
        bleus = clairs = 0
        for y in range(hauteur):
            ligne = lignes[y]
            for x in range(largeur):
                p = ligne[x * bpp:x * bpp + 3]
                if proche(p, fond, 12):
                    continue
                rayon = max(rayon, math.hypot(x - centre, y - centre))
                if proche(p, BLEU, 30):
                    bleus += 1
                elif proche(p, CLAIR, 30):
                    clairs += 1

        sure = largeur * 0.40    # la garantie d'Android, rien de moins
        check(rayon <= sure + 1.5,
              "%s : tout le contenu tient dans la zone sûre (%.1f px pour %.1f)"
              % (entree["src"], rayon, sure))
        # Une icône qui tiendrait dans la zone sûre parce qu'elle est vide
        # passerait le test ci-dessus. On vérifie donc que les deux encres
        # sont bien là, en quantité crédible.
        check(bleus > largeur * hauteur * 0.02 and clairs > largeur * hauteur * 0.004,
              "%s : le VI et le WATCH sont tous les deux dessinés "
              "(%d px d'accent, %d px de texte)"
              % (entree["src"], bleus, clairs))

    # Les icônes « any », elles, ne sont pas recadrées : elles doivent au
    # contraire occuper la tuile, sinon l'app a l'air perdue au milieu de
    # son fond dans le sélecteur d'applis.
    for entree in manifeste["icons"]:
        if entree.get("purpose") == "maskable":
            continue
        chemin = "docs/" + entree["src"]
        if not os.path.exists(chemin):
            continue
        largeur, hauteur, bpp, lignes = _pixels_png(chemin)
        fond = lignes[0][0:3]
        centre = largeur / 2
        rayon = 0.0
        for y in range(hauteur):
            ligne = lignes[y]
            for x in range(largeur):
                p = ligne[x * bpp:x * bpp + 3]
                if max(abs(p[0] - fond[0]), abs(p[1] - fond[1]),
                       abs(p[2] - fond[2])) <= 12:
                    continue
                rayon = max(rayon, math.hypot(x - centre, y - centre))
        check(rayon >= largeur * 0.35,
              "%s : le dessin occupe la tuile (%.1f px, minimum %.1f)"
              % (entree["src"], rayon, largeur * 0.35))

    # Les icônes d'app doivent rester OPAQUES : une maskable transparente
    # laisserait voir le fond du lanceur à travers. C'est l'inverse exact de
    # la contrainte du badge de notification, qui lui exige de la
    # transparence — les confondre est facile.
    for entree in manifeste["icons"]:
        chemin = "docs/" + entree["src"]
        if not os.path.exists(chemin):
            continue
        couleur = struct.unpack(">B", open(chemin, "rb").read()[25:26])[0]
        check(couleur in (0, 2),
              "%s est opaque (type PNG %d) — une icône d'app n'est pas un badge"
              % (entree["src"], couleur))



def test_lecture_backend_ne_gonfle_pas_sur_une_coupure():
    print("\n[app] une coupure réseau ne doit pas faire réclamer le gros fichier")
    import re
    page = open("docs/index.html", encoding="utf-8").read()

    corps = page[page.index("async function checkFromBackend"):]
    corps = corps[:corps.index("\nasync function applyBackendData")]
    # Sans cette ligne, le test échouerait sur sa propre documentation : le
    # commentaire qui EXPLIQUE l'ancien défaut cite forcément la phrase
    # interdite. Déjà rencontré le 16/09 avec `|| ""`.
    sans_commentaires = re.sub(r"//.*", "", corps)

    # Le défaut du 17/09/2026 tenait dans un commentaire trop confiant :
    # « pas de fichier allégé » sur un catch qui attrapait AUSSI les pannes
    # réseau. L'app demandait alors feed.json (2,8 Mo) sur la connexion qui
    # venait de flancher. Ce test interdit le retour de cette confusion.
    check("pas de fichier allégé" not in sans_commentaires,
          "le catch ne prétend plus que le seul échec possible est un fichier absent")
    check("panneReseau" in corps,
          "checkFromBackend distingue une panne réseau d'une réponse négative")
    check(corps.count("throw panneReseau") == 1,
          "une panne réseau remonte, au lieu de basculer sur le fichier complet")

    # Un délai maximal, et un VRAI : une course de promesses rendrait la main
    # sans annuler le téléchargement, qui continuerait à consommer la
    # connexion qu'on cherche à ménager.
    check("AbortController" in page and "ctrl.abort()" in page,
          "les lectures du backend sont annulables (AbortController)")
    m = re.search(r"const BACKEND_TIMEOUT_MS\s*=\s*(\d+)", page)
    check(m is not None, "un délai maximal de lecture est défini")
    if m:
        ms = int(m.group(1))
        check(5000 <= ms <= 30000,
              "ce délai vaut %d ms — assez pour une 3G lente, assez court "
              "pour ne pas figer l'app" % ms)

    # Les DEUX lectures doivent l'utiliser : n'en protéger qu'une laisserait
    # exactement le chemin le plus lourd sans garde-fou.
    check(corps.count("fetchAvecDelai(avecAntiCache(") == 2,
          "le fichier allégé ET le fichier complet passent par le délai maximal")
    check("await fetch(avecAntiCache(" not in corps,
          "plus aucune lecture du backend ne part sans délai")

    # Une seule seconde chance : insister ferait patienter devant un mode
    # direct qui, lui, fonctionne.
    m = re.search(r"tentative < (\d+)", corps)
    check(m is not None and int(m.group(1)) == 2,
          "le fichier allégé est retenté exactement une fois")



def test_un_article_elague_nest_pas_annonce():
    print("\n[feed] un article qui ne survit pas au passage n'est pas une nouveauté")
    import re, feed_store

    # La régression, trouvée le 22/09/2026 sur une capture Discord : une
    # notification par heure annonçant « 301 nouveaux articles GTA 6 »,
    # « 298 », « 296 »… pour un rythme réel de 74 par JOUR. Et 1887 au
    # récapitulatif du matin.
    #
    # Cause : le plafond ramené à 1500 le 18/09 couvre environ dix-sept
    # jours, alors que MAX_ARTICLE_AGE_DAYS en accepte quarante-cinq à
    # l'entrée. Chaque passage réabsorbait les articles de 17 à 45 jours que
    # les flux resservent — absents de l'historique élagué, donc pris pour
    # neufs — puis cap_items les rejetait aussitôt. Mesuré sur deux passages
    # consécutifs : 2 articles réellement entrés, 293 annoncés.
    #
    # Le fichier n'a jamais été faux : c'est le COMPTE qui l'était, parce
    # qu'il était arrêté avant le plafond.
    src = open("fetch_feeds.py", encoding="utf-8").read()
    corps = src[src.index("def fetch_and_update"):] if "def fetch_and_update" in src else src

    pos_cap = corps.index("all_items, dropped = feed_store.cap_items(all_items)")
    pos_reconcile = corps.index("ephemeres = [i for i in newly_added")
    pos_images = corps.index("fetch_missing_images(newly_added)")
    pos_chaudes = corps.index("chaudes_neuves = [i for i in newly_added")
    pos_publie = corps.index('"new_this_run": len(newly_added)')

    check(pos_cap < pos_reconcile,
          "le plafond passe avant la réconciliation des nouveautés")
    for nom, pos in (("les miniatures ne voient", pos_images),
                     ("les actus majeures ne voient", pos_chaudes),
                     ("le compte publié ne voit", pos_publie)):
        check(pos_reconcile < pos,
              "%s que les articles qui ont survécu" % nom)

    # La réconciliation elle-même, rejouée : un article trop vieux entre,
    # le plafond le retire, il ne doit ni être annoncé ni compté.
    def art(lien, jour, **kw):
        i = {"link": lien, "title": lien, "source": "Test",
             "date": "2026-%02d-%02dT00:00:00+00:00" % (1 + jour // 28, 1 + jour % 28)}
        i.update(kw)
        return i

    recents = [art("recent-%d" % n, 20 + n % 8) for n in range(4)]
    vieux = art("vieux-resservi", 0)
    protege = art("officiel-vieux", 0, official=True)
    all_items = feed_store.sort_items(recents + [vieux, protege])
    newly_added = [vieux, protege, recents[0]]

    all_items, _ = feed_store.cap_items(all_items, max_size=5)
    survivants = {i["link"] for i in all_items}
    restants = [i for i in newly_added if i["link"] in survivants]

    check("vieux-resservi" not in survivants,
          "l'article trop ancien est bien élagué")
    check([i["link"] for i in restants] == ["officiel-vieux", "recent-%d" % 0],
          "il disparaît des nouveautés ; l'officiel vieux et le récent restent")

    # L'invariant qui résume tout : on n'annonce jamais ce qu'on n'a pas gardé.
    check(all(i["link"] in survivants for i in restants),
          "aucune nouveauté annoncée n'est absente du fil publié")

    # Et le compteur par source doit suivre, sinon la somme par source
    # contredirait le total affiché dans l'app.
    check("new_counts[fid] -= 1" in corps,
          "les compteurs par source sont décrémentés eux aussi")



def test_plafond_suit_le_volume():
    print("\n[tri] le plafond vise une profondeur, pas un nombre d'articles")
    import feed_store
    from datetime import datetime, timedelta, timezone

    # Pourquoi une profondeur plutôt qu'un nombre : 1500 valait dix-sept jours
    # à 74 articles/jour — le régime ordinaire — mais seulement six à 249/jour,
    # celui d'une journée d'annonce comme le 17/09/2026. Le jour de la sortie
    # du jeu, un nombre fixe ne tiendrait plus qu'un jour ou deux.
    #
    # Ce qui compte n'est pas le nombre gardé : c'est que la RECHERCHE (seule
    # à télécharger l'historique complet) remonte toujours aussi loin.
    maintenant = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

    def fil(par_jour, jours=45):
        items = []
        for j in range(jours):
            quand = (maintenant - timedelta(days=j)).isoformat()
            items.extend({"link": "j%d-n%d" % (j, n), "title": "t", "date": quand}
                         for n in range(par_jour))
        return feed_store.sort_items(items)

    def profondeur(gardes):
        dates = [feed_store.parse_date_key(i["date"]) for i in gardes]
        return (dates[0] - dates[-1]).days

    # Les trois régimes, parce que le calcul a trois branches et qu'un test
    # qui n'en visite qu'une laisse les deux autres libres de casser. Les
    # débits sont choisis pour encadrer les bornes à la profondeur du jour :
    # ils sont RECALCULÉS à partir de MAX_HISTORY_DAYS, pour que le test
    # continue de dire la vérité si la profondeur rebouge.
    j = feed_store.MAX_HISTORY_DAYS

    # 1. Semaine creuse : la fenêtre voudrait moins que le plancher, donc le
    # plancher s'applique — et la profondeur obtenue DÉPASSE la cible. C'est
    # voulu : on ne jette pas ce qu'on a de la place à garder.
    maigre = max(1, (feed_store.MIN_HISTORY_SIZE // j) - 10)
    vise = feed_store.taille_historique_visee(fil(maigre), maintenant)
    check(vise == feed_store.MIN_HISTORY_SIZE,
          "semaine creuse (%d/jour) : le plancher de %d s'applique"
          % (maigre, feed_store.MIN_HISTORY_SIZE))

    # 2. Régime ordinaire : la fenêtre commande, entre les deux bornes. C'est
    # la seule branche où la profondeur gardée vaut vraiment celle qu'on vise.
    ordinaire = (feed_store.MIN_HISTORY_SIZE + feed_store.MAX_HISTORY_SIZE) // (2 * j)
    charge = fil(ordinaire)
    vise = feed_store.taille_historique_visee(charge, maintenant)
    attendu = ordinaire * j
    check(feed_store.MIN_HISTORY_SIZE < vise < feed_store.MAX_HISTORY_SIZE,
          "régime ordinaire (%d/jour) : la fenêtre commande (%d articles)"
          % (ordinaire, vise))
    check(abs(vise - attendu) <= ordinaire,
          "et elle vise bien %d jours (%d visés pour ~%d attendus)"
          % (j, vise, attendu))

    # 3. Jour de sortie : le plafond dur protège le fichier. C'est LE cas qui
    # justifie la borne haute — quinze jours à ce rythme feraient 15 000
    # articles et une douzaine de Mo, soit le problème qu'on vient de régler.
    sortie = fil(1000)
    vise = feed_store.taille_historique_visee(sortie, maintenant)
    check(vise == feed_store.MAX_HISTORY_SIZE,
          "jour de sortie (1000/jour) : le plafond dur de %d tient"
          % feed_store.MAX_HISTORY_SIZE)

    # Les protégés ne comptent pas dans la fenêtre : les inclure la ferait
    # rétrécir à mesure qu'ils s'accumulent, et l'historique se réduirait
    # tout seul au fil des mois.
    avec_proteges = charge + [
        {"link": "off-%d" % n, "title": "t", "official": True,
         "date": (maintenant - timedelta(days=2)).isoformat()}
        for n in range(300)]
    check(feed_store.taille_historique_visee(feed_store.sort_items(avec_proteges),
                                             maintenant)
          == feed_store.taille_historique_visee(charge, maintenant),
          "300 articles protégés de plus ne rétrécissent pas la fenêtre")

    # Une date illisible est plus vieille que tout par construction : la
    # laisser peser réduirait la profondeur à cause d'un article mal daté.
    avec_illisible = charge + [{"link": "bancal-%d" % n, "title": "t",
                                "date": "pas une date"} for n in range(50)]
    check(feed_store.taille_historique_visee(feed_store.sort_items(avec_illisible),
                                             maintenant)
          == feed_store.taille_historique_visee(charge, maintenant),
          "un article mal daté ne compte pas dans la fenêtre")

    # Et le bout du bout : cap_items sans max_size explicite doit utiliser
    # cette visée, sinon tout ce qui précède ne décrirait qu'une fonction
    # que personne n'appelle.
    gardes, retires = feed_store.cap_items(sortie)
    check(len(gardes) == feed_store.MAX_HISTORY_SIZE,
          "cap_items sans argument applique bien la visée (%d gardés)" % len(gardes))
    check(profondeur(gardes) <= feed_store.MAX_HISTORY_DAYS,
          "la profondeur obtenue ne dépasse pas la cible quand le plafond mord")




def test_la_profondeur_est_reellement_atteinte():
    print("\n[tri] la profondeur annoncée est celle qu'on obtient vraiment")
    import feed_store
    from datetime import datetime, timedelta, timezone

    # Ce contrôle existe parce qu'un réglage a été changé le 22/09/2026 —
    # MAX_HISTORY_DAYS de 15 à 30 — sans que RIEN ne bouge dans le fichier
    # publié, et sans qu'aucun test ne le dise. La suite vérifiait la
    # fonction de calcul ; personne ne vérifiait que le robot, en la
    # rejouant passage après passage, arrivait quelque part.
    #
    # La cause était circulaire : la visée se lisait sur les articles DÉJÀ
    # stockés tombant dans la fenêtre, or cette liste est elle-même
    # plafonnée par la visée. En dessous d'un certain débit, le système
    # était gelé au plancher et la profondeur ne montait jamais.
    #
    # Un test de la fonction seule ne pouvait pas le voir. Celui-ci REJOUE
    # le cap, jour après jour, et regarde ce qu'on obtient au bout.
    maintenant = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

    def rejoue(par_jour, jours=45, proteges=140):
        items = [{"link": "prot-%d" % n, "title": "t", "official": True,
                  "date": (maintenant - timedelta(days=200 + n)).isoformat()}
                 for n in range(proteges)]
        n = 0
        for j in range(jours):
            quand = maintenant + timedelta(days=j + 1)
            for k in range(par_jour):
                n += 1
                items.append({"link": "a-%d" % n, "title": "t",
                              "date": (quand - timedelta(minutes=2 * k)).isoformat()})
            items = feed_store.sort_items(items)
            items, _ = feed_store.cap_items(
                items, feed_store.taille_historique_visee(items, quand))
        ordinaires = [i for i in items if not feed_store.item_protege(i)]
        dates = sorted(feed_store.parse_date_key(i["date"]) for i in ordinaires)
        return len(items), (dates[-1] - dates[0]).days

    vise = feed_store.MAX_HISTORY_DAYS

    # Régime réel du fil (94 articles/jour au 22/09). C'est LE cas qui
    # échouait : 14 jours obtenus pour 30 demandés.
    gardes, profondeur = rejoue(94)
    check(abs(profondeur - vise) <= 3,
          "à 94 articles/jour : %d jours obtenus pour %d demandés"
          % (profondeur, vise))
    check(gardes > feed_store.MIN_HISTORY_SIZE,
          "et la fenêtre a bien dépassé le plancher (%d articles)" % gardes)

    # Juste au-dessus du seuil de bascule de l'ancien calcul (le nombre de
    # protégés). L'ancien y donnait 9 jours, le pire de tous.
    gardes, profondeur = rejoue(142)
    check(abs(profondeur - vise) <= 4,
          "à 142 articles/jour : %d jours obtenus pour %d demandés"
          % (profondeur, vise))

    # Semaine creuse : le plancher commande, et la profondeur DÉPASSE la
    # cible. C'est voulu — on ne jette pas ce qu'on a la place de garder.
    gardes, profondeur = rejoue(40)
    check(gardes == feed_store.MIN_HISTORY_SIZE and profondeur >= vise,
          "semaine creuse : le plancher tient (%d articles, %d jours)"
          % (gardes, profondeur))

    # Jour de sortie : le plafond dur reprend la main et la fenêtre
    # rétrécit. C'est ce qui protège le fichier le 19/11.
    gardes, profondeur = rejoue(500)
    check(gardes == feed_store.MAX_HISTORY_SIZE and profondeur < vise,
          "gros débit : le plafond dur tranche (%d articles, %d jours)"
          % (gardes, profondeur))

    # Le débit se mesure sur les jours RÉVOLUS. Le jour en cours est
    # incomplet par construction : à midi il ne porte que la moitié de ses
    # articles, et le compter tirerait la mesure vers le bas à chaque
    # passage de la matinée.
    fil = [{"link": "j-%d-%d" % (j, k), "title": "t",
            "date": (maintenant - timedelta(days=j, minutes=k)).isoformat()}
           for j in range(1, 9) for k in range(100)]
    fil += [{"link": "aujourdhui-%d" % k, "title": "t",
             "date": (maintenant - timedelta(minutes=k)).isoformat()}
            for k in range(3)]
    check(feed_store.debit_quotidien(fil, maintenant) == 100,
          "le jour en cours, incomplet, ne tire pas le débit vers le bas "
          "(%g au lieu de 100)" % feed_store.debit_quotidien(fil, maintenant))

    # La MÉDIANE et non la moyenne : un seul jour d'annonce ne doit pas
    # élargir la fenêtre d'un tiers pour une semaine.
    calme = [{"link": "c-%d-%d" % (j, k), "title": "t",
              "date": (maintenant - timedelta(days=j, minutes=k)).isoformat()}
             for j in range(1, 8) for k in range(80)]
    pic = calme + [{"link": "pic-%d" % k, "title": "t",
                    "date": (maintenant - timedelta(days=3, minutes=k)).isoformat()}
                   for k in range(80, 700)]
    check(feed_store.debit_quotidien(pic, maintenant) == 80,
          "un jour à 700 articles ne bouge pas la médiane (%g)"
          % feed_store.debit_quotidien(pic, maintenant))

    # Et le garde-fou inverse : le débit ne doit JAMAIS faire jeter ce qui
    # tient déjà dans la fenêtre. Un afflux massif sur un historique jeune
    # n'a aucun jour révolu à mesurer.
    afflux = [{"link": "neuf-%d" % n, "title": "t", "date": maintenant.isoformat()}
              for n in range(feed_store.MAX_HISTORY_SIZE + 50)]
    check(feed_store.taille_historique_visee(afflux, maintenant)
          == feed_store.MAX_HISTORY_SIZE,
          "un afflux sans jour révolu n'est pas coupé au plancher")


def test_plancher_refuse_ce_qui_serait_elague():
    print("\n[feed] un article condamné d'avance n'entre plus du tout")
    import feed_store, datetime

    # Pourquoi ce filtre. Le plafond garde une profondeur ; le filtre
    # d'entrée accepte MAX_ARTICLE_AGE_DAYS (45 jours). Les flux resservent
    # donc en permanence des articles situés entre les deux : absents de
    # l'historique élagué, ils passaient pour neufs, étaient décodés,
    # dédupliqués, comptés — puis élagués à la fin du même passage. C'est
    # cette valse qui a produit le faux compteur du 22/09.
    maintenant = datetime.datetime(2026, 9, 22, 12, 0, tzinfo=datetime.timezone.utc)

    def art(lien, jours, **kw):
        i = {"link": lien, "title": lien, "source": "Test",
             "date": (maintenant - datetime.timedelta(days=jours)).isoformat()}
        i.update(kw)
        return i

    # --- Le plancher ne se déclenche que sur un historique PLEIN ---
    petit = feed_store.sort_items([art("a%d" % n, n % 10) for n in range(50)])
    check(feed_store.plancher_de_retention(petit) is None,
          "historique pas plein : aucun plancher, tout entre")

    # Le piège rencontré en écrivant ceci : interroger cap_items pour savoir
    # ce qui serait élagué ne marche pas. L'historique stocké sort DÉJÀ
    # plafonné du passage précédent, donc cap_items n'a plus rien à retirer
    # et le plancher serait toujours None — le filtre ne servirait jamais.
    plein = feed_store.sort_items(
        [art("vieux%d" % n, 30 + n % 20) for n in range(feed_store.MIN_HISTORY_SIZE)])
    _, retires = feed_store.cap_items(plein)
    check(retires == 0, "un historique déjà plafonné n'a plus rien à élaguer")
    check(feed_store.plancher_de_retention(plein) is not None,
          "et pourtant le plancher existe : il se lit sur le REMPLISSAGE, "
          "pas sur ce que cap_items retirerait")

    # --- Le plancher est bien le plus ancien ordinaire conservé ---
    plancher = feed_store.plancher_de_retention(plein)
    ordinaires = [feed_store.parse_date_key(i["date"]) for i in plein
                  if not feed_store.item_protege(i)]
    check(plancher == min(ordinaires),
          "le plancher est la date du plus ancien article ordinaire gardé")

    # --- Une date illisible ne doit pas écraser le plancher à 1970 ---
    avec_bancal = plein + [{"link": "bancal", "title": "t", "date": "pas une date"}]
    check(feed_store.plancher_de_retention(feed_store.sort_items(avec_bancal))
          == plancher,
          "un article mal daté ne tire pas le plancher jusqu'en 1970")

    # --- LE contrat : refuser à l'entrée ne change pas le fichier publié ---
    #
    # Ce n'est pas « on espère ne rien perdre » : le plancher vient du même
    # calcul que le plafond, donc tout article refusé aurait été retiré à la
    # fin du même passage. Les deux chemins doivent donner le MÊME résultat.
    entrants = ([art("recent%d" % n, n % 3) for n in range(20)]
                + [art("tresvieux%d" % n, 60 + n) for n in range(40)]
                + [art("officiel-vieux", 80, official=True)]
                + [art("rmag-vieux", 70, rockstarmag=True)])

    def publie(avec_filtre):
        fil = list(plein)
        refuses = 0
        for it in entrants:
            if (avec_filtre and plancher is not None
                    and not feed_store.item_protege(it)
                    and feed_store.parse_date_key(it["date"]) < plancher):
                refuses += 1
                continue
            fil.append(it)
        garde, _ = feed_store.cap_items(feed_store.sort_items(fil))
        return [i["link"] for i in garde], refuses

    sans, _ = publie(False)
    avec, refuses = publie(True)
    check(sans == avec,
          "le fichier publié est IDENTIQUE avec et sans le filtre")
    check(refuses == 40,
          "les 40 articles condamnés d'avance sont refusés à l'entrée (%d)" % refuses)
    check("officiel-vieux" in avec and "rmag-vieux" in avec,
          "un protégé vieux passe toujours — c'est tout l'intérêt de le protéger")
    check(sum(1 for l in avec if l.startswith("recent")) == 20,
          "et les articles récents entrent tous")

    # --- Le pipeline passe bien le plancher, et rend le compte des refus ---
    src = open("fetch_feeds.py", encoding="utf-8").read()
    check("plancher = feed_store.plancher_de_retention(existing_items)" in src,
          "le passage calcule le plancher sur l'état d'avant")
    check("plancher=plancher" in src,
          "et le transmet à merge_results")
    check("refuses_trop_vieux[0] += 1" in src,
          "les refus sont comptés, pas silencieux")
    check("item_protege(item)" in src,
          "les protégés sont exemptés dans la boucle de fusion")



def test_lecteurs_de_liaison_mutualises():
    print("\n[notif] les deux canaux lisent exactement la même chose")
    import feed_store, discord_notify, push_notify, json as _json, os as _os, tempfile

    # Ces deux lecteurs vivaient en double, à l'identique, dans
    # discord_notify et push_notify — et c'était la zone la moins testée du
    # dépôt (audit du 22/09). La duplication exacte est précisément ce qui
    # avait dérivé en silence sur la liste des sources fin août : trois
    # sources marquées « sans filtre » d'un côté et filtrées de l'autre.
    #
    # Ici l'enjeu est direct : deux copies qui divergeraient feraient
    # annoncer deux nombres DIFFÉRENTS pour le même passage, l'un sur
    # Discord et l'autre en notification push.
    check(discord_notify.lire_liste is feed_store.lire_liste
          and push_notify.lire_liste is feed_store.lire_liste,
          "les deux canaux partagent la même lire_liste")
    check(discord_notify.lire_totaux_recap is feed_store.lire_totaux_recap
          and push_notify.lire_totaux_recap is feed_store.lire_totaux_recap,
          "et la même lire_totaux_recap")

    # --- lire_liste : tolérante par construction ---
    #
    # Un fichier absent est le cas NORMAL : le robot ne l'écrit que s'il a
    # quelque chose à dire. Un fichier abîmé ne doit pas empêcher la
    # notification de partir — mieux vaut annoncer sans détail que se taire.
    check(feed_store.lire_liste("") == [], "chemin vide -> liste vide")
    check(feed_store.lire_liste("/rien/du/tout.json") == [],
          "fichier absent -> liste vide, pas une exception")

    tmp = tempfile.mkdtemp()
    def fichier(nom, contenu):
        c = _os.path.join(tmp, nom)
        with open(c, "w", encoding="utf-8") as f:
            f.write(contenu)
        return c

    check(feed_store.lire_liste(fichier("bon.json", '[{"t": 1}]')) == [{"t": 1}],
          "une vraie liste est rendue telle quelle")
    check(feed_store.lire_liste(fichier("casse.json", "{pas du json")) == [],
          "un JSON abîmé -> liste vide")
    check(feed_store.lire_liste(fichier("objet.json", '{"a": 1}')) == [],
          "un objet au lieu d'une liste -> liste vide")

    # --- lire_totaux_recap : None plutôt qu'un compte à moitié lu ---
    #
    # Renvoyer un tuple partiel ferait annoncer un nombre FAUX, ce qui est
    # pire que de retomber sur le comptage direct de la liste.
    vrai = _os.environ.get("RECAP_TOTALS_FILE")
    try:
        _os.environ.pop("RECAP_TOTALS_FILE", None)
        check(feed_store.lire_totaux_recap() is None,
              "variable absente -> None, on retombe sur le comptage direct")

        _os.environ["RECAP_TOTALS_FILE"] = fichier(
            "t.json", _json.dumps({"articles": 14, "officiels": 1, "sommet": 4}))
        check(feed_store.lire_totaux_recap() == (14, 1, 4),
              "des totaux sains sont lus dans l'ordre attendu")

        for nom, contenu, cas in (
                ("vide.json", "", "fichier vide"),
                ("liste.json", "[1, 2]", "une liste au lieu d'un objet"),
                ("texte.json", '{"articles": "beaucoup"}', "un compte non numérique")):
            _os.environ["RECAP_TOTALS_FILE"] = fichier(nom, contenu)
            check(feed_store.lire_totaux_recap() is None,
                  "%s -> None plutôt qu'un compte faux" % cas)

        _os.environ["RECAP_TOTALS_FILE"] = "/rien/du/tout.json"
        check(feed_store.lire_totaux_recap() is None,
              "fichier absent -> None")
    finally:
        if vrai is None:
            _os.environ.pop("RECAP_TOTALS_FILE", None)
        else:
            _os.environ["RECAP_TOTALS_FILE"] = vrai
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    # Les copies ne doivent pas revenir : c'est le seul moyen d'empêcher la
    # divergence de se réinstaller au prochain ajustement.
    for f in ("discord_notify.py", "push_notify.py"):
        src = open(f, encoding="utf-8").read()
        check("def lire_liste(" not in src and "def lire_totaux_recap(" not in src,
              "%s ne redéfinit aucun des deux lecteurs" % f)



def test_epoque_unix_nest_pas_une_date():
    print("\n[dates] l'époque Unix est une absence de date, pas une date")
    import fetch_feeds, feed_store, datetime

    # Repéré par l'audit du 22/09 : la page de support de Rockstar arrivait
    # datée du 01/01/1970 et se rangeait pour toujours en fin de liste.
    # Comme elle est officielle, elle est protégée de l'élagage : elle y
    # serait restée indéfiniment, avec une date qui ne veut rien dire.
    t = datetime.datetime(2026, 9, 22, 10, 0, tzinfo=datetime.timezone.utc)

    # On ne réécrit JAMAIS une date lisible. Inventer une date est
    # exactement ce que normalize_date refuse de faire ; on comble une
    # absence, on ne corrige pas une information.
    check(fetch_feeds.date_ou_premiere_vue("2026-09-01T10:00:00+00:00", t)
          == "2026-09-01T10:00:00+00:00",
          "une date lisible est laissée intacte")
    for cas, valeur in (("l'époque Unix", "1970-01-01T00:00:00+00:00"),
                        ("une date vide", ""),
                        ("une date illisible", "pas une date")):
        check(fetch_feeds.date_ou_premiere_vue(valeur, t) == t.isoformat(),
              "%s est remplacée par la première vue" % cas)

    # normalize_date ne doit plus LAISSER PASSER l'époque en amont, sinon le
    # repli ne servirait qu'à rattraper ce qu'on vient de produire.
    import time
    epoque = time.struct_time((1970, 1, 1, 0, 0, 0, 3, 1, 0))
    check(fetch_feeds.normalize_date({"published_parsed": epoque}) == "",
          "normalize_date ne rend plus la date d'époque, mais du vide")
    vraie = time.struct_time((2026, 9, 1, 12, 0, 0, 1, 244, 0))
    check(fetch_feeds.normalize_date({"published_parsed": vraie})
          == "2026-09-01T12:00:00+00:00",
          "et une vraie date structurée passe toujours")

    # --- La reprise rétroactive ---
    #
    # L'historique n'est jamais rejoué dans le pipeline de collecte : sans
    # elle, les articles déjà engrangés garderaient leur 1970 pour toujours.
    fil = [{"link": "a", "title": "t", "date": "1970-01-01T00:00:00+00:00"},
           {"link": "b", "title": "t", "date": "2026-09-10T00:00:00+00:00"},
           {"link": "c", "title": "t", "date": ""}]
    fetch_feeds.repare_dates_epoque(fil, t)
    check(fil[0]["date"] == t.isoformat() and fil[2]["date"] == t.isoformat(),
          "les articles sans date exploitable sont repositionnés")
    check(fil[1]["date"] == "2026-09-10T00:00:00+00:00",
          "et celui qui avait une vraie date n'a pas bougé")

    # Idempotente : un second passage ne doit rien re-déplacer, sinon la
    # date avancerait d'une heure à chaque exécution du robot.
    plus_tard = t + datetime.timedelta(hours=3)
    fetch_feeds.repare_dates_epoque(fil, plus_tard)
    check(fil[0]["date"] == t.isoformat(),
          "et un second passage ne la fait pas glisser (idempotente)")

    # Branchée dans le pipeline, sinon tout ce qui précède décrit une
    # fonction que personne n'appelle.
    src = open("fetch_feeds.py", encoding="utf-8").read()
    check("existing_items = repare_dates_epoque(existing_items)" in src,
          "la reprise est appelée avec les autres corrections rétroactives")
    check("date_ou_premiere_vue(date)" in src,
          "et le repli s'applique à la construction de chaque article")


def test_readme_annonce_le_bon_nombre():
    print("\n[doc] le README annonce le vrai nombre de vérifications")
    import re
    # DOIT rester la dernière fonction de la liste : elle lit le compteur
    # global, donc tout le reste doit avoir déjà tourné.
    #
    # Ce nombre est resté bloqué à 728 pendant que la suite en atteignait 892,
    # sans que rien ne le signale : le test des constantes ne surveille que ce
    # qui est écrit entre dos d'âne, pas les chiffres en toute lettre. Un
    # nombre faux dans un README est pire que pas de nombre du tout — il a
    # l'air vérifié.
    readme = open("README.md", encoding="utf-8").read()
    m = re.search(r"\*\*(\d+)\s+vérifications\*\*", readme)
    check(m is not None, "le README annonce un nombre de vérifications")
    if not m:
        return
    annonce = int(m.group(1))
    # +1 : la vérification ci-dessous. Celle de l'existence du nombre, juste
    # au-dessus, a DÉJÀ incrémenté le compteur — la compter une seconde fois
    # donnait un total de trop.
    reel = CHECKS + 1
    check(annonce == reel,
          "le README annonce %d vérifications, la suite en compte %d%s"
          % (annonce, reel,
             "" if annonce == reel else "  →  corrige le README avec %d" % reel))


for fn in (test_parse_date_key, test_sort_and_cap, test_normalize_stored_dates,
           test_merge_no_loss, test_merge_keeps_our_version, test_merge_normalizes_and_caps,
           test_merge_refuses_empty_local, test_feed_store_io,
           test_canonical_link, test_canonicalize_stored_links,
           test_push_payload, test_push_subscriptions, test_push_vapid_subject,
           test_push_masquage_endpoint,
           test_push_survit_a_un_telephone_verrouille,
           test_real_history,
           test_fetch_parallele_identique, test_chaine_youtube_rockstar,
           test_onglets_par_domaine, test_couverture_par_lien,
           test_chaine_youtube_rockstarmag,
           test_doublons_de_titre,
           test_garde_fou_archives,
           test_dedup_meme_passage,
           test_libelle_actu_majeure, test_promotion_entre_passages,
           test_suivi_sources_muettes,
           test_retirer_une_source_ne_laisse_pas_ses_articles,
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
           test_plafond_epargne_rockstar, test_prefiltre_de_ressemblance,
           test_ergonomie_tactile,
           test_structure_et_annonces,
           test_derniers_reports_tactiles_et_courbes,
           test_echelle_m3_verrouillee,
           test_panneaux_sont_de_vrais_dialogues,
           test_recap_du_matin_couvre_la_nuit,
           test_alerte_officielle_rockstar,
           test_pause_nocturne,
           test_filtres_persistants,
           test_icones_en_emoji,
           test_contraste_des_deux_themes,
           test_readme_ne_cite_que_des_constantes_reelles,
           test_panne_serveur_nest_pas_une_source_cassee,
           test_les_workflows_epinglent_leurs_dependances,
           test_envoi_push_reel_testable,
           test_aucun_mot_cle_nen_contient_un_autre,
           test_variantes_du_nom_dans_les_requetes_google_news,
           test_panneau_parametres_intact,
           test_panneau_parametres_applique_vraiment,
           test_plafond_daffichage_a_de_vraies_bornes,
           test_cibles_tactiles_du_panneau,
           test_panneau_parametres_accessible,
           test_panneau_parametres_structure,
           test_ligne_etat_sans_double_compte,
           test_ligne_run_tient_sur_une_ligne,
           test_confirmation_des_actions_sans_retour,
           test_haut_de_page_une_seule_carte,
           test_validation_avant_ecriture,
           test_badge_de_notification_a_un_canal_alpha,
           test_lecture_backend_ne_gonfle_pas_sur_une_coupure,
           test_elagage_declare_au_garde_fou,
           test_un_article_elague_nest_pas_annonce,
           test_plafond_suit_le_volume,
           test_la_profondeur_est_reellement_atteinte,
           test_plancher_refuse_ce_qui_serait_elague,
           test_lecteurs_de_liaison_mutualises,
           test_epoque_unix_nest_pas_une_date,
           test_archive_mensuelle,
           test_laudit_surveille_larchive,
           test_ce_que_le_robot_publie_est_bien_commite,
           test_icones_de_lapp,
           test_readme_annonce_le_bon_nombre):
    fn()

print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} vérifications passées")
if FAILURES:
    print("ÉCHECS :")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("Tout est vert.")
