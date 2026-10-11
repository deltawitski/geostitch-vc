from dataclasses import replace

import pytest

from geostitch.adjustment import AlignmentResult
from geostitch.alignment_feedback import AlignmentFeedback, residual_color
from geostitch.models import GroundControlPoint, PointConstraint, Project, SolverSettings


def make_feedback_project():
    project = Project()
    images = [project.add_image(f"image-{index}.png") for index in range(3)]
    points = []
    for index, image in enumerate(images[:2]):
        point = GroundControlPoint.create(f"point-{index}")
        point.set_observation(image.id, 20, 30)
        project.add_control_point(point)
        points.append(point)
    other = GroundControlPoint.create("other")
    other.set_observation(images[1].id, 50, 60)
    project.add_control_point(other)
    links = [PointConstraint.create(points[0].id, point.id) for point in (points[1], other)]
    for link in links:
        project.add_constraint(link)
    result = AlignmentResult(
        {image.id: (index * 100.0, 0.0, 0.0, 1.0) for index, image in enumerate(images[:2])},
        {links[0].id: 0.3, links[1].id: 1.2}, 5, False, (images[0].id,),
    )
    return project, images, points, links, result


def test_feedback_aggregates_unique_links_per_image_and_point():
    project, images, points, links, result = make_feedback_project()
    feedback = AlignmentFeedback.capture(project, result, 0.5)
    assert feedback.point_residuals[points[0].id] == 1.2
    assert feedback.point_residuals[points[1].id] == 0.3
    assert feedback.image_residuals[images[0].id] == pytest.approx((0.3 ** 2 / 2 + 1.2 ** 2 / 2) ** 0.5)
    assert feedback.image_residuals[images[1].id] == feedback.image_residuals[images[0].id]
    assert images[2].id not in feedback.image_residuals
    assert feedback.is_current(project)
    assert not feedback.matches_layout(project)
    for image in images[:2]:
        image.aligned_x, image.aligned_y, image.aligned_rotation, image.aligned_scale = result.transforms[image.id]
    assert feedback.matches_layout(project)


def test_feedback_ignores_display_edits_but_detects_changed_inputs():
    project, images, points, links, result = make_feedback_project()
    feedback = AlignmentFeedback.capture(project, result, 0.5)
    images[0].visible = False
    images[0].name = "Renamed"
    points[0].name = "Renamed point"
    points[0].observations[0].locked = True
    project.canvas_state = "aligned"
    assert feedback.is_current(project)
    points[0].observations[0].x += 1
    assert not feedback.is_current(project)
    points[0].observations[0].x -= 1
    assert feedback.is_current(project)
    images[0].position_x = 100
    assert not feedback.is_current(project)


def test_feedback_detects_link_deletion_and_has_fixed_tolerance_colors():
    project, images, points, links, result = make_feedback_project()
    feedback = AlignmentFeedback.capture(project, result, 0.5)
    project.constraints.pop()
    assert not feedback.is_current(project)
    assert residual_color(0.5, 0.5) == "#2f9e68"
    assert residual_color(1.0, 0.5) == "#c28b19"
    assert residual_color(1.01, 0.5) == "#d44f54"


@pytest.mark.parametrize("field, value", [
    ("version", 6),
    ("tolerance", float("nan")),
    ("constraint_residuals", {"link": -1}),
    ("input_signature", [[], []]),
    ("result", {"transforms": []}),
])
def test_feedback_rejects_invalid_saved_metadata(field, value):
    project, images, points, links, result = make_feedback_project()
    data = AlignmentFeedback.capture(project, result, 0.5).to_dict()
    if field == "constraint_residuals":
        data["result"][field] = value
    else:
        data[field] = value
    with pytest.raises(ValueError):
        AlignmentFeedback.from_dict(data)


def test_feedback_version_five_saves_only_original_inputs_and_result():
    project, images, points, links, result = make_feedback_project()
    feedback = AlignmentFeedback.capture(project, result, 0.5)
    data = feedback.to_dict()
    assert data["version"] == 5
    assert set(data) == {"version", "result", "tolerance", "input_signature"}
    assert AlignmentFeedback.from_dict(data) == feedback


def test_feedback_preserves_optimizer_diagnostics_in_saved_project():
    project, images, points, links, result = make_feedback_project()
    result = replace(
        result, optimizer_status=1, optimizer_message="gtol satisfied", max_iterations=321,
        settings=SolverSettings(transform_model="rigid", loss="huber"),
    )
    project.alignment_feedback = AlignmentFeedback.capture(project, result, 0.5)

    restored = Project.from_dict(project.to_dict())

    assert restored.alignment_feedback.result == result


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_feedback_without_optimizer_diagnostics_loads_as_unknown(version):
    project, images, points, links, result = make_feedback_project()
    data = AlignmentFeedback.capture(project, result, 0.5).to_dict()
    data["version"] = version
    for field in ("optimizer_status", "optimizer_message", "max_iterations"):
        data["result"].pop(field)

    restored = AlignmentFeedback.from_dict(data)

    assert restored.result == result
    assert restored.result.optimizer_status is None
    assert restored.result.optimizer_message == ""
    assert restored.result.max_iterations is None


