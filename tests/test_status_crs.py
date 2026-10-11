from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from pyproj import CRS, Transformer
import pytest

from geostitch.app import MainWindow
from geostitch.models import GroundControlPoint, Project, SolverSettings
from geostitch.project_io import save_project


def test_project_crs_selector_is_permanent_at_status_bar_right(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "status.ini"), QSettings.Format.IniFormat))
    window.show()
    app.processEvents()
    try:
        bar = window.statusBar()
        selector = window.map_crs_combo
        assert bar.isAncestorOf(selector)
        assert not window.world_map_dock.isAncestorOf(selector)
        assert selector.currentText() == window.project.crs
        assert selector.count() > 5000
        assert selector.findData("EPSG:27700") >= 0
        selector.showPopup()
        app.processEvents()
        assert selector._popup.width() > selector.width()
        assert selector._popup.layout().itemAt(0).widget() is selector.search_edit
        assert selector._popup.layout().itemAt(1).widget() is selector.results_view
        selector.search_edit.setText("british national grid")
        app.processEvents()
        results = selector.results_view.model()
        assert results.rowCount() >= 1
        assert any(
            "British National Grid" in results.data(results.index(row, 0))
            for row in range(results.rowCount())
        )
        selector.results_view.clicked.emit(results.index(0, 0))
        assert window.project.crs == selector.currentText() == "EPSG:27700"
        assert not selector._popup.isVisible()
        long_crs = "CUSTOM:" + "coordinate-system-" * 12
        selector.set_compact_text(long_crs)
        assert selector.currentText() == long_crs
        assert selector.lineEdit().alignment() & Qt.AlignmentFlag.AlignLeft
        assert selector.lineEdit().cursorPosition() == 0
        selector.set_compact_text(window.project.crs)
        window.world_map_dock.hide()
        bar.showMessage("A long message " * 50)
        bar.begin_progress(100)
        app.processEvents()
        assert selector.isVisible()
        assert window.status_crs_widget.geometry().left() > window.hint_label.geometry().right()
        assert window.status_crs_widget.geometry().right() > bar.progress_bar.geometry().right()
        assert bar.rect().contains(window.status_crs_widget.geometry())
        bar.end_progress()
    finally:
        window.close()


def test_status_crs_selector_keeps_selection_edit_and_reopen_behavior(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "status.ini"), QSettings.Format.IniFormat))
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    try:
        selector = window.map_crs_combo
        index = selector.findData("EPSG:32610")
        selector.setCurrentIndex(index)
        selector.activated.emit(index)
        assert window.project.crs == window.crs_edit.text() == "EPSG:32610"
        selector.setCurrentText("EPSG:26910")
        selector.lineEdit().editingFinished.emit()
        assert window.project.crs == window.crs_edit.text() == "EPSG:26910"
        selector.setCurrentText("invalid-crs")
        selector.lineEdit().editingFinished.emit()
        assert errors
        assert window.project.crs == selector.currentText() == "EPSG:26910"
        selector.setCurrentText("ESRI:102003")
        selector.lineEdit().editingFinished.emit()
        assert window.project.crs == selector.currentText() == "ESRI:102003"
        project = Project(crs="EPSG:32611")
        path = tmp_path / "project.gsp"
        save_project(project, path)
        window._open_project_path(str(path))
        assert selector.currentText() == "EPSG:32611"
        assert window.statusBar().isAncestorOf(selector)
    finally:
        window.close()


def test_rejected_popup_crs_change_keeps_old_crs_visible(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "popup-crs-reject.ini"), QSettings.Format.IniFormat))
    window.project.crs = "EPSG:4326"
    window.crs_edit.setText(window.project.crs)
    window.map_crs_combo.set_compact_text(window.project.crs)
    questions = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: questions.append(args[2]) or QMessageBox.StandardButton.No,
    )
    try:
        selector = window.map_crs_combo
        selector.showPopup()
        selector.search_edit.setText("pseudo-mercator")
        results = selector.results_view.model()
        candidate = next(
            results.index(row, 0) for row in range(results.rowCount())
            if results.data(results.index(row, 0), Qt.ItemDataRole.UserRole) == "EPSG:3857"
        )
        hide_popup = selector.hidePopup

        def hide_and_finish_editing():
            hide_popup()
            selector.lineEdit().editingFinished.emit()

        monkeypatch.setattr(selector, "hidePopup", hide_and_finish_editing)

        selector.results_view.clicked.emit(candidate)

        assert len(questions) == 1
        assert window.project.crs == "EPSG:4326"
        assert window.crs_edit.text() == selector.currentText() == "EPSG:4326"
    finally:
        window.close()


