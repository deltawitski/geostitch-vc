from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot, isclose, isfinite, radians, sqrt
from typing import Callable

from geostitch.models import Project, SolverSettings
from geostitch.mapping import ImageMapping
from geostitch.transforms import MatrixTransform, similarity_mapping
from geostitch.transforms import apply_similarity_transform as _apply_transform
from geostitch.warp import WarpTransform

ImageTransform = tuple[float, float, float, float]


@dataclass(frozen=True)
class AlignmentResult:
    transforms: dict[str, ImageTransform]
    constraint_residuals: dict[str, float]
    iterations: int
    converged: bool
    anchor_image_ids: tuple[str, ...]
    optimizer_status: int | None = None
    optimizer_message: str = ""
    max_iterations: int | None = None
    settings: SolverSettings | None = None
    matrix_transforms: dict[str, MatrixTransform] | None = None
    source_dimensions: dict[str, tuple[int, int]] | None = None
    warp_transforms: dict[str, WarpTransform] | None = None

    def __post_init__(self) -> None:
        warps = dict(self.warp_transforms or {})
        if any(not isinstance(value, WarpTransform) for value in warps.values()):
            raise ValueError("Solver warp mappings require validated TPS transforms.")
        if set(warps) & set(self.matrix_transforms or {}):
            raise ValueError("An alignment result cannot store a mapping as both matrix and TPS.")
        if warps:
            if not set(self.transforms).issubset(set(warps) | set(self.matrix_transforms or {})):
                raise ValueError("Every alignment transform requires a full mapping record.")
            for image_id, warp in warps.items():
                expected = _mapping_compatibility_transform(warp.base_mapping)
                actual = self.transforms.get(image_id)
                if actual is None or any(
                    not isclose(value, target, rel_tol=1e-12, abs_tol=1e-12)
                    for value, target in zip(actual, expected)
                ):
                    raise ValueError("Solver compatibility transform disagrees with its TPS base mapping.")
            object.__setattr__(self, "warp_transforms", warps)
        if self.matrix_transforms is not None:
            matrices = dict(self.matrix_transforms)
            if any(not isinstance(value, MatrixTransform) for value in matrices.values()):
                raise ValueError("Solver mappings require validated matrix transforms.")
            for image_id, transform in self.transforms.items():
                if image_id in warps:
                    continue
                if image_id not in matrices or matrices[image_id].coefficients != similarity_mapping(transform).coefficients:
                    raise ValueError("Solver matrix and compatibility transform disagree.")
            object.__setattr__(self, "matrix_transforms", matrices)
        if self.source_dimensions is not None:
            dimensions = dict(self.source_dimensions)
            mappings = self.mappings
            for image_id, size in dimensions.items():
                mapping = mappings.get(image_id)
                if mapping is None:
                    raise ValueError("Source dimensions refer to an image absent from the solver result.")
                dimensions[image_id] = ImageMapping(mapping, "layout", source_size=size).source_size
            object.__setattr__(self, "source_dimensions", dimensions)

    @property
    def maximum_residual(self) -> float:
        return max(self.constraint_residuals.values(), default=0.0)

    @property
    def mappings(self) -> dict[str, MatrixTransform | WarpTransform]:
        mappings: dict[str, MatrixTransform | WarpTransform] = dict(self.matrix_transforms or {})
        mappings.update(self.warp_transforms or {})
        if mappings:
            return mappings
        return {image_id: similarity_mapping(transform) for image_id, transform in self.transforms.items()}

    @property
    def mapping_records(self) -> dict[str, ImageMapping]:
        return {
            image_id: ImageMapping(mapping, "layout", source_size=(self.source_dimensions or {}).get(image_id))
            for image_id, mapping in self.mappings.items()
        }


def _mapping_compatibility_transform(mapping: MatrixTransform) -> ImageTransform:
    if mapping.model == "similarity":
        return mapping.to_similarity()
    origin_x, origin_y = mapping.map_point(0.0, 0.0)
    a, _b, d, _e = mapping.jacobian(0.0, 0.0)
    return origin_x, origin_y, degrees(atan2(d, a)), hypot(a, d)


@dataclass(frozen=True)
class _Match:
    constraint_id: str
    source_image_id: str
    source_x: float
    source_y: float
    target_image_id: str
    target_x: float
    target_y: float


