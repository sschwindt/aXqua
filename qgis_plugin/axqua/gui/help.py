"""Help: the documentation, opened at the section of the active tab.

The documentation is built into the plugin archive (``scripts/build_plugin_zip.py``), so
*Help* works on a computer without a network connection and shows the documentation of
the installed version. A plugin folder that is linked from a source checkout has no
built documentation unless it was built once with ``build_plugin_zip.py --help-only``;
Help then opens the published documentation instead.

One page per tab key (``hydraulics-telemac.html``) redirects to the page and the anchor.
A ``file://`` address does not reliably keep its ``#anchor`` on the way through the
desktop into the browser, and a redirect page needs none.
"""

from __future__ import annotations

from pathlib import Path

from . import sections

#: Where the archive build puts the documentation, inside the plugin folder.
LOCAL = Path(__file__).resolve().parent.parent / "help"

#: The published documentation, for a plugin folder without a built copy.
PUBLISHED = "https://axqua.readthedocs.io/en/latest/"


def key_for(section_key: str, sub_key: str = "") -> str:
    return f"{section_key}-{sub_key}" if sub_key else section_key


def url_for(section_key: str, sub_key: str = "", *, local: Path | None = None) -> str:
    """The address Help opens for a tab, or for one of its sub-tabs."""
    local = LOCAL if local is None else local
    redirect = local / f"{key_for(section_key, sub_key)}.html"
    if redirect.is_file():
        return redirect.as_uri()
    page, label = sections.help_target(section_key, sub_key)
    return f"{PUBLISHED}{page}.html#{label}"


def url_for_window(key: str, *, local: Path | None = None) -> str:
    """The address Help opens from a window that belongs to no tab (a wizard)."""
    local = LOCAL if local is None else local
    redirect = local / f"{key}.html"
    if redirect.is_file():
        return redirect.as_uri()
    page, label = sections.WINDOWS[key]
    return f"{PUBLISHED}{page}.html#{label}"


def url_for_code(code: str, severity: str = "warning", *,
                 local: Path | None = None) -> str:
    """Where the documentation explains a warning or an error with this code."""
    local = LOCAL if local is None else local
    page = "troubleshooting/errors" if severity == "error" else "troubleshooting/warnings"
    anchor = code.replace(".", "-").replace("_", "-")
    built = local / "html" / f"{page}.html"
    if built.is_file():
        return f"{built.as_uri()}#{anchor}"
    return f"{PUBLISHED}{page}.html#{anchor}"


redirect_page = sections.redirect_page


def open_url(url: str) -> bool:
    """Hand an address to the desktop. Returns whether the desktop took it."""
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices

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
