# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A TELEMAC hydro-morphodynamic model of a reach of the **Inn river in Bavaria, Germany**. The goal is a 2D depth-averaged hydraulic + sediment-transport simulation (TELEMAC-2D coupled with GAIA), set up automatically and calibrated with quantified uncertainty via [HydroBayesCal](https://github.com/Ecohydraulics/hydrobayescal).

**Overarching aim:** largely automate *both* sides of the workflow around HydroBayesCal - **pre-processing** (geodata → ready TELEMAC case) and **post-processing** (field **ground-truth** → calibration targets + result extraction). Pre-processing (stages 1–5) is built for the 2D hydraulic path; the **post-processing / ground-truth ingestion is the active frontier** (turning FlowTracker velocity/depth, grain-size distributions, and DEM-of-Difference topographic change into the calibration-points CSV and GAIA inputs HydroBayesCal consumes). See "Ground-truth & calibration data" below.

Each case lives in `cases/<name>/` and tracks only its case config and workflow scripts (`cases/case-template/` is the data-free starting point, `cases/example-Inn/` the worked example). Input data live in `user-sources/`, produced artifacts in `axqua-case/`, split by phase: `preprocessing/`, `simulation/`, `postprocessing/` (with `mesh-convergence/`, `vertical-convergence/`), `calibration-validation/`; each phase's script writes its outputs and its `axqua.log` into its own subfolder. Both folders are **gitignored** (plus a safety net of binary/geo extensions), and `.github/workflows/file-size-limit.yml` fails any push/PR with a tracked file over 20 MB. Don't assume the data files are present in a fresh checkout. All config paths resolve relative to the config file's directory.

## The `axqua` workflow

`pipeline.run` (`src/axqua/pipeline.py`) builds a case in five stages: (1) DEM clip and DEM of difference, (2) mesh and SELAFIN geometry, (3) boundary classification and `.cli`, (4) friction table and steering files, (5) ground truth and the HydroBayesCal inputs. A case runs as `preprocessing.py` -> `initial_run.py` -> `mesh_convergence_study.py` -> `run_Bayes_cal.py`. **Read the `telemac-build` skill before changing any stage, the initial conditions (dry start, pre-wetting), the steady run, flux convergence, the hotstart or the wetting report.** Rules that hold everywhere:

