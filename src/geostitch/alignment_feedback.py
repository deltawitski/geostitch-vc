from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt, isclose
from typing import Any

from geostitch.adjustment import AlignmentResult, ImageTransform
from geostitch.models import Project, SolverSettings
from geostitch.mapping import ImageMapping
from geostitch.transforms import MatrixTransform, similarity_mapping
from geostitch.warp import WarpTransform


def residual_color(value: float, tolerance: float) -> str:
    if value <= tolerance:
        return "#2f9e68"
    if value <= tolerance * 2:
        return "#c28b19"
    return "#d44f54"


def _input_signature(project: Project) -> tuple:
    return (
        tuple(sorted(
            (image.id, image.position_x, image.position_y, image.rotation, image.scale,
             image.rest_intrinsic.coefficients if image.rest_intrinsic is not None else None)
            for image in project.images
        )),
        tuple(sorted(
            (point.id, observation.image_id, observation.x, observation.y)
            for point in project.control_points for observation in point.observations
        )),
        tuple(sorted(
            (link.id, link.first_point_id, link.second_point_id)
            for link in project.constraints
        )),
    )


def _read_residuals(data: dict) -> dict[str, float]:
    if not isinstance(data, dict):
        raise ValueError("Alignment residuals must be an object.")
    values = {str(item_id): float(value) for item_id, value in data.items()}
    if any(not isfinite(value) or value < 0 for value in values.values()):
        raise ValueError("Alignment residuals must be finite and non-negative.")
    return values


def _residual_summaries(result: AlignmentResult, signature: tuple) -> tuple[dict[str, float], dict[str, float]]:
    image_ids = {entry[0] for entry in signature[0]}
    point_images: dict[str, set[str]] = {}
    for point_id, image_id, _, _ in signature[1]:
        if image_id in image_ids:
            point_images.setdefault(point_id, set()).add(image_id)
    point_residuals: dict[str, float] = {}
    image_links: dict[str, set[str]] = {}
    for link_id, first_point_id, second_point_id in signature[2]:
        residual = result.constraint_residuals.get(link_id)
        if residual is None or first_point_id not in point_images or second_point_id not in point_images:
            continue
        for point_id in (first_point_id, second_point_id):
            point_residuals[point_id] = max(point_residuals.get(point_id, 0.0), residual)
            for image_id in point_images[point_id]:
                image_links.setdefault(image_id, set()).add(link_id)
    image_residuals = {
        image_id: sqrt(sum(result.constraint_residuals[link_id] ** 2 for link_id in links) / len(links))
        for image_id, links in image_links.items()
    }
    return point_residuals, image_residuals


