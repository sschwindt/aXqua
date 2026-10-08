QGIS plugin
===========

The plugin lives in ``qgis_plugin/axqua/`` and is compatible with QGIS 3.44 and QGIS 4. It contains no numerical code. Every task is passed to the program ``axqua`` (:doc:`structure-uml`).

.. list-table::
   :header-rows: 1
   :widths: 36 64

   * - Module
     - Content
   * - ``plugin.py``
     - The object that QGIS loads: the panel, the menu entries, the toolbar button and the Processing provider, and their removal when the plugin is unloaded.
   * - ``compat.py``
     - The only module that distinguishes QGIS 3 from QGIS 4. All other modules import Qt classes through ``qgis.PyQt`` and take enumeration values from this module.
   * - ``core/runner_client.py``
     - Locates the program ``axqua``, calls it with an argument list and reads its JSON output.
   * - ``core/tasks.py``
     - Runs calls of the program in the background, so that QGIS remains responsive.
   * - ``core/job_model.py``
     - The job table and the rules for refreshing it.
   * - ``core/result_loader.py``
     - Finds the result files of a job and adds them to the QGIS project as layers.
   * - ``core/styles.py``
     - The predefined map styles for water depth and flow velocity.
   * - ``core/project.py``
     - The project file of the plugin, which lists the cases of a project.
   * - ``core/batch.py``
     - The steps of a batch and the shell script that runs them without QGIS.
   * - ``gui/sections.py``
     - The table of the tabs and sub-tabs: their order, the capabilities each one shows, and the documentation page and label that *Help* opens for it.
   * - ``gui/dock.py``
     - The panel: the fixed tabs, the job list below them, and *Help*.
   * - ``gui/configuration_tab.py``, ``gui/profile_editor.py``
     - The *Configuration* tab and the editor of the profile of this computer.
   * - ``gui/case_tab.py``, ``gui/section_pages.py``
     - The *Case Setup* tab and the pages of the workflow tabs.
   * - ``gui/findings.py``, ``gui/help.py``
     - The warning triangles, and the addresses of the documentation that the plugin opens.
   * - ``gui/`` (other files)
     - The job table, the log viewer, the settings dialog, the print layout and the movie export.
   * - ``processing/``
     - The algorithms for the QGIS Processing Toolbox.

For development, link the plugin folder into the plugin folder of a QGIS user profile instead of installing the archive:

.. code-block:: bash

   ln -s /path/to/aXqua/qgis_plugin/axqua ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/axqua

Where the installed QGIS is older than version 3.44, the script ``scripts/qgis_dev.sh`` starts the QGIS of a conda environment with a user profile of its own, in which it links and enables the plugin. Existing profiles are not changed:

.. code-block:: bash

   conda create -n qgis-dev -c conda-forge "qgis>=3.44"
   scripts/qgis_dev.sh

QGIS lists a plugin only if it can read ``metadata.txt``. The command ``python scripts/build_plugin_zip.py --check`` reads the file in the same way as QGIS.

*Help* in the plugin opens the documentation that is built into the plugin archive. A linked plugin folder has no built documentation, so that *Help* opens the published documentation instead. To build it into the linked folder, run ``python scripts/build_plugin_zip.py --help-only``.

The tests of the plugin are in ``qgis_plugin/tests/``. The tests that require QGIS are skipped when the Python module ``qgis`` is not available. The script ``scripts/build_plugin_zip.py`` builds the plugin archive and verifies the requirements of the QGIS plugin repository.
