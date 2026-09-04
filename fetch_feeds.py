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
import socket
import sys
import threading
import time
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

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

# Délai maximal d'attente sur une opération réseau, en secondes.
#
# feedparser.parse() n'accepte PAS de paramètre de timeout : il passe par
# urllib, qui utilise le délai par défaut des sockets — et ce défaut est
# None, c'est-à-dire une attente infinie. Une source qui accepte la
# connexion puis ne répond jamais bloquait donc son fil indéfiniment. Les
# autres sources continuaient (elles sont sur d'autres fils), mais le
# passage ne se terminait jamais : rien n'était publié, et seul le
# timeout-minutes du workflow finissait par tuer le job vingt minutes plus
# tard. Un site qui traîne coûtait le run entier.
#
# 20 s est très large : un passage complet dure ~1 min 25 pour 50 sources,
# et la source la plus lente répond en quelques secondes. Le seuil n'existe
# que pour les cas pathologiques, pas pour discipliner les sites lents.
#
# setdefaulttimeout agit sur tout le processus, ce qui est exactement ce
# qu'on veut : feedparser, mais aussi les appels réseau de googlenewsdecoder
# qui n'en fixaient aucun non plus. fetch_og_image garde le sien (8 s), plus
# strict, parce qu'un timeout explicite passé à requests prime sur ce défaut.
FETCH_TIMEOUT = 20
socket.setdefaulttimeout(FETCH_TIMEOUT)

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
REPRISE_PAUSE = 3.0     # respiration avant la seconde tentative

# Décodages Google News simultanés, tous flux confondus. Le plafond est
# global (et non par flux) : sans lui, 3 flux Google News traités en même
# temps auraient multiplié d'autant la charge envoyée à Google.
DECODE_WORKERS = 4

# Miniatures récupérées de front (lot 3 : 5 -> 8). Ces requêtes visent
# chacune un site différent, il n'y a pas de politesse par domaine à tenir.
IMAGE_WORKERS = 8

_DECODE_SEMA = threading.Semaphore(DECODE_WORKERS)

# Échecs de décodage Google News du passage en cours.
#
# Pourquoi compter : 20 des 50 sources passent par Google News, et
# gnewsdecoder dépend d'un format que Google peut changer sans prévenir. Un
# échec ne perd JAMAIS l'article — decode_google_news_link renvoie le lien
# d'origine — mais le lien reste un redirecteur news.google.com, ce qui a
# deux conséquences discrètes : la déduplication par lien ne reconnaît plus
# le même article vu ailleurs, et le classement par domaine ne peut plus
# décider de l'onglet. La couverture se dégrade sans que rien n'échoue.
#
# Le compteur rend cette dégradation visible dans le bilan du passage, au
# lieu de la laisser dans les logs. Verrou nécessaire : predecode_links est
# appelé depuis plusieurs fils, un par flux Google News.
_DECODE_ECHECS = 0
_DECODE_ECHECS_LOCK = threading.Lock()


def reinitialise_echecs_decodage():
    global _DECODE_ECHECS
    with _DECODE_ECHECS_LOCK:
        _DECODE_ECHECS = 0


def echecs_decodage():
    with _DECODE_ECHECS_LOCK:
        return _DECODE_ECHECS

# ---------------------------------------------------------------------------
# Liste des sources — copiée depuis DEFAULT_FEEDS dans gta6-watch.html.
# Si tu ajoutes/retires une source dans le tracker HTML, reporte le
# changement ici aussi pour garder les deux synchronisés.
# ---------------------------------------------------------------------------
FEEDS = [
    {"id": "rockstar-en", "name": "Rockstar Games (officiel EN)",
     "url": "https://news.google.com/rss/search?q=site:rockstargames.com&hl=en&gl=US&ceid=US:en",
     "official": True, "max_entrees": 100, "garder_les_archives": True},
    {"id": "rockstar-fr", "name": "Rockstar Games (officiel FR)",
     "url": "https://news.google.com/rss/search?q=site:rockstargames.com&hl=fr&gl=FR&ceid=FR:fr",
     "official": True, "lang": "fr", "max_entrees": 100, "garder_les_archives": True},
    {"id": "rockstar-announce", "name": "Rockstar Games (annonces)", "url": "https://news.google.com/rss/search?q=%22Rockstar+Games%22+(%22Grand+Theft+Auto+VI%22+OR+%22GTA+6%22)+(announce+OR+announces+OR+reveals+OR+confirms)&hl=en&gl=US&ceid=US:en", "official": True},
    {"id": "gta6-netflix", "name": "GTA 6 x Netflix", "url": "https://news.google.com/rss/search?q=(%22GTA+6%22+OR+%22Grand+Theft+Auto+VI%22)+Netflix&hl=en&gl=US&ceid=US:en", "official": False, "specialist_source": True},
    {"id": "take2-ir", "name": "Take-Two Investor Relations (officiel)", "url": "https://ir.take2games.com/rss/news-releases.xml?items=15", "official": True, "garder_les_archives": True},
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
     "official_keywords_extra": ["trailer"],
     # Une bande-annonce de GTA 6 ne périme pas. Le flux ne porte de toute
     # façon que 15 entrées, donc l'exemption ne peut pas déverser grand-
     # chose — mais sans elle, la seule vidéo GTA 6 un peu ancienne des 15
     # était écartée à chaque passage.
     "garder_les_archives": True},
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
    # (26 URL sondées avec le même agent utilisateur et le même filtre que
    # le robot) : il répond, il est frais, et on sait
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
    # /rss répond 301 vers /feed depuis le 29/08/2026, de façon stable et
    # reproductible — contrairement aux 404 intermittents de YouTube, qui
    # eux étaient du rationnement. feedparser suit bien la redirection, mais
    # ce qu'il reçoit au bout n'est pas toujours un flux : la source
    # apparaissait muette la moitié du temps. Autant demander directement
    # l'adresse que le serveur réclame.
    {"id": "kotaku", "name": "Kotaku", "url": "https://kotaku.com/feed", "official": False},
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

# Vidéos trop anciennes pour le flux de leur chaîne.
#
# Le flux Atom d'une chaîne YouTube ne porte que 15 entrées et ne pagine
# pas : tout ce qui précède est hors de portée du robot, définitivement.
# Les deux bandes-annonces de GTA 6 comptent trop pour qu'on les laisse
# manquer sous prétexte que Rockstar a publié quinze vidéos depuis.
#
# CHAQUE CHAMP VIENT D'UNE MESURE, jamais de mémoire. Les titres sortent
# de `--video` (oEmbed, lancé depuis un runner) ; les vignettes se
# déduisent de l'identifiant ; les dates ont été lues sur les fiches
# YouTube elles-mêmes. Deux tentatives automatiques les avaient données
# fausses — « 2 days ago » pour une vidéo de 2025, puis un dateCreated de
# 2026 — parce que la page servie à un serveur n'est pas celle d'un
# navigateur. Une date approximative rangerait la vidéo au mauvais endroit
# du fil et plus rien ne viendrait la corriger.
#
# Midi UTC quand seul le jour est connu : à minuit, un décalage horaire
# ferait afficher la veille.
VIDEOS_ARCHIVEES = [
    {"source": "rockstar-youtube", "video": "QdBZY2fkU-0",
     "title": "Grand Theft Auto VI Trailer 1",
     "date": "2023-12-05T12:00:00+00:00"},
    {"source": "rockstar-youtube", "video": "VQRLujxTm3c",
     "title": "Grand Theft Auto VI Trailer 2",
     "date": "2025-05-06T12:00:00+00:00"},
    {"source": "rockstar-youtube", "video": "tJbzMqJGH4k",
     "title": "Grand Theft Auto VI: An Extended Look",
     "date": "2026-08-27T18:00:48-07:00"},
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


# Identifiant d'une vidéo YouTube, dans les différentes formes d'URL que
# le flux ou une redirection peuvent produire.
_ID_YOUTUBE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|embed/|v/|shorts/)"
    r"|youtu\.be/)([A-Za-z0-9_-]{11})")


def vignette_youtube(url):
    """Miniature déduite de l'identifiant de la vidéo, sans appel réseau.

    Filet de sécurité pour les deux chaînes YouTube : même si le flux
    cessait un jour de fournir media:thumbnail, l'adresse reste calculable
    à partir du lien. Zéro requête, et rien à maintenir.

    hqdefault et non maxresdefault : la première existe pour toute vidéo
    publiée, la seconde manque sur les vidéos de faible définition — et une
    vignette absente est exactement ce qu'on cherche à corriger.
    """
    trouve = _ID_YOUTUBE.search(url or "")
    return f"https://i.ytimg.com/vi/{trouve.group(1)}/hqdefault.jpg" if trouve else None


def _media_est_une_image(media):
    """Vrai tant que rien n'indique que ce média N'EST PAS une image.

    Prudence délibérée. Beaucoup de flux ne déclarent ni `medium` ni
    `type`, et leurs URL n'ont pas d'extension : les CDN de Clubic, du
    Jerusalem Post ou d'Unsplash servent de vraies images depuis des
    chemins sans .jpg. Rejeter par défaut ferait perdre des dizaines de
    vignettes valides. On n'écarte donc que ce qui s'annonce explicitement
    comme autre chose qu'une image.
    """
    medium = (media.get("medium") or "").lower()
    if medium:
        return medium == "image"
    type_mime = (media.get("type") or "").lower()
    if type_mime:
        return type_mime.startswith("image/")
    return True


def image_du_flux(entry):
    """Miniature fournie par le flux lui-même, si elle en est vraiment une.

    L'ordre compte, et l'ancien était faux pour YouTube. Son flux Atom
    fournit les deux : un media:thumbnail — la vraie vignette — et un
    media:content qui est l'ancienne URL du lecteur Flash
    (/v/{id}?version=3, type application/x-shockwave-flash). Comme
    media:content était pris en premier sans vérifier ce qu'il annonçait,
    les 17 vidéos du fil enregistraient une adresse qui n'est pas une
    image. L'app masque une image cassée (onerror), donc elles
    s'affichaient simplement sans vignette, sans le moindre signal.
    """
    for media in (getattr(entry, "media_content", None) or []):
        if _media_est_une_image(media) and media.get("url"):
            return media["url"]
    for media in (getattr(entry, "media_thumbnail", None) or []):
        if media.get("url"):
            return media["url"]
    return None


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


def _sans_nombres(titre):
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", normalize_title(titre))).strip()


# Mots présents dans presque TOUS les titres du fil : ils ne distinguent
# rien, et les compter fait ressembler entre eux des articles qui n'ont
# rien à voir. Le nom du jeu sous ses graphies courantes, et le nom du
# studio qui sert de suffixe à quantité de pages (« … - Rockstar Games »).
#
# Volontairement court. Chaque mot retiré rapproche TOUS les titres entre
# eux ; une liste bavarde ferait fusionner des articles distincts.
_MOTS_SANS_VALEUR = {"grand", "theft", "auto", "vi", "6", "gta", "gta6",
                     "gtavi", "rockstar", "games"}


# Le segment final « … - Kotaku », « … | IGN France », « … - Rockstar
# Games » : 60 % des titres du fil en portent un. C'est le nom du média,
# pas le sujet, et il fait diverger deux reprises du même article.
_SUFFIXE_MEDIA = re.compile(r"\s+[-–—|]\s+([^-–—|]{2,28})$")

