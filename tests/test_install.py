"""Installing the simulation programs: detection, plan, run and profile.

No test here installs anything. The installer scripts are stood in for by a folder of
the same layout whose scripts create the files the real ones promise, which is what the
part of aXqua under test acts on: it reads the package lists out of the scripts, runs
them as a detached process, checks what they produced and enters it in the profile.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from axqua.install import host as hosts
from axqua.install import recipes, runner

FAKES = Path(__file__).resolve().parent / "fakes"

DEBIAN12 = 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\nVERSION_ID="12"\n' \
           "VERSION_CODENAME=bookworm\n"
UBUNTU24 = 'PRETTY_NAME="Ubuntu 24.04.1 LTS"\nID=ubuntu\nID_LIKE=debian\n' \
           'VERSION_ID="24.04"\nVERSION_CODENAME=noble\n'
UBUNTU22 = 'ID=ubuntu\nID_LIKE=debian\nVERSION_ID="22.04"\nVERSION_CODENAME=jammy\n'
MINT22 = 'ID=linuxmint\nID_LIKE="ubuntu debian"\nVERSION_ID="22"\n' \
         "VERSION_CODENAME=wilma\nUBUNTU_CODENAME=noble\n"
FEDORA = 'ID=fedora\nVERSION_ID="41"\nPRETTY_NAME="Fedora Linux 41"\n'

TELEMAC_SCRIPT = r'''#!/usr/bin/env bash
set -euo pipefail
apt_install_deps_telemac() {
  if [ "$SKIP_APT" -eq 1 ]; then
    return
  fi
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git git-lfs \
    build-essential gfortran g++ \
    libmed-dev \
    wget curl

  git lfs install
}

apt_install_deps_salome() {
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    unzip python3-toml \
    || warn "SALOME runtime deps: some packages may be missing; check with sat."
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    libboost-all-dev doxygen \
    || warn "SALOME compile deps: some packages may be missing."
}

ROOT_DIR="$HOME/opt"
SKIP_APT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --root) shift; ROOT_DIR="$1" ;;
    --tag) shift ;;
    --skip-apt) SKIP_APT=1 ;;
  esac
  shift
done
[ "$SKIP_APT" -eq 1 ] || { echo "would have asked for a password"; exit 9; }
echo "[*] Installation root: $ROOT_DIR"
@BODY@
echo "[*] Installation finished."
'''

BUILDS = r'''
mkdir -p "$ROOT_DIR/telemac-mascaret/configs" "$ROOT_DIR/telemac-mascaret/builds/fake/bin"
printf 'source "%s"\n' "@PYSOURCE@" > "$ROOT_DIR/telemac-mascaret/configs/@NAME@"
touch "$ROOT_DIR/telemac-mascaret/builds/fake/bin/telemac2d"
'''

INSTALLER_PY = r'''"""A stand-in for the OpenFOAM installer."""
import argparse
import json
import sys
from pathlib import Path

BUILD_PACKAGES = [
    "build-essential", "cmake", "flex", "bison",
    "libopenmpi-dev",
]
GUI_PACKAGES = [
    "paraview", "libsm6",
]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=Path)
    parser.add_argument("--jobs", type=int)
    parser.add_argument("--reuse-openfoam")
    parser.add_argument("--visit-platform")
    parser.add_argument("--skip-visualization", action="store_true")
    parser.add_argument("--examples", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--install-system-packages", action="store_true")
    args = parser.parse_args(argv)
    if args.install_system_packages:
        print("would have asked for a password")
        return 9
    for tool in ("git", "gcc"):
        pass
    prefix = args.prefix
    (prefix / "user/platforms/fake/bin").mkdir(parents=True)
    (prefix / "user/platforms/fake/bin/sediDriftFoam2").write_text("")
    (prefix / "shell-rc.sh").write_text('source "@BASHRC@"\n')
    from sediment_installer import core
    (prefix / "receipt.json").write_text(json.dumps({
        "argv": sys.argv[1:], "path": core.clean_environment().get("PATH", "")}))
    if not args.skip_visualization:
        visit = prefix / "apps" / ("visit-9.9.9-" + args.visit_platform) / "bin"
        visit.mkdir(parents=True)
        (visit / "visit").write_text("#!/bin/sh\n")
        (visit / "visit").chmod(0o755)
    print("Build complete.")
    return 0
'''

VISUALIZATION_PY = r'''from pathlib import Path


def install_visit(prefix, platform_tag, download, run):
    folder = Path(prefix) / "apps" / ("visit-9.9.9-" + platform_tag) / "bin"
    folder.mkdir(parents=True)
    launcher = folder / "visit"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    return launcher
'''

CORE_PY = r'''def checked_download(url, sha, destination):
    return destination


def clean_environment():
    return {"PATH": "/usr/local/bin:/usr/bin:/bin"}


def run(argv, cwd=None, log=None, env=None):
    return None
'''


@pytest.fixture
def installers(tmp_path, fake_pysource, fake_bashrc) -> Path:
    """A folder laid out like the installer repository, with scripts that build
    nothing and leave behind the files the real ones promise."""
    root = tmp_path / "installers"
    for folder, script, name in (
            ("debian12", "telemac_debian12_installer.sh", "pysource.debian12.sh"),
            ("ubuntu24-mint22", "telemac_ubuntu24_installer.sh", "pysource.mint22.sh")):
        (root / folder).mkdir(parents=True)
        body = BUILDS.replace("@PYSOURCE@", str(fake_pysource)).replace("@NAME@", name)
        (root / folder / script).write_text(TELEMAC_SCRIPT.replace("@BODY@", body))
    package = root / "OpenFOAM-installer" / "sediment_installer"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "installer.py").write_text(INSTALLER_PY.replace("@BASHRC@",
                                                               str(fake_bashrc)))
    (package / "visualization.py").write_text(VISUALIZATION_PY)
    (package / "core.py").write_text(CORE_PY)
    (root / "OpenFOAM-installer" / "install.py").write_text(
        "from sediment_installer.installer import main\n"
        "raise SystemExit(main())\n")
    return root


@pytest.fixture
def debian():
    return hosts.detect(os_release=DEBIAN12, system="linux", release="6.1.0",
                        machine="x86_64")


def _script(installers: Path, body: str) -> None:
    """Replace what the stand-in TELEMAC installer does."""
    path = installers / "debian12" / "telemac_debian12_installer.sh"
    path.write_text(TELEMAC_SCRIPT.replace("@BODY@", body))


def _codes(findings) -> set[str]:
    return {f.code if hasattr(f, "code") else f["code"] for f in findings}


# ------------------------------------------------------------------------------ host


@pytest.mark.parametrize("text, base", [
    (DEBIAN12, "debian12"), (UBUNTU24, "ubuntu24"), (UBUNTU22, "ubuntu22"),
    (MINT22, "ubuntu24"), (FEDORA, ""),
    ("ID=debian\nVERSION_ID=\"11\"\n", ""),
])
def test_the_base_of_a_system_is_the_release_whose_packages_it_uses(text, base):
    here = hosts.detect(os_release=text, system="linux", release="6.1", machine="x86_64")
    assert here.base == base
    assert here.supported is bool(base)


def test_ubuntu_is_not_taken_for_debian_because_it_says_it_is_like_debian():
    here = hosts.detect(os_release='ID=ubuntu\nID_LIKE=debian\nVERSION_ID="23.10"\n',
                        system="linux", release="6.5", machine="x86_64")
    assert here.base == "" and not here.supported


def test_a_user_can_name_the_base_of_a_derivative_that_is_not_recognized():
    here = hosts.detect(os_release='ID=pop\nID_LIKE="ubuntu debian"\nVERSION_ID="24.04"\n',
                        system="linux", release="6.9", machine="x86_64",
                        base="ubuntu24")
    assert here.supported and here.base == "ubuntu24"


def test_the_windows_subsystem_for_linux_is_linux_and_says_so():
    here = hosts.detect(os_release=UBUNTU24, system="linux",
                        release="5.15.153.1-microsoft-standard-WSL2", machine="x86_64")
    assert here.system == "linux" and here.wsl and here.supported
    assert "Windows Subsystem" in here.describe()


def test_windows_and_macos_are_named_and_not_supported():
    assert hosts.detect(system="win32", machine="AMD64").system == "windows"
    assert hosts.detect(system="darwin", machine="arm64").system == "darwin"
    assert not hosts.detect(system="win32", machine="AMD64").supported


# -------------------------------------------------------------------------- packages


def test_the_package_list_is_read_out_of_the_installer_script(installers, debian):
    names = recipes.packages_needed("telemac", installers, debian, recipes.Options())
    assert names == ["git", "git-lfs", "build-essential", "gfortran", "g++",
                     "libmed-dev", "wget", "curl"]


def test_the_packages_of_salome_are_added_only_when_salome_is_installed(
        installers, debian, tmp_path):
    archive = tmp_path / "SALOME.tar.gz"
    archive.write_text("")
    names = recipes.packages_needed("telemac", installers, debian,
                                    recipes.Options(salome=archive))
    # the text of the warning after '||' is not a list of packages
    assert names[-4:] == ["unzip", "python3-toml", "libboost-all-dev", "doxygen"]
    assert "SALOME" not in names and "sat." not in names


def test_the_packages_of_openfoam_come_from_the_lists_in_its_installer(
        installers, debian):
    with_gui = recipes.packages_needed("openfoam", installers, debian, recipes.Options())
    without = recipes.packages_needed("openfoam", installers, debian,
                                      recipes.Options(visualization=False))
    assert with_gui == ["build-essential", "cmake", "flex", "bison", "libopenmpi-dev",
                        "paraview", "libsm6"]
    assert without == with_gui[:5]
    assert recipes.packages_needed("postprocessors", installers, debian,
                                   recipes.Options()) == ["paraview", "libsm6"]


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_a_package_another_one_provides_is_not_missing(monkeypatch):
    """dpkg does not know a name that is only provided; apt does."""
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        if argv[0] == "dpkg-query":
            return _Done(0, "git ii \ncmake ii \n", "no packages found matching ...")
        if "nowhere" in argv:
            return _Done(100, "", "E: Unable to locate package nowhere\n")
        return _Done(0, "Note, selecting 'libtiff-dev' instead of 'libtiff5-dev'\n"
                        "Inst libfoo-dev (1.0 Debian:12 [amd64])\n"
                        "Inst libtiff-dev (4.5 Debian:12 [amd64])\n")

    monkeypatch.setattr(recipes, "_run", fake_run)
    monkeypatch.setattr(recipes, "_which", lambda name: f"/usr/bin/{name}")
    missing, unavailable = recipes.packages_state(
        ["git", "cmake", "libfoo-dev", "awk", "libtiff5-dev", "nowhere"])
    # awk is provided by an installed package: apt would install nothing for it
    assert missing == ["libfoo-dev", "libtiff5-dev"]
    assert unavailable == ["nowhere"]
    assert len(calls) == 3                       # one dpkg, two apt: not one per name


def test_the_administrator_command_names_only_packages(monkeypatch):
    command = recipes.admin_command("openfoam", ["gawk", "libmpc-dev"])
    assert command.endswith("--no-install-recommends gawk libmpc-dev")
    assert command.startswith("sudo apt-get update && sudo ")
    assert recipes.admin_command("telemac", []) == ""
    # what does not look like a package name never reaches a shell
    monkeypatch.setattr(recipes, "elevation", lambda: "pkexec")
    answer = recipes.install_packages("telemac", ["; rm -rf ~"], Path("/nonexistent"))
    assert answer == {"returncode": 0, "log": "", "message": "nothing to install"}


def test_without_a_desktop_the_command_is_handed_over_instead_of_run(monkeypatch,
                                                                    tmp_path):
    monkeypatch.setattr(recipes, "elevation", lambda: "")
    answer = recipes.install_packages("telemac", ["gfortran"], tmp_path)
    assert answer["returncode"] == 127
    assert "sudo apt-get update" in answer["message"]


# ------------------------------------------------------------------------------ plan


#: The function itself, before the fixture below replaces it for every test.
SHADOWED_TOOLS = recipes.shadowed_tools


@pytest.fixture(autouse=True)
def _no_local_compilers(monkeypatch):
    """The computer of a test may have compilers in /usr/local/bin; a plan must not
    depend on that unless the test is about it."""
    monkeypatch.setattr(recipes, "shadowed_tools", lambda: [])


def _plan(target, installers, host, tmp_path, **options):
    options.setdefault("folder", tmp_path / "target")
    return recipes.plan(target, recipes.Options(installers=installers, **options),
                        host=host, check_packages=False)


def test_a_telemac_plan_never_asks_for_a_password(installers, debian, tmp_path):
    plan = _plan("telemac", installers, debian, tmp_path)
    assert plan.ready and not plan.findings
    build = plan.steps[-1]
    assert build.argv[:2] == ["/bin/bash", str(installers / "debian12"
                                               / "telemac_debian12_installer.sh")]
    assert "--skip-apt" in build.argv and "--root" in build.argv
    assert not any("sudo" in word for step in plan.steps for word in step.argv)
    assert plan.outputs == {"solvers.telemac.setup_script": str(
        tmp_path / "target" / "telemac-mascaret" / "configs" / "pysource.debian12.sh")}


def test_the_examples_of_telemac_are_most_of_the_download_and_can_be_left_out(
        installers, debian, tmp_path):
    """1,500 files in Git LFS, fetched one by one: on the development computer they
    were more than an hour of an installation whose build takes a quarter of one."""
    full = _plan("telemac", installers, debian, tmp_path)
    assert full.steps[-1].env == {} and "hours" in full.estimate

    lean = _plan("telemac", installers, debian, tmp_path, telemac_examples=False)
    build = lean.steps[-1]
    assert build.env == {"GIT_LFS_SKIP_SMUDGE": "1"}
    assert build.argv == full.steps[-1].argv              # the installer is the same
    assert build.as_dict()["command"].startswith("GIT_LFS_SKIP_SMUDGE=1 /bin/bash ")
    assert "minutes" in lean.estimate
    assert any("git lfs pull" in note for note in lean.notes)

    # the setting reaches the installer, which is where Git reads it
    _script(installers, 'echo "skip=${GIT_LFS_SKIP_SMUDGE:-unset}"\n' + BUILDS.replace(
        "@PYSOURCE@", str(FAKES / "fake_pysource.sh")).replace(
            "@NAME@", "pysource.debian12.sh"))
    lean = _plan("telemac", installers, debian, tmp_path, telemac_examples=False)
    status = runner.start(lean, detach=False)
    assert status["state"] == "succeeded", status
    assert "skip=1" in Path(status["log"]).read_text()


def test_linux_mint_gets_the_ubuntu_installer_and_its_environment_script(
        installers, tmp_path):
    mint = hosts.detect(os_release=MINT22, system="linux", release="6.8",
                        machine="x86_64")
    plan = _plan("telemac", installers, mint, tmp_path)
    assert plan.ready
    assert plan.steps[-1].argv[1].endswith("telemac_ubuntu24_installer.sh")
    assert plan.outputs["solvers.telemac.setup_script"].endswith("pysource.mint22.sh")


@pytest.mark.parametrize("target, expected", [
    ("telemac", "axqua.install.unsupported_system"),      # no script for Ubuntu 22.04
])
def test_a_system_without_an_installer_gets_a_finding_and_no_steps(
        installers, tmp_path, target, expected):
    jammy = hosts.detect(os_release=UBUNTU22, system="linux", release="5.15",
                         machine="x86_64")
    plan = _plan(target, installers, jammy, tmp_path)
    assert expected in _codes(plan.findings)
    assert not plan.ready and plan.steps == []
    # the OpenFOAM installer does cover that release
    assert _plan("openfoam", installers, jammy, tmp_path,
                 reuse_openfoam="no").ready


def test_on_windows_the_plan_points_to_the_subsystem_for_linux(installers, tmp_path):
    windows = hosts.detect(system="win32", machine="AMD64")
    plan = _plan("openfoam", installers, windows, tmp_path)
    assert _codes(plan.findings) == {"axqua.install.windows_needs_wsl"}
    assert not plan.ready
    assert "wsl --install" in plan.findings[0].remedy


def test_a_folder_of_windows_or_with_a_space_is_an_error(installers, tmp_path):
    wsl = hosts.detect(os_release=UBUNTU24, system="linux",
                       release="5.15-microsoft-standard-WSL2", machine="x86_64")
    plan = _plan("telemac", installers, wsl, tmp_path,
                 folder=Path("/mnt/c/Users/me/opt"))
    assert "axqua.install.windows_folder" in _codes(plan.findings)
    spaced = _plan("telemac", installers, wsl, tmp_path, folder=tmp_path / "my opt")
    assert "axqua.install.folder_with_space" in _codes(spaced.findings)
    assert not spaced.ready
    assert any(f.subject == "install.telemac.folder" for f in spaced.findings)


def test_an_existing_telemac_folder_is_a_warning_and_the_plan_stays_ready(
        installers, debian, tmp_path):
    (tmp_path / "target" / "telemac-mascaret").mkdir(parents=True)
    plan = _plan("telemac", installers, debian, tmp_path)
    assert _codes(plan.findings) == {"axqua.install.folder_exists"}
    assert plan.ready


def test_a_salome_archive_that_does_not_exist_is_an_error_at_its_field(
        installers, debian, tmp_path):
    plan = _plan("telemac", installers, debian, tmp_path,
                 salome=tmp_path / "nowhere.tar.gz")
    finding = next(f for f in plan.findings if f.code == "axqua.install.file_missing")
    assert finding.subject == "install.telemac.salome"


def test_openfoam_builds_on_an_installed_release_or_says_that_it_compiles(
        installers, debian, tmp_path):
    bashrc = tmp_path / "openfoam2406" / "etc" / "bashrc"
    bashrc.parent.mkdir(parents=True)
    bashrc.write_text("")
    (bashrc.parent.parent / "META-INFO").mkdir()
    (bashrc.parent.parent / "META-INFO" / "api-info").write_text("api=2406\npatch=0\n")
    plan = _plan("openfoam", installers, debian, tmp_path, reuse_openfoam=str(bashrc))
    argv = plan.steps[0].argv
    assert plan.ready
    assert argv[argv.index("--reuse-openfoam") + 1] == str(bashrc)
    assert "--install-system-packages" not in argv
    assert argv[argv.index("--visit-platform") + 1] == "debian12"
    assert plan.outputs["solvers.openfoam.setup_script"].endswith("shell-rc.sh")
    assert set(plan.outputs) == {"solvers.openfoam.setup_script",
                                 "postprocessors.paraview", "postprocessors.visit"}

    compiled = _plan("openfoam", installers, debian, tmp_path, reuse_openfoam="no")
    assert "--reuse-openfoam" not in compiled.steps[0].argv
    assert "axqua.install.compiles_openfoam" in _codes(compiled.findings)
    assert compiled.ready                        # a warning, the user may want it
    assert "hours" in compiled.estimate


def test_another_release_of_openfoam_is_refused_before_anything_runs(
        installers, debian, tmp_path):
    bashrc = tmp_path / "openfoam2312" / "etc" / "bashrc"
    bashrc.parent.mkdir(parents=True)
    bashrc.write_text("")
    (bashrc.parent.parent / "META-INFO").mkdir()
    (bashrc.parent.parent / "META-INFO" / "api-info").write_text("api=2312\n")
    plan = _plan("openfoam", installers, debian, tmp_path, reuse_openfoam=str(bashrc))
    assert "axqua.install.wrong_openfoam" in _codes(plan.findings)
    assert not plan.ready
    missing = _plan("openfoam", installers, debian, tmp_path,
                    reuse_openfoam=str(tmp_path / "none" / "etc" / "bashrc"))
    assert "axqua.install.file_missing" in _codes(missing.findings)


def test_the_openfoam_installer_only_takes_a_new_folder_or_its_own(
        installers, debian, tmp_path):
    (tmp_path / "target").mkdir()
    plan = _plan("openfoam", installers, debian, tmp_path, reuse_openfoam="no")
    assert "axqua.install.folder_in_use" in _codes(plan.findings)
    assert not plan.ready
    (tmp_path / "target" / ".sediment-installer.json").write_text("{}")
    again = _plan("openfoam", installers, debian, tmp_path, reuse_openfoam="no")
    assert "axqua.install.folder_exists" in _codes(again.findings)
    assert again.ready


def test_a_compiler_in_usr_local_does_not_decide_what_openfoam_is_built_with(
        installers, debian, tmp_path, monkeypatch):
    """Found on the development computer: a GCC 9.4 of 2021 in /usr/local/bin came
    first on the installer's search path, and no solver could be linked against the
    packaged OpenFOAM, which is built with the system's GCC 12."""
    local, system = tmp_path / "local-bin", tmp_path / "bin"
    for folder, names in ((local, ("gcc", "gfortran", "htop")),
                          (system, ("gcc", "gfortran", "make"))):
        folder.mkdir()
        for name in names:
            (folder / name).write_text("")
    assert SHADOWED_TOOLS(local, system) == ["gcc", "gfortran"]
    assert SHADOWED_TOOLS(tmp_path / "none", system) == []
    assert recipes.clean_environment()["PATH"].startswith("/usr/bin:/bin:")

    monkeypatch.setattr(recipes, "shadowed_tools", lambda: [])
    plain = _plan("openfoam", installers, debian, tmp_path, reuse_openfoam="no",
                  visualization=False)
    assert plain.steps[0].argv[1].endswith("install.py")     # the installer as it is
    status = runner.start(plain, detach=False)
    assert status["state"] == "succeeded", status
    receipt = json.loads((tmp_path / "target" / "receipt.json").read_text())
    assert receipt["path"].startswith("/usr/local/bin:")

    monkeypatch.setattr(recipes, "shadowed_tools", lambda: ["gcc", "g++"])
    guarded = _plan("openfoam", installers, debian, tmp_path, reuse_openfoam="no",
                    visualization=False, folder=tmp_path / "guarded")
    assert guarded.steps[0].argv[1:3] == ["-I", "-c"]
    assert any("gcc, g++" in note for note in guarded.notes)
    # handled, and therefore a note and not a warning the user would have to act on
    assert guarded.ready
    assert _codes(guarded.findings) == {"axqua.install.compiles_openfoam"}
    status = runner.start(guarded, detach=False)
    assert status["state"] == "succeeded", status
    receipt = json.loads((tmp_path / "guarded" / "receipt.json").read_text())
    assert receipt["path"] == "/usr/bin:/bin"
    assert "--prefix" in receipt["argv"] and "-c" not in receipt["argv"]


