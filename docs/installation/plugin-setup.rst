.. _help-configuration:

Plugin setup
============

The plugin setup contains everything that describes the **computer** and not the river: where Python and the simulation software are installed, and where simulations are written. These settings are made once per computer. They are kept apart from the description of a river reach (:doc:`../usage/case-setup`), so that a case can be copied to another computer without changes.

.. _plugin-profile:

Plugin profile
--------------

.. note::

   A single profile file (``.axq-profile``) that holds all computer-specific settings is not yet available in this version. Until then, the settings are made in the three places described on this page.

The plugin itself stores three values, which are set in *aXqua > Settings*:

* the path of the ``axqua`` program (:doc:`qgis-plugin`);
* the **minimum water depth** for maps (default 0.01 m). Shallower water is drawn transparent, because a water film of a few millimeters stands between the roughness elements of the bed and does not flow;
* the **upper limit of the velocity color scale** (default 5 m/s). Higher depth-averaged velocities typically occur in almost dry cells at the edge of the water and would otherwise flatten the color scale of the entire map.

.. _solver-bindings:

Define solver bindings
----------------------

A solver binding tells aXqua which environment script belongs to which code. The bindings are stored in the file ``solvers.yml`` in the aXqua configuration folder of the user (``~/.config/axqua/`` on Linux):

.. code-block:: yaml

   telemac: /home/user/opt/telemac-mascaret/configs/pysource.debian12.sh
   openfoam: /usr/lib/openfoam/openfoam2406/etc/bashrc

aXqua looks for the environment script of a code in the following order and uses the first one that it finds:

#. the environment variable ``AXQUA_TELEMAC_PYSOURCE`` or ``AXQUA_OPENFOAM_BASHRC``;
#. a file ``solvers.local.yml`` next to the case file, for a case that requires a different installation than the rest of the computer;
#. the file ``solvers.yml`` described above;
#. the entry ``telemac.pysource`` or ``openfoam.bashrc`` in the case file;
#. the usual installation folders ``/opt`` and ``/usr/local``.

To verify the bindings, run the following command for any case. It enters the environment of each code and reports whether the code can be reached:

.. code-block:: text

   axqua status <case-file> --check-env

Job execution
-------------

Every simulation runs as a **job**: an independent process with its own folder, which continues when QGIS is closed. Two settings on the *Setup* tab control how jobs are run:

* **Job root** is the folder in which the job folders are created. Select a drive with sufficient free space, because the results of a single simulation can reach tens of gigabytes.
* **Launcher** is the method that detaches a job from QGIS. The default ``auto`` selects a suitable method for the operating system and rarely needs to be changed.

The number of processor cores is taken from the case file (``telemac.n_processors`` and ``openfoam.n_processors``) and can be changed for a single run in the tab from which the run is started.

A computer with several installations of a code can describe each of them as a **solver profile** in the file ``profiles.yml`` of the aXqua configuration folder. A profile names the environment script, the number of processor cores and the job root. It is selected on the *Setup* tab. ``axqua profiles validate`` verifies all profiles. :doc:`../automation/batch-headless` describes jobs in detail.
