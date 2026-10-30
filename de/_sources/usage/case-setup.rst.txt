.. _help-case-setup:

aXqua case setup
================

An **aXqua case** is the complete description of one free-surface water body for one modeling task, such as a river reach, a lake, a reservoir, or the surroundings of a hydraulic structure: the terrain, the boundaries of the model, the roughness of the bed, the settings of the simulation, and the field measurements for the calibration. The description is stored in one **case file**, whose name ends with ``.axq-case``. All simulation steps read this file, so that a 2D TELEMAC model and a 3D OpenFOAM model of the same water body use the same input.

.. admonition:: Fundamentals of this tutorial are explained on YouTube
   :class: tip dropdown

   .. raw:: html

      <div class="video-embed">
        <iframe
          src="https://www.youtube.com/embed/gK83Q8bJCHI"
          title="TELEMAC 2D simulations with aXqua: dry start"
          loading="lazy"
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
          referrerpolicy="strict-origin-when-cross-origin"
          allowfullscreen>
        </iframe>
      </div>

   `Open the tutorial on YouTube <https://youtu.be/2qWOA7l5xeg>`_.


A case file contains nothing that is specific to a computer. Where the simulation software is installed is stored in the profile of the computer (:ref:`plugin-profile`). A case folder can therefore be copied to another computer, or published together with a study, and used there without changes.

.. _help-example-case:

**Example cases.** Two complete cases are available for trying out every step before a case of one's own is set up. ``example-isar`` describes a braided reach of the Isar River of 6.6 ha at a discharge of 2.4 m³/s, with a coarse mesh, so that the model is built in one minute and a steady simulation takes about two minutes. It includes the velocity and depth measurements of two field campaigns at two discharges, so that the calibration and the validation can be tried as well. ``example-agrohydrology`` is an arable field of 5.6 ha on a hillslope in Lower Bavaria under a rain of 42 mm in 7 hours, for the hydrology mode (:ref:`help-hydrology`): Green-Ampt infiltration with two soils, sheet flow over the field, and the runoff that leaves it over three of its edges. Each example is a folder that contains the case file, the input data in the subfolder ``user-sources/``, and a guide (``README.md``) that explains every step from the build to the loading of the result in QGIS. The folders have a size of a few megabytes. Their terrain models are cut to the outline of the model, which is all that aXqua reads of a terrain model.

On the *Case Setup* tab, *Example case...* opens a window that lists the available examples. Select the folder in which the folder of the example is to be created and click *Download*. The example is then added to the list of cases, and the next step is *Build* on the *Preprocessing* tab. In a terminal, the following command does the same:

.. code-block:: text

   axqua example get <name> --folder <folder>

The examples are not part of the installed program. They are downloaded from the aXqua repository on GitHub. Their input data may be reused and adapted for any purpose, provided that aXqua is cited (Creative Commons Attribution 4.0 International License). The file ``LICENSE.md`` in the folder of the example states the terms and the citation. A folder with the name of the example that already exists is never overwritten, because it may contain results. Delete the folder, or select another one, to download the example again.

**In the plugin.** The *Case Setup* tab has two boxes. The box *Cases* lists the cases that the plugin knows. *New case...* creates a case file and opens its settings, *Add case...* adds an existing case file, *Example case...* downloads the example, and *Remove from list* takes the active case off the list without deleting its file. The selected case is the active one, to which all other tabs refer. The plugin remembers the list when QGIS is closed; there is no separate project file, because a case is the project. A project file of an earlier version (``.axqua-prj``) can still be selected with *Add case...*, which adds the cases that it lists.

The box *Case settings* shows the file of the active case. *Open case settings...* opens the settings of the active case in a window of their own, and *Check* examines the case without opening the window. The window shows the settings on pages, which are listed on the left in the order of the subsections below: *Project settings*, *Geodata*, *Liquid boundaries*, *Initial conditions*, *Mesh configuration*, *Wall roughness*, *Hydraulic simulation*, *Hydrology*, *Morphodynamic simulation*, *TELEMAC*, *OpenFOAM*, *Calibration and validation*, and *Post-processing*. A page can hold several frames: the exchange with a porous body is a frame of the page *Liquid boundaries*, the drying of thin films a frame of *Initial conditions*, the lattice of the OpenFOAM model a frame of *Mesh configuration*, and the structures and the surfaces from CAD are frames of *OpenFOAM*. On each page, the window shows the settings that every case needs and all settings that the case file already contains. Every other setting of a frame is added with *Add setting...*. A tooltip explains each setting, and a frame whose purpose is not obvious has an information button.

