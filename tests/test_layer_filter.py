import pytest

from geostitch.layer_filter import match_layers
from geostitch.models import GroundControlPoint, PointConstraint, Project, VectorLayer


def make_project():
    project = Project()
    images = [project.add_image(name) for name in ("North.png", "South.png", "Empty.png")]
    points = []
    for index, image in enumerate((images[0], images[0], images[1])):
        point = GroundControlPoint.create(f"unique-{index}")
        point.short_name = f"P{index + 1}"
        point.label = "Bridge" if index == 0 else None
        point.set_observation(image.id, 10, 20)
        project.add_control_point(point)
        points.append(point)
    points[0].geographic_x, points[0].geographic_y = 100, 200
    points[1].geographic_x = 300
    link = PointConstraint.create(points[0].id, points[2].id)
    project.add_constraint(link)
    project.vector_layers.append(VectorLayer("vector", "roads.geojson", "Roads"))
    return project, images, points, link


def test_empty_filter_matches_all_items_without_mutation():
    project, images, points, link = make_project()
    before = project.to_dict()
    matches = match_layers(project)
    assert matches.image_ids == {image.id for image in images}
    assert matches.point_ids == {point.id for point in points}
    assert matches.vector_ids == {"vector"}
    assert matches.link_rows == {(link.id, points[0].id), (link.id, points[2].id)}
    assert project.to_dict() == before


def test_search_matches_names_labels_and_retains_link_endpoint_context():
    project, images, points, link = make_project()
    matches = match_layers(project, "BRIDGE")
    assert matches.point_ids == {points[0].id, points[2].id}
    assert matches.image_ids == {images[0].id, images[1].id}
    assert points[1].id not in matches.point_ids
    assert match_layers(project, "unique-1").point_ids == {points[1].id}
    assert match_layers(project, "p2").point_ids == {points[1].id}
    assert match_layers(project, "roads").vector_ids == {"vector"}


def test_image_scope_applies_with_status_and_search():
    project, images, points, link = make_project()
    matches = match_layers(project, "north", "missing_geo", images[0].id)
    assert matches.point_ids == {points[1].id}
    assert matches.image_ids == {images[0].id}
    assert not matches.vector_ids
    assert match_layers(project, image_id=images[2].id).image_ids == {images[2].id}


@pytest.mark.parametrize("status,indices", [
    ("missing_geo", [1, 2]), ("with_geo", [0]), ("unlinked", [1]), ("linked", [0, 2]),
])
def test_point_status_filters(status, indices):
    project, images, points, link = make_project()
    matches = match_layers(project, status=status)
    assert matches.point_ids == {points[index].id for index in indices}
    assert images[2].id not in matches.image_ids
    assert not matches.vector_ids


def test_link_name_and_type_search_shows_both_endpoints():
    project, images, points, link = make_project()
    link.kind = "check"
    assert match_layers(project, link.name).point_ids == {points[0].id, points[2].id}
    assert match_layers(project, "check").link_rows == {(link.id, points[0].id), (link.id, points[2].id)}


def test_unknown_status_is_rejected():
    with pytest.raises(ValueError):
        match_layers(Project(), status="unknown")