from __future__ import annotations

from math import floor, isfinite, log2

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QPen, QPixmap, QTransform
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkDiskCache, QNetworkRequest
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)
from PySide6.QtCore import QStandardPaths


class WorldMapView(QGraphicsView):
    """Interactive OSM slippy map with georeferenced image overlays."""

    WORLD_ORIGIN = 20037508.342789244
    MIN_ZOOM = 0
    MAX_ZOOM = 19
    TILE_SIZE = 256
    coordinate_picked = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom = 2
        self._tile_items: dict[tuple[int, int, int], QGraphicsPixmapItem] = {}
        self._pending_tiles: set[tuple[int, int, int]] = set()
        self._image_items: dict[str, QGraphicsPixmapItem] = {}
        self._images: list[dict] = []
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
        self._attribution = self._scene.addSimpleText("© OpenStreetMap contributors")
        self._attribution.setBrush(QBrush(QColor("#172522")))
        self._attribution.setFlag(QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self._attribution.setZValue(10000)
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

    def set_pick_mode(self, enabled: bool) -> None:
        self._pick_mode = enabled
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def set_world_images(self, images: list[dict], fit: bool = False) -> None:
        self._images = [image for image in images if image.get("pixmap") and not image["pixmap"].isNull()]
        self._refresh_image_items()
        if fit:
            self.fit_world()

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
        return bounds

    def _refresh_image_items(self) -> None:
        for item in self._image_items.values():
            self._scene.removeItem(item)
        self._image_items.clear()
        for image in self._images:
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
            item.setZValue(100)
            self._scene.addItem(item)
            self._image_items[image["id"]] = item
        self._position_attribution()

    def _zoom_for_scale(self, scale: float) -> int:
        return min(self.MAX_ZOOM, max(self.MIN_ZOOM, round(log2(max(scale, 1e-12) * 156543.03392804097))))

    def _update_visible_tiles(self) -> None:
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
            if key not in needed:
                item = self._tile_items.pop(key)
                self._scene.removeItem(item)
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
                zoom, tile_x, tile_y = key
                tile_count = 1 << zoom
                tile_span = 2.0 * self.WORLD_ORIGIN / tile_count
                item = self._scene.addPixmap(pixmap)
                item.setOffset(0.0, 0.0)
                item.setScale(tile_span / self.TILE_SIZE)
                item.setPos(-self.WORLD_ORIGIN + tile_x * tile_span, -self.WORLD_ORIGIN + tile_y * tile_span)
                item.setZValue(-1000)
                self._tile_items[key] = item
        reply.deleteLater()

    def _position_attribution(self) -> None:
        viewport_rect = self.viewport().rect()
        position = self.mapToScene(viewport_rect.bottomRight())
        self._attribution.setPos(position.x() - self._attribution.boundingRect().width() - 8, position.y() - 22)

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
        self._refresh_image_items()
        delta = self.mapToScene(event.position().toPoint()) - scene_anchor
        self.translate(delta.x(), delta.y())
        self._position_attribution()
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
