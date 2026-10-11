import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsEllipseItem

from geostitch.canvas import ImageCanvas
from geostitch.icons import KIND_COLORS
from geostitch.image_view import ImageViewer
from geostitch.map_view import WorldMapView


def test_point_and_selected_link_styles_match_between_views():
    app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    canvas = ImageCanvas()
    image_view = ImageViewer()
    world_map = WorldMapView()
    try:
        canvas.set_layers([
            {"id": "first", "pixmap": pixmap, "position_x": 0, "position_y": 0,
             "rotation": 0, "scale": 1, "points": [("one", 20, 30, True)]},
            {"id": "second", "pixmap": pixmap, "position_x": 150, "position_y": 0,
             "rotation": 0, "scale": 1, "points": [("two", 40, 50, True)]},
        ], [("one", "two")])
        canvas.set_selected_constraints({0})
        image_view.set_image("first", pixmap, [("one", "P1", 20, 30)], [], "inside", False,
                             selected_point_id="one", locked_point_ids={"one"})
        world_map.set_control_points([
            {"id": "one", "x": 20, "y": 30, "name": "P1", "locked": True,
             "checkpoint": False, "georeferenced": True, "selected": True},
            {"id": "two", "x": 170, "y": 50, "name": "P2", "locked": True,
             "checkpoint": False, "georeferenced": True, "selected": True},
        ], [{"first_id": "one", "second_id": "two", "selected": True, "color": KIND_COLORS["link"]}])

        canvas_marker = canvas._point_items[("first", "one")]
        image_marker = image_view._point_items["one"]
        map_marker = next(item for item in world_map._point_overlay_items if item.data(0) == "one")
        assert isinstance(map_marker, QGraphicsEllipseItem)
        for marker in (canvas_marker, image_marker, map_marker):
            assert marker.rect().width() == marker.rect().height() == 12
            assert marker.pen().color() == QColor("#ffffff")
            assert marker.pen().widthF() == 3
            assert marker.pen().style() == Qt.PenStyle.DashLine
            assert marker.brush().color() == QColor(KIND_COLORS["point"])

        canvas_line = canvas._constraint_items[0]
        canvas_outer, canvas_inner = canvas._constraint_halos[("one", "two", 0)]
        map_outer, map_inner, map_line = world_map._constraint_overlay_items
        assert map_line.pen().color() == canvas_line.pen().color() == QColor(KIND_COLORS["link"])
        assert map_line.pen().widthF() == canvas_line.pen().widthF() == 3
        assert map_outer.pen().color() == canvas_outer.pen().color() == QColor("#172022")
        assert map_inner.pen().color() == canvas_inner.pen().color() == QColor("#ffffff")
    finally:
        canvas.close()
        image_view.close()
        world_map.close()
