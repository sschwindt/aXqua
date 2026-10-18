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

**The tabs are fixed** (`gui/sections.py`, pure data): Configuration, Case Setup,
Preprocessing, Simulations [Baseline (Telemac) | OpenFOAM | Mesh convergence |
Morphodynamics], Calibration & validation, Postprocessing [QGIS | ParaView | VisIt],
Batch-processing - the user's corrections of 2026-10-16 merged the former tabs Hydraulic
simulation, Mesh convergence and Morphodynamic simulation into *Simulations* (key
`hydraulics`, kept so that labels and placements did not move) and renamed its TELEMAC
sub-tab *Baseline (Telemac)*; pinned by `test_the_tabs_are_the_prescribed_ones_in_their_order`.
A sub-tab with `choose_solver` (Mesh convergence, Morphodynamics) is a
`section_pages.SolverChoicePage`: a combo box *Simulation program* (TELEMAC/OpenFOAM)
filters the boxes to one solver, with an empty text per solver (`dock.EMPTY` keyed
`(section, sub, solver)`). A sub-tab may name a docs page of its own (`SubSection.page`),
so Help on *Mesh convergence* still opens `usage/mesh-convergence`. **What a tab offers
still comes from `axqua case-status --json`**: `sections.placement` only says *where* a
capability is shown, as a box (`section_pages.SectionPage` wrapping the unchanged
`CapabilityTab`); `n/a` hides a box (OpenFOAM has no depth-averaged mode - a category
error, not a gap), `no` shows it disabled *with the reason*, and the buttons enable from
`configured`/`built`/`run`. A capability nobody has placed is shown under Simulations in the
sub-tab of its solver, so adding a capability to aXqua still surfaces with no plugin
change. The OpenFOAM mesh study is the kind `openfoam-mesh-convergence`
(`capability_tabs.BY_SOLVER`, options `n_processors`, `auto_extend`, `pre_run`). The build of
the 2D model has the Preprocessing tab to itself (its button is hidden in the Steady 2D box),
with the capability matrix as the "checkup" table. **The job list is below the tabs**, not
one of them: a job is submitted on one tab and its result is used on another.

**No project file any more** (the user, 2026-10-16: "a project is a case"). The Case Setup
tab has the boxes *Cases* (New, Add, Example, Remove from list; the list) and *Case
settings* (the active case, *Open case settings...*, Check, Open folder, Refresh). The list
of cases and the active one live in QSettings (`core/project.SETTINGS_KEY`,
`as_settings`/`from_settings`), written by `AxquaDock._remember_cases` on every change;
*Add case...* still accepts a `.axqua-prj`/`.hydromate-prj` and adds its cases
(`import_cases`). The project's `profile`/`job_root`/`launcher` overrides are gone: the
computer profile carries the job root and the launcher, so `submit` and `list` no longer
pass them. Plugin tests never touch the settings file of the test QGIS (an autouse
fixture stubs `_stored_cases` and `_remember_cases`; the test QgsApplication writes
`~/.local/share/profiles/default/Unknown Organization.ini`, not a real QGIS profile).

