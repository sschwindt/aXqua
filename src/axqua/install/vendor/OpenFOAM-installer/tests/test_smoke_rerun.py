"""A repeated installation in the same prefix reaches the smoke run again."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from sediment_installer import installer  # noqa: E402
from sediment_installer.core import InstallError  # noqa: E402

PASSED = "Selecting BC\nwaterLevel BC on outlet\nEnd\n"


def _case(prefix: Path) -> None:
    source = prefix / "cases/baw-interFoam"
    (source / "0/bak").mkdir(parents=True)
    (source / "0/bak/alpha.water").write_text("field\n")


def _fake_foam(log_text: str):
    def run(bashrc, prefix, argv, jobs, cwd=None, log=None, capture_output=False):
        if log is not None:
            Path(log).write_text(log_text)
        return ""
    return run


class SmokeRerunTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(jobs=1)

    def test_a_smoke_run_that_passed_earlier_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            _case(prefix)
            earlier = prefix / "runs/baw-smoke"
            earlier.mkdir(parents=True)
            (earlier / "log.smoke").write_text(PASSED)
            with mock.patch.object(installer, "foam_command",
                                   side_effect=AssertionError("ran again")), \
                    contextlib.redirect_stdout(io.StringIO()):
                installer.smoke_baw(prefix, Path("/bashrc"), self.args)
            self.assertEqual(sorted(p.name for p in (prefix / "runs").iterdir()),
                             ["baw-smoke"])

    def test_a_smoke_run_that_failed_earlier_is_kept_and_run_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            _case(prefix)
            earlier = prefix / "runs/baw-smoke"
            earlier.mkdir(parents=True)
            (earlier / "log.smoke").write_text("FOAM FATAL ERROR\n")
            with mock.patch.object(installer, "foam_command", side_effect=_fake_foam(PASSED)), \
                    contextlib.redirect_stdout(io.StringIO()):
                installer.smoke_baw(prefix, Path("/bashrc"), self.args)
            kept = prefix / "runs/baw-smoke.failed-1/log.smoke"
            self.assertEqual(kept.read_text(), "FOAM FATAL ERROR\n")
            self.assertIn("waterLevel BC on", (earlier / "log.smoke").read_text())

    def test_a_failing_smoke_run_names_its_log_and_its_last_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            _case(prefix)
            with mock.patch.object(installer, "foam_command",
                                   side_effect=_fake_foam("--> FOAM FATAL ERROR: no BC\n")), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(InstallError, "log.smoke.*\n.*no BC"):
                    installer.smoke_baw(prefix, Path("/bashrc"), self.args)


if __name__ == "__main__":
    unittest.main()
