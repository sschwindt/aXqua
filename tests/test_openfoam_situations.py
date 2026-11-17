"""The OpenFOAM calibration and validation at the discharges of measured situations.

Nothing here runs OpenFOAM, TELEMAC or HydroBayesCal: the TELEMAC runs that give the rigid lid its water surface, the launch of the driver and the solver are replaced, and what is checked is what aXqua decides - which surface each situation follows, which points are compared, what the driver is told, which job does what.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from axqua import openfoam_situations, validation
from axqua.calibration import _resolve_quantity, build_calibration_csv
from axqua.config import CalibrationParameter, ValidationSituation
from axqua.core.errors import ConfigError
from axqua.solvers.openfoam import calibration as ofcal
from test_openfoam_calibration import _cfg, _tidy, _write_tidy
from test_validation import CAS, LIQUIDS, TABLE


@pytest.fixture
def built(fake_case):
    """A built case (the fixture of test_validation.py, which a test module cannot share
    by import without shadowing it)."""
    cfg = fake_case
    cfg.model_path(cfg.cas_file).write_text(CAS)
    cfg.model_path("friction.tbl").write_text(TABLE)
    cfg.model_path(cfg.liquid_boundaries_json).write_text(json.dumps(LIQUIDS))
    (cfg.model_path("user_fortran")).mkdir()
    return cfg


# --------------------------------------------------------------- the measured speed
def test_the_speed_of_a_survey_is_horizontal():
    """The user's decision of 2026-11-17: the FlowTracker components are in the frame of
    the probe, so the horizontal speed is compared, and the vertical component (mostly
    the tilt of the probe) never enters it. It used to inflate U_MAG by 40 % on KB15."""
    df = _tidy()
    df["w"] = [0.5, -0.5, 0.5]
    values, err = _resolve_quantity(df, "U_MAG")
    np.testing.assert_allclose(values, np.hypot(df["u"], df["v"]))
    expected = np.sqrt((df.u * df.u_err) ** 2 + (df.v * df.v_err) ** 2) / np.hypot(df.u, df.v)
    np.testing.assert_allclose(err, expected.fillna(0.0))
    same, _ = _resolve_quantity(df, "SCALAR VELOCITY")
    assert same.equals(values)


def test_a_point_filter_leaves_out_points_before_anything_is_placed(tmp_path):
    cfg = _cfg(tmp_path, quantities=("U_MAG",))
    _write_tidy(cfg, _tidy())
    seen = []

    def resolver(df):
        seen.append(len(df))
        return np.full(len(df), 371.0)

    out = pd.read_csv(build_calibration_csv(
        cfg, path=tmp_path / "m.csv", z_resolver=resolver,
        point_filter=lambda df: df["x"].to_numpy() != 101.0))
    assert out["x"].tolist() == [100.0, 102.0] and out["id"].tolist() == [1, 2]
    assert seen == [2]
    with pytest.raises(ValueError, match="point filter"):
        build_calibration_csv(cfg, path=tmp_path / "n.csv",
                              point_filter=lambda df: np.ones(1, dtype=bool))


# ------------------------------------------------------------------ the situations
def test_a_situation_is_the_case_at_its_discharge_with_its_measurements(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.boundaries.prescribed_flowrate = 54.3
    source = {"category": "hydraulics", "kind": "points", "positions": "a.gpkg"}
    there = ofcal.situation_config(cfg, {"name": "March 2026", "tag": "march-2026",
                                         "discharge": 45.8, "sources": [source]},
                                   tmp_path / "out")
    assert there.boundaries.prescribed_flowrate == 45.8 == there.situation_discharge
    assert there.ground_truth.sources == [source]
    assert there.ground_truth.measurements == tmp_path / "out" / "ground-truth-march-2026.xlsx"
    assert cfg.boundaries.prescribed_flowrate == 54.3 and not cfg.ground_truth.sources
    # a situation without measurements of its own keeps those of the case
    own = ofcal.situation_config(cfg, {"name": "case discharge", "tag": "case-discharge",
                                       "discharge": 54.3, "sources": []}, tmp_path)
    assert own.ground_truth.measurements == cfg.ground_truth.measurements


def test_inflow_lines_with_their_own_flows_keep_their_split(tmp_path):
    from types import SimpleNamespace

    from axqua.solvers.openfoam.case import _inlet_discharges

    mesh = SimpleNamespace(inlet_patches=["in1", "in2"],
                           inlet_discharge={"in1": 1.6, "in2": 0.8})
    cfg = _cfg(tmp_path)
    assert _inlet_discharges(mesh, cfg, 2.4) == {"in1": 1.6, "in2": 0.8}
    cfg.situation_discharge = 4.8
    assert _inlet_discharges(mesh, cfg, 4.8) == pytest.approx({"in1": 3.2, "in2": 1.6})


def test_every_situation_needs_a_water_surface(tmp_path):
    cfg = _cfg(tmp_path, parameters=[("ks", 0.02, 0.3)])
    with pytest.raises(ConfigError, match="no 2D result"):
        ofcal.run_openfoam_situation_calibration(
            cfg, [{"name": "A", "tag": "a", "discharge": 1.0, "sources": [], "seed": None}])
    with pytest.raises(ConfigError, match="same name"):
        ofcal.run_openfoam_situation_calibration(
            cfg, [{"name": "A", "tag": "a", "discharge": 1.0, "seed": None}] * 2)
    with pytest.raises(ConfigError, match="no calibration situations"):
        ofcal.run_openfoam_situation_calibration(cfg, [])


def test_the_driver_is_told_every_situation(tmp_path):
    cfg = _cfg(tmp_path)
    params = [CalibrationParameter(name="ks", min=0.02, max=0.30),
              CalibrationParameter(name="Cmu", min=0.06, max=0.12)]
    flows = [{"name": "august-2026", "case_template_dir": tmp_path / "a" / "case-template",
              "calibration_pts_file_path": tmp_path / "m-a.csv"},
             {"name": "march-2026", "case_template_dir": tmp_path / "m" / "case-template",
              "calibration_pts_file_path": tmp_path / "m-m.csv"}]
    path = ofcal.emit_openfoam_config(
        cfg, template=flows[0]["case_template_dir"], csv=flows[0]["calibration_pts_file_path"],
        parameters=params, calibration_quantities=["U_MAG"],
        extraction_quantities=list(ofcal.SITUATION_EXTRACTION), out_dir=tmp_path / "out",
        flows=flows)
    assert path.name == "config_OpenFOAM_multiflow.py"
    namespace: dict = {}
    exec(compile(path.read_text(), str(path), "exec"), namespace)    # noqa: S102
    assert [f["name"] for f in namespace["multiflow"]["flows"]] == ["august-2026", "march-2026"]
    assert namespace["multiflow"]["flows"][1]["case_template_dir"] == str(flows[1]["case_template_dir"])
    assert namespace["calibration"]["calibration_quantities"] == ["U_MAG"]
    assert "bal_openfoam_multiflow.py" in path.read_text()
    # without flows, the single-discharge config is what it was
    single = ofcal.emit_openfoam_config(
        cfg, template=tmp_path / "t", csv=tmp_path / "c.csv", parameters=params,
        calibration_quantities=["U_x"], extraction_quantities=["U_x"],
        out_dir=tmp_path / "single")
    assert single.name == "config_OpenFOAM.py" and "multiflow" not in single.read_text()


def test_the_situations_are_those_of_the_telemac_calibration(fake_case):
    cfg = fake_case
    assert [s.name for s in openfoam_situations.calibration_situations(cfg)] == ["case discharge"]
    cfg.calibration.situations = [ValidationSituation(name="March 2026", prescribed_flowrate=1.1)]
    names = [s.name for s in openfoam_situations.calibration_situations(cfg)]
    assert names[-1] == "March 2026"
    specs = openfoam_situations.situation_specs(
        cfg, openfoam_situations.calibration_situations(cfg)[-1:],
        {"march-2026": Path("seed.slf")})
    assert specs == [{"name": "March 2026", "tag": "march-2026", "discharge": 1.1,
                      "sources": [], "seed": Path("seed.slf"), "duration": None,
                      "seed_stamp": None}]


def test_the_case_discharge_has_all_the_boundary_values_of_the_case(fake_case):
    """Review 2026-11-23: the situation of a case without situations carried only the total
    discharge, so a case with a prescribed outflow level, or with a discharge per inflow
    line and no total, could not run its TELEMAC water surface."""
    from axqua import bayescal

    cfg = fake_case
    cfg.boundaries.outflow_condition = "elevation"
    cfg.boundaries.prescribed_elevation = 100.2
    (own,) = openfoam_situations.calibration_situations(cfg)
    assert own.prescribed_elevation == 100.2
    assert own == bayescal.case_situation(cfg)
    cfg.boundaries.prescribed_flowrate = None
    cfg.model_path(cfg.liquid_boundaries_json).write_text(json.dumps(LIQUIDS))
    (own,) = openfoam_situations.calibration_situations(cfg)
    assert own.inflows == {1: 0.8, 3: 1.6} and own.total_flowrate == pytest.approx(2.4)


# ---------------------------------------------------- the TELEMAC water surfaces
class _Telemac:
    def __init__(self):
        self.runs = []

    def _launch(self, cfg, *, ctx, cas_file, ncsize, solver, name, duration=None):
        self.runs.append(cas_file)
        stem = cas_file[:-len(".cas")]
        cfg.model_path(f"r2d-{stem}.slf").write_text("result")


def _telemac(monkeypatch):
    from types import SimpleNamespace

    from axqua.core import registry

    backend = _Telemac()
    monkeypatch.setattr(registry, "get", lambda name: SimpleNamespace(load=lambda: backend))
    monkeypatch.setattr(openfoam_situations, "_check_balance", lambda *a: None)
    monkeypatch.setattr("axqua.bayescal.other_calibration_running", lambda model: False)
    return backend


def test_a_water_surface_is_run_once_and_again_when_the_values_change(built, monkeypatch):
    backend = _telemac(monkeypatch)
    situation = ValidationSituation(name="March 2026", prescribed_flowrate=2.0,
                                    prescribed_elevation=100.2)
    first = openfoam_situations.seed_runs(built, [situation], parameters={"zone4": 0.05})
    assert backend.runs == ["openfoam-seed-march-2026.cas"]
    assert first == {"march-2026": built.model_path("r2d-openfoam-seed-march-2026.slf")}
    assert json.loads(first["march-2026"].with_suffix(".json").read_text())[
        "parameters"] == {"zone4": 0.05}
    openfoam_situations.seed_runs(built, [situation], parameters={"zone4": 0.05})
    assert len(backend.runs) == 1, "an unchanged water surface is reused"
    openfoam_situations.seed_runs(built, [situation], parameters={"zone4": 0.07})
    assert len(backend.runs) == 2, "other calibrated values run it again"
    # the built case is untouched
    assert built.model_path(built.cas_file).read_text() == CAS


def test_no_water_surface_is_run_while_a_telemac_calibration_runs(built, monkeypatch):
    _telemac(monkeypatch)
    monkeypatch.setattr("axqua.bayescal.other_calibration_running", lambda model: True)
    with pytest.raises(ConfigError, match="TELEMAC calibration of this case is running"):
        openfoam_situations.seed_runs(built, [ValidationSituation(name="A",
                                                                  prescribed_flowrate=1.0)],
                                      parameters={})


def test_the_seed_run_is_named_for_the_openfoam_model(built):
    setup = validation.build(built, ValidationSituation(name="March 2026",
                                                        prescribed_flowrate=2.0,
                                                        prescribed_elevation=100.2),
                             {"zone4": 0.05}, stem="openfoam-seed", role="calibration")
    assert setup.cas.name == "openfoam-seed-march-2026.cas"
    assert setup.results.name == "r2d-openfoam-seed-march-2026.slf"
    assert setup.friction.name == "friction-openfoam-seed-march-2026.tbl"
    assert "TITLE : 'fakecase openfoam-seed march-2026'" in setup.cas.read_text()


def test_points_near_an_inflow_or_outside_the_model_are_not_compared(fake_case, monkeypatch):
    points = pd.DataFrame({"x": [0.0, 1.0, 2.0, 3.0], "y": [0.0, 0.0, 0.0, 0.0],
                           "id": [7, 8, 9, 10]})
    monkeypatch.setattr("axqua.bayescal._inside_the_model",
                        lambda cfg, p: p.loc[p["x"] != 1.0].reset_index(drop=True)
                        .assign(id=lambda d: range(1, len(d) + 1)))
    monkeypatch.setattr("axqua.bayescal.away_from_liquid_boundaries",
                        lambda cfg, x, y: np.asarray(x) != 3.0)
    keep = openfoam_situations.point_filter(fake_case, points)
    assert keep.tolist() == [True, False, True, False]


# -------------------------------------------------------------------------- the jobs
def test_the_openfoam_calibration_and_validation_are_jobs():
    from axqua.core.capabilities import Capability
    from axqua.jobs.model import (KIND_META, JobKind, OpenFoamCalibrationOptions,
                                  OpenFoamValidationOptions, parse_kind)

    for kind, alias in ((JobKind.OPENFOAM_CALIBRATION, "of-bal"),
                        (JobKind.OPENFOAM_VALIDATION, "of-val")):
        meta = KIND_META[kind]
        assert (meta.solver, meta.capability, meta.verb) == ("openfoam",
                                                             Capability.CALIBRATION, "study")
        assert parse_kind(alias) is kind is parse_kind(kind.value)
    assert OpenFoamValidationOptions.from_dict({"situation": "November 2025"}).validate
    assert not OpenFoamCalibrationOptions().smoke


def test_the_job_prepares_its_water_surfaces_above_both_backends(fake_case, monkeypatch):
    from types import SimpleNamespace

    from axqua.jobs import executor
    from axqua.jobs.model import (JobKind, OpenFoamCalibrationOptions,
                                  OpenFoamValidationOptions)

    calls = []
    monkeypatch.setattr(openfoam_situations, "prepare",
                        lambda cfg, **kw: calls.append(kw) or {"situations": ["s"]})
    ctx = SimpleNamespace(sink=SimpleNamespace(step=lambda *a, **k: None))
    for kind, options in ((JobKind.OPENFOAM_CALIBRATION, OpenFoamCalibrationOptions()),
                          (JobKind.OPENFOAM_VALIDATION,
                           OpenFoamValidationOptions(situation="November 2025", ncsize=4))):
        spec = SimpleNamespace(kind=kind, options=options,
                               resources=SimpleNamespace(mpi_processes=16))
        assert executor._pre_step(spec, fake_case, ctx) == {"situations": ["s"]}
    assert [c["validate"] for c in calls] == [False, True]
    assert calls[1]["situation"] == "November 2025"
    # the 16 processes of the job are OpenFOAM's: TELEMAC takes the case's count
    # (None) unless the job names its own (review 2026-11-23)
    assert calls[0]["ncsize"] is None and calls[1]["ncsize"] == 4


def test_the_backend_routes_the_calibration_and_the_validation(tmp_path, monkeypatch):
    from axqua.core.capabilities import Capability
    from axqua.solvers.openfoam import backend as module
    from axqua.solvers.openfoam import validation as ofval

    seen = {}
    monkeypatch.setattr(ofcal, "run_openfoam_situation_calibration",
                        lambda cfg, situations, **kw: seen.update(cal=(situations, kw)) or 0)
    monkeypatch.setattr(ofval, "run_validation",
                        lambda backend, cfg, situation, **kw: seen.update(val=(situation, kw)))
    cfg = _cfg(tmp_path)
    backend = module.BACKEND
    backend.study(cfg, Capability.CALIBRATION, situations=["a", "b"], smoke=True,
                  point_filter="f", ncsize=16)
    assert seen["cal"][0] == ["a", "b"]
    assert seen["cal"][1]["launch_mode"] == "smoke" and seen["cal"][1]["point_filter"] == "f"
    backend.study(cfg, Capability.CALIBRATION, validate=True, situations=["nov"],
                  values={"ks": 0.1}, situation="nov", free_surface=True)
    assert seen["val"][0] == "nov" and seen["val"][1]["values"] == {"ks": 0.1}
    assert seen["val"][1]["free_surface"] is True


def test_a_target_outside_the_water_of_the_lattice_is_left_out(tmp_path, monkeypatch, caplog):
    """HydroBayesCal reads the nearest cell with no distance limit, so a target on a
    shallow margin that the coarse lattice dropped would be compared with a cell meters
    away. KB15, 2026-11-17: 13 of the 33 August points on the 3.55 m lattice."""
    cfg = _cfg(tmp_path, quantities=("U_MAG",))
    _write_tidy(cfg, _tidy())
    monkeypatch.setattr(ofcal, "model_relative_z_resolver",
                        lambda *a, **k: (lambda df: np.array([370.3, np.nan, 370.5])))
    with caplog.at_level("WARNING", logger="axqua"):
        path = ofcal._velocity_csv(cfg, cfg, out_dir=tmp_path, calibration_quantities=["U_MAG"],
                                   extraction_quantities=["U_MAG"], vel_err_floor=0.01,
                                   vertical_err_floor=0.03, path=tmp_path / "m.csv")
    table = pd.read_csv(path)
    assert table["x"].tolist() == [100.0, 102.0] and table["id"].tolist() == [1, 2]
    assert table["z"].tolist() == [370.3, 370.5]
    assert "1 of 3 measurement points lie outside the water" in caplog.text


# ------------------------------------------- refused before the TELEMAC runs (review 2026-11-23)
def _no_seeds(monkeypatch):
    def seed_runs(*args, **kwargs):
        raise AssertionError("a TELEMAC water surface was run before the job was refused")

    monkeypatch.setattr(openfoam_situations, "seed_runs", seed_runs)


def test_a_validation_without_an_openfoam_calibration_runs_no_telemac(fake_case, monkeypatch):
    _no_seeds(monkeypatch)
    fake_case.calibration.validation = [ValidationSituation(name="November 2025",
                                                            prescribed_flowrate=1.0)]
    with pytest.raises(ConfigError):
        openfoam_situations.prepare(fake_case, validate=True, situation="November 2025")


def test_a_calibration_the_installed_hydrobayescal_cannot_run_runs_no_telemac(fake_case,
                                                                            monkeypatch):
    _no_seeds(monkeypatch)
    monkeypatch.setattr("axqua.hbc.hbc_version", lambda: "1.13.0")
    with pytest.raises(ConfigError, match="cannot calibrate OpenFOAM at several discharges"):
        openfoam_situations.prepare(fake_case)
    monkeypatch.setattr("axqua.hbc.hbc_version", lambda: "1.15.0")
    fake_case.calibration.situations = [ValidationSituation(name="March 2026",
                                                            prescribed_flowrate=1.0),
                                        ValidationSituation(name="march 2026",
                                                            prescribed_flowrate=2.0)]
    with pytest.raises(ConfigError, match="same name"):
        openfoam_situations.prepare(fake_case)


def test_a_split_among_the_inflow_lines_the_openfoam_model_cannot_follow_is_refused(
        built, monkeypatch):
    """The OpenFOAM inlets take the Target flow of their lines scaled to the total, so a
    situation's own discharges per line are followed only in those proportions."""
    _no_seeds(monkeypatch)
    monkeypatch.setattr("axqua.hbc.hbc_version", lambda: "1.15.0")
    same = ValidationSituation(name="A", inflows={1: 1.6, 3: 3.2})
    openfoam_situations._check_inflows(built, [same], "calibration")
    other = ValidationSituation(name="B", inflows={1: 3.0, 3: 2.0})
    built.calibration.situations = [other]
    with pytest.raises(ConfigError, match="other proportions"):
        openfoam_situations.prepare(built)


