General tips & logfiles
=======================

Tips
----

* **Start with a coarse mesh.** The default edge lengths produce a fine mesh with long computing times. Use larger edge lengths while the boundaries, the roughness and the initial state are still being verified, and refine afterwards (:ref:`case-mesh`).
* **Check before building.** ``axqua <case-file> --check`` verifies the case file, the input files and the simulation software within a second and detects most problems that would otherwise stop the preprocessing after several minutes.
* **Read the log of the preprocessing before starting a simulation.** The mesh quality, the assignment of the boundaries and the initial state are decided during preprocessing and are reported in its log file.
* **Test an OpenFOAM model with the rigid lid first.** A coarse run in the mode ``rigid-lid`` takes minutes and reveals errors in the mesh and the boundaries before a long run is started (:ref:`preprocessing-openfoam-choices`).
* **Repeat a failed job in the foreground.** ``axqua execute <job-id>`` runs the job in the terminal, so that all messages are visible.
* **Use the same minimum water depth in all evaluations.** The evaluations of aXqua regard water shallower than ``hydrodynamics.wet_depth`` (0.01 m) as dry. Apply the same limit in QGIS, ParaView and VisIt, so that maps and reported numbers agree.

Logfiles
--------

aXqua records every step in log files. Which file to read depends on how the step was started.

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - File
     - Content
   * - ``axqua.log``
     - The log of a workflow step. Each step writes its own file into its output folder, for example ``axqua-case/simulation/axqua.log`` for the preprocessing and the first run.
   * - ``<job-folder>/runner.log``
     - The log of a job: its states, the commands that were issued and, if the job failed, the error with its remedy.
   * - ``<job-folder>/solver/``
     - The output of the simulation code for a job.
   * - ``*.sortie``
     - The listing of TELEMAC, in the model folder. It contains the messages of TELEMAC itself and the discharges at the boundaries.
   * - ``checkMesh.log``
     - The mesh check of OpenFOAM, in the OpenFOAM case folder.

In the plugin, *View logs* below the job list opens the log of the selected job. The messages of the plugin itself are listed in the QGIS panel *Log Messages* on the tab *aXqua*.

A line of ``axqua.log`` consists of the time, the level of the message and the text. Search the file for the two levels that require attention:

``WARNING``
    The step continued, but the result may be affected. Each warning should be understood before the result is used (:doc:`warnings`).
``ERROR``
    The step could not be completed (:doc:`errors`).

The lines ``START`` and ``DONE ... in N s`` mark the beginning and the end of each part of a step. They show where a step failed and how long each part took.

If a simulation stops without an error message of aXqua, the cause is usually reported by the simulation code. Read the end of the ``.sortie`` listing (TELEMAC) or the end of the output of the simulation code in the folder ``solver/`` of the job (OpenFOAM).

.. _help-cleanup:

Freeing disk space
------------------

A case collects files that nothing needs any more, and results that are larger than they have to be. A steady TELEMAC run writes every time step into its result, of which aXqua reads only the last one; on a reach with a few hundred thousand nodes such a result takes about 1 GB, and every run of a calibration or of a mesh convergence study writes one. *Clean Up*, left of *Help* above the tabs of the plugin, lists for one case at a time what can go, by category and with the space it frees:

* working folders that TELEMAC leaves behind when a run was stopped or failed;
* listings of the single processors of a parallel run, and listings of earlier runs (the newest listing of each steering file is kept);
* **time steps of TELEMAC results**: the result is rewritten with the last time step only, or with every 5th or 10th time step and the last one. The kept time steps are copied unchanged. The last time step is the one that aXqua reads, from which a hotstart continues and which QGIS shows, so these keep working; an animation of the run loses the removed time steps;
* processor folders of OpenFOAM runs that were reconstructed, the time folders between the start and the latest time, and the results of an earlier run that OpenFOAM set aside in ``previous-run``;
* exports for ParaView and VisIt, which *Export* makes again.

The categories that need no second thought are ticked; the time steps are not. *Remove selected* asks before anything is removed, and removed files cannot be restored. The input files, the case file, the built model (geometry, boundary conditions, steering and friction files, initial conditions) and the last time step of a result are never listed, and neither are the processor folders of an OpenFOAM run that was not reconstructed, which hold the only copy of its results. Nothing is removed while a job of the case is waiting or running. In a terminal, ``axqua cleanup <case-file>`` lists the same items and ``--remove`` removes them (:doc:`../automation/terminal-syntax`).

Reporting a problem
-------------------

Report problems at https://github.com/sschwindt/aXqua/issues. Include the version of aXqua (``axqua --version``), the version of QGIS, the operating system, and the relevant log file.
