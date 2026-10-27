# aXqua

**Automated setup, persistent execution and calibration of models of free-surface water bodies: rivers, lakes, reservoirs and hydraulic structures.**

aXqua builds a **TELEMAC-2D/3D** case (optionally with **GAIA**) or an **OpenFOAM** free-surface case from geodata, runs it as a job that outlives the shell or QGIS that started it, and calibrates it with [HydroBayesCal](https://github.com/Ecohydraulics/hydrobayescal) (surrogate-assisted Bayesian calibration with quantified uncertainty). A **QGIS plugin** drives the whole workflow; everything also works from a terminal.

**Documentation:** [axqua.eu](https://axqua.eu) (English), [axqua.eu/de](https://axqua.eu/de/) (Deutsch), [axqua.eu/fr](https://axqua.eu/fr/) (Français), and [axqua.ch](https://axqua.readthedocs.io) (Switzerland).

## Install

```bash
pip install axqua                        # Python 3.10 or newer, best in an environment of its own
axqua profile init                       # find TELEMAC, OpenFOAM, ParaView, VisIt on this computer
```

`pip install "axqua[calibration]"` adds HydroBayesCal for the Bayesian calibration. For development, install from a clone of this repository:

```bash
mamba env create -f environment.yml      # the environment axqua-env
mamba activate axqua-env
pip install -e .
```

The QGIS plugin (QGIS 3.44 or newer) is `qgis_plugin/axqua/`; for development link it into a QGIS profile, or start `scripts/qgis_dev.sh`, which uses a QGIS of its own. The installation of the plugin and of the simulation software is described in the [documentation](https://axqua.eu/installation/index.html).

## Quick start

```bash
axqua example get example-isar --folder ~/aXqua-examples    # a small Isar case with its data (6 MB)
axqua submit <case-file> --kind preprocessing  # build the TELEMAC model, as a job
axqua submit <case-file> --kind steady         # the steady 2D run (waits for the build)
axqua list                                     # the jobs, with state and progress
axqua cleanup <case-file>                      # what the case could do without (frees disk space)
```

The example cases and their guides are in [`cases/example-isar/`](https://github.com/sschwindt/aXqua/blob/main/cases/example-isar/README.md) (river reach, calibration and validation) and [`cases/example-agrohydrology/`](https://github.com/sschwindt/aXqua/blob/main/cases/example-agrohydrology/README.md) (hydrology mode: rainfall-runoff on an arable field). Every command is listed in the [terminal syntax](https://axqua.eu/automation/terminal-syntax.html).

## Repository

| Folder | Content |
|---|---|
| `src/axqua/` | the library and the command `axqua` |
| `qgis_plugin/axqua/` | the QGIS plugin (GPL-2.0-or-later; it talks to `axqua` only through the command line) |
| `cases/` | the example cases and the template of a case; case data (`user-sources/`) and results (`axqua-case/`) are not tracked |
| `docs/` | the documentation (Sphinx); `docs/locale/` holds its German and French translations |
| `tests/`, `qgis_plugin/tests/` | the tests (`pytest tests qgis_plugin/tests`) |
| `scripts/` | tools: plugin archive, screenshots and UML diagrams of the documentation, data of the example case, backup of case data to Google Drive (`backup_cases_to_drive.sh`, rclone) |
| `deploy.sh` | publishes the documentation (below) |

## Documentation: build and publish

Build the English pages alone (Sphinx and the theme suffice; heavy dependencies are mocked):

```bash
pip install -r docs/requirements-docs.txt
sphinx-build -W -b html docs docs/_build/html
```

Build and publish all three languages on GitHub Pages:

```bash
mamba env create -f docs/i18n/environment.yml   # once: the environment axqua-docs (Sphinx + Argos Translate)
mamba activate axqua-docs
bash deploy.sh                                  # build into docs/_build/site and copy it into the gh-pages worktree
bash deploy.sh --push                           # ... then push main and gh-pages: the site is live within a minute
```

`deploy.sh` translates only the paragraphs that are new or changed since the last deploy (seconds; `--retranslate` translates everything again, about 4 minutes), commits the updated catalogs `docs/locale/**.po` on `main`, pushes `main` and then the branch `gh-pages`, which GitHub Pages serves at `https://axqua.eu`. It refuses to run with uncommitted changes. Read the Docs builds the English pages from `main` by itself. The glossaries of technical terms, corrections of a translation by hand and the other settings are described in [`docs/i18n/README.md`](https://github.com/sschwindt/aXqua/blob/main/docs/i18n/README.md).

## License

aXqua is BSD-3-Clause; the QGIS plugin is GPL-2.0-or-later. The data of the example cases are CC BY 4.0 (`cases/example-*/LICENSE.md`).
