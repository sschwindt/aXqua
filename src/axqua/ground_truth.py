"""Ground-truth ingestion: field measurements -> tidy calibration tables.

Calibration ground truth comes in two halves that live in separate places and
are joined here:

* **positions** - a point layer (shapefile/GeoPackage) giving where each
  measurement was taken, in *some* CRS (often not the project CRS); reprojected
  to the project CRS on ingest.
* **values** - the measured quantities, in a source-specific export (e.g. a
  SonTek FlowTracker2 ``.ft.sum`` workbook).

Every source is normalised to the same **tidy** schema: the first three columns
are ``x, y, z`` (project CRS, metres), followed by quantity columns. For
FlowTracker hydraulics these are ``u, v, w`` (velocity components, m/s),
``u_err, v_err, w_err`` (per-component error, m/s) and ``h`` (water depth, m).

FlowTracker is only *one* possible source; the tidy schema is generic so other
ground-truth (e.g. ADCP, hand-held probes, sediment samples) can be added as
further adapters without touching the calibration stage that consumes the tidy
tables.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import logging

import numpy as np
import pandas as pd

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


# --------------------------------------------------------------------------- #
# Robust .xlsx reader (bypasses openpyxl, which chokes on the FlowTracker and
# GSD workbooks: "PatternFill.__init__() got an unexpected keyword 'extLst'").
# We read the worksheet XML and shared strings straight out of the zip.
# --------------------------------------------------------------------------- #
def _col_index(ref: str) -> int:
    """Spreadsheet cell ref (e.g. ``AB12``) -> 0-based column index."""
    letters = re.match(r"([A-Z]+)", ref).group(1)
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def read_xlsx_sheet(path: Path, sheet: int | str = 0) -> pd.DataFrame:
    """Read one worksheet into a raw, header-less :class:`~pandas.DataFrame`.

    Cells keep their string form except plain numbers, which are coerced to
    float. Robust to the styling that defeats ``pandas.read_excel``/openpyxl.
    """
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(_NS + "t"))
                      for si in sst.iter(_NS + "si")]

        wb = ET.fromstring(z.read("xl/workbook.xml"))
        names = [s.get("name") for s in wb.iter(_NS + "sheet")]
        if isinstance(sheet, str):
            sheet_no = names.index(sheet) + 1
        else:
            sheet_no = sheet + 1
        sheet_xml = f"xl/worksheets/sheet{sheet_no}.xml"
        if sheet_xml not in z.namelist():
            members = sorted(p for p in z.namelist()
                             if re.match(r"xl/worksheets/sheet\d+\.xml", p))
            sheet_xml = members[sheet if isinstance(sheet, int) else 0]
        ws = ET.fromstring(z.read(sheet_xml))

    rows: list[dict[int, object]] = []
    for r in ws.iter(_NS + "row"):
        cells: dict[int, object] = {}
        for c in r.findall(_NS + "c"):
            if c.get("t") == "inlineStr":     # openpyxl writes strings inline
                inline = c.find(_NS + "is")
                if inline is not None:
                    cells[_col_index(c.get("r"))] = "".join(
                        t.text or "" for t in inline.iter(_NS + "t"))
                continue
            v = c.find(_NS + "v")
            if v is None or v.text is None:
                continue
            if c.get("t") == "s":
                val: object = shared[int(v.text)]
            else:
                try:
                    val = float(v.text)
                except ValueError:
                    val = v.text
            cells[_col_index(c.get("r"))] = val
        rows.append(cells)

    if not rows:
        return pd.DataFrame()
    ncol = max((max(c) for c in rows if c), default=-1) + 1
    data = [[row.get(i) for i in range(ncol)] for row in rows]
    return pd.DataFrame(data)


# --------------------------------------------------------------------------- #
# FlowTracker2 adapter
# --------------------------------------------------------------------------- #
# header label in the .ft.sum sheet  ->  tidy column name
_FT_VALUE_COLUMNS = {
    "VelX": "u", "VelY": "v", "VelZ": "w",
    "VxErr": "u_err", "VyErr": "v_err", "VzErr": "w_err",
    # FinalD is the TOTAL depth of the vertical; MeasD is how far below the
    # SURFACE the probe sat. Both are needed to know where in the water column
    # the measurement is - see ground_truth.relative_height.
    "FinalD": "h", "MeasD": "h_meas", "% Depth": "pct_depth",
}


#: Column aliases used by per-reading PROFILE sheets, whose headers are suffixed
#: ``raw`` so that the summary-sheet readers do not pick them up by accident.
_FT_PROFILE_COLUMNS = {
    "v(x) raw [m/s]": "u", "v(y) raw [m/s]": "v", "v(z) raw [m/s]": "w",
    "v_err(x) raw [m/s]": "u_err", "v_err(y) raw [m/s]": "v_err",
    "v_err(z) raw [m/s]": "w_err",
    "Total Depth raw [m]": "h", "Meas. Depth raw [m]": "h_meas",
    "% Depth": "pct_depth",
    "u' raw [m/s]": "u_std", "v' raw [m/s]": "v_std", "w' raw [m/s]": "w_std",
    "TKE raw [m2/s2]": "tke",
}


def read_flowtracker_values(xlsx: Path, sheet: str | int | None = None
                            ) -> pd.DataFrame:
    """Read a SonTek FlowTracker2 ``.ft.sum`` workbook -> per-vertical values.

    Returns columns ``ID, u, v, w, u_err, v_err, w_err, h`` (one row per
    measurement vertical). Coordinates are *not* here - they come from the
    paired DGPS position layer (see :func:`read_flowtracker`).

    *sheet* names a worksheet to read instead of the first. A **profile** sheet
    (several readings per vertical, headers suffixed ``raw``) is detected by its
    own column names and returns one row per *reading*, keyed by the same ``ID``
    - so a vertical appears several times and the caller must apply
    :func:`select_profile_rows`.
    """
    if sheet is not None:
        return _read_flowtracker_profile(Path(xlsx), sheet)
    raw = read_xlsx_sheet(Path(xlsx))
    # the header row is the one whose first cell is "ID"; units row follows it.
    header_idx = next(i for i in range(len(raw))
                      if str(raw.iloc[i, 0]).strip() == "ID")
    header = [str(h).strip() if h is not None else "" for h in raw.iloc[header_idx]]
    data = raw.iloc[header_idx + 2:].copy()       # skip header and units rows
    data.columns = header
    data = data[data["ID"].notna()]

    out = pd.DataFrame()
    out["ID"] = pd.to_numeric(data["ID"], errors="coerce").astype("Int64")
    for src, dst in _FT_VALUE_COLUMNS.items():
        if src in data.columns:
            out[dst] = pd.to_numeric(data[src], errors="coerce")
    return out.dropna(subset=["ID"]).reset_index(drop=True)


def _read_flowtracker_profile(xlsx: Path, sheet: str | int) -> pd.DataFrame:
    """Read a per-reading PROFILE worksheet -> one row per measurement reading.

    Such a sheet carries a title row, then the header, then data - and repeats
    the ``ID`` once per reading. A ``Site`` column, when present, may hold several
    reaches in one workbook; all are returned and the caller filters.
    """
    raw = read_xlsx_sheet(Path(xlsx), sheet)
    header_idx = next(
        (i for i in range(min(len(raw), 10))
         if any(str(c).strip() == "ID" for c in raw.iloc[i])), 0)
    header = [str(h).strip() if h is not None else "" for h in raw.iloc[header_idx]]
    data = raw.iloc[header_idx + 1:].copy()
    data.columns = header
    data = data[data["ID"].notna()]

    out = pd.DataFrame(index=data.index)
    out["ID"] = pd.to_numeric(data["ID"], errors="coerce").astype("Int64")
    for src, dst in _FT_PROFILE_COLUMNS.items():
        if src in data.columns:
            out[dst] = pd.to_numeric(data[src], errors="coerce")
    for passthrough in ("Site", "Point", "Station"):
        if passthrough in data.columns:
            out[passthrough.lower()] = data[passthrough].astype(str)
    return out.dropna(subset=["ID"]).reset_index(drop=True)


def read_flowtracker(xlsx: Path, positions: Path, crs_epsg: int,
                     join_key: str = "ID", *,
                     sheet: str | int | None = None,
                     profile: str = "single",
                     group_key: str | None = None) -> pd.DataFrame:
    """Join FlowTracker values to their DGPS positions -> tidy hydraulics table.

    Parameters
    ----------
    xlsx : Path
        the ``.ft.sum`` export with the measured velocities/depths.
    positions : Path
        point layer (shp/gpkg) of the survey points, with a matching
        ``join_key`` column; reprojected to ``crs_epsg`` for the x/y output.
    crs_epsg : int
        project CRS the output coordinates are expressed in.

    Returns
    -------
    pandas.DataFrame
        the tidy schema ``x, y, z`` then ``u, v, w, u_err, v_err, w_err, h``.
    """
    import geopandas as gpd

    values = read_flowtracker_values(Path(xlsx), sheet)

    pts = gpd.read_file(Path(positions))
    if pts.crs is not None and pts.crs.to_epsg() != crs_epsg:
        pts = pts.to_crs(epsg=crs_epsg)
    pcols = {c.lower(): c for c in pts.columns}
    key = pcols.get(join_key.lower())
    if key is None:
        raise ValueError(
            f"position layer {Path(positions).name!r} has no '{join_key}' column "
            f"to join on (has {[c for c in pts.columns if c != 'geometry']})"
        )
    pos = pd.DataFrame({
        "ID": pd.to_numeric(pts[key], errors="coerce").astype("Int64"),
        "x": pts.geometry.x.to_numpy(),
        "y": pts.geometry.y.to_numpy(),
    })
    zcol = pcols.get("z")
    pos["z"] = pd.to_numeric(pts[zcol], errors="coerce") if zcol else 0.0

    # A profile workbook may cover several reaches in one sheet (KB15 and KB08
    # share FT_TKE_Summary.xlsx). Drop whole SITES that this position layer knows
    # nothing about, but keep the unmatched-ID guard strict within the sites it
    # does know - that distinguishes "this workbook covers another reach" from
    # "the join key is wrong", which is what the guard is actually for.
    if "site" in values.columns and values["site"].notna().any():
        known = set(pos["ID"].dropna())
        mine = {s for s, g in values.groupby("site")
                if set(g["ID"].dropna()) & known}
        dropped = sorted(set(values["site"].dropna()) - mine)
        if dropped and mine:
            log.info("profile workbook also covers site(s) %s, which %s has no "
                     "positions for - not compiled here",
                     ", ".join(map(str, dropped)), Path(positions).name)
            values = values[values["site"].isin(mine)].reset_index(drop=True)
    values = values.drop(columns=[c for c in ("site", "point", "station")
                                 if c in values.columns])

    # Select AFTER the site filter, never before: the rules group by ID, and two
    # reaches in one workbook may reuse an ID. Selecting first would merge two
    # different verticals into one group and silently average across reaches.
    values = select_profile_rows(values, profile, group_key=group_key or join_key)

    # one_to_many, not many_to_many: a vertical may contribute several readings,
    # but each must still resolve to exactly ONE position. Relaxing the left side
    # too would let a duplicated position silently multiply the targets.
    merged = pos.merge(values, on="ID", how="inner",
                       validate="one_to_one" if profile == "single"
                       else "one_to_many")
    if len(merged) < len(values):
        missing = sorted(set(values["ID"].dropna()) - set(pos["ID"].dropna()))
        raise ValueError(
            f"{len(values) - len(merged)} FlowTracker vertical(s) had no matching "
            f"position in {Path(positions).name!r} (unmatched IDs: {missing})"
        )

    lead = ["x", "y", "z"]
    rest = [c for c in merged.columns if c not in (*lead, "ID")]
    return merged[[*lead, *rest]].reset_index(drop=True)


def scalar_velocity(df: pd.DataFrame) -> pd.Series:
    """Depth-averaged scalar velocity magnitude from the components present."""
    comps = [df[c] for c in ("u", "v", "w") if c in df.columns]
    return np.sqrt(sum(c.astype(float) ** 2 for c in comps))


# --------------------------------------------------------------------------- #
# Canonical tidy table: one sheet per category, columns ``x, y, z`` then
# quantities. Column headers are normalised to canonical names where known;
# unrecognised columns pass through unchanged so arbitrary quantities work.
# --------------------------------------------------------------------------- #
log = logging.getLogger("axqua")

COORD_COLUMNS = ("x", "y", "z")

_COLUMN_ALIASES = {
    "x": "x", "easting": "x", "ostwert": "x", "e": "x",
    "y": "y", "northing": "y", "nordwert": "y", "n": "y",
    "z": "z", "elevation": "z", "bed": "z", "bed_level": "z",
    "u": "u", "vx": "u", "velx": "u", "u_x": "u", "ux": "u",
    "v": "v", "vy": "v", "vely": "v", "u_y": "v", "uy": "v",
    "w": "w", "vz": "w", "velz": "w", "u_z": "w", "uz": "w",
    "u_err": "u_err", "u'": "u_err", "uerr": "u_err", "vxerr": "u_err",
    "v_err": "v_err", "v'": "v_err", "verr": "v_err", "vyerr": "v_err",
    "w_err": "w_err", "w'": "w_err", "werr": "w_err", "vzerr": "w_err",
    # TOTAL water depth of the vertical.
    "h": "h", "depth": "h", "water_depth": "h", "waterdepth": "h",
    "finald": "h", "total depth": "h", "totald": "h",
    # DEPTH OF THE MEASUREMENT ITSELF, measured DOWN FROM THE SURFACE - which is
    # what a FlowTracker records. It is NOT the column depth: mapping both
    # `measd` and `finald` to "h" produced two columns of that name, so df["h"]
    # silently became a DataFrame. The two are what let the measurement's height
    # above the bed be recovered: height = h - h_meas = (1 - h_meas/h) * h.
    "measd": "h_meas", "meas. depth": "h_meas", "meas depth": "h_meas",
    "measured_depth": "h_meas",
    # the same quantity as a FRACTION of the column, again from the surface
    "% depth": "pct_depth", "pct depth": "pct_depth", "pct_depth": "pct_depth",
}


def _canonical_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={c: _COLUMN_ALIASES.get(str(c).strip().lower(),
                                                    str(c).strip().lower())
                            for c in df.columns})
    lead = [c for c in COORD_COLUMNS if c in df.columns]
    rest = [c for c in df.columns if c not in COORD_COLUMNS]
    return df[[*lead, *rest]]


def read_points(path: Path, crs_epsg: int) -> pd.DataFrame:
    """Read a point layer/CSV that already carries coords + quantities -> tidy.

    A spatial layer (shp/gpkg/geojson) is reprojected to ``crs_epsg`` and its
    geometry becomes ``x, y``; a CSV must provide ``x``/``y`` columns (or
    aliases). All other columns pass through (canonicalised where recognised).
    """
    path = Path(path)
    if path.suffix.lower() in (".shp", ".gpkg", ".geojson"):
        import geopandas as gpd

        gdf = gpd.read_file(path)
        if gdf.crs is not None and gdf.crs.to_epsg() != crs_epsg:
            gdf = gdf.to_crs(epsg=crs_epsg)
        df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
        df = _canonical_columns(df)
        df["x"] = gdf.geometry.x.to_numpy()       # geometry wins over any x/y attrs
        df["y"] = gdf.geometry.y.to_numpy()
    else:
        df = _canonical_columns(read_xlsx_sheet(path) if path.suffix.lower() in
                                (".xlsx", ".xlsm") else pd.read_csv(path))
    if "z" not in df.columns:
        df["z"] = 0.0
    lead = [c for c in COORD_COLUMNS if c in df.columns]
    return df[[*lead, *[c for c in df.columns if c not in COORD_COLUMNS]]]


def _compile_source(src, crs_epsg: int) -> pd.DataFrame:
    """Run one :class:`~axqua.config.GroundTruthSource` -> tidy DataFrame."""
    if src.kind == "flowtracker":
        return read_flowtracker(src.values, src.positions, crs_epsg, src.join_key,
                                sheet=getattr(src, "sheet", None),
                                profile=getattr(src, "profile", "single"),
                                group_key=getattr(src, "group_key", None))
    if src.kind == "points":
        layer = src.positions or src.values
        return read_points(layer, crs_epsg)
    raise ValueError(f"unknown ground_truth source kind: {src.kind!r}")


def compile_ground_truth(cfg) -> Path | None:
    """Compile the configured raw sources into the tidy multi-tab table.

    Sources sharing a ``category`` are concatenated into one sheet; a filled
    calibration-target template (``ground_truth.targets``) contributes its
    ``hydraulics`` / ``morphodynamics`` tabs the same way. Returns the written
    path (``cfg.ground_truth_path``), or ``None`` when neither is configured
    (the user supplies the tidy table directly).
    """
    tables: dict[str, list[pd.DataFrame]] = {}
    for src in cfg.ground_truth.sources:
        tables.setdefault(src.category, []).append(_compile_source(src, cfg.crs_epsg))
    if cfg.ground_truth.targets is not None:
        from axqua import targets as targets_mod

        for category, df in targets_mod.read_targets(cfg).items():
            tables.setdefault(category, []).append(df)
    if not tables:
        return None
    merged = {cat: pd.concat(parts, ignore_index=True) for cat, parts in tables.items()}
    out = cfg.ground_truth_path
    write_tidy(merged, out)

    # Sanity-check the elevations, LOG ONLY. This must never raise: pipeline stage 5
    # wraps the whole HydroBayesCal setup in a blanket `except Exception`, so a raise
    # here would turn a warning into a silent skip of the entire calibration setup -
    # a louder defect than the one being guarded. The calibration entry points call
    # the same check with strict=True, where refusing is the right response.
    try:
        from axqua.ground_truth_qa import check_ground_truth_elevations, report
        report(check_ground_truth_elevations(cfg, tables=merged))
    except Exception as exc:                              # noqa: BLE001
        log.debug("elevation check skipped: %s: %s", type(exc).__name__, exc)
    return out


def read_tidy(path: Path) -> dict[str, pd.DataFrame]:
    """Read the tidy multi-tab ground-truth table -> ``{category: DataFrame}``.

    Each sheet/file becomes one category; columns are canonicalised and any
    quantity columns absent from a given dataset are simply not present
    (callers must tolerate missing quantities).
    """
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        sheets = pd.read_excel(path, sheet_name=None)
        return {name: _canonical_columns(df) for name, df in sheets.items()}
    # a single-table CSV -> one category named after the file stem
    return {path.stem: _canonical_columns(pd.read_csv(path))}


def write_tidy(tables: dict[str, pd.DataFrame], path: Path) -> Path:
    """Write ``{category: DataFrame}`` to a multi-tab xlsx (one sheet per category)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for category, df in tables.items():
            _canonical_columns(df).to_excel(writer, sheet_name=category[:31], index=False)
    return path