def simulate_alignment(
    project: Project,
    tolerance: float = 0.5,
    max_iterations: int = 100,
    initial_transforms: dict[str, ImageTransform] | None = None,
    progress: Callable[[float], None] | None = None,
    settings: SolverSettings | None = None,
    initial_mappings: dict[str, MatrixTransform] | None = None,
) -> AlignmentResult:
    """Jointly align constrained images with the configured transform and loss.

    One image in each connected image component is held fixed to remove the free
    global translation/rotation/scale. Every linked observation contributes to the
    same residual vector. TPS mappings are tessellated for display and retain source pixels.
    """
    config = settings or SolverSettings(tolerance, max_iterations)
    tolerance, max_iterations = config.tolerance, config.max_iterations
    if tolerance <= 0:
        raise ValueError("Tolerance must be greater than zero.")
    if max_iterations < 1:
        raise ValueError("Maximum iterations must be at least one.")

    from scipy.optimize import least_squares

    image_order = [image.id for image in project.images]
    transforms: dict[str, ImageTransform] = {}
    for image in project.images:
        if initial_transforms and image.id in initial_transforms:
            transforms[image.id] = initial_transforms[image.id]
        else:
            transforms[image.id] = (
                image.position_x if image.position_x is not None else 0.0,
                image.position_y,
                image.rotation,
                image.scale,
            )

    points = {point.id: point for point in project.control_points}
    matches: list[_Match] = []
    adjacency: dict[str, set[str]] = {}
    for constraint in project.constraints:
        first = points.get(constraint.first_point_id)
        second = points.get(constraint.second_point_id)
        if first is None or second is None:
            continue
        for first_observation in first.observations:
            for second_observation in second.observations:
                if first_observation.image_id == second_observation.image_id:
                    continue
                matches.append(_Match(
                    constraint_id=constraint.id,
                    source_image_id=first_observation.image_id,
                    source_x=first_observation.x,
                    source_y=first_observation.y,
                    target_image_id=second_observation.image_id,
                    target_x=second_observation.x,
                    target_y=second_observation.y,
                ))
                adjacency.setdefault(first_observation.image_id, set()).add(second_observation.image_id)
                adjacency.setdefault(second_observation.image_id, set()).add(first_observation.image_id)

    if not matches:
        raise ValueError("No constraints connect observations from different images.")
    if config.transform_model not in ("affine", "projective", "tps") and any(image.id in adjacency and image.rest_intrinsic is not None for image in project.images):
        raise ValueError("The current alignment solver cannot consume intrinsic layout mappings.")

    components = _image_components(adjacency, image_order)
    anchor_ids = tuple(component[0] for component in components)
    anchor_set = set(anchor_ids)
    variable_image_ids = [image_id for image_id in image_order if image_id in adjacency and image_id not in anchor_set]
    if config.transform_model == "tps":
        from geostitch.deformable_alignment import simulate_tps_alignment

        return simulate_tps_alignment(
            project, matches, adjacency, transforms, initial_mappings or {},
            anchor_ids, config, progress,
        )
    if config.transform_model == "affine":
        return _simulate_affine_alignment(
            project, matches, adjacency, variable_image_ids, anchor_ids, transforms,
            initial_mappings or {}, config, progress,
        )
    if config.transform_model == "projective":
        return _simulate_projective_alignment(
            project, matches, adjacency, variable_image_ids, anchor_ids, transforms,
            initial_mappings or {}, config, progress,
        )
    pair_match_counts: dict[tuple[str, str], int] = {}
    for match in matches:
        pair = tuple(sorted((match.source_image_id, match.target_image_id)))
        pair_match_counts[pair] = pair_match_counts.get(pair, 0) + 1
    underconstrained_images = {
        image_id
        for pair, count in pair_match_counts.items()
        if count < 2
        for image_id in pair
    }
    translation_ids = set(variable_image_ids) if config.transform_model == "translation" else underconstrained_images
    scale_min = config.scale_min if config.scale_min is not None else 0.05
    scale_max = config.scale_max if config.scale_max is not None else 20.0
    if config.transform_model == "similarity" and scale_min >= scale_max:
        raise ValueError("Minimum alignment scale must be less than maximum scale.")
    initial_values: list[float] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []
    for image_id in variable_image_ids:
        x, y, rotation, scale = transforms[image_id]
        if image_id in translation_ids:
            initial_values.extend((x, y))
            lower_bounds.extend((-float("inf"), -float("inf")))
            upper_bounds.extend((float("inf"), float("inf")))
        elif config.transform_model == "rigid":
            initial_values.extend((x, y, radians(rotation)))
            lower_bounds.extend((-float("inf"), -float("inf"), -float("inf")))
            upper_bounds.extend((float("inf"), float("inf"), float("inf")))
        else:
            initial_values.extend((x, y, radians(rotation), min(scale_max, max(scale_min, scale))))
            lower_bounds.extend((-float("inf"), -float("inf"), -float("inf"), scale_min))
            upper_bounds.extend((float("inf"), float("inf"), float("inf"), scale_max))

    evaluations = [0]
    budget = max_iterations * (len(initial_values) + 1)

    def unpack(values: list[float]) -> dict[str, ImageTransform]:
        candidate = dict(transforms)
        offset = 0
        for image_id in variable_image_ids:
            if image_id in translation_ids:
                candidate[image_id] = (
                    values[offset],
                    values[offset + 1],
                    transforms[image_id][2],
                    transforms[image_id][3],
                )
                offset += 2
            elif config.transform_model == "rigid":
                candidate[image_id] = (
                    values[offset], values[offset + 1], degrees(values[offset + 2]), transforms[image_id][3],
                )
                offset += 3
            else:
                candidate[image_id] = (
                    values[offset],
                    values[offset + 1],
                    degrees(values[offset + 2]),
                    values[offset + 3],
                )
                offset += 4
        return candidate

    def residual_vector(values: list[float]) -> list[float]:
        if progress is not None:
            evaluations[0] += 1
            progress(min(0.99, evaluations[0] / budget))
        candidate = unpack(values)
        residuals = []
        for match in matches:
            source_x, source_y = _apply_transform(
                match.source_x,
                match.source_y,
                candidate[match.source_image_id],
            )
            target_x, target_y = _apply_transform(
                match.target_x,
                match.target_y,
                candidate[match.target_image_id],
            )
            residuals.extend((source_x - target_x, source_y - target_y))
        return residuals

    if initial_values:
        optimization = least_squares(
            residual_vector,
            initial_values,
            bounds=(lower_bounds, upper_bounds),
            max_nfev=max_iterations,
            method=config.optimizer_method,
            loss=config.loss,
            f_scale=config.loss_scale,
            x_scale="jac" if config.parameter_scaling == "jacobian" else 1.0,
            ftol=config.ftol,
            xtol=config.xtol,
            gtol=config.gtol,
        )
        transforms = unpack(optimization.x.tolist())
        iterations_run = optimization.nfev
        optimizer_converged = optimization.success
        optimizer_status = int(optimization.status)
        optimizer_message = str(optimization.message)
    else:
        iterations_run = 0
        optimizer_converged = True
        optimizer_status = None
        optimizer_message = "No variable transforms required optimization."

    residuals = _constraint_residuals(matches, transforms)
    converged = optimizer_converged and bool(residuals) and max(residuals.values()) <= tolerance

    return AlignmentResult(
        transforms={image_id: transforms[image_id] for image_id in adjacency},
        constraint_residuals=residuals,
        iterations=iterations_run,
        converged=converged,
        anchor_image_ids=anchor_ids,
        optimizer_status=optimizer_status,
        optimizer_message=optimizer_message,
        max_iterations=max_iterations,
        settings=config,
        source_dimensions={
            image.id: image.source_dimensions for image in project.images
            if image.id in adjacency and image.source_dimensions is not None
        } or None,
    )


