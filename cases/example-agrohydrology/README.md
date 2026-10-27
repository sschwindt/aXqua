# Example: arable field in QGIS, rainfall-runoff with Green-Ampt infiltration

This example runs the hydrology mode of aXqua from the QGIS plugin. Rain falls on every node of a two-dimensional (2D) TELEMAC model of an arable field, part of it infiltrates into the soil after Green-Ampt, and the rest flows over the field as sheet flow and leaves it over its lower edges. The example checks the input data, builds the model, runs the storm, loads the result into QGIS, and repeats the storm on a second soil. Every step is a button in the plugin. The same steps are listed as terminal commands at the end.

## What the example computes

The field covers 5.6 ha on a hillslope in Lower Bavaria, between 388 and 425 m above sea level, with a median slope of 9 %. It falls to the north, toward a stream, and the tillage lines of the field run down the slope to the northeast. The terrain model is the DGM1 of the Bavarian surveying administration, with cells of 1 m.

| Item | Value in this example |
| --- | --- |
| Case file | `example-agrohydrology.axq-case` |
| Rain | 42.1 mm in 7 hours, uniform over the field, given hour by hour: 6.72, 4.11, 9.44, 1.67, 8.71, 9.35 and 2.13 mm (`user-sources/hydrology/hyetograph.txt`) |
| Simulated time | 8 hours: the 7 hours of rain and 1 hour after it (`hydrology.drain_time`) |
| Infiltration | Green-Ampt, solution of Sadeghi et al. as recommended by de Vos et al. (2024): ponded water infiltrates also after the rain |
| Soil | saturated hydraulic conductivity K_s = 0.61 mm/h, suction head 0.1 m, water content 0.20 at the start and 0.40 at saturation (`soil-ks-0.61.csv`); a second soil with K_s = 2.0 mm/h, 0.32 and 0.56 (`soil-ks-2.csv`) |
| Bed roughness | Strickler coefficient 30 m^(1/3)/s on the whole field |
| Mesh | about 16,600 nodes and 32,600 triangles of near-equal sides, edge length 2 m |
| Numerical kernel | finite volumes (HLLC) with the hydrostatic reconstruction of Chen and Noelle, the default of the hydrology mode |
| Outlet | three outflow lines on the north, west and southeast edges of the field, with the condition `overfall` |

**Where the water leaves the field.** A field is no catchment: its outline is not a drainage divide, and the water leaves it over whole edges. The outflow lines of this example follow the edges over which the terrain falls away beyond the outline. Routed over the terrain model, 78 % of the field drains over the north edge toward the stream, 11 % over the west edge, where the terrain drops down a steep bank, and 9 % over the southeast edge into a furrow 5 m outside the field. The other parts of the outline are walls. The hydrology checkup finds 0.3 % of the field draining across them, in stretches of at most 25 m², and names no wall that the flow crosses. The condition `overfall` lets the water leave over the outflow lines as over a brink, at critical flow at most, and lets none come back (documentation, section *Hydrology simulations*, *Boundaries and outlet*).

**What the example leaves out.** The walls also keep out the water of the neighboring fields. Routed over the terrain model, about 2.3 ha upslope of the field drain onto it, mostly along the tillage lines of the field to the southwest. A model of this field alone cannot include that run-on. A model that does has to extend its outline over that area and needs the soil there.

Measured run times with 4 processor cores on a workstation with 32 cores:

| Step | Button in the plugin | Run time |
| --- | --- | --- |
| Hydrology checkup | *Preprocessing* > *Hydrology checkup* > *Check* | 1 s |
| Build the model | *Preprocessing* > *Build* | 3 s |
| Rainfall-runoff simulation | *Simulations* > *Hydrology* > *Submit* | 7 to 8 min |

## Where the data come from

The example was reconstructed from two TELEMAC models of the field under this storm, which differ in their soil only. What the example takes from them:

