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
# les 8 fils pouvaient tous se retrouver bloqués sur news.google.com (20 flux
# à lui seul) pendant que les 29 autres domaines attendaient leur tour.
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
    # Chaîne YouTube officielle de Rockstar.
    #
    # C'est LA source primaire : un trailer sort ici, la presse en parle dix
    # à trente minutes plus tard. Sans elle, le robot apprend l'événement
    # par ceux qui le commentent.
    #
    # Deux réglages spécifiques, sans quoi elle serait inutile :
    #
    #   official_domains — les liens pointent vers youtube.com, pas vers
    #     rockstargames.com. Avec la liste par défaut, la vérification de
    #     domaine retirerait le statut « officiel » à chaque passage et les
    #     vidéos n'apparaîtraient jamais dans l'onglet Rockstar de l'app,
    #     qui filtre précisément sur ce statut.
    #
    #   official_keywords_extra — « trailer » s'ajoute aux mots-clés GTA 6. Une
    #     vidéo intitulée simplement « Trailer 3 » ne contient aucun de ces
    #     mots-clés et serait rejetée : précisément le jour qui compte.
    #     Contrepartie assumée : quelques bandes-annonces GTA Online
    #     passeront aussi. Rater LA vidéo coûte infiniment plus cher que
    #     d'en afficher deux de trop.
    #
    # Le filtre reste actif, contrairement à RockstarMag : cette chaîne
    # publie régulièrement du contenu GTA Online et Red Dead qui n'a rien à
    # faire dans l'onglet Rockstar.
    {"id": "rockstar-youtube", "name": "Rockstar Games (YouTube)",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCULwHhkI31JHAKe57LZdzcA",
     "official": True,
     # youtu.be par précaution : le flux Atom de YouTube donne des liens
     # complets youtube.com, mais un lien raccourci perdrait son statut.
     "official_domains": ["youtube.com", "youtu.be"],
     "official_keywords_extra": ["trailer"]},
    {"id": "gnews-fr", "name": "Google News (FR)", "url": "https://news.google.com/rss/search?q=%22GTA+6%22&hl=fr&gl=FR&ceid=FR:fr", "official": False, "lang": "fr"},
    {"id": "gnews-en", "name": "Google News (EN)", "url": "https://news.google.com/rss/search?q=%22GTA+6%22&hl=en&gl=US&ceid=US:en", "official": False},
    {"id": "pcgamer", "name": "PC Gamer", "url": "https://www.pcgamer.com/rss.xml", "official": False},
    {"id": "insider", "name": "Insider Gaming", "url": "https://insider-gaming.com/feed/", "official": False},
    {"id": "tomshw", "name": "Tom's Hardware", "url": "https://www.tomshardware.com/feeds/all", "official": False},
    {"id": "jvcom", "name": "Jeuxvideo.com", "url": "https://www.jeuxvideo.com/rss/rss.xml", "official": False, "lang": "fr"},
    {"id": "gamekult", "name": "Gamekult", "url": "https://www.gamekult.com/feed.xml", "official": False, "lang": "fr"},
    {"id": "ignfr", "name": "IGN France", "url": "https://fr.ign.com/feed.xml", "official": False, "lang": "fr"},
    # Les neuf sources qui suivent (RockstarMag, RockstarINTEL, IGN,
    # GameSpot, Polygon, Kotaku, GamesRadar+, Rock Paper Shotgun, Eurogamer)
    # passaient par rss.app. Le 29/08/2026 vers 13h00 UTC elles se sont tues
    # toutes les neuf d'un coup : d'abord des flux valides mais vides, puis
    # un franc HTTP 402 Payment Required — le quota du plan gratuit, pas une
    # panne. Rien ne serait revenu tout seul.
    #
    # Chaque flux natif retenu ici a été VÉRIFIÉ depuis un runner GitHub
    # (sonde_flux.py, 26 URL sondées) : il répond, il est frais, et on sait
    # combien de ses entrées passent le filtre GTA 6. Deux pièges relevés au
    # passage :
    #   - RockstarINTEL DOIT rester sans « www » : les variantes www.
    #     échouent au handshake TLS (TLSV1_ALERT_INTERNAL_ERROR) côté serveur.
    #   - GameSpot : /feeds/news/ (30 entrées) et non /feeds/game-news/ (15),
    #     qui donnait deux fois moins de GTA 6.
    # Polygon est le seul dont aucune entrée récente ne passait le filtre —
    # son flux est bien vivant, c'est leur couverture GTA 6 qui est rare.
    #
    # Bénéfice de structure : plus aucun point de défaillance unique à 18 %
    # des sources, et 30 domaines distincts au lieu de 20.
    {"id": "rockstarmag", "name": "RockstarMag", "url": "https://www.rockstarmag.fr/feed/", "official": False, "rockstarmag": True, "specialist_source": True, "no_filter_at_all": True, "lang": "fr"},
    # La chaîne YouTube du même média. rockstarmag=True la range dans son
    # onglet : le lien pointe vers youtube.com, donc le classement par
    # domaine ne peut pas la reconnaître — c'est le chemin « la source le
    # déclare » de statut_rockstarmag qui prend le relais.
    #
    # PAS de no_filter_at_all, contrairement au flux d'articles : la chaîne
    # couvre toute la production Rockstar (Red Dead, GTA Online, GTA 5), et
    # tout accepter sans filtre y noierait GTA 6. Le filtre porte sur le
    # titre ET la description, et le flux Atom de YouTube fournit les deux —
    # une vidéo au titre elliptique passe donc quand même si sa description
    # parle de GTA 6.
    {"id": "rockstarmag-youtube", "name": "RockstarMag (YouTube)",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCmU6lJbZKzpAU_S1h6Ec-dg",
     "official": False, "rockstarmag": True, "specialist_source": True, "lang": "fr"},
    {"id": "rockstarintel", "name": "RockstarINTEL", "url": "https://rockstarintel.com/feed/", "official": False, "specialist_source": True},
    {"id": "ign", "name": "IGN", "url": "https://feeds.ign.com/ign/games-all", "official": False},
    {"id": "gamespot", "name": "GameSpot", "url": "https://www.gamespot.com/feeds/news/", "official": False},
    {"id": "polygon", "name": "Polygon", "url": "https://www.polygon.com/rss/index.xml", "official": False},
    {"id": "kotaku", "name": "Kotaku", "url": "https://kotaku.com/rss", "official": False},
    {"id": "gamesradar", "name": "GamesRadar+", "url": "https://www.gamesradar.com/rss/", "official": False},
    # Flux Google News restreint au domaine plutôt que le flux maison :
    # celui de rss.app était un flux VG247 GÉNÉRALISTE. Il renvoyait
    # fidèlement 25 articles par passage — Nintendo, PlayStation, tout le
    # catalogue — dont zéro sur GTA 6, tous écartés par le filtre. Vérifié
    # le 29/08/2026 : 0 article VG247 dans l'historique, et aucun n'y était
    # non plus arrivé via les autres flux, donc la couverture manquait
    # réellement.
    {"id": "vg247", "name": "VG247", "url": "https://news.google.com/rss/search?q=site:vg247.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=en&gl=US&ceid=US:en", "official": False},
    {"id": "rps", "name": "Rock Paper Shotgun", "url": "https://www.rockpapershotgun.com/feed", "official": False},
    {"id": "eurogamer", "name": "Eurogamer", "url": "https://www.eurogamer.net/feed", "official": False},
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

    # ------------------------------------------------------------------
    # Sources ajoutées le 29/08/2026.
    #
    # Toutes passent par une recherche Google News restreinte au domaine
    # plutôt que par le flux RSS natif de chaque site. Ce n'est pas un
    # choix esthétique : les adresses de flux natives n'étaient pas
    # vérifiables au moment de l'ajout, alors que ce format-là est déjà
    # éprouvé par sept sources en production. Une recherche qui ne renvoie
    # rien (domaine erroné) fait basculer la source en « muette » et
    # déclenche l'alerte de source morte sous trois heures — l'erreur se
    # signale donc d'elle-même.
    #
    # Le garde-fou MAX_ARTICLE_AGE_DAYS est indispensable ici : ces
    # recherches classent par pertinence et remontent volontiers des
    # archives de plusieurs années.
    # ------------------------------------------------------------------
    {"id": "xboxygen", "name": "Xboxygen", "url": "https://news.google.com/rss/search?q=site:xboxygen.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=fr&gl=FR&ceid=FR:fr", "official": False, "lang": "fr"},
    {"id": "purexbox", "name": "Pure Xbox", "url": "https://news.google.com/rss/search?q=site:purexbox.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=en&gl=US&ceid=US:en", "official": False},
    {"id": "xboxwire", "name": "Xbox Wire", "url": "https://news.google.com/rss/search?q=site:news.xbox.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=en&gl=US&ceid=US:en", "official": False},
    {"id": "trueach", "name": "TrueAchievements", "url": "https://news.google.com/rss/search?q=site:trueachievements.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=en&gl=US&ceid=US:en", "official": False},
    {"id": "jdg", "name": "Journal du Geek", "url": "https://news.google.com/rss/search?q=site:journaldugeek.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=fr&gl=FR&ceid=FR:fr", "official": False, "lang": "fr"},
    {"id": "numerama", "name": "Numerama", "url": "https://news.google.com/rss/search?q=site:numerama.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=fr&gl=FR&ceid=FR:fr", "official": False, "lang": "fr"},
    {"id": "gamergen", "name": "Gamergen", "url": "https://news.google.com/rss/search?q=site:gamergen.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=fr&gl=FR&ceid=FR:fr", "official": False, "lang": "fr"},
    {"id": "frandroid", "name": "Frandroid", "url": "https://news.google.com/rss/search?q=site:frandroid.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=fr&gl=FR&ceid=FR:fr", "official": False, "lang": "fr"},
    {"id": "theverge", "name": "The Verge", "url": "https://news.google.com/rss/search?q=site:theverge.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=en&gl=US&ceid=US:en", "official": False},
    {"id": "engadget", "name": "Engadget", "url": "https://news.google.com/rss/search?q=site:engadget.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=en&gl=US&ceid=US:en", "official": False},
    {"id": "arstechnica", "name": "Ars Technica", "url": "https://news.google.com/rss/search?q=site:arstechnica.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=en&gl=US&ceid=US:en", "official": False},
    {"id": "pushsquare", "name": "Push Square", "url": "https://news.google.com/rss/search?q=site:pushsquare.com+(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)&hl=en&gl=US&ceid=US:en", "official": False},
    {"id": "schreier", "name": "Jason Schreier", "url": "https://news.google.com/rss/search?q=%22Jason+Schreier%22+(Rockstar+OR+%22GTA+6%22+OR+%22Take-Two%22)&hl=en&gl=US&ceid=US:en", "official": False},
]

