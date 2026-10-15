"""Executable Bash regressions for OpenCFD's sourced-settings argv interface.

Use SEDIMENT_TEST_BASH to select a Bash-compatible executable explicitly (for
example bundled Git's sh.exe on Windows). No OpenFOAM build or network needed.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from sediment_installer import core, installer  # noqa: E402

# Windows PATH may contain a WSL bash.exe shim, not a native GNU Bash runtime.
# Require an explicit executable there; Ubuntu CI can use its system Bash.
BASH = os.environ.get("SEDIMENT_TEST_BASH") or (shutil.which("bash") if os.name != "nt" else None)
OPENFOAM_ARCHIVE = os.environ.get("OPENFOAM_ARCHIVE")

FAKE_BASHRC = r'''# Mimic the relevant OpenCFD settings parser, without OpenFOAM.
export FOAM_TEST_SOURCE_COUNT="$#"
export FOAM_TEST_FUNCTION_DEPTH="${#FUNCNAME[@]}"
export FOAM_TEST_LOADED=yes
export WM_PROJECT_DIR="${BASH_SOURCE[0]%/etc/bashrc}"
export WM_PROJECT_VERSION=v2406
export WM_OPTIONS=linux64GccDPInt32Opt
foamEtcFile() { printf '2406\n'; }
export -f foamEtcFile
if [[ "$#" -gt 0 && "${1#-}" == "$1" ]]; then
    for setting in "$@"; do
        if [[ -f "$setting" ]]; then
            source "$setting"
        elif [[ "$setting" == *=* ]]; then
            eval "export $setting"
        fi
    done
fi
:
'''

PROBE = r'''printf 'source_count=%s\nfunction_depth=%s\nloaded=%s\npoison=%s\nargs=%s\n' \
    "${FOAM_TEST_SOURCE_COUNT:-missing}" "${FOAM_TEST_FUNCTION_DEPTH:-missing}" \
    "${FOAM_TEST_LOADED:-missing}" \
    "${FOAM_TEST_POISON:-unset}" "$#"
printf 'arg=<%s>\n' "$@"
'''


def shell_path(path: Path) -> str:
    """Convert only fixture/executable paths for Git Bash, never payload argv."""
    path = Path(path).resolve()
    value = path.as_posix()
    if os.name == "nt":
        if not path.drive or len(path.drive) != 2 or path.drive[1] != ":":
            raise ValueError("Git Bash test fixtures require a local drive path")
        return "/" + path.drive[0].lower() + value[2:]
    return value


class ShellTemplateTests(unittest.TestCase):
    def test_top_level_loader_is_shared_by_all_generated_solver_wrappers(self):
        with tempfile.TemporaryDirectory() as task_dir:
            prefix = Path(task_dir) / "prefix"
            # Generated text is checked without requiring this path to exist.
            bashrc = PurePosixPath("/example path/OpenFOAM-v2406/etc/bashrc")
            installer.write_launchers(prefix, bashrc, None, 2)
            helper = installer._source_openfoam_without_arguments(shlex.quote(str(bashrc)))
            common = (prefix / "shell-rc.sh").read_text(encoding="utf-8")
            self.assertIn(helper, common)
            self.assertIn("FOAM_CONFIG_MODE=o FOAM_MODULE_PREFIX=false WM_NCOMPPROCS=2", common)
            self.assertLess(common.index("FOAM_CONFIG_MODE=o"), common.index(helper))
            self.assertIn('source ', helper)
            self.assertIn('_sediment_saved_argv=("$@")', helper)
            self.assertIn('set +e +u\nset --\nsource ', helper)
            self.assertIn('set -- "${_sediment_saved_argv[@]}"', helper)
            self.assertNotIn('_sediment_load_openfoam', helper)
            for solver in ("sediDriftFoam", "sediDriftFoam2", "sediDriftFoam2Rating"):
                with self.subTest(solver=solver):
                    # shlex decodes the quoted -c script but does not execute it.
                    words = shlex.split((prefix / "bin" / solver).read_text(encoding="utf-8"))
                    command_script = words[words.index("-c") + 1]
                    self.assertIn(helper, command_script)
                    self.assertIn("FOAM_CONFIG_MODE=o FOAM_MODULE_PREFIX=false WM_NCOMPPROCS=2",
                                  command_script)
                    self.assertLess(command_script.index("FOAM_CONFIG_MODE=o"),
                                    command_script.index(helper))
                    self.assertIn(f'exec "$FOAM_USER_APPBIN/{solver}" "$@"', command_script)

    def test_build_command_disables_user_group_configuration_and_modules(self):
        with mock.patch.object(installer, "capture", return_value="") as capture:
            installer.foam_command(Path("/project/etc/bashrc"), Path("/prefix"),
                                   ["/bin/true"], 2, capture_output=True)
        command_script = capture.call_args.args[0][4]
        self.assertIn('WM_NCOMPPROCS="$jobs" FOAM_MODULE_PREFIX=false FOAM_CONFIG_MODE=o',
                      command_script)
        self.assertLess(command_script.index("FOAM_CONFIG_MODE=o"),
                        command_script.index('source "$bashrc"'))


@unittest.skipUnless(BASH, "No Bash runtime; set SEDIMENT_TEST_BASH explicitly")
class ExecutableShellTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.prefix = self.base / "prefix"
        self.project = self.base / "foam project with spaces"
        self.bashrc = self.project / "etc/bashrc"
        self.bashrc.parent.mkdir(parents=True)
        self.bashrc.write_text(FAKE_BASHRC, encoding="utf-8", newline="\n")
        self.settings = self.base / "existing settings with spaces.sh"
        # This inert fixture only sets a marker if erroneously sourced.
        self.settings.write_text("export FOAM_TEST_POISON=existing-file\n", encoding="utf-8")
        self.payload = [shell_path(self.settings), "argument with spaces",
                        "FOAM_TEST_POISON=assignment-value", "literal $(do-not-execute)"]

    def execute(self, command, env=None):
        environment = dict(os.environ if env is None else env)
        # Win32/MSYS process initialization requires these; unlike OpenFOAM
        # variables they cannot contaminate a compiler or library ABI.
        if os.name == "nt":
            for key in ("SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP"):
                if key in os.environ:
                    environment[key] = os.environ[key]
        environment.pop("FOAM_TEST_POISON", None)
        return subprocess.run(command, env=environment, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8",
                              errors="replace", timeout=20)

    def capture_adapter(self, command, env=None, cwd=None):
        command = list(command)
        # Only the wrapper executable and its first two reserved fixture-path
        # parameters are adapted. All command payload arguments stay unchanged.
        command[0] = BASH
        command[6] = shell_path(self.bashrc)
        command[7] = shell_path(self.prefix)
        if os.name == "nt" and command[9] == "/bin/bash":
            command[9] = shell_path(Path(BASH))
        result = self.execute(command, env)
        if result.returncode:
            self.fail(f"Bash wrapper failed ({result.returncode}): {result.stderr}")
        return result.stdout.strip()

    def assert_probe(self, output):
        self.assertIn("source_count=0\nfunction_depth=0\nloaded=yes\npoison=unset\nargs=4\n", output)
        self.assertEqual([line for line in output.splitlines() if line.startswith("arg=<")],
                         [f"arg=<{value}>" for value in self.payload])

    def test_foam_command_sources_zero_args_and_preserves_exec_argv(self):
        command = ["/bin/bash", "--noprofile", "--norc", "-c", PROBE, "probe", *self.payload]
        with mock.patch.object(installer, "capture", side_effect=self.capture_adapter):
            output = installer.foam_command(self.bashrc, self.prefix, command, 2,
                                            capture_output=True)
        self.assert_probe(output)

    def test_optional_nonzero_probe_continues_with_incoming_errexit(self):
        optional = FAKE_BASHRC + r'''
_fixture_optional_probe() { return 1; }
_fixture_optional_probe
unset -f _fixture_optional_probe
export FOAM_TEST_OPTIONAL_COMPLETED=yes
'''
        self.bashrc.write_text(optional, encoding="utf-8", newline="\n")
        command = ["/bin/bash", "--noprofile", "--norc", "-c",
                   'printf "optional=%s\\n" "$FOAM_TEST_OPTIONAL_COMPLETED"\n' + PROBE,
                   "probe", *self.payload]
        with mock.patch.object(installer, "capture", side_effect=self.capture_adapter):
            output = installer.foam_command(self.bashrc, self.prefix, command, 2,
                                            capture_output=True)
        self.assertIn("optional=yes", output)
        self.assert_probe(output)

    def test_incoming_errexit_and_nounset_states_are_restored(self):
        helper = installer._source_openfoam_without_arguments('"$bashrc"')
        flags_probe = r'''case $- in *e*) printf 'errexit=on\n';; *) printf 'errexit=off\n';; esac
case $- in *u*) printf 'nounset=on\n';; *) printf 'nounset=off\n';; esac
'''
        for errexit in (False, True):
            for nounset in (False, True):
                with self.subTest(errexit=errexit, nounset=nounset):
                    script = "set +e +u\n"
                    if errexit:
                        script += "set -e\n"
                    if nounset:
                        script += "set -u\n"
                    script += 'bashrc=$1; shift\n' + helper + flags_probe + PROBE
                    result = self.execute([BASH, "--noprofile", "--norc", "-c", script,
                                           "options", shell_path(self.bashrc), *self.payload])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"errexit={'on' if errexit else 'off'}\n", result.stdout)
                    self.assertIn(f"nounset={'on' if nounset else 'off'}\n", result.stdout)
                    self.assert_probe(result.stdout)

    def test_nonzero_source_return_fails_explicitly_before_exec(self):
        self.bashrc.write_text(FAKE_BASHRC + "\nreturn 7\n", encoding="utf-8", newline="\n")
        script = 'set -e -u\nbashrc=$1; shift\n'
        script += installer._source_openfoam_without_arguments('"$bashrc"')
        script += "printf unreachable\n"
        result = self.execute([BASH, "--noprofile", "--norc", "-c", script, "failure",
                               shell_path(self.bashrc), *self.payload])
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("OpenFOAM environment sourcing failed (status 7)", result.stderr)
        self.assertNotIn("unreachable", result.stdout)

    def test_legacy_inherited_argv_reproduces_binary_source_failure(self):
        # Reproduce the previous pattern using only a harmless shell binary.
        # set -e stops at the failed source, before any payload is executed.
        binary = shell_path(Path(BASH))
        old_script = 'set -e\nbashrc=$1; shift\nsource "$bashrc"\nprintf unreachable\n'
        result = self.execute([BASH, "--noprofile", "--norc", "-c", old_script,
                               "legacy", shell_path(self.bashrc), binary, *self.payload])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot execute binary file", result.stderr.lower())
        self.assertNotIn("unreachable", result.stdout)

    def test_generated_common_startup_sources_zero_args_and_preserves_outer_argv(self):
        # PurePosixPath keeps MSYS fixture spelling when generation runs under
        # Windows Python; no production installer paths are changed.
        bashrc = PurePosixPath(shell_path(self.bashrc))
        installer.write_launchers(self.prefix, bashrc, None, 2)
        script = 'startup=$1; shift\nsource "$startup"\n' + PROBE
        result = self.execute([BASH, "--noprofile", "--norc", "-c", script, "startup",
                               shell_path(self.prefix / "shell-rc.sh"), *self.payload])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_probe(result.stdout)

    @unittest.skipIf(os.name == "nt", "Git Bash lacks the generated Linux /bin/bash wrapper path")
    def test_generated_solver_wrapper_preserves_payload_on_native_linux(self):
        installer.write_launchers(self.prefix, self.bashrc, None, 2)
        executable = self.prefix / "user/platforms/linux64GccDPInt32Opt/bin/sediDriftFoam2"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/bash\n" + PROBE, encoding="utf-8", newline="\n")
        executable.chmod(0o755)
        result = self.execute([BASH, str(self.prefix / "bin/sediDriftFoam2"), *self.payload])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_probe(result.stdout)


@unittest.skipUnless(BASH and OPENFOAM_ARCHIVE,
                     "Set OPENFOAM_ARCHIVE to the pinned local v2406 archive and select Bash")
class OfficialOpenFOAMShellTests(unittest.TestCase):
    """Exercise unmodified upstream shell files, not just a synthetic parser."""
    execute = ExecutableShellTests.execute

    @classmethod
    def setUpClass(cls):
        archive_path = Path(OPENFOAM_ARCHIVE)
        expected = installer.CORE_ARTIFACTS["OpenFOAM-v2406"][1]
        if core.sha256(archive_path) != expected:
            raise AssertionError("OPENFOAM_ARCHIVE is not the pinned official v2406 archive")
        required = {"etc/config.sh/functions", "etc/config.sh/CGAL",
                    "bin/foamEtcFile", "META-INFO/api-info"}
        cls.upstream_files = {}
        with tarfile.open(archive_path, "r:*") as archive:
            for member in archive:
                relative = member.name.removeprefix("OpenFOAM-v2406/")
                if relative in required and member.isfile():
                    source = archive.extractfile(member)
                    if source is None:
                        raise AssertionError(f"Missing official fixture bytes: {member.name}")
                    with source:
                        cls.upstream_files[relative] = source.read()
                    if required <= cls.upstream_files.keys():
                        break
        for relative in required:
            if relative not in cls.upstream_files:
                raise AssertionError(f"Required official fixture absent: {relative}")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        (self.base / ".sediment-test-fixture").write_text("Owned temporary upstream fixture\n")
        self.project = self.base / "OpenFOAM-v2406"
        # Explicitly chosen regular fixture files only; no tar.extractall,
        # archive links, solver execution, installation, or networking.
        for relative, content in self.upstream_files.items():
            target = self.project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            if relative.startswith("bin/"):
                target.chmod(0o755)
        self.bashrc = self.project / "etc/bashrc"

    def write_cgal_scaffold(self):
        scaffold = r'''export FOAM_TEST_SOURCE_COUNT="$#"
export FOAM_TEST_FUNCTION_DEPTH="${#FUNCNAME[@]}"
export WM_PROJECT_DIR="${BASH_SOURCE[0]%/etc/bashrc}"
export WM_PROJECT_VERSION=v2406 WM_ARCH=linux64 WM_COMPILER=Gcc WM_COMPILER_LIB_ARCH=64
export WM_THIRD_PARTY_DIR="$WM_PROJECT_DIR/ThirdParty-v2406"
export FOAM_CONFIG_MODE=o
unset WM_SHELL_FUNCTIONS GMP_ARCH_PATH MPFR_ARCH_PATH
source "$WM_PROJECT_DIR/etc/config.sh/functions"
_foamEtc -config CGAL
export FOAM_TEST_CGAL_COMPLETED=yes
:
'''
        self.bashrc.write_text(scaffold, encoding="utf-8", newline="\n")

    def test_stock_functions_cgal_chain_survives_optional_probe_with_new_loader(self):
        self.write_cgal_scaffold()
        script = 'set -e -u\nbashrc=$1; shift\n'
        script += installer._source_openfoam_without_arguments('"$bashrc"')
        script += r'''printf 'completed=%s\nsource_count=%s\nfunction_depth=%s\nargs=%s\n' \
    "$FOAM_TEST_CGAL_COMPLETED" "$FOAM_TEST_SOURCE_COUNT" "$FOAM_TEST_FUNCTION_DEPTH" "$#"
printf 'arg=<%s>\n' "$@"
'''
        result = self.execute([BASH, "--noprofile", "--norc", "-c", script, "official",
                               shell_path(self.bashrc), "argument with spaces", "KEY=value"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("completed=yes\nsource_count=0\nfunction_depth=0\nargs=2\n", result.stdout)
        self.assertIn("arg=<argument with spaces>\narg=<KEY=value>\n", result.stdout)

    def test_stock_functions_cgal_chain_aborts_with_legacy_strict_function(self):
        self.write_cgal_scaffold()
        script = r'''set -e
bashrc=$1; shift
_legacy_load() { source "$bashrc"; }
_legacy_load
printf unreachable
'''
        result = self.execute([BASH, "--noprofile", "--norc", "-c", script, "legacy",
                               shell_path(self.bashrc)])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("unreachable", result.stdout)

if __name__ == "__main__":
    unittest.main()
