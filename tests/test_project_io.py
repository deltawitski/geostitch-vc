import pytest

from geostitch.alignment_feedback import AlignmentFeedback
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


def test_save_preserves_previous_project_as_backup(tmp_path):
    path = tmp_path / "survey.gsp"
    save_project(Project(name="Original"), path)
    assert not path.with_suffix(".gsp.bak").exists()
    save_project(Project(name="Updated"), path)
    assert load_project(path).name == "Updated"
    assert load_project(path.with_suffix(".gsp.bak")).name == "Original"
    save_project(Project(name="Latest"), path)
    assert load_project(path.with_suffix(".gsp.bak")).name == "Updated"


def test_failed_atomic_replace_preserves_original_and_cleans_temporary_files(tmp_path, monkeypatch):
    import geostitch.project_io as project_io

    path = tmp_path / "survey.gsp"
    save_project(Project(name="Original"), path)
    replace = project_io.os.replace

    def fail_project_replace(source, destination):
        if destination == path:
            raise OSError("Simulated replacement failure")
        replace(source, destination)

    monkeypatch.setattr(project_io.os, "replace", fail_project_replace)
    with pytest.raises(OSError, match="replacement failure"):
        save_project(Project(name="Updated"), path)
    assert load_project(path).name == "Original"
    assert load_project(path.with_suffix(".gsp.bak")).name == "Original"
    assert not list(tmp_path.glob("*.tmp"))


def test_recovery_save_can_skip_previous_version_backup(tmp_path):
    path = tmp_path / "recovery.gsp"
    save_project(Project(name="First"), path, backup=False)
    save_project(Project(name="Second"), path, backup=False)
    assert load_project(path).name == "Second"
    assert not path.with_suffix(".gsp.bak").exists()


def test_project_round_trips_view_state(tmp_path):
    from geostitch.project_io import load_project, save_project
    from geostitch.models import Project

    project = Project()
    project.views = {"canvas": {"center": [1.5, 2.5], "scale": 0.5}, "map": {"center": [10.0, -20.0], "scale": 0.001}}
    path = tmp_path / "views.gsp"
    save_project(project, path)

    assert load_project(path).views == project.views


@pytest.mark.parametrize("outdated", [False, True])
def test_project_round_trips_examined_alignment_feedback(tmp_path, outdated):
    from test_alignment_feedback import make_feedback_project

    project, images, points, links, result = make_feedback_project()
    feedback = AlignmentFeedback.capture(project, result, 0.25)
    project.alignment_feedback = feedback
    if outdated:
        points[0].observations[0].x += 1
    path = tmp_path / "examined-errors.gsp"
    save_project(project, path)
    restored = load_project(path)
    saved_feedback = restored.alignment_feedback

    assert isinstance(saved_feedback, AlignmentFeedback)
    assert restored.to_dict()["version"] == 17
    assert restored.to_dict() == project.to_dict()
    assert saved_feedback.result == result
    assert saved_feedback.tolerance == 0.25
    assert saved_feedback.point_residuals == feedback.point_residuals
    assert saved_feedback.image_residuals == feedback.image_residuals
    assert saved_feedback.is_current(restored) is not outdated


@pytest.mark.parametrize("version", range(1, 18))
def test_older_projects_without_alignment_feedback_still_load(version):
    data = Project().to_dict()
    data["version"] = version
    data.pop("alignment_feedback", None)
    restored = Project.from_dict(data)
    assert restored.alignment_feedback is None


@pytest.mark.parametrize("feedback", [[], "report", {}, {"version": 2}])
def test_project_rejects_malformed_alignment_feedback(feedback):
    data = Project().to_dict()
    data["alignment_feedback"] = feedback
    with pytest.raises(ValueError):
        Project.from_dict(data)


def test_project_rejects_unsupported_version():
    data = Project().to_dict()
    data["version"] = 18
    with pytest.raises(ValueError):
        Project.from_dict(data)
