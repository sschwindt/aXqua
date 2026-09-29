"""C_Q for a DROWNED vertical slot, as a function of how drowned it is.

`fit_slot_coefficient.py` measures C_Q = 0.496 for a **free** slot. munich-vsf does not
run free over its whole length: on lww-133's settled 2D result pools 2 to 9 step at the
design rate but 10 to 14 back up as the tailwater rises into them -

    pool 2-9   +0.1251 m mean      pool 12  +0.062
    pool 10    +0.102              pool 13  +0.039
    pool 11    +0.094              pool 14  +0.030

- while the depth grows 0.26 to 0.62 m. Roughly the lower third is submerged and we
have no coefficient for it.

**Overtopping is not drowning, and the distinction decides the experiment.** The flume
reached a drowned-looking state by accident at dx 0.05, Q 0.135 and 0.200, but that was
the pool rising over the 1.50 m baffle crest and sending flow OVER the baffle - a
different mechanism with a different area. A slot is drowned when the pool BELOW it is
deep enough to submerge it, which is a tailwater condition, so this sweep holds the
discharge and raises the downstream level.

That means giving the flume a prescribed outflow elevation. Its own config explains why
it normally has a free one:

    prescribing one would put the answer into the measurement

True for the free-slot coefficient, and false here: in the submerged regime the
tailwater IS the independent variable. The free-flow reference, measured on
`dx25mm-q135`, is a water surface elevation of 1.949 m at the last baffle dropping
supercritically to 1.086 m at the exit - so a prescribed level below about 1.95 m
changes nothing and the sweep starts above it.

**The result is not a number.** It is C_Q against submergence, because that is what the
regime is: a single coefficient is what the free case has and the drowned case does not.

    python cases/slot-flume/drowned_slot_sweep.py            # the whole sweep
    python cases/slot-flume/drowned_slot_sweep.py --report   # re-read finished runs
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_geodata as design                                   # noqa: E402
import slot_resistance_study as study                           # noqa: E402

from axqua import pipeline                                      # noqa: E402
from axqua.config import load_config                            # noqa: E402
from axqua.env import TelemacRuntime                            # noqa: E402
from axqua.workflow import run_solver_streaming                 # noqa: E402

G = 9.81
DX = 0.025               #: the level fit_slot_coefficient.py trusts
Q = 0.135                #: design discharge, held fixed; tailwater is the variable
DURATION = 900.0         #: as the free sweep, and for the same measured reason

#: [m] prescribed outflow water surface elevation. The free run sits at 1.949 m at the
#: last baffle, so 2.05 drowns roughly the last pool and 2.65 roughly the last five -
#: which is the span lww-133 measures on munich.
LEVELS = (2.05, 2.20, 2.35, 2.50, 2.65)


def run_dir(cfg, level):
    return (Path(cfg.postprocessing_dir) / "drowned-slot"
            / f"dx{1000 * DX:.0f}mm-tw{1000 * level:.0f}")


def build_and_run(base, level, *, run=True):
    cfg = copy.deepcopy(base)
    cfg.mesh.size_scale = DX / base.mesh.default_size
    cfg.boundaries.prescribed_flowrate = Q
    cfg.boundaries.outflow_condition = "elevation"
    cfg.boundaries.prescribed_elevation = float(level)
    cfg.hydrodynamics.duration = DURATION
    target = run_dir(base, level)
    cfg.model_dir = target
    cfg.preprocessing_dir = target / "preprocessing"
    cfg.postprocessing_dir = target / "postprocessing"
    cfg.calibration_dir = target / "calibration"
    cfg.ensure_dirs()
    pipeline.run(cfg, validate_env=False, log_to_file=False)
    if run:
        proc = run_solver_streaming(TelemacRuntime(cfg.telemac), cfg)
        if proc.returncode != 0:
            raise RuntimeError(f"telemac2d exited {proc.returncode} at tw={level}")
    return target


def per_slot(base, level):
    """C_Q and submergence at every slot of one run.

    Per slot rather than per run: the whole point is that submergence varies ALONG the
    pass, so one run carries fourteen points on the curve rather than one.
    """
    cfg = copy.deepcopy(base)
    target = run_dir(base, level)
    cfg.model_dir = target
    levels = study.pool_levels(target, cfg)
    wse = np.array(levels["levels"], dtype=float)
    depth = np.array(levels["depths"], dtype=float)
    out = []
    for i in range(design.N_BAFFLES - 1):
        up, down = wse[i], wse[i + 1]
        dh = up - down
        h = depth[i]
        invert = design.bed_z(design.baffle_x(i))
        if not np.isfinite(dh) or dh <= 1e-4 or not np.isfinite(h) or h <= 0:
            continue
        # Submergence: how much of the upstream head over the slot invert is still
        # there on the downstream side. 0 = free overfall, 1 = no drop at all.
        s = (down - invert) / max(up - invert, 1e-9)
        out.append({"slot": i + 1, "wse_up": float(up), "wse_down": float(down),
                    "dh": float(dh), "depth": float(h), "submergence": float(s),
                    "c_q": float(Q / (design.SLOT * h * np.sqrt(2 * G * dh)))})
    return out


def main() -> None:
    base = load_config(HERE / "case-config.yml")
    base.ensure_dirs()
    store = Path(base.postprocessing_dir) / "drowned-slot"
    store.mkdir(parents=True, exist_ok=True)

    only_report = "--report" in sys.argv
    records = {}
    for level in LEVELS:
        if not only_report:
            print(f"\n=== tailwater {level:g} m, Q {Q:g} m3/s, dx {DX:g} m ===")
            build_and_run(base, level)
        try:
            rows = per_slot(base, level)
        except Exception as exc:                          # noqa: BLE001
            print(f"  tw={level}: could not measure ({type(exc).__name__}: {exc})")
            continue
        records[level] = rows

    if not records:
        raise SystemExit("no runs to report")

    print(f"\n{'tw':>6} {'slot':>5} {'wse up':>8} {'wse dn':>8} {'dh':>7} "
          f"{'depth':>7} {'submerg':>8} {'C_Q':>7}")
    allrows = []
    for level, rows in sorted(records.items()):
        for r in rows:
            allrows.append((level, r))
            print(f"{level:6.2f} {r['slot']:5d} {r['wse_up']:8.3f} "
                  f"{r['wse_down']:8.3f} {r['dh']:7.4f} {r['depth']:7.3f} "
                  f"{r['submergence']:8.3f} {r['c_q']:7.3f}")

    s = np.array([r["submergence"] for _, r in allrows])
    c = np.array([r["c_q"] for _, r in allrows])
    print(f"\n{len(allrows)} slot-points over {len(records)} runs")
    print(f"  submergence {s.min():.3f}..{s.max():.3f}, C_Q {c.min():.3f}..{c.max():.3f}")
    for lo, hi in ((0.0, 0.6), (0.6, 0.75), (0.75, 0.85), (0.85, 0.95), (0.95, 1.01)):
        m = (s >= lo) & (s < hi)
        if m.sum() >= 3:
            print(f"  submergence {lo:.2f}-{hi:.2f}: C_Q {c[m].mean():.3f} "
                  f"+/- {c[m].std():.3f}  (n={m.sum()})")
    print("\nfree-slot reference from fit_slot_coefficient.py: C_Q 0.496 +/- 0.011")
    print("As with that one, this is at dx 0.025 and the mesh series has not "
          "plateaued,\nso treat it as the same kind of lower bound and compare only "
          "against itself.")

    (store / "drowned-slot.json").write_text(
        json.dumps({str(k): v for k, v in records.items()}, indent=2) + "\n")
    print(f"wrote {store / 'drowned-slot.json'}")


if __name__ == "__main__":
    main()
