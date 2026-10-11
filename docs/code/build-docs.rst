Building the documentation
==========================

The documentation is written in reStructuredText and built with `Sphinx <https://www.sphinx-doc.org>`_. The build requires only Sphinx and the theme. The packages that aXqua needs at run time are replaced by placeholders during the build (``autodoc_mock_imports`` in ``docs/conf.py``).

.. code-block:: bash

   pip install -r docs/requirements-docs.txt
   sphinx-build -b html docs docs/_build/html

Open ``docs/_build/html/index.html`` in a web browser to read the result. The output folder ``docs/_build/`` is excluded from version control.

Rules for the structure
-----------------------

* The outline of sections and subsections is fixed and verified by the test ``tests/test_docs_outline.py``. Change the test together with the outline.
* Each subsection is one file. Subsubsections are headings within that file.
* The navigation menu shows sections and subsections at all times and opens subsubsections on demand. The sections are set in bold capital letters. This behavior is defined by ``html_theme_options`` in ``docs/conf.py`` and by ``docs/_static/axqua.css``.
* Labels that start with ``help-`` mark the targets that the plugin opens from its tabs. Keep them when a page is reorganized.
* The screenshots of the plugin (``docs/img/gui-*.png``) are taken by ``python scripts/build_screenshots.py --case <case-file>``, which starts QGIS without a window, frames and numbers the places that a caption explains, and replaces the folders of the computer by neutral ones. The case has to be built and to have a steady result. Run the script again after a change to the plugin that a picture shows.
* The UML diagrams (``docs/img/uml-*.svg``) are generated from the source code with ``python scripts/build_uml.py``, which requires `Graphviz <https://graphviz.org>`_. The same test fails if a diagram no longer agrees with the code. Run the script again in that case.
