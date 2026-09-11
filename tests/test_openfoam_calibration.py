"""OpenFOAM calibration: quantity mapping, parameter filtering, template guards.

None of these need OpenFOAM, HydroBayesCal or a solver: they exercise the parts
axqua owns - what goes into the calibration CSV, which parameters are allowed
through, what the emitted HydroBayesCal config says, and the template assertions
that are meant to fire *before* a campaign starts rather than hours into it.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from axqua.calibration import _resolve_quantity, build_calibration_csv
from axqua.solvers.openfoam import calibration as ofcal


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _tidy() -> pd.DataFrame:
    """A FlowTracker-shaped hydraulics table: the KB15 column set exactly."""
    return pd.DataFrame({
        "x": [100.0, 101.0, 102.0],
        "y": [200.0, 201.0, 202.0],
        "z": [370.0, 370.1, 370.2],
        "u": [1.0, 0.8, 0.5],
        "v": [0.2, 0.1, 0.0],
        # w is small and its measured error is zero - the case the floor exists for
        "w": [0.01, -0.02, 0.0],
        "u_err": [0.10, 0.08, 0.05],
        "v_err": [0.02, 0.01, 0.00],
        "w_err": [0.00, 0.00, 0.00],
        "h": [0.50, 0.45, 0.40],
    })


def _cfg(tmp_path, quantities=("U_x", "U_y", "U_z"), parameters=()):
    from axqua.config import (Boundaries, Calibration, CalibrationParameter, Config,
                              Friction, Geodata, GroundTruth, Hydrodynamics,
                              Initialization, MeshConfig, Morphodynamics, OpenFoam,
                              TelemacEnv)

    params = [CalibrationParameter(name=n, min=lo, max=hi) for n, lo, hi in parameters]
    dummy = tmp_path / "dummy"
    dummy.write_text("")
    return Config(
        name="test", crs_epsg=25832, config_dir=tmp_path,
        preprocessing_dir=tmp_path / "pre", model_dir=tmp_path / "sim",
        postprocessing_dir=tmp_path / "post", calibration_dir=tmp_path / "cal",
        telemac=TelemacEnv(pysource=dummy),
        geodata=Geodata(dem_initial=dummy, boundary=dummy), boundaries=Boundaries(),
        initialization=Initialization(), mesh=MeshConfig(), friction=Friction(),
        hydrodynamics=Hydrodynamics(), morphodynamics=Morphodynamics(),
        ground_truth=GroundTruth(), openfoam=OpenFoam(),
        calibration=Calibration(calibration_quantities=list(quantities),
                                parameters=params),
    )


# --------------------------------------------------------------------------- #
# quantities
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("qty,column", [("U_x", "u"), ("U_y", "v"), ("U_z", "w")])
def test_component_velocities_map_to_the_tidy_columns(qty, column):
    df = _tidy()
    values, err = _resolve_quantity(df, qty)
    assert list(values) == list(df[column])
    assert list(err) == list(df[f"{column}_err"])


def test_u_mag_and_scalar_velocity_are_one_implementation():
    """They are the same quantity under two solvers' names; the error propagation
    is the part that would be easy to get subtly different."""
    df = _tidy()
    a_val, a_err = _resolve_quantity(df, "U_MAG")
    b_val, b_err = _resolve_quantity(df, "SCALAR VELOCITY")
    assert a_val.equals(b_val)
    assert a_err.equals(b_err)


def test_tke_resolves_only_when_measured():
    df = _tidy()
    assert _resolve_quantity(df, "TKE") is None
    df["tke"] = [0.01, 0.02, 0.03]
    values, _ = _resolve_quantity(df, "TKE")
    assert list(values) == [0.01, 0.02, 0.03]


def _write_tidy(cfg, df):
    """Write the tidy table and point the config at it as user-supplied ground truth."""
    path = cfg.preprocessing_dir / "ground-truth.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path) as writer:
        df.to_excel(writer, sheet_name="hydraulics", index=False)
    cfg.ground_truth.measurements = path


def test_csv_columns_keep_the_exact_openfoam_spelling(tmp_path):
    """OpenFOAMModel validates calibration_quantities case-SENSITIVELY, so the
    spelling the caller passes has to survive into the header."""
    cfg = _cfg(tmp_path)
    _write_tidy(cfg, _tidy())
    out = build_calibration_csv(cfg, path=tmp_path / "m.csv")
    header = pd.read_csv(out).columns.tolist()
    assert header[:4] == ["id", "x", "y", "z"]
    for qty in ("U_x", "U_y", "U_z"):
        assert f"{qty}_DATA" in header and f"{qty}_ERROR" in header


def test_error_floor_lifts_only_the_floored_quantity(tmp_path):
    """A measured w_err of 0 gives U_z a near-zero variance, which lets the least
    trustworthy component dominate the whole likelihood."""
    cfg = _cfg(tmp_path)
    _write_tidy(cfg, _tidy())

    bare = pd.read_csv(build_calibration_csv(cfg, path=tmp_path / "bare.csv"))
    assert (bare["U_z_ERROR"] == 0.0).all()          # the problem, reproduced

    floored = pd.read_csv(build_calibration_csv(
        cfg, floors={"U_z": 0.03, "U_x": 0.01}, path=tmp_path / "floored.csv"))
    assert (floored["U_z_ERROR"] == 0.03).all()
    # U_x's measured errors are all above its floor, so they are untouched
    assert list(floored["U_x_ERROR"]) == list(bare["U_x_ERROR"])


def test_telemac_csv_is_unchanged_without_floors(tmp_path):
    """The floors argument must not perturb the existing TELEMAC output."""
    cfg = _cfg(tmp_path, quantities=("WATER DEPTH", "SCALAR VELOCITY"))
    _write_tidy(cfg, _tidy())
    a = build_calibration_csv(cfg, path=tmp_path / "a.csv").read_text()
    b = build_calibration_csv(cfg, floors=None, path=tmp_path / "b.csv").read_text()
    assert a == b


# --------------------------------------------------------------------------- #
# parameters
# --------------------------------------------------------------------------- #
def test_telemac_parameters_are_dropped_with_a_warning(tmp_path, caplog):
    """merged_parameters() also returns roughness zones and .cas keywords; the
    OpenFOAM binding rejects them *after* the design has been sampled."""
    cfg = _cfg(tmp_path, parameters=[("zone1", 0.05, 0.5),
                                     ("VELOCITY DIFFUSIVITY", 1e-3, 5e-2),
                                     ("ks", 0.02, 0.30),
                                     ("Cmu", 0.06, 0.12)])
    with caplog.at_level("WARNING"):
        picked = ofcal.openfoam_parameters(cfg)
    assert sorted(p.name for p in picked) == ["Cmu", "ks"]
    assert "zone1" in caplog.text and "VELOCITY DIFFUSIVITY" in caplog.text


def test_spelling_is_canonicalised(tmp_path):
    """HydroBayesCal matches case-insensitively but writes the name straight into
    the OpenFOAM dictionary, where sigmaeps is not sigmaEps."""
    cfg = _cfg(tmp_path, parameters=[("sigmaeps", 1.0, 1.5), ("KS", 0.02, 0.3)])
    assert sorted(p.name for p in ofcal.openfoam_parameters(cfg)) == ["ks", "sigmaEps"]


def test_no_legal_parameter_explains_what_to_write(tmp_path):
    cfg = _cfg(tmp_path, parameters=[("zone1", 0.05, 0.5)])
    with pytest.raises(SystemExit) as excinfo:
        ofcal.openfoam_parameters(cfg)
    assert "name: ks" in str(excinfo.value)


def test_catalog_openfoam_entries_round_trip_to_routable_names():
    from axqua.solvers.openfoam.spec import OPENFOAM_PARAMETERS
    from axqua.targets import PARAMETER_CATALOG

    entries = [s for s in PARAMETER_CATALOG if s.module == "openfoam"]
    assert entries, "the catalog lost its OpenFOAM group"
    for spec in entries:
        assert spec.hbc_name(None).lower() in OPENFOAM_PARAMETERS
    # C3 is deliberately absent: OF9's kEpsilon has no such coefficient, so it
    # would raise on the first run, after the design had been sampled
    assert not any(s.keyword == "C3" for s in entries)


def test_telemac_catalog_entries_are_untouched():
    from axqua.targets import PARAMETER_CATALOG

    friction = [s for s in PARAMETER_CATALOG if s.module == "friction"]
    assert friction and friction[0].hbc_name(3) == "zone3"
    gaia = [s for s in PARAMETER_CATALOG if s.module == "gaia"]
    assert gaia and gaia[0].hbc_name(1).startswith("gaia")


# --------------------------------------------------------------------------- #
# turbulence + dictionaries
# --------------------------------------------------------------------------- #
def test_kepsilon_coefficient_needs_the_kepsilon_closure(tmp_path):
    from axqua.config import CalibrationParameter

    cfg = _cfg(tmp_path)
    cfg.openfoam.turbulence = "kOmegaSST"
    coeff = [CalibrationParameter(name="Cmu", min=0.06, max=0.12)]
    with pytest.raises(SystemExit) as excinfo:
        ofcal.check_turbulence(cfg, coeff)
    assert "turbulence: kEpsilon" in str(excinfo.value)
    # ks alone is fine under any closure - it is a wall BC, not a coefficient
    ofcal.check_turbulence(cfg, [CalibrationParameter(name="ks", min=0.02, max=0.3)])


def test_momentum_transport_emits_coefficients_only_for_kepsilon(tmp_path):
    from axqua.solvers.openfoam.dicts import momentum_transport

    cfg = _cfg(tmp_path)
    cfg.openfoam.turbulence = "kEpsilon"
    text = momentum_transport(cfg)
    assert "kEpsilonCoeffs" in text
    for key in ("Cmu", "C1", "C2", "sigmak", "sigmaEps"):
        assert re.search(rf"^\s*{key}\s+[0-9.]+;", text, re.M), key

    # a kOmegaSST case must be byte-for-byte what it always was
    cfg.openfoam.turbulence = "kOmegaSST"
    assert "kEpsilonCoeffs" not in momentum_transport(cfg)


def test_uniform_bed_ks_replaces_the_per_face_list(tmp_path):
    """HydroBayesCal rewrites one 'Ks' line and cannot skip a multi-line list."""
    from axqua.solvers.openfoam.fields import _wall_entries

    cfg = _cfg(tmp_path)

    class _Mesh:
        bed_ks = np.array([0.1, 0.2, 0.3])

    listed = _wall_entries(_Mesh(), cfg, "bed")["Ks"]
    assert listed.startswith("nonuniform")

    uniform = _wall_entries(_Mesh(), cfg, "bed", uniform=True)["Ks"]
    assert uniform.startswith("uniform")


# --------------------------------------------------------------------------- #
# the emitted config
# --------------------------------------------------------------------------- #
def _emit(tmp_path, **kw):
    from axqua.config import CalibrationParameter

    cfg = _cfg(tmp_path)
    cfg.openfoam.turbulence = "kEpsilon"
    params = [CalibrationParameter(name="ks", min=0.02, max=0.30),
              CalibrationParameter(name="Cmu", min=0.06, max=0.12)]
    return ofcal.emit_openfoam_config(
        cfg, template=tmp_path / "case-template", csv=tmp_path / "m.csv",
        parameters=params, calibration_quantities=["U_x", "U_y", "U_z"],
        extraction_quantities=["U_x", "U_y", "U_z", "TKE"],
        out_dir=tmp_path / "out", **kw).read_text()


def test_emitted_config_uses_the_openfoam_schema(tmp_path):
    """Not the TELEMAC one: different block names and a different home for n_cpus."""
    text = _emit(tmp_path)
    assert "simulation = {" in text
    assert "interfoam = {" in text
    assert "hydrodynamic_simulation" not in text
    sampling = text[text.index("sampling = {"):text.index("execution = {")]
    assert "'n_cpus'" in sampling, "n_cpus belongs inside sampling for OpenFOAM"


def test_emitted_config_names_the_quantities_and_template(tmp_path):
    text = _emit(tmp_path)
    flat = re.sub(r"[ \t]+", " ", text)
    assert "'calibration_quantities': ['U_x', 'U_y', 'U_z']" in flat
    assert "'extraction_quantities': ['U_x', 'U_y', 'U_z', 'TKE']" in flat
    assert "'parameters': ['ks', 'Cmu']" in flat
    assert "case-template" in text


def test_smoke_and_resume_switch_the_execution_block(tmp_path):
    assert "'only_bal_mode':          False" in _emit(tmp_path)
    assert "'only_bal_mode':          True" in _emit(tmp_path, only_bal_mode=True)


# --------------------------------------------------------------------------- #
# template guards - each must fire before the campaign starts
# --------------------------------------------------------------------------- #
def _template(tmp_path, *, ks="uniform 0.05", coeffs=True, start="startTime",
              extra_time=None):
    case = tmp_path / "tmpl"
    (case / "0").mkdir(parents=True)
    (case / "constant").mkdir()
    (case / "system").mkdir()
    (case / "0" / "nut").write_text(
        "boundaryField{ bed { type nutkRoughWallFunction;\n"
        "        Ks              " + ks + ";\n        Cs uniform 0.5; } }\n")
    body = "RAS\n{\n    model kEpsilon;\n"
    if coeffs:
        body += ("    kEpsilonCoeffs\n    {\n        Cmu             0.09;\n"
                 "        C1              1.44;\n    }\n")
    (case / "constant" / "momentumTransport").write_text(body + "}\n")
    (case / "system" / "controlDict").write_text(
        f"startFrom       {start};\nendTime         600;\n")
    (case / "system" / "decomposeParDict").write_text("numberOfSubdomains 8;\n")
    if extra_time:
        (case / extra_time).mkdir()
    return case


def _params(*names):
    from axqua.config import CalibrationParameter
    return [CalibrationParameter(name=n, min=0.0, max=1.0) for n in names]


def test_template_rejects_a_per_face_ks(tmp_path):
    cfg = _cfg(tmp_path)
    case = _template(tmp_path, ks="nonuniform List<scalar>\n3\n(\n0.1\n0.2\n0.3\n)")
    with pytest.raises(SystemExit, match="per-face bed Ks"):
        ofcal._assert_template(case, cfg, _params("ks"), 5)


def test_template_rejects_a_missing_coefficient_block(tmp_path):
    cfg = _cfg(tmp_path)
    case = _template(tmp_path, coeffs=False)
    with pytest.raises(SystemExit, match="kEpsilonCoeffs"):
        ofcal._assert_template(case, cfg, _params("Cmu"), 5)


def test_template_rejects_a_coefficient_that_is_not_in_the_file(tmp_path):
    """OpenFOAM falls back to its built-in value for a coefficient it cannot find,
    so perturbing an absent one is a silent no-op."""
    cfg = _cfg(tmp_path)
    case = _template(tmp_path)          # has Cmu and C1, but not sigmaEps
    with pytest.raises(SystemExit, match="no 'sigmaEps' line"):
        ofcal._assert_template(case, cfg, _params("sigmaEps"), 5)


def test_template_rejects_too_few_write_times(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.openfoam.end_time, cfg.openfoam.write_interval = 600.0, 200.0   # 3 writes
    with pytest.raises(SystemExit, match="averages the last 5"):
        ofcal._assert_template(_template(tmp_path), cfg, _params("ks"), 5)


def test_template_rejects_a_stray_result_directory(tmp_path):
    """A leftover time directory would be copied into every run and averaged in."""
    cfg = _cfg(tmp_path)
    cfg.openfoam.end_time, cfg.openfoam.write_interval = 600.0, 20.0
    case = _template(tmp_path, extra_time="300")
    with pytest.raises(SystemExit, match="result time director"):
        ofcal._assert_template(case, cfg, _params("ks"), 5)


def test_template_accepts_a_well_formed_case(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.openfoam.end_time, cfg.openfoam.write_interval = 600.0, 20.0
    ofcal._assert_template(_template(tmp_path), cfg, _params("ks", "Cmu"), 5)


def test_campaign_config_does_not_disturb_the_caller(tmp_path):
    """The caller's config still describes the production case the verification
    run rebuilds."""
    cfg = _cfg(tmp_path)
    cfg.openfoam.cell_size = 0.5
    campaign = ofcal.campaign_config(cfg, cell_size_factor=4.0, end_time=600.0,
                                     write_interval=20.0, n_processors=8)
    assert campaign.openfoam.mode == "rigid-lid"
    assert campaign.openfoam.turbulence == "kEpsilon"
    assert campaign.openfoam.spinup_time == 0.0     # no interface to settle
    assert campaign.openfoam.purge_write == 6       # n_avg_timesteps + 1
    # unchanged
    assert cfg.openfoam.mode == "vof"
    assert cfg.openfoam.turbulence == "kOmegaSST"
    assert cfg.openfoam.spinup_time == 30.0


# --------------------------------------------------------------------------- #
# backend + report
# --------------------------------------------------------------------------- #
def test_final_imbalance_is_the_settled_value():
    from axqua.solvers.openfoam.report import STEADY_WINDOW, DischargeHistory

    n = 40
    inflow = np.full(n, 2.0)
    outflow = np.concatenate([np.full(n - STEADY_WINDOW, 1.0),
                              np.full(STEADY_WINDOW, 1.98)])
    history = DischargeHistory(time=np.arange(n, dtype=float),
                               inflow=inflow, outflow=outflow)
    assert history.final_imbalance == pytest.approx(0.01, abs=1e-9)


def test_final_imbalance_is_none_when_nothing_was_recorded():
    """None is not zero: a caller ranking runs must tell 'balanced' from 'never
    reported'."""
    from axqua.solvers.openfoam.report import DischargeHistory

    assert DischargeHistory(time=np.zeros(0)).final_imbalance is None


