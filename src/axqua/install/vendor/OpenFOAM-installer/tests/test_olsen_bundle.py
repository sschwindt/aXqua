from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from sediment_installer import installer  # noqa: E402


class OlsenBundleTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((REPO / "sediment_installer/olsen_sources.json").read_text())

    def test_every_pinned_file_is_bundled_with_its_pinned_bytes(self):
        for item, digest in self.manifest["files"].items():
            path = installer.OLSEN_SOURCES / item
            self.assertTrue(path.is_file(), item)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, item)

    def test_the_sources_are_taken_from_the_bundle_without_a_download(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(installer, "checked_download",
                                  side_effect=AssertionError("downloaded")):
            prefix = Path(tmp)
            sources = installer.acquire_olsen(prefix)
            again = installer.acquire_olsen(prefix)       # already there: kept
            self.assertEqual(sources, again)
            for item in self.manifest["files"]:
                self.assertTrue((prefix / "src" / item).is_file(), item)

    def test_a_changed_file_in_the_prefix_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            installer.acquire_olsen(prefix)
            target = prefix / "src/sediDriftFoam2/sediDriftFoam2.C"
            target.write_text("changed")
            with self.assertRaises(installer.InstallError):
                installer.acquire_olsen(prefix)

    def test_a_failed_download_is_an_installer_error(self):
        error = OSError("HTTP Error 502: Bad Gateway")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(installer, "checked_download", side_effect=error):
            with self.assertRaises(installer.InstallError) as raised:
                installer.acquire_olsen(Path(tmp), bundle=Path(tmp) / "nothing")
            self.assertIn("502", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