**Required settings** are written in bold dark purple, the color of the aXqua icon. For a minimal case, these are the name and the coordinate reference system, the terrain model, the model outline, the inflow and outflow lines, and the discharge, plus the outflow water level while the outflow condition is a fixed level. A case in hydrology mode needs neither the discharge nor inflow lines (:ref:`hydrology-case-setup`). An option that is switched on adds the settings that it needs: an unsteady simulation requires the hydrograph, sediment transport with GAIA the sediment classes, fine sediment over a fixed bed the grain size and the concentration at the inflow, and the DEM of difference the second terrain model. The pages *TELEMAC*, *OpenFOAM* and *Calibration and validation* begin with a switch (*Simulate with TELEMAC*, *Simulate with OpenFOAM*, *Calibrate and validate this case*). A switch that is off leaves the block out of the case file and grays out its settings, also where they are shown on another page. Switched on, its required settings turn purple as well. A page whose required setting is still empty is marked in the list on the left, and saving reports each empty required setting.

.. figure:: ../img/gui-case-setup.png
   :alt: The Case Setup tab of the plugin
   :width: 70%

   The *Case Setup* tab. (1) *Add case...* and *Example case...*. (2) The cases that the plugin knows. The selected case is the active one, to which all other tabs refer. (3) What the active case can do with the simulation software of this computer. (4) *Open case settings...* opens the settings of the active case, and *Check* examines the case.

Nothing has to be typed that can be selected. Every file has a button that opens a file dialog, and the button *Layers* offers the layers that are open in QGIS. A file in or near the case folder is entered with a path relative to the case file, so that the case can be moved together with its data.

.. figure:: ../img/gui-case-editor.png
   :alt: The case settings with the page Geodata
   :width: 85%

   The case settings with the page *Geodata*. (1) The pages of the case settings; required settings are bold dark purple. (2) A file is selected with the button ``...`` or, with *Layers*, from the layers that are open in QGIS. (3) *Add setting...* adds a setting of the block that is not shown yet. (4) *Save*, *Cancel* and *Exit*.

The window has three buttons at the bottom right. *Save* writes the case file and checks it, and the window stays open. *Cancel* discards the changes since the last save and closes the window. *Exit* closes the window and asks first if there are changes that were not saved. The check never prevents saving, because a case is incomplete while it is being set up. It marks each setting that is not in order with a triangle, orange for a warning and dark red for an error, and marks the page in the list on the left. A click on a triangle opens the message with its remedy (:doc:`../troubleshooting/warnings`, :doc:`../troubleshooting/errors`). The button *Check* on the *Case Setup* tab runs the same check without opening the window. *Help* at the bottom left opens the section of this documentation that belongs to the page that is showing.

Saving from the window rewrites the case file from its settings. Comments of a case file that was written by hand are not kept. The original is preserved as ``<name>.bak`` the first time the window saves.

**In a text editor.** The case file is a text file in YAML format. Each block of the file is explained in one subsection below. A commented template is the case file `case-template.axq-case <https://github.com/sschwindt/aXqua/blob/main/cases/case-template/case-template.axq-case>`_ of the repository; *Download raw file* on that page saves it. Save it in the folder of the new case and rename it. In a copy of the repository (:ref:`install-from-source`), copy the folder ``cases/case-template/`` instead. The command ``axqua check <case-file>`` reports everything that is not in order.

Case files of earlier aXqua versions are named ``case-config.yml`` and may contain the location of the simulation software. They are still read. The following command writes a case file of the current type next to such a file:

.. code-block:: text

   axqua migrate case-config.yml --to-case

