from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox, QMessageBox, QSpinBox, QToolButton, QVBoxLayout, QWidget

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
        self.stage = stage
        self.setWindowTitle("Alignment settings" if stage == "alignment" else "Georeferencing settings")
        self.setMinimumWidth(480)
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
        self.model_combo = QComboBox()
        for label, value in (("Translation", "translation"), ("Rigid (translation + rotation)", "rigid"), ("Similarity (translation + rotation + scale)", "similarity")):
            self.model_combo.addItem(label, value)
        self.model_combo.setCurrentIndex(self.model_combo.findData(settings.transform_model))
        self.model_combo.setToolTip("Translation preserves initial rotation and scale. Rigid preserves initial scale. Georeferencing fits linked groups as rigid units.")
        self.loss_combo = QComboBox()
        for label, value in (("Least squares", "linear"), ("Soft L1", "soft_l1"), ("Huber", "huber"), ("Cauchy", "cauchy")):
            self.loss_combo.addItem(label, value)
        self.loss_combo.setCurrentIndex(self.loss_combo.findData(settings.loss))
        self.loss_combo.setToolTip("Robust losses reduce outlier influence. The accuracy target still uses raw maximum residuals.")
        self.loss_scale_spin = self._numeric_control(settings.loss_scale, 1e-8, 1e12, 8)
        self.loss_scale_spin.setSuffix(" scene units" if stage == "alignment" else " CRS units")
        self.loss_scale_spin.setToolTip("Residual component magnitude where robust downweighting becomes significant.")
        form.addRow("Target maximum residual", self.tolerance_spin)
        form.addRow("Maximum iterations", self.iterations_spin)
        self.iterations_spin.setToolTip("Maximum optimizer function evaluations. A solver can stop earlier when improvement becomes negligible.")
        form.addRow("Transform model", self.model_combo)
        form.addRow("Residual loss", self.loss_combo)
        form.addRow("Robust loss threshold", self.loss_scale_spin)
        form.addRow(self.auto_apply_checkbox)
        layout.addLayout(form)
        self.advanced_group = QGroupBox("Advanced")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(False)
        advanced_layout = QVBoxLayout(self.advanced_group)
        self.advanced_controls = QWidget()
        advanced_form = QFormLayout(self.advanced_controls)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        self.method_combo = QComboBox()
        self.method_combo.addItem("Trust-region reflective", "trf")
        self.method_combo.addItem("Dogbox", "dogbox")
        self.method_combo.setCurrentIndex(self.method_combo.findData(settings.optimizer_method))
        self.method_combo.setToolTip("Dogbox can suit small problems but may be slow for weakly constrained networks.")
        self.scaling_combo = QComboBox()
        self.scaling_combo.addItem("Unit scaling", "unit")
        self.scaling_combo.addItem("Jacobian scaling", "jacobian")
        self.scaling_combo.setCurrentIndex(self.scaling_combo.findData(settings.parameter_scaling))
        self.scaling_combo.setToolTip("Jacobian scaling adapts parameter step sizes to their influence on residuals.")
        self.ftol_spin = self._numeric_control(settings.ftol, 1e-14, 1e9, 14)
        self.xtol_spin = self._numeric_control(settings.xtol, 1e-14, 1e9, 14)
        self.gtol_spin = self._numeric_control(settings.gtol, 1e-14, 1e9, 14)
        for control in (self.ftol_spin, self.xtol_spin, self.gtol_spin):
            control.setToolTip("Smaller thresholds demand tighter optimizer convergence, not a smaller positional residual.")
        self.default_bounds_checkbox = QCheckBox("Use default scale bounds")
        self.default_bounds_checkbox.setChecked(settings.scale_min is None and settings.scale_max is None)
        self.scale_min_spin = self._numeric_control(settings.scale_min if settings.scale_min is not None else (0.05 if stage == "alignment" else 1e-12), 1e-12, 1e12, 12)
        self.scale_max_spin = self._numeric_control(settings.scale_max if settings.scale_max is not None else (20.0 if stage == "alignment" else 1e6), 1e-12, 1e12, 12)
        self.scale_max_checkbox = QCheckBox("Limit maximum scale")
        self.scale_max_checkbox.setChecked(stage == "alignment" or settings.scale_max is not None)
        self.scale_max_checkbox.setVisible(stage == "georeference")
        self.scale_min_spin.setToolTip("Bounds apply only to scales optimized by the similarity model; fixed reference and translation-only scales are preserved.")
        advanced_form.addRow("Optimizer", self.method_combo)
        advanced_form.addRow("Parameter scaling", self.scaling_combo)
        advanced_form.addRow("Error improvement (ftol)", self.ftol_spin)
        advanced_form.addRow("Transform change (xtol)", self.xtol_spin)
        advanced_form.addRow("Gradient (gtol)", self.gtol_spin)
        advanced_form.addRow(self.default_bounds_checkbox)
        advanced_form.addRow("Minimum scale", self.scale_min_spin)
        advanced_form.addRow(self.scale_max_checkbox)
        advanced_form.addRow("Maximum scale", self.scale_max_spin)
        advanced_layout.addWidget(self.advanced_controls)
        self.advanced_controls.setVisible(False)
        self.advanced_group.toggled.connect(self.advanced_controls.setVisible)
        layout.addWidget(self.advanced_group)
        self.model_combo.currentIndexChanged.connect(self._update_controls)
        self.loss_combo.currentIndexChanged.connect(self._update_controls)
        self.default_bounds_checkbox.toggled.connect(self._update_controls)
        self.scale_max_checkbox.toggled.connect(self._update_controls)
        self._update_controls()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.run_button = self.buttons.addButton("Run", QDialogButtonBox.ButtonRole.AcceptRole)
        self.run_button.setDefault(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply_settings)
        self.run_button.clicked.connect(self.run)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @staticmethod
    def _numeric_control(value: float, minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        control = QDoubleSpinBox()
        control.setDecimals(decimals)
        control.setRange(minimum, maximum)
        control.setValue(value)
        control.setSingleStep(value if value > 0 else minimum)
        return control

    def _update_controls(self) -> None:
        similarity = self.model_combo.currentData() == "similarity"
        custom_bounds = similarity and not self.default_bounds_checkbox.isChecked()
        self.loss_scale_spin.setEnabled(self.loss_combo.currentData() != "linear")
        self.default_bounds_checkbox.setEnabled(similarity)
        self.scale_min_spin.setEnabled(custom_bounds)
        self.scale_max_checkbox.setEnabled(custom_bounds)
        self.scale_max_spin.setEnabled(custom_bounds and self.scale_max_checkbox.isChecked())

    def apply_settings(self) -> bool:
        default_bounds = self.default_bounds_checkbox.isChecked()
        try:
            settings = SolverSettings(
                self.tolerance_spin.value(), self.iterations_spin.value(), self.auto_apply_checkbox.isChecked(),
                transform_model=self.model_combo.currentData(), optimizer_method=self.method_combo.currentData(),
                loss=self.loss_combo.currentData(), loss_scale=self.loss_scale_spin.value(),
                scale_min=None if default_bounds else self.scale_min_spin.value(),
                scale_max=None if default_bounds or not self.scale_max_checkbox.isChecked() else self.scale_max_spin.value(),
                ftol=self.ftol_spin.value(), xtol=self.xtol_spin.value(), gtol=self.gtol_spin.value(),
                parameter_scaling=self.scaling_combo.currentData(),
            )
        except ValueError as error:
            QMessageBox.warning(self, "Invalid solver settings", str(error))
            return False
        self.settings_applied.emit(settings)
        return True

    def run(self) -> None:
        if not self.apply_settings():
            return
        self.run_requested = True
        self.accept()