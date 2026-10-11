from geostitch.models import GroundControlPoint, PointConstraint, Project, SolverSettings
from geostitch.solver_readiness import check_readiness


def add_point(project, image, name, location, geographic=None):
    point = GroundControlPoint.create(name)
    point.set_observation(image.id, *location)
    if geographic is not None:
        point.geographic_x, point.geographic_y = geographic
    project.add_control_point(point)
    return point


def test_empty_project_reports_stage_specific_blockers():
    issues = check_readiness(Project())
    assert {(issue.stage, issue.code) for issue in issues if issue.severity == "Error"} == {
        ("alignment", "no_links"), ("georeference", "no_geographic_controls"),
    }


def test_single_link_warns_translation_only_without_blocking():
    project = Project()
    first, second = [project.add_image(name) for name in ("one.png", "two.png")]
    points = [add_point(project, image, str(index), (10, 20)) for index, image in enumerate((first, second))]
    link = PointConstraint.create(points[0].id, points[1].id)
    project.add_constraint(link)
    issues = check_readiness(project, ("alignment",))
    assert not any(issue.severity == "Error" for issue in issues)
    warning = next(issue for issue in issues if issue.code == "translation_only")
    assert set(warning.image_ids) == {first.id, second.id}
    assert warning.constraint_ids == (link.id,)


def test_explicit_translation_model_does_not_warn_about_automatic_parameter_fallback():
    project = Project()
    project.alignment_settings = SolverSettings(transform_model="translation")
    project.georeference_settings = SolverSettings(transform_model="translation")
    first, second = [project.add_image(name) for name in ("one.png", "two.png")]
    points = [add_point(project, image, str(index), (10, 20)) for index, image in enumerate((first, second))]
    points[0].geographic_x, points[0].geographic_y = 100, 200
    project.add_constraint(PointConstraint.create(points[0].id, points[1].id))
    issues = check_readiness(project)
    assert not any(issue.code == "translation_only" for issue in issues)
    assert not any(issue.severity == "Error" for issue in issues)


def test_affine_alignment_readiness_requires_noncollinear_controls():
    project = Project()
    first, second = [project.add_image(name) for name in ("one.png", "two.png")]
    project.alignment_settings = SolverSettings(transform_model="affine")
    first_points = [add_point(project, first, f"a-{index}", location) for index, location in enumerate(((0, 0), (20, 20), (40, 40)))]
    second_points = [add_point(project, second, f"b-{index}", location) for index, location in enumerate(((10, 10), (30, 30), (50, 50)))]
    for source, target in zip(first_points, second_points):
        project.add_constraint(PointConstraint.create(source.id, target.id))
    issues = check_readiness(project, ("alignment",))
    assert any(issue.stage == "alignment" and issue.code == "affine_geometry" and issue.severity == "Error" for issue in issues)


def test_tps_alignment_readiness_requires_per_image_geometry_and_dimensions():
    project = Project()
    first, second = [project.add_image(name) for name in ("one.png", "two.png")]
    project.alignment_settings = SolverSettings(transform_model="tps")
    first_points = [add_point(project, first, f"a-{index}", location) for index, location in enumerate(((0, 0), (20, 20), (40, 40)))]
    second_points = [add_point(project, second, f"b-{index}", location) for index, location in enumerate(((10, 10), (30, 30), (50, 50)))]
    for source, target in zip(first_points, second_points):
        project.add_constraint(PointConstraint.create(source.id, target.id))

    issues = check_readiness(project, ("alignment",))

    assert any(issue.code == "tps_alignment_geometry" and issue.severity == "Error" for issue in issues)
    assert {image_id for issue in issues if issue.code == "tps_alignment_source_dimensions" for image_id in issue.image_ids} == {first.id, second.id}

    first.source_dimensions = second.source_dimensions = (100, 100)
    for index, point in enumerate(first_points):
        point.observations[0].x, point.observations[0].y = ((0, 0), (20, 0), (0, 20))[index]
    for index, point in enumerate(second_points):
        point.observations[0].x, point.observations[0].y = ((10, 10), (30, 10), (10, 30))[index]
    issues = check_readiness(project, ("alignment",))

    assert not any(issue.severity == "Error" for issue in issues)