def _affine_control_rank(locations: list[tuple[float, float]]) -> int:
    unique = list(dict.fromkeys(locations))
    if len(unique) < 3:
        return len(unique)
    span = max(
        max(x for x, _ in unique) - min(x for x, _ in unique),
        max(y for _, y in unique) - min(y for _, y in unique),
        1.0,
    )
    threshold = span * span * 1e-10
    first = unique[0]
    for second_index in range(1, len(unique) - 1):
        second = unique[second_index]
        for third in unique[second_index + 1:]:
            area_twice = abs(
                (second[0] - first[0]) * (third[1] - first[1])
                - (third[0] - first[0]) * (second[1] - first[1])
            )
            if area_twice > threshold:
                return 3
    return 2


AFFINE_CONTROL_CONDITION_WARNING = 1e4


def _affine_control_condition(locations: list[tuple[float, float]]) -> float:
    import numpy as np

    unique = list(dict.fromkeys(locations))
    if len(unique) < 3:
        return float("inf")
    coordinates = np.asarray(unique, dtype=float)
    centered = coordinates - coordinates.mean(axis=0)
    spread = float(np.sqrt(np.mean(np.sum(centered ** 2, axis=1) / 2)))
    if spread <= 1e-12:
        return float("inf")
    design = np.column_stack((centered / spread, np.ones(len(centered))))
    singular_values = np.linalg.svd(design, compute_uv=False)
    if singular_values[-1] <= 0:
        return float("inf")
    return float(singular_values[0] / singular_values[-1])


