"""
Contrôle de synchronisation entre le backend Python et le mode de secours JS.

    python check_sources_sync.py

Pourquoi ce script plutôt qu'un fichier de configuration unique
---------------------------------------------------------------
La liste des sources est dupliquée entre FEEDS (fetch_feeds.py) et
DEFAULT_FEEDS (docs/index.html), tout comme les 139 mots-clés. Cette
duplication est assumée : le mode de secours a besoin des vraies URLs de
flux précisément quand le backend est injoignable — c'est-à-dire quand
feed.json, qui pourrait autrement centraliser cette liste, ne l'est pas non
plus. Un fichier chargé à l'exécution casserait donc la seule raison d'être
de ce mode.

Ce qui posait vraiment problème, ce n'est pas la duplication : c'est
qu'elle dérivait en silence. Fin août 2026, trois sources
(GTA 6 x Netflix, RockstarINTEL, GTA6 Times) étaient marquées "sans filtre"
côté JS alors que le backend leur appliquait le filtre normal — personne ne
l'avait vu. Ce script fait échouer la CI dès qu'un écart apparaît, en le
nommant précisément.

Le backend Python fait foi : c'est lui qui alimente le fichier réellement
servi, et c'est son comportement que décrit le README.
"""

import json
import re
import sys

HTML_PATH = "docs/index.html"

# Correspondance des champs : nom Python -> (nom JS, valeur par défaut)
FIELDS = {
    "official": ("official", False),
    "lang": ("lang", None),
    "rockstarmag": ("rockstarmag", False),
    "specialist_source": ("specialist", False),
    "no_filter_at_all": ("noFilter", False),
}


def extract_js_literal(html, start_marker):
    """Extrait un littéral tableau JS et le convertit en structure Python.

    Les littéraux d'objet JS ont des clés non quotées, donc illisibles par
    json.loads tel quel. On les quote au passage. La substitution ne cible
    que les clés (précédées de { ou , ) : une URL comme "https://x" est à
    l'intérieur d'une chaîne, jamais précédée de ces caractères.
    """
    idx = html.find(start_marker)
    if idx == -1:
        raise SystemExit(f"ERREUR : repère introuvable dans {HTML_PATH} : {start_marker!r}")

    start = html.index("[", idx)
    depth = 0
    for pos in range(start, len(html)):
        if html[pos] == "[":
            depth += 1
        elif html[pos] == "]":
            depth -= 1
            if depth == 0:
                raw = html[start:pos + 1]
                break
    else:
        raise SystemExit(f"ERREUR : tableau non refermé après {start_marker!r}")

    quoted = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', raw)
    quoted = re.sub(r",(\s*[\]}])", r"\1", quoted)  # virgules traînantes
    try:
        return json.loads(quoted)
    except json.JSONDecodeError as e:
        raise SystemExit(f"ERREUR : littéral JS illisible après {start_marker!r} : {e}")


def main():
    sys.path.insert(0, ".")
    import fetch_feeds

    html = open(HTML_PATH, encoding="utf-8").read()
    js_feeds = extract_js_literal(html, "const DEFAULT_FEEDS =")
    js_keywords = extract_js_literal(html, "  keywords: [")
    js_official_keywords = extract_js_literal(html, "const officialKeywords =")

    problems = []

    # --- sources : présence et ordre ---
    py_ids = [f["id"] for f in fetch_feeds.FEEDS]
    js_ids = [f["id"] for f in js_feeds]

    for missing in [i for i in py_ids if i not in js_ids]:
        problems.append(f"source « {missing} » présente dans FEEDS (Python) mais absente de DEFAULT_FEEDS (JS)")
    for extra in [i for i in js_ids if i not in py_ids]:
        problems.append(f"source « {extra} » présente dans DEFAULT_FEEDS (JS) mais absente de FEEDS (Python)")

    js_by_id = {f["id"]: f for f in js_feeds}
    for py_feed in fetch_feeds.FEEDS:
        js_feed = js_by_id.get(py_feed["id"])
        if not js_feed:
            continue
        where = f"source « {py_feed['id']} »"
        if py_feed["name"] != js_feed.get("name"):
            problems.append(f"{where} : nom différent — Python {py_feed['name']!r}, JS {js_feed.get('name')!r}")
        if py_feed["url"] != js_feed.get("url"):
            problems.append(f"{where} : URL différente\n      Python : {py_feed['url']}\n      JS     : {js_feed.get('url')}")
        for py_key, (js_key, default) in FIELDS.items():
            py_val = py_feed.get(py_key, default)
            js_val = js_feed.get(js_key, default)
            if bool(py_val) != bool(js_val) if isinstance(default, bool) else py_val != js_val:
                problems.append(f"{where} : {py_key}={py_val!r} (Python) mais {js_key}={js_val!r} (JS)")

    if py_ids != js_ids and not [p for p in problems if "absente" in p]:
        problems.append("les sources sont les mêmes des deux côtés mais pas dans le même ordre "
                        "(sans incidence fonctionnelle, mais ça rend la comparaison manuelle pénible)")

    # --- mots-clés ---
    if fetch_feeds.KEYWORDS != js_keywords:
        only_py = [k for k in fetch_feeds.KEYWORDS if k not in js_keywords]
        only_js = [k for k in js_keywords if k not in fetch_feeds.KEYWORDS]
        if only_py:
            problems.append(f"{len(only_py)} mot(s)-clé(s) seulement côté Python : {', '.join(only_py[:8])}"
                            + (" …" if len(only_py) > 8 else ""))
        if only_js:
            problems.append(f"{len(only_js)} mot(s)-clé(s) seulement côté JS : {', '.join(only_js[:8])}"
                            + (" …" if len(only_js) > 8 else ""))
        if not only_py and not only_js:
            problems.append("les mots-clés sont les mêmes des deux côtés mais pas dans le même ordre")

    if fetch_feeds.OFFICIAL_KEYWORDS != js_official_keywords:
        problems.append(f"mots-clés des sources officielles différents —\n"
                        f"      Python : {fetch_feeds.OFFICIAL_KEYWORDS}\n"
                        f"      JS     : {js_official_keywords}")

    # --- verdict ---
    print(f"Sources   : {len(py_ids)} côté Python, {len(js_ids)} côté JS")
    print(f"Mots-clés : {len(fetch_feeds.KEYWORDS)} côté Python, {len(js_keywords)} côté JS")

    if problems:
        print(f"\n{len(problems)} divergence(s) entre fetch_feeds.py et docs/index.html :\n")
        for p in problems:
            print(f"  - {p}")
        print("\nLe backend Python fait foi : aligne docs/index.html sur fetch_feeds.py.")
        return 1

    print("\nLes deux définitions sont synchronisées.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
