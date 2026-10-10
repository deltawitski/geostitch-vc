import os
import threading
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPointF, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from geostitch.image_tiles import TileLoader, visible_tiles
from geostitch.image_view import ImageViewer
from PySide6.QtCore import QSettings
import pytest


@pytest.fixture
def window(tmp_path, monkeypatch):
    from geostitch.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "tiles.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(tmp_path / "source.png")
    preview = QPixmap(100, 60)
    preview.fill(QColor("red"))
    window._image_pixmaps[image.id] = preview
    window._image_sizes[image.id] = QSize(1000, 600)
    window.DETAIL_CACHE_BYTES = 512 * 512 * 4
    window._refresh_all()
    window._on_canvas_image_selected(image.id)
    window.show()
    window.image_view_dock.setFloating(True)
    window.image_view_dock.resize(400, 300)
    window.image_view_dock.show()
    requests = []
    monkeypatch.setattr(window._tile_loader, "request", lambda key, path, region: requests.append((key, region)))
    app.processEvents()
    window.image_viewer.resetTransform()
    window.image_viewer.scale(2, 2)
    window.image_viewer.centerOn(250, 250)
    window._refresh_detail_demand()
    yield app, window, image, requests
    window.close()


def test_oversized_source_requests_native_tiles_not_whole_image(window):
    from PySide6.QtGui import QImage
    from geostitch.image_decode import _with_source_size

    app, window, image, requests = window
    assert not window._detail_requested_ids
    assert requests
    key, region = requests[-1]
    tile = QImage(region.size(), QImage.Format.Format_RGB32)
    tile.fill(QColor("blue"))
    window._on_tile_loaded(key, _with_source_size(tile, QSize(1000, 600)), "")
    assert window.image_viewer.resolution_label.text() == "Native detail"
    assert window.image_viewer._tile_items
    total = sum(pixmap.width() * pixmap.height() * 4 for pixmap in window._tile_pixmaps.values())
    assert total <= window.DETAIL_CACHE_BYTES
    assert window.canvas._layers[image.id].pixmap().width() == 100


def test_tile_pan_rejects_late_region_and_hide_releases_cache(window):
    from PySide6.QtGui import QImage
    from geostitch.image_decode import _with_source_size

    app, window, image, requests = window
    key, region = requests[-1]
    window.image_viewer.centerOn(850, 450)
    window._refresh_detail_demand()
    assert key not in window._tile_requested_keys
    tile = _with_source_size(QImage(region.size(), QImage.Format.Format_RGB32), QSize(1000, 600))
    window._on_tile_loaded(key, tile, "")
    assert key not in window._tile_pixmaps
    new_key, new_region = requests[-1]
    decoded = _with_source_size(QImage(new_region.size(), QImage.Format.Format_RGB32), QSize(1000, 600))
    window._on_tile_loaded(new_key, decoded, "")
    assert window._tile_pixmaps
    window.image_view_dock.hide()
    window._refresh_detail_demand()
    assert not window._tile_pixmaps
    assert not window.image_viewer._tile_items


def test_transparent_native_tile_does_not_double_blend_preview():
    app = QApplication.instance() or QApplication([])
    view = ImageViewer()
    view.resize(500, 400)
    pixels = QPixmap(100, 80)
    pixels.fill(QColor(255, 0, 0, 120))
    view.set_image("image", pixels, [], [], "inside", False, source_size=QSize(1000, 800))
    view.show()
    app.processEvents()
    view.fit_image()
    try:
        position = view.mapFromScene(QPointF(500, 400))
        before = view.viewport().grab().toImage().pixelColor(position)
        tile = QPixmap(200, 200)
        tile.fill(QColor(255, 0, 0, 120))
        view.set_detail_tiles({(400, 300, 200, 200): tile})
        after = view.viewport().grab().toImage().pixelColor(position)
        assert after == before
    finally:
        view.close()


