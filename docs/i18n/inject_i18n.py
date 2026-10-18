#!/usr/bin/env python3
"""Connect the English, German and French builds of the documentation into one site.

Runs over the assembled site (``docs/_build/site``: English at the top, German in ``de/``, French in
``fr/``) and adds to every page:

* ``hreflang`` links to the same page in the other languages, for search engines;
* the language links EN | DE | FR at the top right of the page, next to "View page source";
* ``_i18n/lang.js``, which remembers the language a reader chose and, on a first visit to an
  English page, opens the page in the language of the browser (see the comments in the script);
* on a German or French page, a note that the page is a machine translation, with a link to the
  English original.

It also writes ``404.html``, which GitHub Pages shows for an address that does not exist. Only the
build for GitHub Pages runs this script; Read the Docs serves its own builds and language menu.

Pages that carry the marker already are left alone, so the script can run twice.

Usage:  inject_i18n.py <site> [--site-url https://axqua.eu]
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
from pathlib import Path

I18N = Path(__file__).resolve().parent
ASSETS = I18N / "assets"
MARKER = "<!-- axqua-i18n -->"
LANGS = ("en", "de", "fr")
LABELS = {"en": "English", "de": "Deutsch", "fr": "Français"}

NOTE = {
    "de": ("Diese Seite wurde maschinell aus dem Englischen übersetzt und kann Fehler enthalten.",
           "Englisches Original", "Hinweis schließen"),
    "fr": ("Cette page a été traduite automatiquement de l’anglais et peut contenir des erreurs.",
           "Original en anglais", "Fermer la remarque"),
}

NOT_FOUND = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>aXqua: page not found</title>
<link rel="icon" href="/_static/favicon.ico">
<style>
  body {{ font-family: Lato, "Helvetica Neue", Arial, sans-serif; color: #404040; background: #fcfcfc;
         margin: 0; padding: 3rem 1rem; }}
  main {{ max-width: 40rem; margin: 0 auto; }}
  h1 {{ font-family: "Roboto Slab", Georgia, serif; font-weight: 700; }}
  a {{ color: #2980b9; }}
  section {{ margin-bottom: 2rem; }}
</style>
</head>
<body>
<main>
<img src="/_static/{logo}" alt="aXqua" width="96" height="96">
{sections}
</main>
<script>
  // show the reader's language first: the one of the address, else the one of the browser
  (function () {{
    var m = location.pathname.match(/^\\/(de|fr)(\\/|$)/);
    var lang = m ? m[1] : ((navigator.language || "en").slice(0, 2));
    var first = document.getElementById("nf-" + lang);
    if (first) first.parentNode.insertBefore(first, first.parentNode.querySelector("section"));
  }})();
</script>
</body>
</html>
"""

NOT_FOUND_TEXT = {
    "en": ("Page not found", "This page does not exist (anymore). The documentation starts at",
           "/"),
    "de": ("Seite nicht gefunden", "Diese Seite gibt es nicht (mehr). Die Dokumentation beginnt bei",
           "/de/"),
    "fr": ("Page introuvable", "Cette page n’existe pas (ou plus). La documentation commence à",
           "/fr/"),
}


def language_of(rel: Path) -> tuple[str, Path]:
    """``de/usage/x.html`` -> ``("de", usage/x.html)``; ``usage/x.html`` -> ``("en", ...)``."""
    if rel.parts and rel.parts[0] in LANGS[1:]:
        return rel.parts[0], Path(*rel.parts[1:])
    return "en", rel


def address(lang: str, page: Path) -> str:
    """The address of a page below the site, as GitHub Pages serves it."""
    path = page.as_posix()
    if path == "index.html":
        path = ""
    elif path.endswith("/index.html"):
        path = path[: -len("index.html")]
    return ("" if lang == "en" else f"{lang}/") + path


