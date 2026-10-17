"""Help: the online documentation, opened at the section of the active tab.

*Help* opens the published documentation (the user's decision of 2026-10-16), at the
page and the label of the tab that is showing. The documentation lives at
``axqua.readthedocs.io``, which forwards to ``axqua.eu`` once that address is the
documentation's own. A copy of the documentation built into the plugin archive, which
earlier versions opened instead, is no longer made.
"""

from __future__ import annotations

from . import sections

#: The published documentation.
PUBLISHED = "https://axqua.readthedocs.io/en/latest/"


def key_for(section_key: str, sub_key: str = "") -> str:
    return f"{section_key}-{sub_key}" if sub_key else section_key


def url_for(section_key: str, sub_key: str = "") -> str:
    """The address Help opens for a tab, or for one of its sub-tabs."""
    page, label = sections.help_target(section_key, sub_key)
    return f"{PUBLISHED}{page}.html#{label}"


def url_for_window(key: str) -> str:
    """The address Help opens from a window that belongs to no tab (a wizard)."""
    page, label = sections.WINDOWS[key]
    return f"{PUBLISHED}{page}.html#{label}"


def url_for_code(code: str, severity: str = "warning") -> str:
    """Where the documentation explains a warning or an error with this code."""
    page = "troubleshooting/errors" if severity == "error" else "troubleshooting/warnings"
    anchor = code.replace(".", "-").replace("_", "-")
    return f"{PUBLISHED}{page}.html#{anchor}"


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