# Liste enrichie fournie par l'utilisateur (139 mots-clés) — remplace
# l'ancienne liste courte de 19 termes, jamais synchronisée avec celle-ci
# jusqu'à présent malgré la duplication FEEDS/DEFAULT_FEEDS déjà documentée
# plus haut dans ce fichier.
KEYWORDS = [
    "gta 6", "gta vi", "gta6", "gtavi", "grand theft auto vi", "grand theft auto 6", "gta-6", "gta-vi", "grand-theft-auto-6", "gta_6", "gta_vi", "gtaonline6", "gta 6 news", "gta vi news", "gta6 news", "rockstar gta 6", "rockstar's gta 6", "rockstar gta vi", "rockstar's gta vi", "rockstar next game", "rockstar new game", "rockstar upcoming game", "next gta", "new gta", "future gta", "upcoming gta", "gta next", "grand theft auto next", "gta sixth game", "gta sequel", "vice city", "vicecity", "new vice city", "gta vice city 2026", "leonida", "leonida state", "leonida map", "leonida gta", "cyberleek", "cyber leek", "cyberleak", "cyber leak gta", "gta 6 cyberleek", "take-two", "take two", "take-two interactive", "taketwo", "take2", "rockstar games", "rockstar north", "rockstar san diego", "rockstargames", "rockstar studio", "rockstar dev", "gta 6 release date", "gta vi release date", "gta 6 launch date", "gta 6 launch", "gta 6 date", "gta 6 november", "gta 6 2026", "gta vi 2026", "gta 6 delay", "gta 6 delayed", "gta vi delay", "gta 6 postponed", "gta 6 trailer", "gta vi trailer", "gta 6 trailer 3", "gta 6 new trailer", "gta 6 teaser", "gta vi teaser", "gta 6 extended look", "gta 6 netflix", "gta 6 gameplay", "gta vi gameplay", "gta 6 gameplay leak", "gta 6 gameplay video", "gta 6 footage", "gta vi footage", "gta 6 clip", "gta 6 leak", "gta vi leak", "gta 6 leaks", "gta 6 leaked", "gta 6 leaked footage", "gta 6 leaked gameplay", "gta 6 leaked map", "gta 6 hack", "gta 6 breach", "gta 6 map", "gta vi map", "gta 6 map leak", "gta 6 characters", "gta vi characters", "lucia caminos", "jason duval", "gta 6 protagonist", "gta 6 pre-order", "gta vi pre-order", "gta 6 preorder", "gta 6 price", "gta 6 edition", "gta 6 collector", "gta 6 pc", "gta 6 ps5", "gta 6 xbox", "gta 6 console", "gta 6 xbox series", "gta 6 playstation 5", "gta 6 system requirements", "gta 6 online", "gta 6 multiplayer", "gta online 2", "gta 6 rating", "gta 6 esrb", "gta 6 age rating", "gta 6 budget", "gta 6 development", "gta 6 dev", "gta 6 news today", "gta 6 update", "gta 6 announcement", "gta 6 reveal", "gta 6 confirmed", "gta 6 rumor", "gta 6 rumors", "gta 6 speculation", "gta 6 preview event", "gta 6 media event", "gta 6 hands-on", "gta 6 preview", "gta 6 review", "gta 6 dlc", "gta 6 season pass", "gta 6 dmca", "gta 6 takedown", "gta 6 copyright", "gta 6 discord"
]
OFFICIAL_KEYWORDS = ["gta 6", "gta vi", "gta6", "gtavi", "grand theft auto vi", "grand theft auto 6"]

