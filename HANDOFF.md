# Handoff: munich-vsf across two machines

Written from **lww-134** on 2026-09-14 for the Claude instance on **lww-133**. Temporary coordination file; delete it when the work rejoins.

## The constraint that decides everything

**None of the munich-vsf data is in git**, so lww-133 cannot rebuild or run this case:

| what | where | status |
| --- | --- | --- |
| `cases/munich-vsf/axqua-case/` | results, meshes, OpenFOAM case (~2.6 GB) | **gitignored** (`.gitignore:19`) |
| `cases/munich-vsf/user-sources/` | the contractor CAD, heightmap, flume data (1.6 GB) | not ignored, but **0 files committed** |

Only the scripts, `case-config.yml` and `README.md` are in the repo (20 files). So on lww-133: `git pull` gives you the *instructions* for this case and none of its *inputs*.

Do not try to fix this by committing the data. It is 4.2 GB of CAD and binary results; that is a deliberate `.gitignore` decision, not an oversight.

## The user is copying the data across

**The user has said they will rsync it to lww-133 by hand.** Until it arrives, take the library tasks (§ Tasks) and do not attempt to run the case. Once it arrives, § "If the data has landed" below applies and the division of labour changes.

**Check before assuming either way:**

```bash
ls -la cases/munich-vsf/axqua-case/simulation/r2d.slf \
       cases/munich-vsf/axqua-case/simulation/r3d-hydrostatic.slf \
       cases/munich-vsf/axqua-case/preprocessing/ 2>&1
```

### What is being copied

**Tier 1, ~860 MB, enough to run the OpenFOAM work without touching the CAD:**

| path | size | why |
| --- | --- | --- |
| `axqua-case/preprocessing/` | 6.4 M | DEM (`dem-initial-roi.tif`), `roi-fishpass.gpkg`, `liquid-boundaries-fishpass.gpkg`, roughness zones and table, structures |
| `axqua-case/simulation/r2d.slf` | 549 M | the converged 2D run: the lid, the wetted footprint, the boundary values |
| `axqua-case/simulation/r3d-hydrostatic.slf` | 263 M | the TELEMAC-3D pre-run, which is where the vertical velocity profile comes from |
| `axqua-case/simulation/*.cas`, `*.sortie` | ~2 M | `prerun` reads the listing to confirm the flux balance before reusing the seed |

Deliberately excluded: `r2d-initial.slf` (518 M, superseded by `r2d.slf`) and `*_old1.slf` (287 M, byte-identical duplicates).

**Tier 2, ~1.1 GB, only needed to rebuild from CAD** (a change of geometry or of `surfaces.resolution`): `user-sources/`, excluding `user-of-poor/` (459 M, the old user OpenFOAM case, reference only, read by nothing). `user-sources/ground-truth/` is 32 KB and holds the flume velocities and depths, so it is needed for any lab comparison regardless of tier.

**Not copied:** `axqua-case/openfoam/` (2.6 GB). Build your own from `case-config.yml`; copying it would hand over a half-finished run as well.

### If the data has landed

With Tier 1 present, `openfoam_preprocessing.py` reuses the existing seed rather than re-running TELEMAC (`pre_run.reuse: true`), so lww-133 can build and run an OpenFOAM case in minutes of setup.

**Then lww-133 should take the VOF run**, and say so on the issue before starting so the work is not done twice. Rationale: lww-134 is finishing the rigid-lid run and is shared with another user who periodically takes 16 of its 16 physical cores, so it is the worse machine for a second long job. Set `openfoam.mode: vof` and leave every other setting alone: the crop (`roi: roi-fishpass.gpkg`), the sealed structures, `liquid_boundaries`, `outlet_stage`, and the 3D pre-run all still apply.

Two things about that run:

* **measure dt and s/step over the first 50 steps and post them before committing days to it.** The 22-day figure quoted above predates the seal, and that run had the same leaking baffles suppressing its time step, so it is probably pessimistic. Do not repeat it as though it were a prediction.
* the user's plan is for VOF to be **seeded from the finished rigid-lid run** via `mapFields`. That seed lives on lww-134 and is not worth moving (2.6 GB). So either run VOF cold on lww-133 in parallel, which is still useful and independent, or wait for lww-134 to do the mapped version. Say which you are doing.