def test_real_oversized_image_loads_visible_tiles_on_worker(tmp_path, monkeypatch):
    from geostitch.app import MainWindow
    import geostitch.image_tiles as tiles_module

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "large.jpg"
    with Image.new("RGB", (3000, 2000), (10, 20, 30)) as source:
        source.save(path)
    original = tiles_module.read_region
    threads = []

    def decode(*args):
        threads.append(threading.get_ident())
        return original(*args)

    monkeypatch.setattr(tiles_module, "read_region", decode)
    window = MainWindow(QSettings(str(tmp_path / "real-tiles.ini"), QSettings.Format.IniFormat))
    window.DETAIL_CACHE_BYTES = 2 * 512 * 512 * 4
    try:
        window._import_image_paths([str(path)])
        image = window.project.images[0]
        window._on_canvas_image_selected(image.id)
        window._mark_project_saved()
        window.show()
        window.image_view_dock.setFloating(True)
        window.image_view_dock.resize(500, 400)
        window.image_view_dock.show()
        app.processEvents()
        window.image_viewer.resetTransform()
        window.image_viewer.centerOn(1500, 1000)
        window._refresh_detail_demand()
        deadline = monotonic() + 5
        while not window.image_viewer._tile_items and monotonic() < deadline:
            app.processEvents()
        assert window.image_viewer._tile_items
        assert threads and all(thread != threading.get_ident() for thread in threads)
        assert not window._detail_pixmaps
        assert not window._is_project_modified()
        assert sum(pixmap.width() * pixmap.height() * 4 for pixmap in window._tile_pixmaps.values()) <= window.DETAIL_CACHE_BYTES
    finally:
        window.close()


def test_tiles_remain_below_clip_mask_and_do_not_reset_point_drag():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    view = ImageViewer()
    view.resize(800, 600)
    preview = QPixmap(100, 60)
    preview.fill(QColor("red"))
    view.set_image("image", preview, [("point", "Point", 200, 200)],
                   [[0, 0], [500, 0], [500, 600], [0, 600]], "inside", True, source_size=QSize(1000, 600))
    view.show()
    app.processEvents()
    view.fit_image()
    changed = []
    finished = []
    view.point_observation_changed.connect(lambda *args: changed.append(args))
    view.point_observation_interaction_finished.connect(lambda *args: finished.append(args))
    try:
        start = view.mapFromScene(QPointF(200, 200))
        end = view.mapFromScene(QPointF(300, 250))
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        app.sendEvent(view.viewport(), move)
        marker = view._point_items["point"]
        tile = QPixmap(200, 200)
        tile.fill(QColor("blue"))
        view.set_detail_tiles({(600, 100, 200, 200): tile})
        assert view._point_items["point"] is marker
        assert view._dragged_point_id == "point"
        position = view.mapFromScene(QPointF(700, 200))
        color = view.viewport().grab().toImage().pixelColor(position)
        assert color.blue() < 255
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        expected = view.mapToScene(end)
        assert changed[-1][2:] == pytest.approx((expected.x(), expected.y()))
        assert finished == [("image", "point")]
    finally:
        view.close()


