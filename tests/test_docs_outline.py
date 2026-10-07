"""The documentation outline is a contract, so it is pinned here.

The outline of sections, subsections and subsubsections was prescribed
(``docs/restructuring-instructions.md``), and two other things are built on it: the
navigation menu, which lists sections and subsections at all times, and the QGIS plugin,
whose tabs open the documentation at the ``help-`` labels. A heading that is renamed or
moved in passing would break both without any build error, so the structure is read back
from the sources here - plain text only, no Sphinx, so the test runs wherever the suite
does.

Change the outline and this file together.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"

#: section title -> (index document, [(subsection title, document, subsubsections)]).
#: ``None`` for the subsubsections leaves the headings of that page free.
OUTLINE: list[tuple[str, str, list[tuple[str, str, list[str] | None]]]] = [
    ("Installation & Configuration", "installation/index", [
        ("QGIS plugin", "qgis-plugin", None),
        ("Simulation Software", "simulation-software", ["TELEMAC", "OpenFOAM"]),
        ("Plugin setup", "plugin-setup",
         ["Plugin profile", "Define solver bindings", "Job execution"]),
        ("Postprocessors", "postprocessors", ["VisIt", "ParaView"]),
    ]),
    ("Usage", "usage/index", [
        ("aXqua case setup", "case-setup",
         ["Project paths", "Geodata", "Boundaries", "Initialization",
          "Mesh configuration", "Wall roughness", "Hydraulic simulation",
          "Morphodynamic simulation"]),
        ("Preprocessing", "preprocessing",
         ["aXqua workflow", "Meshing", "Pre-processing checkup", "TELEMAC choices",
          "OpenFOAM choices"]),
        ("Hydraulic simulations", "hydraulic-simulations",
         ["aXqua warm-up concept", "TELEMAC dry runs 2D/3D", "TELEMAC hotstarts",
          "OpenFOAM"]),
        ("Mesh convergence", "mesh-convergence",
         ["Mesh study setup", "Run mesh convergence study", "Study report"]),
        ("Morphodynamic simulations", "morphodynamic-simulations",
         ["Concept", "TELEMAC-GAIA Setup", "OpenFOAM Setup"]),
        ("Calibration & validation", "calibration-validation",
         ["Introduction to Bayesian calibration", "Ground truth data setup",
          "Bayesian calibration & validation setup", "Run HydroBayesCal",
          "Quality analysis"]),
        ("Postprocessing", "postprocessing", ["QGIS map generation", "ParaView", "VisIt"]),
        ("Batch-processing (visual automation)", "batch-processing", None),
    ]),
    ("Automation & Terminal Usage", "automation/index", [
        ("Terminal syntax", "terminal-syntax", None),
        ("Batch-processing (headless)", "batch-headless", None),
    ]),
    ("Code documentation", "code/index", [
        ("General structure & UML", "structure-uml", None),
    ]),
    ("Troubleshooting", "troubleshooting/index", [
        ("General tips & logfiles", "tips-logfiles", None),
        ("Warning messages", "warnings", None),
        ("Error messages", "errors", None),
    ]),
]

#: The targets the plugin opens from its tabs and sub-tabs.
HELP_LABELS = [
    "help-configuration", "help-postprocessors", "help-case-setup", "help-preprocessing",
    "help-hydraulics", "help-hydraulics-telemac", "help-hydraulics-openfoam",
    "help-mesh-convergence", "help-morphodynamics", "help-morphodynamics-telemac",
    "help-morphodynamics-openfoam", "help-calibration", "help-postprocessing",
    "help-postprocessing-qgis", "help-postprocessing-paraview",
    "help-postprocessing-visit", "help-batch",
]


def _headings(document: str, underline: str) -> list[str]:
    """Headings of *document* that are underlined with *underline* characters."""
    lines = (DOCS / f"{document}.rst").read_text(encoding="utf-8").splitlines()
    found = []
    for title, rule in zip(lines, lines[1:]):
        if title.strip() and rule and set(rule) == {underline} and len(rule) >= len(title):
            found.append(title.strip())
    return found


def _title(document: str) -> str:
    return _headings(document, "=")[0]


def _toctree(document: str) -> list[str]:
    """Entries of the toctrees of *document*, as written."""
    lines = (DOCS / f"{document}.rst").read_text(encoding="utf-8").splitlines()
    entries, inside = [], False
    for line in lines:
        if line.startswith(".. toctree::"):
            inside = True
        elif inside and line.strip() and not line.startswith(" "):
            inside = False
        elif inside and line.strip() and not line.strip().startswith(":"):
            entries.append(line.strip())
    return entries


def test_the_sections_are_the_prescribed_ones_in_order():
    expected = ["Purpose <self>"] + [index for _, index, _ in OUTLINE] + ["license"]
    assert _toctree("index") == expected
    assert _title("license") == "License and disclaimer"
    assert "Purpose" in _headings("index", "-")


@pytest.mark.parametrize("title, index, subsections", OUTLINE, ids=[o[0] for o in OUTLINE])
def test_a_section_lists_its_subsections_in_order(title, index, subsections):
    assert _title(index) == title
    folder = index.rsplit("/", 1)[0]
    entries = _toctree(index)
    documents = [document for _, document, _ in subsections]
    # Code documentation continues with one page per package after the pinned first one.
    assert entries[:len(documents)] == documents
    if title != "Code documentation":
        assert entries == documents
    for subsection, document, _ in subsections:
        assert _title(f"{folder}/{document}") == subsection


_PINNED = [(f"{index.rsplit('/', 1)[0]}/{document}", subsubsections)
           for _, index, subsections in OUTLINE
           for _, document, subsubsections in subsections if subsubsections is not None]


@pytest.mark.parametrize("document, subsubsections", _PINNED, ids=[p[0] for p in _PINNED])
def test_a_subsection_has_its_subsubsections_in_order(document, subsubsections):
    assert _headings(document, "-") == subsubsections


def test_every_page_is_reachable_from_the_menu():
    """A page outside every toctree builds, and is then found by nobody."""
    listed = {"index", "license"}
    for _, index, _ in OUTLINE:
        folder = index.rsplit("/", 1)[0]
        listed.add(index)
        listed.update(f"{folder}/{entry}" for entry in _toctree(index))
    on_disk = {str(path.relative_to(DOCS).with_suffix("")) for path in DOCS.rglob("*.rst")
               if "_build" not in path.parts and "_incoming" not in path.parts}
    assert on_disk == listed


def test_the_help_targets_of_the_plugin_exist():
    text = "\n".join(path.read_text(encoding="utf-8") for path in DOCS.rglob("*.rst"))
    labels = set(re.findall(r"^\.\. _([a-z0-9-]+):\s*$", text, flags=re.MULTILINE))
    assert not [label for label in HELP_LABELS if label not in labels]


def test_no_page_contains_an_em_dash():
    """One of the writing rules of this documentation, and the one a machine can check."""
    for path in DOCS.rglob("*.rst"):
        if "_build" in path.parts:
            continue
        assert "—" not in path.read_text(encoding="utf-8"), path
