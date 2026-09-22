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
  3. combien on en garde (MAX_HISTORY_DAYS et ses bornes / cap_items).
"""

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Combien d'articles on garde — exprimé en JOURS, borné en nombre.
#
# Ce que l'historique complet sert vraiment : l'ouverture de l'app lit
# feed-recent.json (300 articles) ; le fichier complet n'est téléchargé que
# pour la RECHERCHE et le bouton « Tout charger ». Ces réglages décident donc
# de la profondeur de recherche, pas de ce qui s'affiche.
#
# Trois étapes, et chacune répare la précédente :
#
#   20000  (jusqu'au 18/09/2026) — pas un plafond, une absence de plafond. À
#          74 articles/jour il n'aurait mordu que dans huit mois, et feed.json
#          aurait alors pesé 15 Mo réécrits vingt-quatre fois par jour.
#    1500  (18/09) — un vrai plafond, mais un nombre FIXE face à un volume
#          variable. 1500 vaut dix-sept jours à 74/jour… et six jours à
#          249/jour, le régime d'une journée d'annonce comme le 17/09.
#   jours  (22/09) — l'intention est une profondeur, pas un nombre. Ce qui
#          suit vise MAX_HISTORY_DAYS et laisse le nombre flotter.
#
# Les deux bornes ne sont pas décoratives. Sans PLANCHER, une semaine creuse
# réduirait l'historique à peau de chagrin ; sans PLAFOND, le jour de la
# sortie du jeu ramènerait exactement le problème qu'on vient de régler — à
# 1000 articles/jour, trente jours feraient 30 000 articles et 27 Mo.
#
# Le plafond dur est un arbitrage sur la recherche : à 4000 articles le
# fichier pèse ~3,7 Mo, soit une dizaine de secondes de téléchargement sur un
# téléphone la première fois qu'on cherche.
#
# QUINZE JOURS -> TRENTE, le 22/09/2026, quand le projet est devenu une veille
# durable et non un compte à rebours. La mesure a dit que c'était bon marché,
# contre mon attente :
#
#   - l'ouverture de l'app ne paie rien : elle lit feed-recent.json, dont la
#     taille est fixée par RECENT_FEED_SIZE et ne bouge pas d'un octet ;
#   - le dépôt git ne paie presque rien : un commit du robot ne pèse pas le
#     fichier mais son DELTA, et le delta suit le nombre d'articles qui ont
#     changé, pas la profondeur. Mesuré : 1028 passages, 7,2 Mo de pack ;
#   - la déduplication ne paie rien non plus : ses deux passes sont déjà
#     bornées indépendamment de l'historique (FENETRE_MAX = 5000 et
#     FUSION_RETRO_MAX = 3000 dans fetch_feeds).
#
# Reste le seul vrai coût : le téléchargement du fichier complet, à la
# recherche et au bouton « Tout charger ». ~1,5 Mo devient ~2,6 Mo bruts, 465
# Ko devient ~800 Ko compressés — sur une action explicite, pas à chaque
# ouverture.
#
# Trente jours et non quatre-vingt-dix : au-delà, c'est un problème d'ARCHIVE
# et non de plafond. Chercher ce qui s'est dit d'un DLC six mois plus tôt
# demande un découpage de l'historique en fichiers qu'on n'interroge qu'au
# besoin, pas un fichier unique qu'on télécharge en entier. Voir le README.
MAX_HISTORY_DAYS = 30
MIN_HISTORY_SIZE = 1500
MAX_HISTORY_SIZE = 4000

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


def item_protege(item):
    """Un article que le plafond ne retirera jamais, quel que soit son âge.

    Trois familles, et la même raison pour les trois : ce qu'on ne retrouve
    pas ailleurs. Une reprise d'actualité se re-trouve sur dix sites ; ces
    trois-là, non.

      - « official » : les publications de Rockstar. Ce sont les PLUS
        ANCIENNES de l'historique — l'annonce, le premier trailer, toute la
        période d'attente — donc exactement celles qu'une troncature par la
        fin emporterait en premier.
      - « rockstarmag » : la source française de référence du fil, suivie
        pour elle-même et pas pour sa reprise d'une dépêche.
      - une actualité MAJEURE (HOT_SOURCE_THRESHOLD rédactions ou plus) :
        quand trois rédactions couvrent le même sujet, c'est un évènement.
        Les grosses journées sont précisément celles qu'un plafond au
        compte emporte en premier, puisqu'elles produisent le plus de
        volume.

    Elles sont 116 sur 3 202 au 18/09/2026, soit 3,6 % : les protéger pour
    toujours ne coûte presque rien en place.
    """
    if item.get("official") or item.get("rockstarmag"):
        return True
    return 1 + len(item.get("extraSources") or []) >= HOT_SOURCE_THRESHOLD


# Sur combien de jours RÉVOLUS on mesure le débit. Sept : assez pour qu'un
# jour creux ou un jour d'annonce ne décide pas à lui seul, assez court pour
# suivre un changement de régime en une semaine.
#
# Le jour EN COURS est exclu. Il est incomplet par construction — à midi il
# ne porte que la moitié de ses articles — et le compter tirerait la mesure
# vers le bas à chaque passage de la matinée.
JOURS_MESURE_DEBIT = 7


def debit_quotidien(items, maintenant=None):
    """Articles ordinaires par jour, en MÉDIANE sur les jours révolus.

    La médiane et non la moyenne : le 18/09/2026 a produit 300 articles
    quand la médiane de la semaine est à 94. Une moyenne aurait laissé ce
    seul jour élargir la fenêtre d'un tiers, puis la laisser rétrécir dès
    qu'il sort de la mesure — l'historique respirerait au rythme des
    journées d'annonce au lieu de suivre le régime de fond.
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    par_jour = {}
    for item in items:
        if item_protege(item):
            continue
        quand = parse_date_key(item.get("date"))
        if quand == DATE_FLOOR:
            continue
        age = (maintenant - quand).days
        if 1 <= age <= JOURS_MESURE_DEBIT:
            par_jour[age] = par_jour.get(age, 0) + 1
    if not par_jour:
        return 0.0
    comptes = sorted(par_jour.get(j, 0) for j in range(1, JOURS_MESURE_DEBIT + 1))
    milieu = len(comptes) // 2
    if len(comptes) % 2:
        return float(comptes[milieu])
    return (comptes[milieu - 1] + comptes[milieu]) / 2