# Un titre peut finir par autre chose qu'un nom de média : « GTA 6 : UN
# LARGE APERÇU - ON DÉCOUVRE CELA ENSEMBLE ! ». Couper à l'aveugle ces
# suffixes-là a fait PERDRE 2 fusions justes sur les 500 derniers titres.
# On ne coupe donc qu'un suffixe qui REVIENT : un nom de média apparaît des
# dizaines de fois dans l'historique (mashable 30, kotaku 28, ign france
# 21…), la fin d'un vrai titre n'apparaît qu'une fois. La liste s'apprend
# donc toute seule sur le fil, et accueille sans rien coder les sources
# ajoutées plus tard.
SUFFIXE_MEDIA_MINIMUM = 3

# Sauf ceux-là, qui sont AUSSI des mots du jeu. « Vice » est un média
# (vice.com, 10 titres) mais Vice City est la ville de GTA 6 : un titre
# finissant par « - Vice City » doit rester entier.
_SUFFIXES_PROTEGES = {"vice city", "vice city fm"}

# Appris au chargement de l'historique par memorise_suffixes_medias().
# Vide par défaut : sans historique on ne coupe rien, ce qui est le
# comportement d'avant.
_SUFFIXES_MEDIAS = frozenset()


def apprend_suffixes_medias(items, minimum=SUFFIXE_MEDIA_MINIMUM):
    """Les fins de titre assez fréquentes pour être des noms de média."""
    vus = {}
    for item in items:
        trouve = _SUFFIXE_MEDIA.search(item.get("title") or "")
        if trouve:
            cle = normalize_title(trouve.group(1))
            if cle and cle not in _SUFFIXES_PROTEGES:
                vus[cle] = vus.get(cle, 0) + 1
    return frozenset(cle for cle, n in vus.items() if n >= minimum)


def memorise_suffixes_medias(items, minimum=SUFFIXE_MEDIA_MINIMUM):
    """Apprend la liste et la retient pour toutes les comparaisons du run."""
    global _SUFFIXES_MEDIAS
    _SUFFIXES_MEDIAS = apprend_suffixes_medias(items, minimum)
    return _SUFFIXES_MEDIAS


def sans_suffixe_media(titre):
    """Le titre sans son « - Nom du média » final, s'il en a un de connu."""
    trouve = _SUFFIXE_MEDIA.search(titre or "")
    if trouve and normalize_title(trouve.group(1)) in _SUFFIXES_MEDIAS:
        return titre[:trouve.start()]
    return titre


def titre_comparable(titre):
    """Le titre débarrassé de ce que tous les articles ont en commun.

    Comparer les titres entiers donnait un score trompeur dans les deux
    sens, mesuré sur les 500 articles les plus récents :

    - « Grand Theft Auto VI - Rockstar Games » n'est QUE le nom du jeu et
      celui du studio. Il atteignait 0,750 avec « … Trailer 1 » — pile le
      seuil — et se comportait en aimant. Le 30/08/2026 il a absorbé le
      Trailer 1 et fait PERDRE le Trailer 2 derrière lui. Nettoyé, il ne
      reste rien de ce titre : il ne peut plus rien attirer.

    - à l'inverse, « 20+ New GTA 6 Screenshots Released » et « New Grand
      Theft Auto 6 Screenshots Revealed » restaient sous le seuil (0,71)
      parce que les deux graphies du nom du jeu comptaient comme une
      différence. C'est le MÊME article, montré deux fois dans le fil.

    Nettoyage : 14 paires rapprochées contre 9, sur les mêmes 500 titres.

    S'y ajoute le retrait du « - Nom du média » final (voir
    sans_suffixe_media) : 7 paires de plus, aucune perdue, sur ces mêmes
    500 titres — quatre reprises du même article y étaient affichées
    séparément parce que seul le suffixe les distinguait.
    """
    return " ".join(m for m in normalize_title(sans_suffixe_media(titre)).split()
                    if m not in _MOTS_SANS_VALEUR)


def titres_dune_meme_serie(a, b):
    """Deux titres identiques À LEUR NUMÉRO PRÈS — donc deux contenus distincts.

    « Grand Theft Auto VI Trailer 1 » et « Grand Theft Auto VI Trailer 2 »
    se ressemblent à 0,966 : très au-dessus de n'importe quel seuil
    raisonnable, alors que ce sont deux vidéos différentes séparées d'un an
    et demi. Le numéro EST l'information qui les distingue, et c'est le seul
    endroit d'un titre où un caractère de différence change tout.

    Constaté en versant les bandes-annonces dans l'historique : le Trailer 2
    disparaissait, absorbé par le Trailer 1, et se retrouvait crédité comme
    « autre source » de celui-ci — un lien qui emmène le lecteur sur un
    autre contenu. Le Trailer 3 sortira avant novembre.

    On préfère ici rater une fusion que d'en faire une fausse : deux cartes
    en double se voient et se corrigent, un article escamoté ne se voit pas.
    """
    # Sur le titre débarrassé du nom du média, comme title_similarity : sans
    # ça, « Trailer 1 » et « Trailer 2 - Rockstar Games » n'étaient PAS vus
    # comme une même série (le suffixe faisait diverger _sans_nombres), et
    # se retrouvaient à 0,889 sans garde-fou. Le Trailer 3 sortira avant
    # novembre et se serait fait absorber par le Trailer 2.
    ta, tb = sans_suffixe_media(a), sans_suffixe_media(b)
    if _sans_nombres(ta) != _sans_nombres(tb):
        return False
    return re.findall(r"\d+", ta) != re.findall(r"\d+", tb)


def title_similarity(a, b):
    """Ressemblance de deux titres, nom du jeu et du studio mis de côté.

    Voir titre_comparable : ces mots-là sont dans presque tous les titres,
    les compter brouille la mesure au lieu de l'affiner. Un titre qui n'en
    contient rien d'autre ne ressemble à rien — c'est le bon résultat.
    """
    return ressemblance_comparables(titre_comparable(a), titre_comparable(b))


def ressemblance_comparables(ta, tb):
    """Le même score, mais sur des titres DÉJÀ passés par titre_comparable.

    Existe pour que la passe 3 de find_duplicate ne nettoie pas deux fois
    les mêmes titres : elle a besoin des formes nettoyées pour le préfiltre
    ci-dessous, autant les réutiliser ici.
    """
    if not ta or not tb:
        return 0.0
    return SequenceMatcher(None, ta, tb).ratio()


def peut_atteindre_le_seuil(ta, tb, seuil=SIMILARITY_THRESHOLD):
    """Vrai si ressemblance_comparables(ta, tb) PEUT atteindre le seuil.

    Deux bornes SUPÉRIEURES du score de difflib, calculées sans construire
    de SequenceMatcher — c'est lui qui coûte cher, pas le nettoyage des
    titres (mesuré : le précalculer ne gagne que 10 %).

    Ce sont exactement les bornes que difflib expose sous real_quick_ratio()
    et quick_ratio(), mais les appeler supposerait d'avoir déjà construit
    l'objet, donc d'avoir déjà payé.

      1. Les longueurs. Le score vaut 2M/T, où T est la somme des deux
         longueurs et M le nombre de caractères appariés — au mieux la
         longueur du plus court. Un titre de 20 caractères et un de 90 ne
         peuvent donc pas dépasser 2×20/110 = 0,36.
      2. Les caractères utilisés. Même généreusement, M ne dépasse pas le
         nombre de caractères que les deux titres ont en commun, multiplicité
         comprise. Deux titres qui ne partagent presque aucune lettre sont
         écartés sans être alignés.

    Ce sont des bornes SUPÉRIEURES : elles ne peuvent répondre que « le
    seuil est hors d'atteinte », jamais « c'est un doublon ». Un vrai
    doublon ne peut donc pas leur échapper. Vérifié sur 175 980 paires de
    titres réels : aucune décision différente, pour 6 fois moins de temps.

    Le garde-fou des numéros (titres_dune_meme_serie) reste appliqué APRÈS,
    par l'appelant : l'ordre est sans effet sur le résultat, les deux ne
    font que refuser.
    """
    total = len(ta) + len(tb)
    if not total:
        return False
    if 2.0 * min(len(ta), len(tb)) / total < seuil:
        return False
    communs = sum((Counter(ta) & Counter(tb)).values())
    return 2.0 * communs / total >= seuil


# Un doublon de titre proche n'a de sens qu'entre articles publiés à peu
# près en MÊME TEMPS : deux rédactions qui couvrent l'actu du jour. Comparer
# un article à un autre vieux de plusieurs mois n'arrive jamais en pratique
# et coûte cher une fois l'historique grossi.
#
# La fenêtre se compte donc en HEURES, pas en articles. Comptée en articles,
# elle se refermait exactement quand il aurait fallu qu'elle s'ouvre :
#
#     régime normal      200 articles = 50 h
#     pic du 27/08/2026  200 articles = 16 h     (293 articles ce jour-là)
#
# Autrement dit, le robot voyait le moins loin les jours où il se passait
# quelque chose. Mesuré sur les 26 doublons que la fenêtre avait laissés
# passer : 5 l'étaient uniquement à cause de cette bordure, et une fenêtre
# de 72 h les aurait tous rattrapés avant qu'ils ne fassent sonner le
# téléphone. Les 18 autres étaient à portée et ont été ratés par la règle
# de comparaison, corrigée depuis.
FENETRE_HEURES = 72

# Deux bornes autour de la fenêtre en heures.
#
# Le plancher garantit qu'on ne compare jamais à MOINS qu'avant : trois
# jours creux ne doivent pas réduire la fenêtre à vingt articles.
# Le plafond borne le coût : une fenêtre de 72 h vaut 534 articles
# aujourd'hui et 593 au pic, mais rien n'interdit à un événement futur de
# produire trois mille articles en trois jours.
#
# Plafond porté de 1 500 à 5 000 le 02/09/2026, une fois la comparaison
# rendue six fois moins chère par peut_atteindre_le_seuil. 1 500 tenait
# large aujourd'hui — le plus gros jour observé (296 articles le 27/08)
# ne remplissait la fenêtre de 72 h qu'à 709 articles, 47 % du plafond —
# mais le plafond mord précisément le jour où la fenêtre sert le plus :
# à 1 500, une journée de sortie à 1 000 articles ramènerait les 72 h
# demandées à 36 h effectives, et le robot verrait le moins loin le jour
# où il y a le plus de doublons. 5 000 couvre 72 h jusqu'à 1 600
# articles par jour, cinq fois le pic connu, et reste moins cher que
# 1 500 ne l'était avant le préfiltre.
TITLE_SIMILARITY_WINDOW = 200          # plancher
FENETRE_MAX = 5000                     # plafond


