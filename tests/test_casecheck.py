"""A case file is checked as a whole, and what is wrong comes back as findings.

Loading a case stops at the first problem. An editor has to show all of them, each at
the setting it concerns, and has to accept a case that is half filled in.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from axqua import casecli
from axqua.core import schema_meta
from axqua.core.casecheck import check_case


def _case(folder: Path, text: str, files=("dem.tif", "outline.gpkg", "lines.gpkg")) -> Path:
    for name in files:
        (folder / name).write_bytes(b"")
    path = folder / "reach.axq-case"
    path.write_text(text, encoding="utf-8")
    return path


COMPLETE = """\
project: {name: reach, crs_epsg: 25832, sim_dir: axqua-case}
telemac: {solver: telemac2d}
geodata: {dem_initial: dem.tif, boundary: outline.gpkg}
boundaries: {liquid_boundaries: lines.gpkg, prescribed_flowrate: 2.4, outflow_condition: free}
"""


def _by_subject(findings):
    return {f.subject: f for f in findings}


def test_a_complete_case_has_no_findings_and_checking_writes_nothing(tmp_path):
    path = _case(tmp_path, COMPLETE)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert check_case(path) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_every_missing_file_is_named_at_its_setting(tmp_path):
    """The loader stops at the first one. A form has to mark them all."""
    path = _case(tmp_path, COMPLETE.replace("dem.tif", "missing-dem.tif").replace(
        "lines.gpkg", "data/missing-lines.gpkg"))
    found = _by_subject(check_case(path))
    assert set(found) == {"geodata.dem_initial", "boundaries.liquid_boundaries"}
    assert all(f.code == "axqua.config.missing_file" and f.severity == "error"
               for f in found.values())
    # a relative path is read from the folder of the case file, and the message says where
    assert str(tmp_path / "data" / "missing-lines.gpkg") in \
        found["boundaries.liquid_boundaries"].message


def test_a_misspelled_setting_is_named_with_the_one_that_was_meant(tmp_path):
    path = _case(tmp_path, COMPLETE + "mesh: {chanel_size: 0.5}\n")
    (finding,) = check_case(path)
    assert finding.code == "axqua.config.unknown_key"
    assert finding.subject == "mesh.chanel_size"
    assert finding.remedy == "Did you mean channel_size?"


def test_a_misspelled_block_is_named_too(tmp_path):
    path = _case(tmp_path, COMPLETE + "geodta: {dem_target: dem.tif}\n")
    found = _by_subject(check_case(path))
    assert found["geodta"].remedy == "Did you mean geodata?"


def test_a_case_that_was_just_started_is_incomplete_not_broken(tmp_path):
    """What ``axqua case new`` writes: the checks say what is still to be entered."""
    path = _case(tmp_path, "project: {name: reach, crs_epsg: 25832}\n", files=())
    found = check_case(path)
    subjects = {f.subject: f.severity for f in found}
    assert subjects["geodata.dem_initial"] == subjects["geodata.boundary"] == "error"
    assert subjects["telemac"] == "warning"                    # no simulation program
    assert subjects["boundaries.prescribed_flowrate"] == "warning"
    assert len(found) == len(subjects)                         # each thing said once


@pytest.mark.parametrize("text,code", [
    ("project: {name: [unclosed\n", "axqua.config.yaml_syntax"),
    ("- a\n- list\n", "axqua.config.not_a_case"),
])
def test_a_file_that_is_no_case_is_one_finding_and_no_exception(tmp_path, text, code):
    path = _case(tmp_path, text, files=())
    (finding,) = check_case(path)
    assert finding.code == code and finding.severity == "error"


def test_an_empty_file_and_a_missing_one_do_not_raise(tmp_path):
    assert check_case(_case(tmp_path, "", files=()))           # incomplete, with findings
    (finding,) = check_case(tmp_path / "no-such.axq-case")
    assert finding.code == "axqua.config.unreadable"


def test_a_value_the_case_cannot_have_is_reported_as_a_fact(tmp_path):
    path = _case(tmp_path, COMPLETE.replace("outflow_condition: free",
                                            "outflow_condition: tidal"))
    (finding,) = check_case(path)
    assert finding.code == "axqua.config.invalid_value"
    assert "outflow_condition" in finding.message


def test_every_block_reports_its_own_problem(tmp_path):
    """Validation before a build stops at the first. Here are two, in two blocks."""
    text = COMPLETE.replace("outflow_condition: free", "outflow_condition: elevation")
    path = _case(tmp_path, text.replace("solver: telemac2d", "solver: telemac4d"))
    found = _by_subject(check_case(path))
    assert found["boundaries.prescribed_elevation"].code == "axqua.config.invalid_value"
    assert "telemac4d" in found["telemac.solver"].message


def test_where_telemac_is_installed_is_not_a_problem_of_the_case(tmp_path):
    """It is a fact about the computer, and the check of the profile reports it. In
    this test there is no TELEMAC at all, and the case is still in order."""
    assert check_case(_case(tmp_path, COMPLETE)) == []


# ----------------------------------------------------------------- the field table


def test_the_table_lists_every_setting_with_a_kind_a_form_knows():
    sections = schema_meta.build()
    fields = [item for section in sections for item in section["fields"]]
    assert len(fields) > 240
    assert {item["kind"] for item in fields} <= set(schema_meta.KINDS)
    assert all(item["label"] for item in fields)
    json.dumps(sections)                                # what the plugin receives


def test_every_curated_setting_exists_and_is_essential():
    """A label for a setting that was renamed would label nothing, silently."""
    by_key = {item["key"]: item for section in schema_meta.build()
              for item in section["fields"]}
    assert set(schema_meta.CURATED) <= set(by_key)
    assert set(schema_meta.CHOICES) <= set(by_key)
    assert {key for key, item in by_key.items() if item["essential"]} == \
        set(schema_meta.CURATED)
    assert by_key["geodata.dem_initial"]["kind"] == "raster"
    assert by_key["geodata.mesh_zones"]["kind"] == "vector"
    assert by_key["geodata.roughness_table"]["kind"] == "table"
    assert by_key["project.sim_dir"]["kind"] == "folder"
    assert by_key["boundaries.outflow_condition"]["choices"] == [
        "elevation", "stage_discharge", "free"]
    # "auto" or a number: kept as it is typed
    assert by_key["hydrodynamics.turbulence_model"]["kind"] == "text"


def test_a_setting_without_a_label_of_its_own_is_explained_by_its_source_comment():
    by_key = {item["key"]: item for section in schema_meta.build()
              for item in section["fields"]}
    growth = by_key["mesh.growth_ratio"]
    assert not growth["essential"] and growth["label"] == "Growth ratio"
    assert "growth" in growth["help"]


def test_the_sections_follow_the_documentation_and_miss_no_block():
    sections = schema_meta.build()
    assert [s["title"] for s in sections][:2] == ["Project paths", "Geodata"]
    from axqua.config import load_config

    path = Path(__file__).resolve().parent.parent / "cases" / "example-isar" / \
        "example-isar.axq-case"
    declared = load_config(path).declared_blocks
    assert declared <= {s["block"] for s in sections}


# ------------------------------------------------------------------ the commands


def _json_out(capsys):
    return json.loads(capsys.readouterr().out)


def test_a_new_case_can_be_written_read_and_checked(tmp_path, capsys, monkeypatch):
    target = tmp_path / "reach.axq-case"
    assert casecli.run_case(["new", str(target), "--name", "reach", "--json"]) == 0
    created = _json_out(capsys)["data"]
    assert created["data"]["project"] == {"name": "reach", "crs_epsg": 25832,
                                          "sim_dir": "axqua-case"}
    assert casecli.run_case(["new", str(target), "--json"]) != 0     # never overwritten
    capsys.readouterr()

    for name in ("dem.tif", "outline.gpkg"):
        (tmp_path / name).write_bytes(b"")
    data = created["data"]
    data["geodata"] = {"dem_initial": "dem.tif", "boundary": "outline.gpkg",
                       "mesh_zones": ""}                      # left empty in the form
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"data": data})))
    assert casecli.run_case(["write", str(target), "--json"]) == 0
    written = _json_out(capsys)["data"]
    assert Path(written["backup"]).name == "reach.axq-case.bak"      # the original
    assert {f.get("subject") for f in written["findings"]} == {
        "boundaries.liquid_boundaries", "boundaries.prescribed_flowrate",
        "boundaries.prescribed_elevation"}

    assert casecli.run_case(["read", str(target), "--json"]) == 0
    read = _json_out(capsys)["data"]["data"]
    assert read["geodata"] == {"dem_initial": "dem.tif", "boundary": "outline.gpkg"}

    assert casecli.run_check([str(target), "--json"]) == 0           # findings, exit 0
    assert len(_json_out(capsys)["data"]["findings"]) == 3


def test_the_backup_is_the_first_original_and_is_not_replaced(tmp_path, capsys,
                                                             monkeypatch):
    target = _case(tmp_path, "# written by hand\n" + COMPLETE)
    for name in ("first", "second"):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
            {"project": {"name": name, "crs_epsg": 25832}})))
        casecli.run_case(["write", str(target), "--json"])
        capsys.readouterr()
    assert "# written by hand" in (tmp_path / "reach.axq-case.bak").read_text()
    assert "second" in target.read_text()


def test_the_schema_command_prints_the_table(capsys):
    assert casecli.run_schema(["--json"]) == 0
    sections = _json_out(capsys)["data"]["sections"]
    assert sections[0]["block"] == "project" and sections[0]["label"] == "case-project"
