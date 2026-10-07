"""Findings: what a check reports instead of raising.

A build has to stop at the first thing it cannot work with, and ``Config.validate``
does. An *editor* must not: somebody filling in a case in the QGIS plugin has to be able
to save a half-finished file, close the window and come back, and be told everything
that is still wrong rather than the first item of it. So checks that serve an editor
return a list of :class:`Finding` and never raise.

A finding carries what a user interface needs to act on it without parsing prose:

``severity``
    ``warning`` (the step can proceed, the result may be affected) or ``error`` (the
    step cannot proceed).
``code``
    Stable and dotted, extending the categories of :mod:`axqua.core.errors`
    (``axqua.environment.script_missing``). It names the paragraph of the
    documentation that explains the message.
``subject``
    The dotted key of the setting concerned (``solvers.telemac.setup_script``), which
    is how a form puts a warning triangle on the right field.
``message``
    One sentence, for a person.
``remedy``
    The one thing to try, where there is one.

Standard library only, like the rest of what a status query touches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

WARNING = "warning"
ERROR = "error"

__all__ = ["ERROR", "WARNING", "Finding", "has_errors", "worst"]


@dataclass(frozen=True)
class Finding:
    """One thing a check found. See the module docstring for the fields."""

    severity: str
    code: str
    message: str
    subject: str = ""
    remedy: str = ""

    def __post_init__(self) -> None:
        if self.severity not in (WARNING, ERROR):
            raise ValueError(f"severity must be 'warning' or 'error', got {self.severity!r}")

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form, with the documentation anchor derived from the code."""
        out: dict[str, Any] = {"severity": self.severity, "code": self.code,
                               "message": self.message, "anchor": self.anchor}
        if self.subject:
            out["subject"] = self.subject
        if self.remedy:
            out["remedy"] = self.remedy
        return out

    @property
    def anchor(self) -> str:
        """The label of this code in the documentation (``axqua-config-missing-file``)."""
        return self.code.replace(".", "-").replace("_", "-")

    def line(self) -> str:
        """One line for a terminal or a log."""
        where = f" ({self.subject})" if self.subject else ""
        remedy = f" {self.remedy}" if self.remedy else ""
        return f"{self.severity.upper()} [{self.code}]{where}: {self.message}{remedy}"


def has_errors(findings: Iterable[Finding]) -> bool:
    return any(f.severity == ERROR for f in findings)


def worst(findings: Iterable[Finding]) -> str | None:
    """``"error"``, ``"warning"`` or ``None`` - what a tab title or a status icon shows."""
    severities = {f.severity for f in findings}
    if ERROR in severities:
        return ERROR
    return WARNING if WARNING in severities else None
