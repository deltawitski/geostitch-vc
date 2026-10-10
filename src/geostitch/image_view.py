from __future__ import annotations

from math import hypot

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from geostitch.canvas import _Magnifier
from geostitch.masking import hidden_path, polygon_path
from geostitch.icons import KIND_COLORS
from geostitch.view_navigation import zoom_at_cursor

_ACCENT = QColor("#76cbb0")


class ImageViewer(QGraphicsView):
    """Shows one image with its control points, its clip polygon, and a polygon drawing tool."""

    polygon_drawn = Signal(list)
    point_clicked = Signal(str)
    point_observation_interaction_started = Signal(str, str)
    point_observation_changed = Signal(str, str, float, float)
    point_observation_interaction_finished = Signal(str, str)
    POINT_DRAG_THRESHOLD = 5
    CLOSE_DISTANCE_PIXELS = 10.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(QBrush(QColor("#20272b")))
        self.setRenderHint(self.renderHints() | self.renderHints().Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._panning = False
        self._last_pan_position = None
        self._image_rect = QRectF()
        self._image_key: str | None = None
        self._point_items: dict[str, QGraphicsEllipseItem] = {}
        self._locked_points: set[str] = set()
        self._pending_point_drag: tuple[str, QPoint] | None = None
        self._dragged_point_id: str | None = None
        self._magnifier = _Magnifier(self)
        self._drawing = False
        self._draft: list[QPointF] = []
        self._cursor_scene: QPointF | None = None
        self._draft_item: QGraphicsPathItem | None = None
        self._placeholder()

    def _placeholder(self) -> None:
        self._scene.clear()
        self._point_items.clear()
        self._locked_points.clear()
        self._draft_item = None
        self._image_rect = QRectF()
        self._image_key = None
        text = self._scene.addSimpleText("Select an image to view it here")
        text.setBrush(QBrush(QColor("#899994")))
        self._scene.setSceneRect(text.boundingRect())

    def has_image(self) -> bool:
        return not self._image_rect.isEmpty()

    def set_image(
        self,
        key: str | None,
        pixmap: QPixmap | None,
        points: list[tuple[str, str, float, float]],
        polygon: list[list[float]],
        mode: str,
        enabled: bool,
        selected_point_id: str | None = None,
        locked_point_ids: set[str] | None = None,
    ) -> None:
        """`points` are (id, name, x, y) in image pixels; the view keeps its zoom while `key` is unchanged."""
        if key != self._image_key or pixmap is None or pixmap.isNull():
            self._finish_point_drag()
        if pixmap is None or pixmap.isNull():
            self.cancel_drawing()
            self._placeholder()
            return
        same_image = key == self._image_key
        self._scene.clear()
        self._point_items.clear()
        self._locked_points = set(locked_point_ids or ())
        self._draft_item = None
        self._image_key = key
        self._image_rect = QRectF(0.0, 0.0, pixmap.width(), pixmap.height())
        margin = max(pixmap.width(), pixmap.height()) * 4.0
        self._scene.setSceneRect(self._image_rect.adjusted(-margin, -margin, margin, margin))

        item = QGraphicsPixmapItem(pixmap)
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(item)

        if len(polygon) >= 3:
            if enabled:
                dim = QGraphicsPathItem(hidden_path(pixmap.width(), pixmap.height(), polygon, mode))
                dim.setPen(QPen(Qt.PenStyle.NoPen))
                dim.setBrush(QBrush(QColor(0, 0, 0, 170)))
                dim.setZValue(10)
                self._scene.addItem(dim)
            outline = QGraphicsPathItem(polygon_path(polygon))
            pen = QPen(_ACCENT, 2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            outline.setPen(pen)
            outline.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            outline.setZValue(20)
            self._scene.addItem(outline)

        for point_id, _name, x, y in points:
            selected = point_id == selected_point_id
            marker = QGraphicsEllipseItem(-6, -6, 12, 12)
            marker.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            marker.setPos(x, y)
            color = QColor(KIND_COLORS["point"])
            style = Qt.PenStyle.DashLine if point_id in self._locked_points else Qt.PenStyle.SolidLine
            marker.setPen(QPen(color, 3 if selected else 2, style))
            marker.setBrush(QBrush(color if selected else color.lighter(140)))
            marker.setZValue(30)
            marker.setData(0, point_id)
            self._scene.addItem(marker)
            self._point_items[point_id] = marker

        if not same_image:
            self.fit_image()
        self._redraw_draft()

    def overlay_items(self) -> list:
        return [
            *self._point_items.values(),
            *([self._draft_item] if self._draft_item is not None else []),
        ]

    def fit_image(self) -> None:
        if self.has_image():
            self.fitInView(self._image_rect.adjusted(-20, -20, 20, 20), Qt.AspectRatioMode.KeepAspectRatio)

    def set_drawing(self, enabled: bool) -> None:
        if enabled and not self.has_image():
            return
        if enabled:
            self._finish_point_drag()
        if not enabled:
            self.cancel_drawing()
        self._drawing = enabled
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        if enabled:
            self.setFocus()

    def cancel_drawing(self) -> None:
        self._draft.clear()
        self._cursor_scene = None
        self._redraw_draft()

    def _clamped(self, scene_position: QPointF) -> QPointF:
        return QPointF(
            min(max(scene_position.x(), 0.0), self._image_rect.width()),
            min(max(scene_position.y(), 0.0), self._image_rect.height()),
        )

    def _redraw_draft(self) -> None:
        if self._draft_item is not None and self._draft_item.scene() is self._scene:
            self._scene.removeItem(self._draft_item)
        self._draft_item = None
        if not self._draft:
            return
        path = QPainterPath(self._draft[0])
        for vertex in self._draft[1:]:
            path.lineTo(vertex)
        if self._cursor_scene is not None:
            path.lineTo(self._cursor_scene)
        item = QGraphicsPathItem(path)
        pen = QPen(_ACCENT, 2)
        pen.setCosmetic(True)
        item.setPen(pen)
        item.setZValue(40)
        self._scene.addItem(item)
        for index, vertex in enumerate(self._draft):
            dot = QGraphicsEllipseItem(-4, -4, 8, 8, item)
            dot.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            dot.setPos(vertex)
            dot.setBrush(QBrush(_ACCENT if index else QColor("#ffffff")))
            dot.setPen(QPen(QColor("#14201d"), 1))
        self._draft_item = item

    def _near_first_vertex(self, scene_position: QPointF) -> bool:
        if len(self._draft) < 3:
            return False
        first = self.mapFromScene(self._draft[0])
        here = self.mapFromScene(scene_position)
        return hypot(first.x() - here.x(), first.y() - here.y()) <= self.CLOSE_DISTANCE_PIXELS

    def finish_drawing(self) -> bool:
        if len(self._draft) < 3:
            return False
        polygon = [[vertex.x(), vertex.y()] for vertex in self._draft]
        self.cancel_drawing()
        self.polygon_drawn.emit(polygon)
        return True

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._last_pan_position = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        scene_position = self.mapToScene(event.position().toPoint())
        if self._drawing and self.has_image():
            if event.button() == Qt.MouseButton.LeftButton:
                if self._near_first_vertex(scene_position):
                    self.finish_drawing()
                else:
                    self._draft.append(self._clamped(scene_position))
                    self._redraw_draft()
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                if self._draft:
                    self._draft.pop()
                    self._redraw_draft()
                event.accept()
                return
        elif event.button() == Qt.MouseButton.LeftButton:
            for item in self.items(event.position().toPoint()):
                while item is not None and item.data(0) not in self._point_items:
                    item = item.parentItem()
                point_id = item.data(0) if item is not None else None
                if point_id is not None:
                    self.point_clicked.emit(point_id)
                    if point_id in self._point_items and point_id not in self._locked_points:
                        self._pending_point_drag = (point_id, event.position().toPoint())
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._drawing and event.button() == Qt.MouseButton.LeftButton:
            self.finish_drawing()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._last_pan_position is not None:
            position = event.position().toPoint()
            delta = position - self._last_pan_position
            self._last_pan_position = position
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._pending_point_drag is not None:
            point_id, press_position = self._pending_point_drag
            if (event.position().toPoint() - press_position).manhattanLength() < self.POINT_DRAG_THRESHOLD:
                event.accept()
                return
            self._pending_point_drag = None
            if point_id in self._locked_points:
                event.accept()
                return
            self._dragged_point_id = point_id
            self.point_observation_interaction_started.emit(self._image_key, point_id)
        if self._dragged_point_id is not None:
            self._move_point_marker(event.position().toPoint())
            event.accept()
            return
        if self._drawing and self._draft:
            self._cursor_scene = self._clamped(self.mapToScene(event.position().toPoint()))
            self._redraw_draft()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and (
            self._pending_point_drag is not None or self._dragged_point_id is not None
        ):
            self._finish_point_drag()
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.viewport().setCursor(Qt.CursorShape.CrossCursor if self._drawing else Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _move_point_marker(self, viewport_position: QPoint) -> None:
        point_id = self._dragged_point_id
        marker = self._point_items.get(point_id)
        if marker is None or point_id in self._locked_points or self._image_key is None:
            return
        position = self.mapToScene(viewport_position)
        marker.setPos(position)
        self._magnifier.track(position, viewport_position)
        self.point_observation_changed.emit(self._image_key, point_id, position.x(), position.y())

    def _finish_point_drag(self) -> None:
        point_id = self._dragged_point_id
        self._pending_point_drag = None
        self._dragged_point_id = None
        self._magnifier.hide()
        if point_id is not None and self._image_key is not None:
            self.point_observation_interaction_finished.emit(self._image_key, point_id)

    def keyPressEvent(self, event) -> None:
        if self._drawing:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.finish_drawing()
                return
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_drawing()
                return
            if event.key() == Qt.Key.Key_Backspace and self._draft:
                self._draft.pop()
                self._redraw_draft()
                return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        cursor = zoom_at_cursor(self, event, factor)
        if self._dragged_point_id is not None:
            self._move_point_marker(cursor)
        event.accept()
