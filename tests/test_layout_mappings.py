import json

import pytest

from geostitch.models import Project
from geostitch.transforms import MatrixTransform
from geostitch.alignment_feedback import AlignmentFeedback
from geostitch.adjustment import AlignmentResult, simulate_alignment
from geostitch.georeference import simulate_georeference
from geostitch.solver_readiness import check_readiness
from geostitch.project_io import load_project
from test_solver_workflow import solver_window


@pytest.mark.parametrize("aligned", [False, True])
@pytest.mark.parametrize("mapping", [
    MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50)),
    MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1)),
])
def test_layout_intrinsic_mapping_round_trips_without_losing_geometry(mapping, aligned):
    project = Project()
    image = project.add_image("image.png")
    image.source_dimensions = (1000, 800)
    image.set_layout_mapping(mapping, aligned=aligned)
    saved = project.to_dict()
    restored = Project.from_dict(json.loads(json.dumps(saved)))
    assert saved["version"] == 27
    assert restored.to_dict() == saved
    actual = restored.images[0].aligned_mapping if aligned else restored.images[0].rest_mapping
    for source in ((0, 0), (500, 400), (1000, 800)):
        assert actual.map_point(*source) == pytest.approx(mapping.map_point(*source))
    assert actual.model == mapping.model
    saved["version"] = 18
    for image_data in saved["images"]:
        image_data.pop("native_world_mapping")
    with pytest.raises(ValueError, match="not enabled"):
        Project.from_dict(saved)


def test_outer_pose_changes_preserve_intrinsic_shear():
    project = Project()
    image = project.add_image("image.png")
    image.set_layout_mapping(MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50)))
    intrinsic = image.rest_intrinsic
    image.position_x, image.position_y, image.rotation, image.scale = 100, 200, 30, 2
    expected = MatrixTransform.from_similarity((100, 200, 30, 2)).compose(intrinsic)
    assert image.rest_mapping == expected
    assert image.rest_intrinsic is intrinsic


def test_invalid_projective_domain_does_not_mutate_pose():
    project = Project()
    image = project.add_image("image.png")
    before = project.to_dict()
    matrix = MatrixTransform((1, 0, 0, 0, 1, 0, -0.002, 0, 1))
    with pytest.raises(ValueError, match="source dimensions"):
        image.set_layout_mapping(matrix)
    assert project.to_dict() == before
    image.source_dimensions = (1000, 800)
    before = project.to_dict()
    with pytest.raises(ValueError, match="horizon"):
        image.set_layout_mapping(matrix)
    assert project.to_dict() == before


def test_intrinsic_pose_mismatch_is_rejected():
    project = Project()
    image = project.add_image("image.png")
    image.set_layout_mapping(MatrixTransform.from_affine((1, 0.3, 40, 0, 1, 50)))
    saved = project.to_dict()
    saved["images"][0]["mappings"]["rest"]["intrinsic"]["mapping"]["coefficients"][1] += 0.1
    with pytest.raises(ValueError, match="disagree"):
        Project.from_dict(saved)


@pytest.mark.parametrize("mapping", [
    MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50)),
    MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1)),
])
def test_app_matrix_layout_move_undo_redo_and_save_reopen(solver_window, tmp_path, mapping):
    app, window, images, alignment, world, errors = solver_window
    image = images[0]
    image.source_dimensions = (1000, 800)
    image.set_layout_mapping(mapping)
    window._refresh_all()
    initial = image.rest_mapping
    assert window.canvas.image_mapping(image.id).coefficients == pytest.approx(initial.coefficients)
    observations = [(point.id, point.observations[0].x, point.observations[0].y) for point in window.project.control_points]
    item = window.canvas._layers[image.id]
    window._begin_project_edit("Move mapped image")
    window.canvas.set_image_position(image.id, 300, 200)
    window._finish_project_edit()
    moved = window.project.images[0].rest_mapping
    assert moved.map_point(0, 0) == pytest.approx((300, 200))
    assert moved.jacobian(20, 30) == pytest.approx(initial.jacobian(20, 30))
    window._refresh_all()
    assert window.canvas._layers[image.id] is item
    assert window.canvas.image_mapping(image.id).coefficients == pytest.approx(moved.coefficients)
    window.undo_stack.undo()
    assert window.project.images[0].rest_mapping.coefficients == pytest.approx(initial.coefficients)
    window.undo_stack.redo()
    assert window.project.images[0].rest_mapping.coefficients == pytest.approx(moved.coefficients)
    assert [(point.id, point.observations[0].x, point.observations[0].y) for point in window.project.control_points] == observations
    window.project_path = tmp_path / "mapped-layout.gsp"
    assert window.save_current_project()
    restored = load_project(window.project_path)
    assert restored.images[0].rest_mapping.coefficients == pytest.approx(moved.coefficients)
    assert not window._is_project_modified()


