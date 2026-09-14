Post-processing with VisIt
==========================

aXqua renders figures from solver results through `VisIt
<https://visit-dav.github.io/visit-website/>`_. VisIt ships its **own Python
interpreter**, so aXqua cannot import it: it generates a script and runs it with
``visit -cli -nowin -s``, the same shape as sourcing an ``etc/bashrc`` before
calling ``interFoam``. The generated scripts are kept under
``<postprocessing_dir>/visit/`` as the reproducible record of each figure.

Configuration
-------------

.. code-block:: yaml

   postproc:
     visit: /home/IWS/public/visit/bin/visit   # the LAUNCHER executable
     scenes: ["free-surface", "velocity-plan", "profiles"]
     image_width: 1600
     image_height: 1000

``visit`` names an executable, not a script to source, which is why it is not
spelled like ``telemac.pysource`` / ``openfoam.bashrc``. The block is additive: a
config without it gets the defaults and nothing consults them.

Running
-------

.. code-block:: bash

   axqua postproc <config> --list          # which scenes are available, and why not
   axqua postproc <config> --script-only   # write the scripts, run nothing
   axqua postproc <config>                 # render

``--script-only`` needs no VisIt at all, which is how to inspect the output on a
machine that has none. ``--list`` reports the *reason* a scene is unavailable
rather than failing inside a renderer.

The scenes
----------

``free-surface``
   An isosurface of ``alpha.water = 0.5`` coloured by speed. Under
   ``openfoam.mode: rigid-lid`` there is no interface to contour, so it falls
   back to the lid patch **and says on the figure that the surface was
   prescribed by the 2D seed rather than solved** - otherwise the picture reads
   as a computed free surface, which it is not.

``velocity-plan``
   A horizontal slice coloured by speed with vector glyphs, taken at the same
   relative depth (0.6 h) a FlowTracker measures at, so the figure and the
   calibration are looking at the same quantity.

``profiles``
   Modelled vertical velocity profiles against the measured verticals, with
   error bars, plus a residual CSV. **VisIt samples, matplotlib draws**: the
   measured overlay needs error bars, the figure has to match aXqua's other
   figures, and once the curves are exported the comparison stays reproducible
   without VisIt. Run it once at the prior centre and once at the calibrated
   parameters - those two panels are the calibration result.

How a backend contributes results
---------------------------------

``axqua.postproc`` never imports a solver. Each backend answers
:meth:`~axqua.core.registry.SolverBackend.describe_results` with plain
:class:`~axqua.postproc.dataset.Dataset` values naming what exists, which fields
and patches it carries, and at which times; the renderer draws whatever comes
back. A test asserts the direction of that dependency.

Two details found only by running it
------------------------------------

* **VisIt opens an OpenFOAM case through** ``system/controlDict``, not the
  ``case.foam`` marker. Unlike ParaView, its reader rejects a zero-byte ``.foam``
  handle - measured against VisIt 3.5.0, where ``case.foam`` yields 0 meshes and
  0 variables. The ``Dataset`` still carries ``case.foam`` because that is what
  ParaView and the QGIS manifest want; translating is the VisIt backend's job.
  Its variables are also mesh-prefixed (``internalMesh/U``), so a bare ``<U>``
  does not resolve.
* **The time slider must be set explicitly.** VisIt opens a database on its first
  state - ``t=0``, the initial condition, where the velocity field is identically
  zero. A figure made without advancing it renders a blank result behind a
  ``0.000`` colour bar while looking perfectly well-formed. This is the same
  failure the ``mean_last`` -> ``last`` patch prevents on the calibration side.
