from dataclasses import replace

import pytest

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox, QPlainTextEdit, QStyleFactory

from geostitch.app import MainWindow
from geostitch.alignment_feedback import AlignmentFeedback
from geostitch.icons import make_icon
from geostitch.models import SolverSettings
from geostitch.solver_dialog import SolverSettingsDialog, SolverToolbarControl
from test_solver_workflow import solver_window


def test_settings_dialog_apply_run_and_cancel():
    app = QApplication.instance() or QApplication([])
    dialog = SolverSettingsDialog("alignment", SolverSettings(0.25, 321, False))
    values = []
    dialog.settings_applied.connect(values.append)
    try:
        assert dialog.tolerance_spin.value() == 0.25
        assert dialog.iterations_spin.value() == 321
        assert not dialog.auto_apply_checkbox.isChecked()
        dialog.tolerance_spin.setValue(0.75)
        dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).click()
        assert values == [SolverSettings(0.75, 321, False)]
        assert not dialog.run_requested
        dialog.tolerance_spin.setValue(2)
        dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        assert len(values) == 1
    finally:
        dialog.close()
    dialog = SolverSettingsDialog("georeference", SolverSettings(1, 1000, True))
    dialog.settings_applied.connect(values.append)
    try:
        dialog.run_button.click()
        assert dialog.run_requested
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert values[-1] == SolverSettings(1, 1000, True)
    finally:
        dialog.close()


def test_solver_toolbar_group_and_one_click_action(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    keys = ("simulate_alignment", "georeference", "align_georeference")
    actions = window.main_toolbar.actions()
    indices = [actions.index(window.solver_toolbar_actions[key]) for key in keys]
    assert indices == list(range(indices[0], indices[0] + len(keys)))
    assert all(not window.shortcut_actions[key].icon().isNull() for key in keys)
    for stage in ("alignment", "georeference"):
        assert window.shortcut_actions[f"{stage}_settings"] not in actions
        control = window.solver_buttons[stage]
        assert control.settings_button.defaultAction() is window.shortcut_actions[f"{stage}_settings"]
        assert control.rect().contains(control.settings_button.geometry())
        assert control.settings_button.geometry().right() == control.rect().right()
        assert control.settings_button.geometry().bottom() == control.rect().bottom()
    calls = []
    monkeypatch.setattr("geostitch.app.simulate_alignment", lambda *args, **kwargs: calls.append(kwargs) or alignment)
    window.shortcut_actions["simulate_alignment"].trigger()
    assert len(calls) == 1
    assert window.project.images[1].has_alignment


def test_settings_apply_without_running(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window

    def edit(dialog):
        dialog.tolerance_spin.setValue(0.2)
        dialog.iterations_spin.setValue(222)
        dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).click()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SolverSettingsDialog, "exec", edit)
    calls = []
    monkeypatch.setattr(window, "run_alignment", lambda: calls.append(True))
    window.open_solver_settings("alignment")
    assert window.project.alignment_settings == SolverSettings(0.2, 222, True)
    assert calls == []


def test_settings_run_saves_before_executing(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window

    def edit(dialog):
        dialog.tolerance_spin.setValue(0.2)
        dialog.run_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SolverSettingsDialog, "exec", edit)
    calls = []
    monkeypatch.setattr(window, "run_alignment", lambda: calls.append(window.project.alignment_settings))
    window.open_solver_settings("alignment")
    assert calls == [SolverSettings(0.2, 100, True)]


@pytest.mark.parametrize("stage", ["alignment", "georeference"])
def test_last_reports_are_on_demand_and_read_only(solver_window, monkeypatch, stage):
    app, window, images, alignment, world, errors = solver_window
    window.project.alignment_feedback = AlignmentFeedback.capture(window.project, alignment, 0.5)
    window._georeference_result = world
    reports = []

    def inspect(dialog):
        editor = dialog.findChild(QPlainTextEdit)
        reports.append((editor.isReadOnly(), editor.toPlainText()))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", inspect)
    window.show_solver_report(stage)
    assert len(reports) == 1
    assert reports[0][0]
    assert "Residuals by" in reports[0][1]
    assert ("scene units" if stage == "alignment" else "CRS units") in reports[0][1]


