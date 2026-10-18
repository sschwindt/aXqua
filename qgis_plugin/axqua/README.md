# aXqua for QGIS

Set up, run and monitor TELEMAC and OpenFOAM models of free-surface water bodies (rivers, lakes, reservoirs, hydraulic structures) from QGIS. Simulations run as jobs of their own and keep running when QGIS is closed.

The documentation is at [axqua.eu](https://axqua.eu), also in [German](https://axqua.eu/de/) and [French](https://axqua.eu/fr/). *Help* in the plugin opens the page of the tab that is showing.

## What the plugin is

The plugin is the user interface. The model build, the solver runs and the calibration are the work of the **aXqua program**, which is installed separately and which the plugin starts as a separate process; the plugin never imports it. QGIS ships its own Python, while aXqua needs gmsh, rasterio, geopandas and a TELEMAC or OpenFOAM installation, and keeping the two apart lets each be updated without the other.

Because QGIS does not own the solver process, closing QGIS does not stop a simulation. When QGIS is opened again, the job list shows each job with the state it has reached.

## Requirements

| | |
|---|---|
| QGIS | 3.44 or newer, QGIS 4 included |
| aXqua program | 1.0 or newer, see [Installation](https://axqua.eu/installation/qgis-plugin.html) |
| TELEMAC and/or OpenFOAM | the *Configuration* tab installs TELEMAC v9.1 and OpenFOAM v2406 with the sediment solvers, or finds existing installations |
| ParaView, VisIt | optional, for 3D postprocessing; the *Configuration* tab installs them too |

On Windows, TELEMAC and OpenFOAM run in the Windows Subsystem for Linux (WSL 2), see [Simulation software](https://axqua.eu/installation/simulation-software.html).

## Getting started

1. Install the aXqua program and check it with `axqua --version`.
2. Open the aXqua panel. On the *Configuration* tab, *Check* finds the program (or set its path in *Settings*), and the profile of the computer names where TELEMAC, OpenFOAM, ParaView and VisIt are installed.
3. On the *Case Setup* tab, create a case, add an existing case file (`*.axq-case`), or download the Isar example case with its data.
4. Build the model on *Preprocessing*, run it on *Simulations*, calibrate and validate it on *Calibration & validation*, and load the results on *Postprocessing* into QGIS, ParaView or VisIt.

*Clean Up*, left of *Help*, removes files a case no longer needs: working folders of interrupted runs, listings, earlier time steps of results and exports. It never lists the inputs, the case file, the built model or the last time step of a result.

## Troubleshooting

* **"aXqua could not be found"**: the message lists every place the plugin looked (the plugin setting, `$AXQUA_EXE`, `PATH`). Set the path in *Settings*.
* **A job failed**: open its logs from the job list. `runner.log` carries the error with what to do about it; the listing of the solver is beside it.
* More in [Tips and log files](https://axqua.eu/troubleshooting/tips-logfiles.html).

## License

The plugin is licensed under GPL-2.0-or-later, because it uses PyQGIS. The aXqua program it drives is licensed under BSD-3-Clause and can be used on its own.
