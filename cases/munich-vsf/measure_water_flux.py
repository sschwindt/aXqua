"""Water discharge per patch, read straight off the decomposed fields.

WHY THIS EXISTS. ESI's `surfaceFieldValue` treats weighting as a separate
operation - `opWeightedSum = (opSum | typeWeighted)` - so `operation sum` with a
`weightField` silently reports the MIXTURE flux, water and air together.
Foundation v9 applied the weight to plain sum, so the v9 -> v2406 move changed
what every discharge monitor meant without changing a line of any case. Measured
on the v2406 damBreak tutorial, same patch and same step:

    operation sum          -4.49e-08      the mixture
    operation weightedSum   1.63e-66      the water

`dicts.py` writes `weightedSum` now, but **a run already in flight cannot be
repaired by editing its controlDict**: `operation_` is assigned only in
surfaceFieldValue's constructors and never in its `read()`, so a runtime re-read
reports the new file and keeps the old operation. The log says so plainly -
"Re-reading object controlDict" followed by "operation = sum".

So for a run started before the fix, the monitors are mixture flux for life, and
this recovers the water flux from what is on disk instead. No restart, and no
`reconstructPar`: a patch's faces are spread across the processor directories, so
summing `alpha.water * phi` over each `processor*/<time>/` and adding gives the
same global number far more cheaply.

    python cases/munich-vsf/measure_water_flux.py <case-dir> [patch ...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from correct_lid import _LIST, _read_list  # noqa: E402

DEFAULT_PATCHES = ("inlet-1", "outlet-1", "atmosphere")


def _patch_field(buf: bytes, want: str, names: list[str]) -> np.ndarray | None:
    """The boundary values of one patch, or None when it carries no list.

    Each patch block is bounded by the NEXT patch header rather than read to the
    end of the file: without that bound a patch whose entry is `uniform 0` picks
    up the next patch's list, which silently reports the bed's flux as the inlet's.
    """
    binary = bool(re.search(rb"format\s+binary\s*;", buf[:2000]))
    start = buf.index(b"boundaryField")
    hits = []
    for name in names:
        m = re.search(rb"\n\s*" + re.escape(name.encode()) + rb"\s*\n\s*\{", buf[start:])
        if m:
            hits.append((start + m.end(), name))
    hits.sort()
    for i, (at, name) in enumerate(hits):
        if name != want:
            continue
        end = hits[i + 1][0] if i + 1 < len(hits) else len(buf)
        m = _LIST.search(buf, at)
        if m is None or m.start() > end:
            return None
        return _read_list(buf, at, binary)[0]
    return None


def patch_names(case: Path) -> list[str]:
    boundary = case / "processor0" / "constant" / "polyMesh" / "boundary"
    if not boundary.is_file():
        boundary = case / "constant" / "polyMesh" / "boundary"
    text = boundary.read_text(errors="replace")
    return re.findall(r"\n\s{4}(\w[\w.-]*)\s*\n\s{4}\{", text)


def water_flux(case: Path, time: str, patches) -> dict[str, tuple[float, float]]:
    """``{patch: (water, mixture)}`` in m3/s, positive OUT of the domain."""
    names = patch_names(case)
    procs = sorted(case.glob("processor*"))
    if not procs:
        procs = [case]
    out = {p: [0.0, 0.0] for p in patches}
    for proc in procs:
        tdir = proc / time
        a_file, p_file = tdir / "alpha.water", tdir / "phi"
        if not (a_file.is_file() and p_file.is_file()):
            continue
        abuf, pbuf = a_file.read_bytes(), p_file.read_bytes()
        for p in patches:
            a = _patch_field(abuf, p, names)
            f = _patch_field(pbuf, p, names)
            if a is None or f is None or a.size != f.size:
                continue
            out[p][0] += float((a * f).sum())
            out[p][1] += float(f.sum())
    return {k: (v[0], v[1]) for k, v in out.items()}


def written_times(case: Path) -> list[str]:
    proc = next(iter(sorted(case.glob("processor*"))), case)
    times = [d.name for d in proc.iterdir()
             if d.is_dir() and re.fullmatch(r"[0-9]+(\.[0-9]+)?", d.name)
             and (d / "alpha.water").is_file()]
    return sorted(times, key=float)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__.strip().splitlines()[-1].strip())
    case = Path(sys.argv[1]).resolve()
    patches = sys.argv[2:] or list(DEFAULT_PATCHES)
    times = written_times(case)
    if not times:
        raise SystemExit(f"no written time with alpha.water under {case}")

    print(f"{case.name}: {len(times)} written time(s), "
          f"{len(sorted(case.glob('processor*'))) or 1} processor dir(s)")
    print(f"\n{'time':>8}  " + "  ".join(f"{p:>22}" for p in patches))
    print(f"{'':>8}  " + "  ".join(f"{'water':>10} {'mixture':>11}" for _ in patches))
    for t in times:
        row = water_flux(case, t, patches)
        cells = "  ".join(f"{row[p][0]:10.5f} {row[p][1]:11.5f}" for p in patches)
        print(f"{float(t):8.2f}  {cells}")

    last = water_flux(case, times[-1], patches)
    if "inlet-1" in last and last["inlet-1"][0]:
        qin = abs(last["inlet-1"][0])
        print(f"\nat t={times[-1]}: water in {qin:.5f} m3/s")
        for p in patches:
            if p != "inlet-1":
                print(f"  {p:<12} water {last[p][0]:+9.5f} m3/s "
                      f"({abs(last[p][0]) / qin * 100:7.1f}% of it)")


if __name__ == "__main__":
    main()