# Domaines dont un lien peut porter le statut « officiel ». Les flux dits
# officiels sont en réalité des recherches Google News sur un domaine : elles
# remontent aussi des articles TIERS qui mentionnent ce domaine, d'où cette
# vérification sur le lien réellement décodé.
#
# Une source peut déclarer sa propre liste via "official_domains" — c'est le
# cas de la chaîne YouTube de Rockstar, dont les liens pointent
# légitimement vers youtube.com et perdraient sinon leur statut officiel à
# chaque passage.
OFFICIAL_DOMAINS = ("rockstargames.com", "take2games.com")


def domaines_officiels(feed):
    return tuple(feed.get("official_domains") or OFFICIAL_DOMAINS)


def mots_cles_officiels(feed):
    """Mots-clés du filtre officiel, plus ceux que la source ajoute.

    La source déclare un SUPPLÉMENT, pas une liste complète : recopier les
    six mots-clés de base dans une entrée de FEEDS les ferait diverger au
    premier ajustement. Et FEEDS est défini avant OFFICIAL_KEYWORDS, donc
    une entrée ne peut de toute façon pas y faire référence.
    """
    return OFFICIAL_KEYWORDS + list(feed.get("official_keywords_extra") or [])


def lien_officiel(url, domaines):
    try:
        domaine = urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(d in domaine for d in domaines)


