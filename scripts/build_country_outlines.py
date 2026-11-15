#!/usr/bin/env python3
"""Write ``src/axqua/survey/outlines.py``: coarse outlines of the countries the survey planner names sources for.

The outlines tell the planner in which country a reach lies when the internet is not asked, so that the report names the coverage map and the correction service of that country. They come from Natural Earth (1:10m Admin 0, public domain), simplified to a few hundred vertices per country, which is exact to about 3 km in Europe and to about 30 km in Canada, China and the United States.

Usage::

    python scripts/build_country_outlines.py [world_map.gpkg]

The default input is the world map that QGIS installs (layer ``countries`` with the field ``iso_a2``), which is Natural Earth data.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "src" / "axqua" / "survey" / "outlines.py"
QGIS_WORLD_MAP = Path("/usr/share/qgis/resources/data/world_map.gpkg")

#: Country code: (tolerance of the simplification [degrees], smallest part kept [square degrees]).
COUNTRIES = {
    "de": (0.03, 0.01), "ch": (0.03, 0.01), "fr": (0.03, 0.01), "at": (0.03, 0.01), "it": (0.03, 0.01), "nl": (0.03, 0.01),
    "cz": (0.03, 0.01), "dk": (0.03, 0.01), "se": (0.03, 0.01), "ca": (0.3, 1.0), "cn": (0.3, 1.0), "us": (0.3, 1.0),
}

HEADER = '''"""Coarse outlines of the countries the survey planner names sources for. Written by ``scripts/build_country_outlines.py``; do not edit.

Made with Natural Earth (public domain, naturalearthdata.com), 1:10m Admin 0, simplified. Each ring is a flat list ``lon, lat, lon, lat, ...`` in WGS 84; the outer rings only.
"""

OUTLINES = {
'''


def main(argv: list[str]) -> int:
    import geopandas as gpd

    source = Path(argv[1]) if len(argv) > 1 else QGIS_WORLD_MAP
    countries = gpd.read_file(source, layer="countries")
    code_field = next(name for name in countries.columns if name.lower() == "iso_a2")
    lines, vertices = [HEADER], 0
    for code, (tolerance, smallest) in COUNTRIES.items():
        shape = countries[countries[code_field].str.lower() == code].geometry.union_all().simplify(tolerance)
        parts = sorted((p for p in getattr(shape, "geoms", [shape]) if p.area >= smallest), key=lambda p: -p.area)
        lines.append(f'    "{code}": [\n')
        for part in parts:
            ring = [f"{value:.2f}" for xy in part.exterior.coords for value in xy]
            vertices += len(ring) // 2
            lines.append(f"        [{', '.join(ring)}],\n")
        lines.append("    ],\n")
    lines.append("}\n")
    TARGET.write_text("".join(lines))
    print(f"{TARGET.relative_to(REPO)}: {len(COUNTRIES)} countries, {vertices} vertices, {TARGET.stat().st_size / 1024:.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
