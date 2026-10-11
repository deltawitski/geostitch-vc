import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox
from pyproj import Transformer

from geostitch.app import MainWindow
from geostitch.georeference import GeoreferenceResult
from geostitch.mapping import ImageMapping
from geostitch.models import GroundControlPoint, Project, SolverSettings


@pytest.fixture
def refit_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    window.project = Project()
    window.project.crs = "EPSG:4326"
    window.project.georeference_settings = SolverSettings(0.25, 321, False)
    image = window.project.add_image(tmp_path / "image.png")
    image.world_x, image.world_y = 10.0, 45.0
    image.world_rotation, image.world_scale = 12.0, 0.001
    point = GroundControlPoint.create("P1")
    point.set_observation(image.id, 4, 6)
    point.geographic_x, point.geographic_y = 10.01, 45.01
    window.project.add_control_point(point)
    pixmap = QPixmap(16, 12)
    pixmap.fill(QColor("white"))
    window._image_pixmaps = {image.id: pixmap}
    window._refresh_all()
    window._georeference_result = GeoreferenceResult(
        {image.id: (10.0, 45.0, 12.0, 0.001)}, {(image.id, point.id): 0.1},
        2, True, window.project.crs,
    )
    window._georeference_result_tolerance = 0.5
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    yield app, window, image.id, point.id, errors
    window.close()


def install_refit_solver(window, monkeypatch, converged=False):
    calls = []

    def solve(project, **kwargs):
        assert window._solver_running
        assert not window.shortcut_actions["georeference"].isEnabled()
        assert not window.run_georeference()
        assert window._solver_progress_dialog is None
        assert window._solver_progress_cancel_button is not None
        kwargs["progress"](0.5)
        assert "50%" in window.statusBar().currentMessage()
        transforms = {
            image_id: (pose[0] + kwargs["tolerance"], pose[1], pose[2], pose[3])
            for image_id, pose in kwargs["initial_transforms"].items()
        }
        result = GeoreferenceResult(
            transforms, {}, min(3, kwargs["max_iterations"]), converged, project.crs,
        )
        calls.append((project.crs, kwargs, result))
        return result

    monkeypatch.setattr("geostitch.app.simulate_georeference", solve)

    def unexpected_apply(*args):
        pytest.fail("CRS refits must not create a separate apply undo step.")

    monkeypatch.setattr(window, "_apply_georeference_result", unexpected_apply)
    return calls


def test_refit_uses_saved_settings_progress_and_updates_report_without_undo(refit_window, monkeypatch):
    app, window, image_id, point_id, errors = refit_window
    window.project.georeference_settings = SolverSettings(
        0.25, 321, False, transform_model="translation", loss="huber", parameter_scaling="jacobian",
    )
    calls = install_refit_solver(window, monkeypatch)
    before_count = window.undo_stack.count()
    window._resolve_world_transforms()
    assert len(calls) == 1
    crs, kwargs, result = calls[0]
    assert crs == window.project.crs
    assert kwargs["tolerance"] == 0.25
    assert kwargs["max_iterations"] == 321
    assert kwargs["settings"] == window.project.georeference_settings
    assert kwargs["initial_transforms"] == {image_id: (10.0, 45.0, 12.0, 0.001)}
    assert window._georeference_result is result
    assert window._georeference_result_tolerance == 0.25
    image = next(image for image in window.project.images if image.id == image_id)
    assert (image.world_x, image.world_y, image.world_rotation, image.world_scale) == result.transforms[image_id]
    assert window.undo_stack.count() == before_count
    assert not window._solver_running
    assert window._solver_progress_dialog is None
    assert window.shortcut_actions["georeference"].isEnabled()
    assert errors == []


@pytest.mark.parametrize("converged", [True, False])
def test_crs_refit_applies_best_result_with_one_undo(refit_window, monkeypatch, converged):
    app, window, image_id, point_id, errors = refit_window
    questions = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: questions.append(args[2]) or QMessageBox.StandardButton.Yes,
    )
    image = next(image for image in window.project.images if image.id == image_id)
    native_mapping = ImageMapping(image.world_mapping, "native_crs", "EPSG:4326")
    image.native_world_mapping = native_mapping
    calls = install_refit_solver(window, monkeypatch, converged)
    before = window.project.to_dict()
    before_count = window.undo_stack.count()
    transformer = Transformer.from_crs(window.project.crs, "EPSG:3857", always_xy=True)
    point = window.project.point_by_id(point_id)
    expected_coordinates = transformer.transform(point.geographic_x, point.geographic_y)
    expected_origin = transformer.transform(10.0, 45.0)
    checkpoint_pixel = (8.5, 9.5)
    native_checkpoint = native_mapping.mapping.map_point(*checkpoint_pixel)
    expected_checkpoint = Transformer.from_crs(
        "EPSG:4326", "EPSG:3857", always_xy=True,
    ).transform(*native_checkpoint)

    def assert_checkpoint_position():
        assert window.world_map._image_items[image_id].map_source_to_scene(checkpoint_pixel) == pytest.approx(
            (expected_checkpoint[0], -expected_checkpoint[1]), abs=1e-7,
        )

    window.crs_edit.setText("EPSG:3857")
    window._update_crs()
    assert len(calls) == 1
    crs, kwargs, result = calls[0]
    assert crs == result.crs == window.project.crs == "EPSG:3857"
    assert kwargs["initial_transforms"][image_id][:2] == pytest.approx(expected_origin)
    assert kwargs["tolerance"] == 0.25
    assert kwargs["max_iterations"] == 321
    assert not window.project.georeference_settings.auto_apply
    assert window._georeference_result is result
    assert window._georeference_result_tolerance == 0.25
    point = window.project.point_by_id(point_id)
    assert (point.geographic_x, point.geographic_y) == pytest.approx(expected_coordinates)
    image = next(image for image in window.project.images if image.id == image_id)
    assert (image.world_x, image.world_y, image.world_rotation, image.world_scale) == result.transforms[image_id]
    assert image.native_world_mapping == native_mapping
    native_point = native_mapping.mapping.map_point(4, 6)
    expected_map_point = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(*native_point)
    assert window.world_map._image_items[image_id].map_source_to_scene((4, 6)) == pytest.approx(
        (expected_map_point[0], -expected_map_point[1]), abs=1e-7,
    )
    assert_checkpoint_position()
    assert window.undo_stack.count() == before_count + 1
    assert not window._solver_running
    assert len(questions) == 1
    assert window._solver_progress_dialog is None
    assert window._solver_progress_cancel_button is None
    after = window.project.to_dict()
    window.undo_stack.undo()
    assert window.project.to_dict() == before
    assert_checkpoint_position()
    window.undo_stack.redo()
    assert window.project.to_dict() == after
    assert_checkpoint_position()
    assert errors == []


