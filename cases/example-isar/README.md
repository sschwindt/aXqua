# Example: Isar River in QGIS, from preprocessing to Bayesian calibration

This example runs the complete TELEMAC workflow of aXqua from the QGIS plugin: build a two-dimensional (2D) model from geodata, run it to a steady state, load the result into QGIS, and calibrate two roughness values against measured flow velocities with HydroBayesCal. Every step is a button in the plugin. The same steps are listed as terminal commands at the end.

The example uses the input data of the research case in `../isar-2025` and a mesh that is three times coarser, so that the whole workflow takes about half an hour instead of more than a day.

## What the example computes

The model covers a braided reach of the Isar River of about 6.6 ha. The terrain model and the boundary conditions stem from a survey in March 2025, when the discharge was 2.4 m³/s. It enters through two inflow lines (1.6 and 0.8 m³/s) and leaves through one outflow line. Between them, the river loses about 65 L/s into a gravel bar and gains this discharge back farther downstream. A depth-averaged model has no groundwater, so aXqua takes the discharge out of the surface flow along the losing line and returns it along the gaining line (block `gain_lose` of the case file).

| Item | Value in this example |
| --- | --- |
| Case file | `example-isar.axq-case` |
| Mesh | about 26,500 nodes and 52,600 triangles, edge length about 0.9 m across the channel |
| Bed roughness | equivalent sand roughness k_s in six zones, from 0.004 m (sand) to 0.5 m (vegetation) |
| Turbulence model | Spalart-Allmaras, selected by aXqua for this cell size |
| Simulated time | 3000 s; inflow and outflow balance to within 0.1 % after about 2200 s |
| Measurements | 84 verticals of a SonTek FlowTracker2, measured on September 30, 2025; the reading closest to 0.6 of the water depth is used |
| Calibration parameters | k_s of roughness zone 4 (coarse gravel, 50 verticals) and of zone 6 (vegetation, 21 verticals) |
| Calibration runs | 8 initial simulations and 4 simulations selected by Bayesian active learning |

**The measurements and the model describe two different flows.** The velocities were measured on September 30, 2025, when the transects of the FlowTracker2 recorded about 5.3 m³/s in the main channel. The model simulates the 2.4 m³/s of the survey in March 2025, because the terrain model and the boundary conditions of the research case belong to that survey. The calibration of this example therefore demonstrates the workflow and the diagnostics of HydroBayesCal, and it does not yield roughness values for the reach. Step 6 shows how the calibration result itself reveals the mismatch.

Measured run times with 8 processor cores on a workstation with 16 cores:

| Step | Button in the plugin | Run time |
| --- | --- | --- |
| Build the model | *Steady 2D* > *Build* | 1 min |
| Steady simulation | *Steady 2D* > *Submit* | 1.5 min |
| Bayesian calibration | *Calibration (BAL)* > *Submit* | 18 min |
| Mesh convergence study (optional) | *Mesh convergence* > *Submit* | 20 min |
| Three-dimensional model (optional) | *Steady 3D* > *Build*, then *Submit* | 19 min |

## Requirements

1. **The input data of the Isar case.** The case file reads its geodata and measurements from `../isar-2025/user-sources`. This folder is not part of the repository because of its size (about 0.9 GB). The example writes nothing into it.
2. **aXqua with a TELEMAC installation.** Run `axqua profile init` once in a terminal. It writes the profile of this computer with the TELEMAC installation it finds. Then run `axqua profile check`, which starts TELEMAC once and reports every problem with its remedy. A warning about OpenFOAM does not affect this example. The section *Installation & Configuration* of the documentation explains how to register a TELEMAC installation that aXqua does not find by itself.
3. **HydroBayesCal 1.8.1 or newer** in the Python environment of aXqua. The Python Package Index currently provides version 1.7.0 only, so install it from the tagged source:

   ```bash
   pip install "git+https://github.com/Ecohydraulics/hydrobayescal.git@v1.9.0"
   ```

4. **QGIS 3.44 or newer.** Many Linux distributions ship an older QGIS. The script `scripts/qgis_dev.sh` starts the QGIS of a conda environment instead, which is created once with:

   ```bash
   conda create -n qgis-dev -c conda-forge "qgis>=3.44"
   ```

## Start QGIS with the plugin

```bash
scripts/qgis_dev.sh
```

The script starts QGIS with a user profile of its own (`axqua-dev`), links the plugin folder of this repository into that profile, and enables the plugin. Other QGIS profiles are not changed. The script also tells the plugin which `axqua` program to call: the one of the conda environment `axqua-env` if it exists, otherwise the first one on the search path. To use another program, set the variable `AXQUA_EXE` before starting the script or enter the path in the plugin under *Plugins > aXqua > Settings...*.

In QGIS, open the panel with the aXqua button in the toolbar or with *Plugins > aXqua > aXqua panel*. The panel docks on the right side of the window.

## Step 1: Open the case