# Domaine de Rockstar Mag. Même logique que pour les domaines officiels : ce
# qui compte est QUI PUBLIE, pas qui a trouvé l'article.
ROCKSTARMAG_DOMAINS = ("rockstarmag.fr",)


def statut_officiel(url, feed=None):
    """Un article est officiel s'il est PUBLIÉ par Rockstar ou Take-Two.

    Deux chemins, parce qu'il y a deux façons de tomber dessus :

    - le lien est sur un domaine officiel, quelle que soit la source qui l'a
      remonté. Sans ce cas, un article du Newswire trouvé par Google News
      atterrissait dans « Non Rockstar » alors que son lien est
      irréprochable — et comme la déduplication garde le premier trouvé, le
      même article changeait d'onglet selon le flux qui gagnait la course ;
    - la source est déclarée officielle ET le lien confirme SES domaines.
      Ce second cas existe pour la chaîne YouTube de Rockstar, dont les
      liens pointent légitimement vers youtube.com : ce domaine ne peut pas
      entrer dans la liste globale, sinon n'importe quelle vidéo deviendrait
      officielle.
    """
    if lien_officiel(url, OFFICIAL_DOMAINS):
        return True
    if feed and feed.get("official") and lien_officiel(url, domaines_officiels(feed)):
        return True
    return False


def statut_rockstarmag(url, feed=None):
    """Même principe : le domaine prime, la déclaration de source complète.

    La déclaration reste utile pour les liens que le flux RSS ne fait pas
    pointer directement sur le site (redirections, agrégateurs).
    """
    if lien_officiel(url, ROCKSTARMAG_DOMAINS):
        return True
    return bool(feed and feed.get("rockstarmag"))
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
# Défini dans feed_store : le seuil sert aussi à décider du ton des
# notifications, et deux valeurs séparées finiraient par diverger.
HOT_SOURCE_THRESHOLD = feed_store.HOT_SOURCE_THRESHOLD

# La taille du fichier allégé vit dans feed_store : l'outil de fusion doit
# le régénérer avec exactement la même règle après un conflit de push.


def find_duplicate(item, existing_items, links_index=None, fenetre_titres=None,
                   titles_index=None):
    """Renvoie l'article déjà connu dont celui-ci est un doublon, ou None.

    Renvoie l'article plutôt qu'un simple booléen : quand plusieurs
    rédactions couvrent la même actualité, le robot jetait purement les
    doublons et perdait au passage une information précieuse — le NOMBRE de
    sources qui en parlent, qui est le meilleur indicateur qu'il se passe
    quelque chose d'important.

    Trois passes, de la moins chère à la plus chère :

    1. `links_index` — correspondance lien -> article. Deux flux qui
       remontent exactement la même URL.

    2. `titles_index` — correspondance titre normalisé -> article, SANS
       limite d'ancienneté. C'est ce qui rattrape le même article publié
       sous deux URL (ign.com et fr.ign.com, bbc.com et bbc.co.uk) : la
       fenêtre de la passe 3 se compte en articles, pas en heures, et lors
       d'un pic à 288 articles/jour elle ne couvre plus que douze heures.
       Onze paires de doublons parfaits avaient ainsi échappé à la
       détection dans l'historique du 29/08. Un dictionnaire n'a ni coût ni
       horizon, là où élargir la fenêtre ne ferait que déplacer la limite.

    3. `fenetre_titres` — comparaison floue, pour les titres seulement
       PROCHES. Doit être fournie par l'appelant, qui seul sait ce qui a
       déjà été ajouté pendant le passage en cours. À défaut, on retombe
       sur les premiers éléments de `existing_items` — le comportement
       d'origine, conservé pour les appels directs.
    """
    if links_index is not None:
        connu = links_index.get(item["link"])
        if connu is not None:
            return connu
    else:
        for other in existing_items:
            if item["link"] == other["link"]:
                return other

    if titles_index is not None:
        # Un titre vide ne prouve rien : deux articles sans titre ne sont
        # pas le même article, et les fusionner ferait disparaître le second.
        cle = normalize_title(item.get("title", ""))
        if cle:
            connu = titles_index.get(cle)
            if connu is not None:
                return connu

    a_comparer = (fenetre_titres if fenetre_titres is not None
                  else existing_items[:TITLE_SIMILARITY_WINDOW])
    for other in a_comparer:
        if title_similarity(item["title"], other["title"]) >= SIMILARITY_THRESHOLD:
            return other
    return None


