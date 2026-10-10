from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QSpinBox, QToolButton, QVBoxLayout, QWidget

from geostitch.models import SolverSettings


class SolverToolbarControl(QWidget):
    def __init__(self, run_action: QAction, settings_action: QAction, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 40)
        self.run_button = QToolButton(self)
        self.run_button.setDefaultAction(run_action)
        self.run_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.run_button.setIconSize(QSize(24, 24))
        self.run_button.setAutoRaise(True)
        self.run_button.setGeometry(self.rect())
        self.run_button.setStyleSheet("QToolButton { padding-right: 8px; padding-bottom: 8px; }")
        self.run_button.setAccessibleName(run_action.text())
        self.settings_button = QToolButton(self)
        self.settings_button.setDefaultAction(settings_action)
        self.settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.settings_button.setIconSize(QSize(14, 14))
        self.settings_button.setAutoRaise(True)
        self.settings_button.setStyleSheet(
            "QToolButton { background: transparent; border: none; padding: 0; border-radius: 9px; }"
            "QToolButton:hover, QToolButton:focus { background: rgba(128, 128, 128, 45); }"
            "QToolButton:pressed { background: rgba(128, 128, 128, 75); }"
        )
        self.settings_button.setGeometry(26, 22, 18, 18)
        self.settings_button.setAccessibleName(settings_action.text())
        self.settings_button.raise_()


class SolverSettingsDialog(QDialog):
    settings_applied = Signal(object)

    def __init__(self, stage: str, settings: SolverSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Alignment settings" if stage == "alignment" else "Georeferencing settings")
        self.setMinimumWidth(380)
        self.run_requested = False
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.tolerance_spin = QDoubleSpinBox()
        self.tolerance_spin.setDecimals(8 if stage == "georeference" else 6)
        self.tolerance_spin.setRange(1e-8 if stage == "georeference" else 1e-6, 1_000_000_000)
        self.tolerance_spin.setSuffix(" scene units" if stage == "alignment" else " CRS units")
        self.tolerance_spin.setValue(settings.tolerance)
        self.iterations_spin = QSpinBox()
        self.iterations_spin.setRange(1, 10000)
        self.iterations_spin.setValue(settings.max_iterations)
        self.auto_apply_checkbox = QCheckBox("Apply automatically when target is reached")
        self.auto_apply_checkbox.setChecked(settings.auto_apply)
        self.auto_apply_checkbox.setToolTip("Uncheck to compute a preview without changing image transforms.")
        form.addRow("Target maximum residual", self.tolerance_spin)
        form.addRow("Maximum iterations", self.iterations_spin)
        form.addRow(self.auto_apply_checkbox)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.run_button = self.buttons.addButton("Run", QDialogButtonBox.ButtonRole.AcceptRole)
        self.run_button.setDefault(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply_settings)
        self.run_button.clicked.connect(self.run)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def apply_settings(self) -> None:
        self.settings_applied.emit(SolverSettings(
            self.tolerance_spin.value(), self.iterations_spin.value(), self.auto_apply_checkbox.isChecked(),
        ))

    def run(self) -> None:
        self.apply_settings()
        self.run_requested = True
        self.accept()