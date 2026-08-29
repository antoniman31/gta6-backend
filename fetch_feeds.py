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
import threading
import time
import os
from datetime import datetime, timezone
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

import feed_store

try:
    from googlenewsdecoder import gnewsdecoder
    HAS_DECODER = True
except ImportError:
    HAS_DECODER = False

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# ---------------------------------------------------------------------------
# Parallélisme de la récupération.
#
# Les 35 sources étaient interrogées à la suite, avec une pause d'une seconde
# entre chacune : 35 s de sommeil pur, plus ~2 min 30 d'attente réseau
# séquentielle, pour une étape qui durait un peu plus de 3 minutes. Or ces
# sources sont indépendantes — rien n'oblige à attendre la réponse de l'une
# avant d'appeler l'autre.
#
# La règle de politesse, elle, ne concerne qu'un même serveur. On répartit
# donc les sources en files : au plus PER_HOST_LIMIT files par domaine, et à
# l'intérieur d'une file les sources s'enchaînent avec HOST_PAUSE entre
# elles. Deux sources d'un même domaine ne partent donc jamais à plus de
# PER_HOST_LIMIT en même temps, quel que soit le nombre de fils.
#
# Ce découpage évite aussi la famine : avec un simple sémaphore par domaine,
# les 8 fils pouvaient tous se retrouver bloqués sur rss.app (10 flux)
# pendant que les 19 autres domaines attendaient leur tour.
FETCH_WORKERS = 8       # sources traitées de front, tous domaines confondus
PER_HOST_LIMIT = 3      # files simultanées pour un même domaine
HOST_PAUSE = 1.0        # pause entre deux requêtes d'une même file

# Décodages Google News simultanés, tous flux confondus. Le plafond est
# global (et non par flux) : sans lui, 3 flux Google News traités en même
# temps auraient multiplié d'autant la charge envoyée à Google.
DECODE_WORKERS = 4

# Miniatures récupérées de front (lot 3 : 5 -> 8). Ces requêtes visent
# chacune un site différent, il n'y a pas de politesse par domaine à tenir.
IMAGE_WORKERS = 8

_DECODE_SEMA = threading.Semaphore(DECODE_WORKERS)

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
        # Le plafond est global : plusieurs flux Google News peuvent être
        # traités en parallèle, mais jamais plus de DECODE_WORKERS
        # décodages en vol au même instant.
        with _DECODE_SEMA:
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


# À partir de combien de sources distinctes une actualité est considérée
# comme majeure. Sur ce sujet, un article isolé est en général une reprise
# ou de la supputation ; quand quatre rédactions publient la même chose
# dans la foulée, c'est un trailer, une date ou une annonce officielle.
HOT_SOURCE_THRESHOLD = 4

# La taille du fichier allégé vit dans feed_store : l'outil de fusion doit
# le régénérer avec exactement la même règle après un conflit de push.


def find_duplicate(item, existing_items, links_index=None):
    """Renvoie l'article déjà connu dont celui-ci est un doublon, ou None.

    Renvoie l'article plutôt qu'un simple booléen : quand plusieurs
    rédactions couvrent la même actualité, le robot jetait purement les
    doublons et perdait au passage une information précieuse — le NOMBRE de
    sources qui en parlent, qui est le meilleur indicateur qu'il se passe
    quelque chose d'important.

    links_index : correspondance lien -> article, pour un accès direct au
    lieu de reparcourir toute la liste. Si absente, comparaison à
    l'ancienne (plus lente mais correcte).
    """
    if links_index is not None:
        connu = links_index.get(item["link"])
        if connu is not None:
            return connu
    else:
        for other in existing_items:
            if item["link"] == other["link"]:
                return other

    for other in existing_items[:TITLE_SIMILARITY_WINDOW]:
        if title_similarity(item["title"], other["title"]) >= SIMILARITY_THRESHOLD:
            return other
    return None


