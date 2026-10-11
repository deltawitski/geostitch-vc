import numpy as np
import pytest
from scipy.interpolate import RBFInterpolator
from scipy.optimize import least_squares
from scipy.spatial import Delaunay

from geostitch.transforms import MatrixTransform


class ThinPlateSplinePrototype:
    """Test-only TPS displacement over a fixed base mapping and convex-hull support."""

    def __init__(self, base_mapping, source_controls, target_controls, smoothing):
        self.base_mapping = base_mapping
        self.source_controls = np.asarray(source_controls, dtype=float)
        self.target_controls = np.asarray(target_controls, dtype=float)
        if self.source_controls.ndim != 2 or self.source_controls.shape[1] != 2:
            raise ValueError("TPS source controls must be an Nx2 array.")
        if self.target_controls.shape != self.source_controls.shape or len(self.source_controls) < 3:
            raise ValueError("TPS target controls must match at least three source controls.")
        if not np.isfinite(self.source_controls).all() or not np.isfinite(self.target_controls).all():
            raise ValueError("TPS controls must be finite.")
        if not np.isfinite(smoothing) or smoothing < 0:
            raise ValueError("TPS smoothing must be finite and non-negative.")
        self.smoothing = float(smoothing)
        self.center = self.source_controls.mean(axis=0)
        centered = self.source_controls - self.center
        self.scale = float(np.sqrt(np.mean(np.sum(centered ** 2, axis=1) / 2)))
        if self.scale <= 1e-12:
            raise ValueError("TPS controls must have non-zero spatial extent.")
        self.normalized_controls = centered / self.scale
        if np.linalg.matrix_rank(np.column_stack((self.normalized_controls, np.ones(len(centered))))) < 3:
            raise ValueError("TPS controls must span a two-dimensional domain.")
        self.support = Delaunay(self.normalized_controls)
        base_targets = np.asarray([
            self.base_mapping.map_point(*point) for point in self.source_controls
        ])
        self.radial = RBFInterpolator(
            self.normalized_controls,
            self.target_controls - base_targets,
            kernel="thin_plate_spline",
            smoothing=self.smoothing,
            degree=1,
        )

    def _map_unchecked(self, points):
        points = np.asarray(points, dtype=float)
        normalized = (points - self.center) / self.scale
        base = np.asarray([self.base_mapping.map_point(*point) for point in points])
        return base + self.radial(normalized)

    def map_points(self, points):
        points = np.asarray(points, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
            raise ValueError("TPS inputs must be a finite Nx2 array.")
        normalized = (points - self.center) / self.scale
        if np.any(self.support.find_simplex(normalized, tol=1e-10) < 0):
            raise ValueError("TPS query is outside the control convex hull.")
        mapped = self._map_unchecked(points)
        if not np.isfinite(mapped).all():
            raise ValueError("TPS mapping produced non-finite coordinates.")
        return mapped

    def jacobian(self, point):
        point = np.asarray(point, dtype=float)
        if point.shape != (2,) or not np.isfinite(point).all():
            raise ValueError("TPS Jacobian needs one finite source point.")
        step = max(self.scale * 1e-5, 1e-7)
        samples = np.asarray((
            point + (step, 0), point - (step, 0),
            point + (0, step), point - (0, step),
        ))
        mapped = self.map_points(samples)
        return np.column_stack((
            (mapped[0] - mapped[1]) / (2 * step),
            (mapped[2] - mapped[3]) / (2 * step),
        ))

    def validate_domain(self, samples, *, minimum_determinant=0.05, maximum_stretch=10.0):
        jacobians = np.asarray([self.jacobian(point) for point in samples])
        determinants = np.linalg.det(jacobians)
        stretches = np.linalg.svd(jacobians, compute_uv=False)[:, 0]
        if np.any(determinants <= minimum_determinant):
            raise ValueError("TPS mapping has a folded or collapsed sampled Jacobian.")
        if np.any(stretches > maximum_stretch):
            raise ValueError("TPS mapping exceeds the sampled stretch limit.")
        return float(determinants.min()), float(stretches.max())

    def inverse_points(self, targets, *, tolerance=1e-7):
        targets = np.asarray(targets, dtype=float)
        if targets.ndim != 2 or targets.shape[1] != 2 or not np.isfinite(targets).all():
            raise ValueError("TPS inverse inputs must be a finite Nx2 array.")
        lower = self.source_controls.min(axis=0)
        upper = self.source_controls.max(axis=0)
        margin = np.maximum((upper - lower) * 1e-10, 1e-10)
        lower, upper = lower + margin, upper - margin
        inverse_base = self.base_mapping.inverse()
        recovered = []
        for target in targets:
            initial = np.asarray(inverse_base.map_point(*target))
            initial = np.clip(initial, lower + margin, upper - margin)
            solution = least_squares(
                lambda source: self._map_unchecked(source[None, :])[0] - target,
                initial,
                bounds=(lower, upper),
                max_nfev=100,
                ftol=1e-12,
                xtol=1e-12,
                gtol=1e-12,
            )
            source = solution.x
            residual = np.linalg.norm(self._map_unchecked(source[None, :])[0] - target)
            normalized = (source - self.center) / self.scale
            if (
                not solution.success or residual > tolerance
                or self.support.find_simplex(normalized, tol=1e-10) < 0
            ):
                raise ValueError("TPS inverse did not converge to a valid supported source point.")
            recovered.append(source)
        return np.asarray(recovered)

    def to_dict(self):
        return {
            "version": 1,
            "kind": "thin_plate_spline_displacement_prototype",
            "kernel": "thin_plate_spline",
            "degree": 1,
            "support_policy": "control_convex_hull",
            "base_mapping": self.base_mapping.to_dict(),
            "source_controls": self.source_controls.tolist(),
            "target_controls": self.target_controls.tolist(),
            "smoothing": self.smoothing,
        }

    @classmethod
    def from_dict(cls, data):
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or data.get("kind") != "thin_plate_spline_displacement_prototype"
            or data.get("kernel") != "thin_plate_spline"
            or data.get("degree") != 1
            or data.get("support_policy") != "control_convex_hull"
        ):
            raise ValueError("Unsupported TPS prototype representation.")
        return cls(
            MatrixTransform.from_dict(data["base_mapping"]), data["source_controls"],
            data["target_controls"], data["smoothing"],
        )


