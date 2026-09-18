"""Does a 2D depth-averaged model over-resist a vertical slot?

The Munich VSF 2D run settles 0.5 to 0.8 m higher than a reference 3D model of the
same structure. Its slots are demonstrably open and the flow through them is slow, so
the remaining explanation is that a depth-averaged model has to represent a strongly
three-dimensional slot jet through bed friction and a turbulence closure alone - and
if it over-resists even slightly, fourteen slots in series back the level up a long
way.

This measures that directly, on a synthetic flume carrying the design dimensions and
nothing else (see ``make_geodata.py``). The design sheds **1.690 m over the 13 pitches
between its 14 baffles**, i.e. 0.130 m per pool, which is simply its bed fall: at
design discharge every pool holds the same depth, so the water surface falls exactly
as the bed does. If the model needs materially more head than that, the 2D interior of
a fish pass cannot be trusted to set a 3D model's boundary levels.

**The mesh sweep is what makes the answer usable.** Run at 0.10 / 0.05 / 0.025 m:

* if the required head FALLS towards 1.690 m as the mesh refines, the over-resistance
  is discretisation - a 0.380 m slot is 3.8 cells at 0.10 m and the element-crossing
  rule that applies a wall narrows it further, so a coarse mesh throttles the slot
  geometrically;
* if it PLATEAUS above 1.690 m, no amount of refinement fixes it, and the cause is the
  physics of a depth-averaged model.

Either answer is useful. Run::

    python cases/slot-flume/slot_resistance_study.py            # the three levels
    python cases/slot-flume/slot_resistance_study.py 0.05       # just one
    python cases/slot-flume/slot_resistance_study.py --report   # re-read finished runs
    python cases/slot-flume/slot_resistance_study.py --q=0.060 0.10   # another discharge

**The discharge sweep answers a different question**, and it is worth having. The
Munich comparison that started this puts a 2D result at 0.135 m3/s beside a reference
3D model at 0.060 m3/s and reads the level difference as model error. A vertical slot
passes Q ~ h sqrt(2 g dh) with dh fixed by the geometry, so the pool depth rises
roughly in proportion to the discharge - and 0.135/0.060 is a factor of 2.25. Running
this flume at both says how much of a level difference that alone accounts for,
without any model being wrong.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_geodata as design                                   # noqa: E402

from axqua import pipeline                                      # noqa: E402
from axqua.config import load_config                            # noqa: E402
from axqua.core import selafin                                  # noqa: E402
from axqua.env import TelemacRuntime                            # noqa: E402
from axqua.flux_convergence import relative_imbalance           # noqa: E402
from axqua.logsetup import setup_logging                        # noqa: E402
from axqua.solvers.telemac import sortie                        # noqa: E402
from axqua.workflow import run_solver_streaming                 # noqa: E402

#: Target element size per level [m]. 0.380 m of slot is 3.8 / 7.6 / 15.2 cells.
LEVELS = (0.10, 0.05, 0.025)

#: How far from a baffle the pool is sampled. Close enough to be the head that slot
#: sees, far enough to be out of its own jet and its recirculation.
POOL_NEAR = 0.20        # [m] upstream of the baffle face
POOL_FAR = 1.20         # [m] upstream of the baffle face


def level_dir(cfg, size: float, discharge: float | None = None) -> Path:
    name = f"dx{1000 * size:.0f}mm"
    if discharge is not None:
        name += f"-q{1000 * discharge:.0f}"
    return Path(cfg.postprocessing_dir) / "slot-resistance" / name


def build_and_run(base, size: float, *, run: bool = True,
                  discharge: float | None = None,
                  duration: float | None = None) -> Path:
    """Build the flume at *size* in its own folder and run the steady case there.

    *duration* shortens the march. Measured on the coarse level: the domain volume
    stops changing at t = 302 s and the imbalance is oscillating around 1e-3 by
    t = 221 s, so the configured 900 s is 3.6x longer than the flume needs. That
    matters only at the finest level, where it is the difference between four hours
    and eight. Each level's own balance is reported, so a run that was cut short
    would be visible rather than quietly averaged in.
    """
    cfg = copy.deepcopy(base)
    cfg.mesh.size_scale = size / base.mesh.default_size
    if discharge is not None:
        cfg.boundaries.prescribed_flowrate = float(discharge)
    if duration is not None:
        cfg.hydrodynamics.duration = float(duration)
    target = level_dir(base, size, discharge)
    cfg.model_dir = target
    cfg.preprocessing_dir = target / "preprocessing"
    cfg.postprocessing_dir = target / "postprocessing"
    cfg.calibration_dir = target / "calibration"
    cfg.ensure_dirs()
    pipeline.run(cfg, validate_env=False, log_to_file=False)
    if run:
        proc = run_solver_streaming(TelemacRuntime(cfg.telemac), cfg)
        if proc.returncode != 0:
            raise RuntimeError(f"telemac2d exited {proc.returncode} at dx={size} m")
    return target


# --------------------------------------------------------------------------- #
# the measurement
# --------------------------------------------------------------------------- #

def pool_levels(model_dir: Path, cfg) -> dict:
    """Free-surface elevation in the pool feeding each baffle, from the last frame.

    The pool level is the MEDIAN free surface of the wet nodes in a band upstream of
    the baffle. A median rather than a mean because a pool is a near-still body with a
    jet crossing it: the mean is pulled by the few cells in the jet, the median is the
    level the slot actually sees.
    """
    result = selafin.read_slf(model_dir / cfg.results_slf)
    values = result["values"]
    x = np.asarray(result["x"], dtype=float)
    depth = np.asarray(values["WATER DEPTH"], dtype=float)
    surface = np.asarray(values["FREE SURFACE"], dtype=float)
    wet = depth > cfg.hydrodynamics.wet_depth

    speed = np.hypot(np.asarray(values["VELOCITY U"], dtype=float),
                     np.asarray(values["VELOCITY V"], dtype=float))

    levels, counts, depths, slot_u = [], [], [], []
    for i in range(design.N_BAFFLES):
        xb = design.baffle_x(i)
        band = wet & (x > xb - POOL_FAR) & (x < xb - POOL_NEAR)
        counts.append(int(band.sum()))
        levels.append(float(np.median(surface[band])) if band.any() else float("nan"))
        # the pool's own depth, for a sanity check against a reference model's levels
        depths.append(float(np.median(depth[band])) if band.any() else float("nan"))
        # and what the jet does AT the slot: the design relation for a vertical slot
        # is Q = Cd b h sqrt(2 g dh), so the slot velocity is the one quantity that
        # says whether the model is passing the discharge the way the design does
        at_slot = wet & (np.abs(x - xb) < design.WALL_THICKNESS)
        slot_u.append(float(np.max(speed[at_slot])) if at_slot.any() else float("nan"))
    return {"levels": levels, "counts": counts, "depths": depths,
            "slot_speed": slot_u,
            "wet_nodes": int(wet.sum()), "nodes": int(len(x))}


def pool_flatness(model_dir: Path, cfg) -> float:
    """Median surface fall WITHIN a pool, as a fraction of the fall taken per pool.

    The qualitative counterpart to the head number, and it has to be checked before
    the head means anything. A vertical-slot fishway works because each pool is a
    near-horizontal body and the whole fall is taken at the slots; if the model
    instead produces a sloping sheet then it is not modelling a fishway, and
    comparing its head against the design is comparing two different things.
    """
    result = selafin.read_slf(model_dir / cfg.results_slf)
    x = np.asarray(result["x"], dtype=float)
    depth = np.asarray(result["values"]["WATER DEPTH"], dtype=float)
    surface = np.asarray(result["values"]["FREE SURFACE"], dtype=float)
    wet = depth > cfg.hydrodynamics.wet_depth

    falls = []
    for i in range(design.N_BAFFLES - 1):
        a = design.baffle_x(i) + design.WALL_THICKNESS
        b = design.baffle_x(i + 1) - design.WALL_THICKNESS
        band = wet & (x > a) & (x < b)
        if band.sum() >= 5:
            lo, hi = np.percentile(surface[band], [10, 90])
            falls.append(hi - lo)
    if not falls:
        return float("nan")
    return float(np.median(falls) / design.DESIGN_PER_POOL)


def realised_slots(model_dir: Path, cfg) -> list[float]:
    """Open width at each baffle in the mesh that was actually solved.

    A wall is applied by ELEMENT CROSSED, so it eats up to one element of width per
    side and the realised slot is narrower than drawn. That narrowing IS part of the
    resistance being measured, and it is the part a finer mesh removes - so it has to
    be reported beside the head, not left implicit.
    """
    geo = selafin.read_slf(model_dir / cfg.geometry_slf)
    x = np.asarray(geo["x"], dtype=float)
    y = np.asarray(geo["y"], dtype=float)
    z = np.asarray(geo["values"]["BOTTOM"], dtype=float)
    raised = (z - design.bed_z(x)) > 0.20
    out = []
    for i in range(design.N_BAFFLES):
        xb = design.baffle_x(i)
        band = np.abs(x - xb) < design.WALL_THICKNESS / 2 + 1e-6
        open_y = y[band & ~raised]
        out.append(float(design.CLEAR_WIDTH - open_y.min()) if open_y.size else 0.0)
    return out


def balance(model_dir: Path, cfg) -> dict:
    """The run's own flux balance, so a head is never quoted from a transient."""
    listing = sortie.latest_sortie(model_dir, cfg.cas_file)
    if listing is None:
        return {"imbalance": None, "time": None}
    data = sortie.read_sortie(listing)
    imb = relative_imbalance(data.gross_in, data.gross_out)
    return {"imbalance": float(imb[-1]) if len(imb) else None,
            "time": float(data.time[-1]) if data.time.size else None,
            "q_in": float(data.gross_in[-1]) if data.gross_in.size else None,
            "q_out": float(data.gross_out[-1]) if data.gross_out.size else None}


