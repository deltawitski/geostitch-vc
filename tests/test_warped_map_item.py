import pytest
from PySide6.QtCore import QPointF, QRectF, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication
from pyproj import Transformer

from geostitch.mapping import CRSMapping, MappingChain
from geostitch.map_view import WorldMapView
from geostitch.transforms import MatrixTransform
from geostitch.warped_map_item import WarpedMapImageItem


def geographic_image_chain():
    return MappingChain((
        MatrixTransform.from_similarity((10.0, 60.0, 0.0, 0.001), pixel_y_down=True),
        CRSMapping("EPSG:4326", "EPSG:3857"),
    ))


def checkerboard(width=1000, height=1000):
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#e7e7e7"))
    painter = QPainter(pixmap)
    edge = 100
    for row in range(height // edge):
        for column in range(width // edge):
            color = QColor("#287c69" if (row + column) % 2 else "#d6943c")
            painter.fillRect(column * edge, row * edge, edge, edge, color)
    painter.end()
    return pixmap


def test_warped_map_mapping_matches_pyproj_at_corners_edges_and_interior():
    app = QApplication.instance() or QApplication([])
    mapping = geographic_image_chain()
    item = WarpedMapImageItem(checkerboard(), mapping, QSize(1000, 1000))
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    for column in range(0, 1001, 100):
        for row in range(0, 1001, 100):
            longitude, latitude = MatrixTransform.from_similarity(
                (10, 60, 0, 0.001), pixel_y_down=True
            ).map_point(column, row)
            mercator_x, mercator_y = transformer.transform(longitude, latitude, errcheck=True)
            assert item.map_source_to_scene((column, row)) == pytest.approx((mercator_x, -mercator_y), abs=1e-7)
    assert item.boundingRect().contains(QPointF(*item.map_source_to_scene((500, 500))))


def test_map_image_tooltip_reports_selected_crs_operation_accuracy():
    app = QApplication.instance() or QApplication([])
    item = WarpedMapImageItem(checkerboard(32, 32), geographic_image_chain(), QSize(32, 32))

    assert "Popular Visualisation Pseudo-Mercator" in item.toolTip()
    assert "Reported operation accuracy: unknown" in item.toolTip()
    assert "Area of use: World." in item.toolTip()


def test_adaptive_mesh_subdivides_nonlinear_projection_to_device_error_target(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    view = WorldMapView()
    view.resize(900, 650)
    pixmap = checkerboard()
    view.set_world_images([{
        "id": "image", "pixmap": pixmap, "source_size": QSize(1000, 1000),
        "mapping_chain": geographic_image_chain(), "order": 0,
    }])
    view.show()
    app.processEvents()
    view.fit_items({"image"}, set(), set())
    app.processEvents()
    item = view._image_items["image"]
    image = QImage(900, 650, QImage.Format.Format_ARGB32)
    image.fill(QColor("#1b2321"))
    painter = QPainter(image)
    view._scene.render(painter, QRectF(0, 0, 900, 650), item.sceneBoundingRect())
    painter.end()
    colors = {image.pixelColor(x, y).rgba() for x in range(0, 900, 10) for y in range(0, 650, 10)}
    assert len(item._mesh) > 2
    assert not item._mesh_capped
    assert item._mesh_error_pixels <= item.DISPLAY_ERROR_PIXELS
    assert len(colors) >= 3
    assert QColor("#287c69").rgba() in colors
    assert QColor("#d6943c").rgba() in colors
    view.close()


def test_warped_raster_paints_the_color_at_its_projected_source_pixel(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    width = height = 1000
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#e7e7e7"))
    painter = QPainter(pixmap)
    painter.fillRect(0, 0, 500, 500, QColor("#287c69"))
    painter.fillRect(500, 500, 500, 500, QColor("#d6943c"))
    painter.end()
    view = WorldMapView()
    view.resize(900, 650)
    view.set_world_images([{"id": "image", "pixmap": pixmap, "source_size": QSize(width, height),
                            "mapping_chain": geographic_image_chain()}])
    view.show()
    app.processEvents()
    view.fit_items({"image"}, set(), set())
    app.processEvents()
    item = view._image_items["image"]
    screenshot = view.viewport().grab().toImage()
    for source, color in (((200, 200), QColor("#287c69")), ((800, 800), QColor("#d6943c"))):
        scene_x, scene_y = item.map_source_to_scene(source)
        viewport_point = view.mapFromScene(QPointF(scene_x, scene_y))
        assert screenshot.pixelColor(viewport_point) == color
    view.close()


def test_mesh_budget_limit_is_exposed_as_view_warning(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    monkeypatch.setattr(WarpedMapImageItem, "MAX_CELLS", 1)
    view = WorldMapView()
    view.resize(500, 350)
    view.set_world_images([{"id": "limited", "pixmap": checkerboard(), "source_size": QSize(1000, 1000),
                            "mapping_chain": geographic_image_chain()}])
    warnings = []
    view.image_mapping_warning.connect(warnings.append)
    view.show()
    app.processEvents()
    view.fit_items({"limited"}, set(), set())
    app.processEvents()
    assert warnings and warnings[-1] == frozenset({"limited"})
    assert view._image_items["limited"]._mesh_capped
    view.close()


def test_invalid_crs_domain_is_clipped_without_nonfinite_map_bounds():
    app = QApplication.instance() or QApplication([])
    mapping = MappingChain((
        MatrixTransform.from_similarity((0.0, 86.0, 0.0, 0.01), pixel_y_down=True),
        CRSMapping("EPSG:4326", "EPSG:3857"),
    ))
    item = WarpedMapImageItem(checkerboard(100, 200), mapping, QSize(100, 200))
    bounds = item.boundingRect()
    assert all(map(abs, (bounds.left(), bounds.top(), bounds.right(), bounds.bottom())))
    assert bounds.width() > 0 and bounds.height() > 0
    assert item.map_source_to_scene((0, 100))[0] == pytest.approx(0, abs=1e-7)
    with pytest.raises(ValueError):
        item.map_source_to_scene((0, 0))


def test_antimeridian_crossing_clips_seam_triangles_and_surfaces_warning(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    mapping = MappingChain((
        MatrixTransform.from_similarity((179.0, 10.0, 0, 0.002), pixel_y_down=True),
        CRSMapping("EPSG:4326", "EPSG:3857"),
    ))
    view = WorldMapView()
    view.resize(700, 450)
    pixmap = checkerboard()
    view.set_world_images([{"id": "date-line", "pixmap": pixmap, "source_size": QSize(1000, 1000),
                            "mapping_chain": mapping}])
    warnings = []
    view.image_mapping_warning.connect(warnings.append)
    view.show()
    app.processEvents()
    item = view._image_items["date-line"]
    assert item._domain_clipped
    assert item.crosses_antimeridian
    assert item.fit_scene_bounds.width() < 500_000
    assert len(view._wrapped_image_items) == 2
    for _source, target in item._mesh:
        assert max(point[0] for point in target) - min(point[0] for point in target) < 20_037_508.342789244
    assert item.map_source_to_scene((100, 0))[0] > 19_000_000
    assert item.map_source_to_scene((900, 0))[0] < -19_000_000
    eastern_copy = next(copy for copy in view._wrapped_image_items if copy._world_offset > 0)
    assert eastern_copy.map_source_to_scene((900, 0))[0] == pytest.approx(
        item.map_source_to_scene((900, 0))[0] + 2 * view.WORLD_ORIGIN,
    )
    view.fit_items({"date-line"}, set(), set())
    app.processEvents()
    center = view.mapToScene(view.viewport().rect().center())
    assert center.x() == pytest.approx(view.WORLD_ORIGIN, abs=150_000)
    assert warnings and warnings[-1] == frozenset({"date-line"})
    view.close()