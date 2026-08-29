#!/usr/bin/env python3
"""Teste des URL de flux candidates et dit lesquelles sont utilisables.

Le 29/08/2026, les neuf sources hébergées par rss.app se sont tues
simultanément — flux valides mais vides, sans erreur HTTP. Les remplacer par
les flux natifs des sites supprimerait ce point de défaillance unique, mais
encore faut-il connaître les bonnes URL : deviner puis pousser reviendrait à
remplacer neuf sources en panne par neuf sources inexistantes.

Cette sonde interroge chaque candidate EXACTEMENT comme le robot le fait
(même agent utilisateur, même bibliothèque) et rapporte ce qu'elle obtient.
Elle ne modifie rien : ni FEEDS, ni docs/, ni quoi que ce soit sur disque.

    python sonde_flux.py            # les candidates de remplacement
    python sonde_flux.py --actuels  # en plus, les rss.app en place

À lancer depuis GitHub Actions (onglet Actions -> « Sonder des flux
candidats »), pas en local : les runners ont un accès réseau complet, ce qui
n'est pas le cas de tous les environnements de développement.

Fichier jetable — à supprimer une fois les remplacements décidés.
"""

import socket
import sys
from datetime import datetime, timezone
from urllib.error import URLError

import feedparser

import fetch_feeds


# Plusieurs variantes par site : les éditeurs déplacent leurs flux, et une
# seule tentative par source ne distingue pas « le site n'a pas de flux » de
# « je me suis trompé d'URL ».
CANDIDATES = {
    "rockstarmag": [
        "https://www.rockstarmag.fr/feed/",
        "https://www.rockstarmag.fr/rss",
        "https://rockstarmag.fr/feed/",
    ],
    "rockstarintel": [
        "https://www.rockstarintel.com/feed/",
        "https://rockstarintel.com/feed/",
        "https://www.rockstarintel.com/rss",
    ],
    "ign": [
        "https://feeds.ign.com/ign/games-all",
        "https://feeds.ign.com/ign/all",
        "https://www.ign.com/rss/articles/feed",
        "https://www.ign.com/feed.xml",
    ],
    "gamespot": [
        "https://www.gamespot.com/feeds/game-news/",
        "https://www.gamespot.com/feeds/news/",
        "https://www.gamespot.com/feeds/mashup/",
    ],
    "polygon": [
        "https://www.polygon.com/rss/index.xml",
        "https://www.polygon.com/rss/gaming/index.xml",
    ],
    "kotaku": [
        "https://kotaku.com/rss",
        "https://kotaku.com/feed",
        "https://kotaku.com/feed/rss",
    ],
    "gamesradar": [
        "https://www.gamesradar.com/rss/",
        "https://www.gamesradar.com/feeds.xml",
        "https://www.gamesradar.com/feeds/articletype/news/",
    ],
    "rps": [
        "https://www.rockpapershotgun.com/feed",
        "https://www.rockpapershotgun.com/feed/",
        "https://www.rockpapershotgun.com/rss",
    ],
    "eurogamer": [
        "https://www.eurogamer.net/feed",
        "https://www.eurogamer.net/feed/news",
        "https://www.eurogamer.net/?format=rss",
    ],
}


def age_en_jours(entree):
    struct = entree.get("published_parsed") or entree.get("updated_parsed")
    if not struct:
        return None
    try:
        quand = datetime(*struct[:6], tzinfo=timezone.utc)
    except Exception:
        return None
    return (datetime.now(timezone.utc) - quand).days


