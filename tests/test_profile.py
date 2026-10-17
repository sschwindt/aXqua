"""The profile of a computer, and the case file that no longer has to describe one.

Two rules are pinned here. A computer **without** a profile behaves exactly as it did
before the profile existed - that is what lets every installation and every tracked
case carry on untouched. And a computer **with** one takes its simulation software from
there, whatever a case file that was copied from somewhere else still says.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml

from axqua import cli
from axqua.config import dump_config, load_config
from axqua.core import casefile, machine, schema
from axqua.core import profile as profiles
from axqua.core.diagnostics import ERROR, WARNING, Finding, has_errors, worst
from axqua.core.errors import ConfigError
from axqua.jobs import profiles as job_profiles

MINIMAL = """
project:
  name: profile-demo
  crs_epsg: 25832
telemac:
{telemac}
geodata:
  dem_initial: dem.tif
  boundary: roi.gpkg
boundaries:
  liquid_boundaries: liquid.gpkg
  prescribed_flowrate: 2.4
  prescribed_elevation: 100.0
"""


def _case(folder: Path, telemac: str = "  n_processors: 2", name: str = "case-config.yml"
          ) -> Path:
    for item in ("dem.tif", "roi.gpkg", "liquid.gpkg"):
        (folder / item).touch()
    target = folder / name
    target.write_text(MINIMAL.format(telemac=telemac), encoding="utf-8")
    return target


def _profile(tmp_path: Path, **solvers) -> profiles.AxquaProfile:
    """A saved default profile binding the given ``solver=script`` pairs."""
    profile = profiles.AxquaProfile(name="bench")
    for solver, script in solvers.items():
        profile.solvers[solver] = profiles.SolverBinding(setup_script=Path(script))
    profiles.save(profile, profiles.default_path())
    return profile


# ------------------------------------------------------------------- the file itself


def test_a_profile_round_trips(tmp_path):
    profile = profiles.AxquaProfile(
        name="bench", python=Path("/env/bin/python"), axqua=Path("/env/bin/axqua"),
        solvers={"telemac": profiles.SolverBinding(setup_script=Path("/t/pysource.sh"),
                                                   mpi_processes=12),
                 "openfoam": profiles.SolverBinding(setup_script=Path("/o/etc/bashrc"),
                                                    environment="wsl", distro="Ubuntu")},
        postprocessors={"visit": Path("/v/bin/visit")},
        job_root=Path("/scratch/jobs"), launcher="posix", min_depth=0.02)
    written = profiles.save(profile, tmp_path / "bench.axq-profile")
    again = profiles.load(written)
    assert again.as_dict() == profile.as_dict()
    assert again.solvers["telemac"].mpi_processes == 12
    assert again.path == written


def test_a_written_profile_records_decisions_not_defaults(tmp_path):
    written = profiles.save(profiles.AxquaProfile(name="bare"), tmp_path / "bare.axq-profile")
    stored = yaml.safe_load(written.read_text())
    assert set(stored) == {"schema_version", "name", "display"}


def test_the_suffix_is_added_rather_than_guessed_at(tmp_path):
    assert profiles.save(profiles.AxquaProfile(), tmp_path / "office").name == \
        "office.axq-profile"


def test_a_misspelled_key_is_named_with_its_place(tmp_path, caplog):
    """A silently ignored key is a setting the user believes is in force. The rest of
    the profile stays in use, and the key is named wherever the profile is looked at."""
    target = tmp_path / "typo.axq-profile"
    target.write_text("solvers:\n  telemac:\n    setup_scirpt: /x.sh\n"
                      "  openfoam:\n    setup_script: /o/etc/bashrc\n")
    profile = profiles.load(target)
    assert profile.solvers["openfoam"].setup_script == Path("/o/etc/bashrc")
    found = [f for f in profiles.check(profile, probe=False)
             if f.code == "axqua.config.unknown_entry"]
    assert [f.subject for f in found] == ["solvers.telemac.setup_scirpt"]
    assert found[0].severity == WARNING and "setup_script" in found[0].remedy
    with caplog.at_level(logging.WARNING, logger="axqua"):
        profiles.load_active(target)
    assert "solvers.telemac.setup_scirpt" in caplog.text


def test_a_profile_of_a_newer_axqua_is_refused_clearly(tmp_path):
    target = tmp_path / "future.axq-profile"
    target.write_text("schema_version: 99\n")
    with pytest.raises(ConfigError, match="newer aXqua"):
        profiles.load(target)


# ------------------------------------- a profile outlives the version that wrote it

#: A profile exactly as aXqua 0.4.2 wrote it, before installation folders existed.
#: This text is never edited: every later version has to read it.
WRITTEN_BY_0_4_2 = """\
schema_version: 1
name: workstation
python:
  executable: /opt/miniforge3/envs/axqua-env/bin/python
  axqua: /opt/miniforge3/envs/axqua-env/bin/axqua
solvers:
  telemac:
    setup_script: /opt/telemac-mascaret/configs/pysource.debian12.sh
    config_name: debgfopenmpi
    mpi_processes: 12
    overrides:
      USETELCFG: debgfopenmpi
  openfoam:
    setup_script: /usr/lib/openfoam/openfoam2406/etc/bashrc
    environment: posix
    mpi_processes: 16
postprocessors:
  paraview: /usr/bin/paraview
  visit: /opt/visit/bin/visit
jobs:
  root: /scratch/axqua-jobs
  launcher: systemd
display:
  min_depth: 0.02
  velocity_cap: 4.0