## Division of labour

**lww-134 (this machine) owns every run of the munich-vsf case.** The mesh, the 2D result, the TELEMAC-3D pre-run and the OpenFOAM case all live here, some of them representing days of compute that cannot be reproduced elsewhere without the source data. It is a 16-physical-core Ryzen 9 5950X shared with another user (`hunter`), who periodically takes 16 ranks for their own TELEMAC-3D work; expect this machine's throughput to halve without warning when that happens.

**lww-133 takes the library and the documentation**, all of which is fully in the repo and needs no case data.

## Where the modelling stands

The 3D sub-model of the fish pass has been through three configurations. The short version, with the numbers that matter:

**VOF (two-phase interFoam) was abandoned on cost.** 1,340,928 cells, 8.70 s/step, dt 5.7e-4 s, throughput 6.5e-5 s/s: about 22 days for the 120 s run. 60.5 % of the cells were air, and `Interface Courant Number max` equalled `Courant Number max` on every step in the log, so the air-water interface was setting the entire time step. Logs kept at `axqua-case/openfoam/vof-record/`.

**Rigid lid (`openfoam.mode: rigid-lid`) was tried instead**, because the requirement was only that the free surface be non-horizontal, not that it be solved. The lid is built from `State2D.sample_surface` per plan vertex, so it *is* the converged 2D free surface. That ran 12x faster (3.79 s/step, dt 3.0e-3, 61 h for 120 s) and its discharge balanced to -0.000 %.

**But the first rigid-lid run was wrong, and nothing in the solver output said so.** 3,719 of its cells sat pinned at the `limitVelocity` cap while continuity error stayed at 3e-8 and the Courant number sat neatly at its 0.90 ceiling for 61 hours. Two causes, both now fixed on branch `rigid-lid-applicability`:

1. **A wall thinner than the lattice blocked nothing.** `build_plan_grid` blanks a column on its *centre*, so the sheet-steel baffles (thinner than the 3 cm lattice) left the mesh joined straight through them. 8,222 columns were straddling a baffle. Solids are now blanked against themselves grown by half a cell diagonal.
2. **A rigid lid cannot represent a plunging surface.** New `mesh.lid_steps` measures the prescribed surface's local range as a fraction of the local depth; the build now warns when the 99th percentile passes 0.5. On this case it reports **172 %**, and it is right to: a vertical-slot fishway is thirteen discrete drops, so its surface steps by more than the water is deep at every slot. A lid is a slip *wall*, so it converts that head into velocity instead, `sqrt(2 g dz)`.

After sealing, the capped cells fell from 3,719 to 679, and **92.3 % of what remains sits in the 5.17 % of columns where the lid steps more than 0.30 m within 15 cm**. Those columns are the slots. The bulk field is now physical (median 0.109 m/s, p95 1.09 m/s), but the slot jets, which are the entire reason for a 3D sub-model here, are not.

