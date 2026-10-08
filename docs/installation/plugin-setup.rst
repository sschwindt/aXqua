.. _help-configuration:

Plugin setup
============

The plugin setup contains everything that describes the **computer** and not the river: where Python and the simulation software are installed, and where simulations are written. These settings are made once per computer. They are kept apart from the description of a river reach (:doc:`../usage/case-setup`), so that a case can be copied to another computer without changes.

.. _plugin-profile:

Plugin profile
--------------

All settings of a computer are stored in one file, the **profile**. Its name ends with ``.axq-profile``. The default profile is the file ``default.axq-profile`` in the aXqua configuration folder of the user (``~/.config/axqua/`` on Linux).

**In the plugin.** The *Configuration* tab shows the profile of this computer. On a computer without a profile, click *Create profile...*: aXqua searches for Python, TELEMAC, OpenFOAM, ParaView and VisIt and opens the profile editor with what it found. With a profile, the button reads *Edit profile...*. Every path in the editor has a button that opens a file dialog, so that nothing has to be typed. The editor has three buttons at the bottom right:

* *Save* writes the profile and checks it. The window stays open and shows what the check found.
* *Cancel* discards the changes since the last save and closes the window.
* *Exit* closes the window and asks first if there are changes that were not saved.

The check never prevents saving. It marks each entry that is not in order with a triangle, orange for a warning and dark red for an error. A click on a triangle opens the message with its remedy and a button that opens this documentation at the explanation of the message (:doc:`../troubleshooting/warnings`, :doc:`../troubleshooting/errors`). The button *Check* on the *Configuration* tab repeats the check at any time.

**In a terminal.** To create the profile, let aXqua detect what is installed on the computer:

.. code-block:: text

   axqua profile init

The command searches for Python, TELEMAC, OpenFOAM, ParaView and VisIt, writes what it finds into the default profile and prints the location of the file. It never overwrites an existing profile. Afterwards, verify the profile:

.. code-block:: text

   axqua profile check

The check reports everything that is missing or wrong, each item with the entry of the profile that it concerns. It also enters the environment of TELEMAC and of OpenFOAM, to verify that the codes can actually be started, which takes a few seconds. ``axqua profile show`` prints the profile in use.

The profile is a text file in YAML format and can be completed with a text editor:

.. code-block:: yaml

   schema_version: 1
   name: workstation
   python:
     executable: /home/user/miniforge3/envs/axqua-env/bin/python
     axqua: /home/user/miniforge3/envs/axqua-env/bin/axqua
   solvers:
     telemac:
       setup_script: /home/user/opt/telemac-mascaret/configs/pysource.debian12.sh
       mpi_processes: 12
     openfoam:
       setup_script: /usr/lib/openfoam/openfoam2406/etc/bashrc
       mpi_processes: 16
   postprocessors:
     paraview: /usr/bin/paraview
     visit: /home/user/opt/visit/bin/visit
   jobs:
     root: /scratch/axqua-jobs
   display:
     min_depth: 0.01
     velocity_cap: 5.0

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Block
     - Content
   * - ``python``
     - The Python interpreter in which aXqua is installed, and the ``axqua`` program that the plugin calls.
   * - ``solvers``
     - One binding per simulation code (:ref:`solver-bindings`).
   * - ``postprocessors``
     - The programs for three-dimensional figures (:doc:`postprocessors`).
   * - ``jobs``
     - Where and how simulations are run (:ref:`plugin-job-execution`).
   * - ``display``
     - Two values for maps in QGIS. ``min_depth`` is the minimum water depth in meters. Shallower water is drawn transparent, because a water film of a few millimeters stands between the roughness elements of the bed and does not flow. ``velocity_cap`` is the upper limit of the velocity color scale in m/s. Higher depth-averaged velocities typically occur in almost dry cells at the edge of the water and would otherwise flatten the color scale of the entire map.

To use another profile than the default one, for example on a computing cluster, set the environment variable ``AXQUA_PROFILE`` to its path, or pass ``--profile <file>`` when a job is submitted.

.. note::

   Editing the profile in a window of the plugin is not yet available in this version. Until then, the plugin stores the path of the ``axqua`` program and the two display values in *aXqua > Settings*.

.. _solver-bindings:

Define solver bindings
----------------------

A solver binding tells aXqua how to reach one simulation code. Its central entry is ``setup_script``, the environment script of the installation (:doc:`simulation-software`):

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Entry
     - Meaning
   * - ``setup_script``
     - The environment script of the code: ``pysource.<system>.sh`` for TELEMAC and ``etc/bashrc`` for OpenFOAM.
   * - ``mpi_processes``
     - Number of processor cores that a simulation uses on this computer. Without this entry, the number in the case file applies. Use the number of physical cores, not of logical cores.
   * - ``environment``
     - ``posix`` (Linux and macOS, the default there), ``windows`` (an installation directly on Windows) or ``wsl`` (an installation in the Windows Subsystem for Linux).
   * - ``distro``
     - Name of the Linux distribution, for ``environment: wsl`` only. The ``setup_script`` is then a path inside that distribution.
   * - ``mpi_launcher``
     - The program that starts parallel runs, if it is not ``mpirun`` (``mpiexec`` on Windows).

A code without a binding cannot be used on the computer. This is no error: a computer on which only TELEMAC is installed simply has no ``openfoam`` entry.

A profile takes precedence over machine settings that a case file may still contain. Three sources take precedence over the profile, for special situations: the environment variables ``AXQUA_TELEMAC_PYSOURCE`` and ``AXQUA_OPENFOAM_BASHRC``, and a file ``solvers.local.yml`` next to a case file, which binds another installation for this one case.

Installations that were set up with an earlier version of aXqua keep working without a profile. Their settings in the files ``solvers.yml`` and ``profiles.yml`` of the configuration folder are still read. ``axqua profile init`` takes over the bindings of ``solvers.yml``.

.. _plugin-job-execution:

Job execution
-------------

Every simulation runs as a **job**: an independent process with its own folder, which continues when QGIS is closed. Two entries of the ``jobs`` block control how jobs are run:

* ``root`` is the folder in which the job folders are created. Select a drive with sufficient free space, because the results of a single simulation can reach tens of gigabytes. Without this entry, jobs are written to the aXqua data folder of the user.
* ``launcher`` is the method that detaches a job from QGIS. The default ``auto`` selects a suitable method for the operating system and rarely needs to be changed.

:doc:`../automation/batch-headless` describes jobs in detail.
