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
from datetime import datetime, timezone

import requests

import feed_store

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
SITE_URL = "https://antoniman31.github.io/gta6-backend/"


def send_discord_with_retry(embed, title_for_log, max_attempts=3):
    """Envoie un embed (ou un message complet) Discord avec nouvelle tentative en cas d'erreur
    temporaire (429 rate-limit ou 5xx serveur). Un 429 renvoie généralement
    un délai précis à respecter (Retry-After) — on l'utilise si présent,
    sinon un backoff exponentiel simple (2s, 4s, 8s...). Les erreurs
    définitives (ex: 400 webhook malformé, 404 webhook supprimé) ne sont
    jamais retentées, ça ne changerait rien."""
    # Un message complet (expéditeur, aperçu, plusieurs cartes) porte déjà sa
    # clé `embeds` ; un embed seul est enveloppé comme avant.
    charge = embed if "embeds" in embed else {"embeds": [embed]}
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json=charge, timeout=10)
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


COULEUR_MAJEURE = 0xE04F5F

# Discord refuse TOUT le message (400, non retenté) si la somme des cartes
# dépasse 6 000 caractères. Quatre sections de cinq lignes de 343 caractères
# en feraient 6 860 : on coupe donc avant, en laissant de la marge.
CARTES_MAX_CARACTERES = 5800
LIGNES_PAR_SECTION = 5

SECTION_OFFICIEL = "officiel"
SECTION_MAJEURE = "majeure"
SECTION_AUTRES = "autres"

# Quand il faut couper, on commence par la section la moins importante.
# L'officiel n'est jamais coupé : c'est ce pour quoi cette veille existe.
ORDRE_DE_COUPE = (SECTION_AUTRES, SECTION_MAJEURE)


def balise_temps(instant, style="R"):
    """Une balise de temps Discord, recalculée à l'affichage."""
    return f"<t:{int(instant.timestamp())}:{style}>"


def nom_expediteur(avec_officiel, avec_majeure):
    """Le nom affiché au-dessus du message : la plus forte catégorie présente."""
    if avec_officiel:
        return "⭐ Rockstar"
    if avec_majeure:
        return "🚨 Actu majeure"
    return "🎮 GTA6_WATCH"


def pied_du_message(maintenant=None):
    """Le compte à rebours vivant et le lien vers l'app, en dernière ligne."""
    maintenant = maintenant or datetime.now(timezone.utc)
    sortie = feed_store.SORTIE_GTA6
    if maintenant < sortie:
        decompte = f"⏳ Sortie {balise_temps(sortie)}"
    else:
        decompte = f"🎮 Sorti {balise_temps(sortie)}"
    return f"{decompte} · [Ouvrir GTA6_WATCH]({SITE_URL})"


def nouvelle_section(nom, entete, couleur, apercus, total):
    """Une section : au plus cinq lignes, et le compte exact de ce qu'elle cache."""
    lignes = [l for l in (ligne_apercu(a) for a in (apercus or ())[:LIGNES_PAR_SECTION]) if l]
    if not lignes:
        return None
    return {"nom": nom, "entete": entete, "couleur": couleur, "lignes": lignes,
            "total": max(int(total or 0), len(lignes))}


def rend_section(section, pied=""):
    """La carte Discord d'une section, avec son « + N autres dans l'app »."""
    lignes = list(section["lignes"])
    cachees = section["total"] - len(lignes)
    if cachees > 0:
        lignes.append(f"*+ {cachees} autre{'s' if cachees > 1 else ''} dans l'app*")
    if pied:
        lignes += ["", pied]
    return {"title": f"{section['entete']} · {section['total']}",
            "description": "\n".join(lignes), "color": section["couleur"]}


def _taille(embeds):
    return sum(len(e.get("title", "")) + len(e.get("description", "")) for e in embeds)


def compose_message(sections, content, pied, maximum=CARTES_MAX_CARACTERES):
    """Assemble les cartes d'un message en restant sous la limite de Discord.

    Rend (cartes, nombre de lignes coupées). La coupe retire la dernière ligne
    de « Les autres », puis de l'actu majeure, jamais d'une annonce officielle ;
    le total affiché dans le titre de la section reste juste, le « + N » grossit.
    """
    sections = [dict(s, lignes=list(s["lignes"])) for s in sections if s]
    coupees = 0

    def rendu():
        # Le pied vit dans la dernière carte.
        return [rend_section(s, pied if i == len(sections) - 1 else "")
                for i, s in enumerate(sections)]

    while _taille(rendu()) + len(content) > maximum:
        for nom in ORDRE_DE_COUPE:
            cible = next((s for s in sections if s["nom"] == nom and s["lignes"]), None)
            if cible:
                cible["lignes"].pop()
                coupees += 1
                if not cible["lignes"]:
                    sections.remove(cible)
                break
        else:
            break
    return rendu(), coupees


