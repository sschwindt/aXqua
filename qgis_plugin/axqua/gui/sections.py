"""The fixed tabs of the panel, as data.

The panel used to grow one tab per capability that ``axqua case-status`` reported, so its
layout changed with the case and matched nothing in the documentation. It now has fixed
tabs in the order of the workflow, and each tab and sub-tab names the page and the label
its *Help* opens.

Since the user's corrections of 2026-10-16 the simulations are one tab, *Simulations*,
with the sub-tabs *Baseline (Telemac)*, *OpenFOAM*, *Mesh convergence* and
*Morphodynamics*. The last two offer a choice of the simulation program
(``SubSection.choose_solver``): the mesh convergence study and the morphodynamic
simulation each exist for TELEMAC and for OpenFOAM.

What a case can do still comes from the capability matrix: this table only says **where**
a capability is shown. A capability nobody has placed here is shown under *Simulations*,
in the sub-tab of its solver, so that a capability added to aXqua appears without a
plugin release, as before.

Pure Python, no Qt: the table is tested where QGIS is not installed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubSection:
    """A sub-tab: one solver, one program, or one study within a section."""

    key: str
    title: str
    label: str                                    # the docs label Help opens
    capabilities: tuple[tuple[str, str], ...] = ()   # (solver, capability)
    page: str = ""                                # docs page, where not the section's
    #: The sub-tab shows the capabilities of one solver at a time, chosen on it.
    choose_solver: bool = False


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
    Section("hydraulics", "Simulations", "usage/hydraulic-simulations",
            "help-hydraulics", subsections=(
                SubSection("telemac", "Baseline (Telemac)", "help-hydraulics-telemac", (
                    ("telemac", "steady2d"), ("telemac", "steady3d"),
                    ("telemac", "unsteady2d"), ("telemac", "unsteady3d"),
                    ("telemac", "gain_lose"))),
                SubSection("openfoam", "OpenFOAM", "help-hydraulics-openfoam", (
                    ("openfoam", "free_surface_3d"),)),
                SubSection("mesh", "Mesh convergence", "help-mesh-convergence", (
                    ("telemac", "mesh_convergence"), ("telemac", "vertical_convergence"),
                    ("openfoam", "mesh_convergence")),
                    page="usage/mesh-convergence", choose_solver=True),
                SubSection("morphodynamics", "Morphodynamics", "help-morphodynamics", (
                    ("telemac", "morphodynamics"), ("openfoam", "morphodynamics")),
                    page="usage/morphodynamic-simulations", choose_solver=True),
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

#: The simulation programs a sub-tab with ``choose_solver`` offers, in this order.
SOLVERS = (("telemac", "TELEMAC"), ("openfoam", "OpenFOAM"))

#: What Help opens from a window that belongs to no tab: ``key -> (page, label)``.
WINDOWS: dict[str, tuple[str, str]] = {
    "install-telemac": ("installation/simulation-software", "install-telemac"),
    "install-openfoam": ("installation/simulation-software", "install-openfoam"),
    "install-postprocessors": ("installation/postprocessors", "help-postprocessors"),
    "example-case": ("usage/case-setup", "help-example-case"),
}

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
    if sub is None:
        return item.page, item.label
    return (sub.page or item.page), sub.label


def help_keys() -> list[tuple[str, str, str]]:
    """Every ``(key, page, label)`` Help can open: ``hydraulics``,
    ``hydraulics-telemac``, and the windows of :data:`WINDOWS`."""
    out = []
    for item in SECTIONS:
        out.append((item.key, item.page, item.label))
        for sub in item.subsections:
            out.append((f"{item.key}-{sub.key}", sub.page or item.page, sub.label))
    out += [(key, page, label) for key, (page, label) in WINDOWS.items()]
    return out