def sonder(url):
    """Renvoie un dictionnaire décrivant ce que l'URL a réellement donné."""
    try:
        parsed = feedparser.parse(url, agent=fetch_feeds.USER_AGENT)
    except Exception as e:
        return {"verdict": "ERREUR", "detail": f"{type(e).__name__}: {e}"[:70]}

    statut = getattr(parsed, "status", None)
    entrees = parsed.entries or []

    if not entrees:
        # Distinguer les quatre « rien » possibles. Le dernier — un flux
        # valide mais vide — est exactement la panne rss.app : c'est celui
        # qu'on doit pouvoir nommer sans ambiguïté.
        if statut and statut >= 400:
            return {"verdict": "REFUSÉ", "statut": statut}
        if parsed.bozo:
            souci = parsed.bozo_exception
            texte = str(souci)
            # feedparser ne lève pas sur une panne réseau : il la range dans
            # bozo, au même endroit qu'un XML mal formé. Les confondre ferait
            # conclure « ce site n'a pas de flux » alors que c'est la machine
            # qui n'a pas pu sortir.
            reseau = isinstance(souci, (URLError, socket.timeout, OSError)) or any(
                m in texte for m in ("urlopen error", "timed out", "Connection",
                                     "Tunnel", "certificate", "Name or service"))
            return {"verdict": "INJOIGNABLE" if reseau else "PAS UN FLUX",
                    "statut": statut, "detail": texte[:60]}
        # feedparser avale une page HTML sans protester : bozo reste faux et
        # la liste d'entrées est vide, exactement comme un flux valide mais
        # vide. Seul `version` les sépare — vide pour un document qui n'est
        # pas un flux, « rss20 » ou « atom10 » sinon. Les confondre ferait
        # prendre une URL erronée pour une source en panne temporaire.
        if not getattr(parsed, "version", ""):
            return {"verdict": "PAS UN FLUX", "statut": statut,
                    "detail": "le document n'annonce aucun format de flux"}
        return {"verdict": "VIDE", "statut": statut,
                "detail": f"flux {parsed.version} valide, mais zéro entrée"}

    # Combien passeraient le filtre du robot. Le filtre porte sur le titre et
    # le résumé, comme en production.
    faux_feed = {"official": False}
    retenues = sum(1 for e in entrees
                   if fetch_feeds.passe_le_filtre(faux_feed, e.get("title", ""),
                                                  e.get("summary", "")))
    ages = [j for j in (age_en_jours(e) for e in entrees) if j is not None]

    return {
        "verdict": "OK",
        "statut": statut,
        "entrees": len(entrees),
        "retenues": retenues,
        "plus_recent": min(ages) if ages else None,
        "sans_date": len(entrees) - len(ages),
        "titre": (parsed.feed.get("title") or "")[:44],
    }


def ligne(url, r):
    if r["verdict"] != "OK":
        detail = r.get("detail", "")
        statut = f"HTTP {r['statut']}" if r.get("statut") else ""
        print(f"    ✗ {r['verdict']:11} {statut:9} {url}")
        if detail:
            print(f"                            {detail}")
        return False

    frais = ("jamais daté" if r["plus_recent"] is None
             else f"le plus récent : {r['plus_recent']} j")
    print(f"    ✓ {r['entrees']:3} entrées, {r['retenues']:2} après filtre GTA 6, "
          f"{frais}")
    print(f"      {url}")
    if r["titre"]:
        print(f"      titre du flux : « {r['titre']} »")
    if r["sans_date"]:
        print(f"      ⚠ {r['sans_date']} entrée(s) sans date exploitable")
    return True


def main():
    avec_actuels = "--actuels" in sys.argv
    par_id = {f["id"]: f for f in fetch_feeds.FEEDS}

    if avec_actuels:
        print("=" * 72)
        print("LES FLUX rss.app ACTUELLEMENT EN PLACE")
        print("=" * 72)
        for sid in CANDIDATES:
            feed = par_id.get(sid)
            if not feed:
                continue
            print(f"\n  {feed['name']}")
            ligne(feed["url"], sonder(feed["url"]))
        print()

    print("=" * 72)
    print("CANDIDATES DE REMPLACEMENT")
    print("=" * 72)

    trouve = {}
    injoignables = 0
    for sid, urls in CANDIDATES.items():
        nom = par_id.get(sid, {}).get("name", sid)
        print(f"\n  {nom}")
        for url in urls:
            r = sonder(url)
            if r["verdict"] == "INJOIGNABLE":
                injoignables += 1
            if ligne(url, r) and sid not in trouve:
                # On garde la PREMIÈRE qui marche : les variantes sont
                # rangées de la plus spécifique à la plus large.
                trouve[sid] = url

    print()
    print("=" * 72)
    print(f"BILAN — {len(trouve)}/{len(CANDIDATES)} sources ont un flux natif utilisable")
    print("=" * 72)
    for sid in CANDIDATES:
        nom = par_id.get(sid, {}).get("name", sid)
        if sid in trouve:
            print(f"  ✓ {nom:22} {trouve[sid]}")
        else:
            print(f"  ✗ {nom:22} aucune candidate ne répond — "
                  f"repli Google News « site: » à envisager")
    if injoignables:
        print()
        print(f"⚠ {injoignables} URL n'ont pas pu être testées faute d'accès réseau.")
        print("  Ce bilan ne veut alors rien dire : relance depuis GitHub Actions,")
        print("  onglet Actions -> « Sonder des flux candidats ».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