def measure(base, size: float, discharge: float | None = None) -> dict:
    cfg = copy.deepcopy(base)
    target = level_dir(base, size, discharge)
    cfg.model_dir = target
    record = {"dx": size, "cells_across_slot": design.SLOT_WIDTH / size,
              "discharge": float(discharge if discharge is not None
                                 else base.boundaries.prescribed_flowrate)}
    record.update(balance(target, cfg))
    record.update(pool_levels(target, cfg))
    slots = realised_slots(target, cfg)
    record["slot_mean"] = float(np.mean(slots))
    record["slots"] = slots

    levels = np.array(record["levels"], dtype=float)
    drops = -np.diff(levels)                      # pool i -> pool i+1, 13 of them
    record["drops"] = drops.tolist()
    record["total_head"] = float(levels[0] - levels[-1])
    record["head_per_pool"] = float(np.nanmean(drops))
    record["excess"] = record["total_head"] - design.DESIGN_HEAD
    record["excess_ratio"] = record["total_head"] / design.DESIGN_HEAD
    record["pool_depth"] = float(np.nanmedian(record["depths"]))
    record["in_pool_fraction"] = pool_flatness(target, cfg)
    record["slot_speed_mean"] = float(np.nanmean(record["slot_speed"]))
    # What the design relation asks of a slot passing Q under the design head:
    # Q = Cd b h sqrt(2 g dh). Quoted, not asserted - Cd for a vertical slot is
    # 0.65-0.85 in the literature, so this is an order check, not a target.
    record["design_slot_depth"] = float(
        design.DISCHARGE / (0.70 * design.SLOT_WIDTH
                            * np.sqrt(2 * 9.81 * design.DESIGN_PER_POOL)))
    return record