* **Outline.** The outline of the mesh of the models, with its 1,203 boundary nodes simplified to 53 vertices by at most 0.5 m. The area is unchanged (55,810 m²).
* **Rain.** The hyetograph file of the models, unchanged. It is TELEMAC's own format (`#HYETOGRAPH FILE`), which aXqua reads as TELEMAC reads it: the depth of each line falls in the hour that ends at its time.
* **Soil.** K_s from the geometry file of each model, and the suction head and the two water contents from its steering file (`SUCTION`, `INITIAL WATER CONTENT`, `SATURATED WATER CONTENT`).
* **Roughness and time.** The Strickler coefficient of 30 and the 8 hours of simulated time.
* **Terrain.** The bed elevation of the mesh agrees with the DGM1 to within 5 cm (standard deviation, no bias), except at about 50 nodes on the outline, where it deviates by up to 20 m. The four DGM1 tiles of 1 km² around the field are open data, so the example uses the DGM1 itself, cut to the outline plus 5 m.

What the example does differently:

* **Boundaries.** In the models, every node of the outline was an open boundary with a prescribed depth of zero, also on the upper edges, where the terrain inside falls away from the outline. Here only the three edges over which the field drains are open, and the rest of the outline is wall.
* **Mesh.** Edges of 2 m instead of about 1 m, which gives 16,600 nodes instead of 61,300. Set `default_size` in the block `mesh` of the case file to 1.0 for the resolution of the models.
* **Infiltration.** The models use the Green-Ampt equations that TELEMAC releases (Travert et al. 2022), in which ponded water does not infiltrate once the rain stops. The example uses the solution of Sadeghi et al. that de Vos et al. (2024) recommend. With `green_ampt: travert`, the example uses TELEMAC's equations, and the infiltrated depth then agrees with that of the models to 0.001 mm: 16.366 mm on the first soil and 28.886 mm on the second, against 16.366 and 28.887 mm in the results of the models. The rain and the soils are therefore the same as in the models.

## Requirements

1. **The folder of the example.** It contains the case file, this guide, and the input data in `user-sources/` (about 0.3 MB): the terrain model, cut to the outline of the field, the outline and the outflow lines, the rain, and the two soils. To get the folder, click *Example case...* on the *Case Setup* tab of the plugin and select *Arable field, rainfall-runoff with Green-Ampt infiltration*, or run `axqua example get example-agrohydrology --folder <folder>` in a terminal. Everything the example produces is written to `axqua-case/` in the same folder.
2. **aXqua with a TELEMAC installation.** aXqua has to know where TELEMAC is installed. Step 1 below sets this up in the plugin. In a terminal, `axqua profile init` writes the profile of this computer with the TELEMAC installation it finds, and `axqua profile check` starts TELEMAC once and reports every problem with its remedy. The example was run with TELEMAC v9.1.1.
3. **QGIS 3.44 or newer with the aXqua plugin** (documentation, section *QGIS plugin*).

## Start QGIS with the plugin

Start QGIS and open the panel with the aXqua button in the toolbar or with *Plugins > aXqua > aXqua panel*. The panel docks on the right side of the window. Its tabs follow the workflow from left to right, and the list of jobs below the tabs is visible from every tab. The button *Help* at the top right opens the documentation at the section of the tab that is showing.

## Step 1: Configure the computer and open the case

1. Go to the tab *Configuration*. The first line names the `axqua` program and its version. If it reports an error, click *Settings...* and select the program.
2. On a computer without a profile, click *Create profile...*. aXqua enters what it finds on the computer into the profile editor. Click *Save*. The editor checks the profile and marks each entry that is not in order with a triangle, orange for a warning and dark red for an error. A click on a triangle opens the message with its remedy. A warning about OpenFOAM does not affect this example. Close the editor with *Exit*.
3. Go to the tab *Case Setup*. An example that was downloaded with *Example case...* is in the list of cases already. Otherwise, click *Add case...* and select `example-agrohydrology.axq-case` in the folder of the example.