**Help opens the docs at the section of the tab that is showing** (`gui/help.py`): a copy built on this computer when `axqua docs --json` names one the default browser can read (the user's decision of 2026-10-18), else the published `https://axqua.eu/`, in `de/` or `fr/` when `QgsApplication.locale()` is German or French. The answer of `axqua docs` is asked once per session (`help.location()`, `configure()` for tests; `qgis_plugin/tests/conftest.py` presets the published English docs so that no test runs the computer's aXqua). A local address with a `#label` is opened through `_help/open.html` beside the copy, because `xdg-open` and Chromium drop the fragment of a file URL. Each section and sub-section names its page and `help-...` label, and a test reads the `.rst` files to assert every label is on its page. **No copy of the docs is built into the archive**: `scripts/build_plugin_zip.py` lost `build_help`, `--help-only` and `--no-help`, a leftover `help/` folder in a linked plugin folder is left out of the archive, and the `plugin_help` tag of `docs/conf.py` is gone. **Clean Up** sits left of Help in the corner widget of the tabs (`gui/cleanup_dialog.py`, `dock.open_cleanup`): one case at a time, the categories of `axqua cleanup <case> --json` as a check tree with the space each frees, the safe categories ticked and the time steps of results not, a choice of how many time steps a result keeps (`saving()` recomputes from `frames`/`header_bytes`/`frame_bytes`), a confirmation, and `cleanup --remove <ids> --keep ...` in a background task; a case with a job that is not terminal shows the reason and disables *Remove selected*. Its Help target is `sections.WINDOWS["cleanup"]` (`troubleshooting/tips-logfiles#help-cleanup`).

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
show|detect|write|check|locate --json`; the plugin still never imports `axqua`.
**A program has two rows: its installation folder and, below it, the file aXqua runs.**
Selecting the folder is enough: the editor asks `profile locate` and fills the row below
(`LOCATED`), once per folder, so that a row nobody changed never replaces what the row
below says. What `profile show` adds for display (`path`, `programs`) is stripped before
the editor keeps the profile (`NOT_IN_THE_FILE`); written back, it would end up in the
file as an entry nobody knows. The Configuration tab and the ParaView/VisIt pages read
the program from `programs`, which is the entry of the profile or the file found in its
folder. On a computer whose profile cannot be read, *Create profile...* says before
anything is saved that the file is kept (`UNREADABLE`), and the editor names the `.bak`
file afterwards.

**The case settings** (`gui/case_editor.py`, window title "Case settings: <file>")
build their pages from `axqua schema --json`: `pages` (from `core/schema_meta.PAGES`:
Project settings, Geodata, Liquid boundaries, Initial conditions, Mesh configuration, Wall
roughness, Hydraulic simulation, Morphodynamic simulation, TELEMAC, OpenFOAM, Calibration
and validation, Post-processing), each page with frames (`{title, info, keys}`); an older
aXqua that sends only `sections` gets one page per block, which the original tests still
exercise. The frames merge what the user asked merged: gain_lose on Liquid boundaries,
drying on Initial conditions (with an info button, `DRYING_INFO`), the `# ---- mesh ----`
group of the openfoam dataclass as *OpenFOAM lattice* on Mesh configuration
(`schema_meta._groups` reads the headings of the dataclass source), structures and
surfaces on OpenFOAM, ground_truth as *Measurements* on Calibration and validation. A
frame's non-essential fields are behind its own *Add setting...* (`_adders` keyed by frame
id: the page key, or `page:frame title`). **Required entries are bold aXqua purple**
(`gui/style.py`, `#2e014b`, `mark_required`): `required` (minimal case) and `required_when`
(conditions `{key, in}` evaluated against the live rows with defaults for unset ones, or
`{block}` = the page switch) come from the schema, are re-evaluated on every widget change
(`changed` -> `update_required`), mark the page in the list while one is empty, and are
reported as `axqua.config.incomplete` findings on Save unless aXqua's own check names the
subject. **Switches** (`SWITCHES`: telemac, openfoam, calibration) are a check box at the top
of a page; off grays out the block's rows wherever they are (the lattice on the mesh page
too) and drops the block from `values()`, on seeds the entry the schema names (an empty block
is never written). **The measurement table** (`MeasurementTable`) replaces the YAML rows of
`ground_truth.sources` and `calibration.validation`: one row per file, *Use for* calibration
or validation, a situation name and its discharge for validation; keys of a situation the
table does not show (inflows, prescribed_elevation, duration) are kept; untouched it returns
its input exactly. A source without `kind` is a FlowTracker source (the loader's default) and
stays without one; the *File* and *Positions* cells are read back; the discharge is one per
situation, so editing it in one row sets it in the other rows of that situation, and one row
with a discharge is enough (found by the review of 2026-10-17). A row that moves out of a situation other rows stay in, by its role, by `set_role` or by a name typed in, leaves the discharge behind with them (`entry["joined"]`, the situation its discharge is of); a row alone in its situation renames it and keeps the discharge, so a first name typed over the default and a misspelling corrected row by row lose nothing (review of 2026-10-18); a file of the calibration keeps none. A `multichoice` kind is a row of check boxes (postproc lists). **A row that
was not touched is written back exactly as it was read** - only a row whose text changed is
converted (`Row.value`); `Row.peek` reads without recording an error. A list that does not
parse keeps its previous content and gets a finding. Findings from `axqua check` land on the
row whose key is the `subject`, on the page in the list, and in the bottom list; a finding for
a setting without a row adds the row. Both editors share `editor_base.EditorDialog` (Save /
Cancel / Exit); the case settings add *Help* at the bottom left (the docs label of the page).
`axqua case write` keeps `<name>.bak` once, because writing goes through the data and loses
hand-written comments - the header says so.

**The installation wizards** (`gui/install_wizard.py`) are one window with three **tabs** - Settings, Check, Installation - that can be visited in any order (the user's order of 2026-10-16; they were pages of a stacked widget), with *Back*/*Next* walking through them; opening the Check tab with settings other than those of the plan shown checks again. They decide nothing themselves: the form is a small table (`FIELDS`), the Check tab shows what `axqua install plan` answers, and the Installation tab polls `axqua install status --tail` every 2 s. The *Install* button sits at the bottom-left of the Check tab (the user's docs say so). An installation is a detached process of the library (see `axqua/install/` in the root notes), so closing the wizard or QGIS does not stop it; the Configuration tab keeps a 15 s timer while one runs and its button then reads *Show the installation...*, which reopens the wizard on the Installation tab. **The plugin never sees a password**: missing system packages are shown under the heading `REQUIRED USER ACTIONS in TERMINAL:` (bold, aXqua purple `#2e014b`, `gui/style.py`) in a box of at least 120 px with purple text; *Run fixes* runs `axqua install packages --elevate` (pkexec, the desktop's dialog) or, without a desktop dialog, opens a terminal window with the command (`terminal_argv`) and re-checks every 15 s; after a pkexec round it re-checks and repeats while the missing list shrinks (`MAX_FIX_ROUNDS` 3). When the fixes stop making progress, or the check itself fails, *Report issue* appears (also on the Installation tab of a failed installation): `gui/report_issue.py` writes a report (home folder as `~`) into an editable window with *Open on GitHub* (prefilled new-issue URL; needs a GitHub account), *Send by e-mail* (`mailto:`; no account) and *Copy report*; both addresses are read from `metadata.txt` (`tracker`, `email`). After a successful installation the cancel button reads *Installation complete* on dark gray-green (`DONE_GREEN`) and closes the window. A wizard is not a tab, so its Help target lives in `sections.WINDOWS`. **The postprocessors are one question** (`viewers`: *ParaView and VisIt (recommended)*, *ParaView only*, and for OpenFOAM *None*), which `install_flags` turns into `--no-visit` / `--no-visualization`; the recommended answer is the first and the default.

**Installing again** (the user's order of 2026-10-16). On the Configuration tab the box is *Simulation & postprocessing software*; a found program is dark green (`FOUND_GREEN`), "Not found on this computer." bold aXqua purple. *Install ...* for a program whose `installed` entry is set asks "Software already installed. Continue with re-installation?"; *Yes* opens the wizard with `reinstall=True`, whose Settings tab starts with *Existing installation*: `replace` (remove, then install in the same folder), `continue` (the installers' own resume), `new` (a new folder, `<folder>-2`; sends no option). `axqua install overview` reports per target `existing: {folder, removable, removes}` (`recipes.existing_installation`, told from the program aXqua runs); only an installation an aXqua installer made is removable, so for an OpenFOAM or ParaView of the operating system only `new` is offered. The library side is `Options.reinstall` / `--reinstall continue|replace`: `replace` adds a first step `rm -rf` of exactly `recipes._removal(...)` (TELEMAC's `telemac-mascaret` subfolder, a folder with the OpenFOAM installer's marker, the `apps/visit*` folders), as administrator for an installation for all users, before the folder is created again. **The OpenFOAM installer refuses to continue a folder another version of it started** (its identity hashes its own files): the plan now computes that identity with the installer's own function (`IDENTITY_DRIVER`, `installer_identity`) and reports `axqua.install.installer_changed` before anything runs, unless `replace` is chosen. Every change to the vendored installer changes the identity, so after an update every existing OpenFOAM folder needs `replace` or a new folder.

**The ParaView and VisIt sub-tabs** (`section_pages.ProgramPage`) list the TELEMAC results
of the active case (`axqua export <case> --list`), export the selected ones in a background
task and start the program of the profile with the exported index file (`.pvd`; VisIt takes
`-o file.visit`) through `QProcess.startDetached`, wrapped in `launch()` so that a test can
replace it. One export serves both programs, so a page asks again whenever it is shown. The
box *OpenFOAM cases of the selected case* lists what `export --list` now also returns under
`openfoam` (`exportcli.openfoam_cases`: the free-surface and the fine-sediment case with
`case.foam` for ParaView and `system/controlDict` for VisIt) and starts the program with it.
**Map styles of a case**: a manifest entry may carry `extra.map_style` (TELEMAC sets it from
`postproc.map_style` on the depth layer, and leaves out maps not in `postproc.telemac_maps`);
`ResultItem.map_style` makes the loader call `apply_map_style` after the plain style. The
plugin's `styles.MAP_STYLES` keys must equal `axqua.config.MAP_STYLES` (a test reads the
library source).

**Validation** (`gui/validation_box.py`): the *Calibration & validation* tab is a
`CalibrationPage` - the capability boxes of a `SectionPage`, and below them a box for **one
validation situation**: a point layer (file dialog, or *From QGIS...* over the point layers
that are open), the discharge of every inflow of the built model (each labeled with the
discharge it carries in the calibrated case, from `axqua validation <case>`), and the
outflow level where the case prescribes one. *Save* merges the entry into
`calibration.validation` through `case read` / `case write`; *Validate* saves and submits
the `validation` kind. **There is no control that holds back calibration data, by the
user's rule** (measurements of one survey are not independent samples), and a test reads
every label of the box to keep it that way. `QFormLayout.setRowVisible` is Qt 6 only, so a
row is hidden through its field and `labelForField`.

**The example case** (`gui/example_dialog.py`): *Example case...* on the Case Setup tab
opens a window that shows what `axqua example list` answers, asks for a folder, and has
`axqua example get` put the example there; the case is then added to the list. The
window decides nothing (which examples exist and where they come from is the library's
answer) and says the path only in the folder field: a long path in the status line was
cut off in the first headless run. Its Help target is in `sections.WINDOWS`.

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
wetting/drying artifact in a nearly-dry cell and one such node would flatten the map.
Everything else points at Layer Symbology - a plausible-looking wrong scale is harder to
notice than an obviously default one. **Four predefined map styles** (`styles.MAP_STYLES`,
`result_loader.apply_map_style`, one choice and one button on *Postprocessing > QGIS*,
applied to the layer selected in QGIS) draw the velocity as arrows or streamlines on top
of the water depth or of the velocity magnitude. **Drawing a real result for them found
what no test had**: the member that colors vectors by their magnitude was looked for on
`QgsMeshRendererVectorSettings`, where it does not exist (it is
`QgsInterpolatedLineColor.ColoringMethod`), so `compat.VECTOR_COLOR_RAMP` was always
`None` and every arrow ever drawn was black; an arrow stood on every mesh node, dry ones
included, because an arrow has a smallest length. Now: on a grid of the map
(`ARROW_SPACING` pixels), none below `VECTOR_MINIMUM` (0.05 m/s), colored by magnitude in
warm colors over the blue depth and dark over the velocity fill (a vector in the color of
its background cannot be seen). To look at a style without a display, render the layer
with `QgsMapRendererParallelJob`.
**`setStaticScalarDatasetIndex` is not in the Python interface of QGIS 3.44** (only the getter is; the setter is left out of the bindings), which had two consequences nobody had seen, because no test rendered a frame: the movie export called it and stopped at its first frame, and **a loaded result showed its first time step**, which of a steady run is the initial state with standing water and no velocity. Two different remedies, and the first attempt at the second was wrong. *A movie frame* is selected through the time its map is drawn for (`result_loader.time_range_of`; a layer whose file names no date gets a reference time first, `ensure_reference_time`, as QGIS gives one to layers added through its own dialogs and not to one a plugin adds). *A loaded result* gets its last step as the step it draws on a map without a time (`show_last_time_step(layer)`): that is a property of the layer, and since Python cannot set it, the description of the layer that a project file stores is written out (`writeLayerXml`), its element `static-active-dataset` changed, and read back (`readLayerXml`; id, style, CRS and extra datasets survive, checked by pictures). The loader does it for every mesh layer it adds or reuses, so the job list and the Processing algorithm both get it. **Do not set the time of the map canvas for this**, which the first version did: a print layout, a reopened project and the map after temporal navigation is switched off still showed the first step, the Temporal Controller was left with a range of 1 ms and one frame, and loading a job that ends earlier moved the other results to an intermediate step (all measured by the review of 2026-10-12). Tests of anything a map shows compare rendered pictures (`_picture` in `tests/test_plugin_qgis.py`), not what a layer says it is set to. Processing algorithms **validate, submit and return**;
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
the Unsteady 2D tab, next to a tab that said it could not be done). **A box shows the
options of its build too, where the build is a job kind of its own** (`_build_fields`):
only the options of the run were shown, so *Use TELEMAC warm-up*, which the documentation
outline prescribes for the OpenFOAM sub-tab and which is an option of `openfoam-build`,
was in the table and nowhere on the screen. `OPTION_FIELDS` has a
`choice` type for the 3D `variant`; like the other two types it has a "not set" state that
is not sent. Loading a 3D job: the depth-averaged companion is the layer, styled by the
vector group `VELOCITY` because it has no `SCALAR VELOCITY`; the volume file is not offered.

