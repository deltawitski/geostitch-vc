import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from geostitch.adjustment import simulate_alignment
from geostitch.app import MainWindow
from geostitch.models import GroundControlPoint, PointConstraint, Project, SolverSettings
from geostitch.project_io import load_project
from geostitch.solver_readiness import check_readiness
from geostitch.transforms import MatrixTransform
from geostitch.warp import WarpTransform


def affine_project(coefficients=(1.2, 0.3, 140.0, 0.2, 0.8, -35.0), *, collinear=False, locations=None):
    project = Project()
    reference, moving = [project.add_image(name) for name in ("reference.png", "moving.png")]
    locations = locations or [(0, 0), (100, 0), (0, 100), (120, 80), (50, 140)]
    if collinear:
        locations = [(index * 20, index * 30) for index in range(5)]
    a, b, c, d, e, f = coefficients
    for index, (x, y) in enumerate(locations):
        target_x, target_y = a * x + b * y + c, d * x + e * y + f
        first = GroundControlPoint.create(f"reference-{index}")
        first.set_observation(reference.id, target_x, target_y)
        second = GroundControlPoint.create(f"moving-{index}")
        second.set_observation(moving.id, x, y)
        project.add_control_point(first)
        project.add_control_point(second)
        project.add_constraint(PointConstraint.create(first.id, second.id))
    return project, reference, moving, locations


def test_affine_alignment_recovers_translation_independent_scales_and_shear():
    project, reference, moving, locations = affine_project()
    settings = SolverSettings(transform_model="affine", tolerance=1e-5, max_iterations=2000)

    result = simulate_alignment(project, settings=settings)

    assert result.converged
    assert result.anchor_image_ids == (reference.id,)
    assert result.mappings[reference.id] == MatrixTransform.from_similarity((0, 0, 0, 1))
    matrix = result.mappings[moving.id]
    assert matrix.model == "affine"
    assert matrix.to_affine() == pytest.approx((1.2, 0.3, 140, 0.2, 0.8, -35), abs=1e-7)
    assert result.maximum_residual < 1e-7
    assert moving.id not in result.transforms
    assert result.settings == settings


def test_tps_alignment_solves_joint_link_constraints():
    project, reference, moving, locations = affine_project()
    reference.source_dimensions = moving.source_dimensions = (160, 180)
    offsets = ((0, 0), (3, -2), (-4, 2), (5, 3), (-2, -4))
    reference_points = project.control_points[::2]
    for point, (offset_x, offset_y) in zip(reference_points, offsets):
        point.observations[0].x += offset_x
        point.observations[0].y += offset_y
    settings = SolverSettings(transform_model="tps", tolerance=1e-5, warp_smoothing=1e-8)

    result = simulate_alignment(project, settings=settings)

    assert result.converged
    assert result.anchor_image_ids == (reference.id,)
    assert isinstance(result.mappings[moving.id], WarpTransform)
    assert result.maximum_residual < 1e-5
    for (x, y), point in zip(locations, reference_points):
        expected = (point.observations[0].x, point.observations[0].y)
        assert result.mappings[moving.id].map_point(x, y) == pytest.approx(expected, abs=1e-4)
    fourth_x, fourth_y = locations[3]
    affine_only = (1.2 * fourth_x + 0.3 * fourth_y + 140, 0.2 * fourth_x + 0.8 * fourth_y - 35)
    assert result.mappings[moving.id].map_point(fourth_x, fourth_y) != pytest.approx(affine_only, abs=1e-3)


