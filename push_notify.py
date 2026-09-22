"""
Notifications push natives — envoyées depuis GitHub Actions, sans serveur.

Comment ça marche sans backend
------------------------------
Le protocole Web Push ne demande pas de serveur permanent : il faut
seulement une paire de clés VAPID et, pour chaque appareil, un
« abonnement » (une URL fournie par le navigateur, plus deux clés de
chiffrement). L'expéditeur peut être n'importe quoi capable d'envoyer une
requête HTTP signée — ici, une étape de workflow qui tourne quelques
secondes par passage.

Les abonnements sont stockés dans le secret GitHub PUSH_SUBSCRIPTIONS, pas
dans le dépôt : un abonnement rendu public permettrait à n'importe qui
d'envoyer des notifications sur l'appareil concerné.

Pourquoi en plus de Discord
---------------------------
Discord ouvre l'application Discord quand on tape la notification, jamais
l'article. Une notification push native ouvre directement le site. Les deux
peuvent coexister : chacune s'active indépendamment par la présence de son
secret.

Décisions de notification, identiques à Discord :
  - UN SEUL message récapitulatif par exécution ;
  - rien s'il n'y a aucun nouvel article ;
  - rien au tout premier lancement ;
  - envoyé APRÈS publication réussie, jamais avant.
"""

import json
import os
import sys
from urllib.parse import urlparse

import feed_store

SITE_URL = "https://antoniman31.github.io/gta6-backend/"

# Durée de vie d'un message, en secondes, et son urgence.
# ------------------------------------------------------
# Sans ces deux réglages, pywebpush envoie TTL: 0. Sa propre documentation
# dit ce que ça veut dire : « discards the message immediately if the
# recipient is unavailable ». Le service de push tente la livraison à
# l'instant même et, si l'appareil ne répond pas, JETTE le message. Aucune
# file d'attente, aucune seconde tentative.
#
# C'est le cas d'un téléphone verrouillé depuis un moment : Android suspend
# la connexion (Doze), le message arrive, ne trouve personne et disparaît.
# Au déverrouillage il n'y a rien à rattraper, il n'existe plus. D'où le
# symptôme : les notifications passent écran allumé et jamais autrement.
#
# Les durées ne sont pas choisies au hasard. Un récapitulatif est périmé au
# passage suivant, environ une heure plus tard : au-delà, il annoncerait un
# compte que le passage d'après a déjà corrigé. Une annonce de Rockstar,
# elle, mérite d'arriver en retard plutôt que jamais.
TTL_RECAP = 3600         # 1 h — la cadence du robot
TTL_OFFICIEL = 86400     # 24 h — une annonce garde sa valeur

# Urgency (RFC 8030 §5.3) dit au service de push si le message vaut la peine
# de réveiller un appareil endormi. Il vaut « normal » par défaut, et
# pywebpush n'en envoie aucun.
#
# « high » est réservé aux annonces de Rockstar. Tout marquer urgent est
# exactement l'abus que les services de push finissent par sanctionner, et
# ferait de ce réglage un bruit de fond au lieu d'un signal.
URGENCE_NORMALE = "normal"
URGENCE_HAUTE = "high"

# Identifiant de contact exigé par la spécification VAPID : les services de
# push (Google, Mozilla, Apple) s'en servent pour joindre l'expéditeur en
# cas d'abus. Jamais montré à l'utilisateur.
#
# Deux pièges, tous deux rencontrés en production :
#
# 1. Le `or` n'est pas cosmétique. Quand un secret GitHub n'existe pas, le
#    workflow définit quand même la variable, à VIDE — or la valeur par
#    défaut de os.environ.get ne s'applique qu'à une variable ABSENTE.
#
# 2. Le repli doit être l'ORIGINE du site, sans chemin. py_vapid valide ce
#    champ avec une expression régulière qui n'accepte qu'un schéma et un
#    hôte : "https://exemple.github.io" passe,
#    "https://exemple.github.io/projet/" est refusé. Les deux échecs
#    remontent sous le même message trompeur, « Missing 'sub' from
#    claims », qui laisse croire que le champ est absent alors qu'il est
#    seulement mal formé.
#
# Une adresse mailto: reste possible via le secret VAPID_SUBJECT ; l'URL
# par défaut évite d'inscrire une adresse personnelle dans un dépôt public.
def _default_subject():
    parties = urlparse(SITE_URL)
    return f"{parties.scheme}://{parties.netloc}"


VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "").strip() or _default_subject()


def load_subscriptions():
    """Charge les abonnements depuis le secret PUSH_SUBSCRIPTIONS.

    Accepte soit un tableau JSON d'abonnements, soit un abonnement seul —
    c'est plus tolérant pour un secret collé à la main depuis l'app.
    """
    raw = os.environ.get("PUSH_SUBSCRIPTIONS", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[push] PUSH_SUBSCRIPTIONS illisible (JSON invalide) : {e}")
        return []

    subs = data if isinstance(data, list) else [data]
    valides = []
    for sub in subs:
        if isinstance(sub, dict) and sub.get("endpoint") and sub.get("keys"):
            valides.append(sub)
        else:
            print("[push] abonnement ignoré : structure inattendue "
                  "(il faut au minimum 'endpoint' et 'keys').")
    return valides


def build_payload(new_items, promus=()):
    """Construit le contenu de la notification.

    Le texte est celui de `feed_store.libelle_recap`, partagé avec Discord :
    les deux canaux annoncent mot pour mot la même chose.

    LE CORPS PORTE UN TITRE D'ARTICLE depuis le 22/09/2026. La version
    d'avant n'en montrait aucun, et son commentaire disait pourquoi :
    « premier » ne veut rien dire, c'est l'ordre de FEEDS et pas une
    importance, donc un titre pris là donne une idée fausse du lot.
    L'argument était juste — il ne visait que le choix « le premier ».

    Antoni : les notifications « ne disent pas assez ». Le titre montré est
    donc celui que `feed_store.article_le_plus_notable` désigne, selon
    l'ordre que l'app utilise déjà pour juger de l'importance : officiel
    Rockstar d'abord, puis le nombre de rédactions sur le sujet, puis la
    date. Ce n'est pas un tirage au sort, et deux passages identiques
    citent le même article.

    Le corps retombe sur « Ouvrir GTA6_WATCH » quand il n'y a rien à citer —
    c'est le cas du récapitulatif du matin, qui travaille sur des comptes
    reportés sans garder les articles. Inventer un titre là serait mentir.
    """
    totaux = lire_totaux_recap()
    if totaux:
        n, officiels, sommet = totaux
        titre = feed_store.libelle_recap_depuis_comptes(n, officiels, sommet)
        majeure = sommet >= feed_store.HOT_SOURCE_THRESHOLD
    else:
        titre = feed_store.libelle_recap(new_items, promus)
        majeure = feed_store.est_actu_majeure(new_items, promus)
    corps = feed_store.corps_recap(list(new_items or ()) + list(promus or ()))
    return {
        "title": titre,
        "body": corps or "Ouvrir GTA6_WATCH",
        "url": SITE_URL,
        # Un tag identique remplace la notification précédente au lieu
        # d'empiler : après une nuit sans regarder son téléphone, on veut
        # un récapitulatif, pas douze bannières.
        #
        # Sauf pour une actu majeure, qui reçoit son propre tag : sinon le
        # récapitulatif de routine du passage suivant l'effacerait en
        # silence une demi-heure plus tard, et c'est précisément celle
        # qu'on ne veut pas rater.
        "tag": "gta6watch-majeur" if majeure else "gta6watch-nouveaux",
    }


def build_payload_officiel(item):
    """La notification d'une annonce publiée par Rockstar lui-même.

    Contrairement au récapitulatif, elle porte le TITRE de l'article. Le
    récapitulatif annonce un nombre parce qu'un lot de dix articles n'a pas
    de titre représentatif ; une annonce de Rockstar est un évènement
    unique, et c'est son contenu qu'on veut lire sans rien ouvrir.

    Le texte vient de feed_store.libelle_officiel, partagé avec Discord :
    les deux canaux disent mot pour mot la même chose.
    """
    entete, titre = feed_store.libelle_officiel(item)
    return {
        "title": entete,
        "body": titre,
        # Le lien mène à l'ARTICLE, pas à l'accueil : c'est une annonce
        # précise, pas une invitation à venir voir.
        "url": (item.get("link") or "").strip() or SITE_URL,
        # Un tag PROPRE À CHAQUE ARTICLE. Le tag commun du récapitulatif
        # remplace la notification précédente : sans celui-ci, le
        # récapitulatif du passage suivant effacerait l'annonce d'un trailer
        # une demi-heure plus tard, en silence.
        "tag": feed_store.etiquette_officiel(item),
        # Lu par le service worker. Une annonce de Rockstar doit se
        # reconnaître SANS lire : une vibration à elle, et une bannière qui
        # ne disparaît pas toute seule. Le récapitulatif de routine, lui,
        # reste discret et s'efface comme n'importe quelle notification.
        "officiel": True,
    }


def check_subject(subject):
    """Valide le champ 'sub' avant l'envoi, avec un message compréhensible.

    py_vapid rejette un 'sub' mal formé sous le message « Missing 'sub'
    from claims », qui fait chercher une valeur absente alors qu'elle est
    seulement invalide. Autant le dire clairement ici.
    """
    try:
        from py_vapid import _check_sub
    except ImportError:
        return True
    if _check_sub(subject):
        return True
    print(f"[push] identifiant de contact refusé : {subject!r}")
    print("       Il faut soit une adresse « mailto:untel@domaine.fr », soit "
          "une URL réduite au schéma et à l'hôte, sans chemin "
          "(« https://exemple.com », pas « https://exemple.com/projet/ »).")
    print("       Corrige le secret VAPID_SUBJECT.")
    return False


def masquer_endpoints(texte, subscriptions):
    """Masque, dans un message d'erreur, les endpoints des abonnements.

    L'endpoint EST le secret : quiconque le possède peut notifier
    l'appareil. Le masquage lui-même vit dans feed_store, partagé avec
    discord_notify qui a exactement le même besoin sur son webhook.
    """
    endpoints = [sub.get("endpoint") for sub in subscriptions or ()
                 if isinstance(sub, dict)]
    return feed_store.masquer_urls(texte, endpoints)


def send_all(subscriptions, payload, private_key,
             ttl=TTL_RECAP, urgence=URGENCE_NORMALE):
    """Envoie une notification à tous les appareils abonnés.

    ttl et urgence ne sont PAS optionnels par confort : ce sont eux qui
    décident si un téléphone verrouillé reçoit quelque chose. Voir le bloc
    TTL_RECAP en tête de fichier. Les valeurs par défaut sont celles du
    récapitulatif, le cas courant.
    """
    from pywebpush import webpush, WebPushException

    envoyes = 0
    expires = []
    for i, sub in enumerate(subscriptions):
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps(payload),
                vapid_private_key=private_key,
                vapid_claims={"sub": VAPID_SUBJECT},
                ttl=ttl,
                headers={"Urgency": urgence},
                timeout=10,
            )
            envoyes += 1
        except WebPushException as e:
            statut = getattr(e.response, "status_code", None)
            if statut in (404, 410):
                # L'appareil s'est désabonné ou le navigateur a renouvelé
                # son abonnement. Il ne sera plus jamais joignable : autant
                # le dire clairement plutôt que de réessayer indéfiniment.
                expires.append(i)
                print(f"[push] abonnement #{i + 1} expiré (HTTP {statut}) — "
                      "à retirer du secret PUSH_SUBSCRIPTIONS et à recréer depuis l'app.")
            else:
                print(f"[push] échec sur l'abonnement #{i + 1} : "
                      f"{masquer_endpoints(str(e), subscriptions)}")
        except Exception as e:
            print(f"[push] erreur inattendue sur l'abonnement #{i + 1} "
                  f"({type(e).__name__}) : "
                  f"{masquer_endpoints(str(e), subscriptions)}")

    return envoyes, expires