def test_without_visualization_no_postprocessor_is_promised(installers, debian,
                                                            tmp_path):
    plan = _plan("openfoam", installers, debian, tmp_path, reuse_openfoam="no",
                 visualization=False)
    assert "--skip-visualization" in plan.steps[0].argv
    assert set(plan.outputs) == {"solvers.openfoam.setup_script"}


def test_an_installer_no_python_here_can_read_is_reported_not_started(
        installers, debian, tmp_path):
    """One version of the OpenFOAM installer needs Python 3.12; a plan finds out by
    trying, so a newer version of the installer needs no change here."""
    assert recipes.installer_python(installers) == sys.executable
    broken = installers / "OpenFOAM-installer" / "sediment_installer" / "installer.py"
    broken.write_text(broken.read_text() + "\nthis is not python (\n")
    assert recipes.installer_python(installers) is None
    plan = _plan("openfoam", installers, debian, tmp_path, reuse_openfoam="no")
    assert _codes(plan.findings) >= {"axqua.install.python_too_old"}
    assert not plan.ready


def test_the_installers_are_not_downloaded_twice(tmp_path, monkeypatch):
    """A commit is fetched once: the marker of a complete download is enough."""
    monkeypatch.delenv(recipes.ENV_INSTALLERS, raising=False)
    root = tmp_path / "installers"
    done = root / recipes.PINNED
    done.mkdir(parents=True)
    (done / ".axqua-installers").write_text(recipes.PINNED + "\n")
    monkeypatch.setattr(recipes, "_clone", lambda *a: pytest.fail("downloaded again"))
    monkeypatch.setattr(recipes, "_download_archive",
                        lambda *a: pytest.fail("downloaded again"))
    assert recipes.fetch_installers(recipes.Options(), root=root) == done


