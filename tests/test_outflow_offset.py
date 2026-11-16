"""The outflow level as a calibration parameter (``calibration.outflow_offset``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from axqua import bayescal, validation
from axqua.calibration import emit_hbc_config, merged_parameters
from axqua.config import Calibration, ValidationSituation, dump_config, load_config
from axqua.core.errors import ConfigError
from axqua.solvers.telemac import outflow_offset
from test_validation import CAS, built  # noqa: F401 - the fixture


def validation_cas() -> str:
    return CAS


def _keyword_of(line: str) -> str:
    """HydroBayesCal's rule (control_telemac.rewrite_steering_file): the text left of
    the first '=' or ':'."""
    cut = min([p for p in (line.find("="), line.find(":")) if p != -1] or [len(line)])
    return line[:cut].strip()


def test_the_range_is_read_written_and_becomes_a_parameter(fake_case, tmp_path):
    assert Calibration.from_dict({}).outflow_offset is None
    assert outflow_offset.parameter(fake_case) is None
    fake_case.calibration.outflow_offset = [0.0, 2.0]
    dump_config(fake_case, tmp_path / "again.axq-case")
    again = load_config(tmp_path / "again.axq-case")
    assert again.calibration.outflow_offset == [0.0, 2.0]
    (offset,) = [p for p in merged_parameters(again) if p.name == "f.OUTFLOW_OFFSET"]
    assert (offset.min, offset.max) == (0.0, 2.0)


@pytest.mark.parametrize("given", [[1.0, 0.5], [0.3], "high", [0.2, 0.2]])
def test_a_range_that_is_not_two_increasing_numbers_is_refused(fake_case, given):
    fake_case.calibration.outflow_offset = given
    with pytest.raises(ConfigError, match="outflow_offset"):
        outflow_offset.parameter(fake_case)


def test_the_routine_fits_the_columns_and_hydrobayescal_finds_one_line(tmp_path):
    text = outflow_offset.source(1.31)
    code = [line for line in text.splitlines() if not line.startswith("!")]
    assert max(len(line) for line in code) <= 72
    hits = [line for line in text.splitlines()
            if _keyword_of(line) == outflow_offset.VARIABLE]
    assert hits == ["      OUTFLOW_OFFSET = 1.31D0"]
    assert "SUBROUTINE USER_SL" in text and "MIN(1.D0,MAX(0.D0,AT/OUTFLOW_RAMP))" in text
    assert outflow_offset.write(tmp_path / "f", -0.25).read_text() == \
        outflow_offset.source(-0.25)


def test_a_case_that_cannot_apply_an_offset_is_refused(fake_case):
    outflow_offset.check(fake_case)                           # elevation: a level
    fake_case.boundaries.outflow_condition = "free"
    with pytest.raises(ConfigError, match="prescribes no level"):
        outflow_offset.check(fake_case)
    fake_case.boundaries.outflow_condition = "elevation"
    fake_case.calibration.solver_name = "Telemac3d"
    with pytest.raises(ConfigError, match="TELEMAC-2D"):
        outflow_offset.check(fake_case)


def test_the_case_is_prepared_and_the_driver_rewrites_the_routine(fake_case):
    cfg = fake_case
    cfg.model_path("steady2d-a.cas").write_text("TITLE : 'a'\nDURATION : 3000.\n")
    cfg.model_path("steady2d-b.cas").write_text("TITLE : 'b'\nDURATION : 300.\n"
                                                "FORTRAN FILE : './user_fortran/'\n")
    assert bayescal.ready_outflow_offset(cfg, merged_parameters(cfg),
                                         ["steady2d-a.cas"]) == []
    assert not (cfg.model_path("user_fortran") / outflow_offset.FILE).exists()
    cfg.calibration.outflow_offset = [0.0, 2.0]
    assert bayescal.ready_outflow_offset(cfg, merged_parameters(cfg),
                                         ["steady2d-a.cas", "steady2d-b.cas"]) == \
        ["steady2d-a.cas", "steady2d-b.cas"]
    assert "OUTFLOW_OFFSET = 0D0" in (
        cfg.model_path("user_fortran") / outflow_offset.FILE).read_text()
    assert "FORTRAN FILE : 'user_fortran'" in cfg.model_path("steady2d-a.cas").read_text()
    b = cfg.model_path("steady2d-b.cas").read_text()
    assert "FORTRAN FILE : './user_fortran/'" in b             # the same folder, as written
    # a run of 300 s ended during the ramp of 600 s (review of 2026-11-16)
    assert "DURATION : 1200" in b and "DURATION : 3000." in cfg.model_path("steady2d-a.cas").read_text()
    assert "user_fortran/user_sl.f" in bayescal._rewritten_by_the_driver(cfg)


def test_a_steering_file_that_compiles_another_folder_is_refused(fake_case):
    """Review of 2026-11-16: a steering file that named another folder kept it, and the routine HydroBayesCal rewrites in user_fortran was never compiled."""
    cfg = fake_case
    cfg.calibration.outflow_offset = [0.0, 2.0]
    cfg.model_path("steady2d-b.cas").write_text("TITLE : 'b'\nFORTRAN FILE : 'mine'\n")
    with pytest.raises(ConfigError, match="compiled only from the folder user_fortran"):
        bayescal.ready_outflow_offset(cfg, merged_parameters(cfg), ["steady2d-b.cas"])
    cfg.user_fortran_dir = "routines"                         # HydroBayesCal looks in user_fortran
    with pytest.raises(ConfigError, match="HydroBayesCal rewrites"):
        outflow_offset.prepare(cfg)


def test_a_routine_user_sl_of_the_case_is_not_replaced(fake_case):
    cfg = fake_case
    cfg.calibration.outflow_offset = [0.0, 2.0]
    folder = cfg.model_path("user_fortran")
    folder.mkdir()
    (folder / "levels.f").write_text("      SUBROUTINE USER_SL\n     &(I, N, SL)\n      END\n")
    with pytest.raises(ConfigError, match="levels.f"):
        outflow_offset.prepare(cfg)
    (folder / "levels.f").unlink()
    outflow_offset.prepare(cfg)                               # aXqua's own file is no obstacle
    outflow_offset.prepare(cfg)


def test_the_built_case_and_a_control_file_are_not_changed(fake_case):
    """Review of 2026-11-16: the calibration added FORTRAN FILE to steady2d.cas before the as-built copy was taken, so every later steady run compiled Fortran."""
    cfg = fake_case
    cfg.calibration.outflow_offset = [0.0, 2.0]
    as_built = "TITLE : 'as_built'\nDURATION : 3000.\n"
    cfg.model_path(cfg.cas_file).write_text(as_built)
    used = bayescal.ready_outflow_offset(cfg, merged_parameters(cfg), [cfg.cas_file], copy=True)
    assert used == [bayescal.CALIBRATION_OFFSET_CAS]
    assert cfg.model_path(cfg.cas_file).read_text() == as_built
    assert "FORTRAN FILE : 'user_fortran'" in cfg.model_path(used[0]).read_text()


def test_a_killed_calibration_leaves_no_offset_behind_a_rebuild(fake_case):
    """Review of 2026-11-16: a rebuild dropped the copy of the built case, and user_sl.f kept the last offset the driver had sampled."""
    cfg = fake_case
    cfg.calibration.outflow_offset = [0.0, 2.0]
    cfg.model_path(cfg.cas_file).write_text("TITLE : 'built'\nDURATION : 3000.\n")
    cfg.model_path("friction.tbl").write_text("1 NIKU 0.05 NULL\n")
    used = bayescal.ready_outflow_offset(cfg, merged_parameters(cfg), [cfg.cas_file], copy=True)
    cfg.calibration.control_file = used[0]
    routine = cfg.model_path("user_fortran") / outflow_offset.FILE
    with bayescal.case_as_built(cfg):
        routine.write_text(outflow_offset.source(1.2))        # the driver's last set
        # killed here: the copy stays, and the case is built anew
        assert bayescal.discard_as_built(cfg)
        assert "OUTFLOW_OFFSET = 0D0" in routine.read_text()
    assert "OUTFLOW_OFFSET = 0D0" in routine.read_text()


def test_the_multiflow_calibration_applies_the_offset(fake_case, tmp_path, monkeypatch):
    """Review of 2026-11-16: run_Bayes_cal_multiflow.py named user_sl.f in its config, but no routine was written and no steering file compiled it, so every run had the offset 0."""
    cfg = fake_case
    cfg.calibration.outflow_offset = [0.0, 2.0]
    cfg.model_path(cfg.cas_file).write_text("TITLE : 'built'\nDURATION : 3000.\n")
    flows = [bayescal.FlowSpec(name="low", discharge=1.0, kind="csv", values=tmp_path / "a.csv"),
             bayescal.FlowSpec(name="high", discharge=3.0, kind="csv", values=tmp_path / "b.csv")]
    monkeypatch.setattr(bayescal, "compile_flow_csvs", lambda cfg, flows, folder: {f.name: tmp_path / f"{f.name}.csv" for f in flows})

    def write_flow_cas(cfg, base, flow, folder, **kwargs):
        dest = Path(kwargs.get("dest_dir") or base.parent) / f"steady2d-{flow.name}.cas"
        dest.write_text(base.read_text().replace("3000.", str(kwargs.get("duration") or 900.0)))
        return dest

    monkeypatch.setattr(bayescal, "write_flow_cas", write_flow_cas)
    monkeypatch.setattr(bayescal, "stage_driver", lambda folder, name, **kw: Path(folder) / name)
    assert bayescal.run_multiflow_calibration(cfg, flows, launch_mode="prepare") == 0
    for flow in flows:
        text = cfg.model_path(f"steady2d-{flow.name}.cas").read_text()
        assert "FORTRAN FILE : 'user_fortran'" in text and "DURATION : 1200" in text
    assert (cfg.model_path("user_fortran") / outflow_offset.FILE).is_file()
    assert "'fortran_file': 'user_sl.f'" in Path(cfg.calibration_path("multiflow") / "config_Telemac_multiflow.py").read_text()
    assert cfg.model_path(cfg.cas_file).read_text() == "TITLE : 'built'\nDURATION : 3000.\n"


def test_both_configurations_name_the_routine(fake_case, tmp_path):
    cfg = fake_case
    cfg.calibration.outflow_offset = [0.0, 2.0]
    text = Path(emit_hbc_config(cfg, None)).read_text()
    assert "'fortran_file': 'user_sl.f'" in text and "'f.OUTFLOW_OFFSET'" in text
    flows = [bayescal.FlowSpec(name="a", discharge=1.0, kind="csv",
                               values=tmp_path / "a.csv")]
    path = bayescal.emit_multiflow_config(cfg, tmp_path, cfg.model_dir, flows,
                                          {"a": tmp_path / "a.csv"}, n_cpus=1,
                                          init_runs=4, max_runs=6)
    assert "'fortran_file': 'user_sl.f'" in Path(path).read_text()
    cfg.calibration.outflow_offset = None
    assert "'fortran_file': None" in Path(emit_hbc_config(cfg, None)).read_text()


def test_the_validation_starts_from_the_control_file_the_calibration_ran(built, monkeypatch):  # noqa: F811
    """Review of 2026-11-16: with calibration.hotstart and calibration.control_file, the calibration ran the control file and the validation a hotstart."""
    built.calibration.hotstart = True
    built.calibration.control_file = "mine.cas"
    built.model_path("mine.cas").write_text(validation_cas().replace("TITLE : 'reach steady'", "TITLE : 'mine'").replace("DURATION : 3000.0", "DURATION : 5000.0"))
    monkeypatch.setattr(bayescal, "situation_start", lambda *a, **k: pytest.fail("the hotstart was used"))
    situation = ValidationSituation(name="v", prescribed_flowrate=2.4, prescribed_elevation=100.0)
    text = validation.build(built, situation, {"zone4": 0.05}).cas.read_text()
    assert "DURATION : 5000.0" in text and "TITLE : 'fakecase validation v'" in text


def test_a_short_validation_run_lasts_long_enough_for_the_offset(built):  # noqa: F811
    situation = ValidationSituation(name="v", prescribed_flowrate=2.4, prescribed_elevation=100.0, duration=400.0)
    setup = validation.build(built, situation, {"f.OUTFLOW_OFFSET": 1.31})
    assert "DURATION : 1200" in setup.cas.read_text()
    assert any("ended before the outflow offset" in note for note in setup.notes)


def test_the_validation_runs_with_the_calibrated_offset(built):  # noqa: F811
    situation = ValidationSituation(name="v", prescribed_flowrate=2.4,
                                    prescribed_elevation=100.0)
    setup = validation.build(built, situation, {"f.OUTFLOW_OFFSET": 1.31})
    assert "FORTRAN FILE : 'user_fortran-validation-v'" in setup.cas.read_text()
    copied = built.model_path("user_fortran-validation-v") / outflow_offset.FILE
    assert "      OUTFLOW_OFFSET = 1.31\n" in copied.read_text()
    original = built.model_path("user_fortran") / outflow_offset.FILE
    assert "OUTFLOW_OFFSET = 0D0" in original.read_text()      # the case keeps 0