def test_extract_objective_returns_the_imbalance(tmp_path, monkeypatch):
    from axqua.solvers.openfoam import backend as ofbackend
    from axqua.solvers.openfoam import report as ofreport

    history = ofreport.DischargeHistory(
        time=np.arange(20, dtype=float), inflow=np.full(20, 4.0),
        outflow=np.full(20, 3.8))
    monkeypatch.setattr(ofreport, "analyse", lambda *a, **k: history)
    value = ofbackend.OpenFoamBackend().extract_objective(_cfg(tmp_path))
    assert value == pytest.approx(0.05)


# --------------------------------------------------------------------------- #
# capability wiring
# --------------------------------------------------------------------------- #
def test_calibration_is_a_supported_capability():
    from axqua.core.capabilities import Capability, Support
    from axqua.solvers.openfoam.spec import SPEC

    assert SPEC.capabilities[Capability.CALIBRATION].support is Support.SUPPORTED


def test_calibration_is_configured_only_with_a_routable_parameter(tmp_path):
    from axqua.solvers.openfoam.spec import _calibration_configured

    cfg = _cfg(tmp_path, parameters=[("zone1", 0.05, 0.5)])
    cfg.declared_blocks = {"openfoam"}
    assert not _calibration_configured(cfg)          # TELEMAC-only parameters

    cfg = _cfg(tmp_path, parameters=[("ks", 0.02, 0.3)])
    cfg.declared_blocks = {"openfoam"}
    assert _calibration_configured(cfg)

    cfg = _cfg(tmp_path, parameters=[("ks", 0.02, 0.3)])
    cfg.declared_blocks = set()                       # no openfoam block
    assert not _calibration_configured(cfg)


