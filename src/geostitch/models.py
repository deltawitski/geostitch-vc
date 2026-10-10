from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4


if TYPE_CHECKING:
    from geostitch.alignment_feedback import AlignmentFeedback


@dataclass
class ImageRecord:
    id: str
    path: str
    name: str
    position_x: float | None = None
    position_y: float = 0.0
    rotation: float = 0.0
    scale: float = 1.0
    world_x: float | None = None
    world_y: float | None = None
    world_rotation: float = 0.0
    world_scale: float = 1.0
    mask_polygon: list[list[float]] = field(default_factory=list)
    mask_mode: str = "inside"
    mask_enabled: bool = False
    aligned_x: float | None = None
    aligned_y: float = 0.0
    aligned_rotation: float = 0.0
    aligned_scale: float = 1.0
    visible: bool = True
    map_visible: bool = True
    rest_locked: bool = False

    @property
    def has_alignment(self) -> bool:
        return self.aligned_x is not None

    @property
    def mask_active(self) -> bool:
        return self.mask_enabled and len(self.mask_polygon) >= 3

    @classmethod
    def from_path(cls, path: str | Path) -> ImageRecord:
        resolved = Path(path).expanduser().resolve()
        return cls(id=str(uuid4()), path=str(resolved), name=resolved.name)


@dataclass
class VectorLayer:
    """GeoJSON file shown on the world map only; `visible` controls its map visibility."""

    id: str
    path: str
    name: str
    visible: bool = True

    @classmethod
    def from_path(cls, path: str | Path) -> VectorLayer:
        resolved = Path(path).expanduser().resolve()
        return cls(id=str(uuid4()), path=str(resolved), name=resolved.name)


@dataclass
class Observation:
    image_id: str
    x: float
    y: float
    locked: bool = False


@dataclass
class GroundControlPoint:
    id: str
    name: str
    geographic_x: float | None = None
    geographic_y: float | None = None
    observations: list[Observation] = field(default_factory=list)
    label: str | None = None
    visible: bool = True
    map_visible: bool = True
    short_name: str | None = None

    @property
    def display_name(self) -> str:
        return self.short_name or self.name

    @property
    def image_id(self) -> str | None:
        """The single image this point is placed in, if any."""
        return self.observations[0].image_id if self.observations else None

    @classmethod
    def create(cls, name: str, label: str | None = None) -> GroundControlPoint:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("A control point needs a name.")
        normalized_label = label.strip() if label else ""
        return cls(id=str(uuid4()), name=normalized_name, label=normalized_label or None)

    def observation_for(self, image_id: str) -> Observation | None:
        return next((item for item in self.observations if item.image_id == image_id), None)

    def set_observation(self, image_id: str, x: float, y: float) -> None:
        existing = self.observation_for(image_id)
        if existing is not None and existing.locked:
            raise ValueError("This image observation is locked.")
        if existing is None:
            self.observations.append(Observation(image_id=image_id, x=float(x), y=float(y)))
        else:
            existing.x = float(x)
            existing.y = float(y)


@dataclass
class PointConstraint:
    id: str
    first_point_id: str
    second_point_id: str
    kind: str = "tie"
    name: str | None = None

    @classmethod
    def create(cls, first_point_id: str, second_point_id: str, kind: str = "tie") -> PointConstraint:
        if first_point_id == second_point_id:
            raise ValueError("A point cannot be constrained to itself.")
        return cls(
            id=str(uuid4()),
            first_point_id=first_point_id,
            second_point_id=second_point_id,
            kind=kind,
        )


