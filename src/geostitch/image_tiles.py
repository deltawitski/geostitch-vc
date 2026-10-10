from math import ceil, floor

from PySide6.QtCore import QObject, QRect, QRectF, QSize, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from geostitch.image_decode import read_region

TILE_EDGE = 512


def visible_tiles(size: QSize, visible: QRectF, edge: int = TILE_EDGE) -> list[QRect]:
    if edge <= 0:
        raise ValueError("Tile edge must be positive.")
    bounds = visible.intersected(QRectF(0, 0, size.width(), size.height()))
    if bounds.isEmpty():
        return []
    tiles = [QRect(column * edge, row * edge,
                   min(edge, size.width() - column * edge), min(edge, size.height() - row * edge))
             for row in range(floor(bounds.top() / edge), ceil(bounds.bottom() / edge))
             for column in range(floor(bounds.left() / edge), ceil(bounds.right() / edge))]
    center = bounds.center()
    return sorted(tiles, key=lambda tile: (
        (QRectF(tile).center() - center).manhattanLength(), tile.y(), tile.x(),
    ))


class _TileSignals(QObject):
    loaded = Signal(object, object, str)


class _TileJob(QRunnable):
    def __init__(self, key: tuple, path: str, region: QRect, signals: _TileSignals):
        super().__init__()
        self.key = key
        self.path = path
        self.region = QRect(region)
        self.signals = signals

    def run(self) -> None:
        try:
            image, error = read_region(self.path, self.region)
        except Exception as exception:
            image, error = QImage(), str(exception)
        try:
            self.signals.loaded.emit(self.key, image, error)
        except RuntimeError:
            pass


class TileLoader(QObject):
    loaded = Signal(object, object, str)

    def __init__(self, parent=None, pool: QThreadPool | None = None):
        super().__init__(parent)
        self._pending: set[tuple] = set()
        self._signals = _TileSignals(self)
        self._signals.loaded.connect(self._on_loaded)
        self._pool = pool if pool is not None else QThreadPool(self)
        if pool is None:
            self._pool.setMaxThreadCount(1)

    def request(self, key: tuple, path: str, region: QRect) -> None:
        if key not in self._pending:
            self._pending.add(key)
            self._pool.start(_TileJob(key, path, region, self._signals))

    def clear_queued(self) -> None:
        self._pool.clear()
        self._pending.clear()

    def _on_loaded(self, key: tuple, image: QImage, error: str) -> None:
        self._pending.discard(key)
        self.loaded.emit(key, image, error)