.. _help-preprocessing:

Preprocessing
=============

Preprocessing converts the case description into the files that a simulation code reads. No simulation is started during preprocessing. Its result is a complete model that can be inspected before computing time is spent.

.. _preprocessing-workflow:

aXqua workflow
--------------

aXqua builds a model in five steps, each of which uses the result of the previous one:

#. **Terrain.** The digital elevation model is converted to the coordinate reference system of the case and cut to the region of interest.
#. **Mesh.** The region of interest is divided into triangles according to the mesh zones, and each mesh node receives its bed elevation from the terrain model and its roughness zone.
#. **Boundaries.** The nodes on the outline of the mesh are compared with the inflow and outflow lines. Nodes on a line become open boundaries, and all other outline nodes become closed walls.
#. **Simulation settings.** The control file of TELEMAC (the steering file) and the roughness table are written from the settings of the case file.
#. **Measurements.** The field measurements are compiled into the table that the calibration reads.

Because all files originate from one case file, they are consistent with each other. For example, the order of the boundary nodes in the boundary file always matches the mesh.

To start preprocessing in the plugin, select the case on the *Case Setup* tab and click *Build* on the *Preprocessing* tab. The table *Preprocessing checkup* on that tab then states for every simulation of the case whether the case file asks for it, whether it is built, and whether it has been run. In a terminal, the command is:

.. code-block:: text

   axqua <case-file>

The model is written to the folder ``axqua-case/simulation/``. It consists of the mesh with bed elevations (``geometry.slf``), the boundary conditions (``boundaries.cli``), the roughness table (``friction.tbl``) and the steering file (``steady2d.cas``). The log file ``axqua.log`` in the same folder records every step with its duration.

.. _preprocessing-meshing:

Meshing
-------

The mesh is created **once** and then reused by all subsequent TELEMAC simulations of the case, including simulations with a varying discharge and 3D simulations, which use the same mesh in plan view. The only exception is the mesh convergence study, which deliberately creates several meshes of different resolution (:doc:`mesh-convergence`).

Two properties of the mesh generation are relevant in practice:

* The edge lengths in the mesh zones are target values. The realized triangles deviate slightly from them, in particular at the transition between the channel and the floodplain, where the triangle size changes gradually.
* Two builds from identical inputs produce slightly different meshes, with differences of less than 1 % in the number of nodes. Both meshes are valid. A result should therefore always be reported together with the mesh on which it was computed, and the mesh file should be kept.

After meshing, aXqua assesses the mesh and writes a quality report into the log file. The report lists the angles and the elongation of the triangles, the size ratio of neighboring triangles and the **shortest edge**. The shortest edge matters most, because it limits the time step of the simulation: one very small triangle can slow down the entire computation.

OpenFOAM requires a three-dimensional mesh. aXqua derives it from the same region of interest in a separate step (:ref:`preprocessing-openfoam-choices`).

.. _preprocessing-checkup:

Pre-processing checkup
----------------------

Three checks show whether a case is complete and plausible before a simulation is started.

**Check the inputs before building.** The following command reads the case file, verifies that all input files exist and that TELEMAC can be reached, and then stops. It takes about one second.

.. code-block:: text

   axqua <case-file> --check

**Check what has been built.** aXqua reports for each code and each type of simulation whether it is available, whether the case requests it, and whether its files exist:

.. code-block:: text

   axqua status <case-file>

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Column
     - Meaning
   * - ``implemented``
     - aXqua offers this type of simulation for this code (``yes``, ``no``, or ``n/a`` when the type does not exist for the code).
   * - ``configured``
     - The case file requests this type of simulation.
   * - ``built``
     - The input files of the simulation exist.
   * - ``run``
     - A result file exists.

The plugin uses the same information to enable and disable its tabs and buttons. aXqua also stores it in two text files in the case folder, whose names state whether the case uses a code (for example ``MODEL=TELEMAC_ENABLED`` and ``MODEL=OPENFOAM_DISABLED``).

**Read the log file of the build.** Verify three items in ``axqua-case/simulation/axqua.log``:

* the mesh quality report contains no warnings for the floodplain and a plausible shortest edge;
* nodes were assigned to every inflow and outflow line. A warning appears when the node spacing at the inflow and at the outflow differs by more than 10 %, which may cause an unstable simulation;
* the measurement points lie inside the model. The command ``axqua check-gt <case-file>`` additionally compares the elevations of the measurement points with the terrain model.

