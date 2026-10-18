Building the documentation
==========================

The documentation is written in reStructuredText and built with `Sphinx <https://www.sphinx-doc.org>`_. The build requires only Sphinx and the theme. The packages that aXqua needs at run time are replaced by placeholders during the build (``autodoc_mock_imports`` in ``docs/conf.py``).

.. code-block:: bash

   pip install -r docs/requirements-docs.txt
   sphinx-build -b html docs docs/_build/html

Open ``docs/_build/html/index.html`` in a web browser to read the result. The output folder ``docs/_build/`` is excluded from version control.

Languages and publication
-------------------------

The documentation is published twice. `Read the Docs <https://axqua.readthedocs.io>`_ builds the English pages from the branch ``main`` by itself, with the commands above, and serves them at ``axqua.readthedocs.io`` and ``axqua.ch``. `GitHub Pages <https://axqua.eu>`_ serves the English pages at ``axqua.eu`` together with a German translation at ``axqua.eu/de/`` and a French one at ``axqua.eu/fr/``; ``axqua.de`` and ``axqua.fr`` forward there. The German and French pages are machine translations, and every page says so.

The translations are kept per page in ``docs/locale/<language>/LC_MESSAGES/``, in the gettext format that Sphinx reads. They require a separate environment with the offline translation engine Argos Translate, and they are published with one script in the root folder of the repository:

.. code-block:: bash

   mamba env create -f docs/i18n/environment.yml
   bash deploy.sh           # build the three languages into docs/_build/site
   bash deploy.sh --push    # publish

``deploy.sh`` builds the English pages, translates only the paragraphs that are new or changed since the last publication, builds the German and French pages, and copies the result into the worktree of the branch ``gh-pages``. With ``--push``, it commits the updated translations on ``main``, pushes ``main`` (which also updates Read the Docs), and commits and pushes ``gh-pages``. It refuses to publish uncommitted changes.

A glossary of technical terms for each language (``docs/i18n/glossary.de.tsv`` and ``glossary.fr.tsv``) makes the engine use the terms of hydraulic engineering, for example *Abflusskurve* and *courbe de tarage* for rating curve. Labels of the plugin in single asterisks, code in double backquotes, and titles of papers in the lists of references are never translated. A translation that was corrected by hand in a catalog is kept as long as the English text does not change. ``docs/i18n/README.md`` explains the glossaries, the corrections by hand and the settings of the scripts.

Rules for the structure
-----------------------

* The outline of sections and subsections is fixed and verified by the test ``tests/test_docs_outline.py``. Change the test together with the outline.
* Each subsection is one file. Subsubsections are headings within that file.
* The navigation menu shows the sections, which are set in bold capital letters. The subsections of a section are folded. They open when the section is opened with its fold button or when one of its pages is shown, and the subsubsections of a subsection open in the same way. This behavior is defined by ``html_theme_options`` in ``docs/conf.py`` and by ``docs/_static/axqua.css``.
* Labels that start with ``help-`` mark the targets that the plugin opens from its tabs. Keep them when a page is reorganized.
* The screenshots of the plugin (``docs/img/gui-*.png``) are taken by ``python scripts/build_screenshots.py --case <case-file>``, which starts QGIS without a window, frames and numbers the places that a caption explains, and replaces the folders of the computer by neutral ones. The case has to be built and to have a completed steady job. The script ends with an error and names the figure if a place that a caption explains is not on the screen, and it records the number of marks of each figure in ``docs/img/gui-screenshots.json``, which the test compares with the captions. Run the script again after a change to the plugin that a picture shows.
* The UML diagrams (``docs/img/uml-*.svg``) are generated from the source code with ``python scripts/build_uml.py``, which requires `Graphviz <https://graphviz.org>`_. The same test fails if a diagram no longer agrees with the code. Run the script again in that case.
