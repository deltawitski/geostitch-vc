import os
import threading
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize, QSettings
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from geostitch.app import MainWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "detail.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(tmp_path / "image.png")
    preview = QPixmap(100, 60)
    preview.fill()
    window._image_pixmaps[image.id] = preview
    window._image_sizes[image.id] = QSize(1000, 600)
    window._refresh_all()
    window._on_canvas_image_selected(image.id)
    window.show()
    window.image_view_dock.setFloating(True)
    window.image_view_dock.resize(600, 400)
    window.image_view_dock.show()
    requests = []
    monkeypatch.setattr(window._detail_loader, "request", lambda key, path: requests.append((key, path)))
    app.processEvents()
    yield app, window, image, requests
    window.close()


def test_detail_requested_only_when_zoom_exceeds_preview_density(window):
    app, window, image, requests = window
    window.image_viewer.resetTransform()
    window.image_viewer.scale(0.05, 0.05)
    window._refresh_detail_demand()
    assert not window._detail_requested_ids
    requests.clear()
    window.image_viewer.resetTransform()
    window._refresh_detail_demand()
    assert window._detail_requested_ids == {image.id}
    assert len(requests) == 1
    before = window.image_viewer.transform()
    decoded = QImage(1000, 600, QImage.Format.Format_RGB32)
    decoded.fill(0)
    window._on_detail_loaded(requests[0][0], decoded, "")
    assert window.image_viewer.transform() == before
    assert window.image_viewer._image_rect.width() == 1000
    assert window._image_pixmaps[image.id].width() == 100
    assert window._detail_pixmaps[image.id].width() == 1000
    window.image_viewer.resetTransform()
    window.image_viewer.scale(0.05, 0.05)
    window._refresh_detail_demand()
    assert not window._detail_pixmaps
    assert window.image_viewer.resolution_label.text() == "Preview"


def test_detail_budget_skips_oversized_native_image(window):
    app, window, image, requests = window
    window.DETAIL_CACHE_BYTES = 100
    window.image_viewer.resetTransform()
    requests.clear()
    window._refresh_detail_demand()
    assert not requests
    assert not window._detail_requested_ids


def test_detail_result_is_discarded_after_deselection(window):
    app, window, image, requests = window
    window.image_viewer.resetTransform()
    window._refresh_detail_demand()
    key = requests[-1][0]
    window.deselect_all()
    window._refresh_detail_demand()
    decoded = QImage(1000, 600, QImage.Format.Format_RGB32)
    window._on_detail_loaded(key, decoded, "")
    assert not window._detail_pixmaps


def test_hidden_view_releases_full_resolution_image(window):
    app, window, image, requests = window
    window.image_viewer.resetTransform()
    window._refresh_detail_demand()
    decoded = QImage(1000, 600, QImage.Format.Format_RGB32)
    decoded.fill(0)
    window._on_detail_loaded(requests[-1][0], decoded, "")
    window.image_view_dock.hide()
    window._refresh_detail_demand()
    assert not window._detail_pixmaps
    assert window.image_viewer.resolution_label.text() == "Preview"


def test_link_review_shares_budget_and_releases_images_when_hidden(window, tmp_path):
    from geostitch.models import GroundControlPoint, PointConstraint

    app, window, first, requests = window
    second = window.project.add_image(tmp_path / "second.png")
    window._image_pixmaps[second.id] = window._image_pixmaps[first.id]
    window._image_sizes[second.id] = QSize(1000, 600)
    points = []
    for image in (first, second):
        point = GroundControlPoint.create(image.name)
        point.set_observation(image.id, 40, 60)
        window.project.add_control_point(point)
        points.append(point)
    link = PointConstraint.create(points[0].id, points[1].id)
    window.project.add_constraint(link)
    window.DETAIL_CACHE_BYTES = 1000 * 600 * 4
    window._refresh_all()
    window.link_review_dock.setFloating(True)
    window.link_review_dock.resize(1000, 650)
    window.link_review_dock.show()
    app.processEvents()
    window._select_constraint(link.id)
    for view in window.link_review.views:
        view.resetTransform()
    requests.clear()
    window._detail_requested_ids.clear()
    window._detail_loading_keys.clear()
    window._refresh_detail_demand()
    assert window._detail_requested_ids == {first.id}
    assert len(requests) == 1
    decoded = QImage(1000, 600, QImage.Format.Format_RGB32)
    decoded.fill(0)
    window._on_detail_loaded(requests[0][0], decoded, "")
    assert sum(pixmap.width() * pixmap.height() * 4 for pixmap in window._detail_pixmaps.values()) <= window.DETAIL_CACHE_BYTES
    assert window.link_review.views[0].resolution_label.text() == "Native resolution"
    assert window.link_review.views[1].resolution_label.text() == "Preview"
    window.link_review_dock.hide()
    window._refresh_detail_demand()
    assert not window._detail_pixmaps
    assert not any(view.has_image() for view in window.link_review.views)


def test_detail_decode_failure_does_not_remove_usable_preview(window):
    app, window, image, requests = window
    window.image_viewer.resetTransform()
    window._refresh_detail_demand()
    key = requests[-1][0]
    window._on_detail_loaded(key, QImage(), "Cannot decode native detail")
    assert window._detail_failures[image.id] == "Cannot decode native detail"
    assert image.id in window._image_pixmaps
    assert window.image_viewer.has_image()
    before = len(requests)
    window._refresh_detail_demand()
    assert len(requests) == before


def test_real_native_decoder_upgrades_visible_view_without_project_edits(tmp_path, monkeypatch, wait_for_image_import):
    from PIL import Image

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "large.jpg"
    with Image.new("RGB", (3000, 2000), (10, 20, 30)) as source:
        source.save(path)
    original_decode = MainWindow._read_image_data
    threads = []

    def decode(path):
        threads.append(threading.get_ident())
        return original_decode(path)

    monkeypatch.setattr(MainWindow, "_read_image_data", staticmethod(decode))
    window = MainWindow(QSettings(str(tmp_path / "real.ini"), QSettings.Format.IniFormat))
    try:
        window._import_image_paths([str(path)])
        wait_for_image_import(window)
        image = window.project.images[0]
        assert window._image_pixmaps[image.id].width() == 2048
        window._on_canvas_image_selected(image.id)
        window._mark_project_saved()
        window.show()
        window.image_view_dock.setFloating(True)
        window.image_view_dock.resize(600, 400)
        window.image_view_dock.show()
        app.processEvents()
        window.image_viewer.resetTransform()
        window._refresh_detail_demand()
        before = window.image_viewer.transform()
        deadline = monotonic() + 5
        while image.id not in window._detail_pixmaps and monotonic() < deadline:
            app.processEvents()
        assert image.id in window._detail_pixmaps
        assert threads and all(thread != threading.get_ident() for thread in threads)
        assert window.image_viewer.transform() == before
        assert window.image_viewer.resolution_label.text() == "Native resolution"
        assert not window._is_project_modified()
        assert window.canvas._layers[image.id].pixmap().width() == 2048
    finally:
        window.close()