The new file is named after the folder of the case. The command does not replace a case file of that name that exists already, because it may have been edited since it was written. To replace it, name it with ``-o <file>``. Applied to a file that is a case file of the current type already, the command rewrites that file and keeps the previous version as ``<name>.bak``.

A minimal case file looks as follows:

.. code-block:: yaml

   project:
     name: my-reach
     crs_epsg: 25832
   telemac:
     solver: telemac2d
     n_processors: 4
   geodata:
     dem_initial: user-sources/geodata/dem.tif
     boundary: user-sources/geodata/roi.gpkg
   boundaries:
     liquid_boundaries: user-sources/geodata/liquid-boundaries.gpkg
     prescribed_flowrate: 47.2      # discharge at the inflow [m3/s]
     outflow_condition: elevation
     prescribed_elevation: 379.5    # water surface elevation at the outflow [m]

.. _case-project:

Project settings
----------------

The ``project`` block names the case, defines its coordinate reference system and states where aXqua writes its files.

.. code-block:: yaml

   project:
     name: my-reach
     crs_epsg: 25832          # metric coordinate reference system (here: ETRS89 / UTM 32N)
     sim_dir: axqua-case      # folder for everything that aXqua produces

A case folder has two parts with different roles. The folder ``user-sources/`` contains the input data, such as terrain models, vector layers and measurements. aXqua reads these files and never changes them. The folder ``axqua-case/`` receives everything that aXqua produces, in one subfolder per workflow step:

.. code-block:: text

   my-reach/
     my-reach.axq-case            the case file
     user-sources/                input data (read only)
     axqua-case/
       preprocessing/             clipped terrain models, compiled measurements
       simulation/                the TELEMAC model and its results
       openfoam/                  the OpenFOAM model and its results
       openfoam-sediment/         the fine sediment simulation and its results
       postprocessing/            mesh convergence studies, figures
       calibration-validation/    input and output of the calibration

Because of this separation, a case can be rebuilt from its inputs at any time, and the folder ``axqua-case/`` can be deleted without losing data that cannot be reproduced.

All paths in the case file are interpreted relative to the folder of the case file. ``crs_epsg`` must be a metric (projected) coordinate reference system. Input layers may use any other reference system, because aXqua converts them when it reads them.

.. _case-geodata:

Geodata
-------

The ``geodata`` block lists the terrain model and the vector layers that describe the reach. The file names are free. What matters is the geometry type and the attribute fields of each layer.

.. list-table::
   :header-rows: 1
   :widths: 22 14 28 36

   * - Entry
     - Geometry
     - Attribute fields
     - Purpose
   * - ``dem_initial`` (required)
     - raster
     - none
     - Digital elevation model (DEM) of the riverbed and the floodplains.
   * - ``boundary`` (required)
     - one polygon
     - none
     - Region of interest. The model covers the area inside this polygon.
   * - ``mesh_zones``
     - polygons
     - ``Zone Name``, ``Max Edge Length (m)``
     - Areas of equal mesh resolution. The zone name must contain ``channel``, ``floodplain`` or ``refinement`` (:ref:`case-mesh`).
   * - ``channel_centerline``
     - one line
     - none
     - Course of the main channel. Mesh cells in the channel are stretched along this line.
   * - ``roughness_zones``
     - polygons
     - ``Zone ID`` (integer)
     - Areas of equal roughness (:ref:`case-roughness`).
   * - ``roughness_table``
     - table (CSV)
     - ``zone_id``, ``ks``
     - Roughness value of each zone.
   * - ``structures``
     - polygons or lines
     - ``Type``, ``Crest (m)`` or ``Height (m)``, ``Width (m)``
     - Dams, weirs, walls and buildings that the DEM does not contain.
   * - ``control_sections``
     - lines
     - a name field
     - Cross sections at which the discharge is evaluated after a simulation.
   * - ``dem_target``
     - raster
     - none
     - DEM of a later survey, for morphodynamic simulations (:ref:`case-morphodynamics`).

The inflow and outflow lines also are a vector layer, which belongs to the boundaries (:ref:`case-boundaries`).

