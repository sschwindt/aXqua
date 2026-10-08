Terminal syntax
===============

The QGIS plugin and the terminal use the same program. A simulation that is started from a terminal therefore produces the same files as one that is started from the plugin, and both appear in the job list of the other.

.. important::

   A terminal shows neither the input layers nor the results. Open the geodata in QGIS before the first run to verify that the layers fit together (:ref:`case-geodata`), and inspect the results of each step on a map before the next step is started. A model can run without any error message and still be wrong, for example because an inflow line lies at the wrong place.

Open a terminal
---------------

On **Linux**, open a terminal and activate the Python environment in which aXqua is installed:

.. code-block:: bash

   mamba activate axqua-env
   axqua --version

On **Windows**, open the *Miniforge Prompt* from the start menu and enter the same two commands. If the simulation software is installed in the Windows Subsystem for Linux, open the terminal of the Linux distribution instead and proceed as on Linux.

Commands
--------

A command consists of the program name, an action and, for most actions, the case file:

.. code-block:: text

   axqua <action> <case-file> [options]

Setting up the computer (:doc:`../installation/plugin-setup`):

.. list-table::
   :header-rows: 1
   :widths: 44 56

   * - Command
     - Function
   * - ``axqua profile init``
     - Create the profile of the computer from the software that is found on it.
   * - ``axqua profile check``
     - Verify the profile, including whether TELEMAC and OpenFOAM can be started.
   * - ``axqua profile show``
     - Print the profile in use.

Installing the simulation software (:doc:`../installation/simulation-software`):

.. list-table::
   :header-rows: 1
   :widths: 44 56

   * - Command
     - Function
   * - ``axqua install``
     - Show the detected operating system and, for TELEMAC, OpenFOAM and the postprocessors, where each is installed.
   * - ``axqua install plan telemac``
     - Show what the installation would do, which system packages are missing, and the command that installs them. Nothing is changed. The other programs are ``openfoam`` and ``postprocessors``.
   * - ``axqua install start telemac``
     - Start the installation as a process of its own and enter the result in the profile. Add ``--foreground`` to follow the installation in the terminal. ``--folder`` selects the installation folder.
   * - ``axqua install status telemac --tail 20``
     - Show the state of the installation and the last lines of its log.
   * - ``axqua install cancel telemac``
     - Stop the installation. What is already downloaded and built is kept.

Preparing and checking a case:

.. list-table::
   :header-rows: 1
   :widths: 44 56

   * - Command
     - Function
   * - ``axqua check <case-file>``
     - Report everything in the case file that is not in order: missing files, unknown or impossible settings, and settings that are still empty. The command lists all problems at once.
   * - ``axqua <case-file> --check``
     - Check the case file, the input files and the simulation software, and stop at the first problem.
   * - ``axqua case new <case-file>``
     - Create a case file with the entries that every case has.
   * - ``axqua <case-file>``
     - Run the preprocessing and write the TELEMAC model.
   * - ``axqua <case-file> --dry-run``
     - Run the preprocessing and start TELEMAC once, to verify that it accepts the model.
   * - ``axqua surface <case-file>``
     - Convert CAD files into the terrain and the layers of a case.
   * - ``axqua status <case-file>``
     - Show which simulations are configured, built and run.
   * - ``axqua openfoam <case-file> --check``
     - Show the number of cells of the OpenFOAM mesh without building it.
   * - ``axqua targets <case-file>``
     - Create the workbook for field measurements.
   * - ``axqua check-gt <case-file>``
     - Compare the elevations of the measurement points with the terrain model.
   * - ``axqua postproc <case-file>``
     - Render the predefined figures with VisIt.

Results for ParaView and VisIt (:doc:`../usage/postprocessing`):

.. list-table::
   :header-rows: 1
   :widths: 44 56

   * - Command
     - Function
   * - ``axqua export <case-file> --list``
     - List the TELEMAC results of the case with their time steps, and state which of them are exported.
   * - ``axqua export <case-file>``
     - Convert the TELEMAC results of the case into files that ParaView (``.pvd``) and VisIt (``.visit``) open. ``--result r2d.slf`` selects one result, and ``--frames last`` only its last time step.
   * - ``axqua export <result.slf> --geometry <geometry.slf>``
     - Convert one result file without a case. The geometry file of the model provides the exact node coordinates.

Tools for input data:

.. list-table::
   :header-rows: 1
   :widths: 44 56

   * - Command
     - Function
   * - ``axqua clip <raster> -b <polygon> -o <output>``
     - Cut a raster, for example a DEM, to a polygon.
   * - ``axqua rating -o <output.csv> ...``
     - Estimate a rating curve from channel width, bed slope and roughness.
   * - ``axqua migrate <old-file> --to-case``
     - Write a case file of the current type (``.axq-case``) next to a case file of an older aXqua version.

Running simulations as jobs (:doc:`batch-headless`):

.. list-table::
   :header-rows: 1
   :widths: 44 56

   * - Command
     - Function
   * - ``axqua submit <case-file> --kind <kind>``
     - Start a simulation as a job and print its ID.
   * - ``axqua list``
     - List all jobs.
   * - ``axqua status <job-id>``
     - Show the state and the progress of a job.
   * - ``axqua logs <job-id> --follow``
     - Show the log of a job while it runs.
   * - ``axqua cancel <job-id>``
     - Stop a job and all processes that it started.

Each command explains its options with ``--help``, for example ``axqua submit --help``.

Options for scripts
-------------------

With the option ``--json``, every command prints its result as one JSON document, which other programs can read reliably. If a command fails, its exit code indicates the cause, so that a script can react without reading the message:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Exit code
     - Cause
   * - 0
     - Success
   * - 2
     - Error in the case file
   * - 3
     - Error in the geodata
   * - 4
     - Simulation software not reachable
   * - 5
     - The simulation failed
   * - 6
     - The mesh could not be created
   * - 1
     - Any other error
   * - 130
     - Cancelled by the user

Scripts in the case folder
--------------------------

The template folder ``cases/case-template/`` contains one Python script per working step, for example ``preprocessing.py``, ``initial_run.py`` and ``mesh_convergence_study.py``. They perform the same steps as the commands above and run in the foreground, so that the output of the simulation code appears directly in the terminal. Settings that apply to one step only are listed at the top of each script.

.. code-block:: bash

   cd cases/my-reach
   python preprocessing.py
   python initial_run.py
