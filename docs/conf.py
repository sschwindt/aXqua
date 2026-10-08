"""Sphinx configuration for the axqua documentation (Read the Docs)."""

import os
import re
import sys
from datetime import datetime

# make the package importable for autodoc (sources live in ../src)
sys.path.insert(0, os.path.abspath("../src"))

# -- Project information ------------------------------------------------------
project = "aXqua"
author = "Sebastian Schwindt"
copyright = f"{datetime.now():%Y}, {author}"
release = "0.4.2"
version = "0.1"

# -- General configuration ----------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",      # pull docstrings from the Python modules
    "sphinx.ext.autosummary",  # summary tables
    "sphinx.ext.napoleon",     # parse NumPy-/Google-style docstrings
    "sphinx.ext.viewcode",     # add [source] links
    "sphinx.ext.intersphinx",  # cross-link to numpy/python docs
]

# The copy of the documentation that is built into the QGIS plugin
# (scripts/build_plugin_zip.py passes "-t plugin_help") leaves the source listings out:
# they are 10 MB of the 25 MB an archive may have, and Help is opened for the workflow
# sections, not to read source code. "tags" is provided by Sphinx when it runs this file.
if tags.has("plugin_help"):  # noqa: F821
    extensions.remove("sphinx.ext.viewcode")

templates_path = ["_templates"]
# _incoming/ is a drop folder for text that still has to be folded into the tree.
exclude_patterns = ["_build", "_incoming", "Thumbs.db", ".DS_Store"]

# -- Autodoc ------------------------------------------------------------------
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}
# Heavy / optional third-party deps are mocked so the RTD build stays light and
# does not need gmsh, GDAL, etc. installed (docstrings are still rendered).
autodoc_mock_imports = [
    "numpy", "pandas", "scipy", "yaml", "pyproj",
    "shapely", "geopandas", "rasterio", "gmsh",
    "matplotlib", "openpyxl",
]

napoleon_google_docstring = False
napoleon_numpy_docstring = True
napoleon_use_param = True
napoleon_use_rtype = True

# Docstrings write absolute values the way hydraulics does, |Q_in| - |Q_out|, which
# reStructuredText reads as a substitution reference and reports as an error. They are
# turned into literals when the docstring is read, so the sources stay as they are
# written. A bar with a space next to it (a table row, a shell pipe) is left alone, and
# so is anything that already stands inside an inline literal.
_ABSOLUTE_VALUE = re.compile(r"(?<![`|\w])\|(\S(?:[^|`\n]{0,38}\S)?)\|(?![`|\w])")
_INLINE_LITERAL = re.compile(r"(``.+?``)")


def _literal_absolute_values(app, what, name, obj, options, lines):
    for index, line in enumerate(lines):
        parts = _INLINE_LITERAL.split(line)
        parts[::2] = [_ABSOLUTE_VALUE.sub(r"``|\1|``", part) for part in parts[::2]]
        lines[index] = "".join(parts)


def setup(app):
    app.connect("autodoc-process-docstring", _literal_absolute_values)


intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}

# -- HTML output --------------------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_title = "aXqua"
# The menu lists sections and their subsections at all times; a subsubsection level opens
# with its parent. The full tree has to be in the page for that (collapse_navigation), and
# _static/axqua.css is what keeps the second level unfolded for sections other than the
# current one.
html_theme_options = {
    "collapse_navigation": False,
    "navigation_depth": 3,
    "titles_only": False,
    "sticky_navigation": True,
}
html_css_files = ["axqua.css"]
