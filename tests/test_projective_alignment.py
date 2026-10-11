import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from geostitch.adjustment import _projective_control_rank, simulate_alignment
from geostitch.app import MainWindow
from geostitch.models import GroundControlPoint, PointConstraint, Project, SolverSettings
from geostitch.solver_readiness import check_readiness
from geostitch.transforms import MatrixTransform


def projective_project(mapping=None, *, locations=None, dimensions=(1000, 800)):
    project = Project()
    reference, moving = [project.add_image(name) for name in ("reference.png", "moving.png")]
    reference.source_dimensions = moving.source_dimensions = dimensions
    mapping = mapping or MatrixTransform((1.08, 0.12, 140, -0.06, 0.93, 35, 0.00035, -0.00018, 1))
    locations = locations or [(40, 30), (900, 45), (80, 720), (850, 700), (300, 400), (670, 250)]
    for index, (x, y) in enumerate(locations):
        target_x, target_y = mapping.map_point(x, y)
        fixed = GroundControlPoint.create(f"fixed-{index}")
        fixed.set_observation(reference.id, target_x, target_y)
        moving_point = GroundControlPoint.create(f"moving-{index}")
        moving_point.set_observation(moving.id, x, y)
        project.add_control_point(fixed)
        project.add_control_point(moving_point)
        project.add_constraint(PointConstraint.create(fixed.id, moving_point.id))
    return project, reference, moving, mapping, locations


def test_projective_alignment_recovers_homography_and_interiors():
    project, reference, moving, expected, locations = projective_project()
    settings = SolverSettings(transform_model="projective", tolerance=1e-5, max_iterations=1000)

    result = simulate_alignment(project, settings=settings)

    actual = result.mappings[moving.id]
    assert result.converged
    assert actual.model == "projective"
    assert result.anchor_image_ids == (reference.id,)
    assert actual.domain_bounds(*moving.source_dimensions)
    for x in range(0, 1001, 100):
        for y in range(0, 801, 100):
            assert actual.map_point(x, y) == pytest.approx(expected.map_point(x, y), abs=2e-4)
    assert result.maximum_residual < 1e-5
    assert moving.id not in result.transforms
    assert result.mapping_records[moving.id].source_size == moving.source_dimensions


def test_projective_rank_preflight_rejects_collinear_and_insufficient_pairs():
    project, _reference, _moving, _mapping, locations = projective_project(locations=[(x, 2 * x) for x in (10, 100, 200, 300, 500)])
    project.alignment_settings = SolverSettings(transform_model="projective")
    assert _projective_control_rank(locations, locations) < 8
    issues = check_readiness(project, ("alignment",))
    assert any(issue.code == "projective_geometry" and issue.severity == "Error" for issue in issues)
    with pytest.raises(ValueError, match="general position"):
        simulate_alignment(project, settings=project.alignment_settings)


def test_projective_requires_source_dimensions_even_when_controls_are_sufficient():
    project, _reference, moving, _mapping, _locations = projective_project()
    moving.source_dimensions = None
    settings = SolverSettings(transform_model="projective")
    with pytest.raises(ValueError, match="source dimensions"):
        simulate_alignment(project, settings=settings)
    project.alignment_settings = settings
    issues = check_readiness(project, ("alignment",))
    assert any(issue.code == "projective_source_dimensions" and issue.severity == "Error" for issue in issues)


@pytest.mark.parametrize("mapping, settings, message", [
    (MatrixTransform((1, 0, 0, 0, 1, 0, -0.002, 0, 1)), SolverSettings(transform_model="projective"), "horizon"),
    (MatrixTransform.from_similarity((0, 0, 0, 30)), SolverSettings(transform_model="projective", projective_max_stretch=10), "stretch"),
    (MatrixTransform.from_affine((-1, 0, 1000, 0, 1, 0)), SolverSettings(transform_model="projective"), "reflects"),
])
def test_projective_domain_and_distortion_limits_are_enforced(mapping, settings, message):
    project, reference, moving, _expected, locations = projective_project(mapping=mapping)
    with pytest.raises(ValueError, match=message):
        simulate_alignment(project, settings=settings)


def test_projective_alignment_applies_renders_saves_and_undoes(tmp_path, monkeypatch):
    import geostitch.app as app_module

    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "projective-alignment.ini"), QSettings.Format.IniFormat))
    project, reference, moving, expected, _locations = projective_project()
    project.alignment_settings = SolverSettings(transform_model="projective", tolerance=1e-5, max_iterations=1000)
    initial_mappings = {}
    simulate = app_module.simulate_alignment

    def capture_initial_mappings(*args, **kwargs):
        initial_mappings.update(kwargs["initial_mappings"])
        return simulate(*args, **kwargs)

    monkeypatch.setattr(app_module, "simulate_alignment", capture_initial_mappings)
    window.project = project
    solver_errors = []
    monkeypatch.setattr(window, "_show_error", solver_errors.append)
    pixmap = QPixmap(1000, 800)
    pixmap.fill(QColor("#729485"))
    window._image_pixmaps = {reference.id: pixmap, moving.id: pixmap}
    window._image_sizes = {reference.id: QSize(1000, 800), moving.id: QSize(1000, 800)}
    window._refresh_all()
    try:
        before_count = window.undo_stack.count()
        assert window.run_alignment(), solver_errors
        assert moving.id in initial_mappings
        assert initial_mappings[moving.id].model == "similarity"
        actual = window.project.point_by_id(next(
            point.id for point in window.project.control_points if point.image_id == moving.id
        ))
        assert window.project.canvas_state == "aligned"
        assert window.project.images[1].aligned_intrinsic.model == "projective"
        for source in ((0, 0), (500, 400), (1000, 800)):
            assert window.canvas.image_mapping(moving.id).map_point(*source) == pytest.approx(expected.map_point(*source), abs=2e-4)
        assert window.undo_stack.count() == before_count + 1
        window.project_path = tmp_path / "projective-alignment.gsp"
        assert window.save_current_project()
        restored = Project.from_dict(window.project.to_dict())
        assert restored.images[1].aligned_mapping == window.project.images[1].aligned_mapping
        assert restored.alignment_feedback.result.mappings[moving.id].coefficients == pytest.approx(
            window.project.images[1].aligned_mapping.coefficients, rel=1e-12, abs=1e-12,
        )
        window.undo_stack.undo()
        assert window.project.images[1].aligned_intrinsic is None
        window.undo_stack.redo()
        assert window.project.images[1].aligned_mapping == restored.images[1].aligned_mapping
    finally:
        window.close()