def send_message(officiels, apercus, totaux, majeurs, content, maintenant=None,
                 apres_sortie=False):
    """Envoie UN SEUL message par passage : des sections colorées sous une
    ligne d'aperçu, et un pied avec le compte à rebours.

    Remplace, depuis le 29/10/2026, le récapitulatif seul et les alertes
    officielles une par une (décision d'Antoni : « un message à sections »).

    `officiels` : les annonces de CE passage, seules à entrer dans la section
    ⭐ — celles de la nuit ont déjà été annoncées quand elles sont tombées.
    `apercus` : les articles de l'arriéré et du passage, rangés par section.
    `totaux` : (articles, officiels, sommet), ou None. Sert au « + N ».
    Après la sortie, « Les autres » ne fait plus partie du message.
    """
    if not DISCORD_WEBHOOK_URL:
        print("[discord] DISCORD_WEBHOOK_URL absent — notification désactivée.")
        return False
    n, off_total, sommet = totaux if totaux else (0, len(officiels), 0)
    par_cat = {"majeur": [], "autre": []}
    for a in apercus or ():
        cat = feed_store.categorie_apercu(a)
        if cat in par_cat:
            par_cat[cat].append(a)

    sections = []
    if officiels:
        sections.append(nouvelle_section(
            SECTION_OFFICIEL, "⭐ Officiel", COULEUR_OFFICIEL,
            feed_store.apercus_recap(officiels), len(officiels)))
    sections.append(nouvelle_section(
        SECTION_MAJEURE, "🚨 Actu majeure", COULEUR_MAJEURE,
        par_cat["majeur"], majeurs))
    if not apres_sortie:
        autres_total = max(0, n - off_total - majeurs)
        sections.append(nouvelle_section(
            SECTION_AUTRES, "🎮 Les autres", feed_store.couleur_compteur(maintenant),
            par_cat["autre"], autres_total))
    sections = [s for s in sections if s]
    pied = pied_du_message(maintenant)
    if sections:
        cartes, coupees = compose_message(sections, content, pied)
    else:
        # Rien à citer (récapitulatif du matin dont les aperçus manquent,
        # lancement à la main) : le message n'en est pas moins vrai. La ligne
        # d'aperçu donne les comptes, et une carte porte le pied — le lien
        # vers l'app, qui était le corps d'avant.
        cartes, coupees = [{"description": pied,
                            "color": feed_store.couleur_compteur(maintenant)}], 0
    charge = {
        "username": nom_expediteur(bool(officiels),
                                   any(s["nom"] == SECTION_MAJEURE for s in sections)
                                   or (totaux is not None and totaux[2] >= feed_store.HOT_SOURCE_THRESHOLD)),
        "content": content[:2000],
        "embeds": cartes,
    }
    detail = ", ".join(f"{s['entete']} {s['total']}" for s in sections) or "sans section"
    print(f"  [discord] envoi du message ({detail}"
          + (f", {coupees} ligne(s) coupée(s) pour tenir sous la limite" if coupees else "") + ")...")
    return send_discord_with_retry(charge, f"message {detail}")