**One name, two things: `BY_SOLVER`** (same file). Morphodynamics is the coupling with GAIA
for TELEMAC, which `PART_OF` explains as part of the hydraulic runs, and a simulation of its
own for OpenFOAM, fine sediment over a bed that stays in place, with the kinds
`openfoam-sediment-build` and `openfoam-sediment`. The tables above are keyed by the
capability alone, so the entry for (solver, capability) is looked up first: title, build
kind and run kind, and `PART_OF` does not apply where there is one. The batch has the same
trap: a `Step` names its `solver` where the capability it needs exists for two, or a case
that couples GAIA would be offered the fine sediment steps. Two things the new box needed:
a number type `count` in `OPTION_FIELDS` (the spin box of `int` ends at 4096, which is
processor cores and not iterations), and **a style for a polygon result**
(`result_loader.color_by_attribute`, the manifest entry's `style: graduated` with its
`variable`): the bed is a GeoPackage of one polygon per column, and loaded without a style
it showed the outline of the model in one color. Seven classes of equal count and no
outlines, because rates of deposition span orders of magnitude and outlines at the scale
of a reach cover the fills. Its test counts the colors that cover more than 2 % of a
rendered picture.

**Two layout defects that only the pictures showed** (2026-10-13, when they were taken
again for the new sub-tab). The step list of the batch was 150 pixels high, which seven
steps filled; with nine, the calibration and the validation were below the fold. It is as
high as its rows now and has no horizontal scroll bar, which in a narrow panel covered a
step (found by the test, whose dock is 134 pixels wide). And **the Configuration tab did
not scroll**: in a panel lower than its three boxes need (about 1,040 pixels), Qt squeezed
the rows, and the second line of an entry of the profile ("installed in ...") was cut off
at its middle, in the panel and in the published picture. Giving the labels a minimum
height made it worse, because the squeeze then took the missing pixels from them all the
same; the tab is in a scroll area now, like the pages of the workflow, and its picture is
taken of a panel high enough for it, with the added height given to the tab through the
splitter (the job list would take it otherwise).

The plugin folder and the library are both called `axqua`, which is right in both
places but means they cannot share a pytest process - `qgis_plugin/tests/conftest.py` loads
the plugin under the alias `axqua_plugin` so one `pytest` at the repo root runs both
suites.