def test_a_failed_download_is_a_finding_with_the_way_around_it(tmp_path, monkeypatch,
                                                               debian):
    monkeypatch.delenv(recipes.ENV_INSTALLERS, raising=False)

    def offline(*_args):
        raise RuntimeError("could not resolve host")

    monkeypatch.setattr(recipes, "_clone", offline)
    monkeypatch.setattr(recipes, "_download_archive", offline)
    plan = recipes.plan("telemac", recipes.Options(folder=tmp_path / "t"), host=debian,
                        check_packages=False)
    finding = next(f for f in plan.findings if f.code == "axqua.install.no_installer")
    assert recipes.ENV_INSTALLERS in finding.remedy
    assert not plan.ready


def test_a_plan_is_a_document_a_window_can_show(installers, debian, tmp_path):
    data = _plan("telemac", installers, debian, tmp_path).as_dict()
    assert json.loads(json.dumps(data)) == data
    assert data["ready"] is True and data["title"] == "TELEMAC"
    assert data["steps"][-1]["command"].startswith("/bin/bash ")
    assert data["installers"]["repository"] == recipes.REPOSITORY
    assert set(data["packages"]) == {"needed", "missing", "unavailable", "command",
                                     "elevation", "note"}


# ------------------------------------------------------------------------------- run


