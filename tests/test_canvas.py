import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QDropEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsTextItem

from geostitch.canvas import ImageCanvas
from geostitch.app import MainWindow
from geostitch.adjustment import AlignmentResult
from geostitch.georeference import GeoreferenceResult
from geostitch.models import GroundControlPoint, PointConstraint
from geostitch.icons import KIND_COLORS, kind_icon


def test_canvas_element_colors_match_layer_palette_and_preserve_state_cues():
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    canvas.set_layers([
        {"id": image_id, "pixmap": pixmap, "position_x": position, "position_y": 0,
         "rotation": 0, "scale": 1, "points": [(point_id, 20, 30)]}
        for image_id, position, point_id in [("first", 0, "point-a"), ("second", 150, "point-b")]
    ], [("point-a", "point-b")])
    try:
        for kind in ("image", "point", "link"):
            icon_color = kind_icon(kind).pixmap(8, 16).toImage().pixelColor(4, 8)
            assert icon_color == QColor(KIND_COLORS[kind])
        marker = canvas._point_items[("first", "point-a")]
        rect = marker.rect()
        normal_alpha = marker.brush().color().alpha()
        assert marker.pen().color() == QColor(KIND_COLORS["point"])
        assert marker.pen().style() == Qt.PenStyle.SolidLine
        canvas.set_selected_points({"point-a"})
        assert marker.pen().color() == QColor(KIND_COLORS["point"])
        assert marker.pen().widthF() == 3
        assert marker.brush().color().alpha() > normal_alpha
        canvas.set_observation_locked("first", "point-a", True)
        assert marker.pen().style() == Qt.PenStyle.DashLine
        assert marker.pen().color() == QColor(KIND_COLORS["point"])
        canvas.set_constraint_source("point-a")
        assert marker.pen().style() == Qt.PenStyle.DotLine
        assert marker.pen().color() == QColor(KIND_COLORS["point"])
        assert marker.rect() == rect
        link = canvas._constraint_items[0]
        assert link.pen().color() == QColor(KIND_COLORS["link"])
        assert link.pen().style() == Qt.PenStyle.DashLine
        canvas.set_selected_constraints({0})
        link = canvas._constraint_items[0]
        assert link.pen().color() == QColor(KIND_COLORS["link"])
        assert link.pen().widthF() == 4
        assert link.pen().style() == Qt.PenStyle.SolidLine
        canvas.set_selected_images({"first"})
        assert canvas._selection_outlines[0].pen().color() == QColor(KIND_COLORS["image"])
        assert all(handle.brush().color() == QColor(KIND_COLORS["image"]) for handle in canvas._transform_handles)
        assert canvas._transform_connector.pen().color() == QColor(KIND_COLORS["image"])
    finally:
        canvas.close()


def drag_transform_handle(canvas, app, kind, delta):
    handle = next(item for item in canvas._transform_handles if item.data(0) == kind)
    start = canvas.mapFromScene(handle.scenePos())
    end = start + delta
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas.viewport(), end, delay=20)
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    app.processEvents()


def test_canvas_places_point_in_clicked_layer_pixel_coordinates():
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    canvas.resize(800, 600)
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    layers = [
        {
            "id": image_id,
            "pixmap": pixmap,
            "position_x": position_x,
            "position_y": 0.0,
            "rotation": 0.0,
            "scale": 1.0,
            "points": [],
        }
        for image_id, position_x in (("first", 0.0), ("second", 130.0))
    ]
    canvas.set_layers(layers)
    canvas.show()
    app.processEvents()
    canvas.fit_images()
    app.processEvents()
    clicks = []
    canvas.image_clicked.connect(lambda *args: clicks.append(args))
    canvas.set_active_image("first")
    canvas.set_active_point("point-id")

    scene_position = canvas._layers["second"].mapToScene(QPointF(30.0, 40.0))
    view_position = canvas.mapFromScene(scene_position)
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=view_position)

    assert clicks[0][0] == "second"
    assert abs(clicks[0][1] - 30.0) < 1.0
    assert abs(clicks[0][2] - 40.0) < 1.0

    canvas.set_image_transform("second", rotation=12.0, scale=1.4)
    assert canvas.image_transform("second") == (130.0, 0.0, 12.0, 1.4)
    canvas.close()


def test_moving_image_inside_canvas_bounds_does_not_pan_or_recenter_view():
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    canvas.resize(800, 600)
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    canvas.set_layers([{
        "id": "image",
        "pixmap": pixmap,
        "position_x": 0.0,
        "position_y": 0.0,
        "rotation": 0.0,
        "scale": 1.0,
        "points": [],
    }])
    canvas.show()
    app.processEvents()
    canvas.fit_images()
    app.processEvents()
    scene_rect_before = canvas.sceneRect()
    center = canvas.viewport().rect().center()
    center_scene_before = canvas.mapToScene(center)
    scroll_before = (canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value())

    canvas.set_image_position("image", 20.0, 15.0)

    center_scene_after = canvas.mapToScene(center)
    scroll_after = (canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value())
    assert canvas.sceneRect() == scene_rect_before
    assert scroll_after == scroll_before
    assert center_scene_after == center_scene_before
    canvas.close()