*Open case settings...* in the box *Case settings* opens the settings of the case. The page *Hydrology* holds the rain, the soil, wind and sun, and the outlet. The check box *Hydrology mode* at the top of the frame *TELEMAC mesh* on the page *Mesh configuration* is the same switch: it gives the mesh triangles of near-equal sides and grays out the settings of a river mesh. *Check* reports what is not in order. For this example, the check finds nothing.

## Step 2: Check the data and build the model

1. Go to the tab *Preprocessing*. The box *Hydrology checkup* lists what is not in order with the rain, the soil, the outlet and the terrain. Click *Check* to include the analysis of the terrain model, which routes the water of every cell of the field to the outline. For this example, the checkup finds nothing.
2. Click *Build*. The job appears in the list of jobs below the tabs. Wait until its state is `COMPLETED`.

The build generates the mesh, interpolates the bed elevation and the soil onto the mesh nodes, assigns the boundary conditions, and writes the TELEMAC input files into `axqua-case/simulation/`:

| File | Content |
| --- | --- |
| `geometry.slf` | the mesh with the bed elevation and, per node, K_s, the product of suction head and moisture deficit (`GA SUCTION A`) and the moisture deficit (`GA DELTA THETA`) |
| `boundaries.cli` | the boundary conditions: `5 4 4` on the outflow lines, `2 2 2` on the walls |
| `forcing.txt` | rain and evaporation as intervals of constant intensity |
| `user_fortran/runoff_greenampt.f` | the infiltration routine that aXqua generates for TELEMAC |
| `hydrology2d.cas` | the TELEMAC steering file of the hydrology run |

The file `axqua.log` in the same folder reports the mesh quality, the soil of the nodes and the duration of the run.

## Step 3: Run the storm

1. Go to the tab *Simulations*, sub-tab *Hydrology*. In the box *Hydrology (rainfall-runoff)*, click *Submit*.
2. In the list of jobs, the column *Progress* shows the simulated share of the 8 hours. The run takes 7 to 8 min with 4 processor cores.

When the run has ended, aXqua writes the outlet hydrograph and the water balance into `axqua-case/postprocessing/hydrology/`:

| File | Content |
| --- | --- |
| `hydrograph.png` | the rain and the discharge over all outflow lines |
| `hydrograph.csv` | the discharge through each outflow line over time |
| `water-balance.csv`, `water-balance.json` | rain, infiltrated and evaporated volumes, outflow, the water left on the field, the error of the volume, the runoff coefficient and the peak discharge |

For this example, the water balance reads:

| Quantity | Value |
| --- | --- |
| Rain | 2,351 m³ (42.1 mm) |
| Infiltrated | 871 m³ (15.6 mm) |
| Outflow | 1,478 m³ (26.5 mm) |
| Water on the field at the end | 2 m³ |
| Error of the volume | 0.001 m³ (0.00005 % of the rain) |
| Runoff coefficient (outflow over rain) | 0.63 |
| Peak discharge | 0.123 m³/s, 6 hours after the start |

The hydrograph follows each hour of rain within about 20 minutes, the response time of a field of this size and slope. In the fourth hour, the rain of 1.7 mm/h hardly exceeds the infiltration capacity, and the outflow falls almost to zero. After the last rain, the field drains within about 15 minutes.

## Step 4: Load the result

Select the completed job in the list and click *Load results* below the list. QGIS adds these layers in the group `axqua` of the layer panel:

| Layer | Content |
| --- | --- |
| *TELEMAC result (hydrology)* | water depth in blue; cells with less than 1 mm of water are transparent |
| *TELEMAC result (hydrology) - velocity* | flow velocity from yellow (slow) to purple (fast) |
| *TELEMAC result (hydrology) - infiltrated depth* | the depth that has infiltrated since the start [m] |
| *TELEMAC result (hydrology) - high water mark* | the highest water depth of the run [m]: TELEMAC's high water mark minus the bed, where the water was deeper than 1 cm (the level itself is the dataset *high water mark* of the layer) |
| *TELEMAC result (hydrology) - highest velocity* | the highest flow velocity of the run, where the water was deeper than 1 cm |
| *Mesh geometry* | the mesh with the bed elevation and the soil |

