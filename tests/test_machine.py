"""Solver locations belong to the machine, never to the shared repository.

Fourteen tracked case configs named one of three different users' absolute paths,
and one was committed pointing at a second machine entirely. A case config
describes a reach and must travel; where TELEMAC is installed does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from axqua.core import machine


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No inherited environment, and a settings file that cannot exist yet."""
    for var in machine.ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("AXQUA_SOLVER_ROOT", raising=False)
    monkeypatch.setenv("AXQUA_HOME", str(tmp_path / "axqua-home"))
    return tmp_path


def test_environment_wins_over_everything(clean_env, monkeypatch):
    """A batch system or container must be able to override without editing files."""
    monkeypatch.setenv("AXQUA_TELEMAC_PYSOURCE", "/from/env.sh")
    machine.write_settings({"telemac": "/from/settings.sh"})
    got = machine.resolve("telemac", configured="/from/case.sh")
    assert got.path == Path("/from/env.sh")
    assert "AXQUA_TELEMAC_PYSOURCE" in got.source


def test_machine_settings_beat_the_case_config(clean_env):
    """The whole point: a shared config's value is a fallback, not the mechanism."""
    machine.write_settings({"telemac": "/from/settings.sh"})
    got = machine.resolve("telemac", configured="/someone/elses/home.sh")
    assert got.path == Path("/from/settings.sh")


def test_a_case_local_override_beats_the_machine(clean_env, tmp_path):
    """For a case that needs a different build from the rest of the machine."""
    machine.write_settings({"openfoam": "/machine/bashrc"})
    case = tmp_path / "case"
    case.mkdir()
    (case / machine.LOCAL_NAME).write_text(yaml.safe_dump({"openfoam": "/case/bashrc"}))
    got = machine.resolve("openfoam", configured=None, case_dir=case)
    assert got.path == Path("/case/bashrc")


def test_the_case_config_still_works_when_nothing_else_is_set(clean_env):
    """Backwards compatibility: an existing self-contained config keeps working."""
    got = machine.resolve("telemac", configured="/in/the/config.sh")
    assert got.path == Path("/in/the/config.sh")
    assert got.source == "case config"


def test_a_relative_config_path_is_relative_to_the_CASE(clean_env, tmp_path):
    """`pysource: pysource.sh` has always meant the one beside the config, and
    `load_config` resolved it that way before solver resolution moved here. Returning
    it unresolved made the meaning depend on the working directory, so three
    surface-stage tests - and any case carrying a relative path - stopped validating.

    It only showed up on a machine with no settings file, because one of those
    outranks the case config and hid it. The case-local override beside it has always
    resolved relative paths this way; this is the same rule.
    """
    case = tmp_path / "case"
    case.mkdir()
    (case / "pysource.sh").write_text("# a stand-in\n")

    got = machine.resolve("telemac", configured="pysource.sh", case_dir=case)
    assert got.path == (case / "pysource.sh").resolve()
    assert got.path.is_file()

    # ...and an absolute one is still taken as it stands
    absolute = machine.resolve("telemac", configured="/opt/telemac/pysource.sh",
                               case_dir=case)
    assert absolute.path == Path("/opt/telemac/pysource.sh")


def test_the_suite_cannot_see_this_machines_solver_settings():
    """The suite's result must not depend on the developer's ~/.config.

    A machine settings file OUTRANKS the case config, so tests that write a stub
    pysource and expect it to be used passed on a machine that had one and failed on
    a machine that did not - with no difference in the code under test. The autouse
    fixture in conftest points AXQUA_HOME at a tmp dir; this asserts it is in force,
    because a fixture that silently stops working would put the divergence straight
    back.
    """
    import os

    assert "AXQUA_HOME" in os.environ
    settings = machine.settings_path()
    assert not settings.is_file(), f"the suite is reading real settings at {settings}"
    for var in machine.ENV_VARS.values():
        assert var not in os.environ


def test_nothing_configured_resolves_to_nothing_not_to_a_guess(clean_env,
                                                               monkeypatch):
    monkeypatch.setattr(machine, "DISCOVERY", {"telemac": ()})
    got = machine.resolve("telemac")
    assert not got
    assert got.path is None
    # and it says how to fix it rather than just failing
    assert "AXQUA_TELEMAC_PYSOURCE" in got.line("telemac")


