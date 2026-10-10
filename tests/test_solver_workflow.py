from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from geostitch.app import MainWindow
from geostitch.georeference import GeoreferenceResult
from geostitch.models import GroundControlPoint, PointConstraint, Project, SolverSettings
from test_alignment_feedback import make_feedback_project


@pytest.fixture
def solver_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    project, images, points, links, result = make_feedback_project()
    window.project = project
    for point in points:
        point.geographic_x, point.geographic_y = 1000, 2000
    pixmap = QPixmap(200, 150)
    pixmap.fill(QColor("white"))
    window._image_pixmaps = {image.id: pixmap for image in images}
    window._refresh_all()
    alignment = replace(result, converged=True)
    world = GeoreferenceResult(
        {image.id: (1000 + index * 100, 2000, 0.0, 2.0) for index, image in enumerate(images[:2])},
        {(images[0].id, points[0].id): 0.1}, 3, True, project.crs,
    )
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    yield app, window, images, alignment, world, errors
    window.close()


def test_one_click_alignment_uses_saved_settings_and_applies(solver_window, monkeypatch):
    app, window, images, result, world, errors = solver_window
    window.project.alignment_settings = SolverSettings(0.25, 321, True)
    calls = []

    def solve(project, **kwargs):
        calls.append(kwargs)
        assert not window.shortcut_actions["simulate_alignment"].isEnabled()
        assert not window.run_alignment()
        kwargs["progress"](0.5)
        return result

    monkeypatch.setattr("geostitch.app.simulate_alignment", solve)
    before = window.undo_stack.count()
    assert window.run_alignment()
    assert len(calls) == 1
    assert calls[0]["tolerance"] == 0.25
    assert calls[0]["max_iterations"] == 321
    assert window.project.images[1].aligned_x == result.transforms[images[1].id][0]
    assert window.undo_stack.count() == before + 1
    assert window._alignment_feedback.tolerance == 0.25
    assert not window._solver_running
    assert window.shortcut_actions["simulate_alignment"].isEnabled()
    assert errors == []


def test_preview_mode_does_not_apply_transforms(solver_window, monkeypatch):
    app, window, images, result, world, errors = solver_window
    window.project.alignment_settings = SolverSettings(0.5, 100, False)
    monkeypatch.setattr("geostitch.app.simulate_alignment", lambda *args, **kwargs: result)
    before = window.undo_stack.count()
    assert not window.run_alignment()
    assert not window.project.images[0].has_alignment
    assert window._alignment_result == result
    assert window._alignment_feedback.is_current(window.project)
    assert window.undo_stack.count() == before