The result holds a map every 10 minutes (`hydrology.printout_period`). The time controller of QGIS steps through them. At the peak of the storm, the water on the field is a film of 1.2 mm in the median, and 99 % of the field carries less than 5 mm. Deeper water stands only in small depressions along a line parallel to the southeast edge and near the northeast corner, at most 9 cm. TELEMAC records its high water mark and its highest velocity only where the water is deeper than 1 cm, so these two layers show the depressions and leave the sheet flow out.

## Step 5: The second soil

The second model of the field has a more permeable soil with a larger pore volume: K_s = 2.0 mm/h, a water content of 0.32 at the start and 0.56 at saturation.

1. On the tab *Case Setup*, click *Open case settings...* and go to the page *Hydrology*.
2. Change *Soil table* to `user-sources/hydrology/soil-ks-2.csv` and click *Save*.
3. Build the model again and submit the hydrology run again.

| Quantity | K_s = 0.61 mm/h | K_s = 2.0 mm/h |
| --- | --- | --- |
| Infiltrated | 15.6 mm | 28.7 mm |
| Runoff coefficient | 0.63 | 0.32 |
| Peak discharge | 0.123 m³/s | 0.089 m³/s |

On the more permeable soil, half as much of the rain leaves the field, and the peak discharge is 28 % lower. The results of the first soil are overwritten. To keep both, copy the folder `axqua-case/` before the second build, or copy the case file under another name and set another `sim_dir` in its block `project`.

## Optional steps

**TELEMAC's own Green-Ampt equations.** Set `green_ampt: travert` in the block `hydrology` of the case file, or *Green-Ampt solution* to `travert` on the page *Hydrology*, and repeat steps 2 and 3. The infiltration then follows the equations that TELEMAC releases, as in the original models, and the water that ponds after the rain no longer infiltrates.

**Data of one's own catchment.** *Data wizard...* on the sub-tab *Simulations > Hydrology* guides through rain, soil, initial moisture, wind and sun, and the outlet, with the data portals of the national agencies first. It can download a rain event, the soil moisture at its start, soil textures and sealed surfaces for any place on Earth. This example does not need it.

## The same workflow in a terminal

Run the commands in the folder of the example:

```bash
axqua profile check
axqua hydrology check example-agrohydrology.axq-case
axqua submit example-agrohydrology.axq-case --kind preprocessing
axqua submit example-agrohydrology.axq-case --kind hydrology
axqua list
axqua status <job id> --watch
```

`axqua submit` prints the identifier of the job and returns at once. The two jobs can be submitted in direct succession, because a job waits for the previous job of the same case. `axqua status <job id> --watch` follows a job until it ends.

## Start over

Delete the folder `axqua-case/` in the folder of the example to remove everything the example has produced. The jobs in the list below the tabs are stored separately in the job folder of aXqua (by default `~/.local/share/axqua/jobs/`) and can be deleted there.

## License of the data

The input data in `user-sources/` may be copied, redistributed, and adapted for any purpose, provided that aXqua is cited (Creative Commons Attribution 4.0 International). The terrain model stems from the Bavarian surveying administration, which is to be named as its source. The data are provided as they are, without any warranty and without liability for their use. The file `LICENSE.md` in this folder states the terms and the citation.

## References

* De Vos, L. F., Broich, K., Wirthensohn, M. and Rüther, N. (2024). Revised implementation of the Green-Ampt infiltration method in TELEMAC-2D. Proceedings of the XXXth TELEMAC User Conference, Chambéry. https://hdl.handle.net/20.500.11970/114347
* Travert, J.-P., Taccone, F. and Bacchi, V. (2022). Modelling runoff for extreme rainfall events on large catchments using TELEMAC-2D. Proceedings of the XXVIIIth TELEMAC User Conference, Paris-Saclay, 131-139.