def send_jalons(jalons):
    """Un message par jalon du compte à rebours : J-14, J-7, J-3, J-1, H-12, H-1, sorti.

    Rares, et chacun ne part qu'une fois : c'est fetch_feeds qui décide et qui
    retient l'état dans feed.json. Le décompte est une balise de temps de
    Discord (`<t:…:R>`), recalculée à l'affichage : elle reste juste dans un
    message ancien. Aucune image.
    """
    if not DISCORD_WEBHOOK_URL or not jalons:
        return False
    sortie = int(feed_store.SORTIE_GTA6.timestamp())
    envoyes = 0
    for j in jalons:
        if not isinstance(j, dict):
            continue
        sorti = j.get("type") == "sortie"
        if sorti:
            titre = "🎉 GTA VI est sorti"
            lignes = [f"Sortie <t:{sortie}:R>."]
        else:
            titre = f"⏳ {j.get('libelle')}"
            lignes = [f"GTA VI sort <t:{sortie}:F> · <t:{sortie}:R>"]
        n = int(j.get("articles") or 0)
        off = int(j.get("officiels") or 0)
        bilan = f"Depuis {j.get('depuis')} : {n} article{'s' if n > 1 else ''}"
        bilan += f", dont {off} officiel{'s' if off > 1 else ''} Rockstar" if off else ""
        lignes.append(bilan + ".")
        sujets = [l for l in (ligne_apercu(a) for a in (j.get("sujets") or ())) if l]
        if sujets:
            lignes.append("**Les sujets les plus couverts**")
            lignes.extend(sujets)
        lignes.append(f"[Ouvrir GTA6_WATCH]({SITE_URL})")
        embed = {
            "title": titre,
            "url": SITE_URL,
            "description": "\n".join(lignes)[:DESCRIPTION_MAX_CARACTERES],
            "color": feed_store.couleur_compteur(),
        }
        if send_discord_with_retry(embed, f"jalon {j.get('id')}"):
            envoyes += 1
    print(f"  [discord] {envoyes} jalon(s) du compte à rebours envoyé(s) sur {len(jalons)}.")
    return envoyes > 0


def send_alertes_report(articles):
    """Une alerte rouge par article OFFICIEL qui parle d'un report de la sortie.

    Voir feed_store.MOTIF_REPORT : le motif est volontairement étroit, et
    limité aux articles officiels. Elle part aussi la nuit, comme une annonce
    de Rockstar : un report ne se découvre pas au réveil.
    """
    if not DISCORD_WEBHOOK_URL or not articles:
        return False
    envoyes = 0
    for item in articles[:feed_store.NOTIFS_OFFICIELLES_MAX]:
        titre, media = feed_store.separe_titre_et_media(item.get("title"), item.get("source"))
        lien = (item.get("link") or "").strip()
        if any(c in lien for c in CARACTERES_INTERDITS_URL):
            lien = ""
        corps = f"[{echappe_markdown(coupe(titre))}](<{lien}>)" if lien else echappe_markdown(coupe(titre))
        embed = {
            "title": "🚨 Report possible · officiel",
            "url": lien or SITE_URL,
            "description": (corps + "\n\nUn article officiel de Rockstar ou de Take-Two parle d'un "
                            "report : à vérifier à la source."),
            "color": 0xE04F5F,
        }
        if send_discord_with_retry(embed, "alerte de report officielle"):
            envoyes += 1
    print(f"  [discord] {envoyes} alerte(s) de report envoyée(s) sur {len(articles)}.")
    return envoyes > 0


# Orange Rockstar, celui de la pastille OFFICIEL dans l'app (#FF6B00) :
# la même annonce se reconnaît à la même couleur d'un canal à l'autre.
COULEUR_OFFICIEL = 0xFF6B00


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
    # Le décodage Google News passe par le même canal : c'est le même
    # geste pour qui lit — quelque chose s'est arrêté, ou est revenu.
    casses = [a for a in alertes if a.get("type") == "decodage-casse"]
    repares = [a for a in alertes if a.get("type") == "decodage-ok"]
    lignes = []
    for a in casses:
        lignes.append(f"🔴 **Décodage Google News** à l'arrêt — {a.get('raison')}.\n"
                      f"Les liens restent sur news.google.com, et plus aucun "
                      f"article n'est reconnu comme officiel.")
    for a in repares:
        duree = f" après {a.get('heures')} h" if a.get("heures") is not None else ""
        lignes.append(f"🟢 **Décodage Google News** rétabli{duree}.")
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
        "title": "⚠️ État des sources" if (tombees or casses) else "✅ État des sources",
        "url": SITE_URL,
        "description": "\n".join(lignes),
        # Rouge s'il y a une panne, vert si ce sont uniquement des retours.
        "color": 0xE04F5F if (tombees or casses) else 0x4FE07A,
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
    """Enveloppe qui tient la promesse écrite plus bas : cette étape ne doit
    JAMAIS faire échouer le workflow.

    Le commentaire l'affirmait, le code ne le garantissait pas. Une panne de
    Discord était bien absorbée — `send_discord_with_retry` renvoie False —
    mais une EXCEPTION ne l'était pas : un fichier d'articles malformé, un
    article sans la forme attendue, et l'étape sortait en erreur.

    Ce que ça coûtait, et c'est pour ça que c'est corrigé à cinq semaines de
    la sortie : l'étape suivante, la notification push, est conditionnée à
    `if: success()`. Les deux canaux sont réputés indépendants — c'est
    même la raison d'être du second, puisque taper une notification Discord
    ouvre Discord et jamais l'article. Un plantage du premier emportait
    silencieusement le second, le jour où il sert le plus.
    """
    try:
        return _main()
    except Exception as e:
        propre = feed_store.masquer_urls(str(e), [DISCORD_WEBHOOK_URL])
        print(f"[discord] ÉCHEC NON PRÉVU ({type(e).__name__}) : {propre}")
        print("[discord] l'étape se termine quand même : les articles sont "
              "publiés, et la notification push ne doit pas être emportée.")
        return 0


