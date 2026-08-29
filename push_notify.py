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


def build_payload(new_items):
    """Construit le contenu de la notification.

    Le texte est celui de `feed_store.libelle_recap`, partagé avec Discord :
    les deux canaux annoncent mot pour mot la même chose.

    Aucun titre d'article n'apparaît. Une version précédente reprenait le
    titre du premier article pour éviter d'avoir à ouvrir l'app — mais
    « premier » ne veut rien dire ici (c'est l'ordre de FEEDS, pas une
    importance), et un titre choisi au hasard parmi plusieurs donne une
    idée fausse de ce que contient le lot.
    """
    return {
        "title": feed_store.libelle_recap(new_items),
        "body": "Ouvrir GTA6_WATCH",
        "url": SITE_URL,
        # Un tag identique remplace la notification précédente au lieu
        # d'empiler : après une nuit sans regarder son téléphone, on veut
        # un récapitulatif, pas douze bannières.
        "tag": "gta6watch-nouveaux",
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


def send_all(subscriptions, payload, private_key):
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


def main():
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

    try:
        with open(path, "r", encoding="utf-8") as f:
            new_items = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        new_items = []

    if not new_items:
        print("[push] aucun nouvel article à annoncer.")
        return 0

    if not check_subject(VAPID_SUBJECT):
        print("[push] envoi abandonné — aucune notification ne partirait de toute façon.")
        return 0

    payload = build_payload(new_items)
    print(f"[push] envoi à {len(subscriptions)} appareil(s) : {payload['title']}")

    envoyes, expires = send_all(subscriptions, payload, private_key)
    print(f"[push] {envoyes}/{len(subscriptions)} notification(s) envoyée(s)"
          + (f", {len(expires)} abonnement(s) expiré(s)" if expires else ""))

    # Comme pour Discord : les articles sont déjà publiés, une panne d'envoi
    # ne doit jamais faire échouer le workflow.
    return 0


if __name__ == "__main__":
    sys.exit(main())