def taille_historique_visee(items, maintenant=None):
    """Combien d'articles garder pour couvrir MAX_HISTORY_DAYS, bornes comprises.

    Le calcul part du DÉBIT observé — combien d'articles arrivent par jour —
    et le multiplie par la profondeur voulue. Ce n'est pas un détail
    d'implémentation, c'est la correction d'un défaut de conception.

    LA VERSION PRÉCÉDENTE COMPTAIT LES ARTICLES DÉJÀ STOCKÉS qui tombaient
    dans la fenêtre. C'est circulaire : la liste stockée est elle-même
    plafonnée par le nombre que cette fonction rend. Conséquence mesurée le
    22/09/2026 sur le vrai historique, 45 jours de robot simulés :

        débit        gardés / profondeur obtenue
        40/jour      1500 / 33 j      le plancher commande
        94/jour      1500 / 14 j      <- le régime réel, et 14 n'est pas 30
        142/jour     1500 /  9 j
        200/jour     4000 / 19 j      la visée décolle enfin

    Le seuil de bascule se calcule : la visée ne dépasse 1500 que si
    l'apport d'un passage suffit à faire franchir ce nombre aux articles
    ORDINAIRES stockés, soit un débit supérieur au nombre de protégés — 142
    aujourd'hui. En dessous, la fenêtre est gelée à MIN_HISTORY_SIZE et
    MAX_HISTORY_DAYS n'a AUCUN effet, quelle que soit sa valeur. Le passage
    de 15 à 30 jours, le matin même, n'avait donc rien changé du tout.

    Le débit, lui, ne dépend pas de ce qu'on garde : il se lit sur les jours
    révolus, qui sont complets quoi qu'on élague. La même simulation avec ce
    calcul-ci donne 28 jours à 94/jour et 27 à 142/jour, et retombe bien à
    19 puis 7 jours aux gros débits — le plafond dur reprend la main, comme
    voulu.

    DEUX ESTIMATIONS, ET ON GARDE LA PLUS GÉNÉREUSE. Le débit dit ce que la
    fenêtre DEVRAIT contenir ; le comptage dit ce qu'elle contient DÉJÀ.
    Prendre le maximum, c'est refuser les deux erreurs opposées :

      - le comptage seul ne peut pas faire grandir la fenêtre, pour la
        raison circulaire ci-dessus ;
      - le débit seul jetterait ce qu'on a la place de garder. Un afflux
        massif sur un historique jeune n'a pas encore de jour révolu à
        mesurer : le débit vaut alors zéro, et il faudrait couper au
        plancher 4050 articles tous publiés dans la fenêtre. C'est un test
        de la suite qui l'a dit, pas une relecture.

    On compte les articles ORDINAIRES : les protégés ne sont jamais retirés,
    les inclure ferait rétrécir la fenêtre à mesure qu'ils s'accumulent.
    Une date illisible (DATE_FLOOR) ne compte pas non plus.
    """
    maintenant = maintenant or datetime.now(timezone.utc)

    par_le_debit = int(debit_quotidien(items, maintenant) * MAX_HISTORY_DAYS)

    limite = maintenant - timedelta(days=MAX_HISTORY_DAYS)
    deja_la = 0
    for item in items:
        if item_protege(item):
            continue
        quand = parse_date_key(item.get("date"))
        if quand == DATE_FLOOR:
            continue
        if quand >= limite:
            deja_la += 1

    vise = max(par_le_debit, deja_la)
    return max(MIN_HISTORY_SIZE, min(MAX_HISTORY_SIZE, vise))