@pytest.mark.parametrize("stage", ["alignment", "georeference"])
@pytest.mark.parametrize("status, expected", [
    (0, "Increase Maximum iterations"),
    (1, "More iterations alone are unlikely to help"),
    (2, "numerical stagnation"),
    (3, "numerical stagnation"),
    (4, "numerical stagnation"),
    (-1, "Check inputs and initial transforms"),
    (None, "Rerun the solver to capture its stop reason"),
])
def test_reports_distinguish_stop_reason_from_target_and_recommend_changes(
    solver_window, monkeypatch, stage, status, expected,
):
    app, window, images, alignment, world, errors = solver_window
    result = replace(
        alignment if stage == "alignment" else world,
        converged=False, iterations=7, optimizer_status=status,
        optimizer_message="Captured stop reason" if status is not None else "",
        max_iterations=321,
    )
    window.project.alignment_feedback = AlignmentFeedback.capture(window.project, result if stage == "alignment" else alignment, 0.5)
    window._georeference_result = result if stage == "georeference" else world
    window.project.alignment_settings = SolverSettings(0.25, 999, True)
    window.project.georeference_settings = SolverSettings(0.25, 999, True)
    reports = []

    def inspect(dialog):
        reports.append(dialog.findChild(QPlainTextEdit).toPlainText())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", inspect)
    window.show_solver_report(stage)

    assert len(reports) == 1
    text = reports[0]
    assert "Status: target not reached" in text
    assert "Function evaluations: 7 / 321" in text
    assert "Optimizer stop:" in text
    assert expected in text
    if status is not None:
        assert "Optimizer message: Captured stop reason" in text
        assert "relaxing it does not improve the fit" in text
        assert ("Link Review" if stage == "alignment" else "project CRS") in text
    else:
        assert "Optimizer stop: Unavailable for this result" in text
    if status != 0:
        assert "Increase Maximum iterations and rerun" not in text


@pytest.mark.parametrize("stage", ["alignment", "georeference"])
@pytest.mark.parametrize("status", [0, 1])
def test_target_miss_review_shows_stop_reason_and_guidance(solver_window, monkeypatch, stage, status):
    app, window, images, alignment, world, errors = solver_window
    result = replace(
        alignment if stage == "alignment" else world,
        converged=False, optimizer_status=status, optimizer_message="Captured reason", max_iterations=100,
    )
    messages = []

    def inspect(box):
        messages.append(box.informativeText())
        return 0

    monkeypatch.setattr(QMessageBox, "exec", inspect)
    assert window._review_solver_result(stage, result) == "discard"
    assert len(messages) == 1
    assert "Optimizer message: Captured reason" in messages[0]
    assert ("Increase Maximum iterations" if status == 0 else "stationary least-squares fit") in messages[0]


def test_target_reached_report_does_not_suggest_solver_changes(solver_window):
    app, window, images, alignment, world, errors = solver_window
    result = replace(alignment, optimizer_status=1, optimizer_message="gtol satisfied")
    text = "\n".join(window._solver_termination_lines("alignment", result))
    assert "Target reached; no solver-setting change is needed" in text
    assert "Increase Maximum iterations" not in text
    assert "relax" not in text


def test_saved_solver_settings_are_restored_on_reopen(solver_window, tmp_path):
    app, window, images, alignment, world, errors = solver_window
    window._apply_solver_settings("alignment", SolverSettings(0.25, 222, False))
    window._apply_solver_settings("georeference", SolverSettings(0.75, 444, True))
    window.project_path = tmp_path / "solver-settings.gsp"
    window.save_current_project()
    reopened = MainWindow(QSettings(str(tmp_path / "reopen.ini"), QSettings.Format.IniFormat))
    try:
        reopened._open_project_path(str(window.project_path))
        assert reopened.project.alignment_settings == SolverSettings(0.25, 222, False)
        assert reopened.project.georeference_settings == SolverSettings(0.75, 444, True)
    finally:
        reopened.close()


@pytest.mark.parametrize("stage, tolerance, iterations", [("alignment", 0.5, 100), ("georeference", 1.0, 1000)])
def test_toolbar_gears_open_correct_current_settings(solver_window, monkeypatch, stage, tolerance, iterations):
    app, window, images, alignment, world, errors = solver_window
    opened = []

    def inspect(dialog):
        opened.append((dialog.tolerance_spin.value(), dialog.iterations_spin.value()))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SolverSettingsDialog, "exec", inspect)
    window.shortcut_actions[f"{stage}_settings"].trigger()
    assert opened == [(tolerance, iterations)]


