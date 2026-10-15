"""Validation of a calibrated model against measurements of another flow situation."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from axqua import validation
from axqua.config import (Calibration, GroundTruthSource, ValidationSituation,
                          config_to_dict, load_config)
from test_selafin_vtk import IKLE, X, Y, write_result

CAS = """/ built by a test
TITLE : 'reach steady'
GEOMETRY FILE : geometry.slf
RESULTS FILE : r2d.slf
DURATION : 3000.0
PRESCRIBED FLOWRATES : 0.8000;0.;1.6000
PRESCRIBED ELEVATIONS : 0.;100.0000;0.
FORTRAN FILE : 'user_fortran'
FRICTION DATA : YES
FRICTION DATA FILE : friction.tbl
VELOCITY DIFFUSIVITY : 1.E-6
"""

TABLE = """* Friction data file
* zone 4: gravel
4\tNIKU\t0.0500\tNULL
* zone 6: vegetation
6\tNIKU\t0.5000\tNULL
END
"""

LIQUIDS = [{"index": 1, "kind": "inflow", "n_nodes": 12, "discharge": 0.8},
           {"index": 2, "kind": "outflow", "n_nodes": 15, "discharge": None},
           {"index": 3, "kind": "inflow", "n_nodes": 15, "discharge": 1.6}]


@pytest.fixture
def built(fake_case):
    """A case as a build and a calibration leave it."""
    cfg = fake_case
    cfg.model_path(cfg.cas_file).write_text(CAS)
    cfg.model_path("friction.tbl").write_text(TABLE)
    cfg.model_path(cfg.liquid_boundaries_json).write_text(json.dumps(LIQUIDS))
    fortran = cfg.model_path("user_fortran")
    fortran.mkdir()
    (fortran / "user_rain.f").write_text("      SUBROUTINE USER_RAIN\n"
                                         "      KF = 1.0D-4\n      END\n")
    return cfg


def _calibrated(cfg, **extra) -> Path:
    folder = Path(cfg.calibration_dir) / validation.RESULTS / "calibration-data" / "V"
    folder.mkdir(parents=True, exist_ok=True)
    data = {"calibration_parameters": ["zone4", "zone6"],
            "joint_optimum": [np.array([0.2, 0.3]), np.array([0.04064, 0.1059])],
            "posterior": [np.zeros((5, 2)), np.full((5, 2), 0.07)], **extra}
    with open(folder / "BAL_dictionary.pkl", "wb") as handle:
        pickle.dump(data, handle)
    return folder / "BAL_dictionary.pkl"


# ------------------------------------------------------------------------ the config


def test_validation_data_is_another_situation_and_not_a_share_of_the_calibration_data(
        tmp_path):
    """Everything under ground_truth is calibration data. What validates the model is
    named apart, with the discharge it was measured at."""
    given = {"init_runs": 8, "validation": {
        "name": "September 2025", "inflows": {"1": 0.2, 3: 5.1}, "duration": 4000,
        "sources": [{"category": "hydraulics", "kind": "points",
                     "positions": "data/september.gpkg"}]}}
    calibration = Calibration.from_dict(given, tmp_path)
    (situation,) = calibration.validation                       # one, written plainly
    assert situation.inflows == {1: 0.2, 3: 5.1}
    assert situation.total_flowrate == pytest.approx(5.3)
    assert situation.sources[0].positions == tmp_path / "data" / "september.gpkg"
    assert ValidationSituation(prescribed_flowrate=4).total_flowrate == 4.0
    assert Calibration.from_dict({}).validation == []
    with pytest.raises(ValueError, match="unknown config keys"):
        Calibration.from_dict({"validation": [{"split": 0.3}]})   # there is no split


def test_a_case_with_validation_data_comes_back_from_a_dump(fake_case, tmp_path):
    from axqua.config import dump_config

    fake_case.calibration.validation = [ValidationSituation(
        name="flood", prescribed_flowrate=40.0, prescribed_elevation=101.2,
        sources=[GroundTruthSource(category="hydraulics", kind="points",
                                   positions=tmp_path / "flood.gpkg")])]
    dump_config(fake_case, tmp_path / "again.axq-case")
    again = load_config(tmp_path / "again.axq-case")
    (situation,) = again.calibration.validation
    assert (situation.name, situation.prescribed_flowrate,
            situation.prescribed_elevation) == ("flood", 40.0, 101.2)
    assert situation.sources[0].positions == tmp_path / "flood.gpkg"
    assert config_to_dict(again)["calibration"]["validation"][0]["name"] == "flood"


def test_a_situation_is_found_by_name_and_one_alone_needs_none(fake_case):
    from axqua.core.errors import ConfigError

    with pytest.raises(ConfigError, match="no validation data"):
        validation.situation_named(fake_case)
    fake_case.calibration.validation = [ValidationSituation(name="September 2025")]
    assert validation.situation_named(fake_case).name == "September 2025"
    assert validation.situation_named(fake_case, "september-2025").name == \
        "September 2025"
    fake_case.calibration.validation.append(ValidationSituation(name="flood"))
    with pytest.raises(ConfigError, match="several"):
        validation.situation_named(fake_case)
    with pytest.raises(ConfigError, match="It has: September 2025, flood"):
        validation.situation_named(fake_case, "spring")


# ------------------------------------------------------------------ calibrated values


def test_the_calibrated_values_are_the_joint_maximum_of_the_last_iteration(built):
    from axqua.core.errors import ConfigError

    with pytest.raises(ConfigError, match="no finished calibration"):
        validation.calibrated_parameters(built)
    source = _calibrated(built)
    found = validation.calibrated_parameters(built)
    assert found.as_dict() == {"zone4": 0.04064, "zone6": 0.1059}
    assert found.estimate == "joint posterior maximum" and found.source == source
    _calibrated(built, joint_optimum=[])            # an older HydroBayesCal
    older = validation.calibrated_parameters(built)
    assert older.estimate == "posterior mean"
    assert older.as_dict() == pytest.approx({"zone4": 0.07, "zone6": 0.07})


# ------------------------------------------------------------------------ the run


def test_the_validation_run_is_the_calibrated_model_with_other_boundary_values(built):
    situation = ValidationSituation(name="September 2025", inflows={1: 0.2, 3: 5.1},
                                    prescribed_elevation=100.4)
    setup = validation.build(built, situation, {"zone4": 0.04064, "zone6": 0.1059})
    assert setup.cas.name == "validation-september-2025.cas"
    assert setup.results.name == "r2d-validation-september-2025.slf"
    assert (setup.flowrate, setup.flowrates, setup.elevations) == \
        (pytest.approx(5.3), "0.2000;0.;5.1000", "0.;100.4000;0.")

    before, after = CAS.splitlines(), setup.cas.read_text().splitlines()
    changed = {a.split(":")[0].strip() for a, b in zip(before, after) if a != b}
    # these lines and no others: a difference to the measurements cannot come from a
    # model that was built differently
    assert changed == {"TITLE", "RESULTS FILE", "PRESCRIBED FLOWRATES",
                       "PRESCRIBED ELEVATIONS", "FRICTION DATA FILE"}
    assert len(before) == len(after)
    table = setup.friction.read_text()
    assert setup.friction.name == "friction-validation-september-2025.tbl"
    assert "4\tNIKU\t0.04064\tNULL" in table and "6\tNIKU\t0.1059\tNULL" in table
    assert table.count("\n") == TABLE.count("\n")
    # the built case is what it was
    assert built.model_path(built.cas_file).read_text() == CAS
    assert built.model_path("friction.tbl").read_text() == TABLE


def test_a_total_discharge_keeps_the_split_of_the_calibrated_case(built):
    situation = ValidationSituation(name="high", prescribed_flowrate=4.8,
                                    prescribed_elevation=100.9, duration=5000)
    setup = validation.build(built, situation, {})
    assert setup.flowrates == "1.6000;0.;3.2000"                # 0.8 : 1.6 as before
    assert "DURATION : 5000.0" in setup.cas.read_text()
    assert setup.friction is None                               # nothing calibrated
    assert validation.boundaries_of(built)[0] == {"index": 1, "kind": "inflow",
                                                  "discharge": 0.8, "n_nodes": 12}


def test_constants_and_keywords_are_set_where_the_calibration_set_them(built):
    situation = ValidationSituation(name="v", prescribed_flowrate=2.4,
                                    prescribed_elevation=100.0)
    setup = validation.build(built, situation, {"f.KF": 3.5e-4,
                                                "VELOCITY DIFFUSIVITY": 0.02})
    text = setup.cas.read_text()
    assert "VELOCITY DIFFUSIVITY : 0.02" in text
    assert "FORTRAN FILE : 'user_fortran-validation-v'" in text
    copied = built.model_path("user_fortran-validation-v") / "user_rain.f"
    assert "      KF = 0.00035" in copied.read_text()
    assert "KF = 1.0D-4" in (built.model_path("user_fortran") / "user_rain.f").read_text()


@pytest.mark.parametrize("situation, parameters, message", [
    (ValidationSituation(name="v"), {}, "names no discharge"),
    (ValidationSituation(name="v", prescribed_flowrate=5.0), {},
     "prescribes the water level at the outflow"),
    (ValidationSituation(name="v", inflows={1: 0.2}, prescribed_elevation=100.0), {},
     "without a discharge: \\[3\\]"),
    (ValidationSituation(name="v", inflows={1: 1, 2: 1, 3: 1},
                         prescribed_elevation=100.0), {}, "not an inflow: \\[2\\]"),
    (ValidationSituation(name="v", prescribed_flowrate=5.0, prescribed_elevation=100.0),
     {"zone9": 0.1}, "no zone 9"),
    (ValidationSituation(name="v", prescribed_flowrate=5.0, prescribed_elevation=100.0),
     {"gaia.MPM": 8.0}, "sediment transport"),
])
def test_what_cannot_be_run_is_said_before_anything_is_started(built, situation,
                                                              parameters, message):
    from axqua.core.errors import ConfigError

    with pytest.raises(ConfigError, match=message):
        validation.build(built, situation, parameters)
    assert not list(Path(built.model_dir).glob("validation-*.cas"))


# ------------------------------------------------------------------- the comparison


def test_the_numbers_a_validation_is_judged_by():
    measured = np.array([1.0, 2.0, 3.0, 4.0, np.nan])
    modelled = np.array([0.9, 1.8, 3.3, 3.2, 1.0])
    numbers = validation.statistics(measured, modelled, np.array([0.1, 0.1, 0.1, 0.5, 1]))
    assert numbers["n"] == 4
    assert numbers["bias"] == pytest.approx(-0.2)             # the model is too slow
    assert numbers["relative_bias"] == pytest.approx(-0.08)
    assert numbers["mae"] == pytest.approx(0.35)
    assert numbers["rmse"] == pytest.approx(np.sqrt((0.01 + 0.04 + 0.09 + 0.64) / 4))
    assert numbers["within_error"] == pytest.approx(0.25)
    assert numbers["within_two_errors"] == pytest.approx(0.75)
    assert validation.statistics([np.nan], [1.0]) == {"n": 0}


def test_the_model_is_read_on_the_mesh_of_the_geometry_and_only_inside_it(tmp_path):
    bed = np.zeros((1, 6))
    geometry = write_result(tmp_path / "geometry.slf", X, Y, IKLE,
                            [("BOTTOM", "M", bed)], [0.0], double=True)
    # the velocity grows linearly along x, the depth is 0.4 m and one corner is dry
    speed = (X - X[0])[None, :] * 0.4
    depth = np.array([[0.4, 0.4, 0.4, 0.0, 0.4, 0.4]])
    result = write_result(tmp_path / "r.slf", X, Y, IKLE, [
        ("SCALAR VELOCITY", "M/S", speed), ("WATER DEPTH", "M", depth)], [3000.0])
    points = pd.DataFrame({
        "id": [1, 2, 3, 4],
        "x": [X[0] + 0.5, X[0] + 2.0, X[3] + 0.001, X[0] - 30.0],
        "y": [Y[0] + 0.1, Y[0] + 0.1, Y[3] - 0.001, Y[0]],
        "SCALAR VELOCITY_DATA": [0.25, 0.80, 0.30, 0.90],
        "SCALAR VELOCITY_ERROR": [0.05, 0.0, 0.05, 0.05],
        "WATER DEPTH_DATA": [0.4, 0.5, 0.3, 0.6],
        "FREE SURFACE_DATA": [1.0, 1.0, 1.0, 1.0]})
    report = validation.compare(points, geometry, result, name="v")
    assert (report.n_points, report.n_outside, report.time) == (4, 1, 3000.0)
    table = report.table
    assert list(table["inside_model"]) == [True, True, True, False]
    # single precision would have put the nodes half a meter apart from where they are
    assert table["scalar_velocity_modelled"][0] == pytest.approx(0.2, abs=1e-6)
    assert table["scalar_velocity_modelled"][1] == pytest.approx(0.8, abs=1e-6)
    assert np.isnan(table["scalar_velocity_modelled"][3])     # no model out there
    velocity = report.summary["SCALAR VELOCITY"]
    assert velocity["n"] == 3 and velocity["bias"] < 0
    # the error of a measurement: its own, and a tenth of the value
    assert table["scalar_velocity_error"][0] == pytest.approx(np.hypot(0.05, 0.025))
    assert table["scalar_velocity_error"][1] == pytest.approx(0.08)
    assert list(table["dry_in_model"]) == [False, False, True, False]
    assert any("dry in the model" in note for note in report.notes)
    assert any("outside the model" in note for note in report.notes)
    assert any("no FREE SURFACE" in note for note in report.notes)
    assert json.loads(json.dumps(report.as_dict()))["n_outside"] == 1


# ------------------------------------------------------------------------- the job


def test_validation_is_a_job_of_the_calibration_capability():
    from axqua.core.capabilities import Capability
    from axqua.core.errors import ConfigError
    from axqua.jobs.model import KIND_META, JobKind, ValidationOptions, parse_kind

    meta = KIND_META[JobKind.VALIDATION]
    assert (meta.solver, meta.capability, meta.verb) == ("telemac",
                                                         Capability.CALIBRATION, "study")
    assert parse_kind("validation") is JobKind.VALIDATION is parse_kind("val")
    options = ValidationOptions.from_dict({"situation": "September 2025"})
    assert options.as_dict() == {"situation": "September 2025", "compare_only": False,
                                 "ncsize": None, "validate": True}
    with pytest.raises(ConfigError, match="unknown option"):
        ValidationOptions.from_dict({"split": 0.3})


# ------------------------------------------------- measurement points and the model


def test_measurement_points_outside_the_model_are_left_out(fake_case, tmp_path):
    """A campaign includes the cross sections at which the inflow was gauged, upstream
    of the model. HydroBayesCal reads the nearest node of the model with no limit on the
    distance, so such a point would be compared with a node at the boundary."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    from axqua import bayescal

    outline = tmp_path / "outline.gpkg"
    gpd.GeoDataFrame(geometry=[Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])],
                     crs="EPSG:25832").to_file(outline, driver="GPKG")
    fake_case.geodata.boundary = outline
    points = pd.DataFrame({"id": [1, 2, 3, 4], "x": [5.0, 50.0, 1.0, -3.0],
                           "y": [5.0, 5.0, 9.0, 5.0],
                           "SCALAR VELOCITY_DATA": [0.5, 0.9, 0.3, 0.7]})
    kept = bayescal._inside_the_model(fake_case, points)
    assert list(kept["SCALAR VELOCITY_DATA"]) == [0.5, 0.3]
    assert list(kept["id"]) == [1, 2]                    # numbered anew, without gaps
    assert len(points) == 4                              # the table given is untouched

    # without an outline that can be read, every point is kept rather than lost
    fake_case.geodata.boundary = tmp_path / "missing.gpkg"
    assert len(bayescal._inside_the_model(fake_case, points)) == 4
