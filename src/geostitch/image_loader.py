from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage


class _Signals(QObject):
    loaded = Signal(object, object, str)


class _ImageJob(QRunnable):
    def __init__(self, key: tuple, path: str, decode: Callable[[str], tuple[QImage, str]], signals: _Signals):
        super().__init__()
        self._key = key
        self._path = path
        self._decode = decode
        self._signals = signals

    def run(self) -> None:
        try:
            image, error = self._decode(self._path)
        except Exception as exception:
            image, error = QImage(), str(exception)
        try:
            self._signals.loaded.emit(self._key, image, error)
        except RuntimeError:
            pass


class ImageLoader(QObject):
    """Decode images on workers; QPixmap conversion belongs to the receiving UI thread."""

    loaded = Signal(object, object, str)

    def __init__(self, decode: Callable[[str], tuple[QImage, str]], parent: QObject | None = None):
        super().__init__(parent)
        self._decode = decode
        self._pending: set[tuple] = set()
        self._signals = _Signals(self)
        self._signals.loaded.connect(self._on_loaded)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)

    def request(self, key: tuple, path: str) -> None:
        if key in self._pending:
            return
        self._pending.add(key)
        self._pool.start(_ImageJob(key, path, self._decode, self._signals))

    def clear_queued(self) -> None:
        self._pool.clear()
        self._pending.clear()

    def _on_loaded(self, key: tuple, image: QImage, error: str) -> None:
        self._pending.discard(key)
        self.loaded.emit(key, image, error)