def record_coverage(existing, item):
    """Note qu'une RÉDACTION de plus couvre la même actualité.

    Alimente le champ extraSources, affiché sous la carte (« + 3 autres
    sources : … ») et sur lequel repose le badge « actu majeure ».

    Le lien fait foi, pas le nom du flux. Quatre requêtes Google News
    différentes remontent souvent la MÊME page : les compter comme quatre
    sources gonflait le badge sans qu'aucune rédaction supplémentaire n'ait
    publié quoi que ce soit. Mesuré sur l'historique du 29/08 : 136 des 168
    articles dits « croisés » n'étaient qu'un seul lien recompté, et le
    premier 🔥 était un article du Newswire trouvé par quatre de nos propres
    requêtes.
    """
    if existing.get("source") == item.get("source"):
        return False
    lien = item.get("link")
    if lien and lien == existing.get("link"):
        return False
    autres = existing.setdefault("extraSources", [])
    if any(a.get("source") == item.get("source") for a in autres):
        return False
    if lien and any(a.get("link") == lien for a in autres):
        return False
    autres.append({"source": item.get("source"), "link": lien})
    return True


def deduplique_couverture(items):
    """Retire des extraSources déjà stockés ceux qui répètent un lien connu.

    L'historique n'est jamais rejoué dans le pipeline de collecte : sans
    cette passe, les 136 articles gonflés avant le correctif garderaient
    leur compte faux indéfiniment, badge « actu majeure » compris.
    """
    nettoyes = 0
    retires = 0
    for item in items:
        autres = item.get("extraSources")
        if not autres:
            continue
        vus = {item.get("link")}
        gardes = []
        for a in autres:
            lien = a.get("link")
            if lien and lien in vus:
                retires += 1
                continue
            vus.add(lien)
            gardes.append(a)
        if len(gardes) != len(autres):
            nettoyes += 1
            if gardes:
                item["extraSources"] = gardes
            else:
                item.pop("extraSources", None)
    if retires:
        print(f"Correction rétroactive : {retires} source(s) en double retirée(s) sur {nettoyes} article(s) (même lien compté plusieurs fois)")
    return items


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


