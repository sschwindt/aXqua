"""Green-Ampt soil parameters: by texture class, per soil zone, and per mesh node.

Green-Ampt needs four soil numbers: the saturated hydraulic conductivity ``Ks``, the
suction head at the wetting front ``psi``, the water content at saturation
``theta_s`` and the water content at the start ``theta_i``. The runoff routine uses
them as ``Ks`` and ``a = psi (theta_s - theta_i)`` per node.

Where no measurements exist, Rawls, Brakensiek and Miller (1983, Journal of Hydraulic
Engineering 109(1), Table 2) give mean values for the eleven USDA texture classes,
which is what :data:`RAWLS_1983` holds, checked against the scan of the paper: total
porosity (used as ``theta_s``), effective porosity (``theta_s`` minus the residual
water content), suction and conductivity. Their conductivity is the Green-Ampt value,
about half the saturated conductivity of a sample, as the paper derives it. Pure silt
has no row (too few samples); it takes the silt loam values.

``theta_i`` is the one number that belongs to the event and not to the soil. It comes
from the soil table, from ``hydrology.initial_water_content`` (for example the
ERA5-Land soil moisture at the start of the event, which the data wizard fetches), or
else from ``hydrology.initial_saturation``: the effective saturation, 0 for a soil at
its residual water content and 1 for a saturated one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("axqua")


@dataclass(frozen=True)
class Texture:
    """One row of Rawls et al. (1983), Table 2 (all horizons)."""

    name: str
    porosity: float              # total porosity, used as theta_s [-]
    effective_porosity: float    # porosity minus residual water content [-]
    suction_cm: float            # wetting-front capillary pressure head [cm]
    ks_cm_h: float               # Green-Ampt hydraulic conductivity [cm/h]

    @property
    def theta_r(self) -> float:
        return self.porosity - self.effective_porosity

    @property
    def ks_mm_h(self) -> float:
        return 10.0 * self.ks_cm_h

    @property
    def suction_m(self) -> float:
        return self.suction_cm / 100.0


RAWLS_1983: dict[str, Texture] = {t.name: t for t in (
    Texture("sand", 0.437, 0.417, 4.95, 11.78),
    Texture("loamy sand", 0.437, 0.401, 6.13, 2.99),
    Texture("sandy loam", 0.453, 0.412, 11.01, 1.09),
    Texture("loam", 0.463, 0.434, 8.89, 0.34),
    Texture("silt loam", 0.501, 0.486, 16.68, 0.65),
    Texture("sandy clay loam", 0.398, 0.330, 21.85, 0.15),
    Texture("clay loam", 0.464, 0.309, 20.88, 0.10),
    Texture("silty clay loam", 0.471, 0.432, 27.30, 0.10),
    Texture("sandy clay", 0.430, 0.321, 23.90, 0.06),
    Texture("silty clay", 0.479, 0.423, 29.22, 0.05),
    Texture("clay", 0.475, 0.385, 31.63, 0.03),
)}

#: Names a texture is also written as, mapped to the row it takes.
ALIASES = {"silt": "silt loam", "sealed": "sealed", "impervious": "sealed"}


def texture(name: str) -> Texture | None:
    """The Rawls row of a texture class, by name (case and separators ignored)."""
    key = " ".join(str(name).lower().replace("_", " ").replace("-", " ").split())
    key = ALIASES.get(key, key)
    return RAWLS_1983.get(key)


def classify_texture(sand, silt, clay) -> np.ndarray:
    """USDA texture class of sand, silt and clay contents in percent (any scale).

    The rules of the USDA soil texture triangle, as the NRCS texture calculator states
    them. The contents are normalized to 100 % first, so SoilGrids' g/kg work as well.
    """
    sand = np.asarray(sand, dtype=float)
    silt = np.asarray(silt, dtype=float)
    clay = np.asarray(clay, dtype=float)
    total = sand + silt + clay
    with np.errstate(invalid="ignore", divide="ignore"):
        sand, silt, clay = (100.0 * v / total for v in (sand, silt, clay))
    out = np.full(sand.shape, "", dtype=object)
    rules = [
        ("sand", silt + 1.5 * clay < 15),
        ("loamy sand", (silt + 1.5 * clay >= 15) & (silt + 2 * clay < 30)),
        ("sandy loam", ((clay >= 7) & (clay < 20) & (sand > 52) & (silt + 2 * clay >= 30))
         | ((clay < 7) & (silt < 50) & (silt + 2 * clay >= 30))),
        ("loam", (clay >= 7) & (clay < 27) & (silt >= 28) & (silt < 50) & (sand <= 52)),
        ("silt loam", ((silt >= 50) & (clay >= 12) & (clay < 27))
         | ((silt >= 50) & (silt < 80) & (clay < 12))),
        ("silt", (silt >= 80) & (clay < 12)),
        ("sandy clay loam", (clay >= 20) & (clay < 35) & (silt < 28) & (sand > 45)),
        ("clay loam", (clay >= 27) & (clay < 40) & (sand > 20) & (sand <= 45)),
        ("silty clay loam", (clay >= 27) & (clay < 40) & (sand <= 20)),
        ("sandy clay", (clay >= 35) & (sand > 45)),
        ("silty clay", (clay >= 40) & (silt >= 40)),
        ("clay", (clay >= 40) & (sand <= 45) & (silt < 40)),
    ]
    for name, mask in rules:
        out[(out == "") & mask & np.isfinite(total) & (total > 0)] = name
    return out


@dataclass
class ZoneSoil:
    """The Green-Ampt parameters of one soil zone."""

    zone_id: int
    ks_mm_h: float
    suction_m: float
    theta_s: float
    theta_i: float
    texture: str = ""

    @property
    def dtheta(self) -> float:
        return max(self.theta_s - self.theta_i, 0.0)

    @property
    def a(self) -> float:
        return self.suction_m * self.dtheta

    def problems(self) -> list[str]:
        out = []
        if not self.ks_mm_h >= 0.0:
            out.append(f"Ks is {self.ks_mm_h!r} mm/h; it cannot be negative")
        if not 0.0 < self.suction_m <= 1.0:
            out.append(f"the suction head is {self.suction_m!r} m; Rawls et al. give "
                       "0.05 to 0.32 m, so a value in m and not in cm is expected")
        if not 0.0 < self.theta_s <= 0.6:
            out.append(f"theta_s is {self.theta_s!r}; a water content is a share "
                       "between 0 and 0.6")
        if not 0.0 <= self.theta_i < self.theta_s:
            out.append(f"theta_i is {self.theta_i!r}; it must be at least 0 and below "
                       f"theta_s ({self.theta_s:g})")
        return out


def _number(value) -> float | None:
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
            if not value:
                return None
        return float(value)
    except (TypeError, ValueError):
        return None


def zone_soil(zone_id: int, *, texture_name: str | None = None, ks_mm_h=None,
              suction_m=None, theta_s=None, theta_i=None,
              initial_water_content: float | None = None,
              initial_saturation: float = 0.3) -> ZoneSoil:
    """Fill what a row does not give from its texture class, then ``theta_i``.

    A sealed zone (``texture: sealed``) has ``Ks = 0`` and takes the rest from loam,
    since without conductivity the other values do nothing.
    """
    name = (texture_name or "").strip()
    sealed = name.lower() in ("sealed", "impervious")
    row = texture("loam") if sealed else (texture(name) if name else None)
    if name and row is None:
        raise ValueError(f"soil zone {zone_id}: unknown texture {name!r}; one of "
                         f"{sorted(RAWLS_1983)} or 'sealed'")
    ks = 0.0 if sealed else _number(ks_mm_h)
    psi = _number(suction_m)
    ts = _number(theta_s)
    ti = _number(theta_i)
    if row is not None:
        ks = row.ks_mm_h if ks is None else ks
        psi = row.suction_m if psi is None else psi
        ts = row.porosity if ts is None else ts
    missing = [n for n, v in (("ks_mm_h", ks), ("suction_m", psi), ("theta_s", ts))
               if v is None]
    if missing:
        raise ValueError(f"soil zone {zone_id}: {', '.join(missing)} not given and no "
                         "texture to take them from")
    if ti is None:
        ti = initial_water_content
    if ti is None:
        residual = row.theta_r if row is not None else 0.0
        ti = residual + float(initial_saturation) * (ts - residual)
    return ZoneSoil(int(zone_id), float(ks), float(psi), float(ts), float(ti),
                    texture=name)


def read_soil_table(path: str | Path, *, initial_water_content: float | None = None,
                    initial_saturation: float = 0.3) -> dict[int, ZoneSoil]:
    """Read ``zone_id, texture, ks_mm_h, suction_m, theta_s, theta_i`` (CSV or Excel).

    Any column but ``zone_id`` may be missing or empty; what a row does not give comes
    from its texture class.
    """
    path = Path(path)
    frame = (pd.read_excel(path) if path.suffix.lower() in (".xlsx", ".xls")
             else pd.read_csv(path, comment="#", sep=None, engine="python"))
    cols = {str(c).lower().strip().replace(" ", "_"): c for c in frame.columns}

    def col(*names):
        return next((cols[n] for n in names if n in cols), None)

    zid = col("zone_id", "zone", "id")
    if zid is None:
        raise ValueError(f"{path.name}: no zone_id column")
    columns = {"texture_name": col("texture", "texture_class", "soil", "class"),
               "ks_mm_h": col("ks_mm_h", "ks", "ksat", "ks_mmh"),
               "suction_m": col("suction_m", "suction", "psi", "psi_m"),
               "theta_s": col("theta_s", "porosity", "thetas"),
               "theta_i": col("theta_i", "thetai", "initial_water_content")}
    out = {}
    for _, row in frame.iterrows():
        ident = _number(row[zid])
        if ident is None:
            continue
        values = {key: (row[c] if c is not None else None) for key, c in columns.items()}
        if isinstance(values["texture_name"], float):
            values["texture_name"] = None
        out[int(ident)] = zone_soil(int(ident), initial_water_content=initial_water_content,
                                    initial_saturation=initial_saturation, **values)
    if not out:
        raise ValueError(f"{path.name}: no soil zones")
    return out


def write_soil_table(path: str | Path, zones: dict[int, ZoneSoil]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([{"zone_id": z.zone_id, "texture": z.texture,
                           "ks_mm_h": round(z.ks_mm_h, 4), "suction_m": round(z.suction_m, 4),
                           "theta_s": round(z.theta_s, 4), "theta_i": round(z.theta_i, 4)}
                          for z in sorted(zones.values(), key=lambda z: z.zone_id)])
    frame.to_csv(path, index=False)
    return path


@dataclass
class NodeSoil:
    """Per-node Green-Ampt fields for the geometry file."""

    ks_mm_h: np.ndarray
    a: np.ndarray
    dtheta: np.ndarray
    zone: np.ndarray
    sealed_nodes: int = 0


def case_zones(cfg) -> dict[int, ZoneSoil]:
    """The soil zones of a case: its table, or one zone of its uniform texture."""
    h = cfg.hydrology
    if h.soil_table is not None:
        return read_soil_table(h.soil_table, initial_water_content=h.initial_water_content,
                               initial_saturation=h.initial_saturation)
    if h.soil_texture:
        return {1: zone_soil(1, texture_name=h.soil_texture,
                             initial_water_content=h.initial_water_content,
                             initial_saturation=h.initial_saturation)}
    raise ValueError("hydrology needs a soil: hydrology.soil_table or "
                     "hydrology.soil_texture")


def node_soil(cfg, x: np.ndarray, y: np.ndarray) -> NodeSoil:
    """Green-Ampt fields per node from the soil zones and the sealed surfaces.

    Without ``soil_zones`` every node takes the one zone of the table (or the uniform
    texture). With them, each node takes the zone of the polygon it lies in, or of the
    nearest polygon just outside; a node under a sealed surface gets ``Ks = 0``.
    """
    import geopandas as gpd

    h = cfg.hydrology
    if h.infiltration == "none":
        n = x.size
        return NodeSoil(np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n, dtype=int),
                        sealed_nodes=n)
    zones = case_zones(cfg)
    points = gpd.GeoDataFrame(geometry=gpd.points_from_xy(x, y),
                              crs=f"EPSG:{cfg.crs_epsg}")
    if h.soil_zones is not None:
        layer = gpd.read_file(h.soil_zones)
        if layer.crs is not None and layer.crs.to_epsg() != cfg.crs_epsg:
            layer = layer.to_crs(epsg=cfg.crs_epsg)
        field = next((c for c in layer.columns
                      if str(c).lower() == str(h.soil_zone_field).lower()), None)
        if field is None:
            raise ValueError(f"soil_zones {Path(h.soil_zones).name!r} has no "
                             f"'{h.soil_zone_field}' column (has {list(layer.columns)})")
        layer = layer[[field, "geometry"]].rename(columns={field: "zone_id"})
        joined = gpd.sjoin_nearest(points, layer, how="left").sort_index()
        ids = joined[~joined.index.duplicated()]["zone_id"].to_numpy()
        ids = np.array([int(v) for v in ids])
        unknown = sorted(set(ids.tolist()) - set(zones))
        if unknown:
            raise ValueError(f"soil zone id(s) {unknown} of "
                             f"{Path(h.soil_zones).name!r} are not in the soil table "
                             f"(has {sorted(zones)})")
    else:
        if len(zones) > 1:
            raise ValueError("the soil table has several zones but hydrology.soil_zones "
                             "is not set, so no node can be told which zone it is in")
        ids = np.full(x.size, next(iter(zones)), dtype=int)
    ks = np.array([zones[i].ks_mm_h for i in ids], dtype=float)
    a = np.array([zones[i].a for i in ids], dtype=float)
    dtheta = np.array([zones[i].dtheta for i in ids], dtype=float)
    if h.sealed_surfaces is not None:
        layer = gpd.read_file(h.sealed_surfaces)
        if layer.crs is not None and layer.crs.to_epsg() != cfg.crs_epsg:
            layer = layer.to_crs(epsg=cfg.crs_epsg)
        inside = gpd.sjoin(points, layer[["geometry"]], how="inner", predicate="within")
        mask = np.zeros(x.size, dtype=bool)
        mask[np.unique(inside.index.to_numpy())] = True
        ks[mask] = 0.0
    return NodeSoil(ks, a, dtheta, ids, sealed_nodes=int((ks == 0.0).sum()))