@dataclass(frozen=True)
class AlignmentFeedback:
    result: AlignmentResult
    tolerance: float
    point_residuals: dict[str, float]
    image_residuals: dict[str, float]
    input_signature: tuple

    @classmethod
    def capture(cls, project: Project, result: AlignmentResult, tolerance: float) -> AlignmentFeedback:
        if not isfinite(tolerance) or tolerance <= 0:
            raise ValueError("Tolerance must be greater than zero.")
        signature = _input_signature(project)
        point_residuals, image_residuals = _residual_summaries(result, signature)
        return cls(result, tolerance, point_residuals, image_residuals, signature)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 5,
            "result": {
                "transforms": {image_id: list(transform) for image_id, transform in self.result.transforms.items()},
                "mappings": {image_id: record.to_dict() for image_id, record in self.result.mapping_records.items()},
                "constraint_residuals": dict(self.result.constraint_residuals),
                "iterations": self.result.iterations,
                "converged": self.result.converged,
                "anchor_image_ids": list(self.result.anchor_image_ids),
                "optimizer_status": self.result.optimizer_status,
                "optimizer_message": self.result.optimizer_message,
                "max_iterations": self.result.max_iterations,
                "settings": vars(self.result.settings).copy() if self.result.settings is not None else None,
            },
            "tolerance": self.tolerance,
            "input_signature": [[list(entry) for entry in group] for group in self.input_signature],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlignmentFeedback:
        try:
            return cls._from_dict(data)
        except (KeyError, TypeError, OverflowError) as error:
            raise ValueError("Invalid saved alignment feedback.") from error

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> AlignmentFeedback:
        if not isinstance(data, dict) or data.get("version") not in (1, 2, 3, 4, 5):
            raise ValueError("Unsupported alignment feedback version.")
        tolerance = float(data["tolerance"])
        if not isfinite(tolerance) or tolerance <= 0:
            raise ValueError("Alignment tolerance must be finite and greater than zero.")
        saved_result = data["result"]
        if not isinstance(saved_result, dict) or not isinstance(saved_result.get("transforms"), dict):
            raise ValueError("Saved alignment transforms must be an object.")
        transforms: dict[str, ImageTransform] = {}
        for image_id, values in saved_result["transforms"].items():
            if not isinstance(values, (list, tuple)) or len(values) != 4:
                raise ValueError("An alignment transform must contain four values.")
            position_x, position_y, rotation, scale = (float(value) for value in values)
            transform = (position_x, position_y, rotation, scale)
            if not all(isfinite(value) for value in transform) or transform[3] <= 0:
                raise ValueError("An alignment transform must be finite with a positive scale.")
            transforms[str(image_id)] = transform
        matrices = None
        warps = None
        source_dimensions = None
        if data["version"] >= 3:
            saved_mappings = saved_result.get("mappings")
            if not isinstance(saved_mappings, dict):
                raise ValueError("Saved alignment mappings must be an object.")
            matrices = {}
            warps = {}
            dimensions = {}
            for image_id, saved_mapping in saved_mappings.items():
                record = ImageMapping.from_dict(saved_mapping)
                if record.target_frame != "layout":
                    raise ValueError("An alignment result must map into layout coordinates.")
                if isinstance(record.mapping, WarpTransform):
                    if data["version"] < 5:
                        raise ValueError("Saved TPS alignment mappings require feedback version 5.")
                    warps[str(image_id)] = record.mapping
                else:
                    matrices[str(image_id)] = record.mapping
                if record.source_size is not None:
                    dimensions[str(image_id)] = record.source_size
            source_dimensions = dimensions or None
            for image_id, transform in transforms.items():
                mapping = warps.get(image_id, matrices.get(image_id))
                if mapping is None:
                    raise ValueError("Saved alignment mapping is missing a compatibility transform.")
                if isinstance(mapping, WarpTransform):
                    from geostitch.adjustment import _mapping_compatibility_transform

                    expected = _mapping_compatibility_transform(mapping.base_mapping)
                    matches = all(isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-12)
                                  for actual, wanted in zip(transform, expected))
                else:
                    from geostitch.adjustment import _mapping_compatibility_transform

                    expected = _mapping_compatibility_transform(mapping)
                    matches = all(isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-12)
                                  for actual, wanted in zip(transform, expected))
                if not matches:
                    raise ValueError("Saved solver matrix and compatibility transform disagree.")
            if not warps and set(matrices) == set(transforms) and all(matrix.model == "similarity" for matrix in matrices.values()):
                matrices = None
        iterations = saved_result["iterations"]
        if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 0 or not isinstance(saved_result["converged"], bool):
            raise ValueError("Invalid alignment solver status.")
        anchors = saved_result["anchor_image_ids"]
        if not isinstance(anchors, list) or not all(isinstance(image_id, str) for image_id in anchors):
            raise ValueError("Invalid alignment reference image ids.")
        optimizer_status = saved_result.get("optimizer_status")
        if optimizer_status is not None and (
            not isinstance(optimizer_status, int) or isinstance(optimizer_status, bool)
            or optimizer_status not in (-1, 0, 1, 2, 3, 4)
        ):
            raise ValueError("Invalid alignment optimizer status.")
        optimizer_message = saved_result.get("optimizer_message", "")
        if not isinstance(optimizer_message, str):
            raise ValueError("Invalid alignment optimizer message.")
        max_iterations = saved_result.get("max_iterations")
        if max_iterations is not None and (
            not isinstance(max_iterations, int) or isinstance(max_iterations, bool)
            or not 1 <= max_iterations <= 10000
        ):
            raise ValueError("Invalid alignment evaluation limit.")
        settings_data = saved_result.get("settings")
        if settings_data is not None and (
            not isinstance(settings_data, dict) or set(settings_data) != set(vars(SolverSettings()))
        ):
            raise ValueError("Saved alignment solver settings are incomplete or contain unknown fields.")
        result = AlignmentResult(
            transforms, _read_residuals(saved_result["constraint_residuals"]),
            iterations, saved_result["converged"], tuple(anchors),
            optimizer_status, optimizer_message, max_iterations,
            SolverSettings.from_dict(saved_result["settings"], SolverSettings()) if saved_result.get("settings") is not None else None,
            matrices,
            source_dimensions,
            warps or None,
        )
        groups = data["input_signature"]
        if not isinstance(groups, list) or len(groups) != 3:
            raise ValueError("Invalid saved alignment inputs.")
        signature = []
        for group, size in zip(groups, (6, 4, 3)):
            allowed_sizes = (5, 6) if size == 6 and data["version"] < 4 else (size,)
            if not isinstance(group, list) or any(not isinstance(entry, list) or len(entry) not in allowed_sizes for entry in group):
                raise ValueError("Invalid saved alignment inputs.")
            string_count = 1 if size == 6 else 2 if size == 4 else 3
            entries = []
            for saved_entry in group:
                entry = list(saved_entry)
                if size == 6 and len(entry) == 5:
                    entry.append(None)
                if not all(isinstance(value, str) for value in entry[:string_count]):
                    raise ValueError("Invalid saved alignment input ids.")
                for index, value in enumerate(entry[string_count:], start=string_count):
                    if size == 6 and index == 5:
                        if value is not None:
                            if not isinstance(value, (list, tuple)) or len(value) != 9:
                                raise ValueError("Invalid saved intrinsic mapping signature.")
                            entry[index] = MatrixTransform(tuple(value)).coefficients
                        continue
                    if size == 6 and index == 1 and value is None:
                        continue
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                        raise ValueError("Saved alignment input positions must be finite numbers.")
                if size == 6 and entry[4] <= 0:
                    raise ValueError("Saved alignment input scale must be positive.")
                entries.append(tuple(entry))
            signature.append(tuple(entries))
        input_signature = tuple(signature)
        point_residuals, image_residuals = _residual_summaries(result, input_signature)
        return cls(result, tolerance, point_residuals, image_residuals, input_signature)

    def is_current(self, project: Project) -> bool:
        return self.input_signature == _input_signature(project)

    def matches_layout(self, project: Project, current: bool | None = None) -> bool:
        images = {image.id: image for image in project.images}
        for image_id, matrix in self.result.mappings.items():
            image = images.get(image_id)
            if image is None or not image.has_alignment:
                return False
            applied = image.aligned_mapping
            if applied is None:
                return False
            if isinstance(matrix, WarpTransform):
                if not isinstance(applied, WarpTransform) or applied.to_dict() != matrix.to_dict():
                    return False
            elif not isinstance(applied, MatrixTransform) or any(
                not isclose(actual, wanted, rel_tol=1e-13, abs_tol=1e-12)
                for actual, wanted in zip(applied.coefficients, matrix.coefficients)
            ):
                return False
        return self.is_current(project) if current is None else current