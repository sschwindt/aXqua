Warning messages
================

A warning means that aXqua completed a step but found something that may affect the result. Warnings appear in the log files with the level ``WARNING`` (:doc:`tips-logfiles`). This page lists the most frequent warnings with their meaning and their remedy. The messages are quoted as they appear in the log, with ``...`` in place of the values of a particular case.

.. note::

   Unique codes for all warnings, which link each message to its explanation, are not yet available in this version.

Case file
---------

``deprecated config keys ..., still loaded but rename them``
    The case file uses entries of an older aXqua version. The case is read correctly. Run ``axqua migrate <case-file> --to-case`` to write a case file of the current type next to it.

``using the pre-rename artifact folder hydromate-case/``
    The case was built with an earlier version, in which the output folder had another name. aXqua continues to use the existing folder. Rename the folder to ``axqua-case`` when no job of this case is running.

Preprocessing
-------------

``BAMG meshing failed (...); retrying with a coarser background metric grid``
    The mesh generator did not succeed with its first settings and repeats the meshing with adapted settings. If no error follows, the mesh is valid, and no action is required. If the meshing fails repeatedly, the difference between the edge lengths of neighboring mesh zones is too large. Increase the smallest edge length or enlarge the refinement zone.

``STABILITY RISK: inflow and outflow are resolved at different node spacings``
    The mesh is much finer at one open boundary than at the other, which can cause an unstable simulation. Assign the mesh zones at the inflow and at the outflow a similar edge length.

``STABILITY RISK: liquid boundaries have ... inflow and ... outflow nodes``
    No mesh nodes were found on an inflow or outflow line. Verify that the lines lie on the outline of the region of interest and that their type field contains ``inflow`` or ``outflow``.

``liquid_boundaries ... has no inflow/outflow type column; treating every line as inflow``
    The line layer has no attribute field that identifies inflow and outflow. Add the field ``Type (inflow/outflow)`` (:ref:`case-boundaries`).

``ground-truth data do not match; skipping HydroBayesCal setup``
    The model was built, but the measurement tables for the calibration were not written, because the field measurements could not be read or do not match their point layer. The reason follows in the same message. Correct the measurement files and repeat the preprocessing before starting a calibration.

``finite volumes accept only TURBULENCE MODEL 1 (constant viscosity)``
    The finite volume method was selected together with another turbulence model. aXqua uses the constant viscosity model instead. Select finite elements if a turbulence model is required (:ref:`preprocessing-telemac-choices`).

``morphodynamics.enabled but neither bedload nor suspended_load is on``
    The morphodynamic simulation would not transport any sediment. Enable at least one transport mode (:ref:`help-morphodynamics-telemac`).

Simulations
-----------

``fluxes did NOT reach the ... imbalance tolerance within the run``
    Inflow and outflow still differed at the end of the run, so that the flow is not steady. Increase ``hydrodynamics.duration`` and repeat the run. If the difference no longer decreases, verify the downstream water level and the wetting report (:ref:`hydraulics-dry-runs`).

``no hotstart end time; extend the run or relax abs_tolerance``
    The difference between inflow and outflow did not remain small for long enough. The hotstart file, which the calibration requires, was therefore not written. Increase ``hydrodynamics.duration``.

``GRACJG: EXCEEDING MAXIMUM ITERATIONS`` (in the TELEMAC listing)
    A linear solver of TELEMAC did not reach its accuracy within the allowed number of iterations. During the filling of a dry reach, this message is common and harmless, provided that it disappears once the reach is filled. If it persists until the end of the run, the result should not be used.

``no 2D result at ...: the OpenFOAM case will use a flat lid and a cold start``
    The OpenFOAM model is built without a TELEMAC warm-up, which increases its computing time considerably. Run the steady TELEMAC simulation first (:ref:`warm-up-concept`).

``checkMesh flagged marginal faces (the mesh is still runnable)``
    The mesh check of OpenFOAM found a small number of cells of low quality, typically at steps in the terrain. The simulation can be run. If it becomes unstable, increase the cell size or smooth the terrain at the reported locations.

Check of a case
---------------

The check of a case (*Check* on the *Case Setup* tab, *Save* in the case editor, or ``axqua check <case-file>``) reports the following warnings. The plugin shows each of them as an orange triangle next to the setting concerned.

.. _axqua-config-no-solver:

``the case names no simulation program, so nothing can be built or run``
    The case file contains neither a ``telemac`` nor an ``openfoam`` block. Add the block of the program to use, for example by selecting the solver in the section *TELEMAC* of the case editor.