def test_whole_native_and_tiles_share_budget_without_retaining_old_detail(window, tmp_path):
    from PySide6.QtGui import QImage
    from geostitch.image_decode import _with_source_size
    from geostitch.models import GroundControlPoint, PointConstraint

    app, window, first, requests = window
    second = window.project.add_image(tmp_path / "second.png")
    window._image_pixmaps[second.id] = window._image_pixmaps[first.id]
    window._image_sizes[second.id] = QSize(10000, 6000)
    points = []
    for image in (first, second):
        point = GroundControlPoint.create(image.name)
        point.set_observation(image.id, 250, 250)
        window.project.add_control_point(point)
        points.append(point)
    link = PointConstraint.create(points[0].id, points[1].id)
    window.project.add_constraint(link)
    native_cost = 1000 * 600 * 4
    window.DETAIL_CACHE_BYTES = native_cost + 512 * 512 * 4
    window._detail_loader.request = lambda *args: None
    window._refresh_all()
    window.link_review_dock.setFloating(True)
    window.link_review_dock.resize(1000, 650)
    window.link_review_dock.show()
    app.processEvents()
    window._select_constraint(link.id)
    for view in window.link_review.views:
        view.resetTransform()
        view.scale(2, 2)
        view.centerOn(250, 250)
    window._refresh_detail_demand()
    assert window._detail_requested_ids == {first.id}
    assert window._tile_budget == 512 * 512 * 4
    native = QImage(1000, 600, QImage.Format.Format_RGB32)
    native.fill(0)
    window._on_detail_loaded((window._image_load_generation, first.id, first.path), native, "")
    for key in list(window._tile_requested_keys):
        tile = QImage(key[5], key[6], QImage.Format.Format_RGB32)
        tile.fill(0)
        window._on_tile_loaded(key, _with_source_size(tile, QSize(10000, 6000)), "")
    total = sum(pixmap.width() * pixmap.height() * 4
                for pixmap in [*window._detail_pixmaps.values(), *window._tile_pixmaps.values()])
    assert total <= window.DETAIL_CACHE_BYTES
    window._refresh_detail_demand()
    assert window._tile_budget == 512 * 512 * 4
    window.link_review.views[0].resetTransform()
    window.link_review.views[0].scale(0.01, 0.01)
    window._refresh_detail_demand()
    assert first.id not in window._detail_pixmaps


def test_visible_tiles_clamp_align_and_prioritize_view_center():
    tiles = visible_tiles(QSize(1200, 800), QRectF(480, 400, 500, 350))
    assert tiles[0] == QRect(512, 512, 512, 288)
    assert len(tiles) == 4
    assert all(tile.x() % 512 == 0 and tile.y() % 512 == 0 for tile in tiles)
    assert not visible_tiles(QSize(1200, 800), QRectF(-100, -100, 10, 10))
    edge = visible_tiles(QSize(1200, 800), QRectF(1100, 700, 100, 100))
    assert edge == [QRect(1024, 512, 176, 288)]


def test_tile_overlay_uses_source_origin_without_resetting_markers_or_zoom():
    app = QApplication.instance() or QApplication([])
    view = ImageViewer()
    preview = QPixmap(100, 60)
    preview.fill(QColor("red"))
    native = QPixmap(50, 40)
    native.fill(QColor("blue"))
    view.resize(800, 600)
    view.set_image("image", preview, [("point", "Point", 700, 400)], [], "inside", False,
                   source_size=QSize(1000, 600))
    view.show()
    app.processEvents()
    view.fit_image()
    marker = view._point_items["point"]
    before = view.transform()
    try:
        view.set_detail_tiles({(200, 100, 50, 40): native})
        assert view._point_items["point"] is marker
        assert view.transform() == before
        assert view._tile_items[(200, 100, 50, 40)].pos() == QPointF(200, 100)
        pixel = view.mapFromScene(QPointF(225, 120))
        assert view.viewport().grab().toImage().pixelColor(pixel) == QColor("blue")
        assert view.resolution_label.text() == "Native detail"
        view.set_detail_tiles({})
        assert view.resolution_label.text() == "Preview"
    finally:
        view.close()


def test_tile_loader_decodes_region_off_ui_thread_and_delivers_on_ui(tmp_path, monkeypatch):
    import geostitch.image_tiles as tiles_module

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "source.png"
    with Image.new("RGB", (1000, 600), (10, 20, 30)) as source:
        source.save(path)
    decoder = tiles_module.read_region
    threads = []
    results = []

    def decode(*args):
        threads.append(threading.get_ident())
        return decoder(*args)

    monkeypatch.setattr(tiles_module, "read_region", decode)
    loader = TileLoader()
    loader.loaded.connect(lambda *args: results.append((threading.get_ident(), args)))
    loader.request((1, "image"), str(path), QRect(200, 100, 50, 40))
    deadline = monotonic() + 3
    while not results and monotonic() < deadline:
        app.processEvents()
    assert threads and threads[0] != threading.get_ident()
    assert results[0][0] == threading.get_ident()
    assert results[0][1][1].size() == QSize(50, 40)
    assert results[0][1][2] == ""