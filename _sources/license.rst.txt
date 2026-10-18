License and disclaimer
======================

License
-------

The aXqua library is distributed under the **BSD 3-Clause License**. The QGIS plugin is distributed under the GNU General Public License, version 2 or later, because it uses the programming interface of QGIS. The full text of the BSD 3-Clause License:

.. literalinclude:: ../LICENSE
   :language: text

The input data of the example case (:ref:`Example case <help-example-case>`) are distributed under the Creative Commons Attribution 4.0 International License. They may be reused and adapted for any purpose, provided that aXqua is cited, and they are provided without warranty and without liability. The file ``LICENSE.md`` in the folder of the example states the terms and the citation.

Disclaimer
----------

``axqua`` automates the *setup* of numerical hydro- and morphodynamic models; it does not validate the physical correctness of any model it produces. The software is provided "as is", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose and non-infringement (see the license text above).

In particular, users remain responsible for:

* the quality, accuracy and coordinate reference of all input data (DEMs, boundaries, discharge, measurements);
* the physical plausibility of mesh resolution, friction laws, boundary conditions and sediment parameters;
* verifying that a generated model converges and reproduces observations before drawing any conclusion from its results;
* interpreting calibration outcomes and their associated uncertainty.

Numerical model results can be sensitive to choices made during setup. Generated models must be reviewed by a qualified modeler and should not be used for design, operational or safety-critical decisions without independent verification. The authors and contributors accept no liability for any loss or damage arising from the use of this software or of the models it produces.

Third-party software
--------------------

``axqua`` orchestrates and depends on independent third-party software, including QGIS, TELEMAC, GAIA, OpenFOAM, gmsh, GDAL, HydroBayesCal, VisIt and ParaView, each distributed under its own license and terms. Installing and using those tools is subject to their respective licenses.
