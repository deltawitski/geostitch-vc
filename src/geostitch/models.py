from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4


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


@dataclass
class Project:
    name: str = "Untitled project"
    crs: str = "EPSG:4326"
    images: list[ImageRecord] = field(default_factory=list)
    control_points: list[GroundControlPoint] = field(default_factory=list)
    constraints: list[PointConstraint] = field(default_factory=list)
    views: dict[str, Any] = field(default_factory=dict)
    canvas_state: str = "rest"

    def add_image(self, path: str | Path) -> ImageRecord:
        resolved = str(Path(path).expanduser().resolve())
        existing = next((image for image in self.images if image.path == resolved), None)
        if existing is not None:
            return existing
        image = ImageRecord.from_path(resolved)
        self.images.append(image)
        return image

    def point_by_id(self, point_id: str) -> GroundControlPoint | None:
        return next((point for point in self.control_points if point.id == point_id), None)

    def next_control_point_name(self, image_id: str) -> str:
        image = next((item for item in self.images if item.id == image_id), None)
        if image is None:
            raise ValueError("Select an image before generating a control-point name.")
        prefix = Path(image.name).stem or "image"
        used_names = {point.name.casefold() for point in self.control_points}
        number = sum(point.observation_for(image_id) is not None for point in self.control_points) + 1
        while f"{prefix}-{number:03d}".casefold() in used_names:
            number += 1
        return f"{prefix}-{number:03d}"

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
        self.constraints.append(constraint)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 10,
            "name": self.name,
            "crs": self.crs,
            "images": [json.loads(json.dumps(vars(image))) for image in self.images],
            "control_points": [
                {
                    "id": point.id,
                    "name": point.name,
                    "label": point.label,
                    "geographic_x": point.geographic_x,
                    "geographic_y": point.geographic_y,
                    "observations": [vars(observation).copy() for observation in point.observations],
                }
                for point in self.control_points
            ],
            "constraints": [vars(constraint).copy() for constraint in self.constraints],
            "views": json.loads(json.dumps(self.views)),
            "canvas_state": self.canvas_state,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        if data.get("version") not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
            raise ValueError("This project uses an unsupported file version.")
        project = cls(name=data.get("name", "Untitled project"), crs=data.get("crs", "EPSG:4326"))
        project.images = [ImageRecord(**image) for image in data.get("images", [])]
        views = data.get("views", {})
        project.views = views if isinstance(views, dict) else {}
        project.canvas_state = "aligned" if data.get("canvas_state") == "aligned" else "rest"
        project.control_points = [
            GroundControlPoint(
                id=point["id"],
                name=point["name"],
                label=point.get("label"),
                geographic_x=point.get("geographic_x"),
                geographic_y=point.get("geographic_y"),
                observations=[Observation(**observation) for observation in point.get("observations", [])],
            )
            for point in data.get("control_points", [])
        ]
        image_ids = {image.id for image in project.images}
        if any(observation.image_id not in image_ids for point in project.control_points for observation in point.observations):
            raise ValueError("A control point refers to an image that is not in this project.")
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

        def link(first: GroundControlPoint, second: GroundControlPoint, kind: str) -> bool:
            key = (frozenset((first.id, second.id)), kind)
            if first.id == second.id or key in linked:
                return False
            linked.add(key)
            self.constraints.append(PointConstraint.create(first.id, second.id, kind))
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
                        created = link(first, second, constraint.kind) or created
            if not created:
                link(first_group[0], second_group[0], constraint.kind)
