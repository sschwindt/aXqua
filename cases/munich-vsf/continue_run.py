"""Continue the steady run from where it stopped, instead of starting again.

The first 1800 s run left the reach **still filling**: storage was rising at 5.5 L/s,
4% of the 135 L/s inflow, which is exactly the flux imbalance the convergence check
reported. The filling is a clean exponential with a time constant of about 550 s, so
another 1800 s takes the residual from 4% to roughly 0.1% - and does it from the
converged field rather than from a pre-wetted guess, which is what makes a continuation
worth five hours where a restart would spend the first third of them getting back to
where this one already is.

It writes ``hotstart2d.cas`` with :func:`axqua.solvers.telemac.steering.write_hotstart_cas`
- the steady case with its initial conditions switched to ``PREVIOUS COMPUTATION FILE :
r2d.slf`` and everything else, notably the prescribed Q and downstream H, carried over
unchanged - and marches it into ``r2d-hotstart.slf``. Chaining again continues from that
one, so a run can be extended as many times as it needs.

    python continue_run.py                 # another 1800 s
    python continue_run.py --duration 900  # or less

Judge the result with the window criterion, not the per-frame one::

    python check_convergence.py --result r2d-hotstart.slf --window
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from axqua import format_flux_convergence, report_wetting, run_solver_streaming, setup_logging
from axqua.config import load_config
from axqua.env import TelemacRuntime
from axqua.flux_convergence import HOTSTART_TOLERANCE, analyze_flux_convergence
from axqua.solvers.telemac.steering import write_hotstart_cas

CONFIG = Path(__file__).resolve().parent / "case-config.yml"

#: More simulated time. 1800 s is about three fill time constants: the residual filling
#: rate falls by e^-3, from 4% of the inflow to roughly 0.1% of it.
DEFAULT_DURATION = 1800.0


def _retarget(cas: Path, discharge: float, stage: float | None) -> Path:
    """Point a continuation at a different steady discharge.

    The three design discharges share a mesh, so the converged 135 L/s field is a far
    better starting point for 60 or 1000 L/s than a dry bed is: the reach is already
    full, and the run only has to redistribute the flow rather than fill 89 m3 of pools
    at the inflow rate first. That is most of a working day saved per scenario.

    The **downstream stage is a modelling decision, not an arithmetic one** - it is the
    tailwater the scenario is defined against - so it is changed only when given, and
    keeping the 135 L/s value at another discharge is called out rather than assumed.
    """
    lines = []
    for line in cas.read_text().splitlines():
        s = line.strip()
        if s.startswith("PRESCRIBED FLOWRATES"):
            lines.append(f"PRESCRIBED FLOWRATES : {discharge:.4f};0.")
        elif s.startswith("PRESCRIBED ELEVATIONS") and stage is not None:
            lines.append(f"PRESCRIBED ELEVATIONS : 0.;{stage:.4f}")
        elif s.startswith("RESULTS FILE"):
            lines.append(f"RESULTS FILE : r2d-q{round(discharge * 1000)}.slf")
        elif s.startswith("TITLE"):
            lines.append(f"TITLE : 'munich-vsf steady {round(discharge * 1000)} L/s'")
        else:
            lines.append(line)
    out = cas.with_name(f"steady2d-q{round(discharge * 1000)}.cas")
    out.write_text("\n".join(lines) + "\n")
    if stage is None:
        print("WARNING: --discharge without --stage keeps the downstream elevation of "
              "the 135 L/s case. Check that it is the tailwater this scenario means.")
    print(f"retargeted to {discharge * 1000:.0f} L/s -> {out.name}")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION,
                        help="further simulated seconds (default: %(default)s)")
    parser.add_argument("--from-result", default=None,
                        help="continue from this result instead of the case's r2d.slf "
                             "- pass r2d-hotstart.slf to extend a continuation")
    parser.add_argument("--np", type=int, default=None, dest="ncsize",
                        help="MPI processes (default: the case's own)")
    parser.add_argument("--discharge", type=float, default=None,
                        help="continue at a different steady inflow [m3/s] - the way to "
                             "reach the 60 and 1000 L/s scenarios without paying the "
                             "filling transient again")
    parser.add_argument("--stage", type=float, default=None,
                        help="downstream prescribed elevation [m] to go with "
                             "--discharge")
    args = parser.parse_args(argv)

    cfg = load_config(CONFIG)
    if args.from_result:
        # write_hotstart_cas continues from cfg.results_slf, so point that at the
        # result being extended rather than teaching it a second spelling.
        cfg.results_slf = args.from_result
    source = cfg.model_path(cfg.results_slf)
    if not source.exists():
        print(f"nothing to continue from: {source} does not exist")
        return 1

    setup_logging(cfg.model_path(cfg.log_file))
    cas = write_hotstart_cas(cfg, duration=args.duration)
    if args.discharge is not None:
        cas = _retarget(cas, args.discharge, args.stage)
    print(f"continuing {source.name} for {args.duration:.0f} s -> "
          f"{cas.name}\nstreaming TELEMAC output:\n")

    runtime = TelemacRuntime(cfg.telemac)
    try:
        runtime.check_available()
        proc = run_solver_streaming(runtime, cfg, cas_file=cas.name,
                                    ncsize=args.ncsize, duration=args.duration)
    except Exception as exc:  # noqa: BLE001 - report cleanly
        print(f"could not run the solver: {type(exc).__name__}: {exc}")
        return 1
    if proc.returncode != 0:
        print(f"FAILED - solver returned {proc.returncode}")
        return proc.returncode

    print("\nOK. Now judge it with the window criterion:")
    print("  python check_convergence.py --result r2d-hotstart.slf --window")
    try:
        for line in format_flux_convergence(
                analyze_flux_convergence(cfg, tolerance=HOTSTART_TOLERANCE)):
            print(line)
        for line in report_wetting(cfg):
            print(line)
    except Exception as exc:  # noqa: BLE001 - the run itself succeeded
        print(f"post-run analysis skipped: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
