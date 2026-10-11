from __future__ import annotations

from math import isfinite

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPixmap, QPolygonF, QTransform
from geostitch.image_item import SourceImageItem
from geostitch.warp import WarpTransform


class WarpedLayoutImageItem(SourceImageItem):
    """Render source pixels through a TPS mapping into layout scene coordinates."""

    GRID_SIZE = 32

    def __init__(self, pixmap: QPixmap, mapping: WarpTransform, source_size=None):
        super().__init__(pixmap, source_size)
        self._mapping = mapping
        self._scene_bounds = QRectF()
        self._shape_path = QPainterPath()
        self._triangles = ()
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable, False)
        self._rebuild_mesh()

    @property
    def mapping(self) -> WarpTransform:
        return self._mapping

    def set_mapping(self, mapping: WarpTransform) -> None:
        if mapping != self._mapping:
            self.prepareGeometryChange()
            self._mapping = mapping
            self._rebuild_mesh()
            self.update()

    def boundingRect(self) -> QRectF:
        return self._scene_bounds

    def shape(self) -> QPainterPath:
        return self._shape_path

    def opaqueArea(self) -> QPainterPath:
        return QPainterPath()

    def map_source_to_scene(self, point) -> tuple[float, float]:
        x = point.x() if hasattr(point, "x") else point[0]
        y = point.y() if hasattr(point, "y") else point[1]
        return self._mapping.map_point(float(x), float(y))

    def map_scene_to_source(self, point) -> tuple[float, float]:
        x = point.x() if hasattr(point, "x") else point[0]
        y = point.y() if hasattr(point, "y") else point[1]
        return self._mapping.inverse_point(float(x), float(y))

    @staticmethod
    def _triangle_transform(source, target) -> QTransform | None:
        (x0, y0), (x1, y1), (x2, y2) = source
        (u0, v0), (u1, v1), (u2, v2) = target
        dx1, dy1, dx2, dy2 = x1 - x0, y1 - y0, x2 - x0, y2 - y0
        determinant = dx1 * dy2 - dx2 * dy1
        if abs(determinant) <= 1e-15:
            return None
        du1, dv1, du2, dv2 = u1 - u0, v1 - v0, u2 - u0, v2 - v0
        m11 = (du1 * dy2 - du2 * dy1) / determinant
        m21 = (du2 * dx1 - du1 * dx2) / determinant
        m12 = (dv1 * dy2 - dv2 * dy1) / determinant
        m22 = (dv2 * dx1 - dv1 * dx2) / determinant
        m31 = u0 - m11 * x0 - m21 * y0
        m32 = v0 - m12 * x0 - m22 * y0
        values = (m11, m12, m21, m22, m31, m32)
        if not all(isfinite(value) for value in values):
            return None
        return QTransform(m11, m12, 0, m21, m22, 0, m31, m32, 1)

    def _rebuild_mesh(self) -> None:
        width, height = self._source_rect.width(), self._source_rect.height()
        columns = rows = self.GRID_SIZE
        mapped = {}
        for row in range(rows + 1):
            y = height * row / rows
            for column in range(columns + 1):
                x = width * column / columns
                mapped[(column, row)] = self._mapping.map_point(x, y)
        xs = [point[0] for point in mapped.values()]
        ys = [point[1] for point in mapped.values()]
        margin_x = max((max(xs) - min(xs)) * 1e-7, 1e-7)
        margin_y = max((max(ys) - min(ys)) * 1e-7, 1e-7)
        self._scene_bounds = QRectF(
            min(xs) - margin_x, min(ys) - margin_y,
            max(xs) - min(xs) + 2 * margin_x, max(ys) - min(ys) + 2 * margin_y,
        )
        boundary = [mapped[(column, 0)] for column in range(columns + 1)]
        boundary.extend(mapped[(columns, row)] for row in range(1, rows + 1))
        boundary.extend(mapped[(column, rows)] for column in range(columns - 1, -1, -1))
        boundary.extend(mapped[(0, row)] for row in range(rows - 1, 0, -1))
        self._shape_path = QPainterPath()
        self._shape_path.addPolygon(QPolygonF([QPointF(*point) for point in boundary]))
        self._shape_path.closeSubpath()
        triangles = []
        for row in range(rows):
            top = height * row / rows
            bottom = height * (row + 1) / rows
            for column in range(columns):
                left = width * column / columns
                right = width * (column + 1) / columns
                corners = (
                    ((column, row), (left, top)),
                    ((column + 1, row), (right, top)),
                    ((column + 1, row + 1), (right, bottom)),
                    ((column, row + 1), (left, bottom)),
                )
                for indices in ((0, 1, 2), (0, 2, 3)):
                    source = tuple(corners[index][1] for index in indices)
                    target = tuple(mapped[corners[index][0]] for index in indices)
                    transform = self._triangle_transform(source, target)
                    if transform is not None:
                        triangles.append((source, target, transform))
        self._triangles = tuple(triangles)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setClipRect(self._scene_bounds, Qt.ClipOperation.IntersectClip)
        source_rect = QRectF(0, 0, self._source_rect.width(), self._source_rect.height())
        pixmap_rect = QRectF(self.pixmap().rect())
        for source, target, transform in self._triangles:
            clip = QPainterPath()
            clip.addPolygon(QPolygonF([QPointF(*point) for point in target]))
            clip.closeSubpath()
            painter.save()
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            painter.setWorldTransform(transform * painter.worldTransform(), False)
            painter.drawPixmap(source_rect, self.pixmap(), pixmap_rect)
            painter.restore()
        painter.restore()
