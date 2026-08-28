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


def test_push_payload():
    print("\n[push] contenu de la notification")
    import push_notify

    un = push_notify.build_payload([{"title": "Rockstar annonce la date de sortie", "official": True}])
    check(un["title"].startswith("1 nouvel article"), "singulier correct pour un seul article")
    check("officiel" in un["title"], "les articles officiels sont signalés dans le titre")
    check(un["body"] == "Rockstar annonce la date de sortie",
          "le titre de l'article sert de corps (sinon il faut ouvrir l'app pour savoir de quoi il s'agit)")

    trois = push_notify.build_payload([
        {"title": "Un trailer inattendu", "official": False},
        {"title": "Autre chose", "official": False},
        {"title": "Encore autre chose", "official": False}])
    check(trois["title"].startswith("3 nouveaux articles"), "pluriel correct")
    check("officiel" not in trois["title"], "rien d'officiel : pas de mention parasite")
    check("et 2 autres" in trois["body"], "le reste est résumé en nombre")
    check(trois["tag"] == un["tag"], "tag identique : une notification remplace la précédente")

    long_titre = push_notify.build_payload([{"title": "x" * 400, "official": False}])
    check(len(long_titre["body"]) <= 180, "corps tronqué pour ne pas déborder")


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


for fn in (test_parse_date_key, test_sort_and_cap, test_normalize_stored_dates,
           test_merge_no_loss, test_merge_keeps_our_version, test_merge_normalizes_and_caps,
           test_merge_refuses_empty_local, test_feed_store_io,
           test_push_payload, test_push_subscriptions, test_real_history):
    fn()

print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} vérifications passées")
if FAILURES:
    print("ÉCHECS :")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("Tout est vert.")