def test_combined_run_uses_alignment_for_world_solve_and_one_undo(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    calls = []
    window.project.georeference_settings = SolverSettings(0.75, 456, True)

    def align(project, **kwargs):
        calls.append("alignment")
        return alignment

    def georeference(project, **kwargs):
        calls.append("georeference")
        assert project.images[1].aligned_x == alignment.transforms[images[1].id][0]
        assert not window.project.images[1].has_alignment
        assert kwargs["tolerance"] == 0.75
        assert kwargs["max_iterations"] == 456
        return world

    monkeypatch.setattr("geostitch.app.simulate_alignment", align)
    monkeypatch.setattr("geostitch.app.simulate_georeference", georeference)
    before = window.undo_stack.count()
    assert window.run_combined_solvers()
    assert calls == ["alignment", "georeference"]
    assert window.project.images[1].has_alignment
    assert window.project.images[1].world_x is not None
    assert window.undo_stack.count() == before + 1
    window.undo_stack.undo()
    assert not window.project.images[1].has_alignment
    assert window.project.images[1].world_x is None
    window.undo_stack.redo()
    assert window.project.images[1].has_alignment
    assert window.project.images[1].world_x == world.transforms[images[1].id][0]


def test_combined_failure_leaves_both_transforms_unchanged(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    monkeypatch.setattr("geostitch.app.simulate_alignment", lambda *args, **kwargs: alignment)

    def failed(*args, **kwargs):
        raise ValueError("No usable geographic control points")

    monkeypatch.setattr("geostitch.app.simulate_georeference", failed)
    before = window.undo_stack.count()
    assert not window.run_combined_solvers()
    assert not window.project.images[1].has_alignment
    assert window.project.images[1].world_x is None
    assert window.undo_stack.count() == before
    assert "No usable" in errors[0]
    assert not window._solver_running


@pytest.mark.parametrize("decision", ["apply", "discard"])
def test_combined_stops_for_review_when_alignment_misses_target(solver_window, monkeypatch, decision):
    app, window, images, alignment, world, errors = solver_window
    monkeypatch.setattr("geostitch.app.simulate_alignment", lambda *args, **kwargs: replace(alignment, converged=False))
    monkeypatch.setattr(window, "_review_solver_result", lambda *args: decision)

    def unexpected(*args, **kwargs):
        pytest.fail("Georeferencing must not run after alignment misses its target.")

    monkeypatch.setattr("geostitch.app.simulate_georeference", unexpected)
    assert not window.run_combined_solvers()
    assert window.project.images[1].has_alignment is (decision == "apply")
    assert window.project.images[1].world_x is None


def test_cancelled_solver_leaves_transforms_unchanged(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window

    def cancel(project, **kwargs):
        window._solver_progress_dialog.cancel()
        kwargs["progress"](0.5)
        pytest.fail("Cancellation should interrupt the progress callback")

    monkeypatch.setattr("geostitch.app.simulate_alignment", cancel)
    assert not window.run_alignment()
    assert not window.project.images[1].has_alignment
    assert not window._solver_running
    assert errors == []


def test_one_click_georeference_applies_saved_settings(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    calls = []

    def solve(project, **kwargs):
        calls.append(kwargs)
        return world

    monkeypatch.setattr("geostitch.app.simulate_georeference", solve)
    assert window.run_georeference()
    assert calls[0]["tolerance"] == 1.0
    assert calls[0]["max_iterations"] == 1000
    assert window.project.images[1].world_x == world.transforms[images[1].id][0]


@pytest.mark.parametrize("preview_stage", ["alignment", "georeference"])
def test_combined_preview_leaves_both_transforms_unchanged(solver_window, monkeypatch, preview_stage):
    app, window, images, alignment, world, errors = solver_window
    setattr(window.project, f"{preview_stage}_settings", SolverSettings(0.5, 100, False))
    monkeypatch.setattr("geostitch.app.simulate_alignment", lambda *args, **kwargs: alignment)
    monkeypatch.setattr("geostitch.app.simulate_georeference", lambda *args, **kwargs: world)
    before = window.undo_stack.count()
    assert not window.run_combined_solvers()
    assert not window.project.images[1].has_alignment
    assert window.project.images[1].world_x is None
    assert window._alignment_result == alignment
    assert window._georeference_result == world
    assert window.undo_stack.count() == before


def test_combined_second_stage_cancellation_retains_both_transforms(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    monkeypatch.setattr("geostitch.app.simulate_alignment", lambda *args, **kwargs: alignment)

    def cancel(project, **kwargs):
        window._solver_progress_dialog.cancel()
        kwargs["progress"](0.5)

    monkeypatch.setattr("geostitch.app.simulate_georeference", cancel)
    assert not window.run_combined_solvers()
    assert not window.project.images[1].has_alignment
    assert window.project.images[1].world_x is None
    assert not window._solver_running
    assert errors == []


def test_adjust_settings_after_target_miss_runs_after_busy_state_clears(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    monkeypatch.setattr("geostitch.app.simulate_alignment", lambda *args, **kwargs: replace(alignment, converged=False))
    monkeypatch.setattr(window, "_review_solver_result", lambda *args: "settings")
    settings_calls = []
    monkeypatch.setattr(window, "open_solver_settings", lambda stage: settings_calls.append((stage, window._solver_running)))
    assert not window.run_alignment()
    app.processEvents()
    assert settings_calls == [("alignment", False)]


def test_preparation_failure_clears_busy_state(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window

    def invalid_snapshot(*args):
        raise ValueError("Invalid solver inputs")

    monkeypatch.setattr(Project, "from_dict", invalid_snapshot)
    assert not window.run_alignment()
    assert not window._solver_running
    assert "Invalid solver inputs" in errors[0]


def test_real_combined_solver_applies_alignment_then_georeference(solver_window, tmp_path):
    app, window, old_images, alignment, world, errors = solver_window
    window.project = Project()
    images = [window.project.add_image(tmp_path / name) for name in ("reference.png", "moving.png")]
    pixmap = QPixmap(200, 150)
    pixmap.fill(QColor("white"))
    window._image_pixmaps = {image.id: pixmap for image in images}
    for index, (pixel_x, pixel_y) in enumerate(((20, 20), (150, 30), (40, 120))):
        reference = GroundControlPoint.create(f"reference-{index}")
        reference.set_observation(images[0].id, pixel_x, pixel_y)
        reference.geographic_x = 1000 + 2 * pixel_x
        reference.geographic_y = 2000 - 2 * pixel_y
        moving = GroundControlPoint.create(f"moving-{index}")
        moving.set_observation(images[1].id, pixel_x - 10, pixel_y - 5)
        window.project.add_control_point(reference)
        window.project.add_control_point(moving)
        window.project.add_constraint(PointConstraint.create(reference.id, moving.id))
    window._refresh_all()
    before = window.undo_stack.count()
    assert window.run_combined_solvers()
    assert window.project.images[1].aligned_x == pytest.approx(10, abs=1e-5)
    assert window.project.images[1].aligned_y == pytest.approx(5, abs=1e-5)
    assert window.project.images[0].world_x == pytest.approx(1000, abs=1e-5)
    assert window.project.images[0].world_y == pytest.approx(2000, abs=1e-5)
    assert window.project.images[1].world_scale == pytest.approx(2, abs=1e-5)
    assert window.undo_stack.count() == before + 1
    assert errors == []


def test_alignment_initial_pose_preserves_displayed_rest_y(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    window.canvas._layers[images[2].id].setPos(400, 500)
    poses = []

    def solve(project, **kwargs):
        poses.append(kwargs["initial_transforms"][images[2].id])
        return alignment

    monkeypatch.setattr("geostitch.app.simulate_alignment", solve)
    assert window.run_alignment()
    assert poses[0][:2] == (400, 500)