The final check is the first simulation itself, which shows whether inflow and outflow reach a balance (:ref:`hydraulics-dry-runs`).

.. _preprocessing-telemac-choices:

TELEMAC choices
---------------

Two choices of the ``hydrodynamics`` block affect how TELEMAC solves the flow equations. Both have defaults that are suited for most river reaches.

**Numerical method.** By default, TELEMAC uses finite elements. With ``finite_volumes: true``, it uses finite volumes instead, which is more robust for flows that change between subcritical and supercritical conditions, for example at a hydraulic jump. The finite volume method only supports a constant turbulent viscosity.

**Turbulence model.** With ``turbulence_model: auto``, aXqua compares the cell size in the channel with the water depth and selects the model that suits the mesh: a large eddy simulation model (Smagorinsky) on fine meshes, the k-epsilon model on meshes of intermediate resolution, and the Spalart-Allmaras model on coarse meshes. A number selects a particular TELEMAC model instead.

**Exchange with a porous body.** Some river reaches lose water into a gravel bar and regain it further downstream. A depth-averaged model has no subsurface flow, so that aXqua represents this exchange by withdrawing water where it infiltrates and adding the same amount where it returns. The ``gain_lose`` block activates this function. ``zone`` names a polygon layer of the porous body, and ``conductivity`` is its hydraulic conductivity in m/s. Without this block, aXqua models no exchange, also if the liquid boundary layer contains internal exchange lines (``int-...``). The build log then names these lines.

.. code-block:: yaml

   gain_lose:
     enabled: true
     zone: user-sources/geodata/porous-body.gpkg
     conductivity: 3.0e-4

The locations of infiltration and return follow from the water levels of the channel at both ends of the porous body and therefore do not have to be drawn. The hydraulic conductivity of a riverbed varies over several orders of magnitude and should be calibrated (:doc:`calibration-validation`).

Structures that are never overtopped, such as walls and buildings, are represented in the TELEMAC model by a raised bed: aXqua lifts the bed at such structures to the crest elevation plus 2 m (``structures.solid_freeboard_2d``). With ``structures.solid_mode: cut``, their footprint is removed from the mesh instead.

.. _preprocessing-openfoam-choices:

OpenFOAM choices
----------------

The ``openfoam`` block activates and controls the three-dimensional model. Its mesh consists of columns of box-shaped cells that follow the riverbed at the bottom. The top of the mesh, which is called the lid, lies shortly above the water surface of the TELEMAC warm-up (:ref:`warm-up-concept`), so that the mesh contains little air.

.. code-block:: yaml

   openfoam:
     mode: vof              # vof | rigid-lid
     cell_size: 0.5         # [m] horizontal cell size
     n_layers: 14           # number of cells over the height
     n_processors: 16
     end_time: 300          # [s] simulated time

``mode`` is the most important choice:

``vof`` (volume of fluid)
    Water and air are both simulated, and the position of the water surface is a result. This mode answers questions about the shape of the water surface, for example at a weir or in a bend. It is the most expensive type of simulation in aXqua.
``rigid-lid``
    The lid is placed exactly on the water surface of the TELEMAC warm-up, and only water is simulated. The water surface cannot move. A coarse run of this type takes minutes where a ``vof`` run takes hours to days. It is suited for detecting errors in the mesh, the boundaries and the roughness before a ``vof`` run is started. It is not applicable where the water surface drops in steps, for example in a fish pass with pools. aXqua detects this situation and refuses to build such a model.

Instead of ``cell_size``, the entry ``cell_size_factor`` sets the horizontal cell size as a multiple of the channel edge length of the TELEMAC mesh. To obtain the number of cells without building the model, use:

.. code-block:: text

   axqua openfoam <case-file> --check

To build the OpenFOAM model in the plugin, click *Build* on the *Free surface (VOF)* tab. The build reports the time step that the simulation will use and the resulting number of time steps, which indicates the computing time. The TELEMAC warm-up is active by default (``openfoam.pre_run.enabled``). With ``openfoam.pre_run.dimension: 3d``, the warm-up continues with a TELEMAC-3D run, so that the OpenFOAM model also starts with a vertical velocity distribution. This option requires a water depth of several horizontal cell sizes and is skipped with a message in shallow reaches.
