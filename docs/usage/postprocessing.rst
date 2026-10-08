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

ParaView is suited for the interactive inspection of results in three dimensions. It opens an OpenFOAM model directly and a TELEMAC result after an export.

.. _export-telemac-results:

**Export of TELEMAC results.** ParaView and VisIt do not read the result format of TELEMAC (SELAFIN). aXqua therefore converts a result once into the VTK format, which both programs read. On the tab *Postprocessing > ParaView*, the table lists the TELEMAC results of the selected case with their number of time steps. Select one or several results, choose between all time steps and the last time step only, and click *Export*. Without a selection, all results are exported. The conversion of one time step takes about one second for a mesh of 230,000 nodes. The same export serves ParaView and VisIt, and is repeated after a simulation was run again: the column *Exported* states whether the exported files are older than the result.

The exported files are written to the folder ``axqua-case/postprocessing/vtk/``. Each result has a file ``<result>.pvd`` for ParaView, a file ``<result>.visit`` for VisIt, and a folder with one file per time step. In a terminal, the export is:

.. code-block:: text

   axqua export <case-file>                              # all results of the case
   axqua export <case-file> --result r2d.slf --frames last
   axqua export <case-file> --list                       # what the case has, and what is exported

What the exported files contain:

* **A 2D result** is a surface of triangles in the horizontal plane, at elevation zero. The fields carry their TELEMAC names, for example ``WATER DEPTH``, ``FREE SURFACE``, ``BOTTOM`` and ``SCALAR VELOCITY``. The velocity is additionally available as the vector ``VELOCITY``, which arrows and streamlines require.
* **A 3D result** is a volume of prisms between the bed and the water surface, with the node elevations of each time step. The vector ``VELOCITY`` has three components.
* **The time** of each time step is the simulated time in seconds.

The node coordinates are taken from the geometry file of the model and not from the result file. TELEMAC stores a result in single precision, including its coordinates, and a coordinate of several million meters has a resolution of 0.5 m in that precision. The computation is not affected, because TELEMAC computes with the exact geometry. The mesh that is stored in a result file, however, is distorted: in a model with channel cells of 0.5 m, 14 % of the cells of the stored mesh had no area left. aXqua places the fields on the exact nodes.

**Open a TELEMAC result.** Click *Open in ParaView*, or start ParaView and open the file ``<result>.pvd``. Click *Apply*. The following steps are typical:

#. Select the field to display in the toolbar, for example ``WATER DEPTH``. The time step is selected with the animation controls.
#. To display the water only, apply the filter *Threshold* to ``WATER DEPTH`` with a lower limit of 0.01 m. This is the minimum water depth that aXqua uses in its own evaluations (:ref:`hydraulics-dry-runs`).
#. To display the relief of a 2D result, apply the filter *Warp By Scalar* with ``BOTTOM`` for the terrain or with ``FREE SURFACE`` for the water surface. The mesh is flat by default for a reason: an area or a volume that the filter *Integrate Variables* computes over the flat mesh is the area or the volume of the model, whereas the same integral over a mesh on the sloping bed is larger.
#. For streamlines, apply the filter *Stream Tracer* with the vector ``VELOCITY`` and a line across the channel as seed.

**Open an OpenFOAM model.** An OpenFOAM model of aXqua needs no export. In ParaView, select *File > Open* and choose the file ``case.foam`` in the folder ``axqua-case/openfoam/``. The mesh regions and the result fields can then be selected in the properties panel. To display the water only, apply the filter *Threshold* or *Clip* to the field ``alpha.water``, which is 1 in water and 0 in air.

.. _help-postprocessing-visit:

VisIt
-----

VisIt serves two purposes: the interactive inspection of results, as with ParaView, and the predefined figures that aXqua renders without interaction.

**Open a TELEMAC result.** Export the result as described for ParaView (:ref:`export of TELEMAC results <export-telemac-results>`). The tab *Postprocessing > VisIt* has the same table and the same *Export* button, and one export serves both programs. Click *Open in VisIt*, or start VisIt and open the file ``<result>.visit``. Add a plot with *Add > Pseudocolor* and select a field, for example ``WATER DEPTH``, then click *Draw*. The time slider moves through the time steps. The operator *Elevate* displays the relief of a 2D result, and the operator *Threshold* restricts the plot to the water.

**Open an OpenFOAM model.** VisIt opens an OpenFOAM model through the file ``system/controlDict`` in the folder ``axqua-case/openfoam/``. Unlike ParaView, it does not accept the file ``case.foam``.

**Predefined figures.** aXqua renders predefined figures of three-dimensional OpenFOAM results with VisIt. No interaction with VisIt is required: aXqua writes a script for each figure and lets VisIt execute it without opening a window.

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

   Starting the predefined figures from the plugin is not yet available in this version. Use the commands above.
