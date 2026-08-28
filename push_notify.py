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

SITE_URL = "https://antoniman31.github.io/gta6-backend/"

# Identifiant de contact exigé par la spécification VAPID : les services de
# push (Google, Mozilla, Apple) s'en servent pour joindre l'expéditeur en
# cas d'abus. Jamais montré à l'utilisateur. La spécification accepte une
# adresse mailto: ou une URL https: — on prend l'URL du site par défaut,
# plutôt que d'inscrire une adresse e-mail dans un dépôt public.
#
# Le `or` est indispensable, pas cosmétique : quand un secret GitHub
# n'existe pas, le workflow définit quand même la variable, à VIDE. Or la
# valeur par défaut de os.environ.get ne s'applique qu'à une variable
# ABSENTE. Sans ce garde-fou, le champ obligatoire "sub" partait vide et
# le service de push rejetait l'envoi avec « Missing 'sub' from claims ».
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "").strip() or SITE_URL


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

    Le titre du premier article est repris tel quel : sur un téléphone, un
    récapitulatif purement numérique ("3 nouveaux articles") oblige à ouvrir
    l'app pour savoir s'il s'agit d'un trailer ou d'un énième article de
    supputation.
    """
    n = len(new_items)
    n_officiels = sum(1 for i in new_items if i.get("official"))

    titre = f"{n} nouvel article GTA 6" if n == 1 else f"{n} nouveaux articles GTA 6"
    if n_officiels:
        titre += f" · {n_officiels} officiel" + ("s" if n_officiels > 1 else "")

    premier = new_items[0].get("title", "")
    corps = premier if n == 1 else (premier + (f" — et {n - 1} autre" + ("s" if n > 2 else "")) if premier else "")

    return {
        "title": titre,
        "body": corps[:180],
        "url": SITE_URL,
        # Un tag identique remplace la notification précédente au lieu
        # d'empiler : après une nuit sans regarder son téléphone, on veut
        # un récapitulatif, pas douze bannières.
        "tag": "gta6watch-nouveaux",
    }


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
                print(f"[push] échec sur l'abonnement #{i + 1} : {e}")
        except Exception as e:
            print(f"[push] erreur inattendue sur l'abonnement #{i + 1} : {e}")

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
