import os
import threading
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from geostitch.app import MainWindow
from geostitch.vector import VectorGeometry


def test_geojson_import_loads_on_worker_and_tracks_status_progress(tmp_path, monkeypatch):
    import geostitch.vector_loader as vector_loader_module

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "points.geojson"
    path.write_text("{}", encoding="utf-8")
    started, release = threading.Event(), threading.Event()
    worker_threads = []
    completion_threads = []

    def read_geojson(_path):
        worker_threads.append(threading.get_ident())
        started.set()
        if not release.wait(3):
            raise OSError("Test GeoJSON reader timed out")
        return VectorGeometry(points=[(1.0, 2.0)]), "EPSG:4326"

    monkeypatch.setattr(vector_loader_module, "read_geojson", read_geojson)
    window = MainWindow(QSettings(str(tmp_path / "geojson.ini"), QSettings.Format.IniFormat))
    ui_thread = threading.get_ident()
    window._vector_loader.loaded.connect(lambda *_args: completion_threads.append(threading.get_ident()))
    window.show()
    try:
        window._import_geojson_paths([str(path)])
        assert started.wait(2)
        assert len(window.project.vector_layers) == 1
        assert worker_threads[0] != ui_thread
        assert window.statusBar().progress_bar.isVisible()
        assert window.statusBar().currentMessage() == "Loading GeoJSON layers for the world map..."

        release.set()
        deadline = monotonic() + 3
        while (window._vector_progress or window._geojson_import_count) and monotonic() < deadline:
            app.processEvents()
        assert not window._vector_progress
        assert not window._geojson_import_count
        assert window._vector_cache
        assert completion_threads == [ui_thread]
        assert not window.statusBar().progress_bar.isVisible()
        assert "Imported 1 GeoJSON layer" in window.statusBar().currentMessage()
    finally:
        release.set()
        window.close()


def test_mixed_drop_serializes_image_and_geojson_progress(tmp_path, monkeypatch):
    import geostitch.vector_loader as vector_loader_module

    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "image.png"
    image = QImage(12, 8, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    assert image.save(str(image_path))
    geojson_path = tmp_path / "points.geojson"
    geojson_path.write_text("{}", encoding="utf-8")
    started, release = threading.Event(), threading.Event()

    def read_geojson(_path):
        started.set()
        if not release.wait(3):
            raise OSError("Test GeoJSON reader timed out")
        return VectorGeometry(points=[(1.0, 2.0)]), "EPSG:4326"

    monkeypatch.setattr(vector_loader_module, "read_geojson", read_geojson)
    window = MainWindow(QSettings(str(tmp_path / "mixed.ini"), QSettings.Format.IniFormat))
    window.show()
    try:
        window._import_dropped_paths([str(image_path), str(geojson_path)])
        deadline = monotonic() + 3
        while not started.is_set() and monotonic() < deadline:
            app.processEvents()
        assert started.is_set()
        assert len(window.project.images) == 1
        assert len(window.project.vector_layers) == 1
        assert window.statusBar().progress_bar.isVisible()

        release.set()
        deadline = monotonic() + 3
        while (window._vector_progress or window._image_import_running) and monotonic() < deadline:
            app.processEvents()
        assert not window._vector_progress
        assert not window._image_import_running
        assert window._vector_cache
        assert not window.statusBar().progress_bar.isVisible()
        assert "Imported 1 GeoJSON layer" in window.statusBar().currentMessage()
    finally:
        release.set()
        window.close()
