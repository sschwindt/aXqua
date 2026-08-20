"""How long must a hotstarted calibration run be? (KB15, August 2026)

A calibration run that continues from the converged ``r2d.slf`` only has to
re-equilibrate to the perturbed roughness instead of re-filling the reach from the
pre-wet seed - on this mesh the difference between ~4.8 h and (hopefully) well
under an hour per run, i.e. between an infeasible and a feasible calibration.

The catch is that a hotstart is only honest if the run is long enough for the flow
field to FORGET the seed. Too short and every calibration run is pulled toward the
roughness ``r2d.slf`` was converged at, which shows up as an artificially damped
sensitivity to ks - a bias that would be invisible in the posterior.

So this probes the two extremes of the prior, where the adjustment away from the
seed is largest:

* ``smooth`` - every calibrated zone at its ``ks_min``,
* ``rough``  - every calibrated zone at its ``ks_max``.

Each is hotstarted from ``r2d.slf`` and run with frequent printouts. The companion
analysis (``--analyse``) tracks the calibration-point water depth and scalar
velocity frame by frame and reports the simulated time after which they stop
drifting - that plateau is the DURATION the calibration runs need.

Run:  mamba run -n hydromate-env python cases/inn-KB15-2025-hydro/hotstart_probe.py [--analyse]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from hydromate import read_roughness_zones, run_solver_streaming
from hydromate.config import load_config
from hydromate.env import TelemacRuntime
from hydromate.logsetup import setup_logging

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "case-config.yml"

DURATION = 2500.0     # long enough to see a plateau if it comes early
# TELEMAC printout periods count TIME STEPS, not seconds. The base case's 500
# steps came out at 24 s apart under the CFL-adaptive dt, so:
GRAPHIC = 2000        # ~96 s apart -> ~26 frames (~0.6 GB), enough to see a plateau
LISTING = 500         # ~24 s apart -> ~100 flux printouts for the balance check
LEVELS = ("smooth", "rough")
HOTSTART_SLF = "hotstart-seed.slf"   # slim one-frame extract of the converged r2d.slf


def _friction_tbl(cfg, level: str) -> str:
    """Friction table with every CALIBRATED zone pinned at one end of its prior.

    Non-calibrated zones keep their nominal ks - they are not perturbed during the
    calibration either, so moving them would probe an adjustment that never happens.
    """
    zones = read_roughness_zones(cfg.geodata.roughness_table)
    lines = ["* hotstart probe: calibrated zones at their prior "
             f"{'lower' if level == 'smooth' else 'upper'} bound",
             "* Columns: Fric_ID  BottomLaw  Coefficient  Mdef", "*"]
    for zid, z in sorted(zones.items()):
        calibrated = bool(z.calibrate and z.ks_min is not None and z.ks_max is not None
                          and z.ks_max > z.ks_min)
        ks = z.ks if not calibrated else (z.ks_min if level == "smooth" else z.ks_max)
        lines += [f"* zone {zid}: {'calibrated' if calibrated else 'pinned'}",
                  f"{zid}\tNIKU\t{ks:.4f}\tNULL"]
    return "\n".join(lines + ["END", ""])


def build(cfg, probe_dir: Path) -> dict[str, Path]:
    """One run directory per level: symlinked geometry/cli/hotstart + own .tbl/.cas."""
    from hydromate.bayescal import FlowSpec, write_flow_cas

    from hydromate.selafin import extract_hotstart

    src = cfg.model_dir
    base_cas = src / cfg.cas_file
    # one slim seed for every run: TELEMAC copies the PREVIOUS COMPUTATION FILE
    # into each run's work directory, and the full r2d.slf is 2.3 GB of printout
    # history for the sake of one frame
    seed = src / HOTSTART_SLF
    if not seed.exists():
        print(f"extracting the converged frame -> {seed.name}", flush=True)
        extract_hotstart(src / cfg.results_slf, seed)
        print(f"  {seed.stat().st_size / 1e6:.1f} MB "
              f"(from {(src / cfg.results_slf).stat().st_size / 1e9:.1f} GB)")
    cases: dict[str, Path] = {}
    for level in LEVELS:
        d = probe_dir / level
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        for name in ("geometry.slf", "boundaries.cli", HOTSTART_SLF):
            (d / name).symlink_to(src / name)
        (d / cfg.friction_tbl).write_text(_friction_tbl(cfg, level))
        # the probe reuses the case's own steady discharge; only the seed changes
        flow = FlowSpec(name=level, discharge=cfg.boundaries.prescribed_flowrate,
                        kind="csv", values=Path("unused"),
                        duration=DURATION, hotstart_from=HOTSTART_SLF)
        cases[level] = write_flow_cas(cfg, base_cas, flow, probe_dir, dest_dir=d,
                                      listing=LISTING, graphic=GRAPHIC)
    return cases


def analyse(cfg, probe_dir: Path) -> int:
    """Frame-by-frame drift of the calibration-point QoI -> the plateau time."""
    from hydromate.selafin import read_slf

    pts = pd.concat([pd.read_csv(HERE / "hydromate-case/preprocessing"
                                 / f"measurements-corrected-{n}.csv")
                     for n in ("q47-3", "q48-45")], ignore_index=True)
    px, py = pts.iloc[:, 1].to_numpy(), pts.iloc[:, 2].to_numpy()

    print(f"\n{'level':>8} {'t [s]':>8} {'dH vs final':>12} {'dU vs final':>12}")
    recommended = 0.0
    for level in LEVELS:
        slf = probe_dir / level / f"r2d-{level}.slf"
        if not slf.exists():
            print(f"  {level}: no result at {slf} - run without --analyse first")
            return 1
        # read_slf returns one frame per call, so walk them (the QoI is only ever
        # the handful of calibration nodes, so only those are kept per frame)
        last = read_slf(slf)
        x, y = last["x"], last["y"]
        node = np.array([int(np.argmin((x - a) ** 2 + (y - b) ** 2))
                         for a, b in zip(px, py)])
        n_times = int(last["n_times"])
        times, H, U = [], [], []
        for i in range(n_times):
            fr = read_slf(slf, frame=i)
            times.append(float(fr["time"]))
            H.append(np.asarray(fr["values"]["WATER DEPTH"])[node])
            U.append(np.asarray(fr["values"]["SCALAR VELOCITY"])[node])
        times, H, U = np.array(times), np.array(H), np.array(U)
        plateau = None
        for i, t in enumerate(times):
            dH = float(np.abs(H[i] - H[-1]).max())
            dU = float(np.abs(U[i] - U[-1]).max())
            print(f"{level:>8} {t:>8.0f} {dH:>12.4f} {dU:>12.4f}")
            # settled = every point within measurement resolution of its final value
            if plateau is None and dH < 0.005 and dU < 0.01:
                plateau = t
        print(f"  -> {level}: QoI within 5 mm / 0.01 m/s of final from t={plateau} s")
        recommended = max(recommended, plateau if plateau is not None else times[-1])
    print(f"\nRECOMMENDED per-flow DURATION >= {recommended:.0f} s "
          f"(the slower extreme, rounded up with margin)")
    print("NOTE: this is re-equilibration from a field converged at the NOMINAL ks. "
          "A calibration run perturbs the same way, so the same duration applies.")
    return 0


def extend(cfg, probe_dir: Path, level: str = "rough",
           extra: float = 2000.0) -> int:
    """Continue a finished probe to settle whether its DURATION was long enough.

    The drift table compares each frame with the *last frame of the same run*, which
    is circular while the run is still converging: an unconverged run's frames all
    approach its final one and look like a plateau. The only non-circular test is to
    run on and see how far the QoI actually moves. Whatever it moves between the old
    end and the new one is the error a run of the original length would have carried.
    """
    from hydromate.selafin import extract_hotstart

    src = probe_dir / level
    d = probe_dir / f"{level}-extended"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    for name in ("geometry.slf", "boundaries.cli"):
        (d / name).symlink_to(cfg.model_dir / name)
    shutil.copy2(src / cfg.friction_tbl, d / cfg.friction_tbl)   # same roughness
    seed = d / HOTSTART_SLF
    extract_hotstart(src / f"r2d-{level}.slf", seed)

    from hydromate.bayescal import FlowSpec, write_flow_cas
    flow = FlowSpec(name=f"{level}-ext", discharge=cfg.boundaries.prescribed_flowrate,
                    kind="csv", values=Path("unused"),
                    duration=extra, hotstart_from=HOTSTART_SLF)
    cas = write_flow_cas(cfg, cfg.model_dir / cfg.cas_file, flow, probe_dir,
                         dest_dir=d, listing=LISTING, graphic=GRAPHIC)
    print(f"\n=== extending {level} by {extra:g} s ({cas}) ===", flush=True)
    proc = run_solver_streaming(TelemacRuntime(cfg.telemac), cfg, cas_file=cas.name,
                                cwd=d, duration=extra)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--analyse", action="store_true",
                    help="analyse finished probe runs instead of launching them")
    ap.add_argument("--extend", metavar="LEVEL", nargs="?", const="rough",
                    help="continue a finished probe further to test its DURATION")
    args = ap.parse_args()

    cfg = load_config(CONFIG)
    probe_dir = cfg.postprocessing_dir / "hotstart-probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(probe_dir / cfg.log_file)

    if args.analyse:
        return analyse(cfg, probe_dir)
    if args.extend:
        return extend(cfg, probe_dir, args.extend)

    cases = build(cfg, probe_dir)
    runtime = TelemacRuntime(cfg.telemac)
    for level, cas in cases.items():
        print(f"\n=== hotstart probe: {level} ({cas}) ===", flush=True)
        proc = run_solver_streaming(runtime, cfg, cas_file=cas.name,
                                    cwd=cas.parent, duration=DURATION)
        if proc.returncode != 0:
            print(f"probe {level} failed (rc={proc.returncode})")
            return proc.returncode
    print("\nboth probes done - now run with --analyse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