def plancher_de_retention(items, maintenant=None):
    """La date sous laquelle un article ORDINAIRE serait élagué dès son entrée.

    Sert à refuser en amont ce que cap_items retirerait de toute façon à la
    fin du même passage. Le plancher vient donc du MÊME calcul que le
    plafond — pas d'une constante parallèle qui divergerait au premier
    ajustement.

    Renvoie None quand il n'y a rien à refuser : historique pas encore
    plein, ou plus aucun article ordinaire.

    Attention au piège, rencontré en écrivant ceci : interroger cap_items
    ne marche PAS. L'historique stocké sort déjà plafonné du passage
    précédent, donc cap_items n'a plus rien à retirer et le plancher serait
    toujours None — le filtre ne se déclencherait jamais. Ce qu'il faut
    regarder, c'est si l'historique est PLEIN.

    Pourquoi c'est sûr. Quand il est plein, ajouter un article plus ancien
    que le plus vieux des ordinaires conservés le condamne : le plafond
    retirera le plus ancien, et ce sera lui. Le refuser à l'entrée donne
    donc exactement le même fichier publié — ce n'est pas une estimation
    prudente, c'est une équivalence.

    Une date illisible ne fixe jamais le plancher : elle vaut DATE_FLOOR,
    donc elle l'écraserait à 1970 et le filtre ne refuserait plus rien.
    Même raison qu'au calcul de la fenêtre.
    """
    if len(items) < taille_historique_visee(items):
        return None
    dates = [parse_date_key(i.get("date")) for i in items
             if not item_protege(i)]
    dates = [d for d in dates if d != DATE_FLOOR]
    return min(dates) if dates else None


def cap_items(items, max_size=None):
    """Plafonne l'historique en retirant les articles NON PROTÉGÉS les plus anciens.

    À n'appeler que sur une liste DÉJÀ triée par sort_items : sur une liste
    mal triée, la troncature retirerait les mauvais articles.

    Ce qui est protégé, et pourquoi : voir item_protege.

    Le plafond reste un vrai plafond : ce qui est épargné à un article
    protégé est pris sur un article ordinaire plus ancien, la liste retombe
    bien à max_size.

    Un seul cas la dépasse : s'il n'y a pas assez d'articles ordinaires à
    retirer, parce que les protégés seuls rempliraient l'historique. La
    liste reste alors plus longue que le plafond — dépasser d'un peu vaut
    mieux que jeter ce qu'on a promis de garder. Loin d'être atteint
    aujourd'hui (116 protégés pour un plafond de 1500), mais une fonction ne
    doit pas dépendre d'un « ça n'arrivera pas ».
    """
    if max_size is None:
        max_size = taille_historique_visee(items)
    a_retirer = len(items) - max_size
    if a_retirer <= 0:
        return items, 0

    # Parcours du plus ancien au plus récent : les premiers retirés sont
    # bien les plus vieux, l'ordre d'origine est rendu tel quel.
    gardes = []
    retires = 0
    for item in reversed(items):
        if retires < a_retirer and not item_protege(item):
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
    # Les aperçus de la nuit ne servent qu'à discord_notify, qui lit le
    # fichier COMPLET. L'app, elle, retélécharge celui-ci à chaque ouverture :
    # y laisser cinq articles en double se paierait à chaque visite, pour
    # quelque chose qu'aucune ligne de docs/index.html ne lit.
    #
    # dict() et non une écriture directe : `allege` est une copie de surface
    # de `data`, modifier son attente_recap modifierait aussi celui du fichier
    # complet — donc effacerait l'arriéré qu'on est en train de reporter.
    attente = allege.get("attente_recap")
    if isinstance(attente, dict) and "apercus" in attente:
        sans = dict(attente)
        sans.pop("apercus", None)
        allege["attente_recap"] = sans
    write_feed(allege, recent_path_for(path))
    return len(allege["items"])


# ---------------------------------------------------------------------------
# Fichiers de liaison entre le robot et les notifications
# ---------------------------------------------------------------------------
# fetch_feeds.py dépose ce qu'il a trouvé dans des fichiers hors du dépôt, et
# discord_notify.py comme push_notify.py les relisent. Ces deux lecteurs
# vivaient en double, à l'identique, dans les deux modules — la duplication
# exacte que check_sources_sync.py existe pour surveiller ailleurs, et qui
# avait déjà dérivé en silence sur la liste des sources fin août.
#
# Ils sont ici parce que les deux canaux doivent lire EXACTEMENT la même
# chose : deux copies qui divergeraient feraient annoncer deux nombres
# différents pour le même passage.


def lire_liste(path):
    """Lit un fichier JSON contenant une liste, ou renvoie [].

    Tolérante par construction : un fichier absent est le cas NORMAL (le
    robot ne l'écrit que s'il a quelque chose à dire), et un fichier abîmé
    ne doit pas empêcher la notification de partir.
    """
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def lire_totaux_recap(variable="RECAP_TOTALS_FILE"):
    """Ce que le récapitulatif doit annoncer, déposé par fetch_feeds.py.

    Contient les comptes du passage PLUS ceux mis de côté pendant la pause
    nocturne : les articles de la nuit ont été publiés au fil de l'eau, donc
    à 5h ils ne sont plus « nouveaux » et la liste ne les contient plus.
    Sans ce fichier — lancement local, version antérieure — on retombe sur
    le comptage direct de la liste, qui reste juste hors pause.

    Renvoie None à la moindre anomalie plutôt qu'un tuple partiel : un
    compte à moitié lu ferait annoncer un nombre faux, ce qui est pire que
    de retomber sur le comptage direct.
    """
    chemin = os.environ.get(variable, "")
    if not chemin:
        return None
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return (int(data.get("articles", 0)),
                int(data.get("officiels", 0)),
                int(data.get("sommet", 0)))
    except (TypeError, ValueError):
        return None


