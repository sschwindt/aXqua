.. _help-hydraulics:

Hydraulic simulations
=====================

A hydraulic simulation computes water depths and flow velocities for the discharge of the case. aXqua runs hydraulic simulations in a fixed order, from the fastest model to the most detailed one, and each model starts from the result of the previous one.

.. _warm-up-concept:

aXqua warm-up concept
---------------------

At the beginning of a simulation, nothing is known about the flow: the riverbed is dry, and the model first has to fill the reach with water until the outflow equals the inflow. This filling is of no interest in itself, but it consumes computing time. The more detailed a model is, the more expensive the filling becomes.

aXqua therefore lets the cheapest model do this work. The sequence is:

#. **TELEMAC-2D dry run.** The depth-averaged model starts on the dry riverbed and runs until the flow is steady. It typically takes minutes to a few hours.
#. **TELEMAC-3D (optional).** The 3D model starts from the 2D result. The reach is already filled, and only the vertical distribution of the velocity has to develop.
#. **OpenFOAM (optional).** The OpenFOAM model starts from the TELEMAC result. The position of the water surface, the wetted area and the velocities are taken from it.

The TELEMAC run that precedes an OpenFOAM simulation is called the **warm-up**. It has three effects on the OpenFOAM model: the cells below the water surface start filled with moving water, the top of the mesh is placed shortly above the water surface so that few cells contain only air, and the mesh is limited to the wetted part of the reach. If the case has no steady 2D result yet, aXqua runs a coarse TELEMAC simulation on its own for this purpose, which takes about one minute for a small reach.

The warm-up is active by default and should not be disabled. Without it, OpenFOAM starts with a flat lid high above the terrain, the mesh contains many times more air cells, and the filling of the reach has to be computed by the most expensive model. The warm-up is controlled by ``openfoam.pre_run.enabled`` in the case file.

.. _hydraulics-dry-runs:
.. _help-hydraulics-telemac:

TELEMAC dry runs 2D/3D
----------------------

A **dry run** is a simulation that starts from a dry riverbed (:ref:`case-initialization`).

**2D.** After preprocessing, click *Submit* on the *Steady 2D* tab of the plugin. The run appears on the *Jobs* tab with its progress. In a terminal, the command is:

.. code-block:: text

   axqua submit <case-file> --kind steady

When the run has finished, aXqua evaluates whether the flow is steady. It reads the discharge through every open boundary from the output of TELEMAC and writes the following files into ``axqua-case/simulation/``:

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - File
     - Content
   * - ``flux-convergence.png``, ``extracted-fluxes.csv``
     - Discharge through each open boundary over the simulated time.
   * - ``convergence-rate.png``, ``convergence-rate.csv``
     - Relative difference between inflow and outflow over the simulated time.
   * - ``wetting-report.csv``
     - Wetted area, divided into flowing water, stagnant thin water films and isolated puddles.
   * - ``outlet-profile.csv``
     - Water surface near the outflow boundary, with a note whether the prescribed water level dams up the flow or draws it down.

The run is steady when the relative difference between inflow and outflow stays below ``hydrodynamics.flux_tolerance``. If this is not the case at the end of the run, increase ``hydrodynamics.duration`` and repeat the run. The wetting report complements this criterion: a simulation can have balanced discharges and still show water in places where the real reach is dry. Large areas of stagnant film usually indicate a start with too much water or a downstream water level that is too high.

*Load results* adds the result (``r2d.slf``) to the QGIS project as water depth and flow velocity layers.

**3D.** A TELEMAC-3D simulation requires the steady 2D result. On the *Steady 3D* tab, *Build* writes the 3D steering files and *Submit* starts the run. aXqua determines the number of vertical layers from the water depth and the cell size. The build provides two variants. The **hydrostatic** variant is faster and is run first, to verify that inflow and outflow balance in 3D as well. The **non-hydrostatic** variant (``hydrodyn``) additionally resolves vertical accelerations, which matter at steep changes of the bed. *Submit* on the *Steady 3D* tab starts the hydrostatic variant, and *Load results* adds its depth-averaged result to the map. The non-hydrostatic variant is started in a terminal in this version:

.. code-block:: text

   axqua submit <case-file> --kind build-3d
   axqua submit <case-file> --kind steady-3d
   axqua submit <case-file> --kind steady-3d --option variant=hydrodyn

The number of vertical layers is a choice of its own, which is examined with a separate study (*Vertical convergence* tab). In a shallow reach with a fine mesh, only few layers fit over the water depth, and a 3D TELEMAC simulation then adds little information to the 2D result.

.. _hydraulics-hotstarts:

TELEMAC hotstarts
-----------------

A **hotstart** is a simulation that continues from the final state of a previous simulation instead of starting dry. The reach is then already filled, which saves the filling time in every subsequent run.

**Steady hotstart.** When the dry run has reached a steady state, aXqua writes the steering file ``hotstart2d.cas`` next to the model. It continues from the result of the dry run with unchanged boundary conditions. The calibration uses this file: each of its many runs changes a parameter slightly and starts from the steady state (:doc:`calibration-validation`).

**Unsteady hotstart (varying discharge).** To simulate a flood wave, enter a discharge time series under ``boundaries.inflow`` (:ref:`case-boundaries`) and repeat the preprocessing. aXqua then additionally writes the steering file ``unsteady2d.cas``. The unsteady run starts from the steady result, so that the reach is filled when the flood wave arrives. The steady discharge of the case (``boundaries.prescribed_flowrate``) should therefore equal the first value of the time series. Start the run with *Submit* on the *Unsteady 2D* tab, where the option *Run in 3D* selects the 3D model, or in a terminal:

.. code-block:: text

   axqua submit <case-file> --kind unsteady
   axqua submit <case-file> --kind unsteady --option mode_3d=true

The duration of the unsteady run follows from the time series. With the outflow condition ``stage_discharge``, the downstream water level follows the rating curve during the flood wave.

.. _help-hydraulics-openfoam:

OpenFOAM
--------

An OpenFOAM simulation resolves the flow in three dimensions together with the water surface. It is required where the vertical structure of the flow matters, for example at structures or in a fish pass. The settings are described in :ref:`preprocessing-openfoam-choices`.

#. **Build.** On the *Free surface (VOF)* tab, click *Build*. aXqua runs the warm-up if required, creates the mesh and writes the OpenFOAM case into ``axqua-case/openfoam/``.
#. **Run.** Click *Submit*. The run has two stages: a short first stage with a small time step, in which the water surface settles, and the main stage. The progress display shows the Courant number (``Co``) and the time step (``dt``). In a healthy run, the time step stays near a constant value. A time step that falls by orders of magnitude indicates a problem, typically air that is accelerated to unrealistic velocities.
#. **Evaluate.** After the run, aXqua reports the discharge of water at the inflow and the outflow and compares their relative difference with ``hydrodynamics.flux_tolerance``, which is the same criterion as for TELEMAC. It also reports whether water reached the lid or the lateral limits of the mesh. In that case, the mesh has constrained the flow, and ``openfoam.freeboard`` or ``openfoam.wet_margin`` has to be increased.

In a terminal, the commands are:

.. code-block:: text

   axqua submit <case-file> --kind openfoam-build
   axqua submit <case-file> --kind openfoam-run

Before the first ``vof`` run of a new case, run the model once in the mode ``rigid-lid`` with a coarse mesh (``cell_size_factor`` of 3 to 5). This run takes minutes and reveals most setup errors.