def test_affine_georeference_blocks_collinear_source_controls():
    project = Project()
    image = project.add_image("one.png")
    project.georeference_settings = SolverSettings(transform_model="affine")
    for index, coordinate in enumerate((0, 20, 40, 60)):
        add_point(project, image, f"control-{index}", (coordinate, 2 * coordinate), (500 + coordinate, 4000 + 3 * coordinate))

    issues = check_readiness(project, ("georeference",))

    assert any(issue.code == "affine_georeference_geometry" and issue.severity == "Error" for issue in issues)


def test_projective_georeferencing_requires_four_controls_and_source_dimensions():
    project = Project()
    project.georeference_settings = SolverSettings(transform_model="projective")
    image = project.add_image("one.png")
    point = add_point(project, image, "control", (10, 20), (100, 200))

    issues = check_readiness(project, ("georeference",))

    assert any(issue.code == "projective_georeference_geometry" and issue.severity == "Error" for issue in issues)
    assert point.geographic_x == 100


def test_tps_georeference_readiness_requires_three_independent_groups_and_dimensions():
    project = Project(crs="EPSG:4326")
    image = project.add_image("one.png")
    project.georeference_settings = SolverSettings(transform_model="tps")
    for index, (x, y) in enumerate(((0, 0), (20, 20), (40, 40))):
        add_point(project, image, f"control-{index}", (x, y), (10 + x * 0.001, 45 + y * 0.001))

    issues = check_readiness(project, ("georeference",))

    assert any(issue.code == "tps_georeference_geometry" and issue.severity == "Error" for issue in issues)
    assert any(issue.code == "tps_georeference_source_dimensions" and issue.image_ids == (image.id,) for issue in issues)

    image.source_dimensions = (100, 100)
    for point in project.control_points:
        observation = point.observations[0]
        observation.x, observation.y = (
            (0, 0), (100, 0), (0, 100),
        )[project.control_points.index(point)]
    issues = check_readiness(project, ("georeference",))
    assert not any(issue.code == "tps_georeference_geometry" for issue in issues)
    assert not any(issue.code == "tps_georeference_source_dimensions" for issue in issues)


def test_tps_georeference_readiness_requires_geographic_project_crs():
    project = Project(crs="EPSG:32610")
    image = project.add_image("one.png")
    project.georeference_settings = SolverSettings(transform_model="tps")
    for index, location in enumerate(((0, 0), (20, 0), (0, 20))):
        add_point(project, image, f"control-{index}", location, (500000 + location[0], 4000000 + location[1]))

    issues = check_readiness(project, ("georeference",))

    assert any(issue.code == "tps_georeference_crs" and issue.severity == "Error" for issue in issues)


def test_projective_georeference_readiness_requires_source_dimensions_after_rank_passes():
    project = Project(crs="EPSG:32610")
    image = project.add_image("one.png")
    project.georeference_settings = SolverSettings(transform_model="projective")
    locations = ((0, 0), (100, 0), (0, 100), (100, 100), (40, 60))
    for index, (x, y) in enumerate(locations):
        add_point(project, image, f"control-{index}", (x, y), (500000 + x, 4000000 + y))

    issues = check_readiness(project, ("georeference",))
    assert any(issue.code == "projective_georeference_source_dimensions" and issue.image_ids == (image.id,) for issue in issues)

    issues = check_readiness(project, ("georeference",), {image.id: (100, 100)})
    assert not any(issue.code == "projective_georeference_source_dimensions" for issue in issues)


def test_projective_georeference_readiness_accepts_geographic_local_metric_frame():
    project = Project(crs="EPSG:4326")
    image = project.add_image("one.png")
    image.source_dimensions = (100, 100)
    project.georeference_settings = SolverSettings(transform_model="projective")
    for index, (x, y) in enumerate(((0, 0), (100, 0), (0, 100), (100, 100), (40, 60))):
        add_point(project, image, f"control-{index}", (x, y), (10 + x * 0.001, 45 + y * 0.001))

    issues = check_readiness(project, ("georeference",))

    assert not any(issue.code == "projective_georeference_crs" for issue in issues)
    assert not any(issue.severity == "Error" for issue in issues)


