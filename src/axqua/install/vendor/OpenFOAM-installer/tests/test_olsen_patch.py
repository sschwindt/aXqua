"""Offline patch contract tests; these are not C++ compilation/physics tests.

Set OLSEN_UPSTREAM_DIR to an exact-source cache to also exercise actual files.
Synthetic anchor fixtures bypass the SHA gate only within a mocked test context.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from sediment_installer import olsen_patch


class TestPatchGuards(unittest.TestCase):
    def test_changed_source_rejected_for_every_entry_point(self):
        with self.assertRaisesRegex(ValueError, "Refusing to patch unfamiliar"):
            olsen_patch.patch_source(b"changed upstream")
        for solver in ("sediDriftFoam", "sediDriftFoam2"):
            with self.subTest(solver=solver), self.assertRaisesRegex(ValueError, "Refusing to patch unfamiliar"):
                olsen_patch.patch_baseline_source(b"changed upstream", solver)

    def test_unknown_solver_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown Olsen solver"):
            olsen_patch.patch_baseline_source(b"", "sediFoam")

    def test_anchor_requires_exactly_one_occurrence(self):
        for source in ("missing", "old old"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                olsen_patch._replace_once(source, "old", "new")
        self.assertEqual(olsen_patch._replace_once("before old after", "old", "new"), "before new after")

    def test_baseline_changes_only_loop_signature(self):
        fixture = b"prefix\nwhile (simple.loop(runTime))\nbody\n"
        fixture_hash = hashlib.sha256(fixture).hexdigest()
        with patch.object(olsen_patch, "OLSEN1_SOURCE_SHA256", fixture_hash), patch.object(
            olsen_patch, "OLSEN2_SOURCE_SHA256", fixture_hash
        ):
            for solver in ("sediDriftFoam", "sediDriftFoam2"):
                self.assertEqual(
                    olsen_patch.patch_baseline_source(fixture, solver),
                    b"prefix\nwhile (simple.loop())\nbody\n",
                )


class TestRatingTransformation(unittest.TestCase):
    # Exact original anchors, in isolation; deliberately not a compilable solver.
    anchors = [
        '#include "Time.H"',
        "while (simple.loop(runTime))",
        "    turbulence->validate();",
        "\tloopnumber += 1.0;",
        "if(fmod(loopnumber,updateinterval) < 0.0001 && loopnumber > initialIterations)",
        "\tsurflevel *= 0.25;",
        "if(loopnumber < initialIterations * variableTimeStepNumber)",
        "\tsedimentTime += timeStepCtemp;",
        "double deltaMoveBed = (Conc[cellbed] * (-fallVelocity) -  S_explicit[cellbed] * 2.0 * dist_to_wall) * timeStepCtemp * conversionCoefficient;",
        "if(bedCenter[bed].x() > maxX - 1.0)",
        "\t\tsumMoveBed += deltaMoveBed * patchBed.magSf()[bed];",
        "double deltaMoveSurf = ((p[cellsurf] -  p[refcell]) / 9.81 + surflevel - levelsurfpatch) * waterRelax;",
        "if(deltaMoveSurf + levelsurfpatch < minwaterlevel)",
        "if(newpoints[pi].x() < -0.01 && newpoints[pi].z() < 0.0)",
        "if(newpoints[pi].z() < newpoints[bedpi].z() + dist_to_wall0 * minDepthMultiplier)",
        "\tint loopSlide;",
        "for(loopSlide = 0; loopSlide < loopSlideMax; loopSlide++)",
        "\tmesh.movePoints(newpoints);",
    ]

    def test_spinup_gates_and_geometry_guards_injected(self):
        fixture = "\n".join(self.anchors).encode()
        with patch.object(olsen_patch, "OLSEN2_SOURCE_SHA256", hashlib.sha256(fixture).hexdigest()):
            result = olsen_patch.patch_source(fixture).decode()
        self.assertIn(olsen_patch.PATCH_MARKER, result)
        self.assertIn("morphActive || ratingCurve.enabled()", result)
        self.assertIn("if (morphActive) sedimentTime +=", result)
        self.assertIn("deltaMoveBed = morphActive ?", result)
        self.assertIn("morphActive && loopSlide <", result)
        self.assertIn("!ratingCurve.enabled() && newpoints[pi].z() <", result)
        self.assertLess(result.index("ratingCurve.enforceOutlet"), result.index("int loopSlide"))
        self.assertLess(result.index("ratingCurve.validateMoved"), result.index("mesh.movePoints"))
        self.assertIn("Pstream::parRun()", result)
        self.assertNotIn("simple.loop(runTime)", result)

    def test_repeated_anchor_aborts_even_with_matching_mock_hash(self):
        fixture = ("\n".join(self.anchors) + '\n#include "Time.H"').encode()
        with patch.object(olsen_patch, "OLSEN2_SOURCE_SHA256", hashlib.sha256(fixture).hexdigest()):
            with self.assertRaisesRegex(ValueError, "occurs 2 times"):
                olsen_patch.patch_source(fixture)


@unittest.skipUnless(os.environ.get("OLSEN_UPSTREAM_DIR"), "Optional exact-source cache not supplied")
class TestActualUpstream(unittest.TestCase):
    def _read(self, solver: str) -> bytes:
        root = Path(os.environ["OLSEN_UPSTREAM_DIR"])
        candidates = [root / solver / "sourceCode" / f"{solver}.C", root / solver / f"{solver}.C"]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.read_bytes()
        self.fail(f"No {solver}.C under {root}")

    def test_exact_baseline_sources(self):
        for solver in ("sediDriftFoam", "sediDriftFoam2"):
            raw = self._read(solver)
            self.assertEqual(
                olsen_patch.patch_baseline_source(raw, solver),
                raw.replace(b"while (simple.loop(runTime))", b"while (simple.loop())", 1),
            )

    def test_exact_moving_bed_transformation(self):
        out = olsen_patch.patch_source(self._read("sediDriftFoam2"))
        self.assertIn(olsen_patch.PATCH_MARKER.encode(), out)
        self.assertEqual(out.count(b"ratingCurve.enforceOutlet"), 1)
        self.assertEqual(out.count(b"mesh.movePoints"), 1)


if __name__ == "__main__":
    unittest.main()