def record_coverage(existing, item):
    """Note qu'une source de plus couvre la même actualité.

    Alimente le champ extraSources, que le tracker affiche déjà sous la
    carte (« + 3 autres sources : … ») dans son mode de secours — le
    backend produit désormais la même chose.
    """
    if existing.get("source") == item.get("source"):
        return False
    autres = existing.setdefault("extraSources", [])
    if any(a.get("source") == item.get("source") for a in autres):
        return False
    autres.append({"source": item.get("source"), "link": item.get("link")})
    return True


def is_hot(item):
    """Une actualité couverte par au moins HOT_SOURCE_THRESHOLD sources."""
    return 1 + len(item.get("extraSources") or []) >= HOT_SOURCE_THRESHOLD


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
    # parse le texte brut (RFC 822 le plus souvent) pour le convertir en ISO
    # malgré tout. Stocker la chaîne brute telle quelle, comme le faisait la
    # version précédente, produisait un historique où ~20 % des articles
    # avaient une date d'un autre format — et un tri par texte les remontait
    # tous en tête du fichier (en ASCII, "W" de "Wed, ..." > "2" de "2026").
    raw = entry.get("published", entry.get("updated", ""))
    if not raw:
        return ""
    parsed = feed_store.parse_date_key(raw)
    return raw if parsed == feed_store.DATE_FLOOR else parsed.isoformat()


def passe_le_filtre(feed, title, description):
    """Décide si une entrée du flux est retenue.

    Sorti de la boucle principale pour que le pré-décodage des liens Google
    News puisse s'appliquer aux seules entrées réellement retenues. Les
    règles sont identiques à ce qu'elles étaient à l'intérieur de la boucle.

    Les sources "officielles" ont un filtre strict sur GTA 6 uniquement
    (titre). Les sources "spécialistes" (specialist_source) sont des sites
    dédiés à la série GTA en général — elles couvrent aussi GTA Online,
    FiveM, GTA RP, etc., donc elles ont BESOIN du même filtre GTA 6 que les
    sources normales pour ne pas remonter du contenu hors-sujet.
    Exception : RockstarMag (no_filter_at_all) — choix explicite de tout
    récupérer sans filtre, quitte à inclure du contenu GTA Online/RP en plus
    du GTA 6, plutôt que de risquer de rater un article. Réservé à cette
    seule source, pas aux autres spécialistes.
    """
    if feed.get("no_filter_at_all"):
        return True
    if feed.get("official"):
        return matches_keywords(title, OFFICIAL_KEYWORDS)
    return matches_keywords(title + " " + description, KEYWORDS)


def predecode_links(liens, decoded_cache=None, journal=None):
    """Résout d'un coup les liens Google News encore inconnus du cache.

    gnewsdecoder impose une pause d'une seconde par lien. Résolus à la
    suite, une poignée d'articles neufs suffisait à ajouter une dizaine de
    secondes par flux Google News. Ils sont donc résolus à plusieurs — le
    plafond réel reste DECODE_WORKERS pour l'ensemble du processus, tenu par
    _DECODE_SEMA à l'intérieur de decode_google_news_link.

    Renvoie {lien brut -> lien résolu} pour les seuls liens traités ici ; le
    cache fourni est mis à jour au passage, ce qui évite à deux flux Google
    News de résoudre deux fois le même article.
    """
    cache = decoded_cache if decoded_cache is not None else {}
    a_faire = [lien for lien in dict.fromkeys(liens) if lien and lien not in cache]
    if not a_faire:
        return {}
    resolus = {}
    with ThreadPoolExecutor(max_workers=min(DECODE_WORKERS, len(a_faire))) as executor:
        for lien, vrai in zip(a_faire, executor.map(decode_google_news_link, a_faire)):
            resolus[lien] = vrai
            # On ne met en cache QUE les décodages réussis. En cas d'échec
            # la fonction renvoie le lien d'origine inchangé ; le mettre en
            # cache empêcherait un autre flux portant le même article de
            # retenter, alors qu'en séquentiel il retentait.
            # Un succès, lui, ne dépend que du lien d'entrée : deux flux
            # aboutissent au même résultat, l'écriture partagée est sûre.
            if vrai != lien:
                cache[lien] = vrai
    if journal is not None:
        journal.append(f"  {len(a_faire)} lien(s) Google News décodé(s) "
                       f"({DECODE_WORKERS} à la fois)")
    return resolus


