"""Measure Cd for a vertical slot, instead of assuming it.

``Q = Cd b h sqrt(2 g dh)`` is the design relation for a vertical-slot fishway, and
aXqua leans on it in two places - `rating.synthesize_outflow_rating` fabricates a
stage-discharge curve when a case has no measured one, and `measure_weir_split.py`
divides the munich discharge between the pass and the HW weir. Both currently take Cd
from the literature (0.65-0.85). Nobody has measured it for OUR geometry as OUR solver
resolves it.

The synthetic flume is the right place to do that: correct by construction, no CAD, no
survey, no calibration. Run the sweep first, one invocation per discharge::

    for q in 0.040 0.060 0.090 0.135 0.200; do
        python cases/slot-flume/slot_resistance_study.py --q=$q --duration=400 0.05
    done
    python cases/slot-flume/fit_slot_coefficient.py

**b is the 0.1697 m diagonal slot, not the 0.380 m gap to the far wall.** Opposite
every baffle stands a slot block offset downstream, so the opening the flow uses is
the diagonal between the two corners. `slot_resistance_study.py` still reports
`design.SLOT_WIDTH` in its own table; this does not.

**A point is only used if its pool stayed below the baffle crest.** The flume's
baffles stand `BAFFLE_HEIGHT` over their own bed and the 2D build adds
`structures.solid_freeboard_2d`, so the realised crest is the sum. Above that the
water is going OVER the baffle as well as through the slot, the relation no longer
describes what is happening, and Cd absorbs the error and drifts upward. Those points
are reported and excluded, not silently averaged in.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_geodata as design                                   # noqa: E402

from axqua.config import load_config                            # noqa: E402

G = 9.81
HERE = Path(__file__).resolve().parent


def records(store: Path) -> list[dict]:
    out = []
    for path in sorted(store.glob("slot-resistance-q*.json")):
        out += json.loads(path.read_text())
    return sorted(out, key=lambda r: r["discharge"])


def main() -> None:
    cfg = load_config(HERE / "case-config.yml")
    store = Path(cfg.postprocessing_dir) / "slot-resistance"
    rows = records(store)
    if not rows:
        raise SystemExit(f"no sweep results in {store} - run the sweep first")

    crest = design.BAFFLE_HEIGHT + cfg.structures.solid_freeboard_2d
    b = design.THROAT
    print(f"slot b        {b:.4f} m  (the diagonal; the gap to the far wall is "
          f"{design.SLOT_WIDTH:.3f} m)")
    print(f"baffle crest  {design.BAFFLE_HEIGHT:.2f} m + "
          f"{cfg.structures.solid_freeboard_2d:.2f} m freeboard = {crest:.2f} m "
          "over its own bed")
    print(f"design drop   {design.DESIGN_PER_POOL:.4f} m per pool\n")

    print(f"{'Q':>7} {'dx':>6} {'pool h':>8} {'drop dh':>8} {'slot U':>7} "
          f"{'Cd':>7} {'over crest?':>12}")
    used = []
    for r in rows:
        h = float(np.nanmedian(r["depths"]))
        dh = float(r["head_per_pool"])
        cd = r["discharge"] / (b * h * np.sqrt(2 * G * dh))
        over = h >= crest
        if not over:
            used.append((r["discharge"], cd, h, dh))
        print(f"{r['discharge']:7.3f} {r['dx']:6.3f} {h:8.3f} {dh:8.4f} "
              f"{r['slot_speed_mean']:7.2f} {cd:7.3f} "
              f"{'OVERTOPPED' if over else 'no':>12}")

    if len(used) < 2:
        raise SystemExit("\nfewer than two points stayed below the crest - "
                         "nothing to fit")
    q = np.array([v[0] for v in used])
    cd = np.array([v[1] for v in used])
    h = np.array([v[2] for v in used])
    print(f"\nCd over the {len(used)} points below the crest "
          f"({q.min():g}-{q.max():g} m3/s):")
    print(f"  mean {cd.mean():.3f}, sd {cd.std():.4f}, "
          f"range {cd.min():.3f}-{cd.max():.3f}")
    print(f"  literature for a vertical slot is 0.65-0.85, so this is "
          f"{0.75 / cd.mean():.1f}x more resistant")

    # Does the relation hold, or does Cd drift with submergence? Q ~ h at fixed dh,
    # so the exponent is the test that does not need Cd at all.
    exponent = float(np.polyfit(np.log(q), np.log(h), 1)[0])
    print(f"\n  pool depth ~ Q^{exponent:.2f} over these points "
          f"(the relation says Q ~ h, exponent 1.00)")
    drift = float(np.polyfit(np.log(q), cd, 1)[0])
    print(f"  Cd drifts {drift:+.3f} per e-fold of Q - "
          + ("flat enough for a single rating"
             if abs(drift) < 0.05 else "NOT flat; a single rating is wrong here"))

    out = store / "slot-coefficient.txt"
    print(f"\nb = {b:.4f} m, Cd = {cd.mean():.3f} +/- {cd.std():.4f}, "
          f"measured not assumed")
    out.write_text(
        f"slot width b (diagonal)  {b:.4f} m\n"
        f"Cd                       {cd.mean():.4f} +/- {cd.std():.4f}\n"
        f"fitted over              {q.min():g}-{q.max():g} m3/s, {len(used)} points\n"
        f"excluded (overtopped)    {len(rows) - len(used)} points\n"
        f"pool depth exponent      {exponent:.3f} (relation says 1.0)\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
