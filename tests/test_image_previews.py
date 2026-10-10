import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, QSize
from PySide6.QtCore import QSettings, QEvent, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from geostitch.canvas import ImageCanvas
from geostitch.image_view import ImageViewer
from geostitch.map_view import WorldMapView
from geostitch.masking import masked_pixmap


def preview():
    app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(100, 60)
    pixmap.fill(QColor("red"))
    return app, pixmap


def test_canvas_preview_uses_original_bounds_and_marker_coordinates():
    app, pixmap = preview()
    canvas = ImageCanvas()
    try:
        canvas.set_layers([{"id": "image", "pixmap": pixmap, "source_size": QSize(1000, 600),
                            "position_x": 200, "position_y": 50, "rotation": 0, "scale": 2,
                            "points": [("point", 750, 400)]}])
        item = canvas._layers["image"]
        assert item.boundingRect() == QRectF(0, 0, 1000, 600)
        assert item.mapToScene(QPointF(1000, 600)) == QPointF(2200, 1250)
        assert canvas._points[("image", "point")] == (750, 400)
        assert canvas._point_items[("image", "point")].pos() == QPointF(1700, 850)
        assert canvas.image_transform("image") == (200, 50, 0, 2)
    finally:
        canvas.close()


def test_reduced_bitmap_renders_and_canvas_click_returns_source_pixels():
    app, pixmap = preview()
    canvas = ImageCanvas()
    canvas.resize(800, 600)
    canvas.set_layers([{"id": "image", "pixmap": pixmap, "source_size": QSize(1000, 600),
                        "position_x": 0, "position_y": 0, "rotation": 0, "scale": 1, "points": []}])
    canvas.set_active_point("point")
    clicks = []
    canvas.image_clicked.connect(lambda *args: clicks.append(args))
    canvas.show()
    app.processEvents()
    canvas.fit_images()
    try:
        position = canvas.mapFromScene(QPointF(900, 500))
        assert canvas.viewport().grab().toImage().pixelColor(position) == QColor("red")
        expected = canvas.mapToScene(position)
        QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=position)
        assert clicks[0][0] == "image"
        assert abs(clicks[0][1] - expected.x()) < 1
        assert abs(clicks[0][2] - expected.y()) < 1
    finally:
        canvas.close()


def test_image_view_preview_preserves_original_pixel_space():
    app, pixmap = preview()
    view = ImageViewer()
    try:
        view.set_image("image", pixmap, [("point", "Point", 750, 400)], [], "inside", False,
                       source_size=QSize(1000, 600))
        assert view._image_rect == QRectF(0, 0, 1000, 600)
        assert view._point_items["point"].pos() == QPointF(750, 400)
    finally:
        view.close()


def test_preview_marker_drag_keeps_source_coordinates_during_native_swap():
    app, pixmap = preview()
    view = ImageViewer()
    view.resize(800, 600)
    size = QSize(1000, 600)
    view.set_image("image", pixmap, [("point", "Point", 750, 400)], [], "inside", False, source_size=size)
    view.show()
    app.processEvents()
    view.fit_image()
    changed = []
    view.point_observation_changed.connect(lambda *args: changed.append(args))
    try:
        start = view.mapFromScene(QPointF(750, 400))
        end = view.mapFromScene(QPointF(850, 450))
        expected = view.mapToScene(end)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
                            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        app.sendEvent(view.viewport(), event)
        before = view.transform()
        native = QPixmap(1000, 600)
        native.fill(QColor("red"))
        view.set_image("image", native, [("point", "Point", expected.x(), expected.y())], [], "inside", False,
                       source_size=size)
        assert view.transform() == before
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert changed[-1][:2] == ("image", "point")
        assert abs(changed[-1][2] - expected.x()) < 1
        assert abs(changed[-1][3] - expected.y()) < 1
        assert view.resolution_label.text() == "Native resolution"
    finally:
        view.close()


def test_map_preview_footprint_uses_original_dimensions():
    app, pixmap = preview()
    view = WorldMapView()
    try:
        view.set_world_images([{"id": "image", "pixmap": pixmap, "source_size": QSize(1000, 600),
                                "mercator_corners": [(100, 200), (2100, 200), (100, -1000)]}])
        item = view._image_items["image"]
        assert item.mapToScene(QPointF(1000, 600)) == QPointF(2100, 1000)
        assert item.transform().m11() == 2
    finally:
        view.close()


def test_preview_mask_scales_original_polygon_to_bitmap():
    app, pixmap = preview()
    result = masked_pixmap(pixmap, [[0, 0], [500, 0], [500, 600], [0, 600]], "inside", QSize(1000, 600))
    assert result.toImage().pixelColor(20, 20).alpha() == 255
    assert result.toImage().pixelColor(80, 20).alpha() == 0


def test_preview_arrangement_and_world_footprints_use_original_size(tmp_path):
    from geostitch.app import MainWindow

    app, pixmap = preview()
    window = MainWindow(QSettings(str(tmp_path / "dimensions.ini"), QSettings.Format.IniFormat))
    first = window.project.add_image(tmp_path / "first.png")
    second = window.project.add_image(tmp_path / "second.png")
    second.rotation = 90
    window._image_pixmaps = {first.id: pixmap, second.id: pixmap}
    window._image_sizes = {first.id: QSize(1000, 600), second.id: QSize(800, 400)}
    window.project.crs = "EPSG:3857"
    first.world_x, first.world_y, first.world_scale = 100, 200, 2
    window._refresh_all()
    try:
        window.arrange_images()
        bounds = window.canvas._layers[second.id].sceneBoundingRect()
        assert abs(bounds.left() - 1040) < 1
        assert abs(bounds.width() - 400) < 1
        map_item = window.world_map._image_items[first.id]
        assert map_item.mapToScene(QPointF(1000, 600)) == QPointF(2100, 1000)
    finally:
        window.close()


def test_preview_import_does_not_reduce_export_resolution(tmp_path, monkeypatch):
    from PIL import Image
    from geostitch.app import MainWindow
    from geostitch.image_decode import read_preview
    from geostitch.export import export_project_images

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "source.png"
    Image.new("RGB", (1200, 800), (10, 20, 30)).save(path)
    window = MainWindow(QSettings(str(tmp_path / "export.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(window, "_read_preview_data", lambda path: read_preview(path, 128))
    try:
        window._import_image_paths([str(path)])
        image = window.project.images[0]
        assert window._image_pixmaps[image.id].width() <= 128
        assert window._image_sizes[image.id] == QSize(1200, 800)
        image.world_x, image.world_y = 100, 200
        result = export_project_images(window.project, tmp_path / "output", ".png")
        with Image.open(result.written[0]) as exported:
            assert exported.size == (1200, 800)
    finally:
        window.close()