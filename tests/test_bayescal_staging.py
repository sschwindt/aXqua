"""How the HydroBayesCal driver is located and staged (:mod:`axqua.bayescal`).

HydroBayesCal is a pip dependency, so the driver comes from the **installed
package** - there is no checkout directory to configure and no second conda
environment to name. These tests pin that, plus the one behavioural patch axqua
applies to the stock driver.

The tests that need the real package are skipped when it is not installed
(``pip install 'axqua[calibration]'``), since it is an optional extra.

Run via: mamba run -n axqua-env pytest tests/test_bayescal_staging.py
"""

from __future__ import annotations

import sys

import pytest

hbc = pytest.importorskip(
    "hydroBayesCal", reason="optional extra: pip install 'axqua[calibration]'")


def test_driver_comes_from_the_installed_package(tmp_path):
    from axqua.bayescal import stage_driver

    staged = stage_driver(tmp_path, "bal_telemac.py")

    assert staged.is_file()
    assert staged.parent == tmp_path
    # no environment variable had to be set for this to resolve
    import os
    assert "HYDROBAYESCAL_DIR" not in os.environ


def test_extraction_window_is_patched_to_the_converged_frame(tmp_path):
    """The stock driver averages the last window of frames; on a run marching to
    steady state that averages the residual transient into the calibration values."""
    from axqua.bayescal import stage_driver

    original = (hbc.driver_path("bal_telemac.py")).read_text()
    staged = stage_driver(tmp_path, "bal_telemac.py").read_text()

    assert original.count('output_extraction_time="mean_last"') > 0   # stock behaviour
    assert 'output_extraction_time="mean_last"' not in staged
    assert staged.count('output_extraction_time="last"') == \
        original.count('output_extraction_time="mean_last"')


def test_every_staged_file_is_patched_not_just_the_primary(tmp_path):
    """The multiflow driver imports run_complex_model from its bal_telemac.py sibling
    and calls it WITHOUT passing output_extraction_time, so it takes the sibling's
    default. Patching only the primary left multi-flow calibrations averaging the
    dry-start transient while single-flow ones did not - a silent, mode-dependent
    difference in what the calibration is fitted to."""
    from axqua.bayescal import stage_driver

    stage_driver(tmp_path, "bal_telemac_multiflow.py")

    remaining = sum(p.read_text().count('output_extraction_time="mean_last"')
                    for p in tmp_path.glob("*.py"))
    assert remaining == 0
    # ... and the sibling is where those sites actually live
    assert (tmp_path / "bal_telemac.py").read_text().count(
        'output_extraction_time="last"') > 0


def test_multiflow_driver_brings_the_sibling_it_imports(tmp_path):
    """bal_telemac_multiflow.py imports bal_telemac.py by file name, so staging one
    without the other yields an ImportError only at run time, hours in."""
    from axqua.bayescal import stage_driver

    stage_driver(tmp_path, "bal_telemac_multiflow.py")

    assert (tmp_path / "bal_telemac_multiflow.py").is_file()
    assert (tmp_path / "bal_telemac.py").is_file()


def test_a_checkout_can_still_override_the_installed_package(tmp_path):
    """For developing against an unreleased driver."""
    from axqua.bayescal import stage_driver

    checkout = tmp_path / "checkout" / "src" / "hydroBayesCal" / "drivers"
    checkout.mkdir(parents=True)
    (checkout / "bal_telemac.py").write_text("# a local edit\n")

    staged = stage_driver(tmp_path / "out", "bal_telemac.py",
                          checkout=tmp_path / "checkout")
    assert staged.read_text() == "# a local edit\n"


def test_missing_driver_names_what_is_available(tmp_path):
    from axqua.bayescal import stage_driver

    with pytest.raises(FileNotFoundError, match="available:"):
        stage_driver(tmp_path, "bal_nonexistent.py")


def test_launch_uses_this_interpreter_and_no_conda_env(tmp_path, monkeypatch, capsys):
    """The calibration runs in axqua's own environment - naming a conda env was
    the thing that made this fragile across machines."""
    from types import SimpleNamespace

    from axqua import bayescal, hbc

    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return SimpleNamespace(returncode=0)

    # The subprocess seam lives in axqua.hbc now: launching a staged driver is the
    # same job whichever solver is being calibrated, so bayescal only supplies the
    # TELEMAC environment and delegates the run.
    monkeypatch.setattr(hbc.subprocess, "run", fake_run)
    cfg = SimpleNamespace(telemac=SimpleNamespace(pysource=tmp_path / "pysource.sh"))
    driver = tmp_path / "bal_telemac.py"
    driver.write_text("")

    bayescal.launch(cfg, driver, tmp_path / "config_Telemac.py")

    command = seen["args"][-1]
    assert sys.executable in command
    assert "mamba run" not in command and "conda run" not in command
    assert "source" in command          # TELEMAC still has to be sourced


