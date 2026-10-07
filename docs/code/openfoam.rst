OpenFOAM backend
================

``axqua.solvers.openfoam`` builds, runs and evaluates OpenFOAM models. aXqua writes the mesh files itself, because a riverbed is a surface with one elevation per location that a mesh of cell columns can follow directly.

Mesh
----

.. automodule:: axqua.solvers.openfoam.polymesh
   :members:

.. automodule:: axqua.solvers.openfoam.mesh
   :members:

.. automodule:: axqua.solvers.openfoam.quality
   :members:

Initial state from a TELEMAC result
-----------------------------------

.. automodule:: axqua.solvers.openfoam.hotstart
   :members:

.. automodule:: axqua.solvers.openfoam.fields
   :members:

Dictionaries and case assembly
------------------------------

.. automodule:: axqua.solvers.openfoam.dicts
   :members:

.. automodule:: axqua.solvers.openfoam.case
   :members:

Runs and evaluation
-------------------

.. automodule:: axqua.solvers.openfoam.runtime
   :members:

.. automodule:: axqua.solvers.openfoam.report
   :members:

Calibration
-----------

.. automodule:: axqua.solvers.openfoam.calibration
   :members:

Single-phase free surface
-------------------------

.. automodule:: axqua.solvers.openfoam.potential
   :members:

Backend adapter
---------------

.. automodule:: axqua.solvers.openfoam.backend
   :members:
