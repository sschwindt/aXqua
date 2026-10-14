"""Where along the pass water leaves through the lid, and how much, per written time.

A patch total says the domain is leaking; it does not say where, and the fix differs
completely between "the head pool is too close to its lid" and "the downstream end is
backing up". Both legs at 0.045 m and the one at 0.030 m lose their result the same way
after about t=40, so this exists to say which end does it.

Reads the decomposed fields directly, like measure_water_flux.py: the atmosphere patch
carries one face per plan column, so a face's flux can be attributed to the column's
station along the pass axis.

    reconstructPar -time 40 && postProcess -func writeCellCentres -time 40
    python cases/munich-vsf/locate_lid_leak.py <case-dir> [time ...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from correct_lid import _LIST, _read_list  # noqa: E402
from measure_water_flux import _patch_field, patch_names, written_times  # noqa: E402

ORIGIN = np.array([7.872073, 42.206112])
BEARING_DEG = 15.543955
TOP = "atmosphere"


def main() -> None:
    case = Path(sys.argv[1]).resolve()
    wanted = [float(t) for t in sys.argv[2:]]
    times = [t for t in written_times(case) if not wanted or float(t) in wanted]
    names = patch_names(case)
    # the RECONSTRUCTED case: the cell centres `postProcess -func writeCellCentres`
    # writes land there, and a face cannot be placed on the pass without them
    procs = [case]

    bearing = np.deg2rad(BEARING_DEG)
    axis = np.array([np.sin(bearing), np.cos(bearing)])
    edges = np.arange(0, 28, 2.0)

    for t in times:
        rows = np.zeros(len(edges))
        total = 0.0
        for proc in procs:
            tdir, cdir = proc / t, proc / "constant"
            a_file, p_file = tdir / "alpha.water", tdir / "phi"
            if not (a_file.is_file() and p_file.is_file()):
                continue
            abuf, pbuf = a_file.read_bytes(), p_file.read_bytes()
            a = _patch_field(abuf, TOP, names)
            f = _patch_field(pbuf, TOP, names)
            if a is None or f is None:
                continue
            w = a * f                                   # water flux, + = out of the lid
            xy = _face_centres(proc, t, len(w))
            if xy is None:
                continue
            s = (xy - ORIGIN) @ axis
            out = np.clip(w, 0.0, None)
            total += float(out.sum())
            idx = np.clip(np.digitize(s, edges) - 1, 0, len(edges) - 1)
            np.add.at(rows, idx, out)
        print(f"\nt = {float(t):g} s   water out through the lid: {total:.4f} m3/s")
        for i, lo in enumerate(edges):
            if rows[i] > 1e-4:
                bar = "#" * int(60 * rows[i] / max(rows.max(), 1e-9))
                print(f"  s {lo:4.0f}-{lo + 2:4.0f} {rows[i]:8.4f}  {bar}")


def _face_centres(proc: Path, t: str, n: int):
    """Plan position of each atmosphere face, from the cell centres of its column."""
    cc = proc / t / "Cx"
    if not cc.is_file():
        return None
    out = []
    for name in ("Cx", "Cy"):
        buf = (proc / t / name).read_bytes()
        binary = bool(re.search(rb"\s*format\s+binary\s*;", buf[:2000]))
        at = buf.index(b"internalField")
        v, _ = _read_list(buf, at, binary)
        out.append(v)
    layers = out[0].size // n
    return np.column_stack([out[0].reshape(n, layers)[:, 0],
                            out[1].reshape(n, layers)[:, 0]])


if __name__ == "__main__":
    main()
