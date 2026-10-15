"""Real-release OpenFOAM shell bootstrap regressions, without compilation.

Opt in with OPENFOAM_ARCHIVE=/path/to/OpenFOAM-v2406.tgz. The archive's pinned
SHA256 is verified before selecting its untouched etc/, bin/, META-INFO/, and
wmake/ scripts (not compiler rules, which contain NTFS case-colliding names).
No network or operating-system installation is performed. On Windows also set
SEDIMENT_TEST_BASH to GNU Git's usr/bin/sh.exe; WSL's bash.exe shim is not used.
SEDIMENT_TEST_TMPDIR can select an existing, whitespace-free temporary parent.
For bundled Git Bash's wmake directory checks, choose a parent outside Windows
TEMP: that runtime aliases TEMP as /tmp, confusing upstream path comparisons.

These tests exercise the ACTUAL v2406 configuration chain, including CGAL's
intentional return-1 optional-library probes. Missing MPI tools are substituted
only for environment discovery; this is not a compiler, library, or solver test.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from sediment_installer import installer  # noqa: E402

ARCHIVE_SHA256 = "8d1450fb89eec1e7cecc55c3bb7bc486ccbf63d069379d1d5d7518fa16a4686a"
BASH = os.environ.get("SEDIMENT_TEST_BASH") or (shutil.which("bash") if os.name != "nt" else None)
ARCHIVE = os.environ.get("OPENFOAM_ARCHIVE")


def shell_path(path: Path) -> str:
    value = Path(path).resolve().as_posix()
    if os.name == "nt":
        drive = Path(path).resolve().drive
        if len(drive) != 2 or drive[1] != ":":
            raise ValueError("Git Bash regression fixtures require a local drive")
        return "/" + drive[0].lower() + value[2:]
    return value


def extract_bootstrap(archive: Path, destination: Path) -> Path:
    """Read checked regular bootstrap files, never extractall or archive links."""
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != ARCHIVE_SHA256:
        raise ValueError("OPENFOAM_ARCHIVE does not match the pinned official v2406 archive")
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            if len(path.parts) < 2 or path.parts[1] not in ("etc", "bin", "wmake", "META-INFO"):
                continue
            if len(path.parts) > 2 and path.parts[1:3] == ("wmake", "rules"):
                continue
            if (
                path.parts[0] != "OpenFOAM-v2406" or path.is_absolute()
                or ".." in path.parts or "\\" in member.name or "\0" in member.name
                or (os.name == "nt" and ":" in member.name)
            ):
                raise ValueError(f"Unsafe bootstrap archive entry: {member.name}")
            # None of the activation entrypoints require the few template/rule links.
            # Avoid symlink privileges/alternate streams on Windows entirely.
            if not (member.isfile() or member.isdir()):
                continue
            output = destination.joinpath(*path.parts)
            if not output.resolve().is_relative_to(destination.resolve()):
                raise ValueError(f"Bootstrap path escapes the scratch directory: {member.name}")
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"Missing bootstrap content: {member.name}")
            with source, output.open("xb") as stream:
                shutil.copyfileobj(source, stream)
            output.chmod(member.mode & 0o755)
    return destination / "OpenFOAM-v2406"


@unittest.skipUnless(ARCHIVE and BASH, "Set OPENFOAM_ARCHIVE and a GNU Bash runtime to test the real release")
class UpstreamEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = Path(ARCHIVE).expanduser().resolve()
        if not archive.is_file():
            raise unittest.SkipTest(f"OPENFOAM_ARCHIVE is missing: {archive}")
        if os.name != "nt" and (platform.system() != "Linux" or platform.machine() != "x86_64"):
            raise unittest.SkipTest("Native release-bootstrap regression currently targets Linux x86-64")
        parent = os.environ.get("SEDIMENT_TEST_TMPDIR")
        cls.scratch = tempfile.TemporaryDirectory(prefix="sediment-upstream-", dir=parent)
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.base = Path(cls.scratch.name)
        if any(character.isspace() for character in str(cls.base)):
            raise unittest.SkipTest("Set SEDIMENT_TEST_TMPDIR to a whitespace-free existing directory")
        cls.project = extract_bootstrap(archive, cls.base)
        cls.bashrc = cls.project / "etc/bashrc"
        cls.home = cls.base / "home"
        cls.home.mkdir()
        cls.payload = ["argument with spaces", "", "FOAM_TEST_POISON=do-not-eval", "literal $(no-command)"]

    def environment(self):
        result = {
            "HOME": shell_path(self.home), "USER": "bootstrap-test", "LOGNAME": "bootstrap-test",
            "PATH": "/usr/bin:/bin", "TERM": "dumb", "FOAM_CONFIG_MODE": "o",
        }
        if os.name == "nt":
            for key in ("SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP"):
                if key in os.environ:
                    result[key] = os.environ[key]
        return result

    def prelude(self):
        text = r'''# Discovery-only fallback: no external MPI executable is run.
if ! command -v orte-info >/dev/null && ! command -v mpicc >/dev/null; then
    mpicc() { printf '%s\n' '-L/usr/lib'; }
    export -f mpicc
fi
'''
        if os.name == "nt":
            # Git Bash's real uname says MINGW. Only OS discovery is emulated so
            # the exact release scripts take the Linux GCC branch under test.
            text = r'''uname()
{
    case "${1:-}" in
        -m) printf 'x86_64\n' ;;
        *) printf 'Linux\n' ;;
    esac
}
export -f uname
''' + text
        return text

    def execute(self, script):
        return subprocess.run(
            [BASH, "--noprofile", "--norc", "-c", self.prelude() + script, "real-release",
             shell_path(self.bashrc), shell_path(self.project), *self.payload],
            env=self.environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=30,
        )

    def test_legacy_errexit_function_fails_at_real_cgal_optional_probe(self):
        # xtrace locates the actual intentional return-1 even on Bash versions
        # where the secondary pop_var_context warning has already been fixed.
        result = self.execute(r'''bashrc=$1; shift 2
set -ex
_legacy_load() { source "$bashrc"; }
_legacy_load
printf 'UNREACHABLE_AFTER_STRICT_SOURCE\n'
''')
        self.assertNotEqual(result.returncode, 0, "Old strict activation unexpectedly passed")
        self.assertNotIn("UNREACHABLE_AFTER_STRICT_SOURCE", result.stdout)
        self.assertIn("config.sh/CGAL", result.stderr)
        self.assertIn("_foamAddLibAuto", result.stderr)
        self.assertIn("return 1", result.stderr)

    def test_fixed_activation_initializes_real_release_and_preserves_shell_state(self):
        script = r'''bashrc=$1; expected_project=$2; shift 2
set -eu
'''
        script += installer._source_openfoam_without_arguments('"$bashrc"')
        script += r'''
[[ "$(foamEtcFile -show-api)" == 2406 ]]
[[ "$WM_OPTIONS" == linux64GccDPInt32Opt ]]
[[ "$WM_PROJECT_DIR" == "$expected_project" ]]
[[ "$-" == *e* && "$-" == *u* ]]
printf 'API=%s\nABI=%s\nROOT=%s\nOPTIONS=%s\nSETTINGS=%s\nGMP=%s\nPOISON=%s\nARGC=%s\n' \
    "$(foamEtcFile -show-api)" "$WM_OPTIONS" "$WM_PROJECT_DIR" "$-" \
    "${FOAM_SETTINGS-unset}" "${GMP_ARCH_PATH-unset}" "${FOAM_TEST_POISON-unset}" "$#"
printf 'arg=<%s>\n' "$@"
'''
        result = self.execute(script)
        self.assertEqual(result.returncode, 0, f"Actual v2406 bootstrap failed:\n{result.stderr[-12000:]}")
        self.assertNotIn("pop_var_context", result.stderr)
        self.assertIn("API=2406\nABI=linux64GccDPInt32Opt\n", result.stdout)
        self.assertIn("ROOT=" + shell_path(self.project) + "\n", result.stdout)
        self.assertIn("SETTINGS=unset\nGMP=unset\nPOISON=unset\nARGC=4\n", result.stdout)
        self.assertEqual(
            [line for line in result.stdout.splitlines() if line.startswith("arg=<")],
            [f"arg=<{value}>" for value in self.payload],
        )

    def test_production_build_wrapper_initializes_real_release(self):
        # Run the exact production command script, including compiler/MPI
        # exports and project/API/ABI gates. Only the host executable and two
        # fixture paths are adapted for GNU Git Bash on Windows.
        with mock.patch.object(installer, "capture", return_value="not executed") as captured:
            installer.foam_command(
                self.bashrc, self.base / "stack",
                ["/usr/bin/printf", "arg=<%s>\\n", *self.payload], 2,
                capture_output=True,
            )
        command = list(captured.call_args.args[0])
        command[0] = BASH
        command[4] = self.prelude() + command[4]
        command[6] = shell_path(self.bashrc)
        command[7] = shell_path(self.base / "stack")
        environment = self.environment()
        environment.pop("FOAM_CONFIG_MODE")  # The production prelude must set it.
        result = subprocess.run(
            command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertEqual(result.returncode, 0, f"Production v2406 wrapper failed:\n{result.stderr[-12000:]}")
        self.assertNotIn("pop_var_context", result.stderr)
        self.assertEqual(
            [line for line in result.stdout.splitlines() if line.startswith("arg=<")],
            [f"arg=<{value}>" for value in self.payload],
        )

    def test_production_wrapper_preserves_cwd_for_stock_wmake_directory_checks(self):
        # No libraries or tools are compiled. src is an empty directory fixture;
        # the untouched stock wmake/check-dir scripts test directory identity.
        if os.name == "nt" and self.base.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            self.skipTest("Git Bash TEMP has a /tmp mount alias; set SEDIMENT_TEST_TMPDIR outside TEMP")
        source = self.project / "src"
        source.mkdir(exist_ok=True)
        for current in (self.project, source):
            with self.subTest(cwd=current.name):
                payload = ["wmake", "-check-dir", shell_path(current)]
                if os.name == "nt":
                    # This bundled Git runtime has GNU Bash as usr/bin/sh.exe,
                    # but no /bin/bash shebang target. Execute the unchanged
                    # wmake script explicitly; native Linux uses PATH/shebang.
                    payload = [shell_path(Path(BASH)), shell_path(self.project / "wmake/wmake"),
                               "-check-dir", shell_path(current)]
                with mock.patch.object(installer, "capture", return_value="not executed") as captured:
                    installer.foam_command(
                        self.bashrc, self.base / "stack",
                        payload, 2,
                        cwd=current, capture_output=True,
                    )
                command = list(captured.call_args.args[0])
                command[0] = BASH
                command[4] = self.prelude() + 'printf "cwd-before=<%s>\\n" "$(pwd -P)" >&2\n' + command[4]
                command[4] = command[4].replace(
                    'exec "$@"',
                    'printf "cwd-after=<%s> PWD=<%s>\\n" "$(pwd -P)" "$PWD" >&2\nexec "$@"',
                )
                command[6] = shell_path(self.bashrc)
                command[7] = shell_path(self.base / "stack")
                result = subprocess.run(
                    command, cwd=current, env=self.environment(),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding="utf-8", errors="replace", timeout=30,
                )
                self.assertEqual(result.returncode, 0,
                                 "stdout:\n" + result.stdout[-12000:] + "\nstderr:\n" + result.stderr[-12000:])


if __name__ == "__main__":
    unittest.main()