def test_calibration_built_and_run_are_path_checks(tmp_path):
    from axqua.solvers.openfoam.spec import _calibration_built, _calibration_run

    cfg = _cfg(tmp_path)
    assert not _calibration_built(cfg) and not _calibration_run(cfg)

    root = tmp_path / "cal" / "openfoam"
    (root / "case-template" / "constant" / "polyMesh").mkdir(parents=True)
    (root / "case-template" / "constant" / "polyMesh" / "faces").write_text("")
    (root / "config_OpenFOAM.py").write_text("")
    assert _calibration_built(cfg)

    restart = root / "auto-saved-results-HydroBayesCal" / "restart_data"
    restart.mkdir(parents=True)
    (restart / "initial-model-outputs.json").write_text("{}")
    assert _calibration_run(cfg)


# --------------------------------------------------------------------------- #
# the two solvers share one calibration.parameters list
# --------------------------------------------------------------------------- #
def test_the_two_solvers_filter_each_other_out(tmp_path):
    """A case calibrating both codes declares both parameter sets in one list.

    Each emitter must take only its own: an OpenFOAM ``ks`` emitted into
    ``config_Telemac.py`` would have HydroBayesCal rewriting a .cas keyword that
    does not exist for the whole campaign, and a TELEMAC ``zone1`` reaching the
    OpenFOAM binding is rejected only after the design has been sampled.
    """
    from axqua.calibration import telemac_parameters

    cfg = _cfg(tmp_path, parameters=[("VELOCITY DIFFUSIVITY", 1e-3, 5e-2),
                                     ("ks", 0.02, 0.30),
                                     ("Cmu", 0.06, 0.12)])
    assert [p.name for p in telemac_parameters(cfg)] == ["VELOCITY DIFFUSIVITY"]
    assert sorted(p.name for p in ofcal.openfoam_parameters(cfg)) == ["Cmu", "ks"]


