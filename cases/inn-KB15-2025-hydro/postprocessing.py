"""Render figures from this case's solver results (workflow: after any run).

Thin wrapper around :func:`axqua.postproc.render.render`. VisIt does the
sampling and rendering; aXqua decides what to draw and writes the script.

Scenes:

``free-surface``
    the water surface coloured by speed. Under ``mode: rigid-lid`` there is no
    interface to contour, so it falls back to the lid patch and **says on the
    figure that the surface was prescribed by the 2D seed** rather than solved.
``velocity-plan``
    the plan-view velocity field, sampled at the same relative depth a
    FlowTracker measures at, so the figure and the calibration look at the same
    thing.
``profiles``
    modelled vertical velocity profiles against the measured verticals, with
    error bars, plus a residual CSV. Run it once with the prior-centre parameters
    and once with the calibrated ones - those two panels are the calibration
    result.

``--script-only`` writes the VisIt scripts without running anything, which is how
to check the output on a machine with no VisIt installed. The generated scripts
are kept under ``<postprocessing_dir>/visit/`` as the record of each figure.

Run: mamba run -n axqua-env python <case>/postprocessing.py [--list]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from axqua import setup_logging
from axqua.config import load_config
from axqua.postproc import render as render_mod

CONFIG = Path(__file__).resolve().parent / "case-config.yml"

# None -> postproc.scenes from case-config.yml
SCENES: list[str] | None = None
SOLVER: str | None = None       # "openfoam" | "telemac" | None for both


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true",
                        help="report which scenes are available, and why not")
    parser.add_argument("--script-only", action="store_true",
                        help="write the VisIt scripts but run nothing")
    args = parser.parse_args()

    cfg = load_config(CONFIG)
    setup_logging(Path(cfg.postprocessing_dir) / cfg.log_file)
    names = SCENES if SCENES is not None else (cfg.postproc.scenes or None)

    if args.list:
        for line in render_mod.plan_lines(cfg, names=names, solver=SOLVER):
            print(line)
        return 0
    return render_mod.render(cfg, names=names, solver=SOLVER,
                             script_only=args.script_only)


if __name__ == "__main__":
    raise SystemExit(main())
