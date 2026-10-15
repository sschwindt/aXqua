# Example: Isar River in QGIS, from the geodata to the steady flow field

This example runs the TELEMAC workflow of aXqua from the QGIS plugin: build a two-dimensional (2D) model from geodata, run it to a steady state, and load the result into QGIS. Every step is a button in the plugin. The same steps are listed as terminal commands at the end.

The example is a fast version of a research case. It uses the same input data and a mesh that is three times coarser, so that the build and the steady simulation take about three minutes instead of more than an hour and a half.

**Calibration and validation are not part of the example yet.** The velocity measurements of this reach are being revised. The steps that use them will be added to this guide together with the measurements: the Bayesian calibration of the bed roughness with HydroBayesCal, the validation against a second flow situation, and the mesh convergence study at the measurement points.

## What the example computes

The model covers a braided reach of the Isar River of about 6.6 ha. The terrain model and the boundary conditions stem from a survey in March 2025, when the discharge was 2.4 m³/s. It enters through two inflow lines (1.6 and 0.8 m³/s) and leaves through one outflow line. Between them, the river loses about 65 L/s into a gravel bar and gains this discharge back farther downstream. A depth-averaged model has no groundwater, so aXqua takes the discharge out of the surface flow along the losing line and returns it along the gaining line (block `gain_lose` of the case file).

| Item | Value in this example |
| --- | --- |
| Case file | `example-isar.axq-case` |
| Mesh | about 26,500 nodes and 52,600 triangles, edge length about 0.9 m across the channel |
| Bed roughness | equivalent sand roughness k_s in six zones, from 0.004 m (sand) to 0.5 m (vegetation) |
| Turbulence model | Spalart-Allmaras, selected by aXqua for this cell size |
| Simulated time | 3000 s; inflow and outflow balance to within 0.1 % after about 2200 s |

Measured run times with 8 processor cores on a workstation with 16 cores:

| Step | Button in the plugin | Run time |
| --- | --- | --- |
| Build the model | *Preprocessing* > *Build* | 1 min |
| Steady simulation | *Hydraulic simulation* > *Telemac* > *Steady 2D* > *Submit* | 1.5 min |
| Three-dimensional model (optional) | *Hydraulic simulation* > *Telemac* > *Steady 3D* > *Build*, then *Submit* | 19 min |

## Requirements

1. **The folder of the example.** It contains the case file, this guide, and the input data in `user-sources/` (about 6 MB): the terrain model, cut to the outline of the model, and the layers and tables that describe the reach. To get the folder, click *Example case...* on the *Case Setup* tab of the plugin, or run `axqua example get --folder <folder>` in a terminal. Everything the example produces is written to `axqua-case/` in the same folder.
2. **aXqua with a TELEMAC installation.** aXqua has to know where TELEMAC is installed. Step 1 below sets this up in the plugin. In a terminal, `axqua profile init` writes the profile of this computer with the TELEMAC installation it finds, and `axqua profile check` starts TELEMAC once and reports every problem with its remedy.
3. **QGIS 3.44 or newer with the aXqua plugin** (documentation, section *QGIS plugin*).

## Start QGIS with the plugin

Start QGIS and open the panel with the aXqua button in the toolbar or with *Plugins > aXqua > aXqua panel*. The panel docks on the right side of the window. Its tabs follow the workflow from left to right, and the list of jobs below the tabs is visible from every tab. The button *Help* at the top right opens the documentation at the section of the tab that is showing.

## Step 1: Configure the computer and open the case

1. Go to the tab *Configuration*. The first line names the `axqua` program and its version. If it reports an error, click *Settings...* and select the program.
2. On a computer without a profile, click *Create profile...*. aXqua enters what it finds on the computer into the profile editor: Python, TELEMAC, OpenFOAM, ParaView and VisIt. Click *Save*. The editor checks the profile and marks each entry that is not in order with a triangle, orange for a warning and dark red for an error. A click on a triangle opens the message with its remedy. A warning about OpenFOAM does not affect this example. Close the editor with *Exit*.
3. Go to the tab *Case Setup*. An example that was downloaded with *Example case...* is in the list of cases already. Otherwise, click *Add case...* and select `example-isar.axq-case` in the folder of the example.
4. The line below the list of cases now reads `telemac - environment ok`, followed by what the case can do.

*Edit case...* opens the case in the case editor, which shows every setting of the reach by block: terrain model, boundaries, mesh, roughness and so on. *Check* reports what is not in order. For this example, the check finds nothing.

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

## Step 4: Check that the simulation reached a steady state

A steady simulation is only useful when the discharge that leaves the model equals the discharge that enters it. aXqua evaluates this after every steady run and writes the following files into `axqua-case/simulation/`:

