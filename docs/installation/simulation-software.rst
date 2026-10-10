Simulation Software
===================

aXqua prepares and starts simulations, but the numerical codes are separate software that must be installed on the computer on which the simulations run. The plugin installs them with one wizard per program. Install TELEMAC first. OpenFOAM is optional and builds on TELEMAC.

The wizards are opened on the *Configuration* tab, in the box *Simulation software*. The box names the operating system that aXqua detected, and states for each program where it is installed or that it was not found. Each wizard has three pages:

#. **Settings.** The installation folder and the few choices that the installer offers. Every folder and file is selected in a dialog, so nothing has to be typed.
#. **Check.** aXqua examines the computer and lists the commands that it is about to run, the expected duration, and everything that is not in order. Nothing has been changed at this point. An orange triangle marks a warning, with which the installation can be started. A dark red triangle marks an error, with which it cannot.
#. **Installation.** The wizard shows the current step and the end of the installation log. The installation is a process of its own: the wizard and QGIS can be closed while it runs. The *Configuration* tab reports that an installation is running, and the wizard shows it again when it is reopened.

At the end, aXqua verifies that the installer produced the program, enters the program in the profile of the computer (:ref:`plugin-profile`), and starts it once. No further configuration is required.

The wizards run the installer scripts of the `installer repository <https://github.com/Ecohydraulics/numerical-software-installers>`_, in the version with which this version of aXqua was tested. The scripts exist for Debian 12, Ubuntu 22.04, Ubuntu 24.04 and Linux Mint 22. For another system that is built on one of these, select the base system under *Further settings* on the first page.

**System packages and administrator rights.** Compilers and numerical libraries are packages of the operating system, and installing them is the only step that requires administrator rights. The *Check* page states how many of the required packages are missing and shows the one command that installs them. The command can be run in two ways:

* *Install the packages...* opens the password dialog of the desktop. The password is entered there and does not pass through the plugin.
* *Copy the command* places the command in the clipboard, to be pasted into a terminal. On a computer that is managed by an institution, send the command to the administrator.

Click *Check again* afterwards. The installation itself runs with the rights of the ordinary user and writes into the installation folder only.

**From a terminal.** Each wizard corresponds to one command, ``axqua install``, which does the same without QGIS (:doc:`../automation/terminal-syntax`).

**Windows.** On Windows, TELEMAC and OpenFOAM run in the Windows Subsystem for Linux (WSL 2). The wizards of a QGIS that runs in Windows do not install into WSL. Install a distribution with ``wsl --install -d Ubuntu-24.04`` in a PowerShell with administrator rights, install aXqua inside the distribution, and run ``axqua install start telemac`` in its terminal. Select an installation folder in the Linux file system: a build in a Windows folder (``/mnt/c/...``) is slower by an order of magnitude. This route has not yet been tested on a real Windows installation.

.. _install-telemac:

TELEMAC
-------

`TELEMAC <http://www.opentelemac.org/>`_ solves the depth-averaged (2D) and the three-dimensional (3D) flow equations. Its sediment transport module GAIA is part of the same installation. aXqua is developed and tested with TELEMAC v9.1.1.

Click *Install TELEMAC...* on the *Configuration* tab. The wizard is available on Debian 12, Ubuntu 24.04 and Linux Mint 22. Its first page has four settings:

.. list-table::
   :header-rows: 1
   :widths: 26 74

   * - Setting
     - Meaning
   * - Installation folder
     - TELEMAC is installed in the subfolder ``telemac-mascaret`` of this folder. The default is the folder ``opt`` in the home folder. If the subfolder exists already, the installer keeps its source code and builds TELEMAC in it again.
   * - TELEMAC version
     - Leave the field empty for v9.1.1.
   * - Example cases of TELEMAC
     - TELEMAC is distributed with about 830 example cases. Their steering files are always installed with the source code. The setting selects how many of their large files are downloaded in addition. *Input files only* (the default) downloads what a run of an example reads, which is mainly its mesh: about 360 files and 460 MB. *Everything* also downloads the reference results, with which TELEMAC validates itself, and the manuals: 1,500 files and 1.65 GB, which takes about one hour. *Steering files only* downloads none of the large files. aXqua itself requires none of these files.
   * - SALOME archive
     - Optional. A downloaded archive of SALOME, which is then installed next to TELEMAC. aXqua does not require SALOME, because it generates the mesh and the input files itself.

The installation downloads the source code of TELEMAC and compiles it for parallel computation with the system libraries MPI, MUMPS, METIS and MED. It requires about 2.5 GB of free space with the input files of the example cases and about 5 GB with everything. On a workstation with 32 processor cores, the installation with the default setting took 11 minutes, of which the input files of the example cases took 7 minutes.