1. Go to the tab *Setup*. The first line names the `axqua` program and its version. If it reports an error, click *Settings...* and select the program.
2. Click *Add case...* and select `cases/example-isar/example-isar.axq-case`.
3. The line at the bottom of the tab now reads `telemac - environment ok`, followed by what the case can do. The plugin has added one tab for each of these capabilities.

*Save project* stores the list of cases in a small project file (`.axqua-prj`), so that the case is listed again after a restart of QGIS. This is optional.

## Step 2: Build the model

1. Go to the tab *Steady 2D*. The first line states `configured`: the case file asks for a steady 2D simulation, and nothing has been built yet.
2. Click *Build*. The plugin submits a job and switches to the tab *Jobs*.
3. Wait until the state of the job is `COMPLETED` (about 1 min).

The build clips the terrain model to the model outline, generates the mesh, interpolates the bed elevation and the roughness zones onto the mesh nodes, assigns the boundary conditions, and writes the TELEMAC input files into `axqua-case/simulation/`. The file `axqua.log` in the same folder reports the mesh quality and every decision of the build, for example the selected turbulence model.

A job runs independently of QGIS. QGIS can be closed while a job runs, and the *Jobs* tab shows the job with its current state when QGIS is opened again.

## Step 3: Run the steady simulation and load the result

1. Go back to the tab *Steady 2D*, which now states `configured, built`, and click *Submit*.
2. On the tab *Jobs*, the column *Progress* shows the simulated share of the 3000 s and the number of time steps. The run is complete after about 1.5 min.
3. Select the completed job and click *Load results*.

QGIS adds three layers in the group `axqua` of the layer panel:

| Layer | Content |
| --- | --- |
| *TELEMAC result (steady)* | water depth in blue; cells with less than 1 cm of water are transparent |
| *TELEMAC result (steady) - velocity* | flow velocity from yellow (slow) to purple (fast) with arrows; dry ground and standing water are transparent |
| *Mesh geometry* | the mesh with the bed elevation |

The layers carry the coordinate reference system of the case (EPSG:25832), so they align with a base map or an orthophoto. The water depth lies on top. Switch it off in the layer panel to see the flow velocity, and switch both result layers off to see the bed elevation.

To compare the result with the measurements, add the layer `../isar-2025/user-sources/ground-truth/flowtracker/Sep25/TKE_Isar_Sep25.gpkg` to the map.

## Step 4: Check that the simulation reached a steady state

A steady simulation is only useful when the discharge that leaves the model equals the discharge that enters it. aXqua evaluates this after every steady run and writes the following files into `axqua-case/simulation/`:

| File | Content |
| --- | --- |
| `flux-convergence.png` | discharge through each open boundary over the simulated time |
| `convergence-rate.png` | relative difference between inflow and outflow, with the tolerance of 0.1 % marked |
| `wetting-report.csv` | wetted area split into flowing water, stagnant film, and isolated puddles |
| `outlet-profile.csv` | water surface, depth, and Froude number in bands upstream of the outflow line |

In this example, the difference between inflow and outflow falls below 0.1 % after about 2200 s of simulated time. The button *View logs* on the tab *Jobs* shows the same numbers in the log of the job.

## Step 5: Calibrate the roughness

1. Go to the tab *Calibration (BAL)* and click *Submit*. Leave the option *Prepare only* unchecked.
2. The tab *Jobs* shows the number of the current simulation in the column *Progress*, for example `iter 5/12`. The calibration takes about 18 min.

The calibration first writes the measurements into the table `axqua-case/calibration-validation/measurements-calibration.csv`, with one row per vertical. HydroBayesCal then proceeds in two stages. It runs the model for eight combinations of the two roughness values that cover the ranges given in the case file (0.02 to 0.30 m for zone 4 and 0.10 to 0.80 m for zone 6), and trains a surrogate model on the results. A surrogate model is a fast statistical approximation of the simulation, here a Gaussian process. In the second stage, Bayesian active learning selects four additional combinations, one after the other, at which a simulation improves the estimate of the roughness values the most.

With *Prepare only* checked, the job writes the table of measurements and the configuration of HydroBayesCal and stops. This is a quick way to inspect the calibration inputs before spending the computing time.

Run one job of a case at a time. HydroBayesCal runs its simulations in the folder of the built case, and a second job in that folder would overwrite its files. When the calibration has finished, aXqua restores the friction table and the steering file of the built case.

## Step 6: Read the calibration result

HydroBayesCal writes its results into `axqua-case/calibration-validation/auto-saved-results-HydroBayesCal/`:

| File | Content |
| --- | --- |
| `plots/SCALAR VELOCITY/calibration-target-agreement.png` | modeled against measured flow velocity at the 84 verticals, before and after the calibration |
| `calibration-data/SCALAR VELOCITY/collocation-points-SCALAR VELOCITY.csv` | the 12 tested combinations of the two roughness values |
| `calibration-data/SCALAR VELOCITY/model-results-calibration-SCALAR VELOCITY.csv` | the modeled velocity at each vertical, one row per simulation |
| `calibration-data/SCALAR VELOCITY/model-results-extraction.csv` | the same with the water depth in addition |
| `surrogate-gpe/` | the trained surrogate models |

