QGIS plugin
===========

The plugin is the graphical user interface of aXqua. It does not compute anything itself. It passes every task to the aXqua program, which runs as a separate process next to QGIS. Two installations are therefore required: the plugin in QGIS and the aXqua program in a Python environment.

Install QGIS
------------

Download QGIS from `qgis.org <https://qgis.org/download/>`_ and install it for the operating system in use. The plugin requires **QGIS 3.44 or newer**, including QGIS 4.

Install the aXqua program
-------------------------

aXqua requires Python 3.10 or newer and a set of geospatial packages (``numpy``, ``pandas``, ``scipy``, ``pyyaml``, ``pyproj``, ``shapely``, ``geopandas``, ``rasterio``, ``gmsh``, ``openpyxl`` and ``matplotlib``). The tested way to obtain them is a separate environment created with `Miniforge <https://github.com/conda-forge/miniforge>`_ from the ``environment.yml`` file of the repository:

.. code-block:: bash

   git clone https://github.com/sschwindt/aXqua.git
   cd aXqua
   mamba env create -f environment.yml
   mamba activate axqua-env
   pip install .
   axqua --version

The last command prints the installed version and confirms that the program can be started. The Bayesian calibration requires one additional package, which is installed with ``pip install ".[calibration]"``.

aXqua can also be installed into the Python that ships with QGIS, provided that the packages listed above can be installed there. The separate environment is the tested route, and it keeps QGIS updates and aXqua updates independent of each other.

Install the plugin
------------------

In QGIS, open *Plugins > Manage and Install Plugins*. In the *Settings* tab, tick *Show also experimental plugins*. Search for **aXqua** and click *Install Plugin*.

Until the plugin is listed in the QGIS plugin repository, download the plugin archive (``.zip``) from the `releases page <https://github.com/sschwindt/aXqua/releases>`_ and install it with *Plugins > Manage and Install Plugins > Install from ZIP*.

Connect the plugin to the aXqua program
---------------------------------------

Open the aXqua panel from the toolbar and click *Check* on the *Setup* tab. The plugin searches for the ``axqua`` program in three places, in this order: the path entered in *aXqua > Settings*, the environment variable ``AXQUA_EXE``, and the system search path. If the program is not found, enter its full path in *Settings* and click *Test*. With the environment from above, the path ends with ``envs/axqua-env/bin/axqua`` (Linux) or ``envs\axqua-env\Scripts\axqua.exe`` (Windows).

The test reports the version that was found. A wrong path is therefore detected at this point and not later, when a simulation is started.
