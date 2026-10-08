# The QGIS plugin (`qgis_plugin/axqua/`)

A GPL-2.0-or-later QGIS 3.44+/4.x plugin (aXqua itself stays BSD-3-Clause, which is
GPL-compatible). **It never imports `axqua`**: QGIS ships its own Python and aXqua
needs gmsh, rasterio, geopandas and a solver environment, so the boundary is a subprocess
and a JSON document and either side can be reinstalled alone. A test asserts this by
reading the source.

`compat.py` is the *only* module that knows QGIS 3.44 and QGIS 4 apart. QGIS 4.0 is
released and **Qt6-only**; compatibility is declared with `qgisMaximumVersion=4.99` and the
old `supportsQt6` flag was removed from core and is ignored. So: `qgis.PyQt` imports only,
`exec()` not `exec_()`, `QRegularExpression` not `QRegExp`, enums resolved **by name**
(`enum_value(Qt, "MatchFlag.MatchExactly", "MatchExactly")`) rather than branched on a
version, and **no `.qrc`/`pyrcc`** - a compiled resource module is built against one Qt
major version and is the most common reason a plugin loads on one QGIS and not the other.

**The tabs are fixed and follow the documentation** (`gui/sections.py`, pure data):
Configuration, Case Setup, Preprocessing, Hydraulic simulation [Telemac | OpenFOAM], Mesh
convergence, Morphodynamic simulation [Telemac | OpenFOAM], Calibration & validation,
Postprocessing [QGIS | ParaView | VisIt], Batch-processing - the order prescribed by
`docs/restructuring-instructions.md` and pinned by `test_the_tabs_are_the_prescribed_ones`.
**What a tab offers still comes from `axqua case-status --json`**: `sections.placement`
only says *where* a capability is shown, as a box (`section_pages.SectionPage` wrapping the
unchanged `CapabilityTab`); `n/a` hides a box (OpenFOAM has no depth-averaged mode - a
category error, not a gap), `no` shows it disabled *with the reason*, and the buttons
enable from `configured`/`built`/`run`. A capability nobody has placed is shown under
Hydraulic simulation in the sub-tab of its solver, so adding a capability to aXqua still
surfaces with no plugin change. The build of the 2D model has the Preprocessing tab to
itself (its button is hidden in the Steady 2D box), with the capability matrix as the
"checkup" table. **The job list is below the tabs**, not one of them: a job is submitted on
one tab and its result is used on another.

**Help opens the docs at the section of the tab that is showing** (`gui/help.py`). Each
section and sub-section names its page and `help-...` label, and a test reads the `.rst`
files to assert every label is on its page. `scripts/build_plugin_zip.py` builds the docs
into `<plugin>/help/html` and writes **one redirect page per tab key**
(`help/hydraulics-telemac.html`), because a `file://` address does not reliably keep its
`#anchor` through the desktop opener. The bundled build passes `-t plugin_help`, which
drops `sphinx.ext.viewcode` in `docs/conf.py`, and prunes every font format but woff2:
25 MiB became 8.4 (3.7 MB zipped; the repository limit is 25). A linked dev folder has no
bundle unless `--help-only` was run (it is gitignored), and Help then opens Read the Docs.

**Findings and triangles** (`gui/findings.py`): what `axqua profile check` returns, shown
as an orange (warning) or dark-red (error) triangle at the row whose dotted key equals the
finding's `subject`, on the tab title (`dock.mark`), and in a list. A click opens the
message, its remedy and a button into `troubleshooting/{warnings,errors}.html#<code>`;
`tests/test_profile.py` asserts every code the check can emit has that anchor. **Findings
never disable anything**: the profile editor (`gui/profile_editor.py`) saves whatever its
rows say and *then* shows the check. Its buttons are the three the instructions name -
Save (write, check, stay open), Cancel (discard since the last save), Exit (ask if
unsaved) - and `values()` starts from the loaded dictionary, so an entry without a row
(`solvers.telemac.overrides`) survives a save. The editor talks to `axqua profile
show|detect|write|check --json`; the plugin still never imports `axqua`.

**The case editor** (`gui/case_editor.py`) builds its forms from `axqua schema --json`
(`src/axqua/core/schema_meta.py`: derived from a fully dumped default case, the dataclass
annotations and the comments beside the fields, so it cannot miss a setting; `CURATED`
gives the ~47 settings a modeler meets first a plain label and unit). A block's page shows
the essential settings plus whatever the file already has; the rest is one *Add setting...*
away. **A row that was not touched is written back exactly as it was read** - only a row
whose text changed is converted (`Row.value`), so opening and saving cannot turn `3` into
`"3"`; the headless run asserts `values() == data` on the real example. A list that does not
parse keeps its previous content and gets a finding, so one typing error does not cost the
rest of the form. Findings from `axqua check` (`core/casecheck.py`, which runs **every**
step of `Config.checks()` instead of stopping at the first, and skips the steps about the
computer) land on the row whose key is the `subject`, on the block in the list, and in the
bottom list; a finding for a setting without a row adds the row. Both editors share
`editor_base.EditorDialog` (Save / Cancel / Exit, the unsaved-changes question).
`axqua case write` keeps `<name>.bak` once, because writing goes through the data and
loses hand-written comments - the editor's header says so.