# ------------------------------------------------------- what the template was built from
def test_a_template_built_from_another_water_surface_is_rebuilt(tmp_path):
    folder = tmp_path / "march-2026"
    situation = {"name": "March 2026", "seed": "seed.slf", "seed_stamp": "1 {}",
                 "discharge": 45.8}
    assert not ofcal._template_outdated(folder, situation, 4.0), "nothing built yet"
    faces = folder / "case-template" / "constant" / "polyMesh" / "faces"
    faces.parent.mkdir(parents=True)
    faces.write_text("")
    assert ofcal._template_outdated(folder, situation, 4.0), "built before the record"
    ofcal._template_record(folder).write_text(
        json.dumps(ofcal._template_source(situation, 4.0)))
    assert not ofcal._template_outdated(folder, situation, 4.0)
    assert ofcal._template_outdated(folder, situation, 3.0)
    newer = dict(situation, seed_stamp="2 {}")
    assert ofcal._template_outdated(folder, newer, 4.0)
    with pytest.raises(SystemExit, match="cannot resume"):
        ofcal._template_outdated(folder, newer, 4.0, resume=True)


def test_the_seed_stamp_changes_with_every_new_seed_run(tmp_path):
    import os

    seed = tmp_path / "r2d-openfoam-seed-a.slf"
    assert openfoam_situations._seed_stamp(None) is None
    assert openfoam_situations._seed_stamp(seed) is None
    seed.write_text("result")
    seed.with_suffix(".json").write_text('{"parameters": {}}')
    first = openfoam_situations._seed_stamp(seed)
    os.utime(seed, ns=(seed.stat().st_mtime_ns + 10**9,) * 2)
    assert openfoam_situations._seed_stamp(seed) != first


