from __future__ import annotations

from math import floor, hypot, isfinite, log2

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPixmap, QPolygonF, QTransform
from geostitch.image_item import SourceImageItem
from geostitch.mapping import CRSMapping, MappingChain


class WarpedMapImageItem(SourceImageItem):
    """Raster item adaptively tessellated from source pixels into map scene coordinates."""

    DISPLAY_ERROR_PIXELS = 0.25
    MAX_TRIANGLES = 50_000
    MAX_CELLS = 20_000
    MAX_DEPTH = 12

    def __init__(self, pixmap: QPixmap, mapping: MappingChain, source_size=None, *, world_offset: float = 0.0):
        if not isinstance(mapping, MappingChain):
            raise ValueError("Map image rendering requires a validated mapping chain.")
        if not isfinite(world_offset):
            raise ValueError("Map world offset must be finite.")
        super().__init__(pixmap, source_size)
        self._mapping = mapping
        self._world_offset = world_offset
        width, height = self.boundingRect().width(), self.boundingRect().height()
        bounds = self._sampled_bounds(width, height)
        self._scene_bounds = QRectF(bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1])
        self._fit_scene_bounds = getattr(self, "_fit_scene_bounds", self._scene_bounds)
        self._mesh_key = None
        self._mesh: list[tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float], ...]]] = []
        self._mesh_capped = False
        self._mesh_error_pixels = 0.0
        self._domain_clipped = False
        self.setTransformOriginPoint(0, 0)
        self.setTransform(QTransform())
        operation_reports = [
            step.operation_info.summary(step.source_crs, step.target_crs)
            for step in mapping.steps if isinstance(step, CRSMapping)
        ]
        if operation_reports:
            self.setToolTip("\n\n".join(operation_reports))

    def boundingRect(self) -> QRectF:
        return getattr(self, "_scene_bounds", super().boundingRect())

    @property
    def crosses_antimeridian(self) -> bool:
        return self._crosses_antimeridian

    @property
    def fit_scene_bounds(self) -> QRectF:
        return QRectF(self._fit_scene_bounds)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self._scene_bounds)
        return path

    def map_source_to_scene(self, point) -> tuple[float, float]:
        source_x = point.x() if hasattr(point, "x") else point[0]
        source_y = point.y() if hasattr(point, "y") else point[1]
        mapped = self._map(float(source_x), float(source_y))
        if mapped is None:
            raise ValueError("Source point is outside the valid map-projection domain.")
        return mapped

    def opaqueArea(self) -> QPainterPath:
        return QPainterPath()

    def _map(self, x: float, y: float) -> tuple[float, float] | None:
        try:
            mapped_x, mapped_y = self._mapping.map_point(x, y)
        except (ValueError, OverflowError):
            return None
        mapped_y = -mapped_y
        limit = 20037508.342789244
        if not isfinite(mapped_x) or not isfinite(mapped_y) or abs(mapped_x) > limit or abs(mapped_y) > limit:
            return None
        return mapped_x + self._world_offset, mapped_y

    def _sampled_bounds(self, width: float, height: float) -> tuple[float, float, float, float]:
        coordinates = []
        invalid_samples = 0
        for row in range(33):
            y = height * row / 32
            for column in range(33):
                point = self._map(width * column / 32, y)
                if point is not None:
                    coordinates.append(point)
                else:
                    invalid_samples += 1
        self._domain_clipped = invalid_samples > 0
        if not coordinates:
            raise ValueError("Image has no valid coordinates in the world-map projection.")
        xs = [point[0] for point in coordinates]
        ys = [point[1] for point in coordinates]
        world_origin = 20037508.342789244
        circumference = 2 * world_origin
        base_xs = [x - self._world_offset for x in xs]
        eastern = [x for x in base_xs if x > world_origin * 0.8]
        western = [x + circumference for x in base_xs if x < -world_origin * 0.8]
        self._crosses_antimeridian = bool(eastern and western)
        margin_x = max((max(xs) - min(xs)) * 1e-4, 1e-4)
        margin_y = max((max(ys) - min(ys)) * 1e-4, 1e-4)
        if self._crosses_antimeridian:
            fit_xs = [*eastern, *western]
            fit_margin_x = max((max(fit_xs) - min(fit_xs)) * 1e-4, 1e-4)
            self._fit_scene_bounds = QRectF(
                min(fit_xs) - fit_margin_x, min(ys) - margin_y,
                max(fit_xs) - min(fit_xs) + 2 * fit_margin_x,
                max(ys) - min(ys) + 2 * margin_y,
            )
        else:
            self._fit_scene_bounds = QRectF(
                min(xs) - margin_x, min(ys) - margin_y,
                max(xs) - min(xs) + 2 * margin_x, max(ys) - min(ys) + 2 * margin_y,
            )
        return min(xs) - margin_x, min(ys) - margin_y, max(xs) + margin_x, max(ys) + margin_y

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
        transform = QTransform(m11, m12, 0, m21, m22, 0, m31, m32, 1)
        if any(not isfinite(value) for value in (m11, m12, m21, m22, m31, m32)):
            return None
        return transform

    def _build_mesh(self, painter: QPainter, key: int) -> None:
        width, height = self._source_rect.width(), self._source_rect.height()
        viewport_transform = painter.deviceTransform()
        cache = {}

        def mapped(x, y):
            key = (x, y)
            if key not in cache:
                cache[key] = self._map(x, y)
            return cache[key]

        triangles = []
        capped = [False]
        cell_count = [0]
        maximum_leaf_error = [0.0]
        clipped = [False]

        def cell(left, top, right, bottom, depth):
            cell_count[0] += 1
            if len(triangles) >= self.MAX_TRIANGLES or cell_count[0] > self.MAX_CELLS:
                capped[0] = True
                return
            center_x, center_y = (left + right) / 2, (top + bottom) / 2
            source_points = (
                (left, top), (right, top), (right, bottom), (left, bottom),
                (center_x, top), (right, center_y), (center_x, bottom), (left, center_y), (center_x, center_y),
            )
            mapped_points = [mapped(*point) for point in source_points]
            corners = mapped_points[:4]
            valid = all(point is not None for point in mapped_points)
            horizon_like = False
            present = [point for point in corners if point is not None]
            if len(present) >= 2:
                span_x = max(point[0] for point in present) - min(point[0] for point in present)
                span_y = max(point[1] for point in present) - min(point[1] for point in present)
                horizon_like = span_x > 20037508.342789244 or span_y > 20037508.342789244
            error = float("inf")
            if valid and not horizon_like:
                pairs = ((4, 0, 1), (5, 1, 2), (6, 3, 2), (7, 0, 3), (8, 0, 2, 1, 3))
                error = 0.0
                for pair in pairs:
                    mid_index, first_index, second_index, *alternate = pair
                    first, second = corners[first_index], corners[second_index]
                    if alternate:
                        first = corners[alternate[0]]
                        second = corners[alternate[1]]
                    midpoint = mapped_points[mid_index]
                    linear = ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2)
                    actual_device = viewport_transform.map(QPointF(*midpoint))
                    expected_device = viewport_transform.map(QPointF(*linear))
                    error = max(error, hypot(actual_device.x() - expected_device.x(), actual_device.y() - expected_device.y()))
            if (not valid or horizon_like or error > self.DISPLAY_ERROR_PIXELS) and depth < self.MAX_DEPTH:
                cell(left, top, center_x, center_y, depth + 1)
                cell(center_x, top, right, center_y, depth + 1)
                cell(center_x, center_y, right, bottom, depth + 1)
                cell(left, center_y, center_x, bottom, depth + 1)
                return
            if not valid or horizon_like:
                clipped[0] = True
                return
            maximum_leaf_error[0] = max(maximum_leaf_error[0], error)
            for triangle in ((0, 1, 2), (0, 2, 3)):
                source = tuple(source_points[index] for index in triangle)
                target = tuple(corners[index] for index in triangle)
                if self._triangle_transform(source, target) is not None:
                    triangles.append((source, target))

        cell(0.0, 0.0, width, height, 0)
        self._mesh = triangles
        self._mesh_capped = capped[0]
        self._mesh_error_pixels = maximum_leaf_error[0]
        self._domain_clipped |= clipped[0]
        self._mesh_key = key

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setClipRect(self._scene_bounds, Qt.ClipOperation.IntersectClip)
        device_transform = painter.deviceTransform()
        scale = max(1e-15, (abs(device_transform.m11() * device_transform.m22())) ** 0.5)
        mesh_key = int(floor(log2(scale) * 4)) if scale > 0 else -1000
        if mesh_key != self._mesh_key:
            self._build_mesh(painter, mesh_key)
        source_rect = QRectF(0, 0, self._source_rect.width(), self._source_rect.height())
        pixmap_rect = QRectF(self.pixmap().rect())
        for source, target in self._mesh:
            transform = self._triangle_transform(source, target)
            if transform is None:
                continue
            clip = QPainterPath()
            clip.addPolygon(QPolygonF([QPointF(*point) for point in target]))
            clip.closeSubpath()
            painter.save()
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            painter.setWorldTransform(transform * painter.worldTransform(), False)
            painter.drawPixmap(source_rect, self.pixmap(), pixmap_rect)
            painter.restore()
        painter.restore()