def _main():
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
    # Deux mesures, sans effet sur ce qui part : le retard des annonces, et les
    # médias derrière chaque actu majeure. Elles passent AVANT l'envoi : une
    # panne de Discord ne doit pas les faire disparaître du journal.
    for ligne in feed_store.lignes_retard(officiels):
        print(ligne)
    for ligne in feed_store.lignes_mesure_majeure(list(new_items or ()) + list(promus or ())):
        print(ligne)
    # Une alerte de report passe AVANT tout : c'est la plus importante. Elle ne
    # concerne que des articles officiels, donc `officiels` suffit.
    reports = feed_store.articles_report(officiels)
    if reports:
        print(f"  [report] {len(reports)} article(s) officiel(s) parlent d'un report.")
        send_alertes_report(reports)

    # Les jalons du compte à rebours : décidés par fetch_feeds, annoncés ici,
    # même pendant la pause nocturne (seul « sorti » pourrait y tomber, et la
    # pause est levée cette nuit-là).
    jalons = lire_liste(os.environ.get("JALONS_FILE", ""))
    if jalons:
        send_jalons(jalons)

    if seulement_officiels:
        # La nuit, seule la section ⭐ part, seule dans son message.
        if officiels:
            send_message(officiels, [], None, 0, feed_store.libelle_officiels(len(officiels)))
        print(f"[discord] pause nocturne — {len(officiels)} annonce(s) officielle(s) "
              f"envoyée(s), le reste attend le matin.")
        return 0

    # Les alertes de source sont indépendantes des articles : elles doivent
    # partir même — surtout — quand il n'y a rien de neuf à annoncer.
    alertes = lire_liste(os.environ.get("SOURCE_ALERTS_FILE", ""))
    if alertes:
        send_source_alerts(alertes)

    # L'arriéré de la nuit justifie un message même si CE passage n'apporte
    # rien : à 5h le premier passage peut ne rien trouver de neuf alors que
    # douze articles attendent d'être annoncés.
    totaux = lire_totaux_recap()
    if not new_items and not promus and totaux is None:
        print("[discord] aucun nouvel article à annoncer.")
        return 0

    n_recap, off_recap, sommet_recap = feed_store.comptes_du_recap(
        new_items, promus, totaux)
    # Après la sortie, le message ne part que s'il a une annonce officielle ou
    # une actu majeure, comme le push : les deux canaux se taisent ensemble.
    apres_sortie = datetime.now(timezone.utc) >= feed_store.SORTIE_GTA6
    if not feed_store.recap_merite_notification(n_recap, off_recap, sommet_recap) and not officiels:
        print(f"[discord] après la sortie — {n_recap} article(s) sans annonce "
              f"officielle ni actu majeure, le message se tait.")
        return 0

    # Déposés par le robot à côté des totaux : ils contiennent l'arriéré de la
    # nuit, que new_items ne peut plus contenir. Sans eux (lancement à la main,
    # version antérieure du robot) on retombe sur le lot et les promus : une
    # liste VIDE veut dire « rien déposé », pas « rien à montrer ».
    apercus = feed_store.lire_apercus_recap() or feed_store.apercus_recap(
        list(new_items or ()) + list(promus or ()))
    majeurs = feed_store.lire_majeurs_recap()
    if majeurs is None:
        majeurs = feed_store.compte_majeurs(list(new_items or ()) + list(promus or ()))
    if totaux:
        contenu = feed_store.libelle_recap_depuis_comptes(n_recap, off_recap, sommet_recap)
    else:
        contenu = feed_store.libelle_recap(new_items, promus)
    send_message(officiels, apercus, (n_recap, off_recap, sommet_recap), majeurs,
                 contenu, apres_sortie=apres_sortie)
    # Cette étape ne doit JAMAIS faire échouer le workflow : les articles
    # sont déjà publiés à ce stade, une panne de Discord n'est pas une
    # panne du robot.
    return 0


if __name__ == "__main__":
    sys.exit(main())