The large files of the example cases are stored separately from the source code, and the download server delivers them one at a time when all of them are requested. For this reason, the default setting requests only the files that a run reads. aXqua determines these files from TELEMAC itself: the keyword dictionaries of TELEMAC state for every file whether a run reads or writes it. The missing files can be downloaded at any time afterwards: ``axqua install telemac-examples <folder>`` downloads the input files that are still missing, and ``git lfs pull`` in the folder ``telemac-mascaret`` downloads everything.

The result is the file ``telemac-mascaret/configs/pysource.<system>.sh`` in the installation folder. This file is the **environment script** of TELEMAC: it sets all variables that TELEMAC requires. The wizard enters it in the profile as the solver binding of TELEMAC (:ref:`solver-bindings`).

If the installation fails, the wizard shows the last lines of the installation log and the location of the complete log. After the cause is corrected, start the wizard again with the same folder. The installer continues with what is already downloaded.

A manual installation is described in the `TELEMAC installation tutorial <https://hydro-informatics.com/get-started/install-telemac-autoinstaller.html>`_. Enter the environment script of a manual installation in the profile editor.

.. _install-openfoam:

OpenFOAM
--------

`OpenFOAM <https://www.openfoam.com/>`_ resolves the flow in three dimensions including the water surface. aXqua requires **OpenFOAM v2406** as distributed by OpenCFD (``openfoam.com``). Other versions, in particular the releases of the OpenFOAM Foundation (``openfoam.org``), use different file formats and do not read the input files that aXqua writes.

.. important::

   Install TELEMAC also when OpenFOAM is the code of interest. aXqua starts every OpenFOAM simulation from the result of a short TELEMAC run, which is called the warm-up (:ref:`warm-up-concept`). Without it, OpenFOAM has to fill a dry riverbed with water on its own, which multiplies the computing time.

Click *Install OpenFOAM...* on the *Configuration* tab. The wizard is available on Debian 12, Ubuntu 22.04, Ubuntu 24.04 and Linux Mint 22, on computers with an x86-64 processor. It installs the following components into one folder:

* **OpenFOAM v2406**, unless an installation of this version exists already (see the settings below).
* The sediment transport solvers **sediDriftFoam** (`Olsen et al. 2023 <https://doi.org/10.2166/hydro.2023.309>`_) and **sediDriftFoam2** (`Olsen 2025 <https://doi.org/10.2166/hydro.2025.059>`_), and a variant of the latter with a stage-discharge outflow, ``sediDriftFoam2Rating``, which the installer marks as not validated.
* The **stage-discharge outflow boundary** of the Federal Waterways Engineering and Research Institute (BAW) for the solver ``interFoam`` (`Thorenz 2024 <https://doi.org/10.3929/ethz-b-000675949>`_).
* The postprocessors **ParaView** and **VisIt** (:ref:`help-postprocessors`).

.. list-table::
   :header-rows: 1
   :widths: 26 74

   * - Setting
     - Meaning
   * - Installation folder
     - A folder that does not exist yet. The installer installs into a new folder or continues an installation of its own, and refuses any other folder.
   * - OpenFOAM v2406
     - *Use the OpenFOAM v2406 that is installed on this computer* builds the solvers on an existing installation, which takes a few minutes. aXqua enters the installation that it found. Select the file ``etc/bashrc`` of another one with *Browse...*. *Compile OpenFOAM v2406 from its source code* is the choice for a computer without OpenFOAM. It takes several hours and requires about 20 GB of free space.
   * - Processes for compiling
     - The number of processor cores used for compiling. Each process requires about 2 GB of memory.
   * - Install ParaView and VisIt
     - Clear the box if the postprocessors are installed already.
   * - Download the example case of the sediment solver
     - Adds the example case that is published with sediDriftFoam.
   * - Run a short test at the end
     - Runs ``interFoam`` for a few time steps with the outflow boundary of the BAW, which verifies that the compiled library can be loaded.

A packaged OpenFOAM v2406 saves the hours of compilation. OpenCFD provides packages for Debian and Ubuntu. An administrator installs them according to the `installation instructions of OpenCFD <https://develop.openfoam.com/Development/openfoam/-/wikis/precompiled/debian>`_, which creates the file ``/usr/lib/openfoam/openfoam2406/etc/bashrc``. The wizard finds this installation by itself.

The result is the file ``shell-rc.sh`` in the installation folder. It is the **environment script** of this installation: it loads OpenFOAM v2406 and adds the compiled solvers and the boundary library. The wizard enters it in the profile as the solver binding of OpenFOAM, and enters ParaView and VisIt as postprocessors. No existing OpenFOAM installation and no startup file of the user account is changed.

.. note::

   aXqua does not yet set up simulations with the sediment transport solvers or with the stage-discharge outflow boundary (:ref:`help-morphodynamics-openfoam`). Both are installed and can be used from a terminal.