def test_moving_image_far_from_canvas_keeps_navigation_bounds_and_view():
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    canvas.resize(800, 600)
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    canvas.set_layers([{
        "id": "image",
        "pixmap": pixmap,
        "position_x": 0.0,
        "position_y": 0.0,
        "rotation": 0.0,
        "scale": 1.0,
        "points": [],
    }])
    canvas.show()
    app.processEvents()
    canvas.fit_images()
    app.processEvents()
    center = canvas.viewport().rect().center()
    center_scene_before = canvas.mapToScene(center)
    original_rect = canvas.sceneRect()

    canvas.set_image_position("image", 900.0, 700.0)

    assert canvas.sceneRect() == original_rect
    assert canvas.mapToScene(center) == center_scene_before
    canvas.close()


def test_canvas_pans_repeatedly_beyond_image_bounds():
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    canvas.resize(500, 400)
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    canvas.set_layers([{
        "id": "image",
        "pixmap": pixmap,
        "position_x": 0.0,
        "position_y": 0.0,
        "rotation": 0.0,
        "scale": 1.0,
        "points": [],
    }])
    canvas.show()
    app.processEvents()
    canvas.fit_images()
    app.processEvents()
    scene_rect = canvas.sceneRect()
    center = canvas.viewport().rect().center()
    center_scene_before = canvas.mapToScene(center)

    for _ in range(2):
        start = canvas.viewport().rect().center()
        end = start + QPoint(180, 130)
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end, delay=10)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=end)
        app.processEvents()

    center_scene_after = canvas.mapToScene(canvas.viewport().rect().center())
    assert canvas.sceneRect() == scene_rect
    assert abs(center_scene_after.x() - center_scene_before.x()) > 80.0
    assert abs(center_scene_after.y() - center_scene_before.y()) > 60.0
    canvas.close()


def test_canvas_scrollbars_are_hidden():
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    canvas.resize(500, 400)
    canvas.show()
    app.processEvents()

    assert not canvas.horizontalScrollBar().isVisible()
    assert not canvas.verticalScrollBar().isVisible()
    canvas.close()


def test_fit_images_uses_selected_layer_or_all_layers_when_none_selected():
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    canvas.resize(800, 600)
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    canvas.set_layers([
        {
            "id": image_id,
            "pixmap": pixmap,
            "position_x": x,
            "position_y": 0.0,
            "rotation": 0.0,
            "scale": 1.0,
            "points": [],
        }
        for image_id, x in (("first", 0.0), ("second", 1000.0))
    ])
    canvas.show()
    app.processEvents()

    canvas.fit_images({"second"})
    selected_center = canvas.mapToScene(canvas.viewport().rect().center())
    assert abs(selected_center.x() - 1050.0) < 1.0

    canvas.fit_images()
    all_center = canvas.mapToScene(canvas.viewport().rect().center())
    assert abs(all_center.x() - 550.0) < 2.5
    canvas.close()