def test_a_roughness_zone_stays_with_telemac(tmp_path):
    """zone<N> is TELEMAC's own roughness parameter and must survive the filter -
    only names that are exclusively OpenFOAM's are removed."""
    from axqua.calibration import telemac_parameters

    cfg = _cfg(tmp_path, parameters=[("zone1", 0.05, 0.5), ("ks", 0.02, 0.30)])
    assert [p.name for p in telemac_parameters(cfg)] == ["zone1"]


# --------------------------------------------------------------------------- #
# the discharge balance means different things in the two modes
# --------------------------------------------------------------------------- #
def _steady_history(rigid: bool):
    from axqua.solvers.openfoam.report import DischargeHistory

    n = 40
    return DischargeHistory(
        time=np.arange(n, dtype=float), inflow=np.full(n, 47.3),
        outflow=np.full(n, 47.3), target=47.3, tolerance=1e-3,
        converged=True, steady_time=2.14, rigid_lid=rigid)


def test_a_rigid_lid_balance_is_reported_as_structural_not_converged():
    """Under a rigid lid the water volume is FIXED, so inflow equals outflow at
    every step whatever the flow is doing. Measured on inn-KB15: the balance
    'converged' at t=2.14 s of a 600 s run, while the velocity field was still
    fluctuating 1.2% per write. Reporting that as convergence is false comfort."""
    lines = " ".join(_steady_history(rigid=True).lines())
    assert "RIGID LID" in lines and "structural" in lines
    assert "VELOCITY FIELD" in lines
    assert "CONVERGED at t" not in lines


