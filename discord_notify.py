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


# ---------------------------------------------------------------------------
# Le corps du récapitulatif : les derniers articles, cliquables
# ---------------------------------------------------------------------------
# Antoni, le 22/09/2026 : « faut aussi que tu détailles les notif discord avec
# des liens pour les 5 derniers article ».
#
# Le TITRE de l'embed ne bouge pas d'un caractère : c'est le texte partagé
# mot pour mot avec les notifications push (feed_store.libelle_recap), et
# l'invariant « les deux canaux disent la même chose » tient toujours. Ce qui
# s'ajoute, c'est le corps du message — propre à Discord, qui est le seul des
# deux à savoir afficher cinq liens cliquables.

# Discord tolère 4096 caractères de description. On est loin du compte avec
# cinq lignes, mais un titre du fil monte déjà à 137 caractères et rien ne
# garantit qu'un flux n'en enverra pas un de 500.
TITRE_MAX_CARACTERES = 100

# La vraie limite Discord pour une description d'embed est 4096. On s'arrête
# avant : la ligne « + N autres dans l'app » s'ajoute APRÈS le calcul, et
# mieux vaut de la marge qu'un 400 sur un caractère.
DESCRIPTION_MAX_CARACTERES = 3900

# Caractères qui changent le SENS d'une ligne Markdown Discord. Sans
# échappement, « GTA 6's soundtrack has *checks notes* Keith Richards »
# perdrait ses astérisques et passerait en italique, et un titre contenant
# « [GTA6] » casserait le lien qui l'entoure. Les deux existent dans le fil.
MARKDOWN_A_ECHAPPER = "\\[]*_`~|"

# Un lien Markdown se termine à la première parenthèse fermante. Aucun des
# 1 811 liens du fil n'en contient, ni d'espace — mais un flux mal formé
# suffirait à faire déborder le lien sur le reste de la ligne, et une ligne
# cassée est pire qu'une ligne sans lien.
CARACTERES_INTERDITS_URL = "() <>"


def echappe_markdown(texte):
    """Neutralise ce qui ferait dérailler la mise en forme Discord."""
    for c in MARKDOWN_A_ECHAPPER:
        texte = texte.replace(c, "\\" + c)
    return texte


def coupe(texte, maximum=TITRE_MAX_CARACTERES):
    """Coupe un titre trop long sur un espace, avec une ellipse."""
    texte = (texte or "").strip()
    if len(texte) <= maximum:
        return texte
    tronque = texte[:maximum].rstrip()
    espace = tronque.rfind(" ")
    # Un titre sans le moindre espace dans ses 100 premiers caractères existe
    # (URL recopiée en guise de titre) : on coupe alors au caractère près
    # plutôt que de rendre une chaîne vide.
    if espace > maximum // 2:
        tronque = tronque[:espace]
    return tronque.rstrip(" ,;:-–—") + "…"


def ligne_apercu(apercu):
    """Une ligne du récapitulatif : marqueur, titre cliquable, média.

    Le marqueur reprend la hiérarchie appliquée partout ailleurs — ⭐ pour une
    publication de Rockstar, 🔥 pour un sujet couvert par assez de rédactions
    pour être une actu majeure, • pour le reste.
    """
    titre_brut, media = feed_store.separe_titre_et_media(
        apercu.get("title"), apercu.get("source"))
    titre = echappe_markdown(coupe(titre_brut))
    if not titre:
        return ""

    lien = (apercu.get("link") or "").strip()
    if any(c in lien for c in CARACTERES_INTERDITS_URL):
        lien = ""

    sources = apercu.get("sources") or 1
    if apercu.get("official"):
        marqueur = "⭐"
    elif sources >= feed_store.HOT_SOURCE_THRESHOLD:
        marqueur = "🔥"
    else:
        marqueur = "•"

    corps = f"[{titre}](<{lien}>)" if lien else titre
    ligne = f"{marqueur} {corps}"
    if media:
        ligne += f" — *{echappe_markdown(media)}*"
    # Le nombre de rédactions n'est dit que quand il est l'information : sur
    # une ligne ordinaire il n'apprendrait rien.
    if sources >= feed_store.HOT_SOURCE_THRESHOLD:
        ligne += f" · {sources} sources"
    return ligne


