from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from geostitch.vector import PreparedVectors, prepare_vectors, read_geojson


class _JobSignals(QObject):
    progress = Signal(object, int, int)
    loaded = Signal(object, object)
    failed = Signal(object, str)


class _Job(QRunnable):
    def __init__(self, key: tuple, path: str, project_crs: str, signals: _JobSignals):
        super().__init__()
        self._key, self._path, self._crs, self._signals = key, path, project_crs, signals

    def run(self) -> None:
        try:
            geometry, source_crs = read_geojson(self._path)
            prepared = prepare_vectors(
                geometry, source_crs, self._crs, lambda done, total: self._signals.progress.emit(self._key, done, total)
            )
            self._signals.loaded.emit(self._key, prepared)
        except RuntimeError:
            return  # the window closed while loading
        except Exception as error:
            try:
                self._signals.failed.emit(self._key, str(error))
            except RuntimeError:
                pass


class VectorLoader(QObject):
    """Reads and reprojects GeoJSON files on worker threads; results arrive as queued signals."""

    progress = Signal(object, int, int)
    loaded = Signal(object, object)
    failed = Signal(object, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._pending: set[tuple] = set()
        self._signals = _JobSignals(self)
        self._signals.progress.connect(self.progress)
        self._signals.loaded.connect(self._on_loaded)
        self._signals.failed.connect(self._on_failed)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)

    def is_pending(self, key: tuple) -> bool:
        return key in self._pending

    def request(self, key: tuple, path: str, project_crs: str) -> None:
        if key in self._pending:
            return
        self._pending.add(key)
        self._pool.start(_Job(key, path, project_crs, self._signals))

    def _on_loaded(self, key: tuple, prepared: PreparedVectors) -> None:
        self._pending.discard(key)
        self.loaded.emit(key, prepared)

    def _on_failed(self, key: tuple, message: str) -> None:
        self._pending.discard(key)
        self.failed.emit(key, message)
