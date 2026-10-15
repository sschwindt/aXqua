"""Pure/offline tests; no actual downloads or application installations."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from sediment_installer.visualization import (  # noqa: E402
    VISIT_INSTALLER_SHA256, install_visit, platform_tag, visit_manifest,
)

spec = importlib.util.spec_from_file_location("postprocess", REPO / "postprocess.py")
postprocess = importlib.util.module_from_spec(spec)
spec.loader.exec_module(postprocess)


class PlatformTests(unittest.TestCase):
    def test_matching_bases(self):
        self.assertEqual(platform_tag({"ID": "debian", "VERSION_ID": "12"}), "debian12")
        self.assertEqual(platform_tag({"ID": "ubuntu", "VERSION_ID": "22.04"}), "ubuntu22")
        self.assertEqual(platform_tag({"ID": "linuxmint", "UBUNTU_CODENAME": "noble"}), "ubuntu24")

    def test_no_debian_lineage_guess(self):
        for release in ({"ID": "ubuntu", "VERSION_ID": "20.04", "ID_LIKE": "debian"},
                        {"ID": "unknown", "ID_LIKE": "debian"},
                        {"ID": "debian", "VERSION_ID": "13"}):
            with self.assertRaises(ValueError):
                platform_tag(release)

    def test_exact_manifest(self):
        self.assertEqual(len(VISIT_INSTALLER_SHA256), 64)
        self.assertEqual(visit_manifest("debian12")["archive"],
                         "visit3_5_0.linux-x86_64-debian12.tar.gz")


class InstallTests(unittest.TestCase):
    def test_install_once_and_preserve(self):
        with tempfile.TemporaryDirectory() as task_dir:
            prefix = Path(task_dir)
            downloads, runs = [], []

            def download(url, sha, path):
                downloads.append((url, sha, path))
                path.write_text("mock", encoding="utf-8")
                return path

            def run(argv, cwd=None, log=None, env=None):
                runs.append((argv, cwd))
                self.assertNotEqual(cwd, prefix / "cache")
                self.assertEqual(cwd.parent, prefix / "apps")
                self.assertTrue(cwd.name.startswith(".visit-work-"))
                self.assertTrue((cwd / "visit-install3_5_0").is_file())
                self.assertTrue((cwd / "visit3_5_0.linux-x86_64-debian12.tar.gz").is_file())
                (cwd / "distribution").mkdir()
                launcher = Path(argv[-1]) / "bin" / "visit"
                launcher.parent.mkdir(parents=True)
                launcher.write_text("#!/bin/sh\n", encoding="utf-8")
                launcher.chmod(0o755)

            launcher = install_visit(prefix, "debian12", download, run)
            self.assertTrue(launcher.is_file())
            self.assertEqual(len(downloads), 2)
            self.assertEqual(runs[0][0][2:5], ["-c", "none", "3.5.0"])
            self.assertEqual(runs[0][0][5], "linux-x86_64-debian12")
            self.assertFalse(runs[0][1].exists())
            self.assertTrue((prefix / "cache" / "visit-install3_5_0").is_file())
            self.assertEqual(install_visit(prefix, "debian12", download, run), launcher)
            self.assertEqual(len(runs), 1)

    def test_unowned_directory_not_touched(self):
        with tempfile.TemporaryDirectory() as task_dir:
            prefix = Path(task_dir)
            destination = prefix / "apps" / "visit-3.5.0-debian12"
            destination.mkdir(parents=True)
            sentinel = destination / "user.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unowned"):
                install_visit(prefix, "debian12", lambda *a: self.fail("download"),
                              lambda *a, **k: self.fail("run"))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_failed_install_cleans_only_owned_workdir(self):
        with tempfile.TemporaryDirectory() as task_dir:
            prefix = Path(task_dir)
            cache = prefix / "cache"
            cache.mkdir()
            sentinel = cache / "distribution" / "user.txt"
            sentinel.parent.mkdir()
            sentinel.write_text("keep", encoding="utf-8")
            workdirs = []

            def download(url, sha, path):
                path.write_text("mock", encoding="utf-8")
                return path

            def run(argv, cwd=None, log=None, env=None):
                workdirs.append(cwd)
                (cwd / "distribution").mkdir()
                raise RuntimeError("mock installer failure")

            with self.assertRaisesRegex(RuntimeError, "mock installer failure"):
                install_visit(prefix, "debian12", download, run)
            self.assertFalse(workdirs[0].exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertTrue((prefix / "apps" / "visit-3.5.0-debian12" /
                             ".sediment-installer-visit.json").is_file())
            with self.assertRaisesRegex(RuntimeError, "Incomplete"):
                install_visit(prefix, "debian12", download, run)


class ManifestTests(unittest.TestCase):
    def test_physical_times_sorted_not_indices(self):
        with tempfile.TemporaryDirectory() as task_dir:
            root = Path(task_dir)
            for name in ("case_2.vtk", "case_10.vtk"):
                (root / name).write_text("fixture", encoding="utf-8")
            series = root / "case.vtk.series"
            series.write_text(json.dumps({"file-series-version": "1.0", "files": [
                {"name": "case_2.vtk", "time": 8.25},
                {"name": "case_10.vtk", "time": 0.125},
            ]}), encoding="utf-8")
            output = postprocess.manifest_from_series(series)
            self.assertEqual(output.name, "case.visit")
            self.assertEqual(output.read_text(encoding="utf-8"),
                             "!NBLOCKS 1\n!TIME 0.125\ncase_10.vtk\n!TIME 8.25\ncase_2.vtk\n")

    def test_paths_cannot_escape(self):
        with tempfile.TemporaryDirectory() as task_dir:
            root = Path(task_dir)
            series = root / "case.vtk.series"
            series.write_text(json.dumps({"file-series-version": "1.0", "files": [
                {"name": "../outside.vtk", "time": 0},
            ]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside"):
                postprocess.manifest_from_series(series)

    def test_explicit_vtk_time_fallback(self):
        with tempfile.TemporaryDirectory() as task_dir:
            root = Path(task_dir)
            vtk = root / "bedWall_20.vtk"
            vtk.write_text("# vtk DataFile Version 3.0\nmock\nASCII\n"
                           "DATASET POLYDATA\nFIELD FieldData 1\n"
                           "TimeValue 1 1 double\n0.35\n", encoding="ascii")
            manifests = postprocess.make_manifests(root)
            self.assertEqual(len(manifests), 1)
            self.assertIn("!TIME 0.35\n", manifests[0].read_text(encoding="utf-8"))

    def test_never_invent_time(self):
        with tempfile.TemporaryDirectory() as task_dir:
            vtk = Path(task_dir) / "case_123.vtk"
            vtk.write_text("# vtk DataFile Version 3.0\nmock\nASCII\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "No explicit"):
                postprocess.time_from_ascii_vtk(vtk)

    def test_foreign_manifest_preserved(self):
        with tempfile.TemporaryDirectory() as task_dir:
            root = Path(task_dir)
            vtk = root / "case_0.vtk"
            vtk.write_text("mock", encoding="utf-8")
            output = root / "case.visit"
            output.write_text("custom", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs"):
                postprocess._write_manifest(output, [(postprocess.Decimal(0), vtk)])
            self.assertEqual(output.read_text(encoding="utf-8"), "custom")


if __name__ == "__main__":
    unittest.main()