def test_fit_action_passes_selected_image_ids_or_none_for_all_images(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    window.project.add_image("first.png")
    window.project.add_image("second.png")
    window._refresh_all()
    fit_calls = []

    def capture_fit(_canvas, image_ids=None):
        fit_calls.append(image_ids)

    monkeypatch.setattr(ImageCanvas, "fit_images", capture_fit)
    window.image_list.clearSelection()
    window._fit_canvas()
    assert fit_calls[-1] is None

    window.image_list.setCurrentItem(window.image_list.item(0))
    window.image_list.clearSelection()
    window.image_list.item(0).setSelected(True)
    selected_image_id = window.image_list.selectedItems()[0].data(Qt.ItemDataRole.UserRole)
    window._fit_canvas()
    assert fit_calls[-1] == {selected_image_id}

    window.image_list.item(1).setSelected(True)
    window._fit_canvas()
    assert len(fit_calls[-1]) == 2
    window.close()


def test_main_window_click_places_observation_and_controls_update_layer(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "layer.png"
    pixmap = QPixmap(200, 150)
    pixmap.fill(QColor("white"))
    assert pixmap.save(str(image_path))

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(image_path)
    first_point = GroundControlPoint.create("bridge-001", "Old stone bridge")
    first_point.set_observation(image.id, 10.0, 12.0)
    second_point = GroundControlPoint.create("road")
    window.project.add_control_point(first_point)
    window.project.add_control_point(second_point)
    window.current_image_id = image.id
    window._refresh_all()
    window.show()
    app.processEvents()
    window.canvas.fit_images()
    app.processEvents()
    window._select_point(first_point.id)
    assert window.pending_point_id is None
    assert window._selected_point_id() == first_point.id
    assert window.label_edit.text() == "Old stone bridge"
    scene_position = window.canvas._layers[image.id].mapToScene(QPointF(35.0, 55.0))
    view_position = window.canvas.mapFromScene(scene_position)

    QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=view_position)

    observation = first_point.observation_for(image.id)
    assert (observation.x, observation.y) == (10.0, 12.0)
    assert window._selected_point_id() is None or window._selected_point_id() == first_point.id

    marker = window.canvas._point_items[(image.id, first_point.id)]
    marker_position = window.canvas.mapFromScene(marker.scenePos())
    QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=marker_position)
    assert window._selected_point_id() == first_point.id
    assert window.pending_point_id is None
    assert not window.image_list.selectedItems()

    window.image_list.setCurrentItem(window.image_list.item(0))
    assert window._selected_point_id() is None
    assert window.image_list.selectedItems()
    drag_transform_handle(window.canvas, app, "scale-se", QPoint(35, 25))
    assert window.project.images[0].scale > 1.0
    scaled_value = window.project.images[0].scale
    drag_transform_handle(window.canvas, app, "rotate", QPoint(25, 0))
    assert abs(window.project.images[0].rotation) > 1.0
    rotated_value = window.project.images[0].rotation
    window.undo_stack.undo()
    assert window.project.images[0].rotation == 0.0
    window.undo_stack.undo()
    assert window.project.images[0].scale == 1.0
    window.undo_stack.redo()
    assert abs(window.project.images[0].scale - scaled_value) < 0.001
    window.undo_stack.redo()
    assert abs(window.project.images[0].rotation - rotated_value) < 0.001

    window._select_point(first_point.id)
    assert not window.image_list.selectedItems()
    monkeypatch.setattr("geostitch.app.QInputDialog.getItem", lambda *args, **kwargs: ("road", True))
    window.create_constraint()
    assert len(window.project.constraints) == 1
    assert window.pending_point_id is None
    assert not window.image_list.selectedItems()
    assert window._selected_point_id() is None
    assert window.selected_constraint_id == window.project.constraints[0].id
    assert window.details_stack.currentIndex() == 3
    window.image_list.setCurrentItem(window.image_list.item(0))
    assert window.image_list.selectedItems()
    assert window._selected_point_id() is None
    assert window.selected_constraint_id is None
    assert window.details_stack.currentIndex() == 1
    window.close()


def test_selecting_points_does_not_arm_or_move_them_without_explicit_command(tmp_path):
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "passive-selection.png"
    pixmap = QPixmap(180, 140)
    pixmap.fill(QColor("white"))
    assert pixmap.save(str(image_path))

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(image_path)
    first = GroundControlPoint.create("point-001")
    second = GroundControlPoint.create("point-002")
    first.set_observation(image.id, 30.0, 35.0)
    second.set_observation(image.id, 75.0, 80.0)
    window.project.add_control_point(first)
    window.project.add_control_point(second)
    window.current_image_id = image.id
    window._refresh_all()
    window.show()
    app.processEvents()
    window.canvas.fit_images()
    app.processEvents()

    def click_marker(point_id):
        marker = window.canvas._point_items[(image.id, point_id)]
        QTest.mouseClick(
            window.canvas.viewport(),
            Qt.MouseButton.LeftButton,
            pos=window.canvas.mapFromScene(marker.scenePos()),
        )

    click_marker(first.id)
    assert window._selected_point_id() == first.id
    assert window.pending_point_id is None
    click_marker(second.id)
    assert window._selected_point_id() == second.id
    assert window.pending_point_id is None

    target = window.canvas.mapFromScene(window.canvas._layers[image.id].mapToScene(QPointF(130.0, 110.0)))
    QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=target)
    assert window.project.point_by_id(first.id).observation_for(image.id).x == 30.0
    assert window.project.point_by_id(first.id).observation_for(image.id).y == 35.0
    assert window.project.point_by_id(second.id).observation_for(image.id).x == 75.0
    assert window.project.point_by_id(second.id).observation_for(image.id).y == 80.0

    window._select_point(second.id)
    assert window.pending_point_id is None
    window.close()


