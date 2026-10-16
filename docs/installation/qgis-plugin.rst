QGIS plugin
===========

The QGIS plugin represents the user interface (**frontend**) and it requires the **aXqua program** (**backend**) that runs tasks as separate processes next to QGIS. So both frontend and backend are required:

* The plugin frontend alone has nothing to pass work to and requires the backend.
* Install in the order below.
* The plugin only looks for the program when a task is started, so it may be installed second.


Install QGIS
------------

* Download from `qgis.org <https://qgis.org/download/>`_. **QGIS 3.44 or newer** is required, QGIS 4 included.
* Follow the installer instructions.

Install the aXqua plugin (QGIS)
-------------------------------

Open **QGIS**, then:

* Go to *Plugins > Manage and Install Plugins*.
* In the *Settings* tab, tick *Show also experimental plugins*.
* Search for **aXqua**, click *Install Plugin*.

Until the plugin is in the QGIS plugin repository, take the ``.zip`` from the `releases page <https://github.com/sschwindt/aXqua/releases>`_ and use *Install from ZIP*.

Install the aXqua program (backend)
-----------------------------------

Requires Python 3.10 or newer and a geospatial stack (``numpy``, ``pandas``, ``scipy``, ``pyyaml``, ``pyproj``, ``shapely``, ``geopandas``, ``rasterio``, ``gmsh``, ``openpyxl``, ``matplotlib``). The best route is a separate environment from `Miniforge <https://github.com/conda-forge/miniforge>`_, which can be installed as follows:

* Open **terminal** and enter:

.. code-block:: bash

   git clone https://github.com/sschwindt/aXqua.git
   cd aXqua
   mamba env create -f environment.yml
   mamba activate axqua-env
   pip install .
   axqua --version

``axqua --version`` printing a version confirms the installation.

* **Bayesian calibration** needs one more package: ``pip install ".[calibration]"``.

.. note::

   If pip finds no matching ``hydrobayescal``, it is not on PyPI yet. Install it from source, without a version tag, so that the newest is taken:

  .. code-block:: bash

     pip install "git+https://github.com/Ecohydraulics/hydrobayescal.git"


Upgrade the backend
-------------------

To **upgrade** ``axqua`` and ``hydrobayescal`` to the newest versions from the ``aXqua`` folder of the clone:

  .. code-block:: bash

     mamba activate axqua-env
     git pull
     pip install --upgrade .
     pip install --upgrade --force-reinstall --no-deps "git+https://github.com/Ecohydraulics/hydrobayescal.git"
     axqua --version

  ``--force-reinstall`` is needed because a newer HydroBayesCal on GitHub may have the same version number as the installed one, which pip would otherwise keep. ``--no-deps`` leaves the packages of the conda environment untouched. *Check* on the *Configuration* tab of the plugin then reports the new version.

.. tip::

   aXqua can also go into the Python that ships with QGIS, if the packages above install there. The separate environment is the tested route and keeps QGIS and aXqua updatable independently.



Connect the plugin to the aXqua program
---------------------------------------

Back in **QGIS**:

* Open the aXqua panel from the toolbar and click *Check* on the *Configuration* tab.
* The plugin looks in three places, in this order: the path in *aXqua > Settings*, the variable ``AXQUA_EXE``, the system search path.
* **Found**: the panel reports the version and where it came from. Nothing else to do.
* **Not found**: enter the path from :ref:`find-the-axqua-path` in *Settings*, then click *Test*.

*Test* reports the version that the path answers with, so a wrong path is caught here and not when a simulation is started.

.. figure:: ../img/gui-settings.png
   :alt: The settings of the plugin with the path of the aXqua program

   The dialog *aXqua > Settings* with a correctly entered path. (1) The full path of the program, ending in ``envs/axqua-env/bin/axqua``; the button *...* opens a file dialog instead. (2) *Test* reports the version that this path answers with.

.. _find-the-axqua-path:

Find the path of the installed program
--------------------------------------

If the plugin does not automatically identify the full path of the ``axqua`` program backend find it via **terminal**; activate the environment and ask the system where the program is:

.. code-block:: bash

   mamba activate axqua-env
   which axqua          # Linux, macOS
   where axqua          # Windows

What it prints is the path to enter in the plugin:

=========  ====================================================================
Linux      ``/home/<user>/miniforge3/envs/axqua-env/bin/axqua``
macOS      ``/Users/<user>/miniforge3/envs/axqua-env/bin/axqua``
Windows    ``C:\Users\<user>\miniforge3\envs\axqua-env\Scripts\axqua.exe``
=========  ====================================================================

.. warning::

   * If the above commands to print anything, the environment is not active, or ``pip install .`` did not run in it.
   * To find an environment without activating it, ``mamba env list`` prints every environment with its folder. The program is then ``<folder>/bin/axqua`` (Linux, macOS) or ``<folder>\Scripts\axqua.exe`` (Windows).

.. important::

   This section only explains the very basic installation of the aXqua QGIS plugin (aka the **frontend**) and the required code behind the scenes (aka the **backend**). The next sections explain the installation of :doc:`installation/simulation-software` (TELEMAC and OpenFOAM), :doc:`installation/plugin-setup`, and the installation of :doc:`installation/postprocessors` (ParaView and Visit DAV).

aXqua panel layout
------------------

After successful configuration, the panel shows tabs that follow the workflow: *Configuration*, *Case Setup*, *Preprocessing*, *Hydraulic simulation*, *Mesh convergence*, *Morphodynamic simulation*, *Calibration & validation*, *Postprocessing*, *Batch-processing*. *Help* at the top right opens this documentation at the section of the tab that is showing. The job list below the tabs is visible from every tab.

.. figure:: ../img/gui-qgis-window.png
   :alt: The aXqua panel in the QGIS window
   :width: 100%

   The aXqua panel in QGIS. (1) The panel with its tabs, the button *Help* and the job list. (2) The map of QGIS, here with the result of a steady simulation in the map style *Water depth with streamlines* (:ref:`help-postprocessing-qgis`). (3) Every function of aXqua is a button on one of the tabs.