def test_intrinsic_shape_changes_mark_feedback_outdated_and_round_trip(solver_window):
    app, window, images, alignment, world, errors = solver_window
    image = images[0]
    image.set_layout_mapping(MatrixTransform.from_affine((1, 0.3, 40, 0, 1, 50)))
    feedback = AlignmentFeedback.capture(window.project, alignment, 0.5)
    assert feedback.is_current(window.project)
    restored = AlignmentFeedback.from_dict(feedback.to_dict())
    assert restored.is_current(window.project)
    image.rest_intrinsic = MatrixTransform.from_affine((1, 0.4, 0, 0, 1, 0))
    assert not feedback.is_current(window.project)
    assert not restored.is_current(window.project)


def test_general_aligned_result_applies_and_toggles_without_losing_rest_layout(solver_window):
    app, window, images, alignment, world, errors = solver_window
    image = images[0]
    rest = MatrixTransform.from_affine((1, 0.3, 40, 0, 1, 50))
    image.set_layout_mapping(rest)
    aligned = MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1))
    result = AlignmentResult({}, {}, 3, True, (image.id,), matrix_transforms={image.id: aligned},
                             source_dimensions={image.id: (1000, 800)})
    window._apply_alignment_result(result)
    assert window.canvas.image_mapping(image.id).coefficients == pytest.approx(aligned.coefficients)
    assert window._alignment_feedback.matches_layout(window.project)
    window._set_canvas_state("rest")
    assert window.canvas.image_mapping(image.id).coefficients == pytest.approx(rest.coefficients)
    window._set_canvas_state("aligned")
    assert window.canvas.image_mapping(image.id).coefficients == pytest.approx(aligned.coefficients)
    window.undo_stack.undo()
    assert window.project.images[0].aligned_intrinsic is None
    window.undo_stack.redo()
    assert window.project.images[0].aligned_mapping.coefficients == pytest.approx(aligned.coefficients)


def test_existing_solvers_do_not_silently_flatten_linked_general_layouts(solver_window):
    app, window, images, alignment, world, errors = solver_window
    images[0].set_layout_mapping(MatrixTransform.from_affine((1, 0.3, 40, 0, 1, 50)))
    issues = check_readiness(window.project)
    assert {issue.stage for issue in issues if issue.code == "unsupported_layout_mapping"} == {"alignment", "georeference"}
    with pytest.raises(ValueError, match="intrinsic layout"):
        simulate_alignment(window.project)
    with pytest.raises(ValueError, match="intrinsic layout"):
        simulate_georeference(window.project)


@pytest.mark.parametrize("dimensions, accepted", [((1000, 800), True), ((1100, 800), False)])
def test_projective_source_repair_validates_geometry_before_mutating_project(tmp_path, dimensions, accepted):
    from PIL import Image
    from geostitch.image_repair import apply_replacements

    project = Project()
    image = project.add_image("missing.png")
    image.source_dimensions = (1000, 800)
    image.set_layout_mapping(MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1)))
    before = project.to_dict()
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", dimensions).save(replacement)
    if accepted:
        assert apply_replacements(project.images, {image.id: replacement}) == [image.id]
        assert image.source_dimensions == (1000, 800)
        assert Project.from_dict(project.to_dict()).images[0].rest_mapping == image.rest_mapping
    else:
        with pytest.raises(ValueError, match="changes source dimensions"):
            apply_replacements(project.images, {image.id: replacement})
        assert project.to_dict() == before