def fenetre_recente(items, heures=FENETRE_HEURES):
    """Les articles publiés dans les dernières `heures`, bornés.

    Le tri est refait ici plutôt que supposé : prendre les N premiers d'une
    liste tiendrait pour acquis qu'elle est déjà triée du plus récent au
    plus ancien. C'est vrai du fichier publié, l'invariant est garanti ici.

    Note : pendant la collecte, la liste n'est pas re-triée à chaque ajout —
    les nouveaux articles rejoignent la fenêtre par la fin. Compromis
    volontaire pour la vitesse ; la dédup par lien exact reste la protection
    principale, la similarité de titre n'est qu'un filet en plus.
    """
    tries = feed_store.sort_items(items)
    if not tries:
        return []
    limite = feed_store.parse_date_key(tries[0].get("date")) - timedelta(hours=heures)
    dedans = 0
    for item in tries:
        if feed_store.parse_date_key(item.get("date")) < limite:
            break
        dedans += 1
    taille = min(max(dedans, TITLE_SIMILARITY_WINDOW), FENETRE_MAX)
    return tries[:taille]


# À partir de combien de sources distinctes une actualité est considérée
# comme majeure. Sur ce sujet, un article isolé est en général une reprise
# ou de la supputation ; quand quatre rédactions publient la même chose
# dans la foulée, c'est un trailer, une date ou une annonce officielle.
# Défini dans feed_store : le seuil sert aussi à décider du ton des
# notifications, et deux valeurs séparées finiraient par diverger.
HOT_SOURCE_THRESHOLD = feed_store.HOT_SOURCE_THRESHOLD

# La taille du fichier allégé vit dans feed_store : l'outil de fusion doit
# le régénérer avec exactement la même règle après un conflit de push.


