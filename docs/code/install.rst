Installation of the simulation software
=======================================

The package ``axqua.install`` installs TELEMAC, OpenFOAM, ParaView and VisIt by running the published installer scripts. It detects the operating system, composes a plan that can be read before anything runs, executes the plan as a detached process, and enters the result in the profile of the computer. The command ``axqua install`` and the installation wizards of the QGIS plugin are two front ends of this package.

Operating system
----------------

.. automodule:: axqua.install.host
   :members:

Installation plans
------------------

.. automodule:: axqua.install.recipes
   :members:

Example cases of TELEMAC
------------------------

.. automodule:: axqua.install.telemac_examples
   :members:

Running an installation
-----------------------

.. automodule:: axqua.install.runner
   :members:
