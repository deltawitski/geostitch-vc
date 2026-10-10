import pytest

from geostitch.models import Project, SolverSettings
from geostitch.project_io import load_project, save_project


def test_solver_settings_round_trip_with_project(tmp_path):
    project = Project()
    project.alignment_settings = SolverSettings(0.25, 250, False)
    project.georeference_settings = SolverSettings(0.75, 1500, True)
    path = tmp_path / "solver-settings.gsp"
    save_project(project, path)
    restored = load_project(path)
    assert restored.alignment_settings == project.alignment_settings
    assert restored.georeference_settings == project.georeference_settings
    assert restored.to_dict() == project.to_dict()


def test_legacy_project_gets_solver_defaults():
    data = Project().to_dict()
    data["version"] = 14
    data.pop("solver_settings", None)
    restored = Project.from_dict(data)
    assert restored.alignment_settings == SolverSettings(0.5, 100, True)
    assert restored.georeference_settings == SolverSettings(1.0, 1000, True)


@pytest.mark.parametrize("values", [
    (0, 100, True), (float("nan"), 100, True),
    (0.5, 0, True), (0.5, 1.5, True), (0.5, 100, "yes"),
])
def test_solver_settings_reject_invalid_values(values):
    with pytest.raises(ValueError):
        SolverSettings(*values)