from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from geostitch.app import MainWindow
from geostitch.models import Project
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
        index = selector.findText("EPSG:32610")
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
        project = Project(crs="EPSG:32611")
        path = tmp_path / "project.gsp"
        save_project(project, path)
        window._open_project_path(str(path))
        assert selector.currentText() == "EPSG:32611"
        assert window.statusBar().isAncestorOf(selector)
    finally:
        window.close()