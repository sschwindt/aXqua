"""How the design discharge divides between the fish pass and the HW weir.

**There is no design report in this case.** `user-sources/` holds the contractor DXF,
Federica Scolari's 69-slide presentation and the flume spreadsheets, and none of them
states a discharge anywhere. So the split cannot be quoted; it can only be computed,
and this computes it from the two structures' own geometry.

What the drawing does say is what they ARE. Its labels are `Schlitzpass` (vertical
slot pass), `HW-Wehr` (high-water weir) and `HW-Ableitung` (high-water diversion), so
the weir and the parallel channel are the flood path and the pass is the low-flow one.
That alone does not settle the split, because a high-water weir still has a crest, and
this one's crest turns out to sit only 0.24 m above the pass's first pool.

The two controls:

* **the weir**, a flat sill measured off the reference model's own `Substratum_weir`
  patch. Broad-crested (Poleni): ``Q = 2/3 mu b sqrt(2g) h**1.5``.
* **the pass**, whose slot, drop per pool and first invert are measured off the CAD
  by `measure_baffle_stations.py`. The standard vertical-slot relation:
  ``Q = C_Q b_slot h sqrt(2 g dh)``, with the measured 0.1697 m slot and the
  measured 0.1297 m drop per pool.

Both are first-order: free overfall, no approach velocity, no submergence, and one
coefficient each. The sensitivity sweep at the end is there because those coefficients
are the whole uncertainty, and it is wide enough to matter to the conclusion.

    python cases/munich-vsf/measure_weir_split.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REFERENCE = HERE / "user-sources" / "reference" / "federica-wetted-bed.csv"
STATIONS = HERE / "user-sources" / "geodata" / "baffle-stations.csv"

G = 9.81
DESIGN_Q = 0.135                 #: [m3/s] MQ, `boundaries.prescribed_flowrate`
WALL_TOP = 2.845                 #: [m] local, the top of the concrete
MU = 0.60                        #: Poleni discharge coefficient, broad-crested
#: Vertical-slot DISCHARGE coefficient. Named C_Q, not Cd: in fluid mechanics Cd is
#: the drag coefficient, and these scripts sit next to velocity and force work where
#: that collision is live.
C_Q_DEFAULT = 0.85
CELL = 0.25                      #: the reference grid step
#: [m] "4.13 m (weir width)" on the dimension slide of
#: `user-sources/geodata/fishpass-dimensions-ssc.fodp`. Kept as a cross-check only:
#: the crest is measured here, not taken from the slide.
DRAWN_WIDTH = 4.13


def weir_geometry():
    """The HW weir's crest elevation and its width across the flow.

    Read from the reference model's `Substratum_weir` patch rather than from the CAD:
    that patch is exactly the face the reference meshed as the weir, so it carries no
    judgement of ours about which triangles belong to it.
    """
    import pandas as pd

    ref = pd.read_csv(REFERENCE)
    w = ref[ref.patch == "Substratum_weir"]
    if w.empty:
        raise SystemExit("no Substratum_weir patch in the reference table")
    # The sill is flat and dominates the patch; the rest is its approach ramp.
    z = np.round(w.bed_z.to_numpy(), 2)
    crest = float(np.bincount((z * 100).astype(int)).argmax()) / 100
    sill = w[np.isclose(w.bed_z, crest, atol=0.005)]
    # Width across the flow: the pass runs 15.54 deg off +y, and the weir is set
    # square to it, so the crest line is the n-extent of the sill.
    bearing = np.radians(15.543955)
    across = np.array([np.cos(bearing), -np.sin(bearing)])
    n = sill[["x", "y"]].to_numpy() @ across
    width = float(n.max() - n.min()) + CELL
    return crest, width, len(sill), float(len(w)) * CELL * CELL


def pass_geometry():
    """Slot, drop per pool and head-pool invert, from the CAD."""
    import pandas as pd

    if not STATIONS.is_file():
        raise SystemExit(f"{STATIONS.name} not built - run "
                         "measure_baffle_stations.py first")
    s = pd.read_csv(STATIONS)
    slot = float(s.slot_m.median())
    drop = float(np.median(-np.diff(s.invert_z.to_numpy())))
    invert = float(s.invert_z.max())
    return slot, drop, invert, len(s)


def q_weir(level, crest, width, mu=MU):
    h = np.maximum(level - crest, 0.0)
    return (2 / 3) * mu * width * np.sqrt(2 * G) * h ** 1.5


def q_pass(level, invert, slot, drop, c_q=C_Q_DEFAULT):
    h = np.maximum(level - invert, 0.0)
    return c_q * slot * h * np.sqrt(2 * G * drop)


def solve_level(total, crest, width, invert, slot, drop, mu=MU, c_q=C_Q_DEFAULT):
    """The upstream level at which the two structures together pass *total*."""
    lo, hi = invert, invert + 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        q = q_weir(mid, crest, width, mu) + q_pass(mid, invert, slot, drop, c_q)
        if q < total:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def main() -> None:
    crest, width, n_sill, patch_area = weir_geometry()
    slot, drop, invert, n_baffles = pass_geometry()

    print("HW weir, from the reference model's own patch")
    print(f"  crest          {crest:.3f} m local, flat over {n_sill} cells "
          f"({n_sill * CELL * CELL:.2f} m2 of a {patch_area:.2f} m2 patch)")
    print(f"  width          {width:.2f} m across the flow, against the "
          f"{DRAWN_WIDTH:.2f} m on")
    print("                 Federica's dimension slide - a 0.25 m grid cannot do "
          "better")
    print("  the drawing calls it HW-Wehr and the parallel channel HW-Ableitung")
    print("\nfish pass, from the CAD")
    print(f"  slot           {slot:.4f} m at each of {n_baffles} baffles")
    print(f"  drop per pool  {drop:.4f} m")
    print(f"  head invert    {invert:.3f} m local")
    print(f"\n  the weir crest stands {crest - invert:.3f} m above the pass's first "
          f"pool,\n  and {WALL_TOP - crest:.3f} m BELOW the {WALL_TOP:.3f} m wall "
          "tops")

    level = solve_level(DESIGN_Q, crest, width, invert, slot, drop)
    qw = q_weir(level, crest, width)
    qp = q_pass(level, invert, slot, drop)
    print(f"\nat the design discharge {DESIGN_Q:.3f} m3/s (mu {MU}, C_Q {C_Q_DEFAULT}):")
    print(f"  upstream level {level:.3f} m, {WALL_TOP - level:.3f} m under the "
          "wall tops")
    print(f"  through the pass {qp * 1000:5.1f} l/s  ({100 * qp / DESIGN_Q:.0f}%)")
    print(f"  over the weir    {qw * 1000:5.1f} l/s  ({100 * qw / DESIGN_Q:.0f}%)")

    print("\nsensitivity - these two coefficients are the whole uncertainty:")
    print(f"  {'mu':>5} {'C_Q':>5} {'level':>7} {'pass':>7} {'weir':>7} {'pass %':>7}")
    for mu in (0.50, 0.60, 0.70):
        for c_q in (0.65, 0.85, 0.95):
            lv = solve_level(DESIGN_Q, crest, width, invert, slot, drop, mu, c_q)
            p = q_pass(lv, invert, slot, drop, c_q)
            print(f"  {mu:5.2f} {c_q:5.2f} {lv:7.3f} {p * 1000:6.1f} "
                  f"{(DESIGN_Q - p) * 1000:6.1f} {100 * p / DESIGN_Q:6.0f}")

    print("\nwhat this settles")
    print(f"  The pool cannot reach the wall tops. Sending all {DESIGN_Q:.3f} m3/s")
    print("  through the pass alone needs "
          f"{invert + DESIGN_Q / (C_Q_DEFAULT * slot * np.sqrt(2 * G * drop)):.3f} m, and "
          f"the weir spills at {crest:.3f} m,")
    print("  so the weir takes the difference long before the concrete is at risk.")
    for lv in (2.823, 3.011, 3.397):
        print(f"  A level of {lv:.3f} m would put {q_weir(lv, crest, width):.2f} "
              f"m3/s over this weir - {q_weir(lv, crest, width) / DESIGN_Q:.0f}x "
              "the whole design discharge.")


if __name__ == "__main__":
    main()
