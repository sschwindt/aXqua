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
    A system library that the geospatial packages require does not match their version. This occurs when packages of different origin are mixed in one environment. Create a new environment that holds Python alone and install aXqua into it with ``pip install axqua`` (:doc:`../installation/qgis-plugin`), so that all geospatial packages come from PyPI. In a copy of the repository, ``environment.yml`` serves the same purpose with packages from conda-forge.

``axqua could not be found`` (plugin)
    The plugin did not find the program ``axqua``. The message lists the four places that were searched (the plugin setting, ``AXQUA_EXE``, the search path, the conda environments). Enter the full path of the program in *aXqua > Settings* and click *Test* (:doc:`../installation/qgis-plugin`).

Installation of the simulation software
---------------------------------------

The *Check* tab of an installation wizard, the *Installation* tab of a failed installation, and ``axqua install`` report the following errors. An installation cannot be started with an error of the *Check* tab.

.. _axqua-install-unsupported-system:

``there is no installer for this system``
    The installer scripts exist for Debian 12, Ubuntu 22.04, Ubuntu 24.04 and Linux Mint 22, and the TELEMAC installer does not cover Ubuntu 22.04. The OpenFOAM installer and the VisIt installer additionally require an x86-64 processor. On a system that is built on one of the supported systems, select the base system under *Further settings* on the *Settings* tab of the wizard. Otherwise install the program by hand and enter it in the profile editor (:doc:`../installation/simulation-software`).

.. _axqua-install-windows-needs-wsl:

``TELEMAC and OpenFOAM run in the Windows Subsystem for Linux (WSL 2), which is not set up on this computer``
    OpenFOAM does not run in Windows itself but inside WSL; TELEMAC can also be built in Windows itself, without WSL (:ref:`TELEMAC built on Windows <setup-telemac-windows-native>`). An administrator sets up WSL once with ``wsl --install -d Ubuntu-24.04`` in a terminal with administrator rights, then restarts the computer; open *Ubuntu* from the Start menu and create a Linux user. The *Setup Wizard* then installs TELEMAC and OpenFOAM into it without an administrator (:doc:`../installation/simulation-software`).

.. _axqua-install-conda-missing:

``TELEMAC built on Windows takes its compilers from conda-forge, and aXqua does not run from a conda installation``
    The installation of TELEMAC in Windows itself downloads its compilers into a conda environment, with the conda of the installation that aXqua runs from. aXqua was installed without conda, for example with the Python of python.org. Install Miniforge in your user folder, create the environment ``axqua-env`` and install aXqua in it as :doc:`../installation/qgis-plugin` describes, select its ``axqua.exe`` in *aXqua > Settings*, and start the installation again (:ref:`TELEMAC built on Windows <setup-telemac-windows-native>`).

.. _axqua-install-windows-user-only:

``TELEMAC built on Windows is installed for you alone, in your user folder``
    TELEMAC built in Windows itself goes into a folder of your user account and needs no administrator. An installation for all users of a Windows computer is not available. Install it for yourself; another user of the computer installs it for themselves.

.. _axqua-install-mpi-native-only:

``the choice of MPI is for TELEMAC built in Windows itself``
    The option ``--mpi`` selects the parallel build of TELEMAC in Windows itself. TELEMAC installed on Linux or inside WSL runs in parallel anyway. Leave out ``--mpi``, or build TELEMAC in Windows itself with ``--native``.

.. _axqua-install-as-root:

``the installers refuse to run as the user root``
    Start QGIS or the terminal as an ordinary user. Administrator rights are needed for the system packages only.

.. _axqua-install-windows-folder:

``... is a folder of Windows``
    In the Windows Subsystem for Linux, a folder below ``/mnt/`` belongs to Windows. Select a folder in the Linux file system, for example in the home folder.

.. _axqua-install-folder-with-space:

``the folder ... has a space in its name``
    Neither TELEMAC nor OpenFOAM can be built in a folder whose path contains a space. Select another folder.

.. _axqua-install-folder-for-all-users:

``... cannot be the folder of an installation for all users``
    At the end of an installation for all users, the whole installation folder is handed over to the administrator. A folder such as ``/opt`` or ``/usr`` itself, or your home folder, would be handed over with everything else it contains. Select a folder of its own, such as ``/opt/telemac``.

.. _axqua-install-folder-not-writable:

``... exists and you cannot write into it``
    The installation folder of an installation for all users belongs to somebody else and was not made by an installation of aXqua for all users, so aXqua does not take it over. Select a folder that does not exist yet.

.. _axqua-install-needs-admin:

``... needs the rights of an administrator``
    An installation for all users needs the password of an administrator before and after the build, and this session could neither open the password dialog of the desktop nor ask in a terminal. This happens to an installation that was started detached, without a desktop. Start it in a terminal with ``axqua install start <program> --for-all-users --foreground``, or have an administrator run the command that the message names and start the installation again. Nothing has been built at that point.

.. _axqua-install-folder-in-use:

``... exists and was not made by this installer``
    The OpenFOAM installer installs into a new folder or continues an installation of its own. Select a folder that does not exist yet.