def test_undo_redo_constraint_and_point_deletion_cascade(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(tmp_path / "constraint-image.png")
    other_image = window.project.add_image(tmp_path / "constraint-other.png")
    first = GroundControlPoint.create("first")
    second = GroundControlPoint.create("second")
    first.set_observation(image.id, 10.0, 10.0)
    second.set_observation(other_image.id, 20.0, 20.0)
    window.project.add_control_point(first)
    window.project.add_control_point(second)
    window.current_image_id = image.id
    window._refresh_points()
    window._select_point(first.id)
    monkeypatch.setattr("geostitch.app.QInputDialog.getItem", lambda *args, **kwargs: ("second", True))

    window.create_constraint()
    assert len(window.project.constraints) == 1
    window.undo_stack.undo()
    assert not window.project.constraints
    window.undo_stack.redo()
    assert len(window.project.constraints) == 1

    window._select_point(first.id)
    window.remove_selected_point()
    assert len(window.project.control_points) == 1
    assert not window.project.constraints
    window.undo_stack.undo()
    assert len(window.project.control_points) == 2
    assert len(window.project.constraints) == 1
    window.undo_stack.redo()
    assert len(window.project.control_points) == 1
    assert not window.project.constraints
    window.close()


def test_control_point_details_filter_by_image_and_edit_point_and_constraint(tmp_path):
    app = QApplication.instance() or QApplication([])
    first_image_path = tmp_path / "detail-a.png"
    second_image_path = tmp_path / "detail-b.png"
    for path in (first_image_path, second_image_path):
        pixmap = QPixmap(100, 80)
        pixmap.fill(QColor("white"))
        assert pixmap.save(str(path))

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    first_image = window.project.add_image(first_image_path)
    second_image = window.project.add_image(second_image_path)
    first = GroundControlPoint.create("shared-001", "shared feature")
    second = GroundControlPoint.create("first-only")
    third = GroundControlPoint.create("second-only")
    first.set_observation(first_image.id, 12.0, 15.0)
    second.set_observation(first_image.id, 40.0, 45.0)
    third.set_observation(second_image.id, 50.0, 35.0)
    for point in (first, second, third):
        window.project.add_control_point(point)
    constraint = PointConstraint.create(first.id, third.id)
    window.project.add_constraint(constraint)
    window.current_image_id = first_image.id
    window._refresh_all()

    window.image_list.setCurrentItem(window.image_list.item(0))
    assert window.details_stack.currentIndex() == 1
    assert window.image_points_list.count() == 2
    assert {
        window.image_points_list.item(row).text().split("  ·  ")[0]
        for row in range(window.image_points_list.count())
    } == {"shared-001", "first-only"}
    assert window.image_links_list.count() == 1

    window._select_point(first.id)
    assert window.details_stack.currentIndex() == 2
    assert window.point_observations_list.count() == 1
    assert window.point_links_list.count() == 1
    selected_id = window._selected_point_id()
    selected_point = window.project.point_by_id(selected_id)
    window.name_edit.setText("shared-renamed")
    window.pixel_x_edit.setText("25.5")
    window.pixel_y_edit.setText("28.5")
    window.geo_x_edit.setText("450000")
    window.geo_y_edit.setText("5400000")
    window.update_selected_point()
    edited = window.project.point_by_id(selected_id)
    assert edited.name == "shared-renamed"
    assert (edited.observation_for(first_image.id).x, edited.observation_for(first_image.id).y) == (25.5, 28.5)
    assert edited.geographic_x == 450000.0

    window._select_constraint(constraint.id)
    assert window.details_stack.currentIndex() == 3
    assert window.current_image_id == first_image.id
    assert window.constraint_second_edit.currentData() == third.id
    window.constraint_second_edit.setCurrentIndex(window.constraint_second_edit.findData(second.id))
    window.constraint_kind_edit.setText("tie-refined")
    window.update_selected_constraint()
    updated_constraint = window.project.constraints[0]
    assert updated_constraint.second_point_id == second.id
    assert updated_constraint.kind == "tie-refined"
    window.close()


def test_apply_alignment_result_updates_transforms_as_one_undoable_edit(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    first = window.project.add_image("anchor.png")
    second = window.project.add_image("moving.png")
    untouched = window.project.add_image("untouched.png")
    untouched.position_x = 730.0
    untouched.position_y = 220.0
    result = AlignmentResult(
        transforms={
            first.id: (0.0, 0.0, 0.0, 1.0),
            second.id: (42.0, -15.0, 6.0, 1.2),
        },
        constraint_residuals={"tie-a": 0.01},
        iterations=4,
        converged=True,
        anchor_image_ids=(first.id,),
    )

    window._apply_alignment_result(result)

    moved = window.project.images[1]
    assert (moved.aligned_x, moved.aligned_y, moved.aligned_rotation, moved.aligned_scale) == (42.0, -15.0, 6.0, 1.2)
    assert moved.position_x is None and moved.rotation == 0.0
    assert window.project.images[2].position_x == 730.0
    assert not window.project.images[2].has_alignment
    assert window._aligned_view_active()
    assert window.toggle_alignment_action.isChecked()
    assert window.canvas._read_only
    window.toggle_alignment_action.setChecked(False)
    assert window.project.canvas_state == "rest"
    assert not window.canvas._read_only
    assert window.undo_stack.undoText() == "Apply simulated alignment"
    window.undo_stack.undo()
    assert not window.project.images[1].has_alignment
    assert not window.toggle_alignment_action.isEnabled()
    window.undo_stack.redo()
    assert window.project.images[1].aligned_x == 42.0
    assert window.project.images[1].aligned_scale == 1.2
    window.close()


def test_apply_georeference_result_saves_world_transforms_and_is_undoable(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image("world-sheet.png")
    result = GeoreferenceResult(
        transforms={image.id: (500_100.0, 5_410_200.0, 4.0, 0.75)},
        observation_residuals={(image.id, "gcp-1"): 0.2},
        iterations=8,
        converged=True,
        crs="EPSG:26910",
    )

    window._apply_georeference_result(result)

    world_image = window.project.images[0]
    assert (world_image.world_x, world_image.world_y) == (500_100.0, 5_410_200.0)
    assert (world_image.world_rotation, world_image.world_scale) == (4.0, 0.75)
    assert world_image.position_x is None
    assert window.undo_stack.undoText() == "Apply GCP georeferencing"
    window.undo_stack.undo()
    assert window.project.images[0].world_x is None
    window.undo_stack.redo()
    assert window.project.images[0].world_x == 500_100.0


def test_clicking_empty_canvas_clears_image_and_control_point_selection(tmp_path):
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "selection.png"
    pixmap = QPixmap(120, 90)
    pixmap.fill(QColor("white"))
    assert pixmap.save(str(image_path))

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(image_path)
    point = GroundControlPoint.create("marker-001")
    point.set_observation(image.id, 30.0, 35.0)
    window.project.add_control_point(point)
    window.current_image_id = image.id
    window._refresh_all()
    window.show()
    app.processEvents()
    window.canvas.fit_images()
    app.processEvents()
    window.image_list.item(0).setSelected(True)
    window._select_point(point.id)
    assert not window.image_list.selectedItems()
    assert window._selected_point_id() == point.id

    QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(10, 10))

    assert not window.image_list.selectedItems()
    assert window.current_image_id is None
    assert window._selected_point_id() is None
    assert window.pending_point_id is None
    assert window.canvas._active_image_id is None
    assert not window.canvas._transform_handles
    window.close()


def test_continuous_add_mode_creates_sequenced_points_in_clicked_images(tmp_path):
    app = QApplication.instance() or QApplication([])
    image_paths = [tmp_path / "north.png", tmp_path / "south.png"]
    for image_path in image_paths:
        pixmap = QPixmap(120, 90)
        pixmap.fill(QColor("white"))
        assert pixmap.save(str(image_path))

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    images = [window.project.add_image(path) for path in image_paths]
    window.current_image_id = images[0].id
    window._refresh_all()
    window.show()
    app.processEvents()
    window.canvas.fit_images()
    app.processEvents()
    window.add_points_action.setChecked(True)

    first_position = window.canvas._layers[images[0].id].mapToScene(QPointF(20.0, 30.0))
    QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=window.canvas.mapFromScene(first_position))
    app.processEvents()
    second_position = window.canvas._layers[images[1].id].mapToScene(QPointF(45.0, 55.0))
    QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=window.canvas.mapFromScene(second_position))

    assert window.add_points_mode
    assert [point.name for point in window.project.control_points] == ["north-001", "south-001"]
    assert window.current_image_id == images[1].id
    assert not window.image_list.selectedItems()
    assert window.canvas._active_image_id is None
    assert window.project.control_points[0].observation_for(images[0].id) is not None
    assert window.project.control_points[1].observation_for(images[1].id) is not None
    assert window.project.control_points[0].label is None
    assert window.project.control_points[1].label is None
    window.undo_stack.undo()
    assert [point.name for point in window.project.control_points] == ["north-001"]
    window.undo_stack.redo()
    assert [point.name for point in window.project.control_points] == ["north-001", "south-001"]
    window.add_points_action.setChecked(False)
    window.close()


