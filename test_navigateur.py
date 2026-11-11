"""
Contrôles rendus — ce que la lecture du code ne peut pas voir.

    python test_navigateur.py

test_pipeline.py lit les fichiers et vérifie que ce qui est ÉCRIT est
cohérent. Ces contrôles-ci ouvrent l'app dans un vrai Chromium et mesurent ce
qui est DESSINÉ. Les deux ne voient pas les mêmes défauts.

Quatre défauts du 16/09/2026, tous invisibles à la lecture, illustrent la
différence :

  - `storageGet(...) || ""` comparait un OBJET à une chaîne — l'accordéon se
    rouvrait à chaque rechargement. Le code se lisait parfaitement.
  - `display:grid` battait le `display:none` que l'attribut `hidden` apporte :
    les familles de sources se rendaient dépliées MALGRÉ leur `hidden`. La
    liste faisait 3244 px au lieu de 244.
  - Une entête `position:sticky` avec une marge négative ne peut pas remonter
    au-dessus de son bloc conteneur : elle recouvrait quatre pixels du haut de
    chaque onglet, coupant le haut des lettres.
  - Un `display` posé en style inline transformait un bouton en conteneur
    flex, et son libellé remontait de 8,5 px.

Deux de ces défauts rendaient l'app PIRE qu'avant le changement censé
l'améliorer.

---

CE QUI ENTRE ICI, ET CE QUI N'Y ENTRE PAS.

Un contrôle qui échoue au hasard est pire que pas de contrôle : on apprend à
ignorer le rouge, et le vrai défaut passe avec le reste. N'entrent donc ici
que des mesures DÉTERMINISTES — des géométries, des présences, des attributs.

  On inclut     : tailles de cible, débordements, replié/déplié, centrage,
                  état annoncé, trois comportements au clic, et le repli de
                  lecture du backend.
  On n'inclut pas : comparaisons d'images, animations, délais, parcours à
                  plusieurs étapes, tout ce qui dépend du réseau.

Le repli du backend mérite un mot, parce qu'il a l'air d'enfreindre la règle
ci-dessus. Il ne dépend d'aucun réseau : le test FABRIQUE la panne avec
page.route(), qui intercepte la requête avant qu'elle ne parte. Une coupure
simulée est aussi déterministe qu'une géométrie — et c'est le seul moyen de
voir ce défaut, invisible à la lecture comme en conditions normales.

Le fil est servi depuis un serveur local jetable : aucun accès réseau, donc
aucune raison de rougir un jour où un site tiers est lent.

Volontairement sans pytest, comme test_pipeline.py, et volontairement séparé
de lui : celui-là doit continuer de tourner partout sans navigateur.
"""

import http.server
import os
import socketserver
import json
import re
import sys
import threading

CHECKS = 0
FAILURES = []

# 320 px est le plus étroit des écrans visés, 390 celui du téléphone qui
# consulte le fil tous les jours. Les défauts de débordement n'apparaissent
# qu'au plus étroit ; ceux de mise en page, souvent qu'au plus large.
LARGEURS = (320, 390)

# Seuils, nommés une fois plutôt que semés dans les assertions.
CIBLE_MINIMALE = 44      # WCAG 2.5.5 — ce que le reste de l'app s'impose
AIR_MINIMAL = 13         # marge latérale autour d'un libellé de bouton
DECENTRAGE_TOLERE = 1.0  # en pixels, horizontal comme vertical


def check(condition, label):
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  ÉCHEC {label}")
        FAILURES.append(label)


class _Silencieux(http.server.SimpleHTTPRequestHandler):
    """Le serveur de test ne doit pas noyer la sortie sous ses journaux."""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory="docs", **kw)

    def log_message(self, *a):
        pass


def sert_docs():
    """Sert docs/ sur un port libre choisi par le système.

    Port 0 et non un port fixe : deux exécutions concurrentes — la suite en
    local pendant que la CI tourne — se marcheraient dessus.
    """
    srv = socketserver.TCPServer(("127.0.0.1", 0), _Silencieux)
    srv.allow_reuse_address = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/index.html" % srv.server_address[1]


def ouvre_parametres(page):
    """Ouvre le panneau Paramètres, ou ne fait rien s'il l'est déjà.

    Idempotente à dessein : un contrôle qui appelle cette fonction ne devrait
    pas avoir à savoir ce que le précédent a laissé ouvert. Sans ça, le clic
    sur l'engrenage est intercepté par le voile du panneau et l'attente
    expire — un échec qui ne dit rien du défaut qu'on cherchait.
    """
    if page.locator("#settingsOverlay.open").count():
        return
    page.click('button[title="Paramètres"]')
    page.wait_for_selector("#settingsOverlay .settings-panel", state="visible")


def ouvre_liste_sources(page):
    """Déplie le <details> des sources, et rend ce qu'il valait avant.

    Depuis le 13/10/2026 la liste est repliée quand le robot alimente le fil
    — les interrupteurs n'y décident de rien. Les contrôles qui visent la
    liste elle-même (cibles tactiles, repli des familles, filtre) doivent
    donc l'ouvrir d'abord. Rendre l'état d'origine n'est pas cosmétique :
    sans lui, un contrôle qui mesure « c'est bien replié » passerait parce
    que le <details> est fermé, pas parce que les familles le sont.
    """
    etait = page.evaluate("document.getElementById('sourcesRepli').open")
    if not etait:
        page.click("#sourcesResume")
        page.wait_for_selector("#sourceList .src-famille", state="visible")
    return etait


# --------------------------------------------------------------------------
# Cibles tactiles
# --------------------------------------------------------------------------
def test_cibles_tactiles(page, largeur):
    print("\n[%d px] tout ce qui se touche est assez grand" % largeur)
    ouvre_parametres(page)

    # Les boutons dérogeant au minimum apparent étendent leur zone par un
    # pseudo-élément. On mesure donc la ZONE, pas la boîte visible.
    trop_petits = page.evaluate(
        """(mini) => {
        const mauvais = [];
        for (const b of document.querySelectorAll('#settingsOverlay button:not(#apercuCouleurs *)')) {
          if (b.offsetParent === null) continue;
          const r = b.getBoundingClientRect();
          const a = getComputedStyle(b, '::after');
          const deborde = a.content && a.content !== 'none'
            ? Math.abs(parseFloat(a.top) || 0) : 0;
          const hauteur = r.height + 2 * deborde;
          if (hauteur < mini) mauvais.push(b.textContent.trim().slice(0, 18)
                                           + '=' + hauteur.toFixed(0) + 'px');
        }
        return mauvais;
      }""",
        CIBLE_MINIMALE,
    )
    check(not trop_petits,
          "aucun bouton du panneau sous %d px de zone cliquable%s"
          % (CIBLE_MINIMALE, "" if not trop_petits else " — " + ", ".join(trop_petits)))

    # La ligne de source est le contrôle le plus nombreux du panneau. Elle a
    # longtemps mesuré 32x19 px, et seul l'interrupteur y répondait.
    page.click('.panneau-tabs button[data-cible="src"]')
    ouvre_liste_sources(page)
    page.click(".src-famille-titre")
    boite = page.locator("#sourceList .source-row").first.bounding_box()
    check(boite and boite["height"] >= CIBLE_MINIMALE,
          "la ligne de source fait au moins %d px (%s)"
          % (CIBLE_MINIMALE, "%.0f" % boite["height"] if boite else "absente"))


# --------------------------------------------------------------------------
# Débordements
# --------------------------------------------------------------------------
def test_rien_ne_deborde(page, largeur):
    print("\n[%d px] rien ne sort de l'écran" % largeur)

    deborde = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check(deborde <= 1, "la page ne défile pas horizontalement (%d px)" % deborde)

    # La ligne du haut de la console a une forme fixe, dimensionnée pour tenir
    # sur une seule ligne au plus étroit. Un morceau de plus et la garantie
    # tombe — d'où cette mesure plutôt qu'une relecture du gabarit.
    page.evaluate("""() => {
        const l = document.getElementById('runLine');
        if (l) l.style.display = '';
      }""")
    d = page.evaluate("""() => {
        const l = document.getElementById('runLine');
        return l ? l.scrollWidth - l.clientWidth : 0;
      }""")
    check(d <= 1, "la ligne d'état du robot ne déborde pas (%d px)" % d)

    ouvre_parametres(page)
    hors = page.evaluate(
        """() => {
        const mauvais = [];
        for (const b of document.querySelectorAll('#settingsOverlay button:not(#apercuCouleurs *)')) {
          if (b.offsetParent === null) continue;
          // Le « ? » est un rond de 24 px contenant un caractère : la notion
          // de « libellé qui sort de sa boîte » ne s'y applique pas.
          if (b.classList.contains('aide')) continue;
          if (b.scrollWidth > b.clientWidth + 1)
            mauvais.push(b.textContent.trim().slice(0, 18));
        }
        return mauvais;
      }"""
    )
    check(not hors,
          "aucun libellé ne sort de son bouton%s"
          % ("" if not hors else " — " + ", ".join(hors)))


# --------------------------------------------------------------------------
# Replié veut dire replié
# --------------------------------------------------------------------------
def test_replie_est_replie(page, largeur):
    print("\n[%d px] ce qui est annoncé replié l'est vraiment" % largeur)
    ouvre_parametres(page)
    page.click('.panneau-tabs button[data-cible="src"]')

    # La liste entière est repliée tant que le robot alimente le fil : les
    # interrupteurs n'y décident de rien, et c'est le titre qui le dit.
    check(page.evaluate("document.getElementById('sourcesRepli').open") is False,
          "la liste des sources est repliée tant que le robot répond")

    # ON L'OUVRE AVANT DE MESURER. Le contrôle ci-dessous porte sur les
    # FAMILLES, pas sur le <details> : les mesurer à travers un repli fermé
    # donnerait une hauteur de 0 et un vert qui ne prouve rien.
    ouvre_liste_sources(page)

    # LE défaut que ce contrôle existe pour attraper : l'attribut `hidden` ne
    # vaut qu'un display:none de la feuille du NAVIGATEUR, que toute règle
    # d'auteur bat. Les familles se rendaient dépliées malgré leur hidden.
    hauteur = page.locator("#sourceList").bounding_box()["height"]
    check(hauteur < 400,
          "les familles de sources sont bien repliées au départ (%d px)" % hauteur)

    ouvertes = page.locator('.src-famille-titre[aria-expanded="true"]').count()
    check(ouvertes == 0, "aucune famille n'est ouverte sans qu'on l'ait demandé")

    # Les explications derrière « ? » partent masquées. Sinon le panneau
    # redevient le mur de texte qu'on a passé une journée à replier.
    visibles = page.evaluate(
        """() => [...document.querySelectorAll('#settingsOverlay .setting-desc')]
             .filter(d => d.textContent.trim().length > 150 && d.offsetParent !== null)
             .length"""
    )
    check(visibles == 0, "aucune explication longue n'est dépliée au départ")


# --------------------------------------------------------------------------
# Centrage et air
# --------------------------------------------------------------------------
def test_libelles_centres(page, largeur):
    print("\n[%d px] les libellés sont centrés et respirent" % largeur)
    ouvre_parametres(page)

    mauvais = page.evaluate(
        """([tol, air]) => {
        const out = [];
        for (const b of document.querySelectorAll('#settingsOverlay button:not(#apercuCouleurs *)')) {
          if (b.offsetParent === null) continue;
          if (b.classList.contains('aide')) continue;   // rond calibré, pas un libellé
          // Le seuil d'air ne vaut que pour les boutons dimensionnés par leur
          // CONTENU. Un onglet est un tiers de rangée : son air dépend de la
          // largeur de l'écran, pas d'un choix de rembourrage — à 320 px les
          // trois onglets du panneau n'ont que 6 px de marge, sans que rien
          // ne déborde ni ne se décentre. Le contrôle de débordement, lui,
          // continue de les surveiller.
          const dimensionneParSonTexte = !b.classList.contains('tab');
          const r = b.getBoundingClientRect();
          const rg = document.createRange();
          rg.selectNodeContents(b);
          const t = rg.getBoundingClientRect();
          if (!t.width) continue;
          const dx = Math.abs((t.left + t.right) / 2 - (r.left + r.right) / 2);
          const dy = Math.abs((t.top + t.bottom) / 2 - (r.top + r.bottom) / 2);
          const lat = (r.width - t.width) / 2;
          if (dx > tol || dy > tol || (dimensionneParSonTexte && lat < air))
            out.push(`${b.textContent.trim().slice(0,14)} dx=${dx.toFixed(1)} dy=${dy.toFixed(1)} air=${lat.toFixed(0)}`);
        }
        return out;
      }""",
        [DECENTRAGE_TOLERE, AIR_MINIMAL],
    )
    check(not mauvais,
          "libellés centrés à %.0f px près, %d px d'air minimum%s"
          % (DECENTRAGE_TOLERE, AIR_MINIMAL,
             "" if not mauvais else " — " + " | ".join(mauvais)))

    # Un groupe d'actions tient sur UNE rangée, à largeurs égales. Avec des
    # boutons dimensionnés par leur texte, le dernier partait à la ligne — et
    # pas le même selon le groupe.
    if largeur >= 380:
        casses = page.evaluate(
            """() => {
            const out = [];
            for (const g of document.querySelectorAll('#settingsOverlay .setting-buttons')) {
              const b = [...g.querySelectorAll('button')].filter(x => x.offsetParent);
              if (b.length < 2) continue;
              const y = new Set(b.map(x => Math.round(x.getBoundingClientRect().y)));
              const l = new Set(b.map(x => Math.round(x.getBoundingClientRect().width)));
              if (y.size > 1) out.push(b.map(x => x.textContent.trim()).join('+') + ' sur ' + y.size + ' rangées');
              if (l.size > 1) out.push('largeurs inégales : ' + [...l].join('/'));
            }
            return out;
          }"""
        )
        check(not casses,
              "chaque groupe d'actions tient sur une rangée, à largeurs égales%s"
              % ("" if not casses else " — " + " | ".join(casses)))


# --------------------------------------------------------------------------
# États annoncés autrement que par la couleur
# --------------------------------------------------------------------------
def test_etats_annonces(page, largeur):
    print("\n[%d px] les bascules disent leur état, pas seulement leur couleur" % largeur)

    for nom, selecteur in (("onglets d'articles", "#tabAll, #tabNonRockstar, #tabRockstar, #tabRockstarmag"),
                           ("onglets du panneau", ".panneau-tabs .tab"),
                           ("boutons de thème", ".theme-toggle button")):
        if "panneau" in nom or "thème" in nom:
            ouvre_parametres(page)
        enfonces = page.evaluate(
            """(sel) => [...document.querySelectorAll(sel)]
                 .filter(b => b.getAttribute('aria-pressed') === 'true').length""",
            selecteur,
        )
        total = page.evaluate(
            """(sel) => [...document.querySelectorAll(sel)]
                 .filter(b => b.hasAttribute('aria-pressed')).length""",
            selecteur,
        )
        check(enfonces == 1 and total >= 2,
              "%s : exactement un enfoncé sur %d annoncés" % (nom, total))

    # Un champ sans nom est annoncé « champ de saisie » et rien d'autre. Le
    # placeholder n'en est pas un : il disparaît à la première frappe.
    ouvre_parametres(page)
    sans_nom = page.evaluate(
        """() => {
        const out = [];
        for (const c of document.querySelectorAll('#settingsOverlay input:not(#apercuCouleurs *), #settingsOverlay textarea:not(#apercuCouleurs *)')) {
          if (c.type === 'checkbox' || c.offsetParent === null) continue;
          const parLabel = c.id && document.querySelector(`label[for="${c.id}"]`);
          if (!c.getAttribute('aria-label') && !c.getAttribute('aria-labelledby') && !parLabel)
            out.push(c.id || c.tagName);
        }
        return out;
      }"""
    )
    check(not sans_nom,
          "chaque champ visible porte un nom lisible%s"
          % ("" if not sans_nom else " — " + ", ".join(sans_nom)))


# --------------------------------------------------------------------------
# Trois comportements, choisis parce qu'ils ont déjà cassé
# --------------------------------------------------------------------------
def test_comportements(page, largeur):
    print("\n[%d px] trois gestes qui ont déjà cassé" % largeur)
    ouvre_parametres(page)
    page.click('.panneau-tabs button[data-cible="src"]')
    ouvre_liste_sources(page)
    page.click(".src-famille-titre")

    # Le <label> n'enveloppait que la case : cliquer le NOM ne faisait rien.
    ligne = page.locator("#sourceList .source-row").first
    avant = ligne.locator("input").is_checked()
    ligne.locator("span").first.click()
    check(ligne.locator("input").is_checked() != avant,
          "cliquer le nom d'une source la bascule")
    ligne.locator("span").first.click()   # on repose l'état

    # Chercher dans des familles repliées sans les ouvrir ne montrerait rien :
    # le filtre aurait l'air cassé alors qu'il aurait bien travaillé.
    page.fill("#sourceFilter", "kotaku")
    page.wait_for_timeout(200)
    check(page.locator("#sourceList .source-row:visible").count() == 1,
          "le filtre ouvre la famille où il trouve et ne laisse qu'elle")
    page.fill("#sourceFilter", "")
    page.wait_for_timeout(200)
    check(page.locator('.src-famille-titre[aria-expanded="true"]').count() == 0,
          "vider le filtre referme tout")

    # Le bilan se posait au ras des boutons, et comme il suit souvent un
    # bouton rouge, il paraissait lui appartenir.
    page.click('.panneau-tabs button[data-cible="adv"]')
    ecart = page.evaluate(
        """() => {
        const el = document.getElementById('tokenStatusLine');
        if (!el || el.offsetParent === null) return 99;
        return el.getBoundingClientRect().top
             - el.previousElementSibling.getBoundingClientRect().bottom;
      }"""
    )
    check(ecart >= 8, "le bilan garde de l'air sous les boutons (%d px)" % ecart)


def test_repli_backend(nav, url):
    """Une coupure réseau ne doit pas faire réclamer le fichier complet.

    Le défaut, constaté sur le téléphone d'Antoni le 17/09/2026 : les quatre
    lignes du journal portaient la MÊME seconde, et celle du milieu disait
    « Lecture directe du backend : …/feed.json » — le fichier de 2,8 Mo, alors
    que l'app aurait dû lire celui de 332 Ko.

    La cause tenait dans un commentaire trop confiant. La tentative sur le
    fichier allégé était enveloppée dans un try/catch annoté « pas de fichier
    allégé » : il ne prévoyait qu'une cause d'échec, le fichier absent. Une
    coupure réseau tombait dans le même catch, et l'app allait demander huit
    fois plus de données sur la connexion qui venait de flancher.

    Les deux échecs se ressemblent dans le code et n'ont rien à voir :
      - le serveur répond 404  -> le fichier complet est le BON repli ;
      - la requête n'arrive pas -> le fichier complet est le PIRE repli.
    """
    base = url.rsplit("/", 1)[0]

    def joue(nom, brancher):
        ctx = nav.new_context(viewport={"width": 390, "height": 850})
        page = ctx.new_page()
        demandes = []
        # La MÉTHODE compte, depuis le 14/10/2026. Le bouton « Tout
        # charger » annonce son poids, et il l'obtient par une requête HEAD
        # sur le fichier complet : des en-têtes, aucun corps. Ce contrôle
        # porte sur ce qui se TÉLÉCHARGE, donc il ne retient que les GET —
        # compter le HEAD lui ferait interdire une requête qui ne coûte
        # rien, et c'est son intention qu'on garde, pas sa lettre.
        page.on("request", lambda r: demandes.append(r.url.split("/")[-1].split("?")[0])
                if (("feed.json" in r.url or "feed-recent.json" in r.url)
                    and r.method == "GET") else None)
        page.goto(url, wait_until="load")
        page.wait_for_selector("#feed", state="attached")
        brancher(page)
        # Le rattrapage automatique du démarrage télécharge le fichier
        # complet exprès, à la demande d'Antoni (22/09/2026). Ce contrôle-ci
        # porte sur une question différente : une lecture normale ne doit
        # pas ESCALADER vers le gros fichier faute de savoir lire un échec.
        # Laisser le rattrapage tourner mélangerait les deux et rendrait le
        # verdict illisible.
        page.evaluate("rattrapageLance = true;")
        page.evaluate("settings.backendUrl = '%s/feed.json';" % base)
        res = page.evaluate("""async () => {
            try { await checkFromBackend(false); return {ok: true}; }
            catch(e){ return {ok: false}; }
        }""")
        articles = page.evaluate("lastItems.length")
        ctx.close()
        return demandes, res, articles

    # --- Nominal : le petit fichier suffit, le gros n'est jamais demandé.
    d, res, n = joue("nominal", lambda pg: None)
    check(res["ok"] and n > 0,
          "[repli] lecture normale : %d article(s) chargé(s)" % n)
    check(d.count("feed.json") == 0,
          "[repli] lecture normale : le fichier complet n'est pas demandé (%s)" % d)

    # --- LE défaut : coupure réseau sur le fichier allégé.
    d, res, n = joue("coupure",
                     lambda pg: pg.route("**/feed-recent.json*",
                                         lambda route: route.abort("failed")))
    check(d.count("feed.json") == 0,
          "[repli] coupure réseau : le fichier complet n'est JAMAIS réclamé (%s)" % d)
    check(d.count("feed-recent.json") == 2,
          "[repli] coupure réseau : le fichier allégé est retenté une fois (%s)" % d)
    check(not res["ok"],
          "[repli] coupure réseau : l'échec remonte, pour laisser la main au mode direct")

    # --- Repli légitime : le fichier allégé n'existe pas.
    d, res, n = joue("404",
                     lambda pg: pg.route("**/feed-recent.json*",
                                         lambda route: route.fulfill(status=404, body="")))
    check(d.count("feed.json") == 1,
          "[repli] fichier allégé absent (404) : on passe bien au complet (%s)" % d)
    check(res["ok"] and n > 0,
          "[repli] fichier allégé absent (404) : %d article(s) chargé(s)" % n)

    # --- Ce que le journal DIT quand tout le backend tombe.
    #
    # Le 18/09/2026, « Backend inaccessible (Failed to fetch) » a envoyé la
    # recherche pendant deux jours du mauvais côté : trois hypothèses côté
    # serveur, alors que la cause était un DNS privé sur le téléphone. Ce
    # contrôle exige que l'app fasse elle-même la distinction qu'il a fallu
    # tout ce temps pour déduire.
    def journal(brancher):
        ctx = nav.new_context(viewport={"width": 390, "height": 850})
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        page.wait_for_selector("#feed", state="attached")
        brancher(page)
        page.evaluate("rattrapageLance = true;")
        page.evaluate("settings.backendUrl = '%s/feed.json';" % base)
        page.evaluate("""async () => {
            try { await checkFromBackend(false); }
            catch(e){
                addLog("fail", "Backend inaccessible (" + (e.message || "") + ")");
                if(!/^HTTP \\d/.test(String(e && e.message || ""))){
                    addLog("info", "Cette erreur n'a pas de code HTTP : la requête n'a pas atteint "
                      + "le serveur. Regarde du côté DNS privé, VPN ou bloqueur de pub — "
                      + "github.io figure sur certaines listes de blocage.");
                }
            }
        }""")
        # Le journal se lit dans l'état `logs`, pas dans le DOM : ses entrées
        # ne sont rendues que lorsque l'onglet Journal est ouvert.
        txt = page.evaluate("logs.map(l => l.message).join('\\n')")
        ctx.close()
        return txt

    # Panne réseau : la piste doit être donnée.
    txt = journal(lambda pg: pg.route("**/feed*.json*",
                                      lambda route: route.abort("failed")))
    check("DNS privé" in txt and "code HTTP" in txt,
          "[repli] panne réseau : le journal oriente vers DNS, VPN ou bloqueur")

    # Refus du serveur : surtout PAS cette piste — le problème est en ligne,
    # et envoyer l'utilisateur fouiller ses réglages réseau serait pire que
    # de se taire.
    txt = journal(lambda pg: pg.route("**/feed*.json*",
                                      lambda route: route.fulfill(status=503, body="")))
    check("HTTP 503" in txt and "DNS privé" not in txt,
          "[repli] refus du serveur (503) : le code est affiché, sans fausse piste")

    # Le message du contrôle doit être CELUI de l'app, pas une copie qui
    # dériverait en silence : on le relit dans la page livrée.
    page_src = open("docs/index.html", encoding="utf-8").read()
    check("DNS privé, VPN ou bloqueur de pub" in page_src,
          "[repli] cette phrase vient bien de docs/index.html")
    check('if(!/^HTTP \\d/.test(' in page_src,
          "[repli] l'app conditionne la piste à l'absence de code HTTP")





def test_recherche_sans_accents(nav, url):
    """Taper sans accent doit trouver ce qui en porte.

    Mesuré sur les 1 846 articles du fil le 22/09/2026 : 523 titres portent
    un accent, soit 28 %, et ce sont exactement les mots qu'on tape dans une
    barre de recherche — « dévoilé » 29 fois, « précommande » 32, « aperçu »
    19. La normalisation NFD existait pourtant déjà dans le fichier, pour la
    similarité des titres ; la recherche ne s'en servait pas.

    Le surlignage est vérifié en même temps, et séparément : une fois les
    accents retirés, la position d'une correspondance ne désigne plus le
    texte d'origine — « é » compte pour un caractère avant décomposition et
    deux après. Un <mark> posé sur la position aplatie tomberait à côté,
    d'un caractère par accent qui précède.
    """
    base = url.rsplit("/", 1)[0]
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    # Un fil fabriqué : on veut des accents précis, pas ceux du jour.
    page.evaluate("""() => {
      lastItems = [
        {title: "Rockstar dévoile un aperçu de GTA 6", link: "https://ex.test/1",
         description: "La présentation était très attendue", date: "2026-09-20T10:00:00Z", source: "S"},
        {title: "Précommandes ouvertes pour l'édition collector", link: "https://ex.test/2",
         description: "", date: "2026-09-20T09:00:00Z", source: "S"},
        {title: "A plain English headline", link: "https://ex.test/3",
         description: "nothing accented here", date: "2026-09-20T08:00:00Z", source: "S"},
      ];
      lastNewLinks = new Set();
      historyPartial = false;
      rattrapageLance = true;
    }""")

    def cherche(q):
        page.evaluate("""(q) => {
          document.getElementById("searchInput").value = q;
          visibleCount = 30;
          applyFilters();
        }""", q)
        return page.evaluate("articlesAffiches().map(i => i.link)")

    # Sans accent : c'est tout l'objet du changement.
    for requete, attendu, pourquoi in [
        ("devoile", "https://ex.test/1", "« devoile » trouve « dévoile »"),
        ("apercu", "https://ex.test/1", "« apercu » trouve « aperçu »"),
        ("precommande", "https://ex.test/2", "« precommande » trouve « Précommandes »"),
        ("edition", "https://ex.test/2", "« edition » trouve « édition »"),
        ("presentation", "https://ex.test/1", "et le résumé compte aussi : « presentation »"),
    ]:
        liens = cherche(requete)
        check(attendu in liens, "%s (%d résultat(s))" % (pourquoi, len(liens)))

    # Avec accent : ne doit rien casser. C'est le sens qu'on n'avait pas
    # avant — aplatir un seul des deux côtés rendrait muet ce qui marchait.
    for requete, attendu in [("dévoile", "https://ex.test/1"),
                             ("précommande", "https://ex.test/2"),
                             ("APERÇU", "https://ex.test/1")]:
        liens = cherche(requete)
        check(attendu in liens, "« %s » avec accent trouve toujours" % requete)

    # Et une recherche qui ne doit rien ramener en ramène toujours zéro.
    check(cherche("zzzintrouvable") == [], "une requête absente ne ramène rien")
    check(len(cherche("english")) == 1, "un titre sans accent se cherche comme avant")

    # ---- Le surlignage, aux bonnes bornes ----
    marque = page.evaluate(
        """() => highlightMatch("Rockstar dévoile un aperçu", "apercu")""")
    check("<mark>aperçu</mark>" in marque,
          "le surlignage entoure le mot ACCENTUÉ du texte d'origine : %s" % marque)
    check(marque.startswith("Rockstar dévoile un "),
          "et ce qui précède est intact, sans décalage : %s" % marque)

    # Deux accents avant la correspondance : le décalage aurait été de deux.
    deux = page.evaluate(
        """() => highlightMatch("L'été à Vice City : précommande", "precommande")""")
    check("<mark>précommande</mark>" in deux,
          "deux accents avant la correspondance ne la décalent pas : %s" % deux)

    # Plusieurs occurrences, et l'échappement HTML qui tient.
    plusieurs = page.evaluate(
        """() => highlightMatch("Édition, édition et <b>édition</b>", "edition")""")
    check(plusieurs.count("<mark>") == 3,
          "les trois occurrences sont surlignées (%d)" % plusieurs.count("<mark>"))
    check("&lt;b&gt;" in plusieurs and "<b>" not in plusieurs,
          "le balisage du titre reste échappé : <mark> est le seul produit ici")

    # L'ancienne version passait la requête à une RegExp. Un titre contenant
    # un caractère spécial de regex ne doit plus poser la question.
    special = page.evaluate("""() => highlightMatch("GTA 6 (édition) [2026]", "(edition)")""")
    check("<mark>(édition)</mark>" in special,
          "une requête pleine de caractères de regex ne casse rien : %s" % special)
    check(page.evaluate("""() => highlightMatch("Rien", "")""") == "Rien",
          "une requête vide rend le titre tel quel")

    ctx.close()


def test_barre_detat_suit_le_theme(nav, url):
    """La bande en haut du téléphone, en PWA installée.

    <meta name="theme-color"> valait le bleu de l'accent depuis le premier
    jour et aucune ligne ne la touchait : en thème sombre, une bande bleue
    coiffait donc en permanence une application noire. Le bleu n'était le
    fond d'aucun des deux thèmes.

    Ce contrôle lit la balise APRÈS un vrai changement de thème dans un vrai
    navigateur : vérifier que la constante existe ne dirait rien de ce que
    le téléphone affiche.
    """
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    def barre():
        return page.evaluate(
            """() => document.querySelector('meta[name="theme-color"]').content.toLowerCase()""")

    def fond():
        return page.evaluate(
            """() => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim().toLowerCase()""")

    for theme in ("dark", "light", "dark"):
        page.evaluate("(t) => setTheme(t)", theme)
        check(barre() == fond(),
              "thème %s : la barre (%s) est exactement le fond de la page (%s)"
              % (theme, barre(), fond()))

    check(barre() != "#5493ff",
          "et ce n'est plus le bleu d'accent, qui n'était le fond d'aucun thème")

    # Le mode « système » doit lui aussi peindre la barre, sinon elle garde
    # la couleur du thème précédemment choisi à la main.
    page.emulate_media(color_scheme="light")
    page.evaluate("() => setTheme('system')")
    check(barre() == fond(),
          "en mode système clair, la barre suit (%s)" % barre())
    page.emulate_media(color_scheme="dark")
    page.evaluate("() => applyTheme()")
    check(barre() == fond(),
          "et quand le téléphone bascule en sombre le soir, elle suit aussi (%s)" % barre())

    ctx.close()


def test_ecran_de_chargement(nav, url):
    """Un écran blanc en thème clair, noir en thème sombre, avec le décompte.

    Demandé par Antoni le 07/11/2026, puis allongé à 1,5 s (« il disparaît trop
    vite »). Ce contrôle regarde le VRAI écran dans un vrai navigateur, dans les
    quatre cas de thème, vérifie qu'il bloque les touches, qu'un appui ou une touche
    le ferme, qu'il s'efface seul, et que le réglage « Écran de chargement » le coupe.

    Piloté par un test, le navigateur annonce `navigator.webdriver` : l'écran est
    alors coupé tant que le réglage n'est pas explicitement allumé — sans quoi chaque
    clic de la suite attendrait qu'il s'efface. Ce contrôle l'allume donc, ou retire
    `webdriver` pour jouer le téléphone réel.
    """
    print("\n[chargement] l'écran suit le thème, dit le décompte, bloque, se ferme et se coupe")

    def lum(css):
        import re as _re
        r, g, b = [int(x) for x in _re.findall(r"\d+", css)[:3]]
        def f(v):
            v /= 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    def ouvre(theme="system", schema="light", reglages=None, quand="2026-11-07T12:00:00+01:00",
              largeur=320, webdriver=True):
        ctx = nav.new_context(viewport={"width": largeur, "height": 780},
                              color_scheme=schema, timezone_id="Europe/Paris")
        corps = dict(theme=theme)
        corps.update(reglages if reglages is not None else {"splashActif": True})
        ctx.add_init_script(
            "try{localStorage.setItem('gta6watch:settings-v1', JSON.stringify(%s));}catch(e){}"
            % json.dumps(corps))
        if not webdriver:
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>false});")
        page = ctx.new_page()
        page.clock.install(time=quand)
        erreurs = []
        page.on("pageerror", lambda e: erreurs.append(str(e)))
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(100)
        return ctx, page, erreurs

    cas = [("light", "light", "clair"), ("dark", "dark", "sombre"),
           ("system", "light", "clair"), ("system", "dark", "sombre")]
    for reglage, schema, attendu in cas:
        ctx, page, erreurs = ouvre(reglage, schema)
        etat = page.evaluate("""() => {
            const e = document.getElementById('splash');
            if(!e) return null;
            const cs = getComputedStyle(e);
            const r = e.getBoundingClientRect();
            const dessus = document.elementFromPoint(r.width / 2, r.height / 2);
            return {fond: cs.backgroundColor, couleur: cs.color,
                    pointeur: cs.pointerEvents, cache: e.getAttribute('aria-hidden'),
                    theme: document.documentElement.getAttribute('data-theme'),
                    j: document.getElementById('splashJ').textContent,
                    l: document.getElementById('splashL').textContent,
                    logo: (document.querySelector('#splash img') || {}).naturalWidth,
                    bloque: !!(dessus && dessus.closest('#splash'))}
        }""")
        nom = "%s, téléphone %s" % (reglage, schema)
        check(etat is not None, "[%s] l'écran est là juste après le chargement" % nom)
        if etat:
            clair = lum(etat["fond"]) > 0.5
            check(clair == (attendu == "clair") and etat["theme"] == ("light" if attendu == "clair" else "dark"),
                  "[%s] fond %s, thème %s : l'écran est %s" % (nom, etat["fond"], etat["theme"], attendu))
            check(abs(lum(etat["fond"]) - lum(etat["couleur"])) > 0.5,
                  "[%s] le texte (%s) se lit sur le fond (%s)" % (nom, etat["couleur"], etat["fond"]))
            check(etat["j"] == "J-12" and etat["l"] == "AVANT LA SORTIE",
                  "[%s] il dit le décompte en jours civils à Paris (%s, %s)" % (nom, etat["j"], etat["l"]))
            check(etat["pointeur"] == "auto" and etat["bloque"] and etat["cache"] == "true" and etat["logo"] == 192,
                  "[%s] il BLOQUE les touches, se cache des lecteurs d'écran et affiche le logo (%s)" % (nom, etat))
        # Encore là à une seconde : c'est tout l'objet de la demande d'Antoni.
        page.wait_for_timeout(900)
        encore = page.evaluate("() => !!document.getElementById('splash') && !document.getElementById('splash').classList.contains('fini')")
        check(encore, "[%s] il est encore affiché une seconde après le chargement" % nom)
        page.wait_for_timeout(1700)
        gone = page.evaluate("() => !document.getElementById('splash')")
        check(gone, "[%s] puis il s'est effacé seul, l'app est à découvert" % nom)
        check(page.evaluate("() => document.documentElement.getAttribute('data-theme')") == ("light" if attendu == "clair" else "dark"),
              "[%s] et applyTheme() retombe sur le même thème que le petit script d'avant l'affichage" % nom)
        check(not erreurs, "[%s] aucune erreur JavaScript (%s)" % (nom, erreurs[:1]))
        ctx.close()

    # Un appui le ferme tout de suite ; une touche aussi.
    ctx, page, _ = ouvre()
    page.mouse.click(160, 300)
    page.wait_for_timeout(500)
    check(page.evaluate("() => !document.getElementById('splash')"),
          "un appui sur l'écran le ferme tout de suite (moins de 0,5 s)")
    ctx.close()
    ctx, page, _ = ouvre()
    page.keyboard.press("Space")
    page.wait_for_timeout(500)
    check(page.evaluate("() => !document.getElementById('splash')"),
          "une touche du clavier le ferme aussi")
    ctx.close()

    # Le réglage : coupé, pas même peint ; piloté sans réglage, coupé ; téléphone réel, allumé.
    ctx, page, _ = ouvre(reglages={"splashActif": False})
    etat = page.evaluate("""() => { const e = document.getElementById('splash');
        return {coupe: document.documentElement.getAttribute('data-splash'),
                affiche: !!e && getComputedStyle(e).display !== 'none'} }""")
    check(etat["coupe"] == "off" and not etat["affiche"],
          "le réglage coupé : l'écran n'est pas affiché, dès le premier affichage (%s)" % etat)
    ctx.close()
    ctx, page, _ = ouvre(reglages={})
    check(page.evaluate("() => document.documentElement.getAttribute('data-splash')") == "off",
          "piloté par un test et sans réglage enregistré : coupé, pour ne pas allonger la suite")
    ctx.close()
    ctx, page, _ = ouvre(reglages={}, webdriver=False)
    check(page.evaluate("() => !!document.getElementById('splash') && document.documentElement.getAttribute('data-splash') === null"),
          "sur un vrai téléphone sans réglage enregistré : allumé par défaut")
    ctx.close()

    # L'interrupteur des Réglages écrit dans le réglage enregistré, et la page le relit.
    ctx, page, _ = ouvre(webdriver=False)
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("() => openSettings()")
    page.wait_for_timeout(300)
    avant = page.evaluate("() => document.getElementById('splashActif').checked")
    page.evaluate("() => { const c = document.getElementById('splashActif'); c.checked = false; c.dispatchEvent(new Event('change')); }")
    page.wait_for_timeout(300)
    enregistre = page.evaluate("() => JSON.parse(localStorage.getItem('gta6watch:settings-v1')).splashActif")
    check(avant is True and enregistre is False,
          "l'interrupteur « Écran de chargement » est allumé par défaut et écrit le réglage (%s, %s)" % (avant, enregistre))
    ctx.close()

    # Le texte du décompte aux frontières : la veille, le jour J, après.
    for quand, grand, petit in (("2026-11-18T23:30:00+01:00", "J-1", "AVANT LA SORTIE"),
                                ("2026-11-19T09:00:00+01:00", "SORTI !", "GTA VI"),
                                ("2026-11-20T09:00:00+01:00", "SORTI", "DEPUIS 1 JOUR"),
                                ("2026-11-22T09:00:00+01:00", "SORTI", "DEPUIS 3 JOURS")):
        ctx, page, _ = ouvre(quand=quand, largeur=390)
        textes = page.evaluate("""() => { const j = document.getElementById('splashJ'),
            l = document.getElementById('splashL'); return j ? [j.textContent, l.textContent] : null; }""")
        check(textes == [grand, petit], "[%s] l'écran dit %s / %s (%s)" % (quand[:16], grand, petit, textes))
        ctx.close()


def test_toast_annuler(nav, url):
    """Ce qui se défait tout seul part sans question, et se reprend.

    Choix d'Antoni le 22/09/2026 : le garde-fou suit la RÉVERSIBILITÉ. Marquer
    des articles comme lus est un état local qui se rétablit à l'identique —
    la confirmation qui disait « il n'y a pas de retour en arrière » énonçait
    une contrainte qu'on vient de lever. Ce qui ne se retrouve nulle part
    ailleurs garde sa confirmation.

    Le piège que ce contrôle verrouille : annuler doit rendre EXACTEMENT
    l'état d'avant. Rejouer l'inverse sur toute la liste affichée
    remarquerait non lus des articles qui l'étaient déjà — l'annulation
    ferait alors plus que défaire.
    """
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    page.evaluate("""() => {
      lastItems = [
        {title: "Un", link: "https://ex.test/1", date: "2026-09-20T10:00:00Z", source: "S"},
        {title: "Deux", link: "https://ex.test/2", date: "2026-09-20T09:00:00Z", source: "S"},
        {title: "Trois", link: "https://ex.test/3", date: "2026-09-20T08:00:00Z", source: "S"},
      ];
      lastNewLinks = new Set();
      historyPartial = false;
      rattrapageLance = true;
      // « Trois » est DÉJÀ lu avant le geste : c'est lui qui piège une
      // annulation écrite à l'envers de l'affichage.
      readSet = new Set(["https://ex.test/3"]);
      document.getElementById("searchInput").value = "";
      applyFilters();
    }""")

    check(page.evaluate("readSet.size") == 1, "au départ, un seul article est lu")

    # --- Le geste part sans confirmation ---
    page.evaluate("() => markAllRead(true)")
    page.wait_for_function("readSet.size === 3", timeout=3000)
    check(page.evaluate("""() => document.getElementById("confirmOverlay").classList.contains("open")""") is False,
          "aucune confirmation ne s'interpose : l'action est réversible")
    check(page.evaluate("readSet.size") == 3, "les trois sont lus")

    toast = page.locator("#toast")
    check(toast.is_visible(), "le toast s'affiche")
    check("2 articles marqués lus" in page.evaluate(
              """() => document.getElementById("toastTexte").textContent"""),
          "et il annonce les DEUX réellement changés, pas les trois affichés : %s"
          % page.evaluate("""() => document.getElementById("toastTexte").textContent"""))

    # Le focus ne doit pas avoir été volé : on vient de faire un geste
    # volontaire, déplacer le curseur ferait perdre sa place dans la liste.
    check(page.evaluate("""() => document.activeElement.id !== "toastAnnuler" """),
          "le toast ne vole pas le focus")
    check(page.evaluate("""() => document.getElementById("toast").getAttribute("role")""") == "status",
          "role=status et non alert : le message accompagne, il n'interrompt pas")

    # --- Annuler rend l'état EXACT d'avant ---
    page.click("#toastAnnuler")
    page.wait_for_function("readSet.size === 1", timeout=3000)
    restant = page.evaluate("[...readSet]")
    check(restant == ["https://ex.test/3"],
          "après annulation, « Trois » est TOUJOURS lu — il l'était avant le "
          "geste et l'annulation ne défait que ce que le geste a fait : %s" % restant)
    check(toast.is_visible() is False, "et le toast se referme")

    # --- Il disparaît tout seul, et l'action reste faite ---
    page.evaluate("() => markAllRead(true)")
    page.wait_for_function("readSet.size === 3", timeout=3000)
    page.evaluate("() => { clearTimeout(_toastId); fermeToast(); }")
    check(toast.is_visible() is False, "le toast expiré disparaît")
    check(page.evaluate("readSet.size") == 3,
          "et l'action reste faite : expirer n'annule pas")

    # --- Ce qui n'est pas réversible garde sa confirmation ---
    page.evaluate("() => { toutesSources(false); }")
    page.wait_for_timeout(200)
    check(page.evaluate("""() => document.getElementById("confirmOverlay").classList.contains("open")"""),
          "« Tout désactiver » demande toujours confirmation : la sélection "
          "perdue ne se rend pas")
    page.evaluate("() => repondConfirmation(false)")

    ctx.close()


def test_poids_de_tout_charger(nav, url):
    """Le bouton « Tout charger » annonce ce qu'il va télécharger.

    Mesuré le 14/10/2026 : 2,8 Mo aujourd'hui, 9,3 Mo si le fil atteint son
    plafond. Appuyer en données mobiles le jour de la sortie coûtait dix
    méga sans que rien ne l'ait dit.
    """
    import os, re
    base = url.rsplit("/", 1)[0]
    ctx = nav.new_context(viewport={"width": 390, "height": 800})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("rattrapageLance = true;")

    reel = os.path.getsize("docs/feed.json")
    page.evaluate("""([u]) => {
        settings.backendUrl = u;
        poidsHistorique = null; poidsHistoriqueDemande = false;
        historyPartial = true;
        updateHistoryLine();
    }""", ["%s/feed.json" % base])
    # Quinze secondes et non cinq : le sondage de poids part en HEAD vers
    # le serveur de test, et sur un coureur d'intégration chargé la
    # première requête peut traîner. Un contrôle qui dépend du temps doit
    # laisser de la marge, sinon il rougit pour la charge de la machine et
    # non pour un défaut. (Je n'ai PAS la preuve que c'est ce qui a fait
    # rougir `main` le 14/10/2026 à 15h35 — les journaux ne sont pas
    # lisibles d'ici — mais c'était le délai le plus serré que j'avais
    # introduit ce jour-là.)
    page.wait_for_function(
        "() => (document.getElementById('historyLine').textContent || '')"
        ".includes('(')", timeout=15000)
    texte = page.evaluate(
        "document.getElementById('historyLine').textContent")
    m = re.search(r"\(([\d,]+)\s*(Mo|Ko)\)", texte)
    check(bool(m), "[poids] le bouton annonce une taille (%s)" % texte.strip())
    if m:
        annonce = float(m.group(1).replace(",", "."))
        attendu = reel / 1024 / 1024 if m.group(2) == "Mo" else reel / 1024
        # LE CHIFFRE VIENT DU FICHIER, pas d'une constante : il doit suivre
        # le fichier réellement servi, à l'arrondi d'affichage près.
        check(abs(annonce - attendu) <= 0.1,
              "[poids] et c'est la taille du fichier réellement servi "
              "(%s annoncé, %.2f attendu)" % (m.group(0), attendu))

    # Une seule requête : le sondage ne doit pas repartir à chaque rendu.
    requetes = []
    page.on("request", lambda r: requetes.append(r.method))
    page.evaluate("updateHistoryLine(); updateHistoryLine();")
    page.wait_for_timeout(300)
    check(requetes.count("HEAD") == 0,
          "[poids] redessiner la ligne ne relance pas le sondage (%d requête(s))"
          % requetes.count("HEAD"))

    # TROIS ÉTATS, UN SEUL JUGE. Défaut trouvé le 14/10/2026 en
    # chronométrant la visibilité du bouton : trois endroits écrivaient
    # dans cette ligne, et applyFilters() passait toujours en dernier.
    # Mesuré sur réseau bridé, le message de chargement vivait 0,01 s en
    # 4G avant d'être remplacé par un bouton qui ne fait rien pendant les
    # dix secondes du téléchargement ; le message d'échec, lui, était
    # effacé dans la milliseconde — une panne ne se voyait nulle part.
    etats = page.evaluate("""() => {
        const el = document.getElementById('historyLine');
        const lis = () => el.textContent.trim();
        const out = {};
        historyPartial = true; historyLoading = true; historyErreur = false;
        updateHistoryLine(); out.pendant = lis();
        // Le rendu du fil ne doit plus pouvoir écraser l'état.
        applyFilters(); out.pendantApresRendu = lis();
        out.boutonPendant = el.querySelectorAll('button').length;
        historyLoading = false; historyErreur = true;
        updateHistoryLine(); out.echec = lis();
        applyFilters(); out.echecApresRendu = lis();
        historyErreur = false;
        updateHistoryLine(); out.repos = lis();
        return out;
    }""")
    check("Chargement" in etats["pendant"] and etats["boutonPendant"] == 0,
          "[poids] pendant le chargement, la ligne le DIT et n'offre aucun "
          "bouton mort (%s)" % etats["pendant"])
    check(etats["pendantApresRendu"] == etats["pendant"],
          "[poids] et un rendu du fil ne l'efface plus (%s)"
          % etats["pendantApresRendu"])
    check("indisponible" in etats["echec"]
          and etats["echecApresRendu"] == etats["echec"],
          "[poids] un échec reste affiché après le rendu suivant (%s)"
          % etats["echecApresRendu"])
    check("Réessayer" in etats["echec"] and "(" in etats["echec"],
          "[poids] et « Réessayer » annonce le poids lui aussi — c'est là "
          "que la question se pose vraiment (%s)" % etats["echec"])
    check("Tout charger" in etats["repos"],
          "[poids] au repos, le bouton revient (%s)" % etats["repos"])

    # ET SI ON NE SAIT PAS, ON NE DIT RIEN. Un chiffre faux serait pire que
    # pas de chiffre : le bouton redevient exactement ce qu'il était.
    page.evaluate("""() => {
        poidsHistorique = null; poidsHistoriqueDemande = true;
        updateHistoryLine();
    }""")
    nu = page.evaluate("document.getElementById('historyLine').textContent")
    check("(" not in nu,
          "[poids] sans réponse du serveur, le bouton n'invente aucun "
          "chiffre (%s)" % nu.strip())

    # Le sondage échoué ne casse rien et ne crie pas : une taille inconnue
    # est un confort manquant, pas une panne.
    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.evaluate("""() => {
        settings.backendUrl = 'http://127.0.0.1:1/absent.json';
        poidsHistorique = null; poidsHistoriqueDemande = false;
        updateHistoryLine();
    }""")
    page.wait_for_timeout(600)
    check(not erreurs, "[poids] un sondage qui échoue ne jette rien (%s)" % erreurs)
    ctx.close()


def test_plafond_des_lus(nav, url):
    """4.2 de l'audit : la liste des articles lus a enfin un plafond.

    C'était la seule structure qui grandissait sans borne, et la seule dont
    le contenu ne se reconstitue pas : un article lu l'est pour toujours,
    aucun serveur ne le redira. Le plafond ne s'applique qu'à la copie
    écrite sur l'appareil, et il ne jette QUE ce qu'aucun écran ne peut
    plus afficher.
    """
    ctx = nav.new_context(viewport={"width": 390, "height": 800})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    plafond = page.evaluate("MAX_LIENS_LUS_PERSISTES")
    check(isinstance(plafond, int) and plafond > 1000,
          "[lus] un plafond existe (%s)" % plafond)

    # Le décor : un peu plus que le plafond, dont une poignée encore
    # affichables (présents dans le fil ou l'archive).
    montage = """(plafond) => {
        const joignables = 50;
        lastItems = []; readSet = new Set();
        for(let i = 0; i < plafond + 100; i++){
            const l = 'https://t.test/vieux/' + i;
            readSet.add(l);
        }
        for(let i = 0; i < joignables; i++){
            const l = 'https://t.test/encore/' + i;
            readSet.add(l);
            lastItems.push({link: l, title: 't' + i, date: new Date().toISOString()});
        }
        return {total: readSet.size, joignables};
    }"""
    etat = page.evaluate(montage, plafond)

    # 1. Sans l'historique complet ni l'archive, on ne sait pas ce qui est
    #    encore joignable : on ne jette RIEN, quitte à dépasser.
    page.evaluate("() => { historyPartial = true; archiveChargee = false; }")
    check(page.evaluate("lusAPersister().length") == etat["total"],
          "[lus] historique incomplet : rien n'est jeté, même au-dessus du "
          "plafond — on ne choisit pas à l'aveugle")
    page.evaluate("() => { historyPartial = false; archiveChargee = false; }")
    check(page.evaluate("lusAPersister().length") == etat["total"],
          "[lus] archive incomplète non plus")

    # 2. Avec tout en main, le surplus part — et seulement le surplus.
    page.evaluate("() => { historyPartial = false; archiveChargee = true; }")
    garde = page.evaluate("lusAPersister()")
    check(len(garde) == plafond,
          "[lus] avec le fil et l'archive en main, la copie écrite retombe "
          "au plafond (%d pour %d en mémoire)" % (len(garde), etat["total"]))
    restants = set(garde)
    perdus_joignables = [i for i in range(etat["joignables"])
                         if "https://t.test/encore/%d" % i not in restants]
    check(not perdus_joignables,
          "[lus] et AUCUN lien encore affichable n'est jeté (%d perdu(s))"
          % len(perdus_joignables))
    check("https://t.test/vieux/0" not in restants
          and "https://t.test/vieux/%d" % (plafond + 99) in restants,
          "[lus] ce sont les plus anciennement enregistrés qui partent, pas "
          "les derniers lus")

    # 3. Si TOUT est encore affichable, le plafond est dépassé sciemment :
    #    faire revenir un article en « non lu » sous les yeux du lecteur
    #    serait pire que d'écrire un peu plus.
    page.evaluate("""(plafond) => {
        lastItems = []; readSet = new Set();
        for(let i = 0; i < plafond + 100; i++){
            const l = 'https://t.test/vivant/' + i;
            readSet.add(l);
            lastItems.push({link: l, title: 't', date: new Date().toISOString()});
        }
    }""", plafond)
    check(page.evaluate("lusAPersister().length") == plafond + 100,
          "[lus] tout afficher l'emporte sur le plafond quand les deux "
          "s'opposent")

    # 4. La SAUVEGARDE garde tout : un fichier n'a pas la limite de 5 Mo,
    #    et une sauvegarde amputée ne serait plus un filet.
    page.evaluate("""(plafond) => {
        lastItems = []; readSet = new Set();
        for(let i = 0; i < plafond + 100; i++) readSet.add('https://t.test/x/' + i);
        historyPartial = false; archiveChargee = true;
    }""", plafond)
    combien = page.evaluate("contenuSauvegarde().lus.length")
    check(combien == plafond + 100,
          "[lus] la sauvegarde exportée les garde TOUS (%d pour un plafond "
          "de %d) — un fichier n'a pas la limite de 5 Mo" % (combien, plafond))

    # 5. Le message de quota ne doit plus conseiller de détruire
    #    l'irremplaçable : « vide les données du site » effaçait justement
    #    les articles lus, que rien ne reconstitue.
    msg = page.evaluate("String(storageSet)")
    check("Vide les données du site" not in msg,
          "[lus] le message de stockage plein ne conseille plus d'effacer "
          "les données du site")
    check("sauvegarde" in msg.lower(),
          "[lus] il renvoie vers la sauvegarde à la place")
    ctx.close()


def test_sauvegarde_export_import(nav, url):
    """Sortir ses réglages de l'appareil, et les y ramener.

    Tout vit dans le localStorage d'un navigateur : vider les données du site
    ou changer de téléphone efface les réglages, les mots-clés affinés pendant
    des semaines et l'état de lecture, sans retour.

    Deux propriétés sont verrouillées, et la seconde est la plus importante :
    l'aller-retour rend exactement ce qu'on avait, et le JETON GITHUB N'Y EST
    JAMAIS. Une sauvegarde doit pouvoir rester dans un dossier de
    téléchargements sans rien exposer.
    """
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    # Un état reconnaissable, jeton compris.
    page.evaluate("""() => {
      settings.keywords = ["gta 6", "un mot à moi"];
      settings.maxDisplay = 123;
      settings.denseMode = true;
      readSet = new Set(["https://ex.test/a", "https://ex.test/b"]);
      seenMap = {"https://ex.test/a": "2026-09-01T00:00:00Z"};
      localStorage.setItem(STORAGE_PREFIX + TOKEN_STORAGE_KEY, "github_pat_SECRET_A_NE_PAS_EXPORTER");
    }""")

    paquet = page.evaluate("() => JSON.stringify(contenuSauvegarde())")

    # ---- Ce que le fichier NE contient pas ----
    check("SECRET_A_NE_PAS_EXPORTER" not in paquet,
          "le jeton GitHub n'est pas dans la sauvegarde")
    check("github_pat" not in paquet,
          "et aucune trace d'un jeton, sous quelque forme que ce soit")
    check("last-items" not in paquet and '"items"' not in paquet,
          "le cache du fil non plus : il se reconstruit seul à l'ouverture")

    # ---- Ce qu'il contient ----
    import json as _json
    d = _json.loads(paquet)
    check(d["app"] == "GTA6_WATCH" and d["version"] == 1,
          "le fichier s'annonce : application et version")
    check("un mot à moi" in d["settings"]["keywords"], "les mots-clés y sont")
    check(d["settings"]["maxDisplay"] == 123, "les seuils aussi")
    check(sorted(d["lus"]) == ["https://ex.test/a", "https://ex.test/b"],
          "les articles lus aussi")
    check(d["vus"] == {"https://ex.test/a": "2026-09-01T00:00:00Z"},
          "et les articles déjà vus")

    # ---- L'aller-retour rend l'état exact ----
    page.evaluate("""() => {
      settings.keywords = ["autre chose"];
      settings.maxDisplay = 500;
      readSet = new Set();
      seenMap = {};
    }""")
    page.evaluate("""(p) => {
      const lu = valideSauvegarde(p);
      settings = Object.assign({}, DEFAULT_SETTINGS, lu.data.settings);
      readSet = new Set(lu.data.lus);
      seenMap = lu.data.vus;
    }""", paquet)
    check(page.evaluate("settings.maxDisplay") == 123, "après import, le seuil est revenu")
    check(page.evaluate("readSet.size") == 2, "et les deux articles lus aussi")
    check(page.evaluate("""() => settings.keywords.includes("un mot à moi")"""),
          "et le mot-clé personnel")

    # Le jeton de l'appareil n'a pas été touché par l'import.
    check(page.evaluate(
              """() => localStorage.getItem(STORAGE_PREFIX + TOKEN_STORAGE_KEY)""")
          == "github_pat_SECRET_A_NE_PAS_EXPORTER",
          "et le jeton de CET appareil est intact — l'import n'y touche pas")

    # ---- Les refus : en bloc, jamais à moitié ----
    for brut, pourquoi in [
        ("pas du json", "un fichier illisible"),
        ('{"app":"AutreApp","version":1,"settings":{},"lus":[],"vus":{}}', "un fichier d'une autre app"),
        ('{"app":"GTA6_WATCH","version":99,"settings":{},"lus":[],"vus":{}}', "une version plus récente"),
        ('{"app":"GTA6_WATCH","version":1,"lus":[],"vus":{}}', "des réglages absents"),
        ('{"app":"GTA6_WATCH","version":1,"settings":{},"vus":{}}', "une liste de lus absente"),
        ('{"app":"GTA6_WATCH","version":1,"settings":{},"lus":[],"vus":[]}', "des vus du mauvais type"),
        ("[]", "un tableau au lieu d'un objet"),
    ]:
        r = page.evaluate("(b) => valideSauvegarde(b)", brut)
        check(r["ok"] is False and r.get("raison"),
              "refusé avec une raison : %s → %s" % (pourquoi, r.get("raison", "")))

    # Un fichier valide passe, évidemment — sinon les refus ci-dessus ne
    # prouveraient rien.
    check(page.evaluate("(p) => valideSauvegarde(p).ok", paquet) is True,
          "et une vraie sauvegarde est bien acceptée")

    # ---- Le champ de fichier se réarme ----
    # Sans remise à zéro, réimporter DEUX FOIS le même fichier ne déclenche
    # pas de second `change` : le second import semblerait ignoré.
    html = page.content()
    check('onchange="importerSauvegarde(this)"' in html,
          "le champ de fichier est branché")
    corps = page.evaluate("() => importerSauvegarde.toString()")
    check('champ.value = ""' in corps,
          "et il se réarme, sinon le même fichier ne s'importe qu'une fois")

    ctx.close()


def test_vue_statistiques(nav, url):
    """La vue statistiques, et d'abord le piège qu'elle devait éviter.

    Le piège a été mal jugé deux fois. L'archive ne corrige pas les vieux
    mois (créée la veille à partir de feed.json, elle a le même défaut), et
    « le plus ancien article ordinaire » ne marque pas le début d'une
    couverture complète : sur le fil réel, la falaise du 07/09 est la limite
    de l'ancienne fenêtre de 15 jours, pas un creux de la presse. Rien dans
    feed.json ne permet de dater une couverture complète.

    La règle retenue ne devine rien : la rétention n'est jamais descendue
    sous 15 jours, donc un histogramme borné à 14 jours complets ne contient
    aucun jour élagué. Et AUCUNE date de couverture n'est affichée — c'est le
    contrôle qui aurait attrapé la seconde erreur.

    Le reste vérifie la bascule de vue dans setTab(), le point annoncé comme
    risqué avant d'y toucher : ajouter une vue en oubliant un seul des
    affichages laissait le fil ou la recherche sous les statistiques.
    """
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    # Midi, heure de Paris, le 23/09/2026 : l'horloge est injectée.
    MAINTENANT = "2026-09-23T10:00:00Z"

    # ---- 1. LE piège : de vieux officiels, et 20 jours d'ordinaires ----
    s = page.evaluate("""(m) => {
      const items = [
        {title: "Officiel 2023", link: "o1", date: "2023-12-05T12:00:00Z", official: true},
        {title: "Officiel 2025", link: "o2", date: "2025-05-06T12:00:00Z", official: true},
        {title: "Officiel juin", link: "o3", date: "2026-06-10T12:00:00Z", official: true},
      ];
      // Ordinaires du 03/09 au 23/09, trois par jour.
      for(let j = 3; j <= 23; j++){
        for(let k = 0; k < 3; k++){
          const d = String(j).padStart(2, "0");
          // padStart et non "T0" + heure : la première version écrivait
          // « T010 » pour 10 h, une date invalide. Le code l'ignorait à juste
          // titre, et c'est le test qui échouait.
          items.push({title: "a" + j + k, link: "a" + j + "-" + k,
                      date: "2026-09-" + d + "T" + String(k + 8).padStart(2, "0") + ":00:00Z"});
        }
      }
      return calculeStats(items, null, new Date(m));
    }""", MAINTENANT)
    check(s["couverture"] is None and s["mois"] == [],
          "sans date publiée par le robot, AUCUNE couverture ni aucun mois : "
          "rien dans le fil ne permet de les établir sans deviner")
    check(s["officiels"] == 3, "les officiels sont comptés à part (%d)" % s["officiels"])

    jours = [j["jour"] for j in s["jours"]]
    check(len(jours) == 14,
          "l'histogramme couvre 14 jours — sous la plus petite fenêtre de "
          "rétention jamais utilisée, 15 jours (%d)" % len(jours))
    check("2026-09-23" not in jours, "aujourd'hui, pas fini, n'y est pas")
    check(jours[0] == "2026-09-09" and jours[-1] == "2026-09-22",
          "il va du 09 au 22 (%s → %s)" % (jours[0], jours[-1]))
    check(all(j["n"] == 3 for j in s["jours"]), "trois articles chaque jour")
    check(s["mediane"] == 3, "médiane : 3 par jour (%d)" % s["mediane"])

    # Une installation récente : le premier jour du fil, partiel, est exclu.
    s_jeune = page.evaluate("""(m) => calculeStats([
        {title: "a", link: "1", date: "2026-09-19T15:00:00Z"},
        {title: "b", link: "2", date: "2026-09-20T12:00:00Z"},
        {title: "c", link: "3", date: "2026-09-21T12:00:00Z"},
        {title: "d", link: "4", date: "2026-09-22T12:00:00Z"},
      ], null, new Date(m))""", MAINTENANT)
    check([j["jour"] for j in s_jeune["jours"]] == ["2026-09-20", "2026-09-21", "2026-09-22"],
          "sur un fil de quatre jours, le premier — partiel — n'est pas dessiné")

    # ---- 2. Même avec cent jours de données, jamais plus de 14 ----
    s2 = page.evaluate("""(m) => {
      const items = [];
      const d = new Date("2026-06-10T12:00:00Z");
      while(d < new Date("2026-09-23T00:00:00Z")){
        items.push({title: "x", link: "x" + d.getTime(), date: d.toISOString()});
        d.setTime(d.getTime() + 86400000);
      }
      return calculeStats(items, null, new Date(m));
    }""", MAINTENANT)
    check(len(s2["jours"]) == 14,
          "cent jours de données ne font pas un histogramme plus long : la "
          "borne ne dépend pas de ce que le fil paraît contenir (%d)" % len(s2["jours"]))

    # Une date illisible venue d'un flux ne plante rien et ne compte dans
    # aucun jour. Le défaut est apparu ici par accident — le test lui-même
    # fabriquait « T010 » — et il vaut mieux le verrouiller exprès.
    s_bad = page.evaluate("""(m) => calculeStats([
        {title: "ok", link: "1", date: "2026-09-20T12:00:00Z"},
        {title: "abîmée", link: "2", date: "2026-09-21T010:00:00Z"},
        {title: "ok", link: "3", date: "2026-09-22T12:00:00Z"},
      ], null, new Date(m))""", MAINTENANT)
    check(sum(j["n"] for j in s_bad["jours"]) == 1,
          "une date illisible n'est comptée dans aucun jour (%s)"
          % [(j["jour"], j["n"]) for j in s_bad["jours"]])

    # Un jour SANS article, à l'intérieur de la couverture, est un vrai zéro.
    s3 = page.evaluate("""(m) => calculeStats([
        {title: "a", link: "1", date: "2026-09-18T12:00:00Z"},
        {title: "b", link: "2", date: "2026-09-20T12:00:00Z"},
        {title: "c", link: "3", date: "2026-09-22T12:00:00Z"},
      ], null, new Date(m))""", MAINTENANT)
    check([(j["jour"], j["n"]) for j in s3["jours"]]
          == [("2026-09-19", 0), ("2026-09-20", 1), ("2026-09-21", 0), ("2026-09-22", 1)],
          "un jour creux dans la couverture compte zéro, il n'est pas sauté : %s"
          % [(j["jour"], j["n"]) for j in s3["jours"]])

    # ---- 3. Sujets repris, et sources ----
    s4 = page.evaluate("""(m) => calculeStats([
        {title: "Peu repris", link: "p", date: "2026-09-20T12:00:00Z", extraSources: [{}]},
        {title: "Très repris", link: "t", date: "2026-09-19T12:00:00Z", extraSources: [{}, {}, {}, {}]},
        {title: "Seul", link: "s", date: "2026-09-21T12:00:00Z"},
      ], {
        sources_health: [
          {id: "a", name: "Alpha", status: "ok", articles_exclusifs: 3},
          {id: "b", name: "Bravo", status: "ok", articles_exclusifs: 19},
          {id: "c", name: "Charlie", status: "tarie", articles_exclusifs: 0},
          {id: "r", name: "Reddit — suivi", status: "en_attente", articles_exclusifs: 0},
        ],
        sources_declining: {a: {habituel: 20, recents: [2, 1, 3]}},
      }, new Date(m))""", MAINTENANT)
    check([r["titre"] for r in s4["repris"]] == ["Très repris", "Peu repris"],
          "les sujets repris sont classés par nombre de rédactions, et un article "
          "seul n'y figure pas")
    check(s4["repris"][0]["redactions"] == 5, "l'article et ses 4 reprises : 5 rédactions")
    check([p["nom"] for p in s4["sources"]["productives"]] == ["Bravo", "Alpha"],
          "les sources qui apportent le plus, de la plus à la moins productive")
    check(s4["sources"]["steriles"] == ["Charlie"],
          "« aucun exclusif » ne compte pas une source simplement au repos : %s"
          % s4["sources"]["steriles"])
    check(s4["sources"]["enBaisse"] == ["Alpha"],
          "les sources en baisse sont nommées, pas désignées par leur id")
    check(s4["sources"]["statuts"] == {"ok": 2, "tarie": 1, "en_attente": 1},
          "et les statuts sont comptés (%s)" % s4["sources"]["statuts"])

    # ---- 4. La bascule de vue : chaque panneau affiché ou masqué ----
    def visible(sel):
        return page.evaluate("(s) => { const e = document.querySelector(s); "
                             "return !!e && getComputedStyle(e).display !== 'none'; }", sel)

    # LES DATES SONT RELATIVES À AUJOURD'HUI, et ce n'est pas un détail.
    #
    # Elles étaient écrites en dur : « 2026-09-01 » à « 2026-09-22 ». Sans
    # `couverture_depuis`, la fenêtre des statistiques vaut
    # STATS_JOURS_SECOURS = 14 jours, et elle avance d'un jour par jour. Le
    # 06/10/2026 elle allait du 22/09 au 05/10 et attrapait le dernier
    # article du lot, au jour près. Le 07/10 à minuit heure de Paris elle
    # est passée au 23/09, le fil de référence s'est retrouvé ENTIÈREMENT
    # hors fenêtre, l'histogramme n'a plus eu une seule colonne non nulle
    # et le contrôle du maximum écrit est tombé — sur un code que personne
    # n'avait touché.
    #
    # Un contrôle qui expire tout seul à une date qu'aucun commentaire
    # n'annonce est pire qu'un contrôle absent : il part en rouge un matin,
    # sur la branche de quelqu'un d'autre, et accuse le mauvais changement.
    page.evaluate("""() => {
      historyPartial = false; rattrapageLance = true; archiveChargee = true;
      lastItems = [];
      const base = Date.now();
      for(let j = 1; j <= 22; j++){
        lastItems.push({title: "Article " + j, link: "https://ex.test/" + j,
          source: "S", date: new Date(base - j * 86400000).toISOString(),
          extraSources: j === 5 ? [{source: "X", link: "https://y.test"}] : null});
      }
      derniereReponseBackend = {sources_health: [
        {id: "a", name: "Alpha", status: "ok", articles_exclusifs: 4}]};
    }""")
    page.click("#statsBtn")
    page.wait_for_selector("#statsContenu .stats-bloc", timeout=5000)
    check(visible("#statsPanel"), "📊 ouvre le panneau des statistiques")
    check(not visible("#feed"), "le fil est masqué sous les statistiques")
    check(not visible(".search-row"), "la recherche aussi — elle n'a pas de sens ici")
    check(not visible("#logsPanel"), "et le Journal reste fermé")
    check(page.get_attribute("#statsBtn", "aria-pressed") == "true",
          "le bouton annonce son état actif")

    page.evaluate("() => setTab('logs')")
    check(visible("#logsPanel") and not visible("#statsPanel"),
          "passer au Journal referme les statistiques")
    page.evaluate("() => setTab('all')")
    check(visible("#feed") and visible(".search-row") and not visible("#statsPanel"),
          "revenir au fil rend le fil ET la recherche")
    check(page.get_attribute("#statsBtn", "aria-pressed") == "false",
          "et le bouton redevient inactif")

    # Une vue n'est pas mémorisée : rouvrir l'app ramène au fil.
    page.evaluate("() => setTab('stats')")
    memo = page.evaluate("() => JSON.parse(localStorage.getItem(STORAGE_PREFIX + CLE_FILTRES) || '{}').onglet")
    check(memo != "stats", "l'onglet mémorisé n'est jamais « stats » (%s)" % memo)

    # ---- 5. Le rendu : une seule étiquette, et un tableau ----
    page.wait_for_selector(".stats-histo", timeout=5000)
    check(page.locator(".stats-histo").count() >= 1, "l'histogramme quotidien est dessiné")
    check(page.locator(".stats-histo em").count() == page.locator(".stats-histo").count(),
          "un seul chiffre écrit par histogramme — le maximum, pas un par colonne "
          "(%d em pour %d histogramme(s))"
          % (page.locator(".stats-histo em").count(),
             page.locator(".stats-histo").count()))
    # Le filet qui aurait nommé la vraie cause ci-dessus. Zéro colonne
    # remplie ne se distingue pas, dans le contrôle précédent, d'un maximum
    # qu'on aurait oublié d'écrire — et c'est pourtant tout l'écart entre
    # « le fil de référence a glissé hors fenêtre » et « le rendu est
    # cassé ».
    vides = page.locator('.stats-histo .stats-col[data-lecture="0"]').count()
    colonnes = page.locator(".stats-histo .stats-col").count()
    check(colonnes > 0 and vides < colonnes,
          "et le fil de référence tombe bien DANS la fenêtre : %d colonne(s) "
          "remplie(s) sur %d, pas un histogramme vide"
          % (colonnes - vides, colonnes))
    graphes = page.locator(".stats-histo").count() + page.locator(".stats-carte").count()
    check(page.locator(".stats-tableau table").count() == graphes,
          "chaque graphique — histogrammes et carte jour × heure — a son tableau : "
          "tout reste lisible sans toucher une seule colonne")
    texte_fil = page.inner_text("#statsContenu")
    check("Articles par mois" not in texte_fil,
          "aucun histogramme mensuel : il faudrait que le robot publie sa couverture")
    check("ouverture complète" not in texte_fil and "depuis le" not in texte_fil,
          "et AUCUNE date de couverture affichée — c'est l'affirmation fausse "
          "que la première version montrait (« depuis le 07/08 »)")
    check("pas la presse" in texte_fil,
          "le graphique dit ce qu'il mesure : le fil, pas le volume de la presse")

    # Bloc vide = bloc absent.
    page.click("#statsOnglet_sources")
    page.wait_for_timeout(100)
    texte = page.inner_text("#statsContenu")
    check("Alpha" in texte, "la rubrique Sources affiche la réponse du robot")
    check("En forte baisse" not in texte,
          "et n'affiche PAS « en forte baisse » quand aucune ne l'est")
    check(page.get_attribute("#statsOnglet_sources", "aria-pressed") == "true",
          "le sous-onglet actif l'annonce")

    # ---- 6. Tant que l'historique est partiel : pas de chiffres faux ----
    page.route("**/feed.json*", lambda r: r.abort())
    page.evaluate("() => { historyPartial = true; ongletStats = 'fil'; renderStats(); }")
    texte = page.inner_text("#statsContenu")
    check("Chargement de l'historique complet" in texte,
          "sur un historique partiel, la vue attend au lieu de calculer")
    check(page.locator("#statsContenu .stats-histo").count() == 0,
          "et ne dessine aucun histogramme sur les 300 articles récents")
    page.unroute("**/feed.json*")

    ctx.close()

    # ---- 7. Pas de débordement sur un petit écran ----
    ctx = nav.new_context(viewport={"width": 320, "height": 700})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("""() => {
      historyPartial = false; rattrapageLance = true; archiveChargee = true;
      lastItems = [];
      const d = new Date("2026-08-01T12:00:00Z");
      for(let n = 0; n < 60; n++){
        lastItems.push({title: "Un titre d'article assez long pour tester le retour à la ligne " + n,
          link: "https://ex.test/" + n, source: "S", date: new Date(d.getTime() + n * 86400000).toISOString(),
          extraSources: n % 7 === 0 ? [{}, {}] : null});
      }
      setTab('stats');
    }""")
    page.wait_for_selector(".stats-histo", timeout=5000)
    # Les quatre rubriques, pas seulement la première : c'est le quatrième
    # sous-onglet, « Ma lecture », qui dépassait de 49 px.
    for o in ("fil", "sources", "robot", "moi"):
        page.evaluate("(o) => ongletStatsChoisi(o)", o)
        page.wait_for_timeout(100)
        deborde = page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")
        check(not deborde, "à 320 px, la rubrique « %s » ne déborde pas horizontalement" % o)
    ctx.close()


def test_barre_du_bas(nav, url):
    """Barre flottante du bas et entête à tuile (choix d'Antoni, 07/11/2026).

    Mesuré dans un vrai navigateur, à 320 et 390 px, en clair et en sombre :
    aucune tuile ne déborde, la barre ne recouvre pas le dernier article, chaque
    entrée fait au moins 44 px, « Fil » revient au dernier filtre, le Journal
    s'ouvre depuis Statistiques › Robot, et le gros chiffre suit le calendrier
    (J-n en jours civils à Paris, H-n le dernier jour, +n j après la sortie).
    """
    print("\n[barre] navigation du bas, tuile du compteur, Journal dans Statistiques")

    def ouvre(largeur, schema, quand, jeton=False):
        ctx = nav.new_context(viewport={"width": largeur, "height": 740}, color_scheme=schema,
                              timezone_id="Europe/Paris", reduced_motion="reduce")
        ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));}catch(e){}")
        page = ctx.new_page()
        page.clock.install(time=quand)
        erreurs = []
        page.on("pageerror", lambda e: erreurs.append(str(e)))
        page.goto(url, wait_until="load")
        page.wait_for_selector(".barre-bas")
        page.clock.run_for(1500)
        return ctx, page, erreurs

    for largeur, schema in ((320, "dark"), (390, "light"), (320, "light"), (390, "dark")):
        ctx, page, erreurs = ouvre(largeur, schema, "2026-11-07T10:00:00+01:00")
        etiquette = "[%d px, %s]" % (largeur, schema)
        mesures = page.evaluate("""() => {
            const r = e => e.getBoundingClientRect();
            const barre = r(document.querySelector('.barre-bas:not(#apercuCouleurs *)'));
            const b = [...document.querySelectorAll('.barre-bas:not(#apercuCouleurs *) button')].map(x => r(x));
            const tuile = r(document.querySelector('.tuile-compteur'));
            const hors = [...document.querySelectorAll('.tuile-compteur > *, header.console > *')]
              .filter(e => e.offsetParent !== null && (r(e).right > innerWidth + 0.5 || r(e).left < -0.5)).length;
            return {bas: barre.bottom, haut: barre.top, larg: barre.width, vh: innerHeight, vw: innerWidth,
                    hauteurs: b.map(x => x.height), largeurs: b.map(x => x.width),
                    hors, debordement: document.documentElement.scrollWidth > innerWidth,
                    tuileDroite: tuile.right};
        }""")
        check(not mesures["debordement"] and mesures["hors"] == 0,
              "%s aucune tuile ni page qui déborde sur le côté" % etiquette)
        check(all(h >= 44 for h in mesures["hauteurs"]) and all(w >= 44 for w in mesures["largeurs"]),
              "%s chaque entrée de la barre fait au moins 44 px (%s × %s)"
              % (etiquette, [round(w) for w in mesures["largeurs"]], [round(h) for h in mesures["hauteurs"]]))
        check(mesures["bas"] <= mesures["vh"] and mesures["haut"] > 0,
              "%s la barre est entière dans l'écran" % etiquette)
        # Le dernier article ne doit pas finir sous la barre : on défile tout en bas.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        bas_contenu = page.evaluate("""() => {
            const f = document.querySelector('#feed') || document.body;
            const dernier = [...document.querySelectorAll('#feed > *, .info-btn')].filter(e => e.offsetParent !== null).pop();
            return dernier ? dernier.getBoundingClientRect().bottom : 0;
        }""")
        check(bas_contenu <= mesures["haut"] + 1,
              "%s le bas du contenu (%.0f) reste au-dessus de la barre (%.0f)" % (etiquette, bas_contenu, mesures["haut"]))
        check(not erreurs, "%s aucune erreur JavaScript" % etiquette)
        ctx.close()

    # Les gestes, à 390 px.
    ctx, page, erreurs = ouvre(390, "light", "2026-11-07T10:00:00+01:00")
    # Les trois sources sont repliées derrière « Par source » (11/11/2026) : on déplie avant de choisir.
    page.click("#sourcesBtn")
    page.click("#tabRockstar")
    page.click("#statsBtn")
    check(page.get_attribute("#navFil", "aria-pressed") == "false"
          and page.get_attribute("#statsBtn", "aria-pressed") == "true",
          "Stats s'allume et Fil s'éteint")
    visibles = page.evaluate("""() => ['#tabAll', '#tabMajeur', '#tabNonRockstar', '#tabRockstar', '#tabRockstarmag', '#countLine']
        .filter(s => document.querySelector(s).offsetParent !== null)""")
    check(visibles == [],
          "dans Statistiques, plus d'onglets du fil ni de ligne de comptage (encore visibles : %s)" % visibles)
    page.click("#navFil")
    check(page.evaluate("() => currentTab") == "rockstar"
          and page.get_attribute("#navFil", "aria-pressed") == "true",
          "« Fil » revient au dernier filtre choisi (Rockstar), pas à « Tous »")
    page.click("#statsBtn")
    page.evaluate("() => ongletStatsChoisi('robot')")
    page.click("#ouvreJournal")
    check(page.is_visible("#logsPanel") and not page.is_visible("#statsPanel"),
          "Statistiques › Robot ouvre le Journal des passages")
    check(not page.is_visible("#tabAll") and not page.is_visible("#countLine"),
          "et le Journal non plus n'a pas les onglets du fil")
    page.click("#navFil")
    check(page.is_visible("#feed") and not page.is_visible("#logsPanel"),
          "« Fil » referme le Journal")
    check(page.is_visible("#tabAll") and page.is_visible("#countLine"),
          "et rend au fil ses onglets et sa ligne de comptage")
    page.click("#countdown")
    check(page.evaluate("() => document.getElementById('compteurOverlay').classList.contains('open')"),
          "toucher la tuile de l'entête ouvre la fenêtre du compteur")
    page.keyboard.press("Escape")
    page.click("#navReglages")
    check(page.evaluate("() => document.getElementById('settingsOverlay').classList.contains('open')"),
          "« Réglages » ouvre les réglages")
    ctx.close()

    # QUATRE TUILES vivantes, et une ligne réduite dont la précision suit l'urgence.
    import re as _re
    for quand, motif, sous, zero, nom in (
            ("2026-11-07T10:00:00+01:00", r"^11j 1[34]h$", "avant la sortie", False, "loin : jours et heures"),
            ("2026-11-16T10:00:00+01:00", r"^2j 1[34]h \d\dm$", "avant la sortie", False, "sous sept jours : avec les minutes"),
            ("2026-11-18T23:30:00+01:00", r"^00:\d\d:\d\d$", "avant la sortie", True, "dernier jour : HH:MM:SS"),
            ("2026-11-19T10:00:00+01:00", r"^SORTI !$", "GTA VI est sorti · J+0", True, "le jour de la sortie"),
            ("2026-11-22T10:00:00+01:00", r"^\+3 j$", "depuis la sortie · J+3", False, "trois jours après")):
        ctx, page, erreurs = ouvre(390, "light", quand)
        page.evaluate("updateCountdown()")
        lu = page.evaluate("""() => ({t: document.getElementById('cbCompteur').textContent,
            s: document.querySelector('.tuile-compteur .compteur-sous').textContent,
            n: document.getElementById('countdown').getAttribute('aria-label'),
            cases: [...document.querySelectorAll('#countdown .cd-t')].map(e => e.querySelector('b').textContent),
            libelles: [...document.querySelectorAll('#countdown .cd-t i')].map(e => e.textContent),
            grises: [...document.querySelectorAll('#countdown .cd-t.zero')].map(e => e.dataset.u),
            taille: parseFloat(getComputedStyle(document.querySelector('#countdown .cd-t b')).fontSize)})""")
        check(_re.match(motif, lu["t"]) is not None and lu["s"] == sous,
              "%s : ligne réduite « %s », libellé « %s »" % (nom, lu["t"], lu["s"]))
        check(len(lu["cases"]) == 4 and all(_re.match(r"^\d\d+$", c) for c in lu["cases"])
              and lu["libelles"] == ["jours", "heures", "min", "sec"],
              "%s : quatre tuiles jours, heures, min, sec, deux chiffres au moins (%s)" % (nom, lu["cases"]))
        check((lu["grises"] == ["j"]) == zero and (zero or lu["grises"] == []),
              "%s : la tuile des jours n'est grisée que quand elle vaut 00 (%s)" % (nom, lu["grises"]))
        check(lu["taille"] >= 24 and lu["t"] in (lu["n"] or ""),
              "%s : chiffres de %.0f px, bouton nommé pour les lecteurs d'écran" % (nom, lu["taille"]))
        ctx.close()

    # L'icône ▶ n'apparaît qu'avec un jeton, et fait au moins 44 px une fois visible.
    ctx, page, erreurs = ouvre(390, "light", "2026-11-07T10:00:00+01:00")
    check(not page.is_visible("#triggerRunBtn"), "sans jeton, « Relancer le robot » est caché")
    page.evaluate("() => { storageSet(TOKEN_STORAGE_KEY, 'x'); refreshTokenUI(); }")
    page.wait_for_selector("#triggerRunBtn", state="visible")
    boite = page.evaluate("""() => { const r = document.getElementById('triggerRunBtn').getBoundingClientRect();
        const apres = getComputedStyle(document.getElementById('triggerRunBtn'), '::after');
        return {w: r.width, h: r.height, ext: parseFloat(apres.top) }; }""")
    check(boite["w"] + 2 * abs(boite["ext"]) >= 44 and boite["h"] + 2 * abs(boite["ext"]) >= 44,
          "avec un jeton, l'icône ▶ a au moins 44 px de zone de clic (%.0f × %.0f + %.0f)"
          % (boite["w"], boite["h"], abs(boite["ext"])))
    ctx.close()


def test_page_reglages(nav, url):
    """Les Réglages sont une PAGE, plus un pop-up (demande d'Antoni, 07/11/2026).

    Mesuré dans un vrai navigateur : la page s'ouvre sous la ligne réduite (l'en-tête déplié est masqué
    sur cette page depuis le 10/11/2026), le fil se masque, la barre du bas allume « Réglages » (et eux seuls), les rubriques
    restent collées en haut pendant le défilement, le dernier réglage se lit
    au-dessus de la barre, rien ne déborde, et « Fil » comme « Stats » la referment.
    """
    print("\n[réglages] une page sous la ligne réduite, plus un pop-up")
    for largeur, schema in ((320, "dark"), (390, "light")):
        etiq = "[%d px, %s]" % (largeur, schema)
        ctx = nav.new_context(viewport={"width": largeur, "height": 740}, color_scheme=schema,
                              timezone_id="Europe/Paris", reduced_motion="reduce")
        ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));}catch(e){}")
        page = ctx.new_page()
        erreurs = []
        page.on("pageerror", lambda e: erreurs.append(str(e)))
        page.goto(url, wait_until="load")
        page.wait_for_selector(".barre-bas")
        page.click("#navReglages")
        page.wait_for_timeout(300)   # l'observateur de la ligne réduite réagit à l'image suivante
        etat = page.evaluate("""() => {
            const vis = s => { const e = document.querySelector(s); return !!e && e.offsetParent !== null
              && getComputedStyle(e).visibility !== 'hidden'; };
            const sp = document.querySelector('#settingsOverlay .settings-panel');
            return {page: vis('#settingsOverlay'), fil: vis('#feed'), entete: vis('#countdown'),
                    // La ligne réduite est en position FIXE : offsetParent y vaut toujours null, donc vis() la
                    // croirait cachée. On lit sa classe.
                    ligne: document.getElementById('barreHaut').classList.contains('visible'),
                    pressed: [...document.querySelectorAll('.barre-bas:not(#apercuCouleurs *) button')].map(b => b.getAttribute('aria-pressed')),
                    corps: getComputedStyle(document.body).position,
                    fond: getComputedStyle(document.getElementById('settingsOverlay')).position,
                    cadre: sp ? getComputedStyle(sp).borderTopWidth : null,
                    dialogue: !!document.querySelector('#settingsOverlay [aria-modal]'),
                    focus: document.activeElement.id,
                    deborde: document.documentElement.scrollWidth > innerWidth}; }""")
        # Depuis le 10/11/2026 l'en-tête DÉPLIÉ est masqué sur Réglages (planche d'Antoni) : la ligne
        # réduite le remplace. Avant, ce contrôle exigeait l'en-tête visible.
        check(etat["page"] and not etat["fil"] and not etat["entete"] and etat["ligne"],
              "%s la page s'ouvre sous la ligne réduite, l'en-tête déplié et le fil se masquent" % etiq)
        check(etat["pressed"] == ["false", "false", "true"],
              "%s la barre du bas allume « Réglages » seul (%s)" % (etiq, etat["pressed"]))
        check(etat["corps"] != "fixed" and etat["fond"] != "fixed" and not etat["dialogue"]
              and etat["cadre"] == "0px",
              "%s ni voile, ni fond figé, ni cadre de pop-up" % etiq)
        check(etat["focus"] == "titreParametres", "%s le titre reçoit le focus" % etiq)
        check(not etat["deborde"] and not erreurs, "%s rien ne déborde, aucune erreur JavaScript" % etiq)

        # Les rubriques restent collées en haut quand on défile.
        page.click('.panneau-tabs button[data-cible="adv"]')
        page.evaluate("window.scrollTo(0, 500)")
        page.wait_for_timeout(150)
        haut = page.evaluate("() => document.querySelector('.panneau-tabs').getBoundingClientRect().top")
        # Collées SOUS la ligne réduite de 52 px (07/11/2026) : en défilant, elle est apparue.
        check(abs(haut - 52) <= 1, "%s les rubriques restent collées sous la ligne réduite en défilant (%.0f px)" % (etiq, haut))

        # Le dernier réglage se lit entièrement, au-dessus de la barre flottante.
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(150)
        bas = page.evaluate("""() => { const c = [...document.querySelectorAll('#settingsOverlay .setting-group')]
            .filter(e => e.offsetParent !== null).pop();
            return {contenu: c.getBoundingClientRect().bottom,
                    barre: document.querySelector('.barre-bas:not(#apercuCouleurs *)').getBoundingClientRect().top}; }""")
        check(bas["contenu"] <= bas["barre"] + 1,
              "%s le dernier réglage (%.0f) reste au-dessus de la barre (%.0f)" % (etiq, bas["contenu"], bas["barre"]))

        # « Stats » et « Fil » la referment.
        page.click("#statsBtn")
        etat = page.evaluate("""() => ({page: document.getElementById('settingsOverlay').classList.contains('open'),
            stats: document.getElementById('statsPanel').offsetParent !== null,
            pressed: [...document.querySelectorAll('.barre-bas:not(#apercuCouleurs *) button')].map(b => b.getAttribute('aria-pressed'))})""")
        check(not etat["page"] and etat["stats"] and etat["pressed"] == ["false", "true", "false"],
              "%s « Stats » referme la page et ouvre les statistiques (%s)" % (etiq, etat["pressed"]))
        page.click("#navReglages")
        page.click("#navFil")
        etat = page.evaluate("""() => ({page: document.getElementById('settingsOverlay').classList.contains('open'),
            fil: document.getElementById('feed').offsetParent !== null,
            pressed: [...document.querySelectorAll('.barre-bas:not(#apercuCouleurs *) button')].map(b => b.getAttribute('aria-pressed'))})""")
        check(not etat["page"] and etat["fil"] and etat["pressed"] == ["true", "false", "false"],
              "%s « Fil » referme la page et rend le fil (%s)" % (etiq, etat["pressed"]))
        ctx.close()


def test_entete_tuiles_et_ligne_reduite(nav, url):
    """En-tête final (07/11/2026) : rangée centrée, quatre tuiles, ligne réduite en défilant.

    Mesuré dans un vrai navigateur, à 320 et 390 px, avec et sans jeton : titre, pastille
    « Backend », ▶ et ↻ ont le MÊME centre vertical (la maquette avait posé la pastille 6 px
    plus bas), rien ne sort de la carte (avec un jeton à 390 px, la rangée entière dépassait
    de 30 px), le libellé est centré sous les tuiles, et la ligne réduite apparaît quand
    l'entête quitte le haut, avec le point vert seulement en mode backend, le jour
    « AUJOURD'HUI » et les rubriques des Réglages se collant dessous.
    """
    print("\n[entête] rangée centrée, tuiles, ligne réduite")
    for largeur, schema, jeton in ((390, "light", False), (390, "light", True),
                                   (320, "dark", False), (320, "dark", True)):
        etiq = "[%d px, %s, %s]" % (largeur, schema, "jeton" if jeton else "sans jeton")
        ctx = nav.new_context(viewport={"width": largeur, "height": 700}, color_scheme=schema,
                              timezone_id="Europe/Paris", reduced_motion="reduce")
        ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));%s}catch(e){}"
                            % ("localStorage.setItem('gta6watch:github-token-v1','x');" if jeton else ""))
        page = ctx.new_page()
        erreurs = []
        page.on("pageerror", lambda e: erreurs.append(str(e)))
        page.goto(url, wait_until="load")
        page.wait_for_selector(".barre-bas")
        page.evaluate("""() => { const m = document.getElementById('modeIndicator');
            m.className = 'mode-indicator backend';
            m.innerHTML = '<span class="dot"></span><span class="mi-mot">Backend GitHub</span>';
            lastItems = Array.from({length: 14}, (_, i) => ({title: 'Article de test numéro ' + (i + 1) + ' — un titre assez long pour passer sur deux lignes',
              link: 'https://ex.test/' + i, description: 'Résumé.', date: new Date(Date.now() - i * 3600e3).toISOString(), source: 'S' + (i % 3)}));
            lastNewLinks = new Set(); historyPartial = false; rattrapageLance = true; visibleCount = 30; applyFilters(); }""")
        page.wait_for_timeout(300)
        m = page.evaluate("""() => {
            const r = e => e.getBoundingClientRect();
            const haut = [...document.querySelector('.header-haut').children].filter(e => e.offsetParent !== null);
            const carte = r(document.querySelector('header.console'));
            const centre = e => (r(e).top + r(e).bottom) / 2;
            const sous = r(document.querySelector('.tuile-compteur .compteur-sous')), cd = r(document.getElementById('countdown'));
            const mot = document.querySelector('#modeIndicator .mi-mot');
            return {centres: haut.map(centre), n: haut.length,
                    sortent: haut.filter(e => r(e).right > carte.right - 14 + 0.5 || r(e).left < carte.left + 14 - 0.5).length,
                    ecartLibelle: Math.abs((sous.left + sous.right) / 2 - (cd.left + cd.right) / 2),
                    mot: getComputedStyle(mot).display !== 'none',
                    deborde: document.documentElement.scrollWidth > innerWidth}; }""")
        check(max(m["centres"]) - min(m["centres"]) <= 1.0,
              "%s titre, pastille, ▶ et ↻ ont le même centre vertical (%s)" % (etiq, [round(c) for c in m["centres"]]))
        check(m["sortent"] == 0 and not m["deborde"],
              "%s rien ne sort de la carte ni de l'écran (%d sortent)" % (etiq, m["sortent"]))
        check(m["ecartLibelle"] <= 1.0, "%s le libellé est centré sous les tuiles (écart %.1f px)" % (etiq, m["ecartLibelle"]))
        attendu = (largeur >= 430) or (largeur >= 360 and not jeton)
        check(m["mot"] == attendu,
              "%s le mot « Backend GitHub » est %s (%s)" % (etiq, "affiché" if attendu else "remplacé par un point", m["mot"]))

        # La ligne réduite : cachée en haut, visible en défilant.
        etat = page.evaluate("""() => { const b = document.getElementById('barreHaut'); const cs = getComputedStyle(b);
            return {visible: b.classList.contains('visible'), vis: cs.visibility}; }""")
        check(not etat["visible"] and etat["vis"] == "hidden",
              "%s tout en haut, la ligne réduite est cachée (%s)" % (etiq, etat["vis"]))
        page.evaluate("window.scrollTo(0, 500)")
        page.wait_for_timeout(700)
        r2 = page.evaluate("""() => { const b = document.getElementById('barreHaut'); const q = b.getBoundingClientRect();
            const jour = [...document.querySelectorAll('.day-label')].find(e => e.getBoundingClientRect().bottom > 0);
            return {visible: b.classList.contains('visible'), top: q.top, h: q.height,
                    point: getComputedStyle(document.querySelector('.bh-point')).display,
                    cpt: document.getElementById('cbCompteur').textContent,
                    jourTop: jour ? jour.getBoundingClientRect().top : null,
                    ghost: document.documentElement.scrollWidth > innerWidth}; }""")
        check(r2["visible"] and abs(r2["top"]) <= 0.5 and abs(r2["h"] - 52) <= 0.5,
              "%s en défilant, la ligne réduite de 52 px se colle en haut (top %.0f, haut %.0f)" % (etiq, r2["top"], r2["h"]))
        check(r2["point"] != "none", "%s son point vert est là en mode backend" % etiq)
        check(r2["jourTop"] is not None and abs(r2["jourTop"] - 52) <= 1.5,
              "%s le jour « AUJOURD'HUI » se colle juste dessous (top %s)" % (etiq, r2["jourTop"]))
        page.evaluate("() => { const m = document.getElementById('modeIndicator'); m.className = 'mode-indicator direct'; }")
        pt = page.evaluate("() => getComputedStyle(document.querySelector('.bh-point')).display")
        check(pt == "none", "%s hors mode backend (mode direct), le point disparaît" % etiq)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(700)
        check(not page.evaluate("() => document.getElementById('barreHaut').classList.contains('visible')"),
              "%s remonté tout en haut, la ligne réduite repart" % etiq)

        # Dans les Réglages, les rubriques se collent sous la ligne.
        page.evaluate("() => { const m = document.getElementById('modeIndicator'); m.className = 'mode-indicator backend'; }")
        page.click("#navReglages")
        page.click('.panneau-tabs button[data-cible="adv"]')   # la rubrique la plus longue : la page défile
        page.evaluate("window.scrollTo(0, 900)")
        page.wait_for_timeout(700)
        top = page.evaluate("() => document.querySelector('.panneau-tabs').getBoundingClientRect().top")
        check(abs(top - 52) <= 1.5, "%s dans les Réglages, les rubriques se collent sous la ligne (top %.0f)" % (etiq, top))
        check(not erreurs, "%s aucune erreur JavaScript" % etiq)
        ctx.close()


def test_fenetre_du_compteur(nav, url):
    """La fenêtre du compteur : centrée, quatre coins arrondis, barre rectangulaire.

    Demandé par Antoni le 07/11/2026, capture à l'appui : la fenêtre était collée en haut,
    arrondie seulement au-dessus, et la barre de progression était une pastille dont les
    bouts arrondis cachaient les derniers pourcents. Mesuré à 320 et 390 px, en clair et en
    sombre : le centre de la fenêtre est celui de l'écran (à 1 px), les quatre rayons sont
    arrondis et égaux, la barre a des coins vifs, et la part restante se distingue.
    """
    print("\\n[fenêtre du compteur] centrée, arrondie, barre rectangulaire")
    for largeur, hauteur, schema in ((320, 640, "dark"), (390, 844, "light"), (390, 700, "dark")):
        etiq = "[%dx%d, %s]" % (largeur, hauteur, schema)
        ctx = nav.new_context(viewport={"width": largeur, "height": hauteur}, color_scheme=schema,
                              timezone_id="Europe/Paris", reduced_motion="reduce")
        ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));}catch(e){}")
        page = ctx.new_page()
        erreurs = []
        page.on("pageerror", lambda e: erreurs.append(str(e)))
        page.goto(url, wait_until="load")
        page.wait_for_selector("#countdown")
        page.click("#countdown")
        page.wait_for_selector("#compteurOverlay .sheet-panel", state="visible")
        page.wait_for_timeout(300)
        m = page.evaluate("""() => {
            const p = document.querySelector('#compteurOverlay .sheet-panel'), r = p.getBoundingClientRect(), cs = getComputedStyle(p);
            const bar = document.querySelector('.compteur-barre'), bs = getComputedStyle(bar), fill = bar.querySelector(':scope > span');
            return {dx: Math.abs((r.left + r.right) / 2 - innerWidth / 2), dy: Math.abs((r.top + r.bottom) / 2 - innerHeight / 2),
                    haut: r.top, bas: innerHeight - r.bottom, gauche: r.left, droite: innerWidth - r.right,
                    rayons: [cs.borderTopLeftRadius, cs.borderTopRightRadius, cs.borderBottomLeftRadius, cs.borderBottomRightRadius],
                    bordBas: cs.borderBottomWidth,
                    barre: [bs.borderTopLeftRadius, bs.borderTopRightRadius, bs.borderBottomLeftRadius, bs.borderBottomRightRadius],
                    reste: bar.getBoundingClientRect().right - fill.getBoundingClientRect().right,
                    deborde: document.documentElement.scrollWidth > innerWidth}; }""")
        check(m["dx"] <= 1.0 and m["dy"] <= 1.0,
              "%s la fenêtre est centrée sur l'écran (écarts %.1f px en largeur, %.1f px en hauteur)" % (etiq, m["dx"], m["dy"]))
        check(m["haut"] >= 0 and m["bas"] >= 0 and m["gauche"] >= 0 and m["droite"] >= 0,
              "%s elle tient entière dans l'écran" % etiq)
        check(len(set(m["rayons"])) == 1 and m["rayons"][0] != "0px" and m["bordBas"] != "0px",
              "%s les quatre coins sont arrondis, et le bas a son bord (%s)" % (etiq, m["rayons"]))
        check(set(m["barre"]) == {"0px"},
              "%s la barre de progression est rectangulaire (%s)" % (etiq, m["barre"]))
        check(m["reste"] > 0.5, "%s la part restante de la barre se voit (%.1f px)" % (etiq, m["reste"]))
        check(not m["deborde"] and not erreurs, "%s rien ne déborde, aucune erreur JavaScript" % etiq)
        ctx.close()

    # LES ÉQUIVALENCES en cartes à icônes : le pire total (151 millions de secondes, à 320 px) tient dans sa carte.
    ctx = nav.new_context(viewport={"width": 320, "height": 700}, timezone_id="Europe/Paris",
                          reduced_motion="reduce")
    ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));}catch(e){}")
    ctx.clock.install(time="2022-02-06T12:00:00Z")
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#countdown")
    page.click("#countdown")
    page.wait_for_selector("#compteurContenu .compteur-tuiles.equiv", state="visible")
    eq = page.evaluate("""() => { const g = document.querySelector('#compteurContenu .compteur-tuiles.equiv');
        const cartes = [...g.children]; let dehors = null;
        for(const d of cartes){ const b = d.querySelector('b'), r = document.createRange(); r.selectNodeContents(b);
          const q = r.getBoundingClientRect(), dq = d.getBoundingClientRect();
          if(q.right > dq.right + 0.5 || q.left < dq.left - 0.5) dehors = b.textContent + ' sort de sa carte'; }
        return {n: cartes.length, icones: cartes.filter(d => d.querySelector('svg.ic')).length,
                libelles: cartes.map(d => d.querySelector('span').textContent.trim()), dehors: dehors,
                longs: cartes.filter(d => d.querySelector('b.long')).length}; }""")
    check(eq["n"] == 4 and eq["icones"] == 4 and eq["libelles"] == ["jours", "heures", "minutes", "secondes"],
          "[320 px] quatre cartes d'équivalence, chacune avec son icône (%s)" % eq["libelles"])
    check(eq["dehors"] is None and eq["longs"] >= 1,
          "[320 px] même 151 millions de secondes tiennent dans leur carte (corps réduit sur %d carte(s), %s)"
          % (eq["longs"], eq["dehors"]))
    ctx.close()

    # APRÈS LA SORTIE, des mois puis des années s'ajoutent : cinq puis six tuiles dans l'en-tête, qui
    # doivent tenir (chiffre et libellé) à 320 px.
    for utc, n_attendu in (("2026-12-24T08:30:00Z", 5), ("2028-02-02T17:45:00Z", 6)):
        ctx = nav.new_context(viewport={"width": 320, "height": 700}, timezone_id="Europe/Paris",
                              reduced_motion="reduce")
        ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));}catch(e){}")
        ctx.clock.install(time=utc)
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        page.wait_for_selector("#countdown")
        page.evaluate("updateCountdown()")
        r = page.evaluate("""() => { let dehors = null; const tuiles = [...document.querySelectorAll('#countdown .cd-t')];
            for(const t of tuiles){ for(const n of [t.querySelector('b'), t.querySelector('i')]){
              const g = document.createRange(); g.selectNodeContents(n);
              const q = g.getBoundingClientRect(), tq = t.getBoundingClientRect();
              if(q.right > tq.right + 0.5 || q.left < tq.left - 0.5) dehors = n.textContent + ' sort de sa tuile'; } }
            const c = document.querySelector('.tuile-compteur').getBoundingClientRect();
            const sortent = tuiles.filter(t => t.getBoundingClientRect().right > c.right + 0.5).length;
            return {n: tuiles.length, dehors: dehors, sortent: sortent, ligne: document.getElementById('cbCompteur').textContent}; }""")
        check(r["n"] == n_attendu and r["dehors"] is None and r["sortent"] == 0,
              "[320 px] %d tuiles dans l'en-tête après la sortie, rien ne sort (%s, %s ; ligne réduite « %s »)"
              % (n_attendu, r["dehors"], r["sortent"], r["ligne"]))
        ctx.close()

def test_compteurs_jumeaux(nav, url):
    """Le compteur de l'en-tête et celui de la fenêtre sont de VRAIS JUMEAUX (Antoni, 07/11/2026).

    « Le compteur principal et celui en haut de la carte doivent toujours être pareils. » Ce
    contrôle ouvre la fenêtre et compare, tuile par tuile, ce que l'œil voit : mêmes chiffres,
    même tuile grisée, même libellé, mêmes classes de palier, et un rendu identique (corps,
    graisse, couleur, halo, trait, fond, bord, rayon, libellé). Il le fait à plusieurs
    échéances (un palier chacune, le jour J, 111 jours après), dans les deux thèmes, à 320 et
    390 px, et encore une seconde plus tard : les deux se mettent à jour au MÊME instant.
    """
    print("\n[jumeaux] l'en-tête et la fenêtre du compteur sont identiques")
    LIT = """() => {
        const lire = g => [...g.querySelectorAll('.cd-t')].map(t => {
            const b = t.querySelector('b'), i = t.querySelector('i');
            const cb = getComputedStyle(b), ci = getComputedStyle(i), ct = getComputedStyle(t);
            return {txt: b.textContent, lib: i.textContent, zero: t.classList.contains('zero'), long: b.classList.contains('long'),
                    corps: cb.fontSize, graisse: cb.fontWeight, couleur: cb.color, halo: cb.textShadow,
                    trait: cb.webkitTextStrokeWidth + ' ' + cb.webkitTextStrokeColor,
                    fond: ct.backgroundColor, bord: ct.borderTopWidth + ' ' + ct.borderTopStyle + ' ' + ct.borderTopColor,
                    rayon: ct.borderTopLeftRadius, libCorps: ci.fontSize, libCouleur: ci.color, libCasse: ci.textTransform};
        });
        const h = document.getElementById('countdown'), p = document.querySelector('#compteurContenu .tuile-panneau .countdown');
        const sous = g => g.parentElement.querySelector('.compteur-sous').textContent;
        const nom = g => (g.getAttribute('aria-label') || '').replace(/ — ouvrir le détail$/, '');
        return {h: lire(h), p: lire(p), ch: h.className, cp: p.className, sh: sous(h), sp: sous(p), nh: nom(h), np: nom(p),
                ecart: Math.abs((h.querySelector('.cd-t').getBoundingClientRect().height) - (p.querySelector('.cd-t').getBoundingClientRect().height)),
                cadre: [getComputedStyle(h.parentElement).backgroundColor === getComputedStyle(p.parentElement).backgroundColor,
                        getComputedStyle(h.parentElement).borderTopLeftRadius === getComputedStyle(p.parentElement).borderTopLeftRadius]}; }"""
    for utc, nom in (("2026-09-10T13:34:21Z", "loin (palier ordinaire)"),
                     ("2026-10-28T10:00:07Z", "palier soon"),
                     ("2026-11-14T10:00:07Z", "palier urgent"),
                     ("2026-11-18T15:00:07Z", "palier final"),
                     ("2026-11-18T23:30:07Z", "le jour J, 00 h 30"),
                     ("2026-11-22T10:00:07Z", "3 jours après la sortie"),
                     ("2026-12-24T08:30:07Z", "1 mois après la sortie (5 tuiles)"),
                     ("2028-02-02T17:45:07Z", "1 an 2 mois après la sortie (6 tuiles)")):
        for largeur, schema in ((320, "dark"), (390, "light")):
            etiq = "[%s, %d px, %s]" % (nom, largeur, schema)
            ctx = nav.new_context(viewport={"width": largeur, "height": 800}, color_scheme=schema,
                                  timezone_id="Europe/Paris", reduced_motion="reduce")
            ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));}catch(e){}")
            ctx.clock.install(time=utc)
            page = ctx.new_page()
            erreurs = []
            page.on("pageerror", lambda e: erreurs.append(str(e)))
            page.goto(url, wait_until="load")
            page.wait_for_selector("#countdown .cd-t")
            page.click("#countdown")
            page.wait_for_selector("#compteurOverlay .tuile-panneau .cd-t", state="visible")
            page.wait_for_timeout(250)
            for tour in (0, 1):
                if tour:
                    page.clock.run_for(1000)
                    page.wait_for_timeout(250)
                # Les transitions de couleur doivent être FINIES : sur une CI plus lente, la fin de l'une
                # (rgb(25, 230, 255) contre rgb(26, 229, 254)) était lue comme un écart entre les deux compteurs.
                page.evaluate("() => Promise.all(document.getAnimations().filter(a => a.transitionProperty)"
                              ".map(a => a.finished.catch(() => {})))")
                v = page.evaluate(LIT)
                check(len(v["h"]) in (4, 5, 6) and v["h"] == v["p"],
                      "%s les %d tuiles sont identiques, chiffre par chiffre et style par style%s"
                      % (etiq, len(v["h"]), " (une seconde plus tard)" if tour else ""))
                if v["h"] != v["p"]:
                    for a, b in zip(v["h"], v["p"]):
                        if a != b:
                            print("      écart :", {k: (a[k], b[k]) for k in a if a[k] != b[k]})
                check(v["ch"] == v["cp"] and v["sh"] == v["sp"] and v["nh"] == v["np"],
                      "%s même palier (%s), même libellé (« %s »), même nom accessible"
                      % (etiq, v["ch"], v["sh"]))
                if not tour:
                    check(all(v["cadre"]) and v["ecart"] <= 1.5,
                          "%s même contenant, et tuiles de même hauteur (écart %.1f px)" % (etiq, v["ecart"]))
            check(not erreurs, "%s aucune erreur JavaScript" % etiq)
            ctx.close()


def test_entete_sans_vide(nav, url):
    """L'entête tient sur une ligne, et ce qui en est sorti reste accessible.

    Signalé par Antoni le 23/09/2026, capture à l'appui : un grand vide à
    gauche de l'entête. Cinq boutons de 34 px ne tenaient plus à côté du
    titre sous 390 px — et à sa largeur (≈ 368 px), le vide existait déjà
    avec quatre. Il a choisi de sortir le thème et les informations de
    l'entête ; le thème a son sélecteur dans ⚙️, les informations un bouton
    dans ⚙️ et un autre en bas du fil.

    Le piège verrouillé : applyTheme() écrivait l'icône dans #themeBtn. Le
    bouton retiré sans cette ligne, getElementById rendait null et le
    démarrage de l'app plantait dès le thème.
    """
    for largeur in (340, 360, 368, 390, 412):
        ctx = nav.new_context(viewport={"width": largeur, "height": 800})
        page = ctx.new_page()
        erreurs = []
        page.on("pageerror", lambda e, err=erreurs: err.append(str(e)))
        page.goto(url, wait_until="load")
        page.wait_for_selector(".header-haut")
        page.evaluate("""() => { const m = document.getElementById('modeIndicator');
                                 m.className = 'mode-indicator backend';
                                 m.innerHTML = '<span class="dot"></span><span class="mi-mot">Backend GitHub</span>'; }""")
        une_ligne = page.evaluate("""() => {
          const t = document.querySelector('.brand').getBoundingClientRect();
          const d = document.getElementById('refreshBtn').getBoundingClientRect();
          return d.top < t.bottom; }""")
        check(une_ligne, "[%d px] le titre et le bouton ↻ tiennent sur une ligne, sans le vide signalé" % largeur)
        deborde = page.evaluate("""() => { const c = document.querySelector('header.console').getBoundingClientRect();
          return [...document.querySelectorAll('header.console *')].filter(e => e.offsetParent !== null
            && e.getBoundingClientRect().right > c.right + 0.5).length; }""")
        check(deborde == 0, "[%d px] rien ne sort de la carte de l'entête, mode « Backend GitHub » affiché" % largeur)
        check(not erreurs, "[%d px] aucune erreur au démarrage — applyTheme() n'écrit plus "
              "dans un bouton disparu%s" % (largeur, "" if not erreurs else " : " + erreurs[0][:100]))
        ctx.close()

    ctx = nav.new_context(viewport={"width": 368, "height": 800})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector(".barre-bas")
    boutons = page.evaluate("""() => [...document.querySelectorAll('.barre-bas:not(#apercuCouleurs *) button')]
                                       .map(b => b.textContent.trim())""")
    check(boutons == ["Fil", "Stats", "Réglages"],
          "trois entrées dans la barre du bas : %s" % boutons)
    check(page.locator("header .icon-btn").count() == 1,
          "l'entête ne garde qu'un bouton, l'actualisation")
    check(page.locator("#themeBtn").count() == 0, "plus de bouton de thème dans l'entête")

    # Le thème reste réglable, depuis ⚙️.
    page.evaluate("() => openSettings()")
    page.click("#themeLightBtn")
    check(page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "light",
          "le sélecteur de ⚙️ passe bien en thème clair")
    page.click("#themeDarkBtn")
    check(page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "dark",
          "et en sombre")

    # Les informations aussi : le panneau se FERME avant, deux dialogues
    # empilés se disputeraient le focus et la touche Échap.
    page.click(".panneau-tabs button[data-cible=\"adv\"]")
    page.click("text=Informations et derniers passages du robot")
    page.wait_for_timeout(200)
    check(page.evaluate("() => document.getElementById('infoOverlay').classList.contains('open')"),
          "le bouton de ⚙️ ouvre les informations")
    check(not page.evaluate("() => document.getElementById('settingsOverlay').classList.contains('open')"),
          "et referme les paramètres au passage")
    page.evaluate("() => closeInfo()")
    check(page.locator(".info-btn").count() == 1,
          "le bouton ℹ️ en bas du fil est toujours là")
    ctx.close()


def test_heure_de_premiere_vue(nav, url):
    """L'heure où un article apparaît pour la première fois, et qu'elle tienne.

    seenMap était réécrit en entier à chaque actualisation : une heure
    ajoutée naïvement aurait été effacée au passage suivant. Et la poser sur
    les articles déjà connus leur aurait donné à tous l'heure du déploiement.
    """
    ctx = nav.new_context(viewport={"width": 390, "height": 800})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    r = page.evaluate("""() => {
      vusDepuis = null;
      seenMap = { "https://ex.test/ancien": {title: "Ancien", date: "2026-09-01T10:00:00Z"} };
      const lot = [
        {title: "Ancien", link: "https://ex.test/ancien", date: "2026-09-01T10:00:00Z"},
        {title: "Neuf", link: "https://ex.test/neuf", date: "2026-09-23T10:00:00Z"},
      ];
      const avant = Date.now();
      memoriseVus(lot);
      const premier = { ancien: seenMap["https://ex.test/ancien"].vu,
                        neuf: seenMap["https://ex.test/neuf"].vu,
                        depuis: vusDepuis, avant: avant,
                        stocke: localStorage.getItem(STORAGE_PREFIX + CLE_VUS_DEPUIS) };
      // Une seconde actualisation, plus tard : c'est elle qui effaçait tout.
      memoriseVus(lot);
      premier.neufApres = seenMap["https://ex.test/neuf"].vu;
      premier.ancienApres = seenMap["https://ex.test/ancien"].vu;
      return premier;
    }""")
    check(r["ancien"] is None,
          "un article déjà connu ne reçoit PAS d'heure : lui donner l'heure du "
          "déploiement ferait croire qu'on a tout vu au même instant")
    check(r["neuf"] is not None and r["neuf"] >= r["avant"],
          "un article nouveau reçoit l'heure où il apparaît")
    check(r["neufApres"] == r["neuf"],
          "et une seconde actualisation la CONSERVE — la réécriture de seenMap l'aurait effacée")
    check(r["ancienApres"] is None, "l'article ancien reste sans heure après coup aussi")
    check(r["depuis"] is not None and r["stocke"] == str(r["depuis"]),
          "le début de l'enregistrement est posé une fois, et gardé sur le téléphone")

    # La sauvegarde l'emporte et le restaure.
    paquet = page.evaluate("() => JSON.stringify(contenuSauvegarde())")
    import json as _json
    d = _json.loads(paquet)
    check(d.get("vus_depuis") == r["depuis"], "la sauvegarde emporte le début de l'enregistrement")
    check(d["vus"]["https://ex.test/neuf"].get("vu") == r["neuf"],
          "et l'heure de première vue de chaque article")
    ctx.close()


def test_reprise_des_liens_redecodes(nav, url):
    """Un lien corrigé côté robot ne doit pas remettre l'article en non lu.

    Du 03/10/2026 au 06/10, 453 articles sont entrés avec le lien du kiosque
    Google News. Le robot les a re-décodés — mais tout l'état local est rangé
    PAR LIEN. Sans reprise, les 453 redevenaient non lus ET « nouveaux » d'un
    coup, badge compris.
    """
    ctx = nav.new_context(viewport={"width": 390, "height": 800})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    r = page.evaluate("""() => {
      const KIOSQUE = "https://news.google.com/rss/articles/CBMiAAA";
      const VRAI = "https://ign.com/gta6-trailer";
      const AUTRE = "https://pcgamer.com/stable";

      // L'état d'AVANT la correction : l'article est lu, il est connu, et la
      // copie locale du fil le porte encore sous le lien du kiosque.
      readSet = new Set([KIOSQUE, AUTRE]);
      seenMap = { [KIOSQUE]: {title: "Trailer", date: "2026-10-04T10:00:00Z", vu: 1700000000000},
                  [AUTRE]: {title: "Stable", date: "2026-10-04T09:00:00Z", vu: 1700000000001} };
      lastItems = [{title: "Trailer", link: KIOSQUE, date: "2026-10-04T10:00:00Z",
                    favicon: "kiosque"},
                   {title: "Stable", link: AUTRE, date: "2026-10-04T09:00:00Z"}];
      firstLoad = false;

      // Ce que le robot publie APRÈS la réparation.
      const bruts = [{title: "Trailer", link: VRAI, source_link: KIOSQUE,
                      date: "2026-10-04T10:00:00Z"},
                     {title: "Stable", link: AUTRE, date: "2026-10-04T09:00:00Z"}];
      const bouges = migreLiensDecodes(bruts);

      const apres = {
        bouges: bouges,
        luVrai: readSet.has(VRAI), luKiosque: readSet.has(KIOSQUE),
        luAutre: readSet.has(AUTRE), tailleLus: readSet.size,
        vuVrai: seenMap[VRAI] ? seenMap[VRAI].vu : null,
        vuKiosque: !!seenMap[KIOSQUE],
        titreVu: seenMap[VRAI] ? seenMap[VRAI].title : null,
        liensLocaux: lastItems.map(i => i.link),
        faviconRefait: lastItems[0].favicon !== "kiosque",
        // La question qui compte : après reprise, l'article n'est plus
        // « nouveau ». C'est findNewItems qui décide du badge.
        nouveaux: findNewItems(bruts.map(i => ({...i}))).length,
        // Et le stockage a suivi : un rechargement ne doit pas tout défaire.
        lusStockes: JSON.parse(localStorage.getItem(STORAGE_PREFIX + "read-items-v1") || "[]"),
      };
      // Idempotence : rejouée sur la même réponse, elle ne doit rien casser.
      apres.rejoue = migreLiensDecodes(bruts);
      apres.luVraiApres = readSet.has(VRAI);
      apres.tailleApres = readSet.size;
      return apres;
    }""")

    check(r["luVrai"] and not r["luKiosque"],
          "l'article lu l'est toujours, suivi jusqu'à sa nouvelle adresse")
    check(r["luAutre"] and r["tailleLus"] == 2,
          "rien n'est créé ni perdu : la reprise DÉPLACE, elle n'ajoute pas")
    check(r["vuVrai"] == 1700000000000 and not r["vuKiosque"],
          "l'heure de première vue suit aussi — sinon la statistique de délai repartirait de zéro")
    check(r["titreVu"] == "Trailer", "et l'entrée déménage entière, pas seulement sa clé")
    check(r["nouveaux"] == 0,
          "l'article re-décodé n'est pas annoncé comme nouveau : c'est tout "
          "l'objet de la reprise, 453 badges d'un coup sinon")
    check(r["liensLocaux"][0] == "https://ign.com/gta6-trailer",
          "la copie locale du fil suit, sinon la fusion le mettrait À CÔTÉ de son ancienne version")
    check(r["faviconRefait"], "et son favicon est recalculé sur le nouveau domaine")
    check(sorted(r["lusStockes"]) == sorted(["https://ign.com/gta6-trailer",
                                             "https://pcgamer.com/stable"]),
          "le tout est écrit sur le téléphone, pas seulement en mémoire")
    check(r["rejoue"] == 0 and r["luVraiApres"] and r["tailleApres"] == 2,
          "rejouée, la reprise ne fait plus rien : l'ancienne clé n'existe plus")

    # Un article jamais lu ne doit pas DEVENIR lu au passage.
    r2 = page.evaluate("""() => {
      readSet = new Set(); seenMap = {}; lastItems = [];
      migreLiensDecodes([{title: "X", link: "https://a.test/x",
                          source_link: "https://news.google.com/rss/articles/X"}]);
      return { lus: readSet.size, vus: Object.keys(seenMap).length };
    }""")
    check(r2["lus"] == 0 and r2["vus"] == 0,
          "sur un état vide elle ne crée rien — elle ne marque jamais lu de sa propre initiative")
    ctx.close()


def test_bloc_incidents(nav, url):
    """Le bloc qui s'affiche MÊME VIDE, et c'est tout son intérêt.

    Le 05/10/2026 au soir j'ai annoncé qu'une source rendait un flux vide
    « depuis deux passages » et qu'une alerte partirait. Les deux étaient
    faux : la panne datait de trois jours, avait duré deux heures, et
    s'était réparée seule. Pour le savoir il avait fallu rejouer 120
    versions de feed.json.
    """
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    socle = """([incidents, compte]) => {
      derniereReponseBackend = {
        generated_at: "2026-10-06T20:00:00Z",
        sources_health: [{id: "a", name: "Alpha", status: "ok",
                          http_status: 200, entries_fetched: 25,
                          days_since_last_article: 0}],
        sources_incidents: incidents,
        sources_incidents_compte: compte,
      };
      setTab("stats"); ongletStatsChoisi("sources");
    }"""

    # --- le cas le plus fréquent, et le plus utile : rien à signaler ----
    page.evaluate(socle, [[], {}])
    page.wait_for_selector(".stats-bloc")
    vide = page.locator(".stats-bloc").filter(has_text="Incidents des 30 derniers jours")
    check(vide.count() == 1,
          "le bloc existe même sans incident — « aucun » est une information, "
          "pas un vide à masquer")
    check("Aucun incident de plus de trois heures depuis 30 jours" in vide.inner_text(),
          "et il le dit en toutes lettres, SEUIL COMPRIS : sans le seuil, la "
          "phrase promettrait zéro coupure là où il y en a 449 par mois "
          "(« %s »)" % vide.inner_text().replace("\n", " ")[:90])
    check(page.locator(".stats-bloc").filter(has_text="Coupures par source").count() == 0,
          "et sans compteur, pas de bloc de compteur")

    # --- avec des incidents --------------------------------------------
    #
    # Les deux vraies entrées du fil au 07/10/2026 : la plus courte que le
    # filtre laisse passer, et la panne du décodeur — l'incident le plus
    # grave du projet, celui que le journal avait manqué parce qu'il s'était
    # refermé quinze heures avant que le journal n'existe.
    page.evaluate(socle, [[
        {"source": "reddit-leaks", "nom": "Reddit — fuites et rumeurs",
         "debut": "2026-10-02T00:02:00Z", "fin": "2026-10-02T04:02:00Z",
         "heures": 4.0, "alertee": False},
        {"source": "__decodage__", "nom": "Décodage Google News",
         "debut": "2026-10-03T16:01:34Z", "fin": "2026-10-06T05:36:17Z",
         "heures": 61.6, "alertee": True},
    ], {}])
    page.wait_for_selector(".stats-bloc")
    bloc = page.locator(".stats-bloc").filter(has_text="Incidents des 30 derniers jours")
    titre = bloc.locator("h4, .stats-titre, strong").first.inner_text() if bloc.locator("h4, .stats-titre, strong").count() else bloc.inner_text()
    check("(2)" in titre or "(2)" in bloc.inner_text(),
          "le nombre figure au titre")
    lignes = bloc.locator("li").all_inner_texts()
    check(len(lignes) == 2, "une ligne par incident (%d)" % len(lignes))
    check("Reddit" in lignes[0] and "4 h" in lignes[0] and "résolu seul" in lignes[0],
          "la panne courte est marquée « résolu seul » — c'est CE détail que "
          "je n'avais pas su dire (« %s »)" % lignes[0].replace("\n", " "))
    check("Décodage Google News" in lignes[1] and "alerte" in lignes[1],
          "le décodage y figure comme une source, et son alerte est signalée "
          "(« %s »)" % lignes[1].replace("\n", " "))
    check("3 j" in lignes[1],
          "une panne de 62 h se lit en jours, pas en heures (« %s »)"
          % lignes[1].replace("\n", " "))

    # --- LE COMPTEUR, qui dit ce que la liste filtrée ne dit plus -------
    #
    # Sans lui, une source qui tombe onze fois par jour sans jamais passer
    # trois heures n'apparaîtrait nulle part. C'est exactement le cas des
    # deux flux YouTube, qui sont les deux premiers du classement réel.
    page.evaluate(socle, [[], {
        "2026-09": {"vg247": {"nom": "VG247", "n": 2, "heures": 3.0}},
        "2026-10": {
            "rockstar-youtube": {"nom": "Rockstar Games (YouTube)", "n": 6, "heures": 38.0},
            "jvc": {"nom": "Jeuxvideo.com", "n": 1, "heures": 0.5},
        },
    }])
    page.wait_for_selector(".stats-bloc")
    cpt = page.locator(".stats-bloc").filter(has_text="Coupures par source")
    check(cpt.count() == 1, "le bloc paraît dès qu'il y a de quoi compter")
    lignes = cpt.locator("li").all_inner_texts()
    check(len(lignes) == 2,
          "il ne montre QUE le mois le plus récent — deux mois empilés "
          "diraient une tendance que personne n'a demandée (%d lignes)"
          % len(lignes))
    check("Rockstar Games (YouTube)" in lignes[0] and "6 coupures" in lignes[0],
          "la source la plus coupée d'abord, avec son nombre (« %s »)"
          % lignes[0].replace("\n", " "))
    check("38 h" in lignes[0] or "2 j" in lignes[0],
          "et sa durée cumulée (« %s »)" % lignes[0].replace("\n", " "))
    check("Jeuxvideo.com" in lignes[1] and "1 coupure" in lignes[1]
          and "coupures" not in lignes[1],
          "une seule coupure se dit au singulier (« %s »)"
          % lignes[1].replace("\n", " "))
    check("au total" not in lignes[1],
          "et une demi-heure cumulée ne s'affiche pas : « 30 min au total » "
          "sur un mois entier n'apprend rien (« %s »)"
          % lignes[1].replace("\n", " "))

    # --- le piège du sélecteur -----------------------------------------
    check(page.locator(".stats-bloc").filter(has_text="État des sources").count() == 1,
          "le nouveau bloc ne reprend pas la phrase « État des sources » : "
          "deux blocs la portant casseraient le sélecteur des autres contrôles")
    ctx.close()


def test_stats_quatre_rubriques(nav, url):
    """Chaque statistique ajoutée le 23/09/2026, sur un fil dont on connaît la réponse."""
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    M = "2026-10-20T10:00:00Z"   # 20/10, midi à Paris

    # ---- 1. Avec la date de couverture : tous les jours depuis, plafonnés ----
    s = page.evaluate("""(m) => {
      const items = [];
      for(let j = 1; j <= 19; j++){
        items.push({title: "a", link: "l" + j, source: "S",
                    date: "2026-10-" + String(j).padStart(2, "0") + "T10:00:00Z"});
      }
      return calculeStats(items, {couverture_depuis: "2026-10-08"}, new Date(m), {});
    }""", M)
    jours = [j["jour"] for j in s["jours"]]
    check(jours[0] == "2026-10-08" and jours[-1] == "2026-10-19",
          "avec la couverture publiée, les jours vont du 08 (premier jour complet) "
          "à la veille : %s → %s" % (jours[0], jours[-1]))
    check(s["couverture"] == "2026-10-08", "et la couverture est restituée telle quelle")
    s_long = page.evaluate("""(m) => calculeStats([{title: "a", link: "x", date: "2026-10-19T10:00:00Z"}],
        {couverture_depuis: "2026-01-01"}, new Date(m), {})""", M)
    check(len(s_long["jours"]) == 30,
          "une couverture ancienne ne donne jamais plus de 30 jours : l'histogramme reste lisible")

    # ---- 2. Les mois : entièrement couverts, et archive chargée ----
    fil_mois = """([m, arch]) => {
      const items = [];
      const d = new Date("2026-08-01T12:00:00Z");
      while(d < new Date("2026-12-20T00:00:00Z")){
        items.push({title: "x", link: "x" + d.getTime(), date: d.toISOString()});
        d.setTime(d.getTime() + 86400000);
      }
      return calculeStats(items, {couverture_depuis: "2026-09-08"}, new Date(m), {archiveChargee: arch});
    }"""
    avec = page.evaluate(fil_mois, ["2026-12-20T10:00:00Z", True])
    check([x["mois"] for x in avec["mois"]] == ["2026-10", "2026-11"],
          "octobre et novembre, entiers, apparaissent — septembre (couvert depuis le 08) "
          "et décembre (en cours) non : %s" % [x["mois"] for x in avec["mois"]])
    sans = page.evaluate(fil_mois, ["2026-12-20T10:00:00Z", False])
    check(sans["mois"] == [],
          "sans l'archive chargée, aucun mois : un mois plus vieux que la fenêtre n'est entier qu'avec elle")

    # ---- 3. Heures, jours de la semaine, langues ----
    s3 = page.evaluate("""(m) => calculeStats([
        {title: "a", link: "1", date: "2026-10-19T12:30:00Z", lang: "fr"},
        {title: "b", link: "2", date: "2026-10-19T12:45:00Z", lang: "en"},
        {title: "c", link: "3", date: "2026-10-18T21:30:00Z"},
      ], {couverture_depuis: "2026-10-18"}, new Date(m), {})""", M)
    check(s3["heures"][14] == 2,
          "12 h 30 UTC en octobre, c'est 14 h à Paris : deux articles comptés à 14 h (%s)" % s3["heures"][14])
    check(s3["heures"][23] == 1, "21 h 30 UTC, c'est 23 h à Paris")
    check(s3["semaine"][0] == 2 and s3["semaine"][6] == 1,
          "le 19/10/2026 est un lundi (indice 0), le 18 un dimanche (indice 6)")
    check(s3["langues"] == {"fr": 1, "en": 1, "inconnue": 1},
          "français, anglais, et la langue inconnue comptée à part plutôt que devinée : %s" % s3["langues"])

    # ---- 4. Le vrai média, et les mots ----
    medias = page.evaluate("""() => [
        mediaDe({title: "Un sujet - IGN", source: "Google News (EN)"}),
        mediaDe({title: "Un sujet - IGN", source: "GameSpot"}),
        mediaDe({title: "Sujet - and it's finally time to book that holiday", source: "Google News (EN)"}),
        mediaDe({title: "Pas de tiret", source: "Google News (FR)"}),
        mediaDe({title: "Pas de tiret", source: "Google News (FR)",
                 link: "https://www.theshortcut.com/a/b"}),
        domaineDe("https://www.numerama.com/x"),
        domaineDe("pas une url"),
      ]""")
    check(medias[0] == "IGN", "un titre Google News rend son vrai média : %s" % medias[0])
    check(medias[1] == "GameSpot",
          "un flux natif garde sa source — la queue de titre y serait un sous-titre")
    check(medias[2] == "Google News (EN)", "une queue trop bavarde n'est pas un média")
    check(medias[3] == "Google News (FR)",
          "sans tiret NI lien exploitable, la source sert de dernier recours")
    # LE REPLI MESURÉ : la queue de titre donne le média 260 fois sur 262
    # (16/10/2026). Pour les deux autres, le domaine du lien décodé reste
    # exact — sans lui, deux cartes sur trois cents diraient « Google News ».
    check(medias[4] == "theshortcut.com",
          "sans tiret mais AVEC un lien, le domaine prend le relais : %s" % medias[4])
    check(medias[5] == "numerama.com", "le domaine est rendu sans « www. »")
    check(medias[6] == "", "une URL illisible ne rend pas de domaine, elle rend vide")

    # UNE URL EN GUISE DE NOM DE MÉDIA. Vu sur une capture le 24/10/2026 :
    # « HTTPS://TECH-INSIDER.ORG/ » en tête de carte. 22 articles sur 2 970
    # ont un titre qui finit par « - https://tech-insider.org/ » — l'éditeur
    # a mis son adresse là où Google News attend un nom.
    url = page.evaluate("""() => {
        const a = {title: "GTA VI Survey: 10% Plan to Skip Work Launch Day [2026] - https://tech-insider.org/",
                   source: "Google News (EN)"};
        const b = {title: "Un sujet - https://ascendants.in/", source: "Google News (EN)"};
        const c = {title: "Un sujet - IGN", source: "Google News (EN)"};
        return {
          nom: mediaDe(a), nomB: mediaDe(b),
          // Le titre affiché ne doit PAS garder l'URL : mediaDe rend un
          // domaine, et le titre se termine par l'URL entière.
          titre: titreSansMedia(a), titreB: titreSansMedia(b),
          // Et le cas ordinaire n'a pas bougé.
          nomC: mediaDe(c), titreC: titreSansMedia(c),
          corps: corpsDuTitre(a),
          // L'URL ne doit pas non plus compter comme un mot du titre pour
          // les badges : « tech-insider.org » ne parle pas de « insider ».
          sansFaux: estPC({title: "Un titre neutre - https://pc-news.example/"}),
        };
    }""")
    check(url["nom"] == "tech-insider.org" and url["nomB"] == "ascendants.in",
          "une URL en guise de média rend son DOMAINE, plus l'adresse brute "
          "en majuscules (« %s », « %s »)" % (url["nom"], url["nomB"]))
    check(url["titre"] == "GTA VI Survey: 10% Plan to Skip Work Launch Day [2026]"
          and url["titreB"] == "Un sujet",
          "et le titre affiché ne traîne pas l'URL — la queue exacte est "
          "retirée, pas le nom affiché (« %s »)" % url["titre"])
    check(url["nomC"] == "IGN" and url["titreC"] == "Un sujet",
          "un média ordinaire n'a pas bougé (« %s » / « %s »)"
          % (url["nomC"], url["titreC"]))
    check("tech-insider" not in url["corps"],
          "le corps du titre, qui sert aux badges, perd l'URL aussi (« %s »)"
          % url["corps"][-40:])
    check(not url["sansFaux"],
          "un nom de domaine en queue ne déclenche pas un badge (PC)")

    s4 = page.evaluate("""(m) => calculeStats([
        {title: "GTA 6 : la bande-annonce dévoilée, la bande-annonce ! - IGN", link: "1", source: "Google News (FR)", date: "2026-10-19T10:00:00Z"},
        {title: "Grand Theft Auto VI trailer devoile - GameSpot", link: "2", source: "Google News (EN)", date: "2026-10-19T11:00:00Z"},
      ], {couverture_depuis: "2026-10-19"}, new Date(m), {})""", M)
    mots = {x["nom"]: x["n"] for x in s4["mots"]}
    check(mots.get("devoilee") == 1 and mots.get("devoile") == 1,
          "les mots sont comparés sans accents (%s)" % mots)
    check(mots.get("bande") == 1,
          "un mot répété dans UN titre compte une seule fois : on compte des articles, pas des occurrences")
    check(not any(m in mots for m in ("gta", "grand", "theft", "auto", "ign", "gamespot")),
          "ni « GTA », ni le nom du média en fin de titre ne comptent comme sujet")

    # ---- 5. Les mots qui montent : 14 jours exigés ----
    montent = page.evaluate("""(m) => {
      const items = [];
      for(let j = 6; j <= 19; j++){
        const jour = "2026-10-" + String(j).padStart(2, "0");
        const n = j >= 13 ? 3 : 0;              // « leonida » n'apparaît que la dernière semaine
        for(let k = 0; k < n; k++) items.push({title: "Leonida " + k, link: jour + k, date: jour + "T10:00:00Z"});
        items.push({title: "Constant", link: "c" + jour, date: jour + "T10:00:00Z"});
      }
      return calculeStats(items, {couverture_depuis: "2026-10-06"}, new Date(m), {}).montent;
    }""", M)
    check(montent and montent[0]["nom"] == "leonida" and montent[0]["avant"] == 0,
          "un mot absent la semaine d'avant et présent cette semaine monte (%s)" % montent[:2])
    check(all(x["nom"] != "constant" for x in montent), "un mot stable ne monte pas")
    court = page.evaluate("""(m) => calculeStats([{title: "Leonida", link: "1", date: "2026-10-19T10:00:00Z"}],
        {couverture_depuis: "2026-10-12"}, new Date(m), {}).montent""", M)
    check(court == [], "sur moins de 14 jours, aucune « montée » : la comparaison serait inégale")

    # ---- 6. Officiels par année : axe continu ----
    s6 = page.evaluate("""(m) => calculeStats([
        {title: "o", link: "1", date: "2023-12-05T10:00:00Z", official: true},
        {title: "o", link: "2", date: "2025-05-06T10:00:00Z", official: true},
        {title: "o", link: "3", date: "2025-06-06T10:00:00Z", official: true},
      ], null, new Date(m), {}).officielsAnnees""", M)
    check(s6 == [{"an": "2023", "n": 1}, {"an": "2024", "n": 0}, {"an": "2025", "n": 2}],
          "une année sans publication officielle apparaît à zéro plutôt que d'être sautée : %s" % s6)

    # ---- 7. Sources et robot ----
    s7 = page.evaluate("""(m) => calculeStats([], {
        generated_at: "2026-10-20T09:40:00Z", duration_seconds: 42, new_this_run: 3,
        total_articles: 1800, decode_failures: 0, attente_recap: {articles: 0},
        sources_health: [
          {id: "a", name: "Alpha", status: "ok", http_status: 200, entries_fetched: 25, days_since_last_article: 0},
          {id: "b", name: "Bravo", status: "ok", http_status: null, not_modified: true, entries_fetched: 0, days_since_last_article: 12},
          {id: "d", name: "Delta", status: "cassee", http_status: null, entries_fetched: 0, days_since_last_article: 3},
          {id: "c", name: "Charlie", status: "tarie", http_status: 200, entries_fetched: 10, days_since_last_article: null},
          {id: "r", name: "Reddit", status: "en_attente", http_status: null, entries_fetched: 0},
        ]}, new Date(m), {archiveIndex: {mois: [{mois: "2026-09", articles: 1500}, {mois: "2026-10", articles: 900}]}})""", M)
    codes = {c["code"]: c["n"] for c in s7["sources"]["codes"]}
    check(codes == {"200": 2, "304": 1, "sans réponse": 1},
          "les codes HTTP sont comptés, et la source au repos n'y figure pas — pas interrogée, pas de code : %s" % codes)
    check(codes.get("304") == 1,
          "un flux inchangé compte en 304 : le robot l'écrit not_modified, sans http_status — "
          "le 23/09/2026, 17 sources passaient ainsi pour muettes")
    check([v["nom"] for v in s7["sources"]["volume"]] == ["Alpha", "Charlie"],
          "le volume rapporté classe les sources, sans celles à zéro")
    check([(x["nom"], x["jours"]) for x in s7["sources"]["silencieuses"]] == [("Charlie", None), ("Bravo", 12)],
          "les plus silencieuses : « jamais » d'abord, puis la plus ancienne ; une source du jour n'y est pas")
    r = s7["robot"]
    check(r["ilYaMin"] == 20, "le dernier passage date d'il y a 20 minutes (%s)" % r["ilYaMin"])
    check(r["archiveArticles"] == 2400 and r["archiveMois"] == 2, "et l'archive est résumée : 2 400 articles sur 2 mois")

    # ---- 8. Ma lecture, et le délai qui ne ment pas ----
    s8 = page.evaluate("""(m) => {
      const depuis = new Date("2026-10-10T00:00:00Z").getTime();
      const items = [], vus = {};
      for(let k = 0; k < 25; k++){
        const pub = new Date("2026-10-15T10:00:00Z").getTime() + k * 3600000;
        items.push({title: "n" + k, link: "n" + k, date: new Date(pub).toISOString(), source: "S"});
        vus["n" + k] = {vu: pub + 30 * 60000};          // vu 30 minutes après
      }
      // Publié AVANT l'enregistrement, découvert tard : ne doit pas compter.
      items.push({title: "vieux", link: "v", date: "2026-10-01T10:00:00Z", source: "S"});
      vus["v"] = {vu: new Date("2026-10-18T10:00:00Z").getTime()};
      items.push({title: "officiel", link: "o", date: "2026-10-16T10:00:00Z", official: true, source: "R"});
      return calculeStats(items, {couverture_depuis: "2026-10-01"}, new Date(m),
        {lus: new Set(["n0", "n1"]), vus, vusDepuis: depuis}).moi;
    }""", M)
    check(s8["delaisNotes"] == 25,
          "25 délais notés : l'article publié avant l'enregistrement est exclu, il a été découvert tard, pas vu en retard (%s)" % s8["delaisNotes"])
    check(s8["delaiMedianMin"] == 30, "délai médian : 30 minutes (%s)" % s8["delaiMedianMin"])
    check(s8["lus"] == 2 and s8["officielsNonLus"] == 1, "deux lus, un officiel non lu")
    peu = page.evaluate("""(m) => calculeStats([{title: "a", link: "a", date: "2026-10-15T10:00:00Z"}],
        null, new Date(m), {vus: {a: {vu: new Date("2026-10-15T11:00:00Z").getTime()}},
        vusDepuis: new Date("2026-10-01").getTime()}).moi.delaiMedianMin""", M)
    check(peu is None, "sous 20 articles notés, aucun délai médian annoncé : un seul chiffre n'est pas une tendance")

    # ---- 9. Le rendu des quatre rubriques ----
    page.evaluate("""() => {
      historyPartial = false; rattrapageLance = true; archiveChargee = true;
      lastItems = [{title: "Un sujet - IGN", link: "https://ex.test/1", source: "Google News (EN)",
                    date: new Date(Date.now() - 86400000).toISOString(), lang: "en"}];
      derniereReponseBackend = {generated_at: new Date().toISOString(), duration_seconds: 40,
        sources_health: [{id: "a", name: "Alpha", status: "ok", http_status: 200, entries_fetched: 25}]};
      setTab('stats');
    }""")
    for o, attendu in (("sources", "Réponses au dernier passage"), ("robot", "Dernier passage"),
                       ("moi", "Entre la publication et ton écran")):
        page.evaluate("(o) => ongletStatsChoisi(o)", o)
        page.wait_for_timeout(80)
        check(attendu in page.inner_text("#statsContenu"),
              "la rubrique « %s » affiche « %s »" % (o, attendu))
        check(page.get_attribute("#statsOnglet_" + o, "aria-pressed") == "true",
              "et son sous-onglet l'annonce")
    ctx.close()

def test_officiel_et_filtres_etroits(nav, url):
    """La règle « officiel » de l'app, et les trois filtres sur petit écran (23/09/2026)."""
    ctx = nav.new_context(viewport={"width": 390, "height": 800})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    r = page.evaluate("""() => ({
      www: estOfficiel("https://www.rockstargames.com/newswire/article/1/x", null),
      support: estOfficiel("https://support.rockstargames.com/VI", null),
      nu: estOfficiel("https://rockstargames.com/VI", null),
      social: estOfficiel("https://socialclub.rockstargames.com/job/gtav/abc", {official: true}),
      faux1: estOfficiel("https://faux-rockstargames.com/gta6", null),
      faux2: estOfficiel("https://rockstargames.com.arnaque.net/gta6", null),
      faux3: estOfficiel("https://rockstargames.com@arnaque.net/gta6", null),
      yt: estOfficiel("https://www.youtube.com/watch?v=abc",
                      {official: true, officialDomains: ["youtube.com", "youtu.be"]}),
      rmag: estRockstarmag("https://www.rockstarmag.fr/a", null),
      pasRmag: estRockstarmag("https://pasrockstarmag.fr/a", null),
    })""")
    check(r["www"] and r["support"] and r["nu"],
          "l'app tient pour officiels le domaine de Rockstar et ses vrais sous-domaines")
    check(not r["social"],
          "mais pas Social Club, même depuis une source officielle — alignée sur le robot")
    check(not (r["faux1"] or r["faux2"] or r["faux3"]),
          "ni un domaine qui ne fait que CONTENIR rockstargames.com (`includes` les acceptait)")
    check(r["yt"], "la chaîne YouTube de Rockstar garde son statut par ses domaines propres")
    check(r["rmag"] and not r["pasRmag"], "même règle stricte pour Rockstar Mag")

    # LE MOT QUI COMPTE, en mode de secours (4.1 de l'audit, corrigé le
    # 14/10/2026). Le robot ajoute « trailer » aux mots-clés officiels de la
    # seule chaîne YouTube de Rockstar : une vidéo intitulée simplement
    # « Trailer 3 » ne contient aucun des six mots-clés GTA 6 et serait
    # rejetée — le jour qui compte. Le mode de secours n'avait pas ce
    # supplément, et c'est LUI qui tourne quand le backend est injoignable.
    yt = {"official": True, "officialDomains": ["youtube.com", "youtu.be"],
          "officialKeywordsExtra": ["trailer"]}
    autre = {"official": True}
    films = [{"title": "Trailer 3"}, {"title": "Grand Theft Auto VI Trailer 3"},
             {"title": "NBA 2K27 Launch Trailer"}, {"title": "Une annonce sans rapport"}]
    passe = page.evaluate("""([yt, autre, films]) => ({
        chaine: filterFeedItems(films, yt).map(i => i.title),
        ailleurs: filterFeedItems(films, autre).map(i => i.title),
    })""", [yt, autre, films])
    check("Trailer 3" in passe["chaine"],
          "[secours] « Trailer 3 », sans le moindre « GTA », passe le filtre "
          "officiel sur la chaîne qui déclare le supplément (%s)"
          % passe["chaine"])
    check("Trailer 3" not in passe["ailleurs"]
          and "NBA 2K27 Launch Trailer" not in passe["ailleurs"],
          "[secours] mais le supplément NE FUIT PAS sur les autres sources "
          "officielles — sinon Take-Two laisse entrer les trailers de NBA 2K "
          "(%s)" % passe["ailleurs"])
    check("Grand Theft Auto VI Trailer 3" in passe["ailleurs"],
          "[secours] et les six mots-clés de base continuent de s'appliquer "
          "partout (%s)" % passe["ailleurs"])
    # Le supplément est un SUPPLÉMENT : la source ne redéfinit pas la liste.
    cles = page.evaluate("""([yt]) => ({
        avec: motsClesOfficiels(yt), sans: motsClesOfficiels({}),
        nul: motsClesOfficiels(null),
    })""", [yt])
    check(all(k in cles["avec"] for k in cles["sans"]) and "trailer" in cles["avec"],
          "[secours] le supplément s'AJOUTE aux six mots-clés, il ne les "
          "remplace pas (%d contre %d)" % (len(cles["avec"]), len(cles["sans"])))
    check(cles["nul"] == cles["sans"],
          "[secours] et une source sans réglage particulier garde la liste "
          "de base")
    ctx.close()

    # Les trois filtres, avec les compteurs qu'ils portent en vrai. La boîte
    # du TEXTE est comparée à la zone intérieure du bouton : le contenu est
    # centré, il déborde donc des deux côtés, et scrollWidth ne voit pas le
    # débordement à gauche — une première version de ce contrôle passait
    # alors que « RockstarMag » touchait le bord à 390 px.
    for largeur in (320, 360, 390, 412, 430, 479, 480, 600):
        ctx = nav.new_context(viewport={"width": largeur, "height": 800})
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        # Repliées depuis le 11/11/2026 : on déplie, et on exige une largeur réelle, sans quoi la mesure
        # d'un bouton caché (0 px) passerait « sans débordement » pour une mauvaise raison.
        page.wait_for_selector("#tabRockstarmag", state="attached")
        page.click("#sourcesBtn")
        page.wait_for_selector("#tabRockstarmag", state="visible")
        mesures = page.evaluate("""() => {
          document.getElementById("badgeNonRockstar").textContent = "1758";
          document.getElementById("badgeRockstar").textContent = "45";
          document.getElementById("badgeRockstarmag").textContent = "83";
          return ["tabNonRockstar", "tabRockstar", "tabRockstarmag"].map(id => {
            const e = document.getElementById(id), b = e.getBoundingClientRect();
            const cs = getComputedStyle(e);
            const r = document.createRange(); r.selectNodeContents(e);
            const t = r.getBoundingClientRect();
            const gauche = b.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
            const droite = b.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight);
            return [id, Math.max(gauche - t.left, t.right - droite)];
          });
        }""")
        debordent = ["%s (+%.1f px)" % (m[0], m[1]) for m in mesures if m[1] > 0.5]
        check(not debordent,
              "à %d px, le texte des trois filtres reste dans son bouton, marge comprise (%s)"
              % (largeur, ", ".join(debordent) or "aucun débordement"))

        # LA RANGÉE DU HAUT, depuis qu'elle porte deux boutons (09/10/2026).
        #
        # Deux mesures et non une, parce que le défaut trouvé en la livrant
        # n'était PAS un débordement de texte : `.tab` est en
        # `white-space:nowrap` et `flex-shrink:0`, donc le texte tenait
        # parfaitement dans des boutons dont la somme dépassait l'écran. La
        # rangée devenait scrollable, et « Actu majeure » était coupé de
        # 50 px à 320 px — invisible tant qu'on ne balayait pas. Un onglet
        # qu'il faut deviner est un onglet mort.
        haut = page.evaluate("""() => {
          document.getElementById("badgeMajeur").textContent = "50";
          const row = document.querySelectorAll('.tab-row')[0];
          const dedans = ["tabAll", "tabMajeur"].map(id => {
            const e = document.getElementById(id), b = e.getBoundingClientRect();
            const cs = getComputedStyle(e);
            const r = document.createRange(); r.selectNodeContents(e);
            const t = r.getBoundingClientRect();
            const g = b.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
            const d = b.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight);
            return [id, Math.max(g - t.left, t.right - d)];
          });
          return {dedans, coupe: row.scrollWidth - row.clientWidth};
        }""")
        trop = ["%s (+%.1f px)" % (m[0], m[1]) for m in haut["dedans"] if m[1] > 0.5]
        check(not trop,
              "à %d px, le texte des deux vues reste dans son bouton (%s)"
              % (largeur, ", ".join(trop) or "aucun débordement"))
        check(haut["coupe"] <= 0,
              "et la rangée du haut ENTIÈRE tient dans l'écran à %d px : sans ça "
              "« Actu majeure » est coupé et personne ne le trouve (%+d px)"
              % (largeur, haut["coupe"]))
        ctx.close()


def test_badge_actu_majeure_lisible(nav, url):
    """Le badge « ACTU MAJEURE » se lit, dans les deux thèmes, TOUT LE TEMPS.

    Il était à 1,92:1 en thème clair (#ff8c3d sur un orange à 18 %). Le
    balayage de contraste ne l'a vu que le 27/10/2026, quand le vrai fil a
    eu pour la première fois un article à trois sources dans ce qu'il rend —
    et ce jour-là la CI de TOUTES les pull requests est passée au rouge,
    Dependabot compris.

    Un défaut qui n'apparaît que selon le contenu du fil ne se garde pas
    avec un balayage du fil. Ce contrôle fabrique lui-même l'article.
    """
    print("\n[badge] ACTU MAJEURE reste lisible, quel que soit le fil")
    ctx = nav.new_context(viewport={"width": 390, "height": 900},
                          timezone_id="Europe/Paris", reduced_motion="reduce")
    page = ctx.new_page()
    err = []
    page.on("pageerror", lambda e: err.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    for theme in ("light", "dark"):
        m = page.evaluate("""(theme) => {
            document.documentElement.setAttribute('data-theme', theme);
            lastItems = [{
              link: 'https://h.test/1', source: 'T', lang: 'fr',
              date: new Date().toISOString(), title: 'GTA 6 : un sujet repris',
              // deux reprises + l'article = trois rédactions = le seuil
              extraSources: [{source: 'A', link: 'https://a.test/1'},
                             {source: 'B', link: 'https://b.test/1'}],
            }];
            settings.spoilerMode = false;
            visibleCount = 100; settings.maxDisplay = 100; badgeActif = null;
            currentTab = 'all'; currentFilter = 'all'; currentLang = 'all';
            applyFilters();
            const badge = document.querySelector('.card .tag-hot');
            if(!badge) return {absent: true};
            const carte = badge.closest('.card');

            // LE FOND EST TEINTÉ ET TRANSLUCIDE : color-mix(... 18 %,
            // transparent). On le COMPOSITE sur la carte en le peignant
            // réellement sur un canevas, plutôt que d'écrire un second
            // analyseur de couleurs — Chromium rend color-mix() en
            // color(srgb 0.07 0.43 ...) et c'est ce qui a déjà faussé un
            // contrôle ici.
            const cv = document.createElement('canvas');
            cv.width = cv.height = 1;
            const g = cv.getContext('2d', {willReadFrequently: true});
            const lire = () => [...g.getImageData(0, 0, 1, 1).data].slice(0, 3);
            g.fillStyle = getComputedStyle(carte).backgroundColor;
            g.fillRect(0, 0, 1, 1);
            g.fillStyle = getComputedStyle(badge).backgroundColor;
            g.fillRect(0, 0, 1, 1);
            const fond = lire();
            g.clearRect(0, 0, 1, 1);
            g.fillStyle = getComputedStyle(badge).color;
            g.fillRect(0, 0, 1, 1);
            const texte = lire();

            const lin = (c) => { c /= 255;
              return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
            const Y = (p) => 0.2126 * lin(p[0]) + 0.7152 * lin(p[1]) + 0.0722 * lin(p[2]);
            const a = Y(fond), b = Y(texte);
            return {absent: false, fond: fond, texte: texte,
                    ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
                    libelle: badge.textContent.trim()};
        }""", theme)
        check(not m.get("absent"),
              "[badge %s] un article à trois rédactions porte bien son badge "
              "(sinon ce contrôle ne mesure rien)" % theme)
        if m.get("absent"):
            continue
        check("3 SOURCES" in m["libelle"],
              "[badge %s] il annonce trois sources (« %s »)" % (theme, m["libelle"]))
        # L'INSTRUMENT D'ABORD. En thème clair le fond composité doit
        # retrouver ce que le balayage avait mesuré (255, 228, 219) ; si le
        # canevas ne comprenait pas la couleur, il rendrait du noir ou du
        # blanc et le rapport ci-dessous serait faux dans un sens ou
        # l'autre sans que rien ne le dise.
        if theme == "light":
            ecart = max(abs(x - y) for x, y in zip(m["fond"], (255, 228, 219)))
            check(ecart <= 2,
                  "[badge light] l'instrument est juste : le fond composité "
                  "retrouve (255, 228, 219) mesuré par le balayage (%s)" % m["fond"])
        check(m["ratio"] >= 4.5,
              "[badge %s] le texte se lit : %.2f:1 sur %s (exigé 4,5)"
              % (theme, m["ratio"], tuple(m["fond"])))
    check(not err, "[badge] aucune erreur JavaScript (%s)" % err[:1])
    ctx.close()


def test_onglet_actu_majeure(nav, url):
    """L'onglet livré le 09/10/2026, à la demande d'Antoni.

    Ce n'est PAS un filtre du jour. Mesuré sur le fil réel avant de le
    construire : 50 articles majeurs sur 3 291, médiane d'UN par jour, et
    12 des 30 derniers jours n'en ont produit aucun. En filtre du jour,
    l'onglet aurait été vide 40 % du temps — on l'ouvre une fois, il est
    vide, on ne l'ouvre plus. En chronologie complète il couvre deux mois
    et se lit d'un bout à l'autre.
    """
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    # Un fil où les majeurs sont ANCIENS et les ordinaires récents : c'est
    # ce qui distingue une chronologie d'un filtre du jour. Si l'onglet ne
    # montrait que les récents, il serait vide ici.
    page.evaluate("""() => {
      historyPartial = false; rattrapageLance = true; archiveChargee = true;
      hotThreshold = 3;
      lastItems = [];
      for(let j = 1; j <= 10; j++){
        lastItems.push({title: "Ordinaire " + j, link: "https://ex.test/o" + j,
          source: "VG247", date: new Date(Date.now() - j * 3600000).toISOString(),
          lang: "en"});
      }
      [["Vieux majeur A", 3, 40], ["Vieux majeur B", 4, 55],
       ["Majeur d'hier", 3, 1]].forEach((m, k) => {
        const extra = [];
        for(let q = 1; q < m[1]; q++) extra.push({source: "S" + q, link: "https://ex.test/x" + k + q});
        lastItems.push({title: m[0], link: "https://ex.test/m" + k, source: "IGN",
          date: new Date(Date.now() - m[2] * 86400000).toISOString(),
          lang: "en", extraSources: extra});
      });
      // Un article à DEUX rédactions : sous le seuil, il ne doit pas entrer.
      lastItems.push({title: "Deux seulement", link: "https://ex.test/deux",
        source: "IGN", date: new Date().toISOString(), lang: "en",
        extraSources: [{source: "S", link: "https://ex.test/y"}]});
      lastItems.sort((a, b) => new Date(b.date) - new Date(a.date));
      derniereReponseBackend = {hot_threshold: 3};
      applyFilters();
    }""")

    check(page.locator("#tabMajeur").count() == 1, "l'onglet existe")
    rangee = page.evaluate("""() => {
      const r = document.querySelectorAll('.tab-row')[0];
      return [...r.querySelectorAll('.tab')].map(e => e.id);
    }""")
    check(rangee == ["tabAll", "tabMajeur"],
          "il est dans la rangée du HAUT, à côté de « Tous les articles » — "
          "la rangée du bas découpe par source, celle-ci par importance (%s)" % rangee)

    page.evaluate("() => setTab('majeur')")
    page.wait_for_timeout(150)
    titres = page.locator("#feed .card-title").all_inner_texts()
    check(sorted(titres) == ["Majeur d'hier", "Vieux majeur A", "Vieux majeur B"],
          "il montre TOUTE la chronologie, un majeur de 55 jours compris, et "
          "rien d'autre (%s)" % titres)
    check("Deux seulement" not in titres,
          "un sujet à deux rédactions reste dehors : le seuil vient du backend")
    check(titres[0] == "Majeur d'hier",
          "du plus récent au plus ancien, comme le fil (%s)" % titres[0])

    # Le badge de la carte et le filtre de l'onglet disent la MÊME chose.
    # C'est la raison d'être de estMajeur() : recalculés séparément, ils
    # auraient pu diverger sans qu'aucun contrôle ne le voie.
    badges = page.locator("#feed .tag-hot").all_inner_texts()
    check(len(badges) == 3 and all("ACTU MAJEURE" in b for b in badges),
          "chaque carte de l'onglet porte le badge, et il dit le nom de "
          "l'onglet (%s)" % badges)
    check(any("4 SOURCES" in b for b in badges),
          "le nombre de rédactions reste écrit : cinq n'est pas trois (%s)" % badges)

    # La pastille compte les NON LUS, comme les trois autres onglets.
    def pastille():
        return page.locator("#badgeMajeur").inner_text()
    check(pastille() == "3", "la pastille compte les trois non lus (%s)" % pastille())
    page.evaluate("""async () => {
      readSet.add("https://ex.test/m0"); await saveRead(); applyFilters();
    }""")
    page.wait_for_timeout(150)
    check(pastille() == "2", "elle descend quand on en lit un (%s)" % pastille())
    page.evaluate("""async () => {
      ["https://ex.test/m1", "https://ex.test/m2"].forEach(l => readSet.add(l));
      await saveRead(); applyFilters();
    }""")
    page.wait_for_timeout(150)
    check(pastille() == "",
          "et elle disparaît quand tout est lu, au lieu d'afficher 0 (« %s »)" % pastille())

    # L'onglet doit SURVIVRE à un rechargement. Sans son entrée dans
    # ONGLETS_MEMORISES il retomberait sur « Tous », en silence.
    memo = page.evaluate("() => JSON.parse(localStorage.getItem(STORAGE_PREFIX + CLE_FILTRES) || '{}').onglet")
    check(memo == "majeur", "l'onglet choisi est mémorisé (%s)" % memo)
    page.reload(wait_until="load")
    page.wait_for_selector("#tabMajeur")
    page.wait_for_timeout(250)
    actif = page.get_attribute("#tabMajeur", "aria-pressed")
    check(actif == "true",
          "et il est toujours actif après rechargement (aria-pressed=%s)" % actif)

    check(not erreurs, "aucune erreur JavaScript (%s)" % (erreurs or "aucune"))
    ctx.close()


def test_graphiques_detailles(nav, url):
    """Les graphiques refaits le 23/09/2026, à la demande d'Antoni (« plus détaillés et jolis »)."""
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    M = "2026-10-20T10:00:00Z"

    # Des repères ronds, pas des bornes au hasard.
    pas = page.evaluate("[pasGrille(300), pasGrille(402), pasGrille(133), pasGrille(30), pasGrille(1), pasGrille(7)]")
    check(pas == [100, 100, 50, 10, 1, 2],
          "le pas de la grille est le plus petit pas rond en cinq intervalles au plus : %s" % pas)
    check(page.evaluate("Math.ceil(402 / pasGrille(402)) * pasGrille(402)") == 500,
          "402 monte à 500, pas à 600 — la première version laissait un tiers du graphique vide")
    check(page.evaluate("reperesAxe(7)") == [0, 1, 2, 3, 4, 5, 6],
          "sept colonnes : sept étiquettes sous l'axe")
    check(page.evaluate("reperesAxe(15, 7)") == [0, 7, 14],
          "quinze jours : une étiquette par semaine, la dernière comprise")
    check(page.evaluate("reperesAxe(24, 6)") == [0, 6, 12, 18, 23],
          "vingt-quatre heures : 0, 6, 12, 18 et 23 h")

    # Un fil connu, rendu pour de vrai dans la vue.
    page.evaluate("""(m) => {
      const items = [];
      for(let j = 1; j <= 19; j++){
        for(let k = 0; k < (j === 12 ? 30 : 5); k++){
          items.push({title: "GTA 6 album " + j + " " + k, link: "l" + j + "-" + k, source: "S",
                      lang: k % 3 ? "en" : "fr",
                      date: "2026-10-" + String(j).padStart(2, "0") + "T" + String(8 + k % 10).padStart(2, "0") + ":00:00Z"});
        }
      }
      lastItems = items; historyPartial = false;
      derniereReponseBackend = {couverture_depuis: "2026-10-01"};
      const vrai = Date; window.__maintenant = new vrai(m);
      window.Date = class extends vrai {
        constructor(...a){ super(...(a.length ? a : [window.__maintenant])); }
        static now(){ return window.__maintenant.getTime(); }
      };
      setTab("stats"); ongletStatsChoisi("fil");
    }""", M)
    page.wait_for_selector(".stats-histo")
    premier = page.locator(".stats-bloc").filter(has_text="Articles dans le fil, par jour")
    check(premier.locator(".stats-grille").count() >= 3,
          "l'histogramme quotidien a sa grille de repères (%d)" % premier.locator(".stats-grille").count())
    check(premier.locator(".stats-grille span").all_inner_texts()[:2] == ["0", "10"],
          "graduée en valeurs rondes à partir de zéro (%s)" % premier.locator(".stats-grille span").all_inner_texts())
    check(premier.locator(".stats-mediane").count() == 1
          and "médiane 5" in premier.locator(".stats-mediane").inner_text(),
          "et sa médiane, écrite (%s)" % (premier.locator(".stats-mediane").all_inner_texts()))
    check(premier.locator(".stats-col.pic").count() == 1
          and premier.locator(".stats-col.pic em").inner_text() == "30",
          "le jour le plus chargé est en accent plein, sa valeur écrite (30)")
    check(len(premier.locator(".stats-axe span").all_inner_texts()) == 3,
          "une date par semaine sous l'axe, la dernière comprise (%s)"
          % premier.locator(".stats-axe span").all_inner_texts())

    # Toucher une colonne en donne la valeur.
    avant = premier.locator(".stats-lecture").inner_text()
    premier.locator(".stats-col").nth(2).click()
    apres = premier.locator(".stats-lecture").inner_text()
    check("Touche" in avant and apres.replace("\n", " ").startswith("5 03/10"),
          "toucher une colonne écrit sa valeur et sa date au-dessus du graphique (« %s »)" % apres.replace("\n", " "))
    check(premier.locator(".stats-col.choisie").count() == 1,
          "et la colonne touchée passe en accent plein — une seule à la fois")
    premier.locator(".stats-col").nth(5).click()
    check(premier.locator(".stats-col.choisie").count() == 1, "toucher une autre colonne déplace le choix")

    # La carte jour × heure.
    carte = page.locator(".stats-bloc").filter(has_text="Quand tombent")
    check(carte.count() == 1 and carte.locator(".stats-case").count() == 7 * 24,
          "la carte jour × heure a ses 168 cases")
    pleines = carte.locator(".stats-case:not(.vide)").count()
    check(0 < pleines < 7 * 24 and carte.locator(".stats-case.vide").count() == 7 * 24 - pleines,
          "les heures sans article restent sur le fond, les autres prennent la teinte (%d pleines)" % pleines)
    carte.locator(".stats-case:not(.vide)").first.click()
    lu = carte.locator(".stats-lecture").inner_text().replace("\n", " ")
    check(" h – " in lu and lu.split(" ")[0].isdigit(),
          "toucher une case écrit sa valeur, son jour et son heure (« %s »)" % lu)

    # Classements, langues, mots qui montent.
    medias = page.locator(".stats-bloc").filter(has_text="Qui parle le plus")
    check(medias.locator(".stats-barres .trait").count() >= 1, "les médias sont classés en barres")
    langues = page.locator(".stats-bloc").filter(has_text="Français et anglais")
    check(langues.locator(".stats-pile i").count() == 2 and langues.locator(".stats-cles span").count() == 2,
          "français et anglais : une barre à deux parts, et sa légende avec les chiffres")
    check("%" in langues.locator(".stats-cles").inner_text(), "la légende donne aussi les pourcentages")
    # Les deux paires ont été passées au validateur de palette (daltonismes
    # compris), chacune sur le fond de son thème : il faut que ce soit bien
    # celle du thème affiché.
    for theme, attendu in (("dark", ["#3987e5", "#d95926"]), ("light", ["#1d4ed8", "#eb6834"])):
        couleurs = page.evaluate("""(t) => {
          document.documentElement.setAttribute("data-theme", t);
          const cs = getComputedStyle(document.documentElement);
          return [cs.getPropertyValue("--langue-fr").trim(), cs.getPropertyValue("--langue-en").trim()];
        }""", theme)
        check(couleurs == attendu,
              "thème %s : les deux teintes validées au script sont celles affichées (%s)" % (theme, couleurs))

    # Ma lecture : la jauge.
    page.evaluate("() => { readSet = new Set(lastItems.slice(0, 20).map(i => i.link)); ongletStatsChoisi('moi'); }")
    page.wait_for_selector(".stats-jauge")
    # La jauge peut être redessinée par le chargement d'arrière-plan de l'historique et de l'archive (la CI,
    # elle, a le réseau : l'app y va chercher le vrai fil, pas sur un poste hors ligne). On attend qu'une
    # part lue soit VISIBLE, plutôt que de lire la première image : si elle ne l'est jamais, le contrôle
    # échoue quand même, au bout de cinq secondes.
    try:
        page.wait_for_function("parseFloat((document.querySelector('.stats-jauge i') || {style: {}}).style.width) > 0",
                               timeout=5000)
    except Exception:
        pass
    largeur = page.evaluate("document.querySelector('.stats-jauge i').style.width")
    check(largeur.endswith("%") and float(largeur[:-1]) > 0, "la part lue se voit en jauge (%s)" % largeur)

    check(not erreurs, "aucune erreur dans la page (%s)" % erreurs[:2])
    ctx.close()


def test_graphiques_autres_rubriques(nav, url):
    """Sources, Robot et Ma lecture refaits en graphiques (23/09/2026)."""
    import json as _json
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    demandes = []

    def runs(route):
        demandes.append(route.request.url)
        def run(debut, fin, issue, evt):
            return {"status": "completed", "conclusion": issue, "event": evt,
                    "run_started_at": debut, "updated_at": fin, "created_at": debut}
        corps = {"workflow_runs": [
            run("2026-10-20T08:00:00Z", "2026-10-20T08:02:08Z", "success", "repository_dispatch"),
            run("2026-10-20T07:00:00Z", "2026-10-20T07:03:30Z", "failure", "schedule"),
            run("2026-10-20T06:00:00Z", "2026-10-20T06:01:40Z", "success", "workflow_dispatch"),
            {"status": "in_progress", "conclusion": None, "event": "schedule",
             "run_started_at": "2026-10-20T09:00:00Z", "updated_at": "2026-10-20T09:00:30Z"},
        ]}
        route.fulfill(status=200, content_type="application/json", body=_json.dumps(corps))
    page.route("https://api.github.com/**", runs)
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    page.evaluate("""() => {
      const items = [];
      for(let j = 1; j <= 19; j++){
        for(let k = 0; k < 6; k++){
          items.push({title: "GTA 6 " + j + " " + k, link: "l" + j + "-" + k, source: "S", lang: "en",
                      rockstarmag: k === 0, official: false,
                      date: "2026-10-" + String(j).padStart(2, "0") + "T1" + k + ":00:00Z"});
        }
        items.push({title: "Officiel " + j, link: "o" + j, source: "R", lang: "en", official: true,
                    date: "2026-10-" + String(j).padStart(2, "0") + "T09:00:00Z"});
      }
      lastItems = items; historyPartial = false;
      // Un article sur deux lu, et une première vue notée pour 24 d'entre eux.
      readSet = new Set(items.filter((_, i) => i % 2 === 0).map(i => i.link));
      vusDepuis = Date.parse("2026-09-01T00:00:00Z");
      seenMap = {};
      items.slice(0, 24).forEach((it, i) => { seenMap[it.link] = { vu: Date.parse(it.date) + [2, 10, 20, 45, 90, 300, 900, 2000][i % 8] * 60000 }; });
      archiveIndex = {mois: [{mois: "2026-10", articles: 900}, {mois: "2026-09", articles: 1500}, {mois: "2026-08", articles: 40}]};
      archiveChargee = true;
      derniereReponseBackend = {
        couverture_depuis: "2026-09-08", generated_at: "2026-10-20T09:40:00Z",
        sources_health: [
          {id: "a", name: "Alpha", status: "ok", http_status: 200, entries_fetched: 25, days_since_last_article: 0},
          {id: "b", name: "Bravo", status: "ok", http_status: null, not_modified: true, entries_fetched: 0, days_since_last_article: 12},
          {id: "c", name: "Charlie", status: "cassee", http_status: 404, entries_fetched: 0, days_since_last_article: null},
          {id: "d", name: "Delta", status: "tarie", http_status: 200, entries_fetched: 30, days_since_last_article: 20},
          {id: "r", name: "Reddit", status: "en_attente", http_status: null, entries_fetched: 0},
        ],
        sources_entries_history: {a: "20,25,25", b: "5,5,5", c: "10,0", d: "30,30,30"},
      };
      const vrai = Date; window.__maintenant = new vrai("2026-10-20T10:00:00Z");
      window.Date = class extends vrai {
        constructor(...a){ super(...(a.length ? a : [window.__maintenant])); }
        static now(){ return window.__maintenant.getTime(); }
      };
      setTab("stats"); ongletStatsChoisi("sources");
    }""")

    # ---- Sources ----
    page.wait_for_selector(".stats-sparks")
    etat = page.locator(".stats-bloc").filter(has_text="État des sources")
    check(etat.locator(".stats-pile i").count() == 4
          and "cassées 1" in etat.locator(".stats-cles").inner_text().replace("\n", " "),
          "l'état des sources est une barre à parts, chaque statut nommé avec son nombre")
    noms = page.locator(".stats-sparks li > span:first-child").all_inner_texts()
    check(noms[:2] == ["Alpha", "Charlie"] and "Volume constant" in noms,
          "les sources dont le volume bouge d'abord, puis les courbes plates (%s)" % noms)
    check(page.locator(".stats-sparks svg polyline").count() == 4,
          "une mini-courbe par source qui a au moins deux relevés")
    rep = page.locator(".stats-bloc").filter(has_text="Réponses au dernier passage")
    cles = rep.locator(".stats-cles").inner_text().replace("\n", " ")
    check("flux reçu 2" in cles and "inchangé (304) 1" in cles and "erreur 1" in cles,
          "les réponses en quatre familles, la source au repos exclue (%s)" % cles)
    check(rep.locator(".stats-tableau td").count() > 0, "et le détail des codes reste dans son tableau")
    sil = page.locator(".stats-bloc").filter(has_text="Les plus silencieuses").locator(".stats-barres li .n").all_inner_texts()
    check(sil == ["jamais", "20 j", "12 j"], "les silencieuses en barres, « jamais » d'abord (%s)" % sil)

    # ---- Robot ----
    page.evaluate("ongletStatsChoisi('robot')")
    page.wait_for_selector(".stats-bloc:has-text('Les derniers passages') .stats-col")
    passages = page.locator(".stats-bloc").filter(has_text="Les derniers passages")
    check(passages.locator(".stats-col").count() == 3,
          "trois passages terminés dessinés, celui en cours écarté")
    check(passages.locator(".stats-col.echec").count() == 1, "l'échec est en rouge")
    check("2 réussis" in passages.locator(".stats-sous").inner_text(),
          "le sous-titre compte les réussites (%s)" % passages.locator(".stats-sous").inner_text())
    passages.locator(".stats-col").last.click()
    lu = passages.locator(".stats-lecture").inner_text().replace("\n", " ")
    check(lu.startswith("2 min 08") and "cron-job.org" in lu and "réussi" in lu,
          "toucher un passage donne sa durée, son déclencheur et son issue (« %s »)" % lu)
    page.evaluate("ongletStatsChoisi('sources'); ongletStatsChoisi('robot');")
    page.wait_for_timeout(300)
    check(len(demandes) == 1, "rouvrir Robot ne refait pas la requête à GitHub (%d requête(s))" % len(demandes))
    archive = page.locator(".stats-bloc").filter(has_text="L'archive, mois par mois")
    check(archive.locator(".stats-col").count() == 3 and archive.locator(".stats-col.ancien").count() == 1,
          "l'archive mois par mois, août en gris : avant la couverture complète")

    # ---- Ma lecture ----
    page.evaluate("ongletStatsChoisi('moi')")
    page.wait_for_selector(".stats-empile")
    jour = page.locator(".stats-bloc").filter(has_text="jour par jour")
    check(jour.locator(".stats-empile").count() >= 7 and jour.locator(".stats-cles span").count() == 2,
          "lus et non lus empilés jour par jour, avec leur légende")
    jour.locator(".stats-empile").last.click()
    lecture = jour.locator(".stats-lecture").inner_text().replace("\n", " ")
    check(" lus sur 6 · " in lecture, "toucher un jour donne ses lus sur son total (« %s »)" % lecture)
    check(jour.locator(".stats-empile.choisie i.non-lu").count() == 1
          and page.evaluate("getComputedStyle(document.querySelector('.stats-empile.choisie i.non-lu')).backgroundColor")
          != page.evaluate("getComputedStyle(document.querySelector('.stats-empile.choisie i.lu')).backgroundColor"),
          "la colonne touchée garde ses deux couleurs — le non-lu ne passe pas en accent")
    jauges = page.locator(".stats-jauge-ligne .haut").all_inner_texts()
    totaux = [j.replace("\n", " ").split(" / ")[1].split(" ")[0] for j in jauges]
    check(len(jauges) == 3 and jauges[0].startswith("Rockstar") and totaux == ["19", "19", "95"],
          "trois jauges aux règles des onglets : 19 officiels des mêmes jours, 19 RockstarMag, "
          "95 ni l'un ni l'autre (%s)" % [j.replace("\n", " ") for j in jauges])
    delais = page.locator(".stats-bloc").filter(has_text="Entre la publication").locator(".stats-col")
    check(delais.count() == 7, "les délais en sept tranches fines (%d)" % delais.count())
    valeurs = [int(x) for x in delais.evaluate_all("cols => cols.map(c => c.dataset.lecture)")]
    check(valeurs == [3, 3, 3, 3, 3, 3, 6] and sum(valeurs) == 24,
          "chaque délai tombe dans sa tranche, et les 24 notés sont comptés (%s)" % valeurs)

    check(not erreurs, "aucune erreur dans la page (%s)" % erreurs[:2])
    ctx.close()


def test_archive_mois_par_mois(nav, url):
    """Un bouton par mois, et un mois demandé ne télécharge que lui.

    Le chargement automatique prend toute l'archive, donc cette ligne
    n'apparaît que lorsqu'il n'a pas abouti — réseau coupé, délai dépassé.
    C'est exactement le moment où tout redemander d'un coup a le plus de
    chances d'échouer encore, et où ne vouloir qu'un mois est légitime.

    GTA 6 sort le 19/11/2026 : le mois de la sortie pèsera plusieurs fois
    ce que pèse l'archive entière aujourd'hui (3,4 Mo pour 11 mois). Le
    bouton global annoncerait alors un poids que personne ne cliquerait.
    """
    import json as _json
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()

    MOIS = {"2026-09": 3, "2026-08": 2, "2026-07": 1}
    index = {"generated_at": "2026-10-06T12:00:00Z",
             "articles": sum(MOIS.values()),
             "mois": [{"mois": m, "articles": n, "octets": 500_000 * n,
                       "fichiers": [{"fichier": m + ".json", "articles": n,
                                     "octets": 500_000 * n}]}
                      for m, n in MOIS.items()]}

    demandes = []

    def sert(route):
        chemin = route.request.url.split("?")[0].split("/")[-1]
        demandes.append(chemin)
        if chemin == "index.json":
            corps = index
        else:
            m = chemin[:-5]
            corps = {"mois": m, "items": [
                {"title": f"{m} article {k}", "link": f"https://x.test/{m}/{k}",
                 "date": f"{m}-15T1{k}:00:00+00:00"} for k in range(MOIS[m])]}
        route.fulfill(status=200, content_type="application/json",
                      body=_json.dumps(corps))

    page.route("**/archives/*.json*", sert)
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    # L'app croit avoir sa fenêtre complète, et pas encore l'archive :
    # c'est l'état dans lequel la ligne s'affiche.
    page.evaluate("""(index) => {
      settings.backendUrl = location.href.replace(/[^/]+$/, "feed.json");
      historyPartial = false; lastItems = []; archiveIndex = index;
      archiveChargee = false; moisCharges = new Set();
      updateArchiveLine();
    }""", index)

    ligne = page.locator("#archiveLine")
    boutons = ligne.locator("button")
    check(boutons.count() == 4,
          "trois mois + un bouton « Tout » (%d boutons)" % boutons.count())
    libelles = boutons.all_inner_texts()
    # 1 500 000 octets = 1,4 Mio : poidsLisible compte en multiples de 1024,
    # comme tout ce qui annonce un poids de téléchargement.
    check(libelles[0].startswith("sept.") and "1,4 Mo" in libelles[0],
          "chaque mois annonce SON poids, pas celui de l'archive (« %s »)" % libelles[0])
    check(libelles[-1].startswith("Tout") and "2,9 Mo" in libelles[-1],
          "et le bouton global annonce la somme (« %s »)" % libelles[-1])

    # --- un mois, et un seul -------------------------------------------
    demandes.clear()
    boutons.first.click()
    page.wait_for_function("moisCharges.has('2026-09')", timeout=10000)
    check(demandes == ["2026-09.json"],
          "cliquer un mois ne télécharge QUE lui (%s)" % demandes)
    check(page.evaluate("lastItems.length") == 3,
          "ses articles sont bien entrés")
    check(page.evaluate("archiveChargee") is False,
          "et l'app ne se croit pas complète : il manque deux mois, les "
          "statistiques mensuelles seraient fausses")
    check(ligne.locator("button").count() == 3,
          "le mois chargé disparaît des boutons (%d restants)"
          % ligne.locator("button").count())

    # --- le reste d'un coup --------------------------------------------
    demandes.clear()
    ligne.locator("button").last.click()
    page.wait_for_function("archiveChargee === true", timeout=10000)
    check(sorted(demandes) == ["2026-07.json", "2026-08.json"],
          "« Tout » ne redemande pas le mois déjà là (%s)" % sorted(demandes))
    check(page.evaluate("lastItems.length") == 6, "les six articles sont là")
    check(ligne.evaluate("e => e.style.display") == "none",
          "et la ligne disparaît une fois l'archive complète")

    # --- une tranche qui échoue ne fait pas croire le mois chargé ------
    page.unroute("**/archives/*.json*")
    # Un SEUL gestionnaire, et pas deux : Playwright donne la priorité à
    # celui enregistré en dernier, donc une route générale ajoutée après une
    # route précise masque la précise — le 503 ne serait jamais servi.
    page.route("**/archives/*.json*",
               lambda r: r.fulfill(status=503, body="")
               if "2026-08.json" in r.request.url else sert(r))
    page.evaluate("""(index) => {
      lastItems = []; archiveChargee = false; moisCharges = new Set();
      archiveIndex = index; updateArchiveLine();
    }""", index)
    page.evaluate("async () => { await loadArchive(); }")
    check(page.evaluate("moisCharges.has('2026-08')") is False,
          "un mois dont une tranche a échoué n'est PAS marqué chargé — sinon "
          "il quitterait les boutons et ne serait jamais redemandé")
    check(page.evaluate("archiveChargee") is False,
          "et l'archive ne se déclare pas complète")
    check(page.evaluate("moisCharges.has('2026-09') && moisCharges.has('2026-07')"),
          "les mois qui ont répondu, eux, sont bien acquis")
    ctx.close()


def test_actualiser_ne_jette_plus_rien(nav, url):
    """Le défaut signalé par Antoni le 22/09/2026, et sa réparation.

    Reproduit avant d'écrire une ligne de correctif :

        1. ouverture (fichier allégé)     :  300 articles
        2. après « Tout charger »         : 1804 articles
        3. APRÈS UNE SIMPLE ACTUALISATION :  300 articles

    La cause tenait en une ligne, `lastItems = all`, où `all` est le contenu
    du fichier qu'on vient de lire. Au rafraîchissement c'est le fichier
    ALLÉGÉ, donc 300 articles écrasaient tout le reste, archive comprise.

    Deux propriétés sont verrouillées ici, et elles sont indépendantes :
    l'app ne perd plus ce qu'elle a chargé, et elle le charge toute seule au
    démarrage.
    """
    base = url.rsplit("/", 1)[0]
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    # --- Le rattrapage automatique : le fil D'ABORD, le reste derrière.
    page.evaluate("settings.backendUrl = '%s/feed.json';" % base)
    page.evaluate("async () => { await checkFromBackend(false); }")
    tout_de_suite = page.evaluate("lastItems.length")
    check(0 < tout_de_suite <= 300,
          "à l'affichage, seul le fichier allégé est là (%d articles) — le "
          "rattrapage ne retarde pas le fil" % tout_de_suite)

    page.wait_for_function("lastItems.length > %d" % tout_de_suite, timeout=20000)
    page.wait_for_timeout(1200)
    complet = page.evaluate("lastItems.length")
    check(complet > tout_de_suite,
          "puis l'historique complet arrive seul, sans un clic (%d articles)" % complet)
    check(page.evaluate("historyPartial") is False,
          "et l'app se sait complète")

    # --- LE défaut : actualiser ne doit plus ramener à 300.
    page.evaluate("async () => { await checkFromBackend(false); }")
    apres = page.evaluate("lastItems.length")
    check(apres >= complet,
          "après une actualisation : %d articles, pas %d" % (apres, tout_de_suite))
    page.evaluate("async () => { await checkFromBackend(false); }")
    check(page.evaluate("lastItems.length") >= complet,
          "et après une seconde actualisation, toujours autant")

    # La fusion ne doit ni dupliquer ni désordonner.
    check(page.evaluate("new Set(lastItems.map(i => i.link)).size === lastItems.length"),
          "la fusion n'introduit aucun doublon de lien")
    check(page.evaluate("""() => {
        const d = lastItems.map(i => new Date(i.date).getTime());
        return d.every((v, n) => n === 0 || d[n - 1] >= v);
    }"""), "et le fil reste trié du plus récent au plus ancien")

    # Une seule fois par session : la fusion rend tout retéléchargement
    # inutile, et relancer deux gros fichiers à chaque actualisation serait
    # exactement ce que le fichier allégé existe pour éviter.
    demandes = []
    page.on("request", lambda r: demandes.append(r.url.split("/")[-1].split("?")[0])
            if "feed.json" in r.url else None)
    page.evaluate("async () => { await checkFromBackend(false); }")
    page.wait_for_timeout(1200)
    check(demandes == [],
          "une actualisation ne retélécharge PAS le fichier complet (%s)" % demandes)
    ctx.close()


def test_archive_dans_lapp(nav, url):
    """L'archive mensuelle est atteignable depuis l'app, et sans dégât.

    Le backend produit docs/archives/ depuis le 22/09/2026 ; sans ce qui
    suit, ce ne seraient que des fichiers sur un serveur. Trois choses se
    vérifient ici et nulle part ailleurs :

      - la ligne n'apparaît QUE quand la fenêtre est déjà complète, sinon
        deux boutons concurrents proposeraient le même geste ;
      - ce qu'elle annonce est ce qu'elle AJOUTE, pas le total de l'archive.
        L'archive est un sur-ensemble de la fenêtre : annoncer son total
        promettrait des milliers d'articles pour n'en ajouter que quelques
        centaines ;
      - les articles d'archive ne comptent JAMAIS comme des nouveautés. Un
        article de juillet qui arrive aujourd'hui ferait sonner les
        pastilles de non-lus pour des centaines de vieux articles.
    """
    base = url.rsplit("/", 1)[0]

    MOIS = {
        "2026-08.json": [
            {"link": "https://arch.test/a%d" % n, "title": "archive août %d" % n,
             "date": "2026-08-%02dT10:00:00+00:00" % (n + 1), "source": "Test"}
            for n in range(12)],
        "2026-07.json": [
            {"link": "https://arch.test/b%d" % n, "title": "archive juillet %d" % n,
             "date": "2026-07-%02dT10:00:00+00:00" % (n + 1), "source": "Test"}
            for n in range(8)],
    }
    EN_PLUS = sum(len(v) for v in MOIS.values())

    def prepare(page, total_archive, casser_index=False):
        """Branche un faux répertoire d'archives sur le réseau de la page."""
        import json as _json

        def index(route):
            if casser_index:
                return route.fulfill(status=404, body="")
            route.fulfill(status=200, content_type="application/json",
                          body=_json.dumps({
                              "articles": total_archive,
                              "mois": [
                                  {"mois": "2026-08", "articles": 12, "octets": 12000,
                                   "fichiers": [{"fichier": "2026-08.json",
                                                 "articles": 12, "octets": 12000}]},
                                  {"mois": "2026-07", "articles": 8, "octets": 8000,
                                   "fichiers": [{"fichier": "2026-07.json",
                                                 "articles": 8, "octets": 8000}]},
                              ]}))

        page.route("**/archives/index.json*", index)

        # Une FABRIQUE, et pas un lambda à argument par défaut : Playwright
        # appelle le gestionnaire avec (route, request), donc un
        # `lambda route, it=items:` recevait la requête à la place des
        # articles. L'erreur ne se voyait pas dans le verdict du test, elle
        # partait dans un « Error occurred in event listener ».
        def sert(items):
            def gestionnaire(route, request=None):
                route.fulfill(status=200, content_type="application/json",
                              body=_json.dumps({"items": items}))
            return gestionnaire

        for nom, items in MOIS.items():
            page.route("**/archives/" + nom + "*", sert(items))

    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("settings.backendUrl = '%s/feed.json';" % base)

    # Fenêtre INCOMPLÈTE : la ligne d'archive doit rester muette.
    prepare(page, 99999)
    page.evaluate("""async () => {
        rattrapageLance = true;
        historyPartial = true; archiveIndex = null; archiveChargee = false;
        await chargeIndexArchive(); updateArchiveLine();
    }""")
    check(not page.locator("#archiveLine").is_visible(),
          "[archive] fenêtre incomplète : la ligne d'archive ne s'affiche pas")

    # Fenêtre complète : on charge le vrai feed.json, puis on annonce une
    # archive qui contient EN_PLUS articles de plus que lui.
    #
    # `rattrapageLance` est armé À LA MAIN pour neutraliser le rattrapage
    # automatique du démarrage : sans ça il chargerait l'archive tout seul
    # pendant qu'on vérifie la ligne, et on lirait « Chargement de
    # l'archive… » au lieu de ce qu'elle annonce. Ce contrôle-ci porte sur
    # le chemin MANUEL ; l'automatique a le sien, plus bas.
    page.evaluate("rattrapageLance = true;")
    page.evaluate("async () => { await checkFromBackend(true); }")
    dans_fenetre = page.evaluate("lastItems.length")
    check(dans_fenetre > 0,
          "[archive] la fenêtre est chargée (%d article(s))" % dans_fenetre)

    page.unroute("**/archives/index.json*")
    prepare(page, dans_fenetre + EN_PLUS)
    page.evaluate("""async () => {
        historyPartial = false; archiveIndex = null; archiveChargee = false;
        await chargeIndexArchive(); updateArchiveLine();
    }""")
    ligne = page.locator("#archiveLine")
    check(ligne.is_visible(),
          "[archive] fenêtre complète : la ligne d'archive apparaît")
    txt = ligne.inner_text()
    check(str(EN_PLUS) in txt,
          "[archive] elle annonce ce qu'elle AJOUTE (%d), pas le total « %s »"
          % (EN_PLUS, txt.replace("\n", " ")))
    check("2026-07" in txt and "2026-08" in txt,
          "[archive] elle donne la période couverte (« %s »)" % txt.replace("\n", " "))
    check("Ko" in txt or "Mo" in txt,
          "[archive] elle donne le poids, pour prévenir avant de télécharger")

    # Le chargement lui-même.
    avant_nouveaux = page.evaluate("lastNewLinks.size")
    page.evaluate("async () => { await loadArchive(); }")
    apres = page.evaluate("lastItems.length")
    check(apres == dans_fenetre + EN_PLUS,
          "[archive] chargée : %d articles au lieu de %d" % (apres, dans_fenetre))
    check(page.evaluate("lastNewLinks.size") == avant_nouveaux,
          "[archive] aucun article d'archive n'est compté comme nouveau")
    check(page.evaluate(
        "lastItems.filter(i => i.link.startsWith('https://arch.test/')).length") == EN_PLUS,
        "[archive] les articles d'archive sont bien dans le fil")
    check(page.evaluate("""() => {
        const d = lastItems.map(i => new Date(i.date).getTime());
        return d.every((v, n) => n === 0 || d[n - 1] >= v);
    }"""), "[archive] le fil reste trié du plus récent au plus ancien")
    check(not ligne.is_visible(),
          "[archive] la ligne disparaît une fois l'archive chargée")

    # Rejouer ne doit rien faire : sans ce garde, un double clic doublerait
    # chaque article du fil.
    page.evaluate("async () => { await loadArchive(); }")
    check(page.evaluate("lastItems.length") == apres,
          "[archive] rejouer le chargement n'ajoute rien")
    ctx.close()

    # Pas d'archive du tout (backend d'une version antérieure) : la ligne
    # reste muette et RIEN ne s'affiche comme une panne.
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("settings.backendUrl = '%s/feed.json';" % base)
    prepare(page, 0, casser_index=True)
    page.evaluate("""async () => {
        rattrapageLance = true;
        historyPartial = false; archiveIndex = null; archiveChargee = false;
        await chargeIndexArchive(); updateArchiveLine();
    }""")
    check(not page.locator("#archiveLine").is_visible(),
          "[archive] pas d'archive : la ligne reste muette")
    journal = page.evaluate("logs.map(l => l.level + ' ' + l.message).join('\\n')")
    check("Archive indisponible" not in journal,
          "[archive] pas d'archive : aucune erreur affichée, c'est une absence")
    ctx.close()


def test_vignette_ouvre_larticle(nav, url):
    """Cliquer la vignette doit ouvrir l'article, comme cliquer le titre.

    C'est la plus grande zone de la carte. Ne rien faire au clic la faisait
    passer pour décorative, et obligeait à viser le titre.

    Le piège n'est pas de poser le lien, c'est de le poser sans en créer un
    second : l'image est en alt="", donc une ancre focalisable autour d'elle
    serait un lien SANS INTITULÉ vers la même page que le titre. Un lecteur
    d'écran annoncerait « lien » et rien de plus, et le clavier gagnerait un
    arrêt inutile par carte. D'où aria-hidden + tabindex="-1", et le
    contrôle du nombre de liens focalisables ci-dessous.
    """
    base = url.rsplit("/", 1)[0]
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    # Un PNG d'un pixel pour toutes les vignettes : le test ne dépend
    # d'aucun réseau, mais les images existent donc la carte en a une.
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                        "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
                        "0049454e44ae426082")
    # Par TYPE DE RESSOURCE, et pas par extension. Le glob
    # `**/*.{png,jpg,jpeg,webp,gif}` a tenu jusqu'au 13/10/2026, puis la
    # première carte à vignette est devenue un article dont l'image porte
    # une chaîne de requête :
    #
    #     …/gta-6-apercu-etendu-14-resized.jpeg?resize=1600,900&key=6ae0c341
    #
    # Le glob ne matchait plus, l'image partait sur le vrai réseau — bloqué
    # ici —, `onerror` masquait la vignette, et le test cliquait sur un
    # élément invisible. Il a échoué sur `main` sans qu'une ligne de l'app
    # ait changé : c'est la DONNÉE qui avait bougé. Le type de ressource,
    # lui, ne dépend ni de l'extension ni de la query.
    page.route("**/*", lambda r: r.fulfill(status=200, content_type="image/png", body=png)
               if r.request.resource_type == "image" else r.continue_())
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("settings.backendUrl = '%s/feed.json';" % base)
    page.evaluate("async () => { await checkFromBackend(false); }")
    page.wait_for_timeout(300)

    info = page.evaluate("""() => {
        // Une carte SANS voile anti-spoiler : le 10/11/2026 la première carte à vignette du fil est devenue un article
        // voilé (« Afficher cet article, qui peut contenir un spoiler »), le voile recouvre la vignette et le clic
        // n'atteint pas le lien. Encore la DONNÉE qui bouge, pas l'app : on choisit une carte dont la vignette se clique.
        const carte = [...document.querySelectorAll('.card')].find(c => c.querySelector('.card-thumb') && !c.querySelector('.spoiler-voile'));
        if(!carte) return null;
        const img = carte.querySelector('.card-thumb');
        img.setAttribute('data-test-vignette', '1');
        const lien = carte.querySelector('.card-thumb-lien');
        const titre = carte.querySelector('.card-title a');
        const r = img.getBoundingClientRect();
        return {
            dansLien: !!lien && lien.contains(img),
            idCarte: carte.id,
            hrefVignette: lien ? lien.getAttribute('href') : null,
            hrefTitre: titre ? titre.getAttribute('href') : null,
            ariaHidden: lien ? lien.getAttribute('aria-hidden') : null,
            tabindex: lien ? lien.getAttribute('tabindex') : null,
            // Les liens focalisables qui mènent AU MÊME ARTICLE que le
            // titre — c'est précisément ce que ce contrôle surveille : la
            // vignette ne doit pas en ajouter un second, sans intitulé.
            //
            // Et non « tous les liens de la carte » : la ligne « + N autres
            // sources » en porte un par rédaction, focalisables à dessein,
            // et elle vit dans .card-top comme le reste. Compter la carte
            // entière rendait le verdict dépendant du fil du jour — le
            // 06/10/2026 la première carte à vignette a gagné une autre
            // source, et ce contrôle est passé au rouge sans qu'une seule
            // ligne de code ait bougé.
            focalisables: [...carte.querySelectorAll('a[href]')]
                .filter(x => x.tabIndex >= 0
                             && x.getAttribute('href') === titre.getAttribute('href')).length,
            largeur: Math.round(r.width), hauteur: Math.round(r.height),
            // L'image doit rester l'enfant flex de .card-top : si l'ancre
            # produisait une boîte, la géométrie serait celle de l'ancre.
            parentFlex: img.parentElement.closest('.card-top') !== null,
        };
    }""".replace("#", "//"))

    check(info is not None, "[vignette] une carte avec vignette a été trouvée")
    if not info:
        ctx.close()
        return

    check(info["dansLien"], "[vignette] l'image est bien dans un lien")
    check(info["hrefVignette"] == info["hrefTitre"],
          "[vignette] elle mène au MÊME article que le titre")
    check(info["ariaHidden"] == "true" and info["tabindex"] == "-1",
          "[vignette] le lien est retiré de l'arbre d'accessibilité")
    check(info["focalisables"] == 1,
          "[vignette] la vignette et le titre ne font qu'UN seul lien focalisable "
          "(obtenu : %d)" % info["focalisables"])
    # 16/9 à 390 px de large moins les marges : l'enveloppe ne doit rien
    # avoir changé à la géométrie. display:contents est là pour ça.
    ratio = info["largeur"] / info["hauteur"] if info["hauteur"] else 0
    check(abs(ratio - 16 / 9) < 0.05,
          "[vignette] la géométrie est intacte (%dx%d, ratio %.2f)"
          % (info["largeur"], info["hauteur"], ratio))

    # Le clic navigue-t-il vraiment ? On lit l'URL DEMANDÉE : la cible est
    # un vrai site, injoignable depuis la CI, donc l'onglet finira en erreur
    # — ce qui compte est qu'il ait tenté la bonne adresse.
    #
    # `expect_page` et non `wait_for_timeout(700)`. La version d'origine
    # dormait 700 ms en espérant que l'onglet soit apparu, puis regardait
    # une liste remplie par un écouteur. C'est une COURSE, et elle a été
    # perdue une fois en CI le 22/09/2026 : le contrôle a échoué sur un
    # commit, puis passé sur le suivant sans qu'une ligne de l'app ait
    # changé. Un runner chargé met parfois plus de 700 ms à ouvrir un
    # onglet.
    #
    # `expect_page` attend l'ÉVÉNEMENT, avec une limite haute plutôt qu'un
    # délai fixe : il rend la main dès que l'onglet existe, donc le test est
    # à la fois plus sûr et plus rapide dans le cas normal. L'attente
    # restant la même, ce qui est vérifié ne change pas d'un iota — c'est la
    # façon d'attendre qui change, pas l'exigence.
    # L'adresse se lit sur la REQUÊTE, pas sur `page.url` de l'onglet. Le
    # commentaire ci-dessus l'annonçait déjà, mais le code lisait bien
    # `pg.url` — et comme la cible est injoignable, Chromium y met
    # « chrome-error://chromewebdata/ ». Un écouteur de requêtes, lui,
    # enregistre l'adresse TENTÉE, qu'elle aboutisse ou non.
    attendu = info["hrefTitre"]
    demandees = []
    ctx.on("request", lambda r: demandees.append(r.url))
    try:
        with ctx.expect_page(timeout=10000) as onglet:
            page.click("[data-test-vignette]", force=True)
        ouvert = onglet.value
    except Exception:
        ouvert = None

    check(ouvert is not None,
          "[vignette] le clic ouvre bien un onglet sur l'article")
    check(attendu in demandees,
          "[vignette] et c'est la bonne adresse qui est demandée (%s)" % attendu)

    lu = page.evaluate("""() => {
        // Par l'IDENTIFIANT de la carte : markRead relance applyFilters, qui refait la carte et perd le marqueur posé dessus.
        const carte = document.getElementById(%s);
        return carte ? carte.classList.contains('read') : false;
    }""" % json.dumps(info["idCarte"]))
    check(lu, "[vignette] et l'article est marqué comme lu, comme par le titre")
    ctx.close()


def test_sources_disent_la_verite(nav, url):
    """Les interrupteurs de source ne font RIEN quand le robot alimente le fil.

    Le chemin backend construit le fil depuis feed.json et ne consulte jamais
    settings.feeds[].enabled : décocher « IGN » n'en retire pas un article.
    Pire, un commentaire du 22/09/2026 affirmait le contraire, et la
    description de la section disait « une source éteinte n'est plus
    interrogée, et ses articles déjà reçus sortent de la liste ».

    La section reste — on doit pouvoir préparer le réglage À FROID, pas
    seulement pendant une panne — mais elle est repliée et étiquetée quand
    le robot répond, et pleinement utile en mode de secours.
    """
    base = url.rsplit("/", 1)[0]
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("rattrapageLance = true;")
    page.evaluate("settings.backendUrl = '%s/feed.json';" % base)
    page.evaluate("async () => { await checkFromBackend(false); }")

    ouvre_parametres(page)
    etat = page.evaluate("""() => ({
        mode: document.getElementById('modeIndicator').className,
        ouvert: document.getElementById('sourcesRepli').open,
        titre: document.getElementById('sourcesLabelTexte').textContent,
        resume: document.getElementById('sourcesResume').textContent,
        desc: document.getElementById('sourcesDesc').textContent,
        ligne: document.getElementById('infoSourcesLine').textContent,
        nbCases: document.querySelectorAll('#sourceList input[type=checkbox]').length,
        // LE NOMBRE ATTENDU VIENT DE LA LISTE ELLE-MÊME. Il était écrit
        // « 63 » en dur à trois endroits de ce test : le jour où six
        // sources ont été ajoutées pour l'après-sortie (20/10/2026), deux
        // contrôles sont passés au rouge alors que rien n'était cassé.
        // Un nombre recopié dans un test n'est pas un contrôle, c'est une
        // deuxième source de vérité.
        nbSources: DEFAULT_FEEDS.length,
        aide: !!document.querySelector('#sourcesGroup .setting-label .aide'),
    })""")

    check("backend" in etat["mode"],
          "[sources] on est bien en mode backend (%s)" % etat["mode"])
    check(etat["ouvert"] is False,
          "[sources] la section est REPLIÉE quand le robot répond")
    check("secours" in etat["titre"],
          "[sources] et son titre le dit : « %s »" % etat["titre"])
    # Le résumé ne RÉPÈTE pas l'avertissement du titre : à 320 px les deux
    # passaient à la ligne pour dire la même chose. Il ne porte que le
    # décompte, et c'est le titre qui prévient.
    check(etat["resume"] == "%d sources" % etat["nbSources"],
          "[sources] le résumé replié ne porte que le décompte, sans répéter "
          "le titre (« %s »)" % etat["resume"])
    check("ne retirent aucun article" in etat["desc"],
          "[sources] l'explication ne prétend plus l'inverse")
    check("sortent de la liste" not in etat["desc"],
          "[sources] l'ancienne phrase fausse a bien disparu")

    # LE PIÈGE DU BALISAGE. greffeAides() cherche la .setting-desc[hidden] en
    # « :scope > » du .setting-group, et greffe le « ? » dans .setting-label.
    # Enfermer le groupe entier dans le <details>, ou écrire le titre avec
    # label.textContent, faisait disparaître l'aide sans un mot.
    check(etat["aide"],
          "[sources] le bouton « ? » du groupe a survécu à la réorganisation")

    # La ligne du panneau Informations était construite depuis les cases
    # cochées : fausse pour exactement la même raison. Elle annonce
    # maintenant ce que le ROBOT interroge, ce qui est à la fois vrai et plus
    # utile que de la retirer.
    # Sur la FORME et non sur « 63 » : cette ligne compte les sources que
    # le robot a réellement interrogées, lu dans feed.json. Elle suit donc
    # le dernier passage du robot, pas la liste du code — les six sources
    # ajoutées le 20/10/2026 n'y apparaîtront qu'au passage suivant, et un
    # test qui exigeait le nombre d'aujourd'hui serait rouge entre les deux.
    check("robot" in etat["ligne"] and re.search(r"\d+", etat["ligne"]),
          "[sources] la ligne des Informations annonce les sources du ROBOT "
          "(« %s »)" % etat["ligne"])

    # ON N'EFFACE RIEN : le mode de secours doit retrouver le réglage.
    check(etat["nbCases"] == etat["nbSources"],
          "[sources] les %d interrupteurs sont toujours là, repliés "
          "(%d)" % (etat["nbSources"], etat["nbCases"]))

    # --- Bascule en mode direct : ils redeviennent la vraie commande.
    page.evaluate("""() => {
        const m = document.getElementById('modeIndicator');
        m.className = 'mode-indicator direct';
        populateSettingsUI();
    }""")
    direct = page.evaluate("""() => ({
        ouvert: document.getElementById('sourcesRepli').open,
        titre: document.getElementById('sourcesLabelTexte').textContent,
        desc: document.getElementById('sourcesDesc').textContent,
        ligne: document.getElementById('infoSourcesLine').textContent,
    })""")
    check(direct["ouvert"] is True,
          "[sources] en mode de secours la section se DÉPLIE toute seule — "
          "c'est là qu'elle sert, et c'est le pire moment pour un clic de plus")
    check(direct["titre"] == "Sources actives",
          "[sources] et retrouve son vrai titre (« %s »)" % direct["titre"])
    check("décident lesquels" in direct["desc"],
          "[sources] l'explication redevient celle d'une commande réelle")
    check("robot" not in direct["ligne"],
          "[sources] la ligne des Informations reliste les sources de l'appareil")

    # Un déplient-replient manuel ne doit pas être défait au prochain rendu :
    # populateSettingsUI est rejouée à chaque vérification, et refermer sous
    # le doigt une liste qu'on vient d'ouvrir serait pire que tout.
    page.evaluate("document.getElementById('sourcesRepli').open = false;")
    page.evaluate("populateSettingsUI();")
    check(page.evaluate("document.getElementById('sourcesRepli').open") is False,
          "[sources] un repli manuel survit au rendu suivant, à mode constant")

    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.evaluate("populateSettingsUI(); updateSourcesLine();")
    check(not erreurs, "[sources] aucune erreur JavaScript (%s)" % (erreurs[:1],))
    ctx.close()


def test_compte_a_rebours(nav, url):
    """Le compteur avant la sortie, et APRÈS — ce qu'il ne savait pas faire.

    Il affichait « GTA 6 est sorti ! » dès le 19 novembre passé, et ne
    bougeait plus jamais. Au bout d'une semaine, une page qui répète la
    même phrase a l'air abandonnée, et c'est le seul endroit de l'app dont
    le texte dit explicitement quel jour on est.

    L'horloge est FAUSSÉE côté navigateur (page.clock) plutôt que la date
    de sortie déplacée côté page : GTA6_RELEASE est un `const` de module,
    et un contrôle qui aurait besoin de le réécrire n'éprouverait pas le
    vrai chemin. Ici c'est le temps qui bouge, comme dans la réalité.
    """
    print("\n[compteur] avant la sortie, et après")

    def a(instant):
        """Rend le texte et la classe du compteur à cet instant-là.

        Fuseau de Paris explicite : la constante porte son propre décalage,
        donc la comparaison n'en dépend pas — mais le contrôle doit DIRE
        d'où il regarde, sinon il deviendrait muet le jour où quelqu'un
        passerait la sortie en « minuit heure locale ».
        """
        ctx = nav.new_context(viewport={"width": 390, "height": 850},
                              timezone_id="Europe/Paris")
        ctx.clock.install(time=instant)
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        page.wait_for_selector("#countdown", state="attached")
        page.evaluate("updateCountdown()")
        etat = page.evaluate("""() => {
            const el = document.getElementById('countdown');
            return {texte: document.getElementById('cbCompteur').textContent, classe: el.className};
        }""")
        ctx.close()
        return etat

    # La sortie : 19/11/2026 à MINUIT HEURE DE PARIS, soit 23h00 UTC le 18.
    # Le jeu se déverrouille à minuit heure locale, par vagues — minuit en
    # Nouvelle-Zélande d'abord (11h00 UTC le 18), minuit à l'Est des
    # États-Unis en dernier. Relevé dans le fil que l'app collecte
    # elle-même, les 07 et 08/09/2026 (GTA BOOM, VGTimes, Eurogamer).
    avant = a("2026-11-01T12:00:00Z")
    check(__import__("re").match(r"^\d+j \d\dh$", avant["texte"]) is not None,
          "[compteur] avant la sortie, il compte à rebours (« %s »)"
          % avant["texte"])

    # Les paliers d'intensité, à leurs vraies bornes.
    check(a("2026-11-14T12:00:00Z")["classe"].endswith("urgent"),
          "[compteur] à moins de sept jours, le palier passe à « urgent »")
    check(a("2026-11-18T18:00:00Z")["classe"].endswith("final"),
          "[compteur] dans les dernières heures, « final »")

    # LA BORNE, DES DEUX CÔTÉS — et c'est le contrôle qui manquait.
    #
    # La constante a longtemps valu minuit à NEW YORK, soit 6 h du matin à
    # Paris. À 00 h 30 le 19 novembre, pendant qu'on y joue, le compteur
    # annonçait « C'EST AUJOURD'HUI — 5h 29m ». Aucun contrôle ne regardait
    # cette heure-là ; celui-ci la regarde, des deux côtés de minuit.
    veille = a("2026-11-18T22:30:00Z")   # 23 h 30 à Paris, le 18
    check(__import__("re").match(r"^\d\d:\d\d:\d\d$", veille["texte"]) is not None,
          "[compteur] une demi-heure AVANT minuit à Paris, il compte encore "
          "(« %s »)" % veille["texte"])
    check("SORTI" not in veille["texte"].upper(),
          "[compteur] et il n'annonce surtout pas la sortie en avance "
          "(« %s »)" % veille["texte"])
    minuit = a("2026-11-18T23:30:00Z")   # 00 h 30 à Paris, le 19
    check(minuit["texte"] == "SORTI !",
          "[compteur] une demi-heure APRÈS minuit à Paris, c'est sorti — "
          "la valeur « minuit à New York » ratait exactement ça (« %s »)"
          % minuit["texte"])

    # LE JOUR J. Le palier qui crie est mérité ce jour-là, et ce jour-là
    # seulement.
    jour0 = a("2026-11-19T08:00:00Z")
    check(jour0["texte"] == "SORTI !" and "final" in jour0["classe"].split(),
          "[compteur] le jour même, il l'annonce et garde le palier fort "
          "(« %s » / %s)" % (jour0["texte"], jour0["classe"]))

    # ET LE LENDEMAIN, il repart dans l'autre sens au lieu de se figer.
    jour1 = a("2026-11-20T08:00:00Z")
    check(jour1["texte"] == "+1 j",
          "[compteur] le lendemain, il compte les jours écoulés (« %s »)" % jour1["texte"])
    jour3 = a("2026-11-22T08:00:00Z")
    check(jour3["texte"] == "+3 j",
          "[compteur] et continue ensuite (« %s »)" % jour3["texte"])
    check("final" not in jour3["classe"].split() and "countdown" in jour3["classe"].split(),
          "[compteur] le ton redescend passé le premier jour — un bandeau "
          "rouge permanent n'alerte plus de rien (%s)" % jour3["classe"])

    # Trois mois plus tard, il dit encore quelque chose de vrai.
    tard = a("2027-02-19T08:00:00Z")
    check(tard["texte"] == "+3 mois",
          "[compteur] trois mois après, il tient toujours (« %s »)"
          % tard["texte"])


def test_resume_du_mode_de_secours(nav, url):
    """Le mode de secours nettoie les résumés comme le robot.

    Signalé par Antoni le 20/10/2026. Le chemin backend affichait les
    entités HTML telles quelles ; celui-ci ne l'a jamais fait, son
    stripHtml passe par innerHTML qui décode. Ce qui lui manquait, c'est
    la seconde moitié : vider le résumé quand il ne fait que répéter le
    titre — 85,6 % des articles du fil.
    """
    print("\n[résumé] le mode de secours nettoie comme le robot")
    ctx = nav.new_context(viewport={"width": 390, "height": 900},
                          timezone_id="Europe/Paris", reduced_motion="reduce")
    page = ctx.new_page()
    err = []
    page.on("pageerror", lambda e: err.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    cas = page.evaluate("""() => ({
        // Le cas réel du fil, celui de la capture d'écran.
        googleNews: nettoieResume(
          "Daft Punk aurait refusé une offre colossale de Rockstar pour la "
          + "bande-son de GTA 6&nbsp;&nbsp;Dexerto.fr",
          "Daft Punk aurait refusé une offre colossale de Rockstar pour la "
          + "bande-son de GTA 6 - Dexerto.fr"),
        identique: nettoieResume("Le titre exact", "Le titre exact"),
        // Et ce qu'il ne faut PAS vider.
        vrai: nettoieResume("Un r&eacute;sum&eacute; bien réel et assez long",
                            "Un titre tout autre"),
        prolonge: nettoieResume(
          "Le titre exact, et puis une suite qui apporte vraiment quelque chose",
          "Le titre exact"),
        vide: nettoieResume("", "Un titre"),
        balises: nettoieResume("<p>Du <b>texte</b> mis en forme et bien assez long</p>",
                               "Autre"),
    })""")
    check(cas["googleNews"] == "" and cas["identique"] == "",
          "[résumé] un résumé qui ne répète que le titre est vidé (« %s » / "
          "« %s »)" % (cas["googleNews"], cas["identique"]))
    check(cas["vrai"] == "Un résumé bien réel et assez long",
          "[résumé] un vrai résumé est gardé, entités décodées (« %s »)"
          % cas["vrai"])
    check(cas["prolonge"].startswith("Le titre exact, et puis"),
          "[résumé] et un résumé qui prolonge le titre de plus de 25 "
          "caractères reste (« %s »)" % cas["prolonge"][:40])
    check(cas["vide"] == "" and cas["balises"] == "Du texte mis en forme et bien assez long",
          "[résumé] un résumé absent ne plante pas, les balises partent "
          "toujours (« %s »)" % cas["balises"])

    # LE BOUTON D'APERÇU SUIT, SANS SECOND DRAPEAU. Il ne s'affiche que si
    # description est non vide : vider le résumé le fait disparaître tout
    # seul. C'est exactement ce qu'Antoni a choisi — « le cacher quand il
    # n'apprend rien ».
    boutons = page.evaluate("""() => {
        lastItems = [
          {link: "https://r.test/1", source: "T", date: new Date().toISOString(),
           title: "Avec un vrai résumé", lang: "fr",
           description: "Un extrait qui apporte vraiment quelque chose de neuf"},
          {link: "https://r.test/2", source: "T", date: new Date().toISOString(),
           title: "Sans résumé utile", lang: "fr", description: ""},
        ];
        visibleCount = 100; settings.maxDisplay = 100; badgeActif = null;
        currentTab = "all"; currentFilter = "all"; currentLang = "all";
        settings.spoilerMode = false;
        applyFilters();
        return [...document.querySelectorAll(".card")].map(c => ({
          titre: c.querySelector(".card-title").textContent.trim(),
          apercu: !!c.querySelector('[title="Aperçu de l\\'article"]'),
        }));
    }""")
    check(len(boutons) == 2 and boutons[0]["apercu"] and not boutons[1]["apercu"],
          "[résumé] le bouton d'aperçu n'apparaît que là où il y a un "
          "extrait (%s)" % boutons)

    check(not err, "[résumé] aucune erreur JavaScript (%s)" % err[:1])
    ctx.close()


def test_mode_sans_spoiler(nav, url):
    """Flouter les articles à spoiler, et les révéler d'un geste.

    Demandé par Antoni le 20/10/2026 pour l'après-sortie : le 19 au soir
    les titres se mettent à raconter le jeu, et il y jouera des semaines.

    Trois réponses étaient possibles — masquer, grouper à part, flouter.
    Il a choisi flouter : masquer perd l'information, flouter la diffère.
    Ce contrôle tient surtout le point qui n'est pas cosmétique : un flou
    ne désarme pas un lien, donc le voile doit intercepter le geste.
    """
    print("\n[spoiler] le mode sans spoiler")
    ctx = nav.new_context(viewport={"width": 390, "height": 900},
                          timezone_id="Europe/Paris", reduced_motion="reduce")
    page = ctx.new_page()
    err = []
    page.on("pageerror", lambda e: err.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    # ÉTEINT PAR DÉFAUT, liste PRÉ-REMPLIE. Les deux comptent : un fil
    # flouté sans raison avant la sortie ressemble à une panne, et une
    # liste vide obligerait à deviner les mots le soir du 19.
    defaut = page.evaluate("""() => ({
        mode: DEFAULT_SETTINGS.spoilerMode,
        n: (DEFAULT_SETTINGS.spoilerKeywords || []).length,
        // Une COPIE et pas la constante elle-même : sans le .slice(),
        // modifier ses mots dans les réglages modifierait la valeur par
        // défaut, et « réinitialiser » ne rendrait plus la liste d'origine.
        copie: DEFAULT_SETTINGS.spoilerKeywords !== MOTS_SPOILER,
        exemples: (DEFAULT_SETTINGS.spoilerKeywords || []).slice(0, 3),
    })""")
    # ALLUMÉ par défaut depuis le 20/10/2026 au soir, et c'est un
    # revirement du jour même : livré éteint le matin, au motif qu'avant
    # la sortie il n'y a rien à protéger. La mesure a dit l'inverse — la
    # liste pré-remplie voile déjà 41 articles du fil sur 4 350, dont
    # « Cyberleek stops GTA 6 leaks after posting parts of final
    # missions ». Les fuites racontent la fin du jeu un mois avant.
    check(defaut["mode"] is True,
          "[spoiler] ALLUMÉ par défaut : les fuites racontent déjà la fin "
          "du jeu (%s)" % defaut["mode"])
    check(defaut["n"] >= 15,
          "[spoiler] mais sa liste est pré-remplie : la remplir le soir du "
          "19 serait trop tard (%s mots, %s…)" % (defaut["n"], defaut["exemples"]))
    check(defaut["copie"],
          "[spoiler] et c'est une COPIE de la constante — sinon changer ses "
          "mots changerait la valeur par défaut elle-même")

    # LE VOILE, et ce qu'il désarme.
    vue = page.evaluate("""() => {
        settings.spoilerMode = true;
        settings.spoilerKeywords = ["mission finale", "qui meurt"];
        spoilersReveles = new Set();
        lastItems = [
          {link: "https://s.test/1", source: "T", date: new Date().toISOString(),
           title: "GTA 6 : la mission finale en vidéo", lang: "fr"},
          {link: "https://s.test/2", source: "T", date: new Date().toISOString(),
           title: "GTA 6 : le prix de l'édition collector", lang: "fr"},
        ];
        visibleCount = 100; settings.maxDisplay = 100; badgeActif = null;
        currentTab = "all"; currentFilter = "all"; currentLang = "all";
        applyFilters();
        const cartes = [...document.querySelectorAll(".card")];
        const masquee = cartes.find(c => c.classList.contains("spoiler-masque"));
        const nette = cartes.find(c => !c.classList.contains("spoiler-masque"));
        const voile = masquee && masquee.querySelector(".spoiler-voile");
        const lien = masquee && masquee.querySelector(".card-title a");
        return {
          cartes: cartes.length,
          masquees: cartes.filter(c => c.classList.contains("spoiler-masque")).length,
          // LE COMPTE NE BOUGE PAS : flouter n'est pas masquer. Si le
          // mode retirait l'article du fil, il ferait doublon avec les
          // mots d'exclusion et perdrait sa raison d'être.
          ligne: document.getElementById("countLine").textContent,
          voile: !!voile,
          etiquette: voile ? voile.textContent : null,
          aria: voile ? voile.getAttribute("aria-label") : null,
          // Le titre est FLOUTÉ et le lien DÉSARMÉ. Le second compte plus
          // que le premier : un flou laisse un lien cliquable.
          flou: masquee ? getComputedStyle(masquee.querySelector(".card-top")).filter : null,
          clics: lien ? getComputedStyle(lien).pointerEvents : null,
          // Et la carte voisine, qui ne contient aucun mot, est intacte.
          netteFlou: nette ? getComputedStyle(nette.querySelector(".card-top")).filter : null,
          netteVoile: nette ? !!nette.querySelector(".spoiler-voile") : null,
        };
    }""")
    check(vue["cartes"] == 2 and vue["masquees"] == 1,
          "[spoiler] seul l'article qui porte le mot est voilé (%s sur %s)"
          % (vue["masquees"], vue["cartes"]))
    check("2 affichés" in vue["ligne"],
          "[spoiler] et le COMPTE ne bouge pas : flouter n'est pas masquer, "
          "sans quoi ce mode ferait doublon avec les mots d'exclusion "
          "(« %s »)" % vue["ligne"].strip())
    check(vue["voile"] and "Spoiler" in (vue["etiquette"] or ""),
          "[spoiler] un voile dit pourquoi et ce qu'il faut faire (« %s »)"
          % (vue["etiquette"] or "").strip())
    check("Afficher" in (vue["aria"] or ""),
          "[spoiler] et il porte un nom pour les lecteurs d'écran (« %s »)"
          % vue["aria"])
    check("blur" in (vue["flou"] or ""),
          "[spoiler] le contenu est flouté (%s)" % vue["flou"])
    check(vue["clics"] == "none",
          "[spoiler] et le lien du titre est DÉSARMÉ — un flou tout seul "
          "laisserait l'article ouvrable d'un doigt, ce qui est exactement "
          "l'accident à éviter (%s)" % vue["clics"])
    check(vue["netteFlou"] == "none" and vue["netteVoile"] is False,
          "[spoiler] la carte sans mot de spoiler est intacte (%s, voile %s)"
          % (vue["netteFlou"], vue["netteVoile"]))

    # UN GESTE RÉVÈLE, ET SEULEMENT CELUI-LÀ. Le clic ne doit pas non plus
    # ouvrir l'article — le voile est posé par-dessus un lien.
    page.locator(".card.spoiler-masque .spoiler-voile").first.click()
    page.wait_for_timeout(120)
    apres = page.evaluate("""() => ({
        masquees: document.querySelectorAll(".card.spoiler-masque").length,
        voiles: document.querySelectorAll(".spoiler-voile").length,
        flou: getComputedStyle(document.querySelector(".card .card-top")).filter,
        clics: getComputedStyle(document.querySelector(".card .card-title a")).pointerEvents,
        onglets: 1,
    })""")
    check(apres["masquees"] == 0 and apres["voiles"] == 0,
          "[spoiler] un geste révèle la carte et retire son voile (%s / %s)"
          % (apres["masquees"], apres["voiles"]))
    check(apres["flou"] == "none" and apres["clics"] != "none",
          "[spoiler] le titre redevient net ET cliquable (%s, %s)"
          % (apres["flou"], apres["clics"]))

    # CHANGER LA LISTE REMET LES RÉVÉLATIONS À ZÉRO. Sans ça, l'article
    # révélé avant d'ajouter le mot qui le concerne resterait visible : le
    # mot qu'on vient d'écrire pour s'en protéger n'aurait servi à rien.
    remise = page.evaluate("""() => {
        const n = spoilersReveles.size;
        document.getElementById("spoilerMode").checked = true;
        document.getElementById("spoilerKeywordsInput").value = "mission finale";
        appliqueReglages();
        return {avant: n, apres: spoilersReveles.size,
                masquees: document.querySelectorAll(".card.spoiler-masque").length};
    }""")
    check(remise["avant"] == 1 and remise["apres"] == 0,
          "[spoiler] changer la liste remet les révélations à zéro (%s → %s)"
          % (remise["avant"], remise["apres"]))
    check(remise["masquees"] == 1,
          "[spoiler] et la carte redevient voilée (%s)" % remise["masquees"])

    # LE MODE ÉTEINT NE VOILE RIEN, même avec des mots dans la liste.
    eteint = page.evaluate("""() => {
        document.getElementById("spoilerMode").checked = false;
        appliqueReglages();
        return document.querySelectorAll(".card.spoiler-masque").length;
    }""")
    check(eteint == 0,
          "[spoiler] éteint, il ne voile rien même avec des mots (%s)" % eteint)

    # LA CHRONOLOGIE AUSSI. Un mode qui ne s'applique qu'à une vue sur deux
    # est un mode auquel on ne peut pas se fier.
    chrono = page.evaluate("""() => {
        settings.spoilerMode = true;
        settings.spoilerKeywords = ["mission finale"];
        spoilersReveles = new Set();
        lastItems = [{link: "https://s.test/3", source: "Rockstar",
                      date: new Date().toISOString(), official: true,
                      title: "GTA 6 : la mission finale dévoilée", lang: "en"}];
        currentTab = "rockstar";
        visibleCount = 100; settings.maxDisplay = 100;
        applyFilters();
        const pt = document.querySelector(".chrono-point");
        const r = {
          chrono: !!pt,
          masque: pt ? pt.classList.contains("spoiler-masque") : null,
          voile: pt ? !!pt.querySelector(".spoiler-voile") : null,
          clics: pt ? getComputedStyle(pt.querySelector(".chrono-lien")).pointerEvents : null,
        };
        currentTab = "all"; settings.spoilerMode = false; applyFilters();
        return r;
    }""")
    check(chrono["chrono"] and chrono["masque"] and chrono["voile"],
          "[spoiler] la chronologie de l'onglet Rockstar est voilée aussi "
          "(%s)" % chrono)
    check(chrono["clics"] == "none",
          "[spoiler] et son lien est désarmé pareillement (%s)" % chrono["clics"])

    check(not err, "[spoiler] aucune erreur JavaScript (%s)" % err[:1])
    ctx.close()


def test_vecteurs_du_resume(nav, url):
    """`nettoieResume` rend ce que dit vecteurs_resume.json, comme le robot.

    Le fichier est lu aussi par test_pipeline.py contre `nettoie_resume`.
    """
    print("\n[résumé] les vecteurs partagés avec le robot")
    chemin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vecteurs_resume.json")
    with open(chemin, encoding="utf-8") as f:
        cas = json.load(f)["cas"]
    ctx = nav.new_context(viewport={"width": 390, "height": 800})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    obtenus = page.evaluate("(v) => v.map(c => nettoieResume(c.description, c.titre))", cas)
    ecarts = ["%s : %r au lieu de %r" % (c["nom"], o, c["attendu"])
              for c, o in zip(cas, obtenus) if o != c["attendu"]]
    check(len(cas) >= 10 and not ecarts,
          "nettoieResume rend la sortie attendue pour les %d vecteurs%s"
          % (len(cas), "" if not ecarts else " — " + " | ".join(ecarts)))
    ctx.close()


def test_compte_a_rebours_flashy(nav, url):
    """Les trois paliers du compte à rebours en FLUO, lisibles dans les deux thèmes.

    Demandé par Antoni le 28/10/2026 (« pas assez flashy », puis « plutôt des
    couleurs fluo »). Sombre : le texte est fluo, avec un halo. Clair : un texte
    fluo sur blanc est illisible ; le compteur y fut un surligneur jusqu'au
    07/11/2026, où Antoni l'a fait retirer (« supprime le surlignage et colore
    plutôt »), puis a voulu le FLUO tout le temps : le texte reste fluo, cerné
    d'un trait sombre qui porte la lisibilité (4,5:1 entre le fluo et le trait),
    sans fond, sans contour ni halo.
    """
    print("\n[compteur] les trois paliers, en fluo et lisibles")

    def lire(css):
        return tuple(int(x) for x in re.findall(r"\d+", css)[:3])

    def lum(c):
        def f(v):
            v /= 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])

    def contraste(a, b):
        x, y = lum(a), lum(b)
        return (max(x, y) + 0.05) / (min(x, y) + 0.05)

    paliers = (("soon", "2026-10-28T10:00:00Z"), ("urgent", "2026-11-14T10:00:00Z"),
               ("final", "2026-11-18T15:00:00Z"))
    largeurs = {}
    for theme in ("dark", "light"):
        for palier, instant in paliers:
            for largeur in (340, 390):
                ctx = nav.new_context(viewport={"width": largeur, "height": 800},
                                      timezone_id="Europe/Paris", reduced_motion="reduce")
                ctx.clock.install(time=instant)
                page = ctx.new_page()
                erreurs = []
                page.on("pageerror", lambda e, err=erreurs: err.append(str(e)))
                page.goto(url, wait_until="load")
                page.wait_for_selector(".header-haut")
                page.evaluate("""(t) => { document.documentElement.setAttribute('data-theme', t);
                    const m = document.getElementById('modeIndicator');
                    m.className = 'mode-indicator backend'; m.innerHTML = '<span class="dot"></span><span class="mi-mot">Backend GitHub</span>'; }""", theme)
                page.wait_for_timeout(300)
                m = page.evaluate("""(pal) => {
                    const c = document.getElementById('countdown'), cs = getComputedStyle(c);
                    const root = getComputedStyle(document.documentElement);
                    return {classe: c.className, couleur: cs.color, fondTexte: cs.backgroundColor,
                            halo: cs.textShadow, poids: parseInt(cs.fontWeight, 10),
                            contour: cs.outlineStyle + ' ' + cs.outlineWidth,
                            largeur: c.getBoundingClientRect().width,
                            jeton: root.getPropertyValue('--compte-' + pal).trim(),
                            encre: root.getPropertyValue('--compte-encre').trim(),
                            traitLargeur: parseFloat(cs.webkitTextStrokeWidth) || 0,
                            traitCouleur: cs.webkitTextStrokeColor,
                            carte: getComputedStyle(document.querySelector('.console')).backgroundColor}; }""", palier)
                etiq = "[%s %s %d px]" % (theme, palier, largeur)
                h = m["jeton"].lstrip("#")
                fluo = tuple(int(h[k:k + 2], 16) for k in (0, 2, 4))
                check(palier in m["classe"].split(),
                      "%s l'horloge posée donne bien le palier « %s » (%s)" % (etiq, palier, m["classe"]))
                check(max(fluo) >= 240 and min(fluo) <= 90,
                      "%s la teinte du jeton est FLUO (%s : un canal à fond, un autre presque éteint)"
                      % (etiq, m["jeton"]))
                check(m["poids"] >= 800, "%s en gras 800 (%d)" % (etiq, m["poids"]))
                if theme == "dark":
                    r = contraste(lire(m["couleur"]), lire(m["carte"]))
                    check(lire(m["couleur"]) == fluo and r >= 4.5,
                          "%s texte fluo sur la carte, 4,5:1 (%.2f:1)" % (etiq, r))
                    check(m["halo"] != "none", "%s halo lumineux" % etiq)
                else:
                    encre = tuple(int(m["encre"].lstrip("#")[k:k + 2], 16) for k in (0, 2, 4))
                    r = contraste(fluo, encre)
                    check(lire(m["couleur"]) == fluo and lire(m["traitCouleur"]) == encre
                          and m["traitLargeur"] >= 1 and r >= 4.5,
                          "%s texte fluo cerné d'un trait sombre, 4,5:1 fluo/trait (%.2f:1)" % (etiq, r))
                    check(m["fondTexte"] == "rgba(0, 0, 0, 0)" and m["contour"].startswith("none")
                          and m["halo"] == "none",
                          "%s plus de surlignage : ni fond, ni contour, ni halo (fond %s, contour %s)"
                          % (etiq, m["fondTexte"], m["contour"]))
                largeurs[(theme, palier, largeur)] = m["largeur"]
                if largeur == 390:
                    # Le panneau reprend la MÊME teinte : celle du texte du compteur, dans les deux thèmes.
                    page.evaluate("() => ouvreCompteur()")
                    page.wait_for_timeout(300)
                    t = page.evaluate("""() => { const b = document.querySelector(
                          '#compteurContenu .tuile-panneau .countdown .cd-t:not(.zero) b');
                        if(!b) return null; const cs = getComputedStyle(b);
                        return {couleur: cs.color, fond: cs.backgroundColor}; }""")
                    attendu = lire(m["couleur"])
                    obtenu = lire(t["couleur"])
                    check(t is not None and obtenu == attendu,
                          "%s le panneau porte la même teinte (%s)" % (etiq, t))
                check(not erreurs, "%s aucune erreur JavaScript" % etiq)
                ctx.close()
    # Le surligneur ne doit RIEN pousser : même largeur de compteur dans les deux thèmes.
    for (theme, palier, largeur), w in largeurs.items():
        if theme == "light":
            sombre = largeurs[("dark", palier, largeur)]
            check(abs(w - sombre) < 1.0,
                  "[%s %d px] la couleur ne change pas la largeur du compteur (%.0f px / %.0f px)"
                  % (palier, largeur, w, sombre))


def test_agenda_jours_civils(nav, url):
    """Le « J-N » de l'agenda tient aux frontières de minuit, à Paris.

    Les pièges sont ceux du compteur de dodos : un jour n'est pas 24 heures
    (le 25/10/2026 en dure 25 à Paris), et minuit à Paris n'est pas minuit
    UTC. Les vecteurs se placent à la SECONDE de chaque côté des frontières.
    """
    print("\n[agenda] J-N en jours civils à Paris")
    ctx = nav.new_context(viewport={"width": 390, "height": 900},
                          timezone_id="Europe/Paris", reduced_motion="reduce")
    page = ctx.new_page()
    err = []
    page.on("pageerror", lambda e: err.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    r = page.evaluate("""() => {
        const e = (date, extra) => Object.assign({
          date: date, libelle: "Rdv " + date, statut: "presse",
          verifie_le: "2026-10-27",
          source: {media: "M", titre: "T", lien: "https://x.test/a"}}, extra || {});
        const un = (date, iso, extra) =>
          rendezVousAVenir({entrees: [e(date, extra)]}, new Date(iso))[0] || null;
        const J = (x) => x ? x.jours : null;
        return {
          // Minuit à Paris le 12/11/2026, heure d'hiver : 23:00 UTC la veille.
          avantMinuit: J(un("2026-11-12", "2026-11-11T22:59:59Z")),
          apresMinuit: J(un("2026-11-12", "2026-11-11T23:00:00Z")),
          etatJour: (un("2026-11-12", "2026-11-11T23:00:00Z") || {}).etat,
          finDuJour: J(un("2026-11-12", "2026-11-12T22:59:59Z")),
          lendemain: J(un("2026-11-12", "2026-11-12T23:00:00Z")),
          etatLendemain: (un("2026-11-12", "2026-11-12T23:00:00Z") || {}).etat,
          deuxJoursApres: un("2026-11-12", "2026-11-13T23:00:00Z"),
          // Le passage à l'heure d'hiver : le 25/10/2026, 03:00 -> 02:00.
          // Minuit du 25 tombe à 22:00 UTC (encore UTC+2)...
          avantDST: J(un("2026-10-25", "2026-10-24T21:59:59Z")),
          apresDST: J(un("2026-10-25", "2026-10-24T22:00:00Z")),
          // ... et minuit du 26 à 23:00 UTC (déjà UTC+1) : une heure plus
          // tard dans le calendrier UTC que la veille, parce que le 25
          // dure 25 heures.
          avantDST26: J(un("2026-10-26", "2026-10-25T22:59:59Z")),
          apresDST26: J(un("2026-10-26", "2026-10-25T23:00:00Z")),
          // LE VECTEUR QUI DISCRIMINE. À 00:30 UTC le 25/10 il est 02:30 à
          // Paris, et il reste 22,5 h avant minuit du 26 : une division par
          // 24 h donnerait 0 jour. Le calendrier dit J-1. (Un premier vecteur
          // — 48,5 h pour deux jours — rendait 2 des deux façons et ne
          // prouvait rien.)
          vingtCinqHeures: J(un("2026-10-26", "2026-10-25T00:30:00Z")),
          // À revérifier : la 14e journée passe encore, la 15e non.
          revoir14: un("2026-11-20", "2026-11-10T12:00:00Z").aRevoir,
          revoir15: un("2026-11-20", "2026-11-11T12:00:00Z").aRevoir,
          sansVerif: un("2026-11-20", "2026-10-28T12:00:00Z", {verifie_le: undefined}).aRevoir,
          // Un statut inconnu ne devient JAMAIS une annonce.
          statutInconnu: un("2026-11-20", "2026-10-28T12:00:00Z", {statut: "officiel"}).statut,
          statutAnnonce: un("2026-11-20", "2026-10-28T12:00:00Z", {statut: "annonce"}).statut,
        };
    }""")
    check(r["avantMinuit"] == 1 and r["apresMinuit"] == 0,
          "minuit à Paris est la frontière du jour : J-1 à 23:59:59, J-0 à "
          "00:00:00 (%s puis %s)" % (r["avantMinuit"], r["apresMinuit"]))
    check(r["etatJour"] == "aujourdhui",
          "le jour même s'annonce « aujourd'hui » (%s)" % r["etatJour"])
    check(r["finDuJour"] == 0 and r["lendemain"] is None and r["etatLendemain"] is None,
          "à 23:59:59 le jour même elle est encore là (J-0), à minuit elle a DISPARU : plus de « passé d'un jour » grisé "
          "(Antoni, 11/11/2026) (%s, %s, %s)" % (r["finDuJour"], r["lendemain"], r["etatLendemain"]))
    check(r["deuxJoursApres"] is None,
          "deux jours après, elle a toujours disparu (%s)" % r["deuxJoursApres"])
    check(r["avantDST"] == 1 and r["apresDST"] == 0,
          "le changement d'heure du 25/10 ne décale pas la frontière : J-1 "
          "puis J-0 à la seconde (%s puis %s)" % (r["avantDST"], r["apresDST"]))
    check(r["avantDST26"] == 1 and r["apresDST26"] == 0,
          "ni celle du lendemain, que le 25 de 25 heures repousse d'une heure "
          "UTC (%s puis %s)" % (r["avantDST26"], r["apresDST26"]))
    check(r["vingtCinqHeures"] == 1,
          "le jour du changement d'heure : 22,5 h avant minuit, c'est J-1 et "
          "pas 0 comme le rendrait une division par 24 h (%s)" % r["vingtCinqHeures"])
    check(r["revoir14"] is False and r["revoir15"] is True,
          "« à revérifier » au-delà de 14 jours exactement (%s puis %s)"
          % (r["revoir14"], r["revoir15"]))
    check(r["sansVerif"] is True,
          "sans date de vérification lisible, l'entrée est à revérifier")
    check(r["statutInconnu"] == "presse" and r["statutAnnonce"] == "annonce",
          "un statut inconnu vaut « presse », seul « annonce » est une annonce "
          "(%s, %s)" % (r["statutInconnu"], r["statutAnnonce"]))

    # LA ROBUSTESSE : un agenda cassé ne doit jamais planter le panneau.
    robuste = page.evaluate("""() => {
        const m = new Date("2026-10-28T12:00:00Z");
        const cas = [null, undefined, {}, {entrees: "x"}, {entrees: [null, 3, "x"]},
          {entrees: [{date: "2026-13-45", libelle: "x"}]},
          {entrees: [{date: "2026-02-30", libelle: "x"}]},
          {entrees: [{date: "2026-11-12"}]},
          {entrees: [{date: "2026-11-12", libelle: "   "}]},
          {entrees: [{date: 20261112, libelle: "x"}]}];
        return cas.map(c => { try { return rendezVousAVenir(c, m).length; }
                              catch(e){ return "EXCEPTION " + e.message; } });
    }""")
    check(all(x == 0 for x in robuste),
          "dix agendas cassés, aucun plante et aucun ne rend d'entrée (%s)" % robuste)

    # LE TRI ET LA LIMITE.
    tri = page.evaluate("""() => {
        const m = new Date("2026-10-28T12:00:00Z");
        const e = (d) => ({date: d, libelle: "L" + d, statut: "presse",
                           verifie_le: "2026-10-27", source: {lien: "https://x.test"}});
        const brouille = ["2026-12-05", "2026-11-02", "2026-11-30", "2026-11-12",
                          "2026-11-19", "2026-11-05", "2026-11-25"].map(e);
        return rendezVousAVenir({entrees: brouille}, m).map(x => x.date);
    }""")
    check(tri == ["2026-11-02", "2026-11-05", "2026-11-12", "2026-11-19", "2026-11-25"],
          "sept entrées en désordre : les cinq plus proches, dans l'ordre (%s)" % tri)

    check(page.evaluate("""() => [jourIsoValide("2026-02-28"), jourIsoValide("2026-02-30"),
                                  jourIsoValide("2026-13-01"), jourIsoValide("26-11-12"),
                                  jourIsoValide(20261112)]""")
          == [True, False, False, False, False],
          "un jour impossible est refusé, pas débordé sur le mois suivant")
    check(not err, "[agenda] aucune erreur JavaScript (%s)" % err[:1])
    ctx.close()


def test_agenda_dans_le_panneau(nav, url):
    """L'agenda s'affiche sous les dodos, résiste à tout, et tient à 320 px.

    Le panneau repeint son contenu chaque seconde : l'agenda doit donc
    vivre dans l'état de la page, pas dans le DOM. Un fichier absent, un
    404 ou un JSON corrompu laissent le compteur s'ouvrir sans erreur.
    """
    print("\n[agenda] affichage dans le panneau du compteur")
    bon = json.dumps({"genere_le": "2026-10-27", "entrees": [
        {"date": "2026-10-26", "libelle": "Hier passé", "statut": "presse",
         "verifie_le": "2026-10-27",
         "source": {"media": "M", "titre": "T", "lien": "https://x.test/0"}},
        {"date": "2026-10-29",
         "libelle": "Un libellé très long pour forcer le retour à la ligne "
                    "sur un écran de trois cent vingt pixels de large",
         "statut": "annonce", "verifie_le": "2026-10-27",
         "source": {"media": "M", "titre": "T", "lien": "https://x.test/1"}},
        {"date": "2026-11-12", "libelle": "Précharge", "statut": "presse",
         "verifie_le": "2026-09-01",
         "source": {"media": "M", "titre": "T", "lien": "https://x.test/2"}},
    ]})

    def ouvre(largeur, corps, statut=200, theme="dark"):
        ctx = nav.new_context(viewport={"width": largeur, "height": 900},
                              timezone_id="Europe/Paris", reduced_motion="reduce")
        ctx.clock.install(time="2026-10-27T12:00:00Z")
        page = ctx.new_page()
        erreurs = []
        page.on("pageerror", lambda e: erreurs.append(str(e)))
        page.route("**/agenda.json", lambda r: r.fulfill(
            status=statut, content_type="application/json", body=corps))
        page.goto(url, wait_until="load")
        page.wait_for_selector("#feed", state="attached")
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        page.evaluate("() => ouvreCompteur()")
        page.wait_for_timeout(400)
        return ctx, page, erreurs

    ctx, page, erreurs = ouvre(320, bon)
    lignes = page.evaluate("""() => [...document.querySelectorAll('#compteurContenu .compteur-agenda li')]
        .map(li => ({classe: li.className, texte: li.textContent}))""")
    check(len(lignes) == 2, "[agenda] deux lignes affichées, l'événement d'hier a disparu (%d)" % len(lignes))
    check(not any("Hier" in l["texte"] or "passé" in l["texte"] for l in lignes) and not any(l["classe"] == "passe" for l in lignes),
          "[agenda] rien de « passé » ni d'« Hier » : un événement passé disparaît (Antoni, 11/11/2026)")
    if len(lignes) == 2:
        check("J-2" in lignes[0]["texte"] and "annoncé" in lignes[0]["texte"],
              "[agenda] 29/10 est à J-2 et « annoncé » (%s)" % lignes[0]["texte"])
        check("selon la presse" in lignes[1]["texte"] and "à revérifier" in lignes[1]["texte"],
              "[agenda] une vérification de plus de 14 jours se dit « à revérifier »")
        # LA FRISE (choisie le 03/11/2026) : le jour en gros dans sa colonne, le
        # mois en petit dessous, un rail, et le statut dans `data-statut` — pas
        # dans la classe, que le contrôle ci-dessus lit à l'identique.
        frise = page.evaluate("""() => [...document.querySelectorAll('#compteurContenu .compteur-agenda li')]
            .map(li => ({jour: (li.querySelector('.ag-d b') || {}).textContent,
                         mois: (li.querySelector('.ag-d small') || {}).textContent,
                         rail: !!li.querySelector('.ag-rail'),
                         statut: li.getAttribute('data-statut'),
                         libelle: (li.querySelector('.ag-t') || {}).textContent}))""")
        check([f["jour"] for f in frise] == ["29", "12"]
              and [f["mois"] for f in frise] == ["OCT", "NOV"],
              "[agenda] la frise donne le jour et le mois de chaque rendez-vous (%s)" % frise)
        check(all(f["rail"] for f in frise)
              and [f["statut"] for f in frise] == ["annonce", "presse"],
              "[agenda] un rail par ligne, et le statut dans data-statut (%s)" % frise)
        check(frise[0]["libelle"].startswith("Un libellé très long")
              and "29" not in frise[0]["libelle"],
              "[agenda] le libellé ne répète plus la date")
    page.wait_for_timeout(2200)
    check(page.evaluate("() => document.querySelectorAll('#compteurContenu .compteur-agenda li').length") == 2,
          "[agenda] il survit au repeint de chaque seconde")
    sorties = page.evaluate("""() => {
        const p = document.querySelector('#compteurContenu').getBoundingClientRect();
        return [...document.querySelectorAll('#compteurContenu .compteur-agenda li, '
          + '#compteurContenu .compteur-agenda li span')].filter(e => {
          const r = e.getBoundingClientRect();
          return r.right > p.right + 0.5 || r.left < p.left - 0.5
              // Le texte qui déborde de sa propre cellule ne change pas la
              // boîte de la cellule : on lit aussi scrollWidth.
              || e.scrollWidth > e.clientWidth + 0.5;
        }).map(e => e.className || e.tagName);
    }""")
    check(not sorties, "[agenda] aucune ligne ni cellule ne sort du panneau à 320 px (%s)" % sorties)
    check(not erreurs, "[agenda] aucune erreur JavaScript (%s)" % erreurs)
    ctx.close()

    for theme, fond_attendu in (("dark", None), ("light", None)):
        ctx, page, _ = ouvre(390, bon, theme=theme)
        m = page.evaluate("""() => {
            const li = document.querySelectorAll('#compteurContenu .compteur-agenda li');
            let fond = 'rgba(0, 0, 0, 0)', n = li[0];
            while(n && /rgba\\(.*, 0\\)$|transparent/.test(fond)){
              fond = getComputedStyle(n).backgroundColor; n = n.parentElement; }
            const c = (e) => getComputedStyle(e).color;
            // Une opacité < 1 ferait baisser le contraste sans que la couleur
            // calculée le dise.
            const opaque = [...li].every(l => getComputedStyle(l).opacity === '1');
            return {opaque: opaque, fond: fond, normal: c(li[0]),
                    statut: c(li[0].querySelector('.ag-s'))};
        }""")

        def lire(css):
            return tuple(int(x) for x in re.findall(r"\d+", css)[:3])

        def lum(c):
            def f(v):
                v /= 255.0
                return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])

        check(m["opaque"], "[agenda %s] aucune ligne n'est rendue translucide" % theme)
        fond = lire(m["fond"])
        for cle in ("normal", "statut"):
            a, b = lum(lire(m[cle])), lum(fond)
            r = (max(a, b) + 0.05) / (min(a, b) + 0.05)
            check(r >= 4.5, "[agenda %s] %s passe 4,5:1 sur %s (%.2f:1)"
                  % (theme, cle, m["fond"], r))
        ctx.close()

    for libelle, corps, statut in (("un 404", "{}", 404),
                                   ("un JSON corrompu", "{pas du json", 200),
                                   ("un agenda sans entrées", '{"entrees": 3}', 200)):
        ctx, page, erreurs = ouvre(390, corps, statut)
        ouvert = page.evaluate("() => document.getElementById('compteurOverlay').classList.contains('open')")
        vide = page.evaluate("() => document.querySelectorAll('#compteurContenu .compteur-agenda').length") == 0
        tuiles = page.evaluate("() => document.querySelectorAll('#compteurContenu .tuile-panneau .countdown, #compteurContenu .compteur-tuiles').length") > 0
        check(ouvert and vide and tuiles and not erreurs,
              "[agenda] avec %s le compteur s'ouvre, sans section ni erreur (%s)" % (libelle, erreurs))
        ctx.close()


def test_panneau_du_compteur(nav, url):
    """Appuyer sur le compte à rebours ouvre son détail.

    Demandé par Antoni le 16/10/2026. Trois pièges nommés avant d'écrire,
    et chacun a son contrôle ici :

      - « un mois » n'a pas de longueur fixe, et setMonth DÉBORDE en
        JavaScript : le 31 janvier + 1 mois donne le 3 mars ;
      - le panneau bat chaque seconde, donc il laisse un intervalle
        derrière lui s'il ne le coupe pas à la fermeture ;
      - le compteur n'a que 14 px de haut, et le bouton de l'en-tête
        commence 14 px plus bas : une cible étendue vers le bas lui
        volerait ses clics.
    """
    print("\n[compteur] le détail derrière le compte à rebours")

    ctx = nav.new_context(viewport={"width": 390, "height": 850},
                          timezone_id="Europe/Paris")
    page = ctx.new_page()
    err = []
    page.on("pageerror", lambda e: err.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    # (Le piège de setMonth n'est plus éprouvé : le découpage en mois civils a été retiré le
    # 07/11/2026, à la demande d'Antoni — le panneau n'a plus que quatre unités.)
    # Le piège de setMonth, éprouvé sur la fonction elle-même : les mois civils servent APRÈS la sortie.
    mois = page.evaluate("""() => {
        const d = ajouteUnMois(new Date(2026, 0, 31));   // 31 janvier
        const n = ajouteUnMois(new Date(2026, 2, 15));   // 15 mars
        return [d.getMonth(), d.getDate(), n.getMonth(), n.getDate()];
    }""")
    check(mois[0] == 1 and mois[1] == 28,
          "[compteur] 31 janvier + 1 mois = 28 février, pas 3 mars — setMonth déborde tout seul (mois %d, jour %d)"
          % (mois[0], mois[1]))
    check(mois[2] == 3 and mois[3] == 15, "[compteur] et un mois ordinaire reste ordinaire (15 avril)")
    d_ap = page.evaluate("""() => [
        decoupeApres(new Date('2026-11-19T00:00:00+01:00'), new Date('2026-11-22T14:00:00+01:00')).unites.join(''),
        decoupeApres(new Date('2026-11-19T00:00:00+01:00'), new Date('2026-12-24T09:30:00+01:00')).unites.join(''),
        decoupeApres(new Date('2026-11-19T00:00:00+01:00'), new Date('2028-02-02T18:45:00+01:00')).unites.join(''),
        compactApres(decoupeApres(new Date('2026-11-19T00:00:00+01:00'), new Date('2026-12-24T09:30:00+01:00'))),
        compactApres(decoupeApres(new Date('2026-11-19T00:00:00+01:00'), new Date('2028-02-02T18:45:00+01:00'))),
        compactApres(decoupeApres(new Date('2026-11-19T00:00:00+01:00'), new Date('2027-11-19T00:00:00+01:00'))),
    ]""")
    check(d_ap == ["jhms", "Mjhms", "aMjhms", "+1 mois 5 j", "+1 an 2 mois", "+1 an"],
          "[compteur] après la sortie, mois puis années seulement quand il y en a besoin (%s)" % d_ap)

    # (phraseDecoupe, la phrase parlée « 2 jours · 11 heures », a été retirée le 07/11/2026 : le nom
    # accessible des tuiles est maintenant écrit par la même fonction pour l'en-tête et la
    # fenêtre — « avant la sortie : 11j 09h ».)
    check(page.evaluate("() => typeof phraseDecoupe === 'undefined' && typeof tuilesDecoupe === 'undefined'"),
          "[compteur] plus de seconde implémentation des tuiles du panneau")

    # LES DODOS. Un dodo est une NUIT à dormir, donc un minuit à traverser
    # — jamais une division par 24 h. Et le minuit de la sortie NE COMPTE
    # PAS : le jeu tombe à minuit pile, cette nuit-là on joue.
    dodos = page.evaluate("""() => {
        const S = new Date("2026-11-19T00:00:00+01:00");
        const d = (iso) => dodosAvant(new Date(iso), S);
        return {
          loin:      d("2026-10-16T12:24:00+02:00"),
          veille:    d("2026-11-18T23:00:00+01:00"),
          avantHier: d("2026-11-17T12:00:00+01:00"),
          minuit1:   d("2026-11-18T00:30:00+01:00"),
          apres:     d("2026-11-20T10:00:00+01:00"),
          // Le changement d'heure du 25/10/2026 : 24 nuits séparent le
          // 25 octobre du 19 novembre, et une division par 24 h en
          // trouverait une de trop ou de moins.
          heureHiver: d("2026-10-25T12:00:00+01:00"),
          phrases: [phraseDodos(33), phraseDodos(1), phraseDodos(0)],
        };
    }""")
    check(dodos["loin"] == 33,
          "[compteur] le 16/10 à 12 h 24 il reste 33 dodos, pas 34 jours "
          "(%s)" % dodos["loin"])
    check(dodos["avantHier"] == 1,
          "[compteur] l'avant-veille au midi, plus qu'un dodo (%s)"
          % dodos["avantHier"])
    check(dodos["minuit1"] == 0,
          "[compteur] le 18 au matin, ZÉRO dodo — la nuit suivante est "
          "celle de la sortie (%s)" % dodos["minuit1"])
    check(dodos["veille"] == 0,
          "[compteur] et le 18 au soir toujours zéro (%s)" % dodos["veille"])
    check(dodos["apres"] == 0,
          "[compteur] après la sortie il n'y a plus de dodo à compter (%s)"
          % dodos["apres"])
    check(dodos["heureHiver"] == 24,
          "[compteur] le changement d'heure du 25/10 ne décale pas le "
          "compte — ce sont des minuits locaux, pas des tranches de 24 h "
          "(%s)" % dodos["heureHiver"])
    check(dodos["phrases"] == ["33 dodos", "Plus qu'un dodo",
                               "Plus de dodo — c'est cette nuit"],
          "[compteur] et le singulier comme le zéro se disent en français "
          "(%s)" % dodos["phrases"])

    # L'OUVERTURE, et l'intervalle qui va avec.
    ouvert = page.evaluate("""() => {
        ouvreCompteur();
        const t = document.getElementById('compteurContenu').textContent;
        return {ouvert: document.getElementById('compteurOverlay').classList.contains('open'),
                bat: battementCompteur !== null,
                titre: document.getElementById('titreCompteur').textContent,
                dodos: /dodo/.test(t),
                // SUR LA STRUCTURE, pas sur les mots présents à cet
                // instant : ce contrôle tourne à l'heure réelle, et
                // chercher « secondes » dans le texte échouait une fois
                // sur soixante — quand la seconde du découpage vaut zéro
                // et que sa tuile est masquée. C'est la CI qui l'a pris.
                decoupe: (() => {
                  const g = document.querySelector('#compteurContenu .tuile-panneau .countdown');
                  if(!g) return null;
                  return {classe: g.className,
                          unites: [...g.querySelectorAll('i')].map(e => e.textContent)};
                })(),
                barre: !!document.querySelector('#compteurContenu .compteur-barre span'),
                pourcent: (document.querySelector('.compteur-barre i') || {}).textContent,
                tuiles: document.querySelectorAll('#compteurContenu .tuile-panneau .countdown, #compteurContenu .compteur-tuiles').length,
                equiv: (document.querySelector('.compteur-tuiles.equiv') || {}).textContent,
                phrase: (document.querySelector('#compteurContenu .tuile-panneau .countdown') || {})
                          .getAttribute?.('aria-label'),
                date: /19 novembre/.test(t), annonce: /4\/02\/22/.test(t),
                note: document.querySelector('.compteur-note').textContent};
    }""")
    check(ouvert["ouvert"], "[compteur] le panneau s'ouvre")
    check(ouvert["titre"] == "Avant la sortie",
          "[compteur] il s'intitule « Avant la sortie » (« %s »)" % ouvert["titre"])
    unites = (ouvert["decoupe"] or {}).get("unites") or []
    check(unites == ["jours", "heures", "min", "sec"],
          "[compteur] il porte les quatre mêmes tuiles que l'en-tête (%s)"
          % unites)
    # Sur la grille elle-même, et non sur le texte du panneau : à 34 jours
    # de la sortie le découpage porte déjà les mêmes mots, et un contrôle
    # qui cherche « heures » n'importe où passerait sans rien prouver.
    check(all(u in (ouvert["equiv"] or "")
              for u in ("jours", "heures", "minutes", "secondes")),
          "[compteur] et les équivalences, du jour à la SECONDE — le total "
          "en secondes est le plus spectaculaire des quatre (« %s »)"
          % ouvert["equiv"])
    check(ouvert["dodos"], "[compteur] il porte le compte de dodos")
    check(ouvert["barre"], "[compteur] et la barre de progression")
    # LE DÉCOUPAGE EST EN TUILES, PAS EN PHRASE. C'est le point de départ
    # de cette refonte : la phrase se cassait entre le nombre et son unité
    # (« 5 / minutes ») sur un téléphone étroit.
    check(ouvert["tuiles"] == 2,
          "[compteur] deux grilles de tuiles : le découpage et les "
          "équivalences (%s)" % ouvert["tuiles"])
    # phraseDecoupe n'est pas morte : elle est devenue l'étiquette du
    # groupe, pour qu'un lecteur d'écran entende une phrase et non une
    # pile de chiffres nus.
    check("avant la sortie" in (ouvert["phrase"] or ""),
          "[compteur] et le groupe de tuiles a un nom pour les lecteurs d'écran "
          "(« %s »)" % ouvert["phrase"])
    check("%" in (ouvert["pourcent"] or ""),
          "[compteur] le pourcentage est DANS la barre, plus en légende "
          "dessous (« %s »)" % ouvert["pourcent"])
    check(ouvert["annonce"],
          "[compteur] qui dit d'où elle part — une proportion sans sa "
          "référence ne veut rien dire")
    check(not ouvert["date"] and ouvert["note"].strip() == "Barre remplie depuis l'annonce du jeu, le 4/02/22",
          "[compteur] la note se réduit à d'où part la barre, sans date de sortie (« %s »)" % ouvert["note"].strip())

    # L'INTERVALLE EST COUPÉ À LA FERMETURE. Sans ça il bat pour toujours
    # sur un panneau fermé, une fois par seconde, pour rien.
    check(ouvert["bat"], "[compteur] il bat pendant qu'il est ouvert")
    apres = page.evaluate("""() => {
        fermeCompteur();
        return {ouvert: document.getElementById('compteurOverlay').classList.contains('open'),
                bat: battementCompteur !== null};
    }""")
    check(not apres["ouvert"], "[compteur] il se ferme")
    check(not apres["bat"],
          "[compteur] et son intervalle est COUPÉ — sinon il bat pour "
          "toujours sur un panneau fermé")

    # Échap ferme, comme les cinq autres dialogues de l'app.
    echap = page.evaluate("() => { ouvreCompteur(); return battementCompteur !== null; }")
    page.keyboard.press("Escape")
    page.wait_for_timeout(60)
    ferme = page.evaluate("""() => ({
        ouvert: document.getElementById('compteurOverlay').classList.contains('open'),
        bat: battementCompteur !== null})""")
    check(echap and not ferme["ouvert"],
          "[compteur] Échap le ferme, comme les autres dialogues")
    check(not ferme["bat"], "[compteur] et coupe l'intervalle aussi par ce chemin")
    # LE PALIER EST CALCULÉ UNE SEULE FOIS POUR L'EN-TÊTE ET LE PANNEAU.
    # Le seuil des 24 h était écrit deux fois ; rien n'empêchait la
    # disposition précise du panneau de basculer sans que l'en-tête vire
    # au rouge. Les bornes sont éprouvées des deux côtés.
    H = 3600 * 1000
    paliers = page.evaluate("""(h) => [
        palierCompteur(31 * 24 * h), palierCompteur(30 * 24 * h + 1),
        palierCompteur(30 * 24 * h - 1), palierCompteur(7 * 24 * h + 1),
        palierCompteur(7 * 24 * h - 1), palierCompteur(24 * h + 1),
        palierCompteur(24 * h - 1), palierCompteur(0)]""", H)
    check(paliers == ["", "", "soon", "soon", "urgent", "urgent",
                      "final", "final"],
          "[compteur] les quatre paliers basculent à 30 j, 7 j et 24 h, à "
          "la milliseconde près (%s)" % paliers)

    check(not err, "[compteur] aucune erreur JavaScript (%s)" % err[:1])
    ctx.close()

    # LA CIBLE TACTILE, des deux côtés. Vers le bas, un bouton de l'en-tête
    # commence 14 px plus bas à 390 px : la zone ne doit PAS l'atteindre.
    for largeur in (320, 390):
        ctx = nav.new_context(viewport={"width": largeur, "height": 850})
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        page.wait_for_selector("#feed", state="attached")
        g = page.evaluate("""() => {
            const b = document.getElementById('countdown');
            // Depuis la tuile du 07/11/2026, le bouton est positionné « static » : son
            // ::after épouse la TUILE (inset:0), donc la zone de clic est la tuile entière.
            const tuile = getComputedStyle(b).position === 'static' ? b.closest('.tuile-compteur') : null;
            const r = (tuile || b).getBoundingClientRect();
            const a = getComputedStyle(b, '::after');
            const haut = tuile ? 0 : Math.abs(parseFloat(a.top) || 0);
            const bas = tuile ? 0 : Math.abs(parseFloat(a.bottom) || 0);
            let plusProche = 9999;
            for(const e of document.querySelectorAll('button,a,input')){
              if(e === b) continue;
              const q = e.getBoundingClientRect();
              if(q.width === 0) continue;
              if(q.left < r.right + 8 && q.right > r.left - 8 && q.top >= r.bottom){
                plusProche = Math.min(plusProche, q.top - r.bottom);
              }
            }
            return {tag: b.tagName, zone: r.height + haut + bas, bas, plusProche};
        }""")
        check(g["tag"] == "BUTTON",
              "[%d px] le compteur est un vrai bouton (%s)" % (largeur, g["tag"]))
        check(g["zone"] >= CIBLE_MINIMALE,
              "[%d px] sa zone fait au moins %d px (%.0f)"
              % (largeur, CIBLE_MINIMALE, g["zone"]))
        check(g["bas"] < g["plusProche"],
              "[%d px] et elle ne mord pas sur le bouton du dessous "
              "(%.0f px d'extension pour %.0f px d'écart)"
              % (largeur, g["bas"], g["plusProche"]))
        ctx.close()

    # TOUT SUR UNE LIGNE, ET LA LIGNE NE BOUGE PLUS.
    #
    # Deux défauts signalés par Antoni sur la même capture : en trois
    # colonnes, six unités se rangeaient en 3 + 2 ; et comme les unités à
    # zéro étaient masquées, la disposition changeait à chaque fois qu'une
    # d'elles tombait. Les six sont maintenant toujours là, grisées quand
    # elles valent zéro, sur une seule rangée.
    #
    # Trois instants, dont deux encadrent la bascule de taille. Paris est
    # à UTC+1 à cette date : la sortie tombe le 18 à 23:00 UTC. Cinq
    # minutes de marge de part et d'autre, pas une : l'horloge installée
    # avance pendant que la page charge.
    #
    # Le nombre de tuiles n'est plus six partout : sous 24 h, les unités
    # de tête à zéro s'effacent (voir plus bas). L'invariant de cette
    # boucle-ci est la LIGNE UNIQUE, pas le compte.
    QUATRE = ["jours", "heures", "min", "sec"]
    for utc, precis, libelle, attendues in (
            ("2026-11-17T22:55:00Z", False, "T-24 h 05", QUATRE),
            ("2026-11-17T23:05:00Z", True,  "T-23 h 55", QUATRE),
            ("2026-09-10T13:34:21Z", False, "quatre unités", QUATRE),
    ):
        ctx = nav.new_context(viewport={"width": 320, "height": 850},
                              timezone_id="Europe/Paris")
        ctx.clock.install(time=utc)
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        page.wait_for_selector("#feed", state="attached")
        vue = page.evaluate("""() => {
            ouvreCompteur();
            const c = document.getElementById('compteurContenu');
            const d = c.querySelector('.tuile-panneau .countdown');
            const cases = [...d.children];
            // UNE SEULE RANGÉE : toutes les tuiles partagent le même
            // haut. C'est la traduction directe de « tout tient sur une
            // ligne », et c'est précisément ce qui était cassé.
            const hauts = new Set(cases.map(e => Math.round(e.getBoundingClientRect().top)));
            // Le débordement se mesure GÉOMÉTRIQUEMENT : des colonnes
            // 1fr se serrent jusqu'à leur contenu minimum, si bien
            // qu'une grille trop étroite sort du conteneur SANS que son
            // scrollWidth bouge d'un pixel.
            // SUR CHAQUE TUILE, et c'est la troisième fois que cette
            // géométrie me prend : la GRILLE est un bloc, sa largeur est
            // contrainte par le conteneur et elle ne déborde jamais.
            // Mesuré avec quatre colonnes d'équivalences : les pistes
            // totalisent 299 px dans une boîte qui en mesure 246, et ce
            // sont les tuiles qui sortent, pas la boîte. Son scrollWidth
            // ne bouge pas davantage.
            const boite = c.getBoundingClientRect();
            let sort = null, coupe = null;
            for(const t of c.querySelectorAll('.tuile-panneau .cd-t, .compteur-tuiles > div')){
              const q = t.getBoundingClientRect();
              if(q.right > boite.right + 0.5 || q.left < boite.left - 0.5)
                sort = t.textContent + ' (bord droit ' + Math.round(q.right)
                       + ' px pour ' + Math.round(boite.right) + ')';
            }
            // Seuils par rôle : le libellé est à 11 px (une ligne ~14 px,
            // deux ~28), le nombre monte à 28 px en disposition précise
            // (une ligne ~31, deux ~62).
            for(const e of c.querySelectorAll('.tuile-panneau .cd-t i, .tuile-panneau .cd-t b, .compteur-tuiles span, .compteur-tuiles b')){
              const h = e.getBoundingClientRect().height;
              const max = e.tagName === 'B' ? 45 : 22;
              if(h > max) coupe = e.textContent + ' (' + Math.round(h) + ' px)';
            }
            const equiv = c.querySelectorAll('.compteur-tuiles.equiv > div').length;
            const barre = c.querySelector('.compteur-barre');
            // SUR LE TEXTE LUI-MÊME, par un Range, et pas sur la boîte du
            // <i> : il est en inset:0, donc sa boîte ÉPOUSE la barre par
            // construction et la comparer à la barre ne prouve rien.
            const noeud = barre.querySelector('i');
            const trait = document.createRange();
            trait.selectNodeContents(noeud);
            const chiffre = trait.getBoundingClientRect();
            const rb = barre.getBoundingClientRect();
            const dodos = c.querySelector('.compteur-dodos');
            const entete = document.getElementById('countdown');
            const r = {
              tuiles: cases.length,
              rangees: hauts.size,
              classe: d.className,
              // LE MÊME PALIER DES DEUX CÔTÉS. C'est tout l'objet du
              // calcul partagé : le panneau ne peut plus passer en
              // rouge pendant que l'en-tête reste bleue.
              palierEntete: entete ? entete.className : null,
              // La teinte réellement rendue sur un gros chiffre vivant
              // (pas une tuile à zéro, qui garde son gris).
              teinte: (() => {
                const vivant = [...cases].find(c => !c.classList.contains('zero'));
                if(!vivant) return null;
                // En thème CLAIR, la teinte du palier est le FOND du chiffre
                // (surligneur fluo, texte foncé) ; en sombre, c'est le texte.
                const csb = getComputedStyle(vivant.querySelector('b'));
                const rendue = csb.color;
                // La teinte ATTENDUE, résolue depuis le jeton : comparer
                // à une chaîne écrite en dur dans le test figerait la
                // couleur en double, ici et dans la feuille de style.
                const pal = ['soon','urgent','final']
                              .find(x => d.classList.contains(x));
                let attendue = null;
                if(pal){
                  const sonde = document.createElement('span');
                  sonde.style.color = 'var(--compte-' + pal + ')';
                  document.body.appendChild(sonde);
                  attendue = getComputedStyle(sonde).color;
                  sonde.remove();
                }
                return {rendue: rendue, attendue: attendue, palier: pal || ""};
              })(),
              zeros: cases.filter(e => e.classList.contains('zero'))
                          .map(e => e.querySelector('i').textContent),
              // Les tuiles dont le NOMBRE vaut zéro, lues à l'écran : la
              // marque doit coller à la valeur, et c'est le genre de
              // lien qui casse en silence (un `=== 0` changé en test de
              // vérité, un nombre devenu chaîne).
              nuls: cases.filter(e => e.querySelector('b').textContent === '00')
                         .map(e => e.querySelector('i').textContent),
              unites: [...d.querySelectorAll('i')].map(e => e.textContent),
              // L'ORDRE dans le document : le dodo doit venir APRÈS la
              // barre. DOCUMENT_POSITION_FOLLOWING vaut 4.
              dodoApresBarre: dodos
                ? !!(barre.compareDocumentPosition(dodos) & 4) : null,
              coupe: coupe, sort: sort, equiv: equiv,
              deborde: c.scrollWidth > c.clientWidth + 1,
              chiffreDedans: chiffre.right <= rb.right + 0.5
                             && chiffre.left >= rb.left - 0.5
                             && chiffre.top >= rb.top - 0.5
                             && chiffre.bottom <= rb.bottom + 0.5,
            };
            fermeCompteur();
            return r;
        }""")
        check(vue["tuiles"] == len(attendues),
              "[compteur %s] les unités attendues sont là, même à zéro — "
              "les masquer redisposait la ligne à chaque unité qui tombe "
              "(%s pour %s)" % (libelle, vue["tuiles"], len(attendues)))
        check(vue["rangees"] == 1,
              "[compteur %s] et elles tiennent toutes sur UNE rangée à "
              "320 px (%s rangée(s))" % (libelle, vue["rangees"]))
        check(vue["unites"] == attendues,
              "[compteur %s] les libellés de l'en-tête, jours, heures, min, sec (%s)"
              % (libelle, vue["unites"]))
        # Le palier que porte le panneau doit être celui de l'en-tête.
        pal_panneau = [m for m in ("soon", "urgent", "final")
                       if m in (vue["classe"] or "")]
        pal_entete = [m for m in ("soon", "urgent", "final")
                      if m in (vue["palierEntete"] or "")]
        check(pal_panneau == pal_entete,
              "[compteur %s] le panneau et l'en-tête annoncent le même "
              "palier (panneau %s, en-tête %s)"
              % (libelle, pal_panneau or ["aucun"], pal_entete or ["aucun"]))
        t = vue["teinte"] or {}
        check(not t.get("palier") or t.get("rendue") == t.get("attendue"),
              "[compteur %s] et les gros chiffres portent la teinte du "
              "palier « %s » (rendue %s, attendue %s)"
              % (libelle, t.get("palier"), t.get("rendue"), t.get("attendue")))
        check(vue["coupe"] is None,
              "[compteur %s] aucun libellé ne passe à la ligne — c'est "
              "tout l'intérêt des tuiles (« %s »)" % (libelle, vue["coupe"]))
        check(not vue["deborde"] and vue["sort"] is None,
              "[compteur %s] et rien ne déborde (panneau %s, grille "
              "sortie : %s)" % (libelle, "débordé" if vue["deborde"] else "net",
                                vue["sort"]))
        check(vue["chiffreDedans"],
              "[compteur %s] le pourcentage reste dans la barre" % libelle)
        check(vue["equiv"] == 4,
              "[compteur %s] quatre équivalences, secondes comprises (%s)"
              % (libelle, vue["equiv"]))

        check(vue["dodoApresBarre"] is True,
              "[compteur %s] et le dodo est SOUS la barre — « juste un truc "
              "en plus » (%s)" % (libelle, vue["dodoApresBarre"]))
        # Sur l'ÉGALITÉ des deux listes, et non sur une valeur devinée :
        # mon premier jet attendait « mois, sem » à T-24 h 05 et ignorait
        # que les heures y sont à zéro aussi. Le vrai invariant est que la
        # marque suit la valeur, quelle que soit l'heure.
        # Seule la tuile des JOURS se grise (comme dans l'en-tête) : un 00 d'heures ou de
        # minutes reste un chiffre vivant, pas un zéro de tête.
        check(vue["zeros"] == [u for u in vue["nuls"] if u == "jours"],
              "[compteur %s] la tuile des jours est grisée exactement quand elle vaut zéro "
              "(grisées %s, nulles %s)"
              % (libelle, vue["zeros"], vue["nuls"]))
        if libelle == "quatre unités":
            check(vue["nuls"] == [],
                  "[compteur %s] et cet instant-là n'en a aucune : les quatre "
                  "unités valent quelque chose (%s)" % (libelle, vue["nuls"]))
        ctx.close()

    # LES DEUX EXTRÊMES, AU MÊME INSTANT. Deux jours après l'annonce, la
    # barre est à 0,1 % — le pourcentage, aligné sur la fin du
    # remplissage, sortirait de l'écran ; il est donc posé par-dessus,
    # calé à droite.
    #
    # Et c'est AUSSI le pire cas des équivalences : 1 748 jours de la
    # sortie, donc 151 millions de secondes, onze caractères. Les trois
    # instants d'au-dessus sont tous à moins de 70 jours et n'exercent
    # rien — mesuré, quatre colonnes y tiennent encore, parce qu'une
    # colonne 1fr s'élargit à son contenu plutôt que de le rogner.
    ctx = nav.new_context(viewport={"width": 320, "height": 850},
                          timezone_id="Europe/Paris")
    ctx.clock.install(time="2022-02-06T12:00:00Z")
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    vide = page.evaluate("""() => {
        ouvreCompteur();
        const b = document.querySelector('.compteur-barre');
        const i = b.querySelector('i');
        const c = document.getElementById('compteurContenu');
        const g = c.querySelector('.compteur-tuiles.equiv');
        const boite = c.getBoundingClientRect();
        let serre = null;
        for(const t of g.children){
          const q = t.getBoundingClientRect();
          if(q.right > boite.right + 0.5 || q.left < boite.left - 0.5)
            serre = t.textContent + ' (bord droit ' + Math.round(q.right)
                    + ' px pour ' + Math.round(boite.right) + ')';
        }
        const r = {part: parseFloat(b.querySelector(':scope > span').style.width),
                   texte: i.textContent,
                   secondes: g.lastElementChild.querySelector('b').textContent,
                   serre: serre,
                   dedans: i.getBoundingClientRect().right <= b.getBoundingClientRect().right + 0.5};
        fermeCompteur();
        return r;
    }""")
    check(vide["part"] < 1,
          "[compteur] deux jours après l'annonce la barre est quasi vide "
          "(%s %%)" % vide["part"])
    check(vide["dedans"] and "%" in vide["texte"],
          "[compteur] et son pourcentage tient quand même dedans "
          "(« %s »)" % vide["texte"])
    check(len(vide["secondes"]) >= 10,
          "[compteur] cet instant exerce bien le pire cas : le total en "
          "secondes y fait onze caractères (« %s »)" % vide["secondes"])
    check(vide["serre"] is None,
          "[compteur] et aucune équivalence ne sort du panneau (%s)"
          % vide["serre"])
    ctx.close()

    # LE POURCENTAGE DOIT ÊTRE LU, PAS SEULEMENT ÊTRE LÀ.
    #
    # Écrit en --text, il tombait à 2,43:1 sur le remplissage en thème
    # clair et 1,78:1 en sombre. Il était bien dans la barre depuis le
    # début, et Antoni l'a signalé comme disparu — ce qui est le même
    # résultat. Deux exemplaires superposés : celui du dessous en --text
    # pour la piste, celui du dessus rogné par le remplissage, en
    # --accent-contrast.
    #
    # Le contrôle calcule le VRAI rapport de contraste à partir des
    # couleurs calculées, dans les deux thèmes. Un jeton changé sans
    # vérifier le fait tomber.
    for theme in ("light", "dark"):
        ctx = nav.new_context(viewport={"width": 320, "height": 850},
                              timezone_id="Europe/Paris")
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        page.wait_for_selector("#feed", state="attached")
        lis = page.evaluate("""(theme) => {
            document.documentElement.setAttribute('data-theme', theme);
            ouvreCompteur();
            const b = document.querySelector('.compteur-barre');
            const rempli = b.querySelector(':scope > span');
            const dessous = b.querySelector(':scope > i');
            const dessus = rempli.querySelector('i');
            const lin = (c) => { c /= 255;
              return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
            const Y = (css) => { const [r,g,bb] = css.match(/\\d+/g).map(Number);
              return 0.2126*lin(r) + 0.7152*lin(g) + 0.0722*lin(bb); };
            const rapport = (a, c) => { const ya = Y(a), yc = Y(c);
              return (Math.max(ya,yc) + 0.05) / (Math.min(ya,yc) + 0.05); };
            const cs = getComputedStyle;
            const r = {
              haut: b.getBoundingClientRect().height,
              corps: parseFloat(cs(dessus).fontSize),
              surRemplissage: rapport(cs(dessus).color, cs(rempli).backgroundColor),
              surPiste: rapport(cs(dessous).color, cs(b).backgroundColor),
              memeTexte: dessous.textContent === dessus.textContent,
              // Les deux exemplaires doivent se superposer AU PIXEL : le
              // chiffre est à cheval sur la limite du remplissage entre
              // 83 et 97 %, et un décalage s'y verrait en double.
              ecart: Math.abs(dessus.getBoundingClientRect().right
                              - dessous.getBoundingClientRect().right),
            };
            fermeCompteur();
            return r;
        }""", theme)
        check(lis["haut"] >= 24,
              "[compteur %s] la barre fait au moins 24 px de haut (%.0f)"
              % (theme, lis["haut"]))
        check(lis["corps"] >= 12,
              "[compteur %s] et son chiffre au moins 12 px (%.0f)"
              % (theme, lis["corps"]))
        check(lis["surRemplissage"] >= 4.5,
              "[compteur %s] le pourcentage est LISIBLE sur le remplissage "
              "— c'est --accent-contrast qui sert, pas --text (%.2f:1)"
              % (theme, lis["surRemplissage"]))
        check(lis["surPiste"] >= 4.5,
              "[compteur %s] et lisible aussi sur la piste, quand la barre "
              "est loin du bord (%.2f:1)" % (theme, lis["surPiste"]))
        check(lis["memeTexte"] and lis["ecart"] < 1,
              "[compteur %s] les deux exemplaires portent le même texte et "
              "se superposent (écart %.1f px)" % (theme, lis["ecart"]))
        ctx.close()

    # APRÈS LA SORTIE, le panneau compte dans l'autre sens.
    ctx = nav.new_context(viewport={"width": 390, "height": 850},
                          timezone_id="Europe/Paris")
    ctx.clock.install(time="2026-11-22T08:00:00Z")
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    apres = page.evaluate("""() => {
        ouvreCompteur();
        const t = document.getElementById('compteurContenu').textContent;
        const r = {dodos: /dodo/.test(t),
                   titre: document.getElementById('titreCompteur').textContent,
                   note: document.querySelector('.compteur-note').textContent,
                   tuiles: document.querySelectorAll(
                     '#compteurContenu .tuile-panneau .cd-t').length,
                   barres: document.querySelectorAll('#compteurContenu .compteur-barre').length,
                   // Dans une tuile, textContent colle le nombre à son
                   // unité : « 3jours », sans espace.
                   jours: (t.match(/(\d[\d\s ]*)jours/) || [])[1]};
        fermeCompteur();
        return r;
    }""")
    check(not apres["dodos"],
          "[compteur] après la sortie, la ligne des dodos disparaît — "
          "compter des nuits passées n'a aucun sens")
    check(apres["titre"] == "Depuis la sortie",
          "[compteur] après le 19 novembre il s'intitule « Depuis la "
          "sortie » (« %s »)" % apres["titre"])
    check(int((apres["jours"] or "-1").strip()) == 3,
          "[compteur] et il compte à l'envers (%s jours)" % apres["jours"])
    # PLUS DE BARRE après la sortie (Antoni, 07/11/2026) : elle allait de l'annonce à la sortie,
    # donc elle restait pleine pour toujours.
    check(apres["barres"] == 0,
          "[compteur] après la sortie, la barre de progression a disparu (%s)" % apres["barres"])
    # Et la note dit ce que compte le compteur, en une ligne.
    check(apres["note"].strip() == "Temps écoulé depuis la sortie du jeu, le 19/11/26",
          "[compteur] la note explique le compteur : « %s »" % apres["note"].strip())
    # Et le découpage garde ses QUATRE tuiles une semaine après, comme l'en-tête.
    check(apres["tuiles"] == 4,
          "[compteur] une semaine après, les quatre tuiles sont là (%s)" % apres["tuiles"])
    ctx.close()

    # LES QUATRE TUILES, MÊME DANS LES DERNIÈRES 24 HEURES (07/11/2026). Il n'y a plus de coupe
    # des zéros de tête : comme l'en-tête, le panneau garde jours, heures, min, sec, et grise la
    # tuile des jours quand elle vaut 00. Trois instants autour de minuit (la coupe valait des
    # deux côtés) et, après la sortie, un nombre de jours à TROIS chiffres.
    QUATRE_LIB = ["jours", "heures", "min", "sec"]
    for utc, libelle, grises, jours, libelles in (
            ("2026-11-18T22:30:00Z", "T-30 min", ["jours"], "00", QUATRE_LIB),
            ("2026-11-18T23:30:00Z", "T+30 min", ["jours"], "00", QUATRE_LIB),
            ("2026-11-17T22:55:00Z", "T-24 h 05", [], "01", QUATRE_LIB),
            ("2026-12-24T08:30:00Z", "+1 mois (5 tuiles)", [], "01", ["mois", "j", "h", "min", "s"]),
            ("2027-03-10T12:00:00Z", "+3 mois (5 tuiles)", [], "03", ["mois", "j", "h", "min", "s"]),
            ("2028-02-02T17:45:00Z", "+1 an 2 mois (6 tuiles)", [], "01", ["an", "mois", "j", "h", "min", "s"]),
    ):
        ctx = nav.new_context(viewport={"width": 320, "height": 850},
                              timezone_id="Europe/Paris")
        ctx.clock.install(time=utc)
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        page.wait_for_selector("#feed", state="attached")
        t_ = page.evaluate("""() => {
            ouvreCompteur();
            const c = document.getElementById('compteurContenu');
            const d = c.querySelector('.tuile-panneau .countdown');
            const cases = [...d.children];
            const boite = c.getBoundingClientRect();
            let sort = null;
            for(const e of c.querySelectorAll('.tuile-panneau .cd-t, .compteur-tuiles > div')){
              const q = e.getBoundingClientRect();
              if(q.right > boite.right + 0.5 || q.left < boite.left - 0.5)
                sort = e.textContent + ' (bord droit ' + Math.round(q.right) + ' px pour ' + Math.round(boite.right) + ')';
            }
            // Le chiffre ET le libellé doivent tenir DANS leur tuile, même à six tuiles sur 320 px.
            let dehors = null;
            for(const e of cases){
              for(const noeud of [e.querySelector('b'), e.querySelector('i')]){
                const r = document.createRange(); r.selectNodeContents(noeud);
                const q = r.getBoundingClientRect(), tq = e.getBoundingClientRect();
                if(q.right > tq.right + 0.5 || q.left < tq.left - 0.5) dehors = noeud.textContent + ' sort de sa tuile';
              }
            }
            const r = {
              unites: cases.map(e => e.querySelector('i').textContent),
              premier: cases[0].querySelector('b').textContent,
              grises: cases.filter(e => e.classList.contains('zero')).map(e => e.querySelector('i').textContent),
              rangees: new Set(cases.map(e => Math.round(e.getBoundingClientRect().top))).size,
              grilles: c.querySelectorAll('.tuile-panneau .countdown, .compteur-tuiles').length,
              phrase: d.getAttribute('aria-label'),
              sort: sort, dehors: dehors,
            };
            fermeCompteur();
            return r;
        }""")
        check(t_["unites"] == libelles,
              "[compteur %s] les tuiles attendues sont là (%s)" % (libelle, t_["unites"]))
        check(t_["premier"] == jours and t_["grises"] == grises,
              "[compteur %s] la première tuile vaut « %s » et n'est grisée que si elle vaut 00 (%s)"
              % (libelle, jours, t_["grises"]))
        check(t_["rangees"] == 1 and t_["sort"] is None and t_["dehors"] is None,
              "[compteur %s] une rangée à 320 px, rien ne sort (%s, %s)" % (libelle, t_["sort"], t_["dehors"]))
        check(t_["grilles"] == 2,
              "[compteur %s] les équivalences restent, elles (%s grille(s))" % (libelle, t_["grilles"]))
        check(not re.search(r"(?:^|· )0 ", t_["phrase"] or ""),
              "[compteur %s] et l'étiquette parlée n'a jamais porté de zéro (« %s »)" % (libelle, t_["phrase"]))
        ctx.close()



# Le seuil WCAG AA pour du texte ordinaire. Le « grand texte » (au moins
# 24 px, ou 18,66 px en gras) se contente de 3:1 — la norme le dit, et un
# contrôle plus sévère que la norme finirait par être désactivé.
CONTRASTE_MINIMAL = 4.5
CONTRASTE_GRAND_TEXTE = 3.0

# LES EXCEPTIONS SONT NOMMÉES, UNE PAR UNE, AVEC LEUR MESURE.
#
# (Les tuiles à zéro du panneau du compteur, effacées à 0,45 d'opacité, ont été
# retirées de cette liste le 07/11/2026 : la tuile des jours à 00 est maintenant grisée
# sans opacité, au contraste de --text-dim, comme dans l'en-tête.)
#
# Une exception qui ne désigne plus rien est une exception pourrie : le
# contrôle vérifie que chacune correspond encore à au moins un élément.
EXCEPTIONS_CONTRASTE = {
    # L'INTÉRIEUR D'UNE CARTE LUE. Elles sont estompées à 0,70, ce qui
    # met tout leur contenu entre 2,98 et 3,57:1 ; Antoni a tranché le
    # 16/10/2026 que c'est de l'accessoire sur un article déjà lu.
    #
    # UNE SEULE RÈGLE STRUCTURELLE, et c'est une correction. La première
    # version énumérait les éléments QUE LE RELEVÉ DU JOUR AVAIT
    # MONTRÉS — date, source, étoile, boutons, badges. Le 19/10, un
    # article lu portait pour la première fois une source supplémentaire
    # (« + 1 autre source »), absente de la liste : le contrôle est passé
    # au rouge et a bloqué trois pull requests Dependabot. Une liste tirée
    # d'un échantillon n'est pas une liste de ce que la structure peut
    # contenir.
    #
    # Le TITRE, lui, n'est pas couvert par cette exception : il est
    # vérifié à part, plus bas, parce que c'est la seule chose qu'on
    # relit dans un article déjà lu.
    ".card.read *": "2,98 à 3,57:1 — intérieur d'un article lu (16/10)",
}

# Le titre d'un article lu, lui, DOIT rester lisible : c'est pour lui que
# l'estompage est passé de 0,45 à 0,70 le 16/10 (2,80:1 -> 6,05:1).
# L'exception ci-dessus couvre toute la carte, donc ce seuil-là est
# vérifié par un contrôle à part plutôt que par le balayage.
TITRE_LU_MINIMAL = 4.5

# Le plancher d'éléments examinés, par thème. Mesuré, puis arrondi en
# dessous. Sans lui, un balayage qui ne balaierait plus rien — un sélecteur
# cassé, un panneau qui ne s'ouvre plus — passerait pour un sans-faute.
PLANCHER_EXAMINES = 900

SONDE_CONTRASTE = r"""(args) => {
  const [exceptions, seuil, seuilGrand] = args;
  const lin = (c) => { c /= 255;
    return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
  // DEUX ÉCRITURES, ET LA SECONDE M'A EU. Chromium rend un
  // color-mix() sous la forme « color(srgb 0.07 0.43 0.20 / 0.15) » :
  // des flottants de 0 à 1, pas des entiers sur 255. Lus comme du
  // rgb(), ils donnent du presque-noir — le badge vert de l'app était
  // ainsi rapporté à 4,38:1 sur un fond gris imaginaire, alors qu'il
  // est à 4,96:1 sur son vrai fond vert pâle. Un faux positif, pas un
  // défaut, et c'est la sonde qu'il fallait corriger.
  // TROISIÈME ÉCRITURE (11/11/2026) : un color-mix() à base de variables animées sort en « oklab(0.68 0.03 -0.08) »,
  // et lu comme du rgb() il donne du presque-noir (1,0,0) : 200 « textes sous le seuil » qui n'existaient pas. Toute
  // écriture qui n'est ni rgb() ni color() passe par un canvas, qui sait lire n'importe quelle couleur CSS.
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const g2 = cv.getContext('2d', {willReadFrequently: true});
  const parse = (css) => {
    const t = (css || '').trim();
    if(t && !t.startsWith('rgb') && !t.startsWith('color(') && t !== 'transparent'){
      g2.clearRect(0, 0, 1, 1); g2.fillStyle = '#000'; g2.fillStyle = t; g2.fillRect(0, 0, 1, 1);
      const d = g2.getImageData(0, 0, 1, 1).data;
      return {r: d[0], g: d[1], b: d[2], a: d[3] / 255};
    }
    const m = t.match(/[\d.]+(?:e-?\d+)?/g);
    if(!m || m.length < 3) return null;
    const srgb = t.startsWith('color(');
    const e = srgb ? 255 : 1;      // 0–1 pour color(), déjà 0–255 sinon
    const i = srgb ? 0 : 0;
    return {r: +m[i]*e, g: +m[i+1]*e, b: +m[i+2]*e,
            a: m.length > i+3 ? parseFloat(m[i+3]) : 1};
  };
  const Y = (c) => 0.2126*lin(c.r) + 0.7152*lin(c.g) + 0.0722*lin(c.b);
  const melange = (av, ar, a) => ({
    r: av.r*a + ar.r*(1-a), g: av.g*a + ar.g*(1-a), b: av.b*a + ar.b*(1-a), a: 1});

  // LE FOND EFFECTIF : on remonte les parents jusqu'au premier fond
  // opaque, en empilant les couches semi-transparentes au passage. Un
  // seul niveau ne suffit pas : la plupart des textes de l'app sont sur
  // un élément sans fond, posé sur un panneau posé sur la page.
  const fond = (el) => {
    const couches = [];
    let n = el;
    while(n && n.nodeType === 1){
      const c = parse(getComputedStyle(n).backgroundColor);
      if(c && c.a > 0){ couches.push(c); if(c.a >= 0.999) break; }
      n = n.parentElement;
    }
    if(!couches.length || couches[couches.length-1].a < 0.999)
      couches.push(parse(getComputedStyle(document.body).backgroundColor)
                   || {r:255, g:255, b:255, a:1});
    let res = couches.pop();
    while(couches.length){ const av = couches.pop(); res = melange(av, res, av.a); }
    return res;
  };

  // L'OPACITÉ HÉRITÉE, ET C'EST ELLE QUI M'AVAIT ÉCHAPPÉ. opacity ne
  // change pas la couleur calculée : un libellé déclaré à 5,53:1 se rend
  // à 1,92:1 sous opacity .45, et une sonde qui lit color sans la fondre
  // ne voit rien. On multiplie donc les opacités de toute la chaîne.
  const opaciteHeritee = (el) => {
    let o = 1, n = el;
    while(n && n.nodeType === 1){
      const v = parseFloat(getComputedStyle(n).opacity);
      if(!isNaN(v)) o *= v;
      n = n.parentElement;
    }
    return o;
  };

  const ecarts = [];
  let examines = 0;
  const vus = new Set();
  for(const el of document.querySelectorAll('body *')){
    const propre = [...el.childNodes]
      .filter(n => n.nodeType === 3 && n.textContent.trim())
      .map(n => n.textContent.trim()).join(' ');
    if(!propre) continue;
    const st = getComputedStyle(el);
    if(st.visibility === 'hidden' || st.display === 'none') continue;
    const r = el.getBoundingClientRect();
    if(r.width < 1 || r.height < 1) continue;
    const o = opaciteHeritee(el);
    if(o < 0.05) continue;           // invisible : rien à lire, rien à juger
    examines++;
    if(exceptions.some(s => el.matches(s))) continue;
    const brut = parse(st.color);
    if(!brut) continue;
    const ar = fond(el);
    const av = melange(brut, ar, brut.a * o);
    const ya = Y(av), yb = Y(ar);
    let ratio = (Math.max(ya, yb) + 0.05) / (Math.min(ya, yb) + 0.05);
    // UN TRAIT DE CONTOUR PORTE LA LISIBILITÉ (07/11/2026, compteur fluo en thème
    // clair : le jaune fluo fait 1,02:1 sur la tuile, mais il est cerné d'un trait
    // sombre de 1,5 px). Avec un trait opaque d'au moins 1 px, le texte se lit si le
    // remplissage tranche sur le trait ET si le trait tranche sur le fond : on retient
    // le plus faible de ces deux rapports, jamais le meilleur, et SEULEMENT s'il
    // dépasse celui du remplissage nu. Le seuil, lui, ne bouge pas.
    const lt = parseFloat(st.webkitTextStrokeWidth) || 0;
    const tc = lt >= 1 ? parse(st.webkitTextStrokeColor) : null;
    if(tc && tc.a >= 0.99){
      const yt = Y(tc);
      const c = (p, q) => (Math.max(p, q) + 0.05) / (Math.min(p, q) + 0.05);
      ratio = Math.max(ratio, Math.min(c(ya, yt), c(yt, yb)));
    }
    const px = parseFloat(st.fontSize);
    const gras = parseInt(st.fontWeight, 10) >= 700;
    const exige = (px >= 24 || (gras && px >= 18.66)) ? seuilGrand : seuil;
    if(ratio + 0.005 >= exige) continue;
    const ou = el.tagName.toLowerCase()
      + (el.className && typeof el.className === 'string'
         ? '.' + el.className.trim().split(/\s+/).join('.') : '');
    const cle = ou + '|' + propre.slice(0, 24);
    if(vus.has(cle)) continue;
    vus.add(cle);
    ecarts.push({ou: ou, texte: propre.slice(0, 32),
                 ratio: Math.round(ratio*100)/100, exige: exige, px: px,
                 couleur: `rgb(${Math.round(av.r)},${Math.round(av.g)},${Math.round(av.b)})`,
                 fond: `rgb(${Math.round(ar.r)},${Math.round(ar.g)},${Math.round(ar.b)})`});
  }
  // Les exceptions qui ne désignent plus rien sont signalées, pour
  // qu'elles ne survivent pas au code qu'elles couvraient.
  const mortes = exceptions.filter(s => !document.querySelector(s));
  return {examines: examines, ecarts: ecarts, mortes: mortes};
}"""

# Les surfaces, et comment on y arrive. Une seule largeur : la largeur ne
# change pas les couleurs, inutile de doubler la note.
SURFACES_CONTRASTE = (
    ("le fil",            "() => setTab('all')"),
    ("le compteur",       "() => ouvreCompteur()"),
    ("les filtres",       "() => openFiltersSheet()"),
    ("les réglages",      "() => openSettings()"),
    ("les informations",  "() => openInfo()"),
    ("stats · le fil",    "() => { setTab('stats'); ongletStatsChoisi('fil'); }"),
    ("stats · sources",   "() => ongletStatsChoisi('sources')"),
    ("stats · robot",     "() => ongletStatsChoisi('robot')"),
    ("stats · ma lecture","() => ongletStatsChoisi('moi')"),
    ("le journal",        "() => setTab('logs')"),
)

FERME_TOUT = """() => {
  for(const f of ['fermeCompteur', 'closeFiltersSheet', 'closeSettings',
                  'closeInfo', 'closePreview'])
    { try { if(typeof window[f] === 'function') window[f](); } catch(e) {} }
}"""


# Ce que pèse UNE unité de chaque réserve, mesuré le 20/10/2026 sur la
# sauvegarde réelle d'Antoni (7 548 liens lus, 2 000 entrées vues) et sur
# le fil du jour. Valeurs au NEUVIÈME DÉCILE : le garde ci-dessous juge le
# pire cas, pas le cas courant.
#
# La mesure d'origine du dépôt annonçait 300 octets par lien lu — un
# comptage en UTF-16 qui ne correspond pas à ce que pèse la chaîne
# stockée. Le vrai chiffre est 149.
OCTETS_PAR_LIEN_LU = 149
OCTETS_PAR_ENTREE_VUE = 414      # une entrée porte tout le résumé de l'article
OCTETS_PAR_ARTICLE_EN_CACHE = 1145
QUOTA_LOCALSTORAGE = 5 * 1024 * 1024


def test_budget_du_stockage(nav, url):
    """Les trois plafonds, ensemble, tiennent sous le quota du navigateur.

    POURQUOI CE GARDE EXISTE. Le 20/10/2026, deux des trois plafonds ont
    été relevés le même jour — les liens lus de 8 000 à 20 000, les
    articles déjà vus de 2 000 à 4 000. Chacun pris seul semblait tenir.
    Ensemble, au pire cas, ils arrivent à 4,73 Mo sur les 5 que le
    navigateur accorde : 0,27 Mo de marge.

    Et 5 000 entrées vues, qui avait été demandé d'abord, DÉPASSAIT —
    5,12 Mo. Ça n'a été vu qu'en additionnant les trois réserves, pas en
    regardant celle qu'on modifiait.

    Le commentaire « ne pas monter plus haut sans refaire la mesure » ne
    protège que celui qui le lit. Ce contrôle-ci le fait respecter.

    Il ne mesure pas le stockage réel : il applique aux constantes le
    coût unitaire mesuré sur des données réelles. Un plafond relevé sans
    remesurer le fait donc tomber, ce qui est exactement le but.
    """
    ctx = nav.new_context(viewport={"width": 390, "height": 800})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    bornes = page.evaluate("""() => ({
        lus: MAX_LIENS_LUS_PERSISTES,
        vus: MAX_SEEN_ENTRIES,
        cache: MAX_PERSISTED_ITEMS,
    })""")
    ctx.close()

    postes = (
        ("liens lus", bornes["lus"], OCTETS_PAR_LIEN_LU),
        ("articles déjà vus", bornes["vus"], OCTETS_PAR_ENTREE_VUE),
        ("fil en cache", bornes["cache"], OCTETS_PAR_ARTICLE_EN_CACHE),
    )
    total = sum(n * cout for _, n, cout in postes)
    detail = " + ".join("%s %d×%d" % (nom, n, c) for nom, n, c in postes)
    check(total < QUOTA_LOCALSTORAGE,
          "[budget] les trois plafonds tiennent sous les 5 Mo du "
          "navigateur, au pire cas — %.2f Mo (%s)"
          % (total / 1048576, detail))

    # Une marge nulle ne serait pas un garde : elle dirait « ça passe »
    # le jour où ça ne passe plus qu'à un octet près.
    marge = QUOTA_LOCALSTORAGE - total
    check(marge > 128 * 1024,
          "[budget] et il reste de quoi respirer — %.2f Mo de marge"
          % (marge / 1048576))


def test_le_service_worker_sans_push(nav, url):
    """Le service worker ne sait plus recevoir de push, et nettoie l'ancien (03/11/2026).

    Les notifications push sont retirées : le service worker ne garde que son rôle
    de coque hors-ligne. À son activation il ferme l'ancien abonnement s'il en
    reste un, et supprime les anciens caches, celui des réglages d'avant compris.
    Joué dans un navigateur sur le VRAI docs/sw.js, avec un `self` et des `caches`
    factices : on observe ce qu'il fait, pas ce qu'il dit.
    """
    print("\n[sw] plus de push, et le vieil abonnement est fermé")
    racine = os.path.dirname(os.path.abspath(__file__))
    source = open(os.path.join(racine, "docs", "sw.js"), encoding="utf-8").read()
    # Le nom du cache COURANT est lu dans sw.js : le recopier ici (« v66 ») a
    # fait échouer ce contrôle le 03/11/2026, quand le bump à v67 a fait de
    # l'ancien nom un cache à supprimer. Ce n'était pas un défaut du service worker.
    import re as _re
    courant = _re.search(r'CACHE_NAME\s*=\s*"([^"]+)"', source).group(1)
    ctx = nav.new_context()
    page = ctx.new_page()
    page.goto("about:blank")

    def joue(mode):
        return page.evaluate("""async ([src, mode, courant]) => {
            const ecouteurs = {}, journal = [], supprimes = [];
            const noms = ["gta6watch-shell-v0", "gta6watch-prefs", courant];
            const fauxCaches = {
              keys: async () => noms.slice(),
              delete: async (n) => { supprimes.push(n); return true; },
              open: async () => ({ add: async () => {}, put: async () => {} }),
              match: async () => undefined,
            };
            const abo = { unsubscribe: async () => { journal.push("désinscrit"); return true; } };
            const pushManager = mode === "absent" ? undefined : {
              getSubscription: async () => {
                if(mode === "echec") throw new Error("panne du navigateur");
                return mode === "aucun" ? null : abo;
              }
            };
            const fauxSelf = {
              addEventListener: (t, f) => { ecouteurs[t] = f; },
              skipWaiting: () => {},
              clients: { claim: async () => { journal.push("claim"); } },
              registration: { pushManager },
            };
            new Function("self", "caches", src)(fauxSelf, fauxCaches);
            let attente;
            ecouteurs.activate({ waitUntil: (p) => { attente = p; } });
            await attente;
            return { ecouteurs: Object.keys(ecouteurs).sort(), journal, supprimes };
        }""", [source, mode, courant])

    abo = joue("abonne")
    check(abo["ecouteurs"] == ["activate", "fetch", "install"],
          "le service worker n'écoute plus que install, activate et fetch (%s)" % abo["ecouteurs"])
    check("désinscrit" in abo["journal"] and abo["journal"][-1] == "claim",
          "à l'activation, l'ancien abonnement est fermé, PUIS le contrôle est repris (%s)" % abo["journal"])
    check(sorted(abo["supprimes"]) == ["gta6watch-prefs", "gta6watch-shell-v0"],
          "les anciens caches partent, celui des réglages d'avant compris, sans toucher au cache courant (%s)" % abo["supprimes"])
    check("désinscrit" not in joue("aucun")["journal"] and joue("aucun")["journal"] == ["claim"],
          "sans abonnement, rien à fermer et l'activation aboutit")
    check(joue("echec")["journal"] == ["claim"],
          "une panne en cherchant l'abonnement n'empêche jamais l'activation")
    check(joue("absent")["journal"] == ["claim"],
          "un navigateur sans gestionnaire de push non plus")
    ctx.close()


def test_contraste_partout(nav, url):
    """Aucun texte de l'app sous le seuil WCAG, dans les deux thèmes.

    POURQUOI CE CONTRÔLE EXISTE. Le 16/10/2026, le pourcentage de la barre
    du compteur était écrit en --text sur l'aplat --accent : 2,43:1 en
    thème clair, 1,78:1 en sombre. Il était là depuis le matin, il avait
    passé ma relecture, et c'est Antoni qui l'a signalé — en le croyant
    disparu, ce qui est le même résultat. Le jeton --accent-contrast
    existait déjà pour exactement ce cas.

    Écrire la correction ne suffisait pas : rien n'empêchait la suivante.
    Le balayage parcourt donc chaque élément porteur de texte de chaque
    surface, dans les deux thèmes, et calcule le VRAI rapport — fond
    effectif remonté jusqu'à la première couche opaque, opacité héritée
    fondue dans la couleur.

    Ce qu'il ne voit pas, et c'est écrit plutôt que caché : les états qui
    dépendent des données du jour (ligne d'incidents en rouge, badge
    d'erreur, passage en échec, états vides) n'apparaissent que si le fil
    les produit. D'où le plancher d'éléments examinés : un balayage qui
    cesserait de balayer serait indiscernable d'un sans-faute.
    """
    exceptions = list(EXCEPTIONS_CONTRASTE)
    base = url.rsplit("/", 1)[0]
    # UN SEUL CHARGEMENT POUR LES DEUX THÈMES. Charger le fil complet
    # (3 859 articles) est de loin le poste le plus cher du contrôle ; le
    # thème n'est qu'un attribut sur <html>. Mesuré : 77 s avec deux
    # contextes, et le fil chargé deux fois pour rien.
    ctx = nav.new_context(viewport={"width": 390, "height": 900},
                          timezone_id="Europe/Paris",
                          reduced_motion="reduce")
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    # ANIMATIONS COUPÉES, PAR LE CHEMIN DE L'APP ELLE-MÊME. Les cartes
    # entrent avec une animation d'opacité : mesurée en vol, une carte
    # lue était relevée à 0,71 puis 0,52 selon le thème, alors que son
    # estompage réel est 0,45. Le contrôle lisait donc des valeurs
    # transitoires et changeait d'avis d'une exécution à l'autre.
    # reduced_motion déclenche le @media (prefers-reduced-motion) que la
    # page gère déjà — on ne bricole pas le CSS depuis le test.
    # CHARGER LE VRAI FIL D'ABORD. L'app démarre sur « Clique sur
    # Actualiser » : sans ce chargement, le balayage n'examinait que
    # 16 éléments — l'en-tête et un état vide — et rendait un vert qui
    # ne prouvait rien. C'est le plancher d'éléments examinés qui l'a
    # attrapé, le premier jour de son existence.
    page.evaluate("settings.backendUrl = '%s/feed.json';" % base)
    page.evaluate("async () => { await checkFromBackend(false); }")
    page.wait_for_selector("#feed .card-actions", state="attached",
                           timeout=20000)
    # ATTENDRE LA FIN DU RATTRAPAGE. L'app affiche d'abord le fichier
    # allégé, puis l'historique complet arrive seul et remplace
    # lastItems. Marquer des articles lus pendant ce remplacement donne
    # un readSet qui ne correspond à aucune carte affichée, et plus
    # aucune .card.read : le contrôle tombait alors en attente, une fois
    # sur quelques-unes. historyPartial est le signal de l'app elle-même.
    page.wait_for_function("historyPartial === false", timeout=30000)
    # DES ARTICLES LUS DANS LE LOT, sinon .card.read n'existe jamais.
    # Un profil de test est neuf : rien n'y est lu, et l'estompage à
    # 45 % des cartes lues n'était couvert par rien. Il a d'abord fallu
    # s'en apercevoir : les cartes que le balayage relevait comme
    # estompées étaient en fait des cartes en pleine animation
    # d'entrée, pas des cartes lues.
    page.evaluate("""() => {
        readSet = new Set(lastItems.slice(0, 12).map(i => i.link));
        applyFilters();
    }""")
    page.wait_for_selector(".card.read", state="attached", timeout=10000)
    for theme in ("light", "dark"):
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)",
                      theme)
        total = 0
        tous = []
        mortes = None
        for nom, ouvre in SURFACES_CONTRASTE:
            page.evaluate(FERME_TOUT)
            try:
                page.evaluate(ouvre)
            except Exception as e:
                check(False, "[contraste %s] la surface « %s » ne s'ouvre plus "
                             "(%s)" % (theme, nom, str(e).split("\n")[0][:70]))
                continue
            page.wait_for_timeout(250)
            vu = page.evaluate(SONDE_CONTRASTE,
                               [exceptions, CONTRASTE_MINIMAL, CONTRASTE_GRAND_TEXTE])
            total += vu["examines"]
            mortes = vu["mortes"]
            # DEUX RELEVÉS, ET ON NE GARDE QUE CE QUI TIENT AUX DEUX.
            #
            # La CI a rapporté un onglet à 4,10:1 que la même exécution en
            # local ne voyait pas. En remontant le calcul, l'élément était
            # lu à 0,89 d'opacité : une apparition saisie en vol, pas une
            # couleur mal choisie. Plutôt que de chasser l'animation
            # responsable — quatre fois aujourd'hui, le fautif était
            # l'instrument et pas le défaut — le balayage confirme : un
            # écart qui disparaît tout seul n'en était pas un, et un vrai
            # défaut de contraste est immobile par nature.
            if vu["ecarts"]:
                page.wait_for_timeout(500)
                encore = page.evaluate(
                    SONDE_CONTRASTE,
                    [exceptions, CONTRASTE_MINIMAL, CONTRASTE_GRAND_TEXTE])
                confirmes = {(e["ou"], e["texte"]) for e in encore["ecarts"]}
                vu["ecarts"] = [e for e in vu["ecarts"]
                                if (e["ou"], e["texte"]) in confirmes]
            for e in vu["ecarts"]:
                tous.append("%s · %s — %.2f:1 (exigé %.1f) sur %s, %s sur %s"
                            % (nom, e["ou"][:40], e["ratio"], e["exige"],
                               repr(e["texte"])[:30], e["couleur"], e["fond"]))
        check(not tous,
              "[contraste %s] %d texte(s) sous le seuil WCAG : %s"
              % (theme, len(tous), " | ".join(tous[:4])))
        check(total >= PLANCHER_EXAMINES,
              "[contraste %s] le balayage a bien balayé — %d éléments "
              "textuels examinés, plancher %d"
              % (theme, total, PLANCHER_EXAMINES))
        check(not mortes,
              "[contraste %s] aucune exception ne couvre du code disparu "
              "(%s)" % (theme, mortes))

        # LE TITRE D'UN ARTICLE LU, vérifié à part. L'exception
        # ci-dessus couvre tout l'intérieur d'une carte lue, titre
        # compris : sans ce contrôle, revenir à un estompage de 0,45 —
        # qui mettait le titre à 2,80:1 — passerait inaperçu. C'est
        # exactement le défaut pour lequel l'estompage a été relevé.
        page.evaluate("() => setTab('all')")
        titre = page.evaluate("""() => {
            const t = document.querySelector('.card.read .card-title a')
                   || document.querySelector('.card.read .card-title');
            if(!t) return null;
            const lin = (c) => { c /= 255;
              return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
            const p = (css) => (css.match(/[\\d.]+/g) || []).map(Number);
            const Y = (c) => 0.2126*lin(c[0]) + 0.7152*lin(c[1]) + 0.0722*lin(c[2]);
            let o = 1, n = t;
            while(n && n.nodeType === 1){
              const v = parseFloat(getComputedStyle(n).opacity);
              if(!isNaN(v)) o *= v;
              n = n.parentElement;
            }
            const av = p(getComputedStyle(t).color).slice(0, 3);
            const ar = p(getComputedStyle(document.querySelector('.card.read'))
                         .backgroundColor).slice(0, 3);
            const m = av.map((c, i) => c*o + ar[i]*(1-o));
            const ya = Y(m), yb = Y(ar);
            return {ratio: (Math.max(ya,yb)+0.05) / (Math.min(ya,yb)+0.05),
                    texte: t.textContent.trim().slice(0, 30), opacite: o};
        }""")
        detail = ("%.2f:1 à %.2f d'opacité, « %s »"
                  % (titre["ratio"], titre["opacite"], titre["texte"])
                  ) if titre else "aucune carte lue trouvée"
        check(titre is not None and titre["ratio"] >= TITRE_LU_MINIMAL,
              "[contraste %s] le titre d'un article LU reste lisible — "
              "l'estompage ne doit pas l'emporter (%s)" % (theme, detail))
    ctx.close()


def test_mettre_de_cote(nav, url):
    """Une étoile pour garder un article, et le plafond qui va avec.

    OÙ ELLE EST, ET POURQUOI PAS AILLEURS. La rangée du bas porte déjà
    quatre boutons qui se partagent la colonne de texte — 58 px chacun à
    320 px, mesuré ici. Un cinquième les ramènerait à ~45 px : au-dessus
    des 44 px réglementaires, mais tout juste, et le contrôle tactile
    existant ne surveille pas ces boutons-là (il ne scanne que le panneau
    des réglages). L'étoile va donc sur la ligne de source, dont le côté
    droit est vide, et les quatre du bas ne bougent pas d'un pixel.

    SA ZONE FAIT 44 px SANS RIEN FAIRE, et c'est le sujet du contrôle
    ci-dessous : `button { min-height:44px }` est le défaut de l'app, posé
    exprès pour qu'un bouton neuf soit conforme sans qu'on y pense. Un
    ::after avait d'abord été ajouté par réflexe — il portait la cible à
    72 px, un rectangle invisible qui débordait sur le titre et lui aurait
    volé des clics. Retiré. Ce contrôle garde les deux bouts : au moins
    44 px, et pas de débordement qui mange le voisin.
    """
    print("\n[étoile] mettre un article de côté")
    for largeur in (320, 390):
        ctx = nav.new_context(viewport={"width": largeur, "height": 850})
        page = ctx.new_page()
        err = []
        page.on("pageerror", lambda e: err.append(str(e)))
        page.goto(url, wait_until="load")
        page.wait_for_selector("#feed", state="attached")

        g = page.evaluate("""() => {
            localStorage.removeItem('gta6watch:mis-de-cote-v1');
            misDeCote = new Set();
            lastItems = [1,2,3].map(n => ({
              link: "https://x.test/" + n, source: "GTA BOOM",
              title: "Article " + n, date: new Date().toISOString(), lang: "en",
              description: "texte",
            }));
            setFilter('all');
            applyFilters();
            const e = document.querySelector('#feed .card .card-etoile');
            const r = e.getBoundingClientRect();
            const a = getComputedStyle(e, '::after');
            const deborde = Math.abs(parseFloat(a.top) || 0);
            const actions = [...document.querySelectorAll('#feed .card .card-actions button')]
                .map(b => b.getBoundingClientRect().width);
            return {etoiles: document.querySelectorAll('#feed .card-etoile').length,
                    hauteur: r.height + 2 * deborde,
                    actions: Math.min(...actions)};
        }""")
        check(g["etoiles"] == 3,
              "[%d px] chaque carte porte son étoile (%d)" % (largeur, g["etoiles"]))
        check(g["hauteur"] >= CIBLE_MINIMALE,
              "[%d px] sa zone cliquable fait au moins %d px (%.0f)"
              % (largeur, CIBLE_MINIMALE, g["hauteur"]))
        check(g["hauteur"] <= CIBLE_MINIMALE + 8,
              "[%d px] et elle ne déborde pas sur le titre — une cible "
              "invisible trop grande vole les clics du lien (%.0f px)"
              % (largeur, g["hauteur"]))
        # LE CONTRÔLE DE NON-RÉGRESSION : les quatre boutons du bas ne
        # doivent pas avoir rétréci. C'est tout l'intérêt d'avoir mis
        # l'étoile ailleurs. Seuil à 50 px : ils mesurent 58 px à 320 px
        # aujourd'hui, et un cinquième bouton les ramènerait à ~45.
        check(g["actions"] >= 50,
              "[%d px] les quatre boutons du bas n'ont pas rétréci (%.0f px)"
              % (largeur, g["actions"]))
        # LA FEUILLE DES FILTRES N'ÉTAIT SURVEILLÉE PAR RIEN. Le contrôle
        # tactile ne scanne que #settingsOverlay, et celui des débordements
        # porte sur les ONGLETS du fil. Ajouter un quatrième bouton dans
        # une zone sans contrôle, avec le libellé le plus long des quatre,
        # c'est exactement la façon dont une rangée casse en silence à
        # 320 px. On la couvre maintenant.
        f = page.evaluate("""() => {
            openFiltersSheet();
            const ids = ["filterAll","filterUnread","filterNew","filterCote"];
            return ids.map(id => {
              const e = document.getElementById(id), b = e.getBoundingClientRect();
              const cs = getComputedStyle(e);
              const r = document.createRange(); r.selectNodeContents(e);
              const t = r.getBoundingClientRect();
              const gauche = b.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
              const droite = b.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight);
              return {id, deborde: Math.max(gauche - t.left, t.right - droite),
                      hauteur: b.height};
            });
        }""")
        trop = ["%s (+%.1f px)" % (m["id"], m["deborde"]) for m in f if m["deborde"] > 0.5]
        check(not trop,
              "[%d px] le texte des quatre filtres tient dans son bouton (%s)"
              % (largeur, ", ".join(trop) or "aucun débordement"))
        petits = ["%s=%.0f" % (m["id"], m["hauteur"]) for m in f
                  if m["hauteur"] < CIBLE_MINIMALE]
        check(not petits,
              "[%d px] et chacun fait ses %d px de haut (%s)"
              % (largeur, CIBLE_MINIMALE, ", ".join(petits) or "tous conformes"))
        page.evaluate("closeFiltersSheet()")

        check(not err, "[%d px] aucune erreur JavaScript (%s)" % (largeur, err[:1]))
        ctx.close()

    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    err = []
    page.on("pageerror", lambda e: err.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    etat = page.evaluate("""() => {
        localStorage.removeItem('gta6watch:mis-de-cote-v1');
        misDeCote = new Set();
        lastItems = [1,2,3].map(n => ({
          link: "https://x.test/" + n, source: "GTA BOOM",
          title: "Article " + n, date: new Date().toISOString(), lang: "en",
        }));
        setFilter('all');
        applyFilters();
        // On met de côté le deuxième.
        document.querySelectorAll('#feed .card .card-etoile')[1].click();
        const apresClic = {
          pleines: document.querySelectorAll('#feed .card-etoile.cote').length,
          stocke: JSON.parse(localStorage.getItem('gta6watch:mis-de-cote-v1') || '[]'),
        };
        setFilter('cote');
        applyFilters();
        const filtre = [...document.querySelectorAll('#feed .card')]
          .map(c => c.dataset.lien);
        // Et on le retire.
        document.querySelector('#feed .card .card-etoile').click();
        setFilter('all');
        applyFilters();
        return {apresClic, filtre,
                apresRetrait: JSON.parse(localStorage.getItem('gta6watch:mis-de-cote-v1') || '[]'),
                memorise: ETATS_MEMORISES.includes('cote')};
    }""")
    check(etat["apresClic"]["pleines"] == 1,
          "[étoile] cliquer la remplit, et une seule (%d)"
          % etat["apresClic"]["pleines"])
    check(etat["apresClic"]["stocke"] == ["https://x.test/2"],
          "[étoile] le lien est écrit sur l'appareil (%s)"
          % etat["apresClic"]["stocke"])
    check(etat["filtre"] == ["https://x.test/2"],
          "[étoile] le filtre ne montre que les articles mis de côté (%s)"
          % etat["filtre"])
    check(etat["apresRetrait"] == [],
          "[étoile] re-cliquer le retire, et l'appareil suit (%s)"
          % etat["apresRetrait"])
    check(etat["memorise"],
          "[étoile] le filtre survit à la fermeture — il est dans "
          "ETATS_MEMORISES")

    # LA SAUVEGARDE. Un changement de téléphone ne doit pas effacer la
    # liste : readSet y est depuis toujours, celle-ci doit l'y rejoindre.
    sauve = page.evaluate("""() => {
        misDeCote = new Set(["https://x.test/9"]);
        const c = contenuSauvegarde();
        return {cotes: c.cotes, aussiLus: Array.isArray(c.lus)};
    }""")
    check(sauve["cotes"] == ["https://x.test/9"],
          "[étoile] l'export emporte la liste (%s)" % sauve["cotes"])
    check(sauve["aussiLus"], "[étoile] et il emporte toujours les lus")
    # Une sauvegarde d'avant cette version n'a pas le champ : liste vide,
    # pas un plantage.
    tolerant = page.evaluate("""() => {
        const d = {lus: [], cotes: undefined};
        return new Set((d.cotes || []).filter(l => typeof l === "string")).size;
    }""")
    check(tolerant == 0,
          "[étoile] une sauvegarde d'avant cette version s'importe sans "
          "planter")
    check(not err, "[étoile] aucune erreur JavaScript (%s)" % err[:1])
    ctx.close()


def test_le_vrai_editeur(nav, url):
    """La carte nomme qui a écrit l'article, pas par où il est arrivé.

    MESURÉ le 16/10/2026 sur ce que l'app charge à l'ouverture : sur 300
    articles, 262 sont étiquetés « Google News » — 87 %, et les dix
    premières cartes toutes les dix. Or les liens sont décodés et la
    fonction mediaDe() existait déjà, utilisée par les SEULES statistiques.
    Les statistiques disaient donc « IGN » pendant que la carte, la seule
    chose qu'on lit, disait « Google News (FR) ».
    """
    print("\n[éditeur] la carte nomme le média, pas l'agrégateur")
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    err = []
    page.on("pageerror", lambda e: err.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    vu = page.evaluate("""() => {
        lastItems = [
          // L'agrégateur : la carte doit dire NUMERAMA, et le titre ne doit
          // plus répéter le média juste en dessous de lui.
          {link: "https://www.numerama.com/a", source: "Google News (FR)",
           title: "GTA 6 repousse encore sa sortie - Numerama",
           date: new Date().toISOString(), lang: "fr",
           extraSources: [{source: "Google News (EN, 7 jours)",
                           link: "https://www.cjonline.com/story/x"}]},
          // Un flux natif : rien ne doit bouger.
          {link: "https://www.gtaboom.com/b", source: "GTA BOOM",
           title: "Un titre sans suffixe", date: new Date().toISOString(),
           lang: "en", specialist: true},
          // Agrégateur SANS queue lisible : le domaine prend le relais.
          {link: "https://unn.ua/c", source: "Google News (EN)",
           title: "Rockstar has revealed the final trailer",
           date: new Date().toISOString(), lang: "en"},
        ];
        applyFilters();
        const cartes = [...document.querySelectorAll('#feed .card')].map(c => ({
          src: (c.querySelector('.card-src') || {}).textContent || "",
          titre: (c.querySelector('.card-title') || {}).textContent || "",
          extra: (c.querySelector('.card-extra') || {}).textContent || "",
        }));
        return cartes;
    }""")

    check(len(vu) == 3, "[éditeur] les trois cartes sont rendues (%d)" % len(vu))
    check(vu and vu[0]["src"].strip() == "Numerama",
          "[éditeur] la carte nomme le média et non l'agrégateur (« %s »)"
          % (vu[0]["src"].strip() if vu else "—"))
    check(vu and vu[0]["titre"].strip() == "GTA 6 repousse encore sa sortie",
          "[éditeur] et le titre ne répète plus le média affiché au-dessus "
          "(« %s »)" % (vu[0]["titre"].strip() if vu else "—"))
    check(vu and "cjonline.com" in vu[0]["extra"],
          "[éditeur] « + N autres sources » nomme le domaine, pas "
          "l'agrégateur (« %s »)" % (vu[0]["extra"].strip() if vu else "—"))
    check(vu and "Google News" not in vu[0]["extra"],
          "[éditeur] et l'agrégateur n'y apparaît plus")
    check(len(vu) > 1 and vu[1]["src"].strip() == "GTA BOOM",
          "[éditeur] une source native est intacte (« %s »)"
          % (vu[1]["src"].strip() if len(vu) > 1 else "—"))
    check(len(vu) > 1 and vu[1]["titre"].strip() == "Un titre sans suffixe",
          "[éditeur] et son titre n'est pas rogné")
    check(len(vu) > 2 and vu[2]["src"].strip() == "unn.ua",
          "[éditeur] sans queue de titre, le domaine du lien décodé prend le "
          "relais (« %s »)" % (vu[2]["src"].strip() if len(vu) > 2 else "—"))

    # L'aperçu 👁️ porte la même étiquette que la carte : deux endroits qui
    # nommeraient différemment le même article seraient pires qu'un seul
    # endroit qui le nomme mal.
    apercu = page.evaluate("""() => {
        lastItems = [{link: "https://www.numerama.com/a", source: "Google News (FR)",
                      title: "Un sujet - Numerama", description: "texte",
                      date: new Date().toISOString(), lang: "fr"}];
        applyFilters();
        const b = document.querySelector('#feed .card button[onclick^="openPreview"]');
        if(!b) return null;
        b.click();
        return document.getElementById('previewSource').textContent;
    }""")
    check(apercu and apercu.strip() == "NUMERAMA",
          "[éditeur] l'aperçu dit la même chose que la carte (« %s »)" % apercu)
    check(not err, "[éditeur] aucune erreur JavaScript (%s)" % err[:1])
    ctx.close()


def test_mur_du_jour_j(nav, url):
    """Une journée de pic se découpe en tranches, une journée calme non.

    Le 17/09, 223 articles sont tombés dans un seul palier « Aujourd'hui » ;
    le 08/10, 290. Le 19 novembre sera pire. Mesuré avant de coder : les
    tranches horaires FIXES donnent 23 en-têtes sur une journée normale et
    laissent encore 177 articles dans un palier le jour de la sortie. Seul
    un découpage adaptatif tient — il se resserre là où ça déborde.
    """
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")

    def journee(n, pas_minutes=3, doublons_minute=0):
        """Fabrique n articles du jour, espacés, et rend les en-têtes rendus."""
        return page.evaluate("""([n, pas, dbl]) => {
            rattrapageLance = true;
            const base = new Date(); base.setHours(22, 0, 0, 0);
            lastItems = [];
            for(let k = 0; k < n; k++){
                const d = new Date(base.getTime() - k * pas * 60000);
                lastItems.push({
                    link: 'https://t.test/' + k, title: 'Article ' + k,
                    source: 'Test', date: d.toISOString(),
                });
            }
            // Des articles À LA MÊME MINUTE, pour le contrôle de coupure.
            for(let k = 0; k < dbl; k++){
                const d = new Date(base.getTime() - 29 * pas * 60000);
                lastItems.push({
                    link: 'https://t.test/dbl' + k, title: 'Même minute ' + k,
                    source: 'Test', date: d.toISOString(),
                });
            }
            lastItems.sort((a, b) => new Date(b.date) - new Date(a.date));
            visibleCount = 1000; settings.maxDisplay = 1000;
            applyFilters();
            return [...document.querySelectorAll('.day-group')].map(g => ({
                libelle: g.querySelector('.day-label').textContent,
                n: g.querySelectorAll('.card').length,
            }));
        }""", [n, pas_minutes, doublons_minute])

    # --- Journée calme : un seul bloc, nom nu. L'app ne change de forme que
    # les jours où elle le doit.
    g = journee(40)
    check(len(g) == 1 and g[0]["libelle"] == "Aujourd'hui",
          "[mur] une journée de 40 articles reste un seul bloc « %s »"
          % g[0]["libelle"])

    # --- Juste sous le seuil : toujours un bloc.
    g = journee(60)
    check(len(g) == 1,
          "[mur] à 60 articles, le seuil n'est pas encore franchi (%d bloc)"
          % len(g))

    # --- Journée de pic : ça se découpe.
    g = journee(290, pas_minutes=3)
    check(len(g) > 1, "[mur] à 290 articles, la journée se découpe (%d tranches)" % len(g))
    check(max(x["n"] for x in g) <= 40,
          "[mur] aucune tranche ne dépasse la cible de façon déraisonnable "
          "(max %d)" % max(x["n"] for x in g))
    check(all("Aujourd'hui · " in x["libelle"] for x in g),
          "[mur] chaque tranche porte le jour ET ses heures (%s)" % g[0]["libelle"])

    # Les heures descendent : le fil va du plus récent au plus ancien, et le
    # découpage ne doit pas renverser cet ordre.
    import re as _re
    bornes = [_re.findall(r"(\d\d)h(\d\d)", x["libelle"]) for x in g]
    debuts = [int(b[0][0]) * 60 + int(b[0][1]) for b in bornes if b]
    check(debuts == sorted(debuts, reverse=True),
          "[mur] les tranches descendent dans le temps, comme le fil (%s)" % debuts)

    # --- LA RÈGLE QUI ÉVITE UN EN-TÊTE ABSURDE. On ne coupe jamais au milieu
    # d'une minute : sinon deux en-têtes voisins affichent la même borne
    # — « … – 16h49 » suivi de « 16h49 – … » — et ça se lit comme un bogue.
    g = journee(200, pas_minutes=3, doublons_minute=12)
    fins = [_re.findall(r"(\d\d)h(\d\d)", x["libelle"]) for x in g]
    paires = [(b[0], b[-1]) for b in fins if len(b) == 2]
    chevauche = any(paires[k][0] == paires[k + 1][1] for k in range(len(paires) - 1))
    check(not chevauche,
          "[mur] deux tranches voisines ne partagent jamais une borne (%s)"
          % [x["libelle"] for x in g[:3]])
    # Et la règle a bien DÛ jouer : treize articles partagent une minute à
    # l'endroit exact où la cible de 30 voulait couper, donc au moins une
    # tranche déborde. Sans cette exigence, le contrôle ci-dessus resterait
    # vert dans un fil où aucune minute n'est partagée — c'est-à-dire sans
    # jamais tester ce qu'il prétend tester.
    check(max(x["n"] for x in g) > 30,
          "[mur] la règle a joué : une tranche déborde pour ne pas couper "
          "une minute en deux (max %d)" % max(x["n"] for x in g))

    # --- « Cette semaine » couvre PLUSIEURS jours : une tranche horaire y
    # serait ambiguë sans la date. Il ne se découpe donc jamais.
    g = page.evaluate("""() => {
        const base = new Date(); base.setHours(12, 0, 0, 0);
        lastItems = [];
        for(let k = 0; k < 150; k++){
            const d = new Date(base.getTime() - (3 * 86400000) - k * 60000);
            lastItems.push({link: 'https://s.test/' + k, title: 'S' + k,
                            source: 'Test', date: d.toISOString()});
        }
        visibleCount = 1000; settings.maxDisplay = 1000;
        applyFilters();
        return [...document.querySelectorAll('.day-label')].map(h => h.textContent);
    }""")
    check(g == ["Cette semaine"],
          "[mur] « Cette semaine » ne se découpe pas, il couvre plusieurs "
          "jours (%s)" % g)

    # --- LE PIÈGE DE LA PAGINATION. Le fil ne rend que trente cartes au
    # départ : si le seuil se mesurait sur les cartes RENDUES, il ne se
    # déclencherait qu'au troisième « charger plus ». Or la journée compte
    # 290 articles dès le premier écran, et c'est là qu'il faut le dire.
    decoupe = page.evaluate("""() => {
        const base = new Date(); base.setHours(22, 0, 0, 0);
        lastItems = [];
        for(let k = 0; k < 290; k++){
            lastItems.push({link: 'https://p.test/' + k, title: 'P' + k, source: 'Test',
                            date: new Date(base.getTime() - k * 3 * 60000).toISOString()});
        }
        visibleCount = 30; settings.maxDisplay = 1000;
        applyFilters();
        return [...document.querySelectorAll('.day-label')].map(h => h.textContent);
    }""")
    check(len(decoupe) == 1 and "·" in decoupe[0],
          "[mur] dès le premier écran de 30 cartes, le découpage est déjà "
          "annoncé (%s)" % decoupe)

    check(not erreurs, "[mur] aucune erreur JavaScript (%s)" % erreurs[:1])
    ctx.close()


def test_badges_filtres_chrono_et_recherches(nav, url):
    """3.8, 3.4 et 3.5 : les badges filtrent, les officiels se lisent, la
    recherche se souvient."""
    base = url.rsplit("/", 1)[0]
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("rattrapageLance = true;")
    page.evaluate("settings.backendUrl = '%s/feed.json';" % base)
    page.evaluate("async () => { await checkFromBackend(false); }")

    # ---- 3.8 : LA REGEX VIDÉO RESSERRÉE. L'ancienne contenait `\bvideo\b`
    # nu, qui attrapait « video game », « jeux vidéo » et le média *Video
    # Games Chronicle* — 51 faux positifs sur 199 au 13/10/2026.
    faux = page.evaluate("""() => [
        'GTA 6 On Track To Overthrow The Best-Selling Video Game Of All-Time',
        'Will GTA 6 make its way to Switch 2? - Video Games Chronicle',
        'Bad Bunny, animateurs de radio du très attendu jeu vidéo GTA VI',
        'Jeux vidéo : le mode multijoueur de GTA 6 attendu en 2027',
    ].map(t => estVideo({title: t}))""")
    check(not any(faux),
          "[badges] « video game », « Video Games Chronicle » et « jeu(x) "
          "vidéo » ne sont PAS des vidéos (%s)" % faux)
    vrais = page.evaluate("""() => [
        'A New 5-Minute GTA 6 Video Has Appeared Online',
        'GTA 6 : 25 minutes de gameplay volées, ce que révèle vraiment la vidéo',
        'Rockstar drops a new GTA 6 trailer',
        'Leaked footage shows Vice City at night',
    ].map(t => estVideo({title: t}))""")
    check(all(vrais),
          "[badges] et les vraies vidéos passent toujours (%s)" % vrais)

    # Un seul juge, partagé par la carte et par le filtre. Deux expressions
    # recopiées divergent au premier ajustement — c'est exactement ce qui
    # est arrivé à VIDÉO.
    extrait = page.evaluate("""() => {
        const src = document.documentElement.outerHTML;
        const d = src.indexOf('function renderCard');
        return src.slice(d, d + 4000);
    }""")
    # L'INTENTION, pas la forme. Ce contrôle exigeait d'abord la chaîne
    # « estVideo(item) » dans renderCard. Depuis le 13/10/2026 la carte
    # parcourt la table JUGES_BADGE en boucle — neuf badges recopiés à la
    # main garantissaient qu'un ajout en oublierait un, ce qui est
    # exactement ce qui était arrivé à VIDÉO. Le code satisfait mieux
    # l'intention qu'avant ; c'est le test qui regardait la mauvaise chose.
    # Depuis le 20/10/2026 la boucle elle-même est partie dans une
    # fabrique unique, boutonsBadges : la carte ET la chronologie la
    # portaient, recopiée mot pour mot. À vingt et un badges, avec une
    # classe de famille à poser en plus, deux copies garantissaient un
    # oubli. La carte appelle la fabrique, la fabrique passe par la table.
    fabrique = page.evaluate("String(boutonsBadges)")
    check("boutonsBadges(item)" in extrait,
          "[badges] la carte passe par la fabrique unique de badges")
    check("JUGES_BADGE[" in fabrique,
          "[badges] et la fabrique passe par la table des juges, partagée "
          "avec le filtre")
    check("\\bleak" not in extrait and "\\btrailer" not in extrait,
          "[badges] et ne recopie aucune expression régulière chez elle")

    # Le juge « spécialiste » doit dire EXACTEMENT ce que la carte montre :
    # le badge ne s'affiche pas sur un article officiel.
    check(page.evaluate("estSpecialiste({specialist: true, official: true})") is False
          and page.evaluate("estSpecialiste({specialist: true})") is True,
          "[badges] un officiel ne compte pas comme spécialiste — sinon le "
          "filtre ramène des cartes sans le badge cliqué")

    # ---- LES SEPT BADGES SONDÉS LE 13/10/2026.
    #
    # Chaque motif est la version RESSERRÉE : les premières moutures
    # attrapaient n'importe quoi, et c'est le sondage du corpus (8 378
    # titres) qui a imposé le nettoyage. Les cas ci-dessous sont des titres
    # RÉELS tirés de ce sondage, pas des inventions.
    precision = page.evaluate("""() => {
        const faux = {
            // « prix » attrapait les montants nus : le chiffre d'affaires,
            // le piratage, les ventes de consoles.
            prix: ['GTA 6 effect caused PS5 Pro sales to surge by 348%',
                   'Grand Theft Auto VI piracy could cost Rockstar up to $1 billion'],
            // « date » attrapait « lancement » : une soirée, une édition.
            date: ['«GTA VI» va avoir droit à une soirée de lancement à Genève',
                   'Rayman Legends Retold : Ubisoft dévoile l édition de lancement'],
        };
        const vrais = {
            prix: ['Is GTA 6 Overhyped? $79.99 Price Tag Sparks Debate',
                   'GTA 6 : le prix de l édition collector'],
            date: ['GTA 6 : date de sortie, prix et précommande',
                   'GTA 6 release date confirmed by Rockstar'],
            pc: ['The PC version of Grand Theft Auto 6 is reportedly coming',
                 'GTA 6 sur PC dès le premier trimestre 2027 ?'],
            preco: ['GTA 6 pre-orders are breaking records according to Xbox',
                    'GTA VI précommande à prix cassé'],
            perso: ['GTA 6 Lucia and Jason first look', 'Qui est Lucia dans GTA 6 ?'],
            take2: ['Take-Two annoncera ses résultats trimestriels le 9 novembre'],
            rumeur: ['GTA 6 is reportedly coming to Xbox Cloud'],
        };
        const out = {fauxPris: [], vraisRates: []};
        for(const [cle, titres] of Object.entries(faux))
            for(const t of titres)
                if(JUGES_BADGE[cle]({title: t})) out.fauxPris.push(cle + ' : ' + t.slice(0, 45));
        for(const [cle, titres] of Object.entries(vrais))
            for(const t of titres)
                if(!JUGES_BADGE[cle]({title: t})) out.vraisRates.push(cle + ' : ' + t.slice(0, 45));
        return out;
    }""")
    check(not precision["fauxPris"],
          "[badges] les motifs resserrés n'attrapent plus ce qu'ils "
          "attrapaient : chiffre d'affaires, soirée de lancement (%s)"
          % precision["fauxPris"][:1])
    check(not precision["vraisRates"],
          "[badges] et ils n'ont pas perdu les vrais cas au passage (%s)"
          % precision["vraisRates"][:1])

    # Les neuf juges sont dans la table, et la table EST le filtre.
    table = page.evaluate("""() => ({
        juges: Object.keys(JUGES_BADGE).sort(),
        noms: Object.keys(NOMS_BADGE).sort(),
        ordre: ORDRE_BADGES.slice(),
    })""")
    check(table["juges"] == table["noms"],
          "[badges] chaque juge a son libellé, et réciproquement (%s / %s)"
          % (len(table["juges"]), len(table["noms"])))
    manquants = [c for c in table["juges"] if c != "specialist" and c not in table["ordre"]]
    check(not manquants,
          "[badges] et chacun a sa place dans l'ordre d'affichage (%s)" % manquants)
    # L'ordre n'est pas alphabétique à dessein : une ALERTE passe devant un
    # SUJET. Un « PRIX » avant un « LEAK » inverserait la lecture.
    check(table["ordre"].index("leak") < table["ordre"].index("prix")
          and table["ordre"].index("video") < table["ordre"].index("take2"),
          "[badges] les alertes précèdent les sujets (%s)" % table["ordre"][:4])

    # Chaque couleur neuve tient le contraste. Le contrôle Python le vérifie
    # sur le CSS ; celui-ci vérifie qu'elles sont RÉELLEMENT appliquées et
    # qu'aucun badge ne se rend sans sa classe de couleur.
    sansCouleur = page.evaluate("""() => {
        lastItems = Object.keys(JUGES_BADGE).filter(c => c !== 'specialist').map((c, k) => ({
            link: 'https://b.test/' + k, source: 'T', date: new Date().toISOString(),
            title: {leak:'GTA 6 leak', video:'GTA 6 trailer', pc:'GTA 6 sur PC',
                    preco:'GTA 6 précommande', prix:'GTA 6 : le prix',
                    perso:'GTA 6 Lucia', take2:'Take-Two résultats trimestriels',
                    rumeur:'GTA 6 rumeur', date:'GTA 6 date de sortie'}[c],
        }));
        visibleCount = 1000; settings.maxDisplay = 1000; badgeActif = null; applyFilters();
        return [...document.querySelectorAll('.card .tag-badge')]
            .filter(b => !/tag-(leak|video|pc|preco|prix|perso|take2|rumeur|date|hot|lang|cat)/.test(b.className))
            .map(b => b.textContent);
    }""")
    check(not sansCouleur,
          "[badges] aucun badge rendu sans sa classe de couleur (%s)" % sansCouleur[:2])

    # Cliquer filtre, recliquer annule.
    page.evaluate("filtreBadge(null, 'video')")
    page.wait_for_timeout(200)
    etat = page.evaluate("""() => ({
        barre: document.getElementById('badgeFiltre').style.display,
        texte: document.getElementById('badgeFiltre').textContent,
        toutes: [...document.querySelectorAll('.card')].every(c => c.querySelector('.tag-video')),
        n: document.querySelectorAll('.card').length,
    })""")
    check(etat["barre"] == "flex" and "VIDÉO" in etat["texte"],
          "[badges] une barre nomme le filtre actif — sinon un fil filtré "
          "ressemble à un fil vide")
    check(etat["n"] > 0 and etat["toutes"],
          "[badges] et toutes les cartes affichées portent le badge cliqué "
          "(%d cartes)" % etat["n"])
    page.evaluate("filtreBadge(null, 'video')")
    page.wait_for_timeout(200)
    check(page.evaluate("badgeActif") is None
          and page.evaluate("document.getElementById('badgeFiltre').style.display") == "none",
          "[badges] recliquer le même badge annule le filtre")

    # Le badge est dans une carte dont le titre et la vignette sont des
    # liens : sans stopPropagation, le cliquer ouvrirait l'article.
    corps = page.evaluate("String(filtreBadge)")
    check("stopPropagation" in corps and "preventDefault" in corps,
          "[badges] le clic ne remonte pas jusqu'au lien de la carte")

    # ---- LES DOUZE CATÉGORIES DE L'APRÈS-SORTIE, 20/10/2026.
    #
    # Demandées par Antoni : « test, astuces, tuto, gameplay... ». Mêmes
    # règles que les sept d'octobre — un juge unique, un motif resserré
    # sur le corpus réel — avec une difficulté de plus : LE FIL EST
    # PRÉ-SORTIE. Un test de GTA 6 n'existe pas encore. Les motifs ne
    # peuvent donc être éprouvés que sur leurs FAUX POSITIFS d'aujourd'hui
    # et sur des titres d'après-sortie écrits à la main.
    #
    # Les faux ci-dessous sont des titres RÉELS du fil au 20/10/2026, pas
    # des inventions ; chacun est passé entre les mailles d'une version
    # précédente du motif.
    categories = page.evaluate("""() => {
        const faux = {
            // « review copies », « Board Review », « best-reviewed » : sur
            // seize titres pris par le premier motif, seize faux positifs.
            test: ["GTA 6 First Review Details Leaked",
                   "'GTA 6' Rated 'Adults Only' in South Korea Following Private Board Review",
                   "GTA 6 On Track To Overthrow The Best-Selling Video Game Of All-Time",
                   "Les exemplaires de test de GTA 6 seraient envoyés plus tôt",
                   "Noté 19/20, le dernier jeu de Rockstar avant GTA 6 passe à 19,99€"],
            // « how to get » attrape un congé pour la sortie.
            soluce: ["Carer's Leave for GTA 6: How to Get Time Off for the Game",
                     "Where to Pre-Order GTA 6 Controllers for PS5"],
            // « cheating » : le système de drague du jeu, pas la triche.
            astuce: ["A Surprising Number of People Think Dating in GTA 6 is Cheating",
                     "GTA 6: Can you cheat on someone with a video game character?"],
            // « update » nu : trois titres sur quatre ne sont pas un correctif.
            maj: ["GTA 6 Map Update Reveals New Buildings and Size Comparisons",
                  "Rockstar Issues Rare Official GTA 6 Development Update",
                  "GTA 6 Gets One Final Official Release Date Update"],
            // « crash » : la cryptomonnaie du leaker, neuf titres sur treize.
            bug: ["CYBERLEEK Token Crashes 40% as GTA 6 Leaker Makes New Threats",
                  "'There Is a God' — GTA 6 Fans Celebrate as Cyberleek Cryptocurrency Crashes Hard"],
            // « vente » au singulier : la vente aux mineurs, un disque mis en vente.
            ventes: ["GTA 6 est classé Pegi 18, un label qui n'interdit pas la vente aux mineurs",
                     "Un vendeur met en vente un disque PS5 de GTA 6 en édition physique"],
            // « mod » ne doit pas manger « modern » ni « model ».
            mod: ["GTA 6 brings a modern Vice City to life",
                  "The model behind Lucia speaks out"],
        };
        const vrais = {
            test: ["GTA 6 : notre test complet après 40 heures de jeu",
                   "Grand Theft Auto VI review: a masterpiece",
                   "GTA 6 : le test"],
            soluce: ["GTA 6 soluce : toutes les missions principales",
                     "GTA 6: how to unlock the Vice City penthouse",
                     "Comment trouver tous les colis cachés de GTA 6"],
            astuce: ["GTA 6 : toutes les astuces et codes de triche",
                     "GTA 6 cheat codes: the full list",
                     "GTA 6 tips and tricks for beginners"],
            maj: ["GTA 6 : le patch 1.02 corrige les plantages sur PS5",
                  "Rockstar déploie un correctif pour GTA 6",
                  "GTA 6 patch notes: what changed"],
            bug: ["GTA 6 : un bug empêche de terminer la mission finale",
                  "GTA 6 players report a game-breaking glitch"],
            mod: ["Un moddeur ajoute la carte de GTA 5 dans GTA 6",
                  "GTA 6 modding tools are already here"],
            online: ["GTA 6 Online : le mode multijoueur arrive en 2027",
                     "GTA Online ferme ses serveurs sur PS4"],
            perf: ["GTA 6 tourne en 1440p à 30 FPS sur PS5",
                   "GTA 6 : le ray-tracing réservé à la PS5 Pro"],
            trophee: ["GTA 6 : la liste complète des trophées",
                      "GTA 6 achievements revealed"],
            ventes: ["Les ventes de GTA 6 ont dépassé les 6 millions d'exemplaires",
                     "GTA 6 breaks first-day sales records"],
            gameplay: ["GTA 6 : 25 minutes de gameplay inédit",
                       "Our GTA 6 hands-on impressions"],
            speedrun: ["Un speedrunner termine GTA 6 en 4 heures",
                       "GTA 6 speedrun world record broken"],
        };
        const out = {fauxPris: [], vraisRates: []};
        for(const [cle, titres] of Object.entries(faux))
            for(const t of titres)
                if(JUGES_BADGE[cle]({title: t, link: "https://x.test/a"}))
                    out.fauxPris.push(cle + " : " + t.slice(0, 50));
        for(const [cle, titres] of Object.entries(vrais))
            for(const t of titres)
                if(!JUGES_BADGE[cle]({title: t, link: "https://x.test/a"}))
                    out.vraisRates.push(cle + " : " + t.slice(0, 50));
        return out;
    }""")
    check(not categories["fauxPris"],
          "[catégories] les motifs écartent les faux positifs mesurés le "
          "20/10 : « review copies », « is Cheating », « Token Crashes », "
          "« Map Update », « la vente aux mineurs » (%s)"
          % categories["fauxPris"][:2])
    check(not categories["vraisRates"],
          "[catégories] et les vrais articles d'après-sortie passent (%s)"
          % categories["vraisRates"][:2])

    # L'ADRESSE AVANT LE TITRE pour les trois que le titre ne sait pas
    # juger. Aucun article n'a /test/ aujourd'hui, mais 951 ont /news/ :
    # le chemin existe et se remplira le 19 novembre.
    url = page.evaluate("""() => ({
        test: estTest({title: "GTA 6", link: "https://jv.test/test/gta-6-9412"}),
        soluce: estSoluce({title: "GTA 6", link: "https://jv.test/soluce/gta-6/colis"}),
        astuce: estAstuce({title: "GTA 6", link: "https://jv.test/astuces/gta-6"}),
        // Et un chemin qui ne dit rien ne pose rien.
        neutre: estTest({title: "GTA 6 arrive", link: "https://jv.test/news/gta-6"}),
    })""")
    check(url["test"] and url["soluce"] and url["astuce"],
          "[catégories] le CHEMIN de l'adresse suffit : /test/, /soluce/, "
          "/astuces/ (%s)" % url)
    check(not url["neutre"],
          "[catégories] et /news/ ne déclenche rien (%s)" % url["neutre"])

    # ---- LE NOM DU MÉDIA N'EST PAS LE TITRE.
    #
    # 3 406 titres sur 4 225 finissent par « - Nom du média », et dans 302
    # d'entre eux un mot du motif n'est QUE là. Le badge PC se posait sur
    # 21 articles qui ne parlent pas de PC, parce que leur média s'appelle
    # PC Gamer.
    media = page.evaluate("""() => ({
        corps: corpsDuTitre({title: "GTA 6 leaker threatens Take-Two - PC Gamer"}),
        pcFaux: estPC({title: "GTA 6 leaker threatens Take-Two - PC Gamer"}),
        pcFaux2: estPC({title: "Des hackers menacent de diffuser une version jouable - PC Astuces"}),
        astuceFaux: estAstuce({title: "Des hackers menacent de diffuser une version - PC Astuces"}),
        guideFaux: estSoluce({title: "3 movies I'm watching until GTA VI - Tom's Guide"}),
        // Le vrai PC reste pris.
        pcVrai: estPC({title: "GTA 6 sur PC dès 2027 - Gamekult"}),
        // LA GARDE : un suffixe qui ne ressemble pas à un nom de média
        // n'est pas rogné. Sans elle, ce titre-là perdait son badge
        // RUMEUR, et c'était le seul du fil entier.
        rumeur: estRumeur({title: "GTA 6 map - confirmed and rumored locations"}),
    })""")
    check(media["corps"] == "GTA 6 leaker threatens Take-Two",
          "[médias] le suffixe « - Nom du média » est retiré avant de juger "
          "(« %s »)" % media["corps"])
    check(not media["pcFaux"] and not media["pcFaux2"],
          "[médias] PC Gamer et PC Astuces ne posent plus le badge PC — "
          "21 articles le portaient pour cette seule raison (%s / %s)"
          % (media["pcFaux"], media["pcFaux2"]))
    check(not media["astuceFaux"] and not media["guideFaux"],
          "[médias] ni PC Astuces le badge ASTUCE, ni Tom's Guide le badge "
          "SOLUCE (%s / %s)" % (media["astuceFaux"], media["guideFaux"]))
    check(media["pcVrai"],
          "[médias] et un vrai article PC garde le sien (%s)" % media["pcVrai"])
    check(media["rumeur"],
          "[médias] la garde tient : « - confirmed and rumored locations » "
          "n'est pas un nom de média et n'est pas rogné (%s)" % media["rumeur"])

    # GAMEPLAY ET VIDÉO NE DISENT PLUS LA MÊME CHOSE. Les 97 cartes
    # marquées GAMEPLAY étaient TOUTES marquées VIDÉO ; `\\bgameplay\\b`
    # est donc sorti de RE_VIDEO le même jour. Le mot ne prouve aucune
    # vidéo.
    gv = page.evaluate("""() => ({
        detail: estVideo({title: "Bloomberg a révélé de nouveaux détails sur le gameplay de GTA 6"}),
        detailCat: estGameplay({title: "Bloomberg a révélé de nouveaux détails sur le gameplay de GTA 6"}),
        vraieVideo: estVideo({title: "25 minutes de gameplay volées, ce que révèle la vidéo"}),
        trailer: estVideo({title: "Rockstar drops a new GTA 6 trailer"}),
    })""")
    check(not gv["detail"] and gv["detailCat"],
          "[catégories] « des détails sur le gameplay » est une CATÉGORIE, "
          "plus une vidéo (vidéo %s, gameplay %s)"
          % (gv["detail"], gv["detailCat"]))
    check(gv["vraieVideo"] and gv["trailer"],
          "[catégories] et les vraies vidéos restent prises (%s / %s)"
          % (gv["vraieVideo"], gv["trailer"]))

    # ---- DEUX FAMILLES, ET ELLES NE SE RESSEMBLENT PAS.
    #
    # Choix d'Antoni, posé avant d'écrire : à vingt et un badges une carte
    # peut en porter cinq. L'alerte garde son aplat, la catégorie est au
    # trait.
    familles = page.evaluate("""() => {
        lastItems = [{link: "https://f.test/1", source: "T",
                      date: new Date().toISOString(),
                      title: "GTA 6 leak : notre test et les astuces"}];
        visibleCount = 1000; settings.maxDisplay = 1000; badgeActif = null;
        applyFilters();
        const lus = [...document.querySelectorAll(".card .tag-badge")].map(b => {
          const cs = getComputedStyle(b);
          return {texte: b.textContent, cat: b.classList.contains("tag-cat"),
                  fond: cs.backgroundColor, bord: cs.borderTopWidth};
        });
        const alerte = lus.find(b => !b.cat && b.texte === "LEAK");
        const cat = lus.find(b => b.cat && b.texte === "TEST");
        return {
          n: lus.length,
          ordre: lus.map(b => b.texte),
          // La dernière alerte précède la première catégorie.
          separees: lus.length > 1
            && lus.findIndex(b => b.cat) > lus.map(b => b.cat).lastIndexOf(false),
          alerteOpaque: alerte ? alerte.fond : null,
          catTransparente: cat ? cat.fond : null,
          catBordee: cat ? parseFloat(cat.bord) : 0,
        };
    }""")
    check(familles["n"] >= 3,
          "[familles] un titre peut porter alertes et catégories à la fois "
          "(%s)" % familles["ordre"])
    check(familles["separees"],
          "[familles] et les alertes passent TOUTES devant les catégories — "
          "un sujet ne précède jamais un événement (%s)" % familles["ordre"])
    check(familles["alerteOpaque"] not in (None, "rgba(0, 0, 0, 0)"),
          "[familles] l'alerte garde son aplat (%s)" % familles["alerteOpaque"])
    check(familles["catTransparente"] == "rgba(0, 0, 0, 0)"
          and familles["catBordee"] >= 1,
          "[familles] la catégorie est au trait, fond transparent et bord "
          "d'au moins 1 px (fond %s, bord %s px)"
          % (familles["catTransparente"], familles["catBordee"]))

    # LA TABLE EST LE FILTRE, et elle a maintenant vingt-deux entrées.
    # Un juge ajouté sans libellé, ou sans place dans l'ordre, se rendrait
    # « undefined » sur la carte.
    compte = page.evaluate("""() => ({
        juges: Object.keys(JUGES_BADGE).length,
        cats: [...BADGES_CATEGORIE],
        horsOrdre: [...BADGES_CATEGORIE].filter(c => !ORDRE_BADGES.includes(c)),
        sansNom: [...BADGES_CATEGORIE].filter(c => !NOMS_BADGE[c]),
        sansJuge: [...BADGES_CATEGORIE].filter(c => !JUGES_BADGE[c]),
    })""")
    check(len(compte["cats"]) == 12,
          "[catégories] les douze demandées sont là (%s)" % len(compte["cats"]))
    check(not compte["horsOrdre"] and not compte["sansNom"]
          and not compte["sansJuge"],
          "[catégories] chacune a son juge, son libellé et sa place (%s %s %s)"
          % (compte["horsOrdre"], compte["sansNom"], compte["sansJuge"]))

    # ---- UNE COULEUR PAR CATÉGORIE, ET CHACUNE MESURÉE.
    #
    # Demandé par Antoni le 20/10/2026, après la première version où les
    # douze partageaient un même trait neutre. Douze tons à 30° l'un de
    # l'autre : c'est le mieux qu'on puisse faire à douze, et les deux
    # paires les plus proches tiennent dans une cinquantaine d'unités RVB.
    #
    # PAR THÈME, et pas une valeur fixe comme les neuf aplats d'alerte :
    # une catégorie est au trait, donc sa couleur s'écrit directement sur
    # la carte — blanche en clair, #10151a en sombre. Aucun ton unique ne
    # tient 4,5:1 sur les deux.
    for theme, fondAttendu in (("light", "rgb(255, 255, 255)"),
                               ("dark", "rgb(16, 21, 26)")):
        teintes = page.evaluate("""(theme) => {
            document.documentElement.setAttribute("data-theme", theme);
            const sonde = document.createElement("span");
            sonde.className = "tag-badge tag-cat";
            const carte = document.createElement("div");
            carte.className = "card";
            carte.appendChild(sonde);
            document.body.appendChild(carte);
            const fond = getComputedStyle(carte).backgroundColor;
            const lus = {};
            for(const cle of BADGES_CATEGORIE){
              sonde.className = "tag-badge tag-cat tag-" + cle;
              const cs = getComputedStyle(sonde);
              lus[cle] = {texte: cs.color, trait: cs.borderTopColor,
                          corps: parseFloat(cs.fontSize)};
            }
            carte.remove();
            return {fond: fond, lus: lus};
        }""", theme)
        check(teintes["fond"] == fondAttendu,
              "[teintes %s] la carte est bien le fond mesuré (%s)"
              % (theme, teintes["fond"]))

        def lire(css):
            n = [int(x) for x in re.findall(r"\d+", css)[:3]]
            return tuple(n)

        def luminance(c):
            def f(v):
                v /= 255.0
                return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])

        fond = lire(teintes["fond"])
        faibles, muets, vus = [], [], {}
        for cle, v in teintes["lus"].items():
            t = lire(v["texte"])
            ya, yb = luminance(t), luminance(fond)
            r = (max(ya, yb) + 0.05) / (min(ya, yb) + 0.05)
            if r < 4.5:
                faibles.append("%s %.2f:1" % (cle, r))
            # Le trait doit porter la MÊME couleur que le texte : un badge
            # coloré dans un cadre gris se lit comme une erreur de rendu.
            if lire(v["trait"]) != t:
                muets.append("%s (%s / %s)" % (cle, v["texte"], v["trait"]))
            vus[cle] = t
        check(not faibles,
              "[teintes %s] les douze passent 4,5:1 sur la carte (%s)"
              % (theme, faibles))
        check(not muets,
              "[teintes %s] et le trait porte la couleur du texte (%s)"
              % (theme, muets))
        # DOUZE COULEURS DISTINCTES, pas onze et un doublon. Une teinte
        # recopiée par inadvertance passerait tous les contrôles
        # ci-dessus sans que rien ne le dise.
        doublons = [c for c in vus if list(vus.values()).count(vus[c]) > 1]
        check(not doublons,
              "[teintes %s] les douze sont toutes différentes (%s)"
              % (theme, doublons))
        # La plus proche paire, dite plutôt que cachée : à douze teintes
        # elle est serrée par construction, et le seuil retenu (40 unités
        # RVB) est celui sous lequel deux badges de 10 px ne se
        # distinguent plus.
        pires = sorted(
            (sum((x - y) ** 2 for x, y in zip(vus[a], vus[b])) ** 0.5, a, b)
            for i, a in enumerate(sorted(vus)) for b in sorted(vus)[i + 1:])
        check(pires[0][0] >= 40,
              "[teintes %s] et la paire la plus proche reste distinguable "
              "(%s / %s, %.0f unités)"
              % (theme, pires[0][1], pires[0][2], pires[0][0]))
    page.evaluate("""() => document.documentElement.setAttribute("data-theme", "dark")""")

    # UN BADGE RESTE UN BADGE, MÊME DEVENU BOUTON.
    #
    # Le défaut, vu par Antoni sur son téléphone le 13/10/2026 : le badge
    # LEAK est rendu comme un pavé rouge de 44 px de haut, contre 17 pour un
    # badge ordinaire, et toute la rangée de source passait de 17 à 44.
    # La cause : `button{}` impose min-height:44px à TOUT bouton — la règle
    # des cibles tactiles du site — et mon reset ne neutralisait que la
    # bordure, le curseur et l'interligne.
    #
    # POURQUOI AUCUN TEST NE L'A VU : le contrôle tactile vérifie qu'un
    # bouton fait AU MOINS 44 px. Le badge en faisait exactement 44, donc il
    # passait. Un bouton trop GROS était invisible pour lui. Celui-ci
    # regarde l'autre sens.
    tailles = page.evaluate("""() => {
        const btn = document.querySelector('.card-src-row button.tag-badge');
        const spn = document.querySelector('.card-src-row span.tag-badge');
        if(!btn || !spn) return null;
        const b = btn.getBoundingClientRect(), s = spn.getBoundingClientRect();
        return {bouton: Math.round(b.height), etiquette: Math.round(s.height),
                rangee: Math.round(btn.closest('.card-src-row').getBoundingClientRect().height)};
    }""")
    if tailles:
        check(abs(tailles["bouton"] - tailles["etiquette"]) <= 2,
              "[badges] un badge cliquable a la même taille qu'une étiquette "
              "simple (%d px contre %d)" % (tailles["bouton"], tailles["etiquette"]))
        check(tailles["rangee"] <= tailles["etiquette"] + 4,
              "[badges] et il ne gonfle pas la rangée de source (%d px)"
              % tailles["rangee"])

    # LE TEXTE CENTRÉ DANS LE BADGE, et les badges de même hauteur.
    #
    # Signalé par Antoni le 13/10/2026 : « le texte sur les badges est mal
    # centré en hauteur ». Deux défauts, tous les deux réels.
    #
    #   - les hauteurs ne se suivaient pas : « SPÉCIALISTE GTA 6» faisait
    #     16 px, « FR » 17 et les badges de couleur 18 ;
    #   - et le texte était haut d'un pixel dans les badges de couleur. Une
    #     boîte se centre sur ascendante + descendante de la police ; des
    #     capitales n'occupent jamais la descendante.
    #
    # Le contrôle mesure le GABARIT DE CAPITALE, pas l'encre du libellé :
    # « VIDÉO » monte plus haut que « LEAK » à cause de son accent, et l'œil
    # ne juge pas là-dessus. Les métriques viennent du canvas, avec la
    # police réellement résolue — aucune valeur de police codée ici.
    centrage = page.evaluate("""() => {
        const cv = document.createElement('canvas').getContext('2d');
        return [...document.querySelectorAll(
                  '.card-src-row .tag-badge, .card-src-row .specialist-badge, '
                + '.card-src-row .official-badge')].map(el => {
            const cs = getComputedStyle(el);
            const fs = parseFloat(cs.fontSize);
            let lh = parseFloat(cs.lineHeight);
            if(!isFinite(lh)) lh = fs * 1.2;
            cv.font = cs.fontStyle + ' ' + cs.fontWeight + ' ' + fs + 'px ' + cs.fontFamily;
            const m = cv.measureText('H');
            const fa = m.fontBoundingBoxAscent, fd = m.fontBoundingBoxDescent;
            const base = parseFloat(cs.paddingTop) + parseFloat(cs.borderTopWidth)
                       + (lh - (fa + fd)) / 2 + fa;
            const h = el.getBoundingClientRect().height;
            return {nom: el.className.split(' ').pop(),
                    haut: h,
                    dessus: base - m.actualBoundingBoxAscent,
                    dessous: h - base - m.actualBoundingBoxDescent};
        });
    }""")
    check(len(centrage) >= 3,
          "[badges] la carte mesurée porte bien plusieurs badges (%d)"
          % len(centrage))
    pires = max(centrage, key=lambda b: abs(b["dessous"] - b["dessus"]))
    check(abs(pires["dessous"] - pires["dessus"]) <= 0.25,
          "[badges] les capitales sont centrées dans le badge (pire cas : "
          "%s, %.2f px au-dessus, %.2f en dessous)"
          % (pires["nom"], pires["dessus"], pires["dessous"]))
    hauteurs = sorted({round(b["haut"], 2) for b in centrage})
    check(len(hauteurs) == 1,
          "[badges] et tous les badges de la rangée font la même hauteur "
          "(%s)" % hauteurs)

    # La zone de clic est ÉLARGIE malgré tout — cinquième dérogation aux
    # 44 px, assumée : il n'y a que 6 px sous le badge avant le titre, donc
    # une zone de 44 px lui volerait des appuis. ~24 px est tout ce que la
    # place permet.
    zone = page.evaluate("""() => {
        const b = document.querySelector('.card-src-row button.tag-badge');
        if(!b) return null;
        b.scrollIntoView({block: 'center'});
        const r = b.getBoundingClientRect();
        const titre = b.closest('.card-body').querySelector('.card-title a');
        const tr = titre.getBoundingClientRect();
        const surTitre = document.elementFromPoint(tr.left + 20, tr.top + 4);
        return {
            pseudo: getComputedStyle(b, '::after').content !== 'none',
            titreIntact: !!(surTitre && surTitre.closest('.card-title')),
            badgeAtteignable: document.elementFromPoint(r.left + r.width/2, r.top + r.height/2)
                                === b,
        };
    }""")
    if zone:
        check(zone["pseudo"],
              "[badges] un pseudo-élément élargit la zone de clic du badge")
        check(zone["titreIntact"],
              "[badges] et il ne vole AUCUN appui au titre de l'article — "
              "c'est pour ça que la zone s'arrête à 24 px et pas 44")
        check(zone["badgeAtteignable"],
              "[badges] le badge lui-même reste atteignable au centre")

    # ---- 3.5 : la recherche se souvient.
    page.evaluate("""() => { recherchesRecentes = []; memoriseRecherche('date de sortie');
                             memoriseRecherche('précommande'); memoriseRecherche('lucia'); }""")
    puces = page.evaluate("[...document.querySelectorAll('.recherche-texte')].map(b => b.textContent)")
    check(puces == ["lucia", "précommande", "date de sortie"],
          "[recherche] les dernières d'abord (%s)" % puces)
    page.evaluate("memoriseRecherche('ab')")
    check(page.evaluate("recherchesRecentes.length") == 3,
          "[recherche] deux caractères ne sont pas une recherche, c'est une "
          "frappe en cours")
    page.evaluate("memoriseRecherche('LUCIA')")
    check(page.evaluate("recherchesRecentes.length") == 3
          and page.evaluate("recherchesRecentes[0]") == "LUCIA",
          "[recherche] la casse ne crée pas de doublon : « lucia » et "
          "« LUCIA » ne gâchent pas deux des quatre places")
    page.evaluate("""() => { for(const q of ['a1b','a2b','a3b','a4b','a5b']) memoriseRecherche(q); }""")
    check(page.evaluate("recherchesRecentes.length") == 4,
          "[recherche] jamais plus de quatre, sinon la rangée passe à la ligne")
    page.evaluate("relanceRecherche('lucia')")
    page.wait_for_timeout(300)
    check(page.evaluate("document.getElementById('searchInput').value") == "lucia",
          "[recherche] cliquer une puce relance la recherche")
    page.evaluate("oublieRecherche(null, 'a5b')")
    check("a5b" not in page.evaluate("recherchesRecentes"),
          "[recherche] et la croix l'oublie")
    page.evaluate("document.getElementById('searchInput').value = ''; applyFilters();")

    # ---- 3.4 : les officiels en chronologie.
    # Le plafond est remis À DESSEIN : les contrôles précédents fabriquent
    # un fil minuscule et abaissent `settings.maxDisplay` pour le rendre
    # d'un coup. Sans cette ligne, la chronologie héritait du plafond du
    # contrôle d'avant et n'affichait qu'une partie des officiels — quatre
    # échecs qui accusaient la chronologie d'un défaut appartenant au test.
    page.evaluate("""async () => {
        const r = await fetch('%s/feed.json'); const d = await r.json();
        lastItems = d.items;
        settings.maxDisplay = DEFAULT_SETTINGS.maxDisplay;
        visibleCount = DEFAULT_SETTINGS.maxDisplay;
        setTab('rockstar');
    }""" % base)
    page.wait_for_timeout(400)
    chrono = page.evaluate("""() => ({
        points: document.querySelectorAll('.chrono-point').length,
        annees: [...document.querySelectorAll('.chrono-annee')].map(e => e.textContent),
        cartes: document.querySelectorAll('.card').length,
        premier: (document.querySelector('.chrono-titre') || {}).textContent,
    })""")
    check(chrono["points"] > 0 and chrono["cartes"] == 0,
          "[chrono] l'onglet Rockstar se rend en chronologie, pas en cartes "
          "(%d points, %d cartes)" % (chrono["points"], chrono["cartes"]))
    check(len(chrono["annees"]) >= 2
          and chrono["annees"] == sorted(chrono["annees"], reverse=True),
          "[chrono] une année sépare les blocs, du plus récent au plus "
          "ancien (%s)" % chrono["annees"])

    # TOUT y est, sans pagination : une histoire se lit d'un bout à l'autre.
    attendus = page.evaluate("lastItems.filter(i => i.official).length")
    check(chrono["points"] == attendus,
          "[chrono] les %d officiels y sont TOUS, sans « charger plus » "
          "(%d rendus)" % (attendus, chrono["points"]))

    # LA VIGNETTE. La première version de la chronologie n'affichait que la
    # date, le titre et la source : j'avais mesuré que 69 des 77 officiels
    # portent une image, puis construit une forme qui les jetait toutes sans
    # le dire. Antoni l'a vu dans l'heure. Une chronologie d'annonces
    # Rockstar sans les images, c'est ce qu'elle est censée mettre en valeur.
    vignettes = page.evaluate("""() => {
        const avec = lastItems.filter(i => i.official && i.image).length;
        return {attendues: avec,
                rendues: document.querySelectorAll('.chrono-vignette').length};
    }""")
    check(vignettes["rendues"] == vignettes["attendues"] and vignettes["rendues"] > 0,
          "[chrono] chaque officiel qui a une image la montre (%d sur %d)"
          % (vignettes["rendues"], vignettes["attendues"]))
    check(page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth") is True,
          "[chrono] et la vignette ne fait pas déborder la page en largeur")

    # CE QUE LA CHRONOLOGIE AVAIT AUSSI PERDU. Les vignettes n'étaient que
    # la perte VISIBLE ; elle laissait aussi tomber les quatre boutons, les
    # badges et l'heure. Antoni a demandé de tout remettre le 13/10/2026.
    actions = page.evaluate("""() => ({
        boutons: document.querySelectorAll('.chrono-actions .card-mark').length,
        points: document.querySelectorAll('.chrono-point').length,
        // openPreview lit itemsById, que seule renderFeed remplissait : sans
        // ça le bouton 👁️ ne faisait RIEN, en silence.
        connus: [...document.querySelectorAll('.chrono-point')]
                  .every(li => itemsById[li.id.replace('card-', '')]),
        heure: /\d\d:\d\d/.test(document.querySelector('.chrono-date').textContent),
        hauteur: Math.round(document.querySelector('.chrono-point').getBoundingClientRect().height),
    })""")
    check(actions["boutons"] >= actions["points"] * 2,
          "[chrono] chaque entrée porte ses boutons d'action (%d pour %d "
          "entrées)" % (actions["boutons"], actions["points"]))
    check(actions["connus"],
          "[chrono] chaque entrée est inscrite dans itemsById — sinon le "
          "bouton d'aperçu ne fait rien, en silence")
    check(actions["heure"],
          "[chrono] la date porte l'heure : douze annonces du même jour ne "
          "s'ordonnent pas avec « 8 octobre » seul")
    # Les boutons sont COMPACTS. À la première version ils reprenaient
    # .card-actions tel quel — quatre boutons pleine largeur — et la
    # chronologie redevenait une liste de cartes avec un trait à gauche.
    check(actions["hauteur"] < 200,
          "[chrono] une entrée reste compacte malgré les boutons (%d px)"
          % actions["hauteur"])

    # Et SEULEMENT les badges qui disent quelque chose. Mesuré sur les 77
    # officiels : « OFFICIEL ROCKSTAR » serait sur 100 % des lignes et LEAK
    # sur zéro. Deux badges morts, qui ne sont pas rendus.
    badges = page.evaluate("""() => ({
        officiel: document.querySelectorAll('.chrono-point .official-badge').length,
        leak: document.querySelectorAll('.chrono-point .tag-leak').length,
        majeur: document.querySelectorAll('.chrono-point .tag-hot').length,
        video: document.querySelectorAll('.chrono-point .tag-video').length,
        vraisMajeurs: lastItems.filter(i => i.official && estMajeur(i)).length,
        vraiesVideos: lastItems.filter(i => i.official && estVideo(i)).length,
    })""")
    check(badges["officiel"] == 0 and badges["leak"] == 0,
          "[chrono] pas de badge qui serait sur toutes les lignes ou sur "
          "aucune (officiel %d, leak %d)" % (badges["officiel"], badges["leak"]))
    check(badges["majeur"] == badges["vraisMajeurs"]
          and badges["video"] == badges["vraiesVideos"],
          "[chrono] et les badges utiles y sont tous (%d majeurs, %d vidéos)"
          % (badges["majeur"], badges["video"]))

    # Le bouton marque lu SANS ouvrir l'article — c'est la seule perte qui
    # empêchait de FAIRE quelque chose.
    lien = page.evaluate("""() => {
        const li = document.querySelector('.chrono-point');
        const id = li.id.replace('card-', '');
        const item = itemsById[id];
        toggleRead(id, item.link);
        return item.link;
    }""")
    check(page.evaluate("readSet.has(%s)" % json.dumps(lien)),
          "[chrono] le bouton marque lu sans ouvrir l'article")
    page.evaluate("(l) => { readSet.delete(l); applyFilters(); }", lien)

    # Les autres onglets gardent leurs cartes.
    page.evaluate("setTab('all')")
    page.wait_for_timeout(300)
    check(page.evaluate("document.querySelectorAll('.card').length") > 0
          and page.evaluate("document.querySelectorAll('.chrono-point').length") == 0,
          "[chrono] et seul l'onglet Rockstar change de forme")

    check(not erreurs, "[badges] aucune erreur JavaScript (%s)" % erreurs[:1])
    ctx.close()


def _hex_rvb(h):
    h = h.strip().lstrip("#")
    return tuple(int(h[k:k + 2], 16) for k in (0, 2, 4))


def test_palette_du_jour_j(nav, url):
    """Palette du jour J (07/11/2026, planche A) : accent rose, chiffres cyan, dès la sortie.

    LE CYCLE DE COULEURS EST COUPÉ ICI (réglage « cycleCouleurs » à false) : le contrôle porte sur la
    palette FIXE — celle qu'on voit quand le cycle est arrêté — et sur la pulsation du premier jour, que le
    cycle remplace quand il tourne. Le cycle a son propre contrôle (test_cycle_de_couleurs).

    Antoni a choisi, sur planche, « pas de dégradé » : à partir du 19/11/2026 00:00, l'accent
    passe du bleu au rose (sombre #ff5cae, clair #b8146f) et les chiffres du compteur (tuiles de
    l'en-tête, du panneau, et ligne réduite) au cyan #19e6ff, cerné du trait sombre en clair.
    AVANT la sortie rien ne bouge : la palette tient à une classe de page, posée par
    updateCountdown, que ce contrôle lit aux trois moments qui comptent — la veille, le premier
    jour (palier « final » et sa pulsation gardés) et une semaine plus tard.
    """
    print("\n[jour J] accent rose et chiffres cyan à partir de la sortie")
    accent = {"dark": {"avant": "#58b9ff", "apres": "#ff5cae"},
              "light": {"avant": "#1d4ed8", "apres": "#b8146f"}}
    instants = (("avant", "2026-11-18T12:00:00Z"),
                ("apres", "2026-11-18T23:30:00Z"),      # 00:30 à Paris : le premier jour
                ("apres", "2026-11-25T10:00:00Z"))      # une semaine plus tard
    cyan = (25, 230, 255)
    for theme in ("dark", "light"):
        for avant_apres, instant in instants:
            etiq = "[%s %s]" % (theme, instant[:10])
            ctx = nav.new_context(viewport={"width": 390, "height": 800}, color_scheme=theme,
                                  timezone_id="Europe/Paris", reduced_motion="reduce")
            ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', "
                                "JSON.stringify({splashActif:false, cycleCouleurs:false}));}catch(e){}")
            ctx.clock.install(time=instant)
            page = ctx.new_page()
            erreurs = []
            page.on("pageerror", lambda e, err=erreurs: err.append(str(e)))
            page.goto(url, wait_until="load")
            page.wait_for_selector(".header-haut")
            page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
            page.wait_for_timeout(300)
            m = page.evaluate("""() => {
                const racine = document.documentElement;
                const b = document.querySelector('#countdown .cd-t[data-u="s"] b');
                const cs = getComputedStyle(b), cb = getComputedStyle(document.getElementById('cbCompteur'));
                return {classe: racine.classList.contains('apres-sortie'),
                        accent: getComputedStyle(racine).getPropertyValue('--accent').trim(),
                        chiffre: cs.color, trait: parseFloat(cs.webkitTextStrokeWidth) || 0,
                        traitCouleur: cs.webkitTextStrokeColor, ligne: cb.color,
                        classes: document.getElementById('countdown').className,
                        anim: getComputedStyle(document.getElementById('countdown')).animationName,
                        encre: getComputedStyle(racine).getPropertyValue('--compte-encre').trim()}; }""")
            lire = lambda css: tuple(int(x) for x in re.findall(r"\d+", css)[:3])
            attendu = accent[theme][avant_apres]
            # @property <color> : le navigateur rend --accent en rgb(), plus en #hex.
            rendu = lire(m["accent"]) if m["accent"].startswith("rgb") else _hex_rvb(m["accent"])
            check(m["classe"] == (avant_apres == "apres"),
                  "%s la page %s la classe « apres-sortie » (%s)"
                  % (etiq, "porte" if avant_apres == "apres" else "ne porte pas", m["classe"]))
            check(rendu == _hex_rvb(attendu),
                  "%s l'accent est %s (%s)" % (etiq, attendu, m["accent"]))
            if avant_apres == "apres":
                check(lire(m["chiffre"]) == cyan,
                      "%s les chiffres des tuiles sont cyan (%s)" % (etiq, m["chiffre"]))
                check(lire(m["ligne"]) == cyan,
                      "%s la ligne réduite est cyan aussi (%s)" % (etiq, m["ligne"]))
                if theme == "light":
                    check(m["trait"] >= 1 and lire(m["traitCouleur"]) == _hex_rvb(m["encre"]),
                          "%s en clair, le cyan est cerné du trait sombre (%.1f px, %s)"
                          % (etiq, m["trait"], m["traitCouleur"]))
                premier = instant.startswith("2026-11-18T23")
                check(("final" in m["classes"].split()) == premier,
                      "%s le palier « final » ne vaut que le premier jour (%s)" % (etiq, m["classes"]))
                if premier:
                    check(m["anim"] != "none",
                          "%s la pulsation du premier jour est gardée (%s)" % (etiq, m["anim"]))
                page.evaluate("() => ouvreCompteur()")
                page.wait_for_timeout(300)
                t = page.evaluate("""() => { const b = document.querySelector(
                    '#compteurContenu .tuile-panneau .countdown .cd-t:not(.zero) b');
                    return b ? getComputedStyle(b).color : null; }""")
                check(t is not None and lire(t) == cyan,
                      "%s le panneau du compteur est cyan lui aussi (%s)" % (etiq, t))
            else:
                check(lire(m["chiffre"]) != cyan,
                      "%s avant la sortie les chiffres ne sont pas cyan (%s)" % (etiq, m["chiffre"]))
            check(not erreurs, "%s aucune erreur JavaScript" % etiq)
            ctx.close()


def test_contraste_jour_j(nav, url):
    """Aucun texte sous le seuil WCAG avec la palette du jour J, dans les deux thèmes.

    Le balayage de test_contraste_partout ne voit que le fil d'AVANT la sortie. La palette du
    jour J change l'accent, qui colore la source de chaque carte, les pastilles actives, les
    liens et les graphiques : ce contrôle rejoue la MÊME sonde, sur les dix mêmes surfaces, à une
    heure d'après la sortie. Les articles sont fabriqués (dont un officiel) : le fil complet
    n'apporterait rien de plus à cette mesure et coûterait plus d'une minute.
    """
    print("\n[jour J] contraste de la palette sur toutes les surfaces")
    exceptions = list(EXCEPTIONS_CONTRASTE)
    ctx = nav.new_context(viewport={"width": 390, "height": 900}, timezone_id="Europe/Paris",
                          reduced_motion="reduce")
    ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', "
                        "JSON.stringify({splashActif:false}));}catch(e){}")
    ctx.clock.install(time="2026-11-25T10:00:00Z")
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("""() => {
        lastItems = Array.from({length: 12}, (_, i) => ({title: 'Article de test numéro ' + (i + 1),
          link: 'https://ex.test/' + i, description: 'Résumé de l\\'article ' + i,
          date: new Date(Date.now() - i * 3600e3).toISOString(), source: 'S' + (i % 3),
          official: i === 0}));
        lastNewLinks = new Set(); historyPartial = false; rattrapageLance = true; visibleCount = 30;
        readSet = new Set([lastItems[3].link, lastItems[4].link]); applyFilters(); }""")
    page.wait_for_selector(".card", state="attached", timeout=10000)
    check(page.evaluate("() => document.documentElement.classList.contains('apres-sortie')"),
          "[contraste jour J] l'horloge d'après la sortie pose bien la palette")
    for theme in ("light", "dark"):
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        total, tous = 0, []
        for nom, ouvre in SURFACES_CONTRASTE:
            page.evaluate(FERME_TOUT)
            try:
                page.evaluate(ouvre)
            except Exception as e:
                check(False, "[contraste jour J %s] la surface « %s » ne s'ouvre plus (%s)"
                      % (theme, nom, str(e).split("\n")[0][:70]))
                continue
            page.wait_for_timeout(250)
            args = [exceptions, CONTRASTE_MINIMAL, CONTRASTE_GRAND_TEXTE]
            vu = page.evaluate(SONDE_CONTRASTE, args)
            total += vu["examines"]
            if vu["ecarts"]:
                page.wait_for_timeout(500)
                encore = {(e["ou"], e["texte"]) for e in page.evaluate(SONDE_CONTRASTE, args)["ecarts"]}
                vu["ecarts"] = [e for e in vu["ecarts"] if (e["ou"], e["texte"]) in encore]
            for e in vu["ecarts"]:
                tous.append("%s · %s — %.2f:1 (exigé %.1f) sur %s, %s sur %s"
                            % (nom, e["ou"][:40], e["ratio"], e["exige"], repr(e["texte"])[:30],
                               e["couleur"], e["fond"]))
        check(not tous, "[contraste jour J %s] %d texte(s) sous le seuil WCAG : %s"
              % (theme, len(tous), " | ".join(tous[:4])))
        check(total >= 300, "[contraste jour J %s] la sonde a bien balayé — %d éléments textuels "
              "examinés, plancher 300" % (theme, total))
    ctx.close()


def test_texte_douze_px_et_coins(nav, url):
    """Planche B (07/11/2026) : aucun texte sous 12 px, et aucune boîte à texte à coins de 4 px.

    Mesuré avant la refonte, à 390 px : 207 éléments de texte à 10 ou 11 px, et quatre rayons
    (4, 8, 12 px et la pastille). Antoni a choisi 12 px au minimum et deux rayons pour les boîtes
    qui portent du texte. Les 4 px restent permis aux éléments SANS texte (favicons de 14 px,
    barres et pastilles de légende des graphiques, poignée de feuille) : ce ne sont pas des boîtes.
    Le contrôle parcourt les mêmes dix surfaces que le balayage de contraste, 320 et 390 px.
    """
    print("\n[texte] 12 px au minimum, coins de 4 px réservés aux éléments sans texte")
    for largeur in (320, 390):
        ctx = nav.new_context(viewport={"width": largeur, "height": 900}, timezone_id="Europe/Paris",
                              reduced_motion="reduce")
        ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', "
                            "JSON.stringify({splashActif:false}));}catch(e){}")
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        page.wait_for_selector("#feed", state="attached")
        page.evaluate("""() => {
            lastItems = Array.from({length: 12}, (_, i) => ({title: 'Article de test numéro ' + (i + 1),
              link: 'https://ex.test/' + i, description: 'Résumé ' + i,
              date: new Date(Date.now() - i * 3600e3).toISOString(), source: 'S' + (i % 3),
              official: i === 0}));
            lastNewLinks = new Set(); historyPartial = false; rattrapageLance = true; visibleCount = 30;
            // Des articles LUS : sans eux, la rubrique « Ma lecture » n'affiche pas sa jauge, et le <small>
            // de 10 px qu'elle porte n'était pas balayé — la CI l'a vu, pas mon poste (10/11/2026).
            readSet = new Set(lastItems.slice(0, 5).map(i => i.link));
            applyFilters(); }""")
        page.wait_for_selector(".card", state="attached", timeout=10000)
        petits, carres, examines = [], [], 0
        for nom, ouvre in SURFACES_CONTRASTE:
            page.evaluate(FERME_TOUT)
            try:
                page.evaluate(ouvre)
            except Exception:
                check(False, "[texte %d px] la surface « %s » ne s'ouvre plus" % (largeur, nom))
                continue
            page.wait_for_timeout(250)
            if nom == "stats · ma lecture":
                # La jauge de lecture (et son <small>) n'apparaît que si le fil porte des données : en
                # CI, le réseau est ouvert et l'app charge le vrai fil, pas sur un poste hors ligne.
                # On la fabrique, pour que le balayage la voie TOUJOURS.
                page.evaluate("() => document.getElementById('statsContenu')"
                              ".insertAdjacentHTML('beforeend', jaugeStats('Source de test', 3, 10))")
            r = page.evaluate("""() => {
                const petits = [], carres = []; let n = 0;
                for(const e of document.querySelectorAll('body *')){
                  const cs = getComputedStyle(e);
                  if(cs.display === 'none' || cs.visibility === 'hidden' || !e.getClientRects().length) continue;
                  const aTexte = [...e.childNodes].some(c => c.nodeType === 3 && c.textContent.trim());
                  if(aTexte){
                    n++;
                    if(parseFloat(cs.fontSize) < 12 - 0.01)
                      petits.push(e.tagName.toLowerCase() + '.' + [...e.classList].join('.') + ' ' + cs.fontSize);
                  }
                  if(cs.borderTopLeftRadius === '4px' && e.textContent.trim())
                    carres.push(e.tagName.toLowerCase() + '.' + [...e.classList].join('.'));
                }
                return {petits, carres, n}; }""")
            examines += r["n"]
            petits += [nom + " : " + x for x in r["petits"]]
            carres += [nom + " : " + x for x in r["carres"]]
        check(not petits, "[texte %d px] aucun texte sous 12 px (%d fautif(s) : %s)"
              % (largeur, len(petits), " | ".join(sorted(set(petits))[:4])))
        check(not carres, "[texte %d px] aucune boîte à texte aux coins de 4 px (%d : %s)"
              % (largeur, len(carres), " | ".join(sorted(set(carres))[:4])))
        check(examines >= 300, "[texte %d px] le balayage a bien balayé — %d éléments à texte, "
              "plancher 300" % (largeur, examines))
        ctx.close()


def test_balayage_des_cartes(nav, url):
    """Balayage d'une carte (07/11/2026, planche C) : droite = lu, gauche = mis de côté.

    Avant, les DEUX sens faisaient lu / non lu. Antoni a choisi, sur planche, de garder « lu »
    vers la droite et de mettre l'étoile (« Mettre de côté ») vers la gauche. Le contrôle fait de
    VRAIS gestes tactiles par le protocole du navigateur : un geste franc dans chaque sens, le
    même geste répété (qui doit défaire), un geste trop court et un défilement vertical (qui ne
    doivent rien faire), puis l'indice que la carte révèle — l'icône « lu » à droite, l'étoile à
    gauche, jamais les deux.
    """
    print("\n[balayage] droite = lu, gauche = mis de côté")
    ctx = nav.new_context(viewport={"width": 390, "height": 800}, has_touch=True, is_mobile=True,
                          timezone_id="Europe/Paris", reduced_motion="reduce")
    ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', "
                        "JSON.stringify({splashActif:false}));}catch(e){}")
    page = ctx.new_page()
    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("""() => {
        lastItems = Array.from({length: 8}, (_, i) => ({title: 'Article de test numéro ' + (i + 1),
          link: 'https://ex.test/' + i, description: 'Résumé ' + i,
          date: new Date(Date.now() - i * 3600e3).toISOString(), source: 'S' + (i % 3)}));
        lastNewLinks = new Set(); historyPartial = false; rattrapageLance = true; visibleCount = 30;
        readSet = new Set(); misDeCote = new Set(); applyFilters(); }""")
    page.wait_for_selector(".card", state="attached", timeout=10000)
    cdp = ctx.new_cdp_session(page)

    def touche(type_, x=0, y=0):
        pts = [{"x": x, "y": y}] if type_ != "touchEnd" else []
        cdp.send("Input.dispatchTouchEvent", {"type": type_, "touchPoints": pts})

    def centre(i):
        page.evaluate("(i) => document.querySelector('.card[data-lien=\"https://ex.test/' + i + '\"]')"
                      ".scrollIntoView({block: 'center'})", i)
        page.wait_for_timeout(150)
        return page.evaluate("(i) => { const r = document.querySelector('.card[data-lien=\"https://ex.test/'"
                             " + i + '\"]').getBoundingClientRect(); return [r.left + r.width / 2, "
                             "r.top + 40]; }", i)

    def glisse(i, dx, dy=0, relache=True, pas=8):
        x, y = centre(i)
        touche("touchStart", x, y)
        for k in range(1, pas + 1):
            touche("touchMove", x + dx * k / pas, y + dy * k / pas)
        if relache:
            touche("touchEnd")
            page.wait_for_timeout(700)

    def etat(i):
        return page.evaluate("(i) => { const l = 'https://ex.test/' + i; return "
                             "{lu: readSet.has(l), cote: misDeCote.has(l)}; }", i)

    glisse(0, 140)
    e = etat(0)
    check(e["lu"] and not e["cote"], "[balayage] un geste franc vers la DROITE marque lu (%s)" % e)
    glisse(1, -140)
    e = etat(1)
    check(e["cote"] and not e["lu"],
          "[balayage] un geste franc vers la GAUCHE met de côté, sans marquer lu (%s)" % e)
    glisse(1, -140)
    e = etat(1)
    check(not e["cote"] and not e["lu"],
          "[balayage] le même geste vers la gauche retire de la liste (%s)" % e)
    glisse(0, 140)
    check(not etat(0)["lu"], "[balayage] le même geste vers la droite remet en non lu")
    glisse(2, 30)
    glisse(2, -30)
    e = etat(2)
    check(not e["lu"] and not e["cote"], "[balayage] un geste trop court ne fait rien (%s)" % e)
    glisse(3, 10, dy=160)
    e = etat(3)
    check(not e["lu"] and not e["cote"], "[balayage] un défilement vertical ne fait rien (%s)" % e)

    # L'indice que la carte révèle, mesuré PENDANT le geste (relâché sous le seuil : aucune action).
    for sens, dx, visible, cache in (("droite", 40, "sh-lu", "sh-cote"), ("gauche", -40, "sh-cote", "sh-lu")):
        glisse(4, dx, relache=False)
        m = page.evaluate("""([v, c]) => {
            const w = document.querySelector('.swipe-wrap.glisse');
            if(!w) return null;
            const d = x => getComputedStyle(w.querySelector('.' + x)).display;
            return {visible: d(v), cache: d(c), fond: getComputedStyle(w.querySelector('.swipe-hint')).backgroundColor,
                    gauche: w.classList.contains('vers-gauche')}; }""", [visible, cache])
        touche("touchEnd")
        page.wait_for_timeout(500)
        check(m is not None and m["visible"] != "none" and m["cache"] == "none"
              and m["gauche"] == (sens == "gauche"),
              "[balayage] vers la %s l'indice montre « %s » et cache « %s » (%s)"
              % (sens, visible, cache, m))
        check(etat(4) == {"lu": False, "cote": False},
              "[balayage] un geste relâché sous le seuil, vers la %s, n'a rien changé (%s)"
              % (sens, etat(4)))
    check(not erreurs, "[balayage] aucune erreur JavaScript")
    ctx.close()


JOUR_J = "2026-11-18T23:30:00Z"      # 00:30 à Paris le 19/11 : le premier jour
VEILLE = "2026-11-12T10:00:00Z"
LENDEMAIN = "2026-11-20T10:00:00Z"   # plus de 24 h après la sortie


def _ouvre_cycle(nav, url, instant, reglage="auto", theme="dark", reduit=False):
    """Ouvre l'app à `instant`, avec le réglage « cycleCouleurs » demandé (« auto » : jamais touché)."""
    ctx = nav.new_context(viewport={"width": 390, "height": 800}, color_scheme=theme,
                          timezone_id="Europe/Paris",
                          reduced_motion="reduce" if reduit else "no-preference")
    corps = {"splashActif": False}
    if reglage != "auto":
        corps["cycleCouleurs"] = reglage
    # Posé SEULEMENT si le stockage est vide : un script d'initialisation rejoué à chaque chargement
    # effaçait ce que la page venait d'enregistrer, et le contrôle de persistance échouait à tort.
    ctx.add_init_script("try{if(!localStorage.getItem('gta6watch:settings-v1'))"
                        "localStorage.setItem('gta6watch:settings-v1', JSON.stringify(%s));}catch(e){}"
                        % json.dumps(corps))
    ctx.clock.install(time=instant)
    page = ctx.new_page()
    erreurs = []
    page.on("pageerror", lambda e, err=erreurs: err.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector(".header-haut")
    page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
    page.wait_for_timeout(300)
    return ctx, page, erreurs


SONDE_ANIMATIONS = """() => {
    const noms = {};
    for(const a of document.getAnimations()){
      const n = a.animationName || '';
      if(n) noms[n] = (noms[n] || 0) + 1;
    }
    const pause = () => document.getAnimations().length;
    return {noms, classe: document.documentElement.classList.contains('cycle-couleur'),
            pulsation: !!document.getElementById('countdown') && document.getAnimations()
              .some(a => a.animationName === 'countdown-pulse' && a.effect.target === document.getElementById('countdown'))}; }"""

# Ne gèle QUE les animations du cycle. Geler « toutes » les animations mettait aussi en pause les
# TRANSITIONS de couleur (onglets, boutons) et les ramenait à leur premier instant : à 0 ms, l'onglet
# « Robot » était lu blanc sur blanc (1,00:1), un contraste qui n'a jamais existé à l'écran. L'erreur
# était celle de l'instrument, et la CI l'a trouvée avant moi (10/11/2026).
FIGE = """(t) => { for(const a of document.getAnimations()){
    const n = a.animationName;
    if(n && (n === 'cycle-accent' || n.startsWith('cycle-chiffre'))){ a.pause(); a.currentTime = t; } } }"""

LIT_COULEURS = """() => {
    // L'accent est animé sur des blocs, pas sur <html> : on le lit sur l'en-tête.
    const racine = getComputedStyle(document.querySelector('header.console'));
    const b = [...document.querySelectorAll('#countdown .cd-t:not(.zero) b')];
    return {accent: racine.getPropertyValue('--accent').trim(),
            chiffres: b.map(e => getComputedStyle(e).color),
            delais: b.map(e => { const a = e.getAnimations().find(x => x.animationName.startsWith('cycle-chiffre'));
                                  return a ? a.effect.getTiming().delay : null; })}; }"""


def test_cycle_de_couleurs(nav, url):
    """Cycle de couleurs (10/11/2026) : d'office le jour J, activable et coupable dans les Réglages.

    Demandé par Antoni : l'accent et les chiffres du compteur passent en boucle du cyan au rose.
    Actif d'office le jour de la sortie (24 h), allumable n'importe quel autre jour, coupable même
    le jour J. Le contrôle lit les ANIMATIONS EN COURS du navigateur (pas seulement une classe) :
    un cycle qui ne tourne pas serait indiscernable d'un cycle qui tourne. Puis il fige le temps
    pour lire les couleurs aux instants clés, vérifie que les tuiles se décalent (en vague), que la
    pulsation d'opacité est remplacée, que « réduire les animations » l'arrête, et que le réglage
    n'est écrit que par la case — pas par l'enregistrement d'un AUTRE champ, qui aurait figé le
    réglage « automatique » sur « non » dès la première sauvegarde hors du jour J.
    """
    print("\n[cycle] actif d'office le jour J, allumable, coupable")
    lire = lambda css: tuple(int(x) for x in re.findall(r"\d+", css)[:3])
    cas = (
        ("la veille, réglage jamais touché", VEILLE, "auto", False),
        ("le jour J, réglage jamais touché", JOUR_J, "auto", True),
        ("le jour J, coupé par Antoni", JOUR_J, False, False),
        ("la veille, allumé par Antoni", VEILLE, True, True),
        ("le lendemain (24 h passées), jamais touché", LENDEMAIN, "auto", False),
        ("le lendemain, allumé par Antoni", LENDEMAIN, True, True),
    )
    for nom, instant, reglage, attendu in cas:
        ctx, page, erreurs = _ouvre_cycle(nav, url, instant, reglage)
        m = page.evaluate(SONDE_ANIMATIONS)
        check(m["classe"] == attendu, "[cycle] %s : classe « cycle-couleur » %s (%s)"
              % (nom, "posée" if attendu else "absente", m["classe"]))
        check(("cycle-accent" in m["noms"]) == attendu,
              "[cycle] %s : l'animation de l'accent %s (%s)"
              % (nom, "tourne" if attendu else "ne tourne pas", sorted(m["noms"])))
        tuiles = sum(v for k, v in m["noms"].items() if k.startswith("cycle-chiffre"))
        check((tuiles > 0) == attendu, "[cycle] %s : %d animation(s) de chiffres" % (nom, tuiles))
        if instant == JOUR_J:
            check(m["pulsation"] == (not attendu),
                  "[cycle] %s : la pulsation d'opacité du premier jour %s"
                  % (nom, "est remplacée" if attendu else "est gardée"))
        check(not erreurs, "[cycle] %s : aucune erreur JavaScript" % nom)
        ctx.close()

    # LES COULEURS AUX INSTANTS CLÉS, dans les deux thèmes.
    attendu = {"dark": {0: (25, 230, 255), 1000: (179, 136, 255), 2000: (255, 92, 174)},
               "light": {0: (0, 112, 138), 1000: (124, 58, 237), 2000: (184, 20, 111)}}
    for theme in ("dark", "light"):
        ctx, page, _ = _ouvre_cycle(nav, url, JOUR_J, "auto", theme)
        for t, rvb in attendu[theme].items():
            page.evaluate(FIGE, t)
            page.wait_for_timeout(120)
            c = page.evaluate(LIT_COULEURS)
            ecart = max(abs(a - b) for a, b in zip(lire(c["accent"]), rvb))
            check(ecart <= 3, "[cycle %s] à %d ms l'accent est %s (%s)" % (theme, t, rvb, c["accent"]))
        # La tuile des jours est grisée à zéro le premier jour : la première tuile VIVANTE n'est pas la
        # première du cycle. On fige donc au moment où SON décalage la ramène au début du cycle.
        c = page.evaluate(LIT_COULEURS)
        d0 = c["delais"][0] if c["delais"] else None
        rang = round(-d0 / 450) if d0 is not None else 0
        page.evaluate(FIGE, (4000 - 450 * rang) % 4000)
        page.wait_for_timeout(120)
        c = page.evaluate(LIT_COULEURS)
        check(len(c["chiffres"]) >= 3 and lire(c["chiffres"][0]) == (25, 230, 255),
              "[cycle %s] la première tuile vivante est cyan au début de SON cycle (%s)"
              % (theme, c["chiffres"][:1]))
        check(len(set(c["chiffres"])) == len(c["chiffres"]),
              "[cycle %s] les tuiles sont décalées, en vague (%s)" % (theme, c["chiffres"]))
        d = c["delais"]
        check(all(x is not None for x in d) and all(abs((d[i + 1] - d[i]) + 450) < 1 for i in range(len(d) - 1)),
              "[cycle %s] 450 ms de décalage entre deux tuiles (%s)" % (theme, d))
        ctx.close()

    # RÉDUIRE LES ANIMATIONS : la classe est posée, rien ne bouge, et le réglage le dit.
    ctx, page, _ = _ouvre_cycle(nav, url, JOUR_J, "auto", "dark", reduit=True)
    m = page.evaluate(SONDE_ANIMATIONS)
    check(not any(k.startswith("cycle") for k in m["noms"]),
          "[cycle] animations réduites : aucune animation du cycle ne tourne (%s)" % sorted(m["noms"]))
    page.evaluate("() => openSettings()")
    existe = page.evaluate("() => !!document.getElementById('cycleCouleurs')")
    check(existe, "[cycle] la case « Couleurs animées » existe dans les Réglages")
    if not existe:
        ctx.close()
        return
    check(page.evaluate("() => !document.getElementById('cycleCouleursReduit').hidden"),
          "[cycle] animations réduites : les Réglages le disent")
    ctx.close()

    # LA CASE.
    def stocke(page):
        return page.evaluate("() => { const s = JSON.parse(localStorage.getItem('gta6watch:settings-v1') || '{}');"
                             " return Object.prototype.hasOwnProperty.call(s, 'cycleCouleurs') ? s.cycleCouleurs : 'absent'; }")

    # Hors du jour J : décochée ; l'allumer écrit true, pose la classe, survit au rechargement.
    ctx, page, _ = _ouvre_cycle(nav, url, VEILLE, "auto")
    page.evaluate("() => openSettings()")
    check(not page.evaluate("() => document.getElementById('cycleCouleurs').checked"),
          "[cycle] la veille la case est décochée")
    check(not page.evaluate("() => document.getElementById('cycleCouleursReduit').hidden === false"),
          "[cycle] sans animations réduites, la note est cachée")
    page.evaluate("() => { const c = document.getElementById('splashActif'); c.checked = !c.checked; c.onchange(); }")
    check(stocke(page) in ("absent", None),
          "[cycle] enregistrer un AUTRE champ n'écrit pas le cycle — le réglage reste automatique (%s)" % stocke(page))
    page.evaluate("() => { const c = document.getElementById('cycleCouleurs'); c.checked = true; c.onchange(); }")
    check(stocke(page) is True and page.evaluate("() => document.documentElement.classList.contains('cycle-couleur')"),
          "[cycle] cocher écrit true et lance le cycle tout de suite (%s)" % stocke(page))
    page.reload(wait_until="load")
    page.wait_for_selector(".header-haut")
    check(page.evaluate("() => document.documentElement.classList.contains('cycle-couleur')"),
          "[cycle] allumé la veille, il tourne encore après un rechargement")
    page.evaluate("() => openSettings()")
    check(page.evaluate("() => document.getElementById('cycleCouleurs').checked"),
          "[cycle] et la case reste cochée")
    ctx.close()

    # Le jour J : cochée d'office ; la couper écrit false ; un AUTRE champ ne touche à rien.
    ctx, page, _ = _ouvre_cycle(nav, url, JOUR_J, "auto")
    page.evaluate("() => openSettings()")
    check(page.evaluate("() => document.getElementById('cycleCouleurs').checked"),
          "[cycle] le jour J la case est cochée d'office")
    page.evaluate("() => { const c = document.getElementById('splashActif'); c.checked = !c.checked; c.onchange(); }")
    check(stocke(page) in ("absent", None) and page.evaluate("() => document.documentElement.classList.contains('cycle-couleur')"),
          "[cycle] le jour J, enregistrer un autre champ laisse le cycle automatique (%s)" % stocke(page))
    page.evaluate("() => { const c = document.getElementById('cycleCouleurs'); c.checked = false; c.onchange(); }")
    check(stocke(page) is False
          and not page.evaluate("() => document.documentElement.classList.contains('cycle-couleur')"),
          "[cycle] décocher le jour J écrit false et arrête le cycle (%s)" % stocke(page))
    page.wait_for_timeout(1500)
    page.evaluate("() => updateCountdown()")
    check(not page.evaluate("() => document.documentElement.classList.contains('cycle-couleur')"),
          "[cycle] et le battement du compteur ne le rallume pas")
    ctx.close()


def test_cycle_synchronise(nav, url):
    """Toutes les animations du cycle partagent l'horloge du document, y compris celles d'un bloc apparu plus tard.

    SUR L'HORLOGE RÉELLE, et c'est l'enseignement du 10/11/2026. Ce contrôle était d'abord posé dans
    test_cycle_de_couleurs, qui installe l'horloge simulée de Playwright : sous elle, toutes les animations
    démarrent à la même date (0) sans qu'AUCUN recalage n'ait lieu, et le contrôle restait vert avec le
    recalage supprimé. Prouvé : les deux affectations neutralisées, 53 sur 53. Ici, pas d'horloge simulée :
    la page des Réglages, puis le panneau du compteur (processeur ralenti ×6, comme une CI lente) démarrent
    leurs animations bien APRÈS l'en-tête, et doivent se retrouver sur la même phase.
    """
    print("\n[cycle] toutes les animations partagent l'horloge du document (horloge réelle)")
    ctx = nav.new_context(viewport={"width": 390, "height": 800}, timezone_id="Europe/Paris",
                          reduced_motion="no-preference")
    ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', "
                        "JSON.stringify({splashActif:false, cycleCouleurs:true}));}catch(e){}")
    page = ctx.new_page()
    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector(".header-haut")
    page.wait_for_timeout(1500)
    HORS_PHASE = """() => document.getAnimations().filter(a => (a.animationName === 'cycle-accent'
        || (a.animationName || '').startsWith('cycle-chiffre')) && a.playState === 'running' && a.startTime !== 0).length"""
    check(page.evaluate(HORS_PHASE) == 0 and page.evaluate("() => document.getAnimations().some(a => a.animationName === 'cycle-accent')"),
          "[cycle synchro] l'en-tête et les barres tournent, tous sur l'horloge du document")
    page.evaluate("() => openSettings()")
    page.wait_for_timeout(1800)
    sur_reglages = page.evaluate("""() => document.getAnimations().some(a => a.animationName === 'cycle-accent'
        && a.effect.target && a.effect.target.classList.contains('page-reglages'))""")
    hors = page.evaluate(HORS_PHASE)
    check(sur_reglages and hors == 0,
          "[cycle synchro] la page des Réglages, apparue après l'en-tête, est sur la même horloge (%d hors phase)" % hors)
    cdp = ctx.new_cdp_session(page)
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": 6})
    page.evaluate("() => { closeSettings(); ouvreCompteur(); }")
    page.wait_for_timeout(1800)
    page.screenshot(clip={"x": 0, "y": 0, "width": 1, "height": 1})
    page.wait_for_timeout(600)
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})
    j = page.evaluate("""() => { const f = sel => [...document.querySelectorAll(sel)]
          .map(b => b.getAnimations().find(a => a.animationName.startsWith('cycle-chiffre')));
        const h = f('#countdown .cd-t:not(.zero) b'), p = f('#compteurContenu .tuile-panneau .cd-t:not(.zero) b');
        const t = a => a.currentTime - a.effect.getTiming().delay * -1;
        return {n: p.length, ecarts: p.map((a, i) => a && h[i] ? Math.abs(t(a) - t(h[i])) : null)}; }""")
    check(j["n"] >= 3 and all(e is not None and e < 120 for e in j["ecarts"]),
          "[cycle synchro] le panneau du compteur, ouvert longtemps après l'en-tête, est en phase avec lui (écarts en ms : %s)"
          % j["ecarts"])
    check(not erreurs, "[cycle synchro] aucune erreur JavaScript")
    ctx.close()


def test_contraste_du_cycle(nav, url):
    """Aucun texte sous le seuil WCAG à AUCUN instant du cycle, dans les deux thèmes.

    L'accent bouge : un contraste mesuré à l'instant 0 ne dit rien du violet, ni du rose. Le
    contrôle fige les animations à cinq instants (0, 500, 1000, 1500, 2000 ms : le cycle est
    symétrique, la suite est son image) et passe la MÊME sonde que test_contraste_partout sur les
    dix mêmes surfaces. Le jour J, avec les articles fabriqués d'un fil ordinaire.
    """
    print("\n[cycle] contraste à cinq instants du cycle, dix surfaces, deux thèmes")
    exceptions = list(EXCEPTIONS_CONTRASTE)
    for theme in ("light", "dark"):
        ctx, page, _ = _ouvre_cycle(nav, url, JOUR_J, "auto", theme)
        page.evaluate("""() => {
            lastItems = Array.from({length: 12}, (_, i) => ({title: 'Article de test numéro ' + (i + 1),
              link: 'https://ex.test/' + i, description: 'Résumé ' + i,
              date: new Date(Date.now() - i * 3600e3).toISOString(), source: 'S' + (i % 3), official: i === 0}));
            lastNewLinks = new Set(); historyPartial = false; rattrapageLance = true; visibleCount = 30;
            readSet = new Set([lastItems[3].link]); applyFilters(); }""")
        page.wait_for_selector(".card", state="attached", timeout=10000)
        total, tous = 0, []
        for nom, ouvre in SURFACES_CONTRASTE:
            page.evaluate(FERME_TOUT)
            try:
                page.evaluate(ouvre)
            except Exception as e:
                check(False, "[cycle %s] la surface « %s » ne s'ouvre plus (%s)"
                      % (theme, nom, str(e).split("\n")[0][:70]))
                continue
            page.wait_for_timeout(250)
            # Les transitions en cours doivent être FINIES avant de mesurer (onglet qui vient de changer d'état).
            page.evaluate("() => Promise.all(document.getAnimations().filter(a => a.transitionProperty)"
                          ".map(a => a.finished.catch(() => {})))")
            for t in (0, 500, 1000, 1500, 2000):
                page.evaluate(FIGE, t)
                page.wait_for_timeout(100)
                args = [exceptions, CONTRASTE_MINIMAL, CONTRASTE_GRAND_TEXTE]
                vu = page.evaluate(SONDE_CONTRASTE, args)
                total += vu["examines"]
                for e in vu["ecarts"]:
                    tous.append("%s @%d ms · %s — %.2f:1 (exigé %.1f) sur %s, %s sur %s"
                                % (nom, t, e["ou"][:40], e["ratio"], e["exige"], repr(e["texte"])[:30],
                                   e["couleur"], e["fond"]))
        check(not tous, "[cycle %s] %d texte(s) sous le seuil WCAG : %s"
              % (theme, len(tous), " | ".join(tous[:4])))
        check(total >= 1500, "[cycle %s] la sonde a bien balayé — %d relevés, plancher 1500" % (theme, total))
        ctx.close()


def test_stats_et_reglages_compacts(nav, url):
    """Stats et Réglages sans l'en-tête déplié (planche du 10/11/2026).

    L'en-tête déplié prenait environ 268 px sur 844 en haut de chaque page, et se répétait. Sur Stats
    et Réglages il est masqué et la ligne réduite (titre, point vert, compteur) prend le relais ;
    sur le fil il revient. Mesuré dans un vrai navigateur, à 320 et 390 px : l'en-tête est caché,
    la ligne réduite visible, le premier contenu commence SOUS elle (pas dessous), l'onglet
    « Lecture » tient sur une ligne comme ses voisins, « Dans le fil » range ses chiffres sur deux
    colonnes, la phrase d'aide n'apparaît qu'une fois — et un toucher sur une colonne y écrit encore
    sa valeur sans faire sauter le graphique sous le doigt.
    """
    print("\n[compact] Stats et Réglages : en-tête masqué, ligne réduite, grille 2×2, une seule phrase")
    for largeur, schema in ((320, "dark"), (390, "light")):
        etiq = "[%d px, %s]" % (largeur, schema)
        ctx = nav.new_context(viewport={"width": largeur, "height": 800}, color_scheme=schema,
                              timezone_id="Europe/Paris", reduced_motion="reduce")
        ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));}catch(e){}")
        page = ctx.new_page()
        erreurs = []
        page.on("pageerror", lambda e, err=erreurs: err.append(str(e)))
        page.goto(url, wait_until="load")
        page.wait_for_selector(".header-haut")
        base = page.evaluate("""() => { const r = document.querySelector('header.console').getBoundingClientRect();
            return {affiche: getComputedStyle(document.querySelector('header.console')).display !== 'none',
                    barre: document.getElementById('barreHaut').classList.contains('visible')}; }""")
        check(base["affiche"] and not base["barre"],
              "%s sur le fil : l'en-tête est déplié, la ligne réduite absente" % etiq)
        # ---- Stats
        page.evaluate("() => { lastItems = Array.from({length: 240}, (_, i) => ({title: 'Article ' + i, link: 'https://ex.test/' + i,"
                      " description: 'R', date: new Date(Date.now() - i * 7200e3).toISOString(), source: 'S' + (i % 4)}));"
                      " lastNewLinks = new Set(); historyPartial = false; rattrapageLance = true; archiveChargee = true; applyFilters(); }")
        page.evaluate("() => { setTab('stats'); ongletStatsChoisi('fil'); }")
        page.wait_for_timeout(500)
        m = page.evaluate("""() => {
            const q = s => document.querySelector(s), r = e => e.getBoundingClientRect();
            const barre = r(q('#barreHaut'));
            const onglets = [...document.querySelectorAll('.stats-onglets button')].map(b => Math.round(r(b).height));
            const chiffres = [...document.querySelectorAll('.stats-chiffres')].map(g => {
              const t = [...g.children].map(c => Math.round(r(c).top));
              return {colonnes: getComputedStyle(g).gridTemplateColumns.split(' ').length, rangees: new Set(t).size, n: t.length};
            });
            const phrases = [...document.querySelectorAll('.stats-lecture')].map(p => p.textContent.trim());
            return {entete: getComputedStyle(q('header.console')).display, visible: q('#barreHaut').classList.contains('visible'),
                    bh: document.body.classList.contains('bh-visible'), barreBas: barre.bottom,
                    premier: r(q('.stats-onglets')).top, onglets, chiffres, phrases,
                    etiquette: q('#statsOnglet_moi').textContent.trim(),
                    deborde: document.documentElement.scrollWidth > innerWidth}; }""")
        check(m["entete"] == "none" and m["visible"] and m["bh"],
              "%s Stats : en-tête masqué, ligne réduite et classe bh-visible posées (%s, %s, %s)"
              % (etiq, m["entete"], m["visible"], m["bh"]))
        check(m["premier"] >= m["barreBas"] - 0.5,
              "%s Stats : le premier contenu commence sous la ligne réduite (%.0f px, barre jusqu'à %.0f px)"
              % (etiq, m["premier"], m["barreBas"]))
        check(m["etiquette"] == "Lecture" and len(set(m["onglets"])) == 1,
              "%s Stats : l'onglet « %s » tient sur une ligne comme les trois autres (hauteurs %s)"
              % (etiq, m["etiquette"], m["onglets"]))
        c0 = m["chiffres"][0] if m["chiffres"] else None
        check(c0 is not None and c0["colonnes"] == 2 and c0["rangees"] == (c0["n"] + 1) // 2,
              "%s Stats : « Dans le fil » range ses %s chiffres sur deux colonnes (%s)"
              % (etiq, c0 and c0["n"], c0))
        remplies = [x for x in m["phrases"] if x]
        check(len(m["phrases"]) >= 3 and len(remplies) == 1 and remplies[0].startswith("Touche"),
              "%s Stats : la phrase d'aide n'apparaît qu'une fois sur %d graphiques (%d)"
              % (etiq, len(m["phrases"]), len(remplies)))
        check(not m["deborde"], "%s Stats : rien ne déborde à droite" % etiq)
        # Un toucher sur une colonne d'un graphique SANS phrase : la valeur s'écrit, le graphique ne bouge pas.
        t = page.evaluate("""() => {
            const blocs = [...document.querySelectorAll('.stats-bloc')].filter(b => b.querySelector('.stats-lecture')
              && b.querySelector('.stats-col, .stats-case'));
            const b = blocs[1]; if(!b) return null;
            const col = b.querySelector('.stats-col, .stats-case'), haut = b.querySelector('.stats-histo, .stats-carte') || col;
            b.scrollIntoView({block: 'center'});
            const avant = haut.getBoundingClientRect().top + scrollY;
            col.click();
            const apres = haut.getBoundingClientRect().top + scrollY;
            return {texte: b.querySelector('.stats-lecture').textContent.trim(), saut: Math.abs(apres - avant)}; }""")
        check(t is not None and t["texte"] != "" and t["saut"] < 1,
              "%s Stats : toucher une colonne d'un graphique sans phrase y écrit sa valeur, sans faire sauter le graphique (%s)"
              % (etiq, t))
        # ---- Réglages
        page.evaluate("() => openSettings()")
        page.wait_for_timeout(400)
        r_ = page.evaluate("""() => { const q = s => document.querySelector(s), r = e => e.getBoundingClientRect();
            return {entete: getComputedStyle(q('header.console')).display, visible: q('#barreHaut').classList.contains('visible'),
                    barreBas: r(q('#barreHaut')).bottom, titre: r(q('#settingsOverlay .settings-title, #settingsOverlay h2, #settingsOverlay')).top}; }""")
        check(r_["entete"] == "none" and r_["visible"],
              "%s Réglages : en-tête masqué et ligne réduite visible (%s, %s)" % (etiq, r_["entete"], r_["visible"]))
        check(r_["titre"] >= r_["barreBas"] - 0.5,
              "%s Réglages : la page commence sous la ligne réduite (%.0f px, barre jusqu'à %.0f px)"
              % (etiq, r_["titre"], r_["barreBas"]))
        # ---- retour au fil
        page.evaluate("() => retourFil()")
        page.wait_for_timeout(400)
        f = page.evaluate("""() => ({entete: getComputedStyle(document.querySelector('header.console')).display,
            barre: document.getElementById('barreHaut').classList.contains('visible'),
            stats: document.body.classList.contains('vue-stats'), reg: document.body.classList.contains('vue-reglages')})""")
        check(f["entete"] != "none" and not f["barre"] and not f["stats"] and not f["reg"],
              "%s retour au fil : l'en-tête est déplié, la ligne réduite repart, plus de classe de vue (%s)" % (etiq, f))
        check(not erreurs, "%s aucune erreur JavaScript" % etiq)
        ctx.close()


# ---------------------------------------------------------------------------------------------
# LES EFFETS DU JOUR J (11/11/2026) : le cadre autour de l'écran, la barre d'état, l'écran de
# chargement, les commandes, le halo de minuit, J+N. Chacun a sa case ; même règle que le cycle.
# ---------------------------------------------------------------------------------------------
EFFETS = ("cycleCouleurs", "coulCadre", "coulBarreEtat", "coulChargement", "coulControles", "coulBordures")


def _ouvre_effets(nav, url, instant, corps=None, theme="dark", reduit=False, splash=False, reprend=False):
    """Ouvre l'app à `instant` avec les réglages `corps` (les clés absentes restent « automatique »)."""
    ctx = nav.new_context(viewport={"width": 390, "height": 800}, color_scheme=theme,
                          timezone_id="Europe/Paris",
                          reduced_motion="reduce" if reduit else "no-preference")
    reglages = {"splashActif": bool(splash)}
    reglages.update(corps or {})
    ctx.add_init_script("try{if(!localStorage.getItem('gta6watch:settings-v1'))"
                        "localStorage.setItem('gta6watch:settings-v1', JSON.stringify(%s));}catch(e){}"
                        % json.dumps(reglages))
    ctx.clock.install(time=instant)
    page = ctx.new_page()
    erreurs = []
    page.on("pageerror", lambda e, err=erreurs: err.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector(".header-haut")
    page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
    if reprend:
        page.clock.resume()
    page.wait_for_timeout(300)
    return ctx, page, erreurs


ETAT_EFFETS = """() => {
    const h = document.documentElement, cadre = document.getElementById('cadreEcran');
    const cs = getComputedStyle(cadre), r = cadre.getBoundingClientRect();
    const sous = document.querySelector('.compteur-sous');
    const cb = document.querySelector('#sourceList input[type=checkbox]') || document.querySelector('input[type=checkbox]');
    const meta = document.querySelector('meta[name=theme-color]');
    return {
      cadre: h.classList.contains('cadre-j'), controles: h.classList.contains('controles-j'),
      cycle: h.classList.contains('cycle-couleur'),
      cadreDisplay: cs.display, cadrePos: cs.position, cadreEvents: cs.pointerEvents,
      cadreZ: parseInt(cs.zIndex, 10), cadreRect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      cadreAnim: cadre.getAnimations().map(a => a.animationName),
      sous: sous ? sous.textContent : null,
      sousRempli: sous ? getComputedStyle(sous).webkitTextFillColor : null,
      sousCouleur: sous ? getComputedStyle(sous).color : null,
      accentCase: cb ? getComputedStyle(cb).accentColor : null,
      barre: meta ? meta.getAttribute('content') : null,
      erreur: window.__erreurEffets || null };
  }"""


def _melange(fond, teinte, part):
    a = [int(fond[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(teinte[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join("%02x" % int(x + (y - x) * part + 0.5) for x, y in zip(a, b))


def _lum(rgb):
    def c(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * c(rgb[0]) + 0.7152 * c(rgb[1]) + 0.0722 * c(rgb[2])


def _rapport(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_effets_du_jour_j(nav, url):
    print("\n[jour J] effets : cadre, barre d'état, chargement, commandes, halo, J+N — chacun avec sa case")
    FOND_SOMBRE = "#0a0d0f"
    # 1. Qui est actif quand : la veille (rien), le jour J (tout), le lendemain (rien), et chaque case seule.
    cas = (
        ("la veille, rien touché", VEILLE, {}, dict(cadre=False, controles=False, cycle=False)),
        ("le jour J, rien touché", JOUR_J, {}, dict(cadre=True, controles=True, cycle=True)),
        ("le lendemain, rien touché", LENDEMAIN, {}, dict(cadre=False, controles=False, cycle=False)),
        ("le jour J, cadre coupé", JOUR_J, {"coulCadre": False}, dict(cadre=False, controles=True, cycle=True)),
        ("le jour J, commandes coupées", JOUR_J, {"coulControles": False}, dict(cadre=True, controles=False, cycle=True)),
        ("la veille, cadre allumé seul", VEILLE, {"coulCadre": True}, dict(cadre=True, controles=False, cycle=False)),
        ("la veille, commandes allumées seules", VEILLE, {"coulControles": True}, dict(cadre=False, controles=True, cycle=False)),
    )
    for nom, instant, corps, attendu in cas:
        ctx, page, erreurs = _ouvre_effets(nav, url, instant, corps)
        e = page.evaluate(ETAT_EFFETS)
        for cle, v in attendu.items():
            check(e[cle] == v, "[effets] %s : « %s » %s (%s)" % (nom, cle, "posé" if v else "absent", e[cle]))
        check(not erreurs, "[effets] %s : aucune erreur JavaScript (%s)" % (nom, erreurs[:1]))
        ctx.close()

    # 2. Le cadre : couche plein écran, par-dessus l'écran de chargement, qui ne gêne aucun appui, et qui tourne.
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {})
    e = page.evaluate(ETAT_EFFETS)
    check(e["cadreDisplay"] == "block" and e["cadrePos"] == "fixed" and e["cadreRect"] == [0, 0, 390, 800],
          "[effets] le cadre couvre tout l'écran, fixe (%s %s %s)" % (e["cadreDisplay"], e["cadrePos"], e["cadreRect"]))
    check(e["cadreEvents"] == "none", "[effets] le cadre laisse passer les appuis (pointer-events : %s)" % e["cadreEvents"])
    check(e["cadreZ"] > 100000, "[effets] le cadre est AU-DESSUS de l'écran de chargement (z-index %s > 100000)" % e["cadreZ"])
    check("cycle-cadre" in e["cadreAnim"], "[effets] le cadre tourne avec le cycle (%s)" % e["cadreAnim"])
    toucher = page.evaluate("() => { const b = document.getElementById('refreshBtn').getBoundingClientRect();"
                            " return document.elementFromPoint(b.x + b.width/2, b.y + b.height/2).closest('#refreshBtn') !== null; }")
    check(toucher, "[effets] un appui à travers le cadre atteint le bouton dessous")
    ctx.close()
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {}, reduit=True)
    e = page.evaluate(ETAT_EFFETS)
    check(e["cadreDisplay"] == "block" and not e["cadreAnim"],
          "[effets] animations réduites : le cadre reste, fixe (%s, %s)" % (e["cadreDisplay"], e["cadreAnim"]))
    ctx.close()
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {"cycleCouleurs": False})
    e = page.evaluate(ETAT_EFFETS)
    check(e["cadreDisplay"] == "block" and not e["cadreAnim"],
          "[effets] sans le cycle, le cadre reste fixe (%s)" % e["cadreAnim"])
    ctx.close()
    ctx, page, _ = _ouvre_effets(nav, url, VEILLE, {})
    e = page.evaluate(ETAT_EFFETS)
    check(e["cadreDisplay"] == "none", "[effets] la veille, le cadre n'est pas là (%s)" % e["cadreDisplay"])
    ctx.close()

    # 3. Le libellé et J+N.
    for nom, instant, corps, attendu in (
            ("le jour J", JOUR_J, {}, "GTA VI est sorti · J+0"),
            ("le jour J, cadre coupé", JOUR_J, {"coulCadre": False}, "depuis la sortie · J+0"),
            ("le lendemain", LENDEMAIN, {}, "depuis la sortie · J+1"),
            ("le lendemain, cadre allumé", LENDEMAIN, {"coulCadre": True}, "GTA VI est sorti · J+1"),
            ("la veille", VEILLE, {}, "avant la sortie"),
            ("la veille, cadre allumé", VEILLE, {"coulCadre": True}, "avant la sortie")):
        ctx, page, _ = _ouvre_effets(nav, url, instant, corps)
        e = page.evaluate(ETAT_EFFETS)
        check(e["sous"] == attendu, "[effets] libellé %s : « %s » (%s)" % (nom, attendu, e["sous"]))
        # Avec le cycle qui tourne, le sous-titre est un aplat à l'accent (le dégradé figé ne tournait pas : « on en a oublié »,
        # 10/11/2026) ; sans le cycle, il garde son dégradé. Le cycle est allumé d'office les 24 h de la sortie.
        cycle = corps.get("cycleCouleurs") is True or (instant == JOUR_J and corps.get("cycleCouleurs") is not False)
        degrade = (attendu.startswith("GTA VI") or (corps.get("coulCadre") is True)) and not cycle
        check((e["sousRempli"] == "rgba(0, 0, 0, 0)") == degrade,
              "[effets] libellé %s : texte %s (%s)" % (nom, "en dégradé" if degrade else "uni", e["sousRempli"]))
        ctx.close()
    # Le libellé ne déborde pas à 320 px, et il est lisible : chaque couleur du dégradé tient 4,5:1 sur le panneau.
    for theme, palette in (("dark", ("#19e6ff", "#b388ff", "#ff5cae")), ("light", ("#00708a", "#7c3aed", "#b8146f"))):
        ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {}, theme=theme)
        page.set_viewport_size({"width": 320, "height": 700})
        page.wait_for_timeout(200)
        m = page.evaluate("""() => { const s = document.querySelector('.compteur-sous');
            const r = s.getBoundingClientRect(), p = s.parentElement.getBoundingClientRect();
            let n = s, fond = null;
            while(n && !fond){ const c = getComputedStyle(n).backgroundColor;
              if(c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') fond = c; n = n.parentElement; }
            const v = s.getClientRects().length;
            return {largeur: r.width, boite: p.width, lignes: Math.round(r.height / parseFloat(getComputedStyle(s).lineHeight || 16)), fond}; }""")
        check(m["largeur"] <= m["boite"] + 0.5, "[effets %s 320 px] le libellé tient dans son panneau (%.0f px pour %.0f)" % (theme, m["largeur"], m["boite"]))
        fond = [int(x) for x in re.findall(r"\d+", m["fond"] or "0 0 0")[:3]]
        faibles = [c for c in palette if _rapport([int(c[i:i + 2], 16) for i in (1, 3, 5)], fond) < 4.5]
        check(not faibles, "[effets %s] chaque couleur du dégradé du libellé tient 4,5:1 sur le fond %s (sous le seuil : %s)" % (theme, fond, faibles))
        ctx.close()

    # 4. La barre d'état : teinte rose fixe, ou cycle qui change, ou fond d'origine.
    FOND_CLAIR = "#f4f7fa"
    for nom, instant, corps, theme, reduit, attendu in (
            ("la veille", VEILLE, {}, "dark", False, FOND_SOMBRE),
            ("le jour J, animations réduites : rose fixe", JOUR_J, {}, "dark", True, _melange(FOND_SOMBRE, "#ff5cae", 0.16)),
            ("le jour J en clair, animations réduites : rose fixe", JOUR_J, {}, "light", True, _melange(FOND_CLAIR, "#b8146f", 0.16)),
            ("le jour J, case coupée", JOUR_J, {"coulBarreEtat": False}, "dark", False, FOND_SOMBRE),
            ("la veille, case allumée, animations réduites", VEILLE, {"coulBarreEtat": True}, "dark", True, _melange(FOND_SOMBRE, "#ff5cae", 0.16))):
        ctx, page, _ = _ouvre_effets(nav, url, instant, corps, theme=theme, reduit=reduit)
        e = page.evaluate(ETAT_EFFETS)
        check(e["barre"] == attendu, "[effets] barre d'état, %s : %s (%s)" % (nom, attendu, e["barre"]))
        ctx.close()
    # Le cycle sur la barre : les couleurs de départ de chaque segment, et un mouvement réel au fil du temps.
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {})
    pts = page.evaluate("""() => [0, 1000, 2000, 3000, 4000, 500, 1500].map(t => couleurDuCycle(t, CYCLE_SOMBRE))""")
    attendu = ["#19e6ff", "#b388ff", "#ff5cae", "#b388ff", "#19e6ff"]
    check(pts[:5] == attendu, "[effets] la courbe du cycle passe par cyan, violet, rose, violet, cyan (%s)" % pts[:5])
    mi = _melange("#19e6ff", "#b388ff", 0.5)
    check(pts[5] == mi, "[effets] à mi-chemin du premier segment : %s (%s)" % (mi, pts[5]))
    ctx.close()
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {}, reprend=True)
    vus = set()
    for _ in range(14):
        vus.add(page.evaluate("() => document.querySelector('meta[name=theme-color]').getAttribute('content')"))
        page.wait_for_timeout(130)
    check(len(vus) >= 3, "[effets] la barre d'état change de couleur avec le cycle (%d couleurs vues : %s)" % (len(vus), sorted(vus)[:4]))
    check(all(c.startswith("#") and len(c) == 7 for c in vus), "[effets] elles sont toutes des couleurs valides")
    page.evaluate("() => basculeEffet('coulBarreEtat', false)")
    page.wait_for_timeout(300)
    fin = page.evaluate("() => document.querySelector('meta[name=theme-color]').getAttribute('content')")
    page.wait_for_timeout(600)
    fin2 = page.evaluate("() => document.querySelector('meta[name=theme-color]').getAttribute('content')")
    check(fin == FOND_SOMBRE and fin2 == FOND_SOMBRE,
          "[effets] décochée, la barre revient au fond de la page et y reste (%s, %s)" % (fin, fin2))
    ctx.close()

    # 5. Les commandes : cases à cocher et curseur de saisie à la couleur d'accent.
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {})
    c = page.evaluate("""() => { const cb = document.createElement('input'); cb.type = 'checkbox'; document.body.appendChild(cb);
        const ch = document.createElement('input'); ch.type = 'text'; document.body.appendChild(ch);
        const r = {accent: getComputedStyle(cb).accentColor, caret: getComputedStyle(ch).caretColor}; cb.remove(); ch.remove(); return r; }""")
    check(c["accent"] != "auto" and c["caret"] != "auto", "[effets] le jour J, case à cocher et curseur de saisie ont la couleur d'accent (%s)" % c)
    ctx.close()
    ctx, page, _ = _ouvre_effets(nav, url, VEILLE, {})
    c = page.evaluate("""() => { const cb = document.createElement('input'); cb.type = 'checkbox'; document.body.appendChild(cb);
        const r = getComputedStyle(cb).accentColor; cb.remove(); return r; }""")
    check(c == "auto", "[effets] la veille, la case à cocher garde la couleur du navigateur (%s)" % c)
    ctx.close()

    # 6. L'écran de chargement : « VI » rose et dégradé, seulement si la case le dit ; le cadre passe par-dessus.
    for nom, instant, corps, attendu in (
            ("le jour J", JOUR_J, {}, True), ("la veille", VEILLE, {}, False),
            ("la veille, case allumée", VEILLE, {"coulChargement": True}, True),
            ("le jour J, case coupée", JOUR_J, {"coulChargement": False}, False)):
        ctx, page, _ = _ouvre_effets(nav, url, instant, corps, splash=True)
        m = page.evaluate("""() => { const sp = document.getElementById('splash');
            if(!sp) return null;
            return {classe: sp.classList.contains('splash-jourj'),
                    filtre: getComputedStyle(sp.querySelector('.splash-logo')).filter,
                    rempli: getComputedStyle(document.getElementById('splashJ')).webkitTextFillColor}; }""")
        check(m is not None, "[effets] écran de chargement présent (%s)" % nom)
        if m:
            check(m["classe"] == attendu, "[effets] chargement, %s : classe du jour J %s (%s)" % (nom, "posée" if attendu else "absente", m["classe"]))
            check(("hue-rotate" in m["filtre"]) == attendu, "[effets] chargement, %s : le « VI » %s (%s)" % (nom, "vire au rose" if attendu else "reste bleu", m["filtre"]))
            check((m["rempli"] == "rgba(0, 0, 0, 0)") == attendu, "[effets] chargement, %s : la ligne principale %s (%s)" % (nom, "est en dégradé" if attendu else "reste unie", m["rempli"]))
        ctx.close()

    # 7. Le halo : le bouton le lance une seconde ; animations réduites, il ne se lance pas ; il part tout seul à minuit.
    ctx, page, _ = _ouvre_effets(nav, url, VEILLE, {})
    page.evaluate("() => openSettings()")
    check(page.evaluate("() => !!document.getElementById('lanceHaloBtn')"), "[effets] le bouton « Lancer le halo » existe dans les Réglages")
    page.evaluate("() => document.getElementById('lanceHaloBtn').click()")
    h = page.evaluate("() => { const e = document.getElementById('haloJ'), cs = getComputedStyle(e);"
                      " return {vif: e.classList.contains('vif'), disp: cs.display, ombre: cs.boxShadow !== 'none', pos: cs.position, ev: cs.pointerEvents}; }")
    check(h["vif"] and h["disp"] == "block" and h["ombre"] and h["pos"] == "fixed" and h["ev"] == "none",
          "[effets] le bouton lance le halo, plein écran et sans gêner les appuis (%s)" % h)
    page.clock.run_for(6500)
    check(page.evaluate("() => !document.getElementById('haloJ').classList.contains('vif')"), "[effets] le halo s'éteint seul après six secondes")
    ctx.close()
    ctx, page, _ = _ouvre_effets(nav, url, VEILLE, {}, reduit=True)
    page.evaluate("() => openSettings()")
    rendu = page.evaluate("() => lanceHalo()")
    check(rendu is False, "[effets] animations réduites : lanceHalo() refuse (%s)" % rendu)
    ctx.close()
    for nom, corps, attendu in (("case par défaut", {}, True), ("cadre coupé", {"coulCadre": False}, False)):
        ctx, page, _ = _ouvre_effets(nav, url, "2026-11-18T22:59:57Z", corps)
        page.evaluate("() => { window.__halos = 0; new MutationObserver(() => { if(document.getElementById('haloJ').classList.contains('vif')) window.__halos++; })"
                      ".observe(document.getElementById('haloJ'), {attributes: true, attributeFilter: ['class']}); }")
        page.clock.run_for(5000)
        n = page.evaluate("() => window.__halos")
        check((n > 0) == attendu, "[effets] à minuit pile, le halo %s (%s déclenchement(s), %s)" % ("part" if attendu else "ne part pas", n, nom))
        ctx.close()
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {})
    page.evaluate("() => { window.__halos = 0; new MutationObserver(() => { if(document.getElementById('haloJ').classList.contains('vif')) window.__halos++; })"
                  ".observe(document.getElementById('haloJ'), {attributes: true, attributeFilter: ['class']}); }")
    page.clock.run_for(3000)
    check(page.evaluate("() => window.__halos") == 0, "[effets] ouvrir l'app APRÈS minuit ne lance pas le halo")
    ctx.close()

    # 8. Les cases des Réglages : une par effet, cochées d'office le jour J, écrites seulement par la case.
    def stocke(page, cle):
        return page.evaluate("(c) => { const s = JSON.parse(localStorage.getItem('gta6watch:settings-v1') || '{}');"
                             " return Object.prototype.hasOwnProperty.call(s, c) ? s[c] : 'absent'; }", cle)
    ctx, page, _ = _ouvre_effets(nav, url, VEILLE, {})
    page.evaluate("() => openSettings()")
    existent = page.evaluate("(l) => l.map(c => !!document.getElementById(c))", list(EFFETS))
    check(all(existent), "[effets] une case par effet dans les Réglages (%s)" % dict(zip(EFFETS, existent)))
    coches = page.evaluate("(l) => l.map(c => document.getElementById(c).checked)", list(EFFETS))
    check(not any(coches), "[effets] la veille, toutes les cases sont décochées (%s)" % coches)
    for cle in EFFETS:
        page.evaluate("(c) => { const e = document.getElementById(c); e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true})); }", cle)
        check(stocke(page, cle) is True, "[effets] cocher « %s » écrit true (%s)" % (cle, stocke(page, cle)))
    e = page.evaluate(ETAT_EFFETS)
    check(e["cadre"] and e["controles"] and e["cycle"] and e["cadreDisplay"] == "block",
          "[effets] tout cocher la veille pose tous les effets d'un coup (%s)" % {k: e[k] for k in ("cadre", "controles", "cycle", "cadreDisplay")})
    ctx.close()
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {})
    page.evaluate("() => openSettings()")
    coches = page.evaluate("(l) => l.map(c => document.getElementById(c).checked)", list(EFFETS))
    check(all(coches), "[effets] le jour J, toutes les cases sont cochées d'office (%s)" % coches)
    page.evaluate("() => { document.getElementById('maxDisplay').value = 300; document.getElementById('maxDisplay').dispatchEvent(new Event('change', {bubbles: true})); }")
    page.evaluate("() => saveSettings()")
    check(all(stocke(page, c) == "absent" or stocke(page, c) is None for c in EFFETS),
          "[effets] enregistrer un AUTRE champ ne fige aucun effet (%s)" % {c: stocke(page, c) for c in EFFETS})
    page.evaluate("() => { const e = document.getElementById('coulCadre'); e.checked = false; e.dispatchEvent(new Event('change', {bubbles: true})); }")
    check(stocke(page, "coulCadre") is False, "[effets] décocher « coulCadre » le jour J écrit false")
    autres = {c: stocke(page, c) for c in EFFETS if c != "coulCadre"}
    check(all(v == "absent" or v is None for v in autres.values()),
          "[effets] décocher UN effet ne fige pas les autres : ils restent automatiques (%s)" % autres)
    e = page.evaluate(ETAT_EFFETS)
    check(not e["cadre"] and e["cadreDisplay"] == "none" and e["controles"], "[effets] et coupe SEUL le cadre (%s)" % {k: e[k] for k in ("cadre", "controles", "cadreDisplay")})
    ctx.close()
    # Sauvegarde : aucun effet ne s'exporte comme un jeton, mais les réglages voyagent (pas de clé inconnue qui plante).
    ctx, page, erreurs = _ouvre_effets(nav, url, VEILLE, {"coulCadre": True})
    page.reload(wait_until="load")
    page.wait_for_selector(".header-haut")
    page.wait_for_timeout(300)
    e = page.evaluate(ETAT_EFFETS)
    check(e["cadre"], "[effets] un effet allumé la veille survit au rechargement")
    check(not erreurs, "[effets] aucune erreur JavaScript au rechargement (%s)" % erreurs[:1])
    ctx.close()


def _pixels(ctx, png, points):
    """Lit des pixels d'une capture : la décode dans une page vierge (pas de bibliothèque d'images ici)."""
    import base64
    p2 = ctx.new_page()
    p2.set_content("<canvas id=c></canvas>")
    out = p2.evaluate("""async ([b64, pts]) => {
        const img = new Image(); img.src = 'data:image/png;base64,' + b64; await img.decode();
        const c = document.getElementById('c'); c.width = img.width; c.height = img.height;
        const g = c.getContext('2d'); g.drawImage(img, 0, 0);
        return pts.map(([x, y]) => Array.from(g.getImageData(x, y, 1, 1).data).slice(0, 3)); }""",
                      [base64.b64encode(png).decode(), points])
    p2.close()
    return out


def test_coins_du_cadre(nav, url):
    """Les coins du BAS du cadre suivent l'écran arrondi ; ceux du haut restent droits (Antoni, S26 Ultra, 11/11/2026)."""
    print("\n[jour J] les coins du cadre : bas arrondi au rayon réglé, haut à angle droit")
    ctx, page, erreurs = _ouvre_effets(nav, url, JOUR_J, {}, reduit=True)
    d = page.evaluate("""() => { const e = document.getElementById('cadreEcran'), cs = getComputedStyle(e);
        return {hg: cs.borderTopLeftRadius, hd: cs.borderTopRightRadius, bg: cs.borderBottomLeftRadius,
                bd: cs.borderBottomRightRadius, var: document.documentElement.style.getPropertyValue('--rayon-cadre'),
                defaut: RAYON_CADRE_DEFAUT}; }""")
    r = "%dpx" % d["defaut"]
    check(d["defaut"] > 0 and d["var"] == r and d["bg"] == r and d["bd"] == r,
          "[coins] par défaut, les deux coins du BAS ont un rayon de %s (%s, %s)" % (r, d["bg"], d["bd"]))
    check(d["hg"] == "0px" and d["hd"] == "0px",
          "[coins] les deux coins du HAUT restent à angle droit (%s, %s)" % (d["hg"], d["hd"]))
    ctx2, page2, _ = _ouvre_effets(nav, url, VEILLE, {}, reduit=True)
    page2.evaluate("() => openSettings()")
    check(page2.evaluate("() => !!document.getElementById('rayonCadre')"), "[coins] le curseur « Coins du bas de l'écran » existe dans les Réglages")
    check(page2.evaluate("() => getComputedStyle(document.getElementById('cadreEcran')).display") == "none",
          "[coins] la veille, case décochée : pas de cadre")
    page2.evaluate("() => { const c = document.getElementById('rayonCadre'); c.value = '47'; c.dispatchEvent(new Event('input', {bubbles: true})); }")
    e = page2.evaluate("""() => ({disp: getComputedStyle(document.getElementById('cadreEcran')).display,
        r: getComputedStyle(document.getElementById('cadreEcran')).borderBottomLeftRadius,
        haut: getComputedStyle(document.getElementById('cadreEcran')).borderTopLeftRadius,
        val: document.getElementById('rayonCadreVal').textContent,
        stocke: JSON.parse(localStorage.getItem('gta6watch:settings-v1')).rayonCadre})""")
    check(e["disp"] == "block" and e["r"] == "47px" and e["haut"] == "0px" and e["val"] == "47" and e["stocke"] == 47,
          "[coins] tirer le curseur règle le rayon du bas, l'enregistre et montre le cadre le temps du réglage (%s)" % e)
    page2.clock.run_for(2500)
    check(page2.evaluate("() => getComputedStyle(document.getElementById('cadreEcran')).display") == "none",
          "[coins] puis l'aperçu s'éteint tout seul (la case est décochée)")
    page2.reload(wait_until="load")
    page2.wait_for_selector(".header-haut")
    page2.wait_for_timeout(200)
    check(page2.evaluate("() => document.documentElement.style.getPropertyValue('--rayon-cadre')") == "47px",
          "[coins] le rayon réglé survit au rechargement")
    ctx2.close()
    # LA COURBE, lue sur des captures (rayon 40). Haut : angle droit, le trait occupe le coin lui-même.
    ctx3, page3, _ = _ouvre_effets(nav, url, JOUR_J, {}, reduit=True)
    page3.evaluate("() => { settings.rayonCadre = 40; appliqueRayonCadre(); }")
    page3.wait_for_timeout(200)
    vif = lambda px: max(px) > 140 and (max(px) - min(px)) > 60
    haut = page3.screenshot(clip={"x": 0, "y": 0, "width": 60, "height": 60})
    coin_haut, bord_haut = _pixels(ctx3, haut, [[1, 1], [30, 1]])
    check(vif(coin_haut) and vif(bord_haut),
          "[coins] en haut, le trait va jusque dans le coin, à angle droit (%s, %s)" % (coin_haut, bord_haut))
    bas = page3.screenshot(clip={"x": 0, "y": 740, "width": 60, "height": 60})
    # Bas gauche : centre de l'arc (40, 800-40) ; point sur l'arc médian à 45° : x = 40 - 38,5/√2 = 12,8, y = 800 - 12,8.
    hors, sur, loin, dedans = _pixels(ctx3, bas, [[2, 57], [13, 47], [45, 58], [18, 42]])
    check(not vif(hors) and max(hors) < 60, "[coins] en bas, hors de l'arc, dans le coin de l'écran : le fond (%s)" % hors)
    check(vif(sur), "[coins] en bas, sur l'arc à 45° : le trait suit la courbe (%s)" % sur)
    check(vif(loin), "[coins] et sur le bord bas, passé la courbe : le trait continue (%s)" % loin)
    check(not vif(dedans), "[coins] à l'intérieur de l'arc, au-delà de l'épaisseur du trait : le fond (%s)" % dedans)
    ctx3.close()


# ---------------------------------------------------------------------------------------------
# LOT 2A DU 11/11/2026 : chevron « Par source », loupe de recherche, titres de cartes, titres de jour,
# fondu sous la barre du bas.
# ---------------------------------------------------------------------------------------------
def _ouvre_fil(nav, url, largeur=390, theme="dark"):
    ctx = nav.new_context(viewport={"width": largeur, "height": 800}, color_scheme=theme,
                          timezone_id="Europe/Paris", reduced_motion="reduce")
    ctx.add_init_script("try{if(!localStorage.getItem('gta6watch:settings-v1'))"
                        "localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));}catch(e){}")
    page = ctx.new_page()
    erreurs = []
    page.on("pageerror", lambda e, err=erreurs: err.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("""() => {
        lastItems = Array.from({length: 8}, (_, i) => ({title: 'Un titre d\\'article assez long pour passer sur deux lignes, numéro ' + (i + 1),
          link: 'https://ex.test/' + i, description: 'Résumé ' + i,
          date: new Date(Date.now() - i * 3600e3).toISOString(), source: 'S' + (i % 3)}));
        lastNewLinks = new Set(); historyPartial = false; rattrapageLance = true; visibleCount = 30;
        readSet = new Set(); misDeCote = new Set(); applyFilters(); }""")
    page.wait_for_selector(".card", state="attached", timeout=10000)
    return ctx, page, erreurs


def test_lot_2a_interface(nav, url):
    print("\n[lot 2a] chevron « Par source », loupe, titres de cartes et de jour, fondu du bas")
    ETAT = """() => { const r = document.getElementById('rangeeSources'), b = document.getElementById('sourcesBtn');
        const rb = b.getBoundingClientRect(), cs = getComputedStyle(r);
        return {ouverte: cs.display !== 'none', expanded: b.getAttribute('aria-expanded'),
                libelle: document.getElementById('sourcesLibelle').textContent, actif: b.classList.contains('filtre-actif'),
                nom: b.getAttribute('aria-label'), bx: rb.x + rb.width / 2, by: rb.y + rb.height / 2, bh: rb.height,
                ligne: document.querySelector('.feed-count').getBoundingClientRect().height,
                visibles: ['tabNonRockstar', 'tabRockstar', 'tabRockstarmag'].map(i => { const e = document.getElementById(i).getBoundingClientRect(); return e.width > 0 && e.height > 0; })}; }"""
    for largeur in (320, 390):
        ctx, page, erreurs = _ouvre_fil(nav, url, largeur)
        e = page.evaluate(ETAT)
        check(not e["ouverte"] and e["expanded"] == "false" and e["libelle"] == "" and not any(e["visibles"]),
              "[chevron %d px] fermé à l'ouverture : les trois sources sont repliées, le chevron seul, sans mot (%r)" % (largeur, e["libelle"]))
        check(e["nom"] == "Déplier les sources", "[chevron %d px] le bouton a un nom pour les lecteurs d'écran (%s)" % (largeur, e["nom"]))
        # La zone de toucher fait 44 px de haut sans grossir la ligne : 14 px au-dessus et au-dessous du texte.
        zones = page.evaluate("""([x, y, h]) => [y - h/2 - 12, y + h/2 + 12, y - h/2 - 18, y + h/2 + 18]
            .map(yy => { const el = document.elementFromPoint(x, yy); return !!(el && el.closest('#sourcesBtn')); })""",
                              [e["bx"], e["by"], e["bh"]])
        check(zones[0] and zones[1], "[chevron %d px] la zone de toucher déborde de 12 px en haut et en bas (%s)" % (largeur, zones))
        check(not zones[2] and not zones[3], "[chevron %d px] mais pas de 18 px : elle s'arrête à environ 44 px (%s)" % (largeur, zones))
        check(e["ligne"] < 30, "[chevron %d px] la ligne de comptage ne grossit pas (%.0f px)" % (largeur, e["ligne"]))
        page.click("#sourcesBtn")
        e = page.evaluate(ETAT)
        check(e["ouverte"] and e["expanded"] == "true" and all(e["visibles"]) and e["nom"] == "Replier les sources",
              "[chevron %d px] un appui déplie les trois sources (%s)" % (largeur, e["nom"]))
        page.click("#tabRockstar")
        e = page.evaluate(ETAT)
        check(e["libelle"] == "R" and e["actif"] and e["ouverte"] and "Rockstar" in (e["nom"] or ""),
              "[chevron %d px] filtre actif : le bouton porte l'abréviation « R », en accent, son nom complet pour les lecteurs d'écran, la rangée reste ouverte (%s, %s)" % (largeur, e["libelle"], e["nom"]))
        page.click("#sourcesBtn")
        e = page.evaluate(ETAT)
        check(not e["ouverte"] and e["libelle"] == "R" and e["actif"],
              "[chevron %d px] replié avec un filtre actif, l'abréviation reste visible (%s)" % (largeur, e["libelle"]))
        # Rechargement : le filtre revient, donc la rangée s'ouvre seule ; sans filtre, elle est fermée.
        page.reload(wait_until="load")
        page.wait_for_selector("#sourcesBtn")
        page.wait_for_timeout(300)
        e = page.evaluate(ETAT)
        check(e["ouverte"] and e["libelle"] == "R",
              "[chevron %d px] au rechargement avec un filtre de source, la rangée s'ouvre toute seule (%s)" % (largeur, e["libelle"]))
        page.click("#tabAll")
        e = page.evaluate(ETAT)
        check(e["libelle"] == "" and not e["actif"], "[chevron %d px] « Tous » rend le chevron seul (%r)" % (largeur, e["libelle"]))
        page.reload(wait_until="load")
        page.wait_for_selector("#sourcesBtn")
        page.wait_for_timeout(300)
        e = page.evaluate(ETAT)
        check(not e["ouverte"], "[chevron %d px] sans filtre, la rangée est refermée à chaque ouverture (jamais mémorisée)" % largeur)
        check(not erreurs, "[chevron %d px] aucune erreur JavaScript (%s)" % (largeur, erreurs[:1]))
        ctx.close()

    # LA LOUPE.
    ctx, page, _ = _ouvre_fil(nav, url, 390)
    m = page.evaluate("""() => { const l = document.querySelector('.search-loupe').getBoundingClientRect(),
        i = document.getElementById('searchInput').getBoundingClientRect(), cs = getComputedStyle(document.getElementById('searchInput'));
        const lc = getComputedStyle(document.querySelector('.search-loupe'));
        return {gauche: l.left - i.left, droite: l.right - i.left, haut: l.top - i.top, bas: l.bottom - i.top, h: i.height,
                retrait: parseFloat(cs.paddingLeft), events: lc.pointerEvents, fill: lc.fill}; }""")
    check(m["gauche"] >= 8 and m["droite"] <= m["retrait"] and m["haut"] >= 0 and m["bas"] <= m["h"],
          "[loupe] la loupe est DANS le champ, à gauche, sans mordre sur le texte (%.0f à %.0f px, retrait %.0f px)" % (m["gauche"], m["droite"], m["retrait"]))
    check(m["events"] == "none", "[loupe] elle ne prend pas l'appui : le champ reçoit le toucher (%s)" % m["events"])
    page.click("#searchInput")
    check(page.evaluate("() => document.activeElement.id") == "searchInput", "[loupe] un appui sur la loupe focalise bien le champ")
    ctx.close()

    # LES TITRES DE CARTES : 16 px gras ; le mode compact garde 13 px.
    for largeur in (320, 390):
        ctx, page, _ = _ouvre_fil(nav, url, largeur)
        t = page.evaluate("""() => { const c = getComputedStyle(document.querySelector('.card-title'));
            return {px: parseFloat(c.fontSize), poids: c.fontWeight, deborde: document.documentElement.scrollWidth > innerWidth}; }""")
        check(t["px"] == 16 and int(t["poids"]) >= 700 and not t["deborde"],
              "[titres %d px] le titre d'une carte fait 16 px en gras, sans débordement (%s px, %s)" % (largeur, t["px"], t["poids"]))
        page.evaluate("() => toggleDensity()")
        page.wait_for_timeout(200)
        d = page.evaluate("() => parseFloat(getComputedStyle(document.querySelector('.card-title')).fontSize)")
        check(d == 13, "[titres %d px] le mode compact garde ses 13 px (%s)" % (largeur, d))
        ctx.close()

    # LES TITRES DE JOUR : un trait en dégradé jusqu'au bord, collés en haut, fond légèrement flouté.
    ctx, page, _ = _ouvre_fil(nav, url, 390)
    j = page.evaluate("""() => { const e = document.querySelector('.day-label'), cs = getComputedStyle(e), ap = getComputedStyle(e, '::after');
        return {disp: cs.display, pos: cs.position, flou: cs.backdropFilter || cs.webkitBackdropFilter, apres: ap.content,
                bg: ap.backgroundImage.indexOf('linear-gradient') >= 0, flex: ap.flexGrow, h: ap.height}; }""")
    check(j["disp"] == "flex" and j["pos"] == "sticky" and "blur" in (j["flou"] or "") and j["apres"] != "none" and j["bg"] and j["flex"] == "1" and j["h"] == "1px",
          "[jour] le titre de jour est un bandeau collant, flouté, avec un trait de 1 px en dégradé jusqu'au bord (%s)" % j)
    ctx.close()

    # LE FONDU SOUS LA BARRE DU BAS : il passe sous la barre et ne prend aucun appui.
    ctx, page, _ = _ouvre_fil(nav, url, 390)
    f = page.evaluate("""() => { const cs = getComputedStyle(document.body, '::after'), b = getComputedStyle(document.querySelector('.barre-bas:not(#apercuCouleurs *)'));
        return {pos: cs.position, z: parseInt(cs.zIndex, 10), bz: parseInt(b.zIndex, 10), ev: cs.pointerEvents,
                h: parseFloat(cs.height), img: cs.backgroundImage.indexOf('linear-gradient') >= 0, bas: cs.bottom}; }""")
    check(f["pos"] == "fixed" and f["ev"] == "none" and f["img"] and f["h"] >= 96 and f["z"] < f["bz"],
          "[fondu] un dégradé de %s px au bas de l'écran, sous la barre (z %s < %s), sans prendre d'appui (%s)" % (f["h"], f["z"], f["bz"], f))
    ctx.close()


def test_bordures_du_jour_j(nav, url):
    """Bordures en dégradé des tuiles du compteur et des cartes (Antoni, 11/11/2026), sur l'horloge du cycle."""
    print("\n[jour J] bordures des tuiles et des cartes : 2 px en dégradé, synchronisées, seulement les cartes à l'écran")
    INJECTE = """() => { lastItems = Array.from({length: 60}, (_, i) => ({title: 'Un titre ' + (i + 1), link: 'https://ex.test/' + i,
          description: 'Résumé ' + i, date: new Date(Date.now() - i * 3600e3).toISOString(), source: 'S' + (i % 3), official: i === 1}));
        lastNewLinks = new Set(); historyPartial = false; rattrapageLance = true; visibleCount = 60; readSet = new Set(); misDeCote = new Set();
        applyFilters(); }"""
    LIT = """() => { const cs = e => getComputedStyle(e);
        const t = [...document.querySelectorAll('#countdown .cd-t')].map(e => ({zero: e.classList.contains('zero'), l: cs(e).borderTopWidth,
            st: cs(e).borderTopStyle, img: cs(e).backgroundImage.indexOf('conic-gradient') >= 0,
            anim: e.getAnimations().map(a => a.animationName), texte: cs(e.querySelector('b')).color}));
        const cartes = [...document.querySelectorAll('#feed .card')];
        const norm = cartes.find(c => !c.classList.contains('official')), off = cartes.find(c => c.classList.contains('official'));
        return {classe: document.documentElement.classList.contains('bordures-j'), tuiles: t, n: cartes.length,
          ecran: cartes.filter(c => c.classList.contains('dans-ecran')).length,
          norm: norm ? {l: cs(norm).borderTopWidth, img: cs(norm).backgroundImage.indexOf('conic-gradient') >= 0} : null,
          off: off ? {coul: cs(off).borderTopColor, img: cs(off).backgroundImage.indexOf('conic-gradient') >= 0} : null,
          animEcran: cartes.filter(c => c.classList.contains('dans-ecran')).map(c => c.getAnimations().map(a => a.animationName).sort().join('+')),
          animHors: cartes.filter(c => !c.classList.contains('dans-ecran')).some(c => c.getAnimations().length > 0)}; }"""
    # 1. Qui est actif quand, et quoi.
    for nom, instant, corps, attendu in (("le jour J", JOUR_J, {}, True), ("la veille", VEILLE, {}, False),
                                         ("le jour J, case coupée", JOUR_J, {"coulBordures": False}, False),
                                         ("la veille, case allumée", VEILLE, {"coulBordures": True}, True)):
        ctx, page, erreurs = _ouvre_effets(nav, url, instant, corps, reprend=True)
        page.evaluate(INJECTE)
        page.wait_for_selector("#feed .card", state="attached")
        page.wait_for_timeout(500)
        e = page.evaluate(LIT)
        check(e["classe"] == attendu, "[bordures] %s : classe « bordures-j » %s (%s)" % (nom, "posée" if attendu else "absente", e["classe"]))
        if attendu:
            check(len(e["tuiles"]) == 4 and all(t["l"] == "2px" and t["img"] and t["st"] == "solid" for t in e["tuiles"]),
                  "[bordures] %s : les quatre tuiles ont 2 px de dégradé, pleines (%s)" % (nom, [(t["l"], t["img"], t["st"]) for t in e["tuiles"]]))
            check(e["norm"] and e["norm"]["l"] == "2px" and e["norm"]["img"],
                  "[bordures] %s : une carte ordinaire a 2 px de dégradé (%s)" % (nom, e["norm"]))
            # L'orange suit son propre dégradé (blanc, jaune, orange) : toujours un rouge à 255, jamais un dégradé conique.
            check(e["off"] and not e["off"]["img"] and e["off"]["coul"].startswith("rgb(255,"),
                  "[bordures] %s : la carte officielle garde sa bordure unie dans la famille de l'orange (%s)" % (nom, e["off"]))
        else:
            check(all(t["l"] == "1px" and not t["img"] for t in e["tuiles"]) and e["norm"] and not e["norm"]["img"],
                  "[bordures] %s : bordures d'origine (%s)" % (nom, [t["l"] for t in e["tuiles"]]))
        check(not erreurs, "[bordures] %s : aucune erreur JavaScript (%s)" % (nom, erreurs[:1]))
        ctx.close()
    # 2. La tuile « jours » grisée : bordure en dégradé, TEXTE gris.
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {})
    e = page.evaluate(LIT)
    zero = [t for t in e["tuiles"] if t["zero"]]
    vivantes = [t for t in e["tuiles"] if not t["zero"]]
    check(len(zero) == 1 and zero[0]["l"] == "2px" and zero[0]["img"],
          "[bordures] la tuile « jours » grisée à 00 a la bordure en dégradé (%s)" % zero)
    check(zero and zero[0]["texte"] != vivantes[0]["texte"],
          "[bordures] mais son texte reste gris, pas celui des tuiles vivantes (%s contre %s)" % (zero[0]["texte"] if zero else None, vivantes[0]["texte"]))
    ctx.close()
    # 3. Ça tourne : tuiles toujours, cartes seulement à l'écran ; jamais sur celles hors écran.
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {}, reprend=True)
    page.evaluate(INJECTE)
    page.wait_for_selector("#feed .card", state="attached")
    page.wait_for_timeout(600)
    e = page.evaluate(LIT)
    check(all("cycle-bord" in t["anim"] for t in e["tuiles"]), "[bordures] les quatre tuiles tournent (%s)" % [t["anim"] for t in e["tuiles"]])
    check(0 < e["ecran"] < e["n"] and e["ecran"] <= 8,
          "[bordures] seules les cartes à l'écran sont marquées : %d sur %d" % (e["ecran"], e["n"]))
    # La carte officielle (bordure orange) ne tourne que par son accent ; les autres, par l'accent ET la bordure.
    check("cycle-accent+cycle-bord" in e["animEcran"] and set(e["animEcran"]) <= {"cycle-accent+cycle-bord", "cycle-accent"} and not e["animHors"],
          "[bordures] elles tournent (accent et bordure ; l'officielle, l'accent seul) et aucune carte hors écran ne tourne (%s)" % sorted(set(e["animEcran"])))
    # Défilement : de nouvelles cartes entrent, les anciennes sortent.
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight / 2)")
    page.wait_for_timeout(700)
    f = page.evaluate(LIT)
    check(0 < f["ecran"] <= 8 and not f["animHors"] and "cycle-accent+cycle-bord" in f["animEcran"] and set(f["animEcran"]) <= {"cycle-accent+cycle-bord", "cycle-accent"},
          "[bordures] au défilement, d'autres cartes prennent le relais, toujours quelques-unes (%d) (%s)" % (f["ecran"], sorted(set(f["animEcran"]))))
    # 4. SYNCHRONISÉES : tuile, carte, cadre et en-tête sur la même horloge (page.screenshot force une image, comme au contrôle du cycle).
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(600)
    page.screenshot(clip={"x": 0, "y": 0, "width": 1, "height": 1})
    page.wait_for_timeout(100)
    h = page.evaluate("""() => { const dur = 4000;
        const prend = (el, nom) => { const a = (el ? el.getAnimations() : []).find(x => x.animationName === nom);
            return a ? (((a.currentTime - a.effect.getTiming().delay) % dur) + dur) % dur : null; };
        const carte = document.querySelector('#feed .card.dans-ecran:not(.official)');
        return {tuile: prend(document.querySelector('#countdown .cd-t'), 'cycle-bord'), carte: prend(carte, 'cycle-bord'),
                accentCarte: prend(carte, 'cycle-accent'), cadre: prend(document.getElementById('cadreEcran'), 'cycle-cadre'),
                entete: prend(document.querySelector('header.console'), 'cycle-accent')}; }""")
    valeurs = [v for v in h.values() if v is not None]
    ecart = lambda a, b: min(abs(a - b), 4000 - abs(a - b))
    check(len(valeurs) == 5, "[bordures] les cinq animations existent (%s)" % h)
    if len(valeurs) == 5:
        pires = max(ecart(valeurs[0], v) for v in valeurs)
        check(pires < 80, "[bordures] tuile, carte, accent de la carte, cadre et en-tête sont sur la MÊME horloge (écart maximal %.0f ms sur 4 000 : %s)" % (pires, {k: round(v) for k, v in h.items()}))
    ctx.close()
    # 5. Animations réduites : les bordures restent, fixes.
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {}, reduit=True)
    page.evaluate(INJECTE)
    page.wait_for_selector("#feed .card", state="attached")
    page.wait_for_timeout(400)
    e = page.evaluate(LIT)
    check(e["classe"] and all(t["img"] for t in e["tuiles"]) and not any(t["anim"] for t in e["tuiles"]) and not e["animHors"] and not any(e["animEcran"]),
          "[bordures] animations réduites : le dégradé reste, rien ne tourne (%s)" % [t["anim"] for t in e["tuiles"]])
    ctx.close()


def test_lot_2b_interface(nav, url):
    print("\n[lot 2b] repère « nouveaux · déjà vus », chiffres de Stats en tuiles")
    # LE REPÈRE : une seule fois, avant la première carte déjà vue qui suit des cartes neuves.
    def ouvre_avec(neufs, largeur=390):
        ctx, page, erreurs = _ouvre_fil(nav, url, largeur)
        page.evaluate("""(n) => { lastNewLinks = new Set(Array.from({length: n}, (_, i) => 'https://ex.test/' + i));
            applyFilters(); }""", neufs)
        page.wait_for_selector(".card", state="attached")
        return ctx, page, erreurs
    LIT = """() => { const r = [...document.querySelectorAll('.repere-nouveaux')];
        const cartes = [...document.querySelectorAll('#feed .card')];
        const m = r[0];
        const prec = m ? m.previousElementSibling : null, suiv = m ? (m.nextElementSibling || null) : null;
        const idx = m ? cartes.findIndex(c => m.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING) : -1;
        return {n: r.length, texte: m ? m.textContent : null, role: m ? m.getAttribute('role') : null, nom: m ? m.getAttribute('aria-label') : null,
                avant: idx > 0 ? cartes.slice(0, idx).filter(c => c.classList.contains('new')).length : -1,
                avantTout: idx > 0 ? cartes.slice(0, idx).every(c => c.classList.contains('new')) : null,
                apresNeuve: idx >= 0 && idx < cartes.length ? cartes[idx].classList.contains('new') : null,
                disp: m ? getComputedStyle(m).display : null, px: m ? parseFloat(getComputedStyle(m).fontSize) : null}; }"""
    for neufs, attendu in ((3, ("▲ 3 nouveaux · déjà vus ▼", 3)), (1, ("▲ 1 nouveau · déjà vus ▼", 1))):
        ctx, page, erreurs = ouvre_avec(neufs)
        e = page.evaluate(LIT)
        check(e["n"] == 1 and e["texte"] == attendu[0], "[repère] %d carte(s) neuve(s) : une seule ligne « %s » (%s, %s)" % (neufs, attendu[0], e["n"], e["texte"]))
        check(e["avant"] == attendu[1] and e["avantTout"] and e["apresNeuve"] is False,
              "[repère] posée juste entre la dernière carte neuve et la première déjà vue (avant : %s neuves, toutes neuves : %s, suivante neuve : %s)" % (e["avant"], e["avantTout"], e["apresNeuve"]))
        check(e["role"] == "separator" and "au-dessus" in (e["nom"] or "") and e["disp"] == "flex" and e["px"] >= 12,
              "[repère] c'est un séparateur annoncé aux lecteurs d'écran, en 12 px au moins (%s, %s, %s px)" % (e["role"], e["nom"], e["px"]))
        check(not erreurs, "[repère] aucune erreur JavaScript (%s)" % erreurs[:1])
        ctx.close()
    for nom, neufs in (("aucune carte neuve", 0), ("toutes les cartes neuves", 60)):
        ctx, page, _ = ouvre_avec(neufs)
        check(page.evaluate("() => document.querySelectorAll('.repere-nouveaux').length") == 0, "[repère] %s : pas de repère" % nom)
        ctx.close()

    # LES CHIFFRES DE STATS EN TUILES.
    for largeur in (320, 390):
        ctx, page, erreurs = _ouvre_fil(nav, url, largeur)
        page.click("#statsBtn")
        page.wait_for_selector(".stats-chiffre", state="attached")
        page.wait_for_timeout(300)
        # Avec ce jeu de test la grille n'a que deux tuiles (articles, par jour) : « officiels » et « actus majeures »
        # viennent de statistiques que le jeu ne remplit pas. On en ajoute deux, copiées de la première, pour mesurer la règle.
        page.evaluate("""() => { const g = document.querySelector('.stats-chiffres');
            for(let i = 0; i < 2; i++) g.appendChild(g.firstElementChild.cloneNode(true)); }""")
        t = page.evaluate("""() => { const l = [...document.querySelectorAll('.stats-chiffres')][0];
            const tuiles = [...l.querySelectorAll('.stats-chiffre')];
            const acc = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
            return {n: tuiles.length, boite: l.getBoundingClientRect().width,
              det: tuiles.map(e => { const b = e.querySelector('b'), cs = getComputedStyle(e), cb = getComputedStyle(b);
                const r = e.getBoundingClientRect();
                return {px: parseFloat(cb.fontSize), poids: cb.fontWeight, police: cb.fontFamily.split(',')[0], coul: cb.color,
                        bord: cs.borderTopWidth, rayon: cs.borderTopLeftRadius, droite: r.right, gauche: r.left, h: r.height}; }),
              rayonMd: getComputedStyle(document.documentElement).getPropertyValue('--r-md').trim(),
              debord: document.documentElement.scrollWidth > innerWidth}; }""")
        check(t["n"] >= 4, "[stats %d px] la grille porte au moins quatre tuiles, dont deux ajoutées pour la mesure (%d)" % (largeur, t["n"]))
        check(all(d["px"] == 30 and int(d["poids"]) >= 800 and "JetBrains" in d["police"] for d in t["det"]),
              "[stats %d px] chaque chiffre est en 30 px gras, dans la police du compteur (%s)" % (largeur, [(d["px"], d["poids"], d["police"]) for d in t["det"]][:2]))
        check(all(d["bord"] == "1px" and d["rayon"] == t["rayonMd"] for d in t["det"]),
              "[stats %d px] chaque tuile a son trait de 1 px et le coin de l'échelle (%s)" % (largeur, t["det"][0]["rayon"]))
        check(t["det"][0]["coul"] != t["det"][2]["coul"] and t["det"][0]["coul"] == t["det"][1]["coul"],
              "[stats %d px] accent pour les deux premiers, autre couleur pour les suivants (%s, %s)" % (largeur, t["det"][0]["coul"], t["det"][2]["coul"]))
        check(all(d["gauche"] >= 0 and d["droite"] <= largeur for d in t["det"]) and not t["debord"],
              "[stats %d px] aucune tuile ne sort de l'écran" % largeur)
        check(not erreurs, "[stats %d px] aucune erreur JavaScript (%s)" % (largeur, erreurs[:1]))
        ctx.close()


def test_case_globale_des_effets(nav, url):
    """Réglages › Couleurs, rangement C (11/11/2026) : aperçu vivant, préréglages, détail replié, groupe « Écran »."""
    print("\n[réglages] aperçu vivant, préréglages, détail replié sous « Personnaliser », curseur des coins dans « Écran »")
    def stocke(page, cle):
        return page.evaluate("(c) => { const s = JSON.parse(localStorage.getItem('gta6watch:settings-v1') || '{}');"
                             " return Object.prototype.hasOwnProperty.call(s, c) ? s[c] : 'absent'; }", cle)
    ETAT = """() => ({
        presses: [...document.querySelectorAll('.prereglage')].filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.dataset.p),
        cases: %s.map(c => document.getElementById(c).checked),
        classes: ['cadre-j', 'controles-j', 'bordures-j', 'cycle-couleur'].map(c => document.documentElement.classList.contains(c)),
        apercu: ['cadre', 'bord', 'anim'].map(c => document.getElementById('apercuCouleurs').classList.contains(c)),
        compte: document.getElementById('persoCompte').textContent,
        ouvert: getComputedStyle(document.getElementById('effetsDetail')).display !== 'none',
        expanded: document.getElementById('persoBtn').getAttribute('aria-expanded')})""" % json.dumps(list(EFFETS))
    # La veille : rien d'allumé, « Automatique » surligné (tout à null), détail replié.
    ctx, page, erreurs = _ouvre_effets(nav, url, VEILLE, {})
    page.evaluate("() => openSettings()")
    check(page.evaluate("() => ['apercuCouleurs', 'persoBtn', 'effetsDetail'].every(i => !!document.getElementById(i))"
                        " && document.querySelectorAll('.prereglage').length === 4"),
          "[réglages] l'aperçu, les quatre préréglages et le bouton « Personnaliser » existent")
    e = page.evaluate(ETAT)
    check(e["presses"] == ["auto"] and not any(e["cases"]) and not e["ouvert"] and e["expanded"] == "false" and e["compte"] == "0 sur 9 allumés",
          "[réglages] la veille : « Automatique » surligné, tout éteint, détail replié (%s)" % {k: e[k] for k in ("presses", "ouvert", "compte")})
    check(e["apercu"] == [False, False, False], "[réglages] la veille, l'aperçu est nu : ni cadre, ni bordures, ni rotation (%s)" % e["apercu"])
    # Jour J complet.
    page.evaluate("() => document.querySelector('.prereglage[data-p=jourj]').click()")
    e = page.evaluate(ETAT)
    check(e["presses"] == ["jourj"] and all(e["cases"]) and all(e["classes"]) and e["compte"] == "9 sur 9 allumés",
          "[réglages] « Jour J complet » allume les six effets d'un coup (%s)" % {k: e[k] for k in ("presses", "compte")})
    check(all(stocke(page, c) is True for c in EFFETS), "[réglages] et écrit true pour chacun (%s)" % {c: stocke(page, c) for c in EFFETS})
    check(e["apercu"][0] and e["apercu"][1], "[réglages] l'aperçu montre le cadre et les bordures (%s)" % e["apercu"])
    # Discret : cadre et bordures, fixes ; le reste éteint.
    page.evaluate("() => document.querySelector('.prereglage[data-p=discret]').click()")
    e = page.evaluate(ETAT)
    attendu = {"cycleCouleurs": False, "coulCadre": True, "coulBarreEtat": False, "coulChargement": False, "coulControles": False, "coulBordures": True}
    check(e["presses"] == ["discret"] and e["cases"] == [attendu[c] for c in EFFETS] and e["compte"] == "2 sur 9 allumés",
          "[réglages] « Discret » : le cadre et les bordures, sans mouvement ni autre effet (%s)" % e["cases"])
    check(e["apercu"] == [True, True, False], "[réglages] et l'aperçu est fixe : cadre et bordures, pas de rotation (%s)" % e["apercu"])
    # Régler UN effet à la main : plus aucun préréglage surligné.
    page.evaluate("() => { const c = document.getElementById('coulControles'); c.checked = true; c.dispatchEvent(new Event('change', {bubbles: true})); }")
    e = page.evaluate(ETAT)
    check(e["presses"] == [] and e["compte"] == "3 sur 9 allumés",
          "[réglages] un réglage à la main : aucun préréglage n'est surligné (%s, %s)" % (e["presses"], e["compte"]))
    # Aucun, puis Automatique.
    page.evaluate("() => document.querySelector('.prereglage[data-p=aucun]').click()")
    e = page.evaluate(ETAT)
    check(e["presses"] == ["aucun"] and not any(e["cases"]) and not any(e["classes"]) and all(stocke(page, c) is False for c in EFFETS),
          "[réglages] « Aucun » éteint tout et écrit false (%s)" % e["presses"])
    page.evaluate("() => document.querySelector('.prereglage[data-p=auto]').click()")
    e = page.evaluate(ETAT)
    check(e["presses"] == ["auto"] and all(stocke(page, c) is None for c in EFFETS) and not any(e["cases"]),
          "[réglages] « Automatique » remet chaque réglage à null, la veille tout reste éteint (%s)" % e["presses"])
    # Le détail : replié, il s'ouvre au toucher, annonce son état, montre les six cases.
    page.click("#persoBtn")
    e = page.evaluate(ETAT)
    check(e["ouvert"] and e["expanded"] == "true", "[réglages] « Personnaliser » déplie le détail (%s)" % e["expanded"])
    visibles = page.evaluate("(l) => l.map(c => { const r = document.getElementById(c).getBoundingClientRect(); return r.width > 0 && r.height > 0; })", list(EFFETS))
    check(all(visibles), "[réglages] les six cases sont visibles une fois déplié (%s)" % visibles)
    page.click("#persoBtn")
    check(not page.evaluate(ETAT)["ouvert"], "[réglages] un second appui le replie")
    # Le grand moment reste hors du détail, visible replié ; le curseur des coins est dans le groupe « Écran ».
    hors = page.evaluate("""() => { const b = document.getElementById('lanceHaloBtn'), r = b.getBoundingClientRect();
        return {visible: r.width > 0 && r.height > 0, dansDetail: !!b.closest('#effetsDetail')}; }""")
    check(hors["visible"] and not hors["dansDetail"], "[réglages] « Lancer le halo » est visible détail replié, hors du détail (%s)" % hors)
    ecran = page.evaluate("""() => { const g = document.getElementById('rayonCadre').closest('.setting-group');
        return {titre: g.querySelector('.setting-label').textContent.trim(), dansCouleurs: !!document.getElementById('rayonCadre').closest('#effetsDetail')}; }""")
    check(ecran["titre"].startswith("Écran") and not ecran["dansCouleurs"], "[réglages] le curseur des coins est dans son propre groupe « Écran » (%s)" % ecran)
    # Accessibilité tactile : chaque préréglage et le bouton « Personnaliser » font au moins 44 px de haut.
    haut = page.evaluate("() => [...document.querySelectorAll('.prereglage'), document.getElementById('persoBtn')].map(b => Math.round(b.getBoundingClientRect().height))")
    check(all(h >= 44 for h in haut), "[réglages] préréglages et « Personnaliser » : au moins 44 px de haut (%s)" % haut)
    check(not erreurs, "[réglages] aucune erreur JavaScript (%s)" % erreurs[:1])
    ctx.close()
    # Le jour J : « Automatique » surligné, tout allumé d'office, l'aperçu complet et animé.
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {})
    page.evaluate("() => openSettings()")
    e = page.evaluate(ETAT)
    check(e["presses"] == ["auto"] and all(e["cases"]) and e["apercu"] == [True, True, True] and e["compte"] == "9 sur 9 allumés",
          "[réglages] le jour J : « Automatique » surligné, neuf sur neuf, aperçu complet et animé (%s)" % {k: e[k] for k in ("presses", "apercu", "compte")})
    page.evaluate("() => document.querySelector('.prereglage[data-p=aucun]').click()")
    check(not any(page.evaluate(ETAT)["cases"]), "[réglages] le jour J, « Aucun » éteint vraiment tout")
    page.evaluate("() => document.querySelector('.prereglage[data-p=auto]').click()")
    e = page.evaluate(ETAT)
    check(all(e["cases"]) and all(e["classes"]), "[réglages] le jour J, « Automatique » rallume tout (%s)" % e["presses"])
    ctx.close()
    # UNE SEULE ÉPAISSEUR : cadre, cartes et tuiles ont la même bordure, lue sur la jeton --bord-ep.
    ctx, page, _ = _ouvre_effets(nav, url, JOUR_J, {})
    page.evaluate("""() => { lastItems = Array.from({length: 5}, (_, i) => ({title: 'T' + i, link: 'https://ex.test/' + i, description: 'R',
        date: new Date(Date.now() - i * 3600e3).toISOString(), source: 'S'})); lastNewLinks = new Set(); historyPartial = false;
        rattrapageLance = true; visibleCount = 5; applyFilters(); }""")
    page.wait_for_selector("#feed .card", state="attached")
    ep = page.evaluate("""() => { const cs = e => getComputedStyle(e);
        return {jeton: cs(document.documentElement).getPropertyValue('--bord-ep').trim(),
                cadre: cs(document.getElementById('cadreEcran')).borderTopWidth,
                carte: cs(document.querySelector('#feed .card:not(.official)')).borderTopWidth,
                tuile: cs(document.querySelector('#countdown .cd-t')).borderTopWidth,
                tuileZero: cs(document.querySelector('#countdown .cd-t.zero') || document.querySelector('#countdown .cd-t')).borderTopWidth}; }""")
    check(ep["jeton"] == "2px" and ep["cadre"] == ep["carte"] == ep["tuile"] == ep["tuileZero"] == ep["jeton"],
          "[épaisseur] cadre, cartes, tuiles (grisée comprise) : tous à la même épaisseur que le jeton --bord-ep (%s)" % ep)
    ctx.close()


def test_grand_moment_de_minuit(nav, url):
    """Le grand moment de minuit (11/11/2026) : flash, deux ondes, halo qui respire, confettis, vibration, six secondes."""
    print("\n[minuit] le grand moment : flash, ondes, halo qui respire, confettis, vibration, 6 s, sans gêner les appuis")
    STUB = "() => { window.__vibre = []; navigator.vibrate = p => { window.__vibre.push(p); return true; }; }"
    LIT = """() => { const f = document.querySelector('.fete'), h = document.getElementById('haloJ');
        const cs = f ? getComputedStyle(f) : null;
        return {fete: !!f, pos: cs && cs.position, ev: cs && cs.pointerEvents, z: cs && parseInt(cs.zIndex, 10),
                flash: f ? f.querySelectorAll('.fete-flash').length : 0, ondes: f ? f.querySelectorAll('.fete-onde').length : 0,
                toiles: f ? f.querySelectorAll('canvas.fete-toile').length : 0, anciens: f ? f.querySelectorAll('.fete-confetti').length : 0, halo: h.classList.contains('vif'),
                anims: f ? f.getAnimations({subtree: true}).length : 0,
                haloAnim: h.getAnimations().map(a => a.animationName), vibre: window.__vibre || null,
                cx: f ? f.style.getPropertyValue('--cx') : null}; }"""
    ctx, page, erreurs = _ouvre_effets(nav, url, VEILLE, {})
    page.evaluate(STUB)
    check(page.evaluate("() => lanceHalo()") is True, "[minuit] lanceHalo() accepte de jouer")
    e = page.evaluate(LIT)
    check(e["fete"] and e["flash"] == 1 and e["ondes"] == 2 and e["toiles"] == 2 and e["anciens"] == 0,
          "[minuit] un flash, deux ondes, deux calques de confettis (le tas et la chute), plus de confettis en éléments (%s, %s, %s, %s)"
          % (e["flash"], e["ondes"], e["toiles"], e["anciens"]))
    check(e["pos"] == "fixed" and e["ev"] == "none" and e["z"] > 100000,
          "[minuit] le calque couvre l'écran, ne prend aucun appui et passe par-dessus tout (%s, %s, %s)" % (e["pos"], e["ev"], e["z"]))
    check(e["halo"] and "halo-j" in e["haloAnim"], "[minuit] le halo des bords joue aussi (%s)" % e["haloAnim"])
    check(e["anims"] >= 3, "[minuit] les animations CSS (flash, ondes) tournent vraiment (%d)" % e["anims"])
    check(e["vibre"] == [[180, 80, 180, 80, 420]], "[minuit] une vibration courte est demandée (%s)" % e["vibre"])
    # Les ondes partent du compteur, pas d'un coin.
    cible = page.evaluate("() => { const b = document.getElementById('countdown').getBoundingClientRect(); return b.left + b.width / 2; }")
    check(abs(float(e["cx"].replace("px", "")) - cible) < 1, "[minuit] les ondes partent du centre du compteur (%s px pour %.0f)" % (e["cx"], cible))
    # D'où partent les ondes quand l'en-tête n'est PAS à l'écran : jamais du coin (0, 0). Défaut d'Antoni : lancé depuis
    # Réglages, où l'en-tête est masqué, elles partaient du coin en haut à gauche.
    ORIGINE = """() => { const f = document.querySelector('.fete'); return [parseFloat(f.style.getPropertyValue('--cx')), parseFloat(f.style.getPropertyValue('--cy')),
        innerWidth, innerHeight]; }"""
    page.evaluate("() => { document.querySelectorAll('.fete').forEach(f => f.remove()); openSettings(); }")
    page.wait_for_timeout(200)
    check(page.evaluate("() => document.getElementById('countdown').getBoundingClientRect().width") == 0,
          "[minuit] (sur Réglages, l'en-tête est bien masqué : sa boîte vaut zéro)")
    page.evaluate("() => lanceHalo()")
    cx, cy, w, h = page.evaluate(ORIGINE)
    # Elles partent du compteur de la ligne réduite (en haut à droite), pas du coin (0, 0).
    check(0 < cx < w and 0 < cy < h and (cx ** 2 + cy ** 2) ** 0.5 > 100,
          "[minuit] lancé depuis Réglages, les ondes ne partent PAS du coin : (%.0f, %.0f) dans un écran de %dx%d" % (cx, cy, w, h))
    page.evaluate("() => { document.querySelectorAll('.fete').forEach(f => f.remove()); closeSettings(); }")
    page.evaluate("() => window.scrollTo(0, 900)")
    page.wait_for_timeout(300)
    page.evaluate("() => lanceHalo()")
    cx, cy, w, h = page.evaluate(ORIGINE)
    check(0 < cx < w and 0 < cy < h, "[minuit] fil défilé, en-tête sorti de l'écran : les ondes partent de l'écran, pas d'ailleurs (%.0f, %.0f)" % (cx, cy))
    page.evaluate("() => { document.querySelectorAll('.fete').forEach(f => f.remove()); window.scrollTo(0, 0); }")
    page.wait_for_timeout(200)
    page.evaluate("() => lanceHalo()")
    # Un appui à travers le calque atteint le bouton dessous.
    atteint = page.evaluate("() => { const b = document.getElementById('refreshBtn').getBoundingClientRect();"
                            " const el = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2); return !!el && !!el.closest('#refreshBtn'); }")
    check(atteint, "[minuit] un appui pendant la fête atteint le bouton dessous")
    FIN = """() => { const f = document.querySelector('.fete-final'); if(!f) return null;
        const cs = getComputedStyle(f), t = f.querySelectorAll('.ff-texte'), r = f.getBoundingClientRect();
        const rects = Array.from(t).map(x => { const b = x.getBoundingClientRect(); return [b.left, b.right, b.width]; });
        return {bg: cs.backgroundColor, pos: cs.position, z: parseInt(cs.zIndex, 10), w: r.width, h: r.height,
                textes: Array.from(t).map(x => x.textContent), anim: Array.from(t).map(x => getComputedStyle(x).animationName),
                cols: Array.from(t).map(x => getComputedStyle(x).color), rects, iw: innerWidth, label: f.getAttribute('aria-label')}; }"""
    ETAT = """() => ({t: etatConfettis.t, posees: etatConfettis.posees, r: etatConfettis.remplissage, mini: etatConfettis.minimum,
                       plein: etatConfettis.plein, actif: etatConfettis.actif})"""
    # Les PIXELS du calque du tas (l'instrument le plus fidèle : pas la carte de hauteurs du code, ce qu'on voit) : la part de pixels
    # pleins au total, dans le quart du haut, dans le quart du bas, et une somme de contrôle de la bande du bas (3 % des lignes).
    COUV = """() => { const c = etatConfettis.toileTas, d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data, L = c.height, Wd = c.width;
        let n = 0, hauts = 0, bas = 0, bande = 0;
        for(let y = 0; y < L; y++) for(let x = 0; x < Wd; x++){
          const i = (y * Wd + x) * 4; if(d[i + 3] > 200){ n++; if(y < L * 0.25) hauts++; if(y >= L * 0.75) bas++; }
          if(y >= L * 0.97) bande = (bande * 31 + d[i] + d[i + 1] * 7 + d[i + 2] * 13 + d[i + 3]) % 1000000007;
        }
        return {tot: n / (Wd * L), haut: hauts / (Wd * L * 0.25), bas: bas / (Wd * L * 0.25), bande}; }"""
    # LA PLUIE (refaite le 10/11/2026 : « en continu, plus long, ça remplit l'écran entier en s'accumulant en bas, chaque
    # confetti reste où il tombe, quand l'écran est plein on passe à la suite »).
    page.clock.run_for(2000); s1 = page.evaluate(ETAT); c1 = page.evaluate(COUV)
    page.clock.run_for(2000); s2 = page.evaluate(ETAT); c2 = page.evaluate(COUV)
    page.clock.run_for(1000); s3 = page.evaluate(ETAT); c3 = page.evaluate(COUV)
    check(s1["posees"] > 0 and s2["posees"] > s1["posees"] + 100 and s3["posees"] > s2["posees"] + 100,
          "[minuit] la pluie est CONTINUE : des confettis se posent encore entre 2 s, 4 s et 5 s (%s, %s, %s)" % (s1["posees"], s2["posees"], s3["posees"]))
    check(s1["r"] < s2["r"] < s3["r"] < 1 and s1["r"] < 0.5,
          "[minuit] le tas MONTE peu à peu : %.2f à 2 s, %.2f à 4 s, %.2f à 5 s" % (s1["r"], s2["r"], s3["r"]))
    check(c2["bas"] > c2["haut"] + 0.15 and c2["bas"] > 0.5,
          "[minuit] il s'accumule EN BAS d'abord : %.0f %% de pixels pleins en bas contre %.0f %% en haut à 4 s" % (c2["bas"] * 100, c2["haut"] * 100))
    check(c2["bande"] == c3["bande"], "[minuit] chaque confetti RESTE où il est tombé : la bande du bas n'a pas bougé entre 4 s et 5 s")
    check(page.evaluate(LIT)["fete"] and page.evaluate(LIT)["halo"] and page.evaluate(FIN) is None and not s3["plein"],
          "[minuit] à cinq secondes, c'est encore en cours : la fête, le halo, pas d'écran noir")
    # Jusqu'à l'écran noir : il arrive quand l'écran est plein, vers 7 s.
    ecoule = 5000
    while page.evaluate(FIN) is None and ecoule < 12000:
        page.clock.run_for(100); ecoule += 100
    e_f = page.evaluate(ETAT); cf = page.evaluate(COUV)
    check(page.evaluate(FIN) is not None and e_f["plein"] and 5.5 <= e_f["t"] <= 8.5,
          "[minuit] quand l'écran est plein (%.1f s), l'écran noir arrive (entre 5,5 et 8,5 s : avant le filet de 9 s)" % e_f["t"])
    check(cf["tot"] >= 0.8 and cf["haut"] >= 0.75 and cf["bas"] >= 0.75,
          "[minuit] l'écran est PLEIN à ce moment-là : %.0f %% de pixels pleins, %.0f %% en haut, %.0f %% en bas" % (cf["tot"] * 100, cf["haut"] * 100, cf["bas"] * 100))
    page.clock.run_for(1700)
    e2 = page.evaluate(ETAT)
    check(not page.evaluate(LIT)["fete"] and not page.evaluate(LIT)["halo"] and not e2["actif"] and e2["posees"] == e_f["posees"],
          "[minuit] sous le noir, la fête est retirée, la pluie s'arrête, le halo est fini")
    fin = page.evaluate(FIN)
    check(fin is not None, "[minuit] et l'écran noir de fin reste là")
    if fin:
        check(fin["bg"] == "rgb(0, 0, 0)" and fin["pos"] == "fixed" and fin["w"] == fin["iw"] and fin["z"] > 2147483001,
              "[minuit] l'écran de fin est noir, plein écran et passe par-dessus la fête (%s, %s, %.0f, %s)" % (fin["bg"], fin["pos"], fin["w"], fin["z"]))
        check(fin["textes"] == ["GTA VI", "EST DISPO !"], "[minuit] le texte est « GTA VI » puis « EST DISPO ! » (%s)" % fin["textes"])
        check(all(a == "cycle-accent" for a in fin["anim"]), "[minuit] chaque ligne du texte suit le cycle de couleur (%s)" % fin["anim"])
        check(all(l >= 0 and r <= fin["iw"] for l, r, _ in fin["rects"]),
              "[minuit] le texte tient dans l'écran (%s px de large)" % fin["iw"])
    # LA COULEUR RÉELLE change avec le cycle (défaut d'Antoni : le texte restait figé, la couleur était posée sur le parent).
    cols = []
    for t in (0, 1000, 2000):
        page.evaluate("(t) => { for(const x of document.querySelectorAll('.fete-final .ff-texte')) for(const a of x.getAnimations()){ a.pause(); a.currentTime = t; } }", t)
        page.wait_for_timeout(100)
        cols.append(page.evaluate("() => Array.from(document.querySelectorAll('.fete-final .ff-texte')).map(x => getComputedStyle(x).color)"))
    check(len({c[0] for c in cols}) == 3 and all(c[0] == c[1] for c in cols),
          "[minuit] la couleur du texte change vraiment avec le cycle, les deux lignes ensemble (%s)" % cols)
    page.evaluate("() => { for(const x of document.querySelectorAll('.fete-final .ff-texte')) for(const a of x.getAnimations()) a.play(); }")
    page.clock.run_for(60000)
    check(page.evaluate(FIN) is not None, "[minuit] une minute plus tard, l'écran de fin est TOUJOURS là (il reste jusqu'au toucher)")
    page.mouse.click(100, 400)
    check(page.evaluate(FIN) is None, "[minuit] un appui ferme l'écran de fin")
    page.evaluate("() => lanceHalo()")
    page.clock.run_for(4000)
    check(page.evaluate(FIN) is None, "[minuit] relancé, l'écran de fin n'est pas encore là à 4 s")
    ecoule = 4000
    while page.evaluate(FIN) is None and ecoule < 12000:
        page.clock.run_for(100); ecoule += 100
    check(page.evaluate(FIN) is not None and 5500 <= ecoule <= 8700, "[minuit] relancé, l'écran de fin revient quand l'écran est plein (%d ms)" % ecoule)
    page.keyboard.press("Escape")
    check(page.evaluate(FIN) is None, "[minuit] Échap ferme aussi l'écran de fin")
    page.evaluate("() => { lanceHalo(); }"); page.clock.run_for(10000)
    check(page.evaluate(FIN) is not None, "[minuit] (l'écran de fin est posé avant de relancer)")
    page.evaluate("() => lanceHalo()")
    check(page.evaluate("() => document.querySelectorAll('.fete-final').length") == 0, "[minuit] relancer la fête retire l'écran de fin déjà posé")
    # Relancer remplace au lieu d'empiler.
    page.evaluate("() => { lanceHalo(); lanceHalo(); }")
    check(page.evaluate("() => document.querySelectorAll('.fete').length") == 1
          and page.evaluate("() => document.querySelectorAll('.fete canvas').length") == 2,
          "[minuit] lancer deux fois ne pose qu'un seul calque, avec ses deux toiles")
    check(not erreurs, "[minuit] aucune erreur JavaScript (%s)" % erreurs[:1])
    ctx.close()
    # Animations réduites : rien du tout, pas même la vibration.
    ctx, page, _ = _ouvre_effets(nav, url, VEILLE, {}, reduit=True)
    page.evaluate(STUB)
    check(page.evaluate("() => lanceHalo()") is False and not page.evaluate("() => !!document.querySelector('.fete')")
          and page.evaluate("() => window.__vibre") == [],
          "[minuit] animations réduites : ni calque, ni halo, ni vibration")
    ctx.close()
    # Sans vibration dans le navigateur : la fête joue quand même, sans erreur.
    ctx, page, erreurs = _ouvre_effets(nav, url, VEILLE, {})
    page.evaluate("() => { navigator.vibrate = undefined; }")
    page.evaluate("() => lanceHalo()")
    check(page.evaluate("() => !!document.querySelector('.fete')") and not erreurs,
          "[minuit] sans navigator.vibrate, la fête joue quand même (%s)" % erreurs[:1])
    ctx.close()
    # Le déclenchement automatique : la fête part à 00:00:00 sous les yeux, pas à l'ouverture après minuit.
    ctx, page, _ = _ouvre_effets(nav, url, "2026-11-18T22:59:57Z", {})
    page.evaluate(STUB)
    page.clock.run_for(5000)
    check(page.evaluate("() => !!document.querySelector('.fete') || window.__vibre.length > 0"),
          "[minuit] à 00:00:00, la fête part toute seule")
    ctx.close()


def test_frise_des_jalons(nav, url):
    """La frise des jalons J+1, J+7, J+30 après la sortie (Antoni, 10/11/2026, maquette B)."""
    print("\n[jalons] la frise : cachée avant la sortie, remplie en continu, jalons à leur place, 320 et 390 px, case des Réglages")
    from datetime import datetime, timezone
    SORTIE = datetime(2026, 11, 18, 23, 0, tzinfo=timezone.utc)   # 19/11/2026 00:00 à Paris
    LIT = """() => { const f = document.getElementById('friseJalons'); const r = f.getBoundingClientRect();
        const noms = Array.from(f.querySelectorAll('.fj-nom')).map(n => { const b = n.getBoundingClientRect();
            return {t: n.textContent, l: b.left, r: b.right, cls: n.className}; });
        const pl = f.querySelector('.fj-plein'), tx = f.querySelector('.fj-texte');
        return {attr: f.hidden, cache: f.hidden || r.height === 0, vide: f.children.length === 0, h: r.height, l: r.left, r: r.right, iw: innerWidth,
                largeur: pl ? parseFloat(pl.style.width) : null, texte: tx ? tx.textContent : null,
                debord: tx ? tx.scrollWidth > tx.clientWidth : null, noms,
                repPasses: f.querySelectorAll('.fj-repere.passe').length, aria: f.getAttribute('aria-label'),
                fond: pl ? getComputedStyle(pl).backgroundColor : null}; }"""
    # Avant la sortie : rien.
    ctx, page, erreurs = _ouvre_effets(nav, url, VEILLE, {})
    e = page.evaluate(LIT)
    check(e["cache"] and e["vide"], "[jalons] avant la sortie, la frise est cachée et vide (%s, %s)" % (e["cache"], e["vide"]))
    ctx.close()
    # (instant, jour civil à Paris, jalons passés, texte attendu)
    cas = [("2026-11-18T23:30:00Z", 0, 0, "Prochain jalon : J+1, demain"),
           ("2026-11-19T23:30:00Z", 1, 1, "Prochain jalon : J+7, dans 6 j"),
           ("2026-11-21T12:00:00Z", 2, 1, "Prochain jalon : J+7, dans 5 j"),
           ("2026-11-26T12:00:00Z", 7, 2, "Prochain jalon : J+30, dans 23 j"),
           ("2026-12-19T12:00:00Z", 30, 3, "Les 30 premiers jours sont passés"),
           ("2027-02-01T12:00:00Z", 74, 3, "Les 30 premiers jours sont passés")]
    for instant, jour, passes, texte in cas:
        ctx, page, erreurs = _ouvre_effets(nav, url, instant, {})
        e = page.evaluate(LIT)
        t = datetime.strptime(instant, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        attendu = min(100.0, max(0.0, (t - SORTIE).total_seconds() / (30 * 86400) * 100))
        etiq = "J+%d" % jour
        check(not e["cache"], "[jalons %s] la frise est visible après la sortie" % etiq)
        check(e["largeur"] is not None and abs(e["largeur"] - attendu) < 0.2,
              "[jalons %s] le remplissage suit le temps écoulé (%s %% pour %.2f %%)" % (etiq, e["largeur"], attendu))
        check(e["repPasses"] == passes, "[jalons %s] %d jalon(s) passé(s) (%d)" % (etiq, passes, e["repPasses"]))
        check(e["texte"] == texte, "[jalons %s] le texte dit « %s » (%s)" % (etiq, texte, e["texte"]))
        check(not e["debord"], "[jalons %s] le texte tient sur sa ligne" % etiq)
        check(e["aria"] and texte in e["aria"], "[jalons %s] le lecteur d'écran reçoit la même phrase (%s)" % (etiq, e["aria"]))
        check(not erreurs, "[jalons %s] aucune erreur JavaScript (%s)" % (etiq, erreurs[:1]))
        ctx.close()
    # Les jalons passés sont cochés, les trois sont à leur place sur la barre (3 %, 23 %, 100 %).
    ctx, page, _ = _ouvre_effets(nav, url, "2026-11-26T12:00:00Z", {})
    e = page.evaluate(LIT)
    check([n["t"] for n in e["noms"]] == ["J+1 \u2713", "J+7 \u2713", "J+30"], "[jalons] J+1 et J+7 cochés, J+30 non (%s)" % [n["t"] for n in e["noms"]])
    pos = page.evaluate("() => Array.from(document.querySelectorAll('#friseJalons .fj-repere')).map(r => parseFloat(r.style.left))")
    check(len(pos) == 3 and abs(pos[0] - 100 / 30) < 0.2 and abs(pos[1] - 700 / 30) < 0.2 and abs(pos[2] - 100) < 0.2,
          "[jalons] les repères sont à leur place sur la barre (%s)" % pos)
    ctx.close()
    # Le cycle : le remplissage change de couleur avec l'accent (cycle allumé de force : on est au huitième jour).
    ctx, page, _ = _ouvre_effets(nav, url, "2026-11-26T12:00:00Z", {"cycleCouleurs": True})
    couleurs = []
    for t in (0, 2000):
        page.evaluate("(t) => { for(const a of document.getAnimations()){ if((a.animationName || '').startsWith('cycle-')){ a.pause(); a.currentTime = t; } } }", t)
        page.wait_for_timeout(150)
        couleurs.append(page.evaluate(LIT)["fond"])
    check(couleurs[0] != couleurs[1], "[jalons] le remplissage suit le cycle de couleur (%s, %s)" % tuple(couleurs))
    ctx.close()
    # Les réglages : décocher cache la frise, recocher la remet.
    ctx, page, _ = _ouvre_effets(nav, url, "2026-11-26T12:00:00Z", {})
    page.evaluate("() => openSettings()")
    page.wait_for_timeout(200)
    check(page.evaluate("() => document.getElementById('friseJalonsCase').checked") is True, "[jalons] la case est cochée d'office")
    page.evaluate("() => { const c = document.getElementById('friseJalonsCase'); c.checked = false; c.dispatchEvent(new Event('change')); }")
    # L'attribut `hidden`, pas la hauteur : sur la page des Réglages l'en-tête entier est masqué, la hauteur vaut zéro quoi qu'il arrive.
    check(page.evaluate(LIT)["attr"] is True and page.evaluate("() => settings.friseJalons") is False, "[jalons] décochée, la frise disparaît et le réglage est gardé")
    page.evaluate("() => { const c = document.getElementById('friseJalonsCase'); c.checked = true; c.dispatchEvent(new Event('change')); }")
    page.evaluate("() => closeSettings()")
    page.wait_for_timeout(200)
    check(page.evaluate(LIT)["attr"] is False and not page.evaluate(LIT)["cache"], "[jalons] recochée, la frise revient")
    ctx.close()
    # La géométrie à 320, 360, 390 et 430 px : tout tient, rien ne se touche.
    for larg in (320, 360, 390, 430):
        for instant in ("2026-11-18T23:30:00Z", "2026-11-26T12:00:00Z", "2026-12-19T12:00:00Z"):
            ctx = nav.new_context(viewport={"width": larg, "height": 800}, timezone_id="Europe/Paris", reduced_motion="reduce")
            ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));}catch(e){}")
            ctx.clock.install(time=instant)
            page = ctx.new_page()
            page.goto(url, wait_until="load")
            page.wait_for_selector(".header-haut")
            page.wait_for_timeout(300)
            e = page.evaluate(LIT)
            n = e["noms"]
            dedans = all(x["l"] >= e["l"] - 0.5 and x["r"] <= e["r"] + 0.5 for x in n)
            separes = all(n[i]["r"] + 4 <= n[i + 1]["l"] for i in range(len(n) - 1))
            check(dedans and separes and e["h"] < 70 and not e["debord"],
                  "[jalons %d px, %s] étiquettes dans la frise (%s), séparées (%s), hauteur %.0f px, texte entier (%s)"
                  % (larg, instant[:10], dedans, separes, e["h"], not e["debord"]))
            ctx.close()


def test_aucun_texte_oublie_du_cycle(nav, url):
    """Tout texte, sauf les titres d'articles, change de couleur avec le cycle (Antoni, 10/11/2026 : « on en a oublié »)."""
    print("\n[oubli] chaque texte (sauf les titres d'articles) change de couleur avec le cycle : huit vues, deux thèmes, champs et icônes compris")
    INJECTE = """() => { lastItems = Array.from({length: 8}, (_, i) => ({title: 'Un titre ' + (i + 1), link: 'https://ex.test/' + i,
          description: 'Résumé ' + i, date: new Date(Date.now() - i * 3600e3).toISOString(), source: 'S' + (i % 3), official: i === 0, lang: i % 2 ? 'en' : 'fr'}));
        lastNewLinks = new Set(['https://ex.test/1']); historyPartial = false; rattrapageLance = true; visibleCount = 8; readSet = new Set(['https://ex.test/2']);
        misDeCote = new Set(); applyFilters(); }"""
    FIGE_TOUT = """(t) => { for(const a of document.getAnimations()){ if((a.animationName || '').startsWith('cycle-')){ a.pause(); a.currentTime = t; } } }"""
    # Les textes VISIBLES à l'écran, avec leur couleur réelle (toute syntaxe CSS lue par un canvas) : le texte, le texte d'un champ vide
    # (pseudo-élément ::placeholder, que le navigateur peint d'un gris à lui), les boutons qui ne portent qu'une icône, la loupe.
    # Les titres d'articles (.card-title, liste des statistiques, aperçu) sont mis à part : ils RESTENT blancs, c'est voulu.
    SCAN = """() => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1; const cx = cv.getContext('2d', {willReadFrequently:true});
  const rgb = c => { cx.clearRect(0,0,1,1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0,0,1,1); const d = cx.getImageData(0,0,1,1).data; return [d[0],d[1],d[2]]; };
  const base = rgb(getComputedStyle(document.documentElement).getPropertyValue('--text-base').trim());
  const out = {};
  for(const e of document.querySelectorAll('body *')){
    const r = e.getBoundingClientRect();
    if(!r.width || !r.height || r.bottom < 0 || r.top > innerHeight) continue;
    const cs = getComputedStyle(e);
    if(cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) continue;
    const cls = (typeof e.className === 'string' ? e.className : '').trim().split(/\\s+/).join('.');
    const cle = e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (cls ? '.' + cls : '');
    const titre = !!e.closest('.card-title, .stats-liste, #previewTitle');
    let txt = ''; for(const n of e.childNodes) if(n.nodeType === 3) txt += n.textContent.trim();
    if(txt){ out[cle + '|' + txt.slice(0, 24)] = {c: rgb(cs.color), titre}; continue; }
    if((e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') && e.placeholder && !e.value)
      out[cle + '::placeholder|' + e.placeholder.slice(0, 20)] = {c: rgb(getComputedStyle(e, '::placeholder').color), titre};
    else if(e.querySelector(':scope > svg') && e.tagName === 'BUTTON')
      out[cle + '|icône'] = {c: rgb(cs.color), titre};
    else if(e.tagName === 'svg' && e.classList.contains('search-loupe'))
      out[cle + '|loupe'] = {c: rgb(cs.fill), titre};
  }
  return {out, base}; }"""
    VUES = [("fil", "() => null"), ("fil défilé", "() => window.scrollTo(0, 600)"), ("compteur", "() => ouvreCompteur()"),
            ("aperçu", "() => openPreview(document.querySelector('#feed .card').id.replace('card-', ''))"),
            ("filtres", "() => openFiltersSheet()"), ("réglages", "() => openSettings()"),
            ("réglages bas", "() => { openSettings(); document.querySelector('.page-reglages').scrollTo(0, 1500); window.scrollTo(0, 1500); }"),
            ("stats fil", "() => { setTab('stats'); ongletStatsChoisi('fil'); }"),
            ("stats sources", "() => { setTab('stats'); ongletStatsChoisi('sources'); }")]
    for theme in ("dark", "light"):
        oublis = []; vus_total = 0; champs = 0
        for nom, js in VUES:
            ctx, page, erreurs = _ouvre_cycle(nav, url, JOUR_J, "auto", theme)
            page.evaluate(INJECTE)
            page.wait_for_selector("#feed .card", state="attached")
            page.wait_for_timeout(500)
            page.evaluate(js)
            page.wait_for_timeout(500)
            vus = []
            for tt in (0, 2000):
                page.evaluate(FIGE_TOUT, tt)
                page.wait_for_timeout(150)
                vus.append(page.evaluate(SCAN))
            base = vus[0]["base"]
            if nom == "fil":
                tits = page.evaluate("() => Array.from(document.querySelectorAll('.card-title a')).map(a => getComputedStyle(a).color)")
                attendu = "rgb(%d, %d, %d)" % tuple(base)
                check(len(tits) >= 3 and all(c == attendu for c in tits),
                      "[oubli %s] les titres d'articles RESTENT blancs (%d titres, %s)" % (theme, len(tits), tits[:1]))
            def fige(v0, v2):
                c0 = v0["c"]
                if c0 != v2["c"]: return False
                mx, mn = max(c0), min(c0); lum = sum(c0) / 3 / 255; chroma = (mx - mn) / 255
                rouge = chroma > 0.12 and c0[1] < 0.6 * c0[0] and c0[2] < 0.6 * c0[0]    # les erreurs restent rouges, sur son conseil
                if rouge: return False
                return c0 == base or chroma > 0.12 or (0.2 <= lum < 0.88)             # le blanc d'origine, un coloré, un gris moyen
            suspects = []
            for k, v0 in vus[0]["out"].items():
                v2 = vus[1]["out"].get(k)
                vus_total += 1
                if "placeholder" in k or "icône" in k or "loupe" in k: champs += 1
                if v2 is None or v0["titre"]: continue
                if fige(v0, v2): suspects.append(k)
            if suspects:
                # SECONDE PASSE (la CI a rouge ce contrôle le 10/11/2026 : l'animation d'un bloc fraîchement affiché, ici les onglets des
                # statistiques en thème clair, n'avait pas encore démarré quand on a figé le cycle ; le texte paraissait figé). Un vrai oubli
                # est encore figé une seconde plus tard ; une animation qui n'avait pas démarré, non.
                page.wait_for_timeout(1000)
                vus2 = []
                for tt in (0, 2000):
                    page.evaluate(FIGE_TOUT, tt)
                    page.wait_for_timeout(150)
                    vus2.append(page.evaluate(SCAN))
                for k in suspects:
                    a2, b2 = vus2[0]["out"].get(k), vus2[1]["out"].get(k)
                    if a2 is not None and b2 is not None and fige(a2, b2):
                        oublis.append("%s : %s %s" % (nom, k, tuple(a2["c"])))
            check(not erreurs, "[oubli %s %s] aucune erreur JavaScript (%s)" % (theme, nom, erreurs[:1]))
            ctx.close()
        check(vus_total > 150 and champs >= 8, "[oubli %s] le balayage voit assez de choses : %d éléments, dont %d champs, icônes et loupes" % (theme, vus_total, champs))
        check(not oublis, "[oubli %s] aucun texte (hors titres d'articles) ne reste figé pendant le cycle (%d : %s)" % (theme, len(oublis), oublis[:4]))


def test_categories_de_couleur(nav, url):
    """Réglages › Couleurs : quatre familles × quatre zones, et la maquette fidèle (Antoni, 10/11/2026)."""
    print("\n[catégories] quatre familles x quatre zones : cases, effet réel sur les couleurs, maquette fidèle, 320 et 390 px")
    INJECTE = """() => { lastItems = Array.from({length: 6}, (_, i) => ({title: 'Un titre ' + (i + 1), link: 'https://ex.test/' + i,
          description: 'Résumé ' + i, date: new Date(Date.now() - i * 3600e3).toISOString(), source: 'S' + (i % 3), official: i === 0, lang: i % 2 ? 'en' : 'fr'}));
        lastNewLinks = new Set(['https://ex.test/1']); historyPartial = false; rattrapageLance = true; visibleCount = 6; readSet = new Set(); misDeCote = new Set(); applyFilters(); }"""
    FIGE_TOUT = """(t) => { for(const a of document.getAnimations()){ if((a.animationName || '').startsWith('cycle-')){ a.pause(); a.currentTime = t; } } }"""
    JETON = """([sel, jeton]) => getComputedStyle(document.querySelector(sel)).getPropertyValue(jeton).trim()"""
    FAMILLES = [("texte", "coulTextes", "--c-texte"), ("fond", "coulFonds", "--c-fond"), ("bord", "coulBordures", "--c-bord"), ("badge", "coulBadges", "--ok")]
    ZONES = [("tete", "header.console"), ("fil", ".search-row"), ("barres", ".barre-bas"), ("fenetres", ".page-reglages")]
    def stocke(page):
        return page.evaluate("() => JSON.parse(localStorage.getItem('gta6watch:settings-v1') || '{}')")
    # 1. La structure : quatre familles, chacune avec « Où ? » replié et quatre zones.
    ctx, page, erreurs = _ouvre_cycle(nav, url, VEILLE, "auto", "dark")
    page.evaluate("() => openSettings()"); page.wait_for_timeout(300)
    page.click("#persoBtn")
    struct = page.evaluate("""() => ['texte', 'fond', 'bord', 'badge'].map(f => ({f,
        zones: ['tete', 'fil', 'barres', 'fenetres'].filter(z => !!document.getElementById('zone-' + f + '-' + z)).length,
        replie: getComputedStyle(document.getElementById('zones-' + f)).display === 'none',
        expanded: document.getElementById('ou-' + f).getAttribute('aria-expanded')}))""")
    check(all(x["zones"] == 4 and x["replie"] and x["expanded"] == "false" for x in struct),
          "[catégories] quatre familles, chacune avec ses quatre zones, repliées derrière « Où ? » (%s)" % struct)
    check(page.evaluate("() => ['coulTextes', 'coulFonds', 'coulBordures', 'coulBadges'].every(i => !!document.getElementById(i))"),
          "[catégories] une case par famille : textes, fonds, bordures, badges")
    page.click("#ou-texte")
    check(page.evaluate("() => getComputedStyle(document.getElementById('zones-texte')).display") != "none"
          and page.evaluate("() => document.getElementById('ou-texte').getAttribute('aria-expanded')") == "true",
          "[catégories] « Où ? » déplie les zones et annonce son état")
    # La veille : tout en automatique, donc éteint ; rien d'écrit.
    check(not page.evaluate("() => ['coulTextes', 'coulFonds', 'coulBordures', 'coulBadges'].some(i => document.getElementById(i).checked)")
          and "coulTypes" not in stocke(page) and "coulZones" not in stocke(page),
          "[catégories] la veille, les familles sont éteintes (automatique) et rien n'est écrit tant qu'on ne touche à rien")
    # Cocher une famille écrit true ; une zone coupée n'écrit QUE le couple coupé ; la rallumer l'efface.
    page.evaluate("() => { const c = document.getElementById('coulTextes'); c.checked = true; c.dispatchEvent(new Event('change', {bubbles: true})); }")
    st = stocke(page)
    check(st.get("coulTypes", {}).get("texte") is True, "[catégories] cocher « Textes et icônes » écrit true (%s)" % st.get("coulTypes"))
    page.evaluate("() => { const c = document.getElementById('zone-texte-fil'); c.checked = false; c.dispatchEvent(new Event('change', {bubbles: true})); }")
    check(stocke(page).get("coulZones") == {"texte:fil": False}, "[catégories] couper une zone n'écrit que ce couple (%s)" % stocke(page).get("coulZones"))
    check(page.evaluate("() => document.documentElement.classList.contains('sans-texte-fil')"
                        " && !document.documentElement.classList.contains('sans-texte-tete')"),
          "[catégories] la classe « sans-texte-fil » est posée, pas celle des autres zones")
    check(page.evaluate("() => document.querySelector('.prereglage[aria-pressed=true]')") is None,
          "[catégories] une zone coupée à la main : aucun préréglage n'est surligné")
    page.evaluate("() => { const c = document.getElementById('zone-texte-fil'); c.checked = true; c.dispatchEvent(new Event('change', {bubbles: true})); }")
    check(stocke(page).get("coulZones") == {}, "[catégories] rallumer la zone efface le couple (%s)" % stocke(page).get("coulZones"))
    page.evaluate("() => { const c = document.getElementById('coulTextes'); c.checked = false; c.dispatchEvent(new Event('change', {bubbles: true})); }")
    check(page.evaluate("() => ['tete', 'fil', 'barres', 'fenetres'].every(z => document.getElementById('zone-texte-' + z).disabled)"),
          "[catégories] famille coupée : ses quatre zones sont grisées")
    page.evaluate("() => document.querySelector('.prereglage[data-p=jourj]').click()")
    st = stocke(page)
    check(st.get("coulZones") == {} and all(st["coulTypes"][f] is True for f in ("texte", "fond", "badge")),
          "[catégories] « Jour J complet » allume les trois familles et remet les zones (%s)" % st.get("coulTypes"))
    check(not erreurs, "[catégories] aucune erreur JavaScript (%s)" % erreurs[:1])
    ctx.close()
    # 1 bis. Forcer le cycle un AUTRE jour que la sortie : les familles en automatique le suivent (avant, elles ne coloraient rien : trouvé
    #        par le test des jalons, qui force le cycle le 26/11).
    ctx, page, _ = _ouvre_cycle(nav, url, "2026-11-26T12:00:00Z", True, "dark")
    page.evaluate("() => openSettings()"); page.wait_for_timeout(300)
    check(page.evaluate("() => ['coulTextes', 'coulFonds', 'coulBadges'].every(i => document.getElementById(i).checked)"),
          "[catégories] cycle forcé un autre jour : les familles en automatique le suivent (cochées)")
    page.evaluate("() => closeSettings()"); page.wait_for_timeout(300)
    tok = []
    for tt in (0, 2000):
        page.evaluate(FIGE_TOUT, tt); page.wait_for_timeout(100)
        tok.append(page.evaluate(JETON, ["header.console", "--c-texte"]))
    check(tok[0] != tok[1], "[catégories] et l'en-tête colore vraiment ses textes avec le cycle forcé (%s, %s)" % (tok[0][:20], tok[1][:20]))
    ctx.close()
    # 2. L'EFFET RÉEL : pour chaque famille et chaque zone, le jeton de la zone tourne avec le cycle quand elle est allumée,
    #    et reste sur l'accent FIXE (celui qui ne tourne pas) quand elle est coupée ; les autres zones n'y changent rien.
    for vue in ("fil", "reglages"):
        ctx, page, erreurs = _ouvre_cycle(nav, url, JOUR_J, "auto", "dark")
        page.evaluate(INJECTE)
        page.wait_for_selector("#feed .card.dans-ecran", state="attached")
        if vue == "reglages":
            page.evaluate("() => openSettings()")
        page.wait_for_timeout(500)
        zones = [z for z in ZONES if (z[0] == "fenetres") == (vue == "reglages")]
        for f, cle, jeton in FAMILLES:
            for z, sel in zones:
                def lit(etat):
                    page.evaluate("([f, z, e]) => basculeZone(f, z, e)", [f, z, etat])
                    page.wait_for_timeout(150)
                    r = []
                    for tt in (0, 2000):
                        page.evaluate(FIGE_TOUT, tt); page.wait_for_timeout(100)
                        r.append((page.evaluate(JETON, [sel, jeton]), page.evaluate(JETON, [sel, "--ok-base" if f == "badge" else "--accent-stat"])))
                    return r
                allume = lit(True)
                coupe = lit(False)
                autres = [x for x in ZONES if x[0] != z and ((x[0] == "fenetres") == (vue == "reglages"))]
                lit(True)
                check(allume[0][0] != allume[1][0],
                      "[catégories %s/%s] allumée, la zone suit le cycle (%s puis %s)" % (f, z, allume[0][0][:22], allume[1][0][:22]))
                check(coupe[0][0] == coupe[1][0] == coupe[0][1] and coupe[0][1] == coupe[1][1],
                      "[catégories %s/%s] coupée, la zone reste sur l'accent fixe (%s, %s)" % (f, z, coupe[0][0][:22], coupe[1][0][:22]))
        check(not erreurs, "[catégories %s] aucune erreur JavaScript (%s)" % (vue, erreurs[:1]))
        ctx.close()
    # 3. Une zone coupée ne touche PAS les autres : textes coupés en-tête seulement.
    ctx, page, _ = _ouvre_cycle(nav, url, JOUR_J, "auto", "dark")
    page.evaluate(INJECTE); page.wait_for_selector("#feed .card.dans-ecran", state="attached")
    page.evaluate("() => basculeZone('texte', 'tete', false)"); page.wait_for_timeout(200)
    res = []
    for tt in (0, 2000):
        page.evaluate(FIGE_TOUT, tt); page.wait_for_timeout(100)
        res.append(page.evaluate("""() => { const c = (s) => getComputedStyle(document.querySelector(s)).color;
            return {tete: c('header.console .brand'), fil: c('.search-row .filters-trigger'), barres: c('.barre-bas button:not(.active) span')}; }"""))
    check(res[0]["tete"] == res[1]["tete"] and res[0]["fil"] != res[1]["fil"] and res[0]["barres"] != res[1]["barres"],
          "[catégories] textes coupés dans l'en-tête seulement : le titre ne bouge plus, « Filtres » et la barre du bas tournent encore (%s)" % res)
    page.evaluate("() => basculeZone('texte', 'tete', true)")
    # Les VRAIS éléments : les tuiles en dégradé, un badge de langue.
    page.evaluate("() => basculeZone('bord', 'tete', false)"); page.wait_for_timeout(200)
    check("conic-gradient" not in page.evaluate("() => getComputedStyle(document.querySelector('header.console .cd-t')).backgroundImage")
          and "conic-gradient" in page.evaluate("() => getComputedStyle(document.querySelector('#feed .card:not(.official)')).backgroundImage"),
          "[catégories] bordures coupées dans l'en-tête : les tuiles perdent leur dégradé, les cartes le gardent")
    page.evaluate("() => { basculeZone('bord', 'tete', true); basculeZone('bord', 'fil', false); }"); page.wait_for_timeout(200)
    check("conic-gradient" in page.evaluate("() => getComputedStyle(document.querySelector('header.console .cd-t')).backgroundImage")
          and "conic-gradient" not in page.evaluate("() => getComputedStyle(document.querySelector('#feed .card:not(.official)')).backgroundImage"),
          "[catégories] bordures coupées dans le fil : les cartes perdent leur dégradé, les tuiles le gardent")
    page.evaluate("() => basculeZone('bord', 'fil', true)")
    page.evaluate("() => basculeCouleurFamille('badge', false)"); page.wait_for_timeout(200)
    ok = []
    for tt in (0, 2000):
        page.evaluate(FIGE_TOUT, tt); page.wait_for_timeout(100)
        ok.append(page.evaluate("() => getComputedStyle(document.querySelector('#feed .card.official')).getPropertyValue('--c-orange').trim()"))
    check(ok[0] == ok[1] == "#ff6b00",
          "[catégories] badges coupés : l'orange des articles officiels ne tourne plus (%s)" % ok)
    ctx.close()
    # 4. La maquette : les vrais éléments, copiés, sans identifiants, qui suivent les zones ; elle tient dans l'écran.
    for largeur in (320, 390):
        ctx = nav.new_context(viewport={"width": largeur, "height": 800}, timezone_id="Europe/Paris", reduced_motion="no-preference")
        ctx.add_init_script("try{localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));}catch(e){}")
        ctx.clock.install(time=JOUR_J)
        page = ctx.new_page(); erreurs = []
        page.on("pageerror", lambda e, err=erreurs: err.append(str(e)))
        page.goto(url, wait_until="load"); page.wait_for_selector(".header-haut"); page.wait_for_timeout(300)
        page.evaluate("() => openSettings()"); page.wait_for_timeout(600)
        m = page.evaluate("""() => { const m = document.getElementById('apercuCouleurs'), e = m.querySelector('.maq-ecran'), r = m.getBoundingClientRect();
            return {tuiles: e.querySelectorAll('header.console .cd-t').length, recherche: !!e.querySelector('.search-row input'), onglets: e.querySelectorAll('.tab-row .tab').length,
                    cartes: e.querySelectorAll('.card').length, officiel: !!e.querySelector('.card.official .official-badge'), barre: e.querySelectorAll('.barre-bas button').length,
                    ids: e.querySelectorAll('[id]').length, gestionnaires: e.querySelectorAll('[onclick]').length,
                    gauche: r.left, droite: r.right, haut: r.height, iw: innerWidth, deborde: document.documentElement.scrollWidth > innerWidth,
                    echelle: e.style.transform, hauteurCadre: m.querySelector('.maq-cadre').getBoundingClientRect().height}; }""")
        check(m["tuiles"] == 4 and m["recherche"] and m["onglets"] == 2 and m["cartes"] == 2 and m["officiel"] and m["barre"] == 3,
              "[maquette %d px] l'écran en petit : 4 tuiles, recherche, 2 onglets, 2 cartes dont une officielle, barre du bas à 3 boutons (%s)"
              % (largeur, {k: m[k] for k in ("tuiles", "recherche", "onglets", "cartes", "officiel", "barre")}))
        check(m["ids"] == 0 and m["gestionnaires"] == 0, "[maquette %d px] sans identifiants dupliqués ni gestionnaires de clic (%d, %d)" % (largeur, m["ids"], m["gestionnaires"]))
        check(m["gauche"] >= 0 and m["droite"] <= m["iw"] and not m["deborde"] and m["hauteurCadre"] > 100,
              "[maquette %d px] elle tient dans l'écran (%.0f à %.0f sur %d), sans débordement, hauteur %.0f px" % (largeur, m["gauche"], m["droite"], m["iw"], m["hauteurCadre"]))
        # Elle suit les zones : textes coupés dans l'en-tête, le titre de la copie ne bouge plus ; le « Filtres » de la copie tourne.
        page.evaluate("() => basculeZone('texte', 'tete', false)"); page.wait_for_timeout(250)
        res = []
        for tt in (0, 2000):
            page.evaluate(FIGE_TOUT, tt); page.wait_for_timeout(120)
            res.append(page.evaluate("""() => { const c = s => getComputedStyle(document.querySelector('#apercuCouleurs ' + s)).color;
                return [c('header.console .brand'), c('.search-row .filters-trigger')]; }"""))
        check(res[0][0] == res[1][0] and res[0][1] != res[1][1],
              "[maquette %d px] la copie suit les zones : textes coupés en-tête, son titre est fixe, son « Filtres » tourne (%s)" % (largeur, res))
        check(not erreurs, "[maquette %d px] aucune erreur JavaScript (%s)" % (largeur, erreurs[:1]))
        ctx.close()


def test_apercu_minimaliste(nav, url):
    """Réglages › Couleurs : la vue minimaliste de l'aperçu et son bouton (Antoni, 10/11/2026 : « détaillé mais minimaliste aussi »)."""
    print("\n[aperçu] deux vues : détaillée et minimaliste ; quatre zones x quatre familles en pastilles, 320 et 390 px")
    FIGE_TOUT = """(t) => { for(const a of document.getAnimations()){ if((a.animationName || '').startsWith('cycle-')){ a.pause(); a.currentTime = t; } } }"""
    def stocke(page):
        return page.evaluate("() => JSON.parse(localStorage.getItem('gta6watch:settings-v1') || '{}')")
    for largeur in (320, 390):
        ctx = nav.new_context(viewport={"width": largeur, "height": 800}, timezone_id="Europe/Paris", reduced_motion="no-preference")
        ctx.add_init_script("try{if(!localStorage.getItem('gta6watch:settings-v1'))localStorage.setItem('gta6watch:settings-v1', JSON.stringify({splashActif:false}));}catch(e){}")
        ctx.clock.install(time=JOUR_J)
        page = ctx.new_page(); erreurs = []
        page.on("pageerror", lambda e, err=erreurs: err.append(str(e)))
        page.goto(url, wait_until="load"); page.wait_for_selector(".header-haut"); page.wait_for_timeout(300)
        page.evaluate("() => openSettings()"); page.wait_for_timeout(500)
        e = page.evaluate("""() => ({boutons: [...document.querySelectorAll('.vue-apercu')].map(b => [b.dataset.v, b.getAttribute('aria-pressed'), Math.round(b.getBoundingClientRect().height)]),
            vue: document.getElementById('apercuCouleurs').getAttribute('data-vue'),
            detail: getComputedStyle(document.querySelector('#apercuCouleurs .maq-cadre')).display, mini: getComputedStyle(document.getElementById('apercuMini')).display})""")
        check(e["boutons"] == [["detaillee", "true", e["boutons"][0][2]], ["mini", "false", e["boutons"][1][2]]] and e["vue"] == "detaillee"
              and e["detail"] != "none" and e["mini"] == "none" and all(b[2] >= 44 for b in e["boutons"]),
              "[aperçu %d px] deux boutons de 44 px au moins ; la vue détaillée est celle d'origine (%s)" % (largeur, e))
        page.click(".vue-apercu[data-v=mini]"); page.wait_for_timeout(300)
        e = page.evaluate("""() => ({vue: document.getElementById('apercuCouleurs').getAttribute('data-vue'),
            presses: [...document.querySelectorAll('.vue-apercu')].map(b => b.getAttribute('aria-pressed')),
            detail: getComputedStyle(document.querySelector('#apercuCouleurs .maq-cadre')).display, mini: getComputedStyle(document.getElementById('apercuMini')).display,
            zones: [...document.querySelectorAll('#apercuMini .mini-zone')].map(z => z.dataset.z),
            pastilles: document.querySelectorAll('#apercuMini .mini-zone[data-z]:not([data-z=ecran]) .pastille').length,
            tags: document.querySelectorAll('#apercuMini .mini-zone[data-z=ecran] .pastille').length})""")
        check(e["vue"] == "mini" and e["presses"] == ["false", "true"] and e["detail"] == "none" and e["mini"] != "none",
              "[aperçu %d px] « Minimaliste » masque la maquette et montre le schéma (%s)" % (largeur, {k: e[k] for k in ("vue", "presses", "detail", "mini")}))
        check(e["zones"] == ["tete", "fil", "barres", "fenetres", "ecran"] and e["pastilles"] == 16 and e["tags"] == 3,
              "[aperçu %d px] quatre zones x quatre pastilles, plus les trois effets de l'écran du téléphone (%s)" % (largeur, e["zones"]))
        check(stocke(page).get("apercuVue") == "mini", "[aperçu %d px] le choix de la vue est gardé (%s)" % (largeur, stocke(page).get("apercuVue")))
        # Chaque zone range ses quatre pastilles sur UNE rangée, dans l'écran.
        g = page.evaluate("""() => [...document.querySelectorAll('#apercuMini .mini-zone[data-z]:not([data-z=ecran])')].map(z => {
            const r = [...z.querySelectorAll('.pastille')].map(p => p.getBoundingClientRect()), b = z.getBoundingClientRect();
            return {z: z.dataset.z, hauts: new Set(r.map(x => Math.round(x.top))).size, dedans: r.every(x => x.left >= b.left - 1 && x.right <= b.right + 1), largeurs: r.map(x => Math.round(x.width))}; })""")
        check(all(x["hauts"] == 1 and x["dedans"] for x in g) and not page.evaluate("() => document.documentElement.scrollWidth > innerWidth"),
              "[aperçu %d px] chaque zone tient ses quatre pastilles sur une rangée, rien ne déborde (%s)" % (largeur, [(x["z"], x["hauts"], x["largeurs"]) for x in g][:2]))
        # Elle suit l'état : tout allumé d'abord (jour J, automatique).
        lit = """() => [...document.querySelectorAll('#apercuMini .mini-zone[data-z]:not([data-z=ecran]) .pastille')].map(p => p.dataset.z + ':' + p.dataset.f + ':' + (p.classList.contains('on') ? 'on' : 'off'))"""
        etat = page.evaluate(lit)
        check(len(etat) == 16 and all(x.endswith(":on") for x in etat), "[aperçu %d px] le jour J, tout est coloré dans les 16 pastilles" % largeur)
        page.evaluate("() => { basculeZone('texte', 'fil', false); basculeCouleurFamille('badge', false); }"); page.wait_for_timeout(300)
        etat = page.evaluate(lit)
        coupes = sorted(x for x in etat if x.endswith(":off"))
        attendu = sorted(["fil:texte:off"] + ["%s:badge:off" % z for z in ("tete", "fil", "barres", "fenetres")])
        check(coupes == attendu, "[aperçu %d px] une zone et une famille coupées : exactement ces pastilles s'éteignent (%s)" % (largeur, coupes))
        aria = page.evaluate("() => document.querySelector('#apercuMini .mini-zone[data-z=fil]').getAttribute('aria-label')")
        check("textes coupés" in aria and "fonds colorés" in aria, "[aperçu %d px] le bloc le dit en clair à un lecteur d'écran (« %s »)" % (largeur, aria))
        page.evaluate("() => basculeEffet('coulCadre', false)"); page.wait_for_timeout(200)
        tags = page.evaluate("() => [...document.querySelectorAll('#apercuMini [data-e]')].map(p => p.dataset.e + ':' + (p.classList.contains('on') ? 'on' : 'off'))")
        check("coulCadre:off" in tags and "coulBarreEtat:on" in tags, "[aperçu %d px] les trois effets de l'écran suivent leurs cases (%s)" % (largeur, tags))
        # Les pastilles pleines TOURNENT avec le cycle ; une pastille coupée ne bouge pas.
        res = []
        for tt in (0, 2000):
            page.evaluate(FIGE_TOUT, tt); page.wait_for_timeout(120)
            res.append(page.evaluate("""() => { const c = s => getComputedStyle(document.querySelector('#apercuMini ' + s));
                const off = c('.pastille.off[data-f=texte]');
                return [c('.pastille.on[data-f=fond]').backgroundColor, [off.borderTopStyle, off.opacity, off.textDecorationLine]]; }"""))
        check(res[0][0] != res[1][0] and res[0][1] == res[1][1] == ["dashed", "0.5", "line-through"],
              "[aperçu %d px] une pastille pleine suit le cycle ; une coupée est en pointillé, éteinte et barrée (%s)" % (largeur, res))
        check(not erreurs, "[aperçu %d px] aucune erreur JavaScript (%s)" % (largeur, erreurs[:1]))
        # Rouvrir : la vue choisie est celle de l'ouverture.
        page.reload(wait_until="load"); page.wait_for_selector(".header-haut"); page.wait_for_timeout(300)
        page.evaluate("() => openSettings()"); page.wait_for_timeout(400)
        check(page.evaluate("() => document.getElementById('apercuCouleurs').getAttribute('data-vue')") == "mini",
              "[aperçu %d px] rouverte, l'app reprend la vue minimaliste" % largeur)
        page.click(".vue-apercu[data-v=detaillee]"); page.wait_for_timeout(400)
        check(page.evaluate("() => document.getElementById('apercuCouleurs').getAttribute('data-vue')") == "detaillee"
              and page.evaluate("() => document.querySelectorAll('#apercuCouleurs .maq-ecran .card').length") == 2,
              "[aperçu %d px] retour à la vue détaillée : la maquette est reconstruite (2 cartes)" % largeur)
        ctx.close()


def test_tout_dans_le_cycle(nav, url):
    """Tout ce qui n'est pas blanc de base passe dans le cycle (Antoni, 11/11/2026) ; les erreurs rouges restent rouges."""
    print("\n[cycle] statuts, textes gris, badges, orange en blanc-jaune-orange ; les erreurs restent rouges")
    INJECTE = """() => { lastItems = Array.from({length: 6}, (_, i) => ({title: 'Un titre ' + (i + 1), link: 'https://ex.test/' + i,
          description: 'Résumé ' + i, date: new Date(Date.now() - i * 3600e3).toISOString(), source: 'S' + (i % 3), official: i === 0}));
        lastNewLinks = new Set(); historyPartial = false; rattrapageLance = true; visibleCount = 6; readSet = new Set(); misDeCote = new Set(); applyFilters(); }"""
    SONDE = """() => {
        const ent = document.querySelector('header.console'), cs = getComputedStyle(ent);
        const carte = document.querySelector('#feed .card.official'), cc = carte ? getComputedStyle(carte) : null;
        const sonde = document.createElement('span'); sonde.style.color = 'var(--danger)'; ent.appendChild(sonde);
        const danger = getComputedStyle(sonde).color; sonde.remove();
        const g = n => cs.getPropertyValue(n).trim();
        return {accent: g('--accent'), ok: g('--ok'), warn: g('--warn'), gris: g('--text-dim'), base: g('--text-dim-base'),
                hot: g('--hot-texte'), langue: g('--langue-fr'), danger,
                officiel: cc ? cc.borderTopColor : null, orange: carte ? cc.getPropertyValue('--orange').trim() : null,
                cat: carte ? cc.getPropertyValue('--cat-bug').trim() : null, accentCarte: carte ? cc.getPropertyValue('--accent').trim() : null,
                badge: carte && carte.querySelector('.official-badge') ? getComputedStyle(carte.querySelector('.official-badge')).backgroundColor : null}; }"""
    rgb = lambda c: tuple(int(x) for x in re.findall(r"\d+", c)[:3])
    for theme, accents, oranges in (("dark", ["#19e6ff", "#b388ff", "#ff5cae"], ["#ffffff", "#ffd60a", "#ff6b00"]),
                                    ("light", ["#00708a", "#7c3aed", "#b8146f"], ["#ffa500", "#ffc400", "#ff6b00"])):
        ctx, page, erreurs = _ouvre_cycle(nav, url, JOUR_J, "auto", theme)
        page.evaluate(INJECTE)
        page.wait_for_selector("#feed .card.official", state="attached")
        page.wait_for_timeout(500)
        page.evaluate("""() => { const c = document.querySelector('#feed .card.official');
            const r = document.createElement('span'); r.className = 'official-badge'; r.textContent = 'OFFICIEL';
            const row = c.querySelector('.card-src-row') || c.querySelector('.card-top'); if(row) row.appendChild(r); }""")
        vus = []
        for i, t in enumerate((0, 1000, 2000)):
            page.evaluate(FIGE, t)
            page.evaluate("(t) => { for(const a of document.getAnimations()){ if(a.animationName === 'cycle-accent' || (a.animationName || '').startsWith('cycle-')){ a.pause(); a.currentTime = t; } } }", t)
            page.wait_for_timeout(150)
            m = page.evaluate(SONDE)
            vus.append(m)
            check(rgb(m["ok"]) == rgb(m["accent"]) and rgb(m["warn"]) == rgb(m["accent"]),
                  "[tout %s %d ms] les statuts vert et jaune suivent l'accent (%s, %s, %s)" % (theme, t, m["ok"], m["warn"], m["accent"]))
            check(rgb(m["langue"]) == rgb(m["accent"]), "[tout %s %d ms] la couleur des langues suit l'accent (%s)" % (theme, t, m["langue"]))
            check(m["gris"] != m["base"], "[tout %s %d ms] le texte gris est teinté, plus le gris d'origine (%s)" % (theme, t, m["gris"]))
            check(rgb(m["danger"]) == ((248, 113, 113) if theme == "dark" else rgb(m["danger"])) and rgb(m["danger"]) != rgb(m["accent"]),
                  "[tout %s %d ms] les erreurs restent rouges, jamais dans le cycle (%s)" % (theme, t, m["danger"]))
            if m["cat"] is not None:
                check(rgb(m["cat"]) == rgb(m["accentCarte"]), "[tout %s %d ms] les couleurs de catégorie d'une carte suivent l'accent (%s)" % (theme, t, m["cat"]))
            attendu = tuple(int(oranges[i][j:j + 2], 16) for j in (1, 3, 5))
            check(m["orange"] and rgb(m["orange"]) == attendu and rgb(m["officiel"]) == attendu,
                  "[tout %s %d ms] l'orange des officiels est au cran %d du dégradé blanc, jaune, orange (%s, bordure %s)" % (theme, t, i + 1, m["orange"], m["officiel"]))
            if m["badge"]:
                check(rgb(m["badge"]) == attendu, "[tout %s %d ms] le badge « OFFICIEL » suit le même dégradé (%s)" % (theme, t, m["badge"]))
        check(len({v["gris"] for v in vus}) == 3 and len({v["ok"] for v in vus}) == 3,
              "[tout %s] trois instants, trois teintes différentes pour le gris et les statuts" % theme)
        check(not erreurs, "[tout %s] aucune erreur JavaScript (%s)" % (theme, erreurs[:1]))
        ctx.close()
    # Cycle éteint : tout retrouve ses couleurs d'origine.
    ctx, page, _ = _ouvre_cycle(nav, url, JOUR_J, False, "dark")
    page.evaluate(INJECTE)
    page.wait_for_selector("#feed .card.official", state="attached")
    m = page.evaluate(SONDE)
    check(m["gris"] == m["base"] and rgb(m["officiel"]) == (255, 107, 0) and rgb(m["ok"]) != rgb(m["accent"]),
          "[tout] cycle éteint : le gris, l'orange (255, 107, 0) et le vert d'origine (%s, %s, %s)" % (m["gris"], m["officiel"], m["ok"]))
    ctx.close()


def test_chevron_toujours_sur_une_ligne(nav, url):
    """Le chevron « Par source » reste sur la ligne du comptage, partout (Antoni, 11/11/2026)."""
    print("\n[chevron] toujours sur une ligne : comptage + chevron, 320 à 430 px, tous les filtres, lignes d'historique comprises")
    for largeur in (320, 360, 390, 430):
        ctx, page, erreurs = _ouvre_fil(nav, url, largeur)
        for tab in ("all", "non-rockstar", "rockstar", "rockstarmag"):
            page.evaluate("(t) => setTab(t)", tab)
            page.wait_for_timeout(120)
            # Pire cas, posé APRÈS le changement d'onglet (qui réécrit le comptage et la ligne d'historique) : le
            # plus long des textes de comptage ET une ligne d'historique visible.
            page.evaluate("""() => { const c = document.getElementById('countLine');
                c.dataset.variantes = JSON.stringify(['500 affichés · 500 non lus · 142 masqués', '500 affichés · 500 non lus', '500 non lus']);
                const h = document.getElementById('historyLine'); h.style.display = 'flex';
                h.innerHTML = '<span>Historique partiel.</span><button class="small">Tout charger (3,7 Mo)</button>';
                ajusteComptage(); }""")
            m = page.evaluate("""() => { const l = document.querySelector('.comptage-ligne').getBoundingClientRect(),
                c = document.getElementById('countLine'), cr = c.getBoundingClientRect(), b = document.getElementById('sourcesBtn'), br = b.getBoundingClientRect(),
                lh = parseFloat(getComputedStyle(c).lineHeight) || 18;
                return {hLigne: l.height, ecartY: Math.abs((cr.top + cr.height / 2) - (br.top + br.height / 2)), unSeul: c.getClientRects().length === 1,
                        hTexte: cr.height, lh, tient: c.scrollWidth <= c.clientWidth + 1, droite: br.right, boite: l.right,
                        txt: c.textContent, lib: document.getElementById('sourcesLibelle').textContent, largeurBtn: br.width,
                        debord: document.documentElement.scrollWidth > innerWidth}; }""")
            ok = (m["hLigne"] <= 26 and m["ecartY"] <= 6 and m["hTexte"] <= m["lh"] * 1.6 and m["droite"] <= m["boite"] + 1 and not m["debord"])
            check(ok, "[chevron %d px, %s] une seule ligne de %.0f px, le chevron au même niveau que le texte (écart %.1f px) : « %s » + « %s »"
                  % (largeur, tab, m["hLigne"], m["ecartY"], m["txt"], m["lib"]))
            check(m["tient"], "[chevron %d px, %s] le texte garde la phrase la plus complète qui tient, sans ellipse (%s)" % (largeur, tab, m["txt"]))
        check(not erreurs, "[chevron %d px] aucune erreur JavaScript (%s)" % (largeur, erreurs[:1]))
        ctx.close()
    # Abréviations : NR, R, RM, et rien sans filtre.
    ctx, page, _ = _ouvre_fil(nav, url, 390)
    lues = {}
    for tab in ("all", "non-rockstar", "rockstar", "rockstarmag"):
        page.evaluate("(t) => setTab(t)", tab)
        lues[tab] = page.evaluate("() => document.getElementById('sourcesLibelle').textContent")
    check(lues == {"all": "", "non-rockstar": "NR", "rockstar": "R", "rockstarmag": "RM"},
          "[chevron] abréviations : rien, NR, R, RM (%s)" % lues)
    ctx.close()


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright absent — `pip install -r requirements.txt` puis "
              "`python -m playwright install chromium`")
        return 1

    srv, url = sert_docs()
    # PLAYWRIGHT_BROWSERS_PATH pointe déjà le bon dossier en CI comme en local ;
    # on ne force un exécutable que s'il est explicitement fourni.
    exe = os.environ.get("CHROMIUM_PATH") or None
    try:
        with sync_playwright() as p:
            nav = p.chromium.launch(executable_path=exe)
            for largeur in LARGEURS:
                ctx = nav.new_context(viewport={"width": largeur, "height": 850})
                page = ctx.new_page()
                erreurs = []
                page.on("pageerror", lambda e: erreurs.append(str(e)))
                page.goto(url, wait_until="load")
                page.wait_for_selector("#feed", state="attached")
                for fn in (test_cibles_tactiles, test_rien_ne_deborde,
                           test_replie_est_replie, test_libelles_centres,
                           test_etats_annonces, test_comportements):
                    fn(page, largeur)
                    # Chaque contrôle repart d'une page propre : un panneau
                    # laissé ouvert ou un filtre laissé rempli ferait échouer
                    # le suivant pour une raison qui n'est pas la sienne.
                    page.goto(url, wait_until="load")
                    page.wait_for_selector("#feed", state="attached")
                check(not erreurs,
                      "[%d px] aucune erreur JavaScript%s"
                      % (largeur, "" if not erreurs else " — " + erreurs[0][:120]))
                ctx.close()
            # Hors de la boucle des largeurs : ce contrôle ne regarde pas une
            # géométrie, et ouvre ses propres contextes pour brancher ses
            # interceptions avant le chargement de la page.
            test_repli_backend(nav, url)
            test_vignette_ouvre_larticle(nav, url)
            test_archive_dans_lapp(nav, url)
            test_archive_mois_par_mois(nav, url)
            test_actualiser_ne_jette_plus_rien(nav, url)
            test_recherche_sans_accents(nav, url)
            test_barre_detat_suit_le_theme(nav, url)
            test_ecran_de_chargement(nav, url)
            test_barre_du_bas(nav, url)
            test_page_reglages(nav, url)
            test_entete_tuiles_et_ligne_reduite(nav, url)
            test_fenetre_du_compteur(nav, url)
            test_compteurs_jumeaux(nav, url)
            test_toast_annuler(nav, url)
            test_poids_de_tout_charger(nav, url)
            test_plafond_des_lus(nav, url)
            test_sauvegarde_export_import(nav, url)
            test_vue_statistiques(nav, url)
            test_entete_sans_vide(nav, url)
            test_heure_de_premiere_vue(nav, url)
            test_reprise_des_liens_redecodes(nav, url)
            test_bloc_incidents(nav, url)
            test_stats_quatre_rubriques(nav, url)
            test_officiel_et_filtres_etroits(nav, url)
            test_badge_actu_majeure_lisible(nav, url)
            test_onglet_actu_majeure(nav, url)
            test_sources_disent_la_verite(nav, url)
            test_compte_a_rebours(nav, url)
            test_resume_du_mode_de_secours(nav, url)
            test_mode_sans_spoiler(nav, url)
            test_vecteurs_du_resume(nav, url)
            test_compte_a_rebours_flashy(nav, url)
            test_agenda_jours_civils(nav, url)
            test_agenda_dans_le_panneau(nav, url)
            test_panneau_du_compteur(nav, url)
            test_budget_du_stockage(nav, url)
            test_contraste_partout(nav, url)
            test_le_service_worker_sans_push(nav, url)
            test_mettre_de_cote(nav, url)
            test_le_vrai_editeur(nav, url)
            test_mur_du_jour_j(nav, url)
            test_badges_filtres_chrono_et_recherches(nav, url)
            test_graphiques_detailles(nav, url)
            test_graphiques_autres_rubriques(nav, url)
            test_palette_du_jour_j(nav, url)
            test_contraste_jour_j(nav, url)
            test_texte_douze_px_et_coins(nav, url)
            test_balayage_des_cartes(nav, url)
            test_cycle_de_couleurs(nav, url)
            test_cycle_synchronise(nav, url)
            test_contraste_du_cycle(nav, url)
            test_tout_dans_le_cycle(nav, url)
            test_effets_du_jour_j(nav, url)
            test_coins_du_cadre(nav, url)
            test_lot_2a_interface(nav, url)
            test_chevron_toujours_sur_une_ligne(nav, url)
            test_bordures_du_jour_j(nav, url)
            test_lot_2b_interface(nav, url)
            test_case_globale_des_effets(nav, url)
            test_grand_moment_de_minuit(nav, url)
            test_frise_des_jalons(nav, url)
            test_aucun_texte_oublie_du_cycle(nav, url)
            test_categories_de_couleur(nav, url)
            test_apercu_minimaliste(nav, url)
            test_stats_et_reglages_compacts(nav, url)
            nav.close()
    finally:
        srv.shutdown()

    print("\n%d/%d contrôles rendus passés" % (CHECKS - len(FAILURES), CHECKS))
    if FAILURES:
        print("ÉCHECS :")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("Tout est vert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
