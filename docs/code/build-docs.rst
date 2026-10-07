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
* The navigation menu shows sections and subsections at all times and opens subsubsections on demand. This behavior is defined by ``html_theme_options`` in ``docs/conf.py`` and by ``docs/_static/axqua.css``.
* Labels that start with ``help-`` mark the targets that the plugin opens from its tabs. Keep them when a page is reorganized.