"""


def test_a_profile_of_an_earlier_version_still_loads_and_loses_nothing(tmp_path):
    target = tmp_path / "old.axq-profile"
    target.write_text(WRITTEN_BY_0_4_2)
    profile = profiles.load(target)
    assert profile.script("telemac") == \
        Path("/opt/telemac-mascaret/configs/pysource.debian12.sh")
    assert profile.solvers["telemac"].config_name == "debgfopenmpi"
    assert profile.job_root == Path("/scratch/axqua-jobs") and profile.min_depth == 0.02
    assert profile.unknown_entries() == []
    profiles.save(profile, target)
    assert yaml.safe_load(target.read_text()) == yaml.safe_load(WRITTEN_BY_0_4_2)
    assert profile.backup is None


def test_an_entry_of_a_newer_version_survives_a_save(tmp_path):
    """The plugin and aXqua are updated separately, so each may meet a profile the
    other wrote. What one of them does not know, it must not delete."""
    later = yaml.safe_load(WRITTEN_BY_0_4_2)
    later["cluster"] = {"queue": "long", "nodes": 4}
    later["jobs"]["keep_days"] = 30
    later["solvers"]["telemac"]["modules"] = ["gcc/13", "openmpi/5"]
    later["solvers"]["delft3d"] = {"setup_script": "/opt/delft3d/env.sh"}
    later["postprocessors"]["tecplot"] = "/opt/tecplot/bin/tec360"
    target = tmp_path / "later.axq-profile"
    target.write_text(yaml.safe_dump(later))

    profile = profiles.load(target)
    assert sorted(profile.unknown_entries()) == [
        "cluster", "jobs.keep_days", "postprocessors.tecplot", "solvers.delft3d",
        "solvers.telemac.modules"]
    assert profile.script("telemac") is not None             # the rest is in use
    profile.solvers["telemac"].mpi_processes = 8             # an edit by this version
    profiles.save(profile, target)
    stored = yaml.safe_load(target.read_text())
    later["solvers"]["telemac"]["mpi_processes"] = 8
    assert stored == later


def test_text_where_a_number_belongs_is_a_message_and_not_a_crash(tmp_path, caplog):
    """The profile is read on every case load."""
    profiles.default_path().parent.mkdir(parents=True, exist_ok=True)
    for text, subject in (
            ("solvers:\n  telemac:\n    mpi_processes: many\n",
             "solvers.telemac.mpi_processes"),
            ("display:\n  min_depth: shallow\n", "display.min_depth"),
            ("solvers: 3\n", "solvers"),
            ("schema_version: one\n", "schema_version")):
        profiles.default_path().write_text(text)
        with pytest.raises(ConfigError) as caught:
            profiles.load(profiles.default_path())
        assert caught.value.subject == subject
        assert machine.resolve("telemac", configured="/fallback.sh").path == \
            Path("/fallback.sh")


def test_a_file_that_could_not_be_read_is_kept_when_a_profile_is_saved_over_it(tmp_path):
    """Creating a profile where one of a newer aXqua lies must not cost that one."""
    target = tmp_path / "default.axq-profile"
    newer = "schema_version: 99\nfleet:\n  - a\n  - b\n"
    target.write_text(newer)
    profile = profiles.AxquaProfile(name="made here")
    profiles.save(profile, target)
    assert profiles.load(target).name == "made here"
    assert profile.backup is not None and profile.backup.parent == tmp_path
    assert profile.backup.name.startswith("default.axq-profile.") \
        and profile.backup.suffix == ".bak"
    assert profile.backup.read_text() == newer
    # ... and a profile that can be read is simply replaced: the editor carried it.
    profiles.save(profiles.AxquaProfile(name="again"), target)
    assert sorted(path.name for path in tmp_path.iterdir()) == \
        sorted(["default.axq-profile", profile.backup.name])


def test_the_profile_is_kept_outside_of_axqua_and_of_the_plugin(monkeypatch, tmp_path):
    """Updating aXqua or the plugin replaces their folders. The profile is in neither,
    so an update finds it where it was."""
    import axqua

    for var in ("AXQUA_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    where = profiles.default_path()
    assert where == tmp_path / ".config" / "axqua" / "default.axq-profile"
    package = Path(axqua.__file__).resolve().parent
    repository = Path(__file__).resolve().parent.parent
    for folder in (package, repository / "qgis_plugin"):
        assert folder not in where.resolve().parents
    # QGIS keeps a plugin in the folder python/plugins of its own user profile.
    assert "plugins" not in where.parts and "QGIS" not in where.parts


def test_a_relative_path_is_relative_to_the_profile(tmp_path):
    target = tmp_path / "bundle" / "here.axq-profile"
    target.parent.mkdir()
    target.write_text("solvers:\n  telemac:\n    setup_script: telemac/pysource.sh\n")
    script = profiles.load(target).solvers["telemac"].setup_script
    assert script == (tmp_path / "bundle" / "telemac" / "pysource.sh").resolve()


def test_a_wsl_path_is_kept_as_written(tmp_path):
    """It is a path inside the Linux distribution; the host cannot resolve it."""
    target = tmp_path / "wsl.axq-profile"
    target.write_text("solvers:\n  openfoam:\n    environment: wsl\n"
                      "    setup_script: opt/openfoam/etc/bashrc\n")
    assert profiles.load(target).solvers["openfoam"].setup_script == \
        Path("opt/openfoam/etc/bashrc")


# -------------------------------------------------------------- which one is active


def test_a_computer_without_a_profile_has_none():
    assert profiles.active_path() is None
    assert profiles.load_active() is None


def test_the_default_profile_is_found_beside_the_older_settings(tmp_path):
    _profile(tmp_path, telemac="/t/pysource.sh")
    assert profiles.active_path() == machine.settings_path().parent / "default.axq-profile"
    assert profiles.load_active().name == "bench"


def test_the_environment_names_another_profile(tmp_path, monkeypatch):
    _profile(tmp_path, telemac="/default/pysource.sh")
    other = profiles.save(profiles.AxquaProfile(name="cluster"), tmp_path / "c.axq-profile")
    monkeypatch.setenv("AXQUA_PROFILE", str(other))
    assert profiles.load_active().name == "cluster"


def test_a_broken_profile_does_not_stop_a_case_from_loading(tmp_path, caplog):
    """It is read on every case load, and the older settings can still serve the build."""
    profiles.default_path().parent.mkdir(parents=True, exist_ok=True)
    profiles.default_path().write_text("solvers: [this, is, not, a, mapping\n")
    with caplog.at_level(logging.WARNING, logger="axqua"):
        got = machine.resolve("telemac", configured="/fallback.sh")
    assert got.path == Path("/fallback.sh")
    assert "could not read the profile" in caplog.text
    with pytest.raises(ConfigError):
        profiles.load_active(strict=True)


def test_an_edited_profile_is_read_again(tmp_path):
    _profile(tmp_path, telemac="/first.sh")
    assert machine.resolve("telemac").path == Path("/first.sh")
    _profile(tmp_path, telemac="/second.sh")
    assert machine.resolve("telemac").path == Path("/second.sh")


# ------------------------------------------------- where it sits in the resolution


def test_the_profile_beats_the_older_settings_file_and_the_case(tmp_path):
    machine.write_settings({"telemac": "/from/solvers-yml.sh"})
    _profile(tmp_path, telemac="/from/profile.sh")
    got = machine.resolve("telemac", configured="/from/case.sh")
    assert got.path == Path("/from/profile.sh")
    assert got.source.endswith(".axq-profile")


def test_the_environment_and_a_case_local_override_still_beat_the_profile(
        tmp_path, monkeypatch):
    _profile(tmp_path, telemac="/from/profile.sh")
    (tmp_path / "solvers.local.yml").write_text("telemac: /from/local.sh\n")
    assert machine.resolve("telemac", case_dir=tmp_path).path == Path("/from/local.sh")
    monkeypatch.setenv("AXQUA_TELEMAC_PYSOURCE", "/from/env.sh")
    assert machine.resolve("telemac", case_dir=tmp_path).path == Path("/from/env.sh")


def test_a_profile_that_does_not_bind_a_code_lets_the_older_settings_answer(tmp_path):
    machine.write_settings({"openfoam": "/from/solvers-yml/bashrc"})
    _profile(tmp_path, telemac="/from/profile.sh")
    assert machine.resolve("openfoam").path == Path("/from/solvers-yml/bashrc")


def test_a_postprocessor_is_resolved_like_a_solver(tmp_path):
    profile = profiles.AxquaProfile(postprocessors={"paraview": Path("/p/bin/paraview")})
    profiles.save(profile, profiles.default_path())
    assert machine.resolve("paraview").path == Path("/p/bin/paraview")


def test_the_visit_launcher_of_the_profile_reaches_the_case(tmp_path):
    """A case file names no launcher; the computer does."""
    profile = profiles.AxquaProfile(postprocessors={"visit": Path("/v/bin/visit")})
    profiles.save(profile, profiles.default_path())
    assert load_config(_case(tmp_path)).postproc.visit == Path("/v/bin/visit")


def test_detection_sees_the_settings_the_profile_will_replace(tmp_path):
    """Not the profile itself - otherwise a wrong entry could never be re-detected."""
    machine.write_settings({"telemac": "/from/solvers-yml.sh"})
    _profile(tmp_path, telemac="/stale/profile.sh")
    assert profiles.detect().solvers["telemac"].setup_script == Path("/from/solvers-yml.sh")


# ------------------------------------------------------------ installation folders


def _installed(root: Path, *files: str) -> Path:
    """A folder laid out like an installation, with the named files in it."""
    for name in files:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\n")
        target.chmod(0o755)
    return root


def test_a_profile_of_installation_folders_is_a_complete_one(tmp_path):
    """The folder is what a person knows about an installation. The environment script
    and the launcher are found in it."""
    telemac = _installed(tmp_path / "telemac-mascaret", "configs/pysource.debian12.sh",
                         "configs/pysource.template.sh")
    visit = _installed(tmp_path / "visit", "bin/visit")
    profile = profiles.AxquaProfile(name="folders",
                                    installations={"telemac": telemac, "visit": visit})
    profiles.save(profile, profiles.default_path())
    stored = yaml.safe_load(profiles.default_path().read_text())
    assert stored["installations"] == {"telemac": str(telemac), "visit": str(visit)}
    assert "solvers" not in stored and "postprocessors" not in stored

    script, launcher = telemac / "configs" / "pysource.debian12.sh", visit / "bin" / "visit"
    assert profiles.load_active().programs() == {"telemac": str(script),
                                                 "visit": str(launcher)}
    assert machine.resolve("telemac").path == script
    assert machine.resolve("visit").path == launcher
    found = job_profiles.implicit(load_config(_case(tmp_path)), "telemac")
    assert (found.name, found.setup_script) == ("folders:telemac", script)
    assert not [f for f in profiles.check(profiles.load_active(), probe=False)
                if f.subject.startswith(("installations", "solvers.telemac",
                                         "postprocessors"))]


def test_an_entry_of_the_profile_outranks_what_its_folder_holds(tmp_path):
    telemac = _installed(tmp_path / "telemac", "configs/pysource.a.sh")
    other = _installed(tmp_path / "elsewhere", "pysource.mine.sh") / "pysource.mine.sh"
    profile = profiles.AxquaProfile(
        installations={"telemac": telemac},
        solvers={"telemac": profiles.SolverBinding(setup_script=other)})
    assert profile.script("telemac") == other
    found = [f for f in profiles.check(profile, probe=False)
             if f.subject == "installations.telemac"]
    assert [f.code for f in found] == ["axqua.environment.installation_mismatch"]
    assert found[0].severity == WARNING and "aXqua uses the environment script" \
        in found[0].message


def test_which_of_several_environment_scripts_is_meant_is_not_guessed(tmp_path):
    telemac = _installed(tmp_path / "telemac", "configs/pysource.gnu.sh",
                         "configs/pysource.intel.sh", "configs/pysource.template.sh")
    assert [path.name for path in profiles.candidates("telemac", telemac)] == \
        ["pysource.gnu.sh", "pysource.intel.sh"]
    assert profiles.locate("telemac", telemac) is None
    profile = profiles.AxquaProfile(installations={"telemac": telemac})
    assert profile.script("telemac") is None
    found = {f.subject: f for f in profiles.check(profile, probe=False)}
    unbound = found["solvers.telemac.setup_script"]
    assert unbound.code == "axqua.environment.solver_unbound"
    assert "pysource.gnu.sh" in unbound.message and "pysource.intel.sh" in unbound.message


def test_the_folder_an_installation_was_put_in_is_accepted_too(tmp_path):
    """A wizard is given the folder above: TELEMAC lies in telemac-mascaret below it,
    VisIt in apps/visit-<version> below it."""
    root = _installed(tmp_path / "opt", "telemac-mascaret/configs/pysource.mint22.sh",
                      "apps/visit-3.5.0-ubuntu24/bin/visit")
    script = profiles.locate("telemac", root)
    launcher = profiles.locate("visit", root)
    assert script == root / "telemac-mascaret" / "configs" / "pysource.mint22.sh"
    assert launcher == root / "apps" / "visit-3.5.0-ubuntu24" / "bin" / "visit"
    assert profiles.installation_of("telemac", script) == root / "telemac-mascaret"
    assert profiles.installation_of("visit", launcher) == \
        root / "apps" / "visit-3.5.0-ubuntu24"
    assert profiles.locate("openfoam", root) is None


def test_the_installation_folder_is_told_from_the_script(tmp_path):
    stack = _installed(tmp_path / "stack", "shell-rc.sh", "etc/bashrc")
    assert profiles.locate("openfoam", stack) == stack / "shell-rc.sh"   # with the solvers
    assert profiles.installation_of("openfoam", stack / "shell-rc.sh") == stack
    assert profiles.installation_of(
        "openfoam", "/usr/lib/openfoam/openfoam2406/etc/bashrc") == \
        Path("/usr/lib/openfoam/openfoam2406")
    assert profiles.installation_of("paraview", "/opt/ParaView-6.1.1/bin/paraview") == \
        Path("/opt/ParaView-6.1.1")
    # A program of the operating system is installed "in /usr", which says nothing ...
    assert profiles.installation_of("paraview", "/usr/bin/paraview") is None
    assert profiles.installation_of("telemac", "/somewhere/pysource.sh") is None
    # ... and a link in a system folder is followed to the installation it starts.
    visit = _installed(tmp_path / "visit", "bin/visit")
    link = tmp_path / "bin" / "visit"
    link.parent.mkdir()
    link.symlink_to(visit / "bin" / "visit")
    assert profiles.installation_of("visit", link) == visit


def test_detection_enters_the_folders_of_what_it_finds(tmp_path, monkeypatch):
    telemac = _installed(tmp_path / "telemac-mascaret", "configs/pysource.debian12.sh")
    monkeypatch.setattr(machine, "DISCOVERY", {})
    monkeypatch.setattr(profiles.shutil, "which",
                        lambda name: "/usr/bin/paraview" if name == "paraview" else None)
    machine.write_settings({"telemac": str(telemac / "configs" / "pysource.debian12.sh")})
    found = profiles.detect()
    assert found.installations == {"telemac": telemac}
    assert found.postprocessors == {"paraview": Path("/usr/bin/paraview")}


def test_a_folder_that_is_gone_or_holds_no_program_is_reported(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    profile = profiles.AxquaProfile(installations={
        "telemac": tmp_path / "gone", "openfoam": empty, "paraview": empty})
    found = {f.subject: f for f in profiles.check(profile, probe=False)}
    assert found["installations.telemac"].code == "axqua.environment.installation_missing"
    assert "no environment script of openfoam was found" in \
        found["solvers.openfoam.setup_script"].message
    assert found["postprocessors.paraview"].code == "axqua.environment.program_missing"
    assert "installations.openfoam" not in found                 # the folder is there


# ---------------------------------------------------------------------- the checks


def test_a_check_reports_everything_and_raises_nothing(tmp_path):
    profile = profiles.AxquaProfile(
        python=tmp_path / "no-python", launcher="carrier-pigeon", min_depth=-1.0,
        solvers={"telemac": profiles.SolverBinding(setup_script=tmp_path / "gone.sh")},
        postprocessors={"visit": tmp_path / "no-visit"})
    findings = profiles.check(profile, probe=False)
    by_subject = {f.subject: f for f in findings}
    assert by_subject["solvers.telemac.setup_script"].code == \
        "axqua.environment.script_missing"
    assert by_subject["python.executable"].severity == ERROR
    assert by_subject["jobs.launcher"].code == "axqua.config.invalid_value"
    assert by_subject["display.min_depth"].severity == ERROR
    assert by_subject["postprocessors.visit"].severity == ERROR
    # OpenFOAM is simply not used on this computer: worth saying, not an error.
    assert by_subject["solvers.openfoam.setup_script"].severity == WARNING
    assert has_errors(findings) and worst(findings) == ERROR


def test_a_working_installation_passes_the_functional_check(tmp_path, fake_pysource):
    profile = profiles.AxquaProfile(
        solvers={"telemac": profiles.SolverBinding(setup_script=fake_pysource)})
    findings = [f for f in profiles.check(profile, probe=True)
                if f.subject.startswith("solvers.telemac")]
    assert not has_errors(findings)


def test_a_script_that_exists_but_is_not_the_code_fails_the_functional_check(tmp_path):
    """The reason the functional tier exists: the file check passes this one."""
    impostor = tmp_path / "not-telemac.sh"
    impostor.write_text("export SOMETHING_ELSE=1\n")
    profile = profiles.AxquaProfile(
        solvers={"telemac": profiles.SolverBinding(setup_script=impostor)})
    assert not has_errors(f for f in profiles.check(profile, probe=False)
                          if f.subject.startswith("solvers.telemac"))
    probed = profiles.check(profile, probe=True)
    assert any(f.code == "axqua.environment.solver_unreachable" for f in probed)


def test_another_openfoam_release_is_a_warning_with_the_release_named(
        tmp_path, fake_bashrc, monkeypatch):
    profile = profiles.AxquaProfile(
        solvers={"openfoam": profiles.SolverBinding(setup_script=fake_bashrc)})
    monkeypatch.setattr(profiles, "OPENFOAM_RELEASE", "9999")
    findings = profiles.check(profile, probe=True)
    version = [f for f in findings if f.code == "axqua.environment.openfoam_version"]
    assert version and version[0].severity == WARNING and "v9999" in version[0].message


def test_more_processes_than_cores_is_noticed(tmp_path, fake_pysource):
    profile = profiles.AxquaProfile(solvers={"telemac": profiles.SolverBinding(
        setup_script=fake_pysource, mpi_processes=10 ** 6)})
    assert any(f.code == "axqua.environment.too_many_processes"
               for f in profiles.check(profile, probe=False))


def test_a_finding_is_json_safe_and_names_its_documentation_anchor():
    finding = Finding(WARNING, "axqua.environment.solver_unbound", "no script",
                      subject="solvers.openfoam.setup_script")
    payload = json.loads(json.dumps(finding.as_dict()))
    assert payload["anchor"] == "axqua-environment-solver-unbound"
    assert "remedy" not in payload
    with pytest.raises(ValueError):
        Finding("notice", "axqua.x", "not a severity")


# ----------------------------------------------------------------- the job system


def test_a_job_takes_its_binding_from_the_active_profile(tmp_path, fake_pysource):
    cfg = load_config(_case(tmp_path))
    assert job_profiles.implicit(cfg, "telemac").name == "telemac-from-config"

    profile = profiles.AxquaProfile(
        name="bench", job_root=tmp_path / "scratch", launcher="posix",
        solvers={"telemac": profiles.SolverBinding(setup_script=fake_pysource,
                                                   mpi_processes=6)})
    profiles.save(profile, profiles.default_path())
    found = job_profiles.implicit(load_config(_case(tmp_path)), "telemac")
    assert found.name == "bench:telemac"
    assert (found.setup_script, found.mpi_processes) == (fake_pysource, 6)
    assert (found.working_root, found.launcher) == (tmp_path / "scratch", "posix")


def test_every_job_verb_looks_in_the_folder_the_profile_names(tmp_path, monkeypatch):
    """``submit`` put a job into the folder of the profile, and ``list``, ``status``,
    ``logs`` and ``cancel`` then looked in the default folder and did not find it."""
    from axqua.jobs import paths as job_paths

    monkeypatch.delenv(job_paths.ENV_JOB_ROOT, raising=False)
    assert job_paths.job_root() == job_paths.data_dir() / "jobs"       # no profile yet
    profiles.save(profiles.AxquaProfile(name="bench", job_root=tmp_path / "scratch"),
                  profiles.default_path())
    assert job_paths.job_root() == (tmp_path / "scratch").resolve()
    # the more specific settings still win
    monkeypatch.setenv(job_paths.ENV_JOB_ROOT, str(tmp_path / "from-env"))
    assert job_paths.job_root() == (tmp_path / "from-env").resolve()
    assert job_paths.job_root(explicit=tmp_path / "flag") == (tmp_path / "flag").resolve()


def test_a_binding_without_a_process_count_leaves_it_to_the_case(tmp_path, fake_pysource):
    _profile(tmp_path, telemac=str(fake_pysource))
    cfg = load_config(_case(tmp_path, telemac="  n_processors: 7"))
    assert job_profiles.implicit(cfg, "telemac").mpi_processes == 7


def test_submit_accepts_a_profile_file_and_picks_the_code_from_the_kind(
        tmp_path, fake_pysource, fake_bashrc):
    target = profiles.save(profiles.AxquaProfile(name="two", solvers={
        "telemac": profiles.SolverBinding(setup_script=fake_pysource),
        "openfoam": profiles.SolverBinding(setup_script=fake_bashrc)}),
        tmp_path / "two.axq-profile")
    cfg = load_config(_case(tmp_path))
    assert job_profiles.resolve(str(target), solver="openfoam", cfg=cfg).setup_script == \
        fake_bashrc
    with pytest.raises(ConfigError, match="no binding"):
        job_profiles.resolve(str(profiles.save(profiles.AxquaProfile(),
                                               tmp_path / "empty.axq-profile")),
                             solver="telemac", cfg=cfg)


def test_a_job_computes_with_the_script_its_case_was_loaded_with(tmp_path, fake_pysource,
                                                                 monkeypatch):
    """The environment variable and a ``solvers.local.yml`` outrank the profile when a
    case is loaded. A job took the script of the profile all the same, and so computed
    with another installation than the one its case named."""
    of_the_profile = tmp_path / "of-the-profile.sh"
    of_the_profile.touch()
    profiles.save(profiles.AxquaProfile(name="bench", launcher="posix", solvers={
        "telemac": profiles.SolverBinding(
            setup_script=of_the_profile, mpi_processes=6, config_name="gnu.dynamic",
            overrides={"TELEMAC_TMP": "/scratch"})}), profiles.default_path())
    case = _case(tmp_path)
    plain = job_profiles.implicit(load_config(case), "telemac")
    assert (plain.name, plain.setup_script) == ("bench:telemac", of_the_profile)
    assert (plain.config_name, plain.overrides) == ("gnu.dynamic",
                                                    {"TELEMAC_TMP": "/scratch"})

    (tmp_path / "solvers.local.yml").write_text(f"telemac: {fake_pysource}\n")
    cfg = load_config(case)
    local = job_profiles.implicit(cfg, "telemac")
    assert local.setup_script == cfg.telemac.pysource == fake_pysource
    assert "solvers.local.yml" in local.name            # the job record says which one
    # what the profile says about the computer stays ...
    assert (local.mpi_processes, local.launcher) == (6, "posix")
    # ... and what it holds for its own installation is not applied to another one
    assert (local.config_name, local.overrides) == (None, {})

    other = tmp_path / "from-the-environment.sh"
    other.touch()
    monkeypatch.setenv("AXQUA_TELEMAC_PYSOURCE", str(other))
    cfg = load_config(case)
    assert job_profiles.implicit(cfg, "telemac").setup_script == cfg.telemac.pysource \
        == other

    # A variable that names the installation of the profile changes nothing.
    (tmp_path / "solvers.local.yml").unlink()
    monkeypatch.setenv("AXQUA_TELEMAC_PYSOURCE", str(of_the_profile))
    same = job_profiles.implicit(load_config(case), "telemac")
    assert (same.name, same.config_name) == ("bench:telemac", "gnu.dynamic")


def test_a_named_profile_is_not_taken_for_a_file_of_the_same_name(tmp_path, fake_pysource,
                                                                  fake_bashrc, monkeypatch):
    """``--profile cluster`` in a folder that happens to hold a file ``cluster``."""
    job_profiles.save_profiles({"cluster": job_profiles.Profile(
        name="cluster", setup_script=fake_pysource)})
    (tmp_path / "cluster").write_text("some notes, not a profile\n")
    monkeypatch.chdir(tmp_path)
    assert job_profiles.resolve("cluster", solver="telemac").setup_script == fake_pysource

    # A profile file without the suffix is still accepted, after the names ...
    plain = tmp_path / "office"
    plain.write_text(yaml.safe_dump(profiles.AxquaProfile(name="office", solvers={
        "openfoam": profiles.SolverBinding(setup_script=fake_bashrc)}).as_dict()))
    assert job_profiles.resolve("office", solver="openfoam").setup_script == fake_bashrc
    # ... and a name that is neither is named as what it was asked for.
    with pytest.raises(ConfigError, match="no solver profile named 'nowhere'"):
        job_profiles.resolve("nowhere", solver="telemac")


# ------------------------------------------------------------------ the case file


def test_a_case_file_needs_no_machine_settings_once_a_profile_exists(
        tmp_path, fake_pysource):
    _profile(tmp_path, telemac=str(fake_pysource))
    cfg = load_config(_case(tmp_path, name="demo.axq-case"))
    assert cfg.telemac.pysource == fake_pysource
    cfg.validate()


def test_a_portable_dump_leaves_the_computer_out_and_still_loads(tmp_path, fake_pysource):
    cfg = load_config(_case(tmp_path, telemac=f"  pysource: {fake_pysource}\n"
                                              "  n_processors: 3"))
    full = yaml.safe_load(dump_config(cfg))
    portable = yaml.safe_load(dump_config(cfg, portable=True))
    assert "pysource" in full["telemac"]
    for block, names in schema.MACHINE_FIELDS.items():
        assert not set(names) & set(portable.get(block, {})), block
    # What says something about the model stays, and the block still declares the code.
    assert portable["telemac"]["n_processors"] == 3

    _profile(tmp_path, telemac=str(fake_pysource))
    target = tmp_path / "shared.axq-case"
    dump_config(cfg, target, portable=True)
    again = load_config(target)
    assert again.telemac.pysource == fake_pysource
    assert "telemac" in again.declared_blocks


def test_migrate_writes_the_case_file_beside_the_original(tmp_path, fake_pysource, caplog):
    source = _case(tmp_path, telemac=f"  pysource: {fake_pysource}")
    with caplog.at_level(logging.INFO, logger="axqua"):
        assert cli.main(["migrate", str(source), "--to-case"]) == 0
    written = tmp_path / f"{tmp_path.name}.axq-case"
    assert written.is_file() and source.is_file()
    assert "pysource" not in yaml.safe_load(written.read_text()).get("telemac", {})
    # It says what it left out, and where that lives now.
    assert "telemac.pysource" in caplog.text and "profile" in caplog.text


def test_a_variant_keeps_the_name_that_tells_it_apart(tmp_path, fake_pysource):
    source = _case(tmp_path, telemac=f"  pysource: {fake_pysource}",
                   name="case-config-vof.yml")
    assert cli.main(["migrate", str(source), "--to-case"]) == 0
    assert (tmp_path / "case-config-vof.axq-case").is_file()


def test_migrate_does_not_replace_a_case_file_that_nobody_named(tmp_path, fake_pysource,
                                                                caplog):
    """The name of the new file is derived, so running the command a second time used
    to write over a case file that had been edited since the first."""
    source = _case(tmp_path, telemac=f"  pysource: {fake_pysource}")
    assert cli.main(["migrate", str(source), "--to-case"]) == 0
    written = tmp_path / f"{tmp_path.name}.axq-case"
    written.write_text(written.read_text() + "# a note written by hand\n")

    with caplog.at_level(logging.ERROR, logger="axqua"):
        assert cli.main(["migrate", str(source), "--to-case"]) == 1
    assert "a note written by hand" in written.read_text()
    assert f"-o {written}" in caplog.text               # and it says how to replace it
    assert cli.main(["migrate", str(source), "--to-case", "-o", str(written)]) == 0
    assert "a note written by hand" not in written.read_text()


def test_migrate_brings_a_case_file_up_to_date_where_it_is(tmp_path, fake_pysource):
    """For a file that is a case file already, the derived name is the file itself."""
    _profile(tmp_path, telemac=str(fake_pysource))
    source = _case(tmp_path, name="reach.axq-case")
    before = source.read_text()
    assert cli.main(["migrate", str(source), "--to-case"]) == 0
    assert (tmp_path / "reach.axq-case.bak").read_text() == before
    assert yaml.safe_load(source.read_text())["project"]["name"] == "profile-demo"
    assert "pysource" not in yaml.safe_load(source.read_text()).get("telemac", {})
    # --in-place does not write where TELEMAC is on this computer into a case file either
    assert cli.main(["migrate", str(source), "--in-place"]) == 0
    assert "pysource" not in yaml.safe_load(source.read_text()).get("telemac", {})


def test_the_case_file_of_a_folder_is_found_without_being_named(tmp_path):
    with pytest.raises(ConfigError, match="no case file"):
        casefile.find_case_file(tmp_path)
    legacy = _case(tmp_path)
    assert casefile.find_case_file(tmp_path) == legacy
    current = tmp_path / "reach.axq-case"
    current.write_text(legacy.read_text())
    assert casefile.find_case_file(tmp_path) == current      # the new type wins
    (tmp_path / "reach-variant.axq-case").write_text(legacy.read_text())
    with pytest.raises(ConfigError, match="2 case files") as caught:
        casefile.find_case_file(tmp_path)                    # never a silent pick
    assert "reach-variant.axq-case" in str(caught.value)


# -------------------------------------------------------------------- the command


def _run(capsys, *argv) -> dict:
    code = cli.main(["profile", *argv, "--json"])
    payload = json.loads(capsys.readouterr().out)
    payload["exit"] = code
    return payload


def test_the_command_creates_shows_and_checks_a_profile(tmp_path, capsys, fake_pysource,
                                                        monkeypatch):
    # Detection also looks in the usual installation folders, and a developer's machine
    # may well have an OpenFOAM there. What is found must not depend on that.
    monkeypatch.setattr(machine, "DISCOVERY", {})
    monkeypatch.setattr(profiles.shutil, "which", lambda name: None)
    machine.write_settings({"telemac": str(fake_pysource)})

    missing = _run(capsys, "show")
    assert not missing["ok"] and "profile init" in missing["error"]["remedy"]

    created = _run(capsys, "init")
    assert created["ok"] and Path(created["data"]["path"]) == profiles.default_path()
    assert not _run(capsys, "init")["ok"]                     # never overwrites unasked
    assert _run(capsys, "init", "--force")["ok"]

    shown = _run(capsys, "show")
    assert shown["data"]["solvers"]["telemac"]["setup_script"] == str(fake_pysource)

    checked = _run(capsys, "check", "--no-probe")
    assert checked["ok"] and checked["exit"] == 0             # findings are a result
    assert {f["subject"] for f in checked["data"]["findings"]} >= \
        {"solvers.openfoam.setup_script"}


def test_the_command_finds_a_program_in_its_folder_and_says_what_is_run(
        tmp_path, capsys):
    """What the profile editor asks when a folder is selected, and what the
    Configuration tab shows."""
    root = _installed(tmp_path / "opt", "telemac-mascaret/configs/pysource.debian12.sh")
    home = root / "telemac-mascaret"
    script = home / "configs" / "pysource.debian12.sh"
    found = _run(capsys, "locate", "telemac", str(root))["data"]
    assert found == {"program": "telemac", "folder": str(home), "script": str(script),
                     "candidates": [str(script)]}
    nothing = _run(capsys, "locate", "visit", str(root))["data"]
    assert (nothing["script"], nothing["candidates"], nothing["folder"]) == \
        ("", [], str(root))
    assert not _run(capsys, "locate", "fluent", str(root))["ok"]
    assert not _run(capsys, "locate", "telemac")["ok"]

    profiles.save(profiles.AxquaProfile(installations={"telemac": home}),
                  profiles.default_path())
    shown = _run(capsys, "show")["data"]
    assert shown["programs"] == {"telemac": str(script)}
    assert shown["installations"] == {"telemac": str(home)} and "solvers" not in shown


def test_init_asks_about_the_file_it_is_going_to_write(tmp_path, capsys, monkeypatch):
    """``init office`` writes ``office.axq-profile``. It looked for a file ``office``,
    found none, and replaced the profile without being asked to."""
    monkeypatch.setattr(machine, "DISCOVERY", {})
    monkeypatch.setattr(profiles.shutil, "which", lambda name: None)
    kept = profiles.save(profiles.AxquaProfile(name="kept"), tmp_path / "office")
    assert kept == tmp_path / "office.axq-profile"

    refused = _run(capsys, "init", str(tmp_path / "office"))
    assert not refused["ok"] and "--force" in refused["error"]["remedy"]
    assert profiles.load(kept).name == "kept"
    assert _run(capsys, "init", str(tmp_path / "office"), "--force")["ok"]
    assert profiles.load(kept).name != "kept"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["office.axq-profile"]


def test_a_profile_is_written_back_to_the_file_it_was_read_from(tmp_path, capsys,
                                                                monkeypatch):
    """A profile may be kept in a file of any name. Saving it wrote a second file with
    the suffix beside it, which nothing read, so the change never took effect."""
    import io
    named = tmp_path / "this-computer.yml"
    named.write_text(yaml.safe_dump(profiles.AxquaProfile(name="first").as_dict()))
    monkeypatch.setenv(profiles.ENV_VAR, str(named))
    assert profiles.load_active(strict=True).name == "first"

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"name": "second"})))
    assert Path(_run(capsys, "write")["data"]["path"]) == named
    assert profiles.load_active(strict=True).name == "second"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["this-computer.yml"]

    # A name without a file gets the suffix, and is found under it again.
    monkeypatch.setenv(profiles.ENV_VAR, str(tmp_path / "office"))
    assert profiles.active_path() == tmp_path / "office.axq-profile"
    profiles.save(profiles.AxquaProfile(name="third"), profiles.active_path())
    assert profiles.load_active(strict=True).name == "third"


def test_the_command_replaces_a_profile_from_json_and_reports_findings(
        tmp_path, capsys, monkeypatch):
    import io
    document = {"name": "from-plugin",
                "solvers": {"telemac": {"setup_script": str(tmp_path / "gone.sh")}}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(document)))
    written = _run(capsys, "write")
    # Saving is never blocked by what is wrong with the content ...
    assert written["ok"] and profiles.load_active().name == "from-plugin"
    assert any(f["code"] == "axqua.environment.script_missing"
               for f in written["data"]["findings"])
    # ... but something that is not a profile does not replace one that is.
    for document in ({"solvers": ["telemac"]}, {"schema_version": 99},
                     {"display": {"min_depth": "shallow"}}):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(document)))
        assert not _run(capsys, "write")["ok"]
        assert profiles.load_active(strict=True).name == "from-plugin"
    # An entry that is not known is no reason to refuse: it is kept and reported.
    document = {"name": "from-plugin", "solvres": {}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(document)))
    written = _run(capsys, "write")
    assert written["ok"] and written["data"]["backup"] == ""
    assert [f["subject"] for f in written["data"]["findings"]
            if f["code"] == "axqua.config.unknown_entry"] == ["solvres"]


def test_the_command_says_where_an_unreadable_profile_was_kept(tmp_path, capsys,
                                                               monkeypatch):
    """What *Create profile* does on a computer whose profile cannot be read."""
    import io
    profiles.default_path().parent.mkdir(parents=True, exist_ok=True)
    profiles.default_path().write_text("schema_version: 99\n")
    assert not _run(capsys, "show")["ok"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"name": "new"})))
    written = _run(capsys, "write")
    kept = Path(written["data"]["backup"])
    assert written["ok"] and kept.read_text() == "schema_version: 99\n"
    assert profiles.load_active(strict=True).name == "new"


def test_every_code_of_the_profile_check_is_explained_in_the_documentation():
    """A triangle in the plugin opens the documentation at the anchor of its code. A
    code without an anchor opens the right page at the wrong place, silently."""
    import re

    root = Path(__file__).resolve().parent.parent
    # the check of the profile and the check of a case: both end as triangles
    source = "".join((root / "src" / "axqua" / "core" / name).read_text(encoding="utf-8")
                     for name in ("profile.py", "casecheck.py"))
    # and what the case settings of the plugin add on Save
    source += (root / "qgis_plugin" / "axqua" / "gui" / "case_editor.py").read_text(
        encoding="utf-8")
    codes = set(re.findall(r'"(axqua\.(?:environment|config)\.[a-z_]+)"', source))
    assert len(codes) >= 17
    pages = "".join((root / "docs" / "troubleshooting" / name).read_text(encoding="utf-8")
                    for name in ("warnings.rst", "errors.rst"))
    missing = sorted(code for code in codes
                     if f".. _{code.replace('.', '-').replace('_', '-')}:" not in pages)
    assert missing == []