def test_an_installation_never_reaches_the_folders_of_the_developer(tmp_path):
    """These tests write profiles and installation records. Where they write them is
    decided by the isolation of the suite, and a test must not be able to lift it."""
    from axqua.core import profile as profiles

    home = Path.home()
    for folder in (profiles.default_path(), runner.installs_root()):
        assert home / ".config" not in folder.parents
        assert home / ".local" not in folder.parents


def test_the_isolation_survives_a_test_that_undoes_its_own_patches(monkeypatch):
    from axqua.core import profile as profiles

    before = profiles.default_path()
    monkeypatch.undo()
    assert profiles.default_path() == before


def test_an_installation_ends_in_the_profile_of_this_computer(installers, debian,
                                                              tmp_path):
    from axqua.core import profile as profiles

    assert profiles.active_path() is None       # this computer has no profile yet
    plan = _plan("telemac", installers, debian, tmp_path)
    status = runner.start(plan, detach=False)
    assert status["state"] == "succeeded", status
    script = tmp_path / "target" / "telemac-mascaret" / "configs" / "pysource.debian12.sh"
    assert status["outputs"] == {"solvers.telemac.setup_script": str(script)}
    assert status["steps"] == [step.name for step in plan.steps]

    profile = profiles.load_active(strict=True)
    assert str(profile.path) == status["bound"]
    assert profile.solvers["telemac"].setup_script == script
    # the environment was entered once: a path that merely exists would have a finding
    assert status["findings"] == []
    log = Path(status["log"]).read_text()
    assert "=== " in log and "[*] Installation finished." in log


