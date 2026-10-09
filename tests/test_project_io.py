from geostitch.models import GroundControlPoint, Project
from geostitch.project_io import load_project, save_project


def test_project_file_round_trip(tmp_path):
    project = Project(name="field work", crs="EPSG:26910")
    image = project.add_image(tmp_path / "orthophoto.tif")
    point = GroundControlPoint.create("road crossing")
    point.geographic_x = 512345.25
    point.geographic_y = 5456789.5
    point.set_observation(image.id, 280.0, 143.5)
    project.add_control_point(point)
    project_path = tmp_path / "survey.gsp"

    save_project(project, project_path)
    restored = load_project(project_path)

    assert restored.to_dict() == project.to_dict()


def test_project_round_trips_view_state(tmp_path):
    from geostitch.project_io import load_project, save_project
    from geostitch.models import Project

    project = Project()
    project.views = {"canvas": {"center": [1.5, 2.5], "scale": 0.5}, "map": {"center": [10.0, -20.0], "scale": 0.001}}
    path = tmp_path / "views.gsp"
    save_project(project, path)

    assert load_project(path).views == project.views
