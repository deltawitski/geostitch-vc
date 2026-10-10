from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QStyleFactory, QToolButton, QVBoxLayout,
)

from geostitch.icons import DEFAULT_KIND_COLORS


class AppearanceDialog(QDialog):
    applied = Signal(str, str, object, bool)

    def __init__(self, style: str, scheme: str, colors: dict[str, str], parent=None, *, dark_map: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Appearance")
        self.setMinimumWidth(360)
        self.colors = dict(colors)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.style_combo = QComboBox()
        self.style_combo.addItems(QStyleFactory.keys())
        self.style_combo.setCurrentText(style)
        self.scheme_combo = QComboBox()
        self.scheme_combo.addItem("GeoStitch dark", "dark")
        self.scheme_combo.addItem("Qt default", "native")
        self.scheme_combo.setCurrentIndex(max(0, self.scheme_combo.findData(scheme)))
        form.addRow("Qt style", self.style_combo)
        form.addRow("Color scheme", self.scheme_combo)
        self.dark_map_checkbox = QCheckBox("Dark map")
        self.dark_map_checkbox.setToolTip("Show the OpenStreetMap tiles in a dark colour scheme")
        self.dark_map_checkbox.setChecked(dark_map)
        form.addRow("World Map", self.dark_map_checkbox)
        layout.addLayout(form)
        group = QGroupBox("Symbol colors")
        color_form = QFormLayout(group)
        self.color_buttons = {}
        for kind, label in [("image", "Images"), ("point", "Control points"),
                            ("link", "Link constraints"), ("geojson", "GeoJSON layers")]:
            button = QToolButton()
            button.setFixedSize(56, 30)
            button.setAccessibleName(f"{label} color")
            button.clicked.connect(lambda checked=False, symbol=kind: self.choose_color(symbol))
            self.color_buttons[kind] = button
            color_form.addRow(label, button)
        layout.addWidget(group)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.RestoreDefaults,
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        self.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self.restore_defaults)
        layout.addWidget(self.buttons)
        self._refresh_swatches()

    def _refresh_swatches(self) -> None:
        for kind, button in self.color_buttons.items():
            color = QColor(self.colors[kind])
            swatch = QPixmap(32, 18)
            swatch.fill(color)
            button.setIcon(QIcon(swatch))
            button.setIconSize(swatch.size())
            button.setToolTip(color.name())

    def choose_color(self, kind: str) -> None:
        color = QColorDialog.getColor(QColor(self.colors[kind]), self, "Symbol color")
        if color.isValid():
            self.colors[kind] = color.name()
            self._refresh_swatches()

    def restore_defaults(self) -> None:
        self.style_combo.setCurrentText("Fusion")
        self.scheme_combo.setCurrentIndex(0)
        self.dark_map_checkbox.setChecked(False)
        self.colors = DEFAULT_KIND_COLORS.copy()
        self._refresh_swatches()

    def apply(self) -> None:
        self.applied.emit(self.style_combo.currentText(), self.scheme_combo.currentData(),
                  dict(self.colors), self.dark_map_checkbox.isChecked())

    def _accept(self) -> None:
        self.apply()
        self.accept()