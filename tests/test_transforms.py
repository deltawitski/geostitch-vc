from math import cos, radians, sin

import pytest

from geostitch.transforms import MatrixTransform, apply_similarity_transform


@pytest.mark.parametrize("pose", [(0, 0, 0, 1), (500000, 5400000, 23, 2.3), (-1e7, 2e7, -120, 0.001)])
@pytest.mark.parametrize("pixel_y_down", [False, True])
def test_similarity_mapping_preserves_legacy_geometry_and_inverse(pose, pixel_y_down):
    mapping = MatrixTransform.from_similarity(pose, pixel_y_down=pixel_y_down)
    angle = radians(pose[2])
    source = (1234.5, 678.25)
    source_y = -source[1] if pixel_y_down else source[1]
    expected = (
        pose[0] + pose[3] * (cos(angle) * source[0] - sin(angle) * source_y),
        pose[1] + pose[3] * (sin(angle) * source[0] + cos(angle) * source_y),
    )
    assert mapping.map_point(*source) == pytest.approx(expected, abs=1e-8)
    assert mapping.inverse().map_point(*expected) == pytest.approx(source, abs=1e-5)
    assert mapping.to_similarity(pixel_y_down=pixel_y_down) == pytest.approx(pose)
    if not pixel_y_down:
        assert apply_similarity_transform(*source, pose) == pytest.approx(expected, abs=1e-8)


@pytest.mark.parametrize("mapping", [
    MatrixTransform.from_affine((2, 0.3, 500000, 0.2, -1.5, 5400000)),
    MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1)),
])
def test_general_mapping_composition_inverse_serialization_and_bounds(mapping):
    point = (500, 400)
    assert mapping.inverse().map_point(*mapping.map_point(*point)) == pytest.approx(point, abs=1e-8)
    outer = MatrixTransform.from_similarity((140, -30, 17, 1.2))
    assert outer.compose(mapping).map_point(*point) == pytest.approx(outer.map_point(*mapping.map_point(*point)))
    assert mapping.compose(mapping.inverse()).map_point(*point) == pytest.approx(point, abs=1e-8)
    assert MatrixTransform.from_dict(mapping.to_dict()) == mapping
    minimum_x, minimum_y, maximum_x, maximum_y = mapping.domain_bounds(1000, 800)
    for column in range(0, 1001, 100):
        for row in range(0, 801, 100):
            mapped_x, mapped_y = mapping.map_point(column, row)
            assert minimum_x <= mapped_x <= maximum_x
            assert minimum_y <= mapped_y <= maximum_y


def test_projective_jacobian_matches_finite_differences():
    mapping = MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1))
    source = (500, 400)
    mapped = mapping.map_point(*source)
    step = 1e-4
    moved_x = mapping.map_point(source[0] + step, source[1])
    moved_y = mapping.map_point(source[0], source[1] + step)
    expected = ((moved_x[0] - mapped[0]) / step, (moved_y[0] - mapped[0]) / step,
                (moved_x[1] - mapped[1]) / step, (moved_y[1] - mapped[1]) / step)
    assert mapping.jacobian(*source) == pytest.approx(expected, abs=1e-6)


def test_projective_horizon_rejected_for_point_and_entire_domain():
    mapping = MatrixTransform((1, 0, 0, 0, 1, 0, -0.002, 0, 1))
    with pytest.raises(ValueError, match="horizon"):
        mapping.map_point(500, 0)
    with pytest.raises(ValueError, match="horizon"):
        mapping.domain_bounds(1000, 800)
    assert mapping.map_points([(0, 0), (500, 0), (float("nan"), 0)]) == ((0, 0), None, None)


def test_projective_cannot_silently_downgrade_to_affine_or_similarity():
    mapping = MatrixTransform((1, 0, 0, 0, 1, 0, 0.001, 0, 1))
    with pytest.raises(ValueError):
        mapping.to_affine()
    with pytest.raises(ValueError):
        mapping.to_similarity()
    with pytest.raises(ValueError):
        MatrixTransform(mapping.coefficients, "affine")
    with pytest.raises(ValueError):
        MatrixTransform.from_affine((2, 0.3, 0, 0, 1, 0)).to_similarity()


@pytest.mark.parametrize("coefficients", [
    (0,) * 9, (1, 0, 0, 0, 0, 0, 0, 0, 1),
    (1, 0, 0, 0, 1, 0, 0, 0, float("nan")),
    (True, 0, 0, 0, 1, 0, 0, 0, 1), (1,) * 8,
])
def test_invalid_mapping_is_rejected(coefficients):
    with pytest.raises(ValueError):
        MatrixTransform(coefficients)


def test_coefficients_are_immutable_and_homogeneous_scale_is_normalized():
    values = [2, 0, 20, 0, 2, 40, 0, 0, 2]
    mapping = MatrixTransform(values)
    values[2] = 99
    assert mapping.map_point(0, 0) == (10, 20)
    assert isinstance(mapping.coefficients, tuple)