from geostitch.models import GroundControlPoint, PointConstraint, Project


def test_image_rest_lock_round_trips_and_legacy_defaults_unlocked():
    project = Project()
    image = project.add_image("locked.png")
    image.rest_locked = True
    assert Project.from_dict(project.to_dict()).images[0].rest_locked
    legacy = project.to_dict()
    legacy["version"] = 12
    del legacy["images"][0]["rest_locked"]
    legacy["images"][0].pop("native_world_mapping")
    assert not Project.from_dict(legacy).images[0].rest_locked


def test_linked_point_stores_one_observation_per_image():
    project = Project()
    first_image = project.add_image("survey-a.tif")
    second_image = project.add_image("survey-b.tif")
    point = GroundControlPoint.create("bridge")
    project.add_control_point(point)

    point.set_observation(first_image.id, 12.5, 40.0)
    point.set_observation(second_image.id, 8.0, 21.5)
    point.set_observation(first_image.id, 13.0, 41.0)

    assert len(point.observations) == 2
    assert point.observation_for(first_image.id).x == 13.0
    assert point.observation_for(second_image.id).y == 21.5


def test_project_round_trips_linked_points_and_coordinates():
    project = Project(name="coast survey", crs="EPSG:32610")
    image = project.add_image("coast.tif")
    point = GroundControlPoint.create("pier", "Old harbor pier")
    point.geographic_x = 500_000.0
    point.geographic_y = 5_400_000.0
    point.set_observation(image.id, 122.0, 350.0)
    project.add_control_point(point)

    restored = Project.from_dict(project.to_dict())

    assert restored.name == "coast survey"
    assert restored.crs == "EPSG:32610"
    assert restored.control_points[0].geographic_x == 500_000.0
    assert restored.control_points[0].label == "Old harbor pier"
    assert restored.control_points[0].observations[0].image_id == image.id


def test_geographic_checkpoint_role_round_trips_and_legacy_defaults_to_fit_control():
    project = Project(crs="EPSG:4326")
    image = project.add_image("checkpoint.png")
    point = GroundControlPoint.create("check-1")
    point.set_observation(image.id, 10, 20)
    point.set_geographic_coordinates(12.0, 45.0, project.crs)
    point.geographic_checkpoint = True
    project.add_control_point(point)

    saved = project.to_dict()
    restored = Project.from_dict(saved)
    assert saved["version"] == 26
    assert restored.point_by_id(point.id).geographic_checkpoint

    legacy = dict(saved)
    legacy["version"] = 25
    legacy["control_points"] = [dict(saved_point) for saved_point in saved["control_points"]]
    legacy["control_points"][0].pop("geographic_checkpoint")
    assert not Project.from_dict(legacy).point_by_id(point.id).geographic_checkpoint


def test_geographic_checkpoint_role_survives_shared_point_migration():
    project = Project(crs="EPSG:4326")
    first, second = [project.add_image(name) for name in ("first.png", "second.png")]
    point = GroundControlPoint.create("legacy-checkpoint")
    point.geographic_checkpoint = True
    point.set_geographic_coordinates(12.0, 45.0, project.crs)
    point.set_observation(first.id, 10, 20)
    point.set_observation(second.id, 30, 40)
    project.control_points.append(point)

    restored = Project.from_dict(project.to_dict())

    assert len(restored.control_points) == 2
    assert all(item.geographic_checkpoint for item in restored.control_points)
    assert {item.image_id for item in restored.control_points} == {first.id, second.id}


def test_default_control_point_names_increase_per_image():
    project = Project()
    first_image = project.add_image("survey-a.tif")
    second_image = project.add_image("survey-b.tif")

    first_name = project.next_control_point_name(first_image.id)
    first_point = GroundControlPoint.create(first_name)
    project.add_control_point(first_point)
    first_point.set_observation(first_image.id, 10.0, 20.0)

    assert first_name == "P1"
    assert project.next_control_point_name(first_image.id) == "P2"
    assert project.next_control_point_name(second_image.id) == "P1"

    second_point = GroundControlPoint.create(project.next_control_point_name(second_image.id), "  Gate  ")
    assert second_point.label == "Gate"


