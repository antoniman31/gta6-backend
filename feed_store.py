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

# Nombre de sources distinctes à partir duquel un sujet est considéré comme
# une actualité majeure. Un article isolé est en général une reprise ou de
# la supputation ; quatre rédactions dans la foulée signalent un trailer,
# une date ou une annonce.
#
# Vit ici parce que trois modules en dépendent et doivent s'accorder :
# fetch_feeds le publie dans feed.json (l'app y lit le seuil du badge), et
# libelle_recap ci-dessous décide du ton de la notification.
#
# Abaissé de 4 à 3 le 29/08/2026. À 4, le badge était inatteignable : après
# la correction du comptage (record_coverage n'ajoute une source que si elle
# apporte un lien différent), le maximum réellement observé sur 1 299
# articles est de 3 rédactions, et un seuil qu'aucun article n'atteint est
# une fonction morte.
HOT_SOURCE_THRESHOLD = 3

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
    """Plafonne l'historique en retirant les articles NON OFFICIELS les plus anciens.

    À n'appeler que sur une liste DÉJÀ triée par sort_items : sur une liste
    mal triée, la troncature retirerait les mauvais articles.

    Les publications de Rockstar ne se retirent jamais. Ce sont les plus
    anciennes de l'historique — l'annonce, le premier trailer, toute la
    période d'attente — donc exactement celles qu'une troncature par la fin
    emporterait en premier. Ce sont aussi les seules irremplaçables : la
    reprise d'un site d'actu se retrouve ailleurs, le billet officiel non.
    Elles sont 30 sur 1 512 aujourd'hui, la protection ne coûte donc rien
    en place.

    Le plafond reste un vrai plafond : ce qui est épargné à un article
    officiel est pris sur un article ordinaire plus ancien, la liste
    retombe bien à max_size.

    Un seul cas la dépasse : s'il n'y a pas assez d'articles ordinaires à
    retirer, parce que les officiels seuls rempliraient l'historique. La
    liste reste alors plus longue que le plafond — dépasser d'un peu vaut
    mieux que jeter ce qu'on a promis de garder. Inatteignable en pratique
    (il faudrait 20 000 publications de Rockstar), mais une fonction ne
    doit pas dépendre d'un « ça n'arrivera pas ».
    """
    a_retirer = len(items) - max_size
    if a_retirer <= 0:
        return items, 0

    # Parcours du plus ancien au plus récent : les premiers retirés sont
    # bien les plus vieux, l'ordre d'origine est rendu tel quel.
    gardes = []
    retires = 0
    for item in reversed(items):
        if retires < a_retirer and not item.get("official"):
            retires += 1
            continue
        gardes.append(item)
    gardes.reverse()
    return gardes, retires


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


class FeedInvalide(Exception):
    """Le fichier qu'on s'apprête à écrire est abîmé — on n'écrit pas."""


# En dessous de ce seuil on considère que la perte est trop grosse pour être
# une purge légitime. Les deux conditions sont exigées ensemble : un dépôt
# jeune peut perdre un fort pourcentage sur quelques articles sans que ce
# soit grave, et un gros historique peut perdre 50 articles sur 20 000 lors
# d'une déduplication normale.
PERTE_MAX_RATIO = 0.10
PERTE_MAX_ABSOLUE = 50


def valide_avant_ecriture(data, precedent=None):
    """Vérifie qu'un flux est publiable, et lève FeedInvalide sinon.

    Publier un fichier abîmé est pire que ne rien publier : l'app le charge,
    l'affiche, et le prochain passage repart de cet état corrompu. Mieux
    vaut échouer bruyamment — le job passe en échec, le signal de vie part
    en /fail, et l'ancien feed.json reste en place et servi.

    `precedent` : le flux tel qu'il était avant ce passage, pour détecter une
    chute anormale du nombre d'articles. Une déduplication rétroactive en
    retire légitimement quelques-uns ; en perdre un dixième d'un coup est un
    bug, pas un nettoyage.
    """
    items = data.get("items")
    if not isinstance(items, list):
        raise FeedInvalide("« items » absent ou n'est pas une liste")

    sans_lien = sum(1 for i in items if not (isinstance(i, dict) and i.get("link")))
    if sans_lien:
        raise FeedInvalide(f"{sans_lien} article(s) sans lien exploitable")

    sans_titre = sum(1 for i in items if not i.get("title"))
    if sans_titre:
        raise FeedInvalide(f"{sans_titre} article(s) sans titre")

    liens = [i["link"] for i in items]
    doublons = len(liens) - len(set(liens))
    if doublons:
        raise FeedInvalide(f"{doublons} lien(s) en double — la déduplication a échoué")

    avant = len((precedent or {}).get("items") or [])
    perdus = avant - len(items)
    if avant and perdus > PERTE_MAX_ABSOLUE and perdus > avant * PERTE_MAX_RATIO:
        raise FeedInvalide(
            f"{perdus} articles perdus sur {avant} "
            f"({100 * perdus / avant:.0f} %) — trop pour une purge normale")

    return len(items)


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