def test_locked_image_stays_fixed_during_group_drag_and_read_only_toggle():
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    canvas.resize(800, 600)
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    canvas.set_layers([
        {"id": image_id, "pixmap": pixmap, "position_x": position, "position_y": 0,
         "rotation": 0, "scale": 1, "points": [], "rest_locked": locked}
        for image_id, position, locked in [("locked", 0, True), ("free", 200, False)]
    ])
    canvas.show()
    app.processEvents()
    canvas.fit_images()
    canvas.set_active_image("locked")
    assert not canvas._transform_handles
    canvas.set_read_only(True)
    canvas.set_read_only(False)
    before = canvas.image_transform("locked")
    canvas.set_image_position("locked", 50, 50)
    canvas.set_image_transform("locked", rotation=30, scale=2)
    assert canvas.image_transform("locked") == before

    def drag(image_id):
        start = canvas.mapFromScene(canvas._layers[image_id].mapToScene(QPointF(50, 40)))
        end = start + QPoint(30, 20)
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end, delay=20)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
        app.processEvents()

    try:
        drag("locked")
        assert canvas.image_transform("locked") == before
        canvas.set_selected_images({"locked", "free"})
        drag("free")
        assert canvas.image_transform("locked") == before
        assert canvas.image_transform("free")[:2] != (200, 0)
    finally:
        canvas.close()


