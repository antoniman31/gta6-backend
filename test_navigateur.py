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
        page.on("request", lambda r: demandes.append(r.url.split("/")[-1].split("?")[0])
                if ("feed.json" in r.url or "feed-recent.json" in r.url) else None)
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
    check("couvertureDepuis" not in s and "mois" not in s,
          "le calcul ne produit AUCUNE date de couverture ni aucun mois : rien "
          "dans le fil ne permet de les établir sans deviner")
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

    page.evaluate("""() => {
      historyPartial = false; rattrapageLance = true; archiveChargee = true;
      lastItems = [];
      for(let j = 1; j <= 22; j++){
        lastItems.push({title: "Article " + j, link: "https://ex.test/" + j,
          source: "S", date: "2026-09-" + String(j).padStart(2, "0") + "T10:00:00Z",
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
          "un seul chiffre écrit par histogramme — le maximum, pas un par colonne")
    check(page.locator(".stats-tableau table").count() == page.locator(".stats-histo").count(),
          "chaque histogramme a son tableau : l'infobulle ne s'affiche pas au doigt")
    texte_fil = page.inner_text("#statsContenu")
    check("Articles par mois" not in texte_fil,
          "aucun histogramme mensuel : il faudrait que le robot publie sa couverture")
    check("ouverture complète" not in texte_fil and "depuis le" not in texte_fil,
          "et AUCUNE date de couverture affichée — c'est l'affirmation fausse "
          "que la première version montrait (« depuis le 07/08 »)")
    check("pas la presse" in texte_fil,
          "le graphique dit ce qu'il mesure : le fil, pas le volume de la presse")

    # Bloc vide = bloc absent.
    page.click("#statsOngletSources")
    page.wait_for_timeout(100)
    texte = page.inner_text("#statsContenu")
    check("Alpha" in texte, "la rubrique Sources affiche la réponse du robot")
    check("En forte baisse" not in texte,
          "et n'affiche PAS « en forte baisse » quand aucune ne l'est")
    check(page.get_attribute("#statsOngletSources", "aria-pressed") == "true",
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
    deborde = page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")
    check(not deborde, "à 320 px, la vue ne déborde pas horizontalement")
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
    page.route("**/*.{png,jpg,jpeg,webp,gif}",
               lambda r: r.fulfill(status=200, content_type="image/png", body=png))
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
            focalisables: [...carte.querySelectorAll('a[href]')].filter(x => x.tabIndex >= 0).length,
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
          "[vignette] la carte garde UN seul lien focalisable, celui du titre "
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
            test_actualiser_ne_jette_plus_rien(nav, url)
            test_recherche_sans_accents(nav, url)
            test_barre_detat_suit_le_theme(nav, url)
            test_toast_annuler(nav, url)
            test_sauvegarde_export_import(nav, url)
            test_vue_statistiques(nav, url)
            test_entete_sans_vide(nav, url)
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