def head_block(lang: str, page: Path, root: str, site_url: str) -> str:
    links = [f'<link rel="alternate" hreflang="{other}" href="{site_url}/{address(other, page)}">'
             for other in LANGS]
    links.append(f'<link rel="alternate" hreflang="x-default" href="{site_url}/{address("en", page)}">')
    return (MARKER + "".join(links)
            + f'<link rel="stylesheet" href="{root}_i18n/lang.css">'
            + f'<script src="{root}_i18n/lang.js" data-lang="{lang}" data-root="{root}" '
            + f'data-page="{html.escape(page.as_posix())}"></script>')


def switcher(lang: str, page: Path, root: str) -> str:
    items = []
    for other in LANGS:
        label = other.upper()
        if other == lang:
            items.append(f'<strong title="{LABELS[other]}">{label}</strong>')
        else:
            href = root + ("" if other == "en" else f"{other}/") + page.as_posix()
            items.append(f'<a href="{html.escape(href)}" hreflang="{other}" lang="{other}" '
                         f'title="{LABELS[other]}" data-axqua-lang="{other}">{label}</a>')
    return '<span class="axqua-lang" role="navigation" aria-label="Language">' + " | ".join(items) + "</span>"


def note(lang: str, page: Path, root: str) -> str:
    text, original, close = NOTE[lang]
    href = html.escape(root + page.as_posix())
    return (f'<div class="axqua-mt-note" role="note"><button type="button" aria-label="{close}" '
            f'title="{close}">&times;</button>{text} <a href="{href}" hreflang="en" lang="en" '
            f'data-axqua-lang="en">{original}</a></div>')


def inject(path: Path, site: Path, site_url: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return False
    rel = path.relative_to(site)
    lang, page = language_of(rel)
    root = "../" * (len(rel.parts) - 1)
    text = re.sub(r"</head>", lambda m: head_block(lang, page, root, site_url) + m.group(0), text,
                  count=1, flags=re.IGNORECASE)
    text = re.sub(r'(<li class="wy-breadcrumbs-aside">)',
                  lambda m: m.group(1) + switcher(lang, page, root), text, count=1)
    if lang != "en":
        text = re.sub(r'(<div itemprop="articleBody">)',
                      lambda m: m.group(1) + note(lang, page, root), text, count=1)
    path.write_text(text, encoding="utf-8")
    return True


def not_found_page(site: Path) -> str:
    logo = next((p.name for p in sorted((site / "_static").glob("aXqua-icon*.png"))), "")
    sections = []
    for lang in LANGS:
        title, sentence, home = NOT_FOUND_TEXT[lang]
        sections.append(f'<section id="nf-{lang}" lang="{lang}"><h1>{title}</h1>'
                        f'<p>{sentence} <a href="{home}">axqua.eu{home}</a>.</p></section>')
    return NOT_FOUND.format(logo=logo, sections="\n".join(sections))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("site", type=Path, help="the assembled site (docs/_build/site)")
    parser.add_argument("--site-url", default="https://axqua.eu")
    args = parser.parse_args()
    site: Path = args.site.resolve()
    site_url = args.site_url.rstrip("/")
    for lang in LANGS[1:]:
        if not (site / lang / "index.html").exists():
            raise SystemExit(f"{site / lang / 'index.html'} is missing: build the {lang} pages first")

    target = site / "_i18n"
    target.mkdir(exist_ok=True)
    for asset in sorted(ASSETS.iterdir()):
        if asset.is_file():
            shutil.copy2(asset, target / asset.name)

    done = skipped = 0
    for path in sorted(site.rglob("*.html")):
        if path.relative_to(site).parts[0] in ("_i18n", "_static", "_sources", "_images"):
            continue
        if inject(path, site, site_url):
            done += 1
        else:
            skipped += 1
    (site / "404.html").write_text(not_found_page(site), encoding="utf-8")
    print(f"languages connected: {done} pages, {skipped} done already, 404.html written ({site})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
