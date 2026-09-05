"""One tab per capability, built from what axqua said the case can do.

Each tab is the same shape - a status line, the reason an action is unavailable, the
per-kind options, and the three buttons - because the differences between capabilities are
*data* (see :mod:`.capability_tabs`), not layout. A hand-written tab per capability would
be six near-identical files that drift.
"""

from __future__ import annotations

from qgis.PyQt.QtWidgets import (QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                                 QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..compat import PARTIALLY_CHECKED, UNCHECKED
from ..core.runner_client import user_text
from ..core.tasks import run_async

#: Per-kind knobs worth exposing. Everything else stays in ``case-config.yml``, where it
#: is documented - a form that duplicated the whole config would be a second, undocumented
#: copy of it.
OPTION_FIELDS = {
    "steady": [("ncsize", "MPI processes", "int", 0)],
    "steady-3d": [("ncsize", "MPI processes", "int", 0)],
    "unsteady": [("ncsize", "MPI processes", "int", 0),
                 ("mode_3d", "Run in 3D", "bool", False)],
    "mesh-convergence": [("ncsize", "MPI processes", "int", 0),
                         ("auto_extend", "Refine automatically until converged", "bool",
                          False)],
    "vertical-convergence": [("ncsize", "MPI processes", "int", 0)],
    "calibration": [("prepare_only", "Prepare only (write inputs, do not launch)",
                     "bool", False)],
    "openfoam-run": [("n_processors", "MPI ranks", "int", 0),
                     ("reconstruct", "Reconstruct afterwards", "bool", True),
                     ("to_vtk", "Convert to VTK for QGIS", "bool", False)],
    "openfoam-build": [("pre_run", "Seed from a coarse TELEMAC pre-run", "bool", True)],
}


class CapabilityTab(QWidget):
    """A tab for one capability of one solver."""

    def __init__(self, view, context, parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.ctx = context
        self._fields: dict[str, QWidget] = {}
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
        self.state_label.setText(f"<b>{view.title}</b> ({view.solver}) - "
                                 f"{view.state_text}")
        self.reason_label.setText(view.reason)
        self.reason_label.setVisible(bool(view.reason))

        self._rebuild_options(view.submit_kind)
        self.options_box.setVisible(bool(self._fields))

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

    def _rebuild_options(self, kind: str | None) -> None:
        while self.options_form.rowCount():
            self.options_form.removeRow(0)
        self._fields = {}
        for key, label, dtype, default in OPTION_FIELDS.get(kind or "", []):
            if dtype == "int":
                widget = QSpinBox()
                widget.setRange(0, 4096)
                widget.setSpecialValueText("from the case config")
                widget.setValue(int(default))
            else:
                # Tri-state, and starting *partial*, so that a form the user never
                # touched sends nothing. A plain two-state box has no way to say "leave
                # it to the case config": every submit would carry
                # `--option reconstruct=true --option to_vtk=false` and silently
                # override case-config.yml with the widget's defaults, which is the
                # opposite of what the config file is for. The third state is that
                # sentence, and the tooltip says which way the case will go.
                widget = QCheckBox()
                widget.setTristate(True)
                widget.setCheckState(PARTIALLY_CHECKED)
                widget.setToolTip(
                    "Partly checked: leave this to case-config.yml"
                    f" (its default here is {'on' if default else 'off'}).")
            self.options_form.addRow(label, widget)
            self._fields[key] = widget

    def options(self) -> dict:
        """Only what the user actually asked for.

        Both widget types have an explicit "not set" state - 0 for the spin box, the
        partial state for the check box - and neither is sent. An option the plugin does
        not send is one ``case-config.yml`` still decides.
        """
        out = {}
        for key, widget in self._fields.items():
            if isinstance(widget, QSpinBox):
                if widget.value() > 0:      # 0 means "leave it to the config"
                    out[key] = widget.value()
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
        options = self.options() if kind == self.view.submit_kind else {}
        project = self.ctx.project
        run_async(
            f"aXqua: submitting {kind}",
            lambda: client.submit(config, kind, profile=project.profile or None,
                                  job_root=project.job_root or None,
                                  launcher=project.launcher, options=options),
            on_success=self._submitted,
            on_error=lambda exc: self.ctx.error(user_text(exc)), owner=self)

    def _submitted(self, data) -> None:
        job_id = (data or {}).get("job_id", "")
        self.ctx.info(f"Submitted {job_id}. It keeps running if you close QGIS.")
        self.ctx.job_submitted(job_id)

    def _load_results(self) -> None:
        self.ctx.load_latest_results(self.view.submit_kind or "")
