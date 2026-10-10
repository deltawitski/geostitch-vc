import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from geostitch.canvas import ImageCanvas


@pytest.fixture
def canvas():
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    yield canvas
    canvas.close()
    delete(canvas)


def layer(image_id="first", position=0, points=None):
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("red"))
    return {"id": image_id, "pixmap": pixmap, "position_x": position, "position_y": 0,
            "rotation": 0, "scale": 1, "points": points or []}


def test_unchanged_refresh_preserves_image_items(canvas):
    layers = [layer(), layer("second", 200)]
    canvas.set_layers(layers)
    first, second = canvas._layers.values()
    canvas.set_selected_images({"first"})
    canvas.set_layers(layers)
    assert canvas._layers["first"] is first
    assert canvas._layers["second"] is second
    assert canvas._selected_image_ids == {"first"}
    assert len(canvas._scene.items()) >= 2


def test_reused_image_updates_preview_bounds_pose_and_order(canvas):
    first, second = layer(), layer("second", 200)
    canvas.set_layers([first, second])
    item = canvas._layers["first"]
    updated = dict(first, position_x=50, position_y=60, rotation=90, scale=2, source_size=QSize(1000, 800))
    updated["pixmap"] = QPixmap(50, 40)
    updated["pixmap"].fill(QColor("blue"))
    canvas.set_layers([second, updated])
    assert canvas._layers["first"] is item
    assert item.boundingRect().size().toSize() == QSize(1000, 800)
    assert item.pixmap().toImage().pixelColor(10, 10) == QColor("blue")
    assert canvas.image_transform("first") == (50, 60, 90, 2)
    assert item.zValue() > canvas._layers["second"].zValue()


def test_removing_selected_layer_cleans_overlays_and_preserves_other_image(canvas):
    first, second = layer(points=[("point", 20, 30)]), layer("second", 200)
    canvas.set_layers([first, second])
    item = canvas._layers["second"]
    canvas.set_selected_images({"first"})
    assert canvas._transform_handles
    canvas.set_layers([second])
    assert canvas._layers["second"] is item
    assert not canvas._selected_image_ids
    assert not canvas._point_items
    assert not canvas._transform_handles
    canvas.set_layers([])
    assert not canvas._scene.items()


def test_point_and_link_items_survive_edits_and_restyle(canvas):
    from PySide6.QtCore import Qt

    first = layer(points=[("one", 20, 30)])
    second = layer("second", 200, points=[("two", 40, 50)])
    canvas.set_layers([first, second], [("one", "two")])
    marker = canvas._point_items[("first", "one")]
    line = canvas._constraint_items[0]
    canvas.set_selected_constraints({0})
    assert canvas._constraint_items[0] is line
    assert line.pen().widthF() == 4
    canvas.set_constraint_residuals({0: 1.5}, 0.5)
    assert canvas._constraint_items[0] is line
    assert "residual" in line.toolTip()
    updated = dict(first, points=[("one", 70, 60, True, True)], position_x=100)
    canvas.set_layers([updated, second], [("one", "two")])
    assert canvas._point_items[("first", "one")] is marker
    assert marker.pos() == QPointF(170, 60)
    assert marker.pen().style() == Qt.PenStyle.DashLine
    assert canvas._constraint_items[0] is line
    assert line.line().p1() == QPointF(170, 60)
    canvas.set_constraint_residuals({})
    assert line.toolTip() == ""


def test_visibility_removes_only_unavailable_links_and_restores_them(canvas):
    first = layer(points=[("one", 20, 30, False, True)])
    second = layer("second", 200, points=[("two", 40, 50, False, True), ("three", 60, 60)])
    links = [("one", "two"), ("two", "three")]
    canvas.set_layers([first, second], links)
    retained = canvas._constraint_items[1]
    marker = canvas._point_items[("first", "one")]
    first["points"] = [("one", 20, 30, False, False)]
    canvas.set_layers([first, second], links)
    assert canvas._point_items[("first", "one")] is marker
    assert not marker.isVisible()
    assert canvas._constraint_items == [retained]
    first["points"] = [("one", 20, 30, False, True)]
    canvas.set_layers([first, second], links)
    assert len(canvas._constraint_items) == 2
    assert canvas._constraint_items[1] is retained


def test_duplicate_endpoint_links_reuse_distinct_lines_after_reordering(canvas):
    layers = [layer(points=[("one", 20, 30)]), layer("second", 200, points=[("two", 40, 50), ("three", 60, 60)])]
    canvas.set_layers(layers, [("one", "two"), ("one", "two"), ("two", "three")])
    lines = list(canvas._constraint_items)
    assert len({id(line) for line in lines}) == 3
    canvas.set_layers(layers, [("two", "three"), ("one", "two"), ("one", "two")])
    assert canvas._constraint_items == [lines[2], lines[0], lines[1]]
    canvas.set_layers([dict(layers[0], points=[]), layers[1]], [("two", "three")])
    assert canvas._constraint_items == [lines[2]]
    assert "one" not in canvas._point_keys_by_id