- The inflow boundary code is `4 5 5` (LIHBOR 4, never 5), or TELEMAC's DEBIMP aborts on a dry inflow; the IPOBO of the mesh boundary walk must stay consistent with the `.cli` row order.
- The geometry SELAFIN is written in double precision; single precision collapses sub-meter cells on UTM coordinates.
- The calibration target is the velocity at 0.6 h above the riverbed (profile rule `log-profile`, the user's rule of 2026-11-11); a recorded MeasD counts from the surface.
- Calibration CSV schema (read by HydroBayesCal): `id, x, y, z, <QTY>_DATA, <QTY>_ERROR`, where `<QTY>` is a SELAFIN name (e.g. `WATER DEPTH`, `SCALAR VELOCITY`).

### Meshing (stage 2)

Documented in `src/axqua/solvers/telemac/CLAUDE.md` (gmsh + BAMG anisotropic meshing, aspect-ratio cap, BAMG retry ladder, quality assessment, isotropic fallback, mesh-zones vs roughness-zones). **The anisotropic mesh is not reproducible run to run** (~0.4 % node-count spread from BAMG retries): never byte-compare `geometry.slf` / `boundaries.cli` / `initial-conditions.slf` between runs; regression-test on mesh statistics within ~1 %.

### HydroBayesCal calibration, 3D, gain-lose and unsteady extensions

Step 3 (`axqua/bayescal.py` + `axqua/campaigns.py`), the optional 3D / gain-lose / unsteady extensions and the OpenFOAM Bayesian calibration backend are documented in the `hbc-calibration` skill (`.claude/skills/hbc-calibration/SKILL.md`).

### The OpenFOAM free-surface extension

Documented in `src/axqua/solvers/openfoam/CLAUDE.md`, loaded when working on the OpenFOAM backend.

### Fine sediment over a bed that stays in place (`fine_sediment`, `solvers/openfoam/sediment.py`)

Documented in `src/axqua/solvers/openfoam/CLAUDE.md`. Morphodynamics with OpenFOAM is fine sediment over a bed that stays in place, not a moving bed (the user, 2026-10-12: any elevation change on the Isar is a DGPS artifact). **Never use a DEM of difference, a bed elevation change or a scour depth of the Isar as ground truth, as a calibration target or as a result worth showing.**

### Mesh-convergence study and build prerequisites

The mesh-convergence study (`axqua/convergence.py`, r = 1.3 four-mesh ladder, GCI verdict, resumable and auto-refining) is documented in `src/axqua/solvers/telemac/CLAUDE.md`; read it before changing the study.

## Post-processing with VisIt (`axqua/postproc/`)

Documented in `src/axqua/postproc/CLAUDE.md`, which also covers `axqua export`: TELEMAC results as VTK for ParaView and VisIt (neither reads SERAFIN). Two things there concern more than the export. **A TELEMAC result stores its coordinates in single precision**, which at a UTM northing is a spacing of 0.5 m: 14 % of the cells of the isar-2025 baseline mesh have no area in the coordinates of its own `r2d.slf`. Fields are right, the mesh in the file is not, so anything spatial takes x and y from `geometry.slf` (the export, `axqua.validation` and, since 2026-11-02, the mesh and vertical convergence studies do; the wetting report and QGIS drawing the result still read the stored ones). And `core.selafin.SelafinFile` reads one frame at a time where `read_slf` loads all of them.

## Structures: dams, weirs, walls and buildings (`axqua/core/structures.py`)

Documented in `src/axqua/core/CLAUDE.md`. In short: no STL anywhere; `overflow` structures raise the bed to the crest, `solid` ones remove their footprint from the domain (burnt into the TELEMAC bed as crest + `solid_freeboard_2d`); unrecognized text is `overflow` on purpose.

## The config schema: round-tripping, classification, and typed errors

Documented in `src/axqua/core/CLAUDE.md` (`dump_config` and `axqua migrate`, which settings are shared and which belong to a solver in `core/schema.py`, the typed errors of `core/errors.py`).

## The profile and the case file: the computer and the reach are two files

Documented in `src/axqua/core/CLAUDE.md` (resolution order, `installations`, compatibility rules, `axqua case|check|schema`). A case file (`*.axq-case`) names nothing that belongs to one computer; the profile (`*.axq-profile`) describes a computer and nothing else. A computer without a profile behaves exactly as before. A profile outlives the version that wrote it: never drop unknown entries, never overwrite an unreadable profile without a `.bak`. `cases/munich-vsf/` and `cases/slot-flume/` belong to the second workstation; do not convert or edit them.

## Releases and the PyPI package

The release procedure (version in four places, changelog, auto-release, trusted publishing, `scripts/check_wheel.py`) is in the `release` skill. PyPI never takes a version twice. Tests that read the checkout find it from their own file (`Path(__file__).parents[1]`), never from `axqua.__file__`, or they break against an installed wheel.

## Installing the simulation programs (`axqua/install/`, `axqua install`) and what an installation can do (`core/installation.py`)

Documented in `src/axqua/install/CLAUDE.md`. aXqua carries no installer except the vendored OpenFOAM one; the user's repository `/srv/private/numerical-software-installers` is read and run, never edited. **Never run the admin (`system` scope) steps with a real password.**

## Validation: the calibrated model in another flow situation (`axqua/validation.py`)

**The user's rule, 2026-10-08: every dataset given to HydroBayesCal is calibration data, and validation needs a second dataset from another situation. No splits** - no leave-one-out, k-fold or random share, in the library, the plugin or the docs, because the points of one survey in one flow field are not independent. The validation run, the calibrated values it uses and the comparison are documented in the `hbc-calibration` skill.

## The documentation in three languages, on two hosts

Documented in `docs/CLAUDE.md` (`deploy.sh`, `docs/i18n/`, the translation catalogs and Argos repairs). Publish only through `bash deploy.sh [--push]`; never edit gh-pages by hand.

## Help and Clean Up in the plugin (`axqua docs`, `axqua cleanup`)

Help is documented in `docs/CLAUDE.md`, Clean Up in `qgis_plugin/CLAUDE.md`. Clean Up never offers `user-sources`, geometry and initial conditions, the case file, the built model or the last time step, and refuses while a job of the case is not terminal.

## Documentation: the outline is a contract

Documented in `docs/CLAUDE.md`. The outline is pinned by `tests/test_docs_outline.py`, the build is warning-free (`-W` in CI), and the UML diagrams are generated by `scripts/build_uml.py` (a new top-level class or a new package import fails a test until it is re-run). Writing rules are the user's: Bachelor-level hydraulic engineers, American English, no em dash, no hard-wrapped lines, and a function that does not exist yet gets one "not yet available" note.

## The job system: simulations that outlive QGIS (`axqua/jobs/`)

Documented in `src/axqua/jobs/CLAUDE.md`.

## The QGIS plugin (`qgis_plugin/axqua/`)

Documented in `qgis_plugin/CLAUDE.md`.

## The example cases (`cases/example-isar/`, `cases/example-agrohydrology/`, `axqua example`)

Documented in `cases/CLAUDE.md` (what the fast example found, how examples are packaged and indexed, the FlowTracker campaigns). "The example cases" means the user's own examples (Isar, Inn), never TELEMAC's. **Use `cases/example-isar/` whenever a change has to be tried through the whole chain** (build 1 min, steady run 1.5 min, calibration 16 min); the research case costs a day. Editing an example's guide or case file needs `scripts/build_example_data.py --index`, or every download refuses that file.

## Package structure: solver-agnostic core + pluggable backends

Documented in `src/axqua/core/CLAUDE.md` (registry, capabilities and `MODEL=` markers, lazy imports). **Two rules, enforced by tests** (`tests/test_capabilities.py`): nothing in `core` imports a solver, and the OpenFOAM backend never reaches into the TELEMAC backend. **Every historic import path still works**: `axqua/mesh.py`, `steering.py`, `pipeline.py` and the others are shims that alias through `sys.modules`; never turn them into re-export modules.

## Hydrology mode: rainfall-runoff with Green-Ampt (`axqua/hydrology/`, TELEMAC only)

Documented in `src/axqua/hydrology/CLAUDE.md` (Green-Ampt kernels, outlets, wind and evaporation, the checkup, the data wizard, river lines, GAIA sediment in the mode, and the `cases/example-agrohydrology/` example). Read `Config.hydrology_mode`, `cfg.outflow_condition` and `Config.wet_depth`, never the raw fields; compare outlet kinds via `core.boundaries.OUTLET_LEVELS`, never condition names. Never `git add` `cases/agrohydrology/` (its PNGs and `.cas` are not ignored).

## Coordinate system - non-negotiable

**Everything is EPSG:25832 (ETRS89 / UTM Zone 32N), units in meters.** This was verified for the DEMs, all shapefiles, and the gauge metadata. Any new raster, mesh, or shapefile must use the same CRS, or downstream alignment (DEM interpolation onto the mesh, breakline snapping) will silently break.

Vertical datum varies across the gauge data (DHHN12 / DHHN2016 / "m NN" vs "m NHN") - check and reconcile datum before using gauge zero-point heights as model boundary elevations.

### Ground-truth & calibration data

Documented in the `hbc-calibration` skill (tidy table, calibration-target template, FlowTracker layouts, position layers in other CRSs). Gotcha: the Inn FlowTracker/GSD `.xlsx` files break `openpyxl`'s style parser, so `pandas.read_excel` fails on them; parse `xl/worksheets/sheet1.xml` + `xl/sharedStrings.xml` directly.

## Working conventions

- Two traps in the test suite itself: tests built on `git grep` (`test_no_tracked_source_or_test_hard_codes_a_home_directory`) see **tracked files only**, so a new file passes locally until it is committed - `git add` before the final run; and CI runs `pytest`, not `python -m pytest`, so the repository root is not on `sys.path` and `from tests.x import ...` fails there (import `from x import ...` like the other test modules do). The `tests` workflow had been red on `main` since at least 2026-10-03 for the second reason, which is how the first went unnoticed.
- Large rasters (the DEMs/orthos run to hundreds of MB) - don't open them blindly; use `gdalinfo`/`rasterio` to read headers, and window/downsample for any processing.
- Keep raw input data immutable. Generated artifacts go under the configured phase dirs in `cases/example-Inn/axqua-case/` (preprocessing/ simulation/ postprocessing/ mesh-convergence/ calibration-validation/), not mixed into `user-sources/`.
- Per-case `user-sources/` (input data) and `axqua-case/` (produced artifacts) are large/local and gitignored; only the package, the case config + scripts, and docs are tracked.

## The history was rewritten (2026-10-10, 2026-11-01 and 2026-11-02)

On the user's order the history was rewritten three times: on 2026-10-10 to remove a 71 MB zip (`git filter-repo`), on 2026-11-01 condensed to **one commit per release** (`aXqua <version>`), with the host names of the two developer computers replaced by "the main workstation" and "the second workstation" in every version of every file (`tests/test_machine.py` keeps them out). On 2026-11-02 the institute's folder names (`/home/<institute>/...`) were replaced the same way in every version of every file (`git filter-repo --replace-text`), and a second test in `tests/test_machine.py` keeps them out. **Commit ids cited in these notes, in comments and in old messages (`8f34806`, `557d898`, `e1404af`, ...) do not exist in this history.** Backups on the main workstation: `/srv/private/axqua-backup-before-squash-2026-11-01.git` (GitHub mirror) with `-local.bundle` (local repository incl. gh-pages), `/srv/private/axqua-backup-before-history-rewrite-2026-10-10.git` (with `commit-map-old-to-new.txt`), and `/srv/private/axqua-backup-before-folder-names-2026-11-02.git` with `-local.bundle`.

- **Never merge or push a clone or branch from before these dates**: it would bring the removed history back, and nothing on GitHub prevents it. A plain `git pull` there refuses ("unrelated histories"), which is the intended warning. The way over is `git fetch origin && git reset --hard origin/main`, or a fresh clone; unpushed commits are saved first (`git format-patch`) and carried over with `git am`, `git cherry-pick` or `git rebase --onto`, never with a merge.
- GitHub keeps the old commits until its own cleanup runs, and two merged pull requests hold a hidden reference to them that only GitHub Support can remove; a clone does not fetch any of that.