def _normalization_matrix(points):
    import numpy as np

    coordinates = np.asarray(points, dtype=float)
    center = coordinates.mean(axis=0)
    distances = np.linalg.norm(coordinates - center, axis=1)
    mean_distance = float(distances.mean())
    if mean_distance <= 1e-12:
        return None
    scale = 2**0.5 / mean_distance
    matrix = np.array(((scale, 0.0, -scale * center[0]),
                       (0.0, scale, -scale * center[1]),
                       (0.0, 0.0, 1.0)))
    normalized = (coordinates - center) * scale
    return normalized, matrix


def _normalized_dlt(source_points, target_points) -> tuple[MatrixTransform | None, int]:
    import numpy as np

    if len(source_points) != len(target_points) or len(source_points) < 4:
        return None, min(len(source_points), len(target_points)) * 2
    source_normalization = _normalization_matrix(source_points)
    target_normalization = _normalization_matrix(target_points)
    if source_normalization is None or target_normalization is None:
        return None, 0
    source, source_matrix = source_normalization
    target, target_matrix = target_normalization
    rows = []
    for (x, y), (u, v) in zip(source, target):
        rows.append((-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u))
        rows.append((0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v))
    design = np.asarray(rows, dtype=float)
    _left, singular_values, right = np.linalg.svd(design, full_matrices=True)
    rank = int(np.count_nonzero(singular_values > singular_values[0] * 1e-10)) if singular_values.size else 0
    if rank < 8:
        return None, rank
    normalized_homography = right[-1].reshape((3, 3))
    homography = np.linalg.inv(target_matrix) @ normalized_homography @ source_matrix
    divisor = homography[2, 2]
    if abs(divisor) <= 1e-12 * max(abs(value) for value in homography.flat):
        return None, rank
    homography /= divisor
    try:
        return MatrixTransform(tuple(float(value) for value in homography.flat), "projective"), rank
    except ValueError:
        return None, rank


def _projective_control_rank(source_points, target_points) -> int:
    _matrix, rank = _normalized_dlt(source_points, target_points)
    return rank


