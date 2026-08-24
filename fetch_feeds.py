"""
GTA6_WATCH — Backend de récupération des flux RSS.

Ce script tourne sur un planning via GitHub Actions (voir
.github/workflows/update-feeds.yml). Il n'a pas besoin d'être lancé
manuellement — il s'exécute automatiquement toutes les heures et publie
son résultat dans docs/feed.json, servi ensuite par GitHub Pages.

Ce que fait ce script, dans l'ordre :
1. Lit la liste des sources ci-dessous (les mêmes 35 que dans le tracker HTML)
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

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
    {"id": "gta6-netflix", "name": "GTA 6 x Netflix", "url": "https://news.google.com/rss/search?q=(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)+Netflix&hl=en&gl=US&ceid=US:en", "official": False, "specialist_source": True},
    {"id": "take2-ir", "name": "Take-Two Investor Relations (officiel)", "url": "https://ir.take2games.com/rss/news-releases.xml?items=15", "official": True},
    {"id": "gnews-fr", "name": "Google News (FR)", "url": "https://news.google.com/rss/search?q=%22GTA+6%22&hl=fr&gl=FR&ceid=FR:fr", "official": False, "lang": "fr"},
    {"id": "gnews-en", "name": "Google News (EN)", "url": "https://news.google.com/rss/search?q=%22GTA+6%22&hl=en&gl=US&ceid=US:en", "official": False},
    {"id": "pcgamer", "name": "PC Gamer", "url": "https://www.pcgamer.com/rss.xml", "official": False},
    {"id": "insider", "name": "Insider Gaming", "url": "https://insider-gaming.com/feed/", "official": False},
    {"id": "tomshw", "name": "Tom's Hardware", "url": "https://www.tomshardware.com/feeds/all", "official": False},
    {"id": "jvcom", "name": "Jeuxvideo.com", "url": "https://www.jeuxvideo.com/rss/rss.xml", "official": False, "lang": "fr"},
    {"id": "gamekult", "name": "Gamekult", "url": "https://www.gamekult.com/feed.xml", "official": False, "lang": "fr"},
    {"id": "ignfr", "name": "IGN France", "url": "https://fr.ign.com/feed.xml", "official": False, "lang": "fr"},
    {"id": "rockstarmag", "name": "RockstarMag", "url": "https://rss.app/feeds/DLjO259X0dbIodkg.xml", "official": False, "rockstarmag": True, "specialist_source": True, "no_filter_at_all": True, "lang": "fr"},
    {"id": "rockstarintel", "name": "RockstarINTEL", "url": "https://rss.app/feeds/SEi9eWoOTGKoeh2K.xml", "official": False, "specialist_source": True},
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
    {"id": "gta6times", "name": "GTA6 Times", "url": "https://gta6times.com/rss.xml", "official": False, "specialist_source": True},
    {"id": "gameinformer", "name": "Game Informer", "url": "https://gameinformer.com/news.xml", "official": False},
    {"id": "pcgamesn", "name": "PCGamesN", "url": "https://www.pcgamesn.com/mainrss.xml", "official": False},
    {"id": "vgc", "name": "VGC", "url": "https://www.videogameschronicle.com/category/news/feed/", "official": False},
]

# Liste enrichie fournie par l'utilisateur (139 mots-clés) — remplace
# l'ancienne liste courte de 19 termes, jamais synchronisée avec celle-ci
# jusqu'à présent malgré la duplication FEEDS/DEFAULT_FEEDS déjà documentée
# plus haut dans ce fichier.
KEYWORDS = [
    "gta 6", "gta vi", "gta6", "gtavi", "grand theft auto vi", "grand theft auto 6", "gta-6", "gta-vi", "grand-theft-auto-6", "gta_6", "gta_vi", "gtaonline6", "gta 6 news", "gta vi news", "gta6 news", "rockstar gta 6", "rockstar's gta 6", "rockstar gta vi", "rockstar's gta vi", "rockstar next game", "rockstar new game", "rockstar upcoming game", "next gta", "new gta", "future gta", "upcoming gta", "gta next", "grand theft auto next", "gta sixth game", "gta sequel", "vice city", "vicecity", "new vice city", "gta vice city 2026", "leonida", "leonida state", "leonida map", "leonida gta", "cyberleek", "cyber leek", "cyberleak", "cyber leak gta", "gta 6 cyberleek", "take-two", "take two", "take-two interactive", "taketwo", "take2", "rockstar games", "rockstar north", "rockstar san diego", "rockstargames", "rockstar studio", "rockstar dev", "gta 6 release date", "gta vi release date", "gta 6 launch date", "gta 6 launch", "gta 6 date", "gta 6 november", "gta 6 2026", "gta vi 2026", "gta 6 delay", "gta 6 delayed", "gta vi delay", "gta 6 postponed", "gta 6 trailer", "gta vi trailer", "gta 6 trailer 3", "gta 6 new trailer", "gta 6 teaser", "gta vi teaser", "gta 6 extended look", "gta 6 netflix", "gta 6 gameplay", "gta vi gameplay", "gta 6 gameplay leak", "gta 6 gameplay video", "gta 6 footage", "gta vi footage", "gta 6 clip", "gta 6 leak", "gta vi leak", "gta 6 leaks", "gta 6 leaked", "gta 6 leaked footage", "gta 6 leaked gameplay", "gta 6 leaked map", "gta 6 hack", "gta 6 breach", "gta 6 map", "gta vi map", "gta 6 map leak", "gta 6 characters", "gta vi characters", "lucia caminos", "jason duval", "gta 6 protagonist", "gta 6 pre-order", "gta vi pre-order", "gta 6 preorder", "gta 6 price", "gta 6 edition", "gta 6 collector", "gta 6 pc", "gta 6 ps5", "gta 6 xbox", "gta 6 console", "gta 6 xbox series", "gta 6 playstation 5", "gta 6 system requirements", "gta 6 online", "gta 6 multiplayer", "gta online 2", "gta 6 rating", "gta 6 esrb", "gta 6 age rating", "gta 6 budget", "gta 6 development", "gta 6 dev", "gta 6 news today", "gta 6 update", "gta 6 announcement", "gta 6 reveal", "gta 6 confirmed", "gta 6 rumor", "gta 6 rumors", "gta 6 speculation", "gta 6 preview event", "gta 6 media event", "gta 6 hands-on", "gta 6 preview", "gta 6 review", "gta 6 dlc", "gta 6 season pass", "gta 6 dmca", "gta 6 takedown", "gta 6 copyright", "gta 6 discord"
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
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
        if tag and tag.get("content"):
            return tag["content"]
        # La page a répondu correctement mais n'a aucune balise d'image
        # exploitable — vraie limite du site, pas une erreur réseau.
        print(f"  [og:image] aucune balise og:image/twitter:image sur {url[:60]}...")
    except Exception as e:
        print(f"  [og:image] échec pour {url[:60]}... : {e}")
    return None


def normalize_title(title):
    """Nettoie un titre pour la comparaison de similarité (retire ponctuation, minuscules)."""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def title_similarity(a, b):
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


# Un doublon de titre proche n'a de sens qu'entre articles publiés à peu
# près en même temps (deux sources qui couvrent la même actu du jour) —
# comparer un nouvel article à un autre vieux de plusieurs mois pour la
# similarité de titre n'arrive jamais en pratique, et ça coûte cher inutilement
# une fois que l'historique grossit. On ne compare donc la similarité qu'aux
# TITLE_SIMILARITY_WINDOW articles les plus récents de la liste.
# Note : pendant la collecte, la liste n'est pas re-triée à chaque ajout (le
# tri final n'a lieu qu'à la fin) — les nouveaux articles ajoutés tôt dans la
# boucle ne sont donc comparés qu'à l'historique initial, pas aux nouveaux
# articles ajoutés ensuite par d'autres sources. Compromis volontaire pour
# la vitesse ; en pratique la dédup par lien exact reste la protection
# principale, la similarité de titre n'est qu'un filet de sécurité en plus.
TITLE_SIMILARITY_WINDOW = 200


def is_duplicate(item, existing_items, existing_links=None):
    """existing_links : set optionnel des liens déjà connus, pour un lookup
    O(1) au lieu de reparcourir toute la liste à chaque appel. Si non fourni,
    reconstruit la comparaison par lien à l'ancienne (plus lent mais correct)."""
    if existing_links is not None:
        if item["link"] in existing_links:
            return True
    else:
        if any(item["link"] == other["link"] for other in existing_items):
            return True

    for other in existing_items[:TITLE_SIMILARITY_WINDOW]:
        if title_similarity(item["title"], other["title"]) >= SIMILARITY_THRESHOLD:
            return True
    return False


