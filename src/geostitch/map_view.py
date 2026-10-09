from __future__ import annotations

from math import floor, isfinite, log2

import numpy as np

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainterPath, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkDiskCache, QNetworkRequest
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
)
from PySide6.QtCore import QStandardPaths


class WorldMapView(QGraphicsView):
    """Interactive OSM slippy map with georeferenced image overlays."""

    WORLD_ORIGIN = 20037508.342789244
    MIN_ZOOM = 0
    MAX_ZOOM = 19
    TILE_SIZE = 256
    coordinate_picked = Signal(float, float)
    MAX_VERTICES_PER_REDRAW = 400_000
    TILE_FADE_MS = 220
    MAX_POINTS_PER_REDRAW = 50_000

    # Invert, then rotate hue 180 degrees (CSS `invert(1) hue-rotate(180deg)`) so the map goes dark but keeps its hues.
    _DARK_MATRIX = np.array([
        [-0.574, 1.430, 0.144],
        [0.426, 0.430, 0.144],
        [0.426, 1.430, -0.856],
    ])

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dark_mode = False
        self._zoom = 2
        self._tile_items: dict[tuple[int, int, int], QGraphicsPixmapItem] = {}
        self._needed_tiles: set[tuple[int, int, int]] = set()
        self._fades: dict = {}
        self._fade_clock = QElapsedTimer()
        self._fade_clock.start()
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(16)
        self._fade_timer.timeout.connect(self._step_fades)
        self._pending_tiles: set[tuple[int, int, int]] = set()
        self._image_items: dict[str, QGraphicsPixmapItem] = {}
        self._images: list[dict] = []
        self._vectors: list[dict] = []
        self._vector_items: list[tuple] = []
        self._vector_signature: list | None = None
        self._vector_timer = QTimer(self)
        self._vector_timer.setSingleShot(True)
        self._vector_timer.setInterval(80)
        self._vector_timer.timeout.connect(self._rebuild_vector_items)
        self._pick_mode = False
        self._selected_marker: QGraphicsEllipseItem | None = None
        self._point_overlay_items: list = []
        self._panning = False
        self._last_pan_position = None
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(QBrush(QColor("#d9e4df")))
        self.setRenderHint(self.renderHints() | self.renderHints().Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._manager = QNetworkAccessManager(self)
        self._disk_cache = QNetworkDiskCache(self)
        cache_directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        self._disk_cache.setCacheDirectory(f"{cache_directory}/osm-tiles")
        self._disk_cache.setMaximumCacheSize(128 * 1024 * 1024)
        self._manager.setCache(self._disk_cache)
        self._manager.finished.connect(self._on_tile_loaded)
        self._attribution = QLabel("\u00a9 OpenStreetMap contributors", self.viewport())
        self._attribution.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._style_attribution()
        self._attribution.adjustSize()
        self._attribution.raise_()
        self.setMouseTracking(True)
        self._scene.setSceneRect(-self.WORLD_ORIGIN, -self.WORLD_ORIGIN, self.WORLD_ORIGIN * 2, self.WORLD_ORIGIN * 2)
        self.fit_world()
        self._position_attribution()

    def set_control_points(self, points: list[dict]) -> None:
        """Draw control points: green = has map coordinates, yellow = placed via its image's transform; square = locked."""
        for item in self._point_overlay_items:
            self._scene.removeItem(item)
        self._point_overlay_items.clear()
        for point in points:
            if point["locked"]:
                marker = QGraphicsRectItem(-6, -6, 12, 12)
            else:
                marker = QGraphicsEllipseItem(-7, -7, 14, 14)
            marker.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            marker.setPos(point["x"], -point["y"])
            selected = point.get("selected", False)
            marker.setPen(QPen(QColor("#ffffff") if selected else QColor("#14201d"), 3 if selected else 2))
            marker.setBrush(QBrush(QColor("#4cd37b") if point["georeferenced"] else QColor("#ffcc4d")))
            marker.setZValue(8500)
            kind = "georeferenced" if point["georeferenced"] else "placed by image transform"
            marker.setToolTip(f"{point['name']} \u00b7 {kind} \u00b7 {'locked' if point['locked'] else 'unlocked'}")
            self._scene.addItem(marker)
            self._point_overlay_items.append(marker)

    def set_dark_mode(self, enabled: bool) -> None:
        if enabled == self._dark_mode:
            return
        self._dark_mode = enabled
        self.setBackgroundBrush(QBrush(QColor("#20272b" if enabled else "#d9e4df")))
        self._style_attribution()
        for item in self._tile_items.values():
            self._scene.removeItem(item)
        self._tile_items.clear()
        self._update_visible_tiles()

    @classmethod
    def _darken(cls, pixmap: QPixmap) -> QPixmap:
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
        width, height = image.width(), image.height()
        raw = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(height, image.bytesPerLine())
        rgb = 255.0 - raw[:, : width * 3].reshape(height, width, 3).astype(np.float32)
        result = np.ascontiguousarray(np.clip(rgb @ cls._DARK_MATRIX.T, 0, 255).astype(np.uint8))
        return QPixmap.fromImage(QImage(result.data, width, height, width * 3, QImage.Format.Format_RGB888).copy())

    def set_pick_mode(self, enabled: bool) -> None:
        self._pick_mode = enabled
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def set_world_images(self, images: list[dict], fit: bool = False) -> None:
        self._images = [image for image in images if image.get("pixmap") and not image["pixmap"].isNull()]
        self._refresh_image_items()
        if fit:
            self.fit_world()

    def set_world_vectors(self, vectors: list[dict]) -> None:
        """Show vector layers (dicts with `id`, `data` PreparedVectors, optional `order` shared with image order)."""
        signature = [(vector["id"], id(vector["data"]), vector.get("order")) for vector in vectors]
        if signature == self._vector_signature:
            return
        self._vector_signature = signature
        self._vectors = vectors
        self._rebuild_vector_items()

    def _schedule_vector_update(self) -> None:
        if self._vectors:
            self._vector_timer.start()

    def _restack_vector_items(self) -> None:
        fallback = len(self._images) + 1
        for item, index in self._vector_items:
            order = self._vectors[index].get("order")
            item.setZValue(100 + (fallback + index if order is None else order))

    def _rebuild_vector_items(self) -> None:
        """Draw only the parts inside the view, simplified to roughly one vertex per screen pixel."""
        self._vector_timer.stop()
        for item, _index in self._vector_items:
            self._scene.removeItem(item)
        self._vector_items.clear()
        if not self._vectors or self.viewport().width() <= 0:
            return
        view = self.mapToScene(self.viewport().rect()).boundingRect()
        scale = self.transform().m11()
        pixel = 1.0 / scale if scale > 0 else 1.0
        margin = max(view.width(), view.height()) * 0.1
        view = view.adjusted(-margin, -margin, margin, margin)
        stroke = QPen(QColor("#d98e04"), 2)
        stroke.setCosmetic(True)
        budget = [self.MAX_VERTICES_PER_REDRAW]
        for index, vector in enumerate(self._vectors):
            data = vector["data"]
            for parts, closed in ((data.polygons, True), (data.lines, False)):
                path = self._visible_path(parts, closed, view, pixel, budget)
                if path is None:
                    continue
                item = QGraphicsPathItem(path)
                item.setPen(stroke)
                if closed:
                    item.setBrush(QBrush(QColor(242, 184, 75, 60)))
                self._scene.addItem(item)
                self._vector_items.append((item, index))
            dots = self._visible_points(data.points, view, pixel)
            if dots is not None:
                item = QGraphicsPathItem(dots)
                dot_pen = QPen(QColor("#f2b84b"), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                dot_pen.setCosmetic(True)
                item.setPen(dot_pen)
                self._scene.addItem(item)
                self._vector_items.append((item, index))
        self._restack_vector_items()

    @staticmethod
    def _visible_path(parts, closed: bool, view: QRectF, pixel: float, budget: list[int]) -> QPainterPath | None:
        if not parts.count:
            return None
        bounds = parts.bounds
        size = np.maximum(bounds[:, 2] - bounds[:, 0], bounds[:, 3] - bounds[:, 1])
        inside = (
            (bounds[:, 2] >= view.left()) & (bounds[:, 0] <= view.right())
            & (bounds[:, 3] >= view.top()) & (bounds[:, 1] <= view.bottom())
            & (size >= pixel * 0.75)
        )
        path = QPainterPath()
        drawn = False
        for part in np.flatnonzero(inside):
            if budget[0] <= 0:
                break
            coords = parts.coords[parts.offsets[part]:parts.offsets[part + 1]]
            cells = np.floor(coords / pixel).astype(np.int64)
            keep = np.ones(len(coords), dtype=bool)
            keep[1:] = (cells[1:] != cells[:-1]).any(axis=1)
            keep[-1] = True
            coords = coords[keep]
            if len(coords) < (3 if closed else 2):
                continue
            budget[0] -= len(coords)
            path.addPolygon(QPolygonF([QPointF(x, y) for x, y in coords.tolist()]))
            if closed:
                path.closeSubpath()
            drawn = True
        return path if drawn else None

    @staticmethod
    def _visible_points(points, view: QRectF, pixel: float) -> QPainterPath | None:
        if not len(points):
            return None
        inside = points[
            (points[:, 0] >= view.left()) & (points[:, 0] <= view.right())
            & (points[:, 1] >= view.top()) & (points[:, 1] <= view.bottom())
        ]
        if not len(inside):
            return None
        cells = np.floor(inside / (pixel * 4.0)).astype(np.int64)
        _cells, first = np.unique(cells, axis=0, return_index=True)
        path = QPainterPath()
        for x, y in inside[first[:WorldMapView.MAX_POINTS_PER_REDRAW]].tolist():
            path.moveTo(x, y)
            path.lineTo(x + pixel * 0.01, y)
        return path

    def set_selected_coordinate(self, longitude: float | None, latitude: float | None) -> None:
        if self._selected_marker is not None:
            self._scene.removeItem(self._selected_marker)
            self._selected_marker = None
        if longitude is None or latitude is None:
            return
        x, y = self.lonlat_to_mercator(longitude, latitude)
        marker = QGraphicsEllipseItem(-8, -8, 16, 16)
        marker.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
        marker.setPen(QPen(QColor("#14201d"), 2))
        marker.setBrush(QBrush(QColor("#ffcc4d")))
        marker.setPos(x, -y)
        marker.setZValue(9000)
        self._scene.addItem(marker)
        self._selected_marker = marker

    @classmethod
    def lonlat_to_mercator(cls, longitude: float, latitude: float) -> tuple[float, float]:
        latitude = min(85.05112878, max(-85.05112878, latitude))
        x = cls.WORLD_ORIGIN * longitude / 180.0
        import math

        y = cls.WORLD_ORIGIN * math.log(math.tan((90.0 + latitude) * math.pi / 360.0)) / math.pi
        return x, y

    @classmethod
    def mercator_to_lonlat(cls, x: float, y: float) -> tuple[float, float]:
        import math

        longitude = x / cls.WORLD_ORIGIN * 180.0
        latitude = math.degrees(2.0 * math.atan(math.exp(y / cls.WORLD_ORIGIN * math.pi)) - math.pi / 2.0)
        return longitude, latitude

    def fit_world(self) -> None:
        world = QRectF(-self.WORLD_ORIGIN, -self.WORLD_ORIGIN, self.WORLD_ORIGIN * 2, self.WORLD_ORIGIN * 2)
        bounds = self._world_image_bounds()
        bounds = world if bounds is None else bounds.intersected(world)
        if bounds.isEmpty():
            bounds = world
        margin_x = max(bounds.width() * 0.05, 1.0)
        margin_y = max(bounds.height() * 0.05, 1.0)
        self.fitInView(bounds.adjusted(-margin_x, -margin_y, margin_x, margin_y), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = self._zoom_for_scale(self.transform().m11())
        self._update_visible_tiles()
        self._position_attribution()

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
        self._zoom = self._zoom_for_scale(scale)
        self._update_visible_tiles()
        self._refresh_image_items()
        self._position_attribution()

    def _world_image_bounds(self) -> QRectF | None:
        bounds = None
        for item in self._image_items.values():
            item_bounds = item.sceneBoundingRect()
            bounds = item_bounds if bounds is None else bounds.united(item_bounds)
        for vector in self._vectors:
            extent = vector["data"].bounds
            if extent is not None:
                item_bounds = QRectF(extent[0], extent[1], extent[2] - extent[0], extent[3] - extent[1])
                bounds = item_bounds if bounds is None else bounds.united(item_bounds)
        return bounds

    def _refresh_image_items(self) -> None:
        for item in self._image_items.values():
            self._scene.removeItem(item)
        self._image_items.clear()
        for index, image in enumerate(self._images):
            pixmap: QPixmap = image["pixmap"]
            corners = image.get("mercator_corners")
            if not corners or len(corners) != 3:
                continue
            if not all(isfinite(value) and abs(value) <= self.WORLD_ORIGIN * 4 for corner in corners for value in corner):
                continue
            top_left, top_right, bottom_left = corners
            item = QGraphicsPixmapItem(pixmap)
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            item.setTransformOriginPoint(0.0, 0.0)
            item.setTransform(QTransform(
                (top_right[0] - top_left[0]) / max(1, pixmap.width()),
                -(top_right[1] - top_left[1]) / max(1, pixmap.width()),
                (bottom_left[0] - top_left[0]) / max(1, pixmap.height()),
                -(bottom_left[1] - top_left[1]) / max(1, pixmap.height()),
                top_left[0],
                -top_left[1],
            ))
            item.setZValue(100 + image.get("order", index))
            self._scene.addItem(item)
            self._image_items[image["id"]] = item
        self._restack_vector_items()
        self._position_attribution()

    def _zoom_for_scale(self, scale: float) -> int:
        return min(self.MAX_ZOOM, max(self.MIN_ZOOM, round(log2(max(scale, 1e-12) * 156543.03392804097))))

    def _update_visible_tiles(self) -> None:
        self._schedule_vector_update()
        if self.viewport().width() <= 0 or self.viewport().height() <= 0:
            return
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        tile_count = 1 << self._zoom
        tile_span = 2.0 * self.WORLD_ORIGIN / tile_count
        min_x = max(0, int(floor((visible.left() + self.WORLD_ORIGIN) / tile_span)))
        max_x = min(tile_count - 1, int(floor((visible.right() + self.WORLD_ORIGIN) / tile_span)))
        min_y = max(0, int(floor((visible.top() + self.WORLD_ORIGIN) / tile_span)))
        max_y = min(tile_count - 1, int(floor((visible.bottom() + self.WORLD_ORIGIN) / tile_span)))
        needed = {
            (self._zoom, x, y)
            for x in range(min_x, max_x + 1)
            for y in range(min_y, max_y + 1)
        }
        for key in list(self._tile_items):
            if key not in needed and key[0] == self._zoom:
                item = self._tile_items.pop(key)
                self._scene.removeItem(item)
        self._needed_tiles = needed
        self._prune_stale_tiles()
        for zoom, x, y in needed:
            if (zoom, x, y) in self._tile_items or (zoom, x, y) in self._pending_tiles:
                continue
            url = QUrl(f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png")
            request = QNetworkRequest(url)
            request.setRawHeader(b"User-Agent", b"GeoStitchControl/0.1 (desktop map view)")
            reply = self._manager.get(request)
            reply.setProperty("tileKey", (zoom, x, y))
            self._pending_tiles.add((zoom, x, y))

    def _on_tile_loaded(self, reply) -> None:
        key_value = reply.property("tileKey")
        if key_value is None:
            reply.deleteLater()
            return
        key = tuple(key_value)
        self._pending_tiles.discard(key)
        data = reply.readAll()
        if reply.error() == reply.NetworkError.NoError:
            pixmap = QPixmap()
            if pixmap.loadFromData(bytes(data)) and key[0] == self._zoom:
                if self._dark_mode:
                    pixmap = self._darken(pixmap)
                zoom, tile_x, tile_y = key
                tile_count = 1 << zoom
                tile_span = 2.0 * self.WORLD_ORIGIN / tile_count
                item = self._scene.addPixmap(pixmap)
                item.setOffset(0.0, 0.0)
                item.setScale(tile_span / self.TILE_SIZE)
                item.setPos(-self.WORLD_ORIGIN + tile_x * tile_span, -self.WORLD_ORIGIN + tile_y * tile_span)
                item.setZValue(-1000 + zoom)
                self._tile_items[key] = item
                self._fade_in(item)
        reply.deleteLater()

    def _fade_in(self, item: QGraphicsPixmapItem) -> None:
        item.setOpacity(0.0)
        self._fades[item] = self._fade_clock.elapsed()
        if not self._fade_timer.isActive():
            self._fade_timer.start()

    def _step_fades(self) -> None:
        now = self._fade_clock.elapsed()
        for item, started in list(self._fades.items()):
            progress = min(1.0, (now - started) / self.TILE_FADE_MS)
            item.setOpacity(progress)
            if progress >= 1.0:
                del self._fades[item]
        if not self._fades:
            self._fade_timer.stop()
            self._prune_stale_tiles()

    def _prune_stale_tiles(self) -> None:
        """Tiles from other zoom levels stay as a backdrop until the current view's tiles have loaded and faded in."""
        if self._fades or not self._needed_tiles.issubset(self._tile_items):
            return
        for key in [key for key in self._tile_items if key[0] != self._zoom]:
            self._scene.removeItem(self._tile_items.pop(key))

    def _style_attribution(self) -> None:
        text, background = ("#d5dedb", "rgba(32, 39, 43, 170)") if self._dark_mode else ("#172522", "rgba(255, 255, 255, 170)")
        self._attribution.setStyleSheet(f"color: {text}; background: {background}; padding: 1px 4px; font-size: 11px;")
        self._attribution.adjustSize()

    def _position_attribution(self) -> None:
        viewport_rect = self.viewport().rect()
        self._attribution.move(
            viewport_rect.right() - self._attribution.width() + 1, viewport_rect.bottom() - self._attribution.height() + 1
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_visible_tiles()
        self._position_attribution()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self._update_visible_tiles()
        self._position_attribution()

    def wheelEvent(self, event) -> None:
        old_zoom = self._zoom
        scene_anchor = self.mapToScene(event.position().toPoint())
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)
        self._zoom = self._zoom_for_scale(self.transform().m11())
        if self._zoom != old_zoom:
            self._update_visible_tiles()
        delta = self.mapToScene(event.position().toPoint()) - scene_anchor
        self.translate(delta.x(), delta.y())
        self._position_attribution()
        self._schedule_vector_update()
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._pick_mode:
            scene_position = self.mapToScene(event.position().toPoint())
            longitude, latitude = self.mercator_to_lonlat(scene_position.x(), -scene_position.y())
            self.coordinate_picked.emit(longitude, latitude)
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._last_pan_position = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._last_pan_position is not None:
            position = event.position().toPoint()
            delta = position - self._last_pan_position
            self._last_pan_position = position
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._last_pan_position = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)
