"""Bayesian calibration of the OpenFOAM case against measured velocity components.

Thin wrapper around :func:`axqua.solvers.openfoam.calibration.run_openfoam_calibration`
- all the logic lives in the package. Run AFTER ``preprocessing.py`` and
``initial_run.py`` have produced a converged 2D result (``r2d.slf``), which is
what seeds the 3D case.

**What this calibrates, and why it is not the 2D calibration again.** The target
is the *vertical structure* of the flow: ``U_x``, ``U_y``, ``U_z`` at each survey
vertical, which is what an ADV campaign measures and what a depth-averaged 2D
model cannot be calibrated against at all. The parameters are the bed roughness
``ks`` and, optionally, the k-epsilon closure coefficients.

**The campaign runs coarse and under a rigid lid.** About 90% of a two-phase
case's cells are air, and a surrogate needs tens of runs; ``mode: rigid-lid``
removes the air by construction and ``CELL_SIZE_FACTOR`` coarsens the plan
lattice. Two things follow, and neither is a detail:

* the free surface is **prescribed** from the 2D result rather than solved, so a
  roughness error that would have shown up as a surface-slope error is absorbed
  into the velocity field instead. Verify the posterior once at production
  resolution with ``openfoam_verify_posterior.py`` before believing it.
* ``ks`` here is **one global value**. HydroBayesCal's OpenFOAM binding has no
  per-zone roughness, so this posterior is *not* comparable with the zoned
  ``zone<N>`` posterior of a TELEMAC calibration on the same reach. Do not report
  them side by side as if they were the same quantity.

Run:
    mamba run -n axqua-env python <case>/run_Bayes_cal_openfoam.py --smoke
    mamba run -n axqua-env python <case>/run_Bayes_cal_openfoam.py --run

**Always pass --smoke first.** It is a 3-run, 30 s plumbing test that exercises
the whole chain - parameter routing into the OpenFOAM dictionaries, the solver
launch, the field extraction, the per-run accumulation - in minutes rather than
discovering a wiring fault after a night of solver time.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from axqua.config import load_config
from axqua.solvers.openfoam.calibration import run_openfoam_calibration

CONFIG = Path(__file__).resolve().parent / "case-config.yml"

# What the surrogate is trained against. U_z is the weakest of the three - a
# side-looking ADV's vertical component at 0.6*h is dominated by mounting tilt -
# so it carries a raised error floor (calibration.VERTICAL_ERROR_FLOOR). Drop it
# to ("U_x", "U_y") to calibrate on the horizontal field alone; it is still
# extracted either way, so the profiles figure can still show it.
CALIBRATION_QUANTITIES = ["U_x", "U_y", "U_z"]
EXTRACTION_QUANTITIES = ["U_x", "U_y", "U_z", "TKE"]

# None -> whatever the case declares under calibration.parameters. Naming them
# here restricts the campaign to a subset without editing the config.
PARAMETERS: list[str] | None = None

CELL_SIZE_FACTOR = 4.0     # x the 2D channel resolution (0.5 m -> 2.0 m plan)
END_TIME = 600.0           # [s] of simulated time per run
WRITE_INTERVAL = 20.0      # [s]
N_AVG_TIMESTEPS = 5        # average the last 5 writes (100 s) at each point
N_PROCESSORS: int | None = None    # None -> openfoam.n_processors

# None -> calibration.init_runs / max_runs from the config. Size these from ONE
# timed run at the settings above rather than from another reach's numbers.
INIT_RUNS: int | None = None
MAX_RUNS: int | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true",
                      help="write the template, CSV and config; launch nothing")
    mode.add_argument("--smoke", action="store_true",
                      help="3-run, 30 s plumbing test (run this first)")
    mode.add_argument("--run", action="store_true", help="launch the full campaign")
    mode.add_argument("--resume", action="store_true",
                      help="reuse a completed initial design, go straight to BAL")
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild the case template from scratch")
    parser.add_argument("--hbc-checkout", type=Path, default=None,
                        help="use drivers from a HydroBayesCal source checkout")
    args = parser.parse_args()

    launch_mode = ("smoke" if args.smoke else "run" if args.run
                   else "resume" if args.resume else "prepare")

    cfg = load_config(CONFIG)
    return run_openfoam_calibration(
        cfg,
        calibration_quantities=CALIBRATION_QUANTITIES,
        extraction_quantities=EXTRACTION_QUANTITIES,
        parameters=PARAMETERS,
        cell_size_factor=CELL_SIZE_FACTOR,
        end_time=END_TIME,
        write_interval=WRITE_INTERVAL,
        n_avg_timesteps=N_AVG_TIMESTEPS,
        n_processors=N_PROCESSORS,
        init_runs=INIT_RUNS,
        max_runs=MAX_RUNS,
        launch_mode=launch_mode,
        rebuild=args.rebuild,
        checkout=args.hbc_checkout)


if __name__ == "__main__":
    raise SystemExit(main())