def test_discovery_finds_a_conventional_install(clean_env, monkeypatch, tmp_path):
    """A fresh checkout on a machine that has the solver should need no config."""
    root = tmp_path / "site"
    install = root / "OpenFOAM-9" / "etc"
    install.mkdir(parents=True)
    (install / "bashrc").write_text("# stub\n")
    monkeypatch.setenv("AXQUA_SOLVER_ROOT", str(root))
    got = machine.resolve("openfoam")
    assert got.path == install / "bashrc"
    assert got.source == "discovered"


def test_write_settings_merges_rather_than_replaces(clean_env):
    """Setting OpenFOAM must not silently drop a working TELEMAC entry."""
    machine.write_settings({"telemac": "/t.sh"})
    machine.write_settings({"openfoam": "/o/bashrc"})
    stored = yaml.safe_load(machine.settings_path().read_text())
    assert stored == {"telemac": "/t.sh", "openfoam": "/o/bashrc"}


def test_settings_live_outside_any_repository(clean_env, monkeypatch, tmp_path):
    """XDG by default - it cannot be committed because it is not in the tree."""
    monkeypatch.delenv("AXQUA_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert machine.settings_path() == tmp_path / "xdg" / "axqua" / "solvers.yml"


def test_an_unreadable_settings_file_does_not_kill_the_build(clean_env, caplog):
    machine.settings_path().parent.mkdir(parents=True, exist_ok=True)
    machine.settings_path().write_text("{ this is: not: valid yaml\n")
    with caplog.at_level("WARNING"):
        got = machine.resolve("telemac", configured="/fallback.sh")
    assert got.path == Path("/fallback.sh")       # fell through, did not raise


def test_report_names_the_rule_that_won(clean_env):
    machine.write_settings({"telemac": "/t.sh"})
    line = next(row for row in machine.report() if row.startswith("telemac:"))
    assert "/t.sh" in line and "solvers.yml" in line


# --------------------------------------------------------------------------- #
# the repository itself
# --------------------------------------------------------------------------- #
def test_no_tracked_source_or_test_hard_codes_a_home_directory():
    """The rule this module exists to enforce, checked against the tree.

    aXqua ships as a QGIS plugin: a path under someone's /home makes a case
    unopenable by anyone else and turns every checkout into a merge conflict.
    Case configs are exempt - they carry a placeholder a user is meant to edit,
    or nothing at all - but src/ and tests/ must never name one.
    """
    import re
    import subprocess

    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        ["git", "grep", "-nE", r"/home/[a-z]+/", "--", "src", "tests"],
        cwd=root, capture_output=True, text=True).stdout
    offenders = [ln for ln in out.splitlines()
                 if not re.search(r"#|\"\"\"|'''", ln.split(":", 2)[-1][:4])]
    assert not offenders, "hard-coded home directory:\n" + "\n".join(offenders)


def test_a_synthesisable_outflow_rating_is_not_a_validation_error(tmp_path):
    """`rating_method` exists to say how a MISSING rating is synthesised, so refusing
    to validate without one contradicted the config's own documented behaviour.

    munich-vsf is the case: `preprocessing.py` runs fine because
    `workflow.prepare_steady_inputs` synthesises the rating from the outflow line and
    the DEM, but a bare `load_config(...).validate()` raised.
    """
    from axqua.config import load_config

    for name in ("dem.tif", "roi.gpkg", "liquid.gpkg"):
        (tmp_path / name).touch()
    (tmp_path / "pysource.sh").write_text("# stand-in\n")

    def write(extra: str) -> Path:
        path = tmp_path / "case-config.yml"
        path.write_text(f"""
project:
  name: rating-case
  crs_epsg: 25832
telemac:
  pysource: pysource.sh
geodata:
{extra}  boundary: roi.gpkg
boundaries:
  liquid_boundaries: liquid.gpkg
  outflow_condition: stage_discharge
  prescribed_flowrate: 0.135
""")
        return path

    cfg = load_config(write("  dem_initial: dem.tif\n"))
    cfg.validate()                                       # must not raise
    assert cfg.boundaries.stage_discharge is None        # still to be synthesised