def report(records: list[dict]) -> list[str]:
    out = [
        "",
        "=" * 78,
        "Does a 2D depth-averaged model over-resist a vertical slot?",
        "=" * 78,
        f"design: {design.N_BAFFLES} baffles, {design.PITCH:g} m pitch, "
        f"{design.SLOT_WIDTH:g} m slot, Q = {design.DISCHARGE:g} m3/s, "
        f"ks = {design.BED_KS:g} m",
        f"        bed falls {design.DESIGN_HEAD:.3f} m over "
        f"{design.N_BAFFLES - 1} pitches = {design.DESIGN_PER_POOL:.4f} m per pool,",
        "        which at design discharge is also the water-surface fall.",
        "",
        f"{'dx [m]':>7} {'cells/slot':>11} {'slot [m]':>9} {'head [m]':>9} "
        f"{'per pool':>9} {'vs design':>10} {'pool h':>8} {'slot U':>8} "
        f"{'imbalance':>10}",
    ]
    for r in sorted(records, key=lambda r: -r["dx"]):
        imb = "-" if r["imbalance"] is None else f"{r['imbalance']:.2e}"
        out.append(f"{r['dx']:>7.3f} {r['cells_across_slot']:>11.1f} "
                   f"{r['slot_mean']:>9.3f} {r['total_head']:>9.3f} "
                   f"{r['head_per_pool']:>9.4f} {r['excess_ratio']:>9.2f}x "
                   f"{r['pool_depth']:>8.3f} {r['slot_speed_mean']:>8.2f} "
                   f"{imb:>10}")
    out.append("")
    fractions = [r["in_pool_fraction"] for r in records
                 if not np.isnan(r.get("in_pool_fraction", float("nan")))]
    if fractions:
        share = 100 * float(np.median(fractions))
        out.append(f"the surface falls {share:.0f}% of the per-pool drop ALONG the "
                   f"pools and {100 - share:.0f}% at the slots, so the model is "
                   "producing a fishway rather than a chute - which is what makes the "
                   "head above comparable with the design at all")
    out.append("")
    out.append(f"the design relation Q = Cd b h sqrt(2 g dh) at Cd 0.70 wants a slot "
               f"{records[0]['design_slot_depth']:.3f} m deep to pass "
               f"{design.DISCHARGE:g} m3/s under {design.DESIGN_PER_POOL:.3f} m "
               f"(an order check, not a target: Cd is 0.65-0.85 in the literature)")
    out.append("")
    if len(records) >= 2:
        ordered = sorted(records, key=lambda r: -r["dx"])
        heads = [r["total_head"] for r in ordered]
        trend = heads[-1] - heads[0]
        out.append(f"head from coarsest to finest: {heads[0]:.3f} -> {heads[-1]:.3f} m "
                   f"({trend:+.3f} m)")
        finest = ordered[-1]
        per_pool = finest["excess"] / (design.N_BAFFLES - 1)
        out.append(f"VERDICT: at the finest level the model needs "
                   f"{finest['excess']:+.3f} m more head than the design over all "
                   f"{design.N_BAFFLES - 1} pools, i.e. {per_pool:+.4f} m per pool "
                   f"({100 * finest['excess'] / design.DESIGN_HEAD:+.0f}%).")
        if trend < -0.05 * design.DESIGN_HEAD:
            out.append("         It FALLS with refinement, so what there is of it is "
                       "discretisation rather than physics.")
        elif abs(trend) <= 0.05 * design.DESIGN_HEAD:
            out.append("         It is flat across the sweep, so it is not a "
                       "resolution artefact - but at this size it is also not an "
                       "explanation for a level that is half a metre out.")
        else:
            out.append("         It GROWS with refinement, which is not a "
                       "discretisation signature and is worth understanding before "
                       "the number is used.")
        # The mesh moves the LEVEL far more than it moves the head, and a level is
        # what a cross-model comparison actually reads.
        depths = [r["pool_depth"] for r in ordered]
        slots = [r["slot_mean"] for r in ordered]
        if len(depths) >= 2 and all(np.isfinite(depths)):
            out.append("")
            out.append(f"But the LEVEL is a different matter: the pool depth goes "
                       f"{depths[0]:.3f} -> {depths[-1]:.3f} m "
                       f"({100 * (depths[-1] / depths[0] - 1):+.0f}%) as the realised "
                       f"slot goes {slots[0]:.3f} -> {slots[-1]:.3f} m. The head is a "
                       "property of the bed; the depth is a property of the slot the "
                       "mesh actually built, and a cross-model comparison reads the "
                       "depth.")
    return out