| File | Content |
| --- | --- |
| `flux-convergence.png` | discharge through each open boundary over the simulated time |
| `convergence-rate.png` | relative difference between inflow and outflow, with the tolerance of 0.1 % marked |
| `wetting-report.csv` | wetted area split into flowing water, stagnant film, and isolated puddles |
| `outlet-profile.csv` | water surface, depth, and Froude number in bands upstream of the outflow line |

In this example, the difference between inflow and outflow falls below 0.1 % after about 2200 s of simulated time. The button *View logs* below the list of jobs shows the same numbers in the log of the selected job.

## Optional steps

**Three-dimensional model.** On the tab *Hydraulic simulation*, sub-tab *Telemac*, the box *Steady 3D* becomes active as soon as a 2D result exists, because the 3D simulation starts from it. *Build* writes the TELEMAC-3D steering files within seconds. *Submit* runs the variant that is selected under *Variant*: the hydrostatic simulation, which verifies the discharge balance in 3D and takes about 19 min, or the non-hydrostatic simulation, which takes about 10 min. *Load results* then adds the depth-averaged result to the map. For the coarse mesh of this example, aXqua selects only two vertical levels, so that the 3D result adds little to the 2D result. The step demonstrates the workflow. The box *Vertical convergence* on the tab *Mesh convergence* repeats the 3D simulation with different numbers of vertical levels. It was not run for this guide.

**Several steps at once.** On the tab *Batch-processing*, tick the steps to run and click *Submit the ticked steps*. The jobs are submitted in the order of the list, and each waits for the one before it. *Generate batch-processing script...* writes a shell script that runs the same steps without QGIS and stops when a step fails.

## The other tabs and boxes

| Tab or box | State in this example |
| --- | --- |
| *Hydraulic simulation* > *Telemac*: *Unsteady 2D*, *Unsteady 3D* | inactive, because the case file prescribes a constant discharge and no hydrograph (`boundaries.inflow`) |
| *Hydraulic simulation* > *Telemac*: *Gain-lose reach* | nothing to submit. The exchange with the gravel bar is a block of the case file, and it is built and simulated together with the steady 2D model. The box states `configured, built, run` after step 3 |
| *Hydraulic simulation* > *OpenFOAM* | states that this case does not use OpenFOAM |
| *Mesh convergence* | repeats the steady simulation on four meshes and compares water depth and flow velocity at the measurement points of the case. Without measurements, the study samples 40 points along the channel centerline, most of which are dry in this braided reach, so that its verdict says little about the mesh. The step will be described here with the measurements |
| *Morphodynamic simulation* | nothing to submit. Sediment transport is switched on with the block `morphodynamics` of the case file and is then computed together with the flow. This example has no such block |
| *Calibration & validation* | states that the case does not ask for a calibration yet. The step will be described here with the measurements |
| *Postprocessing* > *QGIS* | loads the result of the selected job, adds an A3 print layout, and exports a movie of an unsteady result |
| *Postprocessing* > *ParaView*, *VisIt* | list the TELEMAC results of the case. *Export* converts the selected results into files that both programs read, and *Open in ParaView* or *Open in VisIt* starts the program of the profile with them |
| *Configuration* > *Simulation software* | states where TELEMAC, OpenFOAM, ParaView and VisIt are installed. *Install ...* opens the installation wizard of a program that is missing |

The list of jobs below the tabs shows all jobs with their state. *Cancel* stops a running or waiting job, *View logs* shows its log, and *Open job directory* opens its folder. The algorithms *Submit a simulation job*, *Check job status*, and *Import job results* in the Processing Toolbox do the same from a model or a script.

## The same workflow in a terminal

Run the commands in the folder of the example:

```bash
axqua profile check
axqua submit example-isar.axq-case --kind preprocessing
axqua submit example-isar.axq-case --kind steady
axqua list
axqua status <job id> --watch
```

`axqua submit` prints the identifier of the job and returns at once. The two jobs can be submitted in direct succession, because a job waits for the previous job of the same case. `axqua status <job id> --watch` follows a job until it ends.

## Run the resolution of the research case

Set `size_scale: 1.0` in the block `mesh` of the case file and `duration: 5000.0` in the block `hydrodynamics`, and repeat the steps. The mesh then has about 230,000 nodes. The build takes about 13 min and one steady simulation about 1.5 h with 16 processor cores.

## Start over

Delete the folder `axqua-case/` in the folder of the example to remove everything the example has produced. The jobs in the list below the tabs are stored separately in the job folder of aXqua (by default `~/.local/share/axqua/jobs/`) and can be deleted there.

## License of the data

The input data in `user-sources/` may be copied, redistributed, and adapted for any purpose, provided that aXqua is cited (Creative Commons Attribution 4.0 International). They are provided as they are, without any warranty and without liability for their use. The file `LICENSE.md` in this folder states the terms and the citation.
