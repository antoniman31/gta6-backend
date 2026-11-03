"""Le tableau du jour : UN message Discord, créé au premier passage de la journée
(5 h, heure de Paris) puis MODIFIÉ à chaque passage.

Demandé par Antoni (spec tasks/spec-discord-notification.md, §6). Vérifié le
03/11/2026 par l'envoi TEST : un lien d'envoi sans bot peut modifier un message
déjà envoyé, sans nouvelle notification (le message affiche « modifié »). La
création notifie une fois par jour ; les modifications, jamais.

Tourne APRÈS la publication, dans le workflow du robot : il lit donc le
docs/feed.json qui vient d'être publié. L'identifiant du message est la seule
chose à retenir d'un passage à l'autre ; il vit dans `tableau-discord.json`, un
petit fichier de la racine que l'étape suivante du workflow commite quand il
change (une fois par jour). L'identifiant n'a rien de secret — le secret est le
jeton du lien d'envoi, qui n'est JAMAIS écrit ici : on refuse de retenir autre
chose qu'un nombre.

Cette étape ne doit JAMAIS faire échouer le workflow : les articles sont déjà
publiés, et une panne de Discord n'est pas une panne du robot.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

import discord_notify
import feed_store

ETAT_PATH = "tableau-discord.json"
COULEUR_TABLEAU = 0x4FE07A          # vert, comme le tableau de la spec
FUTUR_TOLERE_MIN = 5
# Un identifiant de message Discord est un « snowflake » : un entier de 17 à 20
# chiffres. Tout le reste (un jeton, une adresse) est refusé à l'écriture.
RE_IDENTIFIANT = re.compile(r"^\d{15,25}$")


def jour_paris(maintenant):
    return maintenant.astimezone(feed_store.PARIS).date()


def bornes_du_jour(maintenant):
    """Minuit à minuit, heure de Paris, en UTC. Le jour du changement d'heure en fait 23 ou 25."""
    jour = jour_paris(maintenant)
    debut = datetime(jour.year, jour.month, jour.day, tzinfo=feed_store.PARIS)
    lendemain = jour + timedelta(days=1)
    fin = datetime(lendemain.year, lendemain.month, lendemain.day, tzinfo=feed_store.PARIS)
    return debut.astimezone(timezone.utc), fin.astimezone(timezone.utc)


def stats_du_jour(items, maintenant):
    """Ce que le tableau affiche : le total du jour, les langues, les médias, les officiels, le dernier."""
    debut, fin = bornes_du_jour(maintenant)
    plafond = maintenant + timedelta(minutes=FUTUR_TOLERE_MIN)
    total = fr = en = officiels = 0
    medias, dernier = set(), None
    for it in items or ():
        if not isinstance(it, dict):
            continue
        quand = feed_store.parse_date_key(it.get("date") or "")
        if quand == feed_store.DATE_FLOOR or not (debut <= quand < fin) or quand > plafond:
            continue
        total += 1
        fr += it.get("lang") == "fr"
        en += it.get("lang") == "en"
        officiels += bool(it.get("official"))
        site = feed_store.site_du_lien(it.get("link") or "")
        if site:
            medias.add(site)
        if dernier is None or quand > dernier[0]:
            dernier = (quand, it)
    return {"jour": jour_paris(maintenant).isoformat(), "jj_mm": jour_paris(maintenant).strftime("%d/%m"),
            "total": total, "fr": fr, "en": en, "medias": len(medias), "officiels": officiels,
            "dernier": dernier}


def pluriel(n, mot):
    return f"{n} {mot}{'s' if n > 1 else ''}"


def message_tableau(stats, maintenant):
    """Le message complet : une ligne d'aperçu et une carte verte."""
    titre = f"📊 {stats['jj_mm']} · {pluriel(stats['total'], 'article')}"
    lignes = []
    if stats["total"]:
        lignes.append(f"🇫🇷 {stats['fr']} · 🇬🇧 {stats['en']} · {pluriel(stats['medias'], 'média')}")
    else:
        lignes.append("Rien de neuf pour l'instant aujourd'hui.")
    if stats["officiels"]:
        lignes.append(f"⭐ {pluriel(stats['officiels'], 'annonce')} officielle{'s' if stats['officiels'] > 1 else ''} Rockstar")
    if stats["dernier"]:
        quand, it = stats["dernier"]
        titre_brut, _ = feed_store.separe_titre_et_media(it.get("title"), it.get("source"))
        texte = discord_notify.echappe_markdown(discord_notify.coupe(titre_brut))
        lien = (it.get("link") or "").strip()
        if texte:
            corps = f"[{texte}](<{lien}>)" if lien and not any(c in lien for c in discord_notify.CARACTERES_INTERDITS_URL) else texte
            lignes.append(f"Dernier : {corps} — {discord_notify.balise_temps(quand)}")
    lignes += ["", discord_notify.pied_du_message(maintenant)]
    return {"content": f"📊 Tableau du jour — {stats['jj_mm']}",
            "embeds": [{"title": titre, "description": "\n".join(lignes), "color": COULEUR_TABLEAU}]}


