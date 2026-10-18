"""Help: the documentation, opened at the section of the active tab.

*Help* opens a copy of the documentation built on this computer when there is one (the user's decision of 2026-10-18), because it describes the installed version of aXqua, and the published documentation at ``https://axqua.eu`` otherwise (the decision of 2026-10-16 had been the published one alone, then at ``axqua.readthedocs.io``). Where the copy is, and whether the default browser may read it, is the answer of ``axqua docs`` (a browser in a snap or Flatpak sandbox reads no file outside the home folder). The panel asks it once per session, in the background and with the axqua program of the plugin settings, as soon as that program has answered (:func:`ask`); until the answer is there, *Help* opens the published documentation.

The language follows QGIS: German and French pages where QGIS is set to German or French and the documentation has them (the published one has both, at ``/de/`` and ``/fr/``).

**A local page is opened through a small page that forwards to it.** On Linux, ``xdg-open`` drops the part after ``#`` of a ``file://`` address before the browser sees it, and Chromium and Chrome do so on Windows as well, so the page opened at its top instead of at the section of the tab. The forwarding page has no ``#`` in its own address and carries the whole address inside. It is written beside the documentation (``_help/open.html``), where a browser that may read the documentation may read it too; ``/tmp`` would be out of reach of a snap browser.
"""

from __future__ import annotations

import html
import json
import tempfile
from pathlib import Path

from . import sections

#: The published documentation, in English; German and French are in /de/ and /fr/.
PUBLISHED = "https://axqua.eu/"
TRANSLATED = ("de", "fr")

#: What ``axqua docs`` answered: None until the first Help of a session asks.
_location: dict | None = None
#: Counts the calls of :func:`configure`; an answer to a question asked before the last one is dropped.
_generation = 0
#: (generation, task) of the question on its way, None when none is.
_asking: tuple | None = None


def configure(location: dict | None) -> None:
    """Set where the documentation is (``axqua docs --json``); None asks again next time. An answer still on its way from before is dropped."""
    global _location, _generation, _asking
    _location = location
    _generation += 1
    _asking = None


def location() -> dict:
    """``{local, languages, readable, published, ...}``; an empty dict until :func:`ask` has an answer, or when aXqua could not give one. Runs nothing: *Help* is clicked on the thread of the window."""
    return _location or {}


def ask(client) -> None:
    """Ask ``axqua docs`` where the documentation is, in the background, unless the answer is known.

    *client* is the client of the panel, which runs the axqua program of the plugin settings; a client of its own would look on ``PATH`` only. A failed answer counts as "no local copy" for the rest of the session, until :func:`configure` forgets it (the panel does so when the settings change).
    """
    global _asking
    from ..core import tasks

    if _location is not None:
        return
    if _asking is not None and _asking[0] == _generation and _asking[1] in tasks._IN_FLIGHT:
        return                  # a question for the same program is on its way
    mine = _generation

    def answered(answer) -> None:
        # the program may have changed while the question was on its way (open_settings forgets the answer): then the answer is of the old program
        if mine == _generation:
            configure(answer or {})

    # No owner: the answer touches nothing but this module, and a task whose owner was closed would drop it. A question that ends without an answer (cancelled in the task bar) leaves the in-flight list, so the next ask asks again.
    task = tasks.run_async("aXqua: looking for the documentation", client.docs,
                           on_success=answered, on_error=lambda _exc: answered({}))
    if mine == _generation and _location is None:
        _asking = (mine, task)


def language() -> str:
    """The language of QGIS, as the documentation names it: ``en``, ``de`` or ``fr``."""
    try:
        from qgis.core import QgsApplication

        name = (QgsApplication.locale() or "").lower()
    except Exception:  # noqa: BLE001
        return "en"
    return next((lang for lang in TRANSLATED if name.startswith(lang)), "en")


def base(lang: str | None = None) -> str:
    """The address of the start of the documentation, ending in a slash."""
    lang = lang or language()
    where = location()
    if where.get("local") and where.get("readable"):
        offered = where.get("languages") or ["en"]
        folder = Path(where["local"])
        if lang in TRANSLATED and lang in offered:
            folder = folder / lang
        return folder.as_uri() + "/"
    return PUBLISHED + (f"{lang}/" if lang in TRANSLATED else "")


def key_for(section_key: str, sub_key: str = "") -> str:
    return f"{section_key}-{sub_key}" if sub_key else section_key


def url_for(section_key: str, sub_key: str = "") -> str:
    """The address Help opens for a tab, or for one of its sub-tabs."""
    page, label = sections.help_target(section_key, sub_key)
    return f"{base()}{page}.html#{label}"


def url_for_window(key: str) -> str:
    """The address Help opens from a window that belongs to no tab (a wizard)."""
    page, label = sections.WINDOWS[key]
    return f"{base()}{page}.html#{label}"


def url_for_code(code: str, severity: str = "warning") -> str:
    """Where the documentation explains a warning or an error with this code."""
    page = "troubleshooting/errors" if severity == "error" else "troubleshooting/warnings"
    anchor = code.replace(".", "-").replace("_", "-")
    return f"{base()}{page}.html#{anchor}"


def forwarding_page(url: str) -> Path:
    """Write the page that forwards to a local *url* with a ``#``, and return its path."""
    root = location().get("local")
    page = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>aXqua documentation</title>
<meta http-equiv="refresh" content="0; url={attr}">
<script>location.replace({js});</script></head>
<body><p><a href="{attr}">aXqua documentation</a></p></body></html>
""".format(attr=html.escape(url, quote=True), js=json.dumps(url))
    for folder in ([Path(root) / "_help"] if root else []) + [Path(tempfile.gettempdir())]:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "open.html"
            target.write_text(page, encoding="utf-8")
            return target
        except OSError:
            continue
    raise OSError("no folder to write the forwarding page into")


def open_url(url: str) -> bool:
    """Hand an address to the desktop. Returns whether the desktop took it."""
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices

    if url.startswith("file:") and "#" in url:
        try:
            return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(forwarding_page(url)))))
        except OSError:
            url = url.split("#", 1)[0]
    return bool(QDesktopServices.openUrl(QUrl(url)))


def open_help(section_key: str, sub_key: str = "") -> str:
    """Open the documentation for a tab and return the address that was opened."""
    url = url_for(section_key, sub_key)
    open_url(url)
    return url


def open_window_help(key: str) -> str:
    """Open the documentation for a window and return the address that was opened."""
    url = url_for_window(key)
    open_url(url)
    return url
