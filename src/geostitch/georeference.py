from __future__ import annotations

from dataclasses import dataclass
from math import cos, degrees, hypot, radians, sin
from cmath import phase
from typing import Callable

from geostitch.models import Project

WorldTransform = tuple[float, float, float, float]
ObservationKey = tuple[str, str]


@dataclass(frozen=True)
class GeoreferenceResult:
    transforms: dict[str, WorldTransform]
    observation_residuals: dict[ObservationKey, float]
    iterations: int
    converged: bool
    crs: str

    @property
    def maximum_residual(self) -> float:
        return max(self.observation_residuals.values(), default=0.0)


@dataclass(frozen=True)
class _ControlMatch:
    image_id: str
    point_id: str
    pixel_x: float
    pixel_y: float
    world_x: float
    world_y: float


def simulate_georeference(
    project: Project,
    tolerance: float = 1.0,
    max_iterations: int = 1000,
    initial_transforms: dict[str, WorldTransform] | None = None,
    progress: Callable[[float], None] | None = None,
) -> GeoreferenceResult:
    """Jointly fit per-image pixel-to-world similarity transforms to every GCP observation."""
    if tolerance <= 0:
        raise ValueError("Tolerance must be greater than zero.")
    if max_iterations < 1:
        raise ValueError("Maximum iterations must be at least one.")

    from scipy.optimize import least_squares

    matches = [
        _ControlMatch(
            image_id=observation.image_id,
            point_id=point.id,
            pixel_x=observation.x,
            pixel_y=-observation.y,
            world_x=point.geographic_x,
            world_y=point.geographic_y,
        )
        for point in project.control_points
        if point.geographic_x is not None and point.geographic_y is not None
        for observation in point.observations
    ]
    if not matches:
        raise ValueError("No control points have geographic X and Y coordinates and image observations.")

    world_origin_x = sum(match.world_x for match in matches) / len(matches)
    world_origin_y = sum(match.world_y for match in matches) / len(matches)

    # Images linked by constraints form a rigid chunk solved as one element in canvas space.
    chunk_of, frames = _chunk_frames(project)
    original_matches = matches
    matches = []
    for match in original_matches:
        frame_x, frame_y = _apply_world_transform(match.pixel_x, match.pixel_y, frames[match.image_id])
        matches.append(_ControlMatch(
            image_id=chunk_of[match.image_id],
            point_id=match.point_id,
            pixel_x=frame_x,
            pixel_y=frame_y,
            world_x=match.world_x,
            world_y=match.world_y,
        ))
    matches_by_image: dict[str, list[_ControlMatch]] = {}
    for match in matches:
        matches_by_image.setdefault(match.image_id, []).append(match)
    chunk_ids = list(dict.fromkeys(chunk_of[image.id] for image in project.images if chunk_of[image.id] in matches_by_image))

    chunk_initial: dict[str, WorldTransform] = {}
    for image in project.images:
        given = (initial_transforms or {}).get(image.id)
        chunk_id = chunk_of[image.id]
        if given is not None and chunk_id not in chunk_initial:
            chunk_initial[chunk_id] = _compose(given, _invert(frames[image.id]))

    initial: dict[str, WorldTransform] = {}
    variable_ids: list[str] = []
    underconstrained_ids: set[str] = set()
    values: list[float] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []
    for chunk_id in chunk_ids:
        image_matches = matches_by_image[chunk_id]
        current = chunk_initial.get(chunk_id) or _closed_form_fit(image_matches) or (
            world_origin_x,
            world_origin_y,
            0.0,
            1.0,
        )
        initial[chunk_id] = current
        unique_pixels = {(round(match.pixel_x, 9), round(match.pixel_y, 9)) for match in image_matches}
        if len(unique_pixels) < 2:
            underconstrained_ids.add(chunk_id)
        variable_ids.append(chunk_id)
        x, y, rotation, scale = current
        if chunk_id in underconstrained_ids:
            values.extend((x - world_origin_x, y - world_origin_y))
            lower_bounds.extend((-float("inf"), -float("inf")))
            upper_bounds.extend((float("inf"), float("inf")))
        else:
            values.extend((x - world_origin_x, y - world_origin_y, radians(rotation), scale))
            lower_bounds.extend((-float("inf"), -float("inf"), -float("inf"), 1e-12))
            upper_bounds.extend((float("inf"), float("inf"), float("inf"), float("inf")))

    def unpack(parameters: list[float]) -> dict[str, WorldTransform]:
        result = dict(initial)
        offset = 0
        for image_id in variable_ids:
            if image_id in underconstrained_ids:
                result[image_id] = (
                    parameters[offset] + world_origin_x,
                    parameters[offset + 1] + world_origin_y,
                    initial[image_id][2],
                    initial[image_id][3],
                )
                offset += 2
            else:
                result[image_id] = (
                    parameters[offset] + world_origin_x,
                    parameters[offset + 1] + world_origin_y,
                    degrees(parameters[offset + 2]),
                    parameters[offset + 3],
                )
                offset += 4
        return result

    evaluations = [0]
    budget = max_iterations * (len(values) + 1)

    def residual_vector(parameters: list[float]) -> list[float]:
        if progress is not None:
            evaluations[0] += 1
            progress(min(0.99, evaluations[0] / budget))
        transforms = unpack(parameters)
        residuals: list[float] = []
        for match in matches:
            predicted_x, predicted_y = _apply_world_transform(
                match.pixel_x,
                match.pixel_y,
                transforms[match.image_id],
            )
            residuals.extend((
                (predicted_x - world_origin_x) - (match.world_x - world_origin_x),
                (predicted_y - world_origin_y) - (match.world_y - world_origin_y),
            ))
        return residuals

    optimization = least_squares(
        residual_vector,
        values,
        bounds=(lower_bounds, upper_bounds),
        max_nfev=max_iterations,
        ftol=1e-10,
        xtol=1e-10,
        gtol=1e-10,
    )
    chunk_transforms = unpack(optimization.x.tolist())
    transforms = {
        image.id: _compose(chunk_transforms[chunk_of[image.id]], frames[image.id])
        for image in project.images
        if chunk_of[image.id] in chunk_transforms
    }
    observation_residuals: dict[ObservationKey, float] = {}
    for match in original_matches:
        predicted_x, predicted_y = _apply_world_transform(
            match.pixel_x,
            match.pixel_y,
            transforms[match.image_id],
        )
        observation_residuals[(match.image_id, match.point_id)] = hypot(
            predicted_x - match.world_x,
            predicted_y - match.world_y,
        )

    return GeoreferenceResult(
        transforms=transforms,
        observation_residuals=observation_residuals,
        iterations=optimization.nfev,
        converged=optimization.success and max(observation_residuals.values(), default=0.0) <= tolerance,
        crs=project.crs,
    )


