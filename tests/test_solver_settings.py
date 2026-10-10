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


def test_advanced_solver_settings_round_trip(tmp_path):
    project = Project()
    project.alignment_settings = SolverSettings(
        transform_model="rigid", optimizer_method="dogbox", loss="huber", loss_scale=2.5,
        scale_min=0.2, scale_max=5.0, ftol=1e-8, xtol=1e-9, gtol=1e-7, parameter_scaling="jacobian",
    )
    path = tmp_path / "advanced.gsp"
    save_project(project, path)
    assert load_project(path).alignment_settings == project.alignment_settings


def test_existing_three_field_settings_keep_advanced_defaults():
    data = Project().to_dict()
    data["solver_settings"]["alignment"] = {"tolerance": 0.25, "max_iterations": 321, "auto_apply": False}
    assert Project.from_dict(data).alignment_settings == SolverSettings(0.25, 321, False)


@pytest.mark.parametrize("options", [
    {"transform_model": "affine"}, {"optimizer_method": "lm"}, {"loss": "unknown"},
    {"parameter_scaling": "unknown"}, {"loss_scale": 0}, {"loss_scale": True},
    {"ftol": 1e-16}, {"xtol": float("nan")}, {"gtol": -1},
    {"scale_min": 0}, {"scale_max": float("inf")}, {"scale_min": 2, "scale_max": 1},
])
def test_advanced_solver_settings_reject_invalid_values(options):
    with pytest.raises(ValueError):
        SolverSettings(**options)


@pytest.mark.parametrize("values", [
    (0, 100, True), (float("nan"), 100, True),
    (0.5, 0, True), (0.5, 1.5, True), (0.5, 100, "yes"),
])
def test_solver_settings_reject_invalid_values(values):
    with pytest.raises(ValueError):
        SolverSettings(*values)