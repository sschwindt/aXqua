#!/usr/bin/env python
"""Build the input data of an example case and the index of the example cases.

An example case of aXqua is a folder that works on its own: the case file, a guide, and
the input data in ``user-sources/``. The data stem from a research case, whose input
folder is large and not published. This script reduces them to what the example reads:

* the **terrain model** is cut to the outline of the model plus a margin and stored as
  32-bit values with lossless compression. aXqua cuts the terrain to the outline before
  it does anything else, so the model built from the reduced file is the model built
  from the original. A 32-bit value resolves 0.06 mm at an elevation of 800 m.
* the **layers** and **tables** are copied as they are.

What the script writes is tracked in the repository, so run it only to change the data
of an example, and read what it reports.

It then writes ``cases/examples.json``, the index that ``axqua example`` reads: every
file of every example with its size and checksum.

Usage, from the root of the repository::

    python scripts/build_example_data.py            # build the data, write the index
    python scripts/build_example_data.py --index    # write the index only
    python scripts/build_example_data.py --check    # exit 1 if the index is out of date
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CASES = REPO / "cases"
INDEX = CASES / "examples.json"

#: Margin around the outline of the model within which the terrain is kept [m].
MARGIN = 5.0

#: The example cases. ``documents`` are the files next to the case file that belong to
#: the example. ``files``: ``target in user-sources/ -> (source, how)``, where the
#: source is relative to ``origin`` and *how* is ``copy`` or ``terrain``. An example is
#: exactly these files: results, status files and whatever else lies in its folder on
#: a computer are not part of it.
EXAMPLES: dict[str, dict] = {
    "example-isar": {
        "title": "Isar River, losing and gaining reach",
        "summary": "A braided gravel-bed reach of 6.6 ha at a discharge of 2.4 m3/s, "
                   "which loses water into a gravel bar and gains it back. "
                   "Two-dimensional TELEMAC model with about 26,500 nodes: the build "
                   "takes one minute and the steady simulation about two minutes.",
        "case": "example-isar.axq-case",
        "documents": ["README.md", "LICENSE.md", "LICENSE-CC-BY-4.0.txt"],
        "origin": "isar-2025/user-sources",
        "outline": "geodata/roi.gpkg",
        "files": {
            "geodata/dem-2025-03.tif": ("geodata/merged-DEM-bathy-corr.tif", "terrain"),
            "geodata/roi.gpkg": ("geodata/roi.gpkg", "copy"),
            "geodata/mesh-zones.gpkg": ("geodata/mesh-zones.gpkg", "copy"),
            "geodata/channel-centerline.gpkg": ("geodata/channel-centerline.gpkg", "copy"),
            "geodata/roughness-zones.gpkg": ("geodata/roughness-zones.gpkg", "copy"),
            "geodata/roughness-table.csv": ("geodata/roughness-table.csv", "copy"),
            "geodata/control-sections.gpkg": ("geodata/baffles.gpkg", "copy"),
            "geodata/liquid-boundaries.gpkg": ("geodata/liquid-boundaries.gpkg", "copy"),
            "geodata/percolation-zone.gpkg": ("geodata/percolation-zone.gpkg", "copy"),
            # The velocity measurements (two FlowTracker campaigns) are NOT part of
            # the example yet: their positions are being corrected. They return here,
            # with the blocks ground_truth and calibration of the case file, when the
            # corrected table is in.
        },
    },
}


def reduce_terrain(source: Path, outline: Path, target: Path, *,
                   margin: float = MARGIN) -> dict:
    """Cut *source* to *outline* plus *margin* and write it compactly to *target*.

    Cells are kept where they are: the grid of the result is the grid of the source,
    so no value is interpolated. Cells farther than *margin* from the outline become
    no-data. Returns what was done, for the report.
    """
    import geopandas as gpd
    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds

    nodata = -9999.0
    with rasterio.open(source) as src:
        shapes = gpd.read_file(outline)
        if src.crs and shapes.crs and shapes.crs != src.crs:
            shapes = shapes.to_crs(src.crs)
        left, bottom, right, top = shapes.total_bounds
        window = from_bounds(left - margin, bottom - margin, right + margin,
                             top + margin, src.transform)
        window = window.round_offsets().round_lengths()
        values = src.read(1, window=window)
        transform = src.window_transform(window)
        keep = ~geometry_mask(list(shapes.geometry.buffer(margin)), values.shape,
                              transform, all_touched=True)
        reduced = np.where(keep, values, nodata).astype("float32")
        profile = {"driver": "GTiff", "height": values.shape[0],
                   "width": values.shape[1], "count": 1, "dtype": "float32",
                   "crs": src.crs, "transform": transform, "nodata": nodata,
                   "tiled": True, "blockxsize": 256, "blockysize": 256,
                   "compress": "deflate", "predictor": 3, "zlevel": 9}
        cells = int(src.width) * int(src.height)
    target.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(target, "w", **profile) as dst:
        dst.write(reduced, 1)
    deviation = float(np.abs(reduced[keep].astype("float64") - values[keep]).max())
    return {"cells": cells, "kept": int(keep.sum()), "deviation": deviation}


def build(name: str, *, say=print) -> None:
    """Write ``cases/<name>/user-sources/`` from the research case of the example."""
    spec = EXAMPLES[name]
    origin = CASES / spec["origin"]
    if not origin.is_dir():
        raise SystemExit(f"{origin} is not there: the data of {name} are built from "
                         "the input folder of its research case")
    target_root = CASES / name / "user-sources"
    for target, (source, how) in spec["files"].items():
        src, dst = origin / source, target_root / target
        dst.parent.mkdir(parents=True, exist_ok=True)
        if how == "terrain":
            done = reduce_terrain(src, origin / spec["outline"], dst)
            say(f"  {target}: {done['kept']:,} of {done['cells']:,} cells, "
                f"{src.stat().st_size / 1e6:.1f} -> {dst.stat().st_size / 1e6:.1f} MB, "
                f"largest change of a value {done['deviation'] * 1000:.3f} mm")
        else:
            shutil.copyfile(src, dst)
            say(f"  {target}: {dst.stat().st_size / 1e3:.0f} kB")


def published(name: str) -> list[str]:
    """The files of the example *name*, relative to its folder, sorted."""
    spec = EXAMPLES[name]
    return sorted([spec["case"], *spec["documents"],
                   *(f"user-sources/{target}" for target in spec["files"])])


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def index() -> dict:
    """The index of the example cases as it follows from the folders."""
    examples = []
    for name, spec in EXAMPLES.items():
        folder = CASES / name
        files = [{"path": relative, "bytes": (folder / relative).stat().st_size,
                  "sha256": checksum(folder / relative)}
                 for relative in published(name)]
        examples.append({"name": name, "title": spec["title"],
                         "summary": spec["summary"], "case": spec["case"],
                         "bytes": sum(item["bytes"] for item in files), "files": files})
    return {"format": 1, "examples": examples}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("names", nargs="*", help="the examples to build (default: all)")
    parser.add_argument("--index", action="store_true",
                        help="write the index only; the data are left as they are")
    parser.add_argument("--check", action="store_true",
                        help="write nothing; exit 1 if the index is out of date")
    args = parser.parse_args(argv)

    if args.check:
        current = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.is_file() else {}
        if current != index():
            print(f"{INDEX.relative_to(REPO)} is out of date; run "
                  "'python scripts/build_example_data.py --index'")
            return 1
        print(f"{INDEX.relative_to(REPO)} is up to date")
        return 0

    if not args.index:
        for name in args.names or list(EXAMPLES):
            print(f"{name}:")
            build(name)
    document = index()
    INDEX.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    for example in document["examples"]:
        print(f"{example['name']}: {len(example['files'])} files, "
              f"{example['bytes'] / 1e6:.1f} MB")
    print(f"wrote {INDEX.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