**The installation wizards** (`gui/install_wizard.py`) are one window with three pages -
Settings, Check, Installation - and decide nothing themselves: the form is a small table
(`FIELDS`), the Check page shows what `axqua install plan` answers, and the Installation
page polls `axqua install status --tail` every 2 s. An installation is a detached process
of the library (see `axqua/install/` in the root notes), so closing the wizard or QGIS
does not stop it; the Configuration tab keeps a 15 s timer while one runs and its button
then reads *Show the installation...*, which reopens the wizard on page 3. **The plugin
never sees a password**: missing system packages are shown with their command (*Copy the
command*), and *Install the packages...* calls `axqua install packages --elevate`, which
lets the desktop's own dialog ask. A wizard is not a tab, so its Help target lives in
`sections.WINDOWS` and gets a redirect page like every tab key.

**Batch** (`core/batch.py`, pure): the steps are job kinds in workflow order; *Submit the
ticked steps* hands them over in ONE background call so their queue tickets are written in
that order (see the workspace queue in `src/axqua/jobs/CLAUDE.md`), and *Generate
batch-processing script* writes the `run_step` loop that also stops at a failed step.

**Polling policy** (`core/job_model.py`): only visible non-terminal rows; **stat before
parse** (compare `status.json`'s mtime and skip the read - most polls find nothing, so this
is the single biggest saving); ~2 s visible / ~30 s hidden; and the timer **stops** when
nothing is running. `JobTable.replace` preserves in-memory progress, because `list` reports
state but not the iteration count and a naive replace would blank the column on every
refresh and make a running job look stalled.

`.axqua-prj` is a **thin pointer** and carries **no simulation status** - the runner
writes status while QGIS is closed, so a copy here would be stale and two open windows
would fight over it. A file carrying one from an older draft has it *ignored*, not trusted.

Results load from the manifest, referenced where they are and never copied. Water depth is
**transparent below the case's own minimum depth** (5 mm of water on a bed with ks 0.05-0.5
m stands *inside* the grain roughness rather than flowing over it, and aXqua's own
reports use the same filter, so the map and the report agree); velocity is capped at 5 m/s
**with a warning**, because a depth-averaged result above that is nearly always a
wetting/drying artefact in a nearly-dry cell and one such node would flatten the map.
Everything else points at Layer Symbology - a plausible-looking wrong scale is harder to
notice than an obviously default one. Processing algorithms **validate, submit and return**;
they never execute.

**Two bugs found only by running it under real PyQGIS**, both of which present as silence
rather than as an error: `QgsTask` does **not** take a Python reference, so a task whose
only reference was a local was garbage collected before it ran - no tabs, an empty
dashboard, no error anywhere (`core/tasks._IN_FLIGHT` now holds them); and
`QListWidget.findItems` needs a real `Qt.MatchFlag` where Qt5 accepted a bare int.

**QGIS skips a plugin whose `metadata.txt` does not parse, without a message.** `findPlugins`
in `qgis/utils.py` reads it with `configparser` and drops the plugin on any error, so it is
simply absent from the Plugin Manager. A changelog line in the first column (`0.2.0`, not a
continuation line) did this for a month while every test passed, because the tests import
the plugin directly and `scripts/build_plugin_zip.py` read the file with a more forgiving
parser of its own. Both now read it the way QGIS does (`qgis_cannot_read`,
`test_qgis_can_read_the_metadata`). **The suite cannot tell whether QGIS loads the plugin;
only QGIS can.** `scripts/qgis_dev.sh` starts the QGIS of a conda environment (`qgis-dev`,
3.44) with a user profile of its own (`axqua-dev`) that links this checkout's plugin folder
and enables it; with `QT_QPA_PLATFORM=offscreen` and `--code <script>` the same command
drives the real plugin headless (add a case, grab each tab with `widget.grab()`, load a
result, `os._exit`), which is how the metadata defect and the missing layer CRS were found.

**No tab claims "no job kind" any more, and three small tables say why**
(`gui/capability_tabs.py`). `PART_OF`: gain-lose and morphodynamics are blocks of the case
file that run *with* the steady and unsteady simulations, so their tabs show the state from
the capability matrix and say there is nothing to submit. `FIXED_OPTIONS`: the Unsteady 3D
tab is the `unsteady` kind with `mode_3d` set (the check box that did this used to sit on
the Unsteady 2D tab, next to a tab that said it could not be done). `OPTION_FIELDS` has a
`choice` type for the 3D `variant`; like the other two types it has a "not set" state that
is not sent. Loading a 3D job: the depth-averaged companion is the layer, styled by the
vector group `VELOCITY` because it has no `SCALAR VELOCITY`; the volume file is not offered.

The plugin folder and the library are both called `axqua`, which is right in both
places but means they cannot share a pytest process - `qgis_plugin/tests/conftest.py` loads
the plugin under the alias `axqua_plugin` so one `pytest` at the repo root runs both
suites.
