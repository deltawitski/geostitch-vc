import os
import threading
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog

import geostitch.repair_dialog as repair_module
from geostitch.models import ImageRecord
from geostitch.repair_dialog import ImageRepairDialog, _SearchJob


def test_search_preview_leaves_ambiguous_images_unselected(tmp_path):
    application = QApplication.instance() or QApplication([])
    images = [ImageRecord.from_path(tmp_path / name) for name in ("one.png", "two.png", "three.png")]
    dialog = ImageRepairDialog(images)
    dialog._job = _SearchJob(images, tmp_path)
    unique = tmp_path / "new" / "one.png"
    ambiguous = [tmp_path / "first" / "two.png", tmp_path / "second" / "two.png"]
    try:
        dialog._search_finished({images[0].id: [unique], images[1].id: ambiguous, images[2].id: []}, "")
        assert dialog.replacements() == {images[0].id: unique}
        assert dialog._choices[images[1].id].currentData() is None
        assert "ambiguous" in dialog.status.text()
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).isEnabled()
        dialog._choices[images[1].id].setCurrentIndex(2)
        assert dialog.replacements()[images[1].id] == ambiguous[1]
        assert images[0].path == str(tmp_path / "one.png")
    finally:
        dialog.reject()


def test_locate_is_only_a_preview_and_survives_subsequent_search(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    image = ImageRecord.from_path(tmp_path / "missing.png")
    replacement = tmp_path / "renamed.png"
    dialog = ImageRepairDialog([image])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(replacement), ""))
    try:
        dialog._locate(image.id)
        dialog._job = _SearchJob([image], tmp_path)
        dialog._search_finished({image.id: []}, "")
        assert dialog.replacements() == {image.id: replacement}
        assert image.path == str(tmp_path / "missing.png")
        dialog.reject()
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.close()


def test_folder_search_runs_off_ui_thread_and_cancel_stops_scan(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    image = ImageRecord.from_path(tmp_path / "missing.png")
    started, release = threading.Event(), threading.Event()
    threads = []

    def blocked_search(images, directory, *, cancelled):
        threads.append(threading.get_ident())
        started.set()
        if not release.wait(3):
            raise OSError("Test search timed out")
        assert cancelled()
        return {}

    monkeypatch.setattr(repair_module, "find_replacements", blocked_search)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path))
    dialog = ImageRepairDialog([image])
    try:
        dialog._search_folder()
        assert started.wait(2)
        assert threads[0] != threading.get_ident()
        application.processEvents()
        assert not dialog.search_button.isEnabled()
        job = dialog._job
        dialog.reject()
        assert job.cancelled.is_set()
    finally:
        release.set()
        dialog.reject()
        repair_module.QThreadPool.globalInstance().waitForDone(3000)
        application.processEvents()