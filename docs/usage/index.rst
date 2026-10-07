Usage
=====

This section follows the order in which a model is built and used. Each subsection corresponds to one working step:

#. :doc:`case-setup` describes the river reach and the modeling task in a case file.
#. :doc:`preprocessing` converts the case into a computational mesh and the input files of the simulation codes.
#. :doc:`hydraulic-simulations` computes water depths and flow velocities, first with TELEMAC and optionally with OpenFOAM.
#. :doc:`mesh-convergence` determines the mesh resolution that the results require.
#. :doc:`morphodynamic-simulations` adds sediment transport and riverbed change.
#. :doc:`calibration-validation` adjusts uncertain parameters to field measurements and quantifies the remaining uncertainty.
#. :doc:`postprocessing` turns the results into maps and figures.
#. :doc:`batch-processing` runs several of these steps in sequence without supervision.

.. toctree::
   :maxdepth: 2

   case-setup
   preprocessing
   hydraulic-simulations
   mesh-convergence
   morphodynamic-simulations
   calibration-validation
   postprocessing
   batch-processing
