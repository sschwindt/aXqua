.. _help-calibration:

Calibration & validation
========================

A numerical model contains parameters that cannot be measured directly, most importantly the roughness of the riverbed. **Calibration** adjusts these parameters until the model reproduces field measurements. **Validation** then tests the calibrated model against measurements that were not used for the calibration. aXqua performs both with `HydroBayesCal <https://hydrobayescal.readthedocs.io>`_.

Two terms are used throughout this section. **Calibration parameters** are the uncertain inputs of the model that are adjusted, for example the roughness of a zone. **Calibration targets** are the measured quantities that the model is compared with, for example the water depth at surveyed points.

.. _calibration-introduction:

Introduction to Bayesian calibration
------------------------------------

A calibration by trial and error ends with one set of parameter values and no statement about how reliable these values are. Bayesian calibration answers a more useful question: which parameter values are probable, given the measurements and their errors?

The user states for each calibration parameter the range in which its true value is expected. This range is the **prior** distribution. Bayes' theorem combines the prior with the measurements. Parameter values with which the model reproduces the measurements become more probable, and all others become less probable. The result is the **posterior** distribution of each parameter. A narrow posterior indicates that the measurements determine the parameter well. A posterior that is as wide as the prior indicates that the measurements contain no information about this parameter.

The direct evaluation of Bayes' theorem requires many thousands of model runs, which is not feasible for a model that runs for hours. HydroBayesCal therefore replaces the model by a **surrogate model**, in the following steps:

#. The numerical model is run for a limited number of parameter combinations that are spread over the prior ranges (30 by default).
#. A Gaussian process emulator is trained with these runs. The emulator is a statistical model that predicts the result of the numerical model for any parameter combination within a fraction of a second, together with the uncertainty of its own prediction (Rasmussen and Williams 2006).
#. The emulator is evaluated for thousands of parameter combinations to compute the posterior distribution.
#. **Bayesian active learning** selects the parameter combination at which one additional run of the numerical model improves the emulator the most. The model is run for this combination, and the emulator is trained again (Oladyshkin et al. 2020).
#. Steps 3 and 4 are repeated until the selected number of runs is reached (50 by default).

In this way, a few dozen model runs are sufficient where a direct Bayesian calibration would require thousands. Applications to reservoir models are documented by Mouris et al. (2023) and Schwindt et al. (2023).

.. _calibration-ground-truth:

Ground truth data setup
-----------------------

Field measurements are called ground truth. aXqua accepts them in a workbook with a fixed structure, which is called the calibration target template. Create it for a case with:

.. code-block:: text

   axqua targets <case-file>

The command writes the workbook ``calibration-target-data.xlsx`` into ``user-sources/ground-truth/``. Its sheets are:

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Sheet
     - Content
   * - ``hydraulics``
     - One row per measurement point: velocity components, their fluctuations, water depth and bed elevation. The velocity magnitude and the turbulent kinetic energy are computed by formulas in the sheet.
   * - ``morphodynamics``
     - Grain sizes (d16 to d90) and the fraction of fine sediment per sample. The measured bed change is filled in automatically from the DEM of difference (:ref:`case-morphodynamics`).
   * - ``parameters``
     - The calibration parameters with their lower and upper limits, selected from a list of available TELEMAC and GAIA parameters.

Each row carries an ID. The positions of the measurements are read from a point layer that contains the same IDs, so that coordinates do not have to be typed in. Enter the workbook and the point layers in the case file:

.. code-block:: yaml

   ground_truth:
     targets:
       file: user-sources/ground-truth/calibration-target-data.xlsx
       hydraulics_positions: user-sources/geodata/measurement-points.gpkg
       sediment_positions: user-sources/geodata/sediment-samples.gpkg
       join_key: ID

Then repeat the preprocessing, which compiles the measurements into the tables that the calibration reads. The IDs have to be unique within a sheet. Measurements of several survey days whose numbering restarts at zero therefore need distinct IDs.

Two alternatives to the workbook exist. ``ground_truth.measurements`` names a table that the user compiled, with the coordinates x, y and z in the first three columns and one column per measured quantity. ``ground_truth.sources`` lists original instrument files together with the point layer of their positions, which aXqua compiles into such a table.

Velocity measurements with a SonTek FlowTracker2 are transferred into the ``hydraulics`` sheet with the script ``extract_flowtracker.py``, which aXqua places next to the workbook. Note that the velocity fluctuation is the standard deviation of the velocity samples. It is not the column ``VxErr`` of the instrument export, which is the standard error of the mean velocity.

.. _calibration-setup:

Bayesian calibration & validation setup
---------------------------------------

The ``calibration`` block of the case file defines what is calibrated against which measurements:

.. code-block:: yaml

   calibration:
     calibration_quantities: ["WATER DEPTH"]
     extraction_quantities: ["WATER DEPTH", "SCALAR VELOCITY"]
     measurement_error: 0.10     # used where no measured error is available
     init_runs: 30               # runs for the first training of the emulator
     max_runs: 50                # total number of runs
     parameters:
       - {name: zone1, min: 0.05, max: 0.50}   # ks of roughness zone 1 [m]
       - {name: zone2, min: 0.10, max: 1.00}   # ks of roughness zone 2 [m]

``calibration_quantities`` are the calibration targets. The names are those of the TELEMAC result variables: ``WATER DEPTH``, ``SCALAR VELOCITY``, ``TURBULENT ENERG.`` (requires the k-epsilon turbulence model) and ``CUMUL BED EVOL`` (requires GAIA).

