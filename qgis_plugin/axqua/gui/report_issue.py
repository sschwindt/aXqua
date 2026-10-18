"""*Report issue*: a problem that the checks and the fixes did not solve.

The report is written out where the user can read and change it before anything leaves
the computer, and the user's home folder is replaced by ``~`` in it. Two ways to send
it: as an issue on GitHub, which needs a free GitHub account, or as an e-mail to the
contact address of the plugin, which needs none. Both addresses are read from
``metadata.txt`` (``tracker`` and ``email``), so that they are kept in one place.

The functions above the dialog are pure, so that the text of a report is tested where
QGIS is not installed.
"""

from __future__ import annotations

import configparser
import os
import platform
from pathlib import Path
from urllib.parse import quote, urlencode

METADATA = Path(__file__).resolve().parent.parent / "metadata.txt"

#: Longest body sent in an address. Browsers and mail programs cut longer addresses,
#: and GitHub refuses them; the full text stays in the window to be copied.
URL_BODY_LIMIT = 6000
MAIL_BODY_LIMIT = 1800


def metadata(path: Path = METADATA) -> dict[str, str]:
    """``tracker``, ``email`` and ``version`` of the plugin, read as QGIS reads them."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
        general = parser["general"]
    except (OSError, KeyError, configparser.Error):
        return {"tracker": "", "email": "", "version": ""}
    return {key: str(general.get(key, "")).strip()
            for key in ("tracker", "email", "version")}


def anonymize(text: str, home: str | None = None) -> str:
    """*text* with the home folder of the user written as ``~``."""
    home = home if home is not None else os.path.expanduser("~")
    return text.replace(home, "~") if home and home != "/" else text


def installation_report(target: str, plan: dict | None = None,
                        status: dict | None = None, *, rounds: int = 0) -> tuple[str, str]:
    """``(title, body)`` of a report on an installation that did not work out."""
    info = metadata()
    plan = plan or {}
    status = status or {}
    host = (plan.get("host") or {}).get("description") or platform.platform()
    lines = [f"**What:** installation of {target} from the aXqua plugin",
             f"**Plugin version:** {info.get('version') or 'unknown'}",
             f"**Computer:** {host}", ""]
    packages = plan.get("packages") or {}
    if packages.get("missing"):
        lines += [f"**System packages still missing after {rounds} round(s) of fixes:**",
                  " ".join(packages["missing"]), "",
                  "**Command that was run:**", "```", str(packages.get("command") or ""),
                  "```", ""]
    findings = plan.get("findings") or []
    if findings:
        lines.append("**What the check reports:**")
        lines += [f"- {f.get('severity', '')} `{f.get('code', '')}`: {f.get('message', '')}"
                  for f in findings]
        lines.append("")
    if status:
        lines += [f"**Installation:** {status.get('state', '')}"
                  + (f": {status.get('message')}" if status.get("message") else ""), ""]
        tail = str(status.get("log_tail") or "").strip().splitlines()[-40:]
        if tail:
            lines += ["**End of the installation log:**", "```", *tail, "```", ""]
    lines.append("<!-- Please add what you did before the problem appeared. -->")
    title = f"Installation of {target} does not work out"
    return title, anonymize("\n".join(lines))


def issue_url(title: str, body: str, tracker: str) -> str:
    """The address of a new GitHub issue with *title* and *body* filled in."""
    base = tracker.rstrip("/")
    if not base:
        return ""
    if len(body) > URL_BODY_LIMIT:
        body = body[:URL_BODY_LIMIT] + "\n\n(cut off; the rest is in the clipboard)"
    return f"{base}/new?" + urlencode({"title": title, "body": body})


def mail_url(title: str, body: str, address: str) -> str:
    """A ``mailto:`` address with *title* and *body* filled in."""
    if not address:
        return ""
    if len(body) > MAIL_BODY_LIMIT:
        body = body[:MAIL_BODY_LIMIT] + "\n\n(cut off; paste the rest from the clipboard)"
    return f"mailto:{address}?subject={quote(title)}&body={quote(body)}"


def report_dialog(title: str, body: str, parent=None):
    """A window with the report and the ways to send it."""
    from qgis.PyQt.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                                     QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout)

    from . import help as help_pages

    info = metadata()
    dialog = QDialog(parent)
    dialog.setWindowTitle("Report issue")
    dialog.resize(700, 560)
    layout = QVBoxLayout(dialog)
    note = QLabel(
        "Read the report and add what you did before the problem appeared. Your home "
        "folder is written as ~. Nothing is sent before you click one of the buttons "
        "below.\n\nOn GitHub: needs a free GitHub account.\nBy e-mail: needs no "
        "account; your mail program opens with the report.")
    note.setWordWrap(True)
    layout.addWidget(note)
    title_edit = QLineEdit(title)
    layout.addWidget(title_edit)
    text = QPlainTextEdit(body)
    layout.addWidget(text, 1)
    status = QLabel("")
    status.setWordWrap(True)
    layout.addWidget(status)
    row = QHBoxLayout()

    def current() -> tuple[str, str]:
        return title_edit.text().strip(), text.toPlainText()

    def copy() -> None:
        subject, content = current()
        QApplication.clipboard().setText(f"{subject}\n\n{content}")
        status.setText("The report is in the clipboard.")

    def github() -> None:
        copy()
        url = issue_url(*current(), info.get("tracker", ""))
        if url and help_pages.open_url(url):
            status.setText("The browser opens a new issue on GitHub. Sign in or create "
                           "a free account there, then click Submit.")

    def mail() -> None:
        copy()
        url = mail_url(*current(), info.get("email", ""))
        if url and help_pages.open_url(url):
            status.setText("Your mail program opens with the report. Send it from there.")
        else:
            status.setText("No mail program answered. The report is in the clipboard; "
                           f"send it to {info.get('email', '')}.")

    for label, slot, enabled in (("Open on GitHub", github, bool(info.get("tracker"))),
                                 ("Send by e-mail", mail, bool(info.get("email"))),
                                 ("Copy report", copy, True)):
        button = QPushButton(label)
        button.setEnabled(enabled)
        button.clicked.connect(slot)
        row.addWidget(button)
    row.addStretch(1)
    close = QPushButton("Close")
    close.clicked.connect(dialog.accept)
    row.addWidget(close)
    layout.addLayout(row)
    dialog.report_text = text
    dialog.report_title = title_edit
    return dialog
