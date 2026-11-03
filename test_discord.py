"""Envoie quelques messages de TEST dans le salon Discord, pour vérifier ce que
Discord accepte et affiche vraiment avec notre lien d'envoi.

Lancé à la main seulement, par .github/workflows/test-discord.yml. Rien n'est
publié, rien n'est écrit dans le dépôt. Chaque message porte « TEST » dans son
nom d'expéditeur et dans son texte, pour qu'aucun ne passe pour une vraie
notification.

Ce qu'on veut apprendre (spec tasks/spec-discord-notification.md, « Vérifier
avant de coder ») :
  A. un lien d'envoi sans bot peut-il porter des BOUTONS-LIENS ?
  B. peut-on MODIFIER un message déjà envoyé (le tableau du jour) ?
  C. la BALISE DE TEMPS relative se rend-elle (« dans 15 jours ») ?
  D. à quoi ressemblent les vrais messages à sections, sur ton téléphone ?
  E. le style gris `-#` est-il rendu dans une carte ?

Le lien d'envoi est un secret : il ne s'écrit jamais dans le journal, qui est
public. On y écrit ce que Discord a RÉPONDU (code HTTP, code d'erreur, message),
jamais l'adresse.

Le programme ÉCHOUE quand aucun message n'est accepté : un test qui reste vert
alors que rien n'arrive ne teste rien (même leçon que test-push.yml). Une
fonction refusée par Discord, elle, est un RÉSULTAT, pas une panne.
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import discord_notify
import feed_store

PAUSE_S = 1.5          # Discord tolère 5 envois par 2 s et par lien ; on reste loin


def url_message(webhook, identifiant):
    """L'adresse d'un message déjà envoyé, pour le modifier."""
    base = webhook.split("?", 1)[0].rstrip("/")
    return f"{base}/messages/{identifiant}"


def _raconte(reponse):
    """Ce que Discord a répondu, sans rien qui ressemble au lien d'envoi."""
    morceau = ""
    try:
        corps = reponse.json() if reponse.text else {}
        if isinstance(corps, dict):
            if "message" in corps or "code" in corps:
                morceau = f" · code {corps.get('code')} · {str(corps.get('message'))[:120]}"
            elif "id" in corps:
                morceau = " · message créé"
    except Exception:
        pass
    return f"HTTP {reponse.status_code}{morceau}"


def envoie(webhook, charge, etiquette, params=None, methode="POST", url=None):
    """Un envoi, journalisé. Rend (accepté, réponse ou None)."""
    cible = url or webhook
    try:
        r = requests.request(methode, cible, params=params or {}, json=charge, timeout=15)
    except Exception as e:
        propre = feed_store.masquer_urls(str(e), [webhook])
        print(f"  [{etiquette}] erreur réseau ({type(e).__name__}) : {propre}")
        return False, None
    ok = r.status_code in (200, 204)
    print(f"  [{etiquette}] {'accepté' if ok else 'REFUSÉ'} — {_raconte(r)}")
    time.sleep(PAUSE_S)
    return ok, r


def marque(charge):
    """Le même message, mais impossible à prendre pour une vraie notification."""
    charge = dict(charge)
    charge["username"] = ("TEST · " + (charge.get("username") or "GTA6_WATCH"))[:80]
    charge["content"] = ("🧪 TEST — " + (charge.get("content") or ""))[:2000]
    return charge


def etape_boutons(webhook):
    print("\nA. Boutons-liens sous un message")
    boutons = [{"type": 1, "components": [
        {"type": 2, "style": 5, "label": "Ouvrir GTA6_WATCH", "url": discord_notify.SITE_URL},
        {"type": 2, "style": 5, "label": "Newswire Rockstar", "url": "https://www.rockstargames.com/newswire"},
    ]}]
    charge = marque({"username": "GTA6_WATCH", "content": "deux boutons-liens sous ce message ?",
                     "embeds": [{"description": "Carte de test, sans importance.", "color": 0x5493FF}],
                     "components": boutons})
    ok, _ = envoie(webhook, charge, "boutons (with_components=true)", {"wait": "true", "with_components": "true"})
    if not ok:
        # Sans le paramètre : certains liens d'envoi l'acceptent tel quel.
        ok, _ = envoie(webhook, charge, "boutons (sans paramètre)", {"wait": "true"})
    return ok


def etape_modification(webhook):
    print("\nB. Modifier un message déjà envoyé")
    ok, r = envoie(webhook, marque({"username": "GTA6_WATCH", "content": "message AVANT modification",
                                    "embeds": [{"title": "📊 Tableau du jour (test)",
                                                "description": "0 article — version 1", "color": 0x4FE07A}]}),
                   "création", {"wait": "true"})
    if not ok or r is None:
        return False
    try:
        identifiant = r.json().get("id")
    except Exception:
        identifiant = None
    if not identifiant:
        print("  [modification] pas d'identifiant dans la réponse : impossible de modifier")
        return True
    ok2, _ = envoie(webhook, marque({"content": "message APRÈS modification (doit remplacer l'avant)",
                                     "embeds": [{"title": "📊 Tableau du jour (test)",
                                                 "description": "12 articles — version 2", "color": 0x4FE07A}]}),
                    "modification", methode="PATCH", url=url_message(webhook, identifiant))
    print("  → une modification réussie ne notifie pas : tu ne dois voir qu'UN message, à sa version 2."
          if ok2 else "  → modification refusée : le repli sera un nouveau message par passage.")
    return True