def trop_vieux(date_iso, maintenant=None):
    """L'article est-il une archive plutôt qu'une nouvelle ?

    Renvoie False quand la date est absente ou illisible : dans le doute on
    garde. Rejeter un article parce qu'on ne sait pas le dater reviendrait
    à vider les flux qui ne fournissent pas de date propre.
    """
    if not date_iso:
        return False
    quand = feed_store.parse_date_key(date_iso)
    if quand == feed_store.DATE_FLOOR:
        return False
    maintenant = maintenant or datetime.now(timezone.utc)
    return (maintenant - quand).days > MAX_ARTICLE_AGE_DAYS


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
        return matches_keywords(title, mots_cles_officiels(feed))
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
    vieux = 0
    for entry in retenues:
        title = entry.get("title", "")
        link = entry.get("link", "")
        date = normalize_date(entry)
        description = entry.get("summary", "")

        # Écarté AVANT le décodage Google News : inutile de payer une
        # seconde de décodage pour un article qu'on ne gardera pas.
        if trop_vieux(date):
            vieux += 1
            continue

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
        is_official = statut_officiel(real_link, feed)

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
            "rockstarmag": statut_rockstarmag(real_link, feed),
            "specialist": feed.get("specialist_source", False),
            "lang": feed.get("lang"),
            "image": image,
            "description": re.sub("<[^<]+?>", "", description)[:500],
        })

    resume = f"  {raw_count} entrée(s) dans le flux, {len(items)} pertinente(s) après filtre"
    if vieux:
        # Rendu visible : c'est le signe qu'une source déverse ses archives,
        # et donc qu'il faut regarder si son flux est bien réglé.
        resume += f" ({vieux} archive(s) de plus de {MAX_ARTICLE_AGE_DAYS} jours écartée(s))"
    journal.append(resume)
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
                  decoded_cache=None, afficher=True, promus=None):
    """Fusionne les résultats des sources dans l'historique.

    ORDRE CRITIQUE : on parcourt `feeds`, jamais l'ordre d'arrivée des
    réponses réseau. C'est cet ordre qui détermine quelle source est retenue
    comme propriétaire d'un article et dans quel ordre les sources
    supplémentaires (badge « N SOURCES ») s'empilent derrière lui. Parcourir
    dans l'ordre d'arrivée rendrait le fichier produit dépendant de la
    vitesse des serveurs, donc différent d'un passage à l'autre.

    Sorti de main() pour être testable : c'est ici que se joue l'équivalence
    entre l'ancienne récupération séquentielle et la nouvelle, parallèle.

    `promus` : liste optionnelle, remplie sur place avec les articles DÉJÀ
    connus qui franchissent le seuil d'actu majeure grâce à une reprise
    trouvée pendant ce passage. Sans ce signal, une couverture étalée sur
    plusieurs passages resterait muette — chaque reprise est un doublon,
    donc « rien de neuf » à annoncer, alors que c'est exactement le moment
    où le sujet devient important.

    Modifie `all_items`, `links_index`, `newly_added` et `promus` sur place.
    Renvoie (feed_infos, new_counts, inchanges).
    """
    # Fenêtre de comparaison par titre.
    #
    # Elle contient les articles les plus récents de l'historique ET ceux
    # ajoutés pendant ce passage. Ce second point est le correctif : les
    # nouveaux articles étaient jusqu'ici ajoutés à la FIN de all_items,
    # donc hors des `all_items[:200]` que consultait find_duplicate. Deux
    # rédactions publiant le même sujet dans le même passage n'étaient
    # donc jamais rapprochées dès que l'historique dépassait 200 articles
    # — ce qui est le cas depuis longtemps. Conséquence visible : le badge
    # « N SOURCES » n'a jamais pu dépasser 3, et 13 doublons manifestes
    # subsistaient dans les 400 articles les plus récents.
    #
    # Le tri est refait ici plutôt que supposé : prendre les N premiers
    # d'une liste tiendrait pour acquis qu'elle est déjà triée du plus
    # récent au plus ancien. C'est vrai du fichier publié, mais l'invariant
    # est garanti ici plutôt que documenté.
    fenetre = feed_store.sort_items(all_items)[:TITLE_SIMILARITY_WINDOW]

    # Index par titre exact sur TOUT l'historique, pas seulement la fenêtre.
    # Le premier inscrit gagne : l'ordre de FEEDS décide déjà quelle source
    # possède un article, et setdefault préserve cette règle.
    titles_index = {}
    for connu in all_items:
        cle = normalize_title(connu.get("title", ""))
        if cle:
            titles_index.setdefault(cle, connu)

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
            deja = find_duplicate(item, all_items, links_index, fenetre, titles_index)
            if deja is None:
                all_items.append(item)
                # L'article rejoint la fenêtre ET l'index : le suivant du
                # même passage lui sera comparé, quelle que soit sa source.
                fenetre.append(item)
                links_index[item["link"]] = item
                cle = normalize_title(item.get("title", ""))
                if cle:
                    titles_index.setdefault(cle, item)
                newly_added.append(item)
                new_counts[feed["id"]] += 1
                if decoded_cache is not None and item.get("source_link"):
                    decoded_cache[item["source_link"]] = item["link"]
            else:
                # Doublon : on ne le garde pas, mais on retient que cette
                # source couvre aussi le sujet.
                avant = 1 + len(deja.get("extraSources") or [])
                record_coverage(deja, item)
                apres = 1 + len(deja.get("extraSources") or [])
                # Strictement au franchissement : un sujet déjà majeur qui
                # gagne une 6e puis une 7e reprise ne réalerte pas.
                if promus is not None and avant < HOT_SOURCE_THRESHOLD <= apres:
                    promus.append(deja)
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

# Âge maximal d'un article JAMAIS VU pour être importé.
#
# Un article vieux de plusieurs mois découvert aujourd'hui n'est pas une
# nouvelle : c'est une archive. Le robot l'annoncerait pourtant comme
# « nouvel article », sur Discord et sur le téléphone.
#
# Le cas s'est produit le 29/08/2026 en changeant le flux VG247 : une
# recherche Google News restreinte à un domaine classe par PERTINENCE, pas
# par date, et a remonté 8 articles de 2022 à 2024 — annoncés comme neufs.
# Sans ce garde-fou, ajouter une source revient à déverser ses archives.
#
# Ne s'applique QU'AUX articles dont la date est réellement lisible : un
# flux sans date exploitable continue de passer, sinon on rejetterait tout
# son contenu en le prenant pour du 1ᵉʳ janvier 1970.
MAX_ARTICLE_AGE_DAYS = 45

