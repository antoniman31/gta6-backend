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
        for (const b of document.querySelectorAll('#settingsOverlay button')) {
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
        for (const b of document.querySelectorAll('#settingsOverlay button')) {
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
        for (const b of document.querySelectorAll('#settingsOverlay button')) {
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
        for (const c of document.querySelectorAll('#settingsOverlay input, #settingsOverlay textarea')) {
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
                                 m.innerHTML = '● Backend GitHub'; }""")
        une_ligne = page.evaluate("""() => {
          const t = document.querySelector('.brand').getBoundingClientRect();
          const d = document.querySelector('.header-right').getBoundingClientRect();
          return d.top < t.bottom; }""")
        check(une_ligne, "[%d px] l'entête tient sur une ligne, sans le vide signalé" % largeur)
        check(not erreurs, "[%d px] aucune erreur au démarrage — applyTheme() n'écrit plus "
              "dans un bouton disparu%s" % (largeur, "" if not erreurs else " : " + erreurs[0][:100]))
        ctx.close()

    ctx = nav.new_context(viewport={"width": 368, "height": 800})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector(".header-actions")
    boutons = page.evaluate("""() => [...document.querySelectorAll('.header-actions .icon-btn')]
                                       .map(b => b.getAttribute('aria-label'))""")
    check(len(boutons) == 3, "trois boutons dans l'entête : %s" % boutons)
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
      ]""")
    check(medias[0] == "IGN", "un titre Google News rend son vrai média : %s" % medias[0])
    check(medias[1] == "GameSpot",
          "un flux natif garde sa source — la queue de titre y serait un sous-titre")
    check(medias[2] == "Google News (EN)", "une queue trop bavarde n'est pas un média")
    check(medias[3] == "Google News (FR)", "sans tiret, la source sert de média")

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
        page.wait_for_selector("#tabRockstarmag")
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
        const carte = [...document.querySelectorAll('.card')].find(c => c.querySelector('.card-thumb'));
        if(!carte) return null;
        const img = carte.querySelector('.card-thumb');
        const lien = carte.querySelector('.card-thumb-lien');
        const titre = carte.querySelector('.card-title a');
        const r = img.getBoundingClientRect();
        return {
            dansLien: !!lien && lien.contains(img),
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
            page.click(".card .card-thumb", force=True)
        ouvert = onglet.value
    except Exception:
        ouvert = None

    check(ouvert is not None,
          "[vignette] le clic ouvre bien un onglet sur l'article")
    check(attendu in demandees,
          "[vignette] et c'est la bonne adresse qui est demandée (%s)" % attendu)

    lu = page.evaluate("""() => {
        const c = [...document.querySelectorAll('.card')].find(x => x.querySelector('.card-thumb'));
        return c ? c.classList.contains('read') : false;
    }""")
    check(lu, "[vignette] et l'article est marqué comme lu, comme par le titre")
    ctx.close()


def test_sources_disent_la_verite(nav, url):
    """Les 63 interrupteurs de source ne font RIEN quand le robot alimente le fil.

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
    check(etat["resume"] == "63 sources",
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
    check("robot" in etat["ligne"] and "63" in etat["ligne"],
          "[sources] la ligne des Informations annonce les sources du ROBOT "
          "(« %s »)" % etat["ligne"])

    # ON N'EFFACE RIEN : le mode de secours doit retrouver le réglage.
    check(etat["nbCases"] == 63,
          "[sources] les 63 interrupteurs sont toujours là, repliés "
          "(%d)" % etat["nbCases"])

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


def page_couverture(nav, url):
    """Combien d'heures le fil réellement chargé couvre-t-il ?

    Le fichier allégé garde un NOMBRE D'ARTICLES, pas un nombre de jours :
    sa couverture en heures rétrécit à mesure que le rythme du fil monte.
    Tout contrôle qui écrit cet écart en dur meurt à la première hausse.
    """
    base = url.rsplit("/", 1)[0]
    ctx = nav.new_context(viewport={"width": 390, "height": 850})
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_selector("#feed", state="attached")
    page.evaluate("rattrapageLance = true;")
    page.evaluate("settings.backendUrl = '%s/feed.json';" % base)
    page.evaluate("async () => { await checkFromBackend(false); }")
    h = page.evaluate("""() => {
        const d = lastItems.map(i => Date.parse(i.date)).filter(Number.isFinite);
        if(d.length < 2) return 0;
        return (Math.max(...d) - Math.min(...d)) / 3600000;
    }""")
    ctx.close()
    return int(h)


def test_ce_que_jai_rate(nav, url):
    """Le bloc d'entrée : un bilan vrai, puis les sources suivies.

    CE QU'IL N'EST PAS. L'idée d'origine était « les cinq sujets importants
    parus depuis la dernière visite ». Mesuré sur le fil réel le 13/10/2026,
    elle ne tient pas : « majeur » est à 0 % pendant trois jours (c'est un
    signal rétrospectif, il faut que d'autres médias reprennent l'article),
    les articles ne se regroupent pas en sujets (119 donnent 117 groupes),
    et un top 5 par score servait quatre fois la même fuite. D'où un bilan
    chiffré plus les sources que le robot étiquette lui-même.
    """
    base = url.rsplit("/", 1)[0]

    def session(heures, avant=None):
        ctx = nav.new_context(viewport={"width": 390, "height": 850})
        page = ctx.new_page()
        erreurs = []
        page.on("pageerror", lambda e: erreurs.append(str(e)))
        page.goto(url, wait_until="load")
        page.wait_for_selector("#feed", state="attached")
        page.evaluate("rattrapageLance = true;")
        page.evaluate("settings.backendUrl = '%s/feed.json';" % base)
        if heures is None:
            page.evaluate("derniereVisite = null;")
        else:
            page.evaluate("() => { derniereVisite = Date.now() - %d*3600*1000; }" % heures)
        if avant:
            avant(page)
        page.evaluate("async () => { await checkFromBackend(false); }")
        page.evaluate("applyFilters();")
        etat = page.evaluate("""() => {
            const el = document.getElementById('rateBloc');
            const bilan = el.querySelector('.rate-bilan');
            return {
                affiche: el.style.display,
                plein: el.dataset.plein,
                bilan: bilan ? bilan.textContent : "",
                lignes: [...el.querySelectorAll('.rate-liste li a')].map(a => ({
                    source: a.querySelector('.rate-source').textContent,
                    href: a.getAttribute('href'),
                })),
            };
        }""")
        return page, ctx, etat, erreurs

    # --- Première visite : aucun point de référence, donc AUCUN bloc. Mieux
    # vaut rien qu'un bandeau qui annonce zéro.
    page, ctx, etat, err = session(None)
    check(etat["affiche"] == "none" and etat["plein"] == "0",
          "[raté] sans visite précédente, pas de bloc du tout")
    ctx.close()

    # --- Une absence que la fenêtre COUVRE : le compte est exact.
    #
    # L'écart n'est plus écrit en dur. Il l'était — 36 h, « parce que la
    # fenêtre chargée fait 2 j 1/2 » — et ça a fini par tomber le
    # 14/10/2026 : le fichier allégé garde 300 articles, pas un nombre de
    # jours, et le rythme du fil est passé de ~125 à ~248 articles/jour à
    # l'approche de la sortie. Les mêmes 300 articles ne couvraient plus
    # que 29 h. L'app avait raison d'écrire « au moins », c'est le contrôle
    # qui vivait sur une mesure périmée — et il aurait replanté chaque fois
    # que le rythme monte, c'est-à-dire tous les jours d'ici novembre.
    #
    # On lit donc la couverture RÉELLE du fil chargé, et on se place de
    # part et d'autre. Les deux comportements sont vérifiés, au lieu d'un
    # seul sur un pari.
    couverture = page_couverture(nav, url)
    dedans = max(1, int(couverture * 0.5))
    dehors = int(couverture * 2) + 24
    page, ctx, etat, err = session(dedans)
    check(etat["affiche"] == "block" and etat["plein"] == "1",
          "[raté] après une absence de %d h, le bloc est là" % dedans)
    check("Depuis ta dernière visite" in etat["bilan"],
          "[raté] il s'ouvre sur un bilan (« %s »)" % etat["bilan"][:60])
    check("au moins" not in etat["bilan"],
          "[raté] dans la fenêtre chargée (%d h sur %d h couvertes), le "
          "compte est exact, pas minoré" % (dedans, couverture))
    ctx.close()

    # --- Et une absence que la fenêtre NE couvre PAS : le bloc le DIT.
    # C'est la moitié du contrat qui n'était pas éprouvée, et c'est celle
    # qui servira le 19 novembre, quand 300 articles ne feront plus
    # quelques heures.
    page2, ctx2, etat2, _ = session(dehors)
    check("au moins" in etat2["bilan"],
          "[raté] au-delà (%d h), il annonce « au moins » au lieu de "
          "minorer en silence (« %s »)" % (dehors, etat2["bilan"][:70]))
    ctx2.close()

    page, ctx, etat, err = session(dedans)
    check(0 < len(etat["lignes"]) <= 4,
          "[raté] au plus quatre lignes, et il rétrécit au lieu de se "
          "remplir (%d ici)" % len(etat["lignes"]))
    check(all(l["href"].startswith("http") for l in etat["lignes"]),
          "[raté] chaque ligne mène à son article")

    # Les sources suivies, et SEULEMENT elles : pas de score inventé.
    srcs = page.evaluate("""() => {
        const liens = [...document.querySelectorAll('#rateBloc .rate-liste a')]
            .map(a => a.getAttribute('href'));
        return liens.map(h => {
            const i = lastItems.find(x => x.link === h);
            return i ? Boolean(i.official || i.specialist || i.rockstarmag) : false;
        });
    }""")
    check(all(srcs),
          "[raté] toutes les lignes viennent de sources suivies (%s)" % srcs)

    # L'ordre : officiels, puis spécialistes, puis RockstarMag.
    #
    # ON INJECTE UN OFFICIEL. Sans lui le fil réel ne contient aujourd'hui
    # que des spécialistes — mesuré : 26 jours sur 30 sans le moindre
    # article officiel — et le contrôle rendait [1, 1, 1, 1], « trié » par
    # accident. Il serait resté vert en laissant passer n'importe quel
    # ordre. Et l'officiel est VIEUX : s'il remontait par sa date, on ne
    # saurait pas si c'est son rang ou sa fraîcheur qui l'a placé.
    # L'ÂGE DE L'OFFICIEL SUIT L'ÉCART, il n'est plus écrit en dur. Il
    # valait 30 h, pour un écart de 36 h ; l'écart se mesure maintenant sur
    # le fil réel et vaut moins que ça. Un officiel plus vieux que la
    # dernière visite ne serait tout simplement pas « depuis ta dernière
    # visite », et le contrôle cherchait une ligne qui ne pouvait pas
    # exister. On le place juste À L'INTÉRIEUR de l'écart : dedans, donc
    # compté, mais plus vieux que les spécialistes du fil, donc classé par
    # son rang et non par sa fraîcheur.
    rangs = page.evaluate("""(heures) => {
        lastItems.push({
            link: 'https://www.rockstargames.com/newswire/article/zzz-test',
            title: 'Annonce officielle de contrôle',
            source: 'Rockstar Games (officiel EN)',
            official: true,
            date: new Date(Date.now() - heures*3600*1000).toISOString(),
        });
        applyFilters();
        return [...document.querySelectorAll('#rateBloc .rate-liste a')]
            .map(a => lastItems.find(x => x.link === a.getAttribute('href')))
            .map(i => i.official ? 0 : i.specialist ? 1 : 2);
    }""", max(1, dedans - 1))
    check(rangs and rangs[0] == 0 and rangs == sorted(rangs) and len(set(rangs)) > 1,
          "[raté] un officiel passe DEVANT des spécialistes plus récents, "
          "et l'ordre reste officiel → spécialiste → RockstarMag (%s)" % rangs)

    # Cliquer une ligne la marque lue — sinon le bloc redit éternellement la
    # même chose à quelqu'un qui l'a déjà suivi.
    lien = etat["lignes"][0]["href"]
    page.evaluate("marqueLuDepuisRate('%s')" % lien)
    check(page.evaluate("readSet.has('%s')" % lien),
          "[raté] cliquer une ligne marque l'article comme lu")
    check(not err, "[raté] aucune erreur JavaScript (%s)" % err[:1])
    ctx.close()

    # --- 10 jours : LE cas malhonnête. La fenêtre chargée ne couvre plus
    # l'absence, et un compte sec mentirait sans le dire — « 300 articles »
    # après une semaine, alors qu'il y en a eu mille. On ne sait pas combien
    # il en manque, donc on ne l'invente pas.
    page, ctx, etat, err = session(24 * 10)
    check("au moins" in etat["bilan"],
          "[raté] au-delà de la fenêtre chargée, le compte se dit minoré "
          "(« %s »)" % etat["bilan"][:70])
    ctx.close()

    # --- Le bloc suit le fil : il n'a aucun sens sous les statistiques.
    page, ctx, etat, err = session(36)
    page.evaluate("setTab('stats')")
    cache = page.evaluate("document.getElementById('rateBloc').style.display")
    page.evaluate("setTab('all')")
    revenu = page.evaluate("document.getElementById('rateBloc').style.display")
    check(cache == "none" and revenu == "block",
          "[raté] masqué sous Statistiques, rendu en revenant au fil "
          "(%s puis %s)" % (cache, revenu))
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
    check("JUGES_BADGE[" in extrait,
          "[badges] la carte passe par la table des juges, partagée avec le "
          "filtre")
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
            .filter(b => !/tag-(leak|video|pc|preco|prix|perso|take2|rumeur|date|hot|lang)/.test(b.className))
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
            test_onglet_actu_majeure(nav, url)
            test_sources_disent_la_verite(nav, url)
            test_ce_que_jai_rate(nav, url)
            test_mur_du_jour_j(nav, url)
            test_badges_filtres_chrono_et_recherches(nav, url)
            test_graphiques_detailles(nav, url)
            test_graphiques_autres_rubriques(nav, url)
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
