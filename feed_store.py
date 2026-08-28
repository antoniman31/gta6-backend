"""
Socle partagé de manipulation de l'historique d'articles (docs/feed.json).

Ce module ne fait AUCUN appel réseau et n'importe aucune dépendance externe :
il est donc importable aussi bien par le robot de collecte (fetch_feeds.py)
que par l'outil de fusion appelé depuis le workflow (merge_feed.py), et
testable hors-ligne (test_pipeline.py).

Il centralise les trois règles qui doivent absolument rester identiques
partout, sous peine de corrompre l'historique :
  1. comment on interprète la date d'un article (parse_date_key) ;
  2. dans quel ordre les articles sont rangés (sort_items) ;
  3. combien on en garde (MAX_HISTORY_SIZE / cap_items).
"""

import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# Au-delà de ce nombre d'articles, les plus anciens sont retirés pour que le
# fichier (et le temps de déduplication) n'augmentent pas indéfiniment.
MAX_HISTORY_SIZE = 2000

FEED_PATH = "docs/feed.json"

# Date de repli pour un article dont la date est absente ou illisible : le
# plancher les envoie en fin de liste plutôt que de faire planter le tri.
DATE_FLOOR = datetime(1970, 1, 1, tzinfo=timezone.utc)

_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def parse_date_key(value):
    """Convertit une date d'article en datetime comparable, toujours
    timezone-aware.

    Trois formats coexistent dans l'historique, pour des raisons historiques :
      - ISO 8601 ("2026-08-28T12:45:37+00:00"), le format normal aujourd'hui ;
      - RFC 822 ("Wed, 29 Jul 2026 20:05:05 GMT"), hérité des articles
        collectés avant l'introduction de normalize_date() — le flux ne
        fournissait pas de date structurée et la chaîne brute était stockée
        telle quelle ;
      - vide ou illisible.

    Renvoyer systématiquement un datetime aware est indispensable : trier une
    liste mélangeant datetimes naïfs et aware lève un TypeError, et comparer
    ces dates sous forme de CHAÎNES donne un ordre faux (en ASCII "W" > "2",
    donc toutes les dates RFC 822 remontent avant les dates ISO).
    """
    if not value:
        return DATE_FLOOR

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    text = str(value).strip()
    if not text:
        return DATE_FLOOR

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass

    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    return DATE_FLOOR


def is_iso_date(value):
    """Vrai si la date est déjà au format ISO 8601 attendu."""
    return bool(value) and bool(_ISO_PREFIX.match(str(value)))


def normalize_stored_dates(items):
    """Réécrit en ISO 8601 les dates stockées qui n'y sont pas déjà.

    Même logique que recheck_official_status : l'historique est rechargé tel
    quel à chaque exécution et n'est jamais repassé dans le pipeline de
    collecte, donc une donnée mal formée lors d'une ancienne version y reste
    indéfiniment. Sans cette repasse, les ~214 articles au format RFC 822
    fausseraient le tri et la fenêtre de déduplication pour toujours.

    Idempotente : une fois les dates converties, les exécutions suivantes ne
    corrigent plus rien. Renvoie le nombre d'articles corrigés.
    """
    fixed = 0
    for item in items:
        raw = item.get("date")
        if is_iso_date(raw):
            continue
        parsed = parse_date_key(raw)
        if parsed == DATE_FLOOR:
            # Illisible : on n'invente pas une date, on laisse tel quel — le
            # tri s'appuie de toute façon sur le plancher pour ces cas-là.
            continue
        item["date"] = parsed.isoformat()
        fixed += 1
    return fixed


def sort_items(items):
    """Trie les articles du plus récent au plus ancien, sur la date réelle."""
    return sorted(items, key=lambda item: parse_date_key(item.get("date")), reverse=True)


def cap_items(items, max_size=MAX_HISTORY_SIZE):
    """Plafonne l'historique en retirant les articles les plus anciens.

    À n'appeler que sur une liste DÉJÀ triée par sort_items : sur une liste
    mal triée, la troncature retirerait les mauvais articles.
    """
    if len(items) <= max_size:
        return items, 0
    return items[:max_size], len(items) - max_size


def load_feed(path=FEED_PATH):
    """Charge le fichier de sortie complet (métadonnées + articles).

    Renvoie une structure vide plutôt que de lever si le fichier est absent
    ou corrompu — le robot doit pouvoir repartir même d'un fichier illisible.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"items": []}
        data.setdefault("items", [])
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"items": []}


def load_items(path=FEED_PATH):
    """Charge uniquement la liste des articles déjà connus."""
    return load_feed(path).get("items", [])


def write_feed(data, path=FEED_PATH):
    """Écrit le fichier de sortie.

    L'indentation est conservée volontairement : sur GitHub Pages le fichier
    est servi compressé, donc la retirer ne ferait gagner que ~3 % sur le
    réseau réel (155 Ko -> 151 Ko une fois gzippé), au prix d'un feed.json
    illisible dans l'interface GitHub et de diffs de commit sur une seule
    ligne. Le compromis n'en vaut pas la peine.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
