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

Where aXqua knows a remedy, the error message contains it. For a failed job, the error is stored with its code and its remedy in the file ``status.json`` of the job and is shown below the job list of the plugin.

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

Check of a case
---------------

The check of a case (*Check* on the *Case Setup* tab, *Save* in the case editor, or ``axqua check <case-file>``) reports the following errors. The plugin shows each of them as a dark red triangle next to the setting concerned. An error does not prevent saving the case, but the model cannot be built until it is corrected.

.. _axqua-config-missing-value:

``... is required`` or ``... is not set``
    A setting that every case needs is empty, for example the terrain model, the model outline, or the inflow and outflow lines. Select the file in the case editor.

.. _axqua-config-missing-file:

``the file of ... does not exist``
    The case file names a file that is not at the stated location. A path that does not start at the root of the drive is read relative to the folder of the case file. Select the file again in the case editor.

.. _axqua-config-unknown-key:

``... is not a setting of a case`` or ``... is not a block of a case file``
    The case file contains a name that aXqua does not know, in most cases because of a typing error. The message proposes the name that was probably meant. Correct the name in the case file with a text editor.

.. _axqua-config-yaml-syntax:

``the case file is not valid YAML``
    The structure of the file is damaged, for example by a missing colon or a wrong indentation. The message names the line. Correct it with a text editor. The case editor keeps the original of a case file as ``<name>.bak`` the first time it saves.

.. _axqua-config-not-a-case:

``the file does not contain the blocks of a case``
    The file is valid YAML but not a case file, for example a list. Select the correct file.

.. _axqua-config-unreadable:

``the case file cannot be read``
    The file does not exist or may not be read. Verify the path and the access rights.

Profile of this computer
------------------------

The check of the profile (*Check* on the *Configuration* tab, or ``axqua profile check``) reports the following errors. The plugin shows each of them as a dark red triangle next to the entry concerned. An error does not prevent saving the profile, but the function that depends on the entry does not work until the entry is corrected.

.. _axqua-environment-program-missing:

``the ... does not exist`` or ``the ... launcher ... is not executable``
    The profile names a program file that is not on this computer, or that may not be started. Select the file again in the profile editor.

.. _axqua-environment-script-missing:

``the environment script of ... does not exist``
    The profile names an environment script that is not on this computer. Select the script of the installation (:ref:`solver-bindings`).

.. _axqua-environment-solver-unreachable:

``... cannot be reached``
    aXqua loaded the environment script and could not start the simulation program. The message states what is missing. Verify in a terminal that the installation works by itself, and that the profile names the environment script of this installation.

.. _axqua-environment-job-root-not-writable:

``the job root ... cannot be written to``
    aXqua cannot create files in the folder for jobs. Select a folder on a drive with write access and sufficient free space.

.. _axqua-config-invalid-value:

``the launcher must be one of ...``, ``the number of processes must be at least 1`` and similar
    An entry of the profile has a value that is not possible. The message names the entry and the permitted values.

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

``TELEMAC-2D stopped the run ... because a value left its limits``
    The water depth or a velocity component left the range that the keyword ``LIMIT VALUES`` of the steering file allows, which is 1000 m/s for the velocity. Such a value is a numerical instability and not a flow. TELEMAC-2D itself ends such a run without an error code, so aXqua reads the stop from the listing and marks the job as ``FAILED``. The message states the quantity, its value, the coordinates of the mesh node, and the simulated time. Inspect the mesh, the bed elevation, and the initial water depth at these coordinates in QGIS. Common causes are a sink or an open boundary on cells that are almost dry, an initial water surface that is far from the steady one, and a time step that is too large (``hydrodynamics.desired_courant``). Internal exchange lines that are applied as source regions with a fixed rate (``boundaries.internal_sources: true``) can cause this stop at the first time step, because the discharge is then withdrawn from a narrow strip regardless of the water depth. Use the block ``gain_lose`` instead, which limits the withdrawal by the water depth (:ref:`preprocessing-telemac-choices`).

``RIGID LID DOES NOT APPLY HERE``
    The water surface of the TELEMAC result drops in steps that exceed the local water depth, for example at the slots of a fish pass. A fixed lid cannot represent such a water surface. Use ``openfoam.mode: vof`` for this case (:ref:`preprocessing-openfoam-choices`).

Jobs
----

A job fails immediately after its start
    In most cases, the simulation software cannot be reached from the job. Read ``runner.log`` of the job and verify the profile with ``axqua profile check`` (:ref:`solver-bindings`). Note the warning about the ambient environment (:doc:`warnings`): a job does not inherit the settings of the terminal or of QGIS.

``the 'systemd' launcher is not available on this machine``
    The Linux user services that aXqua uses by default are not available, which is typical for some remote sessions. Submit the job with the option ``--launcher posix``, or select ``posix`` under *How jobs are detached* in the profile editor of the plugin (*Configuration* tab).

A job remains in the state ``RUNNING`` although nothing is computed
    Query the state with ``axqua status <job-id>`` or click *Refresh* below the job list. aXqua then verifies whether the process still exists and marks the job as ``FAILED`` if it does not, for example after a restart of the computer. The job can be submitted again.

Plugin
------

aXqua is missing from the list of installed plugins
    QGIS lists a plugin only if it can read the file ``metadata.txt`` in the plugin folder, and it skips a plugin with an unreadable file without a message. Run ``python scripts/build_plugin_zip.py --check`` in the repository, which reads the file in the same way as QGIS and reports the line that cannot be read.

The tabs of the workflow show only the note to add a case
    No case is selected, or the case file could not be read. Add a case on the *Case Setup* tab. If the tabs remain empty, run ``axqua case-status <case-file> --no-write`` in a terminal to obtain the error message.

A result layer is loaded without colors
    The name of a result variable differs from the expected one. The file is intact. Assign the style in the QGIS layer properties.
