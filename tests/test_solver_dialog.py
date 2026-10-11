from dataclasses import replace

import pytest

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox, QPlainTextEdit, QStyleFactory

from geostitch.app import MainWindow
from geostitch.alignment_feedback import AlignmentFeedback
from geostitch.georeference import GeoreferenceResult, build_local_metric_frame
from geostitch.icons import make_icon
from geostitch.models import GroundControlPoint, PointConstraint, SolverSettings
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


@pytest.mark.parametrize("model", ["affine", "projective"])
def test_geographic_matrix_settings_label_local_metric_units(model):
    app = QApplication.instance() or QApplication([])
    dialog = SolverSettingsDialog(
        "georeference", SolverSettings(transform_model=model), project_crs="EPSG:4326",
    )
    try:
        assert dialog.tolerance_spin.suffix() == " local metres"
        assert dialog.loss_scale_spin.suffix() == " local metres"
        dialog.model_combo.setCurrentIndex(dialog.model_combo.findData("similarity"))
        assert dialog.tolerance_spin.suffix() == " degrees"
        dialog.model_combo.setCurrentIndex(dialog.model_combo.findData(model))
        assert dialog.tolerance_spin.suffix() == " local metres"
    finally:
        dialog.close()


def test_tps_alignment_and_georeferencing_expose_regularization_controls():
    app = QApplication.instance() or QApplication([])
    alignment = SolverSettingsDialog("alignment", SolverSettings())
    georeference = SolverSettingsDialog(
        "georeference", SolverSettings(transform_model="tps"), project_crs="EPSG:4326",
    )
    try:
        assert alignment.advanced_group.text() == "Advanced settings"
        assert not alignment.advanced_group.isChecked()
        assert alignment.advanced_controls.isHidden()
        assert alignment.layout().indexOf(alignment.advanced_group) < alignment.layout().indexOf(alignment.advanced_controls)
        alignment.model_combo.setCurrentIndex(alignment.model_combo.findData("affine"))
        assert "shear" in alignment.model_help.text()
        assert alignment.model_combo.toolTip() == alignment.model_help.text()
        assert alignment.tolerance_spin.toolTip()
        alignment.show()
        app.processEvents()
        collapsed_height = alignment.height()
        alignment.advanced_group.click()
        app.processEvents()
        assert not alignment.advanced_controls.isHidden()
        expanded_height = alignment.height()
        assert expanded_height > collapsed_height
        alignment.advanced_group.click()
        app.processEvents()
        assert alignment.advanced_controls.isHidden()
        assert alignment.height() < expanded_height

        georeference.advanced_group.setChecked(True)
        assert alignment.model_combo.findData("tps") >= 0
        assert georeference.model_combo.currentData() == "tps"
        assert georeference.tolerance_spin.suffix() == " local metres"
        assert "geographic CRS" in georeference.model_help.text()
        assert georeference.warp_smoothing_spin.isEnabled()
        assert georeference.warp_min_relative_area_spin.isEnabled()
        assert georeference.warp_max_relative_stretch_spin.isEnabled()
        assert georeference.warp_smoothing_spin.toolTip()
        assert not georeference.loss_combo.isEnabled()
        georeference.warp_smoothing_spin.setValue(0.25)
        assert georeference.apply_settings()
        assert georeference.warp_smoothing_spin.value() == 0.25
    finally:
        alignment.close()
        georeference.close()


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


