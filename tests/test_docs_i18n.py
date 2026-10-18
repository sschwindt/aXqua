"""The German and French documentation: docs/i18n/translate.py and docs/i18n/inject_i18n.py.

No translation engine is needed: the engine is replaced by functions that answer the way Argos Translate was seen to answer, including the ways it damages masked text.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

I18N = Path(__file__).resolve().parents[1] / "docs" / "i18n"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"docs_i18n_{name}", I18N / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tr = _load("translate")
inj = _load("inject_i18n")

EMPTY = tr.Glossary("en")


def _identity(prepared):
    return {piece: piece for piece in prepared.pieces()}


@pytest.mark.parametrize("source", [
    "Run ``axqua <case-file> --check`` before the build (:doc:`../usage/preprocessing`).",
    "See :ref:`the validation situation <validation-setup>` and :ref:`help-configuration`.",
    "aXqua uses `HydroBayesCal <https://hydrobayescal.readthedocs.io>`_ and `the installer repository <https://example.org/x>`__.",
    "Click *Open case settings...* on the *Case Setup* tab, then **Save the file.** and *Check*.",
    "The roughness :math:`k_s` of |project| is given in meters [#]_ after Nikuradse [Celik2008]_.",
    "**Verify the `git` installation**",
    "A suffix after a literal: ``geometry.slf``\\ s and https://axqua.eu/de/.",
    "VisIt 3.5.0 and the Visit DAV site, see `GitHub <https://github.com/sschwindt/aXqua/>`_.",
])
@pytest.mark.parametrize("bold_in_context", [True, False])
def test_masking_gives_every_kind_of_markup_back_unchanged(source, bold_in_context):
    prepared = tr.prepare(source, EMPTY, tr.load_keep(), bold_in_context=bold_in_context)
    assert tr.assemble(prepared, _identity(prepared), []) == source
    # nothing that is markup is left for the engine to translate
    for piece in prepared.pieces():
        assert "``" not in piece and ":doc:" not in piece and "https://" not in piece


def test_the_gui_labels_in_single_asterisks_stay_english():
    prepared = tr.prepare("Click *Load results* to add the layer.", EMPTY, [])
    assert "Load results" not in prepared.text


@pytest.mark.parametrize("damaged, numbers", [
    ("@ @ 2 @ @ mit", ["2"]),
    ("@1@@, @2@@ und @3@@", ["1", "2", "3"]),
    ("@@3@@ @@4@@", ["3", "4"]),
    ("@@0@@ 3.5.0", ["0"]),
    ("@@0@@@@ x", ["0"]),
])
def test_placeholders_the_engine_spaced_out_or_shortened_are_found(damaged, numbers):
    assert [m.group(1) for m in tr.SENTINEL_RE.finditer(damaged)] == numbers


def test_a_placeholder_that_lost_its_number_fails_the_message():
    prepared = tr.prepare("A job never stays in the state ``RUNNING`` and ``FAILED``.", EMPTY, [])
    lost = {prepared.text: "Ein Job bleibt nie im Zustand @@0@@ und @@."}
    dropped = {prepared.text: "Ein Job bleibt nie im Zustand @@0@@."}
    assert tr.assemble(prepared, lost, []) is None
    assert tr.assemble(prepared, dropped, []) is None


def test_markup_the_engine_glued_to_a_word_gets_its_space_back():
    prepared = tr.prepare("Open the file ``geometry.slf`` and the ``boundaries.cli`` file.", EMPTY, [])
    answer = {prepared.text: "Öffnen Sie die Datei@@0@@ und die@@1@@-Datei@@0@@s"}
    # the second sentinel 0 makes the answer invalid; one occurrence each is required
    assert tr.assemble(prepared, answer, []) is None
    answer = {prepared.text: "Öffnen Sie die Datei@@0@@s und die @@1@@@@"}
    assert tr.assemble(prepared, answer, []) == \
        "Öffnen Sie die Datei ``geometry.slf``\\ s und die ``boundaries.cli``"


def test_bold_markers_the_engine_spaced_out_are_tidied():
    prepared = tr.prepare("**Calibration** adjusts the parameters.", EMPTY, [])
    assert tr.assemble(prepared, {prepared.text: "** Die Kalibrierung ** passt die Parameter an."},
                       []) == "**Die Kalibrierung** passt die Parameter an."
    assert tr.assemble(prepared, {prepared.text: "Die Kalibrierung passt die Parameter an."}, []) is None


def _glossary(tmp_path, lang, rows):
    (tmp_path / f"glossary.{lang}.tsv").write_text("# test\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return tr.Glossary.load(lang, tmp_path)


def test_german_terms_go_into_the_sentence_in_singular_and_plural(tmp_path):
    glossary = _glossary(tmp_path, "de", [
        "reach|reaches\tFlussabschnitt|Flussabschnitte",
        "mesh convergence study\tNetzkonvergenzstudie",
        "mesh\tRechennetz",
        "DEM\tDGM",
        "layer|layers\tLayer|Layer",
    ])
    prepared = tr.prepare("A mesh convergence study refines the mesh of both reaches and the DEM; "
                          "the dem stays.", glossary, [])
    assert prepared.text.startswith("A Netzkonvergenzstudie refines the Rechennetz of both "
                                    "Flussabschnitte and the DGM")
    assert "dem stays" in prepared.text
    # a term that is the same in German is kept as it is, not sent; per form, since the German plural of Layer is Layer and the English one is layers
    kept = tr.prepare("Each layer is loaded.", glossary, [])
    assert kept.text == "Each @@0@@ is loaded."
    assert tr.assemble(kept, {kept.text: "Jeder @@0@@ wird geladen."}, []) == "Jeder Layer wird geladen."
    assert tr.prepare("Both layers are loaded.", glossary, []).text == "Both Layer are loaded."


def test_french_terms_go_in_title_case_and_come_back_in_their_own(tmp_path):
    glossary = _glossary(tmp_path, "fr", [
        "rating curve\tcourbe de tarage\tf",
        "water surface elevation\tcote de la surface libre\tf",
        "job|jobs\ttâche|tâches\tf",
    ])
    prepared = tr.prepare("The rating curve gives the water surface elevation of a job.", glossary, [])
    assert prepared.text == "The Courbe De Tarage gives the Cote De La Surface Libre of a Tâche."
    answer = {prepared.text: "La Courbe De Tarage donne la Côte De La Surface Libre d'un Tâche."}
    assert tr.assemble(prepared, answer, []) == \
        "La courbe de tarage donne la cote de la surface libre d'une tâche."


def test_french_articles_agree_with_the_gender_of_the_term():
    assert tr._french_articles("Lisez le journal de un tâche.", {"tâche": "f"}) == \
        "Lisez le journal de une tâche."
    assert tr._french_articles("la profil et de la cas", {"profil": "m", "cas": "m"}) == \
        "le profil et du cas"
    assert tr._french_articles("un entrée, le entrée, de la entrée", {"entrée": "f"}) == \
        "une entrée, l'entrée, de l'entrée"
    # an aspirated h does not elide, a mute one does
    assert tr._french_articles("Le hauteur d'eau et l' hydrogramme", {"hauteur d'eau": "f",
                                                                        "hydrogramme": "m"}) == \
        "La hauteur d'eau et l'hydrogramme"


def test_german_articles_of_a_masculine_noun_are_repaired_in_front_of_a_feminine_term():
    assert tr._german_articles("das Keyword des Steuerdatei in einem Rauheitszone",
                               {"Steuerdatei": "f", "Rauheitszone": "f"}) == \
        "das Keyword der Steuerdatei in einer Rauheitszone"


def test_the_term_repairs_see_the_whole_message_but_no_literal(tmp_path):
    (tmp_path / "termfix.de.tsv").write_text("state\t\\bStaat\\b\tZustand\n", encoding="utf-8")
    rules = tr.load_termfix("de", tmp_path)
    prepared = tr.prepare("The state of ``Staat`` is shown in **the state**.", EMPTY, [])
    answer = {prepared.text: "Der Staat von @@0@@ wird in **dem Staat** gezeigt."}
    assert tr.assemble(prepared, answer, rules) == "Der Zustand von ``Staat`` wird in **dem Zustand** gezeigt."


def test_french_gets_a_non_breaking_space_before_a_colon():
    assert tr._french_spacing("Les paramètres de cas: les pages; vu?") == \
        "Les paramètres de cas : les pages ; vu ?"
    assert tr._french_spacing("à 12:30") == "à 12:30"


def test_code_joined_by_or_is_put_together_without_the_engine(tmp_path):
    glossary = _glossary(tmp_path, "de", ["mesh\tRechennetz"])
    prepared = tr.prepare("``BAMG failed`` or ``Fatal error in the meshgenerator``", glossary, [])
    assert prepared.pieces() == []
    assert tr.assemble(prepared, {}, []) == "``BAMG failed`` oder ``Fatal error in the meshgenerator``"


def test_a_label_at_the_start_of_a_paragraph_is_not_sent():
    prepared = tr.prepare("*Bed changes.* The solver can also lower the bed.", EMPTY, [])
    assert prepared.pieces() == ["The solver can also lower the bed."]
    answer = {"The solver can also lower the bed.": "Le solveur peut aussi abaisser le lit."}
    assert tr.assemble(prepared, answer, []) == "*Bed changes.* Le solveur peut aussi abaisser le lit."


def test_a_name_at_the_start_of_a_sentence_stays_in_it():
    """aXqua is kept in English; at the start of a sentence it is the subject and is sent along."""
    prepared = tr.prepare("aXqua builds a model in five steps.", EMPTY, ["aXqua"])
    assert prepared.pieces() == ["aXqua builds a model in five steps."]
    # the engine's spelling of a kept name is put right; a name it lost fails the message
    answer = {prepared.text: "AXqua erstellt ein Modell in fünf Schritten."}
    assert tr.assemble(prepared, answer, []) == "aXqua erstellt ein Modell in fünf Schritten."
    assert tr.assemble(prepared, {prepared.text: "Es erstellt ein Modell."}, []) is None
    # in the fallback, the name is masked
    masked = tr.prepare("aXqua builds a model in five steps.", EMPTY, ["aXqua"], terms_masked=True)
    assert masked.pieces() == ["@@0@@ builds a model in five steps."]
    answer = {masked.text: "@@0@@ erstellt ein Modell in fünf Schritten."}
    assert tr.assemble(masked, answer, []) == "aXqua erstellt ein Modell in fünf Schritten."


def test_entries_of_a_list_of_references_are_recognized():
    assert tr.is_reference('Celik, I. B., Ghia, U., and Raad, P. E. (2008). "Procedure." *J. Fluids Eng.*')
    assert tr.is_reference("Thorenz, C., 2024. Boundary conditions for hydraulic structures.")
    assert not tr.is_reference("Calibration adjusts the parameters until 2008.")


def test_a_german_sentence_in_lower_case_gets_its_nouns_back():
    nouns = {"eintrag", "tippfehler", "hand", "profil", "nachricht"}
    text = ("Das ist gut. Wenn es sich bei dem eintrag um einen tippfehler in einem von hand "
            "bearbeiteten profil handelt, benennt die nachricht den eintrag.")
    assert tr.recapitalize(text, nouns) == (
        "Das ist gut. Wenn es sich bei dem Eintrag um einen Tippfehler in einem von Hand "
        "bearbeiteten Profil handelt, benennt die Nachricht den Eintrag.")
    # a sentence that has a capital is left as it is
    assert tr.recapitalize("Der eintrag ist da, und die Nachricht auch, sagt man hier.", nouns) == \
        "Der eintrag ist da, und die Nachricht auch, sagt man hier."


def test_recapitalizing_leaves_literals_links_and_gui_labels_as_they_are():
    """Review of 2026-10-18: the nouns were capitalized in the whole sentence, also inside a literal that is a command."""
    nouns = {"status", "eintrag", "ergebnisse", "seite", "tippfehler"}
    text = ("mit ``axqua status <config>`` zeigt der eintrag den status, und *load results* "
            "lädt die ergebnisse, siehe `seite <https://axqua.eu/status>`_ zum tippfehler.")
    assert tr.recapitalize(text, nouns) == (
        "mit ``axqua status <config>`` zeigt der Eintrag den Status, und *load results* "
        "lädt die Ergebnisse, siehe `seite <https://axqua.eu/status>`_ zum Tippfehler.")
    # markup that holds a full stop or a colon itself is not cut at it (re-review of 2026-10-18)
    text = ("nach `celik et al. 2008 status <https://doi.org/x>`_ zeigt der eintrag mit ``a. status`` "
            "den status und die ergebnisse.")
    assert tr.recapitalize(text, nouns) == (
        "nach `celik et al. 2008 status <https://doi.org/x>`_ zeigt der Eintrag mit ``a. status`` "
        "den Status und die Ergebnisse.")


def test_a_catalog_reads_back_what_was_written(tmp_path):
    entries = [tr.Entry('Quote " and back\\slash', 'Zitat " und Rück\\strich',
                        comments=["machine translation: argos 0123456789ab"]),
               tr.Entry("Two\nlines", "Zwei\nZeilen"),
               tr.Entry("Context", "Kontext", msgctxt="menu")]
    path = tmp_path / "page.po"
    path.write_text(tr.render_catalog(entries, "de", "page"), encoding="utf-8")
    back = tr.read_catalog(path)
    assert [(e.msgid, e.msgstr, e.msgctxt, e.comments) for e in back] == \
        [(e.msgid, e.msgstr, e.msgctxt, e.comments) for e in entries]
    assert back[0].machine == ("argos", "0123456789ab") and back[1].machine is None


POT = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

#: ../../docs/usage/page.rst:3
msgid "The mesh of the reach."
msgstr ""

#: ../../docs/usage/page.rst:5
msgid "The water depth is computed."
msgstr ""

#: ../../src/axqua/mesh.py:docstring of axqua.mesh.build_mesh:1
msgid "Build the mesh."
msgstr ""
'''


class FakeEngine:
    """Translates by prefixing, and counts what it was asked."""

    def __init__(self):
        self.asked: list[str] = []

    def __call__(self, pieces, engine, lang, jobs):
        self.asked.extend(pieces)
        return {piece: f"DE[{piece}]" for piece in pieces}


def _setup(tmp_path, rows):
    folder = tmp_path / "i18n"
    folder.mkdir()
    (folder / "glossary.de.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    pot = tmp_path / "build" / "gettext"
    (pot / "usage").mkdir(parents=True)
    (pot / "usage" / "page.pot").write_text(POT, encoding="utf-8")
    return folder, pot, tmp_path / "locale"


def test_the_catalogs_translate_only_what_changed_and_keep_what_was_reviewed(tmp_path):
    folder, pot, locale = _setup(tmp_path, ["mesh\tRechennetz"])
    po = locale / "de" / "LC_MESSAGES" / "usage" / "page.po"
    engine = FakeEngine()

    counts = tr.translate_language("de", pot, engine="argos", locale=locale, folder=folder, run=engine)
    assert counts == {"translated": 2}
    entries = {e.msgid: e for e in tr.read_catalog(po)}
    assert "Build the mesh." not in entries        # docstrings stay English
    assert entries["The mesh of the reach."].msgstr == "DE[The Rechennetz of the reach.]"

    # nothing changed: nothing is sent
    engine.asked.clear()
    assert tr.translate_language("de", pot, engine="argos", locale=locale, folder=folder,
                                 run=engine) == {"kept": 2}
    assert engine.asked == []

    # a glossary row concerns one message: only that one is sent again
    (folder / "glossary.de.tsv").write_text("mesh\tRechennetz\nreach\tFlussabschnitt\n", encoding="utf-8")
    tr.translate_language("de", pot, engine="argos", locale=locale, folder=folder, run=engine)
    assert engine.asked == ["The Rechennetz of the Flussabschnitt."]

    # a reviewed entry (the comment deleted) survives --retranslate
    text = po.read_text(encoding="utf-8")
    text = re.sub(r"# machine translation: argos \w+\n(msgid \"The water depth)", r"\1", text)
    text = text.replace('"DE[The water depth is computed.]"', '"Die Wassertiefe wird berechnet."')
    po.write_text(text, encoding="utf-8")
    engine.asked.clear()
    counts = tr.translate_language("de", pot, engine="argos", locale=locale, folder=folder,
                                   run=engine, retranslate=True)
    assert counts == {"reviewed": 1, "translated": 1}
    assert engine.asked == ["The Rechennetz of the Flussabschnitt."]
    entries = {e.msgid: e for e in tr.read_catalog(po)}
    assert entries["The water depth is computed."].msgstr == "Die Wassertiefe wird berechnet."

    # the English text of the reviewed entry changes: it is translated by machine again
    (pot / "usage" / "page.pot").write_text(POT.replace("is computed.", "is computed again."),
                                            encoding="utf-8")
    counts = tr.translate_language("de", pot, engine="argos", locale=locale, folder=folder, run=engine)
    assert counts == {"kept": 1, "translated": 1}

    # a page that no longer exists loses its catalog
    (pot / "usage" / "page.pot").unlink()
    (pot / "other.pot").write_text(POT, encoding="utf-8")
    tr.translate_language("de", pot, engine="none", locale=locale, folder=folder, run=engine)
    assert not po.exists()
    assert (locale / "de" / "LC_MESSAGES" / "other.po").exists()


def test_a_reviewed_docstring_survives_a_run_that_does_not_translate_docstrings(tmp_path):
    """Review of 2026-10-18: a run without --docstrings, which every deploy is, wrote the catalogs without the entries of the API reference, so a reviewed one was lost."""
    folder, pot, locale = _setup(tmp_path, [])
    po = locale / "de" / "LC_MESSAGES" / "usage" / "page.po"
    engine = FakeEngine()
    tr.translate_language("de", pot, engine="argos", locale=locale, folder=folder, run=engine,
                          docstrings=True)
    text = re.sub(r"# machine translation: argos \w+\n(msgid \"Build the mesh)", r"\1",
                  po.read_text(encoding="utf-8"))
    po.write_text(text.replace('"DE[Build the mesh.]"', '"Das Netz bauen."'), encoding="utf-8")
    counts = tr.translate_language("de", pot, engine="argos", locale=locale, folder=folder,
                                   run=engine)
    assert counts == {"kept": 3}
    entries = {e.msgid: e for e in tr.read_catalog(po)}
    assert entries["Build the mesh."].msgstr == "Das Netz bauen."
    assert entries["Build the mesh."].machine is None


def test_a_translation_that_fails_again_keeps_the_earlier_one(tmp_path):
    """Review of 2026-10-18: a paragraph whose new translation failed lost its earlier one and was published in English."""
    folder, pot, locale = _setup(tmp_path, [])
    (pot / "usage" / "page.pot").write_text(
        POT + '\n#: ../../docs/usage/page.rst:7\nmsgid "Run ``axqua build`` first."\nmsgstr ""\n',
        encoding="utf-8")
    po = locale / "de" / "LC_MESSAGES" / "usage" / "page.po"
    tr.translate_language("de", pot, engine="argos", locale=locale, folder=folder, run=FakeEngine())
    before = {e.msgid: e for e in tr.read_catalog(po)}["Run ``axqua build`` first."]
    assert before.msgstr.startswith("DE[")

    def loses_the_placeholders(pieces, engine, lang, jobs):
        return {piece: "kaputt" for piece in pieces}

    counts = tr.translate_language("de", pot, engine="argos", locale=locale, folder=folder,
                                   run=loses_the_placeholders, retranslate=True)
    assert counts.get("failed, earlier translation kept") == 1 and "failed" not in counts
    after = {e.msgid: e for e in tr.read_catalog(po)}["Run ``axqua build`` first."]
    assert (after.msgstr, after.comments) == (before.msgstr, before.comments)
    # and it is named in the report, which would otherwise not say that it fails in every run
    report = (pot.parent / "failed-de.txt").read_text(encoding="utf-8")
    assert "earlier translation is kept" in report and "Run ``axqua build`` first." in report


def test_without_an_engine_the_catalogs_are_updated_and_nothing_is_translated(tmp_path):
    folder, pot, locale = _setup(tmp_path, [])
    engine = FakeEngine()
    counts = tr.translate_language("de", pot, engine="none", locale=locale, folder=folder, run=engine)
    assert counts == {"missing": 2} and engine.asked == []


@pytest.mark.parametrize("lang", ["de", "fr"])
def test_the_glossaries_of_the_repository_load_and_name_each_term_once(lang):
    glossary = tr.Glossary.load(lang)
    forms = [form.lower() for term in glossary.terms for form in set(term.english)]
    assert len(forms) == len(set(forms)), "an English term is in the glossary twice"
    if lang == "fr":
        without = [term.target[0] for term in glossary.terms if not term.gender]
        assert set(without) <= {"majuscules ou minuscules", "sédiments fins"}
    tr.load_termfix(lang)


PAGE = """<!DOCTYPE html><html lang="{lang}"><head><title>x</title></head><body>
<ul class="wy-breadcrumbs"><li class="wy-breadcrumbs-aside">
<a href="_sources/x.rst.txt">View page source</a></li></ul>
<div role="main"><div itemprop="articleBody"><h1>Title</h1></div></div></body></html>"""


def test_the_three_builds_are_connected_into_one_site(tmp_path):
    site = tmp_path / "site"
    for rel, lang in [("index.html", "en"), ("usage/x.html", "en"), ("de/index.html", "de"),
                      ("de/usage/x.html", "de"), ("fr/index.html", "fr"), ("fr/usage/x.html", "fr")]:
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_text(PAGE.format(lang=lang), encoding="utf-8")
    (site / "_static").mkdir()
    (site / "_static" / "aXqua-icon-reduced.png").write_bytes(b"")
    sys_argv = sys.argv
    try:
        sys.argv = ["inject_i18n.py", str(site), "--site-url", "https://axqua.eu/"]
        assert inj.main() == 0
        first = (site / "de" / "usage" / "x.html").read_text(encoding="utf-8")
        assert inj.main() == 0      # a second run changes nothing
    finally:
        sys.argv = sys_argv
    german = (site / "de" / "usage" / "x.html").read_text(encoding="utf-8")
    assert german == first
    assert german.count(inj.MARKER) == 1
    assert '<link rel="alternate" hreflang="fr" href="https://axqua.eu/fr/usage/x.html">' in german
    assert '<link rel="alternate" hreflang="x-default" href="https://axqua.eu/usage/x.html">' in german
    assert 'href="../../_i18n/lang.js"' not in german and 'src="../../_i18n/lang.js"' in german
    # the language button, first in the body, with the other two languages in its menu
    assert re.search(r'<body>\s*<details class="axqua-lang">', german)
    assert '<summary title="Language / Sprache / Langue"' in german and "<span>DE</span>" in german
    assert '<a href="../../usage/x.html" hreflang="en"' in german
    assert '<a href="../../fr/usage/x.html" hreflang="fr"' in german
    assert '<strong aria-current="true" lang="de">Deutsch</strong>' in german
    assert "maschinell aus dem Englischen" in german
    english = (site / "usage" / "x.html").read_text(encoding="utf-8")
    assert "axqua-mt-note" not in english and '<a href="../de/usage/x.html" hreflang="de"' in english
    root = (site / "index.html").read_text(encoding="utf-8")
    assert '<link rel="alternate" hreflang="de" href="https://axqua.eu/de/">' in root
    assert (site / "_i18n" / "lang.js").exists() and (site / "404.html").exists()


@pytest.mark.parametrize("script", ["deploy.sh", "docs/i18n/build_i18n.sh"])
def test_the_shell_scripts_parse(script):
    path = Path(__file__).resolve().parents[1] / script
    subprocess.run(["bash", "-n", str(path)], check=True)
