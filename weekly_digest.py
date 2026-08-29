"""
Récapitulatif hebdomadaire — un message Discord le dimanche soir.

Pourquoi séparé des notifications de passage
--------------------------------------------
Le récapitulatif de passage répond à « quoi de neuf dans la dernière
demi-heure ». Il annonce un NOMBRE, volontairement sans titre : sur un
téléphone, une notification doit se lire d'un coup d'œil.

Celui-ci répond à une autre question : « qu'est-ce que j'ai raté cette
semaine ». On ne le lit pas en passant, on l'ouvre. Il porte donc bien des
titres et des liens — c'est tout son intérêt.

Discord seulement, pas de push : la valeur est dans la liste cliquable, ce
qu'une bannière de notification ne sait pas montrer.

Classement
----------
Par nombre de rédactions ayant couvert le sujet, pas par date. C'est le
seul indicateur d'importance dont on dispose sans lire les articles, et
c'est déjà celui qui pilote le badge « N SOURCES » de l'app.
"""

import sys
from datetime import datetime, timedelta, timezone

import feed_store

SITE_URL = "https://antoniman31.github.io/gta6-backend/"


def lien_markdown(titre, url):
    """Construit un lien Discord `[titre](url)` qui ne peut pas se casser.

    La syntaxe Markdown est fragile aux deux bouts :
      - un crochet dans le TITRE ferme le libellé trop tôt ;
      - une parenthèse dans l'URL ferme la cible trop tôt — et les URL
        parenthésées existent bel et bien (pages de type « GTA (série) »).
    Dans les deux cas le message s'afficherait cassé, avec un bout d'URL en
    texte brut. On neutralise donc les uns et on encode les autres.
    """
    titre = (titre or "sans titre").replace("[", "(").replace("]", ")")
    if len(titre) > 90:
        titre = titre[:89] + "…"
    url = (url or SITE_URL).replace("(", "%28").replace(")", "%29")
    return f"[{titre}]({url})"

# Nombre de sujets listés. Au-delà, le message devient un mur de texte que
# personne ne lit — l'inverse du but recherché.
TOP_N = 8

JOURS = 7


def articles_de_la_semaine(items, maintenant=None, jours=JOURS):
    """Les articles publiés dans les `jours` derniers jours."""
    maintenant = maintenant or datetime.now(timezone.utc)
    limite = maintenant - timedelta(days=jours)
    recents = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        quand = feed_store.parse_date_key(item.get("date"))
        # Une date future (horloge d'éditeur mal réglée, ça arrive) ne doit
        # pas faire disparaître l'article du récapitulatif.
        if quand >= limite:
            recents.append(item)
    return recents


def classer(items):
    """Trie par nombre de rédactions, puis par date, du plus fort au plus récent.

    À nombre de sources égal, le plus récent d'abord : entre deux sujets
    aussi repris, c'est le plus frais qui intéresse.
    """
    return sorted(
        items,
        key=lambda i: (1 + len(i.get("extraSources") or []),
                       feed_store.parse_date_key(i.get("date"))),
        reverse=True,
    )


def construire_embed(items, maintenant=None):
    """Construit le message, ou None s'il n'y a rien à raconter."""
    recents = articles_de_la_semaine(items, maintenant)
    if not recents:
        return None

    classes = classer(recents)[:TOP_N]
    lignes = []
    for item in classes:
        n = 1 + len(item.get("extraSources") or [])
        # Le nombre de rédactions est affiché dès qu'il y en a plus d'une :
        # c'est le classement du récapitulatif, autant qu'il soit lisible.
        # La flamme, elle, reste réservée au seuil d'actu majeure.
        if n >= feed_store.HOT_SOURCE_THRESHOLD:
            marque = f"🔥 **{n} sources** · "
        elif n > 1:
            marque = f"**{n} sources** · "
        else:
            marque = ""
        lignes.append(marque + lien_markdown(item.get("title"), item.get("link")))

    officiels = sum(1 for i in recents if i.get("official"))
    sous_titre = f"**{len(recents)} articles** cette semaine"
    if officiels:
        sous_titre += f", dont {officiels} de source officielle"

    return {
        "title": f"📅 GTA 6 — la semaine en {len(classes)} sujets",
        "url": SITE_URL,
        "description": sous_titre + "\n\n" + "\n".join(lignes),
        "color": 0x5493FF,
    }


def main():
    import discord_notify

    if not discord_notify.DISCORD_WEBHOOK_URL:
        print("[hebdo] DISCORD_WEBHOOK_URL absent — récapitulatif désactivé.")
        return 0

    items = feed_store.load_items()
    embed = construire_embed(items)
    if embed is None:
        print("[hebdo] aucun article cette semaine — rien à envoyer.")
        return 0

    print("[hebdo] envoi du récapitulatif hebdomadaire...")
    discord_notify.send_discord_with_retry(embed, "récapitulatif hebdomadaire")
    # Comme les autres notifications : jamais faire échouer le workflow.
    return 0


if __name__ == "__main__":
    sys.exit(main())