# Nombre de passages consécutifs sans la moindre entrée brute avant de
# signaler une source comme tombée. À 30 minutes par passage, 6 font trois
# heures : assez pour écarter un 503 passager ou une coupure réseau, assez
# peu pour ne pas laisser un flux mort passer la journée inaperçu.
DEAD_SOURCE_RUNS = 6

# Fichier où sont déposées les alertes de source, à destination de
# discord_notify.py. Même mécanique que NEW_ITEMS_FILE : le workflow le
# place hors du dépôt, et son absence désactive simplement l'envoi.
SOURCE_ALERTS_FILE = os.environ.get("SOURCE_ALERTS_FILE", "")

# Articles déjà connus qui viennent de devenir une actu majeure. Séparé de
# NEW_ITEMS_FILE parce qu'ils ne sont PAS nouveaux : les mélanger fausserait
# le décompte « N nouveaux articles ».
PROMOTED_ITEMS_FILE = os.environ.get("PROMOTED_ITEMS_FILE", "")

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


def suivre_sources_muettes(health, compteurs_precedents):
    """Compte les passages consécutifs pendant lesquels chaque source est muette.

    `sources_health` dit si une source est muette MAINTENANT. Seul le
    cumul distingue une panne réelle d'un hoquet : un flux peut répondre
    503 une fois sans être mort. On garde donc un compteur par source dans
    feed.json, faute d'autre stockage persistant.

    Renvoie (compteurs, alertes). Une alerte n'est émise qu'au moment où
    l'état BASCULE — à la panne confirmée, puis au retour. Sans ça le robot
    répéterait la même mauvaise nouvelle 48 fois par jour.
    """
    compteurs = {}
    alertes = []
    for source in health:
        sid = source["id"]
        avant = int((compteurs_precedents or {}).get(sid, 0) or 0)
        if source["status"] == "muette":
            apres = avant + 1
            # Strictement égal : l'alerte part au passage qui franchit le
            # seuil, pas à tous ceux qui suivent.
            if apres == DEAD_SOURCE_RUNS:
                alertes.append({"type": "tombee", "name": source["name"],
                                "runs": apres})
        else:
            apres = 0
            if avant >= DEAD_SOURCE_RUNS:
                alertes.append({"type": "retour", "name": source["name"],
                                "runs": avant})
        # Un compteur à zéro n'apprend rien : on ne garde que les sources
        # réellement en difficulté, pour ne pas gonfler feed.json de 35
        # entrées inutiles à chaque passage.
        if apres:
            compteurs[sid] = apres
    return compteurs, alertes