def collect_feed_items(feed, decoded_cache=None, http_state=None):
    """Récupère et filtre les articles d'une source, SANS aller chercher les
    miniatures manquantes sur les pages.

    Le scraping des og:image a été sorti d'ici volontairement : il tournait
    avant toute déduplication, donc le robot allait chercher la miniature de
    ~30 articles par source × 35 sources à chaque exécution pour en jeter la
    quasi-totalité juste après (sur un run typique, une vingtaine d'articles
    sont réellement nouveaux sur près d'un millier examinés). La
    récupération a lieu maintenant dans main(), une seule fois, sur les
    seuls articles retenus.

    decoded_cache : correspondance {lien Google News brut -> vrai lien déjà
    résolu lors d'une exécution précédente}. Le décodage doit rester AVANT
    la déduplication (elle travaille sur le lien final), or gnewsdecoder
    impose une pause d'une seconde par lien — soit ~120 s par run pour les 4
    flux Google News, payées à chaque fois sur des articles déjà connus.

    Renvoie (items, info, journal). Le journal remplace les impressions
    directes : plusieurs sources étant traitées en parallèle, des `print`
    entrelaceraient les lignes de sources différentes et rendraient les logs
    illisibles. main() les réimprime dans l'ordre de FEEDS.
    """
    journal = [f"[{feed['name']}] récupération..."]

    # Requête conditionnelle : on redemande le flux en précisant la version
    # qu'on a déjà. Si rien n'a changé, le serveur répond 304 sans corps —
    # quelques octets au lieu de plusieurs dizaines de kilo-octets. Sans ça
    # le robot retélécharge intégralement 35 flux 48 fois par jour, même
    # inchangés. Au-delà de la vitesse, c'est une question d'hygiène : la
    # documentation de feedparser prévient qu'un client qui ignore ces
    # en-têtes peut se faire bannir par l'éditeur.
    precedent = (http_state or {}).get(feed["id"], {})
    try:
        parsed = feedparser.parse(
            feed["url"], agent=USER_AGENT,
            etag=precedent.get("etag") or None,
            modified=precedent.get("modified") or None,
        )
    except Exception as e:
        journal.append(f"  échec réseau : {e}")
        return [], {"raw_count": 0, "not_modified": False}, journal

    statut = getattr(parsed, "status", None)
    if statut == 304:
        journal.append("  inchangé depuis la dernière fois (304), rien à retélécharger")
        # On renvoie l'état précédent tel quel : un 304 ne fournit pas de
        # nouveaux validateurs, les réécrire à vide ferait retélécharger le
        # flux entier au prochain passage.
        return [], {"raw_count": 0, "not_modified": True,
                    "etag": precedent.get("etag"),
                    "modified": precedent.get("modified")}, journal

    if parsed.bozo and not parsed.entries:
        journal.append(f"  échec : {parsed.bozo_exception}")
        return [], {"raw_count": 0, "not_modified": False}, journal

    raw_count = len(parsed.entries)

    # Validateurs à renvoyer au prochain passage. feedparser les expose
    # directement quand le serveur les fournit ; beaucoup de flux n'en
    # donnent aucun, auquel cas on retélécharge comme avant.
    nouvel_etat = {
        "raw_count": raw_count,
        "not_modified": False,
        "etag": getattr(parsed, "etag", None),
        "modified": getattr(parsed, "modified", None),
    }

    # Premier passage : on ne garde que les entrées qui passent le filtre par
    # mots-clés, sans encore toucher au réseau.
    retenues = [entry for entry in parsed.entries[:30]
                if passe_le_filtre(feed, entry.get("title", ""), entry.get("summary", ""))]

    # Deuxième passage : les liens Google News encore inconnus sont résolus
    # à plusieurs, une bonne fois, au lieu d'une seconde chacun à la suite.
    est_google = "news.google.com" in feed["url"]
    resolus = {}
    if est_google:
        resolus = predecode_links([entry.get("link", "") for entry in retenues],
                                  decoded_cache, journal)

    items = []
    for entry in retenues:
        title = entry.get("title", "")
        link = entry.get("link", "")
        date = normalize_date(entry)
        description = entry.get("summary", "")

        # Décodage du vrai lien pour les flux Google News, en réutilisant
        # le résultat des exécutions précédentes quand on l'a déjà.
        # L'identifiant d'un article dans un flux Google News est stable
        # d'une exécution à l'autre, donc le cache touche presque à chaque
        # fois ; s'il rate, on décode comme avant (dégradation propre).
        source_link = None
        if est_google:
            cached = (decoded_cache or {}).get(link)
            real_link = cached or resolus.get(link) or decode_google_news_link(link)
            if real_link != link:
                source_link = link
        else:
            real_link = link

        # Nettoyage des paramètres de pistage : deux liens vers le même
        # article ne doivent pas compter pour deux.
        real_link = feed_store.canonical_link(real_link)

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
            # Lien Google News d'origine, conservé uniquement pour éviter de
            # repayer le décodage au prochain run. Absent pour les sources
            # qui ne passent pas par Google News.
            "source_link": source_link,
            "date": date,
            "source": feed["name"],
            "official": is_official,
            "rockstarmag": feed.get("rockstarmag", False),
            "specialist": feed.get("specialist_source", False),
            "lang": feed.get("lang"),
            "image": image,
            "description": re.sub("<[^<]+?>", "", description)[:500],
        })

    journal.append(f"  {raw_count} entrée(s) dans le flux, {len(items)} pertinente(s) après filtre")
    return items, nouvel_etat, journal


