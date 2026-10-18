# aXqua

**Automated setup, persistent execution and calibration of models of free-surface water bodies: rivers, lakes, reservoirs and hydraulic structures.**

aXqua builds a **TELEMAC-2D/3D** case (optionally with **GAIA**) or an **OpenFOAM** free-surface case from geodata, runs it as a job that outlives the shell or QGIS that started it, and calibrates it with [HydroBayesCal](https://github.com/Ecohydraulics/hydrobayescal) (surrogate-assisted Bayesian calibration with quantified uncertainty). A **QGIS plugin** drives the whole workflow; everything also works from a terminal.

**Documentation:** [axqua.eu](https://axqua.eu) (English), [axqua.eu/de](https://axqua.eu/de/) (Deutsch), [axqua.eu/fr](https://axqua.eu/fr/) (Français), and [axqua.readthedocs.io](https://axqua.readthedocs.io) (English).

## Install

```bash
mamba env create -f environment.yml      # the environment axqua-env
mamba activate axqua-env
pip install -e .
axqua profile init                       # find TELEMAC, OpenFOAM, ParaView, VisIt on this computer
```

The QGIS plugin (QGIS 3.44 or newer) is `qgis_plugin/axqua/`; for development link it into a QGIS profile, or start `scripts/qgis_dev.sh`, which uses a QGIS of its own. The installation of the plugin and of the simulation software is described in the [documentation](https://axqua.eu/installation/index.html).

## Quick start

```bash
axqua example get --folder ~/aXqua-examples    # a small Isar case with its data (6 MB)
axqua submit <case-file> --kind preprocessing  # build the TELEMAC model, as a job
axqua submit <case-file> --kind steady         # the steady 2D run (waits for the build)
axqua list                                     # the jobs, with state and progress
axqua cleanup <case-file>                      # what the case could do without (frees disk space)
```

The example case and its guide are in [`cases/example-isar/`](cases/example-isar/README.md). Every command is listed in the [terminal syntax](https://axqua.eu/automation/terminal-syntax.html).

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
bash deploy.sh                                  # build into docs/_build/site and copy it into the gh-pages worktree
bash deploy.sh --push                           # ... then push main and gh-pages: the site is live within a minute
```

`deploy.sh` translates only the paragraphs that are new or changed since the last deploy (seconds; `--retranslate` translates everything again, about 4 minutes), commits the updated catalogs `docs/locale/**.po` on `main`, pushes `main` and then the branch `gh-pages`, which GitHub Pages serves at `https://axqua.eu`. It refuses to run with uncommitted changes. Read the Docs builds the English pages from `main` by itself. The glossaries of technical terms, corrections of a translation by hand and the other settings are described in [`docs/i18n/README.md`](docs/i18n/README.md).

## Domains

GitHub Pages serves one domain per repository, so **axqua.eu** is the site and the other domains forward to it. Each forwarding should be permanent (301) and keep the path, so that `axqua.de/usage/preprocessing.html` opens `axqua.eu/de/usage/preprocessing.html`.

| Domain | Lands on | Where | Setting |
|---|---|---|---|
| axqua.eu | English | Spaceship | DNS: A records `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`, AAAA records `2606:50c0:8000::153` to `2606:50c0:8003::153`, CNAME `www` to `sschwindt.github.io` (done). GitHub repository, *Settings > Pages*: custom domain `axqua.eu`, tick *Enforce HTTPS*. |
| axqua.de | German | Spaceship | *Domain list > axqua.de > URL redirect*: target `https://axqua.eu/de`, permanent (301). Spaceship keeps the path and serves HTTPS itself. |
| axqua.fr | French | Dynadot | target `https://axqua.eu/fr`, see below |
| axqua.ch | German | Dynadot | target `https://axqua.eu/de`, see below; and in Read the Docs, *Admin > Domains*, delete `axqua.ch`, so that `axqua.readthedocs.io` serves its own pages instead of forwarding to axqua.ch |

**Dynadot (axqua.fr, axqua.ch).** Dynadot's own forwarding (*DNS Settings > Dynadot Forwarding*, 301, to the target above) works for addresses typed without `https://`, but it drops the path and answers `https://axqua.fr` with a certificate for `*.dynadot.com`, which browsers show as a security warning. To forward with HTTPS and the path, put the domain on a free [Cloudflare](https://www.cloudflare.com) account: add the domain there, enter the two Cloudflare name servers at Dynadot (*DNS Settings > Name Servers*), create the proxied records `A @ 192.0.2.1` and `A www 192.0.2.1` (an address that only exists so that Cloudflare answers), and add a redirect rule (*Rules > Redirect Rules*) for all requests: dynamic, expression `concat("https://axqua.eu/fr", http.request.uri.path)` (for axqua.ch: `.../de`), status 301, preserve the query string. Cloudflare issues the certificate itself.

Test a forwarding with `curl -sI https://axqua.fr/usage/preprocessing.html`: the answer has to be `301` with `location: https://axqua.eu/fr/usage/preprocessing.html`. The pages themselves never redirect a German or French address, so a visitor who comes through axqua.de, axqua.ch or axqua.fr stays in that language; the button at the top right switches it.

## Migrating from hydromate

The project was formerly released as `hydromate` and renamed for legal reasons; existing cases keep working, and the old names below are still read with a notice.

| was | is now |
|---|---|
| `pip install hydromate` | `pip install git+https://github.com/sschwindt/aXqua.git` (`pip install aXqua` once it is on PyPI) |
| `import hydromate` | `import axqua` |
| `hydromate-case/` | `axqua-case/` (an existing `hydromate-case/` is used until it is renamed) |
| `HYDROMATE_JOB_ROOT` and the other variables | `AXQUA_JOB_ROOT` and so on |
| `<name>.hydromate-prj` | `<name>.axqua-prj` (the plugin's *Add case...* still reads both) |

## License

aXqua is BSD-3-Clause; the QGIS plugin is GPL-2.0-or-later. The data of the example case are CC BY 4.0 (`cases/example-isar/LICENSE.md`). Coordinates are EPSG:25832 throughout; see `CLAUDE.md` for the notes of the developers.