def write_source_alerts_file(alertes):
    """Dépose les alertes de source pour l'étape de notification."""
    if not SOURCE_ALERTS_FILE or not alertes:
        return
    try:
        with open(SOURCE_ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump(alertes, f, ensure_ascii=False)
        print(f"  {len(alertes)} alerte(s) de source déposée(s) -> {SOURCE_ALERTS_FILE}")
    except OSError as e:
        # Comme pour les articles : une alerte non transmise ne doit jamais
        # faire échouer la collecte.
        print(f"  [alerte] impossible d'écrire {SOURCE_ALERTS_FILE} : {e}")


def write_promoted_items_file(promus):
    """Dépose les articles devenus majeurs, à destination des notifications."""
    if not PROMOTED_ITEMS_FILE or not promus:
        return
    try:
        with open(PROMOTED_ITEMS_FILE, "w", encoding="utf-8") as f:
            json.dump(promus, f, ensure_ascii=False)
        print(f"  {len(promus)} article(s) devenu(s) majeur(s) déposé(s) -> {PROMOTED_ITEMS_FILE}")
    except OSError as e:
        print(f"  [notif] impossible d'écrire {PROMOTED_ITEMS_FILE} : {e}")


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
    """Recalcule les drapeaux d'onglet sur les articles déjà stockés.

    L'historique est rechargé tel quel, sans jamais repasser dans le
    pipeline de collecte : sans cette passe, un article mal classé le
    resterait indéfiniment.

    Elle corrige dans LES DEUX SENS. Ne rétrograder que les faux officiels,
    comme avant, laissait à l'abandon le défaut inverse : un article publié
    par Rockstar ou Rockstar Mag mais découvert par Google News n'avait
    jamais été marqué, et rien ne serait jamais venu le chercher.

    La source est retrouvée par son nom pour honorer les domaines qu'elle
    déclare — la chaîne YouTube de Rockstar pointe légitimement vers
    youtube.com et perdrait son statut à chaque passage sinon. Un article
    dont la source a disparu de FEEDS (renommage, retrait) n'est jugé que
    sur son domaine, ce qui reste la bonne réponse.
    """
    par_source = {f["name"]: f for f in FEEDS}
    promus = 0
    retrogrades = 0
    for item in items:
        feed = par_source.get(item.get("source"))
        lien = item.get("link", "")

        officiel = statut_officiel(lien, feed)
        if bool(item.get("official")) != officiel:
            item["official"] = officiel
            if officiel:
                promus += 1
            else:
                retrogrades += 1

        rmag = statut_rockstarmag(lien, feed)
        if bool(item.get("rockstarmag")) != rmag:
            item["rockstarmag"] = rmag
            if rmag:
                promus += 1
            else:
                retrogrades += 1

    if retrogrades:
        print(f"Correction rétroactive : {retrogrades} drapeau(x) retiré(s) (domaine réel ne correspondant pas)")
    if promus:
        print(f"Correction rétroactive : {promus} drapeau(x) posé(s) (article publié par Rockstar ou Rockstar Mag, trouvé via une autre source)")
    return items


def fusionne_doublons_de_titre(items):
    """Fusionne les articles déjà stockés qui portent le MÊME titre exact.

    L'historique n'est jamais rejoué dans le pipeline de collecte : les
    doublons entrés avant l'ajout de l'index par titre y resteraient
    indéfiniment. Onze paires étaient dans ce cas au 29/08 — le même article
    sous deux URL (ign.com et fr.ign.com, bbc.com et bbc.co.uk), séparés par
    plus que ce que la fenêtre floue pouvait couvrir pendant un pic.

    Titre exact UNIQUEMENT, jamais la similarité floue. Rejouer le seuil de
    0,75 sur tout l'historique fusionnerait des articles réellement
    distincts : « Our GTA 6 Extended Look Predictions » et « How Our GTA 6
    Extended Look Predictions Held Up » passent le seuil alors que l'un
    annonce ce que l'autre conclut. Le doute profite à la séparation.

    Le plus ANCIEN est conservé : c'est la publication d'origine, et sa date
    est celle qui situe l'actualité. Le plus récent devient une source
    supplémentaire.
    """
    chronologique = sorted(items, key=lambda i: feed_store.parse_date_key(i.get("date")))
    par_titre = {}
    fusionnes = set()
    for item in chronologique:
        cle = normalize_title(item.get("title", ""))
        if not cle:
            continue
        garde = par_titre.get(cle)
        if garde is None:
            par_titre[cle] = item
        else:
            record_coverage(garde, item)
            fusionnes.add(id(item))
    if not fusionnes:
        return items
    restants = [i for i in items if id(i) not in fusionnes]
    print(f"Correction rétroactive : {len(fusionnes)} doublon(s) de titre fusionné(s) dans l'historique")
    return restants


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
    silence_precedent = stored.get("sources_silence", {}) or {}
    is_first_run = len(existing_items) == 0
    print(f"Historique chargé : {len(existing_items)} article(s) déjà connus" + (" (premier lancement)" if is_first_run else ""))
    existing_items = recheck_official_status(existing_items)
    existing_items = deduplique_couverture(existing_items)
    existing_items = fusionne_doublons_de_titre(existing_items)

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

    promus = []
    feed_infos, new_counts, inchanges = merge_results(
        FEEDS, resultats, all_items, links_index, newly_added, decoded_cache,
        promus=promus)
    if promus:
        print(f"\n🚨 {len(promus)} sujet(s) devenu(s) majeur(s) ce passage "
              f"(reprises trouvées après coup) :")
        for i in promus[:5]:
            print(f"    - [{1 + len(i.get('extraSources') or [])} sources] {i['title'][:70]}")
    write_promoted_items_file(promus)

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

    # État des sources, puis cumul des passages muets. L'état seul ne dit
    # que « muette maintenant » ; c'est le cumul qui distingue une panne
    # d'un hoquet, et qui permet de n'alerter qu'une fois.
    sante = build_sources_health(all_items, feed_infos, new_counts)
    silence, alertes_sources = suivre_sources_muettes(sante, silence_precedent)
    for a in alertes_sources:
        if a["type"] == "tombee":
            print(f"⚠️  Source tombée : {a['name']} — muette depuis {a['runs']} passages")
        else:
            print(f"✅ Source rétablie : {a['name']} — après {a['runs']} passages muets")
    write_source_alerts_file(alertes_sources)

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
        "sources_health": sante,
        # Compteurs de passages muets consécutifs, uniquement pour les
        # sources en difficulté. Sert à n'alerter qu'une fois par panne.
        "sources_silence": silence,
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
