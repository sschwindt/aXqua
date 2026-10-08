"""The fixed tabs of the panel, as data.

The panel used to grow one tab per capability that ``axqua case-status`` reported, so its
layout changed with the case and matched nothing in the documentation. It now has the
sections of the documentation, in the order of the workflow, and each section and
sub-section names the page and the label its *Help* opens.

What a case can do still comes from the capability matrix: this table only says **where**
a capability is shown. A capability nobody has placed here is shown under *Hydraulic
simulation*, in the sub-tab of its solver, so that a capability added to aXqua appears
without a plugin release, as before.

Pure Python, no Qt: the table is tested where QGIS is not installed, and the script that
builds the plugin archive reads it to write the help pages.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubSection:
    """A sub-tab: one solver or one program within a section."""

    key: str
    title: str
    label: str                                    # the docs label Help opens
    capabilities: tuple[tuple[str, str], ...] = ()   # (solver, capability)


@dataclass(frozen=True)
class Section:
    """A tab of the panel and the documentation page it belongs to."""

    key: str
    title: str
    page: str                                     # docs page, without suffix
    label: str
    subsections: tuple[SubSection, ...] = ()
    capabilities: tuple[tuple[str, str], ...] = ()

    def subsection(self, key: str) -> SubSection | None:
        return next((sub for sub in self.subsections if sub.key == key), None)


SECTIONS: tuple[Section, ...] = (
    Section("configuration", "Configuration", "installation/plugin-setup",
            "help-configuration"),
    Section("case", "Case Setup", "usage/case-setup", "help-case-setup"),
    Section("preprocessing", "Preprocessing", "usage/preprocessing", "help-preprocessing"),
    Section("hydraulics", "Hydraulic simulation", "usage/hydraulic-simulations",
            "help-hydraulics", subsections=(
                SubSection("telemac", "Telemac", "help-hydraulics-telemac", (
                    ("telemac", "steady2d"), ("telemac", "steady3d"),
                    ("telemac", "unsteady2d"), ("telemac", "unsteady3d"),
                    ("telemac", "gain_lose"))),
                SubSection("openfoam", "OpenFOAM", "help-hydraulics-openfoam", (
                    ("openfoam", "free_surface_3d"),)),
            )),
    Section("mesh", "Mesh convergence", "usage/mesh-convergence", "help-mesh-convergence",
            capabilities=(("telemac", "mesh_convergence"),
                          ("telemac", "vertical_convergence"),
                          ("openfoam", "mesh_convergence"))),
    Section("morphodynamics", "Morphodynamic simulation",
            "usage/morphodynamic-simulations", "help-morphodynamics", subsections=(
                SubSection("telemac", "Telemac", "help-morphodynamics-telemac", (
                    ("telemac", "morphodynamics"),)),
                SubSection("openfoam", "OpenFOAM", "help-morphodynamics-openfoam", (
                    ("openfoam", "morphodynamics"),)),
            )),
    Section("calibration", "Calibration & validation", "usage/calibration-validation",
            "help-calibration", capabilities=(("telemac", "calibration"),
                                              ("openfoam", "calibration"))),
    Section("postprocessing", "Postprocessing", "usage/postprocessing",
            "help-postprocessing", subsections=(
                SubSection("qgis", "QGIS", "help-postprocessing-qgis"),
                SubSection("paraview", "ParaView", "help-postprocessing-paraview"),
                SubSection("visit", "VisIt", "help-postprocessing-visit"),
            )),
    Section("batch", "Batch-processing", "usage/batch-processing", "help-batch"),
)

#: Where a capability nobody has placed is shown: with the simulations of its solver.
FALLBACK = "hydraulics"

#: The capability whose *Build* is the preprocessing of the case. It has a tab of its own.
PREPROCESSING_CAPABILITY = ("telemac", "steady2d")


def section(key: str) -> Section:
    """One section by key."""
    return next(item for item in SECTIONS if item.key == key)


def placement(solver: str, capability: str) -> tuple[str, str]:
    """``(section key, sub-section key)`` of a capability; the sub-section may be ''."""
    pair = (solver, capability)
    for item in SECTIONS:
        if pair in item.capabilities:
            return item.key, ""
        for sub in item.subsections:
            if pair in sub.capabilities:
                return item.key, sub.key
    fallback = section(FALLBACK)
    return fallback.key, solver if fallback.subsection(solver) else ""


def help_target(section_key: str, sub_key: str = "") -> tuple[str, str]:
    """``(page, label)`` that Help opens for a tab, or for one of its sub-tabs."""
    item = section(section_key)
    sub = item.subsection(sub_key) if sub_key else None
    return item.page, (sub.label if sub else item.label)


def redirect_page(page: str, label: str) -> str:
    """One redirect page: the built documentation is in ``html/`` beside it.

    A ``file://`` address does not reliably keep its ``#anchor`` on the way through the
    desktop into the browser. A page that sends the browser on needs none.
    """
    target = f"html/{page}.html#{label}"
    return ("<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">\n"
            f"<meta http-equiv=\"refresh\" content=\"0; url={target}\">\n"
            "<title>aXqua help</title></head>\n"
            f"<body><p><a href=\"{target}\">Open the aXqua documentation</a></p></body>"
            "</html>\n")


def help_keys() -> list[tuple[str, str, str]]:
    """Every ``(key, page, label)`` Help can open: ``hydraulics`` and
    ``hydraulics-telemac``. The archive build writes one redirect page per key."""
    out = []
    for item in SECTIONS:
        out.append((item.key, item.page, item.label))
        for sub in item.subsections:
            out.append((f"{item.key}-{sub.key}", item.page, sub.label))
    return out