.. _axqua-install-installer-changed:

``... holds an installation made by another version of the installer or against another OpenFOAM``
    The OpenFOAM installer continues only what the same version of it started with the same OpenFOAM. Open the *Setup Wizard* of OpenFOAM, select *Install OpenFOAM*, answer *Yes* to the question whether to install again, and select *Remove the existing installation and install it again in its folder*, or select a new folder.

.. _axqua-install-nothing-to-replace:

``there is no earlier installation to remove: ...``
    *Remove the existing installation* was selected for a folder that holds no installation that an installer of aXqua made. aXqua removes only such installations. Select the folder of the existing installation, or install into a new folder.

.. _axqua-install-file-missing:

``the SALOME archive ... does not exist`` or ``the OpenFOAM environment script ... does not exist``
    A file that was selected on the *Settings* tab of the wizard is not on this computer. Select it again, or clear the field.

.. _axqua-install-wrong-openfoam:

``... belongs to OpenFOAM release ...``
    The sediment transport solvers are built for OpenFOAM v2406 and the selected installation is another version. Select the file ``etc/bashrc`` of OpenFOAM v2406, or let the wizard compile OpenFOAM.

.. _axqua-install-no-installer:

``the installer scripts could not be downloaded``
    aXqua downloads the TELEMAC installer scripts before it checks the computer and did not reach the repository. The OpenFOAM installer and the source code of Olsen's solvers are shipped with aXqua and are not downloaded. Verify the internet connection. For a computer without a connection, copy the `installer repository <https://github.com/Ecohydraulics/numerical-software-installers>`_ onto it and select its folder under *Further settings* (in a terminal: the environment variable ``AXQUA_INSTALLERS``).

.. _axqua-install-python-too-old:

``the OpenFOAM installer does not run with any Python on this computer``
    The OpenFOAM installer requires a newer Python than the one that runs aXqua, and aXqua found no newer one. Install Python 3.12 or newer, for example with Miniforge, and install aXqua with it.

.. _axqua-install-failed:

``'...' ended with an error``
    A step of the installation failed. The message quotes the last lines of the installation log, which normally name the cause, and gives the location of the complete log. Typical causes are a missing system package, an interrupted download and a full drive. Correct the cause and start the installation again with the same folder: the installer continues with what is already downloaded and built.

    The lines ``remote: Retry later`` and ``error: 429`` mean that the download server of TELEMAC temporarily refuses further requests, which occurs after many downloads from one network in a short time. Wait a few minutes and start the installation again.

.. _axqua-install-output-missing:

``the installer finished, but did not produce ...``
    The installer reported success, but a program that it should have built is missing, so the build failed in part. Search the installation log for the first line that contains ``Error``.

``the program this installation enters in the profile is not on this computer``
    The installation consists of a package of the operating system, and the package is not installed. Install the system packages that the *Check* tab of the wizard lists, then start the installation again.

.. _axqua-install-packages-required:

``without VisIt, this installation is the system package of ParaView alone, which is not installed yet``
    With the selection *ParaView only*, the installation of the postprocessors downloads nothing: ParaView is a package of the operating system, and the wizard only enters it in the profile. Install the system packages that the *Check* tab lists, which requires administrator rights, and click *Check again* (:ref:`install-paraview`).

.. _axqua-install-interrupted:

``the installation ended without a result``
    The process of the installation disappeared, typically because the computer was restarted or the user was logged out. Start the installation again with the same folder.

Check of a case
---------------

The check of a case (*Check* on the *Case Setup* tab, *Save* in the case settings, or ``axqua check <case-file>``) reports the following errors. The plugin shows each of them as a dark red triangle next to the setting concerned. An error does not prevent saving the case, but the model cannot be built until it is corrected.

.. _axqua-config-missing-value:

``... is required`` or ``... is not set``
    A setting that every case needs is empty, for example the terrain model, the model outline, or the inflow and outflow lines. Select the file in the case settings (*Open case settings...* on the *Case Setup* tab).

.. _axqua-config-missing-file:

``the file of ... does not exist``
    The case file names a file that is not at the stated location. A path that does not start at the root of the drive is read relative to the folder of the case file. Select the file again in the case settings.

.. _axqua-config-unknown-key:

``... is not a setting of a case`` or ``... is not a block of a case file``
    The case file contains a name that aXqua does not know, in most cases because of a typing error. The message proposes the name that was probably meant. Correct the name in the case file with a text editor.

.. _axqua-config-yaml-syntax:

``the case file is not valid YAML``
    The structure of the file is damaged, for example by a missing colon or a wrong indentation. The message names the line. Correct it with a text editor. The case settings keep the original of a case file as ``<name>.bak`` the first time it saves.

.. _axqua-config-not-a-case:

``the file does not contain the blocks of a case``
    The file is valid YAML but not a case file, for example a list. Select the correct file.

.. _axqua-config-unreadable:

``the case file cannot be read``
    The file does not exist or may not be read. Verify the path and the access rights.

Hydrology checkup
-----------------