def test_geographic_georeference_report_discloses_local_metric_frame(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    frame, _transformer = build_local_metric_frame(
        "EPSG:4326", [(179.999, 60.0), (-179.999, 60.01), (179.998, 60.02)],
    )
    frame = replace(
        frame, maximum_domain_radius_m=150_000,
        sampled_domain_distance_distortion=0.002, domain_sample_count=25,
    )
    result = GeoreferenceResult(
        {}, {}, 1, True, "EPSG:4326", metric_frames={images[0].id: frame},
    )
    window._georeference_result = result
    window._georeference_result_tolerance = 0.5
    reports = []

    def inspect(dialog):
        reports.append(dialog.findChild(QPlainTextEdit).toPlainText())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", inspect)
    window.show_solver_report("georeference")

    assert len(reports) == 1
    text = reports[0]
    assert "metres in local metric frame" in text
    assert "Local metric frames by connected group" in text
    assert "centered at" in text and "maximum control radius" in text
    assert "sampled pairwise distance distortion" in text
    assert "Mapped image-domain samples: 25" in text
    assert "maximum domain radius 150.00 km" in text
    assert "Warning: local-frame extent or sampled distortion is high" in text
    assert "CRS operation" in text and "Reported operation accuracy" in text


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
    assert text.index("Reason:") < text.index("Technical details")
    if status is not None:
        assert text.index("Technical details") < text.index("Optimizer message:")
    assert expected in text
    if status is not None:
        assert "Optimizer message: Captured stop reason" in text
        if status in (1, 2, 3, 4):
            assert ("Link Review" if stage == "alignment" else "project CRS") in text
    else:
        assert "Optimizer stop: Unavailable for this result" in text
    if status != 0:
        assert "Increase Maximum iterations and rerun" not in text


@pytest.mark.parametrize("stage", ["alignment", "georeference"])
@pytest.mark.parametrize("status, expected", [(0, "raise Maximum solver steps"), (1, "fit stopped improving")])
def test_target_miss_review_shows_plain_guidance_and_hides_technical_details(
    solver_window, monkeypatch, stage, status, expected,
):
    app, window, images, alignment, world, errors = solver_window
    result = replace(
        alignment if stage == "alignment" else world,
        converged=False, optimizer_status=status, optimizer_message="Captured reason", max_iterations=100,
    )
    messages = []
    details = []
    headings = []

    def inspect(box):
        headings.append((box.windowTitle(), box.text()))
        messages.append(box.informativeText())
        details.append(box.detailedText())
        return 0

    monkeypatch.setattr(QMessageBox, "exec", inspect)
    assert window._review_solver_result(stage, result) == "discard"
    assert len(messages) == 1
    assert headings[0][0] != headings[0][1]
    assert "target not reached" not in headings[0][0].lower()
    assert "target was not reached" not in headings[0][1].lower()
    assert "Optimizer message: Captured reason" in details[0]
    assert "Optimizer message:" not in messages[0]
    assert "Convergence thresholds:" not in messages[0]
    assert expected in messages[0]
    assert "Largest point mismatch" in messages[0]
    assert messages[0].count("Next step:") == 1
    assert len(messages[0]) < 800


def test_runtime_solver_failure_shows_action_before_technical_error(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    dialogs = []

    def inspect(box):
        dialogs.append((box.text(), box.informativeText(), box.detailedText()))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", inspect)
    window._show_solver_failure(
        "alignment",
        ValueError("Projective homography has rank deficient non-collinear control matrix."),
    )

    assert len(dialogs) == 1
    title, guidance, details = dialogs[0]
    assert "independent positions" in title
    assert "Add more matching points spread across the images" in guidance
    assert "rank deficient" not in title.lower() + guidance.lower()
    assert "rank deficient" in details


def test_dismissing_target_miss_does_not_repeat_popup_summary(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    result = replace(
        alignment, converged=False, optimizer_status=1,
        settings=SolverSettings(transform_model="similarity", tolerance=0.25),
    )
    monkeypatch.setattr(window, "_solver_readiness", lambda stages: [])
    monkeypatch.setattr(window, "_compute_solver_result", lambda *args, **kwargs: result)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)

    assert not window._run_solver_pipeline(("alignment",))

    message = window.statusBar().currentMessage()
    assert message == "Fit not applied. Existing transforms were kept unchanged."
    assert "target not reached" not in message.lower()
    assert "Maximum error" not in message


def test_target_reached_report_does_not_suggest_solver_changes(solver_window):
    app, window, images, alignment, world, errors = solver_window
    result = replace(alignment, optimizer_status=1, optimizer_message="gtol satisfied")
    text = "\n".join(window._solver_review_lines("alignment", result))
    assert "Target reached; no solver-setting change is needed" in text
    assert "Increase Maximum iterations" not in text
    assert "relax" not in text


@pytest.mark.parametrize("stage", ["alignment", "georeference"])
def test_concise_review_counts_errors_and_names_only_the_two_worst(solver_window, stage):
    app, window, images, alignment, world, errors = solver_window
    config = SolverSettings(tolerance=0.5)
    if stage == "alignment":
        first, second = window.project.constraints[:2]
        extra = GroundControlPoint.create("extra-review-point")
        extra.set_observation(images[2].id, 10, 20)
        window.project.add_control_point(extra)
        third = PointConstraint.create(first.first_point_id, extra.id)
        window.project.add_constraint(third)
        residuals = {first.id: 1.1, second.id: 9.0, third.id: 4.5}
        result = replace(alignment, constraint_residuals=residuals, converged=False, optimizer_status=1, settings=config)
        expected_names = [second.name, third.name]
        omitted_name = first.name
    else:
        points = window.project.control_points[:3]
        residuals = {(point.image_id, point.id): value for point, value in zip(points, (1.1, 9.0, 4.5))}
        result = replace(world, observation_residuals=residuals, converged=False, optimizer_status=1, settings=config)
        expected_names = [points[1].display_name, points[2].display_name]
        omitted_name = points[0].display_name
    lines = window._solver_review_lines(stage, result)
    assert "3 of 3" in "\n".join(lines)
    highest = next(line for line in lines if line.startswith("Highest errors:"))
    assert expected_names[0] in highest and expected_names[1] in highest
    assert highest.index(expected_names[0]) < highest.index(expected_names[1])
    assert omitted_name not in highest
    assert len(lines) <= 6
    assert "ftol" not in "\n".join(lines)


@pytest.mark.parametrize("stage", ["alignment", "georeference"])
def test_advanced_dialog_controls_are_loaded_and_applied(stage, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = SolverSettings(
        transform_model="rigid", optimizer_method="dogbox", loss="huber", loss_scale=2.5,
        ftol=1e-8, xtol=1e-9, gtol=1e-7, parameter_scaling="jacobian",
    )
    dialog = SolverSettingsDialog(stage, settings)
    applied = []
    dialog.settings_applied.connect(applied.append)
    try:
        assert dialog.model_combo.currentData() == "rigid"
        assert dialog.method_combo.currentData() == "dogbox"
        assert dialog.loss_scale_spin.isEnabled()
        assert not dialog.scale_min_spin.isEnabled()
        assert dialog.apply_settings()
        assert applied[-1] == settings
        dialog.advanced_group.setChecked(True)
        dialog.model_combo.setCurrentIndex(dialog.model_combo.findData("similarity"))
        dialog.default_bounds_checkbox.setChecked(False)
        dialog.scale_max_checkbox.setChecked(True)
        dialog.scale_min_spin.setValue(0.2)
        dialog.scale_max_spin.setValue(5)
        assert dialog.scale_min_spin.isEnabled() and dialog.scale_max_spin.isEnabled()
        dialog.run()
        assert dialog.run_requested
        assert applied[-1] == replace(settings, transform_model="similarity", scale_min=0.2, scale_max=5)
    finally:
        dialog.close()


def test_dialog_rejects_invalid_scale_bounds_without_running(monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = SolverSettingsDialog("alignment", SolverSettings())
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    applied = []
    dialog.settings_applied.connect(applied.append)
    try:
        dialog.default_bounds_checkbox.setChecked(False)
        dialog.scale_min_spin.setValue(5)
        dialog.scale_max_spin.setValue(1)
        dialog.run()
        assert not dialog.run_requested
        assert applied == []
        assert "Minimum scale" in warnings[0]
    finally:
        dialog.close()


def test_affine_controls_are_visible_only_for_affine_alignment(monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = SolverSettings(transform_model="similarity", affine_allow_reflection=False)
    alignment = SolverSettingsDialog("alignment", settings)
    georeference = SolverSettingsDialog("georeference", settings)
    try:
        alignment.advanced_group.setChecked(True)
        georeference.advanced_group.setChecked(True)
        assert not alignment.affine_min_stretch_spin.isEnabled()
        index = alignment.model_combo.findData("affine")
        alignment.model_combo.setCurrentIndex(index)
        assert alignment.affine_min_stretch_spin.isEnabled()
        assert alignment.affine_max_stretch_spin.isEnabled()
        assert alignment.affine_max_condition_spin.isEnabled()
        assert alignment.affine_allow_reflection_checkbox.isEnabled()
        alignment.affine_min_stretch_spin.setValue(0.1)
        alignment.affine_max_stretch_spin.setValue(10)
        alignment.affine_max_condition_spin.setValue(50)
        alignment.affine_allow_reflection_checkbox.setChecked(True)
        applied = []
        alignment.settings_applied.connect(applied.append)
        assert alignment.apply_settings()
        assert applied[-1].transform_model == "affine"
        assert applied[-1].affine_min_stretch == pytest.approx(0.1)
        assert applied[-1].affine_max_stretch == pytest.approx(10)
        assert applied[-1].affine_max_condition == pytest.approx(50)
        assert applied[-1].affine_allow_reflection
        geo_index = georeference.model_combo.findData("affine")
        assert georeference.model_combo.model().item(geo_index).isEnabled()
        georeference.model_combo.setCurrentIndex(geo_index)
        assert georeference.affine_min_stretch_spin.isEnabled()
        projective_index = alignment.model_combo.findData("projective")
        alignment.model_combo.setCurrentIndex(projective_index)
        assert alignment.projective_min_stretch_spin.isEnabled()
        assert alignment.projective_horizon_margin_spin.isEnabled()
        assert not alignment.affine_min_stretch_spin.isEnabled()
        alignment.projective_min_stretch_spin.setValue(0.1)
        alignment.projective_max_stretch_spin.setValue(8)
        alignment.projective_max_condition_spin.setValue(40)
        alignment.projective_horizon_margin_spin.setValue(0.05)
        alignment.projective_allow_reflection_checkbox.setChecked(True)
        assert alignment.apply_settings()
        assert applied[-1].transform_model == "projective"
        assert applied[-1].projective_min_stretch == pytest.approx(0.1)
        assert applied[-1].projective_max_stretch == pytest.approx(8)
        assert applied[-1].projective_max_condition == pytest.approx(40)
        assert applied[-1].projective_horizon_margin == pytest.approx(0.05)
        assert applied[-1].projective_allow_reflection
        geo_projective_index = georeference.model_combo.findData("projective")
        assert georeference.model_combo.model().item(geo_projective_index).isEnabled()
        georeference.model_combo.setCurrentIndex(geo_projective_index)
        assert georeference.projective_horizon_margin_spin.isEnabled()
    finally:
        alignment.close()
        georeference.close()


def test_report_shows_run_settings_not_current_settings(solver_window):
    app, window, images, alignment, world, errors = solver_window
    settings = SolverSettings(transform_model="translation", optimizer_method="dogbox", loss="huber", loss_scale=3)
    result = replace(alignment, converged=False, optimizer_status=1, settings=settings)
    text = "\n".join(window._solver_termination_lines("alignment", result))
    assert "Transform model: translation | Optimizer: dogbox" in text
    assert "Robust loss threshold: 3" in text
    assert "Fixed parameters: rotation and scale" in text
    assert "accuracy target still use raw distances" in text
    assert "Switch to rigid" in "\n".join(window._solver_review_lines("alignment", result))


def test_projective_report_shows_domain_limits_instead_of_fixed_parameters(solver_window):
    app, window, images, alignment, world, errors = solver_window
    settings = SolverSettings(
        transform_model="projective", projective_min_stretch=0.1, projective_max_stretch=8,
        projective_max_condition=40, projective_horizon_margin=0.05, projective_allow_reflection=True,
    )

    text = "\n".join(window._solver_termination_lines("alignment", replace(alignment, settings=settings)))

    assert "Projective stretches: 0.1 to 8" in text
    assert "maximum condition: 40" in text
    assert "horizon margin: 0.05" in text
    assert "reflections allowed" in text
    assert "Fixed parameters" not in text


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