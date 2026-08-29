"""
Notification Discord — étape SÉPARÉE, exécutée après un push réussi.

Pourquoi c'est séparé du robot de collecte
------------------------------------------
La notification était auparavant envoyée à la fin de fetch_feeds.py, donc
AVANT l'étape de publication du workflow. Quand le push échouait (conflit
sur docs/feed.json entre deux exécutions concurrentes), Discord avait déjà
annoncé des articles qui n'étaient jamais publiés — puis l'exécution
suivante les redétectait comme nouveaux et les réannonçait une deuxième
fois. C'est arrivé sur les runs du 26/08 03:42 et du 28/08 07:17.

Désormais fetch_feeds.py se contente d'ÉCRIRE la liste des nouveaux
articles dans le fichier désigné par $NEW_ITEMS_FILE (placé hors du dépôt,
dans $RUNNER_TEMP, pour ne jamais risquer d'être committé), et le workflow
n'appelle ce script qu'une fois la publication réellement confirmée.

Conséquence assumée : lancé à la main en local sans $NEW_ITEMS_FILE,
fetch_feeds.py n'envoie plus rien — ce qui est le comportement souhaitable.

Décisions de notification inchangées :
  - UN SEUL message récapitulatif par exécution, jamais un par article ;
  - aucun message si zéro nouvel article ;
  - aucun message au tout premier lancement (historique vide).
"""

import json
import os
import sys
import time

import requests

import feed_store

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
SITE_URL = "https://antoniman31.github.io/gta6-backend/"


def send_discord_with_retry(embed, title_for_log, max_attempts=3):
    """Envoie un embed Discord avec nouvelle tentative en cas d'erreur
    temporaire (429 rate-limit ou 5xx serveur). Un 429 renvoie généralement
    un délai précis à respecter (Retry-After) — on l'utilise si présent,
    sinon un backoff exponentiel simple (2s, 4s, 8s...). Les erreurs
    définitives (ex: 400 webhook malformé, 404 webhook supprimé) ne sont
    jamais retentées, ça ne changerait rien."""
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
            if resp.status_code in (200, 204):
                return True
            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 2 ** attempt) if resp.text else 2 ** attempt
                print(f"  [discord] rate-limit (tentative {attempt}/{max_attempts}), attente {retry_after}s...")
                time.sleep(retry_after)
                continue
            if 500 <= resp.status_code < 600:
                wait = 2 ** attempt
                print(f"  [discord] erreur serveur {resp.status_code} (tentative {attempt}/{max_attempts}), attente {wait}s...")
                time.sleep(wait)
                continue
            # Erreur définitive (4xx hors 429) : inutile de retenter.
            print(f"  [discord] échec envoi ({resp.status_code}), non temporaire : {title_for_log[:50]}")
            return False
        except Exception as e:
            # Le message brut contiendrait l'URL du webhook — donc le secret
            # DISCORD_WEBHOOK_URL — dans un journal public. Voir
            # feed_store.masquer_urls.
            propre = feed_store.masquer_urls(str(e), [DISCORD_WEBHOOK_URL])
            print(f"  [discord] erreur réseau (tentative {attempt}/{max_attempts}) "
                  f"({type(e).__name__}) : {propre}")
            if attempt < max_attempts:
                time.sleep(2 ** attempt)

    print(f"  [discord] abandon après {max_attempts} tentatives : {title_for_log[:50]}")
    return False


def send_discord_notification(new_items):
    """Envoie UN SEUL message Discord récapitulatif, avec le nombre de
    nouveaux articles trouvés et un lien vers le site — plutôt qu'un message
    par article. Discord mobile ouvre toujours l'app Discord au tap sur une
    notification (jamais une URL externe directement), donc le lien reste
    cliquable DANS le message une fois Discord ouvert, pas au moment du tap
    sur la notification système elle-même. N'échoue jamais l'étape si
    Discord est indisponible ou mal configuré."""
    if not DISCORD_WEBHOOK_URL:
        print("[discord] DISCORD_WEBHOOK_URL absent — notification désactivée.")
        return False
    if not new_items:
        return False

    n = len(new_items)

    embed = {
        # Texte partagé avec les notifications push : voir
        # feed_store.libelle_recap. Les deux canaux disent mot pour mot la
        # même chose, et ne peuvent plus diverger.
        "title": feed_store.libelle_recap(new_items),
        "url": SITE_URL,
        "description": f"[Ouvrir GTA6_WATCH]({SITE_URL})",
        "color": 0x5493FF,
    }
    print(f"  [discord] envoi du récapitulatif ({n} nouvel(le)(s) article(s))...")
    return send_discord_with_retry(embed, f"récapitulatif {n} article(s)")


def send_source_alerts(alertes):
    """Signale qu'une source est tombée, ou qu'elle est revenue.

    Exception assumée à la règle « un seul message par passage ». Cette
    règle existe pour empêcher un message par ARTICLE ; une alerte de
    source est d'une autre nature, et surtout elle ne peut pas voyager dans
    le récapitulatif : une source morte se manifeste précisément les jours
    où il n'y a aucun nouvel article, donc où aucun récapitulatif ne part.

    Le volume reste nul en régime normal : fetch_feeds n'émet une alerte
    qu'au moment où l'état bascule, jamais tant qu'il dure.
    """
    if not DISCORD_WEBHOOK_URL or not alertes:
        return False

    tombees = [a for a in alertes if a.get("type") == "tombee"]
    retours = [a for a in alertes if a.get("type") == "retour"]
    lignes = []
    for a in tombees:
        lignes.append(f"🔴 **{a.get('name')}** ne renvoie plus rien "
                      f"depuis {a.get('runs')} passages.")
    for a in retours:
        lignes.append(f"🟢 **{a.get('name')}** est revenue.")

    embed = {
        "title": "⚠️ État des sources" if tombees else "✅ État des sources",
        "url": SITE_URL,
        "description": "\n".join(lignes),
        # Rouge s'il y a une panne, vert si ce sont uniquement des retours.
        "color": 0xE04F5F if tombees else 0x4FE07A,
    }
    print(f"  [discord] envoi de {len(alertes)} alerte(s) de source...")
    return send_discord_with_retry(embed, f"alerte source ({len(alertes)})")


def load_source_alerts(path):
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


# Note : une tentative de notification via ntfy.sh a été faite puis
# abandonnée — le serveur confirmait l'envoi (200 OK) sans jamais relayer
# les messages, probablement à cause d'un fail2ban/rate-limit silencieux
# sur les IP partagées des runners GitHub Actions. Détails dans README.md.


def load_new_items(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def main():
    path = os.environ.get("NEW_ITEMS_FILE", "")
    if not path:
        print("[discord] NEW_ITEMS_FILE non défini — rien à notifier.")
        return 0

    # Les alertes de source sont indépendantes des articles : elles doivent
    # partir même — surtout — quand il n'y a rien de neuf à annoncer.
    alertes = load_source_alerts(os.environ.get("SOURCE_ALERTS_FILE", ""))
    if alertes:
        send_source_alerts(alertes)

    new_items = load_new_items(path)
    if not new_items:
        print("[discord] aucun nouvel article à annoncer.")
        return 0

    send_discord_notification(new_items)
    # Cette étape ne doit JAMAIS faire échouer le workflow : les articles
    # sont déjà publiés à ce stade, une panne de Discord n'est pas une
    # panne du robot.
    return 0


if __name__ == "__main__":
    sys.exit(main())