# --------------------------------------------------------------------------- #
# Where in the water column a measurement sits
# --------------------------------------------------------------------------- #
#: Height above the bed, as a fraction of the column, of the conventional
#: "0.6 depth" point. **0.6 is measured DOWN FROM THE SURFACE** (that is the
#: FlowTracker/USGS convention), so the probe sits at 0.4 of the depth ABOVE the
#: bed. Getting this backwards misplaces every target by 0.2*h.
DEFAULT_RELATIVE_HEIGHT = 0.4


def relative_height(df: pd.DataFrame, *,
                    default: float = DEFAULT_RELATIVE_HEIGHT
                    ) -> tuple[pd.Series, str]:
    """Height above the bed as a fraction of the column, per measurement row.

    Returns ``(f, source)``; *source* names which rule fired, so a placement
    audit can say where every target's position came from rather than leaving it
    implicit. First hit wins:

    1. an explicit ``f`` column;
    2. ``h_meas`` and ``h`` -> ``1 - h_meas/h`` (FlowTracker ``MeasD``/``FinalD``);
    3. ``pct_depth`` -> ``1 - pct_depth`` (``% Depth``, also from the surface);
    4. a ``label`` ending ``-<p>h`` (the ``q47-3-1501-0.93h`` convention);
    5. *default*, with one warning naming how many rows took it.
    """
    n = len(df)
    if "f" in df.columns:
        return pd.to_numeric(df["f"], errors="coerce").fillna(default), "f column"

    if "h_meas" in df.columns and "h" in df.columns:
        h = pd.to_numeric(df["h"], errors="coerce")
        hm = pd.to_numeric(df["h_meas"], errors="coerce")
        f = 1.0 - (hm / h.where(h > 0))
        if f.notna().any():
            return f.fillna(default), "1 - MeasD/FinalD"

    if "pct_depth" in df.columns:
        pct = pd.to_numeric(df["pct_depth"], errors="coerce")
        # tolerate a percentage written as 60 rather than 0.60
        if pct.dropna().gt(1.5).any():
            pct = pct / 100.0
        if pct.notna().any():
            return (1.0 - pct).fillna(default), "1 - % Depth"

    if "label" in df.columns:
        got = df["label"].astype(str).str.extract(r"-([0-9.]+)h$")[0]
        f = 1.0 - pd.to_numeric(got, errors="coerce")
        if f.notna().any():
            return f.fillna(default), "label -<p>h suffix"

    log.warning(
        "no MeasD/%%Depth/label in the ground truth, so the measurement height "
        "is unknown for all %d point(s); assuming %.2f of the depth above the "
        "bed (the 0.6-depth convention, which is measured from the SURFACE). "
        "Carry MeasD + FinalD to place targets from the data instead.", n, default)
    return pd.Series(np.full(n, float(default)), index=df.index), f"default {default}"