@dataclass(frozen=True)
class SolverSettings:
    tolerance: float = 0.5
    max_iterations: int = 100
    auto_apply: bool = True
    transform_model: str = "similarity"
    optimizer_method: str = "trf"
    loss: str = "linear"
    loss_scale: float = 1.0
    scale_min: float | None = None
    scale_max: float | None = None
    ftol: float = 1e-10
    xtol: float = 1e-10
    gtol: float = 1e-10
    parameter_scaling: str = "unit"

    def __post_init__(self) -> None:
        if not isfinite(self.tolerance) or self.tolerance <= 0:
            raise ValueError("Solver tolerance must be finite and greater than zero.")
        if not isinstance(self.max_iterations, int) or isinstance(self.max_iterations, bool) or not 1 <= self.max_iterations <= 10000:
            raise ValueError("Maximum solver iterations must be an integer from 1 to 10000.")
        if not isinstance(self.auto_apply, bool):
            raise ValueError("Automatic application must be true or false.")
        for value, choices, label in (
            (self.transform_model, ("translation", "rigid", "similarity"), "Transform model"),
            (self.optimizer_method, ("trf", "dogbox"), "Optimizer method"),
            (self.loss, ("linear", "soft_l1", "huber", "cauchy"), "Robust loss"),
            (self.parameter_scaling, ("unit", "jacobian"), "Parameter scaling"),
        ):
            if value not in choices:
                raise ValueError(f"{label} is not supported.")
        for value, label in (
            (self.loss_scale, "Robust loss threshold"), (self.ftol, "Error improvement threshold"),
            (self.xtol, "Transform change threshold"), (self.gtol, "Gradient threshold"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be finite and greater than zero.")
        if min(self.ftol, self.xtol, self.gtol) < 1e-14:
            raise ValueError("Convergence thresholds must be at least 1e-14.")
        for value in (self.scale_min, self.scale_max):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0
            ):
                raise ValueError("Scale bounds must be finite and greater than zero.")
        if self.scale_min is not None and self.scale_max is not None and self.scale_min >= self.scale_max:
            raise ValueError("Minimum scale must be less than maximum scale.")

    @classmethod
    def from_dict(cls, data: dict[str, Any], defaults: SolverSettings) -> SolverSettings:
        if not isinstance(data, dict):
            raise ValueError("Solver settings must be an object.")
        values = {name: data.get(name, value) for name, value in vars(defaults).items()}
        values["tolerance"] = float(values["tolerance"])
        return cls(**values)


@dataclass
class Project:
    name: str = "Untitled project"
    crs: str = "EPSG:3857"
    images: list[ImageRecord] = field(default_factory=list)
    control_points: list[GroundControlPoint] = field(default_factory=list)
    constraints: list[PointConstraint] = field(default_factory=list)
    vector_layers: list[VectorLayer] = field(default_factory=list)
    layer_order: list[str] = field(default_factory=list)
    views: dict[str, Any] = field(default_factory=dict)
    canvas_state: str = "rest"
    alignment_feedback: AlignmentFeedback | None = None
    alignment_settings: SolverSettings = field(default_factory=SolverSettings)
    georeference_settings: SolverSettings = field(default_factory=lambda: SolverSettings(1.0, 1000))
    next_link_number: int = 1

    def add_image(self, path: str | Path) -> ImageRecord:
        resolved = str(Path(path).expanduser().resolve())
        existing = next((image for image in self.images if image.path == resolved), None)
        if existing is not None:
            return existing
        image = ImageRecord.from_path(resolved)
        self.images.append(image)
        return image

    def stacked_layer_ids(self) -> list[str]:
        """Image and GeoJSON layer ids from bottom to top; unlisted layers go on top, vectors above new images."""
        known = [image.id for image in self.images] + [layer.id for layer in self.vector_layers]
        valid = set(known)
        order = [item for item in dict.fromkeys(self.layer_order) if item in valid]
        listed = set(order)
        return order + [item for item in known if item not in listed]

    def point_by_id(self, point_id: str) -> GroundControlPoint | None:
        return next((point for point in self.control_points if point.id == point_id), None)

    def next_control_point_name(self, image_id: str) -> str:
        image = next((item for item in self.images if item.id == image_id), None)
        if image is None:
            raise ValueError("Select an image before generating a control-point name.")
        numbers = [
            int(match.group(1))
            for point in self.control_points if point.image_id == image_id
            if (match := re.fullmatch(r"P(\d+)", point.display_name, re.IGNORECASE)) is not None
        ]
        return f"P{max(numbers, default=0) + 1}"

    def unique_control_point_name(self, name: str) -> str:
        """Return `name`, or the next free name by incrementing its trailing number."""
        used_names = {point.name.casefold() for point in self.control_points}
        candidate = name.strip()
        if candidate.casefold() not in used_names:
            return candidate
        match = re.fullmatch(r"(.*?)(\d+)", candidate)
        if match:
            prefix, digits = match.groups()
            number, width = int(digits) + 1, len(digits)
        else:
            prefix, number, width = f"{candidate}-", 2, 1
        while f"{prefix}{number:0{width}d}".casefold() in used_names:
            number += 1
        return f"{prefix}{number:0{width}d}"

    def add_control_point(self, point: GroundControlPoint) -> None:
        if any(item.name.casefold() == point.name.casefold() for item in self.control_points):
            raise ValueError(f"A control point named '{point.name}' already exists.")
        self.control_points.append(point)

    def add_constraint(self, constraint: PointConstraint) -> None:
        point_ids = {point.id for point in self.control_points}
        if constraint.first_point_id not in point_ids or constraint.second_point_id not in point_ids:
            raise ValueError("Both constrained points must belong to this project.")
        if constraint.first_point_id == constraint.second_point_id:
            raise ValueError("A point cannot be constrained to itself.")
        if any(
            {item.first_point_id, item.second_point_id}
            == {constraint.first_point_id, constraint.second_point_id}
            and item.kind == constraint.kind
            for item in self.constraints
        ):
            raise ValueError("Those points are already connected by this constraint.")
        used_names = {item.name for item in self.constraints}
        if constraint.name is None:
            while f"L{self.next_link_number}" in used_names:
                self.next_link_number += 1
            constraint.name = f"L{self.next_link_number}"
            self.next_link_number += 1
        elif constraint.name in used_names:
            raise ValueError("Link names must be unique.")
        if match := re.fullmatch(r"L(\d+)", constraint.name):
            self.next_link_number = max(self.next_link_number, int(match.group(1)) + 1)
        self.constraints.append(constraint)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 17,
            "name": self.name,
            "crs": self.crs,
            "images": [json.loads(json.dumps(vars(image))) for image in self.images],
            "control_points": [
                {
                    "id": point.id,
                    "name": point.name,
                    "short_name": point.short_name,
                    "label": point.label,
                    "visible": point.visible,
                    "map_visible": point.map_visible,
                    "geographic_x": point.geographic_x,
                    "geographic_y": point.geographic_y,
                    "observations": [vars(observation).copy() for observation in point.observations],
                }
                for point in self.control_points
            ],
            "constraints": [vars(constraint).copy() for constraint in self.constraints],
            "next_link_number": self.next_link_number,
            "vector_layers": [vars(layer).copy() for layer in self.vector_layers],
            "layer_order": self.stacked_layer_ids(),
            "views": json.loads(json.dumps(self.views)),
            "canvas_state": self.canvas_state,
            "alignment_feedback": self.alignment_feedback.to_dict() if self.alignment_feedback is not None else None,
            "solver_settings": {
                "alignment": vars(self.alignment_settings).copy(),
                "georeference": vars(self.georeference_settings).copy(),
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        if data.get("version") not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17):
            raise ValueError("This project uses an unsupported file version.")
        project = cls(name=data.get("name", "Untitled project"), crs=data.get("crs", "EPSG:4326"))
        project.images = [ImageRecord(**image) for image in data.get("images", [])]
        project.vector_layers = [
            VectorLayer(id=layer["id"], path=layer["path"], name=layer["name"], visible=bool(layer.get("visible", True)))
            for layer in data.get("vector_layers", [])
        ]
        project.layer_order = [str(item) for item in data.get("layer_order", [])]
        views = data.get("views", {})
        project.views = views if isinstance(views, dict) else {}
        project.canvas_state = "aligned" if data.get("canvas_state") == "aligned" else "rest"
        feedback = data.get("alignment_feedback")
        if feedback is not None and not isinstance(feedback, dict):
            raise ValueError("Saved alignment feedback must be an object.")
        if feedback is not None:
            from geostitch.alignment_feedback import AlignmentFeedback

            project.alignment_feedback = AlignmentFeedback.from_dict(feedback)
        settings = data.get("solver_settings", {})
        if not isinstance(settings, dict):
            raise ValueError("Project solver settings must be an object.")
        project.alignment_settings = SolverSettings.from_dict(settings.get("alignment", {}), project.alignment_settings)
        project.georeference_settings = SolverSettings.from_dict(settings.get("georeference", {}), project.georeference_settings)
        project.control_points = [
            GroundControlPoint(
                id=point["id"],
                name=point["name"],
                short_name=point.get("short_name"),
                label=point.get("label"),
                visible=bool(point.get("visible", True)),
                map_visible=bool(point.get("map_visible", True)),
                geographic_x=point.get("geographic_x"),
                geographic_y=point.get("geographic_y"),
                observations=[Observation(**observation) for observation in point.get("observations", [])],
            )
            for point in data.get("control_points", [])
        ]
        image_ids = {image.id for image in project.images}
        if any(observation.image_id not in image_ids for point in project.control_points for observation in point.observations):
            raise ValueError("A control point refers to an image that is not in this project.")
        next_number = data.get("next_link_number", 1)
        if not isinstance(next_number, int) or isinstance(next_number, bool) or next_number < 1:
            raise ValueError("Invalid next link number.")
        project.next_link_number = next_number
        for constraint_data in data.get("constraints", []):
            project.add_constraint(PointConstraint(**constraint_data))
        project._split_shared_points()
        return project

    def _split_shared_points(self) -> None:
        """Older files let one point sit in several images; turn each extra image into its own point tied to the original."""
        images = {image.id: image for image in self.images}
        used_names = {point.name.casefold() for point in self.control_points}
        groups: dict[str, list[GroundControlPoint]] = {}
        for point in self.control_points:
            group = [point]
            for observation in point.observations[1:]:
                stem = Path(images[observation.image_id].name).stem
                name, number = f"{point.name}-{stem}", 2
                while name.casefold() in used_names:
                    name, number = f"{point.name}-{stem}-{number}", number + 1
                used_names.add(name.casefold())
                group.append(GroundControlPoint(
                    id=str(uuid4()),
                    name=name,
                    label=point.label,
                    geographic_x=point.geographic_x,
                    geographic_y=point.geographic_y,
                    observations=[observation],
                ))
            point.observations = point.observations[:1]
            groups[point.id] = group
        if all(len(group) == 1 for group in groups.values()):
            return
        self.control_points = [point for group in groups.values() for point in group]
        old_constraints, self.constraints = self.constraints, []
        linked: set[tuple[frozenset[str], str]] = set()

        def link(first: GroundControlPoint, second: GroundControlPoint, kind: str,
             original: PointConstraint | None = None) -> bool:
            key = (frozenset((first.id, second.id)), kind)
            if first.id == second.id or key in linked:
                return False
            linked.add(key)
            constraint = PointConstraint.create(first.id, second.id, kind)
            if original is not None:
                constraint.id = original.id
                constraint.name = original.name
            self.add_constraint(constraint)
            return True

        for group in groups.values():
            for clone in group[1:]:
                link(group[0], clone, "tie")
        for constraint in old_constraints:
            first_group = groups[constraint.first_point_id]
            second_group = groups[constraint.second_point_id]
            created = False
            for first in first_group:
                for second in second_group:
                    if first.image_id != second.image_id:
                        created = link(first, second, constraint.kind, constraint if not created else None) or created
            if not created:
                link(first_group[0], second_group[0], constraint.kind, constraint)
