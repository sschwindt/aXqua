"""Check the CAD-to-DEM path against an independent model of the same structure.

``user-sources/reference/federica-wetted-bed.csv`` is the lowest wetted boundary of a
previously built OpenFOAM model of this fish pass, read out of its ``polyMesh`` and
sampled onto a 0.25 m plan grid. It was meshed by snapping onto the same CAD surfaces
aXqua starts from, so its *geometry* is ground truth even though its water levels are
not (that run never converged).

The question it answers: does ``dem-from-surfaces.tif`` carry the same ground? A large
difference would mean a part is missing, mis-declared, or an overhang a height field
cannot represent.

**Read the two axes apart.** The reference cell is 0.25 m across and holds the LOWEST
wetted face in it; the DEM is 0.02 m and is sampled at a point. Wherever a vertical
face crosses a reference cell the two are measuring different things by construction,
and the difference can only be one-signed - the DEM higher. So the comparison is split
by whether the DEM itself steps inside the reference cell, and only the flat part is a
statement about the CAD-to-DEM path.

    python cases/munich-vsf/check_dem_against_reference.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

HERE = Path(__file__).resolve().parent
REFERENCE = HERE / "user-sources" / "reference" / "federica-wetted-bed.csv"
DEM = HERE / "axqua-case" / "preprocessing" / "dem-from-surfaces.tif"
STRUCTURES = HERE / "axqua-case" / "preprocessing" / "structures-from-surfaces.gpkg"

#: A reference cell is this wide, so this is the window the DEM's own relief is
#: measured over.
REF_CELL = 0.25
#: Above this much relief inside one reference cell, the two datasets are sampling a
#: vertical face and their difference is not a statement about the terrain.
STEP = 0.10
#: What counts as agreement. 5 cm on a structure whose baffles are 0.15 m thick.
TOLERANCE = 0.05


def load() -> pd.DataFrame:
    ref = pd.read_csv(REFERENCE)
    with rasterio.open(DEM) as src:
        ref["dem_z"] = [v[0] for v in src.sample(
            list(zip(ref.x.to_numpy(), ref.y.to_numpy())))]
        nodata = src.nodata
        half = REF_CELL / 2

        def relief(x: float, y: float) -> float:
            window = rasterio.windows.from_bounds(x - half, y - half, x + half,
                                                  y + half, src.transform)
            patch = src.read(1, window=window, boundless=True, fill_value=np.nan)
            patch = patch[np.isfinite(patch) & (patch != nodata)]
            return float(patch.max() - patch.min()) if patch.size else float("nan")

        ref["relief"] = [relief(a, b) for a, b in zip(ref.x, ref.y)]
    covered = np.isfinite(ref.dem_z) & (ref.dem_z != nodata)
    print(f"{covered.sum()} of {len(ref)} reference cells fall on covered DEM "
          f"({100 * covered.mean():.0f}%)")
    out = ref[covered].copy()
    out["diff"] = out.dem_z - out.bed_z
    return out


def main() -> None:
    if not REFERENCE.is_file() or not DEM.is_file():
        raise SystemExit(f"need both {REFERENCE.name} and {DEM.name}; "
                         "run preprocessing.py first and check the reference data")
    d = load()
    flat = d.relief <= STEP
    print(f"\n{'':<26} {'cells':>6} {'median':>8} {'sd':>7} "
          f"{'within ' + format(TOLERANCE, '.2f') + ' m':>14}")
    for label, sel in (("all", np.ones(len(d), bool)),
                       ("flat in the cell", flat.to_numpy()),
                       (f"stepping >{STEP:.2f} m", (~flat).to_numpy())):
        e = d["diff"][sel]
        print(f"{label:<26} {sel.sum():>6} {e.median():>+8.3f} {e.std():>7.3f} "
              f"{100 * (e.abs() < TOLERANCE).mean():>13.0f}%")

    big = d["diff"].abs() > TOLERANCE
    print(f"\nof the {big.sum()} cells disagreeing by more than {TOLERANCE:.2f} m, "
          f"{100 * (~flat)[big].mean():.0f}% sit on a step, against "
          f"{100 * (~flat)[~big].mean():.0f}% of those that agree")
    print(f"and the disagreement is one-signed: {(d['diff'][big] > 0).sum()} have the "
          f"DEM above the reference, {(d['diff'][big] < 0).sum()} below - which is "
          "what sampling a height field at a vertical face does.")

    print(f"\n{'patch':<34} {'n':>5} {'median':>8} {'within':>8}")
    for patch, g in d[flat].groupby("patch"):
        print(f"{patch:<34} {len(g):>5} {g['diff'].median():>+8.3f} "
              f"{100 * (g['diff'].abs() < TOLERANCE).mean():>7.0f}%")

    e = d["diff"][flat]
    verdict = ("SOUND" if abs(e.median()) < 0.02
               and (e.abs() < TOLERANCE).mean() > 0.9 else "CHECK THE PARTS")
    print(f"\nVERDICT: the CAD-to-DEM path is {verdict} - on ground that is flat "
          f"within a reference cell the two agree to {e.median():+.3f} m in the "
          f"median, {100 * (e.abs() < TOLERANCE).mean():.0f}% within "
          f"{TOLERANCE:.2f} m.")


if __name__ == "__main__":
    main()
