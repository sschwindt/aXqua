"""Mesh-convergence (grid-independence) study for the 2D hydraulic case.

Runs the *same* steady simulation on a sequence of progressively finer meshes and
monitors quantities of interest phi (water depth h, scalar velocity U) at a fixed
set of probe points. Convergence is judged on the Grid Convergence Index (GCI)
of the finest grid triplet - with an asymptotic-range check to decide whether
the GCI is trustworthy, else on the finest successive relative change - against
a tolerance (default 5%; Celik et al. 2008 / Roache: GCI ~5-10% is acceptable
engineering accuracy, ~1-2% is high-precision work). This is the formal way to
quantify the spatial-discretization error and demonstrate grid independence.

Notes following standard practice (Celik et al. 2008, ASME J. Fluids Eng. 130):

* Mesh convergence is distinct from iterative (solver) convergence - the latter
  (residual reduction within one run) must be reached first or the comparison is
  meaningless. The runs here use TELEMAC's variable time step so each mesh marches
  at its own CFL-admissible dt (refining Delta x forces a smaller dt).
* Use at least three meshes with a refinement factor **r >= 1.3** between grids
  (:func:`ratio_levels`). Closer-spaced grids push the grid-to-grid solution
  differences into iterative/sampling noise and degenerate the GCI denominator
  ``r**p - 1``, making the observed order p and the GCI unstable.
* Every level (and every candidate extension) gets the physical validity checks
  of :mod:`axqua.mesh_validity` (y+, roughness regime, dx vs. ks, turbulence
  model consistency) - refinement is only meaningful while the closures hold.

The study can **resume** from completed per-level results (each level folder
carries a ``level.json``) and can **extend** the ladder one finer mesh at a time
after showing a runtime estimate + validity check (interactive prompt; in a
non-interactive session it stops and reports instead).

The TELEMAC execution is injected (the ``simulate`` callable), so the report
maths are unit-testable without the solver.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from axqua.core import selafin
from axqua.config import Config

log = logging.getLogger("axqua")

DEFAULT_QUANTITIES = ("WATER DEPTH", "SCALAR VELOCITY")
GCI_SAFETY = 1.25        # Roache factor of safety for >=3 grids
DEFAULT_TOLERANCE = 0.05  # GCI / relative-change goal (Celik et al. 2008: 5-10%)
THEORETICAL_ORDER = 2.0  # TELEMAC's FE scheme is formally <= 2nd order; the
#                          observed p is clamped to this for the GCI so a
#                          noise-driven p (e.g. 7) cannot fake a tiny GCI
ASYMPTOTIC_BAND = (0.8, 1.25)  # GCI ratio band within which the triplet is taken
#                                to be in the asymptotic range (GCI trustworthy)
PROMPT_TIMEOUT_S = 60.0  # unanswered interactive [y/N] prompt -> assume 'yes' after
#                          this, so an unattended study keeps refining (never stalls)


# --------------------------------------------------------------------------- #
# quantity-of-interest extraction from a result SELAFIN
# --------------------------------------------------------------------------- #


def _resolve_field(values: dict[str, np.ndarray], qty: str) -> np.ndarray:
    """Node field for *qty*, deriving it from components/aliases when needed."""
    names = {k.upper(): k for k in values}
    q = qty.upper()
    if q in names:
        return values[names[q]]
    if q == "SCALAR VELOCITY":
        u = next((values[names[n]] for n in ("VELOCITY U", "U") if n in names), None)
        v = next((values[names[n]] for n in ("VELOCITY V", "V") if n in names), None)
        if u is not None and v is not None:
            return np.hypot(u, v)
    if q == "WATER DEPTH" and "FREE SURFACE" in names and "BOTTOM" in names:
        return values[names["FREE SURFACE"]] - values[names["BOTTOM"]]
    raise KeyError(f"quantity {qty!r} not in result (have {list(values)})")


def _interp_to_points(x, y, field, points):
    """Linear interpolation of a node field onto *points*; nearest for outliers.

    Triangulates the nodes anew (Delaunay), which is all that a file without its own
    connectivity allows. A TELEMAC mesh is sampled with :func:`_interp_on_mesh`.
    """
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

    nodes = np.column_stack([x, y])
    out = LinearNDInterpolator(nodes, field)(points)
    nan = np.isnan(out)
    if nan.any():
        out[nan] = NearestNDInterpolator(nodes, field)(points[nan])
    return out


def _mesh_sampler(x, y, ikle, points):
    """A function ``field -> values at points``, linear in the mesh's own triangles and nearest node outside them; None where the coordinates cannot be triangulated.

    The points are located once (triangle and barycentric weights, or the nearest node), so sampling many frames of one mesh costs a weighted sum per frame instead of a new point location: averaging the ~50-100 frames of a 2D run took minutes per mesh before.
    """
    from scipy.spatial import cKDTree

    from axqua.model_column import safe_triangulation

    points = np.asarray(points, dtype=float)
    try:
        tri = safe_triangulation(x, y, ikle)
    except ValueError:
        return None
    px, py = points[:, 0], points[:, 1]
    found = np.asarray(tri.get_trifinder()(px, py), dtype=int)
    inside = found >= 0
    nodes = np.zeros((points.shape[0], 3), dtype=int)
    weights = np.zeros((points.shape[0], 3), dtype=float)
    if inside.any():
        t = tri.triangles[found[inside]]
        xa, ya = tri.x[t[:, 0]], tri.y[t[:, 0]]
        xb, yb = tri.x[t[:, 1]], tri.y[t[:, 1]]
        xc, yc = tri.x[t[:, 2]], tri.y[t[:, 2]]
        det = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
        wa = ((yb - yc) * (px[inside] - xc) + (xc - xb) * (py[inside] - yc)) / det
        wb = ((yc - ya) * (px[inside] - xc) + (xa - xc) * (py[inside] - yc)) / det
        nodes[inside] = t
        weights[inside] = np.column_stack([wa, wb, 1.0 - wa - wb])
    if (~inside).any():
        _, near = cKDTree(np.column_stack([x, y])).query(points[~inside])
        nodes[~inside, 0] = near
        weights[~inside, 0] = 1.0

    def sample(field):
        field = np.asarray(field, dtype=float)
        return (field[nodes] * weights).sum(axis=1)

    return sample


def _interp_on_mesh(x, y, ikle, field, points):
    """Linear interpolation in the mesh's own triangles; nearest node outside them.

    A Delaunay triangulation of the nodes is not the mesh: it connects nodes across a
    bank or a dry island that the mesh keeps apart. Where the coordinates cannot be
    triangulated (the float32 coordinates of a result, see :func:`mesh_coordinates`),
    this falls back to :func:`_interp_to_points`.
    """
    sample = _mesh_sampler(x, y, ikle, points)
    if sample is None:
        return _interp_to_points(x, y, np.asarray(field, dtype=float),
                                 np.asarray(points, dtype=float))
    return sample(field)


def mesh_coordinates(data: dict, geometry=None):
    """``(x, y, ikle)`` to sample *data* on: those of *geometry* where it matches.

    TELEMAC writes a result in single precision, coordinates included, and a float32
    northing near 5,300,000 m resolves only 0.5 m. On the Inn KB15 ladder this set the
    shortest edge of both fine meshes to exactly 0.50 m (they have 0.77 and 0.58 m) and
    moved the mean depth at the measurement points by up to 1.6 %, a fifth of the
    change between two meshes. ``geometry.slf`` is written in double precision, so a
    level is read on it. Without a geometry file, or with one of another node count (a
    merged parallel result can repeat interface nodes), the result's own coordinates
    are used as before.
    """
    if geometry is not None and Path(geometry).exists():
        try:
            geo = selafin.read_slf(geometry, frame=0)
            if geo["x"].size == np.asarray(data["x"]).size:
                return geo["x"], geo["y"], geo["ikle"]
            log.debug("%s has %d nodes, the result %d: using the result's coordinates",
                      Path(geometry).name, geo["x"].size, np.asarray(data["x"]).size)
        except Exception as exc:                      # noqa: BLE001 - fall back
            log.debug("cannot read %s: %s", geometry, exc)
    return data["x"], data["y"], data["ikle"]


def extract_at_points(slf_path, quantities, points, *,
                      geometry=None) -> dict[str, np.ndarray]:
    """Read the last frame of *slf_path* and sample *quantities* at *points*.

    With *geometry* (the level's ``geometry.slf``) the field is interpolated in the
    triangles of that double-precision mesh (:func:`mesh_coordinates`).
    """
    data = selafin.read_slf(slf_path, frame=-1)
    points = np.asarray(points, dtype=float)
    if geometry is None:
        return {q: _interp_to_points(data["x"], data["y"],
                                     _resolve_field(data["values"], q), points)
                for q in quantities}
    x, y, ikle = mesh_coordinates(data, geometry)
    return {q: _interp_on_mesh(x, y, ikle, _resolve_field(data["values"], q), points)
            for q in quantities}


#: share of the simulated time at the end of each run that a level's values are
#: averaged over. With k-epsilon the 2D flow of Inn KB15 oscillates at the inflow
#: (point 1: 0.97 to 1.36 m/s, period ~1,100 s, on the baseline mesh), so a snapshot
#: would mix the phase of the oscillation into the change between meshes.
TIME_WINDOW = 0.5


@dataclass
class TimeAverage:
    """Values at the points averaged over the end of a run, and how much they move."""

    mean: dict[str, np.ndarray]            # {quantity: (n_points,) time mean}
    fluctuation: dict[str, float]          # {quantity: ||std over time|| / ||mean||}
    n_frames: int                          # frames the mean is taken over
    start: float                           # time of the first of them [s]
    end: float                             # time of the last of them [s]


def extract_mean_at_points(slf_path, quantities, points, *, geometry=None,
                           window: float = 0.5) -> TimeAverage:
    """Average *quantities* at *points* over the last *window* of the simulated time.

    A snapshot is enough where the flow is steady. The 3D runs of the vertical study
    are not, near bars and wet edges: on the Inn KB15 reach, 19 of 30 verticals moved
    by 1 to 5 cm in depth and 0.1 to 0.3 m/s in velocity between two outputs 1,000 s
    apart, as much as the change between two layer counts. The mean over the frames of
    the window compares the layer counts, and ``fluctuation`` (the spread over time
    in the norm the relative change uses) says how far a single frame can be off.
    """
    sf = selafin.SelafinFile(slf_path)
    if sf.n_frames == 0:
        raise ValueError(f"{Path(slf_path).name}: no time frames")
    times = np.asarray(sf.times, dtype=float)
    first = times[0] + (1.0 - float(window)) * (times[-1] - times[0])
    chosen = [i for i, t in enumerate(times) if t >= first - 1e-9] or [sf.n_frames - 1]
    points = np.asarray(points, dtype=float)
    base = {"x": sf.x, "y": sf.y, "ikle": sf.ikle}
    x, y, ikle = (mesh_coordinates(base, geometry) if geometry is not None
                  else (sf.x, sf.y, sf.ikle))
    sample = _mesh_sampler(x, y, ikle, points) if geometry is not None else None
    series: dict[str, list[np.ndarray]] = {q: [] for q in quantities}
    for i in chosen:
        values = sf.frame(i)
        for q in quantities:
            field = np.asarray(_resolve_field(values, q), dtype=float)
            series[q].append(sample(field) if sample is not None
                             else _interp_to_points(x, y, field, points))
    mean, spread = {}, {}
    for q, rows in series.items():
        stack = np.vstack(rows)
        mean[q] = stack.mean(axis=0)
        norm = np.linalg.norm(mean[q])
        spread[q] = float(np.linalg.norm(stack.std(axis=0)) / norm) if norm else 0.0
    return TimeAverage(mean=mean, fluctuation=spread, n_frames=len(chosen),
                       start=float(times[chosen[0]]), end=float(times[chosen[-1]]))


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #


@dataclass
class LevelResult:
    """One mesh in the sequence: its resolution, size and sampled QoI."""

    label: str
    cell_size: float                       # representative (channel) edge length [m]
    n_elem: int
    n_nodes: int
    min_edge: float
    dt: float | None                       # CFL-admissible time step estimate [s]
    runtime_s: float | None
    qoi: dict[str, np.ndarray]             # {quantity: (n_probes,) values}
    scale: float = 1.0                     # mesh.size_scale this level was built at
    mean_depth: float | None = None        # mean probe water depth [m]
    mean_velocity: float | None = None     # mean probe scalar velocity [m/s]
    validity: object | None = None         # mesh_validity.MeshValidity (or None)
    #: {quantity: spread over time / mean} at the points, over the averaged frames
    fluctuation: dict = field(default_factory=dict)
    n_frames: int = 1                      # frames the values are averaged over

    def repr_value(self, qty: str) -> float:
        """Representative scalar: mean magnitude over the probes."""
        return float(np.mean(np.abs(self.qoi[qty])))


@dataclass
class ConvergenceReport:
    levels: list[LevelResult]              # ordered coarse -> fine
    quantities: list[str]
    tolerance: float
    probes: np.ndarray
    rel_change: dict[str, list[float]] = field(default_factory=dict)   # per qty, len n-1
    observed_order: dict[str, float | None] = field(default_factory=dict)
    gci_fine: dict[str, float | None] = field(default_factory=dict)
    converged: bool = False
    case: str = ""                          # case name (for the report title)
    order_used: dict[str, float | None] = field(default_factory=dict)  # p clamped for GCI
    gci_med: dict[str, float | None] = field(default_factory=dict)     # medium-grid GCI
    asymptotic_ratio: dict[str, float | None] = field(default_factory=dict)
    verdict_basis: str = "rel_change"       # "gci" | "rel_change"

    def format(self) -> str:
        lv = self.levels
        w = max(12, *(len(x.label) for x in lv))
        cols = "".join(f"{x.label:>{w}}" for x in lv)
        lines = [
            "Mesh-convergence study (coarse -> fine)",
            f"  probes: {len(self.probes)}   tolerance: {self.tolerance * 100:.1f}%",
            f"  {'cell size [m]':<22}" + "".join(f"{x.cell_size:>{w}.3f}" for x in lv),
            f"  {'size scale [-]':<22}" + "".join(f"{x.scale:>{w}.3f}" for x in lv),
            f"  {'elements':<22}" + "".join(f"{x.n_elem:>{w}d}" for x in lv),
            f"  {'nodes':<22}" + "".join(f"{x.n_nodes:>{w}d}" for x in lv),
            f"  {'shortest edge [m]':<22}" + "".join(f"{x.min_edge:>{w}.4f}" for x in lv),
            f"  {'time step dt [s]':<22}"
            + "".join((f"{x.dt:>{w}.4f}" if x.dt else f"{'-':>{w}}") for x in lv),
            f"  {'runtime [s]':<22}"
            + "".join((f"{x.runtime_s:>{w}.1f}" if x.runtime_s else f"{'-':>{w}}")
                      for x in lv),
            "  " + "-" * (22 + w * len(lv)),
        ]
        lines.append(f"  {'quantity phi':<22}" + cols)
        for q in self.quantities:
            row = "".join(f"{x.repr_value(q):>{w}.4f}" for x in lv)
            lines.append(f"  {q:<22}{row}")
            # relative change between successive meshes, aligned under the finer one
            rc = self.rel_change.get(q, [])
            cells = [f"{'':>{w}}"] + [f"{e * 100:>{w - 1}.2f}%" for e in rc]
            lines.append(f"    {'rel. change':<20}" + "".join(cells))
            extra = []
            p, pu = self.observed_order.get(q), self.order_used.get(q)
            if p is not None:
                extra.append(f"observed order p={p:.2f}"
                             + (f" (p_used={pu:.2f})"
                                if pu is not None and abs(pu - p) > 1e-9 else ""))
            if self.gci_fine.get(q) is not None:
                extra.append(f"GCI(fine)={self.gci_fine[q] * 100:.2f}%")
            if self.gci_med.get(q) is not None:
                extra.append(f"GCI(med)={self.gci_med[q] * 100:.2f}%")
            if self.asymptotic_ratio.get(q) is not None:
                extra.append(f"asymptotic ratio={self.asymptotic_ratio[q]:.2f}")
            if extra:
                lines.append(f"    {'':<20}" + "; ".join(extra))
            if any(q in getattr(x, "fluctuation", {}) for x in lv):
                lines.append(f"    {'fluctuation in time':<20}" + "".join(
                    (f"{x.fluctuation[q] * 100:>{w - 1}.2f}%" if q in x.fluctuation
                     else f"{'-':>{w}}") for x in lv))
        frames = sorted({getattr(x, "n_frames", 1) for x in lv})
        if frames != [1]:
            lines.append(f"  values: mean over the last {TIME_WINDOW:.0%} of each run "
                         f"({'/'.join(str(n) for n in frames)} frames)")
        verdict = "CONVERGED" if self.converged else "NOT converged"
        basis = ("GCI of the finest grid triplet" if self.verdict_basis == "gci"
                 else "finest relative change; GCI not trustworthy - asymptotic "
                      f"range check outside {ASYMPTOTIC_BAND[0]}..{ASYMPTOTIC_BAND[1]} "
                      "or order not computable")
        lines.append(f"  => {verdict} at {self.tolerance * 100:.1f}% ({basis})")
        if not self.converged:
            lines.append("     refine further (the finest mesh still changes the QoI "
                         "by more than the tolerance).")
        for q in self.unsteady_quantities():
            lines.append(f"     {q}: the finest change is not larger than the runs' own "
                         "fluctuation in time; a finer mesh cannot settle it.")
        validity = [x for x in lv if x.validity is not None]
        if validity:
            lines.append("  mesh validity (y+ / roughness / turbulence closure):")
            for x in validity:
                vlines = x.validity.lines()
                lines.append(f"    {x.label}: {vlines[0]}")
                lines.extend(f"      {extra_line}" for extra_line in vlines[1:])
        return "\n".join(lines)

    def unsteady_quantities(self) -> list[str]:
        """Quantities whose last change is within the fluctuation in time of the two finest runs."""
        if len(self.levels) < 2:
            return []
        a, b = self.levels[-2], self.levels[-1]
        out = []
        for q in self.quantities:
            rc = self.rel_change.get(q) or []
            spread = max(getattr(a, "fluctuation", {}).get(q, 0.0),
                         getattr(b, "fluctuation", {}).get(q, 0.0))
            if rc and spread and rc[-1] <= spread:
                out.append(q)
        return out

    def save(self, path) -> Path:
        path = Path(path)
        path.write_text(self.format() + "\n")
        return path

    def recommendation(self) -> dict:
        """Recommend the coarsest grid-independent cell size (convergence vs. cost).

        A mesh is adequate when refining it further (to the next finer mesh)
        changes every quantity of interest by less than the tolerance; the
        coarsest such mesh is the cheapest converged choice. Returns a dict with
        the recommended :class:`LevelResult`, its cell size, whether it converged,
        a one-line reason, and the runtime speed-up versus the finest mesh.
        """
        lv = self.levels
        chosen = None
        for i in range(len(lv) - 1):                       # coarse -> fine
            if all(self.rel_change[q][i] < self.tolerance for q in self.quantities):
                chosen = lv[i]
                break
        if chosen is not None:
            converged = True
            reason = (f"coarsest mesh whose water depth and velocity stay within "
                      f"{self.tolerance * 100:.0f}% under refinement (grid-independent "
                      "and cheapest to run)")
        elif self.verdict_basis == "gci" and self.converged and len(lv) >= 2:
            # GCI-converged although no single level passed the per-level scan:
            # the medium grid of the converged fine triplet is the cheapest mesh
            # whose discretization error is bounded (by GCI(med))
            chosen = lv[-2]
            converged = True
            gm = max((self.gci_med.get(q) or 0.0) for q in self.quantities)
            reason = (f"medium grid of the GCI-converged fine triplet "
                      f"(discretization error bounded by GCI(med) = {gm * 100:.2f}%)")
        else:
            chosen = lv[-1]
            converged = False
            reason = (f"no mesh met the {self.tolerance * 100:.0f}% tolerance; using "
                      "the finest (refine further to confirm grid independence)")
        finest = lv[-1]
        speedup = (finest.runtime_s / chosen.runtime_s
                   if chosen.runtime_s and finest.runtime_s else None)
        return {"level": chosen, "cell_size": chosen.cell_size,
                "converged": converged, "reason": reason, "speedup_vs_finest": speedup}

    def to_xlsx(self, path) -> Path:
        """Write a styled .xlsx convergence report with the cell-size recommendation."""
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        path = Path(path)
        lv = self.levels
        head_fill = PatternFill("solid", fgColor="1F4E78")     # dark blue
        head_font = Font(bold=True, color="FFFFFF")
        label_font = Font(bold=True)
        rec_fill = PatternFill("solid", fgColor="C6EFCE")      # green
        warn_fill = PatternFill("solid", fgColor="FFC7CE")     # red
        sub_fill = PatternFill("solid", fgColor="DDEBF7")      # light blue
        center = Alignment(horizontal="center")
        thin = Side(style="thin", color="BFBFBF")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        wb = Workbook()
        ws = wb.active
        ws.title = "mesh convergence"
        ncol = 1 + len(lv)

        def row(cells, *, fill=None, font=None, fmt=None, align=None):
            ws.append(list(cells))
            r = ws.max_row
            for c in range(1, len(cells) + 1):
                cell = ws.cell(row=r, column=c)
                if fill:
                    cell.fill = fill
                if font:
                    cell.font = font
                if fmt and c > 1 and isinstance(cell.value, (int, float)):
                    cell.number_format = fmt
                if align and c > 1:
                    cell.alignment = align
                cell.border = border
            return r

        # title
        title = "Mesh-convergence study"
        if getattr(self, "case", ""):
            title += f" - case '{self.case}'"
        ws.append([title])
        ws.merge_cells(start_row=1, end_row=1, start_column=1, end_column=ncol)
        ws["A1"].font = Font(bold=True, size=14)
        basis = ("GCI-based" if self.verdict_basis == "gci"
                 else "relative-change fallback (GCI not trustworthy)")
        ws.append([f"probes: {len(self.probes)}    tolerance: {self.tolerance * 100:.1f}%    "
                   f"meshes: {len(lv)} (coarse to fine)    verdict: {basis}"])
        ws.merge_cells(start_row=2, end_row=2, start_column=1, end_column=ncol)
        ws.append([])

        row(["", *[x.label for x in lv]], fill=head_fill, font=head_font, align=center)
        row(["cell size [m]", *[x.cell_size for x in lv]], font=label_font,
            fmt="0.000", align=center)
        row(["size scale [-]", *[x.scale for x in lv]], font=label_font,
            fmt="0.000", align=center)
        row(["elements", *[x.n_elem for x in lv]], font=label_font, fmt="#,##0",
            align=center)
        row(["nodes", *[x.n_nodes for x in lv]], font=label_font, fmt="#,##0",
            align=center)
        row(["shortest edge [m]", *[x.min_edge for x in lv]], font=label_font,
            fmt="0.0000", align=center)
        row(["time step dt [s]", *[(x.dt or float('nan')) for x in lv]],
            font=label_font, fmt="0.0000", align=center)
        row(["runtime [s]", *[(x.runtime_s or float('nan')) for x in lv]],
            font=label_font, fmt="0.0", align=center)
        ws.append([])

        # physical validity of the resolution (see axqua.mesh_validity)
        vals = [x.validity for x in lv]
        if any(v is not None for v in vals):
            def vattr(attr, absent="-"):
                out = []
                for v in vals:
                    a = getattr(v, attr, None) if v is not None else None
                    out.append(a if (a is not None
                                     and not (isinstance(a, float) and math.isnan(a)))
                               else absent)
                return out

            row(["mean depth at probes [m]", *vattr("depth")], font=label_font,
                fmt="0.00", align=center)
            row(["mean velocity [m/s]", *vattr("velocity")], font=label_font,
                fmt="0.00", align=center)
            row(["u* (log law) [m/s]", *vattr("u_star")], font=label_font,
                fmt="0.000", align=center)
            r = row(["wall y+", *vattr("y_plus")], font=label_font, fmt="0",
                    align=center)
            for c, v in enumerate(vals, start=2):
                if v is not None and not v.y_plus_ok:
                    ws.cell(row=r, column=c).fill = warn_fill
            r = row(["ks+ (roughness Re)", *vattr("ks_plus")], font=label_font,
                    fmt="0", align=center)
            for c, v in enumerate(vals, start=2):
                if v is not None and v.ks_regime not in ("fully rough", "unknown"):
                    ws.cell(row=r, column=c).fill = warn_fill
            r = row(["cell size / ks [-]", *vattr("dx_over_ks")], font=label_font,
                    fmt="0.00", align=center)
            for c, v in enumerate(vals, start=2):
                if v is not None and v.dx_below_ks:
                    ws.cell(row=r, column=c).fill = warn_fill
            r = row(["turbulence pick (auto)",
                     *[(v.turbulence_pick_name or "-") if v is not None else "-"
                       for v in vals]], font=label_font, align=center)
            for c, v in enumerate(vals, start=2):
                if v is not None and v.regime_change:
                    ws.cell(row=r, column=c).fill = warn_fill
            warned = [w for v in vals if v is not None for w in v.warnings]
            for w in dict.fromkeys(warned):              # unique, order-preserving
                row(["  ! " + w])
            ws.append([])

        for q in self.quantities:
            row([q, *[x.repr_value(q) for x in lv]], fill=sub_fill, font=label_font,
                fmt="0.0000", align=center)
            rc = ["", ""] + [f"{e * 100:.2f}%" for e in self.rel_change.get(q, [])]
            row(["  rel. change vs. coarser", *rc[1:]], align=center)
            if any(q in getattr(x, "fluctuation", {}) for x in lv):
                row(["  fluctuation in time",
                     *[(f"{x.fluctuation[q] * 100:.2f}%" if q in x.fluctuation else "-")
                       for x in lv]], align=center)
            p, pu = self.observed_order.get(q), self.order_used.get(q)
            extra = []
            if p is not None:
                extra.append(f"observed order p = {p:.2f}"
                             + (f" (p_used = {pu:.2f})"
                                if pu is not None and abs(pu - p) > 1e-9 else ""))
            if self.gci_fine.get(q) is not None:
                extra.append(f"GCI(fine) = {self.gci_fine[q] * 100:.2f}%")
            if self.gci_med.get(q) is not None:
                extra.append(f"GCI(med) = {self.gci_med[q] * 100:.2f}%")
            if self.asymptotic_ratio.get(q) is not None:
                extra.append(f"asymptotic ratio = {self.asymptotic_ratio[q]:.2f}")
            if extra:
                row(["  " + "; ".join(extra)])
        ws.append([])

        rec = self.recommendation()
        sp = rec["speedup_vs_finest"]
        sp_txt = f" (~{sp:.1f}x faster than the finest)" if sp and sp > 1 else ""
        r = row([f"RECOMMENDATION: cell size = {rec['cell_size']:.3f} m"
                 + (f", runtime ~{rec['level'].runtime_s:.0f} s{sp_txt}"
                    if rec['level'].runtime_s else "")],
                fill=rec_fill if rec["converged"] else warn_fill, font=label_font)
        ws.merge_cells(start_row=r, end_row=r, start_column=1, end_column=ncol)
        r = row([rec["reason"]])
        ws.merge_cells(start_row=r, end_row=r, start_column=1, end_column=ncol)

        ws.column_dimensions["A"].width = 28
        for c in range(2, ncol + 1):
            ws.column_dimensions[chr(64 + c)].width = 13
        _points_sheet(wb, self, head_fill=head_fill, head_font=head_font,
                      border=border)
        wb.save(path)
        return path


def _points_sheet(wb, report, *, head_fill, head_font, border) -> None:
    """A sheet with every quantity at every probe point on every mesh.

    The verdict is drawn from the mean over the points, and a mean hides how much
    single points move: on the Inn KB15 ladder the mean depth changed by 2 % between
    two meshes while single points changed by more than 20 %.
    """
    from openpyxl.styles import Alignment

    lv = report.levels
    ws = wb.create_sheet("values at the points")
    head = ["point", "x [m]", "y [m]"]
    for q in report.quantities:
        head += [f"{q} [{_unit(q)}] {x.label}" for x in lv]
        if len(lv) > 1:
            head.append(f"{q}: change of the finest [%]")
    ws.append(head)
    for c in range(1, len(head) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font, cell.border = head_fill, head_font, border
        cell.alignment = Alignment(wrap_text=True, horizontal="center")
    probes = np.asarray(report.probes, dtype=float)
    for i in range(len(probes)):
        values = [i + 1, float(probes[i, 0]), float(probes[i, 1])]
        for q in report.quantities:
            column = [float(x.qoi[q][i]) for x in lv]
            values += column
            if len(lv) > 1:
                prev, last = column[-2], column[-1]
                values.append(100.0 * (last - prev) / last if last else float("nan"))
        ws.append(values)
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=ws.max_row, column=c)
            cell.border = border
            if c in (2, 3):
                cell.number_format = "0.00"
            elif c > 3 and isinstance(value, float):
                cell.number_format = "0.000"
    ws.column_dimensions["A"].width = 7
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 13
    ws.row_dimensions[1].height = 45
    ws.freeze_panes = "D2"


def _unit(quantity: str) -> str:
    return {"WATER DEPTH": "m", "SCALAR VELOCITY": "m/s"}.get(quantity.upper(), "-")


#: An ordinal blue ramp, light (coarse) to dark (fine), for the meshes of a study; a
#: ladder longer than the ramp takes evenly spaced steps of it.
_MESH_RAMP = ("#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281", "#0d366b")
_MESH_MARKERS = ("o", "s", "^", "D", "v", "P", "X")


def plot_report(report, path, *, size_name: str = "cell size [m]") -> Path:
    """Draw a study: values at the points, their mean, and the change per refinement.

    One row per quantity. Left: the value at each probe point on each mesh (light =
    coarse, dark = fine), which shows where the meshes disagree. Middle: the mean over
    the points against the cell size, the number the verdict is drawn from. Right: the
    relative change from one mesh to the next against the tolerance; a study that
    converges shows these bars falling below the line.
    """
    import matplotlib

    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt

    path = Path(path)
    lv = list(report.levels)
    quantities = list(report.quantities)
    n = len(lv)
    idx = np.linspace(0, len(_MESH_RAMP) - 1, max(n, 2)).round().astype(int)
    colors = [_MESH_RAMP[i] for i in idx][-n:] if n else []
    ink, muted, grid = "#0b0b0b", "#52514e", "#e4e3df"
    fig, axes = plt.subplots(len(quantities), 3, figsize=(15, 4.2 * len(quantities)),
                             squeeze=False,
                             gridspec_kw={"width_ratios": [2.4, 1.2, 1.2]})
    fig.patch.set_facecolor("#fcfcfb")
    points = np.arange(1, len(report.probes) + 1)
    sizes = np.array([x.cell_size for x in lv], dtype=float)
    for row, q in enumerate(quantities):
        unit = _unit(q)
        a, b, c = axes[row]
        for ax in (a, b, c):
            ax.set_facecolor("#fcfcfb")
            ax.grid(True, color=grid, linewidth=0.8)
            ax.set_axisbelow(True)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            for side in ("left", "bottom"):
                ax.spines[side].set_color(muted)
            ax.tick_params(colors=muted, labelsize=9)
        for k, x in enumerate(lv):
            a.plot(points, x.qoi[q], linestyle="none",
                   marker=_MESH_MARKERS[k % len(_MESH_MARKERS)], markersize=6,
                   markerfacecolor=colors[k], markeredgecolor="#fcfcfb",
                   markeredgewidth=0.8, label=x.label)
        a.set_xlabel("measurement point", color=muted)
        a.set_ylabel(f"{q.lower()} [{unit}]", color=muted)
        a.set_xticks(points)
        a.set_xlim(0.3, len(points) + 0.7)
        a.set_title(f"{q.capitalize()} at each point", loc="left", color=ink,
                    fontsize=11)
        if row == 0:
            a.legend(frameon=False, fontsize=9, ncol=min(n, 4), loc="upper left",
                     bbox_to_anchor=(0.0, 1.02), handletextpad=0.3,
                     labelcolor=muted)
        means = np.array([x.repr_value(q) for x in lv])
        b.plot(sizes, means, color="#2a78d6", linewidth=2, zorder=1)
        for k in range(n):
            b.plot(sizes[k], means[k], marker=_MESH_MARKERS[k % len(_MESH_MARKERS)],
                   markersize=8, color=colors[k], markeredgecolor="#fcfcfb",
                   markeredgewidth=1.5, zorder=2)
        b.invert_xaxis()
        b.set_xlabel(f"{size_name} (finer to the right)", color=muted)
        b.set_ylabel(f"mean over the points [{unit}]", color=muted)
        b.set_title("Mean over the points", loc="left", color=ink, fontsize=11)
        span = means.max() - means.min()
        pad = max(span * 0.6, abs(means.mean()) * 0.05)
        b.set_ylim(means.min() - pad, means.max() + pad)
        changes = np.array(report.rel_change.get(q, []), dtype=float) * 100.0
        if changes.size:
            labels = [f"to\n{lv[k + 1].label}" for k in range(changes.size)]
            bars = c.bar(range(changes.size), changes, width=0.55,
                         color=colors[1:changes.size + 1])
            for bar, value in zip(bars, changes):
                c.annotate(f"{value:.1f} %", (bar.get_x() + bar.get_width() / 2,
                                              bar.get_height()),
                           textcoords="offset points", xytext=(0, 3), ha="center",
                           fontsize=9, color=ink)
            tol = report.tolerance * 100.0
            c.axhline(tol, color=ink, linewidth=1.2, linestyle=(0, (4, 3)))
            c.text(0.98, 0.98, f"dashed line: tolerance {tol:.0f} %",
                   transform=c.transAxes, ha="right", va="top", fontsize=9,
                   color=ink)
            c.set_xticks(range(changes.size), labels, fontsize=8)
            c.set_ylim(0, max(changes.max(), tol) * 1.35)
        c.set_ylabel("change at the points [%]", color=muted)
        c.set_title("Change per refinement", loc="left", color=ink, fontsize=11)
    verdict = "converged" if report.converged else "not converged"
    case = f"{report.case}: " if getattr(report, "case", "") else ""
    kind = getattr(report, "level_name", "meshes")
    fig.suptitle(f"{case}{len(report.probes)} points, {n} {kind}, {verdict} at "
                 f"{report.tolerance * 100:.0f} %", x=0.01, ha="left", color=ink,
                 fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# study README (dataset documentation for external readers/reviewers)
# --------------------------------------------------------------------------- #


_QUANTITY_DESCRIPTIONS = {
    "WATER DEPTH": "the water depth h [m]",
    "SCALAR VELOCITY": ("the depth-averaged flow velocity magnitude "
                        "U = sqrt(u^2 + v^2) [m/s]"),
}


def _readme_levels(base_dir: Path, report: ConvergenceReport | None):
    """``(label, cell_size, n_elem, n_nodes, runtime_s)`` rows, coarse -> fine.

    Taken from the finished *report* when given; otherwise recovered by scanning
    the per-level ``level.json`` files of an existing study folder (so a README
    can be (re)generated for a study run earlier)."""
    if report is not None:
        return [(x.label, x.cell_size, x.n_elem, x.n_nodes, x.runtime_s)
                for x in report.levels]
    rows = []
    for jf in sorted(Path(base_dir).glob("*/level.json")):
        try:
            m = json.loads(jf.read_text())
            rows.append((str(m["label"]), float(m.get("cell_size") or m["channel_size"]),
                         int(m["n_elem"]), int(m["n_nodes"]), m.get("runtime_s")))
        except (KeyError, ValueError, TypeError, OSError) as exc:
            log.debug("README: skipping unreadable %s: %s", jf, exc)
    rows.sort(key=lambda r: -r[1])                     # coarse (large) -> fine (small)
    return rows


def _readme_ladder_sentence(levels) -> str:
    """The reviewer-requested one-liner on what the summary spreadsheet compares."""
    labels = [lbl for lbl, *_ in levels]
    if "baseline" in labels and len(labels) > 1:
        i = labels.index("baseline")
        n_coarser, n_finer = i, len(labels) - i - 1
        parts = []
        if n_coarser:
            parts.append(f"{n_coarser} performed on "
                         + ("a coarser mesh" if n_coarser == 1 else "coarser meshes"))
        if n_finer:
            parts.append(f"{n_finer} performed on successively refined meshes")
        return (f"which lists the results of the baseline simulation and compares "
                f"them with {len(labels) - 1} additional simulations: "
                + " and ".join(parts) + ".")
    return (f"which lists and compares {len(labels)} simulations performed on "
            "successively refined meshes.")


def write_readme(base_dir, *, report: ConvergenceReport | None = None,
                 case: str = "", tolerance: float | None = None,
                 quantities=None, n_probes: int | None = None,
                 turbulence: str | None = None,
                 extra_entries: dict[str, str] | None = None) -> Path:
    """Write a ``README.md`` documenting the study folder for external readers.

    Explains that ``mesh-convergence.xlsx`` is the central summary file and what it
    contains, the governing equations and quantities examined, and the purpose of
    every file in each per-level model folder. The text is deliberately
    software-agnostic (it names TELEMAC-2D, not the build tooling).

    Normally called with the finished *report* at the end of a study; with
    ``report=None`` the per-level folders are scanned instead (``level.json``),
    so a README can be regenerated for an already-completed study. *extra_entries*
    maps additional file/folder names to one-line descriptions appended to the
    directory-contents section.
    """
    base_dir = Path(base_dir)
    levels = _readme_levels(base_dir, report)
    if report is not None:
        case = case or getattr(report, "case", "")
        tolerance = report.tolerance if tolerance is None else tolerance
        quantities = quantities or report.quantities
        n_probes = len(report.probes) if n_probes is None else n_probes
        if turbulence is None:
            turbulence = next((x.validity.turbulence_pick_name for x in report.levels
                               if x.validity is not None
                               and getattr(x.validity, "turbulence_pick_name", None)),
                              None)
    tolerance = DEFAULT_TOLERANCE if tolerance is None else tolerance
    quantities = list(quantities or DEFAULT_QUANTITIES)

    qty_txt = " and ".join(_QUANTITY_DESCRIPTIONS.get(q.upper(), q.lower())
                           for q in quantities)
    probes_txt = (f"{n_probes} fixed probe points" if n_probes
                  else "a fixed set of probe points")
    turb_txt = (f"using the {turbulence} turbulence closure, held fixed across all "
                "meshes," if turbulence
                else "with the turbulence closure held fixed across all meshes")

    # one logical line per paragraph/bullet - no hard wrapping (readers soft-wrap)
    title = ("# Mesh convergence study"
             + (f" (Telemac case name: '{case}')" if case else ""))
    lines = [
        title,
        "",
        "This folder documents a grid-independence (mesh convergence) study for a "
        "TELEMAC-2D model simulating fluvial hydraulics in two-dimensions based on "
        "the shallow-water approximation. The same steady-flow simulation was "
        "repeated on a series of computational meshes with increasing resolution to "
        "quantify the spatial discretization error and identify the coarsest mesh "
        "yielding grid-independent results.",
        "",
        "## Summary: mesh-convergence.xlsx",
        "",
        "The outcome of the mesh-convergence study is summarized in "
        "**`mesh-convergence.xlsx`** (start there), " + _readme_ladder_sentence(levels)
        + " For every mesh, the spreadsheet lists:",
        "",
        "- mesh metrics: representative cell size [m], number of elements and nodes, "
        "shortest edge [m], admissible time step [s], and solver runtime [s];",
        "- physical validity checks of the resolution: mean water depth and flow "
        "velocity at the probe points, shear velocity u*, wall y+, roughness "
        "Reynolds number ks+, the ratio of cell size to roughness height ks, and "
        "the consistency of the turbulence closure across resolutions;",
        "- the quantities of interest (see below), their relative change between "
        "successive meshes, the observed order of convergence p, and the Grid "
        "Convergence Index (GCI) of the finest grid triplet;",
        "- the verdict and a recommended cell size (bottom rows), balancing accuracy "
        "against computational cost.",
        "",
    ]
    if report is not None or (base_dir / "mesh-convergence.txt").exists():
        lines += ["`mesh-convergence.txt` is a plain-text rendering of the same "
                  "summary.", ""]
    lines += ["The sheet *values at the points* of the workbook lists both quantities "
              "at every probe point on every mesh. `mesh-convergence.png` draws them: "
              "the value at each point, the mean over the points against the cell "
              "size, and the change from one mesh to the next against the "
              "tolerance.", ""]
    lines += [
        "## Mathematical model and software",
        "",
        "All simulations solve the two-dimensional depth-averaged shallow water "
        "equations (Saint-Venant equations): conservation of mass and of momentum "
        "for the depth-averaged horizontal velocity field, with bed friction and a "
        "turbulence closure for the horizontal momentum exchange. The equations are "
        "solved with the finite element method of the open-source solver TELEMAC-2D "
        "(https://www.opentelemac.org) on unstructured triangular meshes. Every run "
        "marches to a steady state at the same constant inflow discharge with an "
        f"adaptive, CFL-bounded time step, {turb_txt} so that differences between "
        "the runs isolate the spatial discretization error.",
        "",
        "## Quantities examined and convergence criteria",
        "",
        f"The quantities of interest are {qty_txt}, sampled at {probes_txt} in the "
        "study reach on every mesh.",
        "",
        "Convergence is judged following Celik et al. (2008):",
        "",
        "- the relative change of each quantity between successive meshes (L2 norm "
        "over the probe points, normalized by the finer mesh);",
        "- the observed order of convergence p from the finest three meshes;",
        "- the GCI of the finest grid triplet with a safety "
        f"factor of {GCI_SAFETY}, accepted only when an asymptotic-range check "
        "passes (otherwise the relative change is the fallback criterion);",
        f"- tolerance: {tolerance * 100:g}% on the finest mesh.",
        "",
        "Reference: Celik, I. B., Ghia, U., Roache, P. J., Freitas, C. J., Coleman, "
        "H., and Raad, P. E. (2008). Procedure for Estimation and Reporting of "
        "Uncertainty Due to Discretization in CFD Applications. ASME Journal of "
        "Fluids Engineering, 130(7), 078001.",
        "",
    ]
    if levels:
        lines += ["## Simulated meshes", "",
                  "| mesh | cell size [m] | elements | nodes | runtime [s] |",
                  "|---|---|---|---|---|"]
        for lbl, dx, ne, nn, rt in levels:
            rt_txt = f"{rt:.0f}" if rt else "-"
            lines.append(f"| {lbl} | {dx:.3f} | {ne:,} | {nn:,} | {rt_txt} |")
        lines.append("")
    lines += [
        "## Directory contents",
        "",
        "Each simulated mesh has its own subfolder named after its refinement level "
        "(for example `baseline/`; `coarser_x1_30/` is 1.3 times coarser and "
        "`finer_x1_69/` is 1.69 times finer than the baseline). Each subfolder is a "
        "complete, self-contained TELEMAC-2D model:",
        "",
        "- `level.json`: machine-readable metadata of the level (cell sizes, element "
        "and node counts, shortest edge, time step, runtime, creation date).",
        "- `geometry.slf`: the computational mesh with the bed elevation "
        "interpolated from the digital elevation model (double-precision SERAFIN "
        "format);",
        "- `boundaries.cli`: the boundary-conditions file (wall, inflow, and "
        "outflow codes per boundary node);",
        "- `friction.tbl`: the zonal bed-friction table;",
        "- `steady2d.cas`: the TELEMAC-2D steering file with all numerical "
        "settings of the run;",
        "- `initial-conditions.slf`: the initial state the run starts from "
        "(pre-wetted channel);",
        "- `liquid-boundaries.json`: metadata on the open (inflow/outflow) "
        "boundaries;",
        "- `r2d.slf`: the simulation results; the last time frame is the steady "
        "state evaluated by the study;",
        "- `steady2d.cas_<timestamp>*.sortie`: the solver listing (console log) "
        "of the run, including the boundary-flux printouts.",
        "",
    ]
    if extra_entries:
        lines += ["In addition, this folder contains:", ""]
        lines += [f"- `{name}`: {desc}" for name, desc in extra_entries.items()]
        lines.append("")
    path = base_dir / "README.md"
    path.write_text("\n".join(lines))
    return path


# --------------------------------------------------------------------------- #
# report maths
# --------------------------------------------------------------------------- #


def _rel_change(prev: np.ndarray, curr: np.ndarray) -> float:
    """Relative L2 change between two probe vectors, normalized by the finer one."""
    denom = np.linalg.norm(curr)
    if denom == 0:
        return float(np.linalg.norm(curr - prev))
    return float(np.linalg.norm(curr - prev) / denom)


def build_report(levels: list[LevelResult], quantities, tolerance: float,
                 probes: np.ndarray) -> ConvergenceReport:
    """Assemble a :class:`ConvergenceReport` (relative change, order, GCI, verdict).

    The verdict is **GCI-based** when, for every quantity, the finest grid triplet
    yields a GCI whose asymptotic-range check passes (``GCI(med) / (r**p GCI(fine))``
    within :data:`ASYMPTOTIC_BAND`, ~1 in the asymptotic range): converged iff
    ``GCI(fine) < tolerance``. Otherwise it falls back to the finest successive
    relative change vs. the tolerance (recorded in ``verdict_basis``).
    """
    rep = ConvergenceReport(levels=list(levels), quantities=list(quantities),
                            tolerance=tolerance, probes=np.asarray(probes))
    for q in quantities:
        changes = [_rel_change(levels[i].qoi[q], levels[i + 1].qoi[q])
                   for i in range(len(levels) - 1)]
        rep.rel_change[q] = changes
        g = _gci_triplet(levels, q)
        rep.observed_order[q] = g.p
        rep.order_used[q] = g.p_used
        rep.gci_fine[q] = g.gci_fine
        rep.gci_med[q] = g.gci_med
        rep.asymptotic_ratio[q] = g.asymptotic_ratio

    def trustworthy(q):
        ar = rep.asymptotic_ratio.get(q)
        return (rep.gci_fine.get(q) is not None and ar is not None
                and ASYMPTOTIC_BAND[0] <= ar <= ASYMPTOTIC_BAND[1])

    if quantities and all(trustworthy(q) for q in quantities):
        rep.verdict_basis = "gci"
        rep.converged = all(rep.gci_fine[q] < tolerance for q in quantities)
    else:
        rep.verdict_basis = "rel_change"
        rep.converged = all(rep.rel_change[q] and rep.rel_change[q][-1] < tolerance
                            for q in quantities)
    return rep


@dataclass
class _Gci:
    """Order/GCI diagnostics of one quantity's finest grid triplet."""

    p: float | None = None                 # observed order (raw)
    p_used: float | None = None            # p clamped to [0.5, THEORETICAL_ORDER]
    gci_fine: float | None = None          # fine-grid GCI (medium->fine pair)
    gci_med: float | None = None           # medium-grid GCI (coarse->medium pair)
    asymptotic_ratio: float | None = None  # GCI(med) / (r2**p_used * GCI(fine))


def _gci_triplet(levels: list[LevelResult], q: str) -> _Gci:
    """Observed order and GCIs from the **finest** (>=3) grid triplet.

    The GCI uses the observed order clamped to ``[0.5, THEORETICAL_ORDER]``: with
    closely spaced or noisy grids the raw p can come out absurdly high (the ering
    case produced p=7 from re-run noise), and ``r**p - 1`` in the denominator then
    shrinks the GCI to a meaninglessly small value. The raw p is still reported.
    """
    g = _Gci()
    if len(levels) < 3:
        return g
    coarse, med, fine = levels[-3], levels[-2], levels[-1]
    r1 = coarse.cell_size / med.cell_size
    r2 = med.cell_size / fine.cell_size
    if min(r1, r2) <= 1.0:
        return g
    f_c, f_m, f_f = coarse.repr_value(q), med.repr_value(q), fine.repr_value(q)
    e21, e32 = f_m - f_c, f_f - f_m
    if e21 == 0 or e32 == 0 or (e32 / e21) <= 0:
        return g                              # oscillatory / degenerate: no order
    p = math.log(abs(e21 / e32)) / math.log(0.5 * (r1 + r2))
    if not math.isfinite(p):
        return g
    g.p = p
    if p <= 0:
        return g
    g.p_used = min(max(p, 0.5), THEORETICAL_ORDER)
    rel_f = abs(e32 / f_f) if f_f != 0 else abs(e32)
    rel_m = abs(e21 / f_m) if f_m != 0 else abs(e21)
    g.gci_fine = GCI_SAFETY * rel_f / (r2 ** g.p_used - 1.0)
    g.gci_med = GCI_SAFETY * rel_m / (r1 ** g.p_used - 1.0)
    if g.gci_fine > 0:
        g.asymptotic_ratio = g.gci_med / (r2 ** g.p_used * g.gci_fine)
    return g


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #


def default_levels(cfg: Config) -> list[tuple[str, float, float]]:
    """Three meshes spanning one doubling of cell size around the config sizes.

    Returns ``(label, channel_size, floodplain_size)`` coarse -> fine. The finest
    matches the config; coarser ones double the cell size (so coarse/fine = 2)."""
    ch, fp = cfg.mesh.channel_size, cfg.mesh.floodplain_size
    return [("coarse", ch * 2.0, fp * 2.0),
            ("medium", ch * math.sqrt(2.0), fp * math.sqrt(2.0)),
            ("fine", ch, fp)]


def percent_levels(cfg: Config,
                   percents=(0.40, 0.20, 0.0, -0.20, -0.40)
                   ) -> list[tuple[str, float, float]]:
    """Cell-size levels at fractional offsets from the config size, coarse -> fine.

    Positive *percents* coarsen (larger cells), negative refine (smaller cells);
    the default gives two 40%/20%-coarser, the baseline, and two 20%/40%-finer
    meshes. Returns ``(label, channel_size, floodplain_size)``.

    .. note:: prefer :func:`ratio_levels` - the +-20% steps give successive grid
       ratios of only ~1.17-1.33, below the r >= 1.3 that Celik et al. (2008)
       recommend for a stable observed order / GCI."""
    ch, fp = cfg.mesh.channel_size, cfg.mesh.floodplain_size
    levels = []
    for p in sorted(percents, reverse=True):           # coarse (large) -> fine (small)
        factor = 1.0 + p
        if abs(p) < 1e-9:
            label = "baseline"
        elif p > 0:
            label = f"+{p * 100:.0f}% coarser"
        else:
            label = f"-{-p * 100:.0f}% finer"
        levels.append((label, ch * factor, fp * factor))
    return levels


def ratio_levels(cfg: Config, ratio: float = 1.3, n_coarser: int = 1,
                 n_finer: int = 2) -> list[tuple[str, float, float]]:
    """Cell-size ladder with a constant refinement factor *ratio*, coarse -> fine.

    The default gives four meshes: one coarser (``x ratio``), the config baseline,
    and two finer (``/ ratio``, ``/ ratio**2``) - the finest three form the GCI
    triplet. Celik et al. (2008) recommend **ratio >= 1.3**: with closer-spaced
    grids the solution differences drop into iterative/sampling noise and the GCI
    denominator ``r**p - 1`` degenerates, so p and the GCI become unstable.
    Returns ``(label, channel_size, floodplain_size)`` tuples; extend the ladder
    one finer level at a time via ``run_mesh_convergence(extend_ratio=...)``.
    """
    if ratio <= 1.0:
        raise ValueError(f"refinement ratio must exceed 1.0 (got {ratio}); "
                         "Celik et al. (2008) recommend >= 1.3")
    ch, fp = cfg.mesh.channel_size, cfg.mesh.floodplain_size
    levels = []
    for k in range(n_coarser, -(n_finer + 1), -1):     # coarse (large) -> fine (small)
        f = ratio ** k
        if k == 0:
            label = "baseline"
        elif k > 0:
            label = f"coarser x{f:.2f}"
        else:
            label = f"finer x{1.0 / f:.2f}"
        levels.append((label, ch * f, fp * f))
    return levels


def _next_finer(levels: list[tuple[str, float, float]],
                ratio: float) -> tuple[str, float, float]:
    """The next extension level: the finest tuple refined by *ratio*, labeled by
    its total refinement factor vs. the baseline (or coarsest) level."""
    _, ch0, fp0 = levels[-1]
    ch, fp = ch0 / ratio, fp0 / ratio
    base = next((c for lbl, c, _ in levels if lbl == "baseline"), levels[0][1])
    return f"finer x{base / ch:.2f}", ch, fp


def _slug(label: str) -> str:
    """Filesystem-safe per-level folder name (labels carry spaces/%/x/+/- that
    TELEMAC and shells dislike in paths)."""
    return "".join(c if c.isalnum() else "_" for c in label).strip("_") or "level"


def default_probes(cfg: Config, n: int = 40) -> np.ndarray:
    """Probe points for the QoI: the ground-truth points if available, else samples
    along the channel centerline (falling back to the ROI bbox diagonal).

    Only the positions of measurements are used, never the measured values, and only
    when the case names its ground truth: a table compiled by an earlier build stays
    in the preprocessing folder after the case drops its sources. The centerline
    points are spaced from half a step after the inflow to half a step before the
    outflow. On the Inn KB15 ladder, a point on the inflow line read 0.09, 0.36, 0.12
    and 0.40 m of depth on the four meshes, the depth of one boundary node or another.
    """
    named = getattr(cfg, "ground_truth", None)
    configured = (named is None or getattr(named, "measurements", None) is not None
                  or bool(getattr(named, "sources", None)))
    gt = cfg.ground_truth_path if configured else None
    if gt is not None and Path(gt).exists():
        try:
            from axqua import ground_truth

            from axqua.bayescal import _inside_the_model

            tables = ground_truth.read_tidy(gt)
            # Points outside the model (a campaign's gauging sections upstream of
            # it) are left out, as the calibration leaves them out: sampled at the
            # nearest node they compare a wall with mid-channel. On the Isar example
            # they were 23 of 56 probes and decided the verdict.
            pts = np.vstack([_inside_the_model(cfg, t[["x", "y"]])
                             .to_numpy(dtype=float)
                             for t in tables.values() if {"x", "y"} <= set(t.columns)])
            if len(pts):
                return pts
        except Exception as exc:  # pragma: no cover - fallback only
            log.debug("probe points from ground truth unavailable: %s", exc)
    if cfg.geodata.channel_centerline is not None:
        import geopandas as gpd
        from shapely.ops import linemerge, unary_union

        gdf = gpd.read_file(cfg.geodata.channel_centerline)
        if gdf.crs and gdf.crs.to_epsg() != cfg.crs_epsg:
            gdf = gdf.to_crs(epsg=cfg.crs_epsg)
        merged = unary_union(gdf.geometry.values)
        # linemerge only accepts a MultiLineString; a single merged LineString is
        # already the line (passing it to linemerge raises "Cannot linemerge")
        line = linemerge(merged) if merged.geom_type == "MultiLineString" else merged
        if line.geom_type == "MultiLineString":
            line = max(line.geoms, key=lambda g: g.length)
        d = (np.arange(n) + 0.5) / n * line.length
        return np.array([[line.interpolate(s).x, line.interpolate(s).y] for s in d])
    raise ValueError("no probe points: provide ground truth or a channel centerline, "
                     "or pass probes= explicitly to run_mesh_convergence")


def make_telemac_simulator(cfg: Config, discharge: float, *, base_dir: Path,
                           runtime=None, n_processors: int | None = None,
                           pinned_turbulence: int | None = None):
    """Build a ``simulate(label, ch, fp, probes, quantities) -> LevelResult`` that
    builds the case at the given resolution, runs TELEMAC once at constant
    *discharge* (variable time step for per-mesh CFL), and samples the QoI.

    The level's ``(ch, fp)`` sizes are interpreted **relative to the config
    baseline**: the level copy is built with ``mesh.size_scale = ch /
    cfg.mesh.channel_size`` so the *whole* sizing field scales - including any
    per-zone ``Max Edge Length (m)`` values from the mesh-zones gpkg, which would
    otherwise silently override the per-level sizes and leave every "level" with
    the same mesh (the bug behind the invalid first ering study).

    *pinned_turbulence* is the turbulence model of every level (default: the choice for *cfg* without a mesh); :func:`run_mesh_convergence` passes the choice for the case's own mesh.
    """
    import copy
    import shutil
    import tempfile
    import time

    from axqua import mesh as mesh_mod
    from axqua import mesh_validity, pipeline, steering

    base_dir = Path(base_dir)
    # pin the turbulence closure to the baseline choice across all grid levels, so
    # the study isolates the discretization error (per-level auto-selection would
    # otherwise switch closures as the cell size changes and confound the comparison)
    if pinned_turbulence is None:
        pinned_turbulence = steering.select_turbulence_model(cfg)[0]
    base_channel = float(cfg.mesh.channel_size)
    try:
        ks_channel = mesh_validity.channel_ks(cfg)
    except Exception as exc:                     # pragma: no cover - geodata issues
        log.debug("channel ks unavailable: %s", exc)
        ks_channel = None

    def simulate(label, ch, fp, probes, quantities):
        lc = copy.deepcopy(cfg)
        # scale the whole sizing field (config *and* gpkg per-zone sizes) uniformly
        lc.mesh.size_scale = cfg.mesh.size_scale * (ch / base_channel)
        lc.hydrodynamics.turbulence_model = pinned_turbulence
        lc.boundaries.prescribed_flowrate = discharge
        # let TELEMAC pick a CFL-admissible dt for this mesh (iterative convergence
        # to steady state must be reached before meshes are compared). These runs are
        # pre-wetted (hotstart), so a faster 0.6 Courant target keeps the study
        # tractable; the production dry-start default is the safer 0.30.
        lc.hydrodynamics.variable_timestep = True
        lc.hydrodynamics.desired_courant = 0.6
        if n_processors:
            lc.telemac.n_processors = n_processors
        # the level folder IS the TELEMAC case: geometry/cli/tbl/cas + results and
        # level.json land directly in it, with no redundant model/ subfolder. The
        # build's preprocessing (a per-level DEM clip, identical across the ladder)
        # and any ground-truth/calibration products are irrelevant to a grid-
        # independence run, so they go to a throwaway scratch dir that is discarded -
        # keeping every level folder a clean, self-contained model.
        d = base_dir / _slug(label)
        scratch = Path(tempfile.mkdtemp(prefix=f"axqua-mc-{_slug(label)}-"))
        lc.preprocessing_dir = scratch
        lc.model_dir = d
        lc.postprocessing_dir = scratch
        lc.calibration_dir = scratch
        lc.ensure_dirs()

        t0 = time.time()
        try:
            # reuse the parent run's one compound logfile (no per-level logfiles)
            pipeline.run(lc, validate_env=False, dry_run=True, log_to_file=False)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        runtime_s = time.time() - t0

        eff = mesh_mod.nominal_channel_size(lc)   # effective (gpkg-aware) size
        res = lc.model_path(lc.results_slf)
        data = selafin.read_slf(res, frame=-1)
        geometry = lc.model_path(lc.geometry_slf)
        min_edge, mean_depth, mean_vel = _geom_stats(data, probes, geometry)
        dt = _cfl_dt(min_edge, mean_depth, mean_vel)
        avg = extract_mean_at_points(res, quantities, probes, geometry=geometry,
                                     window=TIME_WINDOW)
        qoi = avg.mean
        validity = mesh_validity.check_level(
            cfg, dx=eff, depth=mean_depth, velocity=mean_vel, ks=ks_channel,
            pinned_model=pinned_turbulence,
            filter_width=steering.geometry_filter_width(cfg, geometry))
        result = LevelResult(label=label, cell_size=eff, n_elem=data["ikle"].shape[0],
                             n_nodes=data["x"].size, min_edge=min_edge, dt=dt,
                             runtime_s=runtime_s, qoi=qoi, scale=lc.mesh.size_scale,
                             mean_depth=mean_depth, mean_velocity=mean_vel,
                             validity=validity, fluctuation=avg.fluctuation,
                             n_frames=avg.n_frames)
        _write_level_json(d, result, ch, fp)
        return result

    return simulate


def _geom_stats(data: dict, probes, geometry=None):
    """(shortest edge, mean probe depth, mean probe velocity) from a result dict.

    With *geometry* the edges and the sampling use its double-precision coordinates
    (:func:`mesh_coordinates`); the float32 coordinates of a result floor the
    shortest edge at the coordinate resolution.
    """
    x, y, ikle = mesh_coordinates(data, geometry)
    sample = _interp_on_mesh if geometry is not None else (
        lambda x, y, _ikle, field, pts: _interp_to_points(x, y, field, pts))
    p = ikle
    e = np.concatenate([
        np.hypot(x[p[:, 0]] - x[p[:, 1]], y[p[:, 0]] - y[p[:, 1]]),
        np.hypot(x[p[:, 1]] - x[p[:, 2]], y[p[:, 1]] - y[p[:, 2]]),
        np.hypot(x[p[:, 2]] - x[p[:, 0]], y[p[:, 2]] - y[p[:, 0]]),
    ])
    # a merged parallel result duplicates subdomain-interface nodes, producing
    # zero-length edges that would report min_edge=0 (and a NaN CFL dt)
    nonzero = e > 1e-9
    if not nonzero.all():
        log.debug("  _geom_stats: ignoring %d zero-length edges (duplicate "
                  "interface nodes in a merged parallel result)",
                  int((~nonzero).sum()))
        e = e[nonzero]
    probes = np.asarray(probes, dtype=float)
    try:
        depth = float(np.nanmean(sample(
            x, y, ikle, _resolve_field(data["values"], "WATER DEPTH"), probes)))
        vel = float(np.nanmean(sample(
            x, y, ikle, _resolve_field(data["values"], "SCALAR VELOCITY"), probes)))
    except KeyError:
        depth, vel = 1.0, 1.0
    min_edge = float(e.min()) if e.size else float("nan")
    return min_edge, max(depth, 1e-3), max(vel, 0.0)


def _cfl_dt(min_edge: float, depth: float, velocity: float, courant: float = 0.9) -> float:
    """CFL-admissible time step dt = C * dx / (|U| + sqrt(g*h))."""
    celerity = velocity + math.sqrt(9.81 * depth)
    return courant * min_edge / celerity if celerity > 0 else float("nan")


# --------------------------------------------------------------------------- #
# per-level persistence (resume support)
# --------------------------------------------------------------------------- #


def _write_level_json(level_dir: Path, level: LevelResult, ch: float,
                      fp: float) -> None:
    """Persist a completed level's metadata so a later run can resume from it.

    The QoI are deliberately *not* persisted - on resume they are re-extracted
    from the existing result SELAFIN, which keeps resume robust to changed probe
    points or quantities.
    """
    from datetime import datetime, timezone

    meta = {"label": level.label, "channel_size": ch, "floodplain_size": fp,
            "scale": level.scale, "cell_size": level.cell_size,
            "n_elem": level.n_elem, "n_nodes": level.n_nodes,
            "min_edge": level.min_edge, "dt": level.dt,
            "runtime_s": level.runtime_s,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        (Path(level_dir) / "level.json").write_text(json.dumps(meta, indent=2) + "\n")
    except OSError as exc:                       # pragma: no cover - fs issues
        log.warning("could not write %s/level.json: %s", level_dir, exc)


def _scan_completed(cfg: Config, base_dir: Path, levels, *,
                    result_name: str | None = None,
                    turbulence_model: int | None = None) -> dict[str, Path]:
    """Map ``{label: level_dir}`` of planned levels with a reusable result.

    A level is reusable when its folder holds both the result SELAFIN and a
    ``level.json`` whose channel/floodplain sizes match the planned tuple (so a
    changed ladder never silently reuses a mesh built at different sizes), and,
    with *turbulence_model*, when its steering file ran that TELEMAC model (a level
    of an older study with another closure is run again).
    *result_name* is the result file of a level (TELEMAC's ``r2d.slf`` by default)."""
    results_slf = result_name or getattr(cfg, "results_slf", "r2d.slf")
    done: dict[str, Path] = {}
    for label, ch, fp in levels:
        d = Path(base_dir) / _slug(label)
        jf = d / "level.json"
        if not (jf.exists() and (d / results_slf).exists()):
            continue
        try:
            meta = json.loads(jf.read_text())
            ran = _level_turbulence(cfg, d) if turbulence_model is not None else None
            if ran is not None and ran != turbulence_model:
                log.info("existing level '%s' ran turbulence model %d, the study "
                         "uses %d; it will be re-run", label, ran, turbulence_model)
            elif (math.isclose(float(meta["channel_size"]), ch, rel_tol=1e-6)
                    and math.isclose(float(meta["floodplain_size"]), fp, rel_tol=1e-6)):
                done[label] = d
            else:
                log.info("existing level '%s' was built at different sizes "
                         "(%.3f/%.3f m vs. planned %.3f/%.3f m); it will be re-run",
                         label, meta["channel_size"], meta["floodplain_size"], ch, fp)
        except (KeyError, ValueError, TypeError, OSError) as exc:
            log.debug("unreadable level.json in %s: %s", d, exc)
    return done


def _level_turbulence(cfg: Config, level_dir: Path) -> int | None:
    """The TURBULENCE MODEL of a level's steering file, or None."""
    from axqua.solvers.telemac.steering import read_cas_values

    cas = Path(level_dir) / getattr(cfg, "cas_file", "steady2d.cas")
    try:
        value = read_cas_values(cas, "TURBULENCE MODEL")["TURBULENCE MODEL"]
        return int(str(value).strip()) if value else None
    except (OSError, ValueError):
        return None


def _load_completed_level(cfg: Config, level_dir: Path, label: str, probes,
                          quantities, *, ks=None,
                          pinned_model: int | None = None,
                          result_name: str | None = None,
                          check_validity: bool = True) -> LevelResult | None:
    """Rebuild a :class:`LevelResult` from an existing per-level folder, or None
    (-> the level is re-run). QoI/statistics are re-extracted from the stored
    result SELAFIN; runtime and sizes come from ``level.json``."""
    try:
        meta = json.loads((Path(level_dir) / "level.json").read_text())
        res = Path(level_dir) / (result_name or getattr(cfg, "results_slf", "r2d.slf"))
        data = selafin.read_slf(res, frame=-1)
        geometry = Path(level_dir) / getattr(cfg, "geometry_slf", "geometry.slf")
        min_edge, mean_depth, mean_vel = _geom_stats(data, probes, geometry)
        dt = _cfl_dt(min_edge, mean_depth, mean_vel)
        # a TELEMAC level is averaged over the end of its run, as when it was run;
        # a result of another code (result_name) is read at its last frame
        fluct, n_frames = {}, 1
        if result_name is None:
            avg = extract_mean_at_points(res, quantities, probes, geometry=geometry,
                                         window=TIME_WINDOW)
            qoi, fluct, n_frames = avg.mean, avg.fluctuation, avg.n_frames
        else:
            qoi = extract_at_points(res, quantities, probes, geometry=geometry)
        cell_size = float(meta.get("cell_size") or meta["channel_size"])
        validity = None
        try:
            if check_validity:
                from axqua import mesh_validity, steering

                validity = mesh_validity.check_level(
                    cfg, dx=cell_size, depth=mean_depth, velocity=mean_vel, ks=ks,
                    pinned_model=pinned_model,
                    filter_width=steering.geometry_filter_width(cfg, geometry))
        except Exception as exc:                 # dummy test configs
            log.debug("validity check skipped for reused '%s': %s", label, exc)
        return LevelResult(label=label, cell_size=cell_size,
                           n_elem=int(meta.get("n_elem") or data["ikle"].shape[0]),
                           n_nodes=int(meta.get("n_nodes") or data["x"].size),
                           min_edge=min_edge, dt=dt,
                           runtime_s=meta.get("runtime_s"), qoi=qoi,
                           scale=float(meta.get("scale") or 1.0),
                           mean_depth=mean_depth, mean_velocity=mean_vel,
                           validity=validity, fluctuation=fluct, n_frames=n_frames)
    except Exception as exc:
        log.warning("could not reuse completed level '%s' (%s); re-running it",
                    label, exc)
        return None


# --------------------------------------------------------------------------- #
# extension support: runtime extrapolation + interactive prompt
# --------------------------------------------------------------------------- #


def estimate_next_runtime(levels: list[LevelResult],
                          next_cell_size: float) -> tuple[float | None, str]:
    """Estimated wall-clock [s] of a run at *next_cell_size*, with its basis.

    Fits ``log(runtime) = a + b log(cell_size)`` over the completed levels; when
    fewer than two distinct sizes carry runtimes, or the fitted exponent is
    implausible, falls back to the theoretical ``t ~ dx**-3`` scaling from the
    finest level (elements ~ dx**-2, CFL-bound steps ~ dx**-1). ``(None, ...)``
    when no runtimes were recorded (e.g. an injected test simulator).
    """
    pts = [(x.cell_size, x.runtime_s) for x in levels
           if x.runtime_s and x.cell_size and x.cell_size > 0 and x.runtime_s > 0]
    if not pts or next_cell_size <= 0:
        return None, "no runtimes recorded"
    if len({round(dx, 12) for dx, _ in pts}) >= 2:
        b, a = np.polyfit(np.log([dx for dx, _ in pts]),
                          np.log([t for _, t in pts]), 1)
        if -6.0 < b < -1.0:                      # sane scaling exponent
            return float(math.exp(a + b * math.log(next_cell_size))), \
                f"fitted t ~ dx^{b:.1f}"
    dx_f, t_f = pts[-1]
    return float(t_f * (dx_f / next_cell_size) ** 3), "theoretical t ~ dx^-3"


def _default_ask(prompt: str, *, default: bool = False,
                 timeout: float = PROMPT_TIMEOUT_S) -> bool:
    """Yes/no prompt on stdin.

    A non-interactive session returns *default* (so a batch/nohup run never hangs:
    extension stops, resume reuses). At an interactive terminal the prompt waits up
    to *timeout* seconds for a reply; if the user does not respond it assumes
    **'yes'** - an unattended mesh-convergence study then keeps refining to the
    finest feasible mesh instead of stalling on the prompt.
    """
    import select
    import sys

    if not sys.stdin.isatty():
        log.info("non-interactive session: '%s...' -> %s",
                 prompt.splitlines()[0][:70], "yes" if default else "no")
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    sys.stdout.write(prompt + suffix)
    sys.stdout.flush()
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):                # stdin not selectable -> block
        ready = [sys.stdin]
    if not ready:                                # no response within the timeout
        sys.stdout.write("\n")
        sys.stdout.flush()
        log.info("no response within %.0f s: assuming 'yes'", timeout)
        return True
    try:
        reply = sys.stdin.readline().strip().lower()
    except EOFError:                             # stdin closed mid-run
        return default
    return default if not reply else reply in ("y", "yes")


def _with_turbulence_depth(cfg: Config):
    """``(cfg, case_mesh)``: a copy of *cfg* whose turbulence depth is fixed for every mesh of the study, and the light mesh of the case's own geometry (None without one).

    The choice of the turbulence model compares the filter width of the cells with the flow depth (:func:`axqua.steering.select_turbulence_model`). Each level of the study has its own model folder, without a steady result, so each would estimate the depth anew; the depth of the case (its steady result, else normal flow on its mesh) is set once as ``hydrodynamics.turbulence_length_scale`` on the copy instead. A case that sets it keeps its value.
    """
    import copy

    try:
        from axqua import steering

        case_mesh = steering.mesh_from_geometry(cfg.model_path(cfg.geometry_slf))
        if cfg.hydrodynamics.turbulence_length_scale is not None:
            return cfg, case_mesh
        scale = steering.flow_scale(cfg, case_mesh)
    except Exception as exc:                     # noqa: BLE001 - bare test configs
        log.debug("no turbulence depth for the study: %s", exc)
        return cfg, None
    out = copy.deepcopy(cfg)
    out.hydrodynamics.turbulence_length_scale = float(scale.depth)
    log.info("mesh convergence: flow depth %.2f m for the turbulence choice of every "
             "mesh (%s)", scale.depth, scale.source)
    return out, case_mesh


def run_mesh_convergence(cfg: Config, *, discharge: float, levels=None,
                         quantities=DEFAULT_QUANTITIES, probes=None,
                         tolerance: float = DEFAULT_TOLERANCE, simulate=None,
                         base_dir: Path | None = None, runtime=None,
                         n_processors: int | None = None,
                         extend_ratio: float | None = None,
                         max_extra_levels: int = 3,
                         auto_extend: bool = False,
                         ask=None, on_level=None, result_name: str | None = None,
                         check_validity: bool = True) -> ConvergenceReport:
    """Run the convergence study and return the :class:`ConvergenceReport`.

    *on_level(done, total, label)* is called before the first mesh and after every
    mesh that has been run or reused, so that a job can say "mesh 2 of 4" while the
    study runs. A study took twenty minutes and reported nothing until it had ended.

    *levels* is a list of ``(label, channel_size, floodplain_size)`` coarse->fine
    (default: one doubling around the config sizes; prefer :func:`ratio_levels`).
    The sizes are interpreted *relative to the config baseline* - each level is
    built with ``mesh.size_scale = channel_size / cfg.mesh.channel_size`` so gpkg
    per-zone sizes scale along (see :func:`make_telemac_simulator`). *simulate*
    overrides the TELEMAC runner (used in tests); by default each mesh is built
    and run once at constant *discharge*. *probes* default to the ground-truth /
    centerline points. *tolerance* is the GCI / relative-change goal (default 5%,
    per Celik et al. 2008).

    **Resume**: completed levels found under *base_dir* (result SELAFIN +
    ``level.json`` with matching sizes) are offered for reuse - QoI re-extracted,
    only missing levels run. **Extension**: with *extend_ratio* set, a
    not-converged study offers up to *max_extra_levels* additional refinements,
    each preceded by a runtime estimate and the mesh-validity check of the
    candidate size. Both go through *ask(prompt, default=...) -> bool* (default:
    stdin prompt; non-interactive sessions take the stated default: resume = yes,
    extend = no). An interactive prompt left **unanswered for
    ``PROMPT_TIMEOUT_S`` seconds assumes 'yes'**, so an attended-then-abandoned run
    keeps refining rather than stalling. A candidate whose turbulence/roughness
    model no longer matches the pinned closure is still run (the caveat is warned,
    not enforced as a stop). Inject a callable for scripted runs/tests.

    **Auto-refinement**: with *auto_extend* set, resume and every extension are
    auto-approved (no prompt, interactive or not), so the study keeps refining one
    grid at a time until it reaches the GCI/relative-change goal, exhausts
    *max_extra_levels*, or a level fails to mesh/run (the practical refinement
    limit) - then it writes the report from the completed levels.

    *result_name* and *check_validity* serve a study of another code (the OpenFOAM
    free-surface model, :mod:`axqua.solvers.openfoam.convergence`): the result file
    of a level, and whether the checks of the TELEMAC turbulence and roughness
    closure (:mod:`axqua.mesh_validity`) apply to its levels at all.
    """
    levels = list(levels or default_levels(cfg))
    quantities = list(quantities)
    probes = np.asarray(probes if probes is not None else default_probes(cfg), float)
    if base_dir is None:
        base_dir = cfg.postprocessing_path("mesh-convergence")
    base_dir = Path(base_dir)
    if auto_extend:
        # auto-refinement: approve resume + every extension without prompting, so
        # the study marches finer until it converges / hits max_extra_levels / a
        # level fails. An explicit *ask* (e.g. a test stub) still takes precedence.
        ask = ask or (lambda *a, **k: True)
    ask = ask or _default_ask

    # study context for the validity checks (graceful for bare test configs)
    pinned = ks_channel = None
    if check_validity:
        cfg, case_mesh = _with_turbulence_depth(cfg)
        try:
            from axqua import steering

            pinned, why = steering.select_turbulence_model(cfg, case_mesh)
            log.info("mesh convergence: turbulence model of every mesh: %s (%s)",
                     steering.TURB_NAMES.get(pinned, pinned), why)
        except Exception:
            pinned = None
        try:
            from axqua import mesh_validity

            ks_channel = mesh_validity.channel_ks(cfg)
        except Exception:
            ks_channel = None
    if simulate is None:
        simulate = make_telemac_simulator(
            cfg, discharge, base_dir=base_dir, runtime=runtime,
            n_processors=n_processors, pinned_turbulence=pinned)

    reuse: dict[str, Path] = {}
    if base_dir.exists():
        done = _scan_completed(cfg, base_dir, levels, result_name=result_name,
                               turbulence_model=pinned)
        if done:
            summary = ", ".join(f"'{lbl}'" for lbl in done)
            if ask(f"mesh convergence: found {len(done)} completed level(s) in "
                   f"{base_dir} ({summary}).\nResume and reuse them, re-running "
                   "only the missing levels? ('n' re-runs everything; the "
                   "mesh-convergence report files are rewritten either way)",
                   default=True):
                reuse = done

    def _report_level(label: str, *, planned: int = 0) -> None:
        if on_level is None:
            return
        try:
            on_level(len(results), len(levels) + planned, label)
        except Exception:                # noqa: BLE001 - reporting must not stop a study
            log.debug("the level listener failed", exc_info=True)

    results: list[LevelResult] = []
    _report_level(levels[0][0] if levels else "")
    for label, ch, fp in levels:
        res = None
        if label in reuse:
            res = _load_completed_level(cfg, reuse[label], label, probes, quantities,
                                        ks=ks_channel, pinned_model=pinned,
                                        result_name=result_name,
                                        check_validity=check_validity)
            if res is not None:
                log.info("mesh convergence: reused completed '%s' (%d elements, "
                         "runtime %.0f s)", label, res.n_elem, res.runtime_s or 0.0)
        if res is None:
            log.info("mesh convergence: running '%s' (channel %.3f m, floodplain "
                     "%.3f m)", label, ch, fp)
            res = simulate(label, ch, fp, probes, quantities)
            log.info("  '%s': %d elements, shortest edge %.4f m, dt~%.4f s",
                     label, res.n_elem, res.min_edge, res.dt if res.dt else float("nan"))
        results.append(res)
        _report_level(label)

    report = build_report(results, quantities, tolerance, probes)

    # extension: offer one finer mesh at a time while not converged
    extras = 0
    while extend_ratio and not report.converged and extras < max_extra_levels:
        label, ch, fp = _next_finer(levels, extend_ratio)
        finest = results[-1]
        cand_dx = (finest.cell_size / extend_ratio if finest.cell_size > 0 else ch)
        est, basis = estimate_next_runtime(results, cand_dx)
        est_txt = (f"~{est / 3600.0:.1f} h ({est:.0f} s, {basis})"
                   if est is not None else "unknown (no runtimes recorded)")
        validity_txt = ""
        try:
            if not check_validity:
                raise LookupError("no closure checks for this code")
            from axqua import mesh_validity

            fw = getattr(finest.validity, "filter_width", float("nan"))
            cand = mesh_validity.check_level(
                cfg, dx=cand_dx,
                depth=finest.mean_depth if finest.mean_depth else 1.0,
                velocity=finest.mean_velocity if finest.mean_velocity else 1.0,
                ks=ks_channel, pinned_model=pinned,
                estimated=finest.mean_depth is None or finest.mean_velocity is None,
                filter_width=(fw / extend_ratio if fw == fw and fw > 0 else None))
            validity_txt = "\n".join("  " + ln for ln in cand.lines())
            if cand.dx_below_ks or cand.regime_change:
                reasons = []
                if cand.regime_change:
                    reasons.append("the auto-selected turbulence closure "
                                   f"({cand.turbulence_pick_name}) differs from the "
                                   "pinned model")
                if cand.dx_below_ks:
                    reasons.append("the cell size falls below the roughness height ks")
                why = "; ".join(reasons)
                validity_txt += ("\n  *** VALIDITY WARNING: this refinement may not "
                                 f"match the turbulence/roughness model compatibility "
                                 f"({why}); the refinement is run anyway, treat its "
                                 "results with that caveat ***")
                # log it too, so the caveat is recorded even when auto-approved or
                # run non-interactively (no prompt shown) - the finest still runs
                log.warning(
                    "mesh convergence: refinement '%s' (channel %.3f m) may not match "
                    "the turbulence/roughness model compatibility at this resolution "
                    "(%s); running it anyway.", label, cand_dx, why)
        except Exception as exc:
            log.debug("candidate validity check skipped: %s", exc)
        worst = max((report.gci_fine.get(q) if report.verdict_basis == "gci"
                     else (report.rel_change[q][-1] if report.rel_change.get(q)
                           else None)) or 0.0
                    for q in quantities)
        prompt = (f"mesh convergence: NOT converged after {len(results)} meshes "
                  f"(worst {'GCI' if report.verdict_basis == 'gci' else 'rel. change'}"
                  f" {worst * 100:.1f}% vs. {tolerance * 100:.0f}% goal).\n"
                  f"Run one more refinement '{label}' (channel {cand_dx:.3f} m)?\n"
                  f"  estimated runtime: {est_txt}\n"
                  f"  validity at the candidate size:\n{validity_txt}\n"
                  "Continue with this refinement ('n' stops and writes the report)?")
        if not ask(prompt, default=False):
            log.info("mesh convergence: stopping without further refinement "
                     "(user choice / non-interactive default); writing the report")
            break
        _report_level(label, planned=1)      # one more mesh than the ladder had
        try:
            res = simulate(label, ch, fp, probes, quantities)
        except Exception as exc:
            # a finer level can outrun the mesher/solver (e.g. BAMG cannot honor
            # the metric even after its retries, or the run diverges): treat it as
            # the practical refinement limit -- stop extending and report from the
            # levels already completed rather than crashing the whole study.
            log.error("mesh convergence: refinement '%s' (channel %.3f m) failed to "
                      "build/run (%s: %s); stopping extension and writing the report "
                      "from the %d completed level(s)", label, ch,
                      type(exc).__name__, exc, len(results))
            break
        log.info("  '%s': %d elements, shortest edge %.4f m, dt~%.4f s",
                 label, res.n_elem, res.min_edge, res.dt if res.dt else float("nan"))
        results.append(res)
        levels.append((label, ch, fp))
        extras += 1
        _report_level(label)
        report = build_report(results, quantities, tolerance, probes)

    report.case = getattr(cfg, "name", "")
    rec = report.recommendation()
    log.info("mesh convergence: %s at %.1f%% tolerance (%s basis); recommended "
             "cell size %.3f m",
             "CONVERGED" if report.converged else "NOT converged",
             tolerance * 100, report.verdict_basis, rec["cell_size"])
    return report


# --------------------------------------------------------------------------- #
# what a job does with a finished study
# --------------------------------------------------------------------------- #


def level_progress(ctx):
    """What a convergence study calls after each of its runs; ``None`` without a job."""
    if ctx is None:
        return None
    return lambda done, total, label: ctx.progress(
        levels_done=done, levels_total=total, current_label=label)


def write_study_report(report, folder: Path, stem: str, ctx, *,
                       readme: bool = False) -> None:
    """Write the report of a convergence study next to its levels, and record it.

    The case scripts have always done this after the study returned. The job did not:
    it ran the study for twenty minutes and left the verdict in its log, so the plugin
    had nothing to show for it and "the report" the documentation points at did not
    exist. A workbook that cannot be written must not fail a study that ran. Shared by
    the backends: a study of each code ends the same way.
    """
    folder = Path(folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        workbook = report.to_xlsx(folder / f"{stem}.xlsx")
        summary = folder / f"{stem}.txt"
        report.save(summary)
        figure = None
        try:
            figure = plot_report(report, folder / f"{stem}.png",
                                 size_name=getattr(report, "size_name",
                                                   "cell size [m]"))
        except Exception as exc:                    # noqa: BLE001 - a figure is extra
            log.warning("could not draw the %s figure: %s: %s", stem,
                        type(exc).__name__, exc)
        if readme:
            write_readme(folder, report=report)
    except Exception as exc:                        # noqa: BLE001
        log.warning("could not write the %s report: %s: %s", stem, type(exc).__name__, exc)
        return
    log.info("%s report -> %s", stem, workbook)
    if ctx is not None:
        ctx.record(f"{stem.replace('-', ' ').capitalize()} summary", summary, kind="file")
        if figure is not None:
            ctx.record(f"{stem.replace('-', ' ').capitalize()} figure", figure,
                       kind="file")