The file `logfile.log` in `axqua-case/calibration-validation/` reports the most probable roughness values after every iteration and ends with an assessment of the agreement between the model and the measurements. The results of the 12 simulations remain in `axqua-case/simulation/` as `r2d_1.slf` to `r2d_12.slf` and can be added to the map as mesh layers.

Read the result in the order of the section *Quality analysis* of the documentation. In this example, it reads as follows:

1. **The calibrated model does not reproduce the measurements.** The modeled velocities are lower than the measured ones at more than 80 % of the verticals. The mean difference is about 0.40 m/s (48 %) before the calibration and still about 0.35 m/s (42 %) after it. The root-mean-square error decreases only from 0.51 to 0.47 m/s.
2. **The most probable roughness values lie at the lower limits of their ranges**, at about 0.03 m in zone 4 and 0.11 m in zone 6. The calibration reduces the roughness as far as the ranges allow in order to accelerate the flow, and the modeled flow remains too slow.

A systematic difference together with parameter values at a limit indicates an error that no roughness value can compensate. Here, the cause is known: the model simulates 2.4 m³/s, and the velocities were measured at about 5.3 m³/s. Wider roughness ranges would not solve this. The mesh is not the cause either, because the research model with its mesh of 230,000 nodes yields the same mean velocity at these verticals (0.46 m/s, against 0.85 m/s measured).

A calibration that determines the roughness of this reach requires velocity measurements and boundary conditions of the same day. The numbers of a repeated run differ slightly from those above, because the mesh generator does not reproduce a mesh node by node.

## Optional steps

**Mesh convergence.** The tab *Mesh convergence* repeats the steady simulation on four meshes with a refinement ratio of 1.3 (edge lengths of 1.17, 0.90, 0.69, and 0.53 m across the channel) and compares water depth and flow velocity at the 84 measurement verticals. The study takes about 20 min and writes its report into `axqua-case/postprocessing/mesh-convergence/` as a workbook (`mesh-convergence.xlsx`) and as text (`mesh-convergence.txt`). The report states the grid convergence index and recommends a cell size. In this example, the verdict is `NOT converged`: between the two finest meshes, the water depth at the verticals still changes by 6 % and the flow velocity by 7 %, against a tolerance of 5 %. The mesh of this example is therefore too coarse for a final model, which is the price of its short run time. With the option *Refine automatically until converged*, the study continues with finer meshes until the tolerance is met. The column *Progress* of the tab *Jobs* shows the number of meshes only when the study has ended. Until then, *View logs* shows which mesh is running.

**Three-dimensional model.** The tab *Steady 3D* becomes active as soon as a 2D result exists, because the 3D simulation starts from it. *Build* writes the TELEMAC-3D steering files within seconds. *Submit* runs the hydrostatic 3D simulation, which takes about 19 min, and *Load results* then adds its depth-averaged result to the map. For the coarse mesh of this example, aXqua selects only two vertical levels, so that the 3D result adds little to the 2D result. The step demonstrates the workflow. The non-hydrostatic variant is started in a terminal (section *Hydraulic simulations* of the documentation). The tab *Vertical convergence* repeats the 3D simulation with different numbers of vertical levels. It was not run for this guide.

## The other tabs

| Tab | State in this example |
| --- | --- |
| *Unsteady 2D* | inactive, because the case file prescribes a constant discharge and no hydrograph |
| *Unsteady 3D*, *Morphodynamics* | inactive, because this version of the plugin cannot submit these simulations as jobs |
| *Gain-lose reach* | nothing to submit. The exchange with the gravel bar is a block of the case file, and it is built and simulated together with the steady 2D model. The tab states `configured, built, run` after step 3 |
| *Jobs* | all jobs with their state. *Cancel* stops a running job, *View logs* shows its log, *Open job directory* opens its folder |

The algorithms *Submit a simulation job*, *Check job status*, and *Import job results* in the Processing Toolbox do the same from a model or a script.

## The same workflow in a terminal

```bash
axqua profile check
axqua submit cases/example-isar/example-isar.axq-case --kind preprocessing
axqua submit cases/example-isar/example-isar.axq-case --kind steady
axqua submit cases/example-isar/example-isar.axq-case --kind calibration
axqua list
axqua status <job id> --watch
```

`axqua submit` prints the identifier of the job and returns at once. Each command needs the previous job to be completed. `axqua status <job id> --watch` follows a job until it ends.

## Run the resolution of the research case

Set `size_scale: 1.0` in the block `mesh` of the case file and `duration: 5000.0` in the block `hydrodynamics`, and repeat the steps. The mesh then has about 230,000 nodes. The build takes about 13 min and one steady simulation about 1.5 h with 16 processor cores, so that a calibration with 12 simulations takes most of a day.

## Start over

Delete the folder `cases/example-isar/axqua-case/` to remove everything the example has produced. The jobs listed on the tab *Jobs* are stored separately in the job folder of aXqua (by default `~/.local/share/axqua/jobs/`) and can be deleted there.