def test_lock_action_handles_images_and_points_with_undo_and_arrange(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "image-lock.ini"), QSettings.Format.IniFormat))
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    first = window.project.add_image(tmp_path / "first.png")
    second = window.project.add_image(tmp_path / "second.png")
    first.position_x, first.position_y = 700, 800
    second.position_x = 1000
    for image in (first, second):
        window._image_pixmaps[image.id] = pixmap
    point = GroundControlPoint.create("point")
    point.set_observation(first.id, 10, 20)
    window.project.add_control_point(point)
    window._refresh_all()
    window._on_canvas_image_selected(first.id)
    assert window.shortcut_actions["toggle_lock"].isEnabled()
    window.selected_point_ids = [point.id]
    try:
        window.shortcut_actions["toggle_lock"].trigger()
        assert first.rest_locked
        assert point.observations[0].locked
        assert not second.rest_locked
        assert not window.canvas._transform_handles
        window.undo_stack.undo()
        assert not window.project.images[0].rest_locked
        assert not window.project.control_points[0].observations[0].locked
        window.undo_stack.redo()
        assert window.project.images[0].rest_locked
        window.arrange_images()
        assert (window.project.images[0].position_x, window.project.images[0].position_y) == (700, 800)
        window._on_canvas_image_selected(first.id)
        window.selected_point_ids = [point.id]
        window.toggle_selected_locks()
        assert not window.project.images[0].rest_locked
        assert not window.project.control_points[0].observations[0].locked
        assert window.canvas._transform_handles
    finally:
        window.close()


