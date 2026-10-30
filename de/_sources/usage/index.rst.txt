Usage
=====

.. admonition:: Some tutorials are explained on YouTube
   :class: tip dropdown

   .. raw:: html

      <div class="video-embed">
        <iframe
          src="https://www.youtube.com/embed/gK83Q8bJCHI"
          title="TELEMAC 2D simulations with aXqua: dry start"
          loading="lazy"
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
          referrerpolicy="strict-origin-when-cross-origin"
          allowfullscreen>
        </iframe>
      </div>

   `Open the tutorial on YouTube <https://youtu.be/2qWOA7l5xeg>`_.

This section follows the order in which a model is built and used. Each subsection corresponds to one working step:

#. :doc:`case-setup` describes the free-surface water body and the modeling task in a case file.
#. :doc:`preprocessing` converts the case into a computational mesh and the input files of the simulation codes.
#. :doc:`hydraulic-simulations` computes water depths and flow velocities, first with TELEMAC and optionally with OpenFOAM.
#. :doc:`mesh-convergence` determines the mesh resolution that the results require.
#. :doc:`morphodynamic-simulations` adds sediment transport and riverbed change.
#. :doc:`hydrology-simulations` computes the runoff of rain over a catchment or a field, with infiltration, wind and evaporation. It can be read on its own and takes up what a hydrology case needs from the preceding subsections.
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
   hydrology-simulations
   calibration-validation
   postprocessing
   batch-processing