def test_launch_reports_an_ignored_env_argument(tmp_path, monkeypatch, capsys):
    """Old call sites passed env='wrr-proj'; say plainly that it no longer applies
    rather than silently ignoring it."""
    from types import SimpleNamespace

    from axqua import bayescal, hbc

    monkeypatch.setattr(hbc.subprocess, "run",
                        lambda args, **kw: SimpleNamespace(returncode=0))
    cfg = SimpleNamespace(telemac=SimpleNamespace(pysource=tmp_path / "pysource.sh"))
    driver = tmp_path / "bal_telemac.py"
    driver.write_text("")

    bayescal.launch(cfg, driver, tmp_path / "cfg.py", env="wrr-proj")

    assert "ignored" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the built case comes back as the calibration found it
# --------------------------------------------------------------------------- #
def _built_case(tmp_path, *, fortran=False):
    from types import SimpleNamespace

    model = tmp_path / "simulation"
    (model / "user_fortran").mkdir(parents=True)
    (model / "steady2d.cas").write_text("RESULTS FILE : r2d.slf\n")
    (model / "friction.tbl").write_text("4\tNIKU\t0.0500\tNULL\n")
    (model / "user_fortran" / "user_rain.f").write_text("      HMP_KF = 1.D-3\n")
    calibration = tmp_path / "calibration-validation"
    calibration.mkdir()
    cfg = SimpleNamespace(
        model_dir=model, cas_file="steady2d.cas", friction_tbl="friction.tbl",
        gaia_cas="gaia.cas", calibration=SimpleNamespace(control_file=None),
        gain_lose=SimpleNamespace(active=fortran, implementation="fortran"),
        morphodynamics=SimpleNamespace(enabled=False),
        calibration_path=lambda name: calibration / name)
    return cfg, model, calibration


def _as_the_driver_leaves_it(model):
    (model / "steady2d.cas").write_text("RESULTS FILE : r2d_12.slf\n")
    (model / "friction.tbl").write_text("4\tNIKU\t0.195\tNULL\n")
    (model / "user_fortran" / "user_rain.f").write_text("      HMP_KF = 7.D-3\n")


def test_a_calibration_gives_the_built_case_back(tmp_path):
    """HydroBayesCal leaves the last parameter set it tried in the case's own files,
    and the next steady run would use it without a word."""
    from axqua.bayescal import AS_BUILT, case_as_built

    cfg, model, calibration = _built_case(tmp_path, fortran=True)
    with case_as_built(cfg):
        _as_the_driver_leaves_it(model)
        assert (calibration / AS_BUILT / "friction.tbl").is_file()
    assert (model / "friction.tbl").read_text() == "4\tNIKU\t0.0500\tNULL\n"
    assert (model / "steady2d.cas").read_text() == "RESULTS FILE : r2d.slf\n"
    assert "1.D-3" in (model / "user_fortran" / "user_rain.f").read_text()
    assert not (calibration / AS_BUILT).exists()


def test_the_case_comes_back_when_the_driver_fails(tmp_path):
    import pytest

    from axqua.bayescal import case_as_built

    cfg, model, _ = _built_case(tmp_path)
    with pytest.raises(RuntimeError):
        with case_as_built(cfg):
            _as_the_driver_leaves_it(model)
            raise RuntimeError("the driver crashed")
    assert (model / "friction.tbl").read_text() == "4\tNIKU\t0.0500\tNULL\n"


def test_a_file_the_driver_does_not_rewrite_is_left_alone(tmp_path):
    """Without a gain-lose routine the Fortran file is not the driver's to change."""
    from axqua.bayescal import case_as_built

    cfg, model, _ = _built_case(tmp_path, fortran=False)
    with case_as_built(cfg):
        _as_the_driver_leaves_it(model)
    assert "7.D-3" in (model / "user_fortran" / "user_rain.f").read_text()


def test_a_killed_calibration_does_not_leak_into_the_next_simulation(tmp_path):
    """Cancelled from the plugin, then *Submit* on the steady tab: the run must be of
    the case as built."""
    from axqua.bayescal import AS_BUILT, restore_as_built

    cfg, model, calibration = _built_case(tmp_path)
    assert restore_as_built(cfg) is False            # nothing was set aside
    keep = calibration / AS_BUILT
    keep.mkdir()
    (keep / "friction.tbl").write_text((model / "friction.tbl").read_text())
    _as_the_driver_leaves_it(model)
    assert restore_as_built(cfg) is True
    assert (model / "friction.tbl").read_text() == "4\tNIKU\t0.0500\tNULL\n"
    assert not keep.exists() and restore_as_built(cfg) is False


def test_a_calibration_that_was_killed_is_undone_by_the_next_one(tmp_path):
    """A killed process restores nothing. Its copy is still the case as built."""
    from axqua.bayescal import AS_BUILT, case_as_built

    cfg, model, calibration = _built_case(tmp_path)
    keep = calibration / AS_BUILT
    keep.mkdir()
    (keep / "friction.tbl").write_text((model / "friction.tbl").read_text())
    (keep / "steady2d.cas").write_text((model / "steady2d.cas").read_text())
    _as_the_driver_leaves_it(model)                 # what the killed run left behind
    with case_as_built(cfg):
        # the second calibration starts from the built case, not from the leftovers
        assert (model / "friction.tbl").read_text() == "4\tNIKU\t0.0500\tNULL\n"
        _as_the_driver_leaves_it(model)
    assert (model / "friction.tbl").read_text() == "4\tNIKU\t0.0500\tNULL\n"