def lire_apercus_recap(variable="RECAP_TOTALS_FILE"):
    """Les articles que le récapitulatif peut citer, déposés par le robot.

    Lecture SÉPARÉE de lire_totaux_recap, qui rend trois entiers et est
    appelée aussi bien par Discord que par les notifications push. Élargir son
    tuple aurait cassé le second pour un besoin qui ne concerne que le
    premier.

    Rend [] à la moindre anomalie : un récapitulatif sans liens reste un
    récapitulatif juste, un récapitulatif avec des liens inventés ne l'est
    pas.
    """
    chemin = os.environ.get(variable, "")
    if not chemin:
        return []
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    return apercus_assainis(data.get("apercus"))


class FeedInvalide(Exception):
    """Le fichier qu'on s'apprête à écrire est abîmé — on n'écrit pas."""


# En dessous de ce seuil on considère que la perte est trop grosse pour être
# une purge légitime. Les deux conditions sont exigées ensemble : un dépôt
# jeune peut perdre un fort pourcentage sur quelques articles sans que ce
# soit grave, et un gros historique peut perdre 50 articles sur 20 000 lors
# d'une déduplication normale.
PERTE_MAX_RATIO = 0.10
PERTE_MAX_ABSOLUE = 50


def valide_avant_ecriture(data, precedent=None, elagues=0):
    """Vérifie qu'un flux est publiable, et lève FeedInvalide sinon.

    Publier un fichier abîmé est pire que ne rien publier : l'app le charge,
    l'affiche, et le prochain passage repart de cet état corrompu. Mieux
    vaut échouer bruyamment — le job passe en échec, le signal de vie part
    en /fail, et l'ancien feed.json reste en place et servi.

    `precedent` : le flux tel qu'il était avant ce passage, pour détecter une
    chute anormale du nombre d'articles. Une déduplication rétroactive en
    retire légitimement quelques-uns ; en perdre un dixième d'un coup est un
    bug, pas un nettoyage.

    `elagues` : le nombre d'articles que cap_items a retirés VOLONTAIREMENT.
    Sans ce paramètre, abaisser le plafond ferait échouer le premier passage
    qui l'applique — au 18/09/2026, l'historique tombe de 3202 à ~1600, soit
    la moitié : très au-delà du seuil de perte. Or ce n'est pas une perte,
    c'est la purge demandée, et cap_items en rend le compte exact.

    Le garde-fou n'en est pas affaibli : on ne soustrait que ce qui a été
    délibérément retiré et compté. Un article qui disparaît EN PLUS de
    l'élagage fait toujours monter le total et déclenche l'alerte.
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
    perdus = avant - len(items) - max(0, elagues)
    if avant and perdus > PERTE_MAX_ABSOLUE and perdus > avant * PERTE_MAX_RATIO:
        raise FeedInvalide(
            f"{perdus} articles perdus sur {avant} "
            f"({100 * perdus / avant:.0f} %) hors élagage volontaire "
            f"({max(0, elagues)}) — trop pour une purge normale")

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
# L'archive mensuelle
#
# feed.json est une FENÊTRE : il garde MAX_HISTORY_DAYS de profondeur et
# jette le reste. Tant que le projet visait le 19/11/2026, jeter allait de
# soi — personne ne cherche l'actualité d'il y a six mois d'un jeu qui n'est
# pas sorti. Le projet est devenu une veille durable le 22/09/2026, et ce
# geste est devenu une perte : chercher ce qui s'est dit d'un DLC six mois
# plus tôt ne rentrera jamais dans une fenêtre, quel que soit son réglage.
#
# L'archive est l'autre moitié : un fichier par MOIS, sous docs/archives/,
# plus un index. Ce qui sort de la fenêtre y est déjà, et y reste.
#
# Pourquoi le mois, et pas un seul gros fichier ni un fichier par jour :
#
#   - un mois RÉVOLU ne change plus jamais. Son fichier est écrit une fois,
#     puis plus aucun commit du robot ne le touche — le dépôt ne grossit
#     donc pas de son poids à chaque passage, et le navigateur peut le
#     garder en cache indéfiniment. C'est toute la différence avec un
#     fichier unique, qui serait réécrit vingt-quatre fois par jour ;
#   - un fichier par jour ferait des centaines de requêtes pour couvrir une
#     recherche d'un an. Le mois est le grain où l'on demande « et en
#     mars ? » sans avoir à demander trente fois.
#
# CE N'EST PAS UN ÉLAGAGE DÉGUISÉ. On n'archive pas « ce qui va être jeté »,
# on archive TOUT ce qui est publié, à chaque passage. La différence compte :
#
#   - si l'archive rate un article une fois (passage interrompu, conflit de
#     push, bogue), le passage suivant le remet — tant qu'il est encore dans
#     la fenêtre. N'archiver que les élagués n'offrirait aucun rattrapage :
#     l'article serait perdu des deux côtés, et rien ne le dirait ;
#   - la première exécution remplit l'archive avec l'historique entier au
#     lieu de partir de zéro.
#
# Le prix est de réécrire le fichier du mois EN COURS à chaque passage. Les
# autres ne bougent pas. C'est exactement le comportement voulu.
# ---------------------------------------------------------------------------

ARCHIVE_DIR = "docs/archives"

# Au-delà, le mois est coupé en tranches numérotées. Un mois ordinaire
# (~94 articles/jour) en fait environ 2800 et tient en une tranche. Le mois
# de la sortie n'en fera pas 2800 mais plusieurs dizaines de milliers, et un
# fichier de 25 Mo ne se télécharge pas depuis un téléphone.
#
# Les tranches existent DÈS MAINTENANT, avant d'en avoir besoin, et l'index
# donne toujours une LISTE de fichiers même quand elle n'en contient qu'un.
# Les ajouter en novembre voudrait dire changer le format publié pendant le
# mois le plus chargé de la vie du projet — soit le pire moment possible,
# exactement l'argument qui a fait avancer le plancher de rétention au 22/09
# plutôt qu'en octobre.
ARCHIVE_MAX_PAR_FICHIER = 2500


def mois_de(item):
    """Le mois d'un article, « YYYY-MM », d'après sa date."""
    return parse_date_key(item.get("date")).strftime("%Y-%m")


def _nom_tranche(mois, rang):
    """« 2026-09.json » pour la première tranche, « 2026-09.2.json » ensuite.

    La première tranche garde le nom nu pour que l'adresse la plus évidente
    — celle qu'on tape à la main pour vérifier — soit la bonne.
    """
    return f"{mois}.json" if rang == 0 else f"{mois}.{rang + 1}.json"


def tranches_du_mois(mois, repertoire=ARCHIVE_DIR):
    """Les fichiers existants d'un mois, dans l'ordre."""
    noms = []
    rang = 0
    while True:
        chemin = os.path.join(repertoire, _nom_tranche(mois, rang))
        if not os.path.exists(chemin):
            return noms
        noms.append(chemin)
        rang += 1


def _lire_tranches(mois, repertoire=ARCHIVE_DIR):
    """Les articles d'un mois, ET les tranches qu'on n'a pas su lire.

    La distinction n'est pas cosmétique. Une tranche illisible veut dire
    « je ne sais pas ce qu'il y avait dedans », ce qui n'est pas la même
    chose que « il n'y avait rien » — et confondre les deux fait perdre des
    articles pour de bon, voir `archiver`.
    """
    items, illisibles = [], []
    for chemin in tranches_du_mois(mois, repertoire):
        try:
            with open(chemin, encoding="utf-8") as f:
                items.extend(json.load(f).get("items") or [])
        except (OSError, ValueError):
            illisibles.append(os.path.basename(chemin))
    return items, illisibles


def lire_mois(mois, repertoire=ARCHIVE_DIR):
    """Tous les articles archivés d'un mois, toutes tranches confondues.

    Au mieux : une tranche illisible est sautée plutôt que de faire échouer
    la lecture entière. C'est ce que veut un LECTEUR — l'app, l'audit —
    parce que la moitié d'un mois vaut mieux que rien. Ce n'est PAS ce que
    veut celui qui va réécrire : lui doit passer par `_lire_tranches`.
    """
    return _lire_tranches(mois, repertoire)[0]


def archiver(items, repertoire=ARCHIVE_DIR, maintenant=None):
    """Range `items` dans les fichiers de leur mois. Idempotent.

    Rend un dictionnaire {mois: nombre d'articles archivés}, limité aux mois
    RÉELLEMENT réécrits — un mois dont rien n'a changé n'est pas touché, et
    c'est ce qui garde les commits du robot légers.

    La fusion se fait par lien, l'article de `items` gagne : c'est la version
    la plus à jour, celle qui a pu gagner des sources supplémentaires ou une
    miniature depuis son archivage.
    """
    par_mois = {}
    for item in items:
        if not item.get("link"):
            continue
        par_mois.setdefault(mois_de(item), []).append(item)

    ecrits = {}
    for mois, neufs in par_mois.items():
        existants, illisibles = _lire_tranches(mois, repertoire)

        # LE GARDE-FOU DE L'ARCHIVE. Une tranche qu'on ne sait pas lire n'est
        # pas une tranche vide. Réécrire le mois à partir de ce qu'on a pu
        # lire remplacerait son contenu par un sous-ensemble, et l'archive
        # étant le dernier endroit où vivent les articles sortis de la
        # fenêtre, ils disparaîtraient pour de bon.
        #
        # Mesuré avant le correctif, sur un mois de 6000 articles en trois
        # tranches : une seule tranche tronquée, et le passage suivant
        # emportait 2500 articles définitivement, sans un mot. Exactement la
        # perte que l'archive existe pour empêcher.
        #
        # On ne touche donc pas au mois. Perdre la mise à jour d'un passage
        # est sans conséquence — le passage suivant la refera, tant que les
        # articles sont encore dans la fenêtre — alors que réécrire est
        # irréversible. L'audit, lui, le signalera comme grave.
        if illisibles:
            print(f"  [archive] {mois} NON réécrit : tranche(s) illisible(s) "
                  f"({', '.join(illisibles)}). Réécrire le mois à partir du "
                  f"reste effacerait ce qu'elles contenaient.")
            continue

        fusion = {i["link"]: i for i in existants if i.get("link")}
        avant = len(fusion)
        inchange = all(fusion.get(i["link"]) == i for i in neufs)
        if inchange and len(neufs) <= avant:
            # Rien de neuf et rien de modifié : ne pas réécrire, pour ne pas
            # produire un commit qui ne dit rien.
            continue
        fusion.update({i["link"]: i for i in neufs})
        ranges = sort_items(list(fusion.values()))

        # Les tranches se remplissent du plus RÉCENT au plus ancien, dans
        # l'ordre du fil. La tranche nue est donc toujours celle qu'on veut
        # d'abord, et un mois qui grossit ajoute une tranche à la fin sans
        # redistribuer les précédentes.
        tranches = [ranges[d:d + ARCHIVE_MAX_PAR_FICHIER]
                    for d in range(0, len(ranges), ARCHIVE_MAX_PAR_FICHIER)] or [[]]
        for rang, tranche in enumerate(tranches):
            write_feed({"mois": mois, "tranche": rang + 1,
                        "tranches": len(tranches),
                        "generated_at": _horodatage(maintenant),
                        "items": tranche},
                       os.path.join(repertoire, _nom_tranche(mois, rang)))

        # Un mois qui a rétréci (déduplication rétroactive) laisserait
        # traîner ses anciennes tranches, que l'index ne citerait plus mais
        # que le site servirait encore.
        rang = len(tranches)
        while True:
            reste = os.path.join(repertoire, _nom_tranche(mois, rang))
            if not os.path.exists(reste):
                break
            os.remove(reste)
            rang += 1

        ecrits[mois] = len(ranges)
    return ecrits


def ecrire_index_archives(repertoire=ARCHIVE_DIR, maintenant=None):
    """Écrit docs/archives/index.json : ce que l'app lit pour savoir quoi demander.

    L'index porte le POIDS de chaque fichier en plus de son nombre d'articles.
    Sans lui, l'app ne peut pas prévenir avant de lancer un téléchargement de
    plusieurs mégaoctets sur un forfait mobile — et c'est précisément le mois
    de la sortie qui sera le plus lourd.
    """
    if not os.path.isdir(repertoire):
        return {"mois": []}

    mois_vus = sorted({nom.split(".")[0] for nom in os.listdir(repertoire)
                       if nom.endswith(".json") and nom != "index.json"},
                      reverse=True)
    entrees = []
    for mois in mois_vus:
        fichiers = []
        for chemin in tranches_du_mois(mois, repertoire):
            try:
                with open(chemin, encoding="utf-8") as f:
                    nb = len(json.load(f).get("items") or [])
            except (OSError, ValueError):
                continue
            fichiers.append({"fichier": os.path.basename(chemin),
                             "articles": nb,
                             "octets": os.path.getsize(chemin)})
        if fichiers:
            entrees.append({"mois": mois,
                            "articles": sum(f["articles"] for f in fichiers),
                            "octets": sum(f["octets"] for f in fichiers),
                            "fichiers": fichiers})

    index = {"generated_at": _horodatage(maintenant),
             "articles": sum(e["articles"] for e in entrees),
             "mois": entrees}
    write_feed(index, os.path.join(repertoire, "index.json"))
    return index


def _horodatage(maintenant=None):
    return (maintenant or datetime.now(timezone.utc)).isoformat()


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
    return libelle_recap_depuis_comptes(
        len(lot),
        sum(1 for i in lot if i.get("official")),
        max(nb_sources_max(lot), nb_sources_max(promus)))


def libelle_recap_depuis_comptes(n, officiels, sommet):
    """Le même libellé, mais à partir de TROIS NOMBRES au lieu des articles.

    Nécessaire pour le récapitulatif du matin : les articles arrivés pendant
    la pause nocturne ont été publiés au fil de l'eau, donc à 5h ils ne sont
    plus « nouveaux » et la liste ne les contient plus. Seuls leurs comptes
    survivent, accumulés dans feed.json d'un passage à l'autre.

    C'est le MÊME texte, pas une seconde formulation : libelle_recap
    ci-dessus se contente désormais de compter puis d'appeler cette
    fonction. Deux libellés écrits séparément auraient dérivé au premier
    ajustement — ce dépôt a déjà connu ça.
    """
    compte = f"{n} nouv{'eaux' if n > 1 else 'el'} article{'s' if n > 1 else ''} GTA 6"
    if officiels:
        compte += f" (dont {officiels} officiel{'s' if officiels > 1 else ''} Rockstar)"

    if sommet >= HOT_SOURCE_THRESHOLD:
        alerte = f"🚨 Actu majeure — {sommet} sources sur le même sujet"
        # Un article promu sans aucune nouveauté : annoncer « 0 nouvel
        # article » à côté de l'alerte serait absurde.
        return f"{alerte} · {compte}" if n else alerte
    return f"🎮 {compte}"


def articles_officiels(new_items):
    """Les articles publiés par Rockstar ou Take-Two eux-mêmes.

    Le drapeau `official` est posé par la source dans FEEDS : le Newswire de
    Rockstar, sa chaîne YouTube, les relations investisseurs de Take-Two.
    Ce n'est pas une heuristique sur le contenu — c'est l'émetteur.
    """
    return [i for i in (new_items or ())
            if isinstance(i, dict) and i.get("official")]


def article_le_plus_notable(items):
    """Celui qu'on cite quand on ne peut en citer qu'un.

    Le récapitulatif n'affichait AUCUN titre, et le commentaire d'origine
    disait pourquoi : « premier » ne veut rien dire, c'est l'ordre de FEEDS
    et pas une importance, donc un titre pris là donne une idée fausse du
    lot. L'argument était juste, et il ne visait que le choix « le premier
    de la liste ».

    Antoni, le 22/09/2026 : les notifications « ne disent pas assez ». Il y
    a donc un titre à montrer — reste à ne pas le tirer au sort. L'ordre
    ci-dessous n'est pas inventé pour l'occasion, c'est celui que l'app
    utilise déjà pour décider ce qui est important :

      1. un article OFFICIEL de Rockstar passe avant tout le reste ;
      2. puis le nombre de RÉDACTIONS sur le même sujet — c'est ce qui
         pilote le badge « actu majeure » et le seuil HOT_SOURCE_THRESHOLD ;
      3. puis la date, le plus récent gagnant.

    À égalité parfaite, le tri reste stable : deux passages identiques
    citent le même article, ce qui évite qu'une notification remplacée par
    une autre change de titre sans raison.
    """
    candidats = [i for i in (items or ()) if isinstance(i, dict) and i.get("title")]
    if not candidats:
        return None
    return max(candidats, key=lambda i: (
        1 if i.get("official") else 0,
        1 + len(i.get("extraSources") or []),
        parse_date_key(i.get("date")),
    ))


def corps_recap(items):
    """Ce que la notification met sous son titre : un vrai titre d'article.

    Rend une chaîne vide s'il n'y a rien à citer — le récapitulatif du matin
    travaille sur des COMPTES reportés d'un passage à l'autre, sans garder
    les articles, et inventer un titre dans ce cas serait mentir.
    """
    notable = article_le_plus_notable(items)
    if not notable:
        return ""
    titre = (notable.get("title") or "").strip()
    autres = len([i for i in (items or ()) if isinstance(i, dict) and i.get("title")]) - 1
    if autres > 0:
        return f"{titre} · et {autres} autre{'s' if autres > 1 else ''}"
    return titre


# ---------------------------------------------------------------------------
# Aperçus : les quelques articles qu'un récapitulatif peut CITER
# ---------------------------------------------------------------------------
# Antoni, le 22/09/2026 : « faut aussi que tu détailles les notif discord avec
# des liens pour les 5 derniers article ». Le récapitulatif Discord annonçait
# un nombre et rien d'autre ; il annonce désormais un nombre ET montre les
# cinq derniers, cliquables.
#
# Le titre du récapitulatif, lui, ne bouge pas : c'est le texte partagé mot
# pour mot avec les notifications push (voir libelle_recap). Seul le corps du
# message Discord s'enrichit — un téléphone ne sait pas afficher cinq liens
# cliquables dans une notification, forcer la symétrie ici ne donnerait rien
# de lisible.

APERCU_RECAP_MAX = 5

# Bornes de ce qui est reconnu comme un nom de MÉDIA en fin de titre.
#
# Les titres venus de Google News finissent par « - IGN », « - Frandroid »,
# « - ixbt.games ». Affichés tels quels à côté du nom de la source, ils
# donnaient des lignes comme « … - GamesRadar+ — Google News (EN) ».
#
# Mesuré sur les 1 811 articles du fil le 22/09/2026 : 1 437 titres
# contiennent « - », et 1 411 de ces queues sont bien un média ou un domaine.
# Les 26 autres sont de vrais morceaux de titre (« and it's finally time to
# book that holiday to New Zealand », « VGC: "There's a bit of noise…" ») —
# toutes trop longues, trop bavardes ou ponctuées, donc écartées par les
# trois bornes ci-dessous. Le doute profite toujours au titre : quand la
# queue ne ressemble pas franchement à un média, on ne coupe rien.
MEDIA_MAX_CARACTERES = 32
MEDIA_MAX_MOTS = 4
MEDIA_PONCTUATION_INTERDITE = '.?!,;:)"»\u2019'


def separe_titre_et_media(titre, source=""):
    """Sépare « Un titre - IGN » en ("Un titre", "IGN").

    Rend (titre inchangé, `source`) dès qu'il y a le moindre doute : mieux
    vaut une ligne un peu redondante qu'un titre amputé de sa fin.
    """
    titre = (titre or "").strip()
    defaut = (source or "").strip()
    if " - " not in titre:
        return titre, defaut
    debut, queue = titre.rsplit(" - ", 1)
    debut, queue = debut.strip(), queue.strip()
    # Un titre réduit à rien n'est pas un titre : « - IGN » seul ne doit pas
    # produire une ligne vide et cliquable.
    if not debut or not queue:
        return titre, defaut
    if len(queue) > MEDIA_MAX_CARACTERES:
        return titre, defaut
    if len(queue.split()) > MEDIA_MAX_MOTS:
        return titre, defaut
    if queue[-1] in MEDIA_PONCTUATION_INTERDITE:
        return titre, defaut
    return debut, queue


def apercu_de(item):
    """La forme MINIMALE d'un article, telle qu'une notification la cite.

    Six champs et pas un de plus. Cette forme est stockée telle quelle dans
    feed.json pendant la pause nocturne (voir attente_recap), et tout champ
    en trop s'y paierait à chaque passage.

    Volontairement NON nettoyée : le titre est gardé brut, la séparation du
    média se fait au moment de l'affichage. Un aperçu écrit à 2h du matin par
    une version du robot ne doit pas figer la mise en forme que la version de
    5h appliquera.
    """
    item = item if isinstance(item, dict) else {}
    return {
        "title": (item.get("title") or "").strip(),
        "link": (item.get("link") or "").strip(),
        "source": (item.get("source") or "").strip(),
        "official": bool(item.get("official")),
        # Un ARTICLE porte extraSources ; un aperçu déjà réduit, relu depuis
        # feed.json après la pause nocturne, porte le compte déjà fait et plus
        # aucune extraSources. Ne lire que le premier faisait retomber tout
        # aperçu stocké à « 1 source » — donc perdre le 🔥 de l'actu majeure
        # entre 2h et 5h, exactement sur le récapitulatif qui en a besoin.
        "sources": (1 + len(item["extraSources"])
                    if isinstance(item.get("extraSources"), list)
                    else _compte_sources(item.get("sources"))),
        "date": item.get("date") or "",
    }


def _compte_sources(valeur):
    """Un nombre de rédactions relu, ou 1 pour tout ce qui n'en est pas un."""
    if isinstance(valeur, int) and not isinstance(valeur, bool) and valeur >= 1:
        return valeur
    return 1


def apercus_recap(items, maximum=APERCU_RECAP_MAX):
    """Les articles à citer sous le récapitulatif, au plus `maximum`.

    Ordre choisi par Antoni le 22/09/2026 : les plus RÉCENTS — c'est la
    lecture littérale de « les 5 derniers » — mais un article officiel de
    Rockstar remonte toujours en tête. C'est la hiérarchie déjà appliquée
    partout ailleurs : vibration dédiée, notification persistante, pastille
    orange dans l'app.

    Le tri se fait en deux passes stables plutôt qu'avec une clé composite :
    les dates sont des datetime, qu'on ne peut pas nier pour inverser
    seulement ce critère-là. La stabilité garantit que les officiels gardent
    entre eux leur ordre de date.

    Accepte aussi bien des articles complets que des aperçus déjà réduits :
    c'est ce qui permet d'empiler l'arriéré de la nuit passage après passage
    sans jamais garder plus de `maximum` entrées.
    """
    lot = [apercu_de(i) for i in (items or ())
           if isinstance(i, dict) and (i.get("title") or "").strip()
           and (i.get("link") or "").strip()]
    # Un même article peut arriver deux fois : une fois par l'arriéré déjà
    # stocké, une fois par le passage en cours qui le redétecte.
    vus, uniques = set(), []
    for a in lot:
        if a["link"] in vus:
            continue
        vus.add(a["link"])
        uniques.append(a)
    uniques.sort(key=lambda a: parse_date_key(a.get("date")), reverse=True)
    uniques.sort(key=lambda a: 0 if a.get("official") else 1)
    return uniques[:maximum]


def apercus_assainis(brut, maximum=APERCU_RECAP_MAX):
    """Relit une liste d'aperçus venue de feed.json, en se méfiant de tout.

    Même esprit qu'attente_lue pour les compteurs : un fichier écrit par une
    version antérieure n'a pas le champ, et une entrée aberrante ne doit pas
    faire publier un lien vide ou un titre à rallonge. Tout ce qui n'est pas
    exploitable disparaît silencieusement — une notification dégradée vaut
    mieux qu'une notification absente.
    """
    if not isinstance(brut, list):
        return []
    return apercus_recap([a for a in brut if isinstance(a, dict)], maximum)


def libelle_officiel(item):
    """Le texte d'une alerte « officiel Rockstar », écrit UNE seule fois.

    Même principe que libelle_recap : Discord et les notifications push
    disent mot pour mot la même chose, et ne peuvent pas diverger au
    premier ajustement.

    Ici, CONTRAIREMENT au récapitulatif, le titre de l'article apparaît. Le
    récapitulatif annonce un nombre parce qu'un lot de dix articles n'a pas
    de titre représentatif ; une annonce de Rockstar, elle, est un
    évènement unique et c'est justement son contenu qu'on veut lire sans
    ouvrir quoi que ce soit.
    """
    titre = (item.get("title") or "").strip() or "Nouvelle publication"
    # ⭐ et non 🎮. Les deux notifications commençaient par le MÊME emoji, et
    # sur un téléphone c'est la première chose qu'on voit : « 🎮 Rockstar
    # Games — officiel » et « 🎮 5 nouveaux articles GTA 6 » se ressemblaient
    # au point qu'Antoni ne les distinguait pas d'un coup d'œil (22/09/2026).
    # Le reste de la distinction est côté service worker : vibration propre
    # et notification qui reste tant qu'on ne l'a pas écartée.
    return "⭐ Rockstar Games — officiel", titre


def etiquette_officiel(item):
    """Identifiant stable d'une alerte officielle, dérivé du lien.

    Sert de `tag` à la notification push. Un tag PROPRE À CHAQUE ARTICLE est
    indispensable ici : le tag commun du récapitulatif remplace la
    notification précédente, ce qui effacerait en silence l'annonce d'un
    trailer une demi-heure plus tard — précisément celle qu'on ne veut pas
    rater. Deux annonces officielles du même passage ne doivent pas non
    plus s'écraser l'une l'autre.
    """
    lien = (item.get("link") or "").strip()
    return "gta6watch-officiel-" + hashlib.sha1(lien.encode("utf-8")).hexdigest()[:12]


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
