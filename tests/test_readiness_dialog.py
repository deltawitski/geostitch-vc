import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import pytest
from shiboken6 import delete

from geostitch.models import Project
from geostitch.readiness_dialog import ReadinessDialog
from geostitch.solver_readiness import ReadinessIssue, check_readiness


def test_readiness_dialog_filters_and_shows_recommendations():
    app = QApplication.instance() or QApplication([])
    project = Project()
    dialog = ReadinessDialog(project, check_readiness(project))
    try:
        assert dialog.table.rowCount() == 2
        assert "blocking" in dialog.summary.text()
        dialog.stage_filter.setCurrentIndex(1)
        assert dialog.table.rowCount() == 1
        assert "matching points" in dialog.details.toPlainText()
        assert not dialog.select_button.isEnabled()
    finally:
        dialog.close()
        delete(dialog)


def test_readiness_dialog_selects_affected_ids_and_closes():
    app = QApplication.instance() or QApplication([])
    project = Project()
    image = project.add_image("survey.png")
    issue = ReadinessIssue("alignment", "Warning", "example", "Weak group", "Add another link", (image.id,))
    dialog = ReadinessDialog(project, [issue])
    selected = []
    dialog.affected_selected.connect(selected.append)
    try:
        assert dialog.select_button.isEnabled()
        assert "survey.png" in dialog.details.toPlainText()
        dialog.select_button.click()
        assert selected == [issue]
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        dialog.close()
        delete(dialog)


@pytest.mark.parametrize("width", [600, 900])
def test_diagnostics_controls_fit_compact_and_wide_dialog(width):
    app = QApplication.instance() or QApplication([])
    project = Project()
    dialog = ReadinessDialog(project, check_readiness(project))
    try:
        dialog.resize(width, 520)
        dialog.show()
        app.processEvents()
        assert dialog.width() == width
        assert dialog.rect().contains(dialog.table.geometry())
        assert dialog.rect().contains(dialog.details.geometry())
        assert dialog.rect().contains(dialog.stage_filter.geometry())
        assert dialog.table.geometry().bottom() < dialog.details.geometry().top()
        assert dialog.details.isReadOnly()
    finally:
        dialog.close()
        delete(dialog)