The vector layers have to fit together: the mesh zones cover the region of interest without gaps, the inflow and outflow lines lie on its outline, and the centerline runs inside the channel zone. Draw the layers in QGIS on top of a **hillshade** of the DEM, which shows banks, gravel bars and structures clearly (*Processing Toolbox > GDAL > Raster analysis > Hillshade*).

.. figure:: ../img/inflow-bc.jpg
   :alt: Mesh zones, roughness zones, inflow line and channel centerline drawn over a hillshade
   :width: 100%

   Mesh zones, roughness zones, the inflow line and the channel centerline at the upstream end of a model, drawn over a hillshade of the DEM.

To place a small refinement zone inside a larger zone, cut a hole of the same shape into the larger polygon with the QGIS tool *Add Ring* (*Advanced Digitizing Toolbar*), so that the two polygons do not overlap.

**Structures** are drawn as ordinary features. A line with a ``Width (m)`` describes a wall or the crest of a dam. A polygon describes a building or a pier. ``Crest (m)`` gives a horizontal crest at an absolute elevation, and ``Height (m)`` gives a crest at a constant height above the ground. Structures that water can pass over (dam, weir, embankment) raise the riverbed to the crest. Structures that are never overtopped (wall, building, pier) are removed from the flow area. The distinction is taken from the text in the ``Type`` field.

For a morphodynamic simulation, aXqua can compute the elevation change between two surveys itself from ``dem_initial`` and ``dem_target`` (:ref:`case-morphodynamics`).

.. _case-surfaces:

**Designed structures from CAD drawings.** A structure that exists as a construction drawing and not as a survey, such as a fish pass or a flume, can be supplied as a set of STL files. The ``surfaces`` block names each file and states its role. aXqua converts the files into the ordinary inputs of a case (a DEM, the region of interest, the inflow and outflow lines, structures and roughness zones), so that no entries are required under ``geodata`` for them.

.. code-block:: yaml

   surfaces:
     resolution: 0.05     # [m] cell size of the DEM that is created
     scale: 0.001         # the drawing is in millimeters
     parts:
       - {file: user-sources/cad/bed.stl,    role: bed, zone_id: 1, ks: 0.08}
       - {file: user-sources/cad/walls.stl,  role: wall}
       - {file: user-sources/cad/inlet.stl,  role: inflow}
       - {file: user-sources/cad/outlet.stl, role: outflow}

The roles are ``bed`` (terrain, at least one file), ``wall`` (structures that are never overtopped), ``inflow`` and ``outflow`` (open boundaries), ``roi`` (outline of the model) and ``ignore``. A ``bed`` file with a ``zone_id`` and a ``ks`` value also defines a roughness zone. The command ``axqua surface <case-file>`` performs the conversion alone, so that the result can be inspected in QGIS before the model is built. Because a DEM stores one elevation per location, overhanging parts of a drawing cannot be represented. aXqua reports their share. Select the ``resolution`` fine enough for the thinnest wall of the structure.

.. _case-boundaries:

Liquid boundaries
-----------------

At the liquid boundaries of the model, the user states where water enters and leaves and how much. For the subcritical flow that prevails in most rivers and in the inlets and outlets of lakes and reservoirs, the model requires the **discharge at the inflow** and the **water surface elevation at the outflow**. The discharge determines how much water enters. The downstream water level determines how deep and how fast the water leaves the model.

.. code-block:: yaml

   boundaries:
     liquid_boundaries: user-sources/geodata/liquid-boundaries.gpkg
     prescribed_flowrate: 47.2       # [m3/s]
     outflow_condition: elevation    # elevation | stage_discharge | free
     prescribed_elevation: 379.5     # [m]

``liquid_boundaries`` is a line layer with one line per inflow and per outflow cross section. An attribute field named ``Type (inflow/outflow)`` contains the word ``inflow`` or ``outflow`` for each line. All parts of the model outline that are not covered by such a line are closed walls.

``outflow_condition`` selects how the downstream water level is defined:

``elevation`` (default)
    A constant water surface elevation, given by ``prescribed_elevation``.