def test_geographic_local_metric_readiness_warns_for_wide_control_extent():
    project = Project(crs="EPSG:4326")
    image = project.add_image("wide-controls.png")
    project.georeference_settings = SolverSettings(transform_model="affine")
    controls = (
        ((0, 0), (10, 45)), ((100, 0), (12, 45)),
        ((0, 100), (10, 47)), ((100, 100), (12, 47)),
        ((50, 50), (11, 46)),
    )
    for index, (pixel, geographic) in enumerate(controls):
        add_point(project, image, f"wide-{index}", pixel, geographic)

    issues = check_readiness(project, ("georeference",))

    warning = next(issue for issue in issues if issue.code == "local_metric_extent")
    assert warning.severity == "Warning"
    assert warning.image_ids == (image.id,)
    assert "sampled pairwise distance distortion" in warning.message
    assert not any(issue.severity == "Error" for issue in issues)


def test_affine_georeference_readiness_checks_geographic_target_rank():
    project = Project()
    image = project.add_image("one.png")
    project.georeference_settings = SolverSettings(transform_model="affine")
    for index, location in enumerate(((0, 0), (20, 0), (0, 20), (20, 20))):
        add_point(project, image, f"control-{index}", location, (500 + 2 * location[0], 4000 + 3 * location[0]))

    issues = check_readiness(project, ("georeference",))

    assert any(issue.code == "affine_georeference_geometry" and issue.severity == "Error" for issue in issues)


def test_affine_georeference_readiness_accepts_geographic_local_metric_frame():
    project = Project(crs="EPSG:4326")
    image = project.add_image("one.png")
    project.georeference_settings = SolverSettings(transform_model="affine")
    for index, (pixel_x, pixel_y) in enumerate(((0, 0), (20, 0), (0, 20), (20, 20))):
        add_point(project, image, f"control-{index}", (pixel_x, pixel_y), (10 + pixel_x * 0.001, 45 + pixel_y * 0.001))

    issues = check_readiness(project, ("georeference",))

    assert not any(issue.code == "affine_georeference_crs" for issue in issues)
    assert not any(issue.severity == "Error" for issue in issues)


def test_affine_georeference_warns_for_full_rank_nearly_collinear_controls():
    project = Project(crs="EPSG:32610")
    image = project.add_image("one.png")
    project.georeference_settings = SolverSettings(transform_model="affine")
    source = ((0, 0), (100, 0.01), (200, -0.01), (300, 0.02), (400, -0.02))
    targets = ((500, 400), (600, 400), (500, 500), (600, 500), (540, 460))
    for index, (pixel, geographic) in enumerate(zip(source, targets)):
        add_point(project, image, f"control-{index}", pixel, geographic)

    issues = check_readiness(project, ("georeference",))

    warning = next(issue for issue in issues if issue.code == "affine_georeference_conditioning")
    assert warning.severity == "Warning"
    assert warning.image_ids == (image.id,)
    assert not any(issue.severity == "Error" for issue in issues)
def test_disconnected_and_isolated_images_are_identified():
    project = Project()
    images = [project.add_image(f"image-{index}.png") for index in range(5)]
    points = [add_point(project, image, str(index), (10, 20)) for index, image in enumerate(images)]
    for first, second in ((0, 1), (2, 3)):
        project.add_constraint(PointConstraint.create(points[first].id, points[second].id))
    issues = check_readiness(project, ("alignment",))
    assert any(issue.code == "disconnected_groups" for issue in issues)
    assert next(issue for issue in issues if issue.code == "unlinked_images").image_ids == (images[4].id,)


def test_geographic_controls_are_counted_in_connected_group_frames():
    project = Project()
    first, second = [project.add_image(name) for name in ("one.png", "two.png")]
    first.position_x = 0
    second.position_x = 100
    points = [add_point(project, first, "one", (10, 20), (100, 200)),
              add_point(project, second, "two", (10, 20), (200, 200))]
    project.add_constraint(PointConstraint.create(points[0].id, points[1].id))
    issues = check_readiness(project, ("georeference",))
    assert not any(issue.code == "translation_only" for issue in issues)
    assert any(issue.code == "minimal_controls" for issue in issues)
    second.position_x = 0
    issues = check_readiness(project, ("georeference",))
    assert any(issue.code == "translation_only" for issue in issues)