def test_auto_name_skips_an_existing_custom_name():
    project = Project()
    image = project.add_image("map.tif")
    custom = GroundControlPoint.create("P1")
    custom.set_observation(image.id, 0, 0)
    project.add_control_point(custom)

    assert project.next_control_point_name(image.id) == "P2"


def test_compact_point_aliases_preserve_unique_names_and_custom_names():
    project = Project()
    images = [project.add_image(name) for name in ("first.png", "second.png")]
    for image in images:
        short_name = project.next_control_point_name(image.id)
        point = GroundControlPoint.create(project.unique_control_point_name(short_name))
        point.short_name = short_name
        point.set_observation(image.id, 10, 20)
        project.add_control_point(point)
    assert [point.display_name for point in project.control_points] == ["P1", "P1"]
    assert len({point.name for point in project.control_points}) == 2
    custom = GroundControlPoint.create("North gate")
    assert custom.display_name == "North gate"
    restored = Project.from_dict(project.to_dict())
    assert [point.display_name for point in restored.control_points] == ["P1", "P1"]


def test_link_numbers_are_stable_and_not_reused_after_deletion():
    project = Project()
    points = [GroundControlPoint.create(name) for name in ("first", "second", "third")]
    for point in points:
        project.add_control_point(point)
    first = PointConstraint.create(points[0].id, points[1].id)
    project.add_constraint(first)
    assert first.name == "L1"
    project.constraints.remove(first)
    restored = Project.from_dict(project.to_dict())
    second = PointConstraint.create(points[0].id, points[2].id)
    restored.add_constraint(second)
    assert second.name == "L2"


def test_duplicate_control_point_names_are_rejected_case_insensitively():
    project = Project()
    project.add_control_point(GroundControlPoint.create("North gate"))

    try:
        project.add_control_point(GroundControlPoint.create("north GATE"))
    except ValueError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("Duplicate point name should have been rejected")


def test_image_transforms_and_constraints_round_trip():
    project = Project()
    image = project.add_image("mosaic-part.tif")
    image.position_x = 425.0
    image.position_y = -18.0
    image.rotation = 7.5
    image.scale = 1.25
    first = GroundControlPoint.create("west tie")
    second = GroundControlPoint.create("east tie")
    project.add_control_point(first)
    project.add_control_point(second)
    constraint = PointConstraint.create(first.id, second.id)
    project.add_constraint(constraint)

    restored = Project.from_dict(project.to_dict())

    assert restored.images[0].position_x == 425.0
    assert restored.images[0].position_y == -18.0
    assert restored.images[0].rotation == 7.5
    assert restored.images[0].scale == 1.25
    assert restored.constraints[0].first_point_id == first.id
    assert restored.constraints[0].second_point_id == second.id


def test_constraint_rejects_self_links_and_duplicate_pairs():
    project = Project()
    first = GroundControlPoint.create("one")
    second = GroundControlPoint.create("two")
    project.add_control_point(first)
    project.add_control_point(second)

    try:
        project.add_constraint(PointConstraint.create(first.id, first.id))
    except ValueError as error:
        assert "itself" in str(error)
    else:
        raise AssertionError("Self constraints should have been rejected")

    project.add_constraint(PointConstraint.create(first.id, second.id))
    try:
        project.add_constraint(PointConstraint.create(second.id, first.id))
    except ValueError as error:
        assert "already connected" in str(error)
    else:
        raise AssertionError("Duplicate symmetric constraints should have been rejected")


