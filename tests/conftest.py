import pytest


@pytest.fixture
def wait_for_image_import():
    from time import monotonic

    from PySide6.QtWidgets import QApplication

    def wait(window):
        app = QApplication.instance()
        deadline = monotonic() + 5
        while window._image_import_running and monotonic() < deadline:
            app.processEvents()
        assert not window._image_import_running

    return wait


@pytest.fixture(autouse=True)
def discard_unsaved_projects_during_test_cleanup(monkeypatch, request):
    from geostitch.app import MainWindow
    from PySide6.QtWidgets import QMessageBox

    if request.node.path.name != "test_project_safety.py":
        monkeypatch.setattr(MainWindow, "_confirm_project_replacement", lambda self: True)
        return
    question = QMessageBox.question

    def answer_question(parent, title, *args, **kwargs):
        if title == "Unsaved changes":
            return QMessageBox.StandardButton.Discard
        return question(parent, title, *args, **kwargs)

    monkeypatch.setattr(QMessageBox, "question", answer_question)