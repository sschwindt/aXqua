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

The check of a case (*Check* on the *Case Setup* tab, *Save* in the case settings, or ``axqua check <case-file>``) reports the following warnings. The plugin shows each of them as an orange triangle next to the setting concerned.

.. _axqua-config-no-solver:

``the case names no simulation program, so nothing can be built or run``
    The case file contains neither a ``telemac`` nor an ``openfoam`` block. Add the block of the program to use, for example with the switch *Simulate with TELEMAC* on the page *TELEMAC* of the case settings.

.. _axqua-config-incomplete:

``no discharge is set, so the steady simulation has no inflow``
    The case can be saved, but the next step of the workflow needs this setting. Enter the discharge on the page *Liquid boundaries* of the case settings (:ref:`case-boundaries`).

.. _axqua-config-discharge-conflict:

``The files of the validation situation 'High flow' show different discharges; the last one in the table was saved.``
    A validation situation has one discharge, and the table *Measurement files* of the case settings shows it in every row of the situation. Where the rows still show different values, the last one in the table was saved. Enter the discharge of the situation in one of its rows; the other rows follow (:ref:`validation situation <validation-setup>`).

Profile of this computer
------------------------

The check of the profile (*Check* on the *Configuration* tab, or ``axqua profile check``) reports the following warnings. The plugin shows each of them as an orange triangle next to the entry concerned. A click on the triangle opens the message. A warning does not prevent saving the profile.

.. _axqua-environment-solver-unbound:

``no environment script is set for ..., so simulations with it cannot be started``
    The profile does not state where TELEMAC or OpenFOAM is installed. Select the installation folder or the environment script of the installation in the profile editor (:ref:`solver-bindings`). Leave both entries empty if the program is not used on this computer.

``the installation folder of ... holds several environment scripts`` or ``no environment script of ... was found in the installation folder``
    The profile names the installation folder of a simulation code and no environment script, and aXqua cannot determine the script from the folder. A TELEMAC installation can contain one environment script per compiler configuration. Select the environment script itself in the profile editor.

.. _axqua-environment-installation-missing:

``the installation folder of ... does not exist``
    The profile names a folder that is not on this computer, for example because the program was moved or removed. Select the folder again in the profile editor, or clear the entry. A program whose environment script or program file is entered in the profile keeps working.

.. _axqua-environment-installation-mismatch:

``the environment script of ... is not in the installation folder entered for it`` or ``the program of ... is not in the installation folder entered for it``
    The two entries of a program point to different installations. aXqua uses the environment script or the program file, and the folder has no effect. Select the folder to which the file belongs, or clear the folder.

.. _axqua-config-unknown-entry:

``this version of aXqua does not know the entry ...; it is kept as it is and not used``
    The profile contains an entry that the installed version of aXqua does not know. If a newer version of aXqua or of the plugin wrote the entry, update aXqua, and the entry takes effect. If the entry is a typing error in a profile that was edited by hand, the message names the entry that is probably meant. Correct the name in the profile file. The remaining entries of the profile are used in either case, and saving the profile keeps the entry.

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

The *Check* tab of an installation wizard, the *Installation* tab of a finished installation, and ``axqua install plan`` report the following warnings. An installation can be started with a warning.

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
    The drive of the installation folder may be too small. Select a folder on a drive with more free space on the *Settings* tab of the wizard.

.. _axqua-install-folder-in-home:

``... is inside your home folder, which other users usually cannot open``
    An installation for all users was given a folder inside your home folder. Other user accounts usually have no access to a home folder, so they could not run the programs. Select a folder outside of it, such as ``/opt/openfoam-sediment-v2406``.

.. _axqua-install-folder-exists:

``... exists already`` or ``... holds an installation of this installer, which is continued``
    The installation folder contains an earlier installation. The installer continues with it: it keeps what is downloaded and builds the program again. Select another folder to leave the earlier installation untouched.

.. _axqua-install-compiles-openfoam:

``no OpenFOAM v2406 was found on this computer, so it is compiled from its source code``
    Compiling OpenFOAM takes several hours and about 20 GB. If OpenFOAM v2406 is installed, select its file ``etc/bashrc`` on the *Settings* tab of the wizard. A packaged OpenFOAM v2406, which an administrator installs in a few minutes, avoids the compilation (:ref:`install-openfoam`).

.. _axqua-install-paraview-missing:

``ParaView is not installed``
    The wizards use the ParaView package of the operating system, and it is not installed. VisIt is installed all the same. Install the system packages listed on the *Check* tab, or enter another ParaView in the profile editor (:ref:`install-paraview`).

.. _axqua-install-visit-missing:

``VisIt was not installed, so it was not entered in the profile``
    OpenFOAM and the sediment solvers are installed and entered in the profile, but the installer did not leave VisIt where aXqua expects it. The simulation software works without VisIt. Install VisIt alone with the wizard for ParaView and VisIt on the *Configuration* tab, or with ``axqua install start postprocessors`` (:ref:`install-visit`).

.. _axqua-install-step-incomplete:

``'Download the input files of the example cases' did not complete``
    TELEMAC is installed and works, but the input files of its example cases were not downloaded completely. The message quotes the last lines of the installation log. The usual cause is the download server of TELEMAC, which refuses further requests for a while after many downloads from one network (``Rate limit exceeded``, ``error: 429``). Repeat the download later with the command that the message names, ``axqua install telemac-examples <folder>``. It fetches only the files that are still missing.

.. _axqua-install-not-bound:

``the installation could not be entered in the profile``
    The program is installed, but the profile of the computer could not be written, typically because the existing profile contains an error, or because another installation was writing the profile at the same moment and did not finish. The message lists the entries. Open the profile editor on the *Configuration* tab, correct the profile, and enter them there (:ref:`plugin-profile`).

Simulation software
-------------------

``NOTE: already present in the ambient environment``
    The variables of a simulation code were already set in the terminal before aXqua read the environment script. A job does not inherit the settings of a terminal. Verify that the solver binding names the correct environment script (:ref:`solver-bindings`). Otherwise, a simulation may work in the terminal and fail as a job.