def test_tps_alignment_applies_to_canvas_and_survives_save_undo_redo(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings, QSize
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QApplication
    from geostitch.warp import WarpTransform

    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "tps-canvas.ini"), QSettings.Format.IniFormat))
    project, reference, moving, locations = affine_project()
    project.alignment_settings = SolverSettings(transform_model="tps", tolerance=1e-4, warp_smoothing=1e-8)
    solver_errors = []
    monkeypatch.setattr(window, "_show_error", solver_errors.append)
    window.project = project
    pixmap = QPixmap(1000, 800)
    pixmap.fill(QColor("#638a78"))
    window._image_pixmaps = {reference.id: pixmap, moving.id: pixmap}
    window._image_sizes = {reference.id: QSize(1000, 800), moving.id: QSize(1000, 800)}
    window._refresh_all()
    try:
        assert window.run_alignment(), solver_errors
        result = window._alignment_result
        assert result is not None and result.converged
        assert isinstance(window.project.images[1].aligned_mapping, WarpTransform)
        assert isinstance(window.canvas.image_mapping(moving.id), WarpTransform)
        for x, y in locations:
            expected = (1.2 * x + 0.3 * y + 140, 0.2 * x + 0.8 * y - 35)
            assert window.canvas.image_mapping(moving.id).map_point(x, y) == pytest.approx(expected, abs=1e-4)

        window.project_path = tmp_path / "tps-canvas.gsp"
        assert window.save_current_project()
        reopened = load_project(window.project_path)
        assert isinstance(reopened.images[1].aligned_mapping, WarpTransform)
        assert reopened.alignment_feedback.matches_layout(reopened)
        window.undo_stack.undo()
        assert window.project.images[1].aligned_warp is None
        window.undo_stack.redo()
        assert isinstance(window.project.images[1].aligned_mapping, WarpTransform)
    finally:
        window.close()


def test_affine_alignment_with_source_mappings_recovers_shared_scene_geometry():
    project, reference, moving, locations = affine_project()
    project.images[1].set_layout_mapping(MatrixTransform.from_affine((1.1, -0.2, 30, 0.15, 0.9, 45)))
    settings = SolverSettings(transform_model="affine", tolerance=1e-5)
    initial = {image.id: image.rest_mapping for image in project.images}

    result = simulate_alignment(project, settings=settings, initial_mappings=initial)

    assert result.converged
    for x, y in locations:
        expected = (1.2 * x + 0.3 * y + 140, 0.2 * x + 0.8 * y - 35)
        assert result.mappings[moving.id].map_point(x, y) == pytest.approx(expected, abs=1e-6)
    assert result.maximum_residual < 1e-6


def test_affine_alignment_rejects_collinear_pair_geometry_in_solver_and_readiness():
    project, _reference, _moving, _locations = affine_project(collinear=True)
    project.alignment_settings = SolverSettings(transform_model="affine")

    assert any(issue.code == "affine_geometry" and issue.severity == "Error" for issue in check_readiness(project, ("alignment",)))
    with pytest.raises(ValueError, match="non-collinear"):
        simulate_alignment(project, settings=project.alignment_settings)


def test_affine_alignment_warns_for_full_rank_nearly_collinear_controls():
    locations = [(0, 0), (100, 0.01), (200, -0.01), (300, 0.02), (400, -0.02)]
    project, first, second, _locations = affine_project(locations=locations)
    project.alignment_settings = SolverSettings(transform_model="affine")

    issues = check_readiness(project, ("alignment",))

    warning = next(issue for issue in issues if issue.code == "affine_conditioning")
    assert warning.severity == "Warning"
    assert set(warning.image_ids) == {first.id, second.id}
    assert not any(issue.severity == "Error" for issue in issues)


@pytest.mark.parametrize("settings, pattern", [
    (SolverSettings(transform_model="affine", affine_max_stretch=1.1), "principal-stretch"),
    (SolverSettings(transform_model="affine", affine_max_condition=1.1), "condition limit"),
])
def test_affine_alignment_rejects_configured_stretch_or_condition_violations(settings, pattern):
    project, _reference, _moving, _locations = affine_project()
    with pytest.raises(ValueError, match=pattern):
        simulate_alignment(project, settings=settings)


