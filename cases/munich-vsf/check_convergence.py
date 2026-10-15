"""Has the steady run stopped changing?

A steady TELEMAC run is marched in time until nothing moves any more, and "nothing
moves" needs a number. This script reads the result frames and reports, for each pair
of consecutive frames, how much the fields still change:

* **water depth**, **velocity** and **turbulent kinetic energy**, as the RMS change over
  the wet nodes divided by the RMS of the field itself - a global relative change;
* the **discharge** at each liquid boundary, from the solver listing, both as its own
  frame-to-frame change and as the imbalance between inflow and outflow.

Two criteria, because there are two kinds of quantity here
----------------------------------------------------------
A vertical slot fishway does not reach a steady state, and no amount of simulated time
will make it. At constant discharge the slot jets flap and the pool gyres shed vorticity,
so the flow settles into a **statistically stationary** state: a fixed *mean* with
sustained fluctuation about it. On this case the depth settles to 0.45% per frame and
stays there while the velocity field keeps changing by 5-7% per frame indefinitely -
not a transient that needs longer, a limit cycle that has no fixed point to find.

Testing such a flow frame against frame therefore asks a question with no answer. So:

**Depth and discharge** are tested instantaneously, as before. They do settle, and a
depth that is still drifting means the reach is still filling.

**Velocity and TKE** are tested as *window means*: the record's last third is split in
two, each half is averaged over time, and the two averages are compared. A stationary
flow passes this easily - its mean has stopped moving - while a flow still developing
fails it, which is exactly the distinction being asked about. The instantaneous
fluctuation is reported alongside as the **band**, so the unsteadiness is visible as a
number rather than mistaken for a failure to converge.

Why RMS rather than the worst node
----------------------------------
A per-node maximum is dominated by the wetting front, where a node going from 1 mm to
2 mm of water is a 100 % change for ever, and the run would never "converge" however
still the reach became. The RMS over wet nodes measures the thing actually being asked
about - whether the solution as a whole is still moving - and the per-node maximum is
reported alongside it rather than used as the test, so a single misbehaving node is
visible without vetoing the result.

Why 5 cm of water counts as wet
-------------------------------
``--wet`` defaults to aXqua's own ``min_depth``, not to a numerical dry threshold. On a
bed with a Nikuradse roughness of 0.05-0.5 m, water 5 mm deep stands *inside* the grain
roughness rather than flowing over it, and thousands of such nodes flickering wet and dry
at the fringe dominated the statistic: on this case the velocity change reads 6.9% at
5 mm and 3.4% at 10 cm, for the same flow. Judging what the model resolves, rather than
what it merely wets, is the point.

TKE needs the k-epsilon closure (``hydrodynamics.turbulence_model: 3``); Smagorinski
carries no K and the check says so instead of quietly passing.

    python check_convergence.py                     # the built case's r2d.slf
    python check_convergence.py --window            # with the stationary-mean test
    python check_convergence.py --tolerance 0.005   # a stricter threshold
    python check_convergence.py --result r2d-hotstart.slf --window
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
#: Judged instantaneously; the rest are judged as window means. Depth is here because a
#: drifting depth means the reach is still filling, which no averaging should hide.
INSTANTANEOUS = {"depth"}
#: a node is wet, and so counts, above this depth [m] - aXqua's own min_depth
WET = 0.05
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


def window_means(frames, label: str, wet_threshold: float):
    """Compare the time-averaged field over two consecutive windows.

    The record's last third is split in half and each half averaged **in time, node by
    node**; the two averages are then compared with the same RMS measure used between
    frames. For a statistically stationary flow the fluctuation cancels and what is left
    is the drift of the mean, which is the quantity that actually has to reach zero.

    Returns ``(relative change, band, n per window, t0, t1)`` where *band* is the mean
    instantaneous frame-to-frame change over the same span - the size of the
    fluctuation the averaging removed.
    """
    usable = [(t, f) for t, f in frames if label in f and "depth" in f]
    if len(usable) < 6:
        return None
    take = max(2, len(usable) // 3)
    tail = usable[-2 * take:]
    first, second = tail[:take], tail[take:]

    def average(block):
        stack = np.stack([f[label] for _, f in block])
        wet = np.all(np.stack([f["depth"] for _, f in block]) > wet_threshold, axis=0)
        return stack.mean(axis=0), wet

    mean_a, wet_a = average(first)
    mean_b, wet_b = average(second)
    change, _ = field_change(mean_a, mean_b, wet_a & wet_b)

    bands = []
    for (t0, a), (t1, b) in zip(tail, tail[1:]):
        wet = (a["depth"] > wet_threshold) & (b["depth"] > wet_threshold)
        rms, _ = field_change(a[label], b[label], wet)
        if rms is not None:
            bands.append(rms)
    band = float(np.mean(bands)) if bands else float("nan")
    return change, band, take, tail[0][0], tail[-1][0]


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
    for name in ("run-continue.log", "run-full.log"):
        live = folder / name
        if live.exists():
            return live
    return None


def report(result: Path, sortie: Path | None, tolerance: float, log, *,
           use_window: bool = True, wet_threshold: float = WET) -> bool:
    frames = read_frames(result)
    if len(frames) < 2:
        log.warning("only %d frame in %s - nothing to compare yet", len(frames),
                    result.name)
        return False

    log.info("%s: %d frames, t = %.1f .. %.1f s (wet = h > %.0f mm)", result.name,
             len(frames), frames[0][0], frames[-1][0], wet_threshold * 1000)
    missing = [label for _, label in FIELDS if label not in frames[-1][1]]
    if missing:
        log.warning("not in the result: %s. TKE needs the k-epsilon closure "
                    "(hydrodynamics.turbulence_model: 3); Smagorinski carries none.",
                    ", ".join(missing))

    log.info("")
    log.info("  %-9s %-8s %-9s %-9s %-9s %-9s", "t [s]", "dt [s]", "depth", "velocity",
             "tke", "worst")
    passing = {}
    for (t0, previous), (t1, current) in zip(frames, frames[1:]):
        wet = (previous.get("depth", np.zeros(1)) > wet_threshold) & \
              (current.get("depth", np.zeros(1)) > wet_threshold)
        row, worst = [], 0.0
        for _, label in FIELDS:
            if label not in current or label not in previous:
                row.append("     -   ")
                continue
            rms, node = field_change(previous[label], current[label], wet)
            passing[label] = rms
            row.append(f"{rms * 100:7.3f}% " if rms is not None else "     -   ")
            worst = max(worst, node or 0.0)
        # The interval is printed because it is not constant: GRAPHIC PRINTOUT PERIOD
        # counts time steps and the step is variable, so a short final interval makes
        # the last change look smaller than the ones before it for no physical reason.
        log.info("  %-9.1f %-8.1f %s %7.1fx", t1, t1 - t0, " ".join(row), worst)

    log.info("")
    ok = True
    for _, label in FIELDS:
        value = passing.get(label)
        if value is None:
            log.warning("  %-9s not available - NOT converged (cannot be judged)", label)
            ok = False
            continue
        instantaneous = value <= tolerance
        if label in INSTANTANEOUS or not use_window:
            log.info("  %-9s last change %6.3f%%  ->  %s (threshold %.1f%%)",
                     label, value * 100,
                     "converged" if instantaneous else "NOT converged", tolerance * 100)
            ok &= instantaneous
            continue

        window = window_means(frames, label, wet_threshold)
        if window is None:
            log.warning("  %-9s too few frames for the window test", label)
            ok = False
            continue
        change, band, take, t0, t1 = window
        if change is None:
            log.warning("  %-9s window mean could not be formed (no lasting wet nodes)",
                        label)
            ok = False
            continue
        settled = change <= tolerance
        log.info("  %-9s window mean %6.3f%%  ->  %s (threshold %.1f%%)",
                 label, change * 100,
                 "converged" if settled else "NOT converged", tolerance * 100)
        log.info("  %-9s   fluctuates %6.3f%% per frame about that mean, over "
                 "%d+%d frames spanning t = %.0f..%.0f s",
                 "", band * 100, take, take, t0, t1)
        ok &= settled

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
            if imbalance > tolerance:
                log.info("      a persistent surplus means the reach is still filling; "
                         "continue the run rather than restarting it "
                         "(python continue_run.py)")
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
    parser.add_argument("--wet", type=float, default=WET,
                        help="a node counts as wet above this depth [m] "
                             "(default %(default)s, aXqua's min_depth)")
    parser.add_argument("--no-window", dest="window", action="store_false",
                        help="judge velocity and TKE frame against frame, as if the "
                             "flow had a steady state to find")
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
            if report(result, latest_sortie(folder, stem), args.tolerance, log,
                      use_window=args.window, wet_threshold=args.wet):
                return 0
        if not args.watch:
            return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