def test_an_installation_can_leave_the_profile_alone(installers, debian, tmp_path):
    from axqua.core import profile as profiles

    plan = _plan("telemac", installers, debian, tmp_path, bind=False)
    status = runner.start(plan, detach=False)
    assert status["state"] == "succeeded" and status["bound"] == ""
    assert profiles.active_path() is None


def test_a_profile_with_an_error_is_not_written_over(installers, debian, tmp_path):
    from axqua.core import profile as profiles

    path = profiles.default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("solvers: [this is not a profile\n")
    plan = _plan("telemac", installers, debian, tmp_path)
    status = runner.start(plan, detach=False)
    assert status["state"] == "succeeded"       # TELEMAC is installed all the same
    assert _codes(status["findings"]) == {"axqua.install.not_bound"}
    assert "solvers.telemac.setup_script" in status["findings"][0]["remedy"]
    assert path.read_text() == "solvers: [this is not a profile\n"


def test_a_failed_installer_reports_the_end_of_its_log(installers, debian, tmp_path):
    _script(installers, 'echo "CMake Error: could not find MED"; exit 3')
    status = runner.start(_plan("telemac", installers, debian, tmp_path), detach=False)
    assert status["state"] == "failed"
    finding = status["findings"][0]
    assert finding["code"] == "axqua.install.failed"
    assert "could not find MED" in finding["message"]
    assert "exit code 3" in finding["message"]
    assert status["log"] in finding["remedy"]


