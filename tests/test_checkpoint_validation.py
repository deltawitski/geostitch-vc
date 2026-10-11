import pytest

from geostitch.georeference import simulate_georeference
from geostitch.models import GroundControlPoint, Project, SolverSettings
from geostitch.transforms import MatrixTransform


@pytest.mark.parametrize("model", ["affine", "projective"])
def test_georeference_checkpoint_is_excluded_and_reported_separately(model):
    project = Project(crs="EPSG:32610")
    image = project.add_image("checkpoint-survey.png")
    image.source_dimensions = (120, 100)
    if model == "affine":
        truth = MatrixTransform.from_affine((2.0, 0.1, 500000.0, 0.05, 1.5, 4000000.0))
    else:
        truth = MatrixTransform((
            1.2, 0.08, 500000.0, 0.04, 1.1, 4000000.0,
            0.00000002, -0.00000001, 1.0,
        ))
    locations = ((10, 10), (110, 10), (10, 90), (110, 90), (60, 50), (40, 30))
    training_points = []
    for index, (pixel_x, pixel_y) in enumerate(locations):
        point = GroundControlPoint.create(f"fit-{index}")
        point.set_observation(image.id, pixel_x, pixel_y)
        point.set_geographic_coordinates(*truth.map_point(pixel_x, -pixel_y), project.crs)
        project.add_control_point(point)
        training_points.append(point)

    checkpoint = GroundControlPoint.create("independent-check")
    checkpoint_pixel = (72.0, 46.0)
    checkpoint.set_observation(image.id, *checkpoint_pixel)
    expected = truth.map_point(checkpoint_pixel[0], -checkpoint_pixel[1])
    checkpoint.set_geographic_coordinates(expected[0] + 3.0, expected[1] - 4.0, project.crs)
    checkpoint.geographic_checkpoint = True
    project.add_control_point(checkpoint)

    settings = SolverSettings(transform_model=model, tolerance=1e-4, max_iterations=2000)
    result = simulate_georeference(project, settings=settings)
    key = (image.id, checkpoint.id)

    assert result.converged
    assert len(result.observation_residuals) == len(training_points)
    assert key not in result.observation_residuals
    assert result.checkpoint_residuals[key] == pytest.approx(5.0, abs=0.02)
    assert result.maximum_residual < settings.tolerance
    assert result.maximum_checkpoint_error > settings.tolerance