def test_observation_can_be_locked_and_dragged_after_unlock(tmp_path):
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "point-lock.png"
    pixmap = QPixmap(180, 140)
    pixmap.fill(QColor("white"))
    assert pixmap.save(str(image_path))

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(image_path)
    point = GroundControlPoint.create("corner-001")
    point.set_observation(image.id, 45.0, 55.0)
    window.project.add_control_point(point)
    window.current_image_id = image.id
    window._refresh_all()
    window.show()
    app.processEvents()
    window.canvas.fit_images()
    app.processEvents()
    window._select_point(point.id)

    assert window.lock_observation_checkbox.isEnabled()
    assert not window.lock_observation_checkbox.isChecked()
    window.lock_observation_checkbox.setChecked(True)
    assert point.observation_for(image.id).locked

    locked_target = window.canvas.mapFromScene(
        window.canvas._layers[image.id].mapToScene(QPointF(70.0, 75.0))
    )
    QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=locked_target)
    assert (point.observation_for(image.id).x, point.observation_for(image.id).y) == (45.0, 55.0)

    def drag_observation(target):
        marker = window.canvas._point_items[(image.id, point.id)]
        start = window.canvas.mapFromScene(marker.scenePos())
        end = window.canvas.mapFromScene(window.canvas._layers[image.id].mapToScene(target))
        QTest.mousePress(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(window.canvas.viewport(), end, delay=20)
        QTest.mouseRelease(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
        app.processEvents()

    original = point.observation_for(image.id)
    drag_observation(QPointF(95.0, 90.0))
    assert (original.x, original.y) == (45.0, 55.0)

    window.lock_observation_checkbox.setChecked(False)
    drag_observation(QPointF(95.0, 90.0))
    moved = window.project.point_by_id(point.id).observation_for(image.id)
    assert abs(moved.x - 95.0) < 2.0
    assert abs(moved.y - 90.0) < 2.0
    window.undo_stack.undo()
    restored = window.project.point_by_id(point.id).observation_for(image.id)
    assert (restored.x, restored.y, restored.locked) == (45.0, 55.0, False)
    window.undo_stack.undo()
    restored = window.project.point_by_id(point.id).observation_for(image.id)
    assert (restored.x, restored.y, restored.locked) == (45.0, 55.0, True)
    window.close()


def test_control_point_marker_size_is_constant_across_canvas_zoom():
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    canvas.resize(800, 600)
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    canvas.set_layers([{
        "id": "image",
        "pixmap": pixmap,
        "position_x": 0.0,
        "position_y": 0.0,
        "rotation": 0.0,
        "scale": 1.0,
        "points": [("point", 30.0, 40.0)],
    }])
    canvas.show()
    app.processEvents()
    marker = canvas._point_items[("image", "point")]
    assert not any(isinstance(item, QGraphicsTextItem) for item in canvas._scene.items())
    before = marker.deviceTransform(canvas.viewportTransform()).mapRect(marker.boundingRect())

    canvas.scale(3.0, 3.0)
    canvas._refresh_point_overlay_positions()
    after = marker.deviceTransform(canvas.viewportTransform()).mapRect(marker.boundingRect())

    assert abs(before.width() - after.width()) < 0.01
    assert abs(before.height() - after.height()) < 0.01
    canvas.set_image_transform("image", rotation=18.0, scale=1.5)
    expected_position = canvas._layers["image"].mapToScene(QPointF(30.0, 40.0))
    assert marker.pos() == expected_position
    canvas.close()


def test_selected_image_shows_screen_sized_transform_handles():
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    canvas.resize(800, 600)
    pixmap = QPixmap(160, 120)
    pixmap.fill(QColor("white"))
    canvas.set_layers([{
        "id": "image",
        "pixmap": pixmap,
        "position_x": 0.0,
        "position_y": 0.0,
        "rotation": 0.0,
        "scale": 1.0,
        "points": [],
    }])
    canvas.show()
    app.processEvents()

    assert not canvas._transform_handles
    canvas.set_active_image("image")
    assert {handle.data(0) for handle in canvas._transform_handles} == {
        "scale-nw", "scale-ne", "scale-se", "scale-sw", "rotate",
    }
    corner_handle = next(handle for handle in canvas._transform_handles if handle.data(0) == "scale-se")
    before = corner_handle.deviceTransform(canvas.viewportTransform()).mapRect(corner_handle.boundingRect())

    canvas.scale(2.0, 2.0)
    canvas._refresh_transform_handles()
    corner_handle = next(handle for handle in canvas._transform_handles if handle.data(0) == "scale-se")
    after = corner_handle.deviceTransform(canvas.viewportTransform()).mapRect(corner_handle.boundingRect())

    assert abs(before.width() - after.width()) < 0.01
    assert abs(before.height() - after.height()) < 0.01
    canvas.set_active_image(None)
    assert not canvas._transform_handles
    canvas.close()


def test_link_points_tool_creates_multiple_cross_image_constraints(tmp_path):
    app = QApplication.instance() or QApplication([])
    image_paths = [tmp_path / "left.png", tmp_path / "right.png"]
    for image_path in image_paths:
        pixmap = QPixmap(180, 140)
        pixmap.fill(QColor("white"))
        assert pixmap.save(str(image_path))

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    first_image, second_image = [window.project.add_image(path) for path in image_paths]
    points = [GroundControlPoint.create(name) for name in ("left-1", "left-2", "right-1", "right-2")]
    for point, image, x, y in (
        (points[0], first_image, 30.0, 40.0),
        (points[1], first_image, 80.0, 90.0),
        (points[2], second_image, 35.0, 45.0),
        (points[3], second_image, 85.0, 95.0),
    ):
        point.set_observation(image.id, x, y)
        window.project.add_control_point(point)
    window.current_image_id = first_image.id
    window._refresh_all()
    window.show()
    app.processEvents()
    window.canvas.fit_images()
    app.processEvents()
    window.add_points_action.setChecked(True)
    window.link_points_action.setChecked(True)
    assert not window.add_points_mode
    assert window.link_points_mode

    def click_marker(image_id, point_id):
        marker = window.canvas._point_items[(image_id, point_id)]
        position = window.canvas.mapFromScene(marker.scenePos())
        QTest.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=position)

    click_marker(first_image.id, points[0].id)
    assert window.pending_constraint_point_id == points[0].id
    click_marker(first_image.id, points[1].id)
    assert not window.project.constraints
    assert window.pending_constraint_point_id == points[0].id
    click_marker(second_image.id, points[2].id)
    assert len(window.project.constraints) == 1
    assert window.pending_constraint_point_id is None
    assert window.link_points_mode

    click_marker(first_image.id, points[1].id)
    click_marker(second_image.id, points[3].id)
    assert len(window.project.constraints) == 2
    assert window.pending_constraint_point_id is None
    assert window.link_points_action.isChecked()
    window.link_points_action.setChecked(False)
    window.close()


def test_dropping_multiple_images_imports_them_into_near_square_grid(tmp_path):
    app = QApplication.instance() or QApplication([])
    paths = []
    for index in range(5):
        image_path = tmp_path / f"image-{index}.png"
        pixmap = QPixmap(80, 50)
        pixmap.fill(QColor("white"))
        assert pixmap.save(str(image_path))
        paths.append(image_path)

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    window.show()
    app.processEvents()

    class DragEnterProbe:
        def __init__(self, mime_data):
            self._mime_data = mime_data
            self.accepted = False

        def mimeData(self):
            return self._mime_data

        def acceptProposedAction(self):
            self.accepted = True

        def ignore(self):
            self.accepted = False

    canvas_mime_data = QMimeData()
    canvas_mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in paths[:2]])
    drag_enter = DragEnterProbe(canvas_mime_data)
    window.canvas.dragEnterEvent(drag_enter)
    assert drag_enter.accepted
    drop = QDropEvent(
        QPointF(20, 20),
        Qt.DropAction.CopyAction,
        canvas_mime_data,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.canvas.dropEvent(drop)

    assert drop.isAccepted()

    window_mime_data = QMimeData()
    window_mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in paths[2:]])
    window_drag_enter = DragEnterProbe(window_mime_data)
    window.dragEnterEvent(window_drag_enter)
    assert window_drag_enter.accepted
    window_drop = QDropEvent(
        QPointF(20, 20),
        Qt.DropAction.CopyAction,
        window_mime_data,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(window_drop)

    assert window_drop.isAccepted()
    assert len(window.project.images) == 5
    positions = [(round(image.position_x), round(image.position_y)) for image in window.project.images]
    assert len({x for x, _y in positions}) == 3
    assert len({y for _x, y in positions}) == 2
    assert positions == [(0, 0), (120, 0), (240, 0), (0, 90), (120, 90)]
    window.undo_stack.undo()
    assert len(window.project.images) == 2
    window.undo_stack.redo()
    assert len(window.project.images) == 5
    window.close()


def test_import_supports_qt_reader_formats_omitted_from_old_filter(tmp_path):
    app = QApplication.instance() or QApplication([])
    svg_path = tmp_path / "diagram.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="16">'
        '<rect width="24" height="16" fill="#27675b"/></svg>',
        encoding="utf-8",
    )
    mislabeled_path = tmp_path / "photo.data"
    pixmap = QPixmap(18, 12)
    pixmap.fill(QColor("#27675b"))
    assert pixmap.save(str(mislabeled_path), "PNG")
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))

    window._import_image_paths([str(svg_path), str(mislabeled_path)])

    assert len(window.project.images) == 2
    sizes = {window.canvas._layers[image.id].pixmap().width() for image in window.project.images}
    assert sizes == {18, 24}
    window.close()