**The decision (the user's, on 2026-09-12): let the rigid-lid run finish, then run VOF seeded from it.** The sealed rigid-lid run is at t=111 of 120 as this is written, ~9 h out. VOF then gets `mapFields` from the finished rigid-lid case onto its `0/`: the rigid-lid domain stops at the water surface, so the VOF freeboard cells have no source and keep their built values while every water cell starts with a developed `U`, `k` and `omega`. Note the 22-day VOF figure above predates the seal, and that run had the same leaking baffles suppressing its time step, so it is probably pessimistic. It will be measured over the first 50 steps, not assumed.

## Tasks for lww-133

Take them in this order. Work on a branch off `rigid-lid-applicability` (not `main`, which is 6 commits behind it) and open a PR per task rather than pushing to a shared branch.

### 1. Review `rigid-lid-applicability` and run the suite

`git fetch && git checkout rigid-lid-applicability && python -m pytest tests/ -q && python -m ruff check src/ tests/ cases/munich-vsf/`

Expected: 681 pass, ruff clean. A second machine and a second Python are worth having on this; everything here has only ever run on one interpreter. Read commit `c038c4a` in full, and push back on anything in it. In particular I would like a second opinion on two judgement calls:

* **sealing grows every solid by half a cell diagonal**, unconditionally. It changed `tests/test_structures.py::test_a_solid_structure_removes_columns_from_the_openfoam_lattice` from 100 removed columns to 140, and I updated the test to state the new contract. Is unconditional right, or should it be a config flag? My argument for unconditional: a wall that does not separate is not a wall, and a mesh cannot represent a solid thinner than its own cells anyway. Counter-argument worth weighing: it silently thickens every structure in every existing case, `cases/isar-2025/` included.
* **`lid_steps` warns but does not refuse.** Given it correctly predicted a 61-hour wasted run, is a `log.warning` enough?

### 2. Regression-check `cases/isar-2025/`

That case also uses structures and its OpenFOAM mode has been exercised before. You will not have its data either, so this is a code-reading and test-writing task, not a run: establish whether the seal changes its mesh materially, and if you cannot tell without running it, say so plainly rather than guessing. If it matters, the run has to happen here.

### 3. Promote the binary field reader out of the case

`cases/munich-vsf/correct_lid.py` has `read_patch_values` / `_read_list`, which read a patch's `boundaryField` values out of an OpenFOAM field in **either ASCII or binary** format. Binary matters: the case writes `writeFormat binary`, and converting a case to ASCII to read one patch rewrites every field on disk. This is generally useful and belongs in the library, probably `axqua.solvers.openfoam.postprocess` or beside `report.py`. Move it, give it tests against small fixtures of both formats, and have `correct_lid.py` import it.

Watch for the two traps already paid for: the OpenFOAM banner is ~700 bytes, so a fixed-size slice looking for the `format` keyword misses it; and in binary the payload contains `)` bytes, so the list must be found by reading its count and computing the length, never by searching for the closing paren.

### 4. Document when a rigid lid applies

There is no guidance in `docs/` on choosing between `mode: vof` and `mode: rigid-lid`, and the choice is not obvious. Write it, using this case as the worked example. The rule that emerged: a rigid lid is right when the free surface is smooth on the scale of a cell and wrong when it has steps comparable to the depth, so it suits a graded channel and rules itself out at a weir, a drop structure or a fish pass slot. Cover what the mode costs even when it applies (the surface cannot rise, overtop or wet a dry bar; the waterline is a fixed vertical wall; `outlet_stage` becomes a pressure datum rather than a stage) and how to recover the surface afterwards via `correct_lid.py`.

### 5. If you run out of the above

`correct_lid.py` is written but has never completed a real pass, because the run it was pointed at turned out to be invalid. It writes a corrected 2D SELAFIN via `axqua.core.selafin._write_selafin`, using a private function. Either make that public with a supported signature or give the script a sanctioned route. Its `IMPLAUSIBLE = 0.5` m guard fired correctly on the bad run, which is the only part of it proven so far.

## Rules of engagement

**Do not run anything in `/srv/private/axqua/cases/munich-vsf/axqua-case/` from lww-133**, even if the directory somehow exists there. Long runs on this machine are stopped and restarted by hand and a second writer would corrupt them silently.

**Do not merge to `main`.** `main` is 6 commits behind `rigid-lid-applicability` and the user has not asked for a merge. Open PRs and leave them.

**State what you measured and what you assumed, separately.** The 61 hours lost on this case were lost because "continuity error 3e-8, Courant 0.90, discharge balanced" was reported as a healthy run. Those numbers were all true. They measure the solver's bookkeeping and say nothing about whether the physics is right, and no one had written down which is which. If you report a check passing, say what it would have caught.

## Report back

In the PR, or by commenting on the issue that points here:

1. whether the Tier 1 data has arrived yet (this re-divides the work, see above), and whether Tier 2 came with it;
2. its core count and whether TELEMAC and OpenFOAM are installed, and at what paths (do not assume they match lww-134's `/home/modelling/...`);
3. `pytest` and `ruff` results on a second interpreter;
4. your verdict on the two judgement calls in task 1.