def _simulate_affine_alignment(
    project: Project,
    matches: list[_Match],
    adjacency: dict[str, set[str]],
    variable_image_ids: list[str],
    anchor_ids: tuple[str, ...],
    initial_poses: dict[str, ImageTransform],
    initial_mappings: dict[str, MatrixTransform],
    config: SolverSettings,
    progress: Callable[[float], None] | None,
) -> AlignmentResult:
    from math import sqrt

    from scipy.optimize import least_squares

    points_by_image: dict[str, list[tuple[float, float]]] = {image_id: [] for image_id in adjacency}
    for match in matches:
        points_by_image[match.source_image_id].append((match.source_x, match.source_y))
        points_by_image[match.target_image_id].append((match.target_x, match.target_y))
    pair_locations: dict[tuple[str, str], tuple[list[tuple[float, float]], list[tuple[float, float]]]] = {}
    for match in matches:
        pair = tuple(sorted((match.source_image_id, match.target_image_id)))
        first, second = pair_locations.setdefault(pair, ([], []))
        if match.source_image_id == pair[0]:
            first.append((match.source_x, match.source_y))
            second.append((match.target_x, match.target_y))
        else:
            first.append((match.target_x, match.target_y))
            second.append((match.source_x, match.source_y))
    for pair, (first_locations, second_locations) in pair_locations.items():
        if _affine_control_rank(first_locations) < 3 or _affine_control_rank(second_locations) < 3:
            raise ValueError(
                "Affine alignment needs at least three non-collinear linked observations for every connected image pair."
            )

    base_mappings = {
        image_id: initial_mappings.get(image_id) or MatrixTransform.from_similarity(initial_poses[image_id])
        for image_id in adjacency
    }
    normalized_frames: dict[str, tuple[float, float, float]] = {}
    for image_id, locations in points_by_image.items():
        center_x = sum(x for x, _ in locations) / len(locations)
        center_y = sum(y for _, y in locations) / len(locations)
        spread = sqrt(sum((x - center_x) ** 2 + (y - center_y) ** 2 for x, y in locations) / (2 * len(locations)))
        normalized_frames[image_id] = center_x, center_y, max(spread, 1.0)

    mapped_controls = [base_mappings[match.source_image_id].map_point(match.source_x, match.source_y) for match in matches]
    mapped_controls.extend(base_mappings[match.target_image_id].map_point(match.target_x, match.target_y) for match in matches)
    origin_x = sum(x for x, _ in mapped_controls) / len(mapped_controls)
    origin_y = sum(y for _, y in mapped_controls) / len(mapped_controls)
    initial_values: list[float] = []
    for image_id in variable_image_ids:
        center_x, center_y, spread = normalized_frames[image_id]
        matrix = base_mappings[image_id].coefficients
        center = base_mappings[image_id].map_point(center_x, center_y)
        initial_values.extend((
            matrix[0] * spread, matrix[1] * spread, center[0] - origin_x,
            matrix[3] * spread, matrix[4] * spread, center[1] - origin_y,
        ))

    def unpack(parameters: list[float]) -> dict[str, MatrixTransform]:
        result = {image_id: base_mappings[image_id] for image_id in anchor_ids}
        offset = 0
        for image_id in variable_image_ids:
            center_x, center_y, spread = normalized_frames[image_id]
            a, b, translated_x, d, e, translated_y = parameters[offset:offset + 6]
            offset += 6
            pixel_a, pixel_b = a / spread, b / spread
            pixel_d, pixel_e = d / spread, e / spread
            result[image_id] = MatrixTransform.from_affine((
                pixel_a, pixel_b, origin_x + translated_x - pixel_a * center_x - pixel_b * center_y,
                pixel_d, pixel_e, origin_y + translated_y - pixel_d * center_x - pixel_e * center_y,
            ))
        return result

    evaluations = [0]
    evaluation_budget = config.max_iterations * (len(initial_values) + 1)

    def residual_vector(parameters: list[float]) -> list[float]:
        if progress is not None:
            evaluations[0] += 1
            progress(min(0.99, evaluations[0] / evaluation_budget))
        mappings = unpack(parameters)
        residuals = []
        for match in matches:
            source = mappings[match.source_image_id].map_point(match.source_x, match.source_y)
            target = mappings[match.target_image_id].map_point(match.target_x, match.target_y)
            residuals.extend((source[0] - target[0], source[1] - target[1]))
        return residuals

    optimization = least_squares(
        residual_vector,
        initial_values,
        max_nfev=config.max_iterations,
        method=config.optimizer_method,
        loss=config.loss,
        f_scale=config.loss_scale,
        x_scale="jac" if config.parameter_scaling == "jacobian" else 1.0,
        ftol=config.ftol,
        xtol=config.xtol,
        gtol=config.gtol,
    )
    mappings = unpack(optimization.x.tolist())
    for image_id in variable_image_ids:
        mapping = mappings[image_id]
        first, second, _, fourth, fifth, *_ = mapping.coefficients
        trace = first * first + second * second + fourth * fourth + fifth * fifth
        determinant_squared = (first * fifth - second * fourth) ** 2
        discriminant = max(0.0, trace * trace - 4 * determinant_squared) ** 0.5
        maximum_stretch = max(0.0, (trace + discriminant) / 2) ** 0.5
        minimum_stretch = max(0.0, (trace - discriminant) / 2) ** 0.5
        if first * fifth - second * fourth < 0 and not config.affine_allow_reflection:
            raise ValueError(f"Affine alignment reflected image '{image_id}', but reflected solutions are disabled.")
        if minimum_stretch < config.affine_min_stretch or maximum_stretch > config.affine_max_stretch:
            raise ValueError(
                f"Affine alignment for image '{image_id}' exceeds the configured principal-stretch limits."
            )
        if minimum_stretch <= 0 or maximum_stretch / minimum_stretch > config.affine_max_condition:
            raise ValueError(f"Affine alignment for image '{image_id}' exceeds the configured condition limit.")
    compatibility_poses = {
        image_id: mapping.to_similarity()
        for image_id, mapping in mappings.items() if mapping.model == "similarity"
    }
    residuals: dict[str, float] = {}
    for match in matches:
        source = mappings[match.source_image_id].map_point(match.source_x, match.source_y)
        target = mappings[match.target_image_id].map_point(match.target_x, match.target_y)
        residuals[match.constraint_id] = max(
            residuals.get(match.constraint_id, 0.0), hypot(source[0] - target[0], source[1] - target[1]),
        )
    source_dimensions = {
        image.id: image.source_dimensions for image in project.images
        if image.id in mappings and image.source_dimensions is not None
    } or None
    return AlignmentResult(
        transforms=compatibility_poses,
        constraint_residuals=residuals,
        iterations=int(optimization.nfev),
        converged=bool(optimization.success and residuals and max(residuals.values()) <= config.tolerance),
        anchor_image_ids=anchor_ids,
        optimizer_status=int(optimization.status),
        optimizer_message=str(optimization.message),
        max_iterations=config.max_iterations,
        settings=config,
        matrix_transforms=mappings,
        source_dimensions=source_dimensions,
    )


