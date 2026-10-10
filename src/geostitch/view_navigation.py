from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QGraphicsView


def zoom_at_cursor(view: QGraphicsView, event: QWheelEvent, factor: float) -> QPoint:
    cursor = event.position().toPoint()
    scene_before = view.mapToScene(cursor)
    view.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
    view.scale(factor, factor)
    shift = view.mapFromScene(scene_before) - cursor
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + shift.x())
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + shift.y())
    view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
    return cursor