.. _help-postprocessing:

Postprocessing
==============

Postprocessing turns simulation results into maps, figures and tables. Depth-averaged results are best presented as maps in QGIS. Three-dimensional results require ParaView or VisIt (:doc:`../installation/postprocessors`).

.. _help-postprocessing-qgis:

QGIS map generation
-------------------

**Load results.** In the job list below the tabs, select a completed job and click *Load results*. The result layers are added to the QGIS project in a group that is named after the job. aXqua does not copy any files. The layers refer to the result files at their location in ``axqua-case/``.

TELEMAC results are loaded as a mesh layer with two predefined styles:

* **Water depth** is drawn from white over light blue to dark blue. Water that is shallower than the minimum water depth is transparent, so that the map shows the area in which water actually flows (:ref:`plugin-profile`).
* **Flow velocity** is drawn as arrows that are colored by the velocity magnitude. The color scale ends at the upper limit of the plugin settings, and aXqua reports when a result exceeds it.

All other properties of the layers are changed in the QGIS layer properties as usual. There, the display of the velocity can also be switched from arrows to **streamlines** (*Layer Properties > Symbology > Vectors*).

.. note::

   Predefined map styles that combine streamlines or vectors with the water depth are not yet available in this version.

**Print layout.** *aXqua > Add the default A3 print layout* creates a map layout for the current map extent. It contains the map, a north arrow, an arrow in the flow direction, a scale bar and a legend. Complete the legend and export the layout as PDF or image from the QGIS layout manager.

**Movies.** For a simulation with a varying discharge, *aXqua > Export movie* renders one image per output time step of a result variable and combines the images into a video file. The movie uses the current map extent and layer styles. The encoding requires the program ``ffmpeg``. If it is not installed, the images are kept, and aXqua shows the command with which they can be encoded later.

**Export.** The result layers are ordinary QGIS layers. They are exported to other formats with the export functions of QGIS, for example to a raster of water depths.

**Tables.** The evaluations of a run, such as the discharges at the boundaries and the wetted area, are stored as CSV files next to the results (:ref:`hydraulics-dry-runs`). With ``geodata.control_sections``, aXqua additionally computes the discharge through each cross section of that layer from the result. The cross sections can be moved in QGIS and evaluated again without repeating the simulation.

.. _help-postprocessing-paraview:

ParaView
--------

An OpenFOAM model of aXqua can be opened in ParaView without conversion. In ParaView, select *File > Open* and choose the file ``case.foam`` in the folder ``axqua-case/openfoam/``. The mesh regions and the result fields can then be selected in the properties panel. To display the water only, apply the filter *Threshold* or *Clip* to the field ``alpha.water``, which is 1 in water and 0 in air.

.. note::

   ParaView does not read the result format of TELEMAC. The export of TELEMAC results to a ParaView format from the plugin is not yet available in this version.

.. _help-postprocessing-visit:

VisIt
-----

aXqua renders predefined figures of three-dimensional results with VisIt. No interaction with VisIt is required: aXqua writes a script for each figure and lets VisIt execute it without opening a window.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Figure
     - Content
   * - ``free-surface``
     - The water surface, colored by the flow velocity. For a model in the mode ``rigid-lid``, the figure states that the water surface was prescribed and not computed.
   * - ``velocity-plan``
     - The flow velocity in a horizontal section at the relative depth at which point velocity measurements in rivers are usually taken (0.6 of the water depth).
   * - ``profiles``
     - Vertical velocity profiles at the measurement points, together with the measured values and their error bars.

In a terminal, the commands are:

.. code-block:: text

   axqua postproc <case-file> --list      # available figures, and the reason if one is not available
   axqua postproc <case-file>             # render all figures
   axqua postproc <case-file> --script-only   # write the VisIt scripts without rendering

The figures are written to ``axqua-case/postprocessing/figures/``. The scripts are kept in ``axqua-case/postprocessing/visit/``, so that every figure can be reproduced and adapted. The list of figures and the image size are set in the ``postproc`` block of the case file.

.. note::

   Starting these figures from the plugin, and the export of TELEMAC results to a VisIt format, are not yet available in this version.