def chaines_par_hote(feeds, par_hote=PER_HOST_LIMIT):
    """Répartit les sources en files d'attente, au plus `par_hote` par domaine.

    Chaque file est traitée séquentiellement par un fil ; les files, elles,
    tournent en parallèle. Deux sources d'un même domaine ne partent donc
    jamais à plus de `par_hote` en même temps — la politesse due au serveur
    est tenue par construction, sans sémaphore et sans risque de famine.

    Découpage déterministe : à liste de sources identique, mêmes files.
    """
    par_domaine = {}
    for feed in feeds:
        par_domaine.setdefault(urlparse(feed["url"]).netloc.lower(), []).append(feed)
    chaines = []
    for liste in par_domaine.values():
        files = [[] for _ in range(min(par_hote, len(liste)))]
        for rang, feed in enumerate(liste):
            files[rang % len(files)].append(feed)
        chaines.extend(files)
    return chaines


def fetch_all_feeds(feeds, decoded_cache=None, http_state=None, collecte=None):
    """Interroge toutes les sources en parallèle. Renvoie {id: (items, info, journal)}.

    Seul le TÉLÉCHARGEMENT est parallélisé. La fusion des résultats reste
    faite par main() dans l'ordre de FEEDS : c'est cet ordre qui décide
    quelle source « possède » un article et dans quel ordre les sources
    supplémentaires s'empilent derrière lui. Le fichier produit est donc
    identique à ce qu'il était en séquentiel.

    `collecte` permet aux tests d'injecter une fausse récupération.
    """
    une = collecte or collect_feed_items
    chaines = chaines_par_hote(feeds)

    def traiter(chaine):
        sortie = {}
        for rang, feed in enumerate(chaine):
            if rang:
                # Politesse : jamais deux requêtes consécutives vers le même
                # domaine sans marquer une pause.
                time.sleep(HOST_PAUSE)
            try:
                sortie[feed["id"]] = une(feed, decoded_cache, http_state)
            except Exception as e:
                # Une source qui casse de façon imprévue ne doit pas emporter
                # les 34 autres avec elle. En séquentiel, une exception non
                # rattrapée ici arrêtait tout le passage.
                sortie[feed["id"]] = ([], {"raw_count": 0, "not_modified": False},
                                      [f"[{feed['name']}] récupération...",
                                       f"  échec inattendu : {e}"])
        return sortie

    resultats = {}
    with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(chaines) or 1)) as executor:
        for bloc in executor.map(traiter, chaines):
            resultats.update(bloc)
    return resultats


