Batch-processing (headless)
===========================

Headless batch-processing runs simulations on a computer without a graphical desktop and without supervision. Its basis is the **job**: a simulation that runs as an independent process and keeps running after the terminal that started it has been closed.

Jobs
----

.. code-block:: text

   axqua submit <case-file> --kind steady

The command creates a job, starts it in the background, prints the job ID and returns immediately. The terminal can then be closed, and a connection to a remote computer can be ended. The following job kinds exist:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Kind
     - Function
   * - ``preprocessing``
     - Build the TELEMAC model.
   * - ``steady``
     - Run the steady 2D simulation.
   * - ``unsteady``
     - Run the simulation with a varying discharge.
   * - ``build-3d``, ``steady-3d``
     - Write and run the TELEMAC-3D model.
   * - ``mesh-convergence``
     - Run the mesh convergence study.
   * - ``vertical-convergence``
     - Run the study of the number of vertical layers.
   * - ``openfoam-build``, ``openfoam-run``
     - Build and run the OpenFOAM model.
   * - ``calibration``, ``calibration-multiflow``
     - Run the Bayesian calibration.

Settings of a single run are passed with ``--option``, and the number of processor cores with ``--np``:

.. code-block:: text

   axqua submit <case-file> --kind mesh-convergence --np 16 --option tolerance=0.05

An unknown option is rejected before the job is created.

Each job has its own folder in the job root. The default job root is ``~/.local/share/axqua/jobs``. Another folder is selected with ``--job-root`` or with the environment variable ``AXQUA_JOB_ROOT``.

.. code-block:: text

   <job-root>/2026-08-14-my-reach-steady-a3f19c/
     job.json        the task, including a copy of the case settings
     status.json     the current state and progress
     runner.log      the log of aXqua for this job
     solver/         the output of the simulation code
     results/        a list of the result files

The job stores the settings of the case at the time of submission. A later change of the case file does not affect a running job, and a completed job remains reproducible.

Monitor and control jobs
------------------------

.. code-block:: text

   axqua list                          # all jobs with their state
   axqua status <job-id>               # state and progress of one job
   axqua status <job-id> --watch 30    # wait until the job has ended
   axqua logs <job-id> --follow        # follow the log
   axqua logs <job-id> --solver        # output of the simulation code
   axqua cancel <job-id>               # stop the job

A job passes through the states ``QUEUED``, ``STARTING``, ``RUNNING`` and ``POSTPROCESSING`` and ends as ``COMPLETED``, ``FAILED`` or ``CANCELLED``. If the process of a job disappears, for example because the computer was restarted, the next status query marks the job as ``FAILED``. A job therefore never remains in the state ``RUNNING`` by mistake.

To examine a failed job, read ``runner.log`` first. It contains the error with a code and a suggested remedy (:doc:`../troubleshooting/index`). The command ``axqua execute <job-id>`` repeats the job in the foreground, so that all messages appear in the terminal.

Sequences of jobs
-----------------

A sequence in which each step uses the result of the previous one has to wait for each job. The following script for Linux runs the preprocessing, the dry run and the mesh convergence study of a case in sequence and stops when a step fails:

.. code-block:: bash

   #!/bin/bash
   CASE=cases/my-reach/case-config.yml

   run_step () {
       job=$(axqua submit "$CASE" --kind "$1" --json | python -c "import json, sys; print(json.load(sys.stdin)['data']['job_id'])")
       axqua status "$job" --watch 30 > /dev/null
       state=$(axqua status "$job" --json | python -c "import json, sys; print(json.load(sys.stdin)['data']['state'])")
       echo "$1: $state ($job)"
       [ "$state" = "COMPLETED" ] || exit 1
   }

   run_step preprocessing
   run_step steady
   run_step mesh-convergence

Start such a script with ``nohup bash my-study.sh &`` so that it continues after the terminal is closed.

To repeat a step for several discharges, create one case file per discharge and call ``run_step`` in a loop over the case files.

How jobs are detached
---------------------

aXqua reads the environment of the simulation software once when a job is submitted and passes it to the job directly. The job therefore does not depend on the settings of the terminal from which it was started. The method of detaching depends on the operating system and is selected automatically:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Launcher
     - Method
   * - ``systemd``
     - A user service of the Linux system. Preferred on Linux.
   * - ``posix``
     - A separate process group. Used on Linux and macOS when no user services are available, for example in some remote sessions.
   * - ``windows``
     - A Windows job object.
   * - ``wsl``
     - A process inside the Windows Subsystem for Linux.

Another launcher is selected with ``--launcher``. The launchers ``windows`` and ``wsl`` have not yet been tested on a real Windows installation.

Cancelling a job first asks the job to end itself, so that it can close its files. If the job does not react within 20 seconds, aXqua stops all of its processes, including the parallel processes of the simulation code.