def test_an_installer_that_claims_success_without_a_build_is_not_believed(
        installers, debian, tmp_path):
    """The TELEMAC script ends with exit code 0 when its own check of the build
    failed; it only prints a warning."""
    _script(installers, 'mkdir -p "$ROOT_DIR/telemac-mascaret/configs"\n'
                        'touch "$ROOT_DIR/telemac-mascaret/configs/pysource.debian12.sh"')
    status = runner.start(_plan("telemac", installers, debian, tmp_path), detach=False)
    assert status["state"] == "failed"
    assert _codes(status["findings"]) == {"axqua.install.output_missing"}
    assert "telemac2d" in status["findings"][0]["message"]


def test_openfoam_enters_its_own_environment_script_and_both_postprocessors(
        installers, debian, tmp_path, monkeypatch):
    from axqua.core import profile as profiles

    paraview = tmp_path / "paraview"
    paraview.write_text("#!/bin/sh\n")
    paraview.chmod(0o755)
    monkeypatch.setattr(recipes, "PARAVIEW", paraview)
    plan = _plan("openfoam", installers, debian, tmp_path, reuse_openfoam="no")
    status = runner.start(plan, detach=False)
    assert status["state"] == "succeeded", status
    profile = profiles.load_active(strict=True)
    target = tmp_path / "target"
    assert profile.solvers["openfoam"].setup_script == target / "shell-rc.sh"
    assert profile.postprocessors["paraview"] == paraview
    assert profile.postprocessors["visit"] == \
        target / "apps" / "visit-9.9.9-debian12" / "bin" / "visit"
    receipt = json.loads((target / "receipt.json").read_text())
    assert "--install-system-packages" not in receipt["argv"]