def index_des_liens(items):
    """Lien -> article, en comptant AUSSI les liens des sources supplémentaires.

    Un article fusionné dans un autre ne figure plus au fil sous son propre
    lien : il n'y survit que comme source supplémentaire. Sans lui dans
    l'index, son flux le rapporte au passage suivant, il n'est reconnu nulle
    part et il RENTRE une seconde fois — la fusion est défaite, et le
    lecteur reçoit une notification pour un article qu'il a déjà lu.

    La troisième passe de find_duplicate ne rattrape pas le cas : elle ne
    compare qu'aux TITLE_SIMILARITY_WINDOW articles les plus récents, et
    pendant un pic à 288 articles par jour cette fenêtre ne couvre plus une
    journée. Le gardien, lui, peut être vieux de plusieurs jours.

    Constaté le 30/08/2026, juste après le premier rejeu de l'historique :
    5 des 21 articles fusionnés étaient revenus dans le même passage.

    Le lien principal l'emporte sur un lien de source supplémentaire : un
    article présent en propre reste son propre représentant.
    """
    index = {}
    for item in items:
        for autre in (item.get("extraSources") or []):
            if autre.get("link"):
                index.setdefault(autre["link"], item)
    for item in items:
        if item.get("link"):
            index[item["link"]] = item
    return index


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
    # Le titre du candidat n'est nettoyé qu'une fois pour toute la fenêtre.
    ta = titre_comparable(item["title"])
    if not ta:
        return None
    for other in a_comparer:
        tb = titre_comparable(other["title"])
        # Le préfiltre en premier : c'est lui qui écarte la quasi-totalité
        # des paires, et il ne coûte presque rien.
        if not peut_atteindre_le_seuil(ta, tb):
            continue
        if titres_dune_meme_serie(item["title"], other["title"]):
            continue
        if ressemblance_comparables(ta, tb) >= SIMILARITY_THRESHOLD:
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
    rates = 0
    with ThreadPoolExecutor(max_workers=min(DECODE_WORKERS, len(a_faire))) as executor:
        for lien, vrai in zip(a_faire, executor.map(decode_google_news_link, a_faire)):
            resolus[lien] = vrai
            if vrai == lien:
                rates += 1
            # On ne met en cache QUE les décodages réussis. En cas d'échec
            # la fonction renvoie le lien d'origine inchangé ; le mettre en
            # cache empêcherait un autre flux portant le même article de
            # retenter, alors qu'en séquentiel il retentait.
            # Un succès, lui, ne dépend que du lien d'entrée : deux flux
            # aboutissent au même résultat, l'écriture partagée est sûre.
            if vrai != lien:
                cache[lien] = vrai
    if rates:
        global _DECODE_ECHECS
        with _DECODE_ECHECS_LOCK:
            _DECODE_ECHECS += rates
    if journal is not None:
        detail = f" — {rates} échec(s)" if rates else ""
        journal.append(f"  {len(a_faire)} lien(s) Google News décodé(s) "
                       f"({DECODE_WORKERS} à la fois){detail}")
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

    # Un validateur décrit une URL PRÉCISE, pas une source. Quand l'adresse
    # d'une source change dans FEEDS, l'etag de l'ancienne ne veut plus rien
    # dire — et le danger n'est pas qu'il soit refusé, c'est qu'il soit
    # ACCEPTÉ : deux chemins d'un même site partagent souvent le même
    # backend (kotaku.com/rss et kotaku.com/feed), et le serveur peut
    # répondre 304. Le robot noterait alors « inchangé » pour un flux qu'il
    # n'a jamais lu, indéfiniment, sans le moindre message d'erreur.
    #
    # Un état enregistré avant l'ajout de ce champ n'a pas d'URL : il est
    # écarté lui aussi. Ça coûte un téléchargement complet, une fois, pour
    # la quinzaine de sources concernées — et l'état se répare tout seul au
    # passage suivant.
    if precedent and precedent.get("url") != feed["url"]:
        journal.append("  validateurs HTTP ignorés : ils ont été obtenus "
                       "pour une autre adresse")
        precedent = {}

    # Retenu pour la reprise : une réponse obtenue AVEC validateurs et une
    # réponse obtenue sans ne sont pas la même mesure. Voir merite_reprise.
    conditionnelle = bool(precedent.get("etag") or precedent.get("modified"))

    try:
        parsed = feedparser.parse(
            feed["url"], agent=USER_AGENT,
            etag=precedent.get("etag") or None,
            modified=precedent.get("modified") or None,
        )
    except Exception as e:
        journal.append(f"  échec réseau : {e}")
        return [], {"raw_count": 0, "not_modified": False,
                    "conditionnelle": conditionnelle,
                    "injoignable": True}, journal

    statut = getattr(parsed, "status", None)

    # Où la requête a réellement abouti. feedparser suit les redirections et
    # expose l'adresse finale dans `href` : quand elle diffère de l'URL
    # demandée, c'est le flux qui a déménagé, et cette adresse est
    # exactement ce qu'il faut mettre dans FEEDS.
    #
    # Sans ça, une redirection vers une page d'accueil produit « 0 entrée »
    # et un code 301/302 — on sait que ça a bougé, pas vers où. Constaté le
    # 30/08/2026 sur IGN (302) et Kotaku (301), deux sources qu'il a fallu
    # laisser muettes le temps d'un passage de plus faute de cette ligne.
    arrivee = getattr(parsed, "href", None)
    redirige = arrivee if arrivee and arrivee != feed["url"] else None
    if redirige:
        journal.append(f"  redirigé vers {redirige}")

    if statut == 304:
        journal.append("  inchangé depuis la dernière fois (304), rien à retélécharger")
        # On renvoie l'état précédent tel quel : un 304 ne fournit pas de
        # nouveaux validateurs, les réécrire à vide ferait retélécharger le
        # flux entier au prochain passage.
        return [], {"raw_count": 0, "not_modified": True,
                    "url": feed["url"],
                    "etag": precedent.get("etag"),
                    "modified": precedent.get("modified")}, journal

    if parsed.bozo and not parsed.entries:
        journal.append(f"  échec : {parsed.bozo_exception}")
        # Même test de `version` que plus bas : une réponse que feedparser
        # refuse ET qui ne s'annonce comme aucun format de flux n'est pas un
        # flux du tout (page HTML servie en text/html, le plus souvent). Un
        # flux réellement malformé, lui, garde sa version — c'est une source
        # cassée d'une autre nature, à ne pas confondre.
        #
        # `statut is not None` est indispensable : feedparser range AUSSI les
        # pannes réseau dans bozo, sans code HTTP. Sans cette condition, un
        # site injoignable était annoncé « ce n'est pas un flux » — un
        # diagnostic faux, et le pire genre : il accuse l'URL alors que
        # c'est le réseau qui n'a pas répondu.
        #
        # Une panne serveur (5xx, 429) ne prouve rien sur l'URL : le serveur
        # a renvoyé sa page d'erreur, que feedparser refuse évidemment de
        # lire. L'accuser d'« être devenue une page web » est un diagnostic
        # faux, et il coûte cher — voir panne_de_serveur.
        panne = panne_de_serveur(statut)
        pas_un_flux = (statut is not None and not panne
                       and not (getattr(parsed, "version", "") or ""))
        return [], {"raw_count": 0, "not_modified": False,
                    "conditionnelle": conditionnelle,
                    "http_status": statut, "redirect": redirige,
                    "not_a_feed": pas_un_flux,
                    "panne_serveur": panne,
                    "injoignable": statut is None}, journal

    # Un flux vide ne dit pas POURQUOI il est vide, et feedparser ne aide
    # pas : il avale une page HTML sans protester — bozo reste faux et la
    # liste d'entrées est vide, exactement comme un flux valide mais sans
    # article. Seul le champ `version` les sépare : il est renseigné
    # ("rss20", "atom10"…) uniquement quand le document EST un flux.
    #
    # Sans cette distinction, une page de blocage anti-robot et un flux
    # réellement vide produisent la même ligne « 0 entrée(s) » et la même
    # source « muette » — c'est arrivé le 30/08/2026 sur IGN et Kotaku, et
    # le journal ne permettait pas de trancher. Le code HTTP est ajouté
    # pour la même raison : un 403 déguisé en page HTML se lit alors d'un
    # coup d'œil.
    if not parsed.entries:
        version = getattr(parsed, "version", "") or ""
        if not version:
            if panne_de_serveur(statut):
                journal.append(
                    f"  SERVEUR EN PANNE — HTTP {statut}, réessai au passage "
                    f"suivant (rien à corriger ici)")
                return [], {"raw_count": 0, "not_modified": False,
                            "conditionnelle": conditionnelle,
                            "http_status": statut, "redirect": redirige,
                            "panne_serveur": True}, journal
            cause = ("redirigé vers une page qui n'est pas un flux — "
                     "corriger l'URL dans FEEDS" if redirige
                     else "page de blocage ? URL devenue une page web ?")
            journal.append(
                f"  PAS UN FLUX — réponse HTTP {statut or '?'} reçue mais ce "
                f"n'est pas du RSS/Atom ({cause})")
            return [], {"raw_count": 0, "not_modified": False,
                        "conditionnelle": conditionnelle,
                        "http_status": statut, "redirect": redirige,
                        "not_a_feed": True}, journal
        journal.append(f"  flux {version} valide mais vide "
                       f"(HTTP {statut or '?'}) — le site ne publie rien")

    raw_count = len(parsed.entries)

    # Validateurs à renvoyer au prochain passage. feedparser les expose
    # directement quand le serveur les fournit ; beaucoup de flux n'en
    # donnent aucun, auquel cas on retélécharge comme avant.
    nouvel_etat = {
        "raw_count": raw_count,
        "not_modified": False,
        "conditionnelle": conditionnelle,
        # Enregistrée avec les validateurs pour que le passage suivant
        # sache à quelle adresse ils se rapportent. Voir plus haut.
        "url": feed["url"],
        "http_status": statut,
        "redirect": redirige,
        "etag": getattr(parsed, "etag", None),
        "modified": getattr(parsed, "modified", None),
    }

    # Premier passage : on ne garde que les entrées qui passent le filtre par
    # mots-clés, sans encore toucher au réseau.
    #
    # Le plafond existe pour le coût, pas pour la pertinence : chaque entrée
    # retenue d'un flux Google News demande un décodage de lien, et ces
    # décodages pesaient 51 s sur un passage de 74 s. Trente convient à un
    # flux d'actualité générale, où les entrées 30 à 100 sont du bruit.
    #
    # Mais les recherches sur site:rockstargames.com renvoient 100 entrées
    # classées par PERTINENCE, pas par date : les 70 qu'on ignorait ne sont
    # pas « les plus vieilles », ce sont celles d'après — et parmi elles, des
    # pages de Rockstar qui n'apparaissaient nulle part dans l'app. D'où un
    # plafond réglable source par source, relevé là où il fait perdre du
    # contenu voulu, laissé à 30 partout ailleurs.
    retenues = [entry for entry in parsed.entries[:feed.get("max_entrees", MAX_ENTREES)]
                if passe_le_filtre(feed, entry.get("title", ""), entry.get("summary", ""))]

    # Les archives sont écartées AVANT le décodage Google News : inutile de
    # payer une seconde de décodage pour un article qu'on jette la ligne
    # suivante. L'âge se lit sur la date de l'entrée, aucun décodage requis.
    #
    # Le commentaire disait déjà cela, mais le code ne le faisait plus : le
    # décodage groupé, introduit après, avait été posé AVANT la boucle, donc
    # avant le filtre d'âge qu'il était censé suivre. Mesuré sur le passage
    # du 01/09/2026 : 199 liens décodés, dont 91 pour des articles jetés
    # aussitôt — 46 %, soit une vingtaine de secondes sur un passage de 90.
    #
    # L'exemption vaut pour les canaux de Rockstar eux-mêmes. Le garde-fou
    # existe contre les recherches Google News qui déversent des archives
    # classées par pertinence — huit articles de 2022 à 2024 annoncés comme
    # neufs le 29/08/2026. Mais sur le fil de Rockstar, une vieille
    # publication n'est pas du bruit : c'est précisément ce qu'on cherche.
    # Le drapeau est posé source par source, jamais déduit de `official` :
    # « Rockstar Games (annonces) » est officiel ET une recherche web
    # généraliste, l'exempter rouvrirait le défaut.
    garder_archives = feed.get("garder_les_archives")
    vieux = 0
    gardees = []
    for entry in retenues:
        date = normalize_date(entry)
        archive = trop_vieux(date)
        if archive and not garder_archives:
            vieux += 1
            continue
        gardees.append((entry, date, archive))

    # Deuxième passage : les liens Google News encore inconnus sont résolus
    # à plusieurs, une bonne fois, au lieu d'une seconde chacun à la suite.
    est_google = "news.google.com" in feed["url"]
    resolus = {}
    if est_google:
        resolus = predecode_links([entry.get("link", "") for entry, _, _ in gardees],
                                  decoded_cache, journal)

    items = []
    for entry, date, archive in gardees:
        title = entry.get("title", "")
        link = entry.get("link", "")
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
        is_official = statut_officiel(real_link, feed)

        # Miniature trouvée directement dans le flux RSS, si présente — pas
        # besoin d'aller la chercher sur la page dans ce cas. La déduction
        # YouTube ne sert qu'en dernier recours : quand le flux donne une
        # vraie vignette, c'est la sienne qui fait foi.
        image = image_du_flux(entry) or vignette_youtube(real_link)

        items.append({
            "title": title,
            "link": real_link,
            # Lien Google News d'origine, conservé uniquement pour éviter de
            # repayer le décodage au prochain run. Absent pour les sources
            # qui ne passent pas par Google News.
            "source_link": source_link,
            "date": date,
            "source": feed["name"],
            # Rapatrié alors qu'il date d'avant la fenêtre habituelle. Il
            # entre bien dans l'historique, mais ne doit déclencher AUCUNE
            # notification : annoncer d'un coup cinquante publications de
            # 2025 ferait vibrer le téléphone cinquante fois pour des
            # nouvelles vieilles d'un an.
            **({"archive": True} if archive else {}),
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


def item_video_archivee(video, feed):
    """Construit l'article d'une vidéo archivée, comme le ferait le flux."""
    lien = f"https://www.youtube.com/watch?v={video['video']}"
    return {
        "title": video["title"],
        "link": lien,
        "source_link": None,
        "date": video["date"],
        "source": feed["name"],
        "official": statut_officiel(lien, feed),
        "rockstarmag": statut_rockstarmag(lien, feed),
        "specialist": feed.get("specialist_source", False),
        "lang": feed.get("lang"),
        "image": vignette_youtube(lien),
        "description": "",
        # Une bande-annonce de 2023 ne s'annonce pas comme une nouveauté.
        # Le drapeau les tient hors des notifications, comme les archives
        # rapatriées des flux — voir `a_annoncer` dans main().
        "archive": True,
    }


def ajoute_videos_archivees(resultats):
    """Verse les vidéos archivées dans le résultat de leur source.

    Versées dans le résultat de la source plutôt qu'importées à part :
    elles empruntent alors exactement le même chemin que tout le reste —
    déduplication par lien puis par titre, statut d'onglet, validation
    avant écriture. Une seconde voie d'entrée serait une seconde occasion
    de diverger.

    Les rejouer à chaque passage est sans effet : la déduplication les
    reconnaît par leur lien, et record_coverage refuse d'ajouter une source
    supplémentaire à un article qui porte déjà le même lien et le même nom
    de source.
    """
    par_id = {f["id"]: f for f in FEEDS}
    ajoutees = 0
    for video in VIDEOS_ARCHIVEES:
        feed = par_id.get(video["source"])
        entree = resultats.get(video["source"])
        if feed is None or entree is None:
            # Source retirée de FEEDS, ou passage où elle n'a pas répondu :
            # on ne fabrique pas un résultat pour une source absente.
            continue
        entree[0].append(item_video_archivee(video, feed))
        ajoutees += 1
    return ajoutees


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


def panne_de_serveur(statut):
    """Le code HTTP annonce-t-il une panne PASSAGÈRE du serveur ?

    La distinction qui compte n'est pas « ai-je reçu un flux » mais « faut-il
    que quelqu'un aille voir ». Un 503 revient tout seul ; une URL qui ne
    sert plus de RSS ne revient jamais sans qu'on la corrige.

    Mesuré sur 400 passages du 25/08 au 04/09/2026 : quatre épisodes où
    Google News a répondu 503 sur ses vingt flux d'un coup, chaque fois
    résorbés au passage suivant sans que personne ne touche à rien. Ils
    étaient pourtant annoncés « cassées », c'est-à-dire du même mot que la
    chaîne YouTube de Rockstar passée en 404 — qui, elle, demande vraiment
    d'aller voir.

    Le 429 est rangé ici avec les 5xx : « trop de requêtes » est le cas le
    plus passager de tous.

    Un 404 ou un 403 restent en dehors : ils désignent l'adresse, pas le
    serveur, et ne se répareront pas d'eux-mêmes.
    """
    return statut is not None and (statut >= 500 or statut == 429)


def merite_reprise(info):
    """La source n'a rien rapporté, pour une raison qui peut ne pas durer.

    Trois cas sont écartés parce qu'un second essai n'y changerait rien :

      - un 304 : le serveur a répondu, il dit simplement que rien n'a bougé ;
      - la source a rapporté des entrées : il n'y a rien à rattraper ;
      - une redirection obtenue SANS validateurs : le second essai est alors
        strictement la même requête, elle renverra la même réponse. C'est
        l'URL dans FEEDS qu'il faut corriger, et la réessayer masquerait le
        déménagement.

    Une redirection obtenue AVEC validateurs, elle, se réessaie — et c'est le
    cas le moins évident des trois.

    IGN alternait « 20 entrées / 0 entrée » un passage sur deux, avec une
    régularité de métronome : la série du 30/08/2026 se lit
    20,0,20,0,20,0,20,0,20,0,20. Ce motif corrèle 12 fois sur 12 avec la
    présence d'un etag enregistré au passage précédent, et la mécanique est
    la nôtre : un passage réussit et enregistre un validateur ; au passage
    suivant le serveur d'IGN, au lieu du 304 attendu, redirige vers autre
    chose qu'un flux ; ce chemin d'échec n'enregistre aucun validateur, donc
    le passage d'après repart sans et réussit. IGN n'était pas en panne un
    passage sur deux — c'est notre propre requête conditionnelle qui la
    cassait, et la reprise, inconditionnelle par construction, la répare.

    Reste ce qui est réellement volatil : panne réseau, 4xx et 5xx, et la
    page HTML servie à la place du flux — le déguisement habituel d'un
    blocage anti-robot.

    Le 404 est délibérément inclus, alors qu'il annonce « cette ressource
    n'existe pas ». Le 30/08/2026 les deux chaînes YouTube de Rockstar ont
    renvoyé 500 puis 404 sur deux passages, encadrés de 61 passages normaux
    et suivis d'un retour à 200 un quart d'heure plus tard : YouTube
    rationnait l'IP du runner. Un 404 isolé ne prouve donc rien ici. Deux
    404 à quelques secondes d'intervalle, si — et c'est exactement ce que
    la reprise transforme en preuve.
    """
    if info.get("not_modified") or info.get("raw_count"):
        return False
    if info.get("redirect") and not info.get("conditionnelle"):
        return False
    return bool(info.get("injoignable") or info.get("not_a_feed")
                or (info.get("http_status") or 0) >= 400)


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

    def traiter(chaine, etat):
        sortie = {}
        for rang, feed in enumerate(chaine):
            if rang:
                # Politesse : jamais deux requêtes consécutives vers le même
                # domaine sans marquer une pause.
                time.sleep(HOST_PAUSE)
            try:
                sortie[feed["id"]] = une(feed, decoded_cache, etat)
            except Exception as e:
                # Une source qui casse de façon imprévue ne doit pas emporter
                # les 34 autres avec elle. En séquentiel, une exception non
                # rattrapée ici arrêtait tout le passage.
                sortie[feed["id"]] = ([], {"raw_count": 0, "not_modified": False},
                                      [f"[{feed['name']}] récupération...",
                                       f"  échec inattendu : {e}"])
        return sortie

    def passe(liste, etat):
        chaines = chaines_par_hote(liste)
        sortie = {}
        with ThreadPoolExecutor(
                max_workers=min(FETCH_WORKERS, len(chaines) or 1)) as executor:
            for bloc in executor.map(traiter, chaines, [etat] * len(chaines)):
                sortie.update(bloc)
        return sortie

    resultats = passe(feeds, http_state)

    # Une seule reprise, après coup, et seulement pour les sources qui
    # n'ont rien rapporté pour une raison passagère.
    #
    # Après coup et pas sur place : au moment où une source échoue, son
    # hôte vient d'être sollicité et c'est le pire instant pour insister.
    # Quand la première passe se termine, il s'est écoulé plus d'une
    # minute — le serveur a eu le temps de respirer, et les 45 sources
    # saines n'ont pas attendu.
    #
    # Sans validateurs : une source qui vient d'échouer est justement
    # l'endroit où l'on veut une réponse complète et sans ambiguïté, pas un
    # « rien n'a changé » portant sur un contenu qu'on n'a pas.
    a_reprendre = [f for f in feeds
                   if merite_reprise((resultats.get(f["id"]) or (None, {}, None))[1])]
    if a_reprendre:
        time.sleep(REPRISE_PAUSE)
        for fid, (items, info, journal) in passe(a_reprendre, None).items():
            premier = resultats.get(fid)
            # Le second essai fait foi : il est plus récent et inconditionnel.
            # Le journal du premier est conservé au-dessus, sinon la reprise
            # effacerait la trace de la panne qu'elle vient de rattraper.
            entete = premier[2] if premier else []
            resultats[fid] = (items, info, entete + [
                "  rien rapporté — seconde tentative, sans requête conditionnelle :"
            ] + journal[1:])
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
    fenetre = fenetre_recente(all_items)

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
                if record_coverage(deja, item) and item.get("link"):
                    # Le lien vient d'être crédité sous « deja » : il doit
                    # être reconnu tout de suite, sinon un flux suivant du
                    # même passage le rapporterait comme un article neuf.
                    links_index.setdefault(item["link"], deja)
                apres = 1 + len(deja.get("extraSources") or [])
                # Strictement au franchissement : un sujet déjà majeur qui
                # gagne une 6e puis une 7e reprise ne réalerte pas.
                #
                # Et jamais sur une archive. Rapatrier d'un coup les vieilles
                # publications de Rockstar fait gagner une reprise à des
                # sujets déjà connus : sans cette garde, une vague de « sujet
                # devenu majeur » partirait par la bande, alors même que les
                # archives elles-mêmes sont importées en silence. Le sujet
                # sera promu normalement à la prochaine VRAIE reprise.
                if (promus is not None and not item.get("archive")
                        and avant < HOT_SOURCE_THRESHOLD <= apres):
                    promus.append(deja)
    return feed_infos, new_counts, inchanges


def fetch_missing_images(items):
    """Récupère en parallèle (jusqu'à IMAGE_WORKERS à la fois) les miniatures manquantes.

    Appelée une seule fois par exécution, sur les seuls articles NOUVEAUX de
    ce passage — jamais sur l'historique. Un article n'y passe donc qu'une
    fois dans sa vie : les articles du fil restés sans miniature ne sont pas
    redemandés, contrairement à ce que laisse croire leur nombre.

    C'est ce que dit la ligne d'appel, et c'est ce que confirme le journal :
    « Miniatures manquantes à récupérer : 1 article(s) » sur un fil qui en
    comptait 76 sans image. Un garde-fou anti-répétition a été ajouté puis
    retiré le 01/09/2026 pour cette raison — il ne pouvait rien économiser,
    et son abandon après 7 jours privait de miniature les archives, qui
    arrivent justement avec une date ancienne.

    En série avec un timeout de 8 s chacun, quelques sites lents suffisaient
    à ajouter plusieurs minutes au temps total, d'où le parallélisme.
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

# Nombre d'entrées lues par flux et par passage. Une source peut le relever
# via `max_entrees` quand son flux en offre davantage et qu'on les veut.
MAX_ENTREES = 30

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

# Durée d'indisponibilité continue avant de signaler une source comme
# tombée. En HEURES, pas en passages — et c'est le point important.
#
# Le seuil valait 6 passages. Un passage n'est pas une unité de temps : sur
# les 158 passages du 25 au 30/08/2026, l'écart entre deux passages a une
# médiane de 30 min, un p90 de 100 min et un pire cas de 4 h 56, parce que
# le `schedule` de GitHub abandonne des exécutions. Six passages valaient
# donc 2 h 36 en médiane mais 19 h dans le pire cas observé, et le 27/08 il
# n'y a eu que 9 passages dans la journée entière. Le même seuil signifiait
# des choses dix fois différentes selon le jour.
#
# 24 h plutôt que 12 : sur toute la période, la panne continue la plus
# longue jamais observée dure 8 h 48 (Kotaku), et 6 passages se sont
# déclenchés 10 fois pour des pannes qui se sont toutes réparées seules.
# 12 h ne laisserait qu'une marge de 1,4× sur un maximum estimé à partir de
# cinq jours seulement — trop mince. 24 h en laisse 2,7×.
#
# Ce que ça coûte : une panne de 8 h passe désormais sans alerte. C'est
# assumé — l'app affiche l'état des sources à chaque passage, sous les
# boutons. Cette alerte-ci n'est pas le tableau de bord, c'est le réveil.
DEAD_SOURCE_HOURS = 24

# Nombre de passages RÉUSSIS D'AFFILÉE avant de considérer qu'une source
# est vraiment rétablie et de remettre son chronomètre à zéro.
#
# Sans ça, une seule réussite suffirait — et une source qui clignote
# échapperait à la détection pour toujours. Ce n'est pas théorique : IGN a
# alterné « 20 entrées / 0 entrée » un passage sur deux pendant des heures
# le 30/08/2026. Elle n'aurait jamais accumulé 24 h de panne continue, ni
# même une heure, tout en étant cassée la moitié du temps.
#
# Deux, et pas trois : deux réussites d'affilée veulent dire que la source
# fonctionne, pas qu'elle a eu de la chance. Une source qui échoue un
# passage sur trois y échappe encore, mais celle-là rapporte vraiment ses
# articles — le passage suivant rattrape ce qui manque.
REPRISE_CONFIRMEE = 2

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


# "muette" et "cassee" décrivent la même conséquence — la source ne
# rapporte rien — et se distinguent seulement par la cause. Tout ce qui
# raisonne sur « cette source ne produit plus » doit donc les traiter
# ensemble : le compteur de passages muets, l'alerte Discord, le bilan du
# run. Les séparer ici ferait repartir le compteur à zéro le jour où une
# source muette devient cassée, et déclencherait une fausse alerte de
# rétablissement.
STATUTS_SANS_ARTICLE = ("muette", "cassee")


def ne_rapporte_rien(source):
    return source.get("status") in STATUTS_SANS_ARTICLE


# Nombre de passages conservés par source pour juger de ce qui est
# "normal". 12 passages = 6 heures : assez pour établir un régime de
# croisière, assez court pour qu'un site qui change de rythme ne traîne pas
# une référence obsolète pendant des jours.
HISTORIQUE_PASSAGES = 12

# Une source est "en baisse" quand ses derniers passages tombent nettement
# sous son propre régime. Seuils volontairement prudents : on cherche une
# chute franche, pas une variation de rythme éditorial.
BAISSE_RATIO = 0.35        # moins de 35 % de son habitude
BAISSE_PASSAGES = 3        # confirmée sur 3 passages de suite
BAISSE_PLANCHER = 8        # et seulement si l'habitude est assez fournie


def _serie(texte):
    """Lit une série stockée sous forme « 20,20,18,0 »."""
    valeurs = []
    for morceau in (texte or "").split(","):
        morceau = morceau.strip()
        if morceau.lstrip("-").isdigit():
            valeurs.append(int(morceau))
    return valeurs


def maj_historique_entrees(feed_infos, precedent):
    """Empile le nombre d'entrées de ce passage, par source.

    Stocké en chaîne « 20,20,18 » et non en liste : feed.json est écrit
    indenté et committé à chaque passage, une liste JSON y mettrait une
    ligne par valeur — 600 lignes de bruit dans chaque diff. Une chaîne
    tient sur une ligne par source et se lit tout aussi bien.

    Les réponses 304 ne sont pas empilées : elles ne disent rien du volume
    du flux, seulement qu'il n'a pas changé. Les compter comme des zéros
    ferait chuter la référence de toutes les sources bien élevées.
    """
    series = {}
    for fid, info in feed_infos.items():
        passe = _serie((precedent or {}).get(fid))
        if not info.get("not_modified"):
            passe.append(int(info.get("raw_count", 0) or 0))
        passe = passe[-HISTORIQUE_PASSAGES:]
        if passe:
            series[fid] = ",".join(str(v) for v in passe)
    return series


def sources_en_baisse(series):
    """Sources dont le volume s'est effondré sans pour autant tomber à zéro.

    Le cas que rien ne détectait : une source qui passe de 30 entrées à 3
    reste « ok » — elle répond, elle renvoie quelque chose. Elle a pourtant
    perdu 90 % de sa couverture, et personne ne le voit avant de comparer
    deux journaux à la main.
    """
    en_baisse = {}
    for fid, texte in (series or {}).items():
        valeurs = _serie(texte)
        if len(valeurs) < BAISSE_PASSAGES + 2:
            continue
        recents = valeurs[-BAISSE_PASSAGES:]
        avant = sorted(valeurs[:-BAISSE_PASSAGES])
        if not avant:
            continue
        habituel = avant[len(avant) // 2]      # médiane, insensible à un pic
        if habituel < BAISSE_PLANCHER:
            continue
        if all(v <= habituel * BAISSE_RATIO for v in recents):
            en_baisse[fid] = {"habituel": habituel, "recents": recents}
    return en_baisse


def _chrono_precedent(valeur, maintenant):
    """Relit une entrée de `sources_silence`, quelle que soit sa forme.

    L'ancienne forme était un simple nombre de passages. Elle ne porte
    aucune date, donc on ne peut pas reconstituer depuis quand la panne
    dure : on repart de maintenant. Ça ne peut que RETARDER une alerte de
    24 h, jamais en déclencher une fausse — le bon sens du choix par
    défaut quand on migre un état persistant.
    """
    if isinstance(valeur, dict):
        return {"depuis": valeur.get("depuis") or maintenant.isoformat(),
                "succes": int(valeur.get("succes") or 0),
                "alertee": bool(valeur.get("alertee"))}
    if valeur:
        return {"depuis": maintenant.isoformat(), "succes": 0, "alertee": False}
    return None


def suivre_sources_muettes(health, silence_precedent, maintenant=None):
    """Suit DEPUIS QUAND chaque source ne rapporte plus rien.

    `sources_health` dit si une source est muette MAINTENANT. Seule la
    durée distingue une panne réelle d'un hoquet : un flux peut répondre
    503 une fois sans être mort. On garde donc, par source et dans
    feed.json faute d'autre stockage persistant, la date du premier échec.

    En heures et non en passages : voir DEAD_SOURCE_HOURS. Un passage n'est
    pas une unité de temps.

    Le chronomètre ne repart à zéro qu'après REPRISE_CONFIRMEE passages
    réussis d'affilée. Une source qui alterne réussite et échec garde donc
    son chronomètre en marche et finit par être signalée, alors qu'une
    seule réussite suffirait à la rendre invisible pour toujours. Tant que
    la reprise n'est pas confirmée, la source reste dans le suivi mais
    n'est plus « muette » pour autant : c'est `sources_health` qui dit
    l'état courant, pas ce dictionnaire.

    Renvoie (suivi, alertes). Une alerte n'est émise qu'au moment où l'état
    BASCULE — à la panne confirmée, puis au retour. Sans ça le robot
    répéterait la même mauvaise nouvelle 48 fois par jour.
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    limite = timedelta(hours=DEAD_SOURCE_HOURS)
    suivi = {}
    alertes = []
    for source in health:
        sid = source["id"]
        chrono = _chrono_precedent((silence_precedent or {}).get(sid), maintenant)

        if ne_rapporte_rien(source):
            if chrono is None:
                chrono = {"depuis": maintenant.isoformat(), "succes": 0,
                          "alertee": False}
            # Un échec annule les réussites accumulées : la reprise doit
            # être consécutive, sinon une source un coup sur deux
            # finirait par cumuler ses bons passages et se croire guérie.
            chrono["succes"] = 0
            debut = feed_store.parse_date_key(chrono["depuis"])
            ecoule = maintenant - debut
            # `alertee` remplace l'égalité stricte d'avant : avec une durée,
            # la condition reste vraie à tous les passages suivants.
            if ecoule >= limite and not chrono["alertee"]:
                chrono["alertee"] = True
                alertes.append({"type": "tombee", "name": source["name"],
                                "heures": round(ecoule.total_seconds() / 3600, 1)})
            suivi[sid] = chrono
        elif chrono is not None:
            chrono["succes"] += 1
            if chrono["succes"] >= REPRISE_CONFIRMEE:
                if chrono["alertee"]:
                    ecoule = maintenant - feed_store.parse_date_key(chrono["depuis"])
                    alertes.append({"type": "retour", "name": source["name"],
                                    "heures": round(ecoule.total_seconds() / 3600, 1)})
                # Rétablie : on cesse de la suivre. Une entrée par source en
                # bonne santé gonflerait feed.json de 50 lignes inutiles à
                # chaque passage.
                continue
            suivi[sid] = chrono
    return suivi, alertes


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


def repare_vignettes_stockees(items):
    """Remplace, dans l'historique, les fausses images des vidéos YouTube.

    Corriger la collecte ne suffit pas : un article déjà connu n'y repasse
    jamais, il est simplement rechargé depuis feed.json. Sans cette passe,
    les 17 vidéos déjà publiées garderaient éternellement l'URL du lecteur
    Flash — c'est-à-dire exactement le symptôme qu'on vient de corriger.

    Même raison d'être que recheck_official_status juste en dessous, et
    même prudence : on ne touche qu'aux images dont on peut PROUVER
    qu'elles n'en sont pas, pas à toutes celles qui n'ont pas d'extension
    dans l'URL. Un CDN qui sert une image depuis un chemin sans .jpg est
    parfaitement légitime.
    """
    reparees = 0
    for item in items:
        image = item.get("image") or ""
        # La signature du défaut : l'ancienne URL du lecteur, /v/{id}.
        if "youtube.com/v/" not in image:
            continue
        vraie = vignette_youtube(image) or vignette_youtube(item.get("link", ""))
        if vraie and vraie != image:
            item["image"] = vraie
            reparees += 1
    if reparees:
        print(f"Correction rétroactive : {reparees} vignette(s) de vidéo "
              f"remise(s) d'aplomb (lecteur Flash -> miniature)")
    return items


# Une source débaptisée laisse ses anciens articles orphelins : ils portent
# un nom qui n'existe plus dans FEEDS, donc ils ne comptent plus pour la
# santé de leur source et l'audit les signale sans fin. Neuf articles
# étaient dans ce cas au 30/08/2026.
#
# Une correspondance EXPLICITE, jamais une devinette : un nom proche ne
# prouve rien. Et le domaine du lien doit confirmer, parce que ce qui compte
# est qui publie — même règle que pour le statut officiel.
SOURCES_RENOMMEES = {
    "RockstarMag.fr": ("RockstarMag", "rockstarmag.fr"),
}


def repare_noms_de_sources(items):
    """Rebranche les articles d'une source débaptisée sur son nom actuel."""
    connus = {feed["name"] for feed in FEEDS}
    corriges = 0
    for item in items:
        cible = SOURCES_RENOMMEES.get(item.get("source"))
        if not cible:
            continue
        nom, domaine = cible
        if nom not in connus:
            continue
        try:
            if domaine not in urlparse(item.get("link") or "").netloc.lower():
                continue
        except Exception:
            continue
        item["source"] = nom
        corriges += 1
    if corriges:
        print(f"Correction rétroactive : {corriges} article(s) rebranché(s) "
              f"sur le nom actuel de leur source")
    return items


def repare_attributions_croisees(items):
    """Retire les « autres sources » qui pointent vers un AUTRE article du fil.

    Une source supplémentaire est censée dire « cette rédaction couvre le
    même sujet ». Quand son lien est le lien PRINCIPAL d'un autre article
    de l'historique, ce n'est pas la même actu vue deux fois : c'est un
    rapprochement erroné, et le lecteur qui clique atterrit sur un autre
    sujet. `audit_donnees.py` les signale depuis le 30/08/2026 sans que
    rien ne vienne les corriger.

    Corrigé rétroactivement parce que rien d'autre ne le fera : un article
    déjà stocké ne repasse jamais par la collecte. Même mécanique que
    repare_vignettes_stockees et recheck_official_status.

    Ne touche QU'AUX liens qui existent par ailleurs comme article à part
    entière — donc à des rapprochements dont on peut prouver qu'ils sont
    faux, jamais à une source supplémentaire légitime.
    """
    principaux = {i.get("link") for i in items if i.get("link")}
    retires = 0
    for item in items:
        autres = item.get("extraSources")
        if not autres:
            continue
        gardes = [s for s in autres
                  if s.get("link") not in principaux or s.get("link") == item.get("link")]
        if len(gardes) != len(autres):
            retires += len(autres) - len(gardes)
            if gardes:
                item["extraSources"] = gardes
            else:
                item.pop("extraSources", None)
    if retires:
        print(f"Correction rétroactive : {retires} « autre(s) source(s) » "
              f"retirée(s) — elles pointaient vers un autre article du fil")
    return items


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


# Le rejeu de la ressemblance sur l'historique compare chaque article aux
# autres : c'est le seul endroit du script dont le coût grandit avec le
# carré de l'historique. Trois limites le tiennent.
FUSION_RETRO_MAX = 3000          # articles les plus récents balayés
FUSION_RETRO_MOT_COMMUN = 0.15   # un mot présent dans plus de 15 % des titres
                                 # ne désigne plus personne
FUSION_RETRO_MOTS_PARTAGES = 2   # deux candidats en partagent au moins deux

# Une fois le nom du jeu et celui du média retirés, certains titres ne
# pèsent plus que deux mots — et deux mots génériques se ressemblent
# forcément. « extended look gta 6 - GamerGen » se réduit à « extended
# look », soit exactement la page officielle de Rockstar une fois nettoyée :
# 1,00 de similarité pour deux pages différentes.
#
# C'est le problème de l'aimant sous une autre forme : un titre qui ne dit
# presque rien ne doit attirer personne. Mesuré sur l'historique, le
# minimum de 3 mots écarte cette fusion-là et AUCUNE autre — la plus courte
# des fusions légitimes en compte 3 (« new screenshots revealed »).
FUSION_MOTS_MINIMUM = 3


def _paires_candidates(comparables):
    """Les paires qui partagent assez de mots rares pour valoir la comparaison.

    Comparer les 1366 titres deux à deux coûtait 92 s — plus que le passage
    entier. Les mots rares font le tri : deux articles qui parlent de la
    même chose partagent « microtransactions » ou « subpoena », deux qui
    n'ont rien à voir ne partagent que « the » et « new ». Mesuré : 7,7 s,
    et aucune paire manquée par rapport au balayage complet.
    """
    index = defaultdict(list)
    for position, titre in enumerate(comparables):
        for mot in set(titre.split()):
            index[mot].append(position)
    plafond = max(2, int(FUSION_RETRO_MOT_COMMUN * len(comparables)))
    partages = defaultdict(int)
    for positions in index.values():
        if len(positions) > plafond:
            continue
        for rang, gauche in enumerate(positions):
            for droite in positions[rang + 1:]:
                partages[(gauche, droite)] += 1
    return sorted(paire for paire, combien in partages.items()
                  if combien >= FUSION_RETRO_MOTS_PARTAGES)


def fusionne_ressemblances_de_titre(items):
    """Fusionne dans l'historique les articles qui se RESSEMBLENT.

    fusionne_doublons_de_titre ne rapproche que les titres strictement
    identiques. Tout ce que la fenêtre glissante de la collecte a laissé
    passer — deux rédactions qui titrent la même actu à quelques mots près,
    séparées par plus de TITLE_SIMILARITY_WINDOW articles pendant un pic —
    restait affiché deux fois. Mesuré au 30/08 : 26 paquets, 28 cartes.

    Rejouer le seuil de 0,75 tel quel sur tout l'historique était refusé
    jusqu'ici, et à raison : sans garde-fou, ça fusionnait aussi trois
    contenus réellement distincts. Deux règles suffisent à les écarter, et
    elles disent POURQUOI le rapprochement serait faux :

    - **jamais deux fois la même source.** Une rédaction ne republie pas le
      même article ; quand elle republie, c'est une suite. C'est ce qui
      sépare les trois vidéos « ON DÉCOUVRE CELA ENSEMBLE ! / (SUITE) /
      (FIN) » de RockstarMag, et « Our GTA 6 Predictions » de « How Our GTA
      6 Predictions Held Up » chez GTA BOOM — le contre-exemple qui servait
      justement d'argument pour ne rien faire.

    - **pas de chaînage.** Un article ne rejoint un paquet que s'il
      ressemble à TOUS ses membres, pas seulement à celui qui l'a attiré.
      Sans ça, la transitivité rassemblait le Trailer 1 et le Trailer 2 :
      chacun ressemble à sa propre reprise, et les deux paquets se
      soudaient par le milieu alors que la comparaison directe est interdite
      par le garde-fou des numéros.

    Le plus ANCIEN est conservé : c'est la publication d'origine, et sa date
    situe l'actualité. Les autres deviennent des sources supplémentaires.
    """
    chronologique = sorted(items, key=lambda i: feed_store.parse_date_key(i.get("date")))
    balayes = chronologique[-FUSION_RETRO_MAX:]
    comparables = [titre_comparable(item.get("title", "")) for item in balayes]

    paquets = {}      # position du gardien -> positions absorbées
    gardien_de = {}   # position absorbée -> position du gardien

    def sources_deja_la(gardien):
        """Le gardien et toutes les rédactions déjà créditées sous lui."""
        noms = {balayes[gardien].get("source")}
        noms.update(autre.get("source")
                    for autre in (balayes[gardien].get("extraSources") or []))
        return noms

    def peut_rejoindre(candidat, gardien):
        # Le candidat se compare au GARDIEN, jamais à un membre absorbé.
        # C'est ce qui interdit le chaînage — chaque membre ressemble
        # directement à l'ancre — et c'est aussi ce qui rend la passe
        # stable : le gardien, lui, ne disparaît jamais du fil, donc le
        # passage suivant reprend exactement la même décision.
        if balayes[candidat].get("source") in sources_deja_la(gardien):
            return False
        # Une vidéo et un article ne sont pas le même contenu, même sous le
        # même titre : l'un se regarde, l'autre se lit. La page du Newswire
        # « Trailer 1 » et le Trailer 1 lui-même restent deux cartes, sinon
        # la vidéo disparaît derrière l'annonce — et pour « An Extended
        # Look », l'annonce du 6 août aurait mangé la vidéo du 27.
        if bool(vignette_youtube(balayes[candidat].get("link") or "")) != \
           bool(vignette_youtube(balayes[gardien].get("link") or "")):
            return False
        if titres_dune_meme_serie(balayes[candidat].get("title", ""),
                                  balayes[gardien].get("title", "")):
            return False
        a, b = comparables[candidat], comparables[gardien]
        if abs(len(a) - len(b)) > 0.4 * max(len(a), len(b)):
            return False
        return SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD

    for ancien, recent in _paires_candidates(comparables):
        if min(len(comparables[ancien].split()),
               len(comparables[recent].split())) < FUSION_MOTS_MINIMUM:
            continue
        # Le plus récent ne rejoint qu'un seul paquet, et n'en amène jamais
        # un autre avec lui : deux paquets ne fusionnent pas entre eux.
        if recent in gardien_de or recent in paquets:
            continue
        gardien = gardien_de.get(ancien, ancien)
        if not peut_rejoindre(recent, gardien):
            continue
        paquets.setdefault(gardien, []).append(recent)
        gardien_de[recent] = gardien

    fusionnes = set()
    for gardien, absorbes in paquets.items():
        for position in absorbes:
            if record_coverage(balayes[gardien], balayes[position]):
                fusionnes.add(id(balayes[position]))
    if not fusionnes:
        return items
    restants = [item for item in items if id(item) not in fusionnes]
    print(f"Correction rétroactive : {len(fusionnes)} article(s) ressemblant(s) "
          f"fusionné(s) dans l'historique ({len(paquets)} paquet(s))")
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
            # "cassee" est un sous-cas de "muette" : le serveur a répondu,
            # mais avec autre chose qu'un flux. Distingué parce que les
            # gestes ne sont pas les mêmes — une source muette peut revenir
            # seule, une URL qui ne renvoie plus de flux demande d'aller
            # voir.
            #
            # Une panne serveur (5xx, 429) reste donc "muette" et jamais
            # "cassee" : elle revient seule, et l'annoncer comme un défaut
            # d'URL envoie chercher un problème qui n'existe pas. Voir
            # panne_de_serveur, qui porte la mesure.
            status = "cassee" if info.get("not_a_feed") else "muette"
        elif days is None or days > SILENT_SOURCE_DAYS:
            status = "tarie"
        else:
            status = "ok"

        health.append({
            "id": feed["id"],
            "name": name,
            "entries_fetched": raw,
            # Code HTTP du dernier passage, pour lire un 403 sans ouvrir
            # les logs du run. None quand la requête n'a pas abouti.
            "http_status": info.get("http_status"),
            # Adresse d'arrivée si le flux a déménagé — l'URL à recopier
            # dans FEEDS pour réparer la source.
            "redirect": info.get("redirect"),
            "not_modified": bool(info.get("not_modified")),
            "new_this_run": new_counts.get(feed["id"], 0),
            "last_article": last.isoformat() if last and last != feed_store.DATE_FLOOR else None,
            "days_since_last_article": days,
            "status": status,
        })

    muettes = [h for h in health if ne_rapporte_rien(h)]
    taries = [h for h in health if h["status"] == "tarie"]
    if muettes:
        print(f"\n⚠ {len(muettes)} source(s) sans aucune entrée :")
        for h in muettes:
            statut = h.get("http_status")
            if h["status"] == "cassee":
                detail = f"réponse HTTP {statut or '?'} mais ce n'est pas un flux"
                if h.get("redirect"):
                    detail += f"\n      redirigé vers : {h['redirect']}"
            elif panne_de_serveur(statut):
                # Dit dans le journal ce que le statut dit déjà dans les
                # données : ce n'est pas la source qui est en cause.
                detail = f"serveur en panne (HTTP {statut}) — repassera seul"
            else:
                detail = "flux vide"
            print(f"    - {h['name']} — {detail}")
    if taries:
        print(f"\n· {len(taries)} source(s) sans article depuis plus de {SILENT_SOURCE_DAYS} jours :")
        for h in taries:
            age = f"{h['days_since_last_article']} j" if h["days_since_last_article"] is not None else "jamais"
            print(f"    - {h['name']} ({age})")
    if not muettes and not taries:
        print(f"\nToutes les sources ont répondu et ont publié dans les {SILENT_SOURCE_DAYS} derniers jours.")

    return health


def main():
    # Chronométré d'un bout à l'autre, y compris le chargement de
    # l'historique : c'est la durée du passage tel que l'app l'annoncera.
    debut_passage = time.monotonic()
    reinitialise_echecs_decodage()
    stored = feed_store.load_feed()
    existing_items = stored.get("items", [])
    # Validateurs HTTP du passage précédent, par source.
    http_state = stored.get("feed_http_state", {}) or {}
    silence_precedent = stored.get("sources_silence", {}) or {}
    entrees_precedentes = stored.get("sources_entries_history", {}) or {}
    is_first_run = len(existing_items) == 0
    print(f"Historique chargé : {len(existing_items)} article(s) déjà connus" + (" (premier lancement)" if is_first_run else ""))
    memorise_suffixes_medias(existing_items)
    existing_items = repare_vignettes_stockees(existing_items)
    existing_items = repare_noms_de_sources(existing_items)
    existing_items = repare_attributions_croisees(existing_items)
    existing_items = recheck_official_status(existing_items)
    existing_items = deduplique_couverture(existing_items)
    existing_items = fusionne_doublons_de_titre(existing_items)
    existing_items = fusionne_ressemblances_de_titre(existing_items)

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
    links_index = index_des_liens(all_items)  # accès O(1) par lien
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
          f"({FETCH_WORKERS} de front, {PER_HOST_LIMIT} max par domaine)")

    # Ce que la seconde tentative a rattrapé. Sans cette ligne la reprise
    # serait invisible : on ne saurait ni qu'elle a servi, ni qu'elle sert
    # trop souvent — et c'est le second cas qui devrait alerter.
    reprises = [(fid, journal) for fid, (_, _, journal) in resultats.items()
                if any("seconde tentative" in l for l in journal)]
    if reprises:
        sauvees = [fid for fid, _ in reprises if resultats[fid][1].get("raw_count")]
        print(f"  ↻ {len(reprises)} source(s) reprise(s), {len(sauvees)} rattrapée(s)"
              + (f" : {', '.join(sauvees)}" if sauvees else ""))
    print()

    ajoutees = ajoute_videos_archivees(resultats)
    if ajoutees:
        print(f"  + {ajoutees} vidéo(s) archivée(s) versée(s) dans leur source "
              f"(trop anciennes pour son flux)")

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
    # (et le temps de dédup) grossir indéfiniment. Les publications de
    # Rockstar sont épargnées, voir cap_items.
    #
    # La taille d'arrivée est lue sur la liste, pas recopiée depuis
    # MAX_HISTORY_SIZE : dans le cas limite où les officiels empêchent de
    # descendre jusqu'au plafond, annoncer le plafond serait un mensonge.
    avant = len(all_items)
    all_items, dropped = feed_store.cap_items(all_items)
    if dropped:
        print(f"Historique plafonné : {avant} -> {len(all_items)} "
              f"({dropped} articles retirés, les plus anciens hors Rockstar)")

    # État des sources, puis cumul des passages muets. L'état seul ne dit
    # que « muette maintenant » ; c'est le cumul qui distingue une panne
    # d'un hoquet, et qui permet de n'alerter qu'une fois.
    sante = build_sources_health(all_items, feed_infos, new_counts)
    silence, alertes_sources = suivre_sources_muettes(sante, silence_precedent)
    for a in alertes_sources:
        if a["type"] == "tombee":
            print(f"⚠️  Source tombée : {a['name']} — ne rapporte plus rien "
                  f"depuis {a['heures']} h")
        else:
            print(f"✅ Source rétablie : {a['name']} — après {a['heures']} h de panne")
    write_source_alerts_file(alertes_sources)

    duree = round(time.monotonic() - debut_passage, 1)
    rates_decodage = echecs_decodage()

    historique_entrees = maj_historique_entrees(feed_infos, entrees_precedentes)
    # Une source déjà muette ou cassée est signalée par ailleurs : la
    # rapporter aussi « en baisse » dirait deux fois la même chose et
    # noierait le signal utile, qui est la source encore vivante mais
    # amputée.
    deja_signalees = {h["id"] for h in sante if ne_rapporte_rien(h)}
    en_baisse = {fid: d for fid, d in sources_en_baisse(historique_entrees).items()
                 if fid not in deja_signalees}
    if en_baisse:
        noms = {f["id"]: f["name"] for f in FEEDS}
        print(f"\n⚠ {len(en_baisse)} source(s) en forte baisse — elles "
              f"répondent, mais rapportent bien moins qu'à l'habitude :")
        for fid, d in en_baisse.items():
            recents = "/".join(str(v) for v in d["recents"])
            print(f"    - {noms.get(fid, fid)} : {recents} entrées "
                  f"contre {d['habituel']} habituellement")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Durée réelle du passage. Publiée pour que l'app puisse dire l'état
        # du robot sans qu'on aille ouvrir GitHub Actions : une durée qui
        # dérive est le premier signe qu'une source traîne.
        "duration_seconds": duree,
        # Décodages Google News ratés. Zéro en régime normal ; tout autre
        # chiffre annonce que gnewsdecoder se dégrade, bien avant que ça se
        # voie dans les articles.
        "decode_failures": rates_decodage,
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
        # Volume des derniers passages, par source, en chaîne compacte.
        # Permet de repérer une source qui se dégrade sans mourir — invisible
        # autrement, puisqu'elle continue de répondre.
        "sources_entries_history": historique_entrees,
        # Sources dont le volume s'est effondré, avec leur régime habituel.
        "sources_declining": {fid: d["habituel"] for fid, d in en_baisse.items()},
        # Validateurs HTTP par source, pour la requête conditionnelle du
        # prochain passage. Conservés dans feed.json faute d'autre stockage
        # persistant : quelques centaines d'octets, négligeables.
        "feed_http_state": {
            fid: {"url": inf.get("url"),
                  "etag": inf.get("etag"),
                  "modified": inf.get("modified")}
            for fid, inf in feed_infos.items()
            if inf.get("etag") or inf.get("modified")
        },
        # Permet à l'app de proposer les notifications push sans que la clé
        # soit codée en dur dans index.html : elle suit la configuration du
        # dépôt, et disparaît si le secret est retiré.
        "vapid_public_key": VAPID_PUBLIC_KEY,
        "items": all_items,
    }

    # Dernier rempart avant publication. `stored` est l'état d'avant ce
    # passage : il sert de repère pour distinguer une purge légitime d'une
    # perte massive. Rien n'est écrit si le contrôle échoue — l'ancien
    # feed.json reste servi et le job passe en échec, ce qui déclenche le
    # signalement au service de surveillance.
    feed_store.valide_avant_ecriture(output, stored)

    nb_allege = feed_store.write_feed_pair(output)
    print(f"Fichier allégé écrit : {nb_allege} article(s) dans docs/feed-recent.json")

    print(f"\nTerminé en {duree} s — {len(newly_added)} nouveau(x), "
          f"{len(all_items)} au total dans docs/feed.json")
    if rates_decodage:
        # Jamais silencieux : un décodage raté ne casse rien tout de suite,
        # mais c'est le signe avant-coureur d'une panne de gnewsdecoder qui
        # toucherait 20 sources sur 50.
        print(f"⚠️  {rates_decodage} décodage(s) Google News en échec — "
              f"liens laissés sur news.google.com")

    # Pas de notification au tout premier lancement : l'historique est vide,
    # donc "tout" serait considéré comme nouveau — ça enverrait des dizaines
    # de messages d'un coup au lieu de rester silencieux jusqu'à la vraie
    # actualité suivante.
    #
    # Les archives rapatriées sont retirées ici, et ici seulement : elles
    # entrent bien dans l'historique et comptent dans « N nouveaux », mais
    # elles ne doivent faire vibrer aucun téléphone. Une notification fait
    # sortir le téléphone à tous les coups ; cinquante publications de 2025
    # annoncées d'un bloc, ce sont cinquante dérangements pour du vieux.
    a_annoncer = [i for i in newly_added if not i.get("archive")]
    archives = len(newly_added) - len(a_annoncer)
    if archives:
        print(f"{archives} archive(s) rapatriée(s) en silence — ajoutées à "
              f"l'historique, aucune notification")
    if not is_first_run:
        write_new_items_file(a_annoncer)
    elif newly_added:
        print(f"Premier lancement : {len(newly_added)} article(s) initiaux, pas de notification envoyée.")


