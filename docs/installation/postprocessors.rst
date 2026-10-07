.. _help-postprocessors:

Postprocessors
==============

QGIS displays depth-averaged results directly and needs no additional software. Three-dimensional results are best viewed with a dedicated visualization program. aXqua works with two of them, and both are optional.

.. _install-visit:

VisIt
-----

`VisIt <https://visit-dav.github.io/visit-website/>`_ is the program with which aXqua renders its predefined three-dimensional figures (:ref:`help-postprocessing-visit`). aXqua is tested with VisIt 3.5.0. Download the archive for the operating system in use from the VisIt website and unpack it.

aXqua needs the location of the VisIt **launcher**, which is the file ``bin/visit`` in the installation folder. If the command ``visit`` can be started from a terminal, no setting is required. Otherwise, name the launcher in the case file:

.. code-block:: yaml

   postproc:
     visit: /home/user/opt/visit/bin/visit

.. _install-paraview:

ParaView
--------

`ParaView <https://www.paraview.org/download/>`_ is suited for the interactive inspection of three-dimensional results. Download the installer for the operating system in use from the ParaView website. aXqua requires no setting for ParaView.

ParaView opens an OpenFOAM case directly. It does not read the result format of TELEMAC (SELAFIN). TELEMAC results are therefore viewed in QGIS, until the export to a ParaView format becomes available (:ref:`help-postprocessing-paraview`).
