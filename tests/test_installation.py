"""What an installation of TELEMAC or OpenFOAM can do for aXqua (``core.installation``).

The *Setup Wizard* of the plugin asks this about an environment script a user selects,
and *Check* of the profile reports it: an installation that runs the code but lacks a
part of it is valid, and says which aXqua modules will not work.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from axqua import cli
from axqua.core import installation
from axqua.core import profile as profiles
from axqua.core.diagnostics import ERROR, WARNING

FAKES = Path(__file__).parent / "fakes"


def _modules(found) -> dict:
    return {m.name: m.available for m in found.modules}


def _build(tmp_path: Path, *, drop: tuple[str, ...] = ()) -> Path:
    """A copy of the fake TELEMAC build without the files named in *drop*."""
    build = tmp_path / "build"
    shutil.copytree(FAKES / "telemac" / "builds" / "fake", build, symlinks=True)
    for name in drop:
        for path in build.rglob(name):
            path.unlink()
    return build


def test_a_complete_telemac_build_is_valid_and_complete(fake_pysource):
    found = installation.inspect("telemac", fake_pysource)
    assert found.valid, found.detail
    assert _modules(found) == {"telemac2d": True, "telemac3d": True, "gaia": True,
                               "parallel": True, "fortran": True}
    assert not found.missing and "valid and complete" in found.verdict()
    assert not [f for f in found.findings if f.code == "axqua.environment.module_missing"]


def test_a_telemac_without_gaia_is_valid_and_names_what_will_not_work(
        tmp_path, fake_pysource, monkeypatch):
    monkeypatch.setenv("FAKE_TELEMAC_BUILD", str(_build(tmp_path, drop=("libgaia.so",))))
    found = installation.inspect("telemac", fake_pysource)
    assert found.valid
    assert _modules(found)["gaia"] is False and _modules(found)["telemac3d"] is True
    assert "valid, but some aXqua modules will not work" in found.verdict()
    assert "morphodynamics" in found.verdict()
    missing = [f for f in found.findings if f.code == "axqua.environment.module_missing"]
    assert len(missing) == 1 and missing[0].severity == WARNING
    assert missing[0].subject == "solvers.telemac.setup_script"
    assert "GAIA" in missing[0].message


def test_a_telemac_without_telemac2d_is_not_usable(tmp_path, fake_pysource, monkeypatch):
    build = _build(tmp_path, drop=("telemac2d", "libtelemac2d.so"))
    monkeypatch.setenv("FAKE_TELEMAC_BUILD", str(build))
    found = installation.inspect("telemac", fake_pysource)
    assert not found.valid and "TELEMAC-2D" in found.detail
    codes = {(f.severity, f.code) for f in found.findings}
    assert (ERROR, "axqua.environment.solver_incomplete") in codes
    assert found.verdict().startswith("This is not a usable installation of TELEMAC")


def test_no_mpi_launcher_means_no_parallel_runs(tmp_path, fake_pysource, monkeypatch):
    monkeypatch.setenv("FAKE_TELEMAC_BUILD", str(_build(tmp_path, drop=("mpirun",))))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")         # no MPI of this computer either
    monkeypatch.setattr(installation, "_find",
                        lambda name, path=None: None if name in ("mpirun", "mpiexec")
                        else shutil.which(name, path=path))
    found = installation.inspect("telemac", fake_pysource)
    assert found.valid and _modules(found)["parallel"] is False


def test_an_openfoam_without_the_sediment_solvers_is_valid(fake_bashrc):
    found = installation.inspect("openfoam", fake_bashrc)
    assert found.valid, found.detail
    assert _modules(found) == {"solvers": True, "release": False, "sediment": False,
                               "rating": False}
    codes = [f.code for f in found.findings]
    assert "axqua.environment.openfoam_version" in codes      # the fake is release 9
    assert codes.count("axqua.environment.module_missing") == 2
    assert "sediDriftFoam2" in found.verdict()


def test_the_stack_of_the_setup_wizard_is_complete(tmp_path, fake_bashrc):
    """What the OpenFOAM installation of aXqua sets up: v2406, sediDriftFoam2 and the
    library of the BAW, in the folders its shell-rc.sh names."""
    apps, libs = tmp_path / "user" / "bin", tmp_path / "user" / "lib"
    apps.mkdir(parents=True)
    libs.mkdir(parents=True)
    solver = apps / installation.FINE_SEDIMENT_SOLVER
    solver.write_text("#!/bin/sh\n")
    solver.chmod(0o755)
    (libs / installation.BAW_LIBRARY).touch()
    script = tmp_path / "shell-rc.sh"
    script.write_text(f'source "{fake_bashrc}"\nexport WM_PROJECT_VERSION=v2406\n'
                      f'export FOAM_USER_APPBIN="{apps}"\nexport FOAM_USER_LIBBIN="{libs}"\n'
                      f'export PATH="$FOAM_USER_APPBIN:$PATH"\n')
    found = installation.inspect("openfoam", script)
    assert found.valid and all(_modules(found).values()), found.modules
    assert "valid and complete" in found.verdict()


def test_an_openfoam_without_its_solvers_is_not_usable(tmp_path):
    script = tmp_path / "bashrc"
    script.write_text("export WM_PROJECT_DIR=/x WM_PROJECT_VERSION=v2406 "
                      "FOAM_APPBIN=/x/bin PATH=/usr/bin:/bin\n")
    found = installation.inspect("openfoam", script)
    assert not found.valid and "interFoam" in found.modules[0].detail
    assert "axqua.environment.solver_incomplete" in [f.code for f in found.findings]


def test_a_script_that_does_not_exist_or_sets_up_nothing_is_reported(tmp_path):
    missing = installation.inspect("telemac", tmp_path / "pysource.x.sh")
    assert not missing.valid
    assert [f.code for f in missing.findings] == ["axqua.environment.script_missing"]

    other = tmp_path / "setup.sh"
    other.write_text("true\n")
    found = installation.inspect("telemac", other)
    codes = [f.code for f in found.findings]
    assert not found.valid
    assert codes == ["axqua.environment.unusual_script",
                     "axqua.environment.solver_unreachable"]


def test_the_names_looked_for_are_those_the_backends_use():
    from axqua.solvers.openfoam import dicts, sediment

    assert dicts.BAW_LIBRARY == installation.BAW_LIBRARY
    assert sediment.SOLVER == installation.FINE_SEDIMENT_SOLVER
    assert profiles.OPENFOAM_RELEASE == installation.OPENFOAM_RELEASE
    for solver, patterns in installation.SCRIPT_PATTERNS.items():
        assert patterns == profiles.LAYOUT[solver]


def test_check_of_the_profile_reports_the_missing_modules(tmp_path, fake_bashrc):
    profile = profiles.AxquaProfile(name="bench")
    profile.solvers["openfoam"] = profiles.SolverBinding(setup_script=fake_bashrc)
    found = profiles.check(profile, probe=True)
    modules = [f for f in found if f.code == "axqua.environment.module_missing"]
    assert modules and all(f.subject == "solvers.openfoam.setup_script" for f in modules)


# -------------------------------------------------------------------- the command


def _run(capsys, *argv) -> dict:
    code = cli.main(["profile", *argv, "--json"])
    payload = json.loads(capsys.readouterr().out)
    payload["exit"] = code
    return payload


def test_inspect_answers_and_writes_nothing(capsys, fake_bashrc):
    answer = _run(capsys, "inspect", "openfoam", str(fake_bashrc))
    assert answer["ok"] and answer["exit"] == 0
    data = answer["data"]
    assert data["valid"] and data["solver"] == "openfoam"
    assert {m["name"]: m["available"] for m in data["modules"]}["sediment"] is False
    assert "verdict" in data and data["findings"]
    assert not profiles.default_path().exists()
    assert not _run(capsys, "inspect", "visit", str(fake_bashrc))["ok"]
    assert not _run(capsys, "inspect", "telemac")["ok"]


def test_use_enters_a_valid_installation_and_refuses_an_unusable_one(
        capsys, tmp_path, fake_pysource, monkeypatch):
    from axqua.core import machine

    monkeypatch.setattr(machine, "DISCOVERY", {})
    entered = _run(capsys, "use", "telemac", str(fake_pysource))
    assert entered["ok"] and entered["data"]["entered"]
    saved = profiles.load(profiles.default_path())
    assert saved.solvers["telemac"].setup_script == fake_pysource

    broken = tmp_path / "pysource.broken.sh"
    broken.write_text("true\n")
    refused = _run(capsys, "use", "telemac", str(broken))
    assert refused["ok"] and not refused["data"]["entered"]       # an answer, not a crash
    assert profiles.load(profiles.default_path()).solvers["telemac"].setup_script == \
        fake_pysource


def test_use_enters_a_new_installation_without_the_settings_of_the_old_one(
        capsys, tmp_path, fake_pysource, monkeypatch):
    """The check enters the script as the jobs will after bind (profiles.rebound): the entry of the profile for a script of the same installation; for another installation without its configuration name, overrides and shell. The ambient warning is reported: a script that only works because the terminal set the code up is what a detached job cannot reach."""
    from axqua.core import machine
    from axqua.core.diagnostics import WARNING, Finding

    monkeypatch.setattr(machine, "DISCOVERY", {})
    seen = {}
    real = installation.inspect

    def spy(solver, script, **kwargs):
        seen["binding"] = kwargs.get("binding")
        found = real(solver, script, **kwargs)
        found.findings.append(Finding(WARNING, "axqua.environment.ambient", "ambient",
                                      subject="solvers.telemac.setup_script"))
        return found

    monkeypatch.setattr(installation, "inspect", spy)

    def entry(script, **fields):
        profile = profiles.AxquaProfile(name="bench")
        profile.solvers["telemac"] = profiles.SolverBinding(setup_script=script, **fields)
        profiles.save(profile, profiles.default_path())

    entry(fake_pysource, config_name="oldcfg", overrides={"X": "1"})
    answer = _run(capsys, "use", "telemac", str(fake_pysource))          # the same one
    assert seen["binding"].config_name == "oldcfg" and answer["data"]["entered"]
    assert "axqua.environment.ambient" in {f["code"] for f in answer["data"]["findings"]}

    old = tmp_path / "old" / "telemac-mascaret" / "configs" / "pysource.old.sh"
    old.parent.mkdir(parents=True)
    old.write_text("true\n")
    entry(old, config_name="oldcfg", overrides={"HOMETEL": "/old"}, mpi_processes=6)
    answer = _run(capsys, "use", "telemac", str(fake_pysource))          # another one
    assert seen["binding"].config_name is None and seen["binding"].overrides == {}
    saved = profiles.load(profiles.default_path()).solvers["telemac"]
    assert saved.setup_script == fake_pysource and saved.config_name is None
    assert saved.overrides == {} and saved.mpi_processes == 6          # the computer's stays


def test_only_the_same_script_keeps_the_entry_of_its_installation(tmp_path, monkeypatch):
    """Another pysource of the same folder sets another configuration, which the old config_name would override; the same script named relatively, or found in the installation folder of a profile without a script, is the same one. The jobs follow the same rule and leave out the shell too."""
    configs = tmp_path / "telemac" / "telemac-mascaret" / "configs"
    configs.mkdir(parents=True)
    gfortran, hpc = configs / "pysource.gfortran.sh", configs / "pysource.gfortranHPC.sh"
    for script in (gfortran, hpc):
        script.write_text("true\n")

    profile = profiles.AxquaProfile(name="bench")
    profile.solvers["telemac"] = profiles.SolverBinding(
        setup_script=gfortran, config_name="gfortran", overrides={"X": "1"}, shell="/old/bash",
        mpi_processes=6)
    other = profiles.rebound(profile, "telemac", hpc)
    assert other.config_name is None and other.overrides == {} and other.shell is None
    assert other.mpi_processes == 6 and other.setup_script == hpc

    monkeypatch.chdir(configs.parent)
    same = profiles.rebound(profile, "telemac", "configs/pysource.gfortran.sh")
    assert same.config_name == "gfortran" and same.setup_script == gfortran   # stored in full

    folder_only = profiles.AxquaProfile(name="bench")
    folder_only.installations["telemac"] = tmp_path / "telemac"
    folder_only.solvers["telemac"] = profiles.SolverBinding(config_name="gfortran")
    hpc.unlink()                                       # one script in the folder: it is located
    assert folder_only.script("telemac") == gfortran
    assert profiles.rebound(folder_only, "telemac", gfortran).config_name == "gfortran"


def test_a_job_with_the_script_of_another_installation_leaves_out_its_shell(
        tmp_path, monkeypatch, fake_pysource):
    from types import SimpleNamespace

    from axqua.core import machine
    from axqua.jobs import profiles as job_profiles

    profile = profiles.AxquaProfile(name="bench", path=tmp_path / "p.axq-profile")
    profile.solvers["telemac"] = profiles.SolverBinding(
        setup_script=fake_pysource, config_name="cfg", shell="/old/bash")
    other = tmp_path / "pysource.other.sh"
    other.write_text("true\n")
    monkeypatch.setattr(profiles, "load_active", lambda *a, **k: profile)
    monkeypatch.setattr(machine, "resolve", lambda *a, **k: SimpleNamespace(
        path=str(other), source="AXQUA_TELEMAC_PYSOURCE"))
    found = job_profiles.implicit(SimpleNamespace(config_dir=tmp_path), "telemac")
    assert found.setup_script == other
    assert found.config_name is None and found.shell is None
