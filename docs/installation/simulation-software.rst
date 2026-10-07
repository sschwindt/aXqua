Simulation Software
===================

aXqua prepares and starts simulations, but the numerical codes are separate software that must be installed on the computer on which the simulations run. Install TELEMAC first. OpenFOAM is optional and builds on TELEMAC.

.. note::

   A guided installation of both codes from within the plugin is not yet available in this version. Follow the steps below instead.

.. _install-telemac:

TELEMAC
-------

`TELEMAC <http://www.opentelemac.org/>`_ solves the depth-averaged (2D) and the three-dimensional (3D) flow equations. Its sediment transport module GAIA is part of the same installation. aXqua is developed and tested with TELEMAC v9.1.1.

On Debian 12, Ubuntu 24 and Linux Mint 22, TELEMAC can be installed with a script that downloads and compiles the code. The complete procedure, including the list of required system packages, is described in the `TELEMAC installation tutorial <https://hydro-informatics.com/get-started/install-telemac-autoinstaller.html>`_. In short:

#. Ask the system administrator to install the system packages listed in the tutorial. This step requires administrator rights.
#. Choose an installation folder, for example ``$HOME/opt``. The tutorial calls this folder ROOT.
#. Download the three files for the operating system in use (the installer script, the ``systel`` configuration and the ``pysource`` script) from the `installer repository <https://github.com/Ecohydraulics/numerical-software-installers>`_ into ROOT.
#. Run the installer and wait until TELEMAC has been compiled. The downloads exceed 1.4 GB, and the compilation takes one to two hours.

   .. code-block:: bash

      cd $HOME/opt
      chmod +x telemac_debian12_installer.sh
      ./telemac_debian12_installer.sh --root "$HOME/opt"

The installation creates the file ``ROOT/telemac-mascaret/configs/pysource.<system>.sh``. This file is the **environment script** of TELEMAC: it sets all variables that TELEMAC requires. aXqua needs to know its location (:ref:`solver-bindings`).

The tutorial also describes how to add SALOME. aXqua does not require SALOME, because it generates the mesh and the input files itself.

On Windows, TELEMAC is provided either as a native installation with an environment script ``pysource.bat`` or within the Windows Subsystem for Linux (WSL). aXqua supports both, but these two routes have not yet been tested on a real Windows installation.

.. _install-openfoam:

OpenFOAM
--------

`OpenFOAM <https://www.openfoam.com/>`_ resolves the flow in three dimensions including the water surface. aXqua requires **OpenFOAM v2406** as distributed by OpenCFD (``openfoam.com``). Other versions, in particular the releases of the OpenFOAM Foundation (``openfoam.org``), use different file formats and do not read the input files that aXqua writes.

.. important::

   Install TELEMAC also when OpenFOAM is the code of interest. aXqua starts every OpenFOAM simulation from the result of a short TELEMAC run, which is called the warm-up (:ref:`warm-up-concept`). Without it, OpenFOAM has to fill a dry riverbed with water on its own, which multiplies the computing time.

OpenCFD provides installation packages for Debian and Ubuntu. Follow the `installation instructions of OpenCFD <https://develop.openfoam.com/Development/openfoam/-/wikis/precompiled/debian>`_ and select version 2406. The installation creates the environment script ``/usr/lib/openfoam/openfoam2406/etc/bashrc``, whose location aXqua needs to know (:ref:`solver-bindings`).

On Windows, OpenFOAM v2406 runs within the Windows Subsystem for Linux. Install a Linux distribution in WSL first and then follow the Debian or Ubuntu instructions inside it.
