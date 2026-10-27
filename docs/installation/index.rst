Installation & Configuration
============================

A working aXqua installation consists of four parts, which are installed in this order:

#. **QGIS and the aXqua plugin**, which is the user interface (:doc:`qgis-plugin`).
#. **Simulation software**, which is TELEMAC and optionally OpenFOAM. aXqua writes the input files for these codes and starts them, but does not contain them (:doc:`simulation-software`).
#. **The plugin setup**, which tells aXqua where the simulation software is installed on the computer in use (:doc:`plugin-setup`).
#. **Postprocessors**, which are VisIt and ParaView for three-dimensional figures. They are optional (:doc:`postprocessors`).

.. admonition:: Watch this Tutorial on YouTube
   :class: tip dropdown

   .. raw:: html

      <iframe
        src="https://www.youtube.com/embed/gK83Q8bJCHI"
        title="Installing aXqua: QGIS Plugin and Python Backend"
        width="560" height="315"
        style="width: 100%; height: auto; aspect-ratio: 16 / 9; border: 0;"
        loading="lazy"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
        referrerpolicy="strict-origin-when-cross-origin"
        allowfullscreen>
      </iframe>

   `Open the tutorial on YouTube <https://youtu.be/gK83Q8bJCHI>`_.

.. toctree::
   :maxdepth: 2

   qgis-plugin
   simulation-software
   plugin-setup
   postprocessors
