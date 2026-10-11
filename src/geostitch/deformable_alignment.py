from __future__ import annotations

from math import hypot
from typing import Callable

import numpy as np

from geostitch.adjustment import (
    AlignmentResult, ImageTransform, _Match, _mapping_compatibility_transform,
    _affine_control_rank,
)
from geostitch.models import Project, SolverSettings
from geostitch.transforms import MatrixTransform, similarity_mapping
from geostitch.warp import WarpTransform, _thin_plate_kernel


def simulate_tps_alignment(
    project: Project,
    matches: list[_Match],
    adjacency: dict[str, set[str]],
    initial_poses: dict[str, ImageTransform],
    initial_mappings: dict[str, MatrixTransform],
    anchor_ids: tuple[str, ...],
    settings: SolverSettings,
    progress: Callable[[float], None] | None,
) -> AlignmentResult:
    """Jointly fit regularized per-image TPS fields against shared tie constraints."""
    image_records = {image.id: image for image in project.images}
    bases = {
        image_id: initial_mappings.get(image_id) or MatrixTransform.from_similarity(initial_poses[image_id])
        for image_id in adjacency
    }
    if any(not mapping.is_affine for mapping in bases.values()):
        raise ValueError("TPS alignment requires affine or similarity starting mappings; projective layouts are unsupported.")

    controls: dict[str, tuple[tuple[float, float], ...]] = {}
    normalized_controls: dict[str, tuple[np.ndarray, float, np.ndarray]] = {}
    offsets: dict[str, int] = {}
    variable_ids = [image_id for image_id in adjacency if image_id not in set(anchor_ids)]
    coefficient_count = 0
    for image_id in adjacency:
        image = image_records[image_id]
        locations = {
            (match.source_x, match.source_y)
            for match in matches if match.source_image_id == image_id
        } | {
            (match.target_x, match.target_y)
            for match in matches if match.target_image_id == image_id
        }
        control_points = tuple(sorted(locations))
        if image_id not in anchor_ids:
            if image.source_dimensions is None:
                raise ValueError(f"TPS alignment requires known source dimensions for '{image.name}'.")
            if len(control_points) < 3 or _affine_control_rank(list(control_points)) < 3:
                raise ValueError(f"TPS alignment needs three non-collinear linked observations on '{image.name}'.")
        if not control_points:
            continue
        controls[image_id] = control_points
        values = np.asarray(control_points, dtype=float)
        center = values.mean(axis=0)
        spread = float(np.sqrt(np.mean(np.sum((values - center) ** 2, axis=1) / 2)))
        if spread <= 1e-12:
            raise ValueError(f"TPS alignment controls on '{image.name}' have no spatial extent.")
        normalized_controls[image_id] = (center, spread, (values - center) / spread)
        offsets[image_id] = coefficient_count
        if image_id in variable_ids:
            coefficient_count += len(control_points) + 3

    if not variable_ids or coefficient_count == 0:
        raise ValueError("TPS alignment has no non-reference image mappings to solve.")

    def basis(image_id: str, point: tuple[float, float]) -> np.ndarray:
        center, spread, normalized = normalized_controls[image_id]
        source = (np.asarray(point, dtype=float) - center) / spread
        return np.concatenate((_thin_plate_kernel(np.linalg.norm(normalized - source, axis=1)), source[:1] * 0 + 1, source))

    base_positions = []
    for match in matches:
        base_positions.extend((
            bases[match.source_image_id].map_point(match.source_x, match.source_y),
            bases[match.target_image_id].map_point(match.target_x, match.target_y),
        ))
    output_origin = np.asarray(base_positions, dtype=float).mean(axis=0)
    output_scale = float(np.sqrt(np.mean(np.sum((np.asarray(base_positions) - output_origin) ** 2, axis=1) / 2)))
    if not np.isfinite(output_scale) or output_scale <= 1e-12:
        raise ValueError("TPS alignment controls have no layout-space extent.")

    rows: list[np.ndarray] = []
    targets: list[np.ndarray] = []
    for match in matches:
        row = np.zeros(coefficient_count, dtype=float)
        if match.source_image_id in variable_ids:
            start = offsets[match.source_image_id]
            count = len(controls[match.source_image_id]) + 3
            row[start:start + count] += basis(match.source_image_id, (match.source_x, match.source_y))
        if match.target_image_id in variable_ids:
            start = offsets[match.target_image_id]
            count = len(controls[match.target_image_id]) + 3
            row[start:start + count] -= basis(match.target_image_id, (match.target_x, match.target_y))
        source_base = (
            np.asarray(bases[match.source_image_id].map_point(match.source_x, match.source_y)) - output_origin
        ) / output_scale
        target_base = (
            np.asarray(bases[match.target_image_id].map_point(match.target_x, match.target_y)) - output_origin
        ) / output_scale
        rows.append(row)
        targets.append(target_base - source_base)

    if settings.warp_smoothing > 0:
        penalty = settings.warp_smoothing ** 0.5
        for image_id in variable_ids:
            start = offsets[image_id]
            for index in range(len(controls[image_id])):
                row = np.zeros(coefficient_count, dtype=float)
                row[start + index] = penalty
                rows.append(row)
                targets.append(np.zeros(2, dtype=float))

    design = np.asarray(rows, dtype=float)
    target = np.asarray(targets, dtype=float)
    if progress is not None:
        progress(0.1)
    coefficients, _residuals, rank, _singular_values = np.linalg.lstsq(design, target, rcond=1e-12)
    if rank < coefficient_count:
        raise ValueError("TPS alignment coefficient system is rank deficient; add independent linked controls.")

    warps: dict[str, WarpTransform] = {}
    matrices = {image_id: bases[image_id] for image_id in anchor_ids if image_id in bases}
    dimensions = {}
    for image_id in adjacency:
        image = image_records[image_id]
        if image.source_dimensions is not None:
            dimensions[image_id] = image.source_dimensions
        if image_id not in variable_ids:
            continue
        control_count = len(controls[image_id])
        start = offsets[image_id]
        image_coefficients = coefficients[start:start + control_count + 3] * output_scale
        width, height = image.source_dimensions
        support = (*controls[image_id], (0.0, 0.0), (float(width), 0.0),
                   (float(width), float(height)), (0.0, float(height)))
        warp = WarpTransform.from_coefficients(
            bases[image_id], controls[image_id], image_coefficients[:control_count],
            image_coefficients[control_count:], smoothing=settings.warp_smoothing,
            support_points=tuple(dict.fromkeys(support)),
        )
        domain_samples = tuple(
            (width * column / 8, height * row / 8)
            for row in range(9) for column in range(9)
        )
        warp.validate_domain(
            domain_samples,
            minimum_relative_area=settings.warp_min_relative_area,
            maximum_relative_stretch=settings.warp_max_relative_stretch,
        )
        warps[image_id] = warp
        if progress is not None:
            progress(0.1 + 0.8 * (len(warps) / len(variable_ids)))

    residuals: dict[str, float] = {}
    for match in matches:
        source_mapping = warps.get(match.source_image_id, matrices.get(match.source_image_id))
        target_mapping = warps.get(match.target_image_id, matrices.get(match.target_image_id))
        if source_mapping is None or target_mapping is None:
            continue
        source = source_mapping.map_point(match.source_x, match.source_y)
        target = target_mapping.map_point(match.target_x, match.target_y)
        residuals[match.constraint_id] = max(
            residuals.get(match.constraint_id, 0.0), hypot(source[0] - target[0], source[1] - target[1]),
        )
    compatibility = {
        image_id: _mapping_compatibility_transform(bases[image_id])
        for image_id in warps
    }
    compatibility.update({
        image_id: mapping.to_similarity()
        for image_id, mapping in matrices.items() if mapping.model == "similarity"
    })
    if progress is not None:
        progress(0.99)
    return AlignmentResult(
        transforms=compatibility,
        constraint_residuals=residuals,
        iterations=1,
        converged=bool(residuals and max(residuals.values()) <= settings.tolerance),
        anchor_image_ids=anchor_ids,
        optimizer_status=1,
        optimizer_message="Coupled TPS coefficient system solved with fixed reference mappings and shared link constraints.",
        max_iterations=settings.max_iterations,
        settings=settings,
        matrix_transforms=matrices,
        warp_transforms=warps,
        source_dimensions=dimensions or None,
    )
