from __future__ import annotations

from math import isfinite

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QLineEdit, QVBoxLayout,
)
from pyproj import CRS


class ExportGridDialog(QDialog):
    def __init__(self, project_crs: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Output raster grid")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.crs_edit = QLineEdit(project_crs)
        self.crs_edit.setToolTip("Coordinate reference system for the output raster grid.")
        self.resolution_spin = QDoubleSpinBox()
        self.resolution_spin.setDecimals(10)
        self.resolution_spin.setRange(1e-10, 1e12)
        self.resolution_spin.setValue(1.0)
        self._update_resolution_units()
        self.resolution_spin.setToolTip("Output pixel size in the output CRS units. The native pixel grid is preserved when exporting in the native CRS.")
        self.crs_edit.editingFinished.connect(self._update_resolution_units)
        self.resampling_combo = QComboBox()
        self.resampling_combo.addItem("Bilinear", "bilinear")
        self.resampling_combo.addItem("Nearest neighbour", "nearest")
        self.extent_fields = [QLineEdit() for _ in range(4)]
        for field, title in zip(self.extent_fields, ("Minimum X", "Minimum Y", "Maximum X", "Maximum Y")):
            field.setPlaceholderText("Automatic")
            field.setToolTip("For cross-CRS warps, leave all four blank to use the transformed image footprint.")
            form.addRow(title, field)
        form.insertRow(0, "Output CRS", self.crs_edit)
        form.insertRow(1, "Pixel size", self.resolution_spin)
        form.insertRow(2, "Resampling", self.resampling_combo)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> dict:
        crs_text = self.crs_edit.text().strip()
        crs = CRS.from_user_input(crs_text)
        raw_bounds = [field.text().strip() for field in self.extent_fields]
        if any(raw_bounds) and not all(raw_bounds):
            raise ValueError("Enter all four output extent coordinates, or leave all four blank for automatic extent.")
        bounds = tuple(float(value) for value in raw_bounds) if all(raw_bounds) else None
        if bounds is not None and not all(isfinite(value) for value in bounds):
            raise ValueError("Output extent coordinates must be finite.")
        if bounds is not None and (bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
            raise ValueError("Output extent must have positive width and height.")
        return {
            "output_crs": crs,
            "resolution": self.resolution_spin.value(),
            "resampling": self.resampling_combo.currentData(),
            "bounds": bounds,
        }

    def _update_resolution_units(self) -> None:
        try:
            crs = CRS.from_user_input(self.crs_edit.text().strip())
            unit = crs.axis_info[0].unit_name if crs.axis_info else "CRS units"
        except Exception:
            unit = "CRS units"
        self.resolution_spin.setSuffix(f" {unit}")