def merge_results(feeds, resultats, all_items, links_index, newly_added,
                  decoded_cache=None, afficher=True):
    """Fusionne les résultats des sources dans l'historique.

    ORDRE CRITIQUE : on parcourt `feeds`, jamais l'ordre d'arrivée des
    réponses réseau. C'est cet ordre qui détermine quelle source est retenue
    comme propriétaire d'un article et dans quel ordre les sources
    supplémentaires (badge « N SOURCES ») s'empilent derrière lui. Parcourir
    dans l'ordre d'arrivée rendrait le fichier produit dépendant de la
    vitesse des serveurs, donc différent d'un passage à l'autre.

    Sorti de main() pour être testable : c'est ici que se joue l'équivalence
    entre l'ancienne récupération séquentielle et la nouvelle, parallèle.

    Modifie `all_items`, `links_index` et `newly_added` sur place.
    Renvoie (feed_infos, new_counts, inchanges).
    """
    feed_infos = {}
    new_counts = {}
    inchanges = 0
    for feed in feeds:
        items, info, journal = resultats[feed["id"]]
        if afficher:
            for ligne in journal:
                print(ligne)
        feed_infos[feed["id"]] = info
        if info.get("not_modified"):
            inchanges += 1
        new_counts[feed["id"]] = 0
        for item in items:
            deja = find_duplicate(item, all_items, links_index)
            if deja is None:
                all_items.append(item)
                links_index[item["link"]] = item
                newly_added.append(item)
                new_counts[feed["id"]] += 1
                if decoded_cache is not None and item.get("source_link"):
                    decoded_cache[item["source_link"]] = item["link"]
            else:
                # Doublon : on ne le garde pas, mais on retient que cette
                # source couvre aussi le sujet.
                record_coverage(deja, item)
    return feed_infos, new_counts, inchanges


def fetch_missing_images(items):
    """Récupère en parallèle (jusqu'à IMAGE_WORKERS à la fois) les miniatures manquantes.

    Appelée une seule fois par exécution, sur les seuls articles retenus
    après déduplication. En série avec un timeout de 8 s chacun, quelques
    sites lents suffisaient à ajouter plusieurs minutes au temps total.
    """
    needing = [item for item in items if not item.get("image") and item.get("link")]
    if not needing:
        return 0

    print(f"\nMiniatures manquantes à récupérer : {len(needing)} article(s)...")
    found = 0
    with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as executor:
        future_to_item = {executor.submit(fetch_og_image, item["link"]): item for item in needing}
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                item["image"] = future.result()
                if item["image"]:
                    found += 1
            except Exception as e:
                print(f"  [og:image] erreur inattendue pour {item['link'][:60]}... : {e}")
    print(f"  {found}/{len(needing)} miniature(s) trouvée(s)")
    return found


# Le plafond d'historique, le tri et l'interprétation des dates vivent
# désormais dans feed_store.py : merge_feed.py doit appliquer exactement les
# mêmes règles, sinon une fusion après conflit de push corromprait l'ordre
# ou retirerait les mauvais articles.
MAX_HISTORY_SIZE = feed_store.MAX_HISTORY_SIZE

# Au-delà de ce délai sans le moindre article, une source est signalée comme
# "tarie". Ce n'est pas forcément une panne — Rockstar et Take-Two
# communiquent peu, il est normal qu'ils restent muets des semaines — mais
# sans ce signal un flux réellement mort passerait inaperçu indéfiniment.
SILENT_SOURCE_DAYS = 30

