from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage


class _Signals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)


class _ImageImportJob(QRunnable):
    def __init__(self, paths: list[str], decode: Callable[[str], tuple[QImage, str]], signals: _Signals):
        super().__init__()
        self._paths = paths
        self._decode = decode
        self._signals = signals

    def run(self) -> None:
        results = []
        total = len(self._paths)
        for index, path in enumerate(self._paths, start=1):
            try:
                image, error = self._decode(path)
            except Exception as exception:
                image, error = QImage(), str(exception)
            results.append((path, image, error))
            try:
                self._signals.progress.emit(index, total, Path(path).name)
            except RuntimeError:
                return
        try:
            self._signals.finished.emit(results)
        except RuntimeError:
            pass


class ImageImportWorker(QObject):
    """Decode a batch of image previews off the UI thread."""

    progress = Signal(int, int, str)
    finished = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._running = False
        self._signals = _Signals(self)
        self._signals.progress.connect(self.progress)
        self._signals.finished.connect(self._on_finished)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

    def start(self, paths: list[str], decode: Callable[[str], tuple[QImage, str]]) -> None:
        if self._running:
            raise RuntimeError("An image import is already running.")
        self._running = True
        self._pool.start(_ImageImportJob(paths, decode, self._signals))

    def _on_finished(self, results: list[tuple[str, QImage, str]]) -> None:
        self._running = False
        self.finished.emit(results)