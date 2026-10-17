"""One tab per capability, built from what axqua said the case can do.

Each tab is the same shape - a status line, the reason an action is unavailable, the
per-kind options, and the three buttons - because the differences between capabilities are
*data* (see :mod:`.capability_tabs`), not layout. A hand-written tab per capability would
be six near-identical files that drift.
"""

from __future__ import annotations

from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox,
                                 QHBoxLayout, QLabel, QPushButton, QSpinBox,
                                 QVBoxLayout, QWidget)

from ..compat import PARTIALLY_CHECKED, UNCHECKED
from ..core.runner_client import user_text
from ..core.tasks import run_async

#: Per-kind knobs worth exposing. Everything else stays in the case file, where it
#: is documented - a form that duplicated the whole config would be a second, undocumented
#: copy of it.
OPTION_FIELDS = {
    "steady": [("ncsize", "MPI processes", "int", 0)],
    # The first choice carries no value and is not sent: it is the default of the kind.
    "steady-3d": [("ncsize", "MPI processes", "int", 0),
                  ("variant", "Variant", "choice",
                   (("", "hydrostatic (checks the discharge balance)"),
                    ("hydrodyn", "non-hydrostatic")))],
    # 2D or 3D is decided by the tab (capability_tabs.FIXED_OPTIONS), not by a box.
    "unsteady": [("ncsize", "MPI processes", "int", 0)],
    "mesh-convergence": [("ncsize", "MPI processes", "int", 0),
                         ("auto_extend", "Refine automatically until converged", "bool",
                          False)],
    "vertical-convergence": [("ncsize", "MPI processes", "int", 0)],
    "calibration": [("prepare_only", "Prepare only (write inputs, do not launch)",
                     "bool", False)],
    "openfoam-run": [("n_processors", "MPI ranks", "int", 0),
                     ("reconstruct", "Reconstruct afterwards", "bool", True),
                     ("to_vtk", "Convert to VTK for QGIS", "bool", False)],
    # The name the documentation prescribes for it; the setting is openfoam.pre_run.
    "openfoam-build": [("pre_run", "Use TELEMAC warm-up", "bool", True)],
    "openfoam-sediment-build": [("pre_run", "Use TELEMAC warm-up", "bool", True)],
    # "count" is a whole number without the limit of the processor cores
    "openfoam-sediment": [("iterations", "Iterations at most", "count", 0)],
    "openfoam-mesh-convergence": [
        ("n_processors", "MPI ranks", "int", 0),
        ("auto_extend", "Refine automatically until converged", "bool", False),
        ("pre_run", "Use TELEMAC warm-up", "bool", True)],
}