def sonde(url):
    """Interroge une URL et dit ce qu'elle renvoie, sans rien écrire.

    Volontairement un MODE du robot et non un script à côté. La version
    précédente était un fichier séparé (sonde_flux.py) : elle a servi à
    choisir les remplaçants de rss.app, a été supprimée une fois le travail
    fait, et il a fallu la regretter une heure plus tard quand IGN et Kotaku
    se sont tues. Un diagnostic qui vit à côté du code qu'il diagnostique
    finit toujours par en diverger, ou par disparaître.

    Ici, elle emprunte exactement le chemin de récupération du robot :
    collect_feed_items, donc le même agent utilisateur, le même timeout, le
    même filtre, les mêmes verdicts. Elle ne peut pas dire autre chose que
    ce que le robot verra au prochain passage.

        python fetch_feeds.py --sonde https://exemple.com/feed
    """
    # Un feed jetable, sans identifiant réel : rien n'est lu ni écrit dans
    # FEEDS ni dans docs/.
    faux = {"id": "__sonde__", "name": "sonde", "url": url, "official": False}
    items, info, journal = collect_feed_items(faux)

    print(f"\n  {url}")
    for ligne in journal[1:]:
        print("  " + ligne.rstrip())

    statut = info.get("http_status")
    print(f"\n  code HTTP        : {statut if statut is not None else 'aucune réponse'}")
    if info.get("redirect"):
        print(f"  redirigé vers    : {info['redirect']}")
    print(f"  entrées brutes   : {info.get('raw_count', 0)}")
    print(f"  après filtre     : {len(items)}")
    if info.get("not_modified"):
        print("  verdict          : INCHANGÉ (304) — le flux vit, rien de neuf")
    elif info.get("injoignable"):
        print("  verdict          : INJOIGNABLE — aucune réponse HTTP "
              "(DNS, TLS, réseau, ou serveur muet)")
    elif info.get("panne_serveur"):
        print(f"  verdict          : SERVEUR EN PANNE — HTTP {statut}, "
              "passager, rien à corriger dans FEEDS")
    elif info.get("not_a_feed"):
        print("  verdict          : PAS UN FLUX — page de blocage, ou URL qui "
              "ne sert plus de RSS")
    elif info.get("raw_count", 0) == 0:
        print("  verdict          : VIDE — flux valide, mais aucun article")
    else:
        print("  verdict          : OK")

    ages = [jours_depuis(i.get("date")) for i in items]
    ages = [a for a in ages if a is not None]
    if ages:
        print(f"  plus récent      : {min(ages)} j")
    elif items:
        print("  plus récent      : dates illisibles")

    for i in items[:3]:
        print(f"     · {i['title'][:70]}")

    # Quand ce n'est pas un flux, demander à la PAGE où est le sien.
    #
    # Une page web déclare ses flux avec <link rel="alternate"
    # type="application/rss+xml">, c'est le mécanisme que les navigateurs
    # utilisent depuis toujours. Le lire vaut infiniment mieux que d'essayer
    # des adresses au hasard : le 30/08/2026, six candidates sur
    # rockstargames.com ont renvoyé 500 — et un chemin inventé de toutes
    # pièces renvoyait 500 lui aussi. Ce site répond 500 là où un autre
    # répondrait 404, donc essayer des adresses n'apprend rien.
    if info.get("not_a_feed"):
        for trouve in flux_declares(url):
            print(f"  flux déclaré     : {trouve}")

    # Toujours 0 : c'est un rapport, pas un test. Un job rouge parce qu'une
    # adresse candidate n'est pas un flux ferait passer un diagnostic réussi
    # pour une panne. Même principe que audit_donnees.py.
    return 0


