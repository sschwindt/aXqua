"""What a hydrology run produced: the outlet hydrograph and the water balance.

Two sources in the listing of the run (``hydrology2d.cas_<date>.sortie``):

* TELEMAC's water balance at every listing printout (:mod:`axqua.solvers.telemac. sortie`): the volume in the domain and the flux through every liquid boundary, positive entering. The outlet hydrograph is the sum of the exiting fluxes;
* the line ``AXQUA HYDROLOGY t rain infiltrated evaporated`` that aXqua's runoff routine writes at the same printouts: the volumes of rain, infiltration and evaporation since the start [m3].

The balance closes as ``rain + inflow = outflow + infiltrated + evaporated + storage + error``; ``error`` is what TELEMAC's mass conservation and the integration of the printed fluxes leave, and is reported relative to the water that entered (rain and inflow). The runoff coefficient is the net outflow (outflow minus inflow) over the rain, which without an inflow line is outflow over rain.
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
            "of the water that entered)",
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
        # relative to all water that entered: with a river through the catchment the rain may be a small part of it, and the error of integrating the printed fluxes scales with the river, not with the rain
        relative_error=(error / (rain + in_volume) if rain + in_volume > 0
                        else float("nan")),
        # what the catchment adds: with a river, its own water leaves again
        runoff_coefficient=((out_volume - in_volume) / rain if rain > 0
                            else float("nan")),
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


_GAIA_TOTAL = "GAIA MASS-BALANCE OF SEDIMENTS OVER ALL CLASSES"
_GAIA_CLASS = "GAIA MASS-BALANCE OF SEDIMENTS PER CLASS"
_TIME = "RELATIVE ERROR IN VOLUME AT T ="


def _number_after(line: str, sep: str = "=") -> float:
    return fortran_float(line.split(sep, 1)[1].split()[0])


def fortran_float(text: str) -> float:
    """A number as Fortran prints it: ``1.5E-03``, ``1.5D-03``, and ``0.6076331-116`` (an exponent of three digits drops the ``E``)."""
    text = text.strip().upper().replace("D", "E")
    try:
        return float(text)
    except ValueError:
        import re

        match = re.fullmatch(r"([-+]?\d*\.?\d+)([-+]\d+)", text)
        if not match:
            raise
        return float(f"{match.group(1)}E{match.group(2)}")


def read_sediment(path: str | Path) -> dict | None:
    """The sediment of a run with GAIA, from its listing, or None for a run without GAIA.

    Two sources, because neither has everything. **The suspended load** is TELEMAC's tracers, whose balance blocks are cumulated over every time step: per class the mass eroded into suspension ("QUANTITY CREATED BY SOURCE TERM"), the mass that left through the liquid boundaries, and the mass in suspension at the end. TELEMAC's tracer balance has no deposition term, so the mass it reports as lost is the mass deposited; measured on a test catchment, it grew from 52 to 122 kg when infiltration was switched off, with the erosion. **The bed load** is GAIA's flux through each liquid boundary [kg/s, positive entering] in the block ``GAIA MASS-BALANCE OF SEDIMENTS OVER ALL CLASSES`` that precedes TELEMAC's water balance of each printout, or, for a single class, for which GAIA prints no such block (``mass_balance.f``: ``IF(NSICLA.GT.1)``), in the block of that class; it is integrated over the printouts. GAIA's own cumulated balance is printed only when the last time step is the step number ``NIT``, which a run with a variable time step does not reach, and its erosion and deposition fluxes are values of one step that the printouts sample too coarsely to integrate (twice the erosion of the tracer balance on the test catchment).

    Returns ``time`` and ``bedload`` (printouts x boundaries) and ``suspended``: per tracer name ``{eroded, deposited, through_boundaries, final}`` in kg (concentration in g/l times m3), ``through_boundaries`` positive entering.
    """
    rows: list[dict] = []
    block: dict | None = None
    # the bed load of the printout being read: over all classes, and summed per class
    total: dict[int, float] = {}
    per_class: dict[int, float] = {}
    seen = False
    suspended: dict[str, dict] = {}
    tracer = None
    for line in Path(path).read_text(errors="replace").splitlines():
        text = line.strip()
        if _GAIA_TOTAL in text:
            block, seen = total, True
            continue
        if _GAIA_CLASS in text:
            block, seen = per_class, True
            continue
        if block is not None:
            if not text:
                block = None
            elif text.startswith("BEDLOAD FLUX BOUNDARY"):
                index = int(text.split("BOUNDARY", 1)[1].split()[0])
                if block is total:
                    total[index] = _number_after(text)
                else:
                    per_class[index] = per_class.get(index, 0.0) + _number_after(text)
            continue
        if text.startswith(_TIME.strip()) and seen:
            rows.append({"time": float(text.split("=", 1)[1].split()[0]),
                         "bedload": dict(total or per_class)})
            total, per_class, seen = {}, {}, False
        elif text.startswith("BALANCE OF TRACER"):
            # "BALANCE OF TRACER COH SEDIMENT1   g/l": the name without the unit
            tracer = " ".join(text[len("BALANCE OF TRACER"):].split()[:-1]) or text
            suspended.setdefault(tracer, {"eroded": 0.0, "deposited": 0.0,
                                          "through_boundaries": 0.0, "final": 0.0})
        elif tracer is None:
            continue
        elif text.startswith("QUANTITY OF TRACER:"):
            suspended[tracer]["final"] = _number_after(text, ":")
        elif text.startswith("QUANTITY ENTERED THROUGH LIQ. BND."):
            suspended[tracer]["through_boundaries"] = _number_after(text, ":")
        elif text.startswith("QUANTITY CREATED BY SOURCE TERM") and "   :" in line:
            # the cumulated line ("...SOURCE TERM   :"), not the one of the step
            suspended[tracer]["eroded"] = _number_after(text, ":")
        elif text.startswith("FINAL BALANCE OF TRACER"):
            tracer = None
    for item in suspended.values():
        # no deposition term in TELEMAC's tracer balance: what it books as lost
        item["deposited"] = (item["eroded"] + item["through_boundaries"]
                             - item["final"])
    if not rows and not suspended:
        return None
    n = max((max(r["bedload"]) for r in rows if r["bedload"]), default=0)
    bedload = np.array([[r["bedload"].get(j, 0.0) for j in range(1, n + 1)]
                        for r in rows], dtype=float).reshape(len(rows), n)
    return {"time": np.array([r["time"] for r in rows]), "bedload": bedload,
            "suspended": suspended}


def sediment_budget(sediment: dict) -> dict:
    """The sediment budget of the run [kg]: the bed load through each boundary (positive entering), and of the suspended load what was eroded into suspension, deposited again, carried out through the liquid boundaries and still in suspension at the end."""
    t = np.concatenate([[0.0], sediment["time"]])
    bedload = [_integrate(t, np.concatenate([[0.0], sediment["bedload"][:, j]]))
               for j in range(sediment["bedload"].shape[1])]
    classes = sediment["suspended"]

    def total(key: str) -> float:
        return float(sum(item[key] for item in classes.values()))

    return {"bedload_kg": bedload,
            "bedload_out_kg": -sum(bedload),
            "suspended_eroded_kg": total("eroded"),
            "suspended_deposited_kg": total("deposited"),
            "suspended_out_kg": -total("through_boundaries"),
            "suspended_end_kg": total("final"),
            "out_kg": -total("through_boundaries") - sum(bedload),
            "classes": {name: {k: float(v) for k, v in item.items()}
                        for name, item in classes.items()}}


def boundary_volumes(cfg, sortie) -> list[dict]:
    """The volume through each liquid boundary over the run [m3], positive entering, with its kind and outlet from ``liquid-boundaries.json`` of the build: what a river brings in at its inflow line and takes out at its own outflow line, and what leaves over the other outflow lines."""
    t = np.concatenate([[0.0], np.asarray(sortie.time, dtype=float)])
    known: dict[int, dict] = {}
    saved = cfg.model_path(cfg.liquid_boundaries_json)
    if saved.exists():
        try:
            known = {int(b["index"]): b for b in json.loads(saved.read_text())}
        except (ValueError, KeyError, TypeError):
            known = {}
    rows = []
    for j in range(sortie.n_boundaries):
        q = np.concatenate([[0.0], np.asarray(sortie.fluxes[:, j], dtype=float)])
        line = known.get(j + 1, {})
        rows.append({"boundary": j + 1, "kind": line.get("kind", ""),
                     "outlet": (line.get("condition") or
                                (cfg.outflow_condition if line.get("kind") == "outflow"
                                 else "")),
                     "volume_m3": _integrate(t, q)})
    return rows


def analyze(cfg, *, out_dir: Path | None = None) -> WaterBalance:
    """Write the hydrograph and the water balance of the last hydrology run.

    Files, in ``<postprocessing>/hydrology/`` unless *out_dir* says otherwise: ``hydrograph.csv`` (time, every boundary flux, outflow), ``hydrograph.png``, ``water-balance.csv``, ``water-balance.json`` and ``boundary-volumes.csv`` (the volume through each liquid boundary, :func:`boundary_volumes`). The listing copies of each processor of a parallel run are deleted, as after a steady run: the balance is in the main listing.
    """
    import pandas as pd

    from axqua.solvers.telemac.sortie import latest_sortie, processor_sorties, read_sortie

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
    lines = boundary_volumes(cfg, sortie)
    sediment = read_sediment(listing)
    if sediment is not None:
        budget_kg = sediment_budget(sediment)
        for line, mass in zip(lines, budget_kg["bedload_kg"]):
            line["bedload_kg"] = mass
        (out / "sediment-budget.json").write_text(json.dumps(budget_kg, indent=2) + "\n")
        pd.DataFrame([{k: v for k, v in budget_kg.items()
                       if not isinstance(v, (list, dict))}]).to_csv(
            out / "sediment-budget.csv", index=False)
        log.info("  sediment: %.1f kg eroded into suspension, %.1f kg deposited again; "
                 "out through the liquid boundaries %.1f kg in suspension and %.1f kg "
                 "as bed load (net)", budget_kg["suspended_eroded_kg"],
                 budget_kg["suspended_deposited_kg"], budget_kg["suspended_out_kg"],
                 budget_kg["bedload_out_kg"])
    if lines:
        pd.DataFrame(lines).to_csv(out / "boundary-volumes.csv", index=False)
        for line in lines:
            log.info("  boundary %d (%s%s): %+.1f m3", line["boundary"], line["kind"],
                     f", {line['outlet']}" if line["outlet"] else "", line["volume_m3"])
    for line in result.lines():
        log.info("  water balance: %s", line)
    copies = processor_sorties(cfg.model_dir, cfg.hydrology_cas_file)
    for copy in copies:
        copy.unlink()
    if copies:
        log.info("  removed %d per-processor .sortie file(s); kept the main listing",
                 len(copies))
    if np.isfinite(result.relative_error) and abs(result.relative_error) > 0.01:
        log.warning("  the water balance closes only to %.2f %% of the water that entered; the "
                    "listing printouts may be too far apart for the integral of the "
                    "outflow, or the run lost water", 100 * result.relative_error)
    return result
