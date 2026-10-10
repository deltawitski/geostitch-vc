from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
import pytest

from PySide6.QtGui import QColor, QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from geostitch.app import MainWindow
from geostitch.image_view import ImageViewer
from geostitch.models import GroundControlPoint


@pytest.mark.parametrize("zoom", [0.2, 1.0, 5.0])
def test_image_view_placeholder_keeps_screen_size_after_deselection(zoom):
    app = QApplication.instance() or QApplication([])
    view = ImageViewer()
    view.resize(800, 600)
    view.show()
    app.processEvents()
    try:
        placeholder = view._placeholder_label
        expected = placeholder.geometry()
        assert placeholder.isVisible()
        assert placeholder.font().pointSizeF() >= 14
        pixmap = QPixmap(200, 150)
        pixmap.fill(QColor("white"))
        view.set_image("image", pixmap, [], [], "inside", False)
        assert not placeholder.isVisible()
        view.scale(zoom, zoom)
        view.set_image(None, None, [], [], "inside", False)
        assert placeholder.isVisible()
        assert placeholder.geometry() == expected
        view.scale(2, 2)
        assert placeholder.geometry() == expected
        view.translate(100, -150)
        assert placeholder.geometry() == expected
        start = view.viewport().rect().center()
        end = start + QPoint(40, 30)
        QTest.mousePress(view.viewport(), Qt.MouseButton.MiddleButton, pos=start)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(end),
                    QPointF(view.viewport().mapToGlobal(end)), Qt.MouseButton.NoButton,
                    Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), event)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.MiddleButton, pos=end)
        assert placeholder.geometry() == expected
        assert placeholder.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        view.resize(480, 320)
        app.processEvents()
        assert placeholder.geometry() == view.viewport().rect()
    finally:
        view.close()


def move_with_left_button(view, position):
    event = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(position),
        QPointF(view.viewport().mapToGlobal(position)),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(view.viewport(), event)


def make_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "image.png"
    pixmap = QPixmap(200, 150)
    pixmap.fill(QColor("white"))
    assert pixmap.save(str(image_path))
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(image_path)
    point = GroundControlPoint.create("corner-001")
    point.set_observation(image.id, 45.0, 55.0)
    window.project.add_control_point(point)
    window.current_image_id = image.id
    window._refresh_all()
    window.image_list.setCurrentItem(window.image_list.item(0))
    window.show()
    window.image_view_dock.show()
    window.image_view_dock.raise_()
    app.processEvents()
    window.image_viewer.fit_image()
    app.processEvents()
    return app, window, image.id, point.id


def test_image_view_drag_updates_observation_and_supports_undo_redo(tmp_path):
    app, window, image_id, point_id = make_window(tmp_path)
    view = window.image_viewer
    try:
        start = view.mapFromScene(QPointF(45, 55))
        end = view.mapFromScene(QPointF(95, 90))
        expected = view.mapToScene(end)
        before = window.undo_stack.count()
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        move_with_left_button(view, end)
        assert view._magnifier.isVisible()
        assert view._point_items[point_id].childItems() == []
        move_with_left_button(view, end + QPoint(8, 5))
        move_with_left_button(view, end)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        app.processEvents()

        observation = window.project.point_by_id(point_id).observation_for(image_id)
        assert abs(observation.x - expected.x()) < 1.0
        assert abs(observation.y - expected.y()) < 1.0
        assert window._selected_point_id() == point_id
        assert window.undo_stack.count() == before + 1
        marker = window.canvas._point_items[(image_id, point_id)]
        assert abs(marker.pos().x() - observation.x) < 1.0
        assert abs(marker.pos().y() - observation.y) < 1.0
        window.undo_stack.undo()
        restored = window.project.point_by_id(point_id).observation_for(image_id)
        assert (restored.x, restored.y) == (45.0, 55.0)
        window.undo_stack.redo()
        restored = window.project.point_by_id(point_id).observation_for(image_id)
        assert abs(restored.x - expected.x()) < 1.0
        assert abs(restored.y - expected.y()) < 1.0
    finally:
        window.close()


