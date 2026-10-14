# Notes

Working documents that are no longer instructions, kept because they still record
something: a decision, or a list of work nobody has done yet. They are **not**
documentation - `docs/` is - and nothing in the code reads them.

* **`plan-axqua-plugin.md`** - the plan the QGIS plugin was built from. Every phase of
  it has landed, so it is history, except for two parts worth keeping in view: its
  *Remaining* list (acceptance tests against real solvers, Windows and WSL launchers
  never run against a real install, QGIS 4.x unverified, the deferred
  `convergence`/`calibration` split) and its record of decisions that reversed the
  plan - chiefly that `openfoam.cell_size` was deliberately **not** folded into
  `mesh.channel_size`. Those decisions are also in `CLAUDE.md`, which is the live copy.
* **`Remote-Run-Info.md`** - why an SSH disconnect killed a run, from before the job
  system existed. Superseded by detached jobs (`axqua submit`), kept because the
  reasoning about SIGHUP and RDP is still correct.

`docs/restructuring-instructions.md` is **not** here on purpose: it is live, and
`tests/test_docs_outline.py` checks the documentation against the outline it prescribes.