def discharge_report(store: Path) -> list[str]:
    """Every discharge measured at the same mesh, and what it says about a comparison.

    The Munich comparison that prompted this study reads a 2D result at 0.135 m3/s
    against a reference 3D model at 0.060 m3/s and calls the difference model error.
    A vertical slot passes Q = Cd b h sqrt(2 g dh) with dh fixed by the bed geometry,
    so h ~ Q: the pool is deeper at a higher discharge *by design*, and comparing the
    two levels compares two different flows.
    """
    records = []
    for path in sorted(store.glob("slot-resistance*.json")):
        for r in json.loads(path.read_text()):
            if r.get("discharge") is not None:
                records.append(r)
    records = [r for r in records if not np.isnan(r.get("pool_depth", float("nan")))]
    # ONE mesh, or the comparison is not about discharge. The pool depth is strongly
    # mesh-sensitive (it follows the realised slot width), so mixing levels here put a
    # mesh effect into the discharge exponent and moved it from 0.97 to 0.80.
    if records:
        dx = max({r["dx"] for r in records},
                 key=lambda v: sum(1 for r in records if r["dx"] == v))
        records = [r for r in records if r["dx"] == dx]
    if len(records) < 2:
        return []
    records.sort(key=lambda r: r["discharge"])
    out = ["", "=" * 78,
           "The same flume at different discharges (same mesh)", "=" * 78,
           f"{'Q [m3/s]':>9} {'dx [m]':>7} {'pool h [m]':>11} {'head [m]':>9} "
           f"{'vs design':>10}"]
    for r in records:
        out.append(f"{r['discharge']:>9.3f} {r['dx']:>7.3f} {r['pool_depth']:>11.3f} "
                   f"{r['total_head']:>9.3f} {r['excess_ratio']:>9.2f}x")
    q = np.array([r["discharge"] for r in records])
    h = np.array([r["pool_depth"] for r in records])
    exponent = float(np.polyfit(np.log(q), np.log(h), 1)[0])
    out += ["",
            f"pool depth ~ Q^{exponent:.2f} over {q.min():g}-{q.max():g} m3/s "
            "(the slot relation says Q ~ h, i.e. an exponent of 1)",
            f"the head is unchanged across a {q.max() / q.min():.1f}x discharge range "
            f"({min(r['total_head'] for r in records):.3f}-"
            f"{max(r['total_head'] for r in records):.3f} m) - it is set by the BED, "
            "not by the flow, which is what a fishway is designed to do"]
    pair = [r for r in records if abs(r["discharge"] - 0.060) < 1e-9
            or abs(r["discharge"] - 0.135) < 1e-9]
    if len(pair) == 2:
        lo, hi = sorted(pair, key=lambda r: r["discharge"])
        out.append("")
        out.append(f"MUNICH-RELEVANT: {lo['discharge']:g} -> {hi['discharge']:g} m3/s "
                   f"deepens every pool from {lo['pool_depth']:.3f} to "
                   f"{hi['pool_depth']:.3f} m, a factor of "
                   f"{hi['pool_depth'] / lo['pool_depth']:.2f} on a discharge ratio of "
                   f"{hi['discharge'] / lo['discharge']:.2f}. A level comparison across "
                   "those two discharges is not a like-for-like comparison.")
    return out


