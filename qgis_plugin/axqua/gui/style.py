"""The colors of the plugin, and how a required entry is marked.

**aXqua purple** is the dark purple of the icon toward its lower right corner. It marks
what the user has to do: every entry a minimal case cannot do without, the entries an
option makes necessary as soon as it is switched on, and the actions an installation
needs in a terminal. A path that was found is dark green, so that "found" and "to do"
are told apart at a glance.

Pure Python apart from the two helpers at the end, which take any Qt widget.
"""

from __future__ import annotations

#: The dark purple of the aXqua icon, toward its lower right corner.
AXQUA_PURPLE = "#2e014b"
#: A path or a program that was found on this computer.
FOUND_GREEN = "#1b5e20"
#: The background of a button whose work is done.
DONE_GREEN = "#5b7a5e"

REQUIRED_STYLE = f"color: {AXQUA_PURPLE}; font-weight: bold;"
FOUND_STYLE = f"color: {FOUND_GREEN};"
MISSING_STYLE = f"color: {AXQUA_PURPLE}; font-weight: bold;"
DONE_BUTTON_STYLE = (f"QPushButton {{ background-color: {DONE_GREEN}; color: white; "
                     "font-weight: bold; }")

#: Shown in the tooltip of an entry marked as required.
REQUIRED_TIP = "Required"


def required_html(text: str) -> str:
    """*text* in the style of a required entry, for a rich-text label."""
    return f'<span style="color:{AXQUA_PURPLE}; font-weight:bold;">{text}</span>'


def mark_required(widget, required: bool) -> None:
    """Set or clear the purple bold of a required entry on a label (or any widget)."""
    widget.setStyleSheet(REQUIRED_STYLE if required else "")
    tip = widget.toolTip() or ""
    prefix = REQUIRED_TIP + ". "
    if required and not tip.startswith(prefix):
        widget.setToolTip(prefix + tip if tip else REQUIRED_TIP)
    elif not required and tip.startswith(prefix):
        widget.setToolTip(tip[len(prefix):])
    elif not required and tip == REQUIRED_TIP:
        widget.setToolTip("")
