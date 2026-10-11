from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Iterable

import numpy as np
from scipy.spatial import ConvexHull, Delaunay, QhullError

from geostitch.transforms import Coordinate, MatrixTransform

TPS_MAX_CONTROL_POINTS = 512
TPS_SUPPORT_POLICY = "control_convex_hull"


def _thin_plate_kernel(radii: np.ndarray) -> np.ndarray:
    values = np.zeros_like(radii)
    nonzero = radii > 0
    values[nonzero] = radii[nonzero] ** 2 * np.log(radii[nonzero])
    return values


def _coordinates(values, label: str) -> tuple[Coordinate, ...]:
    try:
        raw_coordinates = tuple(tuple(point) for point in values)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{label} must be finite coordinate pairs.") from error
    coordinates = []
    for point in raw_coordinates:
        if len(point) != 2 or any(
            isinstance(component, (bool, np.bool_))
            or not isinstance(component, (int, float, np.number))
            or not isfinite(float(component))
            for component in point
        ):
            raise ValueError(f"{label} must be finite coordinate pairs.")
        coordinates.append((float(point[0]), float(point[1])))
    return tuple(coordinates)


@dataclass(frozen=True)
class WarpTransform:
    """A coefficient-backed TPS displacement over a stable matrix base mapping."""

    base_mapping: MatrixTransform
    source_controls: tuple[Coordinate, ...]
    source_center: Coordinate
    source_scale: float
    radial_coefficients: tuple[Coordinate, ...]
    affine_coefficients: tuple[Coordinate, Coordinate, Coordinate]
    smoothing: float
    support_policy: str = TPS_SUPPORT_POLICY
    support_points: tuple[Coordinate, ...] | None = None
    _support: Any = field(init=False, repr=False, compare=False)
    _hull_vertices: tuple[int, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.base_mapping, MatrixTransform):
            raise ValueError("A TPS warp requires a validated base matrix mapping.")
        if not self.base_mapping.is_affine:
            raise ValueError("A TPS warp requires an affine base mapping.")
        controls = _coordinates(self.source_controls, "TPS source controls")
        support_points = _coordinates(self.support_points or controls, "TPS support points")
        center = _coordinates((self.source_center,), "TPS source center")[0]
        radial = _coordinates(self.radial_coefficients, "TPS radial coefficients")
        affine = _coordinates(self.affine_coefficients, "TPS affine coefficients")
        if not 3 <= len(controls) <= TPS_MAX_CONTROL_POINTS:
            raise ValueError(f"TPS control count must be between 3 and {TPS_MAX_CONTROL_POINTS}.")
        if len(set(controls)) != len(controls):
            raise ValueError("TPS source controls must be unique.")
        if len(set(support_points)) < 3:
            raise ValueError("TPS support needs at least three unique points.")
        if len(radial) != len(controls) or len(affine) != 3:
            raise ValueError("TPS coefficient dimensions do not match the control basis.")
        if (
            isinstance(self.source_scale, (bool, np.bool_))
            or not isinstance(self.source_scale, (int, float, np.number))
            or not isfinite(float(self.source_scale)) or self.source_scale <= 0
        ):
            raise ValueError("TPS source normalization scale must be finite and positive.")
        if (
            isinstance(self.smoothing, (bool, np.bool_))
            or not isinstance(self.smoothing, (int, float, np.number))
            or not isfinite(float(self.smoothing)) or self.smoothing < 0
        ):
            raise ValueError("TPS smoothing must be finite and non-negative.")
        if self.support_policy != TPS_SUPPORT_POLICY:
            raise ValueError("Unsupported TPS boundary policy.")

        source = np.asarray(controls, dtype=float)
        computed_center = source.mean(axis=0)
        computed_scale = float(np.sqrt(np.mean(np.sum((source - computed_center) ** 2, axis=1) / 2)))
        if not np.allclose(center, computed_center, rtol=1e-12, atol=1e-12) or not np.isclose(
            self.source_scale, computed_scale, rtol=1e-12, atol=1e-12,
        ):
            raise ValueError("TPS source normalization does not match its control points.")
        normalized = (source - computed_center) / computed_scale
        normalized_support = (np.asarray(support_points, dtype=float) - computed_center) / computed_scale
        design = np.column_stack((np.ones(len(normalized)), normalized))
        support_design = np.column_stack((np.ones(len(normalized_support)), normalized_support))
        if np.linalg.matrix_rank(design) < 3 or np.linalg.matrix_rank(support_design) < 3:
            raise ValueError("TPS controls must span a two-dimensional domain.")
        try:
            support = Delaunay(normalized_support)
            hull_vertices = tuple(int(index) for index in ConvexHull(normalized_support).vertices)
        except QhullError as error:
            raise ValueError("TPS controls do not define a stable convex-hull support domain.") from error

        object.__setattr__(self, "source_controls", controls)
        object.__setattr__(self, "support_points", support_points)
        object.__setattr__(self, "source_center", center)
        object.__setattr__(self, "radial_coefficients", radial)
        object.__setattr__(self, "affine_coefficients", affine)
        object.__setattr__(self, "_support", support)
        object.__setattr__(self, "_hull_vertices", hull_vertices)

    @classmethod
    def fit(
        cls,
        base_mapping: MatrixTransform,
        source_controls: Iterable[Coordinate],
        target_controls: Iterable[Coordinate],
        *,
        smoothing: float = 0.0,
        support_points: Iterable[Coordinate] | None = None,
    ) -> WarpTransform:
        source = _coordinates(source_controls, "TPS source controls")
        targets = _coordinates(target_controls, "TPS target controls")
        support = _coordinates(support_points if support_points is not None else source, "TPS support points")
        if len(source) != len(targets):
            raise ValueError("TPS source and target control counts must match.")
        if not 3 <= len(source) <= TPS_MAX_CONTROL_POINTS:
            raise ValueError(f"TPS control count must be between 3 and {TPS_MAX_CONTROL_POINTS}.")
        if len(set(source)) != len(source):
            raise ValueError("TPS source controls must be unique.")
        if (
            isinstance(smoothing, (bool, np.bool_))
            or not isinstance(smoothing, (int, float, np.number))
            or not isfinite(float(smoothing)) or smoothing < 0
        ):
            raise ValueError("TPS smoothing must be finite and non-negative.")

        source_array = np.asarray(source, dtype=float)
        target_array = np.asarray(targets, dtype=float)
        center_array = source_array.mean(axis=0)
        scale = float(np.sqrt(np.mean(np.sum((source_array - center_array) ** 2, axis=1) / 2)))
        if scale <= 1e-12:
            raise ValueError("TPS controls must have non-zero spatial extent.")
        normalized = (source_array - center_array) / scale
        polynomial = np.column_stack((np.ones(len(normalized)), normalized))
        normalized_support = (np.asarray(support, dtype=float) - center_array) / scale
        support_polynomial = np.column_stack((np.ones(len(normalized_support)), normalized_support))
        if np.linalg.matrix_rank(polynomial) < 3 or np.linalg.matrix_rank(support_polynomial) < 3:
            raise ValueError("TPS controls must span a two-dimensional domain.")

        base_targets = np.asarray([base_mapping.map_point(*point) for point in source])
        displacements = target_array - base_targets
        differences = normalized[:, None, :] - normalized[None, :, :]
        kernel = _thin_plate_kernel(np.linalg.norm(differences, axis=2))
        count = len(source)
        system = np.block([
            [kernel + float(smoothing) * np.eye(count), polynomial],
            [polynomial.T, np.zeros((3, 3))],
        ])
        right_hand_side = np.vstack((displacements, np.zeros((3, 2))))
        try:
            coefficients = np.linalg.solve(system, right_hand_side)
        except np.linalg.LinAlgError as error:
            raise ValueError("TPS control system is singular or ill-conditioned.") from error
        if not np.isfinite(coefficients).all():
            raise ValueError("TPS fitting produced non-finite coefficients.")
        return cls(
            base_mapping=base_mapping,
            source_controls=source,
            source_center=tuple(float(value) for value in center_array),
            source_scale=scale,
            radial_coefficients=tuple(tuple(float(value) for value in row) for row in coefficients[:count]),
            affine_coefficients=tuple(tuple(float(value) for value in row) for row in coefficients[count:]),
            smoothing=float(smoothing),
            support_points=support,
        )

    @classmethod
    def from_coefficients(
        cls,
        base_mapping: MatrixTransform,
        source_controls: Iterable[Coordinate],
        radial_coefficients: Iterable[Coordinate],
        affine_coefficients: Iterable[Coordinate],
        *,
        smoothing: float,
        support_points: Iterable[Coordinate] | None = None,
    ) -> WarpTransform:
        controls = _coordinates(source_controls, "TPS source controls")
        center = np.asarray(controls, dtype=float).mean(axis=0)
        scale = float(np.sqrt(np.mean(np.sum((np.asarray(controls) - center) ** 2, axis=1) / 2)))
        return cls(
            base_mapping=base_mapping,
            source_controls=controls,
            source_center=tuple(float(value) for value in center),
            source_scale=scale,
            radial_coefficients=_coordinates(radial_coefficients, "TPS radial coefficients"),
            affine_coefficients=_coordinates(affine_coefficients, "TPS affine coefficients"),
            smoothing=float(smoothing),
            support_points=_coordinates(support_points if support_points is not None else controls, "TPS support points"),
        )

    def _map_unchecked(self, points: np.ndarray) -> np.ndarray:
        normalized = (points - np.asarray(self.source_center)) / self.source_scale
        controls = (np.asarray(self.source_controls) - np.asarray(self.source_center)) / self.source_scale
        distances = np.linalg.norm(normalized[:, None, :] - controls[None, :, :], axis=2)
        kernel = _thin_plate_kernel(distances)
        polynomial = np.column_stack((np.ones(len(normalized)), normalized))
        displacement = (
            kernel @ np.asarray(self.radial_coefficients)
            + polynomial @ np.asarray(self.affine_coefficients)
        )
        first, second, third, fourth, fifth, sixth = self.base_mapping.to_affine()
        base = np.column_stack((
            first * points[:, 0] + second * points[:, 1] + third,
            fourth * points[:, 0] + fifth * points[:, 1] + sixth,
        ))
        return base + displacement

    def map_point(self, x: float, y: float) -> Coordinate:
        if not isfinite(x) or not isfinite(y):
            raise ValueError("TPS source coordinates must be finite.")
        point = np.asarray(((x, y),), dtype=float)
        normalized = (point - np.asarray(self.source_center)) / self.source_scale
        if self._support.find_simplex(normalized, tol=1e-10)[0] < 0:
            raise ValueError("TPS source point is outside the control convex-hull support domain.")
        mapped = self._map_unchecked(point)[0]
        if not np.isfinite(mapped).all():
            raise ValueError("TPS mapping produced non-finite coordinates.")
        return float(mapped[0]), float(mapped[1])

    def map_points(self, points: Iterable[Coordinate]) -> tuple[Coordinate, ...]:
        coordinates = _coordinates(points, "TPS source points")
        if not coordinates:
            return ()
        normalized = (np.asarray(coordinates) - np.asarray(self.source_center)) / self.source_scale
        if np.any(self._support.find_simplex(normalized, tol=1e-10) < 0):
            raise ValueError("TPS source point is outside the control convex-hull support domain.")
        mapped = self._map_unchecked(np.asarray(coordinates))
        if not np.isfinite(mapped).all():
            raise ValueError("TPS mapping produced non-finite coordinates.")
        return tuple((float(point[0]), float(point[1])) for point in mapped)

    def supports(self, points: Iterable[Coordinate]) -> tuple[bool, ...]:
        coordinates = _coordinates(points, "TPS support queries")
        if not coordinates:
            return ()
        normalized = (np.asarray(coordinates) - np.asarray(self.source_center)) / self.source_scale
        return tuple(bool(value >= 0) for value in self._support.find_simplex(normalized, tol=1e-10))

    @property
    def source_boundary(self) -> tuple[Coordinate, ...]:
        return tuple(self.support_points[index] for index in self._hull_vertices)

    def jacobian(self, x: float, y: float) -> tuple[float, float, float, float]:
        point = np.asarray((x, y), dtype=float)
        if point.shape != (2,) or not np.isfinite(point).all():
            raise ValueError("TPS Jacobian needs one finite source point.")
        normalized = (point - np.asarray(self.source_center)) / self.source_scale
        if self._support.find_simplex(normalized, tol=1e-10) < 0:
            raise ValueError("TPS Jacobian point is outside the control convex-hull support domain.")
        return self._jacobian_unchecked(x, y)

    def _jacobian_unchecked(self, x: float, y: float) -> tuple[float, float, float, float]:
        point = np.asarray((x, y), dtype=float)
        normalized = (point - np.asarray(self.source_center)) / self.source_scale
        centers = (np.asarray(self.source_controls) - np.asarray(self.source_center)) / self.source_scale
        differences = normalized - centers
        radii = np.linalg.norm(differences, axis=1)
        gradients = np.zeros_like(differences)
        nonzero = radii > 0
        gradients[nonzero] = differences[nonzero] * (
            2 * np.log(radii[nonzero]) + 1
        )[:, None]
        gradients /= self.source_scale
        radial = np.asarray(self.radial_coefficients)
        affine = np.asarray(self.affine_coefficients)
        displacement_dx = gradients[:, 0] @ radial + affine[1] / self.source_scale
        displacement_dy = gradients[:, 1] @ radial + affine[2] / self.source_scale
        base_a, base_b, base_c, base_d = self.base_mapping.jacobian(x, y)
        return (
            base_a + displacement_dx[0], base_b + displacement_dy[0],
            base_c + displacement_dx[1], base_d + displacement_dy[1],
        )

    def _jacobians_unchecked(self, points: np.ndarray) -> np.ndarray:
        normalized = (points - np.asarray(self.source_center)) / self.source_scale
        centers = (np.asarray(self.source_controls) - np.asarray(self.source_center)) / self.source_scale
        differences = normalized[:, None, :] - centers[None, :, :]
        radii = np.linalg.norm(differences, axis=2)
        gradients = np.zeros_like(differences)
        nonzero = radii > 0
        gradients[nonzero] = differences[nonzero] * (
            2 * np.log(radii[nonzero]) + 1
        )[:, None]
        gradients /= self.source_scale
        radial = np.asarray(self.radial_coefficients)
        affine = np.asarray(self.affine_coefficients)
        base_a, base_b, _base_c, base_d, base_e, _base_f = self.base_mapping.to_affine()
        jacobians = np.zeros((len(points), 2, 2), dtype=float)
        jacobians[:, 0, 0] = base_a + gradients[:, :, 0] @ radial[:, 0] + affine[1, 0] / self.source_scale
        jacobians[:, 0, 1] = base_b + gradients[:, :, 1] @ radial[:, 0] + affine[2, 0] / self.source_scale
        jacobians[:, 1, 0] = base_d + gradients[:, :, 0] @ radial[:, 1] + affine[1, 1] / self.source_scale
        jacobians[:, 1, 1] = base_e + gradients[:, :, 1] @ radial[:, 1] + affine[2, 1] / self.source_scale
        return jacobians

    def inverse_points(
        self, targets, *, tolerance: float = 1e-7, max_iterations: int = 40,
        invalid_as_nan: bool = False,
    ) -> np.ndarray:
        target_array = np.asarray(targets, dtype=float)
        if (
            target_array.ndim != 2 or target_array.shape[1] != 2
            or not np.isfinite(target_array).all()
            or isinstance(tolerance, bool) or not isinstance(tolerance, (int, float, np.number))
            or not isfinite(float(tolerance)) or tolerance <= 0
            or isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 1
            or not isinstance(invalid_as_nan, bool)
        ):
            raise ValueError("TPS batch inverse requires finite Nx2 targets and valid iteration/tolerance limits.")
        if not len(target_array):
            return np.empty((0, 2), dtype=float)

        base_inverse = self.base_mapping.inverse()
        a, b, c, d, e, f = base_inverse.to_affine()
        source = np.column_stack((
            a * target_array[:, 0] + b * target_array[:, 1] + c,
            d * target_array[:, 0] + e * target_array[:, 1] + f,
        ))
        support = np.asarray(self.support_points)
        lower = support.min(axis=0)
        upper = support.max(axis=0)
        margin = np.maximum((upper - lower) * 1e-10, 1e-10)
        source = np.clip(source, lower + margin, upper - margin)
        valid = np.ones(len(source), dtype=bool)
        for _iteration in range(max_iterations):
            mapped = self._map_unchecked(source)
            residual = mapped - target_array
            errors = np.linalg.norm(residual, axis=1)
            active = valid & (errors > tolerance)
            if not np.any(active):
                break
            jacobians = self._jacobians_unchecked(source[active])
            determinants = np.linalg.det(jacobians)
            nonsingular = np.abs(determinants) > 1e-14
            active_indices = np.flatnonzero(active)
            valid[active_indices[~nonsingular]] = False
            if np.any(nonsingular):
                active_residual = residual[active][nonsingular]
                steps = np.linalg.solve(
                    jacobians[nonsingular], active_residual[:, :, None],
                )[:, :, 0]
                source[active_indices[nonsingular]] -= steps
            source = np.clip(source, lower + margin, upper - margin)
        errors = np.linalg.norm(self._map_unchecked(source) - target_array, axis=1)
        normalized = (source - np.asarray(self.source_center)) / self.source_scale
        supported = self._support.find_simplex(normalized, tol=1e-10) >= 0
        valid &= supported & (errors <= tolerance)
        if not np.all(valid) and not invalid_as_nan:
            raise ValueError("TPS inverse did not converge to valid supported source points.")
        source[~valid] = np.nan
        return source

    def inverse_point(self, x: float, y: float, *, tolerance: float = 1e-7) -> Coordinate:
        if (
            not isfinite(x) or not isfinite(y)
            or isinstance(tolerance, bool) or not isinstance(tolerance, (int, float, np.number))
            or not isfinite(float(tolerance)) or tolerance <= 0
        ):
            raise ValueError("TPS inverse needs finite target coordinates and positive tolerance.")
        source = self.inverse_points(((x, y),), tolerance=tolerance)[0]
        return float(source[0]), float(source[1])

    def inverse(self) -> InverseWarpTransform:
        return InverseWarpTransform(self)

    def validate_domain(
        self,
        samples: Iterable[Coordinate],
        *,
        minimum_relative_area: float = 0.05,
        maximum_relative_stretch: float = 10.0,
    ) -> tuple[float, float]:
        if (
            isinstance(minimum_relative_area, bool)
            or not isinstance(minimum_relative_area, (int, float, np.number))
            or not isfinite(float(minimum_relative_area)) or minimum_relative_area < 0
            or isinstance(maximum_relative_stretch, bool)
            or not isinstance(maximum_relative_stretch, (int, float, np.number))
            or not isfinite(float(maximum_relative_stretch)) or maximum_relative_stretch <= 0
        ):
            raise ValueError("TPS validity limits must be finite and non-negative.")
        relative_determinants = []
        relative_stretches = []
        for x, y in _coordinates(samples, "TPS domain samples"):
            base_values = np.asarray(self.base_mapping.jacobian(x, y)).reshape((2, 2))
            warp_values = np.asarray(self.jacobian(x, y)).reshape((2, 2))
            try:
                relative = warp_values @ np.linalg.inv(base_values)
            except np.linalg.LinAlgError as error:
                raise ValueError("TPS base mapping has a singular sampled Jacobian.") from error
            determinant = float(np.linalg.det(relative))
            stretch = float(np.linalg.svd(relative, compute_uv=False)[0])
            relative_determinants.append(determinant)
            relative_stretches.append(stretch)
        if not relative_determinants:
            raise ValueError("TPS domain validation requires at least one sample.")
        if min(relative_determinants) <= minimum_relative_area:
            raise ValueError("TPS mapping has a folded or collapsed sampled Jacobian.")
        if max(relative_stretches) > maximum_relative_stretch:
            raise ValueError("TPS mapping exceeds the sampled relative stretch limit.")
        return min(relative_determinants), max(relative_stretches)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "kind": "thin_plate_spline_displacement",
            "kernel": "thin_plate_spline",
            "degree": 1,
            "support_policy": self.support_policy,
            "support_points": [list(point) for point in self.support_points],
            "base_mapping": self.base_mapping.to_dict(),
            "source_controls": [list(point) for point in self.source_controls],
            "source_center": list(self.source_center),
            "source_scale": self.source_scale,
            "radial_coefficients": [list(row) for row in self.radial_coefficients],
            "affine_coefficients": [list(row) for row in self.affine_coefficients],
            "smoothing": self.smoothing,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WarpTransform:
        if not isinstance(data, dict) or any(data.get(key) != value for key, value in (
            ("version", 1), ("kind", "thin_plate_spline_displacement"),
            ("kernel", "thin_plate_spline"), ("degree", 1),
        )):
            raise ValueError("Unsupported warp transform format.")
        return cls(
            MatrixTransform.from_dict(data.get("base_mapping")),
            tuple(tuple(point) for point in data.get("source_controls", ())),
            tuple(data.get("source_center", ())), data.get("source_scale"),
            tuple(tuple(row) for row in data.get("radial_coefficients", ())),
            tuple(tuple(row) for row in data.get("affine_coefficients", ())),
            data.get("smoothing"), data.get("support_policy"),
            tuple(tuple(point) for point in data.get("support_points", ())),
        )


@dataclass(frozen=True)
class InverseWarpTransform:
    forward: WarpTransform

    def __post_init__(self) -> None:
        if not isinstance(self.forward, WarpTransform):
            raise ValueError("An inverse TPS step requires a validated forward warp.")

    def map_point(self, x: float, y: float) -> Coordinate:
        return self.forward.inverse_point(x, y)

    def inverse(self) -> WarpTransform:
        return self.forward

    def to_dict(self) -> dict:
        return {"version": 1, "warp": self.forward.to_dict()}

    @classmethod
    def from_dict(cls, data: dict) -> InverseWarpTransform:
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Unsupported inverse TPS mapping format.")
        return cls(WarpTransform.from_dict(data.get("warp")))