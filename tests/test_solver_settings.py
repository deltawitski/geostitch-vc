import pytest
from pyproj import CRS

from geostitch.georeference import (
    GeoreferenceResult, georeference_distance_unit, rebase_georeference_settings,
)
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
        affine_min_stretch=0.1, affine_max_stretch=10, affine_max_condition=50, affine_allow_reflection=True,
    )
    path = tmp_path / "advanced.gsp"
    save_project(project, path)
    assert load_project(path).alignment_settings == project.alignment_settings


def test_rebase_georeference_distance_settings_between_projected_units():
    settings = SolverSettings(
        tolerance=2.0, loss_scale=3.0, transform_model="affine",
        scale_min=0.5, scale_max=4.0, affine_min_stretch=0.25,
        affine_max_stretch=8.0, projective_min_stretch=0.125,
        projective_max_stretch=16.0,
    )

    rebased = rebase_georeference_settings(settings, "EPSG:32610", "EPSG:2263")

    factor = 1 / CRS.from_epsg(2263).axis_info[0].unit_conversion_factor
    assert rebased is not None
    assert rebased.tolerance == pytest.approx(settings.tolerance * factor)
    assert rebased.loss_scale == pytest.approx(settings.loss_scale * factor)
    assert rebased.scale_min == pytest.approx(settings.scale_min * factor)
    assert rebased.scale_max == pytest.approx(settings.scale_max * factor)
    assert rebased.affine_min_stretch == pytest.approx(settings.affine_min_stretch * factor)
    assert rebased.affine_max_stretch == pytest.approx(settings.affine_max_stretch * factor)
    assert rebased.projective_min_stretch == pytest.approx(settings.projective_min_stretch * factor)
    assert rebased.projective_max_stretch == pytest.approx(settings.projective_max_stretch * factor)


def test_rebase_local_metric_geographic_settings_to_projected_metres_is_identity():
    settings = SolverSettings(transform_model="projective", tolerance=0.25, loss_scale=1.5)

    rebased = rebase_georeference_settings(settings, "EPSG:4326", "EPSG:32610")
    rebased_feet = rebase_georeference_settings(settings, "EPSG:4326", "EPSG:2263")

    assert rebased == settings
    assert rebased_feet is not None
    assert rebased_feet.tolerance == pytest.approx(
        settings.tolerance / CRS.from_epsg(2263).axis_info[0].unit_conversion_factor,
    )


def test_rebase_refuses_exact_conversion_between_angular_and_linear_units():
    settings = SolverSettings(transform_model="similarity", tolerance=0.25, loss_scale=1.5)

    assert rebase_georeference_settings(settings, "EPSG:4326", "EPSG:3857") is None


def test_effective_georeference_units_name_crs_units_and_local_metric_frame():
    assert georeference_distance_unit("EPSG:2263", "similarity").label == "US survey feet"
    assert georeference_distance_unit("EPSG:4326", "similarity").label == "degrees"
    assert georeference_distance_unit("EPSG:4326", "affine").label == "local metres"
    result = GeoreferenceResult(
        {}, {}, 1, True, "EPSG:2263", settings=SolverSettings(),
    )
    assert result.residual_units == "US survey feet"


def test_projective_solver_settings_round_trip(tmp_path):
    project = Project()
    project.alignment_settings = SolverSettings(
        transform_model="projective", projective_min_stretch=0.1, projective_max_stretch=8,
        projective_max_condition=40, projective_horizon_margin=0.05, projective_allow_reflection=True,
    )
    path = tmp_path / "projective-settings.gsp"
    save_project(project, path)
    restored = load_project(path)
    assert restored.to_dict()["version"] == 26
    assert restored.alignment_settings == project.alignment_settings


def test_tps_solver_settings_round_trip(tmp_path):
    project = Project()
    project.georeference_settings = SolverSettings(
        transform_model="tps", warp_smoothing=0.025,
        warp_min_relative_area=0.08, warp_max_relative_stretch=6.0,
    )
    path = tmp_path / "tps-settings.gsp"

    save_project(project, path)

    restored = load_project(path)
    assert restored.to_dict()["version"] == 26
    assert restored.georeference_settings == project.georeference_settings


def test_existing_three_field_settings_keep_advanced_defaults():
    data = Project().to_dict()
    data["version"] = 21
    data["solver_settings"]["alignment"] = {"tolerance": 0.25, "max_iterations": 321, "auto_apply": False}
    assert Project.from_dict(data).alignment_settings == SolverSettings(0.25, 321, False)


@pytest.mark.parametrize("options", [
    {"optimizer_method": "lm"}, {"loss": "unknown"},
    {"parameter_scaling": "unknown"}, {"loss_scale": 0}, {"loss_scale": True},
    {"affine_min_stretch": 2, "affine_max_stretch": 1}, {"affine_max_condition": 0.5},
    {"affine_allow_reflection": "yes"},
    {"projective_min_stretch": 2, "projective_max_stretch": 1},
    {"projective_max_condition": 0.5}, {"projective_horizon_margin": 1.0},
    {"projective_allow_reflection": "yes"},
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