``stage_discharge``
    The water surface elevation is read from a rating curve for the simulated discharge. The curve is a table with the columns ``Q`` and ``WSE``, named by ``stage_discharge``. If no measured curve is available, aXqua estimates one from the channel width, the bed slope and the roughness at the outflow line.
``free``
    No water level is prescribed. This choice is suited for an outflow over a drop or a weir.

For a simulation with a discharge that varies in time, ``inflow`` names a table with a time column and a discharge column, and ``hydrodynamics.regime`` is set to ``unsteady``; the case settings then mark the hydrograph as required. The exchange with a porous body, in which water leaves the surface through a gravel bar and returns further downstream (:ref:`preprocessing-telemac-choices`), is a frame of the page *Liquid boundaries* in the case settings.

**Gauge data.** The script `gauge_data.py <https://github.com/sschwindt/aXqua/blob/main/scripts/gauge_data.py>`_ of the repository retrieves discharge time series and rating curves of river gauges and saves them in the format that aXqua reads. It supports the gauges of the Bavarian flood information service (HND) and of the United States Geological Survey (USGS). It opens in a web browser and requires the package ``streamlit`` (``pip install "axqua[gui]"``). Save it with *Download raw file* on that page, start it in the folder where it was saved (in a copy of the repository: ``python scripts/gauge_data.py``), paste the address or the number of a gauge, and save the two tables into ``user-sources/``:

.. code-block:: bash

   python gauge_data.py

The exported discharge series is the ``inflow`` table, and the exported rating curve is the ``stage_discharge`` table. Enter the elevation of the gauge zero to convert the gauge reading into an absolute water surface elevation, and verify that this elevation and the DEM refer to the same height system.

.. _case-initialization:

Initial conditions
------------------

A simulation requires a state from which it starts. The ``initialization`` block offers two choices.

**Dry start (default).** The riverbed is dry, except for a short stretch at the inflow that is filled with water to the depth ``dry_start_depth`` (default 0.5 m). The inflowing discharge then fills the reach from upstream. This start requires no assumption about the water level, but the model has to simulate the filling of the reach.

**Pre-wetted channel.** When ``prewet_depth`` is set, the channel zones start with water. aXqua estimates the water level at cross sections along the centerline for the discharge of the case and fills the channel to 70 % of this level (``prewet_fill``). The lower filling is intended: a channel with too little water fills within seconds, whereas surplus water on flat ground may remain as a thin film until the end of the simulation, because a 2D model has neither infiltration nor evaporation. The mesh convergence study uses the pre-wetted start to save computing time.

.. code-block:: yaml

   initialization:
     dry_start_depth: 0.5     # [m] water depth of the filled stretch at the inflow
     # prewet_depth: 0.5      # [m] activates the pre-wetted start

**Drying of thin films.** On a coarse bed, water that is thinner than the grain roughness does not flow over the bed: it stands within the grains and, in nature, drains into the ground. A 2D model has no ground below the bed, so such a film remains where it is and makes the wetted area appear much larger than it is. On the Isar case, water shallower than 1 cm covered 13 % of the wetted area and held 0.16 % of the water. With ``drying.film_infiltration: true``, aXqua removes water that is both shallower than ``film_depth`` (default 0.01 m) and slower than ``film_velocity`` (default 0.005 m/s), and returns it where the porous body gives water back, so that no water is lost. The option therefore requires the exchange with a porous body (``gain_lose``). In the case settings, it is the frame *Drying of thin films* of the page *Initial conditions*, whose information button explains it.

.. _case-mesh:

Mesh configuration
------------------

The computational mesh divides the region of interest into triangles. Small triangles resolve the flow in more detail but increase the computing time strongly. aXqua therefore uses different resolutions in different zones:

* ``channel`` zones receive triangles that are stretched along the centerline, because the flow varies less along the channel than across it;
* ``floodplain`` zones receive larger triangles of regular shape;
* ``refinement`` zones receive small triangles of regular shape, for example around a structure.

The edge length of each zone is read from the field ``Max Edge Length (m)`` of the mesh zone layer, so that the resolution is set in QGIS. The ``mesh`` block provides the values that apply where this field is empty:

