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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Au-delà de ce nombre d'articles, les plus anciens sont retirés pour que le
# fichier (et le temps de déduplication) n'augmentent pas indéfiniment.
MAX_HISTORY_SIZE = 20000

FEED_PATH = "docs/feed.json"

# Nombre d'articles publiés dans le fichier allégé, que l'app charge en
# premier : ~50 Ko compressés contre ~164 Ko pour l'historique complet.
RECENT_FEED_SIZE = 300

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


# Paramètres de pistage ajoutés aux URL par les régies et les réseaux
# sociaux. Ils ne changent jamais la page servie, mais rendent deux liens
# vers le MÊME article différents pour la déduplication — le même article
# partagé par deux canaux passait donc deux fois.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "utm_name", "utm_reader", "utm_brand", "utm_social", "utm_social-type",
    "fbclid", "gclid", "gbraid", "wbraid", "dclid", "msclkid", "twclid",
    "igshid", "mc_cid", "mc_eid", "ref_src", "ref_url", "spm",
    "at_medium", "at_campaign", "at_custom1", "at_custom2",
    "xtor", "ncid", "cmpid", "_ga", "_gl", "yclid",
}


def canonical_link(url):
    """Nettoie une URL d'article pour servir de clé de déduplication stable.

    Retire les paramètres de pistage et l'ancre (#...), qui désignent une
    position dans la page et jamais un article différent. Tout le reste est
    conservé tel quel : certains sites font transiter l'identifiant de
    l'article par un paramètre (?p=123, ?id=456), les supprimer casserait
    le lien.

    En cas d'URL illisible, renvoie la valeur d'origine — mieux vaut un
    doublon qu'un lien cassé.
    """
    if not url or "://" not in url:
        return url
    try:
        parts = urlparse(url)
        gardes = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                  if k.lower() not in TRACKING_PARAMS]
        return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params,
                           urlencode(gardes), ""))
    except Exception:
        return url


def canonicalize_stored_links(items):
    """Applique canonical_link à l'historique déjà stocké.

    Même raison que normalize_stored_dates : l'historique n'est jamais
    repassé dans le pipeline de collecte, donc les liens engrangés avant
    cette règle garderaient leurs paramètres de pistage indéfiniment — et
    continueraient de faire doublon avec leurs équivalents propres.

    Les articles qui deviennent identiques après nettoyage sont fusionnés,
    le premier rencontré étant conservé. Renvoie (liste nettoyée, liens
    modifiés, doublons retirés).
    """
    nettoyes = 0
    vus = set()
    resultat = []
    for item in items:
        origine = item.get("link", "")
        propre = canonical_link(origine)
        if propre != origine:
            item["link"] = propre
            nettoyes += 1
        if propre in vus:
            continue
        vus.add(propre)
        resultat.append(item)
    return resultat, nettoyes, len(items) - len(resultat)


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


def recent_path_for(path):
    """Chemin du fichier allégé correspondant à un fichier de flux."""
    if path.endswith("feed.json"):
        return path[:-len("feed.json")] + "feed-recent.json"
    return path + ".recent.json"


def write_feed_pair(data, path=FEED_PATH):
    """Écrit le fichier complet ET sa version allégée.

    Les deux doivent toujours être générés ensemble : après une fusion
    consécutive à un conflit de push, republier le fichier complet sans
    régénérer l'allégé laisserait l'app afficher un état périmé sans que
    rien ne le signale.
    """
    write_feed(data, path)

    allege = dict(data)
    items = data.get("items", [])
    # Tri défensif : prendre les N premiers de la liste telle quelle
    # supposerait qu'elle est déjà triée. C'est vrai des appelants
    # actuels, mais un fichier allégé qui contiendrait silencieusement des
    # articles au hasard serait invisible à l'œil nu — l'invariant est donc
    # garanti ici plutôt que documenté. Sans effet si la liste est triée.
    allege["items"] = sort_items(items)[:RECENT_FEED_SIZE]
    # Prévient l'app qu'elle ne voit pas tout : sans ce drapeau, une
    # recherche renverrait silencieusement des résultats incomplets.
    allege["partial"] = len(items) > RECENT_FEED_SIZE
    allege["full_url"] = os.path.basename(path)
    write_feed(allege, recent_path_for(path))
    return len(allege["items"])


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