def test_selection_overlays_and_handles_are_reused_without_accumulation(canvas):
    layers = [layer(), layer("second", 200)]
    canvas.set_layers(layers)
    canvas.set_selected_images({"first"})
    outline = canvas._selection_outlines[0]
    handles = list(canvas._transform_handles)
    connector = canvas._transform_connector
    count = len(canvas._scene.items())
    for index in range(10):
        layers[0]["rotation"] = index * 10
        canvas.set_layers(layers)
        assert canvas._selection_outlines == [outline]
        assert canvas._transform_handles == handles
        assert canvas._transform_connector is connector
        assert len(canvas._scene.items()) == count
    canvas.set_selected_images({"second"})
    assert canvas._transform_handles == handles
    assert all(handle.data(1) == "second" for handle in handles)
    canvas.set_selected_images(set())
    assert len(canvas._scene.items()) == 2


def test_refresh_preserves_active_point_and_transform_gestures(canvas):
    first = layer(points=[("one", 20, 30)])
    canvas.set_layers([first])
    canvas._dragged_point_key = ("first", "one")
    marker = canvas._point_items[("first", "one")]
    canvas.set_layers([first])
    assert canvas._dragged_point_key == ("first", "one")
    assert canvas._point_items[("first", "one")] is marker
    canvas._dragged_point_key = None
    canvas.set_selected_images({"first"})
    canvas._begin_transform_handle("rotate", QPointF(50, -32))
    canvas.set_layers([first])
    assert canvas._active_transform_handle == "rotate"
    canvas.set_layers([])
    assert canvas._active_transform_handle is None
    assert not canvas._scene.items()


def test_real_point_drag_survives_mid_gesture_refresh(canvas):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest

    app = QApplication.instance()
    first = layer(points=[("one", 20, 30)])
    canvas.resize(800, 600)
    canvas.set_layers([first])
    canvas.show()
    app.processEvents()
    canvas.fit_images()
    started, changed, finished = [], [], []
    canvas.point_observation_interaction_started.connect(lambda *args: started.append(args))
    canvas.point_observation_changed.connect(lambda *args: changed.append(args))
    canvas.point_observation_interaction_finished.connect(lambda *args: finished.append(args))
    start = canvas.mapFromScene(QPointF(20, 30))
    end = canvas.mapFromScene(QPointF(40, 50))
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(canvas.viewport().mapToGlobal(end)),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(canvas.viewport(), move)
    marker = canvas._point_items[("first", "one")]
    expected = canvas._points[("first", "one")]
    first["points"] = [("one", *expected)]
    canvas.set_layers([first])
    assert canvas._point_items[("first", "one")] is marker
    assert canvas._dragged_point_key == ("first", "one")
    assert canvas._magnifier.isVisible()
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert started == finished == [("first", "one")]
    assert changed[-1][2:] == pytest.approx(expected)
    assert canvas._dragged_point_key is None
    assert not canvas._magnifier.isVisible()


def test_refresh_styles_markers_once_instead_of_per_point(canvas):
    from unittest.mock import patch

    points = [(f"point-{index}", index % 100, index % 80) for index in range(200)]
    with patch.object(canvas, "_refresh_marker_styles", wraps=canvas._refresh_marker_styles) as styles:
        canvas.set_layers([layer(points=points)])
        assert styles.call_count == 1
    markers = dict(canvas._point_items)
    with patch.object(canvas, "_add_point_marker", wraps=canvas._add_point_marker) as allocations:
        canvas.set_layers([layer(points=points)])
        assert allocations.call_count == 0
    assert canvas._point_items == markers


def test_refresh_keeps_live_group_drag_positions_until_release(canvas):
    layers = [layer(), layer("second", 200)]
    canvas.set_layers(layers)
    canvas.set_selected_images({"first", "second"})
    canvas._dragged_image_id = "first"
    canvas._layers["first"].setPos(50, 20)
    canvas._layers["second"].setPos(250, 20)
    canvas.set_layers(layers)
    assert canvas._layers["first"].pos() == QPointF(50, 20)
    assert canvas._layers["second"].pos() == QPointF(250, 20)
    canvas._dragged_image_id = None
    canvas.set_layers(layers)
    assert canvas._layers["first"].pos() == QPointF(0, 0)
    assert canvas._layers["second"].pos() == QPointF(200, 0)