# Déplacée dans feed_store : les deux canaux doivent lire
# EXACTEMENT la même chose, deux copies finiraient par diverger.
lire_totaux_recap = feed_store.lire_totaux_recap

# Déplacée dans feed_store : les deux canaux doivent lire
# EXACTEMENT la même chose, deux copies finiraient par diverger.
lire_liste = feed_store.lire_liste

def alerte_discord_push_mort(total):
    """Prévient sur Discord quand plus AUCUN appareil n'est joignable.

    Le 15/09/2026, un passage a affiché « 0/1 notification(s) envoyée(s) »
    et le job est resté vert : l'abonnement avait expiré (HTTP 410) après
    une réinstallation de l'app, et rien ne l'a signalé. Antoni l'a
    découvert en le demandant, trois heures plus tard.

    Le signal de vie ne couvre pas ce cas : il dit « le robot tourne », pas
    « les notifications arrivent ». Discord, lui, fonctionne quand le push
    est mort — c'est donc le bon canal pour annoncer que le push est mort.

    Seulement quand TOUS sont expirés : avec plusieurs appareils, en perdre
    un est banal et ne mérite pas d'alerte.
    """
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        return
    try:
        import requests
        requests.post(webhook, json={"embeds": [{
            "title": "🔕 Notifications push hors service",
            "description": (
                "Les %d abonnement(s) enregistré(s) sont expirés — aucune "
                "notification push ne peut plus arriver.\n\n"
                "**Réparer :** réactiver les notifications dans l'app pour "
                "créer un nouvel abonnement, puis remplacer le secret "
                "`PUSH_SUBSCRIPTIONS` dans les réglages GitHub." % total),
            "color": 0xE67E22,
        }]}, timeout=10)
        print("[push] alerte Discord envoyée : plus aucun appareil joignable.")
    except Exception as e:
        # L'URL du webhook est un secret : jamais dans un journal public.
        propre = feed_store.masquer_urls(str(e), [webhook])
        print(f"[push] alerte Discord non envoyée ({type(e).__name__}) : {propre}")