def _simulate_projective_alignment(
    project: Project,
    matches: list[_Match],
    adjacency: dict[str, set[str]],
    variable_image_ids: list[str],
    anchor_ids: tuple[str, ...],
    initial_poses: dict[str, ImageTransform],
    initial_mappings: dict[str, MatrixTransform],
    config: SolverSettings,
    progress: Callable[[float], None] | None,
) -> AlignmentResult:
    import numpy as np
    from scipy.optimize import least_squares

    image_records = {image.id: image for image in project.images}
    pair_points: dict[tuple[str, str], tuple[list[tuple[float, float]], list[tuple[float, float]]]] = {}
    image_points: dict[str, list[tuple[float, float]]] = {image_id: [] for image_id in adjacency}
    for match in matches:
        image_points[match.source_image_id].append((match.source_x, match.source_y))
        image_points[match.target_image_id].append((match.target_x, match.target_y))
        pair = tuple(sorted((match.source_image_id, match.target_image_id)))
        first, second = pair_points.setdefault(pair, ([], []))
        if match.source_image_id == pair[0]:
            first.append((match.source_x, match.source_y))
            second.append((match.target_x, match.target_y))
        else:
            first.append((match.target_x, match.target_y))
            second.append((match.source_x, match.source_y))
    for pair, (first, second) in pair_points.items():
        if _projective_control_rank(first, second) < 8:
            raise ValueError(
                "Projective alignment needs four correspondences in general position for every image pair."
            )

    for image_id in variable_image_ids:
        image = image_records[image_id]
        if image.source_dimensions is None:
            raise ValueError(f"Projective alignment requires known source dimensions for '{image.name}'.")

    base_mappings = {
        image_id: initial_mappings.get(image_id) or MatrixTransform.from_similarity(initial_poses[image_id])
        for image_id in adjacency
    }
    mapped_controls = [
        base_mappings[match.source_image_id].map_point(match.source_x, match.source_y) for match in matches
    ]
    mapped_controls.extend(
        base_mappings[match.target_image_id].map_point(match.target_x, match.target_y) for match in matches
    )
    origin_x = sum(x for x, _ in mapped_controls) / len(mapped_controls)
    origin_y = sum(y for _, y in mapped_controls) / len(mapped_controls)
    target_spread = max(
        sqrt(sum((x - origin_x) ** 2 + (y - origin_y) ** 2 for x, y in mapped_controls) / (2 * len(mapped_controls))),
        1.0,
    )
    source_frames = {}
    for image_id, points in image_points.items():
        center_x = sum(x for x, _ in points) / len(points)
        center_y = sum(y for _, y in points) / len(points)
        spread = max(sqrt(sum((x - center_x) ** 2 + (y - center_y) ** 2 for x, y in points) / (2 * len(points))), 1.0)
        source_frames[image_id] = (center_x, center_y, spread)
    target_normalization = np.array((
        (1 / target_spread, 0.0, -origin_x / target_spread),
        (0.0, 1 / target_spread, -origin_y / target_spread),
        (0.0, 0.0, 1.0),
    ))
    normalizations = {}
    for image_id, (center_x, center_y, spread) in source_frames.items():
        normalizations[image_id] = np.array((
            (1 / spread, 0.0, -center_x / spread),
            (0.0, 1 / spread, -center_y / spread),
            (0.0, 0.0, 1.0),
        ))

    seed_sources: dict[str, list[tuple[float, float]]] = {image_id: [] for image_id in variable_image_ids}
    seed_targets: dict[str, list[tuple[float, float]]] = {image_id: [] for image_id in variable_image_ids}
    for match in matches:
        if match.source_image_id in seed_sources:
            seed_sources[match.source_image_id].append((match.source_x, match.source_y))
            seed_targets[match.source_image_id].append(
                base_mappings[match.target_image_id].map_point(match.target_x, match.target_y)
            )
        if match.target_image_id in seed_sources:
            seed_sources[match.target_image_id].append((match.target_x, match.target_y))
            seed_targets[match.target_image_id].append(
                base_mappings[match.source_image_id].map_point(match.source_x, match.source_y)
            )

    def normalized_parameters(image_id: str, mapping: MatrixTransform) -> list[float]:
        normalized = target_normalization @ np.asarray(mapping.coefficients).reshape((3, 3)) @ np.linalg.inv(normalizations[image_id])
        divisor = normalized[2, 2]
        if abs(divisor) <= 1e-12 * max(abs(value) for value in normalized.flat):
            raise ValueError("Projective seed maps the normalized image origin to infinity.")
        normalized /= divisor
        return [float(normalized[row, column]) for row in range(3) for column in range(3) if (row, column) != (2, 2)]

    initial_values = []
    for image_id in variable_image_ids:
        seed, rank = _normalized_dlt(seed_sources[image_id], seed_targets[image_id])
        if seed is None or rank < 8:
            seed = base_mappings[image_id]
        initial_values.extend(normalized_parameters(image_id, seed))

    def projective_parameters(parameters: list[float], offset: int) -> tuple[float, ...]:
        values = parameters[offset:offset + 8]
        return (*values[:6], values[6], values[7], 1.0)

    def predict_normalized(image_id: str, x: float, y: float, parameters: list[float], offsets: dict[str, int]):
        if image_id not in offsets:
            mapped_x, mapped_y = base_mappings[image_id].map_point(x, y)
            return (mapped_x - origin_x) / target_spread, (mapped_y - origin_y) / target_spread
        center_x, center_y, spread = source_frames[image_id]
        normalized_x, normalized_y = (x - center_x) / spread, (y - center_y) / spread
        h = projective_parameters(parameters, offsets[image_id])
        denominator = h[6] * normalized_x + h[7] * normalized_y + 1.0
        denominator_scale = abs(h[6] * normalized_x) + abs(h[7] * normalized_y) + 1.0
        if abs(denominator) < config.projective_horizon_margin * denominator_scale:
            return None
        mapped_x = (h[0] * normalized_x + h[1] * normalized_y + h[2]) / denominator
        mapped_y = (h[3] * normalized_x + h[4] * normalized_y + h[5]) / denominator
        if not isfinite(mapped_x) or not isfinite(mapped_y):
            return None
        return mapped_x, mapped_y

    offsets = {image_id: 8 * index for index, image_id in enumerate(variable_image_ids)}
    evaluation_count = [0]
    evaluation_budget = config.max_iterations * (len(initial_values) + 1)

    def residual_vector(parameters: list[float]) -> list[float]:
        if progress is not None:
            evaluation_count[0] += 1
            progress(min(0.99, evaluation_count[0] / evaluation_budget))
        residuals = []
        for match in matches:
            source = predict_normalized(match.source_image_id, match.source_x, match.source_y, parameters, offsets)
            target = predict_normalized(match.target_image_id, match.target_x, match.target_y, parameters, offsets)
            if source is None or target is None:
                return [1e6] * (2 * len(matches))
            residuals.extend((source[0] - target[0], source[1] - target[1]))
        return residuals

    optimization = least_squares(
        residual_vector, initial_values,
        max_nfev=config.max_iterations,
        method=config.optimizer_method,
        loss=config.loss,
        f_scale=config.loss_scale / target_spread,
        x_scale="jac" if config.parameter_scaling == "jacobian" else 1.0,
        ftol=config.ftol,
        xtol=config.xtol,
        gtol=config.gtol,
    )
    mappings = {image_id: base_mappings[image_id] for image_id in anchor_ids}
    for image_id in variable_image_ids:
        normalized_h = np.array(projective_parameters(optimization.x.tolist(), offsets[image_id])).reshape((3, 3))
        matrix = np.linalg.inv(target_normalization) @ normalized_h @ normalizations[image_id]
        divisor = matrix[2, 2]
        if abs(divisor) <= 1e-12 * max(abs(value) for value in matrix.flat):
            raise ValueError(f"Projective fit for '{image_records[image_id].name}' has an invalid homogeneous scale.")
        matrix /= divisor
        mapping = MatrixTransform(tuple(float(value) for value in matrix.flat), "projective")
        width, height = image_records[image_id].source_dimensions
        mapping.domain_bounds(width, height)
        for row in range(7):
            for column in range(7):
                x, y = width * column / 6, height * row / 6
                first, second, _, fourth, fifth, _, seventh, eighth, ninth = mapping.coefficients
                denominator = seventh * x + eighth * y + ninth
                denominator_scale = abs(seventh * x) + abs(eighth * y) + abs(ninth)
                if abs(denominator) < config.projective_horizon_margin * denominator_scale:
                    raise ValueError(f"Projective fit for '{image_records[image_id].name}' approaches a horizon inside the image.")
                jacobian = mapping.jacobian(x, y)
                a, b, c, d = jacobian
                trace = a * a + b * b + c * c + d * d
                determinant_squared = (a * d - b * c) ** 2
                discriminant = max(0.0, trace * trace - 4 * determinant_squared) ** 0.5
                maximum_stretch = max(0.0, (trace + discriminant) / 2) ** 0.5
                minimum_stretch = max(0.0, (trace - discriminant) / 2) ** 0.5
                if a * d - b * c < 0 and not config.projective_allow_reflection:
                    raise ValueError(f"Projective fit for '{image_records[image_id].name}' reflects the image.")
                if minimum_stretch < config.projective_min_stretch or maximum_stretch > config.projective_max_stretch:
                    raise ValueError(f"Projective fit for '{image_records[image_id].name}' exceeds the configured stretch limits.")
                if minimum_stretch <= 0 or maximum_stretch / minimum_stretch > config.projective_max_condition:
                    raise ValueError(f"Projective fit for '{image_records[image_id].name}' exceeds the configured condition limit.")
        mappings[image_id] = mapping

    residuals: dict[str, float] = {}
    for match in matches:
        source = mappings[match.source_image_id].map_point(match.source_x, match.source_y)
        target = mappings[match.target_image_id].map_point(match.target_x, match.target_y)
        residuals[match.constraint_id] = max(
            residuals.get(match.constraint_id, 0.0), hypot(source[0] - target[0], source[1] - target[1]),
        )
    compatibility_poses = {
        image_id: mapping.to_similarity() for image_id, mapping in mappings.items() if mapping.model == "similarity"
    }
    source_dimensions = {
        image_id: image_records[image_id].source_dimensions for image_id in mappings
        if image_records[image_id].source_dimensions is not None
    } or None
    return AlignmentResult(
        transforms=compatibility_poses,
        constraint_residuals=residuals,
        iterations=int(optimization.nfev),
        converged=bool(optimization.success and residuals and max(residuals.values()) <= config.tolerance),
        anchor_image_ids=anchor_ids,
        optimizer_status=int(optimization.status),
        optimizer_message=str(optimization.message),
        max_iterations=config.max_iterations,
        settings=config,
        matrix_transforms=mappings,
        source_dimensions=source_dimensions,
    )


def _image_components(adjacency: dict[str, set[str]], image_order: list[str]) -> list[list[str]]:
    remaining = set(adjacency)
    components = []
    for start in image_order:
        if start not in remaining:
            continue
        remaining.remove(start)
        component = [start]
        pending = [start]
        while pending:
            current = pending.pop()
            for neighbor in adjacency.get(current, set()):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.append(neighbor)
                    pending.append(neighbor)
        component.sort(key=image_order.index)
        components.append(component)
    return components


def _constraint_residuals(matches: list[_Match], transforms: dict[str, ImageTransform]) -> dict[str, float]:
    residuals: dict[str, float] = {}
    for match in matches:
        source_x, source_y = _apply_transform(
            match.source_x,
            match.source_y,
            transforms[match.source_image_id],
        )
        target_x, target_y = _apply_transform(
            match.target_x,
            match.target_y,
            transforms[match.target_image_id],
        )
        distance = hypot(source_x - target_x, source_y - target_y)
        residuals[match.constraint_id] = max(residuals.get(match.constraint_id, 0.0), distance)
    return residuals
