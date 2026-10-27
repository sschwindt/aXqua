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
     - The list of cases of the panel, kept in the settings of QGIS, and the reader of the project files of earlier versions.
   * - ``core/batch.py``
     - The steps of a batch and the shell script that runs them without QGIS.
   * - ``gui/sections.py``
     - The table of the tabs and sub-tabs: their order, the capabilities each one shows, and the documentation page and label that *Help* opens for it.
   * - ``gui/dock.py``
     - The panel: the fixed tabs, the job list below them, *Clean Up* and *Help*.
   * - ``gui/configuration_tab.py``, ``gui/profile_editor.py``
     - The *Configuration* tab and the editor of the profile of this computer.
   * - ``gui/install_wizard.py``, ``gui/report_issue.py``
     - The installation wizards for TELEMAC, OpenFOAM and the postprocessors: the tabs settings, check, and the running installation, a window around ``axqua install``; and the report of a problem that the checks and fixes did not solve.
   * - ``gui/case_editor.py``, ``gui/editor_base.py``
     - The case settings: the pages and frames of a case file, generated from ``axqua schema`` with the settings that are required in bold dark purple, and what both editors share: *Save*, *Cancel* and *Exit*.
   * - ``gui/case_tab.py``, ``gui/section_pages.py``
     - The *Case Setup* tab and the pages of the workflow tabs.
   * - ``gui/findings.py``, ``gui/help.py``
     - The warning triangles, and the addresses of the documentation that the plugin opens (a local copy or the published one, in the language of QGIS). ``gui/style.py`` holds the colors: the dark purple of the aXqua icon for what is required, dark green for what was found.
   * - ``gui/cleanup_dialog.py``
     - The window of *Clean Up*, around ``axqua cleanup``: what a case can do without, ticked by category, and its removal.
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

*Help* in the plugin opens the documentation at the section of the tab that is showing. The plugin archive carries no copy of the documentation: ``axqua docs`` names a copy built on this computer (``$AXQUA_DOCS``, else ``docs/_build/site`` or ``docs/_build/html`` of the source checkout) and whether the default browser can read it, and the plugin opens the published documentation otherwise. A local page is opened through a small forwarding page (``_help/open.html`` beside the copy), because ``xdg-open`` and Chromium drop the section part (``#...``) of a file address.

The tests of the plugin are in ``qgis_plugin/tests/``. The tests that require QGIS are skipped when the Python module ``qgis`` is not available. The script ``scripts/build_plugin_zip.py`` builds the plugin archive and verifies the requirements of the QGIS plugin repository.

**Releases.** The plugin and the program are released together under one version number, which ``pyproject.toml``, ``src/axqua/__init__.py`` (``_FALLBACK_VERSION``), ``qgis_plugin/axqua/metadata.txt`` and ``docs/conf.py`` carry. A tag ``v<version>`` pushed to GitHub starts the workflow ``release`` (``.github/workflows/plugin-release.yml``): it builds the plugin archive and attaches it to the GitHub release, then builds the program, checks the wheel and uploads it to PyPI through trusted publishing, so no token is stored anywhere. The upload runs only after the checks of the plugin and only while the repository variable ``PYPI_UPLOAD`` is ``true``. Without it, the program is uploaded by hand from a clean copy of the tagged commit; ``twine`` asks for an API token of pypi.org (*Account settings > API tokens*; a token limited to the project ``axqua`` suffices):

.. code-block:: bash

   rm -rf build dist/pypi
   python -m pip install --upgrade build twine
   python -m build --outdir dist/pypi
   twine check --strict dist/pypi/*
   python scripts/check_wheel.py dist/pypi/*.whl
   twine upload dist/pypi/*

``scripts/check_wheel.py`` confirms that the wheel holds exactly the tracked files of ``src/axqua`` and the license files that ``pyproject.toml`` names. PyPI accepts a version number only once, also after the files were deleted, so a mistake requires a new version. ``twine upload --repository testpypi dist/pypi/*`` uploads to `TestPyPI <https://test.pypi.org>`_ for a rehearsal. The comment above the job ``pypi`` in ``.github/workflows/plugin-release.yml`` explains how trusted publishing is set up on pypi.org.
