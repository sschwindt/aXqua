"""The outflow level as a calibration parameter (``calibration.outflow_offset``)."""

from __future__ import annotations

import shutil
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
    assert any("ended before the outflow level" in note for note in setup.notes)


def test_the_validation_runs_with_the_calibrated_offset(built):  # noqa: F811
    situation = ValidationSituation(name="v", prescribed_flowrate=2.4,
                                    prescribed_elevation=100.0)
    setup = validation.build(built, situation, {"f.OUTFLOW_OFFSET": 1.31})
    assert "FORTRAN FILE : 'user_fortran-validation-v'" in setup.cas.read_text()
    copied = built.model_path("user_fortran-validation-v") / outflow_offset.FILE
    assert "      OUTFLOW_OFFSET = 1.31\n" in copied.read_text()
    original = built.model_path("user_fortran") / outflow_offset.FILE
    assert "OUTFLOW_OFFSET = 0D0" in original.read_text()      # the case keeps 0


# --------------------------------------------------------------------------- #
# the energy slope at the outlet (calibration.outflow_slope)
# --------------------------------------------------------------------------- #
def _section() -> outflow_offset.Section:
    """A trapezoidal channel 20 m wide at the bottom with banks 1:2, ks 0.1 m, slope 0.002."""
    import numpy as np

    station = np.linspace(0.0, 40.0, 121)
    bed = np.clip(np.abs(station - 20.0) - 10.0, 0.0, None) / 2.0 + 100.0
    return outflow_offset.Section(station=station, bed=bed,
                                  ks=np.full(station.shape, 0.1), slope=0.002)


@pytest.fixture
def sloped(fake_case, monkeypatch):
    """The fake case with a section rating at its outlet and the energy slope calibrated."""
    fake_case.boundaries.outflow_condition = "stage_discharge"
    fake_case.boundaries.rating_method = "section"
    fake_case.calibration.outflow_slope = [1.0e-5, 2.0e-3]
    monkeypatch.setattr(outflow_offset, "section_of", lambda cfg: _section())
    return fake_case


def test_the_slope_range_becomes_a_parameter_in_log10(fake_case, tmp_path):
    import math

    assert Calibration.from_dict({}).outflow_slope is None
    fake_case.calibration.outflow_slope = [1.0e-5, 2.0e-3]
    dump_config(fake_case, tmp_path / "again.axq-case")
    again = load_config(tmp_path / "again.axq-case")
    assert again.calibration.outflow_slope == [1.0e-5, 2.0e-3]
    (slope,) = [p for p in merged_parameters(again) if p.name == "f.OUTFLOW_LOG_SLOPE"]
    assert (slope.min, slope.max) == pytest.approx((-5.0, math.log10(2.0e-3)))
    assert outflow_offset.kind(again) == "slope" and outflow_offset.configured(again)


@pytest.mark.parametrize("given", [[0.0, 1.0e-3], [-1.0e-4, 1.0e-3], [2.0e-3, 1.0e-5]])
def test_a_slope_range_that_is_not_positive_and_increasing_is_refused(fake_case, given):
    fake_case.calibration.outflow_slope = given
    with pytest.raises(ConfigError, match="outflow_slope"):
        outflow_offset.parameter(fake_case)


def test_an_offset_and_a_slope_together_are_refused(fake_case):
    fake_case.calibration.outflow_offset = [0.0, 2.0]
    fake_case.calibration.outflow_slope = [1.0e-5, 2.0e-3]
    with pytest.raises(ConfigError, match="both move the outflow level"):
        merged_parameters(fake_case)


def test_the_slope_needs_the_rating_of_the_outflow_section(sloped):
    outflow_offset.check(sloped)
    sloped.boundaries.rating_method = "trapezoid"
    with pytest.raises(ConfigError, match="'trapezoid'"):
        outflow_offset.check(sloped)
    sloped.boundaries.rating_method = "section"
    sloped.boundaries.outflow_condition = "elevation"
    with pytest.raises(ConfigError, match="fixed level"):
        outflow_offset.check(sloped)


