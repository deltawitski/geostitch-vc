from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPixmap, QPolygonF


def polygon_path(points: list[list[float]]) -> QPainterPath:
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
    path.closeSubpath()
    return path


def visible_path(width: float, height: float, points: list[list[float]], mode: str) -> QPainterPath:
    """Region of the image that stays visible: the polygon ("inside") or everything else ("outside")."""
    polygon = polygon_path(points)
    if mode != "outside":
        return polygon
    full = QPainterPath()
    full.addRect(QRectF(0.0, 0.0, width, height))
    return full.subtracted(polygon)


def hidden_path(width: float, height: float, points: list[list[float]], mode: str) -> QPainterPath:
    full = QPainterPath()
    full.addRect(QRectF(0.0, 0.0, width, height))
    return full.subtracted(visible_path(width, height, points, mode))


def masked_pixmap(pixmap: QPixmap, points: list[list[float]], mode: str) -> QPixmap:
    result = QPixmap(pixmap.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setClipPath(visible_path(pixmap.width(), pixmap.height(), points, mode))
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return result