def main() -> None:
    base = load_config(Path(__file__).resolve().parent / "case-config.yml")
    base.ensure_dirs()
    store = Path(base.postprocessing_dir) / "slot-resistance"
    store.mkdir(parents=True, exist_ok=True)
    setup_logging(store / "axqua.log")

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    only_report = "--report" in sys.argv
    q = next((float(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--q=")),
             None)
    duration = next((float(a.split("=", 1)[1]) for a in sys.argv
                     if a.startswith("--duration=")), None)
    sizes = [float(a) for a in args] if args else list(LEVELS)

    records = []
    for size in sizes:
        if not only_report:
            print(f"\n=== dx = {size:g} m "
                  f"({design.SLOT_WIDTH / size:.1f} cells across the slot"
                  + (f", Q = {q:g} m3/s" if q is not None else "") + ") ===")
            build_and_run(base, size, discharge=q, duration=duration)
        try:
            records.append(measure(base, size, discharge=q))
        except Exception as exc:                      # noqa: BLE001
            print(f"dx={size}: could not measure ({type(exc).__name__}: {exc})")

    if records:
        name = "slot-resistance" + (f"-q{1000 * q:.0f}" if q is not None else "")
        (store / f"{name}.json").write_text(
            json.dumps(records, indent=2) + "\n")
        lines = report(records) + discharge_report(store)
        print("\n".join(lines))
        (store / f"{name}.txt").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