.. code-block:: yaml

   mesh:
     channel_size: 0.5         # [m] edge length across the channel
     floodplain_size: 1.5      # [m]
     refinement_size: 0.5      # [m]
     channel_anisotropy: 4.0   # ratio of the edge length along and across the channel
     growth_ratio: 1.2         # largest size ratio of neighboring triangles

The default values produce a fine mesh. A reach of 0.3 km² then has several hundred thousand triangles. Use larger edge lengths while boundaries and roughness are still being checked, and refine afterwards. The mesh convergence study determines which resolution is sufficient (:doc:`mesh-convergence`).

Without mesh zones and a centerline, aXqua creates a mesh of uniform resolution (``default_size``).

**Hydrology mode.** The check box *Hydrology mode* at the top of the frame *TELEMAC mesh* switches the case to rainfall-runoff over a catchment (:ref:`case-hydrology`). A catchment has no channel to align cells with, so the mesh then consists of near-equal triangles everywhere: the edge length of each mesh zone applies isotropically, ``default_size`` applies outside the zones, and streams drawn as ``geodata.breaklines`` are refined to ``breakline_size``. Between zones the edge length grows by at most ``growth_ratio`` per triangle. The channel centerline is not used and is grayed out.

**OpenFOAM lattice.** The OpenFOAM model is built on a lattice of columns in plan with layers in the vertical (:ref:`preprocessing-openfoam-choices`). Its resolution is set on the same page of the case settings, in the frame *OpenFOAM lattice*. With ``openfoam.cell_size_factor``, the cell size of the lattice follows the edge length in the channel: 3 makes the lattice three times coarser than the TELEMAC mesh, so that a change of the TELEMAC mesh carries over. Without the factor, ``openfoam.cell_size`` is used as it is. ``openfoam.n_layers`` is the number of layers in the vertical.

.. _case-roughness:

Wall roughness
--------------

Flowing water experiences friction forces at the riverbed and the banks. These forces depend on the roughness of the wetted surfaces, and they determine the water depth for a given discharge. Roughness is therefore the most important parameter of a hydraulic model, and it is the parameter that is usually calibrated.

aXqua describes roughness by the **equivalent sand roughness** :math:`k_s` (in meters) after Nikuradse, which is a length of the order of the coarse grains of the bed. Each polygon of the layer ``roughness_zones`` carries an integer ``Zone ID``, and the table ``roughness_table`` assigns a :math:`k_s` value to each ID:

.. code-block:: text

   zone_id,ks
   1,0.2
   2,0.5

In this example, zone 1 is the gravel bed of the channel and zone 2 is a vegetated floodplain. The ``friction`` block contains the roughness of the closed walls at the model outline and the values that apply without roughness zones:

.. code-block:: yaml

   friction:
     roughness_law: 5              # 5 = Nikuradse (ks), 4 = Manning (n), 3 = Strickler
     boundary_law: 3
     boundary_coefficient: 38.0

The :math:`k_s` values of the zones are starting values. The calibration adjusts them within the limits set by the user (:doc:`calibration-validation`).

.. _case-hydraulics:

Hydraulic simulation
--------------------

The ``hydrodynamics`` block controls the hydraulic simulation with TELEMAC. Most entries have default values that are suited for water bodies with dry and wet areas. The entries that a user typically sets are:

.. code-block:: yaml

   hydrodynamics:
     duration: 7200           # [s] simulated time
     turbulence_model: auto   # auto, or the number of a TELEMAC turbulence model
     flux_tolerance: 1.0e-3   # accepted relative difference of inflow and outflow

``duration`` has to be long enough for the reach to fill and for the flow to become steady. A steady state is reached when the same discharge leaves the model that enters it. aXqua evaluates this balance after each run and compares the relative difference with ``flux_tolerance``. The default of 0.1 % is far smaller than the uncertainty of a discharge measurement.

With ``turbulence_model: auto``, aXqua selects the turbulence model from the mesh resolution relative to the water depth. The time step is adapted automatically during the run, so that the simulation remains stable (``desired_courant``).

