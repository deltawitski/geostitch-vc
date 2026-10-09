import pytest

from geostitch.adjustment import simulate_alignment
from geostitch.georeference import simulate_georeference
from geostitch.models import GroundControlPoint, PointConstraint, Project


def test_similarity_solver_recovers_translation_rotation_and_scale():
    project = Project()
    reference = project.add_image("reference.png")
    moving = project.add_image("moving.png")
    target_transform = (140.0, -35.0, 18.0, 1.25)
    source_pixels = [(12.0, 18.0), (100.0, 15.0), (40.0, 95.0), (140.0, 110.0)]

    from math import cos, radians, sin

    angle = radians(target_transform[2])
    for index, (pixel_x, pixel_y) in enumerate(source_pixels):
        point_reference = GroundControlPoint.create(f"ref-{index}")
        point_moving = GroundControlPoint.create(f"moving-{index}")
        world_x = target_transform[0] + target_transform[3] * (cos(angle) * pixel_x - sin(angle) * pixel_y)
        world_y = target_transform[1] + target_transform[3] * (sin(angle) * pixel_x + cos(angle) * pixel_y)
        point_reference.set_observation(reference.id, world_x, world_y)
        point_moving.set_observation(moving.id, pixel_x, pixel_y)
        project.add_control_point(point_reference)
        project.add_control_point(point_moving)
        project.add_constraint(PointConstraint.create(point_reference.id, point_moving.id))

    result = simulate_alignment(project, tolerance=0.01)

    assert result.converged
    assert result.iterations > 0
    assert result.anchor_image_ids == (reference.id,)
    assert result.transforms[reference.id] == (0.0, 0.0, 0.0, 1.0)
    position_x, position_y, rotation, scale = result.transforms[moving.id]
    assert abs(position_x - target_transform[0]) < 0.01
    assert abs(position_y - target_transform[1]) < 0.01
    assert abs(rotation - target_transform[2]) < 0.01
    assert abs(scale - target_transform[3]) < 0.001
    assert result.maximum_residual <= 0.01


def test_single_correspondence_adjusts_translation_only():
    project = Project()
    reference = project.add_image("fixed.png")
    moving = project.add_image("free.png")
    fixed_point = GroundControlPoint.create("fixed")
    moving_point = GroundControlPoint.create("moving")
    fixed_point.set_observation(reference.id, 90.0, 65.0)
    moving_point.set_observation(moving.id, 20.0, 30.0)
    project.add_control_point(fixed_point)
    project.add_control_point(moving_point)
    project.add_constraint(PointConstraint.create(fixed_point.id, moving_point.id))
    initial = {
        reference.id: (0.0, 0.0, 0.0, 1.0),
        moving.id: (0.0, 0.0, 12.0, 1.4),
    }

    result = simulate_alignment(project, tolerance=0.01, initial_transforms=initial)

    assert result.converged
    assert result.transforms[moving.id][2:] == (12.0, 1.4)
    assert result.maximum_residual <= 0.01


def test_solver_rejects_projects_without_cross_image_constraints():
    project = Project()
    image = project.add_image("single.png")
    first = GroundControlPoint.create("first")
    second = GroundControlPoint.create("second")
    first.set_observation(image.id, 5.0, 5.0)
    second.set_observation(image.id, 8.0, 8.0)
    project.add_control_point(first)
    project.add_control_point(second)
    project.add_constraint(PointConstraint.create(first.id, second.id))

    with pytest.raises(ValueError, match="different images"):
        simulate_alignment(project)


def test_unconnected_images_are_excluded_from_solution():
    project = Project()
    reference = project.add_image("reference.png")
    moving = project.add_image("moving.png")
    untouched = project.add_image("untouched.png")
    fixed_point = GroundControlPoint.create("fixed")
    moving_point = GroundControlPoint.create("moving")
    fixed_point.set_observation(reference.id, 20.0, 30.0)
    moving_point.set_observation(moving.id, 5.0, 15.0)
    project.add_control_point(fixed_point)
    project.add_control_point(moving_point)
    project.add_constraint(PointConstraint.create(fixed_point.id, moving_point.id))

    result = simulate_alignment(project)

    assert set(result.transforms) == {reference.id, moving.id}
    assert untouched.id not in result.transforms


def test_three_image_loop_optimizes_all_constraints_jointly():
    project = Project()
    images = [project.add_image(f"image-{index}.png") for index in range(3)]

    def add_edge_constraint(source_index, target_index, wanted_target_translation, point_name):
        source_point = GroundControlPoint.create(f"{point_name}-source")
        target_point = GroundControlPoint.create(f"{point_name}-target")
        source_point.set_observation(images[source_index].id, 0.0, 0.0)
        target_point.set_observation(images[target_index].id, -wanted_target_translation, 0.0)
        project.add_control_point(source_point)
        project.add_control_point(target_point)
        project.add_constraint(PointConstraint.create(source_point.id, target_point.id))

    add_edge_constraint(0, 1, 10.0, "ab")
    add_edge_constraint(1, 2, 10.0, "bc")
    add_edge_constraint(0, 2, 25.0, "ac")

    result = simulate_alignment(project, tolerance=1.67, max_iterations=1000)

    first_x = result.transforms[images[0].id][0]
    second_x = result.transforms[images[1].id][0]
    third_x = result.transforms[images[2].id][0]
    assert first_x == 0.0
    assert abs(second_x - 11.6666667) < 0.01
    assert abs(third_x - 23.3333333) < 0.01
    assert len(result.constraint_residuals) == 3
    assert all(abs(residual - 5.0 / 3.0) < 0.01 for residual in result.constraint_residuals.values())
    assert result.converged
    assert result.iterations > 0


