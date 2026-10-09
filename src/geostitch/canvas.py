from __future__ import annotations

from math import atan2, degrees, hypot

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)


class ImageCanvas(QGraphicsView):
    NAVIGATION_EXTENT = 10_000_000.0

    image_clicked = Signal(str, float, float)
    files_dropped = Signal(list)
    point_marker_clicked = Signal(str, str)
    point_marker_selected = Signal(str, str)
    point_marker_missed = Signal()
    point_observation_changed = Signal(str, str, float, float)
    point_observation_interaction_started = Signal(str, str)
    point_observation_interaction_finished = Signal(str, str)
    image_selected = Signal(str)
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
        self._active_image_id: str | None = None
        self._placing_point = False
        self._active_point_id: str | None = None
        self._linking_points = False
        self._constraint_source_id: str | None = None
        self._selected_point_id: str | None = None
        self._panning = False
        self._placement_click = False
        self._last_pan_position = None
        self._dragged_image_id: str | None = None
        self._drag_start = None
        self._read_only = False

    def set_layers(self, layers: list[dict], constraints: list[tuple[str, str]] | None = None) -> None:
        previous_active_image_id = self._active_image_id
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
            x = layer["position_x"] if layer["position_x"] is not None else layout_x
            pixmap_item.setPos(x, layer["position_y"])
            pixmap_item.setRotation(layer["rotation"])
            pixmap_item.setScale(layer["scale"])
            layout_x = x + layer["pixmap"].width() * layer["scale"] + 40.0
            self._scene.addItem(pixmap_item)
            self._layers[image_id] = pixmap_item
            for point_data in layer["points"]:
                if len(point_data) == 4:
                    point_id, pixel_x, pixel_y, locked = point_data
                else:
                    point_id, pixel_x, pixel_y = point_data
                    locked = False
                self._add_point_marker(pixmap_item, point_id, pixel_x, pixel_y, locked)

        for first_id, second_id in constraints or []:
            self._constraints.append((first_id, second_id))
        if previous_active_image_id in self._layers:
            self._active_image_id = previous_active_image_id
            for index, (image_id, item) in enumerate(self._layers.items()):
                selected = image_id == previous_active_image_id
                item.setSelected(selected)
                item.setZValue(1000 if selected else index)
        self._redraw_constraints()
        self.set_constraint_source(self._constraint_source_id)
        self._refresh_transform_handles()

    def set_read_only(self, read_only: bool) -> None:
        """Disable moving, scaling and rotating images (point markers stay editable)."""
        self._read_only = read_only
        for item in self._layers.values():
            item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable, not read_only)
        self._refresh_transform_handles()

    def _add_point_marker(self, parent: QGraphicsPixmapItem, point_id: str, x: float, y: float, locked: bool = False) -> None:
        marker = QGraphicsEllipseItem(-6, -6, 12, 12)
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

    def set_active_image(self, image_id: str | None) -> None:
        if image_id not in self._layers:
            self._active_image_id = None
            for item in self._layers.values():
                item.setSelected(False)
            self._refresh_transform_handles()
            return
        changed = image_id != self._active_image_id
        self._active_image_id = image_id
        for index, (candidate_id, item) in enumerate(self._layers.items()):
            selected = candidate_id == image_id
            item.setSelected(selected)
            item.setZValue(1000 if selected else index)
        self._refresh_transform_handles()
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
        self._selected_point_id = point_id
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
            is_selected = candidate_id == self._selected_point_id
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

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        self._refresh_point_overlay_positions()
        self._refresh_transform_handles()

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
            if self._active_point_id != "__continuous_add__":
                point = self._point_marker_at(event.position().toPoint())
                if point is not None:
                    image_id, point_id = point
                    self.set_active_image(image_id)
                    self.point_marker_selected.emit(image_id, point_id)
                    key = (image_id, point_id)
                    if key not in self._locked_points:
                        self._dragged_point_key = key
                        self.point_observation_interaction_started.emit(image_id, point_id)
                    self._placement_click = True
                    event.accept()
                    return
            scene_position = self.mapToScene(event.position().toPoint())
            image_id = self._image_at(scene_position)
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
            self.set_active_image(image_id)
            if self._read_only:
                return
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
        if self._active_transform_handle is not None:
            self._update_transform_handle(self.mapToScene(event.position().toPoint()))
            return
        if self._dragged_point_key is not None:
            self._move_point_marker(self._dragged_point_key, self.mapToScene(event.position().toPoint()))
            return
        super().mouseMoveEvent(event)
        if self._dragged_image_id:
            self._refresh_point_overlay_positions()
            self._redraw_constraints()
            self._refresh_transform_handles()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._placement_click:
            self._placement_click = False
            if self._dragged_point_key is not None:
                image_id, point_id = self._dragged_point_key
                self._dragged_point_key = None
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
            self._emit_transform(image_id)
            self.transform_interaction_finished.emit(image_id)

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
        if item is None or self._read_only:
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
        x, y = self._points[point_key]
        item = self._layers.get(image_id)
        if item is None:
            return None
        from PySide6.QtCore import QPointF

        return item.mapToScene(QPointF(x, y))

    def _redraw_constraints(self) -> None:
        for item in self._constraint_items:
            self._scene.removeItem(item)
        self._constraint_items.clear()
        pen = QPen(QColor("#f4c95d"), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        for first_id, second_id in self._constraints:
            first = self._scene_position_for_point(first_id)
            second = self._scene_position_for_point(second_id)
            if first is None or second is None:
                continue
            line = self._scene.addLine(first.x(), first.y(), second.x(), second.y(), pen)
            line.setZValue(2000)
            self._constraint_items.append(line)