def test_unreadable_supported_extension_is_skipped_with_error_status(tmp_path):
    app = QApplication.instance() or QApplication([])
    invalid_path = tmp_path / "broken.png"
    invalid_path.write_bytes(b"not actually a png")
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))

    window._import_image_paths([str(invalid_path)])

    assert not window.project.images
    assert "broken.png" in window.statusBar().currentMessage()
    assert "No images imported" in window.statusBar().currentMessage()
    window.close()


def test_pillow_fallback_decodes_cmyk_jpeg_when_qt_reader_fails(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    jpeg_path = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (32, 24), (0, 80, 160, 0)).save(jpeg_path, quality=90)

    class FailedQtReader:
        def __init__(self, _path):
            pass

        def setAutoTransform(self, _enabled):
            pass

        def setDecideFormatFromContent(self, _enabled):
            pass

        def read(self):
            from PySide6.QtGui import QImage

            return QImage()

        def errorString(self):
            return "test Qt JPEG decode failure"

    monkeypatch.setattr("geostitch.app.QImageReader", FailedQtReader)
    pixmap, error = MainWindow._read_image_pixmap(jpeg_path)

    assert not pixmap.isNull()
    assert pixmap.size().width() == 32
    assert pixmap.size().height() == 24
    assert error == ""

def test_clip_polygon_masks_display_pixmaps_and_is_undoable(tmp_path):
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "clip.png"
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("red"))
    assert pixmap.save(str(image_path))
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    assert not window.canvas_clip_checkbox.isChecked()
    image = window.project.add_image(image_path)
    window.current_image_id = image.id
    window._refresh_all()
    window._on_canvas_image_selected(image.id)

    window._on_mask_polygon_drawn([[10.0, 10.0], [50.0, 10.0], [50.0, 50.0], [10.0, 50.0]])

    displayed, _error = window._display_pixmap_for_image(image)
    assert displayed.toImage().pixelColor(20, 20).alpha() == 255
    assert displayed.toImage().pixelColor(80, 70).alpha() == 0
    assert image.mask_enabled and window.mask_enable_checkbox.isChecked()
    window.mask_mode_combo.setCurrentIndex(1)
    displayed, _error = window._display_pixmap_for_image(image)
    assert displayed.toImage().pixelColor(20, 20).alpha() == 0
    window.mask_enable_checkbox.setChecked(False)
    assert window._display_pixmap_for_image(image)[0].toImage().pixelColor(80, 70).alpha() == 255
    window.undo_stack.undo()
    window.undo_stack.undo()
    window.undo_stack.undo()
    assert not window.project.images[0].mask_polygon
    window.close()