def description_recap(apercus, total=0):
    """Le corps de l'embed : les derniers articles, puis le lien vers l'app.

    Rend le corps d'avant — le seul lien vers le site — quand il n'y a rien à
    citer. C'est le cas d'un récapitulatif déclenché par un sujet devenu
    majeur sans aucun article neuf, et celui d'un fichier d'aperçus absent
    (version antérieure du robot, ou lancement à la main).
    """
    lignes = [l for l in (ligne_apercu(a) for a in (apercus or ())) if l]
    pied = f"[Ouvrir GTA6_WATCH]({SITE_URL})"
    if not lignes:
        return pied

    # Une ligne mesure ~250 caractères, donc cinq lignes sont très loin des
    # 4096 tolérés par Discord. Mais un lien est de longueur arbitraire, et
    # Discord ne tronque pas une description trop longue : il REFUSE l'embed
    # entier avec un 400, que send_discord_with_retry ne retente pas. Un
    # récapitulatif amputé d'une ligne vaut mieux qu'un récapitulatif perdu.
    while lignes and len("\n".join(lignes)) + len(pied) + 2 > DESCRIPTION_MAX_CARACTERES:
        lignes.pop()
    if not lignes:
        return pied

    restants = max(0, int(total or 0) - len(lignes))
    if restants:
        lignes.append(f"*+ {restants} autre{'s' if restants > 1 else ''} "
                      f"dans l'app*")
    return "\n".join(lignes) + f"\n\n{pied}"


def send_discord_notification(new_items, promus=(), apercus=None):
    """Envoie UN SEUL message Discord récapitulatif : le nombre de nouveaux
    articles en titre, et sous ce titre les derniers d'entre eux, cliquables.

    Un seul message, toujours — la règle qui interdit un message PAR article
    tient : cinq liens dans un même embed, ce n'est pas cinq messages.

    Discord mobile ouvre toujours l'app Discord au tap sur une notification
    (jamais une URL externe directement), donc les liens restent cliquables
    DANS le message une fois Discord ouvert, pas au moment du tap sur la
    notification système elle-même.

    `apercus` : les articles à citer, déposés par le robot. Ils sont passés
    explicitement plutôt que redérivés de `new_items`, parce que le
    récapitulatif du matin porte l'arriéré de la nuit — des articles qui ne
    sont plus « nouveaux » à 5h et ne figurent donc dans aucune liste. None
    (lancement à la main) fait retomber sur new_items + promus.

    N'échoue jamais l'étape si Discord est indisponible ou mal configuré."""
    if not DISCORD_WEBHOOK_URL:
        print("[discord] DISCORD_WEBHOOK_URL absent — notification désactivée.")
        return False
    # Les totaux déposés par le robot incluent l'arriéré de la nuit ; sans
    # eux (lancement local), on compte la liste, ce qui reste juste hors
    # pause nocturne.
    totaux = lire_totaux_recap()
    # La garde vient APRÈS la lecture des totaux, et pas avant : à 5h le
    # premier passage de la journée peut ne rien trouver de neuf alors que
    # douze articles de la nuit attendent d'être annoncés. Placée avant,
    # elle faisait taire Discord dans exactement le cas qui justifie tout
    # ce mécanisme — et main() avait beau l'appeler, la fonction repartait
    # sans rien envoyer.
    #
    # On teste la PRÉSENCE du fichier, pas son contenu. Décider ici « 0
    # article, donc rien à dire » écrasait le cas où la nuit n'a apporté
    # aucun article NEUF mais où un sujet déjà connu est devenu majeur : le
    # robot dépose alors {articles:0, sommet:4}, et l'alerte « actu majeure »
    # partait à la poubelle — précisément la notification qu'on ne veut pas
    # rater, et le cas pour lequel le mécanisme `promus` avait été écrit.
    # C'est le robot qui tranche, une seule fois, en ne déposant le fichier
    # que s'il y a quelque chose à annoncer.
    if not new_items and not promus and totaux is None:
        return False
    if totaux:
        n, officiels, sommet = totaux
        titre = feed_store.libelle_recap_depuis_comptes(n, officiels, sommet)
        majeure = sommet >= feed_store.HOT_SOURCE_THRESHOLD
    else:
        n = len(new_items)
        titre = feed_store.libelle_recap(new_items, promus)
        majeure = feed_store.est_actu_majeure(new_items, promus)

    embed = {
        # Texte partagé avec les notifications push : voir
        # feed_store.libelle_recap. Les deux canaux disent mot pour mot la
        # même chose, et ne peuvent plus diverger.
        "title": titre,
        "url": SITE_URL,
        # Les derniers articles, cliquables, puis le lien vers l'app. Quand
        # il n'y a rien à citer, description_recap rend exactement le corps
        # d'avant — le récapitulatif ne perd rien.
        "description": description_recap(
            apercus if apercus is not None
            else feed_store.apercus_recap(list(new_items or ()) + list(promus or ())),
            n),
        # Rouge d'alerte pour une actu majeure, bleu habituel sinon : la
        # couleur se repère d'un coup d'œil dans un salon Discord.
        "color": 0xE04F5F if majeure else 0x5493FF,
    }
    detail = f"{n} nouvel(le)(s) article(s)"
    if promus:
        detail += f", {len(promus)} sujet(s) devenu(s) majeur(s)"
    print(f"  [discord] envoi du récapitulatif ({detail})...")
    return send_discord_with_retry(embed, f"récapitulatif {detail}")


