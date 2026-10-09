from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

_SIZE = 48
_COLOR = QColor("#e8efeb")
_ACCENT = QColor("#76cbb0")


def _arrow(painter: QPainter, tip: QPointF, dx: float, dy: float) -> None:
    """Draw an arrowhead whose tip is at `tip`, pointing along (dx, dy)."""
    painter.drawLine(tip, QPointF(tip.x() - 7 * dx + 6 * dy, tip.y() - 7 * dy - 6 * dx))
    painter.drawLine(tip, QPointF(tip.x() - 7 * dx - 6 * dy, tip.y() - 7 * dy + 6 * dx))


def _draw_new(p: QPainter) -> None:
    p.drawPolyline(QPolygonF([QPointF(12, 6), QPointF(28, 6), QPointF(37, 15), QPointF(37, 42), QPointF(12, 42), QPointF(12, 6)]))
    p.drawPolyline(QPolygonF([QPointF(28, 6), QPointF(28, 15), QPointF(37, 15)]))


def _draw_open(p: QPainter) -> None:
    p.drawPolyline(QPolygonF([QPointF(5, 38), QPointF(5, 12), QPointF(18, 12), QPointF(22, 17), QPointF(36, 17), QPointF(36, 24)]))
    p.drawPolyline(QPolygonF([QPointF(5, 38), QPointF(13, 24), QPointF(43, 24), QPointF(36, 38), QPointF(5, 38)]))


def _draw_save(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(7, 7, 34, 34), 3, 3)
    p.drawRect(QRectF(14, 7, 17, 11))
    p.drawRect(QRectF(13, 27, 22, 14))


def _tray(p: QPainter) -> None:
    p.drawPolyline(QPolygonF([QPointF(8, 30), QPointF(8, 40), QPointF(40, 40), QPointF(40, 30)]))


def _draw_import(p: QPainter) -> None:
    _tray(p)
    p.drawLine(QPointF(24, 6), QPointF(24, 30))
    _arrow(p, QPointF(24, 31), 0, 1)


def _draw_export(p: QPainter) -> None:
    _tray(p)
    p.drawLine(QPointF(24, 32), QPointF(24, 8))
    _arrow(p, QPointF(24, 7), 0, -1)


def _draw_arrange(p: QPainter) -> None:
    for x in (7, 26):
        for y in (7, 26):
            p.drawRect(QRectF(x, y, 15, 15))


def _draw_fit(p: QPainter) -> None:
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x = 6 if sx > 0 else 42
        y = 6 if sy > 0 else 42
        p.drawPolyline(QPolygonF([QPointF(x + 11 * sx, y), QPointF(x, y), QPointF(x, y + 11 * sy)]))


def _draw_add_points(p: QPainter) -> None:
    p.drawEllipse(QPointF(19, 29), 9, 9)
    p.setBrush(_COLOR)
    p.drawEllipse(QPointF(19, 29), 2.5, 2.5)
    p.setPen(QPen(_ACCENT, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(37, 6), QPointF(37, 20))
    p.drawLine(QPointF(30, 13), QPointF(44, 13))


def _draw_link_points(p: QPainter) -> None:
    p.drawLine(QPointF(15, 33), QPointF(33, 15))
    p.setBrush(_ACCENT)
    p.drawEllipse(QPointF(12, 36), 6, 6)
    p.drawEllipse(QPointF(36, 12), 6, 6)


def _draw_pick_gcp(p: QPainter) -> None:
    p.drawEllipse(QPointF(24, 24), 14, 14)
    p.drawLine(QPointF(24, 4), QPointF(24, 15))
    p.drawLine(QPointF(24, 33), QPointF(24, 44))
    p.drawLine(QPointF(4, 24), QPointF(15, 24))
    p.drawLine(QPointF(33, 24), QPointF(44, 24))
    p.setBrush(_ACCENT)
    p.drawEllipse(QPointF(24, 24), 3.5, 3.5)


def _draw_aligned(p: QPainter) -> None:
    p.setPen(QPen(_COLOR, 3, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap))
    p.drawRect(QRectF(5, 12, 16, 24))
    p.setPen(QPen(_ACCENT, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawRect(QRectF(27, 12, 16, 24))
    p.drawLine(QPointF(23, 24), QPointF(25, 24))


def _draw_delete(p: QPainter) -> None:
    p.drawLine(QPointF(8, 12), QPointF(40, 12))
    p.drawLine(QPointF(18, 12), QPointF(18, 7))
    p.drawLine(QPointF(18, 7), QPointF(30, 7))
    p.drawLine(QPointF(30, 7), QPointF(30, 12))
    p.drawPolyline(QPolygonF([QPointF(12, 12), QPointF(14, 42), QPointF(34, 42), QPointF(36, 12)]))
    p.drawLine(QPointF(20, 20), QPointF(20, 34))
    p.drawLine(QPointF(28, 20), QPointF(28, 34))


def _draw_select(p: QPainter) -> None:
    p.setPen(QPen(_COLOR, 3, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap))
    p.drawRect(QRectF(7, 9, 26, 22))
    p.setPen(QPen(_ACCENT, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(30, 28), QPointF(42, 42))
    p.drawLine(QPointF(30, 28), QPointF(30, 38))
    p.drawLine(QPointF(30, 28), QPointF(40, 28))


def _draw_lasso(p: QPainter) -> None:
    p.setPen(QPen(_COLOR, 3, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap))
    p.drawEllipse(QRectF(6, 7, 30, 22))
    p.setPen(QPen(_ACCENT, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(20, 29), QPointF(16, 37))
    p.drawLine(QPointF(16, 37), QPointF(22, 42))
    p.drawLine(QPointF(30, 28), QPointF(42, 42))
    p.drawLine(QPointF(30, 28), QPointF(30, 38))
    p.drawLine(QPointF(30, 28), QPointF(40, 28))


def _draw_lock(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(10, 21, 28, 21), 3, 3)
    p.drawArc(QRectF(15, 6, 18, 24), 0, 180 * 16)
    p.drawLine(QPointF(15, 18), QPointF(15, 21))
    p.drawLine(QPointF(33, 18), QPointF(33, 21))
    p.setPen(QPen(_ACCENT, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(24, 29), QPointF(24, 35))


def _draw_eye(p: QPainter) -> None:
    p.drawArc(QRectF(4, 14, 40, 28), 0, 180 * 16)
    p.drawArc(QRectF(4, 6, 40, 28), 180 * 16, 180 * 16)
    p.setBrush(_ACCENT)
    p.setPen(QPen(_ACCENT, 3))
    p.drawEllipse(QPointF(24, 24), 6, 6)


def _draw_eye_off(p: QPainter) -> None:
    p.setPen(QPen(QColor("#6f7c78"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(QRectF(4, 14, 40, 28), 0, 180 * 16)
    p.drawArc(QRectF(4, 6, 40, 28), 180 * 16, 180 * 16)
    p.drawLine(QPointF(8, 40), QPointF(40, 8))


def _draw_alignment(p: QPainter) -> None:
    dashed = QPen(_COLOR, 3, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
    p.setPen(dashed)
    p.drawRect(QRectF(5, 5, 20, 20))
    p.setPen(QPen(_ACCENT, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawRect(QRectF(21, 21, 22, 22))


def _draw_georeference(p: QPainter) -> None:
    p.drawEllipse(QPointF(24, 24), 18, 18)
    p.drawEllipse(QPointF(24, 24), 8, 18)
    p.drawLine(QPointF(6, 24), QPointF(42, 24))
    p.setPen(QPen(_ACCENT, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(10, 14), QPointF(38, 14))
    p.drawLine(QPointF(10, 34), QPointF(38, 34))


def _draw_undo(p: QPainter) -> None:
    p.drawArc(QRectF(10, 14, 30, 24), 0 * 16, 180 * 16)
    p.drawLine(QPointF(40, 26), QPointF(40, 30))
    p.drawLine(QPointF(10, 26), QPointF(10, 36))
    _arrow(p, QPointF(10, 37), 0, 1)


_GLYPHS = {
    "new": _draw_new,
    "open": _draw_open,
    "save": _draw_save,
    "import": _draw_import,
    "export": _draw_export,
    "arrange": _draw_arrange,
    "fit": _draw_fit,
    "add_points": _draw_add_points,
    "link_points": _draw_link_points,
    "pick_gcp": _draw_pick_gcp,
    "delete": _draw_delete,
    "lock": _draw_lock,
    "select": _draw_select,
    "lasso": _draw_lasso,
    "eye": _draw_eye,
    "eye_off": _draw_eye_off,
    "alignment": _draw_alignment,
    "aligned": _draw_aligned,
    "georeference": _draw_georeference,
    "undo": _draw_undo,
}


def app_icon() -> QIcon:
    """Application icon from assets/app_icon.(png|ico|jpg) (empty if no file exists)."""
    assets = Path(__file__).parent / "assets"
    path = next((item for item in sorted(assets.glob("app_icon.*")) if item.suffix.lower() in {".png", ".ico", ".jpg", ".jpeg"}), None)
    return QIcon(str(path)) if path else QIcon()


def make_icon(name: str) -> QIcon:
    """Return a monochrome vector-style icon; `redo` is the mirrored `undo`."""
    mirrored = name == "redo"
    glyph = _GLYPHS["undo" if mirrored else name]
    pixmap = QPixmap(_SIZE, _SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(_COLOR, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    glyph(painter)
    painter.end()
    if mirrored:
        pixmap = QPixmap.fromImage(pixmap.toImage().flipped(Qt.Orientation.Horizontal))
    return QIcon(pixmap)