def test_combined_toolbar_action_runs_both_solvers(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    monkeypatch.setattr("geostitch.app.simulate_alignment", lambda *args, **kwargs: alignment)
    monkeypatch.setattr("geostitch.app.simulate_georeference", lambda *args, **kwargs: world)
    window.shortcut_actions["align_georeference"].trigger()
    assert window.project.images[1].has_alignment
    assert window.project.images[1].world_x is not None


@pytest.mark.parametrize("stage", ["alignment", "georeference"])
def test_corner_gear_click_opens_settings_without_running(solver_window, monkeypatch, stage):
    app, window, images, alignment, world, errors = solver_window
    opened = []

    def inspect(dialog):
        opened.append(dialog.windowTitle())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SolverSettingsDialog, "exec", inspect)

    def unexpected(*args, **kwargs):
        pytest.fail("Clicking a corner gear must not run a solver")

    monkeypatch.setattr("geostitch.app.simulate_alignment", unexpected)
    monkeypatch.setattr("geostitch.app.simulate_georeference", unexpected)
    window.show()
    app.processEvents()
    control = window.solver_buttons[stage]
    QTest.mouseClick(control.settings_button, Qt.MouseButton.LeftButton)
    assert len(opened) == 1
    assert "settings" in control.settings_button.toolTip().lower()
    window.shortcut_actions["simulate_alignment" if stage == "alignment" else "georeference"].setEnabled(False)
    assert control.settings_button.isEnabled()
    QTest.mouseClick(control.settings_button, Qt.MouseButton.LeftButton)
    assert len(opened) == 2


@pytest.mark.parametrize("stage", ["alignment", "georeference"])
def test_main_solver_icon_click_runs_without_opening_settings(solver_window, monkeypatch, stage):
    app, window, images, alignment, world, errors = solver_window
    calls = []

    def solve(*args, **kwargs):
        calls.append(stage)
        return alignment if stage == "alignment" else world

    monkeypatch.setattr(f"geostitch.app.simulate_{stage}", solve)

    def unexpected(dialog):
        pytest.fail("Clicking the solver icon must not open settings")

    monkeypatch.setattr(SolverSettingsDialog, "exec", unexpected)
    window.show()
    app.processEvents()
    control = window.solver_buttons[stage]
    QTest.mouseClick(control.run_button, Qt.MouseButton.LeftButton, pos=QPoint(14, 14))
    assert calls == [stage]


@pytest.mark.parametrize("style", QStyleFactory.keys())
def test_corner_controls_render_and_fit_across_qt_styles(solver_window, style):
    app, window, images, alignment, world, errors = solver_window
    control = SolverToolbarControl(window.shortcut_actions["simulate_alignment"], window.shortcut_actions["alignment_settings"])
    qt_style = QStyleFactory.create(style)
    qt_style.setParent(control)
    control.run_button.setStyle(qt_style)
    control.settings_button.setStyle(qt_style)
    control.show()
    app.processEvents()
    try:
        assert control.size().width() == 44
        assert control.size().height() == 40
        assert control.rect().contains(control.settings_button.geometry())
        control.settings_button.clearFocus()
        QTest.mouseMove(control.run_button, QPoint(1, 1))
        control.settings_button.hide()
        background = control.grab().toImage()
        control.settings_button.show()
        rendered_control = control.grab().toImage()
        for position in (QPoint(26, 22), QPoint(43, 22), QPoint(26, 39), QPoint(43, 39)):
            assert rendered_control.pixelColor(position) == background.pixelColor(position)
        for button in (control.run_button, control.settings_button):
            rendered = button.grab().toImage()
            colors = {rendered.pixelColor(pixel_x, pixel_y).rgba()
                      for pixel_x in range(rendered.width()) for pixel_y in range(rendered.height())}
            assert len(colors) > 3
    finally:
        control.close()


@pytest.mark.parametrize("key, glyph", [
    ("simulate_alignment", "run_alignment"),
    ("georeference", "run_georeference"),
    ("align_georeference", "run_combined_solve"),
])
def test_solver_run_icons_have_solid_play_triangles(solver_window, key, glyph):
    app, window, images, alignment, world, errors = solver_window
    assert window.TOOLBAR_INFO[key][0] == glyph
    image = make_icon(glyph).pixmap(48, 48).toImage()
    assert image.pixelColor(8, 17).alpha() == 255
    assert image.pixelColor(23, 18).alpha() == 255
    assert image.pixelColor(23, 6).alpha() == 0
    assert image.pixelColor(8, 36).alpha() == 0
    solid_play_pixels = sum(image.pixelColor(pixel_x, pixel_y).alpha() == 255
                           for pixel_x in range(4, 30) for pixel_y in range(3, 23))
    assert solid_play_pixels > 190
    small_image = make_icon(glyph).pixmap(24, 24).toImage()
    assert small_image.pixelColor(4, 8).alpha() == 255
    assert small_image.pixelColor(10, 9).alpha() == 255
    toolbar_image = window.shortcut_actions[key].icon().pixmap(48, 48).toImage()
    assert toolbar_image == image
    assert window.TOOLBAR_INFO["alignment_report"][0] == "alignment"
    assert window.TOOLBAR_INFO["georeference_report"][0] == "georeference"