``parameters`` lists the calibration parameters with the limits of their prior ranges. The name ``zone<N>`` refers to the roughness of zone N. A name that starts with ``gaia`` refers to a GAIA keyword, for example ``gaiaCLASSES SHIELDS PARAMETERS 1`` for the critical Shields parameter of the first grain size class. Parameters that are selected in the ``parameters`` sheet of the workbook are added to this list.

Select wide but physically plausible limits. If the posterior of a parameter accumulates at one of its limits, the true value probably lies outside the range, and the calibration has to be repeated with a wider range.

**Validation.** A validation is only meaningful with measurements that are statistically independent of the calibration data. Two approaches are common: a **data split**, in which a part of the measurement points is withheld from the calibration, and a **second dataset**, for example measurements at another discharge. ``extraction_quantities`` are read from the model results at all measurement points in addition to the calibration targets. A quantity that is extracted but not calibrated, such as the flow velocity in a calibration against water depths, already provides an independent check.

.. note::

   The definition of a data split or of a separate validation dataset in the case file is not yet available in this version.

.. _calibration-run:

Run HydroBayesCal
-----------------

The calibration requires the additional package HydroBayesCal (``pip install ".[calibration]"``) and a steady hotstart of the case (:ref:`hydraulics-hotstarts`), from which each of its runs starts.

In the plugin, click *Submit* on the *Calibration (BAL)* tab. The option *Prepare only* writes all input files without starting the runs, so that they can be inspected first. In a terminal:

.. code-block:: text

   axqua submit <case-file> --kind calibration --option prepare_only=true
   axqua submit <case-file> --kind calibration

The computing time is approximately the number of runs times the duration of one hotstart run. The *Jobs* tab shows the current iteration.

**Several discharges.** Roughness values that were calibrated at one discharge are not necessarily valid at another one. The script ``run_Bayes_cal_multiflow.py`` in the case folder calibrates one common set of parameters against measurements at several discharges. Run it with ``--smoke`` first. This short test with three runs verifies the complete chain before days of computing time are spent.

**OpenFOAM.** An OpenFOAM model can be calibrated against measured velocity components with the script ``run_Bayes_cal_openfoam.py``. Because a calibration requires dozens of runs, they are performed with a coarse mesh in the mode ``rigid-lid``. The script ``openfoam_verify_posterior.py`` then repeats the calibrated case at full resolution in the mode ``vof``. The roughness of an OpenFOAM model is calibrated as one value for the entire bed. It is therefore not comparable with the roughness values per zone of a TELEMAC calibration.

.. _calibration-quality:

Quality analysis
----------------

HydroBayesCal writes its results into the folder ``auto-saved-results-HydroBayesCal`` within ``axqua-case/calibration-validation/``. Assess a calibration in four steps:

#. **Did the active learning converge?** HydroBayesCal records two indicators after every iteration. The Bayesian model evidence (BME) expresses how well the model reproduces the measurements on average over the parameter ranges. The relative entropy (RE) expresses how much information the measurements added to the prior. Both should level off toward the end of the calibration. If they still change strongly, increase ``max_runs``.
#. **Is the posterior narrower than the prior?** Compare the two distributions of each parameter. A posterior that is clearly narrower than the prior and lies inside the range identifies the parameter. A posterior at a limit of the range calls for a wider range. A posterior that resembles the prior means that the measurements do not constrain this parameter.
#. **Does the calibrated model reproduce the calibration targets?** The calibrated parameter set is the most probable combination of the posterior. HydroBayesCal compares the model results for this set with the measurements. Check whether the remaining differences are of the size of the measurement errors, and whether they are distributed randomly or concentrated in one part of the reach. A systematic deviation indicates an error in the model, for example in the terrain data or the boundary conditions, that no parameter value can compensate.
#. **Does the calibrated model reproduce independent data?** Compare the results with the validation measurements. The differences are typically somewhat larger than for the calibration targets. Much larger differences indicate that the calibration has compensated errors of the model structure through the parameters.

The `HydroBayesCal documentation <https://hydrobayescal.readthedocs.io>`_ describes the result files and figures in detail.

References
~~~~~~~~~~

Mouris, K., Acuña Espinoza, E., Schwindt, S., Mohammadi, F., Haun, S., Wieprecht, S., and Oladyshkin, S. (2023). "Stability criteria for Bayesian calibration of reservoir sedimentation models." *Modeling Earth Systems and Environment*, 9, 3643-3661. `doi:10.1007/s40808-023-01712-7 <https://doi.org/10.1007/s40808-023-01712-7>`_

Oladyshkin, S., Mohammadi, F., Kroeker, I., and Nowak, W. (2020). "Bayesian3 active learning for the Gaussian process emulator using information theory." *Entropy*, 22(8), 890. `doi:10.3390/e22080890 <https://doi.org/10.3390/e22080890>`_

Rasmussen, C. E., and Williams, C. K. I. (2006). *Gaussian processes for machine learning*. MIT Press.

Schwindt, S., Callau Medrano, S., Mouris, K., Beckers, F., Haun, S., Nowak, W., Wieprecht, S., and Oladyshkin, S. (2023). "Bayesian calibration points to misconceptions in three-dimensional hydrodynamic reservoir modeling." *Water Resources Research*, 59(3), e2022WR033660. `doi:10.1029/2022WR033660 <https://doi.org/10.1029/2022WR033660>`_