def test_version_one_projects_load_with_default_layer_transforms():
    project = Project.from_dict({
        "version": 1,
        "name": "legacy",
        "crs": "EPSG:4326",
        "images": [{"id": "image-1", "path": "legacy.png", "name": "legacy.png"}],
        "control_points": [],
    })

    assert project.images[0].position_x is None
    assert project.images[0].position_y == 0.0
    assert project.images[0].rotation == 0.0
    assert project.images[0].scale == 1.0
    assert project.images[0].world_x is None
    assert project.images[0].world_y is None
    assert project.images[0].world_rotation == 0.0
    assert project.images[0].world_scale == 1.0
    assert project.constraints == []


def test_project_serialization_is_an_independent_snapshot():
    project = Project()
    image = project.add_image("snapshot.png")
    point = GroundControlPoint.create("snapshot-001")
    point.set_observation(image.id, 10.0, 20.0)
    project.add_control_point(point)
    snapshot = project.to_dict()

    image.position_x = 55.0
    point.observations[0].x = 44.0

    assert snapshot["images"][0]["mappings"]["rest"]["pose"][0] is None
    assert snapshot["control_points"][0]["observations"][0]["x"] == 10.0


def test_observation_lock_round_trips_and_legacy_projects_default_unlocked():
    project = Project()
    image = project.add_image("locked.png")
    point = GroundControlPoint.create("locked-001")
    point.set_observation(image.id, 12.0, 34.0)
    point.observation_for(image.id).locked = True
    project.add_control_point(point)

    restored = Project.from_dict(project.to_dict())
    assert restored.control_points[0].observation_for(image.id).locked

    legacy = Project.from_dict({
        "version": 3,
        "images": [{"id": image.id, "path": image.path, "name": image.name}],
        "control_points": [{
            "id": "legacy-point",
            "name": "legacy",
            "observations": [{"image_id": image.id, "x": 1.0, "y": 2.0}],
        }],
    })
    assert not legacy.control_points[0].observation_for(image.id).locked

    locked_point = restored.control_points[0]
    try:
        locked_point.set_observation(image.id, 90.0, 91.0)
    except ValueError as error:
        assert "locked" in str(error)
    else:
        raise AssertionError("A locked image observation must not be moved")


def test_unique_control_point_name_increments_trailing_number():
    from geostitch.models import GroundControlPoint, Project

    project = Project()
    for name in ("gcp-009", "gcp-010", "corner"):
        project.add_control_point(GroundControlPoint.create(name))

    assert project.unique_control_point_name("fresh") == "fresh"
    assert project.unique_control_point_name("GCP-009") == "GCP-011"
    assert project.unique_control_point_name("corner") == "corner-2"


def test_loading_legacy_shared_point_splits_it_into_linked_per_image_points():
    from geostitch.models import Project

    legacy = {
        "version": 8,
        "images": [
            {"id": "a", "path": "a.png", "name": "a.png"},
            {"id": "b", "path": "b.png", "name": "b.png"},
        ],
        "control_points": [{
            "id": "p", "name": "gcp", "geographic_x": 1.0, "geographic_y": 2.0,
            "observations": [{"image_id": "a", "x": 1.0, "y": 2.0}, {"image_id": "b", "x": 3.0, "y": 4.0}],
        }],
    }

    project = Project.from_dict(legacy)

    assert [point.image_id for point in project.control_points] == ["a", "b"]
    assert project.control_points[1].name == "gcp-b"
    assert project.control_points[1].geographic_x == 1.0
    assert len(project.constraints) == 1
    assert {project.constraints[0].first_point_id, project.constraints[0].second_point_id} == {
        point.id for point in project.control_points
    }


def test_layer_visibility_round_trips_and_defaults_to_visible():
    project = Project()
    image = project.add_image('layer.png')
    point = GroundControlPoint.create('p')
    project.add_control_point(point)
    image.visible = False
    point.visible = False
    data = project.to_dict()
    loaded = Project.from_dict(data)
    assert loaded.images[0].visible is False
    assert loaded.control_points[0].visible is False
    del data['control_points'][0]['visible']
    del data['images'][0]['visible']
    legacy = Project.from_dict(data)
    assert legacy.images[0].visible and legacy.control_points[0].visible
