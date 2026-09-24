"""Measure C_Q for a vertical slot, instead of assuming it.

**C_Q, not Cd.** This is the slot's DISCHARGE coefficient. In fluid mechanics Cd
conventionally means the drag coefficient, and this repository carries velocity and
force work where that collision is live, so the discharge coefficient is spelled C_Q
throughout - here and in `measure_weir_split.py`. The weir's Poleni coefficient keeps
its own conventional symbol, mu.

``Q = C_Q b h sqrt(2 g dh)`` is the design relation for a vertical-slot fishway, and
aXqua leans on it in two places - `rating.synthesize_outflow_rating` fabricates a
stage-discharge curve when a case has no measured one, and `measure_weir_split.py`
divides the munich discharge between the pass and the HW weir. Both currently take C_Q
from the literature (0.65-0.85). Nobody has measured it for OUR geometry as OUR solver
resolves it.

The synthetic flume is the right place to do that: correct by construction, no CAD, no
survey, no calibration. Run the sweep first, one invocation per discharge::

    for q in 0.040 0.060 0.090 0.135 0.200; do
        python cases/slot-flume/slot_resistance_study.py --q=$q --duration=900 0.025
    done
    python cases/slot-flume/fit_slot_coefficient.py

dx 0.025 and 900 s, not something cheaper: at dx 0.05 the pools are deep enough to
overtop the baffles at the top two discharges, and at 400 s the low ones have not
finished filling. Both are measured, both are gated below, and both cost a retraction
to learn.

**Mesh-independence is NOT established and the answer depends on it.** At Q = 0.060
the pool goes 1.555 -> 0.837 -> 0.448 m over dx 0.10 -> 0.05 -> 0.025, still falling
by 46% on the last halving, so C_Q is still rising with refinement. The number below
is therefore a LOWER bound on the coefficient this geometry would show on a converged
mesh, not the converged value. Settling that needs dx 0.0125, which is four times the
cells of the most expensive run here.

**b is the 0.1697 m diagonal slot, not the 0.380 m gap to the far wall.** Opposite
every baffle stands a slot block offset downstream, so the opening the flow uses is
the diagonal between the two corners. `slot_resistance_study.py` still reports
`design.SLOT_WIDTH` in its own table; this does not.

A point is used only if it passes **both** gates, and the first one is here because
skipping it produced a confident wrong answer:

**1. The run must have finished filling.** A flume still filling has shallow pools, and
C_Q goes like 1/h, so an unconverged run reads HIGH. Worse, a set of runs all stopped at
the same wall-clock are all at a similar fraction-filled state, so their C_Q values agree
with each other beautifully and the agreement means nothing. That happened here: five
runs at `--duration=400` gave C_Q = 0.242 with a standard deviation of 0.6%, and three of
those five were passing barely 80% of their own inflow at the final step. The witness is
the **domain volume**, as `slot_resistance_study.balance` argues - outflow oscillates on
this Neumann boundary and the instantaneous imbalance is unreliable, but volume is not
moved by oscillation. Measured over the last quarter of the run, not the last 40
printouts, which on a short run reaches back into the transient.

**2. The pool must have stayed below the baffle crest.** The flume's baffles stand
`BAFFLE_HEIGHT` over their own bed and the 2D build adds
`structures.solid_freeboard_2d`, so the realised crest is the sum. Above it the water
is going OVER the baffle as well as through the slot, the relation no longer describes
what is happening, and C_Q absorbs the error.

Points failing either gate are printed with the reason, never averaged in.
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


#: Volume may drift this much of itself per 900 s and still count as filled.
DRIFT_TOL = 0.02
#: ...and the outflow must match the inflow this closely over the same window.
FLUX_TOL = 0.05


def records(store: Path) -> list[dict]:
    out = []
    for path in sorted(store.glob("slot-resistance-q*.json")):
        out += json.loads(path.read_text())
    return sorted(out, key=lambda r: (r["discharge"], -r["dx"]))


def filled(store: Path, cfg, size: float, discharge: float) -> tuple[bool, str]:
    """Has this run stopped filling? Judged on the LAST QUARTER of its own series.

    The stored `volume_drift_per_900s` fits the last 40 printouts, which on a 400 s
    run reaches back into the transient and condemns runs that had in fact settled.
    """
    from axqua.solvers.telemac import sortie

    name = f"dx{1000 * size:.0f}mm-q{1000 * discharge:.0f}"
    listing = sortie.latest_sortie(store / name, cfg.cas_file)
    if listing is None:
        return False, "no listing"
    data = sortie.read_sortie(listing)
    if data.time.size < 8:
        return False, "too few printouts"
    n = max(4, data.time.size // 4)
    t, vol = data.time[-n:], data.volume[-n:]
    drift = float(np.polyfit(t, vol, 1)[0]) * 900.0
    rel = abs(drift) / max(float(vol.mean()), 1e-9)
    q_in = float(data.gross_in[-n:].mean())
    q_out = float(data.gross_out[-n:].mean())
    flux = abs(q_out - q_in) / max(abs(q_in), 1e-9)
    if rel > DRIFT_TOL:
        return False, f"still filling: volume {rel:+.0%}/900s"
    if flux > FLUX_TOL:
        return False, f"flux short: out/in {q_out / q_in:.2f}"
    return True, f"volume {rel:+.1%}/900s, out/in {q_out / q_in:.3f}"


def main() -> None:
    cfg = load_config(HERE / "case-config.yml")
    store = Path(cfg.postprocessing_dir) / "slot-resistance"
    rows = records(store)
    if not rows:
        raise SystemExit(f"no sweep results in {store} - run the sweep first")

    crest = design.BAFFLE_HEIGHT + cfg.structures.solid_freeboard_2d
    b = design.SLOT
    print(f"slot b        {b:.4f} m  (the diagonal; the gap to the far wall is "
          f"{design.SLOT_WIDTH:.3f} m)")
    print(f"baffle crest  {design.BAFFLE_HEIGHT:.2f} m + "
          f"{cfg.structures.solid_freeboard_2d:.2f} m freeboard = {crest:.2f} m "
          "over its own bed")
    print(f"design drop   {design.DESIGN_PER_POOL:.4f} m per pool\n")

    print(f"{'Q':>7} {'dx':>6} {'pool h':>8} {'drop dh':>8} {'C_Q':>7}  verdict")
    used = []
    for r in rows:
        h = float(np.nanmedian(r["depths"]))
        dh = float(r["head_per_pool"])
        c_q = r["discharge"] / (b * h * np.sqrt(2 * G * dh))
        ok, why = filled(store, cfg, r["dx"], r["discharge"])
        if ok and h >= crest:
            ok, why = False, f"OVERTOPPED: pool {h:.3f} >= crest {crest:.2f}"
        if ok:
            used.append((r["discharge"], c_q, h, dh, r["dx"]))
        print(f"{r['discharge']:7.3f} {r['dx']:6.3f} {h:8.3f} {dh:8.4f} {c_q:7.3f}  "
              f"{'USED, ' + why if ok else 'rejected - ' + why}")

    if len(used) < 2:
        print(f"\n{len(used)} of {len(rows)} runs are usable - not enough to fit a "
              "coefficient.")
        if used:
            q, c_q, h, dh, dx = used[0]
            print(f"The one that is: Q = {q:g} m3/s at dx = {dx:g} m, "
                  f"pool {h:.3f} m, C_Q = {c_q:.3f}.")
        print("A sweep needs several converged discharges at ONE mesh size. Re-run "
              "the\nsweep at the configured duration rather than a shortened one.")
        raise SystemExit(1)
    if len({v[4] for v in used}) > 1:
        print("\nWARNING: the usable points are not all at the same mesh size, so "
              "they\nare not a discharge sweep - mesh size moves C_Q here by more "
              "than discharge does.")
    q = np.array([v[0] for v in used])
    c_q = np.array([v[1] for v in used])
    h = np.array([v[2] for v in used])
    print(f"\nC_Q over the {len(used)} points below the crest "
          f"({q.min():g}-{q.max():g} m3/s):")
    print(f"  mean {c_q.mean():.3f}, sd {c_q.std():.4f}, "
          f"range {c_q.min():.3f}-{c_q.max():.3f}")
    print(f"  literature for a vertical slot is 0.65-0.85, so this is "
          f"{0.75 / c_q.mean():.1f}x more resistant")
    print("  and it is a LOWER BOUND: the mesh series has not plateaued, so a finer "
          "mesh\n  would open the slot further and raise this. See the module "
          "docstring.")

    # Does the relation hold, or does C_Q drift with submergence? Q ~ h at fixed dh,
    # so the exponent is the test that does not need C_Q at all.
    exponent = float(np.polyfit(np.log(q), np.log(h), 1)[0])
    print(f"\n  pool depth ~ Q^{exponent:.2f} over these points "
          f"(the relation says Q ~ h, exponent 1.00)")
    drift = float(np.polyfit(np.log(q), c_q, 1)[0])
    print(f"  C_Q drifts {drift:+.3f} per e-fold of Q - "
          + ("flat enough for a single rating"
             if abs(drift) < 0.05 else "NOT flat; a single rating is wrong here"))

    out = store / "slot-coefficient.txt"
    print(f"\nb = {b:.4f} m, C_Q = {c_q.mean():.3f} +/- {c_q.std():.4f}, "
          f"measured not assumed")
    out.write_text(
        f"slot width b (diagonal)  {b:.4f} m\n"
        f"C_Q                       {c_q.mean():.4f} +/- {c_q.std():.4f}\n"
        f"fitted over              {q.min():g}-{q.max():g} m3/s, {len(used)} points\n"
        f"excluded (overtopped)    {len(rows) - len(used)} points\n"
        f"pool depth exponent      {exponent:.3f} (relation says 1.0)\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
