from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsRectItem, QToolButton

from geostitch.app import MainWindow
from geostitch.models import GroundControlPoint


def test_map_points_overlay_button_toggles_markers_without_picking_coordinates(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "map-control.ini"), QSettings.Format.IniFormat))
    point = GroundControlPoint.create("Mapped point")
    point.geographic_x = 0
    point.geographic_y = 0
    window.project.add_control_point(point)
    window._refresh_all()
    window.show()
    window.world_map_dock.show()
    window.world_map_dock.raise_()
    app.processEvents()
    try:
        view = window.world_map
        button = window.map_points_button
        assert isinstance(button, QToolButton)
        assert button.parentWidget() is view.viewport()
        assert button.isCheckable()
        assert not button.isChecked()
        assert not button.icon().isNull()
        assert "Show control points" in button.toolTip()
        assert not view._point_overlay_items
        picked = []
        view.coordinate_picked.connect(lambda *args: picked.append(args))
        view.set_pick_mode(True)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert button.isChecked()
        assert len(view._point_overlay_items) == 1
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert not button.isChecked()
        assert not view._point_overlay_items
        assert picked == []
    finally:
        window.close()


def test_map_points_overlay_stays_top_right_during_navigation_and_resize(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "map-position.ini"), QSettings.Format.IniFormat))
    point = GroundControlPoint.create("Map point")
    window.project.add_control_point(point)
    window._select_point(point.id)
    window.pick_gcp_action.setChecked(True)
    view = window.world_map
    window.show()
    window.world_map_dock.setFloating(True)
    window.world_map_dock.resize(640, 420)
    window.world_map_dock.show()
    app.processEvents()
    try:
        button = window.map_points_button
        hint = window.map_tool_hint
        assert button.x() == view.viewport().width() - button.width() - 8
        assert button.y() == 8
        assert hint.pos().x() == 8
        assert hint.pos().y() == 8
        expected_hint = hint.geometry()
        expected = button.geometry()
        view.scale(2, 2)
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 30)
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 20)
        assert button.geometry() == expected
        assert hint.geometry() == expected_hint
        window.world_map_dock.resize(480, 320)
        app.processEvents()
        assert button.x() == view.viewport().width() - button.width() - 8
        assert button.y() == 8
        assert view.viewport().rect().contains(button.geometry())
        assert hint.pos().x() == 8
        assert hint.pos().y() == 8
    finally:
        window.close()


def test_geographic_checkpoint_has_a_distinct_world_map_marker(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "checkpoint-map.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image("checkpoint.png")
    point = GroundControlPoint.create("Independent check")
    point.set_observation(image.id, 20, 30)
    point.set_geographic_coordinates(10.0, 45.0, window.project.crs)
    point.geographic_checkpoint = True
    window.project.add_control_point(point)
    window._refresh_all()
    window.map_points_button.setChecked(True)
    try:
        marker = next(
            item for item in window.world_map._point_overlay_items
            if item.data(0) == point.id
        )
        assert isinstance(marker, QGraphicsRectItem)
        assert marker.rotation() == 45
        assert "independent checkpoint" in marker.toolTip()
        assert "[checkpoint]" in window._point_text(point)
    finally:
        window.close()