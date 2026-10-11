import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from geostitch.export_grid_dialog import ExportGridDialog


def test_output_grid_dialog_defaults_to_project_crs_and_auto_extent():
    app = QApplication.instance() or QApplication([])
    dialog = ExportGridDialog("EPSG:32610")
    try:
        options = dialog.options()
        assert options["output_crs"].to_epsg() == 32610
        assert options["resolution"] == 1.0
        assert options["resampling"] == "bilinear"
        assert options["bounds"] is None
    finally:
        dialog.close()


def test_output_grid_dialog_accepts_explicit_extent_and_resampling():
    app = QApplication.instance() or QApplication([])
    dialog = ExportGridDialog("EPSG:3857")
    try:
        for field, value in zip(dialog.extent_fields, ("100", "200", "900", "1000")):
            field.setText(value)
        dialog.resolution_spin.setValue(5)
        dialog.resampling_combo.setCurrentIndex(dialog.resampling_combo.findData("nearest"))
        options = dialog.options()
        assert options["bounds"] == (100.0, 200.0, 900.0, 1000.0)
        assert options["resolution"] == 5
        assert options["resampling"] == "nearest"
    finally:
        dialog.close()


def test_output_grid_dialog_rejects_partial_extent():
    app = QApplication.instance() or QApplication([])
    dialog = ExportGridDialog("EPSG:3857")
    try:
        dialog.extent_fields[0].setText("0")
        with pytest.raises(ValueError, match="all four"):
            dialog.options()
    finally:
        dialog.close()
