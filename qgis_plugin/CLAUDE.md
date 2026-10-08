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

**The tabs are generated from `axqua case-status --json`**, not hardcoded: `n/a` hides
a tab (OpenFOAM has no depth-averaged mode - a category error, not a gap), `no` shows it
disabled *with the reason*, and the buttons enable from `configured`/`built`/`run`. Adding
a capability to aXqua surfaces in the plugin with no plugin change - the point of the
registry. A capability the plugin has never heard of still gets a tab, titled from its own
name.

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
