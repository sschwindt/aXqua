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
| Build the model | *Preprocessing* > *Build* | 1 min |
| Steady simulation | *Hydraulic simulation* > *Telemac* > *Steady 2D* > *Submit* | 1.5 min |
| Bayesian calibration | *Calibration & validation* > *Submit* | 18 min |
| Mesh convergence study (optional) | *Mesh convergence* > *Submit* | 20 min |
| Three-dimensional model (optional) | *Hydraulic simulation* > *Telemac* > *Steady 3D* > *Build*, then *Submit* | 19 min |

## Requirements

1. **The input data of the Isar case.** The case file reads its geodata and measurements from `../isar-2025/user-sources`. This folder is not part of the repository because of its size (about 0.9 GB). The example writes nothing into it.
2. **aXqua with a TELEMAC installation.** aXqua has to know where TELEMAC is installed. Step 1 below sets this up in the plugin. In a terminal, `axqua profile init` writes the profile of this computer with the TELEMAC installation it finds, and `axqua profile check` starts TELEMAC once and reports every problem with its remedy.
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

In QGIS, open the panel with the aXqua button in the toolbar or with *Plugins > aXqua > aXqua panel*. The panel docks on the right side of the window. Its tabs follow the workflow from left to right, and the list of jobs below the tabs is visible from every tab. The button *Help* at the top right opens the documentation at the section of the tab that is showing.

## Step 1: Configure the computer and open the case

1. Go to the tab *Configuration*. The first line names the `axqua` program and its version. If it reports an error, click *Settings...* and select the program.
2. On a computer without a profile, click *Create profile...*. aXqua enters what it finds on the computer into the profile editor: Python, TELEMAC, OpenFOAM, ParaView and VisIt. Click *Save*. The editor checks the profile and marks each entry that is not in order with a triangle, orange for a warning and dark red for an error. A click on a triangle opens the message with its remedy. A warning about OpenFOAM does not affect this example. Close the editor with *Exit*.
3. Go to the tab *Case Setup*, click *Add case...* and select `cases/example-isar/example-isar.axq-case`.
4. The line below the list of cases now reads `telemac - environment ok`, followed by what the case can do.

*Save project* on the same tab stores the list of cases in a small project file (`.axqua-prj`), so that the case is listed again after a restart of QGIS. This is optional.

## Step 2: Build the model

1. Go to the tab *Preprocessing* and click *Build*. The job appears in the list of jobs below the tabs.
2. Wait until the state of the job is `COMPLETED` (about 1 min). The tab then states `The model is built.`

The build clips the terrain model to the model outline, generates the mesh, interpolates the bed elevation and the roughness zones onto the mesh nodes, assigns the boundary conditions, and writes the TELEMAC input files into `axqua-case/simulation/`. The file `axqua.log` in the same folder reports the mesh quality and every decision of the build, for example the selected turbulence model. The table *Preprocessing checkup* states for every simulation of the case whether the case file asks for it, whether it is built, and whether it has been run.

A job runs independently of QGIS. QGIS can be closed while a job runs, and the list of jobs shows the job with its current state when QGIS is opened again.

Jobs of one case run one after the other. A job that is submitted while another job of the same case is running waits for it, and the column *Progress* shows `waiting for` with the name of that job. *Build* and the *Submit* of the next step can therefore be clicked in direct succession.

## Step 3: Run the steady simulation and load the result

1. Go to the tab *Hydraulic simulation*, sub-tab *Telemac*. In the box *Steady 2D*, click *Submit*.
2. In the list of jobs, the column *Progress* shows the simulated share of the 3000 s and the number of time steps. The run is complete after about 1.5 min.
3. Select the completed job in the list and click *Load results* below the list.

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

In this example, the difference between inflow and outflow falls below 0.1 % after about 2200 s of simulated time. The button *View logs* below the list of jobs shows the same numbers in the log of the selected job.

## Step 5: Calibrate the roughness

1. Go to the tab *Calibration & validation* and click *Submit*. Leave the option *Prepare only* as it is.
2. The list of jobs shows the number of the current simulation in the column *Progress*, for example `iter 5/12`. The calibration takes about 18 min.

The calibration first writes the measurements into the table `axqua-case/calibration-validation/measurements-calibration.csv`, with one row per vertical. HydroBayesCal then proceeds in two stages. It runs the model for eight combinations of the two roughness values that cover the ranges given in the case file (0.02 to 0.30 m for zone 4 and 0.10 to 0.80 m for zone 6), and trains a surrogate model on the results. A surrogate model is a fast statistical approximation of the simulation, here a Gaussian process. In the second stage, Bayesian active learning selects four additional combinations, one after the other, at which a simulation improves the estimate of the roughness values the most.

