import pytest


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