# Fichier où sont déposés les nouveaux articles de cette exécution, à
# destination de discord_notify.py. Le workflow le place dans $RUNNER_TEMP,
# donc hors du dépôt : aucun risque qu'il finisse committé. Absent en local,
# ce qui désactive simplement la notification.
NEW_ITEMS_FILE = os.environ.get("NEW_ITEMS_FILE", "")

# Clé publique VAPID, publiée dans feed.json pour que l'app puisse créer un
# abonnement aux notifications push. Elle est publique par nature — c'est
# la clé PRIVÉE, gardée en secret GitHub, qui autorise l'envoi. Absente,
# l'app masque simplement l'option.
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")


def write_new_items_file(new_items):
    """Dépose les nouveaux articles pour l'étape de notification.

    La notification Discord n'est plus envoyée d'ici : elle a lieu APRÈS que
    le workflow a réellement publié docs/feed.json. Sinon une panne de push
    annonçait des articles jamais publiés, puis l'exécution suivante les
    réannonçait (arrivé les 26/08 et 28/08)."""
    if not NEW_ITEMS_FILE:
        return
    try:
        with open(NEW_ITEMS_FILE, "w", encoding="utf-8") as f:
            json.dump(new_items, f, ensure_ascii=False)
        print(f"  {len(new_items)} nouvel(le)(s) article(s) déposé(s) pour notification -> {NEW_ITEMS_FILE}")
    except OSError as e:
        # Ne doit jamais faire échouer la collecte : au pire il n'y a pas de
        # notification, les articles eux-mêmes sont bien publiés.
        print(f"  [notif] impossible d'écrire {NEW_ITEMS_FILE} : {e}")


def recheck_official_status(items):
    """Réapplique la vérification de domaine officiel aux articles déjà
    stockés — sans elle, un article marqué official=True lors d'une
    exécution précédente (avant l'ajout de cette règle, ou via une source
    qui n'est plus site:rockstargames.com/take2games.com) resterait
    mal classé indéfiniment : l'historique est rechargé tel quel, sans
    jamais repasser dans le pipeline de collecte."""
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


def build_sources_health(all_items, feed_infos, new_counts):
    """Dresse l'état de chaque source, pour repérer un flux mort.

    Deux signaux distincts :
      - "muette"  : le flux n'a renvoyé AUCUNE entrée brute cette fois. C'est
                    le signal net d'une URL cassée, d'un domaine expiré ou
                    d'un blocage — indépendant du filtre par mots-clés.
      - "tarie"   : le flux répond, mais aucun de ses articles n'est présent
                    dans l'historique depuis plus de SILENT_SOURCE_DAYS.
                    Informatif : ça peut être parfaitement normal.
    """
    now = datetime.now(timezone.utc)
    latest = {}

    def noter(source, when):
        if not source:
            return
        if source not in latest or when > latest[source]:
            latest[source] = when

    for item in all_items:
        when = feed_store.parse_date_key(item.get("date"))
        noter(item.get("source"), when)
        # Une source dont l'article a été fusionné comme doublon n'apparaît
        # plus comme source principale, seulement dans extraSources. Sans
        # cette boucle, elle serait signalée « tarie » alors qu'elle publie
        # normalement — elle se contente d'arriver après les autres.
        for autre in (item.get("extraSources") or []):
            noter(autre.get("source"), when)

    health = []
    for feed in FEEDS:
        name = feed["name"]
        last = latest.get(name)
        days = None
        if last and last != feed_store.DATE_FLOOR:
            days = (now - last).days

        info = feed_infos.get(feed["id"], {})
        raw = info.get("raw_count", 0)
        # Un flux qui répond 304 ne renvoie aucune entrée, mais il est
        # parfaitement vivant : le confondre avec un flux mort produirait
        # une fausse alerte à chaque passage.
        if raw == 0 and not info.get("not_modified"):
            status = "muette"
        elif days is None or days > SILENT_SOURCE_DAYS:
            status = "tarie"
        else:
            status = "ok"

        health.append({
            "id": feed["id"],
            "name": name,
            "entries_fetched": raw,
            "not_modified": bool(info.get("not_modified")),
            "new_this_run": new_counts.get(feed["id"], 0),
            "last_article": last.isoformat() if last and last != feed_store.DATE_FLOOR else None,
            "days_since_last_article": days,
            "status": status,
        })

    muettes = [h for h in health if h["status"] == "muette"]
    taries = [h for h in health if h["status"] == "tarie"]
    if muettes:
        print(f"\n⚠ {len(muettes)} source(s) MUETTE(S) — flux sans aucune entrée, probablement cassé :")
        for h in muettes:
            print(f"    - {h['name']}")
    if taries:
        print(f"\n· {len(taries)} source(s) sans article depuis plus de {SILENT_SOURCE_DAYS} jours :")
        for h in taries:
            age = f"{h['days_since_last_article']} j" if h["days_since_last_article"] is not None else "jamais"
            print(f"    - {h['name']} ({age})")
    if not muettes and not taries:
        print(f"\nToutes les sources ont répondu et ont publié dans les {SILENT_SOURCE_DAYS} derniers jours.")

    return health


