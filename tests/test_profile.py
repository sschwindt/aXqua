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


def test_a_misspelled_key_is_named_with_its_place(tmp_path):
    """A silently ignored key is a setting the user believes is in force."""
    target = tmp_path / "typo.axq-profile"
    target.write_text("solvers:\n  telemac:\n    setup_scirpt: /x.sh\n")
    with pytest.raises(ConfigError) as caught:
        profiles.load(target)
    assert caught.value.subject == "solvers.telemac.setup_scirpt"
    assert "setup_script" in caught.value.remedy


def test_a_profile_of_a_newer_axqua_is_refused_clearly(tmp_path):
    target = tmp_path / "future.axq-profile"
    target.write_text("schema_version: 99\n")
    with pytest.raises(ConfigError, match="newer aXqua"):
        profiles.load(target)


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
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"solvres": {}})))
    assert not _run(capsys, "write")["ok"]
    assert profiles.load_active(strict=True).name == "from-plugin"


def test_every_code_of_the_profile_check_is_explained_in_the_documentation():
    """A triangle in the plugin opens the documentation at the anchor of its code. A
    code without an anchor opens the right page at the wrong place, silently."""
    import re

    root = Path(__file__).resolve().parent.parent
    # the check of the profile and the check of a case: both end as triangles
    source = "".join((root / "src" / "axqua" / "core" / name).read_text(encoding="utf-8")
                     for name in ("profile.py", "casecheck.py"))
    codes = set(re.findall(r'"(axqua\.(?:environment|config)\.[a-z_]+)"', source))
    assert len(codes) >= 17
    pages = "".join((root / "docs" / "troubleshooting" / name).read_text(encoding="utf-8")
                    for name in ("warnings.rst", "errors.rst"))
    missing = sorted(code for code in codes
                     if f".. _{code.replace('.', '-').replace('_', '-')}:" not in pages)
    assert missing == []