# ---------------------------------------------------------------------------
# Hygiène des journaux
#
# Vit ici plutôt que dans discord_notify.py ou push_notify.py parce que les
# DEUX en ont besoin et qu'aucun des deux n'est importable par l'autre :
# feed_store est déjà le socle commun sans réseau ni dépendance.
# ---------------------------------------------------------------------------

def masquer_urls(texte, urls):
    """Retire d'un message d'erreur les URL qui sont elles-mêmes des secrets.

    Un webhook Discord et un endpoint de notification push ont ceci de
    commun : l'URL EST le pouvoir d'agir. Quiconque la possède peut publier
    sur le salon, ou notifier l'appareil. C'est pour ça qu'elles sont
    rangées dans des secrets GitHub et pas dans le dépôt.

    Or les bibliothèques réseau recopient l'URL appelée dans leurs messages
    d'erreur, et ces messages finissent dans les journaux d'exécution —
    publics puisque le dépôt l'est :

        HTTPSConnectionPool(host='discord.com', port=443):
        Max retries exceeded with url: /api/webhooks/123/cXXXXXXXX

    GitHub masque la valeur EXACTE d'un secret dans les journaux, pas un
    fragment extrait au milieu : l'URL seule, sortie du JSON ou de la
    variable qui l'entoure, passe au travers. Une simple panne réseau
    suffisait donc à la publier.

    On masque l'URL complète ET son chemin seul, parce que urllib3 n'affiche
    souvent que le chemin (l'hôte figure déjà dans le préfixe du message).
    L'hôte, lui, est conservé : il aide au diagnostic et n'identifie rien.

    Ne lève jamais : cette fonction tourne dans un gestionnaire d'exception,
    elle ne doit pas devenir elle-même la cause d'un plantage.
    """
    for url in urls or ():
        if not isinstance(url, str) or not url:
            continue
        texte = texte.replace(url, "<url masquée>")
        try:
            chemin = urlparse(url).path
        except Exception:
            continue
        # Un chemin d'un seul caractère ("/") remplacerait toutes les barres
        # obliques du message et le rendrait illisible pour rien.
        if len(chemin) > 1:
            texte = texte.replace(chemin, "/<chemin masqué>")
    return texte


# ---------------------------------------------------------------------------
# Libellé des notifications
# ---------------------------------------------------------------------------

def libelle_recap(new_items, promus=()):
    """Le texte du récapitulatif, écrit UNE seule fois.

    Discord et les notifications push doivent annoncer exactement la même
    chose. Deux formulations écrites séparément dérivent au premier
    ajustement — ce dépôt a déjà connu ça avec les listes de sources, où
    trois divergences silencieuses avaient fini par échapper à tout le
    monde. Ici l'identité est garantie par construction : un seul texte,
    deux appelants.

    Volontairement AUCUN titre d'article : un récapitulatif annonce
    combien, pas quoi. Le détail est dans l'app, à un tap de là.

    Deux tons possibles. Quand un sujet est couvert par au moins
    HOT_SOURCE_THRESHOLD rédactions, le libellé bascule en alerte : c'est
    la différence entre être notifié d'une rumeur et être prévenu d'un
    trailer, et c'est la seule information dont on dispose sans lire les
    articles.

    `promus` : les articles DÉJÀ connus qui viennent de franchir le seuil
    parce qu'une rédaction supplémentaire les a repris. Sans eux, une
    couverture qui s'étale sur deux heures resterait muette : chaque
    reprise est un doublon, donc « rien de neuf » à annoncer, alors que
    c'est précisément le moment où l'actu devient majeure.
    """
    lot = [i for i in (new_items or ()) if isinstance(i, dict)]
    n = len(lot)
    officiels = sum(1 for i in lot if i.get("official"))
    compte = f"{n} nouv{'eaux' if n > 1 else 'el'} article{'s' if n > 1 else ''} GTA 6"
    if officiels:
        compte += f" (dont {officiels} officiel{'s' if officiels > 1 else ''} Rockstar)"

    sommet = max(nb_sources_max(lot), nb_sources_max(promus))
    if sommet >= HOT_SOURCE_THRESHOLD:
        alerte = f"🚨 Actu majeure — {sommet} sources sur le même sujet"
        # Un article promu sans aucune nouveauté : annoncer « 0 nouvel
        # article » à côté de l'alerte serait absurde.
        return f"{alerte} · {compte}" if n else alerte
    return f"🎮 {compte}"


def nb_sources_max(new_items):
    """Nombre de rédactions couvrant le sujet le plus repris du lot.

    1 (la source principale) + les sources supplémentaires enregistrées à la
    déduplication. Renvoie 0 sur un lot vide.
    """
    return max((1 + len(i.get("extraSources") or [])
                for i in (new_items or ()) if isinstance(i, dict)), default=0)


def est_actu_majeure(new_items, promus=()):
    """Le lot contient-il un sujet couvert par assez de rédactions ?"""
    return max(nb_sources_max(new_items),
               nb_sources_max(promus)) >= HOT_SOURCE_THRESHOLD
