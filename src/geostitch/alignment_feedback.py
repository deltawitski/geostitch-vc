from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Any

from geostitch.adjustment import AlignmentResult, ImageTransform
from geostitch.models import Project


def residual_color(value: float, tolerance: float) -> str:
    if value <= tolerance:
        return "#2f9e68"
    if value <= tolerance * 2:
        return "#c28b19"
    return "#d44f54"


def _input_signature(project: Project) -> tuple:
    return (
        tuple(sorted(
            (image.id, image.position_x, image.position_y, image.rotation, image.scale)
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
            "version": 2,
            "result": {
                "transforms": {image_id: list(transform) for image_id, transform in self.result.transforms.items()},
                "constraint_residuals": dict(self.result.constraint_residuals),
                "iterations": self.result.iterations,
                "converged": self.result.converged,
                "anchor_image_ids": list(self.result.anchor_image_ids),
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
        if not isinstance(data, dict) or data.get("version") not in (1, 2):
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
        iterations = saved_result["iterations"]
        if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 0 or not isinstance(saved_result["converged"], bool):
            raise ValueError("Invalid alignment solver status.")
        anchors = saved_result["anchor_image_ids"]
        if not isinstance(anchors, list) or not all(isinstance(image_id, str) for image_id in anchors):
            raise ValueError("Invalid alignment reference image ids.")
        result = AlignmentResult(
            transforms, _read_residuals(saved_result["constraint_residuals"]),
            iterations, saved_result["converged"], tuple(anchors),
        )
        groups = data["input_signature"]
        if not isinstance(groups, list) or len(groups) != 3:
            raise ValueError("Invalid saved alignment inputs.")
        signature = []
        for group, size in zip(groups, (5, 4, 3)):
            if not isinstance(group, list) or any(not isinstance(entry, list) or len(entry) != size for entry in group):
                raise ValueError("Invalid saved alignment inputs.")
            string_count = 1 if size == 5 else 2 if size == 4 else 3
            for entry in group:
                if not all(isinstance(value, str) for value in entry[:string_count]):
                    raise ValueError("Invalid saved alignment input ids.")
                for index, value in enumerate(entry[string_count:], start=string_count):
                    if size == 5 and index == 1 and value is None:
                        continue
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                        raise ValueError("Saved alignment input positions must be finite numbers.")
                if size == 5 and entry[4] <= 0:
                    raise ValueError("Saved alignment input scale must be positive.")
            signature.append(tuple(tuple(entry) for entry in group))
        input_signature = tuple(signature)
        point_residuals, image_residuals = _residual_summaries(result, input_signature)
        return cls(result, tolerance, point_residuals, image_residuals, input_signature)

    def is_current(self, project: Project) -> bool:
        return self.input_signature == _input_signature(project)

    def matches_layout(self, project: Project, current: bool | None = None) -> bool:
        images = {image.id: image for image in project.images}
        for image_id, transform in self.result.transforms.items():
            image = images.get(image_id)
            if image is None or not image.has_alignment:
                return False
            if (image.aligned_x, image.aligned_y, image.aligned_rotation, image.aligned_scale) != transform:
                return False
        return self.is_current(project) if current is None else current