With *Prepare only* ticked, the job writes the table of measurements and the configuration of HydroBayesCal and stops. This is a quick way to inspect the calibration inputs before spending the computing time.

HydroBayesCal runs its simulations in the folder of the built case and writes each tested roughness value into the friction table there. A job that is submitted during the calibration therefore waits until the calibration has ended. aXqua then restores the friction table and the steering file of the built case.

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

**Mesh convergence.** On the tab *Mesh convergence*, the box *Mesh convergence* repeats the steady simulation on four meshes with a refinement ratio of 1.3 (edge lengths of 1.17, 0.90, 0.69, and 0.53 m across the channel) and compares water depth and flow velocity at the 84 measurement verticals. The study takes about 20 min and writes its report into `axqua-case/postprocessing/mesh-convergence/` as a workbook (`mesh-convergence.xlsx`) and as text (`mesh-convergence.txt`). The report states the grid convergence index and recommends a cell size. In this example, the verdict is `NOT converged`: between the two finest meshes, the water depth at the verticals still changes by 6 % and the flow velocity by 7 %, against a tolerance of 5 %. The mesh of this example is therefore too coarse for a final model, which is the price of its short run time. With the option *Refine automatically until converged*, the study continues with finer meshes until the tolerance is met. The column *Progress* of the list of jobs shows the number of completed meshes, for example `level 2/4`.

**Three-dimensional model.** On the tab *Hydraulic simulation*, sub-tab *Telemac*, the box *Steady 3D* becomes active as soon as a 2D result exists, because the 3D simulation starts from it. *Build* writes the TELEMAC-3D steering files within seconds. *Submit* runs the variant that is selected under *Variant*: the hydrostatic simulation, which verifies the discharge balance in 3D and takes about 19 min, or the non-hydrostatic simulation, which takes about 10 min. *Load results* then adds the depth-averaged result to the map. For the coarse mesh of this example, aXqua selects only two vertical levels, so that the 3D result adds little to the 2D result. The step demonstrates the workflow. The box *Vertical convergence* on the tab *Mesh convergence* repeats the 3D simulation with different numbers of vertical levels. It was not run for this guide.

**Several steps at once.** On the tab *Batch-processing*, tick the steps to run and click *Submit the ticked steps*. The jobs are submitted in the order of the list, and each waits for the one before it. *Generate batch-processing script...* writes a shell script that runs the same steps without QGIS and stops when a step fails.

## The other tabs and boxes

| Tab or box | State in this example |
| --- | --- |
| *Hydraulic simulation* > *Telemac*: *Unsteady 2D*, *Unsteady 3D* | inactive, because the case file prescribes a constant discharge and no hydrograph (`boundaries.inflow`) |
| *Hydraulic simulation* > *Telemac*: *Gain-lose reach* | nothing to submit. The exchange with the gravel bar is a block of the case file, and it is built and simulated together with the steady 2D model. The box states `configured, built, run` after step 3 |
| *Hydraulic simulation* > *OpenFOAM* | states that this case does not use OpenFOAM |
| *Morphodynamic simulation* | nothing to submit. Sediment transport is switched on with the block `morphodynamics` of the case file and is then computed together with the flow. This example has no such block |
| *Postprocessing* > *QGIS* | loads the result of the selected job, adds an A3 print layout, and exports a movie of an unsteady result |
| *Postprocessing* > *ParaView*, *VisIt* | name the program of the profile. The export of TELEMAC results to these programs is not yet available |

The list of jobs below the tabs shows all jobs with their state. *Cancel* stops a running or waiting job, *View logs* shows its log, and *Open job directory* opens its folder. The algorithms *Submit a simulation job*, *Check job status*, and *Import job results* in the Processing Toolbox do the same from a model or a script.

## The same workflow in a terminal

```bash
axqua profile check
axqua submit cases/example-isar/example-isar.axq-case --kind preprocessing
axqua submit cases/example-isar/example-isar.axq-case --kind steady
axqua submit cases/example-isar/example-isar.axq-case --kind calibration
axqua list
axqua status <job id> --watch
```

`axqua submit` prints the identifier of the job and returns at once. The three jobs can be submitted in direct succession, because a job waits for the previous job of the same case. `axqua status <job id> --watch` follows a job until it ends.

## Run the resolution of the research case

Set `size_scale: 1.0` in the block `mesh` of the case file and `duration: 5000.0` in the block `hydrodynamics`, and repeat the steps. The mesh then has about 230,000 nodes. The build takes about 13 min and one steady simulation about 1.5 h with 16 processor cores, so that a calibration with 12 simulations takes most of a day.

## Start over

Delete the folder `cases/example-isar/axqua-case/` to remove everything the example has produced. The jobs in the list below the tabs are stored separately in the job folder of aXqua (by default `~/.local/share/axqua/jobs/`) and can be deleted there.