def etat_lu(chemin=ETAT_PATH):
    """L'état retenu : {"jour": "AAAA-MM-JJ", "id": "…"}, ou {} à la moindre anomalie."""
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            brut = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if (isinstance(brut, dict) and isinstance(brut.get("jour"), str) and isinstance(brut.get("id"), str)
            and RE_IDENTIFIANT.match(brut["id"]) and re.match(r"^\d{4}-\d{2}-\d{2}$", brut["jour"])):
        return {"jour": brut["jour"], "id": brut["id"]}
    return {}


def etat_ecrit(etat, chemin=ETAT_PATH):
    """Écrit l'état, mais seulement s'il ne contient qu'un jour et un identifiant numérique."""
    if not (isinstance(etat.get("id"), str) and RE_IDENTIFIANT.match(etat["id"])):
        raise ValueError("identifiant de message invalide : refusé (jamais un jeton)")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump({"jour": etat["jour"], "id": etat["id"]}, f, indent=2, sort_keys=True)
        f.write("\n")


def _dit(reponse):
    try:
        corps = reponse.json() if reponse.text else {}
        if isinstance(corps, dict) and ("code" in corps or "message" in corps):
            return f"HTTP {reponse.status_code} · code {corps.get('code')} · {str(corps.get('message'))[:100]}"
    except Exception:
        pass
    return f"HTTP {reponse.status_code}"


def met_a_jour(webhook, etat, stats, maintenant):
    """Modifie le tableau du jour s'il existe, le crée sinon. Rend (état, ce qui s'est passé)."""
    message = message_tableau(stats, maintenant)
    base = webhook.split("?", 1)[0].rstrip("/")
    if etat.get("jour") == stats["jour"] and etat.get("id"):
        try:
            r = requests.patch(f"{base}/messages/{etat['id']}", json=message, timeout=15)
        except Exception as e:
            print(f"  [tableau] erreur réseau à la modification ({type(e).__name__}) : "
                  f"{feed_store.masquer_urls(str(e), [webhook])}")
            return etat, "échec transitoire"
        if r.status_code in (200, 204):
            return etat, "modifié"
        print(f"  [tableau] modification refusée — {_dit(r)}")
        # Un message supprimé (404) se recrée. Toute autre erreur (5xx, 429) peut
        # passer seule : en créer un second dupliquerait le tableau et sonnerait.
        if r.status_code != 404:
            return etat, "échec transitoire"
    creation = dict(message, username="📊 GTA6_WATCH")
    try:
        r = requests.post(base, params={"wait": "true"}, json=creation, timeout=15)
    except Exception as e:
        print(f"  [tableau] erreur réseau à la création ({type(e).__name__}) : "
              f"{feed_store.masquer_urls(str(e), [webhook])}")
        return etat, "échec"
    if r.status_code not in (200, 204):
        print(f"  [tableau] création refusée — {_dit(r)}")
        return etat, "échec"
    try:
        identifiant = str(r.json().get("id") or "")
    except Exception:
        identifiant = ""
    if not RE_IDENTIFIANT.match(identifiant):
        print("  [tableau] Discord n'a pas rendu d'identifiant de message : le tableau ne pourra pas être modifié.")
        return etat, "échec"
    return {"jour": stats["jour"], "id": identifiant}, "créé"


def main():
    try:
        return _main()
    except Exception as e:
        print(f"[tableau] ÉCHEC NON PRÉVU ({type(e).__name__}) : "
              f"{feed_store.masquer_urls(str(e), [os.environ.get('DISCORD_WEBHOOK_URL', '')])}")
        print("[tableau] l'étape se termine quand même : les articles sont publiés.")
        return 0


def _main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        print("[tableau] DISCORD_WEBHOOK_URL absent — tableau du jour désactivé.")
        return 0
    if os.environ.get("SEULEMENT_OFFICIELS") == "1":
        print("[tableau] pause nocturne — le tableau du jour est créé au premier passage de 5 h.")
        return 0
    maintenant = datetime.now(timezone.utc)
    chemin_etat = os.environ.get("TABLEAU_ETAT", ETAT_PATH)
    items = feed_store.load_feed().get("items", [])
    stats = stats_du_jour(items, maintenant)
    etat = etat_lu(chemin_etat)
    nouvel, action = met_a_jour(webhook, etat, stats, maintenant)
    print(f"[tableau] {action} — {stats['jj_mm']} : {pluriel(stats['total'], 'article')}, "
          f"{stats['fr']} FR, {stats['en']} EN, {pluriel(stats['medias'], 'média')}, "
          f"{pluriel(stats['officiels'], 'officiel')}.")
    if nouvel != etat:
        etat_ecrit(nouvel, chemin_etat)
        print("[tableau] identifiant du message retenu pour les passages suivants.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
