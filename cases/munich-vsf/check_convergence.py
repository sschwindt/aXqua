"""Has the steady run stopped changing?

A steady TELEMAC run is marched in time until nothing moves any more, and "nothing
moves" needs a number. This script reads the result frames and reports, for each pair
of consecutive frames, how much the fields still change:

* **water depth**, **velocity** and **turbulent kinetic energy**, as the RMS change over
  the wet nodes divided by the RMS of the field itself - a global relative change;
* the **discharge** at each liquid boundary, from the solver listing, both as its own
  frame-to-frame change and as the imbalance between inflow and outflow.

The verdict is against a threshold, 1 % by default, and every quantity has to pass.

Why RMS rather than the worst node
----------------------------------
A per-node maximum is dominated by the wetting front, where a node going from 1 mm to
2 mm of water is a 100 % change for ever, and the run would never "converge" however
still the reach became. The RMS over wet nodes measures the thing actually being asked
about - whether the solution as a whole is still moving - and the per-node maximum is
reported alongside it rather than used as the test, so a single misbehaving node is
visible without vetoing the result.

TKE needs the k-epsilon closure (``hydrodynamics.turbulence_model: 3``); Smagorinski
carries no K and the check says so instead of quietly passing.

    python check_convergence.py                     # the built case's r2d.slf
    python check_convergence.py --tolerance 0.005   # a stricter threshold
    python check_convergence.py --result r2d-smoke.slf --watch
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

import numpy as np

from axqua.config import load_config
from axqua.core.selafin import read_slf
from axqua.logsetup import setup_logging

HERE = Path(__file__).resolve().parent

#: fields to test, as (SELAFIN name alternatives, label)
FIELDS = (
    (("WATER DEPTH",), "depth"),
    (("VELOCITY U", "VELOCITY V"), "velocity"),
    (("TURBULENT ENERG.", "TURBULENT ENERGY"), "tke"),
)
#: a node is wet, and so counts, above this depth [m]
WET = 0.005
#: "FLUX BOUNDARY 1: 0.135 M3/S"
_FLUX = re.compile(r"FLUX\s+BOUNDARY\s+(\d+)\s*:\s*(-?[\d.Ee+-]+)")


def field_change(previous, current, wet):
    """RMS and worst-node relative change between two frames, over the wet nodes."""
    if not wet.any():
        return None, None
    a, b = previous[wet], current[wet]
    scale = float(np.sqrt(np.mean(a ** 2)))
    if scale <= 0:
        return None, None
    rms = float(np.sqrt(np.mean((b - a) ** 2)) / scale)
    with np.errstate(divide="ignore", invalid="ignore"):
        node = np.abs(b - a) / np.maximum(np.abs(a), scale * 1e-3)
    return rms, float(np.nanmax(node))


def read_frames(path: Path):
    """Every frame of a result file as (time, {field: array})."""
    head = read_slf(path, frame=0)
    out = []
    for index in range(head["n_times"]):
        frame = read_slf(path, frame=index)
        names = {n.strip().upper(): n for n in frame["values"]}
        picked = {}
        for options, label in FIELDS:
            found = [names[o] for o in options if o in names]
            if not found:
                continue
            if label == "velocity":
                picked[label] = np.hypot(*[np.asarray(frame["values"][f])
                                           for f in found[:2]])
            else:
                picked[label] = np.asarray(frame["values"][found[0]])
        out.append((frame["time"], picked))
    return out


def boundary_discharges(sortie: Path):
    """Per-printout discharge at each liquid boundary, from the solver listing."""
    series: dict[int, list[float]] = {}
    for line in sortie.read_text(errors="ignore").splitlines():
        match = _FLUX.search(line)
        if match:
            series.setdefault(int(match.group(1)), []).append(float(match.group(2)))
    return series


def latest_sortie(folder: Path, stem: str):
    """The solver listing to read the boundary fluxes from.

    While a parallel run is still going there is no ``.sortie`` yet - TELEMAC merges
    the per-processor files only at the end - but the streamed console log carries the
    same FLUX BOUNDARY lines, so the discharge can be watched live even though the
    fields cannot (the result file is merged at the end too).
    """
    listings = [p for p in sorted(folder.glob(f"{stem}*.sortie"),
                                  key=lambda p: p.stat().st_mtime)
                if "_p0" not in p.name]
    if listings:
        return listings[-1]
    live = folder / "run-full.log"
    return live if live.exists() else None


def report(result: Path, sortie: Path | None, tolerance: float, log) -> bool:
    frames = read_frames(result)
    if len(frames) < 2:
        log.warning("only %d frame in %s - nothing to compare yet", len(frames),
                    result.name)
        return False

    log.info("%s: %d frames, t = %.1f .. %.1f s", result.name, len(frames),
             frames[0][0], frames[-1][0])
    missing = [label for _, label in FIELDS if label not in frames[-1][1]]
    if missing:
        log.warning("not in the result: %s. TKE needs the k-epsilon closure "
                    "(hydrodynamics.turbulence_model: 3); Smagorinski carries none.",
                    ", ".join(missing))

    log.info("")
    log.info("  %-9s %-9s %-9s %-9s %-9s", "t [s]", "depth", "velocity", "tke", "worst")
    passing = {}
    for (t0, previous), (t1, current) in zip(frames, frames[1:]):
        wet = (previous.get("depth", np.zeros(1)) > WET) & \
              (current.get("depth", np.zeros(1)) > WET)
        row, worst = [], 0.0
        for _, label in FIELDS:
            if label not in current or label not in previous:
                row.append("     -   ")
                continue
            rms, node = field_change(previous[label], current[label], wet)
            passing[label] = rms
            row.append(f"{rms * 100:7.3f}% " if rms is not None else "     -   ")
            worst = max(worst, node or 0.0)
        log.info("  %-9.1f %s %7.1fx", t1, " ".join(row), worst)

    log.info("")
    ok = True
    for _, label in FIELDS:
        value = passing.get(label)
        if value is None:
            log.warning("  %-9s not available - NOT converged (cannot be judged)", label)
            ok = False
            continue
        verdict = "converged" if value <= tolerance else "NOT converged"
        log.info("  %-9s last change %6.3f%%  ->  %s (threshold %.1f%%)",
                 label, value * 100, verdict, tolerance * 100)
        ok &= value <= tolerance

    if sortie is not None and sortie.exists():
        series = boundary_discharges(sortie)
        for index, values in sorted(series.items()):
            if len(values) < 2:
                continue
            last, previous = values[-1], values[-2]
            change = abs(last - previous) / max(abs(last), 1e-9)
            log.info("  boundary %d  Q = %+8.4f m3/s, change %6.3f%%  ->  %s",
                     index, last, change * 100,
                     "converged" if change <= tolerance else "NOT converged")
            ok &= change <= tolerance
        if len(series) >= 2:
            inflow = abs(series[min(series)][-1])
            outflow = abs(series[max(series)][-1])
            imbalance = abs(inflow - outflow) / max(inflow, 1e-9)
            log.info("  |Qin| - |Qout| = %+.5f m3/s  (%.3f%% of the inflow)  ->  %s",
                     inflow - outflow, imbalance * 100,
                     "balanced" if imbalance <= tolerance else "NOT balanced")
            ok &= imbalance <= tolerance
    else:
        log.warning("  no solver listing found - the discharge cannot be judged")
        ok = False

    log.info("")
    log.info("VERDICT: %s", "converged" if ok else "not converged yet")
    return ok


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=HERE / "case-config.yml")
    parser.add_argument("--result", type=str, default=None,
                        help="result file inside the simulation folder (default: the "
                             "case's own r2d.slf)")
    parser.add_argument("--tolerance", type=float, default=0.01,
                        help="largest accepted change between frames (default 0.01 = 1%%)")
    parser.add_argument("--watch", action="store_true",
                        help="re-check every few minutes until it converges")
    parser.add_argument("--interval", type=float, default=300.0)
    args = parser.parse_args(argv)
    setup_logging(level=logging.INFO)
    log = logging.getLogger("axqua")

    cfg = load_config(args.config)
    folder = Path(cfg.model_dir)
    result = folder / (args.result or cfg.results_slf)
    stem = Path(args.result or cfg.cas_file).stem.replace("r2d", "steady2d")

    while True:
        if not result.exists():
            log.info("%s does not exist yet", result)
        else:
            if report(result, latest_sortie(folder, stem), args.tolerance, log):
                return 0
        if not args.watch:
            return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
