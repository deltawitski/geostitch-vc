import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPixmap, QTransform, QWheelEvent
from PySide6.QtCore import QBuffer, QEvent, QIODevice, QPoint, QSettings, QPointF, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from pyproj import Transformer
import pytest

from geostitch.app import MainWindow
from geostitch.georeference import GeoreferenceResult, _world_compatibility_transform
from geostitch.mapping import ImageMapping
from geostitch.map_view import WorldMapView
from geostitch.models import GroundControlPoint, Project, VectorLayer
from geostitch.transforms import MatrixTransform


@pytest.mark.parametrize("direction", [1, -1])
def test_world_map_wheel_zoom_stops_at_limits_and_can_reverse(monkeypatch, direction):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    view = WorldMapView()
    view.resize(640, 420)
    view.show()
    app.processEvents()
    position = QPointF(view.viewport().rect().center())

    def scroll(sign):
        event = QWheelEvent(position, position, QPoint(), QPoint(0, sign * 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        view.wheelEvent(event)
        view._zoom_animation.setCurrentTime(view.ZOOM_ANIMATION_MS)

    try:
        minimum, maximum = view._scale_limits()
        for step in range(200):
            scroll(direction)
        limit = maximum if direction > 0 else minimum
        assert view.transform().m11() == pytest.approx(limit)
        before = view.view_state()
        scroll(direction)
        assert view.view_state() == before
        scroll(-direction)
        assert minimum <= view.transform().m11() <= maximum
        assert view.transform().m11() != pytest.approx(limit)
    finally:
        view.close()


@pytest.mark.parametrize("device_ratio", [1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize("fractional_zoom", [8.0, 8.25, 8.75, 12.0])
def test_osm_tile_zoom_does_not_upscale_source_pixels(monkeypatch, device_ratio, fractional_zoom):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    view = WorldMapView()
    monkeypatch.setattr(view.viewport(), "devicePixelRatioF", lambda: device_ratio)
    scale = 2 ** fractional_zoom / 156543.03392804097
    try:
        zoom = view._zoom_for_scale(scale)
        tile_span = 2 * view.WORLD_ORIGIN / (1 << zoom)
        physical_pixels_per_source_pixel = tile_span * scale * device_ratio / view.TILE_SIZE
        assert physical_pixels_per_source_pixel <= 1.0 + 1e-9
        assert physical_pixels_per_source_pixel > 0.5 - 1e-9
    finally:
        view.close()


@pytest.mark.parametrize("scale", [1e-300, 1e300])
def test_saved_map_view_clamps_extreme_finite_scales(monkeypatch, scale):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    view = WorldMapView()
    try:
        minimum, maximum = view._scale_limits()
        view.restore_view_state({"center": [1e300, -1e300], "scale": scale})
        expected = minimum if scale < minimum else maximum
        assert view.transform().m11() == pytest.approx(expected)
        assert abs(view.view_state()["center"][0]) <= 3 * view.WORLD_ORIGIN + 1 / expected
        assert abs(view.view_state()["center"][1]) <= view.WORLD_ORIGIN + 1 / expected
    finally:
        view.close()


@pytest.mark.parametrize("state", [
    {"center": [0, 0], "scale": float("inf")},
    {"center": [0, 0], "scale": float("nan")},
    {"center": [float("inf"), 0], "scale": 1},
    {"center": [0, float("nan")], "scale": 1},
])
def test_saved_map_view_rejects_nonfinite_values(monkeypatch, state):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    view = WorldMapView()
    try:
        before = view.view_state()
        view.restore_view_state(state)
        assert view.view_state() == before
    finally:
        view.close()


def test_fit_and_dpi_changes_respect_overzoom_limit(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    view = WorldMapView()
    view.resize(640, 420)
    pixmap = QPixmap(10, 10)
    pixmap.fill(QColor("white"))
    view.set_world_images([{"id": "tiny", "pixmap": pixmap,
                            "mercator_corners": [(0, 0), (0.001, 0), (0, -0.001)]}])
    monkeypatch.setattr(view.viewport(), "devicePixelRatioF", lambda: 1.0)
    try:
        view.fit_world()
        assert view.transform().m11() == pytest.approx(view._scale_limits()[1])
        view.fit_items({"tiny"}, set(), set())
        assert view.transform().m11() <= view._scale_limits()[1]
        view.restore_view_state({"center": [0, 0], "scale": 1e300})
        monkeypatch.setattr(view.viewport(), "devicePixelRatioF", lambda: 2.0)
        app.sendEvent(view, QEvent(QEvent.Type.DevicePixelRatioChange))
        maximum = view._scale_limits()[1]
        assert view.transform().m11() == pytest.approx(maximum)
        tile_span = 2 * view.WORLD_ORIGIN / (1 << view.MAX_ZOOM)
        assert tile_span * maximum * 2 / view.TILE_SIZE == pytest.approx(8)
        assert view._zoom_for_scale(maximum) == 19
        assert 2 * view.WORLD_ORIGIN * maximum * 2 < 2 ** 31 - 1
    finally:
        view.close()


def test_osm_zoom_updates_when_display_scaling_changes(monkeypatch):
    app = QApplication.instance() or QApplication([])
    update = WorldMapView._update_visible_tiles
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    view = WorldMapView()
    requests = []

    class Reply:
        def setProperty(self, name, value):
            pass

    monkeypatch.setattr(view._manager, "get", lambda request: requests.append(request.url().toString()) or Reply())
    monkeypatch.setattr(view.viewport(), "devicePixelRatioF", lambda: 1.0)
    view.scale((2 ** 8 / 156543.03392804097) / view.transform().m11(), (2 ** 8 / 156543.03392804097) / view.transform().m11())
    monkeypatch.setattr(view, "_update_visible_tiles", lambda: update(view))
    try:
        update(view)
        assert view._zoom == 8
        requests.clear()
        monkeypatch.setattr(view.viewport(), "devicePixelRatioF", lambda: 2.0)
        app.sendEvent(view, QEvent(QEvent.Type.DevicePixelRatioChange))
        assert view._zoom == 9
        assert requests
        assert all("/9/" in url for url in requests)
        assert view._zoom_for_scale(1e-20) == view.MIN_ZOOM
        assert view._zoom_for_scale(1e10) == view.MAX_ZOOM
    finally:
        view.close()


def test_osm_tiles_use_smooth_downsampling(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    view = WorldMapView()
    pixmap = QPixmap(256, 256)
    pixmap.fill(QColor("white"))
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert pixmap.save(buffer, "PNG")
    key = (view._zoom, 0, 0)

    class Reply:
        class NetworkError:
            NoError = 0

        def property(self, name):
            return key

        def readAll(self):
            return buffer.data()

        def error(self):
            return self.NetworkError.NoError

        def deleteLater(self):
            pass

    try:
        view._on_tile_loaded(Reply())
        tile = view._tile_items[key]
        assert tile.transformationMode() == Qt.TransformationMode.SmoothTransformation
        assert tile.scale() == pytest.approx(2 * view.WORLD_ORIGIN / (1 << key[0]) / 256)
        assert tile.pixmap().size() == pixmap.size()
    finally:
        view.close()


def test_osm_tiles_do_not_repeat_beyond_single_world_extent(monkeypatch):
    app = QApplication.instance() or QApplication([])
    update_tiles = WorldMapView._update_visible_tiles
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    view = WorldMapView()
    view.resize(640, 420)
    urls = []
    tile_keys = []

    class Reply:
        def setProperty(self, name, value):
            if name == "tileKey":
                tile_keys.append(value)

    monkeypatch.setattr(view._manager, "get", lambda request: urls.append(request.url().toString()) or Reply())
    try:
        scale = 2 ** 8 / 156543.03392804097
        view.setTransform(QTransform.fromScale(scale, scale))
        view.centerOn(view.WORLD_ORIGIN + 1000, 0)
        update_tiles(view)

        tile_count = 1 << view._zoom
        requested_x = [int(url.split("/")[-2]) for url in urls]
        assert all(0 <= x < tile_count for _zoom, x, _y in tile_keys)
        assert requested_x
        assert all(0 <= x < tile_count for x in requested_x)
        assert all(0 <= x < tile_count for _zoom, x, _y in view._tile_items)
        assert abs(view.view_state()["center"][0]) <= view.WORLD_ORIGIN
    finally:
        view.close()


@pytest.mark.parametrize("selection", ["image", "images", "point", "vector", "mixed", "none"])
def test_layers_hover_fit_routes_selection_to_both_views(tmp_path, monkeypatch, selection):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "fit.ini"), QSettings.Format.IniFormat))
    first = window.project.add_image("first.png")
    second = window.project.add_image("second.png")
    point = GroundControlPoint.create("point")
    point.set_observation(first.id, 10, 20)
    window.project.add_control_point(point)
    vector = VectorLayer(id="vector", path="layer.geojson", name="layer")
    window.project.vector_layers.append(vector)
    window._refresh_all()
    tree = window.image_list
    image_ids = {first.id} if selection in ("image", "mixed") else {first.id, second.id} if selection == "images" else set()
    point_ids = {point.id} if selection in ("point", "mixed") else set()
    vector_ids = {vector.id} if selection in ("vector", "mixed") else set()
    tree.blockSignals(True)
    tree.clearSelection()
    for row in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(row)
        item.setSelected(item.item_id in image_ids | vector_ids)
    tree.blockSignals(False)
    window.selected_point_ids = list(point_ids)
    monkeypatch.setattr(QApplication, "widgetAt", lambda *_args: tree.viewport())
    monkeypatch.setattr(QApplication, "focusWidget", lambda: window.canvas)
    calls = []
    monkeypatch.setattr(window.canvas, "fit_items", lambda *args: calls.append(("canvas", args)))
    monkeypatch.setattr(window.world_map, "fit_items", lambda *args: calls.append(("map", args)))
    monkeypatch.setattr(window.canvas, "fit_images", lambda: calls.append(("canvas-all", ())))
    monkeypatch.setattr(window.world_map, "fit_world", lambda: calls.append(("map-all", ())))
    try:
        window.shortcut_actions["fit_images"].trigger()
        expected = [("canvas", (image_ids, point_ids, set())), ("map", (image_ids, point_ids, vector_ids))]
        assert calls == (expected if selection != "none" else [("canvas-all", ()), ("map-all", ())])
        assert set(window._selected_image_ids()) == image_ids
        assert set(window._selected_point_ids()) == point_ids
        assert set(window._selected_vector_ids()) == vector_ids
    finally:
        window.close()


def test_world_map_fits_selected_images_points_and_vectors():
    from geostitch.vector import VectorGeometry, prepare_vectors

    app = QApplication.instance() or QApplication([])
    view = WorldMapView()
    view.resize(640, 420)
    pixmap = QPixmap(100, 60)
    pixmap.fill(QColor("white"))
    view.set_world_images([
        {"id": "selected", "pixmap": pixmap, "mercator_corners": [(1000, 2000), (1200, 2000), (1000, 1800)]},
        {"id": "other", "pixmap": pixmap, "mercator_corners": [(100000, 2000), (100200, 2000), (100000, 1800)]},
    ])
    data = prepare_vectors(VectorGeometry(points=[(3000, 4000)]), "EPSG:3857", "EPSG:3857")
    view.set_world_vectors([{"id": "vector", "data": data}])
    view.set_control_points([{"id": "point", "x": 5000, "y": 6000, "name": "point", "locked": False, "georeferenced": True}])
    view.show()
    app.processEvents()
    try:
        for image_ids, point_ids, vector_ids, center in [
            ({"selected"}, set(), set(), (1100, -1900)),
            (set(), {"point"}, set(), (5000, -6000)),
            (set(), set(), {"vector"}, (3000, -4000)),
            ({"selected"}, {"point"}, {"vector"}, (3000, -3900)),
        ]:
            view.fit_items(image_ids, point_ids, vector_ids)
            actual = view.mapToScene(view.viewport().rect().center())
            assert actual.x() == pytest.approx(center[0], abs=20)
            assert actual.y() == pytest.approx(center[1], abs=20)
        before = view.view_state()
        view.fit_items({"missing"}, set(), set())
        assert view.view_state() == before
    finally:
        view.close()


def test_world_map_places_images_from_geographic_transforms_north_up():
    app = QApplication.instance() or QApplication([])
    map_view = WorldMapView()
    pixmap = QPixmap(100, 60)
    pixmap.fill(QColor("#387765"))
    map_view.set_world_images([{
        "id": "sheet-a",
        "pixmap": pixmap,
        "mercator_corners": [(500_000.0, 5_400_000.0), (500_200.0, 5_400_050.0), (499_970.0, 5_399_880.0)],
    }])

    item = map_view._image_items["sheet-a"]
    assert item.mapToScene(QPointF(0.0, 0.0)) == QPointF(500_000.0, -5_400_000.0)
    assert item.mapToScene(QPointF(100.0, 0.0)) == QPointF(500_200.0, -5_400_050.0)
    assert item.mapToScene(QPointF(0.0, 60.0)) == QPointF(499_970.0, -5_399_880.0)
    assert not map_view._scene.itemsBoundingRect().isEmpty()
    map_view.close()


def test_world_map_omits_images_without_applied_georeferencing():
    app = QApplication.instance() or QApplication([])
    map_view = WorldMapView()
    pixmap = QPixmap(80, 50)
    pixmap.fill(QColor("white"))
    map_view.set_world_images([
        {
            "id": "georeferenced",
            "pixmap": pixmap,
            "mercator_corners": [(120.0, 35.0), (200.0, 35.0), (120.0, -15.0)],
        },
        {
            "id": "canvas-only",
            "pixmap": pixmap,
            "mercator_corners": None,
        },
    ])

    assert set(map_view._image_items) == {"georeferenced"}
    map_view.set_world_images([{
        "id": "canvas-only",
        "pixmap": pixmap,
        "mercator_corners": None,
    }])
    assert not map_view._image_items
    assert map_view._scene.sceneRect().width() > 40_000_000
    map_view.close()


def test_world_map_has_global_osm_extent_and_emits_clicked_coordinates():
    app = QApplication.instance() or QApplication([])
    map_view = WorldMapView()
    map_view.resize(640, 420)
    map_view.show()
    app.processEvents()
    map_view.fit_world()
    app.processEvents()
    map_view.set_pick_mode(True)
    picked = []
    map_view.coordinate_picked.connect(lambda longitude, latitude: picked.append((longitude, latitude)))
    center = map_view.mapFromScene(QPointF(0.0, 0.0))

    QTest.mouseClick(map_view.viewport(), Qt.MouseButton.LeftButton, pos=center)

    assert map_view.sceneRect().width() > 40_000_000
    assert picked
    assert abs(picked[0][0]) < 0.01
    assert abs(picked[0][1]) < 0.01
    map_view.close()


def test_app_map_dock_updates_when_georeference_result_is_applied(tmp_path):
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "world.png"
    pixmap = QPixmap(40, 30)
    pixmap.fill(QColor("#387765"))
    assert pixmap.save(str(image_path))

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    assert window.pick_gcp_action.isCheckable() is True
    assert window.pick_gcp_action.isVisible()
    assert not window.pick_gcp_action.isEnabled()
    image = window.project.add_image(image_path)
    window._image_pixmaps[image.id] = pixmap
    result = GeoreferenceResult(
        transforms={image.id: (510_000.0, 5_410_000.0, 3.0, 1.5)},
        observation_residuals={(image.id, "gcp-1"): 0.1},
        iterations=6,
        converged=True,
        crs="EPSG:26910",
    )

    window._apply_georeference_result(result)

    map_item = window.world_map._image_items[image.id]
    assert window.project.images[0].native_world_mapping.native_crs == "EPSG:26910"
    to_mercator = Transformer.from_crs("EPSG:26910", "EPSG:3857", always_xy=True)
    top_left = to_mercator.transform(510_000.0, 5_410_000.0)
    assert map_item.map_source_to_scene((0.0, 0.0)) == pytest.approx((top_left[0], -top_left[1]))
    assert window.world_map_dock.windowTitle() == "World Map · EPSG:26910"
    window.close()


def test_app_applies_and_undoes_full_chain_georeference_result(tmp_path):
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "local-frame-world.png"
    pixmap = QPixmap(40, 30)
    pixmap.fill(QColor("#387765"))
    assert pixmap.save(str(image_path))
    window = MainWindow(QSettings(str(tmp_path / "local-frame-world.ini"), QSettings.Format.IniFormat))
    window.project_path = tmp_path / "local-frame-world.gsp"
    image = window.project.add_image(image_path)
    image.source_dimensions = (40, 30)
    point = GroundControlPoint.create("Placed control")
    point.set_observation(image.id, 20, 15)
    window.project.add_control_point(point)
    matrix = MatrixTransform.from_affine((10, 0, 500000, 0, -10, 5400000))
    record = ImageMapping(
        matrix, "native_crs", "EPSG:4326", source_size=(40, 30), matrix_crs="EPSG:32610",
    )
    window._image_pixmaps[image.id] = pixmap
    window._image_sizes[image.id] = pixmap.size()
    result = GeoreferenceResult(
        transforms={image.id: _world_compatibility_transform(record.mapping_chain)},
        observation_residuals={}, iterations=1, converged=True, crs="EPSG:4326",
        matrix_transforms={image.id: matrix}, source_dimensions={image.id: (40, 30)},
        native_mapping_records={image.id: record},
    )

    try:
        window._apply_georeference_result(result)
        applied = window.project.images[0].native_world_mapping
        assert applied == record
        local_point = record.mapping_chain.map_point(20, 15)
        longitude, latitude = local_point
        expected = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(longitude, latitude)
        assert window.world_map._image_items[image.id].map_source_to_scene((20, 15)) == pytest.approx(
            (expected[0], -expected[1]), abs=1e-7,
        )
        window.map_points_button.setChecked(True)
        window._refresh_map_points()
        marker = next(marker for marker in window.world_map._point_overlay_items if marker.data(0) == point.id)
        assert (marker.scenePos().x(), marker.scenePos().y()) == pytest.approx(
            (expected[0], -expected[1]), abs=1e-7,
        )
        assert window.save_current_project()
        assert Project.from_dict(window.project.to_dict()).images[0].native_world_mapping == record
        window.undo_stack.undo()
        assert window.project.images[0].native_world_mapping is None
        window.undo_stack.redo()
        assert window.project.images[0].native_world_mapping == record
    finally:
        window.close()


@pytest.mark.parametrize("crs, origin, rotation, scale", [
    ("EPSG:4326", (10.0, 60.0), 0.0, 0.001),
    ("EPSG:26910", (500000.0, 5400000.0), 17.0, 20.0),
    ("EPSG:3857", (-1_000_000.0, 5_000_000.0), -12.0, 100.0),
])
def test_application_map_chain_matches_pyproj_across_source_interior(tmp_path, crs, origin, rotation, scale):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / f"map-{crs.replace(':', '-')}.png"
    pixmap = QPixmap(800, 600)
    pixmap.fill(QColor("#387765"))
    assert pixmap.save(str(path))
    window = MainWindow(QSettings(str(tmp_path / f"settings-{crs.replace(':', '-')}.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(path)
    image.world_x, image.world_y = origin
    image.world_rotation, image.world_scale = rotation, scale
    window._image_pixmaps[image.id] = pixmap
    window._image_sizes[image.id] = pixmap.size()
    window.project.crs = crs
    window._refresh_world_map()
    item = window.world_map._image_items[image.id]
    converter = Transformer.from_crs(crs, "EPSG:3857", always_xy=True)
    try:
        for column in range(0, 801, 80):
            for row in range(0, 601, 60):
                world_x, world_y = image.world_mapping.map_point(column, row)
                map_x, map_y = converter.transform(world_x, world_y, errcheck=True)
                assert item.map_source_to_scene((column, row)) == pytest.approx((map_x, -map_y), abs=1e-7)
        assert item._scene_bounds.width() > 0 and item._scene_bounds.height() > 0
    finally:
        window.close()


def test_application_map_chain_includes_native_local_matrix_crs(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "local-frame-map.png"
    pixmap = QPixmap(800, 600)
    pixmap.fill(QColor("#387765"))
    assert pixmap.save(str(path))
    window = MainWindow(QSettings(str(tmp_path / "local-frame-map.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(path)
    image.world_x, image.world_y = 10, 45
    image.source_dimensions = (800, 600)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((20, 0, 500000, 0, -20, 5400000)),
        "native_crs", "EPSG:4326", source_size=(800, 600), matrix_crs="EPSG:32610",
    )
    window.project.crs = "EPSG:4326"
    window._image_pixmaps[image.id] = pixmap
    window._image_sizes[image.id] = pixmap.size()

    try:
        window._refresh_world_map()
        item = window.world_map._image_items[image.id]
        local_x, local_y = image.native_world_mapping.mapping.map_point(400, 300)
        longitude, latitude = Transformer.from_crs(
            "EPSG:32610", "EPSG:4326", always_xy=True,
        ).transform(local_x, local_y, errcheck=True)
        expected_x, expected_y = Transformer.from_crs(
            "EPSG:4326", "EPSG:3857", always_xy=True,
        ).transform(longitude, latitude, errcheck=True)

        assert item.map_source_to_scene((400, 300)) == pytest.approx((expected_x, -expected_y), abs=1e-7)
    finally:
        window.close()


def test_map_coordinate_pick_has_visible_cancel_control(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "map-pick-cancel.ini"), QSettings.Format.IniFormat))
    point = GroundControlPoint.create("Map point")
    window.project.add_control_point(point)
    window._select_point(point.id)
    window.show()
    window.world_map_dock.show()
    app.processEvents()
    window.pick_gcp_action.setChecked(True)

    assert window.map_tool_hint.isVisible()
    assert "PICK MAP COORDINATE" in window.map_tool_hint.message_label.text()
    assert window.map_tool_hint.pos().x() == 8
    assert window.map_tool_hint.pos().y() == 8
    window.world_map.setFocus()
    QTest.keyClick(window.world_map, Qt.Key.Key_Escape)
    assert not window.pick_gcp_action.isChecked()
    assert not window.world_map._pick_mode
    assert not window.map_tool_hint.isVisible()

    window.pick_gcp_action.setChecked(True)
    window.world_map.coordinate_picked.emit(0.0, 0.0)
    assert point.geographic_x == pytest.approx(0.0)
    assert point.geographic_y == pytest.approx(0.0)
    assert not window.pick_gcp_action.isChecked()
    assert not window.map_tool_hint.isVisible()
    window.close()