def test_repeated_crs_switches_reproject_from_native_control_coordinates(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window = MainWindow(QSettings(str(tmp_path / "native-crs.ini"), QSettings.Format.IniFormat))
    window.project.crs = "EPSG:4326"
    image = window.project.add_image(tmp_path / "source.png")
    point = GroundControlPoint.create("Native coordinate")
    point.set_observation(image.id, 200, 300)
    point.set_geographic_coordinates(10.0, 60.0, window.project.crs)
    window.project.add_control_point(point)
    monkeypatch.setattr(window, "_resolve_world_transforms", lambda: None)
    native = (10.0, 60.0)
    targets = ("EPSG:3857", "EPSG:32632", "EPSG:4326", "EPSG:3857")
    try:
        for target_crs in targets:
            window.crs_edit.setText(target_crs)
            window._update_crs()
            expected = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True).transform(*native)
            assert (point.geographic_x, point.geographic_y) == pytest.approx(expected, abs=1e-8)
            assert (point.geographic_native_x, point.geographic_native_y, point.geographic_native_crs) == (
                10.0, 60.0, "EPSG:4326",
            )

        restored_values = window.project.to_dict()
        reopened = Project.from_dict(restored_values)
        reopened_point = reopened.point_by_id(point.id)
        assert reopened_point.geographic_native_crs == "EPSG:4326"
        assert (reopened_point.geographic_native_x, reopened_point.geographic_native_y) == native
        assert (reopened_point.geographic_x, reopened_point.geographic_y) == pytest.approx(
            (point.geographic_x, point.geographic_y), abs=1e-8,
        )

        edited = (point.geographic_x + 100, point.geographic_y + 50)
        window.project.set_point_geographic_coordinates(point, *edited)
        assert (point.geographic_native_x, point.geographic_native_y, point.geographic_native_crs) == (
            edited[0], edited[1], window.project.crs,
        )
        window.crs_edit.setText("EPSG:4326")
        window._update_crs()
        expected = Transformer.from_crs(targets[-1], "EPSG:4326", always_xy=True).transform(*edited)
        assert (point.geographic_x, point.geographic_y) == pytest.approx(expected, abs=1e-8)
        assert point.geographic_native_crs == targets[-1]
    finally:
        window.close()


def test_projected_crs_change_rebases_distance_settings_and_undoes(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "unit-rebase.ini"), QSettings.Format.IniFormat))
    settings = SolverSettings(
        tolerance=2.0, loss_scale=3.0, transform_model="affine",
        affine_min_stretch=0.25, affine_max_stretch=8.0,
    )
    window.project.crs = "EPSG:32610"
    window.project.georeference_settings = settings
    window.crs_edit.setText(window.project.crs)
    window.map_crs_combo.setCurrentText(window.project.crs)
    window._resolve_world_transforms = lambda: None
    before_count = window.undo_stack.count()
    factor = 1 / CRS.from_epsg(2263).axis_info[0].unit_conversion_factor
    try:
        window.crs_edit.setText("EPSG:2263")
        window._update_crs()

        assert window.project.crs == "EPSG:2263"
        assert window.project.georeference_settings.tolerance == pytest.approx(settings.tolerance * factor)
        assert window.project.georeference_settings.loss_scale == pytest.approx(settings.loss_scale * factor)
        assert window.project.georeference_settings.affine_min_stretch == pytest.approx(
            settings.affine_min_stretch * factor,
        )
        assert window.undo_stack.count() == before_count + 1
        window.undo_stack.undo()
        assert window.project.crs == "EPSG:32610"
        assert window.project.georeference_settings == settings
    finally:
        window.close()


def test_angular_to_linear_crs_change_requires_confirmation(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "unit-confirm.ini"), QSettings.Format.IniFormat))
    window.project.crs = "EPSG:4326"
    window.project.georeference_settings = SolverSettings(tolerance=0.25, loss_scale=1.5)
    window.crs_edit.setText(window.project.crs)
    window.map_crs_combo.setCurrentText(window.project.crs)
    before = window.project.to_dict()
    before_count = window.undo_stack.count()
    questions = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: questions.append(args[2]) or QMessageBox.StandardButton.No,
    )
    try:
        window.crs_edit.setText("EPSG:3857")
        window._update_crs()

        assert window.project.to_dict() == before
        assert window.crs_edit.text() == window.map_crs_combo.currentText() == "EPSG:4326"
        assert window.undo_stack.count() == before_count
        assert len(questions) == 1
        assert "degrees" in questions[0] and "metres" in questions[0]

        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        window.crs_edit.setText("EPSG:3857")
        window._update_crs()
        assert window.project.crs == "EPSG:3857"
        assert window.project.georeference_settings.tolerance == 0.25
        assert window.project.georeference_settings.loss_scale == 1.5
        assert window.undo_stack.count() == before_count + 1
    finally:
        window.close()


def test_legacy_partial_geographic_coordinate_loads_without_native_pair(tmp_path):
    from geostitch.models import GroundControlPoint

    point = GroundControlPoint.create("Partial")
    point.geographic_x = 10.0
    project = Project(crs="EPSG:4326")
    project.add_control_point(point)
    saved = project.to_dict()
    restored = Project.from_dict(saved)
    restored_point = restored.point_by_id(point.id)
    assert restored_point.geographic_x == 10.0
    assert restored_point.geographic_y is None
    assert restored_point.geographic_native_x is None
    assert restored_point.geographic_native_y is None
    assert restored_point.geographic_native_crs is None