def main():
    stored = feed_store.load_feed()
    existing_items = stored.get("items", [])
    # Validateurs HTTP du passage précédent, par source.
    http_state = stored.get("feed_http_state", {}) or {}
    is_first_run = len(existing_items) == 0
    print(f"Historique chargé : {len(existing_items)} article(s) déjà connus" + (" (premier lancement)" if is_first_run else ""))
    existing_items = recheck_official_status(existing_items)

    # Repasse rétroactive des dates : l'historique est rechargé tel quel et
    # ne repasse jamais dans le pipeline de collecte, donc les articles
    # engrangés avant l'introduction de normalize_date() gardent leur date
    # au format brut du flux (RFC 822). Sans cette correction, un tri par
    # date les remonte tous en tête, ce qui fausse aussi la fenêtre de
    # déduplication par similarité de titre (elle ne compare alors plus aux
    # articles réellement récents). Idempotente.
    fixed_dates = feed_store.normalize_stored_dates(existing_items)
    if fixed_dates:
        print(f"Correction rétroactive : {fixed_dates} date(s) convertie(s) au format ISO 8601")

    # Même logique pour les liens : ceux engrangés avant la règle de
    # nettoyage gardent leurs paramètres de pistage et continueraient de
    # faire doublon avec leurs équivalents propres. Idempotente.
    existing_items, liens_nettoyes, doublons = feed_store.canonicalize_stored_links(existing_items)
    if liens_nettoyes or doublons:
        print(f"Correction rétroactive : {liens_nettoyes} lien(s) nettoyé(s) de leurs paramètres "
              f"de pistage, {doublons} doublon(s) ainsi révélé(s) et retiré(s)")

    all_items = list(existing_items)  # on part de l'historique, pas de zéro
    links_index = {item["link"]: item for item in all_items}  # accès O(1) par lien
    newly_added = []

    # Liens Google News déjà résolus lors des exécutions précédentes : évite
    # de repayer une seconde de décodage par article déjà connu.
    decoded_cache = {item["source_link"]: item["link"]
                     for item in existing_items if item.get("source_link")}
    if decoded_cache:
        print(f"Cache de décodage Google News : {len(decoded_cache)} lien(s) déjà résolu(s)")

    # Téléchargement des 35 sources en parallèle. Voir fetch_all_feeds :
    # seul le réseau est parallélisé, la fusion qui suit reste séquentielle.
    depart = time.time()
    resultats = fetch_all_feeds(FEEDS, decoded_cache, http_state)
    print(f"\n{len(FEEDS)} source(s) interrogée(s) en {time.time() - depart:.0f} s "
          f"({FETCH_WORKERS} de front, {PER_HOST_LIMIT} max par domaine)\n")

    feed_infos, new_counts, inchanges = merge_results(
        FEEDS, resultats, all_items, links_index, newly_added, decoded_cache)

    chaudes = [i for i in all_items if is_hot(i)]
    chaudes_neuves = [i for i in newly_added if is_hot(i)]
    if chaudes_neuves:
        print(f"\n🔥 {len(chaudes_neuves)} actualité(s) majeure(s) ce passage "
              f"({HOT_SOURCE_THRESHOLD}+ sources sur le même sujet) :")
        for i in chaudes_neuves[:5]:
            n = 1 + len(i.get("extraSources") or [])
            print(f"    - [{n} sources] {i['title'][:70]}")

    if inchanges:
        print(f"\n{inchanges}/{len(FEEDS)} source(s) inchangée(s) depuis le dernier passage "
              "(réponse 304, rien retéléchargé)")

    # Les miniatures ne sont cherchées qu'ici, sur les seuls articles
    # réellement retenus — et non plus sur tout ce que chaque flux renvoie.
    fetch_missing_images(newly_added)

    # Tri sur la date RÉELLE (datetime), jamais sur la chaîne : des formats
    # mélangés donnent un ordre faux en comparaison de texte.
    all_items = feed_store.sort_items(all_items)

    # Plafonne la taille de l'historique : au-delà de MAX_HISTORY_SIZE, on
    # retire les articles les plus anciens plutôt que de laisser le fichier
    # (et le temps de dédup) grossir indéfiniment.
    all_items, dropped = feed_store.cap_items(all_items)
    if dropped:
        print(f"Historique plafonné : {len(all_items) + dropped} -> {MAX_HISTORY_SIZE} (les {dropped} plus anciens sont retirés)")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_articles": len(all_items),
        "new_this_run": len(newly_added),
        # Nombre d'actualités couvertes par au moins HOT_SOURCE_THRESHOLD
        # sources : de quoi repérer un trailer ou une annonce sans lire.
        "hot_count": len(chaudes),
        "hot_threshold": HOT_SOURCE_THRESHOLD,
        "sources_count": len(FEEDS),
        # Liste des sources incluse ici pour que le tracker HTML puisse s'en
        # servir (ex: peupler son sélecteur de source) sans avoir à
        # maintenir sa propre copie séparée de FEEDS — les deux listes
        # pouvaient diverger si une source était ajoutée d'un côté sans
        # penser à l'autre.
        "sources": [{"id": f["id"], "name": f["name"], "official": f.get("official", False), "specialist": f.get("specialist_source", False)} for f in FEEDS],
        # État de chaque source : permet de repérer un flux mort sans
        # éplucher les logs du run. Exposé dans feed.json pour pouvoir être
        # affiché plus tard par l'app sans retoucher au backend.
        "sources_health": build_sources_health(all_items, feed_infos, new_counts),
        # Validateurs HTTP par source, pour la requête conditionnelle du
        # prochain passage. Conservés dans feed.json faute d'autre stockage
        # persistant : quelques centaines d'octets, négligeables.
        "feed_http_state": {
            fid: {"etag": inf.get("etag"), "modified": inf.get("modified")}
            for fid, inf in feed_infos.items()
            if inf.get("etag") or inf.get("modified")
        },
        # Permet à l'app de proposer les notifications push sans que la clé
        # soit codée en dur dans index.html : elle suit la configuration du
        # dépôt, et disparaît si le secret est retiré.
        "vapid_public_key": VAPID_PUBLIC_KEY,
        "items": all_items,
    }

    nb_allege = feed_store.write_feed_pair(output)
    print(f"Fichier allégé écrit : {nb_allege} article(s) dans docs/feed-recent.json")

    print(f"\nTerminé — {len(newly_added)} nouveau(x), {len(all_items)} au total dans docs/feed.json")

    # Pas de notification au tout premier lancement : l'historique est vide,
    # donc "tout" serait considéré comme nouveau — ça enverrait des dizaines
    # de messages d'un coup au lieu de rester silencieux jusqu'à la vraie
    # actualité suivante.
    if not is_first_run:
        write_new_items_file(newly_added)
    elif newly_added:
        print(f"Premier lancement : {len(newly_added)} article(s) initiaux, pas de notification envoyée.")


if __name__ == "__main__":
    main()