def test_affine_alignment_rejects_reflections_unless_explicitly_allowed():
    project, _reference, _moving, _locations = affine_project((1, 0, 140, 0, -1, 100))
    with pytest.raises(ValueError, match="reflected"):
        simulate_alignment(project, settings=SolverSettings(transform_model="affine"))
    allowed = simulate_alignment(project, settings=SolverSettings(transform_model="affine", affine_allow_reflection=True))
    assert allowed.converged
    assert allowed.mappings[next(image.id for image in project.images if image.name == "moving.png")].determinant < 0


def test_affine_alignment_applies_to_canvas_and_survives_save_undo_redo(tmp_path):
    from PySide6.QtCore import QSettings, QSize
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "affine-canvas.ini"), QSettings.Format.IniFormat))
    project, reference, moving, locations = affine_project()
    project.alignment_settings = SolverSettings(transform_model="affine", tolerance=1e-4, max_iterations=1000)
    window.project = project
    pixmap = QPixmap(1000, 800)
    pixmap.fill(QColor("#638a78"))
    window._image_pixmaps = {reference.id: pixmap, moving.id: pixmap}
    window._image_sizes = {reference.id: QSize(1000, 800), moving.id: QSize(1000, 800)}
    window._refresh_all()
    try:
        before_count = window.undo_stack.count()
        assert window.run_alignment()
        result = window._alignment_result
        assert result is not None and result.converged
        assert window.project.canvas_state == "aligned"
        assert window.project.images[1].aligned_intrinsic is not None
        assert all(image.source_dimensions == (1000, 800) for image in window.project.images)
        assert all(result.mapping_records[image.id].source_size == (1000, 800) for image in project.images if image.id in result.mappings)
        assert window.canvas.image_mapping(moving.id).coefficients == pytest.approx(result.mappings[moving.id].coefficients)
        assert window.undo_stack.count() == before_count + 1
        for x, y in locations:
            expected = (1.2 * x + 0.3 * y + 140, 0.2 * x + 0.8 * y - 35)
            assert window.canvas.image_mapping(moving.id).map_point(x, y) == pytest.approx(expected, abs=1e-5)

        window.project_path = tmp_path / "affine-canvas.gsp"
        assert window.save_current_project()
        reopened = load_project(window.project_path)
        assert reopened.images[1].aligned_mapping.coefficients == pytest.approx(result.mappings[moving.id].coefficients)
        assert reopened.alignment_feedback.result.mappings[moving.id].coefficients == pytest.approx(result.mappings[moving.id].coefficients)
        window.undo_stack.undo()
        assert window.project.images[1].aligned_intrinsic is None
        window.undo_stack.redo()
        assert window.project.images[1].aligned_mapping.coefficients == pytest.approx(result.mappings[moving.id].coefficients)
        assert not window._is_project_modified()
    finally:
        window.close()


def test_affine_alignment_cannot_be_silently_followed_by_similarity_georeference(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings, QSize
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "affine-combined.ini"), QSettings.Format.IniFormat))
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    project, reference, moving, _locations = affine_project()
    project.alignment_settings = SolverSettings(transform_model="affine", tolerance=1e-4)
    project.georeference_settings = SolverSettings()
    for point in project.control_points:
        if point.image_id == reference.id:
            observation = point.observations[0]
            point.set_geographic_coordinates(observation.x, observation.y, project.crs)
    window.project = project
    pixmap = QPixmap(1000, 800)
    pixmap.fill(QColor("white"))
    window._image_pixmaps = {reference.id: pixmap, moving.id: pixmap}
    window._image_sizes = {reference.id: QSize(1000, 800), moving.id: QSize(1000, 800)}
    window._refresh_all()
    before_poses = [(image.aligned_x, image.aligned_intrinsic) for image in window.project.images]
    undo_count = window.undo_stack.count()
    try:
        assert not window.run_combined_solvers()
        assert [(image.aligned_x, image.aligned_intrinsic) for image in window.project.images] == before_poses
        assert window.undo_stack.count() == undo_count
        assert errors and "Cannot georeference aligned candidate" in errors[-1]
    finally:
        window.close()