def flux_declares(url_page):
    """Adresses de flux que la page déclare elle-même, s'il y en a."""
    try:
        resp = requests.get(url_page, timeout=FETCH_TIMEOUT,
                            headers={"User-Agent": USER_AGENT})
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  [sonde] page illisible pour la découverte : {e}")
        return []
    trouves = []
    for lien in soup.find_all("link", rel=lambda v: v and "alternate" in v):
        type_mime = (lien.get("type") or "").lower()
        if "rss" in type_mime or "atom" in type_mime or "xml" in type_mime:
            href = lien.get("href")
            if href:
                trouves.append(urljoin(url_page, href))
    return trouves


def decris_video_youtube(url):
    """Titre et date de publication d'une vidéo, sans clé d'API.

    Sert à constituer l'archive des vidéos trop anciennes pour le flux :
    celui d'une chaîne YouTube ne porte que 15 entrées et ne pagine pas,
    donc tout ce qui est plus vieux est hors de portée du robot.

    Deux sources, parce qu'aucune ne donne les deux informations :
      - oEmbed, l'API publique de YouTube, donne le titre. Pas la date.
      - la page de la vidéo porte la date dans une balise `datePublished`.

    Et non `videos.xml?video_id=` : ce paramètre n'existe pas, le flux Atom
    n'accepte que channel_id et playlist_id. Essayé le 30/08/2026, HTTP 400
    sur les cinq vidéos.
    """
    trouve = _ID_YOUTUBE.search(url or "")
    if not trouve:
        print(f"  {url} : pas un lien de vidéo YouTube reconnaissable")
        return None
    vid = trouve.group(1)

    titre = date = None
    try:
        r = requests.get("https://www.youtube.com/oembed",
                         params={"url": f"https://www.youtube.com/watch?v={vid}",
                                 "format": "json"},
                         timeout=FETCH_TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        if r.ok:
            titre = (r.json() or {}).get("title")
        else:
            print(f"  [{vid}] oEmbed a répondu {r.status_code}")
    except Exception as e:
        print(f"  [{vid}] oEmbed illisible : {e}")

    # Plusieurs écritures possibles : la page d'une vidéo YouTube ne rend
    # pas le même balisage selon qu'elle sert une fiche complète ou une
    # version allégée. Le premier essai n'a trouvé la date que sur 1 vidéo
    # sur 5 — chercher une seule forme ne suffit pas.
    motifs = (
        r'itemprop="datePublished"[^>]*content="([^"]+)"',
        r'itemprop="uploadDate"[^>]*content="([^"]+)"',
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"uploadDate"\s*:\s*"([^"]+)"',
        r'"publishDate"\s*:\s*"([^"]+)"',
    )
    # PAS de repêchage sur `publishedTimeText` : il porte un texte relatif
    # (« 1 year ago »), inexploitable comme date — et surtout il peut
    # appartenir à une vidéo recommandée dans la marge. Le 30/08/2026 il a
    # rendu « 2 days ago » pour le Trailer 2, sorti en 2025. Une date fausse
    # est pire que pas de date : elle range la vidéo au mauvais endroit du
    # fil et plus rien ne vient la corriger.
    try:
        page = requests.get(f"https://www.youtube.com/watch?v={vid}",
                            timeout=FETCH_TIMEOUT,
                            headers={"User-Agent": USER_AGENT})
        for motif in motifs:
            m = re.search(motif, page.text)
            if m:
                date = m.group(1)
                break
        if not date:
            # Montrer ce que la page contient VRAIMENT plutôt que d'essayer
            # un motif de plus au jugé. Deux tours de devinette coûtent plus
            # cher qu'un tour de mesure.
            print(f"  [{vid}] aucune date ISO — HTTP {page.status_code}, "
                  f"{len(page.text)} caractères. Champs contenant « date » :")
            vus = []
            for cle, valeur in re.findall(
                    r'"([A-Za-z]*[Dd]ate[A-Za-z]*)"\s*:\s*"([^"]{4,40})"',
                    page.text):
                if (cle, valeur) not in vus:
                    vus.append((cle, valeur))
            for cle, valeur in vus[:8]:
                print(f"       {cle} = {valeur}")
            if not vus:
                print("       (aucun)")
    except Exception as e:
        print(f"  [{vid}] page illisible : {e}")

    return {"id": vid, "title": titre, "date": date,
            "link": f"https://www.youtube.com/watch?v={vid}",
            # L'URL complète, pas l'identifiant nu : vignette_youtube attend
            # un lien à analyser. Passer `vid` renvoyait None sans broncher.
            "image": vignette_youtube(f"https://www.youtube.com/watch?v={vid}")}


