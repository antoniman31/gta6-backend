"""
GTA6_WATCH — Récupération de l'historique via Wayback Machine (ponctuel).

Contrairement à fetch_feeds.py (qui tourne automatiquement toutes les 3h),
CE script ne tourne PAS sur un planning — il n'a aucun sens de le relancer
souvent puisqu'il va chercher des archives passées, pas de l'actualité.

À lancer UNE SEULE FOIS manuellement (voir README, section "Historique
depuis 2022"), pour peupler docs/feed.json avec les annonces Rockstar
archivées sur web.archive.org depuis février 2022 (date de l'annonce
officielle du développement de GTA 6). Une fois lancé, son résultat est
fusionné avec le reste de l'historique par fetch_feeds.py à sa prochaine
exécution normale.

Limites honnêtes à connaître avant de lancer ce script :
- L'API Wayback Machine (CDX) peut être lente ou temporairement
  indisponible ; le script réessaie automatiquement, mais peut prendre
  plusieurs minutes à tourner en entier.
- Seules les pages que quelqu'un a pensé à archiver existent — la
  couverture n'est jamais garantie à 100%, notamment sur les tout premiers
  mois de 2022.
- Ce script cible spécifiquement les pages du Newswire Rockstar
  (rockstargames.com/newswire) archivées, pas l'ensemble d'internet.
"""

import json
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CDX_API = "http://web.archive.org/cdx/search/cdx"
START_DATE = "20220201"  # février 2022, mois de l'annonce officielle du développement


def get_archived_snapshots():
    """Interroge l'API CDX pour lister tous les instantanés archivés du
    Newswire Rockstar depuis février 2022, un par jour maximum (evite les
    doublons d'archives multiples du même jour)."""
    params = {
        "url": "rockstargames.com/newswire*",
        "from": START_DATE,
        "to": datetime.now().strftime("%Y%m%d"),
        "output": "json",
        "collapse": "timestamp:8",  # un instantané par jour maximum
        "filter": "statuscode:200",
    }
    try:
        resp = requests.get(CDX_API, params=params, timeout=30)
        resp.raise_for_status()
        rows = resp.json()
        if len(rows) <= 1:  # la première ligne est l'en-tête de colonnes
            return []
        return rows[1:]  # retire l'en-tête
    except Exception as e:
        print(f"Erreur API Wayback CDX : {e}")
        return []


def extract_article_from_snapshot(timestamp, original_url):
    """Récupère le titre et la description d'une page archivée."""
    archive_url = f"http://web.archive.org/web/{timestamp}/{original_url}"
    try:
        resp = requests.get(archive_url, timeout=15, headers={"User-Agent": USER_AGENT})
        soup = BeautifulSoup(resp.text, "html.parser")

        title_tag = soup.find("meta", property="og:title") or soup.find("title")
        title = title_tag.get("content") if title_tag and title_tag.get("content") else (title_tag.text if title_tag else None)
        if not title:
            return None

        desc_tag = soup.find("meta", property="og:description")
        description = desc_tag.get("content", "") if desc_tag else ""

        image_tag = soup.find("meta", property="og:image")
        image = image_tag.get("content") if image_tag else None

        date = f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}T00:00:00"

        return {
            "title": title.strip(),
            "link": original_url,  # on garde le vrai lien Rockstar, pas l'URL d'archive
            "date": date,
            "source": "Rockstar Games Newswire (archive)",
            "official": True,
            "rockstarmag": False,
            "specialist": False,
            "lang": None,
            "image": image,
            "description": description[:500],
        }
    except Exception as e:
        print(f"  échec extraction {archive_url[:80]}... : {e}")
        return None


def matches_gta6_keywords(item):
    keywords = ["gta 6", "gta vi", "grand theft auto vi", "grand theft auto 6"]
    text = (item["title"] + " " + item["description"]).lower()
    return any(k in text for k in keywords)


def main():
    print(f"Recherche des archives du Newswire Rockstar depuis {START_DATE}...")
    snapshots = get_archived_snapshots()
    print(f"{len(snapshots)} instantané(s) archivé(s) trouvé(s)")

    if not snapshots:
        print("Aucun instantané trouvé — vérifie ta connexion ou réessaie plus tard (l'API Wayback est parfois temporairement indisponible).")
        return

    found_items = []
    for i, row in enumerate(snapshots):
        # Colonnes CDX : urlkey, timestamp, original, mimetype, statuscode, digest, length
        timestamp, original_url = row[1], row[2]
        print(f"[{i+1}/{len(snapshots)}] {timestamp} — {original_url[:70]}")

        item = extract_article_from_snapshot(timestamp, original_url)
        if item and matches_gta6_keywords(item):
            found_items.append(item)
            print(f"  -> retenu : {item['title'][:60]}")

        time.sleep(1)  # reste courtois envers l'API gratuite d'archive.org

    print(f"\n{len(found_items)} article(s) GTA 6 trouvés dans les archives")

    # Fusionne avec l'historique existant plutôt que d'écraser
    existing_sources_count = 0
    try:
        with open("docs/feed.json", "r", encoding="utf-8") as f:
            existing = json.load(f)
            existing_items = existing.get("items", [])
            existing_sources_count = existing.get("sources_count", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        existing_items = []

    existing_links = {item["link"] for item in existing_items}
    truly_new = [item for item in found_items if item["link"] not in existing_links]

    all_items = existing_items + truly_new
    all_items.sort(key=lambda x: x["date"], reverse=True)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_articles": len(all_items),
        "new_this_run": len(truly_new),
        "sources_count": existing_sources_count,
        "items": all_items,
    }

    with open("docs/feed.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nTerminé — {len(truly_new)} nouveaux articles d'archive ajoutés, {len(all_items)} au total.")


if __name__ == "__main__":
    main()
