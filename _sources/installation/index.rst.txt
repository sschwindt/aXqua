Installation & Configuration
============================

A working aXqua installation consists of four parts, which are installed in this order:

#. **QGIS and the aXqua plugin**, which is the user interface (:doc:`qgis-plugin`).
#. **Simulation software**, which is TELEMAC and optionally OpenFOAM. aXqua writes the input files for these codes and starts them, but does not contain them (:doc:`simulation-software`).
#. **The plugin setup**, which tells aXqua where the simulation software is installed on the computer in use (:doc:`plugin-setup`).
#. **Postprocessors**, which are VisIt and ParaView for three-dimensional figures. They are optional (:doc:`postprocessors`).

.. toctree::
   :maxdepth: 2

   qgis-plugin
   simulation-software
   plugin-setup
   postprocessors