# --------------------------------------------------------------------------- #
# Which reading of a multi-depth vertical becomes a calibration target
# --------------------------------------------------------------------------- #
#: The USGS three-point rule samples at these depths BELOW THE SURFACE.
_USGS_DEPTHS = (0.2, 0.6, 0.8)

#: Weights of the USGS three-point rule, in the order of :data:`_USGS_DEPTHS`.
_USGS_WEIGHTS = (1.0, 2.0, 1.0)

PROFILE_RULES = ("single", "all", "drop-lowest", "depth-average")


def _height_above_bed(df: pd.DataFrame) -> pd.Series:
    """Height above the bed as a fraction of the column, for ordering readings.

    Shares :func:`relative_height`'s rules so that ordering and *placement* can
    never disagree about where a reading sits - which is the whole reason the
    lowest reading is identified by height rather than by row order.
    """
    f, _ = relative_height(df)
    return pd.to_numeric(f, errors="coerce")


def select_profile_rows(df: pd.DataFrame, rule: str = "single", *,
                        group_key: str = "ID") -> pd.DataFrame:
    """Reduce a multi-depth profile table to the rows that become targets.

    A FlowTracker vertical may carry several readings at different depths. Which
    of them a calibration should see is a modelling decision, not a data
    property, so it is named explicitly:

    ``single``
        one row per vertical - the reading nearest the conventional 0.6 depth.
        **The default, and a no-op when there is already one row per vertical**,
        so every existing config keeps its present behaviour.
    ``all``
        every reading, one target each.
    ``drop-lowest``
        every reading **except the one nearest the bed**, and only where there is
        more than one - a lone reading is kept. The near-bed reading sits inside
        the grain roughness on a coarse bed (KB15: 0.055 m above a bed with
        ks = 0.089 m), where a wall function returns a boundary condition rather
        than a result.
    ``depth-average``
        one row per vertical carrying the **USGS three-point** depth-averaged
        velocity ``(u_0.2 + 2*u_0.6 + u_0.8) / 4`` where three readings allow it,
        else the plain mean. This is the quantity a depth-averaged 2D model
        actually predicts.

    The lowest reading is found by **height above the bed**, never by row order:
    the readings of one vertical arrive in whatever order they were entered
    (KB15 vertical 1501 reads 0.926, 0.540, 0.292 of the depth from the surface),
    and the source workbook has a column literally called ``entry order fixed``.
    """
    if rule not in PROFILE_RULES:
        raise ValueError(
            f"unknown ground-truth profile rule {rule!r}; "
            f"expected one of {', '.join(PROFILE_RULES)}")
    if group_key not in df.columns or df.empty:
        return df.reset_index(drop=True)

    work = df.copy()
    work["_f"] = _height_above_bed(work)
    sizes = work.groupby(group_key)[group_key].transform("size")

    if rule == "all":
        out = work
    elif rule == "drop-lowest":
        # rank by height above bed; rank 0 is the reading nearest the bed
        rank = work.groupby(group_key)["_f"].rank(method="first", ascending=True)
        out = work[(sizes == 1) | (rank > 1)]
    elif rule == "single":
        # nearest the 0.6-depth convention == 0.4 of the column above the bed
        order = (work["_f"] - DEFAULT_RELATIVE_HEIGHT).abs()
        keep = order.groupby(work[group_key]).transform("min") == order
        out = work[keep].groupby(group_key, as_index=False).head(1)
    else:                                                  # depth-average
        out = _depth_average(work, group_key)

    dropped = len(df) - len(out)
    if dropped:
        log.info("profile rule %r: %d of %d reading(s) kept (%d dropped) "
                 "across %d vertical(s)", rule, len(out), len(df), dropped,
                 work[group_key].nunique())
    return out.drop(columns="_f", errors="ignore").reset_index(drop=True)