def mode_test():
    """Envoie une VRAIE notification, de bout en bout, à la demande.

    Le bouton « tester » de l'app ne pouvait afficher qu'une notification
    LOCALE : une vraie push doit être signée avec la clé privée VAPID, qui
    est dans un secret et doit y rester — dans la page, n'importe qui
    pourrait notifier l'appareil. Le seul chemin honnête est donc
    app -> GitHub -> ici -> service de push -> téléphone.

    Contrairement au reste du fichier, cette fonction REND UN CODE D'ERREUR
    quand rien n'est parti. C'est tout l'intérêt : un test qui reste vert
    alors qu'aucune notification n'arrive ne teste rien. Le run devient
    rouge, et l'app le voit.
    """
    subscriptions = load_subscriptions()
    if not subscriptions:
        print("[test] aucun abonnement dans PUSH_SUBSCRIPTIONS — rien à tester.")
        return 1

    private_key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if not private_key:
        print("[test] VAPID_PRIVATE_KEY absent — envoi impossible.")
        return 1
    if not check_subject(VAPID_SUBJECT):
        print("[test] sujet VAPID invalide — envoi impossible.")
        return 1

    charge = {
        "title": "✅ Test GTA6_WATCH",
        "body": "Si tu lis ceci, les notifications push fonctionnent.",
        "url": SITE_URL,
        # Un tag qui lui est propre : un test ne doit jamais remplacer le
        # récapitulatif ni une annonce de Rockstar sur l'écran.
        "tag": "gta6watch-test-reel",
    }
    print(f"[test] envoi à {len(subscriptions)} appareil(s)…")
    # Volontairement les MÊMES durée de vie et urgence que le récapitulatif,
    # donc les valeurs par défaut de send_all. Un test qui emprunterait un
    # chemin plus favorable que la vraie notification ne testerait rien : le
    # bouton resterait vert pendant que les vraies notifications se perdent.
    # C'est précisément ce qui masquait le TTL: 0 — le test se fait toujours
    # écran allumé, le seul cas où un TTL nul passe.
    envoyes, expires = send_all(subscriptions, charge, private_key,
                                ttl=TTL_RECAP, urgence=URGENCE_NORMALE)
    print(f"[test] {envoyes}/{len(subscriptions)} notification(s) envoyée(s)"
          + (f", {len(expires)} abonnement(s) expiré(s)" if expires else ""))

    if envoyes == 0:
        print("[test] ÉCHEC — aucune notification n'est partie.")
        if len(expires) == len(subscriptions):
            print("[test] tous les abonnements sont expirés : réactive les "
                  "notifications dans l'app, puis remplace le secret "
                  "PUSH_SUBSCRIPTIONS.")
            alerte_discord_push_mort(len(subscriptions))
        return 1

    print("[test] OK — la notification devrait arriver dans quelques secondes.")
    return 0