def test_georeference_second_stage_solves_every_image_from_world_control_points():
    project = Project(crs="EPSG:26910")
    first = project.add_image("first-sheet.png")
    second = project.add_image("second-sheet.png")
    first_transform = (500_000.0, 5_400_000.0, 0.0, 2.0)
    second_transform = (500_250.0, 5_400_100.0, 12.0, 1.5)
    first_pixel_points = [(10.0, 15.0), (120.0, 18.0), (30.0, 140.0), (155.0, 120.0)]

    from math import cos, radians, sin

    def world_point(pixel_x, pixel_y, transform):
        world_x, world_y, rotation, scale = transform
        angle = radians(rotation)
        pixel_y = -pixel_y
        return (
            world_x + scale * (cos(angle) * pixel_x - sin(angle) * pixel_y),
            world_y + scale * (sin(angle) * pixel_x + cos(angle) * pixel_y),
        )

    def pixel_point(world_x, world_y, transform):
        origin_x, origin_y, rotation, scale = transform
        angle = radians(rotation)
        dx = (world_x - origin_x) / scale
        dy = (world_y - origin_y) / scale
        return cos(angle) * dx + sin(angle) * dy, sin(angle) * dx - cos(angle) * dy

    for index, first_pixel in enumerate(first_pixel_points):
        geographic_x, geographic_y = world_point(*first_pixel, first_transform)
        second_pixel = pixel_point(geographic_x, geographic_y, second_transform)
        point = GroundControlPoint.create(f"gcp-{index}")
        point.geographic_x = geographic_x
        point.geographic_y = geographic_y
        point.set_observation(first.id, *first_pixel)
        point.set_observation(second.id, *second_pixel)
        project.add_control_point(point)

    result = simulate_georeference(project, tolerance=0.01)

    assert result.converged
    assert result.crs == "EPSG:26910"
    assert set(result.transforms) == {first.id, second.id}
    for image, expected in ((first, first_transform), (second, second_transform)):
        actual = result.transforms[image.id]
        assert abs(actual[0] - expected[0]) < 0.02
        assert abs(actual[1] - expected[1]) < 0.02
        assert abs(actual[2] - expected[2]) < 0.001
        assert abs(actual[3] - expected[3]) < 0.00001
    assert result.maximum_residual < 0.01


def test_georeference_requires_two_distinct_pixels_to_solve_rotation_and_scale():
    project = Project()
    image = project.add_image("one-gcp.png")
    point = GroundControlPoint.create("single")
    point.geographic_x = 1000.0
    point.geographic_y = 2000.0
    point.set_observation(image.id, 20.0, 30.0)
    project.add_control_point(point)

    result = simulate_georeference(project, initial_transforms={image.id: (0.0, 0.0, 15.0, 1.25)})

    assert result.transforms[image.id][2:] == (15.0, 1.25)
    assert result.maximum_residual < 1e-6


def test_georeference_solves_constraint_connected_images_as_one_chunk():
    from geostitch.models import PointConstraint

    project = Project(crs="EPSG:26910")
    first = project.add_image("a.png")
    second = project.add_image("b.png")
    second.position_x = 100.0
    second.position_y = 0.0
    first_point = GroundControlPoint.create("a")
    first_point.set_observation(first.id, 100.0, 0.0)
    second_point = GroundControlPoint.create("b")
    second_point.set_observation(second.id, 0.0, 0.0)
    project.add_control_point(first_point)
    project.add_control_point(second_point)
    project.add_constraint(PointConstraint.create(first_point.id, second_point.id))
    for point, pixel in ((first_point, (0.0, 0.0)), (second_point, (50.0, 50.0))):
        image = first if point is first_point else second
        extra = GroundControlPoint.create(f"g-{point.name}")
        extra.set_observation(image.id, *pixel)
        project.add_control_point(extra)
    extras = project.control_points[2:]
    extras[0].geographic_x, extras[0].geographic_y = 1000.0, 2000.0
    extras[1].geographic_x, extras[1].geographic_y = 1150.0, 1950.0

    result = simulate_georeference(project, tolerance=0.01)

    assert result.converged
    assert set(result.transforms) == {first.id, second.id}
    assert result.transforms[first.id][3] == result.transforms[second.id][3]
    assert abs(result.transforms[second.id][0] - 1100.0) < 0.01
    assert abs(result.transforms[second.id][1] - 2000.0) < 0.01
