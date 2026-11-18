"""Alerte Discord quand le robot tombe, et quand il repart (demandée par Antoni le 14/11/2026).

POURQUOI. Le 11/11/2026, le robot est resté rouge de 16:00 à 16:52 UTC (un garde-fou refusait d'écrire
feed.json) et c'est Antoni qui l'a vu, sur une app figée. Une panne n'alertait que le service de
surveillance externe (HEALTHCHECK_URL/fail) ; rien n'arrivait dans le salon Discord, seul endroit qu'il
regarde. Ce programme ajoute ce message, sans rien changer au reste.

UNE FOIS PAR PANNE, pas toutes les heures. Au premier passage rouge après un passage vert on écrit
« le robot a échoué » ; tant que les passages suivants restent rouges, silence ; au premier passage vert
après un rouge, « le robot est reparti ». « Le passage d'avant » est lu dans l'historique des passages de
CE workflow (API GitHub, lecture seule), le passage courant et ceux qui tournent encore étant exclus.

SANS RISQUE POUR LE ROBOT. Il ne fait jamais échouer le job (le travail est déjà publié, ou déjà
perdu : une alerte qui plante n'y change rien), il ne dit rien du contenu (ni titre, ni lien d'article,
ni adresse du webhook), et sans DISCORD_WEBHOOK_URL ou sans lecture possible de l'historique il se tait
ou envoie l'alerte de panne par prudence — jamais le message « reparti » sans preuve.

LIMITE ASSUMÉE : si le passage échoue AVANT l'installation de Python ou le checkout (réseau de GitHub),
ce programme n'existe pas encore sur la machine : seule la surveillance externe prévient alors.

Usage : `python alerte_panne.py echec|succes` (le workflow), `python alerte_panne.py --essai` (message de TEST).
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import discord_notify

ROUGE = 0xE5484D
VERT = 0x30A46C
GRIS = 0x8B8D98
WORKFLOW = "update-feeds.yml"
PARIS = ZoneInfo("Europe/Paris")


def passage_precedent(runs, id_courant):
    """Le plus récent passage TERMINÉ autre que le courant, ou None."""
    candidats = [r for r in (runs or [])
                 if r.get("status") == "completed" and str(r.get("id")) != str(id_courant)]
    candidats.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return candidats[0] if candidats else None


def decide(mode, precedent):
    """Quel message envoyer : 'echec', 'retour' ou None.

    `precedent` est None quand l'historique est illisible ou vide. Sur un échec on alerte alors par
    prudence ; sur un succès on ne dit rien, faute de preuve qu'il y ait eu une panne.
    """
    avant = (precedent or {}).get("conclusion")
    if mode == "echec":
        return None if avant == "failure" else "echec"
    if mode == "succes":
        return "retour" if avant == "failure" else None
    return None


def message(genre, maintenant, url_passage):
    """L'embed Discord. `maintenant` est une datetime avec fuseau ; l'heure est celle de Paris."""
    heure = maintenant.astimezone(PARIS).strftime("%H:%M")
    if genre == "echec":
        return {"title": "⚠️ Le robot a échoué",
                "description": ("Le passage de %s (heure de Paris) n'a pas pu publier. "
                                "L'app garde son dernier état publié.\n[Voir le passage](%s)") % (heure, url_passage),
                "color": ROUGE}
    if genre == "retour":
        return {"title": "✅ Le robot est reparti",
                "description": "Le passage de %s (heure de Paris) a réussi après une panne.\n[Voir le passage](%s)" % (heure, url_passage),
                "color": VERT}
    return {"title": "🧪 TEST — alerte de panne du robot",
            "description": ("Essai de %s (heure de Paris) : si tu lis ceci, l'alerte arrive bien dans ce salon. "
                            "Rien n'a échoué.") % heure,
            "color": GRIS}


def lis_historique(repo, token):
    """Les derniers passages terminés du workflow, ou None si l'API ne répond pas."""
    try:
        r = requests.get("https://api.github.com/repos/%s/actions/workflows/%s/runs" % (repo, WORKFLOW),
                         params={"status": "completed", "per_page": 10},
                         headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"} if token else {"Accept": "application/vnd.github+json"},
                         timeout=15)
        if r.status_code != 200:
            print("  [alerte] historique illisible (HTTP %s)" % r.status_code)
            return None
        return r.json().get("workflow_runs") or []
    except Exception as e:
        print("  [alerte] historique illisible (%s)" % type(e).__name__)
        return None


def main(argv=None, env=None, maintenant=None):
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    mode = argv[0] if argv else ""
    if mode not in ("echec", "succes", "--essai"):
        print("usage : alerte_panne.py echec|succes|--essai")
        return 0
    if not discord_notify.DISCORD_WEBHOOK_URL:
        print("  [alerte] DISCORD_WEBHOOK_URL absent : aucune alerte Discord.")
        return 0
    maintenant = maintenant or datetime.now(PARIS)
    repo = env.get("GITHUB_REPOSITORY", "antoniman31/gta6-backend")
    id_courant = env.get("GITHUB_RUN_ID", "")
    url = "https://github.com/%s/actions/runs/%s" % (repo, id_courant)
    if mode == "--essai":
        genre = "essai"
    else:
        runs = lis_historique(repo, env.get("GITHUB_TOKEN", ""))
        genre = decide(mode, passage_precedent(runs, id_courant) if runs is not None else None)
    if genre is None:
        print("  [alerte] rien à dire (mode %s, passage précédent déjà dans le même état)." % mode)
        return 0
    try:
        ok = discord_notify.send_discord_with_retry(message(genre, maintenant, url), "alerte robot " + genre)
    except Exception as e:  # jamais faire échouer le job : voir l'en-tête
        print("  [alerte] envoi impossible (%s)." % type(e).__name__)
        return 0
    print("  [alerte] message %r %s." % (genre, "envoyé" if ok else "NON envoyé"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