The number of processor cores is a property of the computer and is set in its profile (``mpi_processes``, :ref:`solver-bindings`). Where the profile names no number, ``telemac.n_processors`` of the case file applies. The settings of the 3D simulation with OpenFOAM are described in :ref:`preprocessing-openfoam-choices`.

.. _case-hydrology:

Hydrology
---------

The page *Hydrology* of the case settings describes a rainfall-runoff simulation over a catchment with TELEMAC (:doc:`hydrology-simulations`). Its switch at the top, which is the same as the check box *Hydrology mode* on the page *Mesh configuration*, writes the block ``hydrology``:

.. code-block:: yaml

   hydrology:
     enabled: true
     rainfall: user-sources/hydrology/rainfall.csv   # time and rain
     rainfall_units: mm/h                             # or mm per interval
     rainfall_stamp: end                              # a time stamp ends its interval
     infiltration: green-ampt
     green_ampt: sadeghi                              # de Vos et al. (2024)
     soil_zones: user-sources/hydrology/soil-zones.gpkg
     soil_table: user-sources/hydrology/soil-table.csv
     initial_water_content: 0.25                      # [m3/m3] at the start
     weather: user-sources/hydrology/weather.csv
     wind: true
     evaporation: et0                                 # FAO-56 reference times 1.05
     outlet_condition: normal_depth
     drain_time: 3600                                 # [s] after the last rain

In hydrology mode, the case needs no discharge and no inflow line: the rain drives it, and the outflow lines of the page *Liquid boundaries* are where the water leaves. Without any outflow line the catchment is closed and keeps its rain. The page has four frames: *Rain*, *Infiltration (Green-Ampt)*, *Wind and sun*, and *Outlet and run*, each with a button that explains it. The data wizard on the sub-tab *Simulations > Hydrology* writes the files and these entries (:ref:`the data wizard <help-hydrology-wizard>`).

.. _case-morphodynamics:

Morphodynamic simulation
------------------------

A morphodynamic simulation computes sediment transport and the resulting change of the bed (:doc:`morphodynamic-simulations`). The page *Morphodynamic simulation* of the case settings has three frames: sediment transport with TELEMAC and GAIA (the block ``morphodynamics``), fine sediment over a fixed bed with OpenFOAM (``fine_sediment``, :ref:`help-morphodynamics-openfoam`), and the DEM of difference (``dem_of_difference``). Sediment transport with GAIA is activated in the ``morphodynamics`` block, which describes the sediment:

.. code-block:: yaml

   morphodynamics:
     enabled: true
     bedload: true
     suspended_load: false
     sediment_classes:             # one entry per grain size class
       - {diameter: 0.0008, density: 2650, shields: 0.047}
       - {diameter: 0.004,  density: 2650, shields: 0.047}
     active_layer_thickness: 0.1   # [m]

Each sediment class is defined by its grain diameter (in meters), its density and its critical Shields parameter, which is the dimensionless shear stress at which grains start to move.

The measured riverbed change serves as the reference for the calibration of a morphodynamic model. It is the difference of two terrain surveys, which is called a **DEM of difference** (DoD). With the following block, aXqua computes the DoD from ``geodata.dem_target`` and ``geodata.dem_initial``:

.. code-block:: yaml

   dem_of_difference:
     enabled: true
     uncertainty_initial: 0.10   # [m] vertical accuracy of the first survey
     uncertainty_target: 0.05    # [m] vertical accuracy of the second survey
     confidence_level: 0.95

Elevation changes that are smaller than the combined uncertainty of the two surveys cannot be distinguished from measurement error. aXqua removes them from the DoD. The limit is called the level of detection and results from the two accuracy values and the confidence level. Alternatively, ``min_lod`` sets the limit directly.

Where the riverbed stays in place and only its content of fine sediment changes, the simulation is defined in the ``fine_sediment`` block instead. It names the grain size of the fine sediment and its concentration in the water that enters the model:

.. code-block:: yaml

   fine_sediment:
     enabled: true
     grain_size: 0.00003          # [m]
     inflow_concentration: 0.02   # [kg/m3]
     pick_up: off

This simulation is computed with OpenFOAM. All entries of the block are explained in :ref:`help-morphodynamics-openfoam`.