@pytest.mark.parametrize("zoom", [0.5, 2.0])
def test_image_view_drag_uses_image_pixels_at_any_zoom(zoom):
    app = QApplication.instance() or QApplication([])
    view = ImageViewer()
    view.resize(800, 600)
    pixmap = QPixmap(200, 150)
    pixmap.fill(QColor("white"))
    view.set_image("image", pixmap, [("point", "corner-001", 45, 55)], [], "inside", False)
    view.show()
    app.processEvents()
    view.fit_image()
    view.scale(zoom, zoom)
    changed = []
    started = []
    finished = []
    view.point_observation_changed.connect(lambda *args: changed.append(args))
    view.point_observation_interaction_started.connect(lambda *args: started.append(args))
    view.point_observation_interaction_finished.connect(lambda *args: finished.append(args))
    try:
        marker = view._point_items["point"]
        assert marker.childItems() == []
        assert not view._magnifier.isVisible()
        start = view.mapFromScene(marker.pos())
        end = view.mapFromScene(QPointF(95, 90))
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        move_with_left_button(view, end)
        assert view._magnifier.isVisible()
        assert view._magnifier._center == marker.scenePos()
        magnified = view._magnifier.grab().toImage()
        assert magnified.pixelColor(75, 75) == QColor("white")
        assert marker.isVisible()
        wheel = QWheelEvent(
            QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
            QPoint(), QPoint(0, 120), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        QApplication.sendEvent(view.viewport(), wheel)
        expected = view.mapToScene(end)
        assert view._magnifier._center == expected
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert not view._magnifier.isVisible()
        assert started == finished == [("image", "point")]
        assert changed[-1][:2] == ("image", "point")
        assert changed[-1][2:] == pytest.approx((expected.x(), expected.y()))
        assert marker.pos() == expected
        assert marker.rect().width() == 12
    finally:
        view.close()


@pytest.mark.parametrize("stop", ["image_change", "clear_image", "draw_polygon"])
def test_image_view_magnifier_hides_when_drag_is_interrupted(stop):
    app = QApplication.instance() or QApplication([])
    view = ImageViewer()
    view.resize(800, 600)
    pixmap = QPixmap(200, 150)
    pixmap.fill(QColor("white"))
    view.set_image("image", pixmap, [("point", "corner-001", 45, 55)], [], "inside", False)
    view.show()
    app.processEvents()
    view.fit_image()
    finished = []
    view.point_observation_interaction_finished.connect(lambda *args: finished.append(args))
    try:
        start = view.mapFromScene(QPointF(45, 55))
        end = view.mapFromScene(QPointF(95, 90))
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        move_with_left_button(view, end)
        assert view._magnifier.isVisible()
        if stop == "draw_polygon":
            view.set_drawing(True)
        elif stop == "clear_image":
            view.set_image(None, None, [], [], "inside", False)
        else:
            view.set_image("other", pixmap, [], [], "inside", False)
        assert not view._magnifier.isVisible()
        assert view._dragged_point_id is None
        assert finished == [("image", "point")]
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert finished == [("image", "point")]
    finally:
        view.close()


def test_image_view_polygon_drawing_does_not_drag_points():
    app = QApplication.instance() or QApplication([])
    view = ImageViewer()
    view.resize(800, 600)
    pixmap = QPixmap(200, 150)
    pixmap.fill(QColor("white"))
    view.set_image("image", pixmap, [("point", "corner-001", 45, 55)], [], "inside", False)
    view.show()
    app.processEvents()
    view.fit_image()
    changed = []
    view.point_observation_changed.connect(lambda *args: changed.append(args))
    try:
        view.set_drawing(True)
        start = view.mapFromScene(QPointF(45, 55))
        end = view.mapFromScene(QPointF(95, 90))
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        move_with_left_button(view, end)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert len(view._draft) == 1
        assert view._point_items["point"].pos() == QPointF(45, 55)
        assert changed == []
        assert not view._magnifier.isVisible()
    finally:
        view.close()


def test_image_view_click_and_locked_drag_do_not_move_point(tmp_path):
    app, window, image_id, point_id = make_window(tmp_path)
    view = window.image_viewer
    try:
        start = view.mapFromScene(QPointF(45, 55))
        before = window.undo_stack.count()
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        move_with_left_button(view, start + QPoint(1, 1))
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        assert window._selected_point_id() == point_id
        assert window.undo_stack.count() == before
        assert not view._magnifier.isVisible()
        window.lock_observation_checkbox.setChecked(True)
        before = window.undo_stack.count()
        end = view.mapFromScene(QPointF(95, 90))
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        move_with_left_button(view, end)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        app.processEvents()
        observation = window.project.point_by_id(point_id).observation_for(image_id)
        assert (observation.x, observation.y, observation.locked) == (45.0, 55.0, True)
        assert window.undo_stack.count() == before
        assert not view._magnifier.isVisible()
    finally:
        window.close()