def test_a_two_phase_balance_still_reports_convergence():
    """With a free surface the water volume CAN change, so the balance is a real
    measurement and the existing verdict stands."""
    lines = " ".join(_steady_history(rigid=False).lines())
    assert "CONVERGED at t = 2.14 s" in lines
    assert "RIGID LID" not in lines


def test_analyse_takes_the_mode_from_the_config(tmp_path, monkeypatch):
    from axqua.solvers.openfoam import report

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(report, "read_monitors", lambda case_dir: {})
    cfg.openfoam.mode = "rigid-lid"
    assert report.analyse(cfg, tmp_path).rigid_lid is True
    cfg.openfoam.mode = "vof"
    assert report.analyse(cfg, tmp_path).rigid_lid is False


# --------------------------------------------------------------------------- #
# model-relative target placement
# --------------------------------------------------------------------------- #
def test_relative_height_reads_the_flowtracker_convention():
    """'0.6 depth' is measured DOWN FROM THE SURFACE, so the probe sits at 0.4*h
    ABOVE the bed. The real KB15 vertical 1501: MeasD 0.401 of FinalD 0.742."""
    from axqua.ground_truth import DEFAULT_RELATIVE_HEIGHT, relative_height

    f, source = relative_height(pd.DataFrame({"h": [0.742], "h_meas": [0.401]}))
    assert f.iloc[0] == pytest.approx(0.4596, abs=1e-4)
    assert float(f.iloc[0] * 0.742) == pytest.approx(0.341, abs=1e-3)  # the height
    assert "MeasD" in source
    # the fallback is 0.4, not 0.6 - the surface-vs-bed convention
    assert DEFAULT_RELATIVE_HEIGHT == 0.4