class CapabilityTab(QWidget):
    """A tab for one capability of one solver."""

    def __init__(self, view, context, parent=None, *, titled: bool = True) -> None:
        super().__init__(parent)
        #: False inside a box that already carries the title of the capability
        self.titled = titled
        self.view = view
        self.ctx = context
        self._fields: dict[str, QWidget] = {}          # options of the run
        self._build_fields: dict[str, QWidget] = {}    # options of a build of its own
        self._build()
        self.apply(view)

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        self.state_label = QLabel("")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)

        self.reason_label = QLabel("")
        self.reason_label.setWordWrap(True)
        self.reason_label.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.reason_label)

        self.options_box = QGroupBox("Options")
        self.options_form = QFormLayout(self.options_box)
        layout.addWidget(self.options_box)

        buttons = QHBoxLayout()
        self.build_button = QPushButton("Build")
        self.build_button.clicked.connect(lambda: self._submit(self.view.build_kind))
        buttons.addWidget(self.build_button)

        self.submit_button = QPushButton("Submit")
        self.submit_button.clicked.connect(lambda: self._submit(self.view.submit_kind))
        buttons.addWidget(self.submit_button)

        self.results_button = QPushButton("Load results")
        self.results_button.clicked.connect(self._load_results)
        buttons.addWidget(self.results_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)

    # -- state --------------------------------------------------------------------
    def apply(self, view) -> None:
        """Re-render from a fresh capability matrix."""
        self.view = view
        self.state_label.setText(
            f"<b>{view.title}</b> ({view.solver}) - {view.state_text}" if self.titled
            else f"State: {view.state_text}")
        self.reason_label.setText(view.reason)
        self.reason_label.setVisible(bool(view.reason))

        self._rebuild_options(view.submit_kind, view.build_kind)
        self.options_box.setVisible(bool(self._fields or self._build_fields))

        self.build_button.setVisible(bool(view.build_kind))
        self.build_button.setEnabled(view.can_submit)
        self.submit_button.setEnabled(bool(view.submit_kind) and view.can_submit)
        if view.needs_build:
            self.submit_button.setToolTip(
                "Nothing is built yet for this capability - build it first, or submit "
                "the build and this run in sequence.")
        else:
            self.submit_button.setToolTip("")
        self.results_button.setEnabled(view.has_results)

    def _rebuild_options(self, kind: str | None, build_kind: str | None = None) -> None:
        """The options of the run and, where the build is a job kind of its own, the
        options of the build before them.

        The build of the OpenFOAM model has one, *Use TELEMAC warm-up*. Only the options
        of the run were shown, so that check box did not exist in the plugin.
        """
        while self.options_form.rowCount():
            self.options_form.removeRow(0)
        self._fields, self._build_fields = {}, {}
        groups = [(kind, self._fields)]
        if build_kind and build_kind != kind:
            groups.insert(0, (build_kind, self._build_fields))
        for which, fields in groups:
            self._add_options(which, fields)

    def _add_options(self, kind: str | None, fields: dict) -> None:
        for key, label, dtype, default in OPTION_FIELDS.get(kind or "", []):
            if dtype in ("int", "count"):
                widget = QSpinBox()
                widget.setRange(0, 4096 if dtype == "int" else 10_000_000)
                if dtype == "count":
                    widget.setSingleStep(500)
                widget.setSpecialValueText("as set for the case")
                widget.setValue(int(default))
            elif dtype == "choice":
                widget = QComboBox()
                for value, text in default:
                    widget.addItem(text, value)
            else:
                # Tri-state, and starting *partial*, so that a form the user never
                # touched sends nothing. A plain two-state box has no way to say "leave
                # it to the case config": every submit would carry
                # `--option reconstruct=true --option to_vtk=false` and silently
                # override the case file with the widget's defaults, which is the
                # opposite of what the config file is for. The third state is that
                # sentence, and the tooltip says which way the case will go.
                widget = QCheckBox()
                widget.setTristate(True)
                widget.setCheckState(PARTIALLY_CHECKED)
                widget.setToolTip(
                    "Partly checked: leave this to the case file"
                    f" (its default here is {'on' if default else 'off'}).")
            self.options_form.addRow(label, widget)
            fields[key] = widget

    def options(self, *, build: bool = False) -> dict:
        """Only what the user actually asked for, for the run or for the *build*.

        Both widget types have an explicit "not set" state - 0 for the spin box, the
        partial state for the check box - and neither is sent. An option the plugin does
        not send is one the case file still decides.
        """
        out = {}
        for key, widget in (self._build_fields if build else self._fields).items():
            if isinstance(widget, QSpinBox):
                if widget.value() > 0:      # 0 means "leave it to the config"
                    out[key] = widget.value()
            elif isinstance(widget, QComboBox):
                if widget.currentData():    # the first entry is the default: not sent
                    out[key] = widget.currentData()
            elif isinstance(widget, QCheckBox):
                state = widget.checkState()
                if state != PARTIALLY_CHECKED:
                    out[key] = state != UNCHECKED
        return out

    # -- actions ------------------------------------------------------------------
    def _submit(self, kind: str | None) -> None:
        if not kind:
            return
        client = self.ctx.client_or_warn()
        config = self.ctx.active_case_or_warn()
        if client is None or config is None:
            return
        # The options of the run belong to the run, and a build with the same kind
        # (unsteady 2D) gets them too. A build of another kind gets its own.
        if kind == self.view.submit_kind:
            options = {**self.options(), **self.view.fixed_options}
        else:
            options = self.options(build=True)
        run_async(
            f"aXqua: submitting {kind}",
            lambda: client.submit(config, kind, options=options),
            on_success=self._submitted,
            on_error=lambda exc: self.ctx.error(user_text(exc)), owner=self)

    def _submitted(self, data) -> None:
        job_id = (data or {}).get("job_id", "")
        self.ctx.info(f"Submitted {job_id}. It keeps running if you close QGIS.")
        self.ctx.job_submitted(job_id)

    def _load_results(self) -> None:
        self.ctx.load_latest_results(self.view.submit_kind or "")
