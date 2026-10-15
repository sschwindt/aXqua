"""Offline safety and command-construction checks (no downloads or sudo)."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from sediment_installer import core, installer  # noqa: E402

OPENFOAM_COLON_MEMBER = (
    "OpenFOAM-v2406/tutorials/heatTransfer/chtMultiRegionSimpleFoam/"
    "jouleHeatingSolid/0.orig/solid/jouleHeatingSource:V"
)


class DownloadResponse(io.BytesIO):
    def __init__(self, body=b"fixture", url="https://example.invalid/file", *,
                 status=200, headers=None):
        super().__init__(body)
        self.url = url
        self.status = status
        self.headers = {"Content-Length": str(len(body)),
                        "Content-Type": "application/octet-stream"}
        for name, value in (headers or {}).items():
            if value is None:
                self.headers.pop(name, None)
            else:
                self.headers[name] = value

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status


def make_archive(path, entries):
    """entries: name, payload/type, optional link target."""
    with tarfile.open(path, "w:gz") as archive:
        for name, kind, payload in entries:
            entry = tarfile.TarInfo(name)
            entry.mode = 0o755 if kind == "directory" else 0o644
            if kind == "file":
                entry.size = len(payload)
                archive.addfile(entry, io.BytesIO(payload))
            else:
                entry.type = {"directory": tarfile.DIRTYPE, "symlink": tarfile.SYMTYPE,
                              "hardlink": tarfile.LNKTYPE, "fifo": tarfile.FIFOTYPE}[kind]
                if kind in ("symlink", "hardlink"):
                    entry.linkname = payload
                archive.addfile(entry)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.target = Path(self.directory.name) / "cache" / "file.tgz"
        self.body = b"known pinned bytes"
        self.digest = hashlib.sha256(self.body).hexdigest()

    def test_download_checks_digest_and_preserves_foreign_part(self):
        self.target.parent.mkdir()
        foreign = self.target.with_name(self.target.name + ".part")
        foreign.write_bytes(b"do not touch")
        with mock.patch.object(core.urllib.request, "urlopen",
                               return_value=DownloadResponse(self.body)) as urlopen:
            result = core.checked_download("https://example.invalid/file", self.digest, self.target)
        self.assertEqual(result, self.target)
        self.assertEqual(result.read_bytes(), self.body)
        self.assertEqual(foreign.read_bytes(), b"do not touch")
        self.assertEqual(list(self.target.parent.glob("*.part-*")), [])
        self.assertEqual(urlopen.call_count, 1)

    def test_verified_cache_does_not_touch_network(self):
        self.target.parent.mkdir()
        self.target.write_bytes(self.body)
        with mock.patch.object(core.urllib.request, "urlopen") as urlopen:
            self.assertEqual(core.checked_download("https://example.invalid/file",
                                                   self.digest.upper(), self.target), self.target)
        urlopen.assert_not_called()

    def test_corrupt_cache_is_retained_not_replaced(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b"corrupt or user modified")
        with mock.patch.object(core.urllib.request, "urlopen") as urlopen:
            with self.assertRaisesRegex(core.InstallError, "Cache checksum mismatch"):
                core.checked_download("https://example.invalid/file", self.digest, self.target)
        self.assertEqual(self.target.read_bytes(), b"corrupt or user modified")
        urlopen.assert_not_called()

    def test_wrong_upstream_digest_removes_only_own_temporary(self):
        with mock.patch.object(core.urllib.request, "urlopen",
                               return_value=DownloadResponse(b"changed upstream")):
            with self.assertRaisesRegex(core.InstallError, "Upstream checksum mismatch"):
                core.checked_download("https://example.invalid/file", self.digest, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_checksum_failure_reports_received_content_without_url_tokens(self):
        changed = b"complete but not the pinned archive"
        received_digest = hashlib.sha256(changed).hexdigest()
        final_url = "https://mirror.invalid/releases/file.tgz?token=private-query#private-fragment"
        with mock.patch.object(core.urllib.request, "urlopen",
                               return_value=DownloadResponse(changed, final_url)):
            with self.assertRaisesRegex(core.InstallError, "Upstream checksum mismatch") as caught:
                core.checked_download("https://example.invalid/file", self.digest, self.target)
        error = str(caught.exception)
        self.assertIn(self.digest, error)
        self.assertIn(received_digest, error)
        self.assertIn(str(len(changed)), error)
        self.assertIn("application/octet-stream", error)
        self.assertIn("https://mirror.invalid/releases/file.tgz", error)
        self.assertNotIn("private-query", error)
        self.assertNotIn("private-fragment", error)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_short_body_is_reported_as_incomplete_not_changed_upstream(self):
        expected_bytes = len(self.body) + 20
        with mock.patch.object(core.urllib.request, "urlopen", return_value=DownloadResponse(
                self.body, headers={"Content-Length": str(expected_bytes)})):
            with self.assertRaisesRegex(core.InstallError, "Incomplete download") as caught:
                core.checked_download("https://example.invalid/file", self.digest, self.target)
        self.assertIn(str(len(self.body)), str(caught.exception))
        self.assertIn(str(expected_bytes), str(caught.exception))
        self.assertNotIn("Upstream checksum mismatch", str(caught.exception))
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_partial_http_response_is_rejected_even_when_digest_matches(self):
        with mock.patch.object(core.urllib.request, "urlopen",
                               return_value=DownloadResponse(self.body, status=206)):
            with self.assertRaisesRegex(core.InstallError, "HTTP.*206"):
                core.checked_download("https://example.invalid/file", self.digest, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_html_response_is_rejected_even_when_digest_matches(self):
        for response_type in ("text/html; charset=utf-8", "application/xhtml+xml"):
            with self.subTest(response_type=response_type):
                with mock.patch.object(core.urllib.request, "urlopen", return_value=DownloadResponse(
                        self.body, headers={"Content-Type": response_type})):
                    with self.assertRaisesRegex(core.InstallError, "(?i)unexpected HTML download response"):
                        core.checked_download("https://example.invalid/file", self.digest, self.target)
                self.assertFalse(self.target.exists())
                self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_missing_content_length_is_accepted_when_digest_matches(self):
        with mock.patch.object(core.urllib.request, "urlopen", return_value=DownloadResponse(
                self.body, headers={"Content-Length": None})):
            result = core.checked_download("https://example.invalid/file", self.digest, self.target)
        self.assertEqual(result.read_bytes(), self.body)
        self.assertEqual(list(self.target.parent.glob("*.part-*")), [])

    def test_invalid_content_length_is_rejected(self):
        for header in ("not-a-number", "-1"):
            with self.subTest(header=header):
                with mock.patch.object(core.urllib.request, "urlopen", return_value=DownloadResponse(
                        self.body, headers={"Content-Length": header})):
                    with self.assertRaisesRegex(core.InstallError, "(?i)invalid.*Content-Length"):
                        core.checked_download("https://example.invalid/file", self.digest, self.target)
                self.assertFalse(self.target.exists())
                self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_html_failure_redacts_requested_and_final_url_tokens(self):
        requested_url = "https://example.invalid/file?auth=private-request#private-request-fragment"
        final_url = "https://mirror.invalid/file?token=private-response#private-response-fragment"
        with mock.patch.object(core.urllib.request, "urlopen", return_value=DownloadResponse(
                self.body, final_url, headers={"Content-Type": "text/html"})):
            with self.assertRaises(core.InstallError) as caught:
                core.checked_download(requested_url, self.digest, self.target)
        for token in ("private-request", "private-request-fragment",
                      "private-response", "private-response-fragment"):
            self.assertNotIn(token, str(caught.exception))
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_insecure_redirect_is_rejected(self):
        with mock.patch.object(core.urllib.request, "urlopen",
                               return_value=DownloadResponse(self.body, "http://example.invalid/file")):
            with self.assertRaisesRegex(core.InstallError, "non-HTTPS"):
                core.checked_download("https://example.invalid/file", self.digest, self.target)
        self.assertFalse(self.target.exists())

    def test_unpinned_or_insecure_urls_do_not_create_cache(self):
        cases = [("http://example.invalid/file", self.digest),
                 ("https://example.invalid/file", "no digest"),
                 ("https://example.invalid/file", "z" * 64)]
        for url, digest in cases:
            with self.subTest(url=url, digest=digest):
                with self.assertRaises(core.InstallError):
                    core.checked_download(url, digest, self.target)
        self.assertFalse(self.target.parent.exists())

    def test_network_error_leaves_no_partial_download(self):
        with mock.patch.object(core.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "offline"):
                core.checked_download("https://example.invalid/file", self.digest, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "Windows symlinks require Developer Mode/elevation")
    def test_symlink_cache_cannot_overwrite_foreign_file(self):
        self.target.parent.mkdir()
        foreign = self.target.parent / "user.txt"
        foreign.write_bytes(self.body)
        self.target.symlink_to(foreign)
        with self.assertRaisesRegex(core.InstallError, "symlink cache"):
            core.checked_download("https://example.invalid/file", self.digest, self.target)
        self.assertEqual(foreign.read_bytes(), self.body)


class ArchivePathTests(unittest.TestCase):
    def test_reported_openfoam_filename_is_valid_on_posix(self):
        # Patch the module's os object, not global os.name: pathlib chooses its
        # concrete Path class from the real platform and must remain untouched.
        with mock.patch.object(core, "os", SimpleNamespace(name="posix")):
            self.assertEqual(core._archive_path(OPENFOAM_COLON_MEMBER).as_posix(),
                             OPENFOAM_COLON_MEMBER)
            self.assertEqual(core._archive_path("root/file:stream").as_posix(),
                             "root/file:stream")

    def test_native_windows_colon_and_ads_paths_are_rejected(self):
        with mock.patch.object(core, "os", SimpleNamespace(name="nt")):
            for name in (OPENFOAM_COLON_MEMBER, "root/file:stream", "root/C:drive",
                         "C:/Windows/file", "C:relative"):
                with self.subTest(name=name):
                    with self.assertRaisesRegex(core.InstallError, "Unsafe archive name"):
                        core._archive_path(name)

    def test_nul_backslash_and_traversal_remain_unsafe_on_posix(self):
        with mock.patch.object(core, "os", SimpleNamespace(name="posix")):
            for name in ("root/file\0tail", "root\\windows", "/absolute",
                         "root/../escape", "../escape"):
                with self.subTest(name=name):
                    with self.assertRaises(core.InstallError):
                        core._archive_path(name)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.archive = self.base / "source.tgz"
        self.target = self.base / "extracted"

    def extract(self, entries):
        make_archive(self.archive, entries)
        return core.safe_extract_tar(self.archive, self.target, "root")

    def test_file_extracts_into_single_known_root(self):
        self.assertEqual(self.extract([("root/sub/file", "file", b"source")]), self.target)
        self.assertEqual((self.target / "sub/file").read_bytes(), b"source")
        self.assertEqual(list(self.base.glob(".extract-*")), [])

    def test_existing_target_is_preserved(self):
        self.target.mkdir()
        sentinel = self.target / "user.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(core.InstallError, "destination exists"):
            self.extract([("root/file", "file", b"upstream")])
        self.assertEqual(sentinel.read_text(), "keep")

    def test_unsafe_names_rejected_without_destination(self):
        for name in ("../escape", "/absolute", "root/../escape", "other/file",
                     "root\\windows", "C:/Windows/file", "C:\\Windows\\file"):
            with self.subTest(name=name):
                with self.assertRaises(core.InstallError):
                    self.extract([(name, "file", b"unsafe")])
                self.assertFalse(self.target.exists())
                self.assertEqual(list(self.base.glob(".extract-*")), [])

    def test_escaping_links_are_rejected_before_extraction(self):
        for kind, target in (("symlink", "../../escape"), ("symlink", "/etc/passwd"),
                             ("hardlink", "other/file"), ("hardlink", "../escape"),
                             ("symlink", "..\\escape"), ("hardlink", "root\\file"),
                             ("symlink", "C:\\Windows\\file")):
            with self.subTest(kind=kind, target=target):
                with self.assertRaises(core.InstallError):
                    self.extract([("root/escape", kind, target)])
                self.assertFalse(self.target.exists())

    def test_nul_link_target_is_rejected_before_normalization(self):
        # TAR string fields use NUL terminators. Supply a synthetic TarInfo to
        # exercise the validation directly without tarfile truncating the name.
        member = tarfile.TarInfo("root/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "file\0tail"
        with mock.patch.object(core.tarfile, "open") as archive_open, \
                mock.patch.object(core.posixpath, "normpath",
                                  wraps=core.posixpath.normpath) as normalize:
            archive_open.return_value.__enter__.return_value.getmembers.return_value = [member]
            with self.assertRaisesRegex(core.InstallError, "Unsafe archive link"):
                core.safe_extract_tar(self.archive, self.target, "root")
            # From Python 3.12 on shutil.rmtree normalizes its own paths, so the
            # check is that the link target never reached normpath, not "no call".
            self.assertFalse(any("\0" in str(arg) for call in normalize.call_args_list
                                 for arg in call.args))
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.base.glob(".extract-*")), [])

    def test_link_normalized_to_dot_is_rejected_without_destination(self):
        for kind, target in (("symlink", ".."), ("hardlink", ".")):
            with self.subTest(kind=kind, target=target):
                with self.assertRaisesRegex(core.InstallError, "link escapes root"):
                    self.extract([("root/escape", kind, target)])
                self.assertFalse(self.target.exists())
                self.assertEqual(list(self.base.glob(".extract-*")), [])

    @unittest.skipUnless(os.name == "nt", "Native Windows filesystem/ADS restriction")
    def test_native_windows_colon_entries_and_drive_links_are_rejected(self):
        for name in ("root/file:stream", "root/C:drive"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(core.InstallError, "Unsafe archive name"):
                    self.extract([(name, "file", b"unsafe")])
                self.assertFalse(self.target.exists())
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(core.InstallError, "Unsafe archive link"):
                    self.extract([("root/link", kind, "C:/Windows/file")])
                self.assertFalse(self.target.exists())

    @unittest.skipIf(os.name == "nt", "Colon filenames require a POSIX filesystem")
    def test_exact_reported_openfoam_colon_member_extracts_on_posix(self):
        make_archive(self.archive, [(OPENFOAM_COLON_MEMBER, "file", b"OpenFOAM field fixture")])
        core.safe_extract_tar(self.archive, self.target, "OpenFOAM-v2406")
        relative = OPENFOAM_COLON_MEMBER.split("/", 1)[1]
        self.assertEqual((self.target / relative).read_bytes(), b"OpenFOAM field fixture")
        self.assertEqual(list(self.base.glob(".extract-*")), [])

    @unittest.skipIf(os.name == "nt", "Colon link targets require a POSIX filesystem")
    def test_internal_colon_symlink_and_hardlink_targets_extract_on_posix(self):
        self.extract([("root/source:V", "file", b"source"),
                      ("root/sub/link", "symlink", "../source:V"),
                      ("root/copy", "hardlink", "root/source:V")])
        self.assertTrue((self.target / "sub/link").is_symlink())
        self.assertEqual((self.target / "sub/link").read_bytes(), b"source")
        self.assertEqual((self.target / "copy").read_bytes(), b"source")

    def test_devices_and_duplicate_files_are_rejected(self):
        with self.assertRaisesRegex(core.InstallError, "entry type"):
            self.extract([("root/pipe", "fifo", "")])
        with self.assertRaisesRegex(core.InstallError, "Duplicate/conflicting"):
            self.extract([("root/file", "file", b"first"), ("root/file", "file", b"second")])
        self.assertFalse(self.target.exists())

    def test_internal_hardlink_is_accepted(self):
        self.extract([("root/file", "file", b"source"), ("root/copy", "hardlink", "root/file")])
        self.assertEqual((self.target / "copy").read_bytes(), b"source")

    @unittest.skipIf(os.name == "nt", "Windows symlinks require Developer Mode/elevation")
    def test_internal_relative_symlink_is_accepted(self):
        self.extract([("root/file", "file", b"source"), ("root/sub/link", "symlink", "../file")])
        self.assertEqual((self.target / "sub/link").read_bytes(), b"source")


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.prefix = self.base / "owned"

    def test_new_prefix_and_same_identity_resume_preserve_user_file(self):
        core.claim_prefix(self.prefix, "source-pin")
        sentinel = self.prefix / "keep.txt"
        sentinel.write_text("user content")
        core.claim_prefix(self.prefix, "source-pin")
        self.assertEqual(sentinel.read_text(), "user content")
        marker = self.prefix / ".sediment-installer.json"
        self.assertEqual(json.loads(marker.read_text()), {"identity": "source-pin"})

    def test_foreign_prefix_is_untouched(self):
        self.prefix.mkdir()
        sentinel = self.prefix / "keep.txt"
        sentinel.write_text("user content")
        with self.assertRaisesRegex(core.InstallError, "Foreign/unmarked"):
            core.claim_prefix(self.prefix, "source-pin")
        self.assertEqual(list(self.prefix.iterdir()), [sentinel])

    def test_changed_identity_is_rejected(self):
        core.claim_prefix(self.prefix, "old-pin")
        with self.assertRaisesRegex(core.InstallError, "source pin changed"):
            core.claim_prefix(self.prefix, "new-pin")
        self.assertEqual(json.loads((self.prefix / ".sediment-installer.json").read_text()),
                         {"identity": "old-pin"})

    def test_home_root_and_whitespace_prefixes_rejected(self):
        with mock.patch.object(core.Path, "home", return_value=self.prefix):
            with self.assertRaisesRegex(core.InstallError, "HOME/root"):
                core.claim_prefix(self.prefix, "pin")
        with self.assertRaisesRegex(core.InstallError, "HOME/root"):
            core.claim_prefix(Path(self.base.anchor), "pin")
        with self.assertRaisesRegex(core.InstallError, "whitespace"):
            core.claim_prefix(self.base / "build with spaces", "pin")
        self.assertFalse(self.prefix.exists())

    def test_generated_foreign_file_is_preserved(self):
        target = self.base / "launcher.sh"
        target.write_text("custom launcher")
        with self.assertRaisesRegex(core.InstallError, "edited or differs"):
            core.write_generated(target, "# Generated by openfoam-sediment-installer\nexit 0\n")
        self.assertEqual(target.read_text(), "custom launcher")

    def test_generated_label_required_and_identical_resume_allowed(self):
        target = self.base / "launcher.sh"
        with self.assertRaisesRegex(core.InstallError, "ownership label"):
            core.write_generated(target, "exit 0\n")
        self.assertFalse(target.exists())
        label = "# Generated by openfoam-sediment-installer\n"
        core.write_generated(target, label + "exit 0\n", executable=True)
        core.write_generated(target, label + "exit 0\n", executable=True)
        with self.assertRaisesRegex(core.InstallError, "edited or differs"):
            core.write_generated(target, label + "exit 1\n", executable=True)
        self.assertEqual(target.read_text(), label + "exit 0\n")

    @unittest.skipIf(os.name == "nt", "Windows symlinks require Developer Mode/elevation")
    def test_symlink_prefix_and_generated_targets_are_rejected(self):
        foreign = self.base / "foreign"
        foreign.mkdir()
        self.prefix.symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(core.InstallError, "prefix may not be a symlink"):
            core.claim_prefix(self.prefix, "pin")
        self.assertEqual(list(foreign.iterdir()), [])
        user_file = foreign / "user.txt"
        user_file.write_text("keep")
        target = self.base / "launcher.sh"
        target.symlink_to(user_file)
        with self.assertRaisesRegex(core.InstallError, "generated-file symlink"):
            core.write_generated(target, "# Generated by openfoam-sediment-installer\nexit 0\n")
        self.assertEqual(user_file.read_text(), "keep")


class CommandTests(unittest.TestCase):
    def test_actual_failed_process_retains_log_and_output(self):
        with tempfile.TemporaryDirectory() as task_dir:
            base = Path(task_dir)
            log = base / "owned-prefix/logs/openfoam-Allwmake.log"
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(core.InstallError, "Command failed.*see") as caught:
                    core.run([sys.executable, "-c",
                              "import sys; print('build failure marker', flush=True); sys.exit(1)"],
                             cwd=base, log=log)
            self.assertIn(str(log), str(caught.exception))
            self.assertTrue(log.parent.is_dir())
            self.assertIn("build failure marker", log.read_text(encoding="utf-8"))

    def test_clean_environment_discards_inherited_abis_and_injection(self):
        contaminated = {"HOME": "/home/user", "USER": "user", "DISPLAY": ":0",
                        "WM_PROJECT_DIR": "/old/OpenFOAM-9", "LD_LIBRARY_PATH": "/old/lib",
                        "PYTHONPATH": "/conda", "LD_PRELOAD": "/inject.so",
                        "BASH_ENV": "/inject.sh", "QT_PLUGIN_PATH": "/wrong/qt",
                        "PATH": "/conda/bin", "HTTPS_PROXY": "https://proxy.invalid"}
        with mock.patch.dict(os.environ, contaminated, clear=True):
            clean = core.clean_environment()
        self.assertEqual(clean["HOME"], "/home/user")
        self.assertEqual(clean["PATH"], "/usr/bin:/bin:/usr/local/bin")
        self.assertEqual(clean["HTTPS_PROXY"], "https://proxy.invalid")
        for key in ("WM_PROJECT_DIR", "LD_LIBRARY_PATH", "PYTHONPATH", "LD_PRELOAD",
                    "BASH_ENV", "QT_PLUGIN_PATH"):
            self.assertNotIn(key, clean)

    def test_foam_command_uses_positional_paths_not_shell_text(self):
        bashrc = Path("/path with spaces/$(touch injected)/etc/bashrc")
        prefix = Path("/owned/prefix")
        with mock.patch.object(installer, "capture", return_value="2406") as capture:
            result = installer.foam_command(bashrc, prefix, ["checkMesh", "-help"], 2,
                                            capture_output=True)
        command = capture.call_args.args[0]
        self.assertEqual(result, "2406")
        self.assertEqual(command[:4], ["/bin/bash", "--noprofile", "--norc", "-c"])
        self.assertNotIn(str(bashrc), command[4])
        self.assertIn('source "$bashrc"', command[4])
        self.assertIn(installer._source_openfoam_without_arguments('"$bashrc"'), command[4])
        self.assertIn('foamEtcFile -show-api', command[4])
        self.assertIn('linux64GccDPInt32Opt', command[4])
        self.assertEqual(command[5:], ["foam-build", str(bashrc), str(prefix), "2", "checkMesh", "-help"])

    def test_dry_run_has_no_mutating_or_network_calls(self):
        with tempfile.TemporaryDirectory() as task_dir:
            target = Path(task_dir) / "not-created"
            with mock.patch.object(installer, "install") as install, \
                    mock.patch.object(core, "run") as run, \
                    mock.patch.object(core.urllib.request, "urlopen") as urlopen, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                result = installer.main(["--dry-run", "--prefix", str(target),
                                         "--install-system-packages", "--smoke-test"])
            self.assertEqual(result, 0)
            self.assertIn("No actions executed", output.getvalue())
            self.assertFalse(target.exists())
            install.assert_not_called()
            run.assert_not_called()
            urlopen.assert_not_called()

    def test_os_release_quoted_values_and_comments(self):
        with tempfile.TemporaryDirectory() as task_dir:
            release = Path(task_dir) / "os-release"
            release.write_text('# comment\nID=debian\nVERSION_ID="12"\n'
                               'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n', encoding="utf-8")
            self.assertEqual(installer.read_os_release(release),
                             {"ID": "debian", "VERSION_ID": "12",
                              "PRETTY_NAME": "Debian GNU/Linux 12 (bookworm)"})

    def test_os_release_malformed_quotes_fail(self):
        with tempfile.TemporaryDirectory() as task_dir:
            release = Path(task_dir) / "os-release"
            release.write_text('ID="debian\n', encoding="utf-8")
            with self.assertRaisesRegex(core.InstallError, "Malformed"):
                installer.read_os_release(release)


if __name__ == "__main__":
    unittest.main()