def test_measd_and_finald_no_longer_collide():
    """Both used to alias to 'h', so df['h'] silently became a DataFrame."""
    from axqua.ground_truth import _canonical_columns

    out = _canonical_columns(pd.DataFrame({"MeasD": [0.4], "FinalD": [0.74]}))
    assert list(out.columns) == ["h_meas", "h"] or set(out.columns) == {"h", "h_meas"}
    assert isinstance(out["h"], pd.Series)


def test_model_column_places_targets_inside_its_own_column():
    from axqua.model_column import ModelColumn

    col = ModelColumn(x=np.zeros(3), y=np.zeros(3),
                      bed=np.array([100.0, 100.0, 100.0]),
                      depth=np.array([1.0, 0.5, 0.0]),
                      inside=np.array([True, True, True]), source="test")
    z, clamped = col.z_at(np.array([0.4, 0.4, 0.4]))
    assert z[0] == pytest.approx(100.4) and z[1] == pytest.approx(100.2)
    assert not clamped.any()
    # a dry column is not usable, whatever the fraction
    assert list(col.usable(min_depth=0.05)) == [True, True, False]


def test_a_fraction_outside_the_margin_is_clamped_and_reported():
    """A target ON the bed sits in the wall-function layer, where the velocity is a
    boundary condition rather than a result."""
    from axqua.model_column import ModelColumn

    col = ModelColumn(x=np.zeros(2), y=np.zeros(2), bed=np.zeros(2),
                      depth=np.ones(2), inside=np.ones(2, bool), source="test")
    z, clamped = col.z_at(np.array([0.0, 0.5]), margin=0.05)
    assert z[0] == pytest.approx(0.05) and clamped[0]
    assert z[1] == pytest.approx(0.5) and not clamped[1]


