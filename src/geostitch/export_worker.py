from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from geostitch.models import Project


class _ExportSignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object, str)


class _ExportJob(QRunnable):
    def __init__(self, project: Project, directory: Path, extension: str, overwrite: bool, grid_options: dict):
        super().__init__()
        self.project = project
        self.directory = directory
        self.extension = extension
        self.overwrite = overwrite
        self.grid_options = dict(grid_options)
        self.cancelled = Event()
        self.signals = _ExportSignals()

    def run(self) -> None:
        try:
            from geostitch.export import export_project_images

            result = export_project_images(
                self.project, self.directory, self.extension, self.signals.progress.emit,
                cancelled=self.cancelled.is_set, overwrite=self.overwrite, **self.grid_options,
            )
            error = ""
        except Exception as exception:
            result, error = None, str(exception)
        try:
            self.signals.finished.emit(result, error)
        except RuntimeError:
            pass


class ExportWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._job: _ExportJob | None = None

    def start(
        self, project: Project, directory: Path, extension: str, *, overwrite: bool = False,
        grid_options: dict | None = None,
    ) -> None:
        if self._job is not None:
            raise RuntimeError("An export is already running.")
        snapshot = Project.from_dict(project.to_dict())
        self._job = _ExportJob(snapshot, directory, extension, overwrite, grid_options or {})
        self._job.signals.progress.connect(self._progress)
        self._job.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(self._job)

    def cancel(self) -> None:
        if self._job is not None:
            self._job.cancelled.set()

    def _progress(self, done: int, total: int, name: str) -> None:
        self.progress.emit(done, total, name)

    def _finished(self, result, error: str) -> None:
        self._job = None
        self.finished.emit(result, error)