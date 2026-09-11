"""Rebuild the calibrated case at production resolution, with a free surface.

Run AFTER ``run_Bayes_cal_openfoam.py --run`` has produced a posterior. This is
not an optional extra: the calibration campaign runs under a **rigid lid**, where
the free surface is prescribed from the 2D result rather than solved. Any
roughness error that would have shown up as a surface-slope error is therefore
absorbed into the velocity field instead - a real bias, not a caveat. The only
way to see whether the calibrated parameters still hold when the surface is free
to move is to run one two-phase (VOF) case at production resolution and look.

What it does:

1. reads the calibrated values from the campaign's ``BAL_dictionary.pkl``
   (:func:`axqua.solvers.openfoam.calibration.read_posterior`);
2. writes them into the config's OpenFOAM block, printing the prior-centre ->
   posterior change for each, and **refuses** if any value sits at the edge of
   its own prior - that is an answer lying outside the range it was allowed;
3. builds the case at ``mode: vof`` and the configured ``cell_size``.

It deliberately does **not** launch the solver: a production two-phase run is
long enough that it should be started knowingly. Run ``openfoam_run.py``
afterwards.

Run: mamba run -n axqua-env python <case>/openfoam_verify_posterior.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from axqua import setup_logging
from axqua.config import load_config
from axqua.prerun import ensure_seed
from axqua.solvers.openfoam import build_case, estimate_cells, load_hotstart, summarise
from axqua.solvers.openfoam.calibration import (CAMPAIGN_SUBDIR, apply_posterior,
                                                read_posterior)

CONFIG = Path(__file__).resolve().parent / "case-config.yml"

# "map" = the posterior's highest-density point (default; a roughness posterior is
# often skewed, and its mean can sit where the sample has little mass), or "mean".
STATISTIC = "map"

# The verification runs at the CONFIGURED production resolution and a free
# surface - that is the whole point, so these are not the campaign's coarse values.
CELL_SIZE: float | None = None      # None -> openfoam.cell_size from the config
CELL_BUDGET = 8_000_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--statistic", default=STATISTIC, choices=("map", "mean"))
    parser.add_argument("--allow-pinned", action="store_true",
                        help="adopt a value sitting at its prior bound anyway")
    args = parser.parse_args()

    cfg = load_config(CONFIG)
    campaign_dir = Path(cfg.calibration_dir) / CAMPAIGN_SUBDIR

    values = read_posterior(campaign_dir, statistic=args.statistic)
    print(f"calibrated values ({args.statistic}) from {campaign_dir}:")
    for line in apply_posterior(cfg, values,
                                bound_margin=0.0 if args.allow_pinned else 0.05):
        print(line)

    # The verification is a TWO-PHASE run: the surface has to be free to move, or
    # it answers nothing the campaign did not already assume.
    cfg.openfoam.mode = "vof"
    cfg.openfoam.cell_size_factor = None
    if CELL_SIZE is not None:
        cfg.openfoam.cell_size = CELL_SIZE

    case_dir = cfg.openfoam_case_dir
    case_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(case_dir / cfg.log_file)

    seed = ensure_seed(cfg)
    for line in seed.summary():
        print(line)
    state = load_hotstart(cfg, seed.path) if seed.ok else None

    n = estimate_cells(cfg, state=state)
    print(f"\n{cfg.openfoam.cell_size:g} m lattice x {cfg.openfoam.n_layers} layers "
          f"-> about {n:,} cells")
    if n > CELL_BUDGET:
        print(f"that is over the {CELL_BUDGET:,}-cell budget in this script. Raise "
              "CELL_BUDGET if you mean it, or coarsen openfoam.cell_size.")
        return 1

    artifacts = build_case(cfg, state=state)
    print()
    for line in summarise(artifacts):
        print(line)
    print(f"\nnext: python {Path(__file__).with_name('openfoam_run.py').name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
