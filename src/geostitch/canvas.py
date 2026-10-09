from __future__ import annotations

from math import atan2, degrees, hypot

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)


class _Magnifier(QWidget):
    """Circular zoomed view of the scene around a point, with a crosshair at its centre."""

    DIAMETER = 150
    ZOOM = 4.0
    CURSOR_OFFSET = 24

    def __init__(self, view: QGraphicsView):
        super().__init__(view.viewport())
        self._view = view
        self._center = QPointF()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(self.DIAMETER, self.DIAMETER)
        self.hide()

    def track(self, scene_center: QPointF, cursor: QPoint) -> None:
        self._center = scene_center
        viewport = self._view.viewport().rect()
        x = cursor.x() + self.CURSOR_OFFSET
        y = cursor.y() - self.CURSOR_OFFSET - self.DIAMETER
        if x + self.DIAMETER > viewport.right():
            x = cursor.x() - self.CURSOR_OFFSET - self.DIAMETER
        if y < viewport.top():
            y = cursor.y() + self.CURSOR_OFFSET
        self.move(x, y)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        circle = QPainterPath()
        circle.addEllipse(QRectF(1, 1, self.DIAMETER - 2, self.DIAMETER - 2))
        painter.setClipPath(circle)
        painter.fillRect(self.rect(), QColor("#20272b"))
        side = self.DIAMETER / (self._view.transform().m11() * self.ZOOM)
        source = QRectF(self._center.x() - side / 2, self._center.y() - side / 2, side, side)
        overlays = self._view.overlay_items()
        for item in overlays:
            item.setVisible(False)
        self._view.scene().render(painter, QRectF(self.rect()), source)
        for item in overlays:
            item.setVisible(True)
        painter.setClipping(False)
        middle = self.DIAMETER / 2
        gap = 6
        edge = 10
        for color, width in ((QColor(0, 0, 0, 90), 2), (QColor(255, 255, 255, 190), 1)):
            painter.setPen(QPen(color, width))
            painter.drawLine(QPointF(middle, edge), QPointF(middle, middle - gap))
            painter.drawLine(QPointF(middle, middle + gap), QPointF(middle, self.DIAMETER - edge))
            painter.drawLine(QPointF(edge, middle), QPointF(middle - gap, middle))
            painter.drawLine(QPointF(middle + gap, middle), QPointF(self.DIAMETER - edge, middle))
        painter.setPen(QPen(QColor("#65cdb1"), 3))
        painter.drawEllipse(QRectF(1.5, 1.5, self.DIAMETER - 3, self.DIAMETER - 3))