def _grid():
    axis = np.linspace(0.0, 100.0, 5)
    return np.asarray([(x, y) for y in axis for x in axis])


def _smooth_displacement(points):
    phase = np.sin(np.pi * points[:, 0] / 100) * np.sin(np.pi * points[:, 1] / 100)
    return np.column_stack((3.0 * phase, -2.0 * phase))


def test_tps_base_equivalence_inverse_and_fit_input_serialization():
    controls = _grid()
    base = MatrixTransform.from_similarity((500.0, 2000.0, 0.0, 1.0))
    targets = np.asarray([base.map_point(*point) for point in controls])
    warp = ThinPlateSplinePrototype(base, controls, targets, smoothing=0.0)
    samples = np.asarray(((30.0, 40.0), (50.0, 50.0), (70.0, 55.0)))

    assert warp.map_points(samples) == pytest.approx(np.asarray([base.map_point(*point) for point in samples]))
    assert warp.inverse_points(warp.map_points(samples)) == pytest.approx(samples, abs=1e-7)
    restored = ThinPlateSplinePrototype.from_dict(warp.to_dict())
    assert restored.map_points(samples) == pytest.approx(warp.map_points(samples), abs=1e-12)
    with pytest.raises(ValueError, match="convex hull"):
        warp.map_points(np.asarray(((-1.0, 50.0),)))


def test_tps_smoothing_reduces_withheld_error_for_a_noisy_control():
    controls = _grid() / 100.0
    truth = _smooth_displacement(controls * 100.0) / 100.0
    noisy_targets = controls + truth
    noisy_targets[12] += (0.18, -0.12)
    queries_axis = np.linspace(7.0, 93.0, 9)
    queries = np.asarray([(x, y) for y in queries_axis for x in queries_axis]) / 100.0
    expected = queries + _smooth_displacement(queries * 100.0) / 100.0
    base = MatrixTransform.identity()
    exact_fit = ThinPlateSplinePrototype(base, controls, noisy_targets, smoothing=0.0)
    regularized_fit = ThinPlateSplinePrototype(base, controls, noisy_targets, smoothing=1.0)
    exact_rmse = np.sqrt(np.mean((exact_fit.map_points(queries) - expected) ** 2))
    regularized_rmse = np.sqrt(np.mean((regularized_fit.map_points(queries) - expected) ** 2))

    assert regularized_rmse < exact_rmse * 0.6


def test_tps_inverse_and_sampled_jacobians_on_a_smooth_deformation():
    controls = _grid()
    targets = controls + _smooth_displacement(controls)
    warp = ThinPlateSplinePrototype(MatrixTransform.identity(), controls, targets, smoothing=0.01)
    samples = np.asarray(((25.0, 30.0), (50.0, 50.0), (75.0, 70.0)))
    warped = warp.map_points(samples)

    minimum_determinant, maximum_stretch = warp.validate_domain(samples)

    assert minimum_determinant > 0.5
    assert maximum_stretch < 1.2
    assert warp.inverse_points(warped) == pytest.approx(samples, abs=1e-5)
    restored = ThinPlateSplinePrototype.from_dict(warp.to_dict())
    assert restored.map_points(samples) == pytest.approx(warped, abs=1e-10)


def test_tps_domain_validation_rejects_a_sampled_fold():
    controls = _grid()
    folded_targets = controls.copy()
    folded_targets[:, 0] += 40 * np.sin(np.pi * controls[:, 0] / 100)
    warp = ThinPlateSplinePrototype(
        MatrixTransform.identity(), controls, folded_targets, smoothing=0.0,
    )

    with pytest.raises(ValueError, match="folded or collapsed"):
        warp.validate_domain(((75.0, 30.0), (87.5, 50.0), (95.0, 70.0)))
