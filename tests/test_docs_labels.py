"""The labels of the plugin stay English in the German and French documentation.

The plugin is in English, so a page has to name a button or tab exactly as the reader sees it. The translation keeps text in single asterisks unchanged (``docs/i18n/translate.py``) and translates bold text with its sentence; a student on a German computer met "Konfiguration", "Setup Assistent..." and "ERFORDERLICHE BENUTZERAKTIONEN" for labels the plugin shows in English (2026-11-06). These tests read the sources only.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "qgis_plugin" / "axqua" / "gui"

#: Module-level names whose strings are labels the wizards and the Configuration tab show.
LABEL_NAMES = {
    "install_wizard.py": ("TABS", "ACTIONS_HEADING", "FIELDS"),
    "setup_wizard.py": ("TABS", "CHOICES", "SETUP_CHOICES"),
    "configuration_tab.py": ("SOFTWARE_TITLE", "SETUP_BUTTON", "INSTALL_VIEWERS"),
}
#: Labels named in the docs that are not module-level constants.
EXTRA_LABELS = ("Configuration", "Select existing config file", "Install ParaView and VisIt...")
#: Words that are a label of the plugin and also the name of a thing, which the pages may set in bold as a name.
NAMES = ("OpenFOAM v2406", "Postprocessors")

_BOLD = re.compile(r"(?<![*\w])\*\*([^*\n]+?)\*\*(?![*\w])")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\s][^*]*?(?<!\s))\*(?![*\w])")


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def plugin_labels() -> set[str]:
    labels = set(EXTRA_LABELS)
    for name, wanted in LABEL_NAMES.items():
        tree = ast.parse((GUI / name).read_text(encoding="utf-8"))
        for node in tree.body:
            targets = [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            if set(targets) & set(wanted):
                # short strings only: the hints of FIELDS are sentences, not labels
                labels.update(s.replace("&&", "&") for s in _strings(node.value)
                              if 1 < len(s) <= 60 and (" " in s or s[:1].isupper()))
    return {label.strip() for label in labels if label.strip() and label.strip() not in NAMES}


def _norm(text: str) -> str:
    return text.strip().rstrip(":.").strip()


def test_the_label_list_is_read_from_the_plugin():
    labels = plugin_labels()
    for expected in ("Settings", "Check", "Installation", "REQUIRED USER ACTIONS in TERMINAL:",
                     "Setup Wizard...", "Simulation & postprocessing software", "Installation folder"):
        assert expected in labels


def test_no_label_of_the_plugin_is_written_in_bold():
    labels = {_norm(label) for label in plugin_labels()}
    found = []
    for page in sorted((ROOT / "docs").rglob("*.rst")):
        if "_build" in page.parts:
            continue
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            found += [f"{page.relative_to(ROOT)}:{number}: **{m.group(1)}**"
                      for m in _BOLD.finditer(line)
                      # a bold run-in head ("**Settings.** The simulation ...") is a heading, not a label
                      if not m.group(1).endswith(".") and _norm(m.group(1)) in labels]
    assert not found, ("a label of the plugin in bold is translated on the German and French pages; "
                       "write it in single asterisks:\n" + "\n".join(found))


def _translate_module():
    spec = importlib.util.spec_from_file_location("docs_i18n_translate_labels",
                                                  ROOT / "docs" / "i18n" / "translate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("lang", ["de", "fr"])
def test_every_label_in_single_asterisks_comes_through_the_translation_unchanged(lang):
    tr = _translate_module()
    lost = []
    for catalog in sorted((ROOT / "docs" / "locale" / lang / "LC_MESSAGES").rglob("*.po")):
        for entry in tr.read_catalog(catalog):
            if not entry.msgstr:
                continue
            lost += [f"{catalog.name}: *{m.group(1)}*" for m in _ITALIC.finditer(entry.msgid)
                     if f"*{m.group(1)}*" not in entry.msgstr]
    assert not lost, "\n".join(lost[:20])
