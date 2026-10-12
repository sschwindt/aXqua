"""The documentation outline is a contract, so it is pinned here.

The outline of sections, subsections and subsubsections was prescribed
(``docs/restructuring-instructions.md``), and two other things are built on it: the
navigation menu, which lists the sections and opens their subsections on demand, and the QGIS plugin,
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


def test_the_uml_diagrams_show_the_code_as_it_is():
    """The diagrams of the code documentation are generated from the sources. One that
    was not generated again after a class or a dependency changed describes a program
    that no longer exists. Graphviz is not needed for this comparison."""
    import subprocess
    import sys

    script = DOCS.parent / "scripts" / "build_uml.py"
    done = subprocess.run([sys.executable, str(script), "--check"],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    for name in ("uml-packages", "uml-case", "uml-backends", "uml-jobs", "uml-computer"):
        assert f"img/{name}.svg" in "".join(
            path.read_text(encoding="utf-8") for path in (DOCS / "code").glob("*.rst"))


#: The pages of the workflow, each with the pictures of the plugin it shows. A page
#: says where in the plugin its subject is set up, and the picture shows the place.
SCREENSHOTS = {
    "installation/qgis-plugin": ["gui-qgis-window"],
    "installation/simulation-software": ["gui-install-wizard"],
    "installation/plugin-setup": ["gui-configuration", "gui-profile-editor"],
    "usage/case-setup": ["gui-case-setup", "gui-case-editor"],
    "usage/preprocessing": ["gui-preprocessing"],
    "usage/hydraulic-simulations": ["gui-hydraulics-telemac", "gui-hydraulics-openfoam"],
    "usage/mesh-convergence": ["gui-mesh-convergence"],
    "usage/morphodynamic-simulations": ["gui-case-editor-morphodynamics"],
    "usage/calibration-validation": ["gui-calibration"],
    "usage/postprocessing": ["gui-postprocessing-qgis", "gui-map-streamlines",
                             "gui-postprocessing-paraview"],
    "usage/batch-processing": ["gui-batch"],
}


def test_every_step_of_the_workflow_shows_where_it_is_set_up_in_the_plugin():
    """The pictures are taken by ``scripts/build_screenshots.py`` and numbered; the
    caption of each explains its numbers. A picture nobody shows, a page that lost its
    picture, and a caption that explains another number of marks than its picture has
    (the script records them in ``gui-screenshots.json``) are noticed here."""
    import json

    marks = json.loads((DOCS / "img" / "gui-screenshots.json").read_text(encoding="utf-8"))
    shown = []
    for document, names in SCREENSHOTS.items():
        text = (DOCS / f"{document}.rst").read_text(encoding="utf-8")
        found = re.findall(r"^\.\. figure:: \.\./img/(gui-[a-z0-9-]+)\.png$", text,
                           flags=re.MULTILINE)
        assert found == names, document
        for name in names:
            assert (DOCS / "img" / f"{name}.png").is_file(), name
            block = text.split(f".. figure:: ../img/{name}.png", 1)[1].split("\n\n", 2)
            options, caption = block[0], block[1]
            assert ":alt:" in options and caption.startswith("   "), name
            numbers = re.findall(r"\((\d)\)", caption)
            # a caption counts its marks from 1 without a gap, as many as the picture has
            assert numbers == [str(n) for n in range(1, len(numbers) + 1)], name
            assert len(numbers) == marks[name], name
        shown += names
    on_disk = sorted(path.stem for path in (DOCS / "img").glob("gui-*.png"))
    assert on_disk == sorted(shown) == sorted(marks)


def test_the_sections_stand_out_in_the_menu():
    """The sections of the outline (Purpose, Installation & Configuration, ...) are set
    in bold capital letters in the navigation menu."""
    css = (DOCS / "_static" / "axqua.css").read_text(encoding="utf-8")
    rule = css.split(".wy-menu-vertical li.toctree-l1 > a,", 1)[1].split("}", 1)[0]
    assert "font-weight: 700" in rule and "text-transform: uppercase" in rule
    assert "li.toctree-l1.current > a" in rule


def test_the_subsections_are_folded_until_their_section_is_opened():
    """The menu shows the sections, and a section opens with its fold button or when one of its pages is shown.

    The theme folds the menu in this way by itself. The stylesheet therefore must not set the display of any menu level, which is how the second level was once forced open and the fold button of a section hidden, and the whole tree has to be in every page, because a section without its subsections in the page gets no fold button.
    """
    css = (DOCS / "_static" / "axqua.css").read_text(encoding="utf-8")
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL))
    assert any("toctree-l1" in selector for selector, _ in rules)
    for selector, body in rules:
        if "toctree-l" in selector:
            assert "display" not in body, " ".join(selector.split())
    conf = (DOCS / "conf.py").read_text(encoding="utf-8")
    assert '"collapse_navigation": False' in conf


def test_no_page_contains_an_em_dash():
    """One of the writing rules of this documentation, and the one a machine can check."""
    for path in DOCS.rglob("*.rst"):
        if "_build" in path.parts:
            continue
        assert "—" not in path.read_text(encoding="utf-8"), path