def etape_balise(webhook, maintenant):
    print("\nC. Balise de temps relative")
    sortie = feed_store.SORTIE_GTA6
    ok, _ = envoie(webhook, marque({
        "username": "GTA6_WATCH", "content": "balise de temps",
        "embeds": [{"description": (f"Sortie {discord_notify.balise_temps(sortie)} · "
                                    f"le {discord_notify.balise_temps(sortie, 'F')}\n"
                                    f"Cette ligne a été écrite {discord_notify.balise_temps(maintenant)} "
                                    f"(elle doit dire « il y a quelques secondes »)."),
                    "color": 0xFF8A00}]}), "balise de temps")
    return ok


def messages_d_exemple(maintenant):
    """Les vrais messages du robot, construits par le vrai code, sur des articles fictifs."""
    # Des dates SANS microsecondes, comme celles des flux : le robot prend une
    # date qui en porte pour une date « première vue » et n'affiche alors pas
    # l'heure de publication (constaté sur l'envoi TEST du 03/11/2026).
    maintenant = maintenant.replace(microsecond=0)

    def art(n, **extra):
        a = {"title": f"Article d'exemple {n} - Média {n}", "link": f"https://exemple.test/{n}", "source": "Exemple",
             "date": (maintenant - timedelta(minutes=10 * n)).isoformat()}
        a.update(extra)
        return a
    officiels = [
        {"title": "L'album arrive - Rockstar Games", "link": "https://www.rockstargames.com/fr/newswire/article/test1/x",
         "source": "Rockstar Games (officiel FR)", "official": True, "date": (maintenant - timedelta(minutes=12)).isoformat()},
        {"title": "The Album arrives - Rockstar Games", "link": "https://www.rockstargames.com/newswire/article/test1/x",
         "source": "Rockstar Games (officiel EN)", "official": True, "date": (maintenant - timedelta(minutes=14)).isoformat()},
        {"title": "Grand Theft Auto VI Keychain - Rockstar Store",
         "link": "https://store.rockstargames.com/merchandise/buy-keychain",
         "source": "Rockstar Games (officiel EN)", "official": True, "date": (maintenant - timedelta(minutes=3)).isoformat()},
    ]
    majeur = art(1, extraSources=[{"source": "a", "link": "https://a.test"}, {"source": "b", "link": "https://b.test"}])
    mag = [{"title": "RockstarMag : un grand dossier sur GTA VI", "link": "https://www.rockstarmag.fr/dossier",
            "description": "<p>Un résumé de test assez long pour être coupé à cent soixante caractères, avec "
                           "<b>du HTML</b> qui doit disparaître et une fin qu'on ne doit pas voir apparaître jusqu'au bout.</p>",
            "date": (maintenant - timedelta(hours=2)).isoformat()},
           {"title": "RockstarMag (vidéo) : le trailer commenté", "link": "https://www.youtube.com/watch?v=test",
            "description": "", "date": (maintenant - timedelta(hours=3)).isoformat()}]
    autres = [art(n) for n in range(2, 9)]
    apercus = feed_store.apercus_recap([majeur] + autres)
    return officiels, apercus, mag


def etape_exemples(webhook, maintenant):
    print("\nD. Les vrais messages du robot (articles fictifs)")
    officiels, apercus, mag = messages_d_exemple(maintenant)
    vrai = discord_notify.send_discord_with_retry
    envoyes = []

    def envoi_marque(charge, label, **k):
        repli = k.get("repli")
        ok = vrai(marque(charge), label, repli=marque(repli) if repli else None)
        envoyes.append(ok)
        time.sleep(PAUSE_S)
        return ok
    discord_notify.send_discord_with_retry = envoi_marque
    try:
        # Le message complet d'un passage de jour.
        discord_notify.send_message(officiels, apercus, (13, 3, 4), 1,
                                    feed_store.libelle_recap_depuis_comptes(13, 3, 4), maintenant=maintenant,
                                    rockstarmag=mag)
        # La nuit : seule la section officielle.
        discord_notify.send_message(officiels[:1], [], None, 0, feed_store.libelle_officiels(1), maintenant=maintenant)
        # Un message ordinaire, après la sortie.
        discord_notify.send_message([], apercus[1:], (7, 0, 1), 0, feed_store.libelle_recap_depuis_comptes(7, 0, 1),
                                    maintenant=feed_store.SORTIE_GTA6 + timedelta(days=2))
    finally:
        discord_notify.send_discord_with_retry = vrai
    return any(envoyes)


def etape_gris(webhook):
    print("\nE. Style gris « -# » dans une carte")
    ok, _ = envoie(webhook, marque({
        "username": "GTA6_WATCH", "content": "style gris",
        "embeds": [{"description": "Ligne normale\n-# Cette ligne devrait être petite et grise\n*Cette ligne est en italique*",
                    "color": 0x8E5CFF}]}), "style gris")
    return ok


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        print("DISCORD_WEBHOOK_URL absent : rien à envoyer.")
        return 1
    discord_notify.DISCORD_WEBHOOK_URL = webhook
    maintenant = datetime.now(timezone.utc)
    acceptes = [etape_boutons(webhook), etape_modification(webhook), etape_balise(webhook, maintenant),
                etape_exemples(webhook, maintenant), etape_gris(webhook)]
    print(f"\n{sum(bool(a) for a in acceptes)}/{len(acceptes)} étapes ont fait accepter au moins un message.")
    # Une fonction refusée est un résultat. Aucun message accepté du tout : une panne.
    return 0 if any(acceptes) else 1


if __name__ == "__main__":
    sys.exit(main())
