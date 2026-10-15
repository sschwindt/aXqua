"""Version-gated v2406 API patches and opt-in moving-bed rating extension.

The installer must retain a pristine upstream copy and write this result into its
build copy. sediDriftFoam receives only the required simpleControl API patch.
"""

from __future__ import annotations

import hashlib


OLSEN1_SOURCE_SHA256 = "acf814bb7822c46e3923287fe1d2c1c91f146be8ac9aa126827a76d2dab2a4ee"
OLSEN2_SOURCE_SHA256 = "c0e045ef3d58e106de8fc272417c7709d00f87b93f2141215a1cf0050c81cbde"
PATCH_MARKER = "// sediment-installer: opt-in serial geometric rating curve"


def _replace_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"Olsen source anchor occurs {count} times, expected once: {old[:100]!r}")
    return source.replace(old, new, 1)


def patch_source(source: bytes) -> bytes:
    """Return patched .C bytes; reject changed or already-patched upstream input."""
    digest = hashlib.sha256(source).hexdigest()
    if digest != OLSEN2_SOURCE_SHA256:
        raise ValueError(
            "Refusing to patch unfamiliar sediDriftFoam2.C. "
            f"Expected SHA256 {OLSEN2_SOURCE_SHA256}; got {digest}. "
            "Review upstream changes before updating the installer."
        )
    text = source.decode("utf-8")
    text = _replace_once(text, "while (simple.loop(runTime))", "while (simple.loop())")
    text = _replace_once(
        text,
        '#include "Time.H"',
        '#include "Time.H"\n#include "olsenRatingCurve.H"\n' + PATCH_MARKER,
    )
    text = _replace_once(
        text,
        "    turbulence->validate();",
        """    turbulence->validate();

    // The upstream topology algorithm and scour output are not MPI-safe.
    if (Pstream::parRun())
    {
        FatalErrorInFunction
            << "sediDriftFoam2 must run in serial; its point-column algorithm "
            << "has not been validated for domain decomposition." << exit(FatalError);
    }
    OlsenRatingCurve ratingCurve(mesh, runTime, phi, p);
""",
    )
    text = _replace_once(
        text,
        "\tloopnumber += 1.0;",
        "\tloopnumber += 1.0;\n        const bool morphActive = loopnumber > initialIterations;",
    )
    text = _replace_once(
        text,
        "if(fmod(loopnumber,updateinterval) < 0.0001 && loopnumber > initialIterations)",
        "if(fmod(loopnumber,updateinterval) < 0.0001 && (morphActive || ratingCurve.enabled()))",
    )
    text = _replace_once(
        text,
        "\tsurflevel *= 0.25;",
        """\tsurflevel *= 0.25;
        if (ratingCurve.enabled())
        {
            refcell = ratingCurve.referenceCell();
            surflevel = ratingCurve.stage(phi);
        }
""",
    )
    text = _replace_once(
        text,
        "if(loopnumber < initialIterations * variableTimeStepNumber)",
        "if(morphActive && loopnumber < initialIterations * variableTimeStepNumber)",
    )
    text = _replace_once(text, "\tsedimentTime += timeStepCtemp;", "\tif (morphActive) sedimentTime += timeStepCtemp;")
    text = _replace_once(
        text,
        "double deltaMoveBed = (Conc[cellbed] * (-fallVelocity) -  S_explicit[cellbed] * 2.0 * dist_to_wall) * timeStepCtemp * conversionCoefficient;",
        "double deltaMoveBed = morphActive ? (Conc[cellbed] * (-fallVelocity) -  S_explicit[cellbed] * 2.0 * dist_to_wall) * timeStepCtemp * conversionCoefficient : 0.0;",
    )
    text = _replace_once(text, "if(bedCenter[bed].x() > maxX - 1.0)", "if(morphActive && bedCenter[bed].x() > maxX - 1.0)")
    text = _replace_once(text, "\t\tsumMoveBed += deltaMoveBed * patchBed.magSf()[bed];", "\t\tif (morphActive) sumMoveBed += deltaMoveBed * patchBed.magSf()[bed];")
    text = _replace_once(
        text,
        "double deltaMoveSurf = ((p[cellsurf] -  p[refcell]) / 9.81 + surflevel - levelsurfpatch) * waterRelax;",
        "double deltaMoveSurf = ((p[cellsurf] - p[refcell]) / (ratingCurve.enabled() ? ratingCurve.gravity() : 9.81) + surflevel - levelsurfpatch) * (ratingCurve.enabled() ? ratingCurve.relaxation() : waterRelax);",
    )
    text = _replace_once(
        text,
        "if(deltaMoveSurf + levelsurfpatch < minwaterlevel)",
        "if(!ratingCurve.enabled() && deltaMoveSurf + levelsurfpatch < minwaterlevel)",
    )
    text = _replace_once(
        text,
        "if(newpoints[pi].x() < -0.01 && newpoints[pi].z() < 0.0)",
        "if(morphActive && newpoints[pi].x() < -0.01 && newpoints[pi].z() < 0.0)",
    )
    text = _replace_once(
        text,
        "if(newpoints[pi].z() < newpoints[bedpi].z() + dist_to_wall0 * minDepthMultiplier)",
        "if(!ratingCurve.enabled() && newpoints[pi].z() < newpoints[bedpi].z() + dist_to_wall0 * minDepthMultiplier)",
    )
    text = _replace_once(
        text,
        "\tint loopSlide;",
        """        // Smoothing must not move the downstream target away from its rating.
        if (ratingCurve.enabled()) ratingCurve.enforceOutlet(newpoints, points, surflevel);

\tint loopSlide;""",
    )
    text = _replace_once(
        text,
        "for(loopSlide = 0; loopSlide < loopSlideMax; loopSlide++)",
        "for(loopSlide = 0; morphActive && loopSlide < loopSlideMax; loopSlide++)",
    )
    text = _replace_once(
        text,
        "\tmesh.movePoints(newpoints);",
        "\tif (ratingCurve.enabled()) ratingCurve.validateMoved(newpoints);\n\tmesh.movePoints(newpoints);",
    )
    return text.encode("utf-8")


def patch_olsen2_source(source: bytes) -> bytes:
    """Descriptive alias for installer integrations."""
    return patch_source(source)


def patch_olsen1_source(source: bytes) -> bytes:
    """Apply only the v2406 simpleControl loop API fix to the fixed-mesh solver."""
    return patch_baseline_source(source, "sediDriftFoam")


def patch_baseline_source(source: bytes, solver: str) -> bytes:
    """Apply ONLY v2406's simpleControl API fix, preserving the upstream models."""
    expected_hashes = {
        "sediDriftFoam": OLSEN1_SOURCE_SHA256,
        "sediDriftFoam2": OLSEN2_SOURCE_SHA256,
    }
    if solver not in expected_hashes:
        raise ValueError(f"Unknown Olsen solver {solver!r}; expected sediDriftFoam or sediDriftFoam2")
    digest = hashlib.sha256(source).hexdigest()
    expected = expected_hashes[solver]
    if digest != expected:
        raise ValueError(
            f"Refusing to patch unfamiliar {solver}.C. "
            f"Expected SHA256 {expected}; got {digest}."
        )
    text = source.decode("utf-8")
    return _replace_once(text, "while (simple.loop(runTime))", "while (simple.loop())").encode("utf-8")
