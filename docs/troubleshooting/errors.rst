Error messages
==============

An error means that aXqua could not complete a step. Errors appear in the log files with the level ``ERROR`` and in the message bar of the plugin (:doc:`tips-logfiles`).

Each error belongs to one of five categories. The category determines where to look for the cause:

.. list-table::
   :header-rows: 1
   :widths: 24 22 54

   * - Category
     - Code
     - Where to look
   * - Case file
     - ``axqua.config``
     - An entry of the case file is missing, misspelled or has an invalid value.
   * - Geodata
     - ``axqua.geodata``
     - An input layer or raster is missing or lacks a required property.
   * - Simulation software
     - ``axqua.environment``
     - TELEMAC or OpenFOAM cannot be reached with the current solver bindings.
   * - Simulation
     - ``axqua.solver``
     - The simulation code started and stopped with an error. Read its own output.
   * - Mesh
     - ``axqua.mesh``
     - The mesh could not be created or is not usable.

Where aXqua knows a remedy, the error message contains it. For a failed job, the error is stored with its code and its remedy in the file ``status.json`` of the job and is shown on the *Jobs* tab of the plugin.

.. note::

   Unique codes for the individual errors within these categories, which link each message to its explanation, are not yet available in this version.

Installation
------------

``ModuleNotFoundError: No module named 'axqua'``
    The aXqua program is not installed in the active Python environment. Activate the environment and install aXqua (:doc:`../installation/qgis-plugin`). A copy of the repository alone is not sufficient, because the source code lies in the subfolder ``src/``.

``ImportError: libjxl.so...: cannot open shared object file``
    A system library that the geospatial packages require does not match their version. This occurs when packages of different origin are mixed in one environment. Create the environment again from ``environment.yml``, which installs all geospatial packages consistently from one source.

``axqua could not be found`` (plugin)
    The plugin did not find the program ``axqua``. The message lists the three places that were searched. Enter the full path of the program in *aXqua > Settings* and click *Test* (:doc:`../installation/qgis-plugin`).

Case file
---------

``... unknown config keys [...]``
    A block of the case file contains an entry that aXqua does not know, usually because of a typing error. The message names the block and the entry. Compare the spelling with the template in ``cases/case-template/``.

``telemac.pysource is not set`` or ``TELEMAC pysource script not found``
    aXqua does not know where TELEMAC is installed, or the stated environment script does not exist. Create the profile of the computer with ``axqua profile init``, or correct its binding for TELEMAC (:ref:`solver-bindings`).

``outflow_condition: elevation requires boundaries.prescribed_elevation``
    The outflow boundary is set to a constant water surface elevation, but no elevation is given. Enter ``boundaries.prescribed_elevation``, or select another outflow condition (:ref:`case-boundaries`).

``initialization.prewet_depth needs geodata.mesh_zones``
    The pre-wetted start fills the channel zones with water and therefore requires a mesh zone layer with a zone whose name contains ``channel`` (:ref:`case-initialization`).

``morphodynamics.enabled but no geodata.dem_target / dem_of_difference provided``
    A morphodynamic case requires the measured bed change as reference. Enter a second terrain model or a DEM of difference (:ref:`case-morphodynamics`).

Preprocessing and simulations
-----------------------------

``BAMG failed`` or ``Fatal error in the meshgenerator``
    The mesh generator could not create the mesh, also after repeating the attempt with adapted settings. The edge lengths of neighboring mesh zones differ too strongly. Increase the smallest edge length or enlarge the refinement zone, so that the triangle size can change gradually.

TELEMAC stops at the first time step with a message of the subroutine ``DEBIMP``
    The inflow cross section is dry, so that TELEMAC cannot distribute the discharge over it. Verify that the inflow line lies in the channel and that the water depth of the filled stretch at the inflow (``initialization.dry_start_depth``) covers the bed along the entire line.

``RIGID LID DOES NOT APPLY HERE``
    The water surface of the TELEMAC result drops in steps that exceed the local water depth, for example at the slots of a fish pass. A fixed lid cannot represent such a water surface. Use ``openfoam.mode: vof`` for this case (:ref:`preprocessing-openfoam-choices`).

Jobs
----

A job fails immediately after its start
    In most cases, the simulation software cannot be reached from the job. Read ``runner.log`` of the job and verify the profile with ``axqua profile check`` (:ref:`solver-bindings`). Note the warning about the ambient environment (:doc:`warnings`): a job does not inherit the settings of the terminal or of QGIS.

``the 'systemd' launcher is not available on this machine``
    The Linux user services that aXqua uses by default are not available, which is typical for some remote sessions. Submit the job with the option ``--launcher posix``, or select the launcher ``posix`` on the *Setup* tab of the plugin.

A job remains in the state ``RUNNING`` although nothing is computed
    Query the state with ``axqua status <job-id>`` or refresh the *Jobs* tab. aXqua then verifies whether the process still exists and marks the job as ``FAILED`` if it does not, for example after a restart of the computer. The job can be submitted again.

Plugin
------

The panel shows only the tabs *Setup* and *Jobs*
    No case is selected, or the case file could not be read. Add a case on the *Setup* tab. If the tabs remain missing, run ``axqua case-status <case-file> --no-write`` in a terminal to obtain the error message.

A result layer is loaded without colors
    The name of a result variable differs from the expected one. The file is intact. Assign the style in the QGIS layer properties.
