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
release = "1.1.0"
version = "0.1"

# -- General configuration ----------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",      # pull docstrings from the Python modules
    "sphinx.ext.autosummary",  # summary tables
    "sphinx.ext.napoleon",     # parse NumPy-/Google-style docstrings
    "sphinx.ext.viewcode",     # add [source] links
    "sphinx.ext.intersphinx",  # cross-link to numpy/python docs
    "sphinx_design",           # tab-set / tab-item
]

templates_path = ["_templates"]
# _incoming/ is a drop folder for text that still has to be folded into the tree. i18n/ holds the tools of the translations and locale/ their catalogs; neither has pages.
exclude_patterns = ["_build", "_incoming", "i18n", "locale", "Thumbs.db", ".DS_Store"]

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
# "name : str, optional" names no object called optional. Read as a type, the word
# became a link to whatever attribute of that name the reference documents.
napoleon_preprocess_types = True

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


# -- Translations -------------------------------------------------------------
# The German and French pages are built from the catalogs in docs/locale/<lang>/LC_MESSAGES, one per page, which docs/i18n/translate.py keeps up to date (docs/i18n/README.md). Sphinx reads them when it builds with -D language=de or fr; Read the Docs does the same for a translation project. The English build does not read them.
locale_dirs = ["locale/"]
gettext_compact = False
gettext_uuid = False
gettext_last_translator = "docs/i18n/translate.py"
gettext_language_team = "aXqua"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}

# -- HTML output --------------------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_title = "aXqua"
# The mark in the top-left of the sidebar, above the search box, and the icon a
# browser shows in the tab and in a bookmark. Both paths are relative to this file;
# Sphinx copies them into the built _static itself, so they do not belong in
# html_static_path.
html_logo = "img/aXqua-icon-reduced.png"
# A .ico rather than the .png: it carries 16, 24, 32, 48, 64, 128 and 256 px in one
# file, so a browser takes the size it needs instead of downsampling 500 px artwork
# into a 16 px tab. The mark is thin white curves on a dark drop, which is exactly
# what a bad downsample destroys.
html_favicon = "img/favicon.ico"
# The menu lists the sections. The subsections of a section are folded until the section is opened with its fold button or one of its pages is shown, and the subsubsections of a subsection open in the same way. The full tree has to be in the page for a section to have a fold button (collapse_navigation). _static/axqua.css sets the sections in bold capital letters.
html_theme_options = {
    "collapse_navigation": False,
    "navigation_depth": 3,
    "titles_only": False,
    "sticky_navigation": True,
}
html_css_files = ["axqua.css"]
html_baseurl = os.environ.get(
    "READTHEDOCS_CANONICAL_URL", "https://axqua.eu/"
)
