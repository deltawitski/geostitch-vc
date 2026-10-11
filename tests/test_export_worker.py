import os
import threading
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from geostitch.export import ExportResult
from geostitch.export_worker import ExportWorker
from geostitch.models import Project


def test_export_worker_uses_snapshot_off_ui_thread_and_can_cancel(tmp_path, monkeypatch):
    import geostitch.export as export_module

    app = QApplication.instance() or QApplication([])
    project = Project(name="Snapshot")
    started, release = threading.Event(), threading.Event()
    thread_ids = []
    observed = []
    grid_options_seen = []
    finished = []
    progress = []
    progress_threads = []

    def export(snapshot, directory, extension, report, *, cancelled, overwrite, **grid_options):
        thread_ids.append(threading.get_ident())
        grid_options_seen.append(grid_options)
        started.set()
        if not release.wait(3):
            raise OSError("Test worker timed out")
        observed.append(snapshot.name)
        report(0, 1, "Image")
        return ExportResult([], [], cancelled=cancelled())

    monkeypatch.setattr(export_module, "export_project_images", export)
    worker = ExportWorker()
    worker.finished.connect(lambda *args: finished.append(args))
    def record_progress(*args):
        progress.append(args)
        progress_threads.append(threading.get_ident())

    worker.progress.connect(record_progress)
    try:
        worker.start(
            project, tmp_path, ".png",
            grid_options={"output_crs": "EPSG:3857", "resolution": 10.0, "resampling": "nearest"},
        )
        assert started.wait(2)
        assert thread_ids[0] != threading.get_ident()
        project.name = "Changed after export began"
        worker.cancel()
        release.set()
        deadline = monotonic() + 3
        while not finished and monotonic() < deadline:
            app.processEvents()
        assert observed == ["Snapshot"]
        assert grid_options_seen == [{"output_crs": "EPSG:3857", "resolution": 10.0, "resampling": "nearest"}]
        assert progress == [(0, 1, "Image")]
        assert progress_threads == [threading.get_ident()]
        assert finished[0][0].cancelled
        assert finished[0][1] == ""
    finally:
        release.set()