.. _help-postprocessors:

Postprocessors
==============

QGIS displays depth-averaged results directly and needs no additional software. Three-dimensional results are best viewed with a dedicated visualization program. aXqua works with two of them, and both are optional.

Both programs are installed by a wizard. On a computer that runs OpenFOAM, the OpenFOAM wizard installs them together with the solvers (:ref:`install-openfoam`). On a computer that runs TELEMAC only, click *Install ParaView and VisIt...* in the box *Simulation software* of the *Configuration* tab. The wizard has two settings, the installation folder of VisIt and the selection of the programs, and works as described for the simulation software (:doc:`simulation-software`). It is available on Debian 12, Ubuntu 22.04, Ubuntu 24.04 and Linux Mint 22. The same installation is started in a terminal with ``axqua install start postprocessors``.

**Selection of the programs.** *ParaView and VisIt* is the default. *ParaView only* leaves VisIt out: nothing is downloaded, and the wizard enters the ParaView of the operating system in the profile. The OpenFOAM wizard offers the same selection and, in addition, *None*. In a terminal, the option ``--no-visit`` leaves VisIt out. aXqua recommends installing VisIt, because it renders its predefined three-dimensional figures with VisIt (:ref:`help-postprocessing-visit`). Without VisIt, these figures are not available, and three-dimensional results are inspected interactively in ParaView. VisIt can be installed at any time afterwards with the same wizard.

.. _install-visit:

VisIt
-----

`VisIt <https://visit-dav.github.io/visit-website/>`_ is the program with which aXqua renders its predefined three-dimensional figures (:ref:`help-postprocessing-visit`). aXqua is tested with VisIt 3.5.0.

The wizard downloads VisIt 3.5.0 for the operating system in use from the VisIt project (600 MB), verifies the download against its published checksum, and installs it into the selected folder. This requires no administrator rights. The wizard then enters the VisIt **launcher**, which is the file ``bin/visit`` of the installation, in the profile of the computer (:ref:`plugin-profile`).

For a VisIt that is installed already, select its installation folder in the profile editor. aXqua then enters the launcher. The same is achieved by one entry in the profile file:

.. code-block:: yaml

   installations:
     visit: /home/user/opt/visit

``axqua profile init`` enters the launcher automatically if the command ``visit`` can be started from a terminal or if VisIt is installed in ``/opt``.

.. _install-paraview:

ParaView
--------

`ParaView <https://www.paraview.org/download/>`_ is suited for the interactive inspection of three-dimensional results.

The wizards use the ParaView package of the operating system, which is one of the system packages listed on the *Check* tab. Installing it requires administrator rights (:doc:`simulation-software`). Once the package is installed, the wizard enters the program ``/usr/bin/paraview`` in the profile under ``postprocessors.paraview``. A newer ParaView from the ParaView website can be used instead: unpack it and select the unpacked folder as the installation folder of ParaView in the profile editor.

ParaView opens an OpenFOAM case directly. It does not read the result format of TELEMAC (SELAFIN), and neither does VisIt. aXqua therefore exports TELEMAC results into a format that both programs read (:ref:`help-postprocessing-paraview`).

TELEMAC itself is distributed with the source code of a ParaView plugin that reads SELAFIN files (``optionals/addons/Paraview_Plugins/SerafinReader`` in the TELEMAC folder). This plugin has to be compiled for the version of ParaView in use and does not exist for VisIt. The export of aXqua requires neither.