def decris_videos(urls):
    """Affiche, prêt à recopier, ce qu'il faut pour archiver ces vidéos."""
    trouves = []
    for url in urls:
        infos = decris_video_youtube(url)
        if infos:
            trouves.append(infos)
            print(f"\n  {infos['id']}")
            print(f"    titre : {infos['title'] or '— INTROUVABLE —'}")
            print(f"    date  : {infos['date'] or '— INTROUVABLE —'}")
    print("\n--- à recopier ---")
    print(json.dumps(trouves, ensure_ascii=False, indent=2))
    return 0


def jours_depuis(date_iso):
    """Âge d'un article en jours, ou None si la date est inexploitable."""
    if not date_iso:
        return None
    quand = feed_store.parse_date_key(date_iso)
    if quand == feed_store.DATE_FLOOR:
        return None
    return (datetime.now(timezone.utc) - quand).days


if __name__ == "__main__":
    # Un seul mode alternatif, et il est en lecture seule : sonder une URL
    # pour savoir ce qu'elle renvoie réellement avant de la mettre dans
    # FEEDS, ou pour comprendre pourquoi une source déjà en place se tait.
    if len(sys.argv) > 2 and sys.argv[1] == "--sonde":
        # Plusieurs adresses d'affilée : chercher le flux natif d'un site
        # veut dire essayer cinq ou six adresses candidates, et les lancer
        # une par une depuis Actions coûtait un déclenchement par essai.
        code = 0
        for url in sys.argv[2:]:
            code = sonde(url) or code
        sys.exit(code)
    if len(sys.argv) > 1 and sys.argv[1] == "--sonde":
        print("usage : python fetch_feeds.py --sonde <url> [url...]")
        sys.exit(2)
    # Décrit des vidéos YouTube pour constituer l'archive des anciennes,
    # celles que le flux de la chaîne ne porte plus. Lecture seule.
    if len(sys.argv) > 2 and sys.argv[1] == "--video":
        sys.exit(decris_videos(sys.argv[2:]))
    main()