def test_the_float32_triangulation_trap_is_named(tmp_path):
    """SERAFIN stores x/y as float32; at easting ~759,500 that collapses a 0.5 m
    mesh. matplotlib says only 'Triangulation is invalid'."""
    from axqua.model_column import safe_triangulation, triangulation_defects

    # two triangles whose cross-stream edge is finer than float32 resolves there
    x = np.array([759500.0, 759500.5, 759500.0, 759500.5])
    y = np.array([5348500.0, 5348500.0, 5348500.02, 5348500.02])
    ikle = np.array([[0, 1, 2], [1, 3, 2]])
    assert triangulation_defects(x, y, ikle)["zero_area"] == 0
    safe_triangulation(x, y, ikle)                      # float64: fine

    xf = x.astype(np.float32).astype(float)
    yf = y.astype(np.float32).astype(float)
    with pytest.raises(ValueError, match="float32"):
        safe_triangulation(xf, yf, ikle, source="r2d.slf")


def test_placement_guard_refuses_when_targets_miss_the_water():
    """HydroBayesCal's nearest-point search has NO distance cutoff, so aXqua is the
    only place a mis-placed target can be caught."""
    placement = pd.DataFrame({
        "id": range(1, 11),
        "usable": [True] * 2 + [False] * 8,
        "status": ["ok"] * 2 + ["outside-domain"] * 8,
    })
    with pytest.raises(SystemExit, match="usable model column"):
        ofcal._assert_targets_placed(placement)

    placement["usable"] = [True] * 8 + [False] * 2
    placement["status"] = ["ok"] * 8 + ["outside-domain"] * 2
    ofcal._assert_targets_placed(placement)             # 8 of 10 is fine


# --------------------------------------------------------------------------- #
# the elevation guard: the discriminator is the whole point
# --------------------------------------------------------------------------- #
def _residual_case(monkeypatch, tmp_path, residual, depth, extra=None):
    """A tidy table whose z sits `residual` above a flat reference bed."""
    from axqua import ground_truth_qa as qa

    n = len(residual)
    df = pd.DataFrame({"x": np.arange(n, dtype=float), "y": np.zeros(n),
                       "z": 100.0 + np.asarray(residual), "h": np.asarray(depth)})
    if extra:
        for k, v in extra.items():
            df[k] = v
    monkeypatch.setattr(qa, "reference_bed",
                        lambda cfg, x, y: (np.full(n, 100.0), "test bed"))
    return qa.check_ground_truth_elevations(_cfg(tmp_path),
                                            tables={"hydraulics": df})


def test_an_unsubtracted_instrument_height_is_an_error(tmp_path, monkeypatch):
    """The KB15 defect: a large, tight, depth-independent offset."""
    rng = np.random.default_rng(0)
    depth = rng.uniform(0.3, 1.0, 12)
    findings = _residual_case(monkeypatch, tmp_path,
                              2.26 + rng.normal(0, 0.02, 12), depth)
    errors = [f for f in findings if f.code == "instrument-height"]
    assert len(errors) == 1
    assert errors[0].severity == "error"
    assert errors[0].offset == pytest.approx(2.26, abs=0.05)


