"""Read-only retry caches and captured startup diagnostics; no network."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sediment_installer import core, installer  # noqa: E402


class SourceCacheTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.base = Path(self.scratch.name)
        self.cache = self.base / "old/cache"
        self.cache.mkdir(parents=True)
        self.prefix = self.base / "new"
        self.payloads = {}
        self.artifacts = {}
        for root in ("OpenFOAM-v2406", "ThirdParty-v2406"):
            stream = io.BytesIO()
            body = b"test source\n"
            with tarfile.open(fileobj=stream, mode="w:gz") as tar:
                member = tarfile.TarInfo(root + "/etc/bashrc")
                member.size = len(body)
                member.mode = 0o644
                tar.addfile(member, io.BytesIO(body))
            self.payloads[root] = stream.getvalue()
            self.artifacts[root] = ("https://example.invalid/" + root + ".tgz",
                                    hashlib.sha256(stream.getvalue()).hexdigest())
        patcher = mock.patch.object(installer, "CORE_ARTIFACTS", self.artifacts)
        patcher.start()
        self.addCleanup(patcher.stop)

    def populate_cache(self):
        for root, body in self.payloads.items():
            (self.cache / (root + ".tgz")).write_bytes(body)

    def test_default_install_needs_no_previous_cache(self):
        def download(url, digest, destination):
            root = destination.name.removesuffix(".tgz")
            self.assertEqual(destination, self.prefix / "cache" / (root + ".tgz"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(self.payloads[root])
            return destination

        # The standard command has no --source-cache argument. Neither an old
        # installation nor an existing download directory is required.
        args = installer.parser().parse_args(["--prefix", str(self.prefix)])
        self.assertIsNone(args.source_cache)
        with mock.patch.object(installer, "checked_download", side_effect=download) as mocked:
            bashrc = installer.acquire_core(self.prefix, args.source_cache)
        self.assertEqual(mocked.call_count, 2)
        self.assertTrue(bashrc.is_file())
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_verified_cache_is_read_only_and_sources_are_extracted_fresh(self):
        self.populate_cache()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.cache.iterdir()}
        with mock.patch.object(installer, "checked_download") as download:
            bashrc = installer.acquire_core(self.prefix, self.cache)
        download.assert_not_called()
        self.assertEqual(bashrc, self.prefix / "src/OpenFOAM-v2406/etc/bashrc")
        self.assertEqual(bashrc.read_bytes(), b"test source\n")
        self.assertFalse((self.prefix / "cache").exists())
        self.assertEqual(before,
                         {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.cache.iterdir()})
        for root, (_, digest) in self.artifacts.items():
            self.assertEqual((self.prefix / "src" / root / ".installer-archive-sha256").read_text().strip(),
                             digest)

    def test_missing_archive_downloads_only_into_new_prefix(self):
        root = "OpenFOAM-v2406"
        (self.cache / (root + ".tgz")).write_bytes(self.payloads[root])

        def download(url, digest, destination):
            self.assertEqual(destination, self.prefix / "cache/ThirdParty-v2406.tgz")
            destination.parent.mkdir(parents=True)
            destination.write_bytes(self.payloads["ThirdParty-v2406"])
            return destination

        with mock.patch.object(installer, "checked_download", side_effect=download) as mocked:
            installer.acquire_core(self.prefix, self.cache)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual([p.name for p in self.cache.iterdir()], [root + ".tgz"])

    def test_corrupt_archive_is_retained_and_not_redownloaded(self):
        bad = self.cache / "OpenFOAM-v2406.tgz"
        bad.write_bytes(b"bad archive")
        with mock.patch.object(installer, "checked_download") as download:
            with self.assertRaisesRegex(core.InstallError, "checksum mismatch.*file retained"):
                installer.acquire_core(self.prefix, self.cache)
        download.assert_not_called()
        self.assertEqual(bad.read_bytes(), b"bad archive")
        self.assertFalse(self.prefix.exists())

    def test_missing_directory_and_nonregular_archive_are_rejected(self):
        with self.assertRaisesRegex(core.InstallError, "directory not found"):
            installer.acquire_core(self.prefix, self.base / "missing")
        (self.cache / "OpenFOAM-v2406.tgz").mkdir()
        with self.assertRaisesRegex(core.InstallError, "non-regular cached archive"):
            installer.acquire_core(self.prefix, self.cache)
        self.assertFalse(self.prefix.exists())

    @unittest.skipIf(os.name == "nt", "Windows symlinks require Developer Mode/elevation")
    def test_symlink_archive_is_rejected_without_touching_target(self):
        foreign = self.base / "foreign.tgz"
        foreign.write_bytes(self.payloads["OpenFOAM-v2406"])
        (self.cache / "OpenFOAM-v2406.tgz").symlink_to(foreign)
        with self.assertRaisesRegex(core.InstallError, "non-regular cached archive"):
            installer.acquire_core(self.prefix, self.cache)
        self.assertEqual(foreign.read_bytes(), self.payloads["OpenFOAM-v2406"])

    def test_read_only_cache_does_not_bypass_source_tree_marker(self):
        self.populate_cache()
        target = self.prefix / "src/OpenFOAM-v2406"
        target.mkdir(parents=True)
        sentinel = target / "user.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(core.InstallError, "Foreign/incomplete source tree"):
            installer.acquire_core(self.prefix, self.cache)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_parser_accepts_read_only_source_cache(self):
        args = installer.parser().parse_args(["--source-cache", str(self.cache)])
        self.assertEqual(args.source_cache, self.cache)

    def test_bad_source_cache_fails_before_prefix_or_package_changes(self):
        for extra, message in (([], "directory not found"),
                               (["--reuse-openfoam", "/existing/etc/bashrc"], "not --reuse-openfoam")):
            with self.subTest(extra=extra):
                args = installer.parser().parse_args([
                    "--prefix", str(self.prefix), "--source-cache", str(self.base / "missing"),
                    "--install-system-packages", *extra])
                with mock.patch.object(installer.sys, "platform", "linux"), \
                        mock.patch.object(installer.os, "geteuid", return_value=1000, create=True), \
                        mock.patch.object(installer.platform, "machine", return_value="x86_64"), \
                        mock.patch.object(installer, "claim_prefix") as claim, \
                        mock.patch.object(installer, "run") as run:
                    with self.assertRaisesRegex(core.InstallError, message):
                        installer.install(args)
                claim.assert_not_called()
                run.assert_not_called()


class CaptureLogTests(unittest.TestCase):
    def test_capture_appends_both_streams_and_reports_failure_log(self):
        with tempfile.TemporaryDirectory() as scratch:
            log = Path(scratch) / "logs/environment-probe.log"
            success = subprocess.CompletedProcess(["probe"], 0, "API=2406\n", "optional warning\n")
            failure = subprocess.CompletedProcess(["probe"], 7, "partial output\n", "startup failed\n")
            with mock.patch.object(core.subprocess, "run", return_value=success):
                self.assertEqual(core.capture(["probe"], log=log), "API=2406")
            with mock.patch.object(core.subprocess, "run", return_value=failure):
                with self.assertRaises(core.InstallError) as caught:
                    core.capture(["probe"], log=log)
            self.assertIn("startup failed", str(caught.exception))
            self.assertIn(str(log), str(caught.exception))
            content = log.read_text()
            for value in ("API=2406", "optional warning", "partial output", "startup failed"):
                self.assertIn(value, content)
            self.assertEqual(content.count("=== captured stdout ==="), 2)

    def test_environment_probe_requests_persistent_log(self):
        with tempfile.TemporaryDirectory() as scratch:
            prefix = Path(scratch)
            with mock.patch.object(installer, "foam_command", return_value="API=2406") as command:
                installer.verify_environment(Path("/source/etc/bashrc"), prefix, 2)
            self.assertEqual(command.call_args.kwargs["log"], prefix / "logs/environment-probe.log")
            self.assertTrue(command.call_args.kwargs["capture_output"])
            self.assertEqual((prefix / "build-environment.txt").read_text(), "API=2406\n")


if __name__ == "__main__":
    unittest.main()
