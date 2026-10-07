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
   * - ``gui/``
     - The panel with its tabs, the job table, the log viewer, the settings dialog, the print layout and the movie export.
   * - ``processing/``
     - The algorithms for the QGIS Processing Toolbox.

For development, link the plugin folder into the plugin folder of a QGIS user profile instead of installing the archive:

.. code-block:: bash

   ln -s /path/to/aXqua/qgis_plugin/axqua ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/axqua

The tests of the plugin are in ``qgis_plugin/tests/``. The tests that require QGIS are skipped when the Python module ``qgis`` is not available. The script ``scripts/build_plugin_zip.py`` builds the plugin archive and verifies the requirements of the QGIS plugin repository.
