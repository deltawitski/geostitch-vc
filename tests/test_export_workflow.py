import os
import threading
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from geostitch.app import MainWindow


@pytest.fixture
def window(tmp_path):
    application = QApplication.instance() or QApplication([])
    main_window = MainWindow(QSettings(str(tmp_path / "export.ini"), QSettings.Format.IniFormat))
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 4), (10, 20, 30)).save(source)
    image = main_window.project.add_image(source)
    image.world_x, image.world_y, image.world_scale = 100, 200, 2
    main_window._mark_project_saved()
    yield main_window
    if main_window._export_running:
        main_window.cancel_export()
        deadline = monotonic() + 5
        while main_window._export_running and monotonic() < deadline:
            application.processEvents()
    main_window._mark_project_saved()
    main_window.close()


def test_window_stays_responsive_and_export_keeps_starting_snapshot(window, tmp_path, monkeypatch):
    import geostitch.export as export_module

    app = QApplication.instance()
    started, release = threading.Event(), threading.Event()
    original = export_module._export_image
    threads = []

    def blocked_export(*args):
        threads.append(threading.get_ident())
        started.set()
        if not release.wait(3):
            raise OSError("Test export timed out")
        return original(*args)

    monkeypatch.setattr(export_module, "_export_image", blocked_export)
    directory = tmp_path / "output"
    try:
        window._start_export(directory, ".png")
        assert started.wait(2)
        assert threads[0] != threading.get_ident()
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        app.processEvents()
        assert heartbeat == [True]
        assert not window.shortcut_actions["export_images"].isEnabled()
        assert window.shortcut_actions["cancel_export"].isEnabled()
        window.project.images[0].world_x = 999
        release.set()
        deadline = monotonic() + 3
        while window._export_running and monotonic() < deadline:
            app.processEvents()
        assert not window._export_running
        values = [float(value) for value in (directory / "source_georef.pgw").read_text().split()]
        assert values[4] == 101
        assert window.project.images[0].world_x == 999
        assert window._export_dialog is None
        assert window.shortcut_actions["open_export_folder"].isEnabled()
        assert not window.shortcut_actions["cancel_export"].isEnabled()
    finally:
        release.set()


def test_close_requests_cancel_and_does_not_interrupt_current_image(window, tmp_path, monkeypatch):
    import geostitch.export as export_module

    app = QApplication.instance()
    source = tmp_path / "second.png"
    Image.new("RGB", (8, 4)).save(source)
    second = window.project.add_image(source)
    second.world_x, second.world_y = 100, 200
    window._mark_project_saved()
    started, release = threading.Event(), threading.Event()
    original = export_module._export_image
    files = []

    def blocked_export(image, *args):
        files.append(image.id)
        started.set()
        if not release.wait(3):
            raise OSError("Test export timed out")
        return original(image, *args)

    monkeypatch.setattr(export_module, "_export_image", blocked_export)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    directory = tmp_path / "output"
    window.show()
    try:
        window._start_export(directory, ".png")
        assert started.wait(2)
        assert not window.close()
        assert window.isVisible()
        assert window._export_running
        release.set()
        deadline = monotonic() + 3
        while window._export_running and monotonic() < deadline:
            app.processEvents()
        assert not window._export_running
        assert len(files) == 1
        assert "cancelled" in window.statusBar().currentMessage()
        assert (directory / "source_georef.png").exists()
        assert not (directory / "second_georef.png").exists()
    finally:
        release.set()


@pytest.mark.parametrize("replace", [False, True])
def test_existing_output_requires_confirmation(window, tmp_path, monkeypatch, replace):
    directory = tmp_path / "output"
    directory.mkdir()
    existing = directory / "source_georef.pgw"
    existing.write_text("Existing metadata", encoding="utf-8")
    started = []
    prompts = []
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("PNG + world file (.png/.pgw)", True))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(directory))

    def confirm(box):
        prompts.append(box.detailedText())
        return QMessageBox.StandardButton.Yes if replace else QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", confirm)
    monkeypatch.setattr(window, "_start_export", lambda *args, **kwargs: started.append((args, kwargs)))
    window.export_georeferenced_images()
    assert len(prompts) == 1
    assert str(existing) in prompts[0]
    assert bool(started) is replace
    if replace:
        assert started[0][1]["overwrite"]
    assert existing.read_text() == "Existing metadata"


def test_worker_error_releases_export_controls(window, tmp_path, monkeypatch):
    import geostitch.export as export_module

    app = QApplication.instance()
    errors = []

    def fail_export(*args, **kwargs):
        raise OSError("Destination unavailable")

    monkeypatch.setattr(export_module, "export_project_images", fail_export)
    monkeypatch.setattr(window, "_show_error", errors.append)
    window._start_export(tmp_path / "output", ".png")
    deadline = monotonic() + 3
    while window._export_running and monotonic() < deadline:
        app.processEvents()
    assert not window._export_running
    assert window._export_dialog is None
    assert len(errors) == 1 and "Destination unavailable" in errors[0]
    assert window.shortcut_actions["export_images"].isEnabled()


def test_export_folder_action_opens_completed_destination(window, tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    opened = []
    window._last_export_directory = tmp_path / "completed output"
    window._update_hints()
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    window.shortcut_actions["open_export_folder"].trigger()
    assert opened == [str(window._last_export_directory.resolve()).replace("\\", "/")]