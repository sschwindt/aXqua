"""Warning triangles: what a check found, shown at the item it is about.

A finding is what ``axqua profile check`` and ``axqua check`` return: a severity, a
code, the item it concerns, a message and what to do about it. The panel shows each one
as a triangle, orange for a warning and dark red for an error. Clicking a triangle opens
the message with its remedy and a button into the documentation at that code.

Findings never disable anything. A check that prevented saving an incomplete profile
would stop the user from saving half-finished work, which is the normal state of a
profile while it is being filled in.
"""

from __future__ import annotations

from dataclasses import dataclass

from qgis.PyQt.QtCore import QPointF, QSize
from qgis.PyQt.QtGui import QIcon, QPainter, QPixmap, QPolygonF
from qgis.PyQt.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QToolButton,
                                 QVBoxLayout, QWidget)

from ..compat import color, enum_value, exec_dialog
from . import help as help_pages

ORANGE = "#e67e00"
DARK_RED = "#8b0000"
_ANTIALIAS = enum_value(QPainter, "RenderHint.Antialiasing", "Antialiasing")


@dataclass(frozen=True)
class Finding:
    """One result of a check, as the command line reports it."""

    severity: str = "warning"          # warning | error
    code: str = ""
    message: str = ""
    subject: str = ""
    remedy: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Finding":
        return cls(severity=str(data.get("severity") or "warning"),
                   code=str(data.get("code") or ""),
                   message=str(data.get("message") or ""),
                   subject=str(data.get("subject") or ""),
                   remedy=str(data.get("remedy") or ""))

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


def from_payload(payload) -> list[Finding]:
    """The findings out of a command's answer; tolerant of an answer without any."""
    items = (payload or {}).get("findings") if isinstance(payload, dict) else payload
    return [Finding.from_dict(item) for item in (items or []) if isinstance(item, dict)]


def worst(findings) -> str:
    """``error``, ``warning`` or '' for a list of findings."""
    findings = list(findings)
    if any(item.is_error for item in findings):
        return "error"
    return "warning" if findings else ""


def for_subject(findings, subject: str) -> list[Finding]:
    """The findings about one item. A finding about ``solvers.telemac.setup_script`` is
    also a finding about ``solvers.telemac``, so that a group can show it."""
    return [item for item in findings
            if item.subject == subject or item.subject.startswith(subject + ".")]


def triangle(severity: str, size: int = 16) -> QIcon:
    """A filled triangle with an exclamation mark, orange or dark red."""
    pixmap = QPixmap(size, size)
    pixmap.fill(color("#000000", alpha=0))
    painter = QPainter(pixmap)
    painter.setRenderHint(_ANTIALIAS)
    fill = color(DARK_RED if severity == "error" else ORANGE)
    painter.setBrush(fill)
    painter.setPen(fill)
    edge = size - 1.0
    painter.drawPolygon(QPolygonF([QPointF(edge / 2, 1.0), QPointF(edge, edge),
                                   QPointF(0.0, edge)]))
    painter.setPen(color("#ffffff"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(int(size * 0.62))
    painter.setFont(font)
    painter.drawText(pixmap.rect().adjusted(0, int(size * 0.2), 0, 0),
                     int(enum_value(_alignment(), "AlignmentFlag.AlignCenter",
                                    "AlignCenter")), "!")
    painter.end()
    return QIcon(pixmap)


def _alignment():
    from qgis.PyQt.QtCore import Qt
    return Qt


def describe(finding: Finding) -> str:
    """The text of the message window."""
    lines = [finding.message]
    if finding.subject:
        lines.append(f"\nConcerns: {finding.subject}")
    if finding.remedy:
        lines.append(f"\nWhat to do: {finding.remedy}")
    if finding.code:
        lines.append(f"\nCode: {finding.code}")
    return "\n".join(lines)


def show(finding: Finding, parent=None) -> None:
    """The message with its remedy, and a button into the documentation."""
    box = QMessageBox(parent)
    box.setWindowTitle("aXqua " + ("error" if finding.is_error else "warning"))
    box.setIconPixmap(triangle(finding.severity, 32).pixmap(QSize(32, 32)))
    box.setText(describe(finding))
    docs = box.addButton("Open documentation",
                         enum_value(QMessageBox, "ButtonRole.HelpRole", "HelpRole"))
    box.addButton(enum_value(QMessageBox, "StandardButton.Close", "Close"))
    exec_dialog(box)
    if box.clickedButton() is docs:
        help_pages.open_url(help_pages.url_for_code(finding.code, finding.severity))


class TriangleButton(QToolButton):
    """The triangle next to one item. Hidden while the item has no finding."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAutoRaise(True)
        self.findings: list[Finding] = []
        self.clicked.connect(self._open)
        self.setVisible(False)

    def set_findings(self, findings) -> None:
        self.findings = list(findings)
        level = worst(self.findings)
        self.setVisible(bool(level))
        if level:
            self.setIcon(triangle(level))
            self.setToolTip("\n".join(item.message for item in self.findings))

    def _open(self) -> None:
        for finding in self.findings:
            show(finding, self)


class FindingList(QWidget):
    """Every finding of a check, one row each: triangle, then the message."""

    def __init__(self, empty_text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.empty_text = empty_text
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.findings: list[Finding] = []
        self.set_findings([])

    def set_findings(self, findings, *, checked: bool = False) -> None:
        self.findings = list(findings)
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if not self.findings:
            text = "No warnings and no errors." if checked else self.empty_text
            label = QLabel(text)
            label.setWordWrap(True)
            self._layout.addWidget(label)
            return
        for finding in self.findings:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            button = TriangleButton()
            button.set_findings([finding])
            line.addWidget(button)
            text = QLabel(finding.message)
            text.setWordWrap(True)
            line.addWidget(text, 1)
            self._layout.addWidget(row)