# Orange Rockstar, celui de la pastille OFFICIEL dans l'app (#FF6B00) :
# la même annonce se reconnaît à la même couleur d'un canal à l'autre.
COULEUR_OFFICIEL = 0xFF6B00


def send_official_alerts(officiels):
    """Une alerte SÉPARÉE par article publié par Rockstar lui-même.

    Deuxième exception assumée à la règle « un seul message par passage »,
    après les alertes de source. Elle se justifie de la même façon : la
    règle existe pour empêcher un message par ARTICLE ordinaire, et le
    volume reste dérisoire. Mesuré sur l'historique complet du dépôt :
    34 articles officiels sur 2 183, répartis sur 15 journées en presque
    trois ans, au pire 4 dans la même journée.

    Le récapitulatif continue de les COMPTER (« dont 1 officiel Rockstar »),
    il n'est pas amputé : il annonce un volume, ces alertes annoncent un
    contenu. Sur les rares passages où les deux partent, la redondance est
    le prix d'un récapitulatif qui ne ment pas sur ses chiffres.
    """
    if not DISCORD_WEBHOOK_URL or not officiels:
        return False

    envoyees = 0
    for item in officiels:
        entete, titre = feed_store.libelle_officiel(item)
        lien = (item.get("link") or "").strip()
        source = (item.get("source") or "Rockstar Games").strip()
        embed = {
            "title": f"{entete} · {titre}"[:250],
            # Le lien pointe sur l'ARTICLE et non sur le site : c'est une
            # annonce précise, pas une invitation à venir voir.
            "url": lien or SITE_URL,
            "description": f"**{source}**\n[Ouvrir dans GTA6_WATCH]({SITE_URL})",
            "color": COULEUR_OFFICIEL,
        }
        if send_discord_with_retry(embed, f"officiel Rockstar — {titre[:40]}"):
            envoyees += 1
    print(f"  [discord] {envoyees}/{len(officiels)} alerte(s) officielle(s) envoyée(s).")
    return envoyees > 0


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
        # En heures et non en passages : un passage n'est pas une unité de
        # temps, l'écart entre deux va de 30 min à près de 5 h selon que
        # GitHub honore ou abandonne l'exécution planifiée. « depuis 6
        # passages » ne disait donc rien d'exploitable.
        lignes.append(f"🔴 **{a.get('name')}** ne renvoie plus rien "
                      f"depuis {a.get('heures')} h.")
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


