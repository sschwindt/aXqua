"""Calibration runs that start from the converged steady result (``calibration.hotstart``).

Each run of a calibration used to repeat the steady run from its dry or pre-wetted
start: on the finest Inn KB15 mesh 56 min a run. Starting from ``r2d.slf`` the flow only
settles to the roughness being tested.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from axqua import bayescal
from axqua.core import errors, selafin
from axqua.solvers.telemac.steering import read_cas_values

STEADY = """TITLE : 'reach steady'
GEOMETRY FILE : geometry.slf
BOUNDARY CONDITIONS FILE : boundaries.cli
RESULTS FILE : r2d.slf
VARIABLES FOR GRAPHIC PRINTOUTS : 'U,V,S,B,H,M'
DURATION : 8000.0
FRICTION DATA FILE : friction.tbl
/ dry start
PREVIOUS COMPUTATION FILE : initial-conditions.slf
INITIAL TIME SET TO ZERO : YES
&ETA
"""

# a square of 10 m x 10 m, two triangles
X = np.array([0.0, 10.0, 10.0, 0.0]) + 700000.0
Y = np.array([0.0, 0.0, 10.0, 10.0]) + 5340000.0
IKLE = np.array([[0, 1, 2], [0, 2, 3]])
IPOBO = np.array([1, 2, 3, 4])


@pytest.fixture
def cfg(tmp_path):
    from axqua.config import (
        Boundaries, Calibration, Config, Friction, Geodata, GroundTruth,
        Hydrodynamics, Initialization, MeshConfig, Morphodynamics, TelemacEnv,
    )

    model = tmp_path / "model"
    model.mkdir()
    dummy = tmp_path / "dummy"
    dummy.write_text("")
    cfg = Config(
        name="reach", crs_epsg=25832, config_dir=tmp_path,
        preprocessing_dir=tmp_path, model_dir=model, postprocessing_dir=tmp_path,
        calibration_dir=tmp_path / "calibration",
        telemac=TelemacEnv(pysource=dummy),
        geodata=Geodata(dem_initial=dummy, boundary=dummy),
        boundaries=Boundaries(liquid_boundaries=dummy, outflow_condition="elevation",
                              prescribed_elevation=373.5, prescribed_flowrate=0.01),
        initialization=Initialization(), mesh=MeshConfig(), friction=Friction(),
        hydrodynamics=Hydrodynamics(), morphodynamics=Morphodynamics(),
        ground_truth=GroundTruth(), calibration=Calibration(hotstart=True),
    )
    (model / "steady2d.cas").write_text(STEADY)
    (model / "friction.tbl").write_text("1\tNIKU\t0.1\tNULL\nEND\n")
    selafin.write_geometry(model / "geometry.slf", X, Y, IKLE, IPOBO, np.zeros(4))
    _steady_result(model, depth=1.0)
    (model / "hotstart2d.cas").write_text("")
    return cfg


def _steady_result(model, *, depth, x=X, y=Y, ikle=IKLE, ipobo=IPOBO):
    path = selafin.write_initial_state(model / "r2d.slf", x, y, ikle, ipobo,
                                       np.full(len(x), depth), double_precision=False)
    later = os.stat(model / "geometry.slf").st_mtime + 10
    os.utime(path, (later, later))
    return path


def test_a_run_lasts_the_residence_time_of_the_reach(cfg):
    """100 m3 of water at 0.01 m3/s is exchanged once in 10,000 s."""
    expected = max(bayescal.HOTSTART_MIN_DURATION,
                   np.ceil(bayescal.HOTSTART_RESIDENCE_TIMES * 10000.0 / 100) * 100)
    assert bayescal.calibration_hotstart_duration(cfg) == pytest.approx(expected)


def test_a_given_duration_wins(cfg):
    cfg.calibration.hotstart_duration = 1234.0
    assert bayescal.calibration_hotstart_duration(cfg) == 1234.0


def test_the_steering_file_continues_from_the_steady_result(cfg):
    cfg.calibration.hotstart_duration = 2000.0
    name = bayescal.write_calibration_hotstart(cfg)
    assert name == bayescal.CALIBRATION_HOTSTART_CAS
    path = cfg.model_path(name)
    values = read_cas_values(path, "PREVIOUS COMPUTATION FILE", "DURATION",
                             "RESULTS FILE", "INITIAL TIME SET TO ZERO",
                             "VARIABLES FOR GRAPHIC PRINTOUTS")
    assert values["PREVIOUS COMPUTATION FILE"] == "r2d.slf"
    assert float(values["DURATION"]) == 2000.0
    assert values["RESULTS FILE"] != "r2d.slf"          # never overwrites its seed
    assert values["INITIAL TIME SET TO ZERO"] == "YES"
    assert "M" in values["VARIABLES FOR GRAPHIC PRINTOUTS"]
    # the built case is left as it was
    assert cfg.model_path("steady2d.cas").read_text() == STEADY


def test_without_a_steady_result_the_calibration_is_refused(cfg):
    cfg.model_path("r2d.slf").unlink()
    with pytest.raises(errors.ConfigError) as caught:
        bayescal.write_calibration_hotstart(cfg)
    assert caught.value.subject == "calibration.hotstart"
    assert "steady simulation" in caught.value.remedy


def test_a_result_of_an_earlier_build_is_refused(cfg):
    earlier = os.stat(cfg.model_path("geometry.slf")).st_mtime - 10
    os.utime(cfg.model_path("r2d.slf"), (earlier, earlier))
    with pytest.raises(errors.ConfigError, match="earlier build"):
        bayescal.write_calibration_hotstart(cfg)


def test_a_result_on_another_mesh_is_refused(cfg):
    x = np.append(X, X[0] + 20.0)
    y = np.append(Y, Y[0])
    _steady_result(cfg.model_dir, depth=1.0, x=x, y=y,
                   ikle=np.vstack([IKLE, [[0, 4, 1]]]), ipobo=np.append(IPOBO, 5))
    with pytest.raises(errors.ConfigError, match="different meshes"):
        bayescal.write_calibration_hotstart(cfg)


def test_a_steady_run_that_had_not_balanced_is_named(cfg, caplog):
    cfg.model_path("hotstart2d.cas").unlink()
    bayescal.write_calibration_hotstart(cfg)
    assert "not balanced" in caplog.text


def test_the_calibration_drives_the_hotstart_steering_file(cfg, monkeypatch, tmp_path):
    """The emitted HydroBayesCal config names calibration2d.cas, and the case's own
    steady2d.cas stays the one a later simulation runs."""
    csv = tmp_path / "points.csv"
    table = pd.DataFrame({"id": [1], "x": [X[0]], "y": [Y[0]], "z": [0.0],
                          "WATER DEPTH_DATA": [1.0], "WATER DEPTH_ERROR": [0.1]})
    table.to_csv(csv, index=False)
    monkeypatch.setattr(bayescal, "build_velocity_csv", lambda cfg, **_: (csv, table))
    monkeypatch.setattr(bayescal, "stage_driver",
                        lambda folder, name, **_: tmp_path / name)
    assert bayescal.run_single_flow_calibration(cfg, prepare_only=True) == 0
    text = cfg.calibration_path(cfg.hbc_config).read_text()
    assert f"'control_file': {bayescal.CALIBRATION_HOTSTART_CAS!r}" in text
    assert "'results_filename_base': 'r2d'" in text
    assert cfg.model_path(bayescal.CALIBRATION_HOTSTART_CAS).is_file()


def test_a_named_steering_file_is_not_replaced(cfg, monkeypatch, tmp_path, caplog):
    cfg.calibration.control_file = "steady2d.cas"
    csv = tmp_path / "points.csv"
    table = pd.DataFrame({"id": [1], "x": [X[0]], "y": [Y[0]], "z": [0.0],
                          "WATER DEPTH_DATA": [1.0], "WATER DEPTH_ERROR": [0.1]})
    table.to_csv(csv, index=False)
    monkeypatch.setattr(bayescal, "build_velocity_csv", lambda cfg, **_: (csv, table))
    monkeypatch.setattr(bayescal, "stage_driver",
                        lambda folder, name, **_: tmp_path / name)
    bayescal.run_single_flow_calibration(cfg, prepare_only=True)
    assert "ignored" in caplog.text
    assert not cfg.model_path(bayescal.CALIBRATION_HOTSTART_CAS).exists()


def test_the_case_editor_offers_the_setting():
    from axqua.core import schema_meta

    assert schema_meta.CURATED["calibration.hotstart"]["kind"] == "bool"
    assert schema_meta.CURATED["calibration.hotstart_duration"]["unit"] == "s"
    assert schema_meta.DISABLED_WHEN["calibration.hotstart_duration"] == [
        {"key": "calibration.hotstart", "in": [False]}]


def test_a_killed_calibration_does_not_bring_back_its_steering_file(cfg):
    """case_as_built() restores what a killed calibration set aside; the steering file
    written for the new calibration must not be among it."""
    stale = cfg.calibration_path(bayescal.AS_BUILT) / bayescal.CALIBRATION_HOTSTART_CAS
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("DURATION : 1.0\n")
    cfg.calibration.hotstart_duration = 2000.0
    name = bayescal.write_calibration_hotstart(cfg)
    with bayescal.case_as_built(cfg):
        values = read_cas_values(cfg.model_path(name), "DURATION")
    assert float(values["DURATION"]) == 2000.0


def test_without_a_discharge_or_a_duration_the_hotstart_is_a_config_error(cfg):
    cfg.boundaries.prescribed_flowrate = None
    cfg.hydrodynamics.duration = None
    with pytest.raises(errors.ConfigError) as caught:
        bayescal.calibration_hotstart_duration(cfg)
    assert caught.value.subject == "calibration.hotstart_duration"
    cfg.hydrodynamics.duration = 900.0
    assert bayescal.calibration_hotstart_duration(cfg) == 900.0
