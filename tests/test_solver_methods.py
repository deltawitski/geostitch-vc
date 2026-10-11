from math import cos, radians, sin
from types import SimpleNamespace

import numpy as np
import pytest

from geostitch.adjustment import simulate_alignment
from geostitch.georeference import simulate_georeference
from geostitch.models import GroundControlPoint, PointConstraint, Project, SolverSettings


def fitting_project(stage, transform, outlier=False):
    project = Project(crs="EPSG:26910")
    reference = project.add_image("reference.png") if stage == "alignment" else None
    moving = project.add_image("moving.png")
    pixels = [(10, 20), (100, 20), (20, 100), (110, 110), (60, 40), (40, 70)]
    angle = radians(transform[2])
    for index, (pixel_x, pixel_y) in enumerate(pixels):
        source_y = pixel_y if stage == "alignment" else -pixel_y
        target_x = transform[0] + transform[3] * (cos(angle) * pixel_x - sin(angle) * source_y)
        target_y = transform[1] + transform[3] * (sin(angle) * pixel_x + cos(angle) * source_y)
        if outlier and index == len(pixels) - 1:
            target_x += 100
        point = GroundControlPoint.create(f"moving-{index}")
        point.set_observation(moving.id, pixel_x, pixel_y)
        if stage == "georeference":
            point.geographic_x, point.geographic_y = target_x, target_y
        project.add_control_point(point)
        if reference is not None:
            fixed = GroundControlPoint.create(f"reference-{index}")
            fixed.set_observation(reference.id, target_x, target_y)
            project.add_control_point(fixed)
            project.add_constraint(PointConstraint.create(fixed.id, point.id))
    return project, moving


@pytest.mark.parametrize("stage, solve", [("alignment", simulate_alignment), ("georeference", simulate_georeference)])
@pytest.mark.parametrize("method", ["trf", "dogbox"])
@pytest.mark.parametrize("model", ["translation", "rigid", "similarity"])
def test_transform_model_recovers_allowed_parameters_and_preserves_locked_ones(stage, solve, method, model):
    target = (140.0, -35.0, 12.0 if model == "translation" else 22.0, 1.2)
    project, moving = fitting_project(stage, target)
    initial = (0.0, 0.0, 12.0 if model == "translation" else 0.0, 1.0 if model == "similarity" else 1.2)
    config = SolverSettings(tolerance=0.001, max_iterations=1000, transform_model=model, optimizer_method=method)

    result = solve(project, settings=config, initial_transforms={moving.id: initial})

    assert result.converged
    assert result.transforms[moving.id] == pytest.approx(target, abs=1e-5)
    assert result.settings == config
    if model == "translation":
        assert result.transforms[moving.id][2:] == initial[2:]
    elif model == "rigid":
        assert result.transforms[moving.id][3] == initial[3]


@pytest.mark.parametrize("stage, solve", [("alignment", simulate_alignment), ("georeference", simulate_georeference)])
def test_similarity_scale_limits_are_enforced(stage, solve):
    project, moving = fitting_project(stage, (140, -35, 12, 2))
    config = SolverSettings(tolerance=0.001, scale_min=0.8, scale_max=1.2)

    result = solve(project, settings=config)

    assert 0.8 <= result.transforms[moving.id][3] <= 1.2
    assert result.transforms[moving.id][3] == pytest.approx(1.2)
    assert not result.converged
    assert result.maximum_residual > config.tolerance


@pytest.mark.parametrize("stage, solve", [("alignment", simulate_alignment), ("georeference", simulate_georeference)])
@pytest.mark.parametrize("loss", ["soft_l1", "huber", "cauchy"])
def test_robust_loss_reduces_outlier_influence_but_keeps_raw_residuals(stage, solve, loss):
    target = (10, 20, 0, 1)
    project, moving = fitting_project(stage, target, outlier=True)
    linear = solve(project, settings=SolverSettings(transform_model="translation"))
    robust = solve(project, settings=SolverSettings(transform_model="translation", loss=loss, loss_scale=1))

    assert abs(robust.transforms[moving.id][0] - target[0]) < 1
    assert abs(linear.transforms[moving.id][0] - target[0]) > 10
    assert robust.maximum_residual > 90
    assert not robust.converged


@pytest.mark.parametrize("stage, solve", [("alignment", simulate_alignment), ("georeference", simulate_georeference)])
def test_optimizer_receives_advanced_controls(stage, solve, monkeypatch):
    project, moving = fitting_project(stage, (10, 20, 0, 1))
    config = SolverSettings(
        optimizer_method="dogbox", loss="huber", loss_scale=3, ftol=1e-8, xtol=1e-7, gtol=1e-6,
        parameter_scaling="jacobian", max_iterations=222,
    )
    calls = []

    def optimize(function, values, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(x=np.asarray(values), nfev=1, success=True, status=1, message="gtol")

    monkeypatch.setattr("scipy.optimize.least_squares", optimize)
    solve(project, settings=config)
    assert len(calls) == 1
    assert {key: calls[0][key] for key in ("method", "loss", "f_scale", "ftol", "xtol", "gtol", "x_scale", "max_nfev")} == {
        "method": "dogbox", "loss": "huber", "f_scale": 3, "ftol": 1e-8, "xtol": 1e-7, "gtol": 1e-6,
        "x_scale": "jac", "max_nfev": 222,
    }


@pytest.mark.parametrize("stage, solve", [("alignment", simulate_alignment), ("georeference", simulate_georeference)])
def test_real_solver_result_retains_known_source_dimensions(stage, solve):
    project, moving = fitting_project(stage, (10, 20, 0, 1))
    moving.source_dimensions = (3000, 2000)
    result = solve(project)
    assert result.converged
    assert result.source_dimensions[moving.id] == (3000, 2000)
    assert result.mapping_records[moving.id].source_size == (3000, 2000)
    moving.source_dimensions = (2000, 3000)
    assert result.mapping_records[moving.id].source_size == (3000, 2000)