def _closed_form_fit(matches: list[_ControlMatch]) -> WorldTransform | None:
    """Least-squares similarity (complex linear) fit used to seed the solver."""
    source = [complex(match.pixel_x, match.pixel_y) for match in matches]
    target = [complex(match.world_x, match.world_y) for match in matches]
    source_mean = sum(source) / len(source)
    target_mean = sum(target) / len(target)
    variance = sum(abs(value - source_mean) ** 2 for value in source)
    if variance < 1e-12:
        return None
    factor = sum((t - target_mean) * (s - source_mean).conjugate() for s, t in zip(source, target)) / variance
    if abs(factor) < 1e-12:
        return None
    offset = target_mean - factor * source_mean
    return (offset.real, offset.imag, degrees(phase(factor)), abs(factor))


def _compose(outer: WorldTransform, inner: WorldTransform) -> WorldTransform:
    x, y = _apply_world_transform(inner[0], inner[1], outer)
    return (x, y, outer[2] + inner[2], outer[3] * inner[3])


def _invert(transform: WorldTransform) -> WorldTransform:
    x, y, rotation, scale = transform
    inverse_x, inverse_y = _apply_world_transform(-x, -y, (0.0, 0.0, -rotation, 1.0 / scale))
    return (inverse_x, inverse_y, -rotation, 1.0 / scale)


def _chunk_frames(project: Project) -> tuple[dict[str, str], dict[str, WorldTransform]]:
    """Map each image to its constraint-connected chunk id and its canvas transform (identity if unconstrained)."""
    points = {point.id: point for point in project.control_points}
    adjacency: dict[str, set[str]] = {}
    for constraint in project.constraints:
        first = points.get(constraint.first_point_id)
        second = points.get(constraint.second_point_id)
        if first is None or second is None:
            continue
        for first_observation in first.observations:
            for second_observation in second.observations:
                if first_observation.image_id != second_observation.image_id:
                    adjacency.setdefault(first_observation.image_id, set()).add(second_observation.image_id)
                    adjacency.setdefault(second_observation.image_id, set()).add(first_observation.image_id)

    chunk_of: dict[str, str] = {}
    frames: dict[str, WorldTransform] = {}
    for image in project.images:
        if image.id in chunk_of:
            continue
        chunk_of[image.id] = image.id
        frames[image.id] = (0.0, 0.0, 0.0, 1.0)
        pending = [image.id]
        while pending:
            for neighbor in adjacency.get(pending.pop(), ()):
                if neighbor not in chunk_of:
                    chunk_of[neighbor] = image.id
                    pending.append(neighbor)
    members = {image.id: image for image in project.images}
    for image_id, chunk_id in chunk_of.items():
        if image_id != chunk_id or adjacency.get(image_id):
            image = members[image_id]
            frames[image_id] = (
                image.aligned_x if image.has_alignment else (image.position_x if image.position_x is not None else 0.0),
                -(image.aligned_y if image.has_alignment else image.position_y),
                -(image.aligned_rotation if image.has_alignment else image.rotation),
                image.aligned_scale if image.has_alignment else image.scale,
            )
    return chunk_of, frames


def _apply_world_transform(x: float, y: float, transform: WorldTransform) -> tuple[float, float]:
    world_x, world_y, rotation, scale = transform
    angle = radians(rotation)
    return (
        world_x + scale * (cos(angle) * x - sin(angle) * y),
        world_y + scale * (sin(angle) * x + cos(angle) * y),
    )