def _depth_average(work: pd.DataFrame, group_key: str) -> pd.DataFrame:
    """Collapse each vertical to one row of depth-averaged velocity components.

    Applies the USGS three-point weights where the vertical carries three
    readings. ``% Depth`` is used directly - it is measured **from the surface**,
    which is the convention the rule itself is written in.

    **The weights are applied to the COMPONENTS, not to the speeds**, and the
    distinction is not cosmetic. A depth-averaged 2D model carries depth-averaged
    *components*: TELEMAC's ``SCALAR VELOCITY`` is ``sqrt(U^2 + V^2)`` built from
    them, i.e. the speed of the mean vector. Averaging the per-reading *speeds*
    instead answers a different question - the mean speed - and by the triangle
    inequality it is **always the larger of the two** whenever the flow direction
    changes over the column.

    Measured on the KB15 September-2025 profiles (20 three-point verticals, median
    direction shear 16 degrees over the column): averaging speeds reads high by a
    median +0.6% but up to **+9.9%**, and the error concentrates in the slow,
    strongly sheared verticals (1501: 73 degrees of shear, +9.9%) - which is
    precisely where the velocity data is weakest and least able to absorb a bias.
    So the comparison is made vector-first.
    """
    comps = [c for c in ("u", "v", "w") if c in work.columns]
    if "pct_depth" in work.columns:
        pct = pd.to_numeric(work["pct_depth"], errors="coerce")
        if pct.dropna().gt(1.5).any():                     # written as 60, not 0.60
            pct = pct / 100.0
    else:
        pct = 1.0 - work["_f"]                             # from the surface

    rows = []
    for key, grp in work.groupby(group_key, sort=False):
        row = grp.iloc[0].copy()
        p = pct.loc[grp.index]
        if len(grp) >= 3 and p.notna().all():
            idx = [(p - d).abs().idxmin() for d in _USGS_DEPTHS]
            weight = sum(_USGS_WEIGHTS)
            for c in comps:
                vals = pd.to_numeric(grp[c], errors="coerce")
                row[c] = sum(w * vals.loc[i]
                             for w, i in zip(_USGS_WEIGHTS, idx)) / weight
        else:
            for c in comps:
                row[c] = pd.to_numeric(grp[c], errors="coerce").mean()
        rows.append(row)
    return pd.DataFrame(rows)