def test_visit_is_installed_alone_for_a_computer_that_only_runs_telemac(
        installers, debian, tmp_path, monkeypatch):
    """The postprocessors without OpenFOAM: the installer's own VisIt download, and
    ParaView only if the system package is there."""
    from axqua.core import profile as profiles

    monkeypatch.setattr(recipes, "PARAVIEW", tmp_path / "no-paraview-here")
    # the computer of a test may have a ParaView of its own on the search path
    monkeypatch.setattr(profiles.shutil, "which", lambda _name: None)
    plan = _plan("postprocessors", installers, debian, tmp_path)
    assert "axqua.install.paraview_missing" in _codes(plan.findings)
    assert plan.ready
    status = runner.start(plan, detach=False)
    assert status["state"] == "succeeded", status
    assert _codes(status["findings"]) == {"axqua.install.paraview_missing"}
    profile = profiles.load_active(strict=True)
    assert profile.postprocessors["visit"].name == "visit"
    assert "paraview" not in profile.postprocessors
    assert "VisIt launcher:" in Path(status["log"]).read_text()


def _wait(condition, seconds=20.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.1)
    return condition()


def test_a_detached_installation_can_be_followed_and_cancelled(installers, debian,
                                                               tmp_path):
    from axqua.core.errors import EnvironmentError as AxquaEnvironmentError
    from axqua.jobs import procs

    _script(installers, 'echo "[*] compiling"; sleep 120')
    plan = _plan("telemac", installers, debian, tmp_path)
    status = runner.start(plan, detach=True)
    assert status["state"] in ("queued", "running")
    ident = status["id"]
    try:
        running = _wait(lambda: (s := runner.read(ident, lines=5)).get("child_pid")
                        and "compiling" in s.get("log_tail", "") and s)
        assert running and running["state"] == "running"
        assert running["step_name"] == plan.steps[-1].name
        assert running["pid"] != os.getpid()
        child = int(running["child_pid"])

        with pytest.raises(AxquaEnvironmentError, match="already running"):
            runner.start(plan, detach=True)     # one installation per program
        assert runner.latest("telemac", active_only=True)["id"] == ident

        done = runner.cancel(ident)
        assert done["state"] == "cancelled"
        assert _wait(lambda: not procs.pid_alive(child))
        assert runner.latest("telemac", active_only=True) is None
    finally:
        runner.cancel(ident)


