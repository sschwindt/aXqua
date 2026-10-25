"""What a hydrology run produced: the outlet hydrograph and the water balance.

Two sources in the listing of the run (``hydrology2d.cas_<date>.sortie``):

* TELEMAC's water balance at every listing printout (:mod:`axqua.solvers.telemac. sortie`): the volume in the domain and the flux through every liquid boundary, positive entering. The outlet hydrograph is the sum of the exiting fluxes;
* the line ``AXQUA HYDROLOGY t rain infiltrated evaporated`` that aXqua's runoff routine writes at the same printouts: the volumes of rain, infiltration and evaporation since the start [m3].

The balance closes as ``rain + inflow = outflow + infiltrated + evaporated + storage + error``; ``error`` is what TELEMAC's mass conservation leaves, and is reported relative to the rain. The runoff coefficient is outflow over rain.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("axqua")

BUDGET_RE = re.compile(r"^\s*AXQUA HYDROLOGY\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)")


def read_budget(path: str | Path) -> dict[str, np.ndarray]:
    """The ``AXQUA HYDROLOGY`` lines of a listing: time, rain, infiltrated, evaporated."""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            match = BUDGET_RE.match(line)
            if match:
                try:
                    rows.append(tuple(float(v.replace("D", "E")) for v in match.groups()))
                except ValueError:
                    continue
    data = np.array(rows, dtype=float).reshape(-1, 4)
    # one line per step; a step repeated by a restart keeps its last values
    if data.size:
        _, last = np.unique(data[::-1, 0], return_index=True)
        data = data[::-1][np.sort(last)]
        data = data[np.argsort(data[:, 0])]
    return {"time": data[:, 0], "rain": data[:, 1], "infiltrated": data[:, 2],
            "evaporated": data[:, 3]}


@dataclass
class WaterBalance:
    """The volumes of a hydrology run [m3] and what follows from them."""

    duration_s: float
    rain_m3: float
    inflow_m3: float
    outflow_m3: float
    infiltrated_m3: float
    evaporated_m3: float
    storage_m3: float
    error_m3: float
    relative_error: float
    runoff_coefficient: float
    peak_outflow_m3_s: float
    peak_time_s: float

    def lines(self) -> list[str]:
        return [
            f"rain           {self.rain_m3:14.1f} m3",
            f"inflow         {self.inflow_m3:14.1f} m3",
            f"outflow        {self.outflow_m3:14.1f} m3",
            f"infiltrated    {self.infiltrated_m3:14.1f} m3",
            f"evaporated     {self.evaporated_m3:14.1f} m3",
            f"storage        {self.storage_m3:14.1f} m3 (water left on the catchment)",
            f"error          {self.error_m3:14.1f} m3 ({100 * self.relative_error:.3f} % "
            "of the rain)",
            f"runoff coefficient {self.runoff_coefficient:.3f}",
            f"peak outflow   {self.peak_outflow_m3_s:.3f} m3/s at {self.peak_time_s:.0f} s",
        ]


def _integrate(t: np.ndarray, q: np.ndarray) -> float:
    if t.size < 2:
        return 0.0
    return float(np.trapezoid(q, t)) if hasattr(np, "trapezoid") else float(
        np.trapz(q, t))


FINAL_KEYS = {
    "INITIAL VOLUME": "initial",
    "FINAL VOLUME": "final",
    "VOLUME THAT ENTERED THE DOMAIN": "entered",
    "VOLUME ADDED BY SOURCE TERM": "source",
    "TOTAL VOLUME LOST": "lost",
}


def read_final_balance(path: str | Path) -> dict[str, float] | None:
    """TELEMAC-2D's ``FINAL BALANCE OF WATER VOLUME``, or None for an unfinished run.

    ``entered`` is the net volume through the liquid boundaries (negative leaving), ``source`` the net volume of the source terms, here rain less infiltration and evaporation, and ``lost`` TELEMAC's own mass error. A closed domain prints the volumes only; the others are then 0.
    """
    out: dict[str, float] = {}
    seen = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "FINAL BALANCE OF WATER VOLUME" in line:
                seen, out = True, {}
                continue
            if not seen or ":" not in line:
                continue
            key, _, rest = line.partition(":")
            name = FINAL_KEYS.get(key.strip())
            if name:
                try:
                    out[name] = float(rest.split()[0].replace("D", "E"))
                except (IndexError, ValueError):
                    continue
    if not seen or "initial" not in out or "final" not in out:
        return None
    # a closed domain prints neither a boundary volume nor, without rain, a source
    out.setdefault("entered", 0.0)
    out.setdefault("source", 0.0)
    out.setdefault("lost", 0.0)
    return out


def balance(sortie, budget: dict[str, np.ndarray],
            final: dict[str, float] | None = None) -> WaterBalance:
    """The water balance from TELEMAC's fluxes and the routine's volumes.

    With TELEMAC's final balance the storage and the net boundary volume are exact; the split of the boundary volume into inflow and outflow comes from the printed fluxes (integrated from t = 0, where nothing flows yet). Without it, as for a run that was stopped, the volume at t = 0 is taken back from the first printout.
    """
    t = np.concatenate([[0.0], np.asarray(sortie.time, dtype=float)])
    outflow = np.concatenate([[0.0], np.asarray(sortie.gross_out, dtype=float)])
    inflow = np.concatenate([[0.0], np.asarray(sortie.gross_in, dtype=float)])
    in_volume = _integrate(t, inflow)
    out_volume = _integrate(t, outflow)
    if budget["time"].size:
        rain = float(budget["rain"][-1])
        infiltrated = float(budget["infiltrated"][-1])
        evaporated = float(budget["evaporated"][-1])
    else:
        rain = infiltrated = evaporated = 0.0
    if final is not None:
        storage = final["final"] - final["initial"]
        # the net boundary volume is exact; keep the printed split consistent with it
        if in_volume > 0.0:
            out_volume = in_volume - final["entered"]
        else:
            out_volume = 0.0 - final["entered"] + 0.0
    elif sortie.time.size and budget["time"].size:
        # the source terms up to the first printout, from the routine's own line
        k = int(np.argmin(np.abs(budget["time"] - sortie.time[0])))
        net0 = budget["rain"][k] - budget["infiltrated"][k] - budget["evaporated"][k]
        initial = float(sortie.volume[0] - net0 + 0.5 * sortie.time[0]
                        * (sortie.gross_out[0] - sortie.gross_in[0]))
        storage = float(sortie.volume[-1]) - initial
    else:
        storage = float(sortie.volume[-1] - sortie.volume[0]) if sortie.time.size else 0.0
    error = rain + in_volume - out_volume - infiltrated - evaporated - storage
    k = int(np.argmax(outflow))
    return WaterBalance(
        duration_s=float(t[-1]), rain_m3=rain, inflow_m3=in_volume,
        outflow_m3=out_volume, infiltrated_m3=infiltrated, evaporated_m3=evaporated,
        storage_m3=storage, error_m3=error,
        relative_error=error / rain if rain > 0 else float("nan"),
        runoff_coefficient=out_volume / rain if rain > 0 else float("nan"),
        peak_outflow_m3_s=float(outflow[k]), peak_time_s=float(t[k]))


def plot(path: Path, sortie, forcing_table, title: str) -> Path:
    """Rain on top, outlet discharge below, one time axis (two panels, no twin axis)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (top, bottom) = plt.subplots(2, 1, figsize=(7.2, 5.6), sharex=True,
                                      gridspec_kw={"height_ratios": [1, 2]})
    hours = 3600.0
    if forcing_table is not None and len(forcing_table) > 1:
        t = forcing_table["time_s"].to_numpy(dtype=float)
        r = forcing_table["rain_mm_h"].to_numpy(dtype=float)
        top.bar(t[:-1] / hours, r[:-1], width=np.diff(t) / hours, align="edge",
                color="#7FA7CF", edgecolor="#FFFFFF", linewidth=0.6)
    top.set_ylabel("rain (mm/h)")
    top.invert_yaxis()
    top.set_title(title)
    bottom.plot(np.asarray(sortie.time) / hours, sortie.gross_out, lw=2.0,
                color="#1F4E78")
    if np.any(np.asarray(sortie.gross_in) > 0):
        bottom.plot(np.asarray(sortie.time) / hours, sortie.gross_in, lw=1.4, ls="--",
                    color="#5B6B7A", label="inflow")
        bottom.plot([], [], lw=2.0, color="#1F4E78", label="outflow")
        bottom.legend(frameon=False)
    bottom.set_ylabel("discharge at the outlet (m3/s)")
    bottom.set_xlabel("time (h)")
    end = float(np.max(sortie.time)) / hours if len(sortie.time) else None
    for ax in (top, bottom):
        ax.set_axisbelow(True)
        ax.grid(True, color="0.9", lw=0.6)
        if end:
            ax.set_xlim(0.0, end)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def analyze(cfg, *, out_dir: Path | None = None) -> WaterBalance:
    """Write the hydrograph and the water balance of the last hydrology run.

    Files, in ``<postprocessing>/hydrology/`` unless *out_dir* says otherwise: ``hydrograph.csv`` (time, every boundary flux, outflow), ``hydrograph.png``, ``water-balance.csv`` and ``water-balance.json``.
    """
    import pandas as pd

    from axqua.solvers.telemac.sortie import latest_sortie, read_sortie

    listing = latest_sortie(cfg.model_dir, cfg.hydrology_cas_file)
    if listing is None:
        raise FileNotFoundError(f"no listing of {cfg.hydrology_cas_file} in "
                                f"{cfg.model_dir}; run the hydrology simulation first")
    sortie = read_sortie(listing)
    if sortie.time.size == 0:
        raise ValueError(f"{listing.name} holds no water balance yet")
    budget = read_budget(listing)
    result = balance(sortie, budget, read_final_balance(listing))
    out = Path(out_dir) if out_dir else Path(cfg.postprocessing_dir) / "hydrology"
    out.mkdir(parents=True, exist_ok=True)
    frame = sortie.to_frame()
    frame["outflow (m3/s)"] = sortie.gross_out
    frame.to_csv(out / "hydrograph.csv")
    forcing = None
    forcing_path = cfg.model_path(cfg.forcing_file)
    if forcing_path.exists():
        forcing = pd.read_csv(forcing_path, sep=r"\s+", comment="#", skiprows=3,
                              header=None,
                              names=["time_s", "rain_mm_h", "evaporation_mm_h"])
    plot(out / "hydrograph.png", sortie, forcing,
         f"{cfg.name}: rain and outlet hydrograph")
    pd.DataFrame([asdict(result)]).to_csv(out / "water-balance.csv", index=False)
    (out / "water-balance.json").write_text(json.dumps(asdict(result), indent=2) + "\n")
    for line in result.lines():
        log.info("  water balance: %s", line)
    if np.isfinite(result.relative_error) and abs(result.relative_error) > 0.01:
        log.warning("  the water balance closes only to %.2f %% of the rain; the "
                    "listing printouts may be too far apart for the integral of the "
                    "outflow, or the run lost water", 100 * result.relative_error)
    return result