.. _axqua-config-incomplete:

``no discharge is set, so the steady simulation has no inflow``
    The case can be saved, but the next step of the workflow needs this setting. Enter the discharge in the section *Boundaries* of the case editor (:ref:`case-boundaries`).

Profile of this computer
------------------------

The check of the profile (*Check* on the *Configuration* tab, or ``axqua profile check``) reports the following warnings. The plugin shows each of them as an orange triangle next to the entry concerned. A click on the triangle opens the message. A warning does not prevent saving the profile.

.. _axqua-environment-solver-unbound:

``no environment script is set for ..., so simulations with it cannot be started``
    The profile does not state where TELEMAC or OpenFOAM is installed. Enter the environment script of the installation in the profile (:ref:`solver-bindings`). Leave the entry empty if the program is not used on this computer.

.. _axqua-environment-ambient:

``the variables of ... were already set before its environment script ran``
    The terminal or QGIS session already contained the settings of the simulation program before aXqua loaded the environment script, typically because a system-wide startup file loads them. A job does not inherit these settings. Verify that the environment script of the profile alone sets up the installation, in particular if several versions of the program are installed.

.. _axqua-environment-too-many-processes:

``... processes are requested for ..., but this computer has ... logical cores``
    More processes than processor cores slow a simulation down. Reduce the number of processor cores in the profile.

.. _axqua-environment-openfoam-version:

``this is OpenFOAM ...; aXqua writes input files for v2406``
    The environment script belongs to another OpenFOAM version. aXqua writes its input files for the version v2406, and other versions may reject them. Enter the environment script of OpenFOAM v2406 in the profile.

Installation of the simulation software
---------------------------------------

The *Check* page of an installation wizard, the last page of a finished installation, and ``axqua install plan`` report the following warnings. An installation can be started with a warning.

.. _axqua-install-packages-missing:

``... of ... system packages are not installed``
    The installation requires packages of the operating system that are missing. The message lists them and gives the command that installs them, which requires administrator rights. Click *Install the packages...* in the wizard, or copy the command into a terminal, and click *Check again* (:doc:`../installation/simulation-software`). For OpenFOAM with an existing OpenFOAM v2406, most of the listed packages are needed only for compiling OpenFOAM itself, and the installation normally works without them.

.. _axqua-install-packages-unavailable:

``the package sources of this system do not offer: ...``
    The operating system does not know a package that the installer names, typically because the system is older or newer than the one the installer was written for. The installation may still work if another package provides the same files. Otherwise an administrator has to add a package source.

.. _axqua-install-packages-unknown:

``the list of system packages could not be read from the installer``
    aXqua reads the list of required packages from the installer script and did not find it in this version of the script. The installation can be started. It stops with a message of its own if a package is missing.

.. _axqua-install-little-space:

``... GiB are free in ...; the installation needs about ... GiB``
    The drive of the installation folder may be too small. Select a folder on a drive with more free space on the first page of the wizard.

.. _axqua-install-folder-exists:

``... exists already`` or ``... holds an installation of this installer, which is continued``
    The installation folder contains an earlier installation. The installer continues with it: it keeps what is downloaded and builds the program again. Select another folder to leave the earlier installation untouched.

.. _axqua-install-compiles-openfoam:

``no OpenFOAM v2406 was found on this computer, so it is compiled from its source code``
    Compiling OpenFOAM takes several hours and about 20 GB. If OpenFOAM v2406 is installed, select its file ``etc/bashrc`` on the first page of the wizard. A packaged OpenFOAM v2406, which an administrator installs in a few minutes, avoids the compilation (:ref:`install-openfoam`).

.. _axqua-install-paraview-missing:

``ParaView is not installed``
    The wizards use the ParaView package of the operating system, and it is not installed. VisIt is installed all the same. Install the system packages listed on the *Check* page, or enter another ParaView in the profile editor (:ref:`install-paraview`).

.. _axqua-install-not-bound:

``the installation could not be entered in the profile``
    The program is installed, but the profile of the computer could not be written, typically because the existing profile contains an error. The message lists the entries. Open the profile editor on the *Configuration* tab, correct the profile, and enter them there (:ref:`plugin-profile`).

Simulation software
-------------------

``NOTE: already present in the ambient environment``
    The variables of a simulation code were already set in the terminal before aXqua read the environment script. A job does not inherit the settings of a terminal. Verify that the solver binding names the correct environment script (:ref:`solver-bindings`). Otherwise, a simulation may work in the terminal and fail as a job.