def test_uncontrolled_group_and_partial_coordinate_are_warnings():
    project = Project()
    first, second = [project.add_image(name) for name in ("one.png", "two.png")]
    add_point(project, first, "one", (10, 20), (100, 200))
    point = add_point(project, second, "two", (10, 20))
    point.geographic_x = 300
    issues = check_readiness(project, ("georeference",))
    assert any(issue.code == "incomplete_geographic" for issue in issues)
    assert next(issue for issue in issues if issue.code == "ungrounded_group").image_ids == (second.id,)


def test_clustered_controls_use_source_dimensions_without_modifying_project():
    project = Project()
    image = project.add_image("image.png")
    for index in range(3):
        add_point(project, image, str(index), (index * 10, index * 10), (100 + index, 200 + index))
    before = project.to_dict()
    issues = check_readiness(project, ("georeference",), {image.id: (5000, 3000)})
    assert any(issue.code == "clustered_controls" for issue in issues)
    assert project.to_dict() == before


def test_nonfinite_observation_is_blocking_and_identifies_point():
    project = Project()
    image = project.add_image("image.png")
    point = add_point(project, image, "Point", (10, 20))
    point.observations[0].x = float("nan")
    issues = check_readiness(project, ("alignment",))
    assert issues[0].severity == "Error"
    assert issues[0].point_ids == (point.id,)


def test_missing_image_files_warn_without_blocking_valid_geographic_controls():
    project = Project()
    image = project.add_image("missing.png")
    add_point(project, image, "one", (10, 20), (100, 200))
    issues = check_readiness(project, ("georeference",))
    assert any(issue.code == "missing_sources" for issue in issues)
    assert not any(issue.severity == "Error" for issue in issues)


def test_invalid_crs_blocks_only_georeference():
    project = Project(crs="not a CRS")
    issues = check_readiness(project)
    assert next(issue for issue in issues if issue.code == "invalid_crs").stage == "georeference"


def test_reversed_links_use_consistent_image_coordinates():
    project = Project()
    first, second = [project.add_image(name) for name in ("one.png", "two.png")]
    first_points = [add_point(project, first, f"one-{index}", location) for index, location in enumerate(((10, 20), (30, 40)))]
    second_points = [add_point(project, second, f"two-{index}", location) for index, location in enumerate(((30, 40), (10, 20)))]
    project.add_constraint(PointConstraint.create(first_points[0].id, second_points[0].id))
    project.add_constraint(PointConstraint.create(second_points[1].id, first_points[1].id))
    issues = check_readiness(project, ("alignment",))
    assert not any(issue.code == "coincident_links" for issue in issues)


def test_repeated_pixel_links_are_not_treated_as_independent_geometry():
    project = Project()
    first, second = [project.add_image(name) for name in ("one.png", "two.png")]
    for index in range(2):
        source = add_point(project, first, f"first-{index}", (10, 20))
        target = add_point(project, second, f"second-{index}", (40 + index * 10, 50))
        project.add_constraint(PointConstraint.create(source.id, target.id))
    issues = check_readiness(project, ("alignment",))
    assert any(issue.code == "coincident_links" for issue in issues)
    assert not any(issue.code == "minimal_links" for issue in issues)


def test_invalid_linked_frame_is_blocking_for_both_stages():
    project = Project()
    first, second = [project.add_image(name) for name in ("one.png", "two.png")]
    source = add_point(project, first, "one", (10, 20), (100, 200))
    target = add_point(project, second, "two", (40, 50))
    project.add_constraint(PointConstraint.create(source.id, target.id))
    second.scale = 0
    issues = check_readiness(project)
    assert {(issue.stage, issue.image_ids) for issue in issues if issue.code == "invalid_frame"} == {
        ("alignment", (second.id,)), ("georeference", (second.id,)),
    }


def test_duplicate_geographic_targets_warn_but_do_not_block():
    project = Project()
    image = project.add_image("image.png")
    for index in range(3):
        add_point(project, image, str(index), (10 + index * 100, 20), (100, 200))
    issues = check_readiness(project, ("georeference",))
    assert any(issue.code == "coincident_geographic_targets" for issue in issues)
    assert not any(issue.severity == "Error" for issue in issues)