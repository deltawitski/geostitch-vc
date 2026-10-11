import numpy as np
import pytest
from scipy.interpolate import RBFInterpolator

from geostitch.transforms import MatrixTransform
from geostitch.warp import WarpTransform


def control_grid():
    axis = np.linspace(0.0, 100.0, 5)
    return np.asarray([(x, y) for y in axis for x in axis])


def smooth_displacement(points):
    phase = np.sin(np.pi * points[:, 0] / 100) * np.sin(np.pi * points[:, 1] / 100)
    return np.column_stack((3.0 * phase, -2.0 * phase))


def test_tps_fit_matches_scipy_public_interpolator_and_persists_coefficients():
    source = control_grid()
    base = MatrixTransform.from_affine((1.2, 0.1, 500.0, 0.05, 0.9, 900.0))
    base_targets = np.asarray([base.map_point(*point) for point in source])
    targets = base_targets + smooth_displacement(source)
    smoothing = 0.1
    warp = WarpTransform.fit(base, source, targets, smoothing=smoothing)
    normalized = (source - source.mean(axis=0)) / warp.source_scale
    displacement_fit = RBFInterpolator(
        normalized, targets - base_targets,
        kernel="thin_plate_spline", smoothing=smoothing, degree=1,
    )
    queries = np.asarray(((20.0, 25.0), (50.0, 50.0), (80.0, 75.0)))
    normalized_queries = (queries - warp.source_center) / warp.source_scale
    expected = np.asarray([base.map_point(*point) for point in queries]) + displacement_fit(normalized_queries)

    assert np.asarray(warp.map_points(queries)) == pytest.approx(expected, abs=1e-9)
    saved = warp.to_dict()
    assert len(saved["radial_coefficients"]) == len(source)
    restored = WarpTransform.from_dict(saved)
    assert restored.to_dict() == saved
    assert np.asarray(restored.map_points(queries)) == pytest.approx(
        np.asarray(warp.map_points(queries)), abs=1e-12,
    )


def test_tps_identity_displacement_matches_base_and_inverse_round_trips():
    source = control_grid()
    base = MatrixTransform.from_affine((1.5, 0.2, 300.0, -0.1, 1.2, 800.0))
    targets = np.asarray([base.map_point(*point) for point in source])
    warp = WarpTransform.fit(base, source, targets)
    queries = np.asarray(((25.0, 35.0), (50.0, 50.0), (75.0, 65.0)))
    expected = np.asarray([base.map_point(*point) for point in queries])
    mapped = np.asarray(warp.map_points(queries))

    assert mapped == pytest.approx(expected, abs=1e-9)
    assert np.asarray([warp.inverse_point(*point) for point in mapped]) == pytest.approx(queries, abs=1e-7)
    with pytest.raises(ValueError, match="convex-hull"):
        warp.map_point(-1.0, 50.0)


def test_tps_batch_inverse_marks_unsupported_targets_individually():
    source = control_grid()
    targets = source + smooth_displacement(source)
    warp = WarpTransform.fit(MatrixTransform.identity(), source, targets, smoothing=0.01)
    source_points = np.asarray(((25.0, 30.0), (50.0, 50.0)))
    mapped = np.asarray(warp.map_points(source_points))
    mixed_targets = np.vstack((mapped, (150.0, 150.0)))

    recovered = warp.inverse_points(mixed_targets, tolerance=1e-6, invalid_as_nan=True)

    assert recovered[:2] == pytest.approx(source_points, abs=1e-6)
    assert np.isnan(recovered[2]).all()
    with pytest.raises(ValueError, match="did not converge"):
        warp.inverse_points(mixed_targets, tolerance=1e-6)


def test_tps_regularization_reduces_withheld_error_for_a_noisy_control():
    source = control_grid() / 100.0
    physical_source = source * 100.0
    targets = source + smooth_displacement(physical_source) / 100.0
    targets[12] += (0.18, -0.12)
    axis = np.linspace(7.0, 93.0, 9) / 100.0
    queries = np.asarray([(x, y) for y in axis for x in axis])
    truth = queries + smooth_displacement(queries * 100.0) / 100.0
    base = MatrixTransform.identity()
    unregularized = WarpTransform.fit(base, source, targets, smoothing=0.0)
    regularized = WarpTransform.fit(base, source, targets, smoothing=1.0)
    unregularized_error = np.sqrt(np.mean((np.asarray(unregularized.map_points(queries)) - truth) ** 2))
    regularized_error = np.sqrt(np.mean((np.asarray(regularized.map_points(queries)) - truth) ** 2))

    assert regularized_error < unregularized_error * 0.6


def test_tps_analytic_jacobian_matches_finite_difference():
    source = control_grid()
    targets = source + smooth_displacement(source)
    warp = WarpTransform.fit(MatrixTransform.identity(), source, targets, smoothing=0.01)
    x, y = 43.0, 58.0
    step = 1e-4
    mapped = np.asarray(warp.map_points(((x + step, y), (x - step, y), (x, y + step), (x, y - step))))
    finite_difference = np.column_stack((
        (mapped[0] - mapped[1]) / (2 * step),
        (mapped[2] - mapped[3]) / (2 * step),
    ))
    first_x, first_y, second_x, second_y = warp.jacobian(x, y)

    assert np.asarray(((first_x, first_y), (second_x, second_y))) == pytest.approx(
        finite_difference, rel=1e-6, abs=1e-7,
    )


def test_tps_domain_validation_rejects_a_local_fold():
    source = control_grid()
    targets = source.copy()
    targets[:, 0] += 40 * np.sin(np.pi * source[:, 0] / 100)
    warp = WarpTransform.fit(MatrixTransform.identity(), source, targets)

    with pytest.raises(ValueError, match="folded or collapsed"):
        warp.validate_domain(((75.0, 30.0), (87.5, 50.0), (95.0, 70.0)))