@pytest.mark.parametrize("failure", [None, ValueError, RuntimeError, ImportError])
def test_crs_refit_cancellation_or_failure_rolls_back(refit_window, monkeypatch, failure):
    app, window, image_id, point_id, errors = refit_window
    before = window.project.to_dict()
    previous_report = window._georeference_result
    previous_tolerance = window._georeference_result_tolerance
    before_count = window.undo_stack.count()
    before_index = window.undo_stack.index()
    refreshes = []
    refresh_all = window._refresh_all

    def refresh():
        refreshes.append(window.project.crs)
        refresh_all()

    monkeypatch.setattr(window, "_refresh_all", refresh)

    def fail(project, **kwargs):
        assert project.crs == "EPSG:3857"
        assert project.to_dict() != before
        assert window._solver_running
        if failure is not None:
            raise failure("Refit failed")
        assert window._solver_progress_cancel_button is not None
        window._solver_progress_cancel_button.click()
        kwargs["progress"](0.5)
        pytest.fail("Cancellation must propagate out of the progress callback.")

    monkeypatch.setattr("geostitch.app.simulate_georeference", fail)
    window.crs_edit.setText("EPSG:3857")
    window._update_crs()
    assert window.project.to_dict() == before
    assert window._georeference_result is previous_report
    assert window._georeference_result_tolerance == previous_tolerance
    assert window.crs_edit.text() == window.map_crs_combo.currentText() == before["crs"]
    assert refreshes == [before["crs"]]
    assert window.undo_stack.count() == before_count
    assert window.undo_stack.index() == before_index
    assert not window._solver_running
    assert window._solver_progress_dialog is None
    assert window._solver_progress_cancel_button is None
    assert window.shortcut_actions["georeference"].isEnabled()
    if failure is None:
        assert errors == []
        assert "cancelled" in window.statusBar().currentMessage().lower()
    else:
        assert len(errors) == 1
        assert "Refit failed" in errors[0]


@pytest.mark.parametrize("missing", ["coordinates", "observation", "points"])
def test_crs_without_usable_geographic_points_skips_solver_and_clears_report(refit_window, monkeypatch, missing):
    app, window, image_id, point_id, errors = refit_window
    point = window.project.point_by_id(point_id)
    if missing == "coordinates":
        point.geographic_y = None
    elif missing == "observation":
        point.observations.clear()
    else:
        window.project.control_points.clear()

    def unexpected(*args, **kwargs):
        pytest.fail("No usable geographic points must skip computation.")

    monkeypatch.setattr(window, "_compute_solver_result", unexpected)
    before_count = window.undo_stack.count()
    window.crs_edit.setText("EPSG:3857")
    window._update_crs()
    assert window.project.crs == "EPSG:3857"
    assert window._georeference_result is None
    assert window._georeference_result_tolerance is None
    assert window.undo_stack.count() == before_count + 1
    assert not window._solver_running
    assert errors == []


def test_busy_guard_prevents_nested_crs_edit_and_refit(refit_window, monkeypatch):
    app, window, image_id, point_id, errors = refit_window
    before = window.project.to_dict()
    previous_report = window._georeference_result
    before_count = window.undo_stack.count()

    def unexpected(*args, **kwargs):
        pytest.fail("Busy solver must prevent both nested CRS edits and refits.")

    monkeypatch.setattr(window, "_reproject_project", unexpected)
    monkeypatch.setattr(window, "_compute_solver_result", unexpected)
    window._solver_running = True
    try:
        window.crs_edit.setText("EPSG:3857")
        window._update_crs()
        window._resolve_world_transforms()
        assert window._solver_running
        assert window.project.to_dict() == before
        assert window._georeference_result is previous_report
        assert window.undo_stack.count() == before_count
        assert errors == []
    finally:
        window._solver_running = False