The hydrology checkup of a case in hydrology mode (*Check* on the sub-tab *Simulations > Hydrology* and on the tab *Preprocessing*, ``axqua hydrology check <case-file>``, and the start of every build) reports the following errors. An error stops the build (:doc:`../usage/hydrology-simulations`).

.. _axqua-hydrology-rainfall-unreadable:

``The rain series cannot be read`` or ``is not usable``
    The file of ``hydrology.rainfall`` is missing, has no time column, or has times that do not increase, intervals that overlap, or negative rain. A table of time and rain, in mm/h or in mm per interval, is expected; the data wizard writes one from a record.

.. _axqua-hydrology-weather-unreadable:

``The weather series cannot be used``
    Wind or evaporation is switched on, but ``hydrology.weather`` is missing or lacks the columns the choice needs: wind needs ``wind_speed_10m`` and ``wind_direction_10m``, evaporation ``et0`` needs ``et0``, ``penman`` needs radiation, temperature, humidity and wind with dates, and ``series`` needs ``evaporation``.

.. _axqua-hydrology-outlet-unknown:

``An outflow line has the outlet ... in the column Outlet``
    The column ``Outlet`` of the liquid boundaries gives an outflow line an outlet of its own, and the text in it is none of ``normal_depth``, ``free``, ``elevation`` or ``overfall``. Correct the text, or leave the cell empty, and the line takes ``hydrology.outlet_condition`` (:ref:`hydrology-outlet`). The data wizard writes the column on its tab *River*.

.. _axqua-hydrology-sediment-with-river:

``Bed load without any class in suspension stops GAIA at a river inflow`` or ``Suspended sand of ... mm ... stops TELEMAC at a river inflow``
    The case has a river that flows into the model through an inflow line, and its sediment setup is one that TELEMAC v9.1.1 cannot run there. The river brings clear water over a bed that the flow erodes at its full capacity, and the finite-volume kernel applies GAIA's erosion and deposition explicitly, so the concentration of coarse suspended sand overshoots beyond 1000 g/l within the first time steps and TELEMAC stops at its limit values; bed load without any class in suspension ends in a crash of GAIA. With a river, the suspended sand can be 0.15 mm at most when the soil also has a cohesive class or the river brings clear water, and 0.5 mm at most when the soil is sand only and the river enters at equilibrium (``morphodynamics.equilibrium_inflow``). Follow the remedy of the message, or leave the river line out of the liquid boundaries. Without a river, all these soils run (:ref:`hydrology-sediment`).

.. _axqua-hydrology-soil-invalid:

``Soil zone ...`` or ``The soil cannot be read``
    A value of the soil table is impossible (a negative Ks, a suction head above 1 m, which is usually a value in centimeters, or a water content at the start above saturation), a texture class is unknown, or a zone number of the soil layer is missing from the table.

Profile of this computer
------------------------

The check of the profile (*Check* on the *Configuration* tab, or ``axqua profile check``) and the check of an existing installation in the *Setup Wizard* (``axqua profile inspect``) report the following errors. The plugin shows each of them as a dark red triangle next to the entry concerned. An error does not prevent saving the profile, but the function that depends on the entry does not work until the entry is corrected.

.. _axqua-environment-program-missing:

``the ... does not exist``, ``the ... launcher ... is not executable`` or ``the ... program was not found in the installation folder ...``
    The profile names a program file that is not on this computer, or that may not be started, or it names an installation folder that does not contain the program. Select the file again in the profile editor.

.. _axqua-environment-script-missing:

``the environment script of ... does not exist``
    The profile names an environment script that is not on this computer. Select the script of the installation (:ref:`solver-bindings`).

.. _axqua-environment-solver-unreachable:

``... cannot be reached``
    aXqua loaded the environment script and could not start the simulation program. The message states what is missing. Verify in a terminal that the installation works by itself, and that the profile names the environment script of this installation.

.. _axqua-environment-solver-incomplete:

``...: the environment was entered, but ... is missing``
    The environment script sets up the variables of the simulation program, but a part that every simulation needs is not there: TELEMAC-2D in the build folder of TELEMAC, or ``interFoam``, ``checkMesh``, ``decomposePar`` or ``reconstructPar`` of OpenFOAM. The installation is incomplete, for example because its compilation stopped. The *Setup Wizard* does not enter such an installation in the profile. Select the environment script of a complete build, or install the program again with the *Setup Wizard*.

.. _axqua-environment-job-root-not-writable:

``the job root ... cannot be written to``
    aXqua cannot create files in the folder for jobs. Select a folder on a drive with write access and sufficient free space.

.. _axqua-config-invalid-value:

``the launcher must be one of ...``, ``the number of processes must be at least 1`` and similar
    An entry of the profile has a value that is not possible. The message names the entry and the permitted values.

Case file
---------

``... unknown config keys [...]``
    A block of the case file contains an entry that aXqua does not know, usually because of a typing error. The message names the block and the entry. Compare the spelling with the `commented template <https://github.com/sschwindt/aXqua/blob/main/cases/case-template/case-template.axq-case>`_ of the repository.

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