def test_the_section_reproduces_the_rating_and_finds_the_slope_of_a_level(tmp_path):
    import pandas as pd

    from axqua.rating import section_rating

    section = _section()
    path = section_rating(tmp_path / "r.csv", [5.0, 20.0], station=section.station,
                          bed=section.bed, ks=section.ks, slope=section.slope)
    rated = pd.read_csv(path)["WSE"].tolist()
    assert [section.stage(5.0), section.stage(20.0)] == pytest.approx(rated, abs=1e-4)
    # a backwater: the level 0.8 m higher needs a far smaller energy slope
    level = section.stage(20.0) + 0.8
    slope = section.slope_for(20.0, level)
    assert slope < section.slope / 10
    assert section.stage(20.0, slope) == pytest.approx(level, abs=1e-6)


def test_the_slope_routine_fits_the_columns_and_hydrobayescal_finds_one_line():
    import math

    section = _section()
    text = outflow_offset.slope_source(section)
    code = [line for line in text.splitlines() if not line.startswith("!")]
    assert max(len(line) for line in code) <= 72
    hits = [line for line in text.splitlines()
            if _keyword_of(line) == outflow_offset.SLOPE_VARIABLE]
    assert len(hits) == 1
    # neutral: the slope of the reach, at which the level stays that of the rating
    assert float(hits[0].split("=")[1].replace("D", "E")) == pytest.approx(
        math.log10(section.slope), abs=1e-9)
    assert text.startswith(outflow_offset.MARK) and "SUBROUTINE USER_SL" in text
    # every value of the section, in DATA statements of few continuation lines
    assert text.count("DATA AXQ_ZB(") == math.ceil(121 / 45)
    values = "".join(line[8:] for line in text.splitlines() if line.startswith("     &  "))
    assert values.count("D") == 3 * 121 + 120
    for statement in text.split("      DATA ")[1:]:
        assert statement.split("/\n")[0].count("\n     &") <= 19
    # the discharge of the run and the ramp
    assert "DEBIT(AXQ_K)" in text and "MIN(1.D0,MAX(0.D0,AT/OUTFLOW_RAMP))" in text


def test_a_slope_calibration_prepares_the_routine_and_both_configurations(sloped, tmp_path):
    cfg = sloped
    cfg.model_path("steady2d-a.cas").write_text("TITLE : 'a'\nDURATION : 3000.\n")
    assert bayescal.ready_outflow_offset(cfg, merged_parameters(cfg),
                                         ["steady2d-a.cas"]) == ["steady2d-a.cas"]
    routine = (cfg.model_path("user_fortran") / outflow_offset.FILE).read_text()
    assert routine == outflow_offset.slope_source(_section())
    assert "FORTRAN FILE : 'user_fortran'" in cfg.model_path("steady2d-a.cas").read_text()
    assert "user_fortran/user_sl.f" in bayescal._rewritten_by_the_driver(cfg)
    text = Path(emit_hbc_config(cfg, None)).read_text()
    assert "'fortran_file': 'user_sl.f'" in text and "'f.OUTFLOW_LOG_SLOPE'" in text
    flows = [bayescal.FlowSpec(name="a", discharge=1.0, kind="csv", values=tmp_path / "a.csv")]
    path = bayescal.emit_multiflow_config(cfg, tmp_path, cfg.model_dir, flows,
                                          {"a": tmp_path / "a.csv"}, n_cpus=1,
                                          init_runs=4, max_runs=6)
    assert "'fortran_file': 'user_sl.f'" in Path(path).read_text()


def test_the_validation_solves_the_rating_at_the_calibrated_slope(built, monkeypatch):  # noqa: F811
    monkeypatch.setattr(outflow_offset, "section_of", lambda cfg: _section())
    situation = ValidationSituation(name="v", prescribed_flowrate=2.4,
                                    prescribed_elevation=100.0)
    setup = validation.build(built, situation, {"f.OUTFLOW_LOG_SLOPE": -4.4})
    assert "FORTRAN FILE : 'user_fortran-validation-v'" in setup.cas.read_text()
    copied = (built.model_path("user_fortran-validation-v") / outflow_offset.FILE).read_text()
    assert "      OUTFLOW_LOG_SLOPE = -4.4\n" in copied and "AXQ_STAGE" in copied
    # an offset routine the folder held is replaced by the routine of the slope
    original = built.model_path("user_fortran") / outflow_offset.FILE
    original.write_text(outflow_offset.source(0.0))
    validation.build(built, situation, {"f.OUTFLOW_LOG_SLOPE": -4.0})
    copied = (built.model_path("user_fortran-validation-v") / outflow_offset.FILE).read_text()
    assert "      OUTFLOW_LOG_SLOPE = -4\n" in copied


