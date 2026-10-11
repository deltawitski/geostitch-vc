from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QMessageBox, QSpinBox, QToolButton, QVBoxLayout, QWidget

from geostitch.georeference import georeference_distance_unit
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

    def __init__(self, stage: str, settings: SolverSettings, parent=None, project_crs: str | None = None):
        super().__init__(parent)
        self.stage = stage
        self.project_crs = project_crs
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
        self.auto_apply_checkbox.setToolTip(
            "When off, review the result first. When on, apply it automatically if it meets the target."
        )
        self.model_combo = QComboBox()
        model_options = (
            ("Translation", "translation", "Moves images only. Their current rotation and size stay unchanged."),
            ("Rigid", "rigid", "Moves and rotates images. Their current size stays unchanged."),
            ("Similarity", "similarity", "Moves, rotates, and resizes images evenly in every direction."),
            ("Affine", "affine", "Can also shear or stretch images differently by direction. Use well-spread links."),
            ("Projective", "projective", "Corrects perspective, such as an image photographed at an angle. Needs at least four well-spread links and known image sizes."),
            ("Deformable (TPS)", "tps", "Allows smooth local bending to fit links. Needs well-spread links and known image sizes."),
        )
        for label, value, description in model_options:
            index = self.model_combo.count()
            self.model_combo.addItem(label, value)
            self.model_combo.setItemData(index, description, Qt.ItemDataRole.ToolTipRole)
        self.model_combo.setCurrentIndex(self.model_combo.findData(settings.transform_model))
        self.model_help = QLabel(self)
        self.model_help.setWordWrap(True)
        self.model_help.setObjectName("solverModelHelp")
        self.model_help.setStyleSheet("color: palette(mid); font-size: 9pt;")
        self.loss_combo = QComboBox()
        loss_help = {
            "linear": "Treats every link equally.",
            "soft_l1": "Reduces the influence of links with unusually large errors.",
            "huber": "Reduces the influence of large errors while treating small errors normally.",
            "cauchy": "Strongly reduces the influence of links with very large errors.",
        }
        for label, value in (("Least squares", "linear"), ("Soft L1", "soft_l1"), ("Huber", "huber"), ("Cauchy", "cauchy")):
            index = self.loss_combo.count()
            self.loss_combo.addItem(label, value)
            self.loss_combo.setItemData(index, loss_help[value], Qt.ItemDataRole.ToolTipRole)
        self.loss_combo.setCurrentIndex(self.loss_combo.findData(settings.loss))
        self.loss_combo.setToolTip("Choose how much unusually large link errors affect the fit. The target error remains the maximum raw mismatch.")
        self.loss_scale_spin = self._numeric_control(settings.loss_scale, 1e-8, 1e12, 8)
        self.loss_scale_spin.setSuffix(" scene units" if stage == "alignment" else " CRS units")
        self.loss_scale_spin.setToolTip("Errors around this size start receiving less weight when a robust loss is selected.")
        form.addRow("Transform model", self.model_combo)
        form.addRow("Target mismatch", self.tolerance_spin)
        form.addRow("", self.model_help)
        self.tolerance_spin.setToolTip(
            "Alignment: largest allowed gap between linked points. Georeferencing: largest allowed gap "
            "between an image point and its entered coordinates. A smaller target is stricter."
        )
        self.iterations_spin.setToolTip("Maximum number of solver steps. Most runs stop sooner when the fit stabilizes.")
        form.addRow(self.auto_apply_checkbox)
        layout.addLayout(form)
        self.advanced_group = QToolButton(self)
        self.advanced_group.setCheckable(True)
        self.advanced_group.setText("Advanced settings")
        self.advanced_group.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_group.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_group.setToolTip("Show or hide detailed solver tuning options.")
        self.advanced_group.setAccessibleName("Advanced solver settings")
        self.advanced_group.setAutoRaise(True)
        self.advanced_group.setChecked(False)
        self.advanced_controls = QWidget()
        advanced_form = QFormLayout(self.advanced_controls)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.addRow("Maximum solver steps", self.iterations_spin)
        advanced_form.addRow("Residual weighting", self.loss_combo)
        advanced_form.addRow("Robust-loss threshold", self.loss_scale_spin)
        self.method_combo = QComboBox()
        self.method_combo.addItem("Trust-region reflective", "trf")
        self.method_combo.addItem("Dogbox", "dogbox")
        self.method_combo.setCurrentIndex(self.method_combo.findData(settings.optimizer_method))
        self.method_combo.setToolTip("Choose the numerical search strategy. The default works well for most projects.")
        self.scaling_combo = QComboBox()
        self.scaling_combo.addItem("Unit scaling", "unit")
        self.scaling_combo.addItem("Jacobian scaling", "jacobian")
        self.scaling_combo.setCurrentIndex(self.scaling_combo.findData(settings.parameter_scaling))
        self.scaling_combo.setToolTip("Changes how the solver sizes its steps when different settings have different effects. Usually leave the default.")
        self.ftol_spin = self._numeric_control(settings.ftol, 1e-14, 1e9, 14)
        self.xtol_spin = self._numeric_control(settings.xtol, 1e-14, 1e9, 14)
        self.gtol_spin = self._numeric_control(settings.gtol, 1e-14, 1e9, 14)
        self.ftol_spin.setToolTip("Stop when another solver step is unlikely to improve the fit by this amount.")
        self.xtol_spin.setToolTip("Stop when another solver step is unlikely to change the image transforms by this amount.")
        self.gtol_spin.setToolTip("Stop when the solver has little remaining direction in which to improve the fit.")
        self.default_bounds_checkbox = QCheckBox("Use default scale bounds")
        self.default_bounds_checkbox.setChecked(settings.scale_min is None and settings.scale_max is None)
        self.scale_min_spin = self._numeric_control(settings.scale_min if settings.scale_min is not None else (0.05 if stage == "alignment" else 1e-12), 1e-12, 1e12, 12)
        self.scale_max_spin = self._numeric_control(settings.scale_max if settings.scale_max is not None else (20.0 if stage == "alignment" else 1e6), 1e-12, 1e12, 12)
        self.scale_max_checkbox = QCheckBox("Limit maximum scale")
        self.scale_max_checkbox.setChecked(stage == "alignment" or settings.scale_max is not None)
        self.scale_max_checkbox.setVisible(stage == "georeference")
        self.scale_min_spin.setToolTip("Smallest allowed image size for the Similarity model. Other models or the fixed reference may not use this limit.")
        self.scale_max_spin.setToolTip("Largest allowed image size for the Similarity model.")
        self.affine_min_stretch_spin = self._numeric_control(settings.affine_min_stretch, 1e-8, 1e8, 8)
        self.affine_max_stretch_spin = self._numeric_control(settings.affine_max_stretch, 1e-8, 1e8, 8)
        self.affine_max_condition_spin = self._numeric_control(settings.affine_max_condition, 1.0, 1e8, 4)
        self.affine_allow_reflection_checkbox = QCheckBox("Allow reflected affine solutions")
        self.affine_allow_reflection_checkbox.setChecked(settings.affine_allow_reflection)
        self.projective_min_stretch_spin = self._numeric_control(settings.projective_min_stretch, 1e-8, 1e8, 8)
        self.projective_max_stretch_spin = self._numeric_control(settings.projective_max_stretch, 1e-8, 1e8, 8)
        self.projective_max_condition_spin = self._numeric_control(settings.projective_max_condition, 1.0, 1e8, 4)
        self.projective_horizon_margin_spin = self._numeric_control(settings.projective_horizon_margin, 1e-8, 0.999999, 6)
        self.projective_allow_reflection_checkbox = QCheckBox("Allow reflected projective solutions")
        self.projective_allow_reflection_checkbox.setChecked(settings.projective_allow_reflection)
        self.warp_smoothing_spin = self._numeric_control(settings.warp_smoothing, 0.0, 1e6, 6)
        self.warp_smoothing_spin.setSingleStep(0.01)
        self.warp_min_relative_area_spin = self._numeric_control(settings.warp_min_relative_area, 1e-8, 0.999999, 6)
        self.warp_max_relative_stretch_spin = self._numeric_control(settings.warp_max_relative_stretch, 1e-8, 1e8, 6)
        self.projective_horizon_margin_spin.setToolTip("Keeps the perspective transform away from positions where the image would stretch without bound.")
        self.projective_max_condition_spin.setToolTip("Rejects perspective transforms that stretch one direction far more than another.")
        self.affine_min_stretch_spin.setToolTip("Rejects affine transforms that shrink the image below this amount.")
        self.affine_max_stretch_spin.setToolTip("Rejects affine transforms that enlarge the image beyond this amount.")
        self.affine_max_condition_spin.setToolTip("Rejects affine transforms that stretch one direction far more than another.")
        self.affine_allow_reflection_checkbox.setToolTip("Allow the result to flip an image. Usually leave this off.")
        self.projective_allow_reflection_checkbox.setToolTip("Allow the perspective result to flip an image. Usually leave this off.")
        self.warp_smoothing_spin.setToolTip("Higher values make the TPS bend more gently between linked points; lower values follow the points more closely.")
        self.warp_min_relative_area_spin.setToolTip("Rejects a warp if it squeezes any sampled area below this fraction of its original size or flips it.")
        self.warp_max_relative_stretch_spin.setToolTip("Rejects a warp if it stretches any sampled part of the image beyond this factor.")
        advanced_form.addRow("Optimizer", self.method_combo)
        advanced_form.addRow("Parameter scaling", self.scaling_combo)
        advanced_form.addRow("Error improvement (ftol)", self.ftol_spin)
        advanced_form.addRow("Transform change (xtol)", self.xtol_spin)
        advanced_form.addRow("Gradient (gtol)", self.gtol_spin)
        advanced_form.addRow(self.default_bounds_checkbox)
        advanced_form.addRow("Minimum scale", self.scale_min_spin)
        advanced_form.addRow(self.scale_max_checkbox)
        advanced_form.addRow("Maximum scale", self.scale_max_spin)
        advanced_form.addRow("Affine minimum stretch", self.affine_min_stretch_spin)
        advanced_form.addRow("Affine maximum stretch", self.affine_max_stretch_spin)
        advanced_form.addRow("Affine maximum condition", self.affine_max_condition_spin)
        advanced_form.addRow(self.affine_allow_reflection_checkbox)
        advanced_form.addRow("Projective minimum stretch", self.projective_min_stretch_spin)
        advanced_form.addRow("Projective maximum stretch", self.projective_max_stretch_spin)
        advanced_form.addRow("Projective maximum condition", self.projective_max_condition_spin)
        advanced_form.addRow("Projective horizon margin", self.projective_horizon_margin_spin)
        advanced_form.addRow(self.projective_allow_reflection_checkbox)
        advanced_form.addRow("TPS smoothing", self.warp_smoothing_spin)
        advanced_form.addRow("TPS minimum relative area", self.warp_min_relative_area_spin)
        advanced_form.addRow("TPS maximum relative stretch", self.warp_max_relative_stretch_spin)
        self.advanced_controls.setVisible(False)
        layout.addWidget(self.advanced_group)
        layout.addWidget(self.advanced_controls)
        self.advanced_group.toggled.connect(self._set_advanced_expanded)
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
        self.adjustSize()

    def _set_advanced_expanded(self, expanded: bool) -> None:
        self.advanced_controls.setVisible(expanded)
        self.advanced_group.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self.adjustSize()

    @staticmethod
    def _numeric_control(value: float, minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        control = QDoubleSpinBox()
        control.setDecimals(decimals)
        control.setRange(minimum, maximum)
        control.setValue(value)
        control.setSingleStep(value if value > 0 else minimum)
        return control

    def _update_controls(self) -> None:
        model = self.model_combo.currentData()
        similarity = model == "similarity"
        affine = model == "affine"
        projective = model == "projective"
        tps = model == "tps"
        if tps and self.loss_combo.currentData() != "linear":
            self.loss_combo.blockSignals(True)
            self.loss_combo.setCurrentIndex(self.loss_combo.findData("linear"))
            self.loss_combo.blockSignals(False)
        unit_suffix = " scene units" if self.stage == "alignment" else " CRS units"
        if self.stage == "georeference" and self.project_crs:
            try:
                unit_suffix = f" {georeference_distance_unit(self.project_crs, model).label}"
            except ValueError:
                pass
        self.tolerance_spin.setSuffix(unit_suffix)
        self.loss_scale_spin.setSuffix(unit_suffix)
        custom_bounds = similarity and not self.default_bounds_checkbox.isChecked()
        self.loss_combo.setEnabled(not tps)
        self.loss_scale_spin.setEnabled(not tps and self.loss_combo.currentData() != "linear")
        self.default_bounds_checkbox.setEnabled(similarity)
        self.scale_min_spin.setEnabled(custom_bounds)
        self.scale_max_checkbox.setEnabled(custom_bounds)
        self.scale_max_spin.setEnabled(custom_bounds and self.scale_max_checkbox.isChecked())
        for control in (self.affine_min_stretch_spin, self.affine_max_stretch_spin,
                        self.affine_max_condition_spin, self.affine_allow_reflection_checkbox):
            control.setEnabled(affine)
        for control in (self.projective_min_stretch_spin, self.projective_max_stretch_spin,
                        self.projective_max_condition_spin, self.projective_horizon_margin_spin,
                        self.projective_allow_reflection_checkbox):
            control.setEnabled(projective)
        for control in (
            self.warp_smoothing_spin, self.warp_min_relative_area_spin,
            self.warp_max_relative_stretch_spin,
        ):
            control.setEnabled(tps)
        descriptions = {
            "translation": "Moves images only; their rotation and size stay unchanged.",
            "rigid": "Moves and rotates images while keeping their current size.",
            "similarity": "Moves, rotates, and resizes images evenly in every direction.",
            "affine": "Can shear or stretch images differently by direction. Use well-spread links.",
            "projective": "Corrects perspective, such as an image photographed at an angle. Needs at least four well-spread links and known image sizes.",
            "tps": (
                "Fits smooth local bends to the links. Needs well-spread links and known image sizes. "
                + ("Uses a geographic CRS for georeferencing." if self.stage == "georeference" else
                   "Keeps one reference image fixed in each connected group.")
            ),
        }
        self.model_help.setText(descriptions.get(model, ""))
        self.model_combo.setToolTip(descriptions.get(model, "Choose how much each image may move or deform."))
        advanced_form = self.advanced_controls.layout()
        if isinstance(advanced_form, QFormLayout):
            advanced_form.setRowVisible(self.loss_combo, not tps)
            advanced_form.setRowVisible(self.loss_scale_spin, not tps and self.loss_combo.currentData() != "linear")
            advanced_form.setRowVisible(self.default_bounds_checkbox, similarity)
            advanced_form.setRowVisible(self.scale_min_spin, custom_bounds)
            advanced_form.setRowVisible(self.scale_max_checkbox, custom_bounds and self.stage == "georeference")
            advanced_form.setRowVisible(self.scale_max_spin, custom_bounds)
            for control in (self.affine_min_stretch_spin, self.affine_max_stretch_spin,
                            self.affine_max_condition_spin, self.affine_allow_reflection_checkbox):
                advanced_form.setRowVisible(control, affine)
            for control in (self.projective_min_stretch_spin, self.projective_max_stretch_spin,
                            self.projective_max_condition_spin, self.projective_horizon_margin_spin,
                            self.projective_allow_reflection_checkbox):
                advanced_form.setRowVisible(control, projective)
            for control in (self.warp_smoothing_spin, self.warp_min_relative_area_spin,
                            self.warp_max_relative_stretch_spin):
                advanced_form.setRowVisible(control, tps)

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
                affine_min_stretch=self.affine_min_stretch_spin.value(),
                affine_max_stretch=self.affine_max_stretch_spin.value(),
                affine_max_condition=self.affine_max_condition_spin.value(),
                affine_allow_reflection=self.affine_allow_reflection_checkbox.isChecked(),
                projective_min_stretch=self.projective_min_stretch_spin.value(),
                projective_max_stretch=self.projective_max_stretch_spin.value(),
                projective_max_condition=self.projective_max_condition_spin.value(),
                projective_horizon_margin=self.projective_horizon_margin_spin.value(),
                projective_allow_reflection=self.projective_allow_reflection_checkbox.isChecked(),
                warp_smoothing=self.warp_smoothing_spin.value(),
                warp_min_relative_area=self.warp_min_relative_area_spin.value(),
                warp_max_relative_stretch=self.warp_max_relative_stretch_spin.value(),
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