def normalize_date(entry):
    """Convertit la date d'un article en un vrai format ISO 8601 comparable,
    peu importe le format d'origine du flux (RFC 822, ISO, etc.) — un tri
    par texte brut sur des formats mélangés donne un ordre chronologique
    faux, donc on passe systématiquement par la structure de temps déjà
    parsée par feedparser plutôt que par la chaîne de texte brute."""
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        try:
            dt = datetime(*struct[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    # Filet de sécurité si aucune date structurée n'est disponible : on
    # utilise le texte brut tel quel (mieux que rien), sachant qu'il pourrait
    # se trier de façon imprécise par rapport aux autres sources.
    return entry.get("published", entry.get("updated", ""))


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
        date = normalize_date(entry)
        description = entry.get("summary", "")

        # Filtre par mots-clés. Les sources "officielles" ont un filtre
        # strict sur GTA 6 uniquement (titre). Les sources "spécialistes"
        # (specialist_source) sont des sites dédiés à la série GTA en
        # général — elles couvrent aussi GTA Online, FiveM, GTA RP, etc.,
        # donc elles ont BESOIN du même filtre GTA 6 que les sources
        # normales pour ne pas remonter du contenu hors-sujet.
        # Exception : RockstarMag (no_filter_at_all) — choix explicite de
        # tout récupérer sans filtre, quitte à inclure du contenu GTA
        # Online/RP en plus du GTA 6, plutôt que de risquer de rater un
        # article. Réservé à cette seule source, pas aux autres
        # spécialistes.
        if feed.get("no_filter_at_all"):
            pass
        elif feed.get("official"):
            if not matches_keywords(title, OFFICIAL_KEYWORDS):
                continue
        elif not matches_keywords(title + " " + description, KEYWORDS):
            continue

        # Décodage du vrai lien pour les flux Google News
        real_link = decode_google_news_link(link) if "news.google.com" in feed["url"] else link

        # Les flux "officiels" sont en réalité des recherches Google News sur
        # site:rockstargames.com — Google peut aussi indexer des articles
        # tiers qui MENTIONNENT ce domaine sans être eux-mêmes publiés par
        # Rockstar (ex: un article IGN qui cite une page du site officiel).
        # On vérifie donc le vrai domaine du lien décodé avant de garder le
        # statut officiel, plutôt que de se fier uniquement au flux d'origine.
        is_official = feed.get("official", False)
        if is_official:
            try:
                real_domain = urlparse(real_link).netloc.lower()
            except Exception:
                real_domain = ""
            official_domains = ("rockstargames.com", "take2games.com")
            if not any(d in real_domain for d in official_domains):
                is_official = False

        # Miniature trouvée directement dans le flux RSS, si présente — pas
        # besoin d'aller la chercher sur la page dans ce cas.
        image = None
        if "media_content" in entry and entry.media_content:
            image = entry.media_content[0].get("url")
        elif "media_thumbnail" in entry and entry.media_thumbnail:
            image = entry.media_thumbnail[0].get("url")

        items.append({
            "title": title,
            "link": real_link,
            "date": date,
            "source": feed["name"],
            "official": is_official,
            "rockstarmag": feed.get("rockstarmag", False),
            "specialist": feed.get("specialist_source", False),
            "lang": feed.get("lang"),
            "image": image,
            "description": re.sub("<[^<]+?>", "", description)[:500],
        })

    # Phase 2 : récupère en parallèle (jusqu'à 5 à la fois) les miniatures
    # des articles qui n'en ont pas déjà une venant du flux RSS lui-même.
    # Avant cette optimisation, chaque appel était bloquant et en série —
    # avec un timeout de 8s chacun, quelques sites lents suffisaient à
    # ajouter plusieurs minutes au temps total d'exécution.
    items_needing_image = [item for item in items if not item["image"] and item["link"]]
    if items_needing_image:
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_item = {executor.submit(fetch_og_image, item["link"]): item for item in items_needing_image}
            for future in as_completed(future_to_item):
                item = future_to_item[future]
                try:
                    item["image"] = future.result()
                except Exception as e:
                    print(f"  [og:image] erreur inattendue pour {item['link'][:60]}... : {e}")

    print(f"  {len(items)} article(s) pertinent(s)")
    return items


MAX_HISTORY_SIZE = 2000  # au-delà, on retire les plus vieux articles pour garder le script rapide

# URL du webhook Discord, configurée comme secret GitHub (voir README) —
# jamais écrite en clair dans ce fichier. Si absente, les notifications
# sont simplement désactivées, tout le reste du script continue de marcher.
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


def send_discord_notification(new_items):
    """Envoie un message Discord pour chaque nouvel article, en priorisant
    l'affichage des sources officielles en premier. N'échoue jamais le
    script principal si Discord est indisponible ou mal configuré."""
    if not DISCORD_WEBHOOK_URL:
        return
    if not new_items:
        return

    # Les plus récentes et officielles en premier, mais AUCUNE n'est
    # coupée — une notification est envoyée pour chaque nouvel article.
    to_send = sorted(new_items, key=lambda x: (x.get("official", False), x.get("specialist", False), x.get("date", "")), reverse=True)
    print(f"  [discord] {len(to_send)} nouvel(le)(s) article(s) à notifier...")

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

        send_discord_with_retry(embed, item["title"])
        time.sleep(1)  # évite le rate-limit Discord (webhooks limités à quelques req/s)


def send_discord_with_retry(embed, title_for_log, max_attempts=3):
    """Envoie un embed Discord avec nouvelle tentative en cas d'erreur
    temporaire (429 rate-limit ou 5xx serveur). Un 429 renvoie généralement
    un délai précis à respecter (Retry-After) — on l'utilise si présent,
    sinon un backoff exponentiel simple (2s, 4s, 8s...). Les erreurs
    définitives (ex: 400 webhook malformé, 404 webhook supprimé) ne sont
    jamais retentées, ça ne changerait rien."""
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
            if resp.status_code in (200, 204):
                return
            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 2 ** attempt) if resp.text else 2 ** attempt
                print(f"  [discord] rate-limit (tentative {attempt}/{max_attempts}), attente {retry_after}s...")
                time.sleep(retry_after)
                continue
            if 500 <= resp.status_code < 600:
                wait = 2 ** attempt
                print(f"  [discord] erreur serveur {resp.status_code} (tentative {attempt}/{max_attempts}), attente {wait}s...")
                time.sleep(wait)
                continue
            # Erreur définitive (4xx hors 429) : inutile de retenter.
            print(f"  [discord] échec envoi ({resp.status_code}), non temporaire : {title_for_log[:50]}")
            return
        except Exception as e:
            print(f"  [discord] erreur réseau (tentative {attempt}/{max_attempts}) : {e}")
            if attempt < max_attempts:
                time.sleep(2 ** attempt)

    print(f"  [discord] abandon après {max_attempts} tentatives : {title_for_log[:50]}")


# Note : une tentative de notification via ntfy.sh a été faite puis
# abandonnée — le serveur confirmait l'envoi (200 OK) sans jamais relayer
# les messages, probablement à cause d'un fail2ban/rate-limit silencieux
# sur les IP partagées des runners GitHub Actions. Détails dans README.md.


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


def recheck_official_status(items):
    """Réapplique la vérification de domaine officiel aux articles déjà
    stockés — sans elle, un article marqué official=True lors d'une
    exécution précédente (avant l'ajout de cette règle, ou via une source
    qui n'est plus site:rockstargames.com/take2games.com) resterait
    mal classé indéfiniment, puisque load_existing_items() les conserve
    tels quels sans jamais les repasser dans le pipeline de fetch_feed()."""
    official_domains = ("rockstargames.com", "take2games.com")
    corrected = 0
    for item in items:
        if item.get("official"):
            try:
                real_domain = urlparse(item["link"]).netloc.lower()
            except Exception:
                real_domain = ""
            if not any(d in real_domain for d in official_domains):
                item["official"] = False
                corrected += 1
    if corrected:
        print(f"Correction rétroactive : {corrected} article(s) reclassé(s) de officiel à non-officiel (domaine réel ne correspondant pas)")
    return items


def main():
    existing_items = load_existing_items()
    is_first_run = len(existing_items) == 0
    print(f"Historique chargé : {len(existing_items)} article(s) déjà connus" + (" (premier lancement)" if is_first_run else ""))
    existing_items = recheck_official_status(existing_items)

    all_items = list(existing_items)  # on part de l'historique, pas de zéro
    existing_links = {item["link"] for item in all_items}  # lookup O(1) par lien
    newly_added = []

    for feed in FEEDS:
        items = fetch_feed(feed)
        for item in items:
            if not is_duplicate(item, all_items, existing_links):
                all_items.append(item)
                existing_links.add(item["link"])
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
        # Liste des sources incluse ici pour que le tracker HTML puisse s'en
        # servir (ex: peupler son sélecteur de source) sans avoir à
        # maintenir sa propre copie séparée de FEEDS — les deux listes
        # pouvaient diverger si une source était ajoutée d'un côté sans
        # penser à l'autre.
        "sources": [{"id": f["id"], "name": f["name"], "official": f.get("official", False), "specialist": f.get("specialist_source", False)} for f in FEEDS],
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
    elif newly_added:
        print(f"Premier lancement : {len(newly_added)} article(s) initiaux, pas de notification envoyée.")


if __name__ == "__main__":
    main()