def main():
    if "--test" in sys.argv:
        return mode_test()

    subscriptions = load_subscriptions()
    if not subscriptions:
        print("[push] aucun abonnement configuré — notifications push désactivées.")
        return 0

    private_key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if not private_key:
        print("[push] VAPID_PRIVATE_KEY absent — notifications push désactivées.")
        return 0

    path = os.environ.get("NEW_ITEMS_FILE", "")
    if not path:
        print("[push] NEW_ITEMS_FILE non défini — rien à notifier.")
        return 0

    new_items = lire_liste(path)
    # Articles déjà connus devenus majeurs : ils justifient une notification
    # À EUX SEULS, même sans le moindre article nouveau. C'est le cas d'une
    # annonce reprise progressivement par la presse — chaque reprise est un
    # doublon, donc « rien de neuf », alors que le sujet vient de devenir
    # important.
    promus = lire_liste(os.environ.get("PROMOTED_ITEMS_FILE", ""))

    # Pendant la pause nocturne, seule une annonce de Rockstar réveille.
    # Les trois plus grosses de l'histoire du jeu sont tombées entre 2h24 et
    # 3h48 heure de Paris — les retenir jusqu'à 5h raterait exactement ce
    # pour quoi cette veille existe.
    seulement_officiels = os.environ.get("SEULEMENT_OFFICIELS") == "1"
    officiels = feed_store.articles_officiels(new_items)

    totaux = lire_totaux_recap()
    if not new_items and not promus and totaux is None:
        print("[push] aucun nouvel article à annoncer.")
        return 0
    if seulement_officiels and not officiels:
        print("[push] pause nocturne et aucune annonce officielle — rien n'est envoyé.")
        return 0

    if not check_subject(VAPID_SUBJECT):
        print("[push] envoi abandonné — aucune notification ne partirait de toute façon.")
        return 0

    # Une notification par annonce officielle, AVANT le récapitulatif : sur
    # un téléphone, la dernière arrivée est celle du dessus, et on veut que
    # ce soit le récapitulatif qui se range sous l'annonce, pas l'inverse.
    for item in officiels:
        charge = build_payload_officiel(item)
        print(f"[push] annonce officielle : {charge['body'][:60]}")
        envoyes, expires = send_all(subscriptions, charge, private_key,
                                    ttl=TTL_OFFICIEL, urgence=URGENCE_HAUTE)
        print(f"[push] {envoyes}/{len(subscriptions)} envoyée(s)"
              + (f", {len(expires)} abonnement(s) expiré(s)" if expires else ""))

    if seulement_officiels:
        print(f"[push] pause nocturne — {len(officiels)} annonce(s) officielle(s), "
              f"le récapitulatif attend le matin.")
        return 0

    payload = build_payload(new_items, promus)
    print(f"[push] envoi à {len(subscriptions)} appareil(s) : {payload['title']}")

    envoyes, expires = send_all(subscriptions, payload, private_key,
                                ttl=TTL_RECAP, urgence=URGENCE_NORMALE)
    print(f"[push] {envoyes}/{len(subscriptions)} notification(s) envoyée(s)"
          + (f", {len(expires)} abonnement(s) expiré(s)" if expires else ""))

    # Plus personne n'est joignable : le dire là où ça s'entend encore.
    if envoyes == 0 and len(expires) == len(subscriptions):
        alerte_discord_push_mort(len(subscriptions))

    # Comme pour Discord : les articles sont déjà publiés, une panne d'envoi
    # ne doit jamais faire échouer le workflow.
    return 0


if __name__ == "__main__":
    sys.exit(main())
