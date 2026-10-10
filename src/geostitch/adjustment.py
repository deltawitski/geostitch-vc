from __future__ import annotations

from dataclasses import dataclass
from math import degrees, hypot, radians
from typing import Callable

from geostitch.models import Project, SolverSettings
from geostitch.transforms import apply_similarity_transform as _apply_transform

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

    @property
    def maximum_residual(self) -> float:
        return max(self.constraint_residuals.values(), default=0.0)


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
) -> AlignmentResult:
    """Jointly align constrained images with the configured transform and loss.

    One image in each connected image component is held fixed to remove the free
    global translation/rotation/scale. Every linked observation contributes to the
    same residual vector. Image pixels are not resampled or warped.
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

    components = _image_components(adjacency, image_order)
    anchor_ids = tuple(component[0] for component in components)
    anchor_set = set(anchor_ids)
    variable_image_ids = [image_id for image_id in image_order if image_id in adjacency and image_id not in anchor_set]
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