@pytest.mark.parametrize("field, value", [
    ("optimizer_status", True), ("optimizer_status", 5), ("optimizer_status", "1"),
    ("optimizer_message", None), ("optimizer_message", []),
    ("max_iterations", False), ("max_iterations", 0), ("max_iterations", 10001),
    ("max_iterations", 1.5),
])
def test_feedback_rejects_invalid_optimizer_diagnostics(field, value):
    project, images, points, links, result = make_feedback_project()
    data = AlignmentFeedback.capture(project, result, 0.5).to_dict()
    data["result"][field] = value
    with pytest.raises(ValueError):
        AlignmentFeedback.from_dict(data)


def test_feedback_rejects_invalid_saved_run_settings():
    project, images, points, links, result = make_feedback_project()
    data = AlignmentFeedback.capture(project, result, 0.5).to_dict()
    data["result"]["settings"] = {"transform_model": "affine"}
    with pytest.raises(ValueError):
        AlignmentFeedback.from_dict(data)


@pytest.mark.parametrize("version", [1, 2])
def test_feedback_rebuilds_summaries_from_original_topology_after_project_edits(version):
    project, images, points, links, result = make_feedback_project()
    feedback = AlignmentFeedback.capture(project, result, 0.5)
    data = feedback.to_dict()
    data["version"] = version
    if version == 1:
        data["point_residuals"] = {points[0].id: -999, "wrong-point": 999}
        data["image_residuals"] = {images[2].id: 999}
    project.constraints.pop()
    project.constraints[0].second_point_id = points[0].id
    points[0].observations[0].image_id = images[2].id
    points[1].observations[0].x += 100

    restored = AlignmentFeedback.from_dict(data)

    assert restored.result == result
    assert restored.input_signature == feedback.input_signature
    assert restored.point_residuals == feedback.point_residuals
    assert restored.image_residuals == feedback.image_residuals
    assert restored.point_residuals[points[0].id] == 1.2
    assert restored.image_residuals[images[0].id] == pytest.approx((0.3 ** 2 + 1.2 ** 2) ** 0.5 / 2 ** 0.5)
    assert images[2].id not in restored.image_residuals
    assert not restored.is_current(project)


@pytest.mark.parametrize("transforms", [{}, {"unrelated-image": (0.0, 0.0, 0.0, 1.0)}])
def test_feedback_summaries_do_not_depend_on_result_transforms(transforms):
    project, images, points, links, result = make_feedback_project()
    expected = AlignmentFeedback.capture(project, result, 0.5)
    result = AlignmentResult(transforms, result.constraint_residuals, 0, False, ())
    feedback = AlignmentFeedback.capture(project, result, 0.5)
    assert feedback.point_residuals == expected.point_residuals
    assert feedback.image_residuals == expected.image_residuals
    assert AlignmentFeedback.from_dict(feedback.to_dict()) == feedback


def test_feedback_ignores_residual_links_without_original_observed_endpoints():
    project, images, points, links, result = make_feedback_project()
    points[0].observations.clear()
    result.constraint_residuals["unknown-link"] = 100
    feedback = AlignmentFeedback.capture(project, result, 0.5)
    assert feedback.point_residuals == {}
    assert feedback.image_residuals == {}
    assert AlignmentFeedback.from_dict(feedback.to_dict()) == feedback


def test_feedback_counts_same_image_link_only_once():
    project, images, points, links, result = make_feedback_project()
    points[0].observations[0].image_id = images[1].id
    feedback = AlignmentFeedback.capture(project, result, 0.5)
    assert feedback.image_residuals == {
        images[1].id: pytest.approx(((0.3 ** 2 + 1.2 ** 2) / 2) ** 0.5),
    }


@pytest.mark.parametrize("group, index, value", [
    (0, 0, []),
    (0, 1, "invalid"),
    (0, 2, float("inf")),
    (0, 4, 0),
    (1, 0, None),
    (1, 1, []),
    (1, 2, float("nan")),
    (2, 1, []),
])
def test_feedback_rejects_invalid_signature_values(group, index, value):
    project, images, points, links, result = make_feedback_project()
    data = AlignmentFeedback.capture(project, result, 0.5).to_dict()
    data["input_signature"][group][0][index] = value
    with pytest.raises(ValueError):
        AlignmentFeedback.from_dict(data)


@pytest.mark.parametrize("tolerance", [0, -1, float("nan"), float("inf")])
def test_feedback_capture_rejects_invalid_tolerance(tolerance):
    project, images, points, links, result = make_feedback_project()
    with pytest.raises(ValueError):
        AlignmentFeedback.capture(project, result, tolerance)


def test_feedback_matches_layout_accepts_precomputed_current_state():
    project, images, points, links, result = make_feedback_project()
    feedback = AlignmentFeedback.capture(project, result, 0.5)
    for image in images[:2]:
        image.aligned_x, image.aligned_y, image.aligned_rotation, image.aligned_scale = result.transforms[image.id]
    assert feedback.matches_layout(project, current=True)
    assert not feedback.matches_layout(project, current=False)
    points[0].observations[0].x += 1
    assert not feedback.matches_layout(project)
    assert feedback.matches_layout(project, current=True)