class ImageCanvas(QGraphicsView):
    NAVIGATION_EXTENT = 10_000_000.0
    POINT_DRAG_THRESHOLD = 5

    image_clicked = Signal(str, float, float)
    files_dropped = Signal(list)
    point_marker_clicked = Signal(str, str)
    point_marker_selected = Signal(str, str)
    point_marker_missed = Signal()
    point_observation_changed = Signal(str, str, float, float)
    point_observation_interaction_started = Signal(str, str)
    point_observation_interaction_finished = Signal(str, str)
    image_selected = Signal(str)
    image_toggled = Signal(str)
    area_selected = Signal(list, list, list, bool)
    constraint_clicked = Signal(int)
    constraint_toggled = Signal(int)
    point_marker_toggled = Signal(str, str)
    background_clicked = Signal()
    transform_changed = Signal(str, float, float, float, float)
    transform_interaction_started = Signal(str)
    transform_interaction_finished = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._scene.setSceneRect(QRectF(
            -self.NAVIGATION_EXTENT,
            -self.NAVIGATION_EXTENT,
            self.NAVIGATION_EXTENT * 2,
            self.NAVIGATION_EXTENT * 2,
        ))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor("#20272b")))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHint(self.renderHints() | self.renderHints().Antialiasing)
        self._layers: dict[str, QGraphicsPixmapItem] = {}
        self._points: dict[tuple[str, str], tuple[float, float]] = {}
        self._point_items: dict[tuple[str, str], QGraphicsEllipseItem] = {}
        self._locked_points: set[tuple[str, str]] = set()
        self._dragged_point_key: tuple[str, str] | None = None
        self._magnifier = _Magnifier(self)
        self._pending_point_drag: tuple[tuple[str, str], QPoint] | None = None
        self._selection_mode = "rect"
        self._marquee_origin: QPoint | None = None
        self._marquee_points: list[QPointF] = []
        self._marquee_active = False
        self._marquee_background = False
        self._marquee_item: QGraphicsPathItem | None = None
        self._transform_handles: list[QGraphicsEllipseItem] = []
        self._transform_connector: QGraphicsLineItem | None = None
        self._active_transform_handle: str | None = None
        self._transform_start_scene = QPointF()
        self._transform_center_scene = QPointF()
        self._transform_start_scale = 1.0
        self._transform_start_rotation = 0.0
        self._transform_start_distance = 1.0
        self._constraints: list[tuple[str, str]] = []
        self._constraint_items: list[QGraphicsLineItem] = []
        self._selected_constraint_indices: set[int] = set()
        self._active_image_id: str | None = None
        self._selected_image_ids: set[str] = set()
        self._selection_outlines: list[QGraphicsRectItem] = []
        self._selected_point_ids: set[str] = set()
        self._pending_collapse_id: str | None = None
        self._drag_moved = False
        self._placing_point = False
        self._active_point_id: str | None = None
        self._linking_points = False
        self._constraint_source_id: str | None = None
        self._panning = False
        self._placement_click = False
        self._last_pan_position = None
        self._dragged_image_id: str | None = None
        self._drag_start = None
        self._read_only = False

    def set_layers(self, layers: list[dict], constraints: list[tuple[str, str]] | None = None) -> None:
        previous_selected_ids = set(self._selected_image_ids)
        self._selection_outlines.clear()
        self._marquee_item = None
        self._marquee_origin = None
        self._scene.clear()
        self._layers.clear()
        self._points.clear()
        self._point_items.clear()
        self._locked_points.clear()
        self._dragged_point_key = None
        self._transform_handles.clear()
        self._transform_connector = None
        self._active_transform_handle = None
        self._constraint_items.clear()
        self._constraints = []
        self._active_image_id = None
        self._selected_image_ids = set()
        valid_layers = [layer for layer in layers if not layer["pixmap"].isNull()]
        if not valid_layers:
            return

        layout_x = 0.0
        for index, layer in enumerate(valid_layers):
            image_id = layer["id"]
            pixmap_item = QGraphicsPixmapItem(layer["pixmap"])
            pixmap_item.setData(0, image_id)
            pixmap_item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable, True)
            pixmap_item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable, not self._read_only)
            pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            pixmap_item.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
            pixmap_item.setZValue(index)
            pixmap_item.setVisible(layer.get("visible", True))
            x = layer["position_x"] if layer["position_x"] is not None else layout_x
            pixmap_item.setPos(x, layer["position_y"])
            pixmap_item.setRotation(layer["rotation"])
            pixmap_item.setScale(layer["scale"])
            layout_x = x + layer["pixmap"].width() * layer["scale"] + 40.0
            self._scene.addItem(pixmap_item)
            self._layers[image_id] = pixmap_item
            for point_data in layer["points"]:
                visible = True
                if len(point_data) == 5:
                    point_id, pixel_x, pixel_y, locked, visible = point_data
                elif len(point_data) == 4:
                    point_id, pixel_x, pixel_y, locked = point_data
                else:
                    point_id, pixel_x, pixel_y = point_data
                    locked = False
                self._add_point_marker(pixmap_item, point_id, pixel_x, pixel_y, locked, visible)

        for first_id, second_id in constraints or []:
            self._constraints.append((first_id, second_id))
        self._apply_image_selection(previous_selected_ids)
        self._redraw_constraints()
        self.set_constraint_source(self._constraint_source_id)
        self._refresh_transform_handles()

    def set_selection_mode(self, mode: str) -> None:
        self._selection_mode = "lasso" if mode == "lasso" else "rect"

    def _marquee_path(self) -> QPainterPath:
        path = QPainterPath()
        if self._selection_mode == "lasso":
            path.addPolygon(QPolygonF(self._marquee_points))
        else:
            path.addRect(QRectF(self._marquee_points[0], self._marquee_points[-1]).normalized())
        path.closeSubpath()
        return path

    def _update_marquee(self, viewport_position: QPoint) -> None:
        if not self._marquee_active:
            if (viewport_position - self._marquee_origin).manhattanLength() < self.POINT_DRAG_THRESHOLD:
                return
            self._marquee_active = True
            self._marquee_item = QGraphicsPathItem()
            pen = QPen(QColor("#65cdb1"), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._marquee_item.setPen(pen)
            self._marquee_item.setBrush(QBrush(QColor(101, 205, 177, 40)))
            self._marquee_item.setZValue(5000)
            self._scene.addItem(self._marquee_item)
        scene_position = self.mapToScene(viewport_position)
        if self._selection_mode == "lasso":
            self._marquee_points.append(scene_position)
        else:
            self._marquee_points = [self._marquee_points[0], scene_position]
        self._marquee_item.setPath(self._marquee_path())

    def _finish_marquee(self, additive: bool) -> None:
        active, background = self._marquee_active, self._marquee_background
        path = self._marquee_path() if active else None
        if self._marquee_item is not None:
            self._scene.removeItem(self._marquee_item)
        self._marquee_item = None
        self._marquee_origin = None
        self._marquee_active = False
        if path is None:
            if background and not additive:
                self.background_clicked.emit()
            return
        images = []
        for image_id, item in self._layers.items():
            if not item.isVisible():
                continue
            corners = (item.mapToScene(corner) for corner in (
                item.boundingRect().topLeft(), item.boundingRect().topRight(),
                item.boundingRect().bottomRight(), item.boundingRect().bottomLeft(),
            ))
            if all(path.contains(corner) for corner in corners):
                images.append(image_id)
        points = [
            key for key, marker in self._point_items.items()
            if marker.isVisible() and path.contains(marker.scenePos())
        ]
        links = []
        for index, (first_id, second_id) in enumerate(self._constraints):
            first = self._scene_position_for_point(first_id)
            second = self._scene_position_for_point(second_id)
            if first is not None and second is not None and path.contains(first) and path.contains(second):
                links.append(index)
        self.area_selected.emit(images, points, links, additive)

    def overlay_items(self) -> list:
        """Markers, links, handles and outlines drawn above the images."""
        return [
            *self._point_items.values(),
            *self._constraint_items,
            *self._transform_handles,
            *self._selection_outlines,
            *([self._transform_connector] if self._transform_connector is not None else []),
        ]

    def set_read_only(self, read_only: bool) -> None:
        """Disable moving, scaling and rotating images (point markers stay editable)."""
        self._read_only = read_only
        for item in self._layers.values():
            item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable, not read_only)
        self._refresh_transform_handles()

    def _add_point_marker(
        self, parent: QGraphicsPixmapItem, point_id: str, x: float, y: float, locked: bool = False, visible: bool = True
    ) -> None:
        marker = QGraphicsEllipseItem(-6, -6, 12, 12)
        marker.setVisible(visible)
        marker.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
        marker.setPos(parent.mapToScene(QPointF(x, y)))
        marker.setPen(QPen(QColor("#ff695e"), 2))
        marker.setBrush(QBrush(QColor(255, 105, 94, 90)))
        marker.setZValue(3000)
        marker.setData(0, point_id)
        marker.setData(1, parent.data(0))
        marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(marker)
        key = (parent.data(0), point_id)
        self._points[key] = (x, y)
        self._point_items[key] = marker
        if locked:
            self._locked_points.add(key)
        self._refresh_marker_styles()

    def _apply_image_selection(self, image_ids: set[str]) -> None:
        self._selected_image_ids = {image_id for image_id in image_ids if image_id in self._layers}
        # Transform handles only make sense for a single image.
        self._active_image_id = next(iter(self._selected_image_ids)) if len(self._selected_image_ids) == 1 else None
        for index, (image_id, item) in enumerate(self._layers.items()):
            selected = image_id in self._selected_image_ids
            item.setSelected(selected)
            item.setZValue(index)
        for outline in self._selection_outlines:
            self._scene.removeItem(outline)
        self._selection_outlines.clear()
        for image_id in self._selected_image_ids:
            item = self._layers[image_id]
            outline = QGraphicsRectItem(item.boundingRect(), item)
            pen = QPen(QColor("#65cdb1"), 2)
            pen.setCosmetic(True)
            outline.setPen(pen)
            outline.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            outline.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._selection_outlines.append(outline)
        self._refresh_transform_handles()

    def set_selected_images(self, image_ids: set[str]) -> None:
        self._apply_image_selection(image_ids)
        self._redraw_constraints()

    def set_active_image(self, image_id: str | None) -> None:
        if image_id not in self._layers:
            self._apply_image_selection(set())
            return
        changed = image_id != self._active_image_id or len(self._selected_image_ids) != 1
        self._apply_image_selection({image_id})
        self._redraw_constraints()
        if changed:
            self.image_selected.emit(image_id)

    def set_active_point(self, point_id: str | None) -> None:
        self._active_point_id = point_id
        self._placing_point = point_id is not None

    def set_constraint_mode(self, enabled: bool) -> None:
        self._linking_points = enabled
        if enabled:
            self._placing_point = False

    def set_constraint_source(self, point_id: str | None) -> None:
        self._constraint_source_id = point_id
        self._refresh_marker_styles()

    def set_selected_point(self, point_id: str | None) -> None:
        self.set_selected_points({point_id} if point_id else set())

    def set_selected_points(self, point_ids: set[str]) -> None:
        self._selected_point_ids = set(point_ids)
        self._refresh_marker_styles()

    def set_observation_locked(self, image_id: str, point_id: str, locked: bool) -> None:
        key = (image_id, point_id)
        if locked:
            self._locked_points.add(key)
        else:
            self._locked_points.discard(key)
        self._refresh_marker_styles()

    def _refresh_marker_styles(self) -> None:
        for (_image_id, candidate_id), marker in self._point_items.items():
            is_source = candidate_id == self._constraint_source_id
            is_selected = candidate_id in self._selected_point_ids
            is_locked = (_image_id, candidate_id) in self._locked_points
            color = QColor("#f4c95d") if is_source else QColor("#65cdb1") if is_selected else QColor("#79a9d1") if is_locked else QColor("#ff695e")
            marker.setPen(QPen(color, 3 if is_source or is_selected else 2))
            marker.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 100 if is_source or is_selected or is_locked else 90)))

    def set_constraints(self, constraints: list[tuple[str, str]]) -> None:
        self._constraints = constraints
        self._redraw_constraints()

    def set_image_transform(self, image_id: str, rotation: float | None = None, scale: float | None = None) -> None:
        item = self._layers.get(image_id)
        if item is None:
            return
        if rotation is not None:
            item.setRotation(rotation)
        if scale is not None:
            item.setScale(scale)
        self._redraw_constraints()
        self._emit_transform(image_id)

    def set_image_position(self, image_id: str, x: float, y: float) -> None:
        item = self._layers.get(image_id)
        if item is None:
            return
        item.setPos(x, y)
        self._emit_transform(image_id)

    def image_transform(self, image_id: str) -> tuple[float, float, float, float] | None:
        item = self._layers.get(image_id)
        if item is None:
            return None
        position = item.pos()
        return position.x(), position.y(), item.rotation(), item.scale()

    def view_state(self) -> dict:
        center = self.mapToScene(self.viewport().rect().center())
        return {"center": [center.x(), center.y()], "scale": self.transform().m11()}

    def restore_view_state(self, state: dict) -> None:
        try:
            center_x, center_y = (float(value) for value in state["center"])
            scale = float(state["scale"])
        except (KeyError, TypeError, ValueError):
            return
        if scale <= 0:
            return
        self.setTransform(QTransform.fromScale(scale, scale))
        self.centerOn(center_x, center_y)
        self._refresh_point_overlay_positions()
        self._refresh_transform_handles()

    def fit_images(self, image_ids: set[str] | None = None) -> None:
        image_ids = image_ids or set(self._layers)
        selected_items = [self._layers[image_id] for image_id in image_ids if image_id in self._layers]
        if not selected_items:
            return
        rect = selected_items[0].sceneBoundingRect()
        for item in selected_items[1:]:
            rect = rect.united(item.sceneBoundingRect())
        if not rect.isEmpty():
            self.fitInView(rect.adjusted(-20, -20, 20, 20), Qt.AspectRatioMode.KeepAspectRatio)
            self._refresh_point_overlay_positions()
            self._refresh_transform_handles()

    def fit_items(self, image_ids: set[str], point_ids: set[str], constraint_indices: set[int]) -> None:
        rects = [self._layers[image_id].sceneBoundingRect() for image_id in image_ids if image_id in self._layers]
        positions = [self._scene_position_for_point(point_id) for point_id in point_ids]
        for index in constraint_indices:
            if 0 <= index < len(self._constraints):
                positions.extend(self._scene_position_for_point(point_id) for point_id in self._constraints[index])
        for position in positions:
            if position is not None:
                rects.append(QRectF(position, position))
        if not rects:
            return
        rect = rects[0]
        for other in rects[1:]:
            rect = rect.united(other)
        # Keep a lone point or short link from zooming in absurdly far.
        minimum = 120.0
        rect = rect.adjusted(
            -max(0.0, (minimum - rect.width()) / 2), -max(0.0, (minimum - rect.height()) / 2),
            max(0.0, (minimum - rect.width()) / 2), max(0.0, (minimum - rect.height()) / 2),
        )
        margin = max(rect.width(), rect.height()) * 0.1
        self.fitInView(rect.adjusted(-margin, -margin, margin, margin), Qt.AspectRatioMode.KeepAspectRatio)
        self._refresh_point_overlay_positions()
        self._refresh_transform_handles()

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        # Qt's mouse anchor goes stale while a point drag swallows mouse moves, so anchor manually.
        cursor = event.position().toPoint()
        scene_before = self.mapToScene(cursor)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.scale(factor, factor)
        shift = self.mapFromScene(scene_before) - cursor
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + shift.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + shift.y())
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._refresh_point_overlay_positions()
        self._refresh_transform_handles()
        if self._dragged_point_key is not None:
            self._move_point_marker(self._dragged_point_key, self.mapToScene(cursor))
            marker = self._point_items.get(self._dragged_point_key)
            if marker is not None:
                self._magnifier.track(marker.scenePos(), cursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._last_pan_position = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._linking_points:
                point = self._point_marker_at(event.position().toPoint())
                if point is None:
                    self.point_marker_missed.emit()
                else:
                    image_id, point_id = point
                    self.point_marker_clicked.emit(image_id, point_id)
                self._placement_click = True
                event.accept()
                return
            if self._active_point_id != "__continuous_add__":
                handle_kind = self._transform_handle_at(event.position().toPoint())
                if handle_kind is not None:
                    if self._active_image_id is not None:
                        self.transform_interaction_started.emit(self._active_image_id)
                    self._begin_transform_handle(handle_kind, self.mapToScene(event.position().toPoint()))
                    event.accept()
                    return
            additive = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if self._active_point_id != "__continuous_add__":
                point = self._point_marker_at(event.position().toPoint())
                if point is not None and additive:
                    self.point_marker_toggled.emit(*point)
                    self._placement_click = True
                    event.accept()
                    return
                if point is not None:
                    image_id, point_id = point
                    self.set_active_image(image_id)
                    self.point_marker_selected.emit(image_id, point_id)
                    key = (image_id, point_id)
                    if key not in self._locked_points:
                        self._pending_point_drag = (key, event.position().toPoint())
                    self._placement_click = True
                    event.accept()
                    return
                constraint_index = self._constraint_at(event.position().toPoint())
                if constraint_index is not None:
                    if additive:
                        self.constraint_toggled.emit(constraint_index)
                    else:
                        self.constraint_clicked.emit(constraint_index)
                    self._placement_click = True
                    event.accept()
                    return
            scene_position = self.mapToScene(event.position().toPoint())
            image_id = self._image_at(scene_position)
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if not self._placing_point and (image_id is None or shift):
                self._marquee_origin = event.position().toPoint()
                self._marquee_points = [scene_position]
                self._marquee_active = False
                self._marquee_background = image_id is None
                event.accept()
                return
            if image_id is None:
                self.background_clicked.emit()
                super().mousePressEvent(event)
                return
            if self._placing_point:
                local_position = self._layers[image_id].mapFromScene(scene_position)
                self.image_clicked.emit(image_id, local_position.x(), local_position.y())
                self._placement_click = True
                event.accept()
                return
            if additive:
                self.image_toggled.emit(image_id)
                self._placement_click = True
                event.accept()
                return
            if image_id in self._selected_image_ids and len(self._selected_image_ids) > 1:
                # Keep the group so it can be dragged together; collapse on release if it did not move.
                self._pending_collapse_id = image_id
            else:
                self.set_active_image(image_id)
            if self._read_only:
                return
            self._drag_moved = False
            self._dragged_image_id = image_id
            self.transform_interaction_started.emit(image_id)
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._last_pan_position is not None:
            position = event.position().toPoint()
            delta = position - self._last_pan_position
            self._last_pan_position = position
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return
        if self._marquee_origin is not None:
            self._update_marquee(event.position().toPoint())
            return
        if self._active_transform_handle is not None:
            self._update_transform_handle(self.mapToScene(event.position().toPoint()))
            return
        if self._pending_point_drag is not None:
            key, press_position = self._pending_point_drag
            if (event.position().toPoint() - press_position).manhattanLength() < self.POINT_DRAG_THRESHOLD:
                return
            self._pending_point_drag = None
            self._dragged_point_key = key
            self.point_observation_interaction_started.emit(*key)
        if self._dragged_point_key is not None:
            self._move_point_marker(self._dragged_point_key, self.mapToScene(event.position().toPoint()))
            marker = self._point_items.get(self._dragged_point_key)
            if marker is not None:
                self._magnifier.track(marker.scenePos(), event.position().toPoint())
            return
        super().mouseMoveEvent(event)
        if self._dragged_image_id:
            self._drag_moved = True
            self._refresh_point_overlay_positions()
            self._redraw_constraints()
            self._refresh_transform_handles()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._marquee_origin is not None:
            self._finish_marquee(bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._placement_click:
            self._placement_click = False
            self._pending_point_drag = None
            if self._dragged_point_key is not None:
                image_id, point_id = self._dragged_point_key
                self._dragged_point_key = None
                self._magnifier.hide()
                self.point_observation_interaction_finished.emit(image_id, point_id)
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._last_pan_position = None
            self.unsetCursor()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._active_transform_handle is not None:
            image_id = self._active_image_id
            self._active_transform_handle = None
            self.unsetCursor()
            if image_id is not None:
                self._emit_transform(image_id)
                self.transform_interaction_finished.emit(image_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self._dragged_image_id:
            image_id = self._dragged_image_id
            self._dragged_image_id = None
            self._drag_start = None
            moved = self._drag_moved
            for selected_id in sorted(self._selected_image_ids - {image_id}):
                if moved:
                    self._emit_transform(selected_id)
            self._emit_transform(image_id)
            self.transform_interaction_finished.emit(image_id)
        collapse_id, self._pending_collapse_id = self._pending_collapse_id, None
        if event.button() == Qt.MouseButton.LeftButton and collapse_id and not self._drag_moved:
            self.set_active_image(collapse_id)

    def _image_at(self, scene_position) -> str | None:
        if self._placing_point and self._active_image_id:
            active_item = self._layers.get(self._active_image_id)
            if active_item is not None and active_item.contains(active_item.mapFromScene(scene_position)):
                return self._active_image_id
        for item in self._scene.items(scene_position):
            current = item
            while current is not None:
                image_id = current.data(0)
                if image_id in self._layers:
                    return image_id
                current = current.parentItem()
        if self._active_image_id:
            active_item = self._layers.get(self._active_image_id)
            if active_item is not None and active_item.contains(active_item.mapFromScene(scene_position)):
                return self._active_image_id
        return None

    def _point_marker_at(self, viewport_position: QPoint) -> tuple[str, str] | None:
        item = self.itemAt(viewport_position)
        while item is not None:
            point_id = item.data(0)
            image_id = item.data(1)
            if image_id is not None and (image_id, point_id) in self._point_items:
                return image_id, point_id
            item = item.parentItem()
        return None

    def _move_point_marker(self, key: tuple[str, str], scene_position: QPointF) -> None:
        image_id, point_id = key
        image_item = self._layers.get(image_id)
        marker = self._point_items.get(key)
        if image_item is None or marker is None or key in self._locked_points:
            return
        local_position = image_item.mapFromScene(scene_position)
        self._points[key] = (local_position.x(), local_position.y())
        marker.setPos(image_item.mapToScene(local_position))
        self.point_observation_changed.emit(image_id, point_id, local_position.x(), local_position.y())
        self._redraw_constraints()

    def _clear_transform_handles(self) -> None:
        for handle in self._transform_handles:
            self._scene.removeItem(handle)
        self._transform_handles.clear()
        if self._transform_connector is not None:
            self._scene.removeItem(self._transform_connector)
            self._transform_connector = None

    def _refresh_transform_handles(self) -> None:
        self._clear_transform_handles()
        image_id = self._active_image_id
        item = self._layers.get(image_id) if image_id else None
        if item is None or self._read_only or not item.isVisible():
            return
        bounds = item.boundingRect()
        corners = (
            ("scale-nw", QPointF(bounds.left(), bounds.top())),
            ("scale-ne", QPointF(bounds.right(), bounds.top())),
            ("scale-se", QPointF(bounds.right(), bounds.bottom())),
            ("scale-sw", QPointF(bounds.left(), bounds.bottom())),
        )
        for kind, local_position in corners:
            self._add_transform_handle(kind, item.mapToScene(local_position), QColor("#65cdb1"))

        top_center = item.mapToScene(QPointF(bounds.center().x(), bounds.top()))
        rotate_position = item.mapToScene(QPointF(bounds.center().x(), bounds.top() - 32.0 / max(item.scale(), 0.05)))
        connector_pen = QPen(QColor("#e8efeb"), 1)
        connector_pen.setCosmetic(True)
        self._transform_connector = self._scene.addLine(
            top_center.x(), top_center.y(), rotate_position.x(), rotate_position.y(), connector_pen
        )
        self._transform_connector.setZValue(3999)
        self._add_transform_handle("rotate", rotate_position, QColor("#f4c95d"))

    def _add_transform_handle(self, kind: str, position: QPointF, color: QColor) -> None:
        handle = QGraphicsEllipseItem(-6, -6, 12, 12)
        handle.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
        handle.setPos(position)
        handle.setPen(QPen(QColor("#12201e"), 1.5))
        handle.setBrush(QBrush(color))
        handle.setZValue(4000)
        handle.setData(0, kind)
        handle.setData(1, self._active_image_id)
        handle.setToolTip("Drag to rotate" if kind == "rotate" else "Drag to scale image")
        handle.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(handle)
        self._transform_handles.append(handle)

    def _transform_handle_at(self, viewport_position: QPoint) -> str | None:
        nearest_kind = None
        nearest_distance = 12.0
        for handle in self._transform_handles:
            handle_position = self.mapFromScene(handle.scenePos())
            distance = (handle_position - viewport_position).manhattanLength()
            if distance <= nearest_distance:
                nearest_distance = distance
                nearest_kind = handle.data(0)
        return nearest_kind

    def _begin_transform_handle(self, kind: str, scene_position: QPointF) -> None:
        item = self._layers.get(self._active_image_id)
        if item is None:
            return
        self._active_transform_handle = kind
        self._transform_start_scene = scene_position
        self._transform_center_scene = item.mapToScene(item.boundingRect().center())
        self._transform_start_scale = item.scale()
        self._transform_start_rotation = item.rotation()
        self._transform_start_distance = max(
            1.0,
            hypot(
                scene_position.x() - self._transform_center_scene.x(),
                scene_position.y() - self._transform_center_scene.y(),
            ),
        )
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _update_transform_handle(self, scene_position: QPointF) -> None:
        image_id = self._active_image_id
        item = self._layers.get(image_id) if image_id else None
        if item is None or self._active_transform_handle is None:
            return
        if self._active_transform_handle == "rotate":
            start = self._transform_start_scene - self._transform_center_scene
            current = scene_position - self._transform_center_scene
            delta = start.x() * current.y() - start.y() * current.x()
            dot = start.x() * current.x() + start.y() * current.y()
            angle_delta = degrees(atan2(delta, dot))
            item.setRotation(self._transform_start_rotation + angle_delta)
        else:
            distance = max(
                1.0,
                hypot(
                    scene_position.x() - self._transform_center_scene.x(),
                    scene_position.y() - self._transform_center_scene.y(),
                ),
            )
            scale = self._transform_start_scale * distance / self._transform_start_distance
            item.setScale(min(20.0, max(0.05, scale)))

        current_center = item.mapToScene(item.boundingRect().center())
        item.setPos(item.pos() + self._transform_center_scene - current_center)
        self._refresh_point_overlay_positions()
        self._redraw_constraints()
        self._refresh_transform_handles()
        self._emit_transform(image_id)

    def _emit_transform(self, image_id: str) -> None:
        transform = self.image_transform(image_id)
        if transform is not None:
            self.transform_changed.emit(image_id, *transform)
            self._refresh_point_overlay_positions()
            self._redraw_constraints()
            self._refresh_transform_handles()

    def _refresh_point_overlay_positions(self) -> None:
        for key, (x, y) in self._points.items():
            image_id, _point_id = key
            image_item = self._layers.get(image_id)
            marker = self._point_items.get(key)
            if image_item is None or marker is None:
                continue
            scene_position = image_item.mapToScene(QPointF(x, y))
            marker.setPos(scene_position)

    def _scene_position_for_point(self, point_id: str):
        point_key = next((key for key in self._points if key[1] == point_id), None)
        if point_key is None:
            return None
        image_id, _point_id = point_key
        if not self._point_items[point_key].isVisible():
            return None
        x, y = self._points[point_key]
        item = self._layers.get(image_id)
        if item is None:
            return None
        from PySide6.QtCore import QPointF

        return item.mapToScene(QPointF(x, y))

    def set_selected_constraints(self, indices: set[int]) -> None:
        if indices != self._selected_constraint_indices:
            self._selected_constraint_indices = set(indices)
            self._redraw_constraints()

    def _constraint_at(self, viewport_position: QPoint) -> int | None:
        best_index = None
        best_distance = 7.0
        for index, (first_id, second_id) in enumerate(self._constraints):
            first = self._scene_position_for_point(first_id)
            second = self._scene_position_for_point(second_id)
            if first is None or second is None:
                continue
            a = self.mapFromScene(first)
            b = self.mapFromScene(second)
            dx, dy = b.x() - a.x(), b.y() - a.y()
            length_squared = dx * dx + dy * dy
            t = 0.0 if length_squared == 0 else max(0.0, min(1.0, (
                (viewport_position.x() - a.x()) * dx + (viewport_position.y() - a.y()) * dy
            ) / length_squared))
            distance = hypot(viewport_position.x() - (a.x() + t * dx), viewport_position.y() - (a.y() + t * dy))
            if distance <= best_distance:
                best_distance = distance
                best_index = index
        return best_index

    def _redraw_constraints(self) -> None:
        for item in self._constraint_items:
            self._scene.removeItem(item)
        self._constraint_items.clear()
        pen = QPen(QColor("#f4c95d"), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        selected_pen = QPen(QColor("#65cdb1"), 4)
        selected_pen.setCosmetic(True)
        for index, (first_id, second_id) in enumerate(self._constraints):
            first = self._scene_position_for_point(first_id)
            second = self._scene_position_for_point(second_id)
            if first is None or second is None:
                continue
            selected = index in self._selected_constraint_indices
            line = self._scene.addLine(first.x(), first.y(), second.x(), second.y(), selected_pen if selected else pen)
            line.setZValue(2500 if selected else 2000)
            self._constraint_items.append(line)
