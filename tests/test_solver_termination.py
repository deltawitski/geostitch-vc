from types import SimpleNamespace

import numpy as np
import pytest

from geostitch.adjustment import simulate_alignment
from geostitch.georeference import simulate_georeference
from geostitch.models import GroundControlPoint, PointConstraint, Project


def linked_project():
    project = Project()
    images = [project.add_image(f"image-{index}.png") for index in range(3)]
    for index, (source_index, target_index, translation) in enumerate(
        [(0, 1, 10.0), (1, 2, 10.0), (0, 2, 25.0)]
    ):
        source = GroundControlPoint.create(f"source-{index}")
        target = GroundControlPoint.create(f"target-{index}")
        source.set_observation(images[source_index].id, 0.0, 0.0)
        target.set_observation(images[target_index].id, -translation, 0.0)
        source.geographic_x, source.geographic_y = 1000.0, 2000.0
        project.add_control_point(source)
        project.add_control_point(target)
        project.add_constraint(PointConstraint.create(source.id, target.id))
    return project


@pytest.mark.parametrize("solve", [simulate_alignment, simulate_georeference])
@pytest.mark.parametrize("status", [0, 1, 2, 3, 4])
def test_solver_preserves_optimizer_termination(monkeypatch, solve, status):
    def optimize(residual_vector, values, **kwargs):
        assert kwargs["max_nfev"] == 321
        return SimpleNamespace(
            x=np.asarray(values), nfev=7, success=status > 0,
            status=status, message=f"Termination status {status}",
        )

    monkeypatch.setattr("scipy.optimize.least_squares", optimize)
    result = solve(linked_project(), tolerance=0.01, max_iterations=321)

    assert result.optimizer_status == status
    assert result.optimizer_message == f"Termination status {status}"
    assert result.max_iterations == 321
    assert result.iterations == 7


def test_stationary_solution_can_miss_target_before_budget_is_exhausted():
    result = simulate_alignment(linked_project(), tolerance=0.01, max_iterations=1000)

    assert not result.converged
    assert result.maximum_residual == pytest.approx(5.0 / 3.0)
    assert result.iterations < result.max_iterations
    assert result.optimizer_status == 1
    assert "gtol" in result.optimizer_message