def test_a_situation_derives_its_own_outflow_rating(tmp_path, monkeypatch):
    """As the TELEMAC run of the situation does (validation.with_boundaries_of): a case
    without a measured curve derives the level for the situation's own discharge."""
    made = []
    monkeypatch.setattr("axqua.workflow.synthesize_rating_if_missing",
                        lambda cfg, q: made.append((cfg.boundaries.stage_discharge, q)))
    cfg = _cfg(tmp_path)
    cfg.boundaries.outflow_condition = "stage_discharge"
    there = ofcal.situation_config(cfg, {"name": "March 2026", "tag": "march-2026",
                                         "discharge": 45.8}, tmp_path / "out")
    assert made == [(tmp_path / "out" / "rating-march-2026.csv", 45.8)]
    assert there.boundaries.stage_discharge == tmp_path / "out" / "rating-march-2026.csv"
    assert cfg.boundaries.stage_discharge is None, "the case is untouched"
    measured = tmp_path / "measured.csv"
    measured.write_text("Q,h\n10,370\n60,371\n")
    cfg.boundaries.stage_discharge = measured
    ofcal.situation_config(cfg, {"name": "A", "tag": "a", "discharge": 45.8}, tmp_path)
    assert len(made) == 1, "a measured curve is used as it is"


def test_the_log_names_the_folder_of_the_calibration():
    source = Path("cal/openfoam/multiflow/auto-saved-results-HydroBayesCal/calibration-data/"
                  "U_MAG/BAL_dictionary.pkl")
    assert openfoam_situations._campaign(source) == "multiflow"


def test_a_validation_compares_at_fewer_than_six_points(tmp_path, monkeypatch):
    """Six points is the floor of a calibration; a validation compares at what it has."""
    from axqua.solvers.openfoam import validation as ofval

    seen = {}

    class Placed(Exception):
        pass

    def velocity_csv(*args, **kwargs):
        seen.update(kwargs)
        raise Placed

    cfg = _cfg(tmp_path, parameters=[("ks", 0.02, 0.3)])
    seed = tmp_path / "seed.slf"
    seed.write_text("")
    monkeypatch.setattr(ofcal, "_velocity_csv", velocity_csv)
    monkeypatch.setattr("axqua.solvers.openfoam.hotstart.load_hotstart", lambda cfg, s: None)
    case = (Path(cfg.calibration_dir) / "openfoam" / "validation" / "nov-calibration-settings"
            / "case" / "constant" / "polyMesh")
    case.mkdir(parents=True)
    with pytest.raises(Placed):
        ofval.run_validation(None, cfg, {"name": "Nov", "tag": "nov", "discharge": 1.0,
                                         "seed": seed}, values={"ks": 0.1}, compare_only=True)
    assert seen["min_points"] == 1
