"""The outflow level as a calibration parameter (``calibration.outflow_offset``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from axqua import bayescal, validation
from axqua.calibration import emit_hbc_config, merged_parameters
from axqua.config import Calibration, ValidationSituation, dump_config, load_config
from axqua.core.errors import ConfigError
from axqua.solvers.telemac import outflow_offset
from test_validation import built  # noqa: F401 - the fixture


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
    cfg.model_path("steady2d-a.cas").write_text("TITLE : 'a'\n")
    cfg.model_path("steady2d-b.cas").write_text("TITLE : 'b'\nFORTRAN FILE : 'mine'\n")
    assert not bayescal.ready_outflow_offset(cfg, merged_parameters(cfg),
                                             ["steady2d-a.cas"])
    assert not (cfg.model_path(cfg.user_fortran_dir) / outflow_offset.FILE).exists()
    cfg.calibration.outflow_offset = [0.0, 2.0]
    assert bayescal.ready_outflow_offset(cfg, merged_parameters(cfg),
                                         ["steady2d-a.cas", "steady2d-b.cas"])
    assert "OUTFLOW_OFFSET = 0D0" in (
        cfg.model_path(cfg.user_fortran_dir) / outflow_offset.FILE).read_text()
    assert "FORTRAN FILE : 'user_fortran'" in cfg.model_path("steady2d-a.cas").read_text()
    assert cfg.model_path("steady2d-b.cas").read_text() == \
        "TITLE : 'b'\nFORTRAN FILE : 'mine'\n"                # its own folder is kept
    assert "user_fortran/user_sl.f" in bayescal._rewritten_by_the_driver(cfg)


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


def test_the_validation_runs_with_the_calibrated_offset(built):  # noqa: F811
    situation = ValidationSituation(name="v", prescribed_flowrate=2.4,
                                    prescribed_elevation=100.0)
    setup = validation.build(built, situation, {"f.OUTFLOW_OFFSET": 1.31})
    assert "FORTRAN FILE : 'user_fortran-validation-v'" in setup.cas.read_text()
    copied = built.model_path("user_fortran-validation-v") / outflow_offset.FILE
    assert "      OUTFLOW_OFFSET = 1.31\n" in copied.read_text()
    original = built.model_path("user_fortran") / outflow_offset.FILE
    assert "OUTFLOW_OFFSET = 0D0" in original.read_text()      # the case keeps 0