def test_the_report_shows_the_slope_itself():
    from axqua.calibration_report import LABELS, _value_text

    assert _value_text(-4.4255, False, "f.OUTFLOW_LOG_SLOPE") == "S = 3.8e-05"
    assert LABELS["f.OUTFLOW_LOG_SLOPE"][0] == "Energy slope at the outlet"
    assert _value_text(1.31, False, "f.OUTFLOW_OFFSET") == "+1.31 m"


# --------------------------------------------------------------------------- #
# review of 2026-11-18 (eb293859..1035f96d)
# --------------------------------------------------------------------------- #
def test_a_rating_curve_of_the_case_is_refused_and_one_of_the_section_is_not(sloped, tmp_path):
    """The levels of a given curve are not the normal stage of the section, so the slope would be an offset of no defined meaning on top of them."""
    section = _section()
    derived = tmp_path / "derived.csv"
    derived.write_text("Q,WSE,depth\n" + "".join(
        f"{q},{section.stage(q):.4f},0\n" for q in (5.0, 20.0)))
    sloped.boundaries.stage_discharge = derived
    outflow_offset.prepare(sloped)                            # aXqua's own section rating
    measured = tmp_path / "measured.csv"
    measured.write_text("Q,WSE\n5.0,100.9\n20.0,101.6\n")
    sloped.boundaries.stage_discharge = measured
    with pytest.raises(ConfigError, match="not the normal stage of the outflow section") as caught:
        outflow_offset.prepare(sloped)
    assert "outflow_offset" in caught.value.remedy


def test_the_rating_check_reads_the_curve_as_the_solver_path_does(sloped, tmp_path):
    """Other column names and a semicolon are read as read_rating_pairs reads them; a stale derived curve is refused with the remedy to derive it again."""
    section = _section()
    other = tmp_path / "other.csv"
    other.write_text("discharge;stage\n" + "".join(
        f"{q};{section.stage(q):.4f}\n" for q in (5.0, 20.0)))
    sloped.boundaries.stage_discharge = other
    outflow_offset.check_rating(sloped, section)
    stale = tmp_path / "stale.csv"
    stale.write_text("Q,WSE\n" + "".join(f"{q},{section.stage(q) + 0.05:.4f}\n" for q in (5.0, 20.0)))
    sloped.boundaries.stage_discharge = stale
    with pytest.raises(ConfigError) as caught:
        outflow_offset.check_rating(sloped, section)
    assert "delete the file" in caught.value.remedy


def test_the_outflow_level_as_a_row_of_the_parameter_list_is_refused(fake_case):
    """A row named f.OUTFLOW_LOG_SLOPE made the routine of the offset, which has no line for it."""
    from axqua.config import CalibrationParameter

    fake_case.calibration.parameters = [
        CalibrationParameter(name="f.OUTFLOW_LOG_SLOPE", min=-5.0, max=-3.0)]
    with pytest.raises(ConfigError, match="calibration.outflow_offset or calibration.outflow_slope"):
        merged_parameters(fake_case)
    fake_case.calibration.parameters = [
        CalibrationParameter(name="f.outflow_offset", min=0.0, max=1.0)]
    with pytest.raises(ConfigError, match="calibration.outflow_offset or calibration.outflow_slope"):
        merged_parameters(fake_case)


def test_the_messages_of_a_slope_calibration_name_the_slope(sloped):
    sloped.user_fortran_dir = "routines"
    with pytest.raises(ConfigError) as caught:
        outflow_offset.prepare(sloped)
    assert "calibration.outflow_slope" in caught.value.remedy
    sloped.user_fortran_dir = "user_fortran"
    with pytest.raises(ConfigError) as caught:
        outflow_offset.with_fortran_folder(sloped, "FORTRAN FILE : 'mine'\n")
    assert "calibration.outflow_slope" in caught.value.remedy


def test_the_validation_names_the_setting_of_the_calibrated_values(fake_case):
    """The validation knows from the calibrated values which setting moved the level; the case file may no longer name it, or name both."""
    fake_case.user_fortran_dir = "routines"
    with pytest.raises(ConfigError) as caught:
        outflow_offset.check_folder(fake_case, which="slope")
    assert "calibration.outflow_slope" in caught.value.remedy
    fake_case.calibration.outflow_offset = [0.0, 2.0]
    fake_case.calibration.outflow_slope = [1.0e-5, 2.0e-3]
    with pytest.raises(ConfigError, match="the user Fortran folder") as caught:
        outflow_offset.check_folder(fake_case, which="slope")
    with pytest.raises(ConfigError) as caught:
        outflow_offset.with_fortran_folder(fake_case, "FORTRAN FILE : 'mine'\n", "slope")
    assert "calibration.outflow_slope" in caught.value.remedy


def test_a_small_slope_raises_the_stage_above_the_old_bracket():
    """The stage was bracketed at the highest bed point + 10 m and saturated there."""
    section = _section()
    top = float(section.bed.max()) + 10.0
    deep = section.stage(400.0, 1e-9)
    assert deep > top + 1.0
    deeper = section.stage(400.0, 1e-10)
    assert deeper > deep


_STUBS = """
module bief
end module bief
module declarations_special
  integer :: lu = 6
end module declarations_special
module declarations_telemac
end module declarations_telemac
module declarations_telemac2d
  double precision :: at = 0.d0
  double precision, allocatable :: debit(:)
  integer :: ndebit = 0
end module declarations_telemac2d
module interface_telemac2d
  interface
    subroutine user_sl(i, n, sl)
      integer, intent(in) :: i, n
      double precision, intent(inout) :: sl
    end subroutine user_sl
    double precision function sl(i, n)
      integer, intent(in) :: i, n
    end function sl
  end interface
end module interface_telemac2d
"""

_DRIVER = """
subroutine plante(i)
  integer, intent(in) :: i
  write(*, *) 'PLANTE', i
  stop 3
end subroutine plante
program drive
  use declarations_telemac2d
  implicit none
  double precision :: level
  character(len=32) :: arg
  allocate(debit(2))
  call get_command_argument(1, arg)
  read(arg, *) debit(2)
  debit(1) = 0.d0
  ndebit = 2
  at = 1200.d0
  level = 100.d0
  call user_sl(1, 1, level)
  write(*, '(F24.12)') level - 100.d0
end program drive
"""


@pytest.mark.skipif(shutil.which("gfortran") is None, reason="no gfortran")
@pytest.mark.parametrize("slope", [2.0e-4, 1.0e-9])
def test_the_compiled_routine_shifts_the_level_as_the_python_rating_does(tmp_path, slope):
    """The Keulegan rating is written twice, in Python and in the Fortran of the routine; this keeps them together (review of 2026-11-18). 1e-9 needs the raised bracket."""
    import math
    import subprocess

    section = _section()
    (tmp_path / "stubs.f90").write_text(_STUBS)
    (tmp_path / "user_sl.f").write_text(outflow_offset.slope_source(section, math.log10(slope)))
    (tmp_path / "driver.f90").write_text(_DRIVER)
    std = ["-std=f2008", "-fimplicit-none"]
    for args in (["-c", "stubs.f90"], ["-ffixed-form", "-c", "user_sl.f"],
                 ["driver.f90", "user_sl.o", "stubs.o", "-o", "drive"]):
        subprocess.run(["gfortran", *std, *args], cwd=tmp_path, check=True,
                       capture_output=True, text=True)
    for discharge in (5.0, 20.0):
        run = subprocess.run(["./drive", str(discharge)], cwd=tmp_path, check=True,
                             capture_output=True, text=True)
        expected = section.stage(discharge, slope) - section.stage(discharge)
        assert float(run.stdout.split()[-1]) == pytest.approx(expected, abs=1e-6)
    # no positive prescribed discharge: the run stops instead of applying no shift
    run = subprocess.run(["./drive", "0.0"], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode != 0 and "NO POSITIVE PRESCRIBED FLOWRATE" in run.stdout
