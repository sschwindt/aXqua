"""A TELEMAC case aXqua did not build, imported for post-processing (the user's order of
2026-11-20): ``axqua case import`` and what an imported case can and cannot do."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from axqua.config import load_config
from axqua.core.errors import ConfigError
from axqua.solvers.telemac import importcase
from test_selafin_vtk import IKLE, X, Y, write_result

CAS_2D = """/ a TELEMAC-2D case of somebody else
TITLE = 'reach'
GEOMETRY FILE            = 'geo.slf'
BOUNDARY CONDITIONS FILE : bc.cli
RESULTS FILE             = 'out/r2d-reach.slf'
DURATION                 = 3600.
"""

CAS_3D = """GEOMETRY FILE : geo.slf
NUMBER OF HORIZONTAL LEVELS : 5
3D RESULT FILE : r3d.slf
2D RESULT FILE : r3d-2d.slf
"""


def _model(folder: Path, cas: str = CAS_2D, *, result: bool = True) -> Path:
    """A TELEMAC folder: the steering file, the geometry and (with *result*) a result."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "t2d.cas").write_text(cas)
    bed = np.array([815.0, 815.2, 815.4, 816.0, 816.1, 816.3])
    write_result(folder / "geo.slf", X, Y, IKLE, [("BOTTOM", "M", bed[None, :])], [0.0])
    if result:
        (folder / "out").mkdir(exist_ok=True)
        depth = np.array([[0.5, 0.4, 0.0, 0.2, 0.1, 0.0], [1.0, 0.8, 0.3, 0.6, 0.5, 0.2]])
        write_result(folder / "out" / "r2d-reach.slf", X, Y, IKLE, [
            ("WATER DEPTH", "M", depth), ("SCALAR VELOCITY", "M/S", depth * 0.9)],
            [0.0, 3600.0])
    return folder / "t2d.cas"


def test_the_steering_file_names_what_the_case_takes_over(tmp_path):
    steering = importcase.read(_model(tmp_path / "m"))
    assert steering.solver == "telemac2d"
    assert (steering.geometry, steering.boundary, steering.results) == (
        "geo.slf", "bc.cli", "out/r2d-reach.slf")
    (tmp_path / "m" / "t3d.cas").write_text(CAS_3D)
    three = importcase.read(tmp_path / "m" / "t3d.cas")
    assert three.solver == "telemac3d" and three.result_names == ["r3d-2d.slf", "r3d.slf"]
    (tmp_path / "m" / "bare.cas").write_text("GEOMETRY FILE : geo.slf\n")
    with pytest.raises(ConfigError, match="names no RESULTS FILE"):
        importcase.read(tmp_path / "m" / "bare.cas")
    with pytest.raises(ConfigError, match="no steering file"):
        importcase.read(tmp_path / "nothing.cas")


def test_the_import_writes_a_case_beside_the_steering_file_that_loads(tmp_path):
    cas = _model(tmp_path / "m")
    answer = importcase.import_steering(cas, crs_epsg=31468)
    case = Path(answer["case"])
    assert case == (tmp_path / "m" / "t2d.axq-case").resolve()
    assert answer["solver"] == "telemac2d" and answer["missing"] == []
    assert answer["results"] == [{"name": "out/r2d-reach.slf", "exists": True,
                                  "path": str((tmp_path / "m" / "out" / "r2d-reach.slf").resolve())}]
    text = case.read_text()
    assert "model_dir: ." in text and "imported_from: t2d.cas" in text   # moves along
    cfg = load_config(case)
    assert cfg.imported and cfg.crs_epsg == 31468
    assert cfg.geodata.dem_initial is None and cfg.geodata.boundary is None
    assert cfg.model_path(cfg.results_slf).is_file()
    # never over an existing case file
    with pytest.raises(ConfigError, match="already exists"):
        importcase.import_steering(cas)


def test_a_case_file_elsewhere_names_the_model_by_its_full_path(tmp_path):
    cas = _model(tmp_path / "model")
    answer = importcase.import_steering(cas, tmp_path / "cases" / "reach", name="Reach")
    case = Path(answer["case"])
    assert case.name == "reach.axq-case"
    cfg = load_config(case)
    assert cfg.name == "Reach" and Path(cfg.model_dir) == (tmp_path / "model").resolve()
    assert str(tmp_path / "model") in case.read_text()


def test_an_imported_case_is_checked_for_its_files_not_for_geodata(tmp_path):
    from axqua.casecli import _all_findings

    cas = _model(tmp_path / "m", result=False)
    case = Path(importcase.import_steering(cas)["case"])
    findings = _all_findings(case)
    assert [(f.severity, f.code) for f in findings] == [("warning", "axqua.config.incomplete")]
    assert "does not exist yet" in findings[0].message
    (tmp_path / "m" / "geo.slf").unlink()
    codes = [f.code for f in _all_findings(case) if f.severity == "error"]
    assert codes == ["axqua.config.missing_file"]


def test_an_imported_case_is_not_built_and_runs_no_job(tmp_path):
    from axqua.jobs.submit import submit_job
    from axqua.solvers.telemac import pipeline

    cfg = load_config(importcase.import_steering(_model(tmp_path / "m"))["case"])
    with pytest.raises(ConfigError, match="does not build or run it"):
        submit_job(cfg, "steady", dry_run=True, validate_env=False)
    with pytest.raises(ConfigError, match="the build is not started"):
        pipeline.run(cfg, validate_env=False)
    assert sorted(p.name for p in (tmp_path / "m").iterdir()) == [
        "geo.slf", "out", "t2d.axq-case", "t2d.cas"]               # nothing written


def test_the_listing_names_the_map_layers_of_each_result(tmp_path, capsys):
    from axqua.exportcli import run_export

    case = importcase.import_steering(_model(tmp_path / "m"), crs_epsg=31468)["case"]
    assert run_export([case, "--list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["crs_epsg"] == 31468 and data["imported"] is True
    (row,) = data["results"]
    assert row["name"] == "out/r2d-reach.slf"
    assert [(layer["style"], layer["variable"]) for layer in row["layers"]] == [
        ("water-depth", "WATER DEPTH"), ("velocity", "SCALAR VELOCITY")]
    assert all(layer["kind"] == "mesh" for layer in row["layers"])


def test_the_command_imports_from_the_steering_file_alone(tmp_path, capsys):
    from axqua.casecli import run_case

    cas = _model(tmp_path / "m")
    assert run_case(["import", str(cas), "--crs", "25832", "--json"]) == 0
    answer = json.loads(capsys.readouterr().out)["data"]
    assert Path(answer["case"]).name == "t2d.axq-case"
    assert run_case(["import", str(tmp_path / "x.axq-case"), "--json"]) != 0
    assert "--steering" in json.loads(capsys.readouterr().out)["error"]["remedy"]
