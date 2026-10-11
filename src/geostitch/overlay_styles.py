from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen

POINT_MARKER_DIAMETER = 12


def point_marker_pen(selected: bool = False, locked: bool = False, source: bool = False) -> QPen:
    color = QColor("#ffffff" if selected else "#14201d")
    style = Qt.PenStyle.DotLine if source else Qt.PenStyle.DashLine if locked else Qt.PenStyle.SolidLine
    pen = QPen(color, 3 if selected or source else 2, style)
    pen.setCosmetic(True)
    return pen


def constraint_marker_pens(color: str, selected: bool = False) -> tuple[QPen | None, QPen | None, QPen]:
    if selected:
        outer = QPen(QColor("#172022"), 9)
        inner = QPen(QColor("#ffffff"), 6)
        center = QPen(QColor(color), 3, Qt.PenStyle.SolidLine)
        for pen in (outer, inner, center):
            pen.setCosmetic(True)
        return outer, inner, center
    center = QPen(QColor(color), 2, Qt.PenStyle.DashLine)
    center.setCosmetic(True)
    return None, None, center