def test_an_installation_whose_process_is_gone_does_not_stay_running(tmp_path):
    """After a restart of the computer the status file still says "running"."""
    from axqua.jobs import procs

    folder = runner.installs_root() / "20260101-000000-telemac"
    folder.mkdir(parents=True)
    (folder / "status.json").write_text(json.dumps({
        "id": folder.name, "target": "telemac", "state": "running",
        "created": time.time() - 500, "started": time.time() - 400,
        "host": procs.host_id(), "pid": 2 ** 22 + 12345, "proc_started": 1.0,
        "steps": ["Download and build TELEMAC"], "step": 1}))
    status = runner.read(folder.name)
    assert status["state"] == "failed"
    assert _codes(status["findings"]) == {"axqua.install.interrupted"}
    # and the correction was written down, so it is found once
    assert json.loads((folder / "status.json").read_text())["state"] == "failed"
    assert runner.list_all()[0]["id"] == folder.name


def test_the_end_of_a_log_shows_what_a_progress_bar_showed_last(tmp_path):
    log = tmp_path / "install.log"
    log.write_bytes(b"line 1\n[ 10%] Building\r[ 55%] Building\r[100%] Built\nlast\n")
    assert runner.tail(log, 2) == "[100%] Built\nlast"
    assert runner.tail(tmp_path / "missing.log") == ""


# ------------------------------------------------------------------------------- CLI


def test_the_command_answers_with_one_document(installers, tmp_path, capsys,
                                               monkeypatch):
    from axqua.installcli import run_install

    monkeypatch.setenv(recipes.ENV_INSTALLERS, str(installers))
    monkeypatch.setattr(hosts, "detect", lambda **_kw: hosts.Host(
        system="linux", distro="debian", version="12", base="debian12",
        machine="x86_64", pretty="Debian GNU/Linux 12 (bookworm)"))
    monkeypatch.setattr(recipes, "packages_state", lambda names: (["gfortran"], []))

    assert run_install(["plan", "telemac", "--folder", str(tmp_path / "t"),
                        "--json"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["ok"] and answer["command"] == "install.plan"
    assert answer["data"]["packages"]["missing"] == ["gfortran"]
    assert "gfortran" in answer["data"]["packages"]["command"]
    assert [f["code"] for f in answer["data"]["findings"]] == \
        ["axqua.install.packages_missing"]
    assert answer["data"]["ready"] is True      # a warning does not prevent starting

    assert run_install(["overview", "--json"]) == 0
    overview = json.loads(capsys.readouterr().out)["data"]
    assert [t["target"] for t in overview["targets"]] == list(recipes.TARGETS)
    assert overview["host"]["base"] == "debian12"

    assert run_install(["start", "nonsense", "--json"]) != 0
    refused = json.loads(capsys.readouterr().out)
    assert not refused["ok"] and "telemac" in refused["error"]["message"]

    assert run_install(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == {"installs": []}


def test_every_code_of_an_installation_is_explained_in_the_documentation():
    """A triangle in the wizard opens the documentation at the anchor of its code."""
    import re

    root = Path(__file__).resolve().parent.parent
    source = "".join(path.read_text(encoding="utf-8")
                     for path in (root / "src" / "axqua" / "install").glob("*.py"))
    codes = set(re.findall(r'"(axqua\.install\.[a-z_]+)"', source))
    assert len(codes) >= 20
    pages = "".join((root / "docs" / "troubleshooting" / name).read_text(encoding="utf-8")
                    for name in ("warnings.rst", "errors.rst"))
    missing = sorted(code for code in codes
                     if f".. _{code.replace('.', '-').replace('_', '-')}:" not in pages)
    assert missing == []