def test_a_depth_proportional_bed_bias_is_NOT_an_error(tmp_path, monkeypatch):
    """The false-alarm test, and the one that matters: KB15's DEM really is ~0.3 m
    high in the wetted channel, and a guard that refused that would block a
    legitimate campaign."""
    rng = np.random.default_rng(1)
    depth = rng.uniform(0.3, 1.0, 20)
    findings = _residual_case(monkeypatch, tmp_path,
                              0.05 + 0.7 * depth + rng.normal(0, 0.05, 20), depth)
    assert not [f for f in findings if f.severity == "error"]
    assert [f.code for f in findings] == ["depth-proportional-bias"]


def test_both_modes_are_reported_when_both_are_present(tmp_path, monkeypatch):
    """They superpose. Testing only 'is it flat?' sees the slope and misses the
    constant - which is exactly how the first implementation got KB15 backwards."""
    rng = np.random.default_rng(2)
    depth = rng.uniform(0.3, 1.0, 20)
    findings = _residual_case(monkeypatch, tmp_path,
                              2.26 + 0.7 * depth + rng.normal(0, 0.02, 20), depth)
    codes = sorted(f.code for f in findings)
    assert codes == ["depth-proportional-bias", "instrument-height"]


def test_a_small_offset_is_not_reported(tmp_path, monkeypatch):
    rng = np.random.default_rng(3)
    depth = rng.uniform(0.3, 1.0, 12)
    assert not _residual_case(monkeypatch, tmp_path,
                              0.10 + rng.normal(0, 0.02, 12), depth)


def test_a_large_but_scattered_offset_is_not_an_instrument_height(tmp_path, monkeypatch):
    """Fails the 4-sigma rule: too noisy to be one pole length."""
    rng = np.random.default_rng(4)
    depth = rng.uniform(0.3, 1.0, 15)
    findings = _residual_case(monkeypatch, tmp_path,
                              0.50 + rng.normal(0, 0.30, 15), depth)
    assert not [f for f in findings if f.code == "instrument-height"]


def test_groups_come_from_an_explicit_column_not_from_clustering(tmp_path, monkeypatch):
    """Clustering on the residual manufactures depth-flat groups, which invents
    instrument heights that are not there."""
    rng = np.random.default_rng(5)
    depth = np.concatenate([rng.uniform(0.3, 1.0, 8), rng.uniform(0.3, 1.0, 8)])
    residual = np.concatenate([2.26 + rng.normal(0, 0.02, 8),
                               2.70 + rng.normal(0, 0.02, 8)])
    findings = _residual_case(monkeypatch, tmp_path, residual, depth,
                              extra={"pole_height": [2.26] * 8 + [2.70] * 8})
    offsets = sorted(round(f.offset, 1) for f in findings
                     if f.code == "instrument-height")
    assert offsets == [2.3, 2.7]


def test_reporting_an_error_does_not_raise_unless_strict():
    """The contract the compile-time hook depends on: pipeline stage 5 wraps the
    whole HydroBayesCal setup in a blanket `except Exception`, so a raise during
    compilation would silently skip it - a louder defect than the one guarded."""
    from axqua.ground_truth_qa import ElevationFinding, report

    finding = ElevationFinding("error", "instrument-height", "all", 12,
                               2.26, 0.0, 0.9, 0.02, "test")
    report([finding])                       # must not raise
    with pytest.raises(SystemExit, match="not usable"):
        report([finding], strict=True)


def test_the_compile_hook_is_wrapped(tmp_path):
    """Belt and braces on the above: the call site itself must be guarded, since a
    future refactor could make the check raise for a new reason."""
    import inspect

    from axqua.ground_truth import compile_ground_truth

    src = inspect.getsource(compile_ground_truth)
    assert "check_ground_truth_elevations" in src
    hook = src[src.index("check_ground_truth_elevations"):]
    assert "except Exception" in src[:src.index("check_ground_truth_elevations")] \
        or "except Exception" in hook, "the elevation check must not be able to raise here"