@pytest.mark.parametrize("mapping", [
    MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50)),
    MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1)),
])
def test_real_mouse_drag_preserves_warp_and_creates_one_undo_step(solver_window, mapping):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest

    app, window, images, alignment, world, errors = solver_window
    image = images[0]
    image.source_dimensions = (1000, 800)
    image.set_layout_mapping(mapping)
    window._refresh_all()
    window.resize(1200, 800)
    window.show()
    app.processEvents()
    window.canvas.fit_images()
    window.canvas.set_selected_images({image.id})
    start = window.canvas.mapFromScene(QPointF(*mapping.map_point(600, 500)))
    end = start + QPoint(40, 25)
    delta = window.canvas.mapToScene(end) - window.canvas.mapToScene(start)
    before_count = window.undo_stack.count()
    QTest.mousePress(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(window.canvas.viewport().mapToGlobal(end)),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window.canvas.viewport(), event)
    QTest.mouseRelease(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    actual = window.project.images[0].rest_mapping
    for source in ((0, 0), (500, 400), (1000, 800)):
        original = mapping.map_point(*source)
        assert actual.map_point(*source) == pytest.approx((original[0] + delta.x(), original[1] + delta.y()), abs=1e-6)
    assert window.undo_stack.count() == before_count + 1
    window.undo_stack.undo()
    assert window.project.images[0].rest_mapping.coefficients == pytest.approx(mapping.coefficients)
    window.undo_stack.redo()
    assert window.project.images[0].rest_mapping.coefficients == pytest.approx(actual.coefficients)


def test_real_warped_point_drag_updates_source_pixels_and_undo(solver_window):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest

    app, window, images, alignment, world, errors = solver_window
    image = images[0]
    mapping = MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1))
    image.source_dimensions = (1000, 800)
    image.set_layout_mapping(mapping)
    point = next(point for point in window.project.control_points if point.image_id == image.id)
    point.observations[0].x, point.observations[0].y = 500, 400
    window._refresh_all()
    window.resize(1200, 800)
    window.show()
    app.processEvents()
    window.canvas.fit_images()
    marker = window.canvas._point_items[(image.id, point.id)]
    start = window.canvas.mapFromScene(marker.pos())
    end = start + QPoint(30, 20)
    expected = mapping.inverse().map_point(*(
        window.canvas.mapToScene(end).x(), window.canvas.mapToScene(end).y()
    ))
    before_count = window.undo_stack.count()
    QTest.mousePress(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(window.canvas.viewport().mapToGlobal(end)),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window.canvas.viewport(), event)
    QTest.mouseRelease(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    updated = window.project.point_by_id(point.id).observations[0]
    assert (updated.x, updated.y) == pytest.approx(expected, abs=1e-6)
    assert window.undo_stack.count() == before_count + 1
    window.undo_stack.undo()
    restored = window.project.point_by_id(point.id).observations[0]
    assert (restored.x, restored.y) == (500, 400)


def test_combined_similarity_rerun_replaces_old_general_aligned_mapping(solver_window, monkeypatch):
    app, window, images, alignment, world, errors = solver_window
    matrix = MatrixTransform.from_affine((1, 0.3, 40, 0, 1, 50))
    images[0].set_layout_mapping(matrix, aligned=True)
    window.project.canvas_state = "aligned"
    window._refresh_all()
    monkeypatch.setattr("geostitch.app.simulate_alignment", lambda *args, **kwargs: alignment)
    calls = []

    def georeference(candidate, **kwargs):
        assert all(image.aligned_intrinsic is None for image in candidate.images)
        calls.append(True)
        return world

    monkeypatch.setattr("geostitch.app.simulate_georeference", georeference)
    assert window.run_combined_solvers()
    assert calls == [True]
    assert all(image.aligned_intrinsic is None for image in window.project.images)
    assert errors == []


def test_arrange_matrix_layers_uses_warped_bounds_and_preserves_intrinsics(solver_window):
    app, window, images, alignment, world, errors = solver_window
    for index, image in enumerate(images):
        image.source_dimensions = (200, 150)
        image.set_layout_mapping(MatrixTransform.from_affine((1.2, 0.3, 40 + index * 20, 0.2, 0.8, 50)))
    intrinsics = {image.id: image.rest_intrinsic for image in images}
    window._refresh_all()
    window.arrange_images()
    bounds = [window.canvas._layers[image.id].sceneBoundingRect() for image in images]
    assert bounds[0].left() == pytest.approx(0)
    assert bounds[0].top() == pytest.approx(0)
    assert bounds[1].left() >= bounds[0].right() + 39
    assert bounds[2].top() >= bounds[0].bottom() + 39
    for image in window.project.images:
        assert image.rest_intrinsic.coefficients == pytest.approx(intrinsics[image.id].coefficients)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_legacy_report_signatures_migrate_to_intrinsic_aware_schema(solver_window, version):
    app, window, images, alignment, world, errors = solver_window
    feedback = AlignmentFeedback.capture(window.project, alignment, 0.5)
    saved = feedback.to_dict()
    saved["version"] = version
    saved["input_signature"][0] = [entry[:5] for entry in saved["input_signature"][0]]
    if version < 3:
        saved["result"].pop("mappings")
    restored = AlignmentFeedback.from_dict(saved)
    assert restored.is_current(window.project)
    assert restored.to_dict()["version"] == 5
    assert restored.result == alignment


@pytest.mark.parametrize("kind", ["rotate", "scale-se"])
def test_real_matrix_handle_gesture_commits_complete_mapping_and_undo(solver_window, kind):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest

    app, window, images, alignment, world, errors = solver_window
    image = images[0]
    image.source_dimensions = (1000, 800)
    mapping = MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1))
    image.set_layout_mapping(mapping)
    window._refresh_all()
    window.resize(1200, 800)
    window.show()
    app.processEvents()
    window.canvas.fit_images()
    window.canvas.set_selected_images({image.id})
    handle = next(handle for handle in window.canvas._transform_handles if handle.data(0) == kind)
    start = window.canvas.mapFromScene(handle.pos())
    center = QPointF(*mapping.map_point(500, 400))
    delta = handle.pos() - center
    destination = center + (QPointF(-delta.y(), delta.x()) if kind == "rotate" else delta * 0.8)
    end = window.canvas.mapFromScene(destination)
    before_count = window.undo_stack.count()
    QTest.mousePress(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(window.canvas.viewport().mapToGlobal(end)),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window.canvas.viewport(), event)
    QTest.mouseRelease(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    actual = window.project.images[0].rest_mapping
    assert actual.coefficients != mapping.coefficients
    assert actual.map_point(500, 400) == pytest.approx(mapping.map_point(500, 400), abs=1e-6)
    assert actual.model == "projective"
    assert window.canvas.image_mapping(image.id).coefficients == pytest.approx(actual.coefficients)
    assert window.undo_stack.count() == before_count + 1
    window.undo_stack.undo()
    assert window.project.images[0].rest_mapping.coefficients == pytest.approx(mapping.coefficients)
    window.undo_stack.redo()
    assert window.project.images[0].rest_mapping.coefficients == pytest.approx(actual.coefficients)


@pytest.mark.parametrize("background", [False, True])
def test_changed_source_domain_is_not_drawn_under_existing_intrinsic_mapping(solver_window, monkeypatch, background):
    from PySide6.QtGui import QImage

    app, window, images, alignment, world, errors = solver_window
    image = images[0]
    image.source_dimensions = (1000, 800)
    image.set_layout_mapping(MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1)))
    window._image_pixmaps.pop(image.id, None)
    decoded = QImage(1100, 800, QImage.Format.Format_RGB32)
    before = window.project.to_dict()
    if background:
        window._reset_image_loading(background=True)
        key = (window._image_load_generation, image.id, image.path)
        window._on_image_loaded(key, decoded, "")
        assert image.id not in window._image_pixmaps
        assert "Source dimensions changed" in window._image_load_errors[image.id]
    else:
        window._background_images = False
        monkeypatch.setattr(window, "_read_preview_data", lambda path: (decoded, ""))
        pixmap, error = window._pixmap_for_image(image)
        assert pixmap.isNull()
        assert "Source dimensions changed" in error
    assert window.project.to_dict() == before


def test_alignment_application_rejects_result_with_wrong_source_domain_before_mutation(solver_window):
    from PySide6.QtCore import QSize

    app, window, images, alignment, world, errors = solver_window
    image = images[0]
    window._image_sizes[image.id] = QSize(1000, 800)
    matrix = MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1))
    result = AlignmentResult({}, {}, 3, True, (image.id,), matrix_transforms={image.id: matrix},
                             source_dimensions={image.id: (2000, 1600)})
    before = window.project.to_dict()
    undo_count = window.undo_stack.count()
    with pytest.raises(ValueError, match="do not match"):
        window._apply_alignment_result(result)
    assert window.project.to_dict() == before
    assert window.undo_stack.count() == undo_count