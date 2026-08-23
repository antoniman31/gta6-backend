"""
GTA6_WATCH — Backend de récupération des flux RSS.

Ce script tourne sur un planning via GitHub Actions (voir
.github/workflows/update-feeds.yml). Il n'a pas besoin d'être lancé
manuellement — il s'exécute automatiquement toutes les 3 heures et publie
son résultat dans docs/feed.json, servi ensuite par GitHub Pages.

Ce que fait ce script, dans l'ordre :
1. Lit la liste des sources ci-dessous (les mêmes 34 que dans le tracker HTML)
2. Récupère chaque flux RSS — aucun souci de CORS ici, on est côté serveur
3. Pour les flux Google News : décode le vrai lien de l'article (au lieu du
   lien de redirection news.google.com) via le package googlenewsdecoder
4. Va chercher la vraie miniature (og:image) de chaque article sans image
5. Filtre par mots-clés selon le type de source (identique au tracker HTML)
6. Déduplique par lien + par similarité de titre
7. Écrit tout dans docs/feed.json, un fichier statique servi par GitHub Pages

Le tracker HTML n'aura plus qu'à lire ce seul fichier JSON, au lieu de
contacter 34 flux + des proxys CORS à chaque vérification.
"""

import json
import re
import time
import os
from datetime import datetime, timezone
from difflib import SequenceMatcher

import feedparser
import requests
from bs4 import BeautifulSoup

try:
    from googlenewsdecoder import gnewsdecoder
    HAS_DECODER = True
except ImportError:
    HAS_DECODER = False

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# ---------------------------------------------------------------------------
# Liste des sources — copiée depuis DEFAULT_FEEDS dans gta6-watch.html.
# Si tu ajoutes/retires une source dans le tracker HTML, reporte le
# changement ici aussi pour garder les deux synchronisés.
# ---------------------------------------------------------------------------
FEEDS = [
    {"id": "rockstar-en", "name": "Rockstar Games (officiel EN)", "url": "https://news.google.com/rss/search?q=site:rockstargames.com&hl=en&gl=US&ceid=US:en", "official": True},
    {"id": "rockstar-fr", "name": "Rockstar Games (officiel FR)", "url": "https://news.google.com/rss/search?q=site:rockstargames.com&hl=fr&gl=FR&ceid=FR:fr", "official": True, "lang": "fr"},
    {"id": "rockstar-announce", "name": "Rockstar Games (annonces)", "url": "https://news.google.com/rss/search?q=%22Rockstar+Games%22+(%22Grand+Theft+Auto+VI%22+OR+%22GTA+6%22)+(announce+OR+announces+OR+reveals+OR+confirms)&hl=en&gl=US&ceid=US:en", "official": True},
    {"id": "gta6-netflix", "name": "GTA 6 x Netflix", "url": "https://news.google.com/rss/search?q=(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)+Netflix&hl=en&gl=US&ceid=US:en", "official": False, "no_filter": True},
    {"id": "take2-ir", "name": "Take-Two Investor Relations (officiel)", "url": "https://ir.take2games.com/rss/news-releases.xml?items=15", "official": True},
    {"id": "gnews-fr", "name": "Google News (FR)", "url": "https://news.google.com/rss/search?q=%22GTA+6%22&hl=fr&gl=FR&ceid=FR:fr", "official": False, "lang": "fr"},
    {"id": "gnews-en", "name": "Google News (EN)", "url": "https://news.google.com/rss/search?q=%22GTA+6%22&hl=en&gl=US&ceid=US:en", "official": False},
    {"id": "pcgamer", "name": "PC Gamer", "url": "https://www.pcgamer.com/rss.xml", "official": False},
    {"id": "insider", "name": "Insider Gaming", "url": "https://insider-gaming.com/feed/", "official": False},
    {"id": "tomshw", "name": "Tom's Hardware", "url": "https://www.tomshardware.com/feeds/all", "official": False},
    {"id": "jvcom", "name": "Jeuxvideo.com", "url": "https://www.jeuxvideo.com/rss/rss.xml", "official": False, "lang": "fr"},
    {"id": "gamekult", "name": "Gamekult", "url": "https://www.gamekult.com/feed.xml", "official": False, "lang": "fr"},
    {"id": "ignfr", "name": "IGN France", "url": "https://fr.ign.com/feed.xml", "official": False, "lang": "fr"},
    {"id": "rockstarmag", "name": "RockstarMag.fr", "url": "https://rss.app/feeds/DLjO259X0dbIodkg.xml", "official": False, "rockstarmag": True, "no_filter": True, "lang": "fr"},
    {"id": "rockstarintel", "name": "RockstarINTEL", "url": "https://rss.app/feeds/SEi9eWoOTGKoeh2K.xml", "official": False, "no_filter": True},
    {"id": "ign", "name": "IGN", "url": "https://rss.app/feeds/h34hzwwy7imC3FZL.xml", "official": False},
    {"id": "gamespot", "name": "GameSpot", "url": "https://rss.app/feeds/vjQBa27Dcc6PFmvy.xml", "official": False},
    {"id": "polygon", "name": "Polygon", "url": "https://rss.app/feeds/cs8cjYH9LJUvrQxI.xml", "official": False},
    {"id": "kotaku", "name": "Kotaku", "url": "https://rss.app/feeds/lxhehgdlVzWzuQMQ.xml", "official": False},
    {"id": "gamesradar", "name": "GamesRadar+", "url": "https://rss.app/feeds/dhyjfZPgrCnv3JBq.xml", "official": False},
    {"id": "vg247", "name": "VG247", "url": "https://rss.app/feeds/UjAtI3JL6IVYdlG7.xml", "official": False},
    {"id": "rps", "name": "Rock Paper Shotgun", "url": "https://rss.app/feeds/P80yvK0878prZ0RB.xml", "official": False},
    {"id": "eurogamer", "name": "Eurogamer", "url": "https://rss.app/feeds/TR4mCR6nRT4S88lX.xml", "official": False},
    {"id": "gtaboom", "name": "GTA BOOM", "url": "https://www.gtaboom.com/feed.xml", "official": False},
    {"id": "vgtimes", "name": "VGTimes", "url": "https://vgtimes.com/news/rss.xml", "official": False},
    {"id": "ginjfo", "name": "GinjFo", "url": "https://www.ginjfo.com/feed", "official": False, "lang": "fr"},
    {"id": "gameblog", "name": "Gameblog.fr", "url": "https://www.gameblog.fr/rssmap/rss_all.xml", "official": False, "lang": "fr"},
    {"id": "jeuxactu", "name": "JeuxActu", "url": "https://www.jeuxactu.com/rss/ja.rss", "official": False, "lang": "fr"},
    {"id": "actugaming", "name": "ActuGaming", "url": "https://www.actugaming.net/feed/", "official": False, "lang": "fr"},
    {"id": "gamerant", "name": "Game Rant", "url": "https://gamerant.com/feed", "official": False},
    {"id": "dualshockers", "name": "DualShockers", "url": "https://www.dualshockers.com/feed", "official": False},
    {"id": "gta6times", "name": "GTA6 Times", "url": "https://gta6times.com/rss.xml", "official": False, "no_filter": True},
    {"id": "gameinformer", "name": "Game Informer", "url": "https://gameinformer.com/news.xml", "official": False},
    {"id": "pcgamesn", "name": "PCGamesN", "url": "https://www.pcgamesn.com/mainrss.xml", "official": False},
    {"id": "vgc", "name": "VGC", "url": "https://www.videogameschronicle.com/category/news/feed/", "official": False},
]

KEYWORDS = [
    "gta 6", "gta vi", "gta6", "gtavi", "grand theft auto vi", "grand theft auto 6",
    "gta-6", "gta-vi", "grand-theft-auto-6", "rockstar gta 6", "vice city", "leonida",
    "cyberleek", "take-two", "rockstar games", "gta 6 release date", "gta 6 trailer",
    "gta 6 gameplay", "gta 6 leak",
]
OFFICIAL_KEYWORDS = ["gta 6", "gta vi", "gta6", "gtavi", "grand theft auto vi", "grand theft auto 6"]
SIMILARITY_THRESHOLD = 0.75


def matches_keywords(text, keywords):
    text_low = text.lower()
    return any(k in text_low for k in keywords)


def decode_google_news_link(url):
    """Résout le vrai lien d'article derrière un lien news.google.com."""
    if not HAS_DECODER or "news.google.com" not in url:
        return url
    try:
        result = gnewsdecoder(url, interval=1)
        if result.get("status") and result.get("decoded_url"):
            return result["decoded_url"]
    except Exception as e:
        print(f"  [decode] échec pour {url[:60]}... : {e}")
    return url


def fetch_og_image(url, timeout=8):
    """Récupère l'image de prévisualisation (og:image) d'une vraie page article."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        soup = BeautifulSoup(resp.text, "html.parser")
        tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
        if tag and tag.get("content"):
            return tag["content"]
    except Exception as e:
        print(f"  [og:image] échec pour {url[:60]}... : {e}")
    return None


def normalize_title(title):
    """Nettoie un titre pour la comparaison de similarité (retire ponctuation, minuscules)."""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def title_similarity(a, b):
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def is_duplicate(item, existing_items):
    for other in existing_items:
        if item["link"] == other["link"]:
            return True
        if title_similarity(item["title"], other["title"]) >= SIMILARITY_THRESHOLD:
            return True
    return False


def fetch_feed(feed):
    print(f"[{feed['name']}] récupération...")
    try:
        parsed = feedparser.parse(feed["url"], agent=USER_AGENT)
        if parsed.bozo and not parsed.entries:
            print(f"  échec : {parsed.bozo_exception}")
            return []
    except Exception as e:
        print(f"  échec réseau : {e}")
        return []

    items = []
    for entry in parsed.entries[:30]:
        title = entry.get("title", "")
        link = entry.get("link", "")
        date = entry.get("published", entry.get("updated", ""))
        description = entry.get("summary", "")

        # Filtre par mots-clés, identique à la logique du tracker HTML
        if feed.get("official"):
            if not matches_keywords(title, OFFICIAL_KEYWORDS):
                continue
        elif not feed.get("no_filter"):
            if not matches_keywords(title + " " + description, KEYWORDS):
                continue

        # Décodage du vrai lien pour les flux Google News
        real_link = decode_google_news_link(link) if "news.google.com" in feed["url"] else link

        # Miniature : cherche d'abord dans le flux RSS lui-même, sinon va la
        # chercher sur la vraie page de l'article
        image = None
        if "media_content" in entry and entry.media_content:
            image = entry.media_content[0].get("url")
        elif "media_thumbnail" in entry and entry.media_thumbnail:
            image = entry.media_thumbnail[0].get("url")
        if not image and real_link:
            image = fetch_og_image(real_link)

        items.append({
            "title": title,
            "link": real_link,
            "date": date,
            "source": feed["name"],
            "official": feed.get("official", False),
            "rockstarmag": feed.get("rockstarmag", False),
            "specialist": feed.get("no_filter", False),
            "lang": feed.get("lang"),
            "image": image,
            "description": re.sub("<[^<]+?>", "", description)[:500],
        })

    print(f"  {len(items)} article(s) pertinent(s)")
    return items


MAX_HISTORY_SIZE = 2000  # au-delà, on retire les plus vieux articles pour garder le script rapide

# URL du webhook Discord, configurée comme secret GitHub (voir README) —
# jamais écrite en clair dans ce fichier. Si absente, les notifications
# sont simplement désactivées, tout le reste du script continue de marcher.
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
NOTIFY_MAX_PER_RUN = 5  # évite de spammer le salon Discord si beaucoup d'articles arrivent d'un coup


def send_discord_notification(new_items):
    """Envoie un message Discord pour chaque nouvel article (limité), en
    priorisant les sources officielles. N'échoue jamais le script principal
    si Discord est indisponible ou mal configuré."""
    if not DISCORD_WEBHOOK_URL or not new_items:
        return

    # Priorise les annonces officielles et spécialistes, puis les plus récentes
    sorted_items = sorted(new_items, key=lambda x: (not x.get("official"), not x.get("specialist"), x.get("date", "")), reverse=False)
    to_send = sorted_items[:NOTIFY_MAX_PER_RUN]

    for item in to_send:
        badge = "🟠 OFFICIEL ROCKSTAR" if item.get("official") else ("⭐ Spécialiste GTA 6" if item.get("specialist") else "")
        embed = {
            "title": item["title"][:250],
            "url": item["link"],
            "description": (item.get("description") or "")[:300],
            "color": 0xFF6B00 if item.get("official") else 0x5493FF,
            "footer": {"text": item["source"] + (" — " + badge if badge else "")},
        }
        if item.get("image"):
            embed["thumbnail"] = {"url": item["image"]}

        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
            if resp.status_code not in (200, 204):
                print(f"  [discord] échec envoi ({resp.status_code}) pour : {item['title'][:50]}")
        except Exception as e:
            print(f"  [discord] erreur d'envoi : {e}")
        time.sleep(1)  # évite le rate-limit Discord (webhooks limités à quelques req/s)


# Nom du "topic" ntfy.sh, configuré comme secret GitHub (voir README) — un
# nom que toi seul connais fait office de mot de passe implicite, puisque
# ntfy.sh est un service public sans compte. Si absent, désactivé, le reste
# du script continue de marcher normalement.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")  # personnalisable si auto-hébergé plus tard


def send_ntfy_notification(new_items):
    """Envoie une vraie notification push Android/iOS via ntfy.sh — plus
    simple que Discord pour qui veut juste une notification classique sur
    son téléphone, sans app de chat. Nécessite d'avoir installé l'app ntfy
    (Google Play ou F-Droid) et de s'être abonné au même topic."""
    if not NTFY_TOPIC or not new_items:
        return

    sorted_items = sorted(new_items, key=lambda x: (not x.get("official"), not x.get("specialist"), x.get("date", "")), reverse=False)
    to_send = sorted_items[:NOTIFY_MAX_PER_RUN]

    for item in to_send:
        tag = "rotating_light" if item.get("official") else ("star" if item.get("specialist") else "video_game")
        headers = {
            "Title": ("[OFFICIEL] " if item.get("official") else "") + item["source"],
            "Tags": tag,
            "Click": item["link"],
            "Priority": "high" if item.get("official") else "default",
        }
        if item.get("image"):
            headers["Attach"] = item["image"]

        try:
            resp = requests.post(
                f"{NTFY_SERVER}/{NTFY_TOPIC}",
                data=item["title"][:250].encode("utf-8"),
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                print(f"  [ntfy] échec envoi ({resp.status_code}) pour : {item['title'][:50]}")
        except Exception as e:
            print(f"  [ntfy] erreur d'envoi : {e}")
        time.sleep(1)


def load_existing_items():
    """Charge les articles déjà connus depuis la dernière exécution, pour ne
    jamais rien perdre même si un article sort de la fenêtre RSS récente
    d'une source (généralement limitée aux 10-30 derniers items)."""
    try:
        with open("docs/feed.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("items", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def main():
    existing_items = load_existing_items()
    is_first_run = len(existing_items) == 0
    print(f"Historique chargé : {len(existing_items)} article(s) déjà connus" + (" (premier lancement)" if is_first_run else ""))

    all_items = list(existing_items)  # on part de l'historique, pas de zéro
    newly_added = []

    for feed in FEEDS:
        items = fetch_feed(feed)
        for item in items:
            if not is_duplicate(item, all_items):
                all_items.append(item)
                newly_added.append(item)
        time.sleep(1)  # anti rate-limit entre les sources

    all_items.sort(key=lambda x: x["date"], reverse=True)

    # Plafonne la taille de l'historique : au-delà de MAX_HISTORY_SIZE, on
    # retire les articles les plus anciens plutôt que de laisser le fichier
    # (et le temps de dédup) grossir indéfiniment.
    if len(all_items) > MAX_HISTORY_SIZE:
        print(f"Historique plafonné : {len(all_items)} -> {MAX_HISTORY_SIZE} (les plus anciens sont retirés)")
        all_items = all_items[:MAX_HISTORY_SIZE]

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_articles": len(all_items),
        "new_this_run": len(newly_added),
        "sources_count": len(FEEDS),
        "items": all_items,
    }

    with open("docs/feed.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nTerminé — {len(newly_added)} nouveau(x), {len(all_items)} au total dans docs/feed.json")

    # Pas de notification au tout premier lancement : l'historique est vide,
    # donc "tout" serait considéré comme nouveau — ça enverrait des dizaines
    # de messages d'un coup au lieu de rester silencieux jusqu'à la vraie
    # actualité suivante.
    if not is_first_run:
        send_discord_notification(newly_added)
        send_ntfy_notification(newly_added)
    elif newly_added:
        print(f"Premier lancement : {len(newly_added)} article(s) initiaux, pas de notification envoyée.")


if __name__ == "__main__":
    main()
