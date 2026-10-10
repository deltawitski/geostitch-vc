import os
import subprocess
import sys
import threading
from pathlib import Path
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QImage, QUndoCommand
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

import geostitch.app as app_module
from geostitch.app import MainWindow
from geostitch.models import Project
from geostitch.project_io import save_project, save_recovery


def test_window_deletion_with_undo_history_does_not_call_deleted_widgets(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    exceptions = []
    monkeypatch.setattr(sys, "excepthook", lambda *exception: exceptions.append(exception))
    window = MainWindow(QSettings(str(tmp_path / "teardown.ini"), QSettings.Format.IniFormat))
    window.undo_stack.push(QUndoCommand("Test edit"))
    assert window.shortcut_actions["undo"].text() == "Undo Test edit"
    window.undo_stack.undo()
    assert window.shortcut_actions["undo"].text() == "Undo"
    assert window.shortcut_actions["redo"].text() == "Redo Test edit"
    window.undo_stack.redo()
    assert window.shortcut_actions["undo"].text() == "Undo Test edit"
    assert window.shortcut_actions["redo"].text() == "Redo"
    undo_stack = window.undo_stack
    undo_stack.setParent(None)
    window._mark_project_saved()
    window.close()
    delete(window)
    try:
        undo_stack.clear()
        app.processEvents()
        assert exceptions == []
    finally:
        delete(undo_stack)


def test_window_startup_does_not_import_solver_or_export_dependencies(tmp_path):
    code = """
import sys
from geostitch.app import MainWindow
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
app = QApplication([])
window = MainWindow(QSettings(sys.argv[1], QSettings.Format.IniFormat))
assert 'scipy.optimize' not in sys.modules
assert 'geostitch.export' not in sys.modules
window.close()
"""
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "startup.ini")],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_project_open_queues_images_and_discards_stale_results(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    project = Project()
    first = project.add_image(tmp_path / "first.png")
    second = project.add_image(tmp_path / "second.png")
    first.position_x = 250
    first.rest_locked = True
    project.views = {"canvas": {"center": [1000, 2000], "scale": 0.5}}
    path = tmp_path / "project.gsp"
    save_project(project, path)
    window = MainWindow(QSettings(str(tmp_path / "open.ini"), QSettings.Format.IniFormat))
    queued = {}
    monkeypatch.setattr(window._image_loader, "request", lambda key, image_path: queued.setdefault(key, image_path))

    def no_sync_decode(*args):
        raise AssertionError("Image decoding must not block project opening")

    monkeypatch.setattr(window, "_read_image_pixmap", no_sync_decode)
    try:
        window._open_project_path(str(path))
        assert len(queued) == 2
        assert window.image_list.count() == 2
        assert window._image_pixmaps == {}
        before_view = window.canvas.view_state()
        image = QImage(100, 80, QImage.Format.Format_RGB32)
        first_key = next(key for key in queued if key[1] == first.id)
        window._on_image_loaded(first_key, image, "")
        window._refresh_loaded_images()
        assert first.id in window.canvas._layers
        assert first.id in window.canvas._locked_images
        assert window.canvas.view_state() == before_view
        assert not window._selected_image_ids()
        stale_key = next(key for key in queued if key[1] == second.id)
        window._on_image_loaded(stale_key, QImage(), "Missing image")
        window._refresh_loaded_images()
        assert window._pixmap_for_image(window.project.images[1])[1] == "Missing image"
        assert window._loading_image_keys == set()
        assert "Missing image" in window.statusBar().currentMessage()
        window.new_project()
        window._on_image_loaded(stale_key, image, "")
        assert window._image_pixmaps == {}
        assert window.project.images == []
    finally:
        window.close()


def test_background_project_loading_keeps_ui_events_running(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    project = Project()
    record = project.add_image(tmp_path / "image.png")
    record.position_x = 10
    path = tmp_path / "background.gsp"
    save_project(project, path)
    started = threading.Event()
    release = threading.Event()
    worker_threads = []
    ui_thread = threading.get_ident()

    def decode(image_path):
        worker_threads.append(threading.get_ident())
        started.set()
        if not release.wait(5):
            raise TimeoutError("Test decoder was not released")
        image = QImage(100, 80, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        return image, ""

    monkeypatch.setattr(MainWindow, "_read_preview_data", staticmethod(decode))
    window = MainWindow(QSettings(str(tmp_path / "background.ini"), QSettings.Format.IniFormat))
    heartbeat = []
    try:
        window._open_project_path(str(path))
        assert started.wait(2)
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        app.processEvents()
        assert heartbeat == [True]
        assert len(worker_threads) == 1
        assert worker_threads[0] != ui_thread
        assert window._image_pixmaps == {}
        release.set()
        deadline = monotonic() + 5
        while record.id not in window.canvas._layers and monotonic() < deadline:
            app.processEvents()
        assert record.id in window.canvas._layers
        assert window._loading_image_keys == set()
        assert window._image_load_errors == {}
    finally:
        release.set()
        window.close()


def test_last_project_is_not_opened_before_window_show(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "last.gsp"
    save_project(Project(), path)
    settings = QSettings(str(tmp_path / "last.ini"), QSettings.Format.IniFormat)
    settings.setValue("last_project", str(path))
    window = MainWindow(settings)
    opened = []
    monkeypatch.setattr(window, "_open_project_path", lambda *args, **kwargs: opened.append(window.isVisible()))
    try:
        app.processEvents()
        assert opened == []
        window.show()
        assert window._startup_timer.isActive()
        assert opened == []
        window._reopen_startup_project()
        assert opened == [True]
    finally:
        window.close()


def test_main_opens_explicit_project_instead_of_last_project(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    last_path = tmp_path / "last.gsp"
    requested_path = tmp_path / "requested project.gsp"
    save_project(Project(name="Last project"), last_path)
    save_project(Project(name="Requested project"), requested_path)
    settings = QSettings(str(tmp_path / "explicit.ini"), QSettings.Format.IniFormat)
    settings.setValue("last_project", str(last_path))
    window = MainWindow(settings)
    monkeypatch.setattr(app_module, "QApplication", lambda arguments: app)
    monkeypatch.setattr(app_module, "MainWindow", lambda: window)
    monkeypatch.setattr(app, "arguments", lambda: ["GeoStitch.exe", str(requested_path)])

    def run_events():
        assert window.isVisible()
        assert window.project_path is None
        assert not window._startup_timer.isActive()
        app.processEvents()
        assert window.project_path == requested_path
        assert window.project.name == "Requested project"
        window._reopen_startup_project()
        assert window.project_path == requested_path
        return 0

    monkeypatch.setattr(app, "exec", run_events)
    try:
        assert app_module.main() == 0
    finally:
        window.close()


def test_main_reports_missing_explicit_project_without_opening_last(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    last_path = tmp_path / "last.gsp"
    missing_path = tmp_path / "missing project.gsp"
    save_project(Project(name="Last project"), last_path)
    settings = QSettings(str(tmp_path / "missing.ini"), QSettings.Format.IniFormat)
    settings.setValue("last_project", str(last_path))
    window = MainWindow(settings)
    save_recovery(Project(name="Unrelated recovery"), window._recovery_directory / "unrelated.gsp", None)
    errors = []
    monkeypatch.setattr(app_module, "QApplication", lambda arguments: app)
    monkeypatch.setattr(app_module, "MainWindow", lambda: window)
    monkeypatch.setattr(app, "arguments", lambda: ["GeoStitch.exe", str(missing_path)])
    monkeypatch.setattr(window, "_show_error", errors.append)

    def run_events():
        app.processEvents()
        assert len(errors) == 1
        assert "Could not open project" in errors[0]
        assert window.project_path is None
        assert not window._startup_timer.isActive()
        assert not window._startup_pending
        assert window.project_path is None
        return 0

    monkeypatch.setattr(app, "exec", run_events)
    try:
        assert app_module.main() == 0
    finally:
        window.close()


def test_installer_association_passes_quoted_project_path():
    installer_path = Path(__file__).resolve().parents[1] / "packaging" / "installer.iss"
    installer = installer_path.read_text(encoding="utf-8")
    assert "ChangesAssociations=yes" in installer
    assert 'Subkey: "Software\\Classes\\.gsp"; ValueType: string; ValueName: ""; ValueData: "GeoStitch.Project"' in installer
    assert 'Subkey: "Software\\Classes\\GeoStitch.Project\\shell\\open\\command"; ValueType: string; ValueName: ""; ValueData: """{app}\\GeoStitch.exe"" ""%1"""' in installer