from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem


class SourceImageItem(QGraphicsPixmapItem):
    def __init__(self, pixmap: QPixmap, source_size: QSize | None = None):
        size = source_size if source_size is not None and source_size.isValid() else pixmap.size()
        self._source_rect = QRectF(0, 0, size.width(), size.height())
        self._detail_regions: list[QRectF] = []
        super().__init__(pixmap)

    def boundingRect(self) -> QRectF:
        return self._source_rect

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self._source_rect)
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        if self._detail_regions:
            visible = QPainterPath()
            visible.addRect(self._source_rect)
            for region in self._detail_regions:
                hole = QPainterPath()
                hole.addRect(region)
                visible = visible.subtracted(hole)
            painter.setClipPath(visible, Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,
                              self.transformationMode() == Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap(self._source_rect, self.pixmap(), QRectF(self.pixmap().rect()))
        painter.restore()

    def set_detail_regions(self, regions: list[QRectF]) -> None:
        self._detail_regions = regions
        self.update()

    def set_source_image(self, pixmap: QPixmap, source_size: QSize | None = None) -> None:
        size = source_size if source_size is not None and source_size.isValid() else pixmap.size()
        bounds = QRectF(0, 0, size.width(), size.height())
        changed_bounds = bounds != self._source_rect
        if changed_bounds:
            self.prepareGeometryChange()
            self._source_rect = bounds
        if pixmap.cacheKey() != self.pixmap().cacheKey():
            self.setPixmap(pixmap)
        elif changed_bounds:
            self.update()