aXqua
=====

.. admonition:: About
   :class: note

   aXqua is designed as a QGIS plugin to support numerical simulations of surface waters with the open-source codes `TELEMAC <http://www.opentelemac.org/>`_ and `OpenFOAM <https://www.openfoam.com/>`_. This vibe-coded plugin is hosted on `GitHub <https://github.com/sschwindt/aXqua/>`_ and maintained by `Sebastian Schwindt <https://sebastian-schwindt.org/>`_ with the help of `Claude Code <https://claude.com/product/claude-code>`_. Installing the QGIS plugin requires a backend installation of the aXqua code, so please make sure to follow **each step** in the :doc:`installation/qgis-plugin` and :doc:`installation/plugin-setup` sections, including instructions to get ready with TELEMAC and OpenFOAM, as well as `ParaView <https://www.paraview.org/>`_, and `Visit DAV <https://visit-dav.github.io/visit-website/index.html>`_ for post-processing. This documentation is published at `axqua.eu <https://axqua.eu>`_.

.. _purpose:

Purpose
-------

Building a numerical model by hand involves many steps in which input files can silently disagree, for instance, a boundary numbering that does not match the mesh or a roughness zone that does not match the terrain. aXqua derives all input files from one case description, so that the same inputs always produce the same model. Specifically, aXqua sets up, runs and evaluates standardized numerical simulations of georeferenced free-surface water bodies including hydraulic structures with the open-source codes `TELEMAC <http://www.opentelemac.org/>`_ and `OpenFOAM <https://www.openfoam.com/>`_. The software treats each case as a **reach** (computational domain) that is described once, by a digital elevation model, boundaries, wall roughness, and field measurements. Both TELEMAC and OpenFOAM read that one description, so that a depth-averaged two-dimensional (2D) model and a three-dimensional (3D) model of the same reach rely on identical input data. The workflow covers the following steps that also decide whether a simulation result can be trusted via mesh convergence studies and uncertainty quantification:

#. **Preprocessing** converts geodata into a computational mesh, boundary conditions, and input files (:doc:`usage/preprocessing`).
#. **Hydraulic simulations** begin with a TELEMAC run on a dry riverbed. The result serves as the starting point for 3D simulations with TELEMAC-3D or OpenFOAM (:doc:`usage/hydraulic-simulations`).
#. **Mesh convergence studies** quantify how much a result depends on the mesh resolution, with the grid convergence index of Celik et al. (2008) (:doc:`usage/mesh-convergence`).
#. **Morphodynamic simulations** add sediment transport and bed elevation change (:doc:`usage/morphodynamic-simulations`).
#. **Hydrology** computes the runoff of rain over a catchment or a field with TELEMAC, with Green-Ampt infiltration, wind and evaporation (:doc:`usage/hydrology-simulations`).
#. **Calibration and validation** adjust uncertain model parameters to field measurements with a surrogate-assisted Bayesian method (Oladyshkin et al. 2020) and report the remaining uncertainty (:doc:`usage/calibration-validation`) based on the `hydroBayesCal <https://hydrobayescal.readthedocs.io>`_ project.
#. **Postprocessing** turns results into maps and figures (:doc:`usage/postprocessing`) via QGIS, `ParaView <https://www.paraview.org/>`_, and `Visit DAV <https://visit-dav.github.io/visit-website/index.html>`_.

aXqua is operated from a QGIS plugin, because the geodata of a reach is usually open in GIS software already. **Simulations** run as independent jobs outside of QGIS and **continue when QGIS is closed**. Every function is also available from a terminal without QGIS (:doc:`automation/index`).


References
~~~~~~~~~~

Celik, I. B., Ghia, U., Roache, P. J., Freitas, C. J., Coleman, H., and Raad, P. E. (2008). "Procedure for estimation and reporting of uncertainty due to discretization in CFD applications." *ASME Journal of Fluids Engineering*, 130(7), 078001.

Oladyshkin, S., Mohammadi, F., Kroeker, I., and Nowak, W. (2020). "Bayesian3 active learning for the Gaussian process emulator using information theory." *Entropy*, 22(8), 890. `doi:10.3390/e22080890 <https://doi.org/10.3390/e22080890>`_

.. toctree::
   :maxdepth: 3
   :hidden:

   Purpose <self>
   installation/index
   usage/index
   automation/index
   code/index
   troubleshooting/index
   license
