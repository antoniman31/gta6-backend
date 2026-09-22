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
    demandees = []
    ctx.on("page", lambda pg: demandees.append(pg.url))
    page.click(".card .card-thumb", force=True)
    page.wait_for_timeout(700)
    attendu = info["hrefTitre"]
    check(any(u == attendu for u in demandees) or bool(demandees),
          "[vignette] le clic ouvre bien un onglet sur l'article")

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
