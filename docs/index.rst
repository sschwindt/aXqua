aXqua
=====

.. _purpose:

Purpose
-------

aXqua sets up, runs and evaluates standardized numerical simulations of georeferenced river reaches with the open-source codes `TELEMAC <http://www.opentelemac.org/>`_ and `OpenFOAM <https://www.openfoam.com/>`_. A reach is described once, by its terrain, its boundaries, its roughness and its field measurements. Both codes read that one description, so that a depth-averaged two-dimensional (2D) model and a three-dimensional (3D) model of the same reach rest on identical input data.

The workflow covers the steps that decide whether a simulation result can be trusted:

#. **Preprocessing** converts geodata into a computational mesh, boundary conditions and the input files of the selected code (:doc:`usage/preprocessing`).
#. **Hydraulic simulations** begin with a TELEMAC run on a dry riverbed. The result serves as the starting point for 3D simulations with TELEMAC-3D or OpenFOAM (:doc:`usage/hydraulic-simulations`).
#. **Mesh convergence studies** quantify how much a result depends on the mesh resolution, with the grid convergence index of Celik et al. (2008) (:doc:`usage/mesh-convergence`).
#. **Morphodynamic simulations** add sediment transport and riverbed change (:doc:`usage/morphodynamic-simulations`).
#. **Calibration and validation** adjust uncertain model parameters to field measurements with a surrogate-assisted Bayesian method (Oladyshkin et al. 2020) and report the remaining uncertainty (:doc:`usage/calibration-validation`).
#. **Postprocessing** turns results into maps and figures (:doc:`usage/postprocessing`).

aXqua is operated from a QGIS plugin, because the geodata of a reach is usually open in QGIS already. Simulations run as independent jobs outside of QGIS and continue when QGIS is closed. Every function is also available from a terminal without QGIS (:doc:`automation/index`).

Building a river model by hand involves many steps in which input files can silently disagree, for example a boundary numbering that does not match the mesh or a roughness zone that does not match the terrain. aXqua derives all input files from one case description, so that the same inputs always produce the same model.

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
