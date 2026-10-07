.. _help-batch:

Batch-processing (visual automation)
====================================

A complete study consists of several steps that depend on each other, for example preprocessing, a dry run, a mesh convergence study and a calibration. Batch-processing runs such a sequence without supervision.

.. note::

   A tab in which a sequence of steps is assembled graphically and exported as a script is not yet available in this version. The functions described on this page exist today.

Jobs continue without QGIS
--------------------------

Every simulation that is started from the plugin is a job that runs independently of QGIS. QGIS can be closed after a job has been submitted, and the computer can be used for other work. When QGIS is opened again, the *Jobs* tab lists all jobs with their current state, including jobs that finished or failed in the meantime. A job that was interrupted by a restart of the computer is marked as failed and can be submitted again.

Sequences with the QGIS model designer
--------------------------------------

aXqua adds three algorithms to the QGIS *Processing Toolbox*, which can be combined in the model designer of QGIS (menu *Processing*):

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Algorithm
     - Function
   * - *Submit a simulation job*
     - Checks the case, creates a job and returns its ID. The algorithm does not wait for the simulation.
   * - *Check job status*
     - Returns the current state and progress of a job.
   * - *Import job results*
     - Loads the results of a completed job with the predefined map styles.

With these algorithms, the same case can for example be submitted for a series of discharges. Sequences in which one step has to wait for the previous one are currently run from a terminal (:doc:`../automation/batch-headless`).