# Déplacée dans feed_store : les deux canaux doivent lire
# EXACTEMENT la même chose, deux copies finiraient par diverger.
lire_totaux_recap = feed_store.lire_totaux_recap

# Déplacée dans feed_store : les deux canaux doivent lire
# EXACTEMENT la même chose, deux copies finiraient par diverger.
lire_liste = feed_store.lire_liste


# Note : une tentative de notification via ntfy.sh a été faite puis
# abandonnée — le serveur confirmait l'envoi (200 OK) sans jamais relayer
# les messages, probablement à cause d'un fail2ban/rate-limit silencieux
# sur les IP partagées des runners GitHub Actions. Détails dans README.md.


def main():
    path = os.environ.get("NEW_ITEMS_FILE", "")
    if not path:
        print("[discord] NEW_ITEMS_FILE non défini — rien à notifier.")
        return 0

    new_items = lire_liste(path)
    # Un sujet devenu majeur justifie un message même sans article nouveau :
    # voir feed_store.libelle_recap.
    promus = lire_liste(os.environ.get("PROMOTED_ITEMS_FILE", ""))

    # Pendant la pause nocturne, le robot tourne et publie normalement mais
    # ne dit rien — SAUF pour une annonce de Rockstar. Les trois plus
    # grosses de l'histoire du jeu sont tombées entre 2h24 et 3h48, heure
    # de Paris : la révélation de décembre 2023, le premier trailer, et
    # l'Extended Look. Une pause qui les retiendrait jusqu'à 5h raterait
    # exactement ce pour quoi cette veille existe.
    seulement_officiels = os.environ.get("SEULEMENT_OFFICIELS") == "1"

    officiels = feed_store.articles_officiels(new_items)
    if officiels:
        send_official_alerts(officiels)

    if seulement_officiels:
        print(f"[discord] pause nocturne — {len(officiels)} annonce(s) officielle(s) "
              f"envoyée(s), le reste attend le matin.")
        return 0

    # Les alertes de source sont indépendantes des articles : elles doivent
    # partir même — surtout — quand il n'y a rien de neuf à annoncer.
    alertes = lire_liste(os.environ.get("SOURCE_ALERTS_FILE", ""))
    if alertes:
        send_source_alerts(alertes)

    # L'arriéré de la nuit justifie un récapitulatif même si CE passage
    # n'apporte rien : à 5h le premier passage peut ne rien trouver de neuf
    # alors que douze articles attendent d'être annoncés.
    totaux = lire_totaux_recap()
    if not new_items and not promus and totaux is None:
        print("[discord] aucun nouvel article à annoncer.")
        return 0

    # Déposés par le robot à côté des totaux : ils contiennent l'arriéré de
    # la nuit, que new_items ne peut plus contenir.
    # `or None` et non la liste telle quelle : une liste VIDE veut dire « le
    # robot n'a rien déposé » (fichier absent, version antérieure, lancement à
    # la main), et doit faire retomber sur new_items — pas produire un
    # récapitulatif sans le moindre lien alors qu'on a les articles sous la
    # main.
    send_discord_notification(new_items, promus,
                              feed_store.lire_apercus_recap() or None)
    # Cette étape ne doit JAMAIS faire échouer le workflow : les articles
    # sont déjà publiés à ce stade, une panne de Discord n'est pas une
    # panne du robot.
    return 0


if __name__ == "__main__":
    sys.exit(main())
