from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from math import atan2, degrees, hypot, isfinite, isclose
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from geostitch.transforms import MatrixTransform, similarity_mapping
from geostitch.mapping import ImageMapping
from geostitch.warp import WarpTransform

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
    source_dimensions: tuple[int, int] | None = None
    rest_intrinsic: MatrixTransform | None = None
    aligned_intrinsic: MatrixTransform | None = None
    aligned_warp: WarpTransform | None = None
    native_world_mapping: ImageMapping | None = None

    def __post_init__(self) -> None:
        if self.source_dimensions is not None:
            if not isinstance(self.source_dimensions, (list, tuple)) or len(self.source_dimensions) != 2 or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in self.source_dimensions
            ):
                raise ValueError("Image source dimensions must be two positive integers.")
            self.source_dimensions = tuple(self.source_dimensions)
        for name, matrix in (("rest", self.rest_intrinsic), ("aligned", self.aligned_intrinsic)):
            if matrix is not None:
                self._validate_layout_mapping(matrix)
                if (self.position_x if name == "rest" else self.aligned_x) is None:
                    raise ValueError("An intrinsic layout mapping requires an available outer pose.")
        if self.aligned_warp is not None:
            if not isinstance(self.aligned_warp, WarpTransform) or self.source_dimensions is None:
                raise ValueError("An aligned TPS mapping requires a validated warp and known source dimensions.")
            if self.aligned_intrinsic is not None or self.aligned_x is None:
                raise ValueError("An aligned TPS mapping cannot also use an intrinsic matrix mapping.")
            if not all(self.aligned_warp.supports((
                (0.0, 0.0), (float(self.source_dimensions[0]), 0.0),
                (float(self.source_dimensions[0]), float(self.source_dimensions[1])),
                (0.0, float(self.source_dimensions[1])),
            ))):
                raise ValueError("Aligned TPS support must cover the complete source image domain.")
        if self.native_world_mapping is not None:
            self._validate_native_world_mapping(self.native_world_mapping)

    def _validate_layout_mapping(self, mapping: MatrixTransform) -> None:
        if not isinstance(mapping, MatrixTransform):
            raise ValueError("Layout mappings require validated matrix transforms.")
        if not mapping.is_affine and self.source_dimensions is None:
            raise ValueError("Projective image mappings require known source dimensions.")
        if self.source_dimensions is not None:
            mapping.domain_bounds(*self.source_dimensions)

    def _validate_native_world_mapping(self, record: ImageMapping) -> None:
        if not isinstance(record, ImageMapping) or record.target_frame != "native_crs":
            raise ValueError("A native world mapping must target its declared CRS.")
        if self.source_dimensions is not None and record.source_size not in (None, self.source_dimensions):
            raise ValueError("Native world mapping dimensions disagree with the image.")

    def ensure_native_world_mapping(self, crs: str) -> None:
        mapping = self.world_mapping
        if mapping is None:
            self.native_world_mapping = None
        elif self.native_world_mapping is None:
            self.native_world_mapping = ImageMapping(mapping, "native_crs", crs)

    def set_layout_mapping(self, mapping: MatrixTransform | WarpTransform, *, aligned: bool = False) -> None:
        if isinstance(mapping, WarpTransform):
            if not aligned or self.source_dimensions is None:
                raise ValueError("TPS mappings are supported only for aligned layouts with known source dimensions.")
            base = mapping.base_mapping
            if base.model == "similarity":
                pose = base.to_similarity()
            else:
                origin_x, origin_y = base.map_point(0.0, 0.0)
                a, _b, d, _e = base.jacobian(0.0, 0.0)
                pose = (origin_x, origin_y, degrees(atan2(d, a)), hypot(a, d))
            self.aligned_x, self.aligned_y, self.aligned_rotation, self.aligned_scale = pose
            self.aligned_intrinsic = None
            self.aligned_warp = mapping
            return
        self._validate_layout_mapping(mapping)
        if mapping.model == "similarity":
            pose = mapping.to_similarity()
            intrinsic = None
        else:
            origin_x, origin_y = mapping.map_point(0, 0)
            pose = (origin_x, origin_y, 0.0, 1.0)
            intrinsic = similarity_mapping((-origin_x, -origin_y, 0, 1)).compose(mapping)
        if aligned:
            self.aligned_x, self.aligned_y, self.aligned_rotation, self.aligned_scale = pose
            self.aligned_intrinsic = intrinsic
            self.aligned_warp = None
        else:
            self.position_x, self.position_y, self.rotation, self.scale = pose
            self.rest_intrinsic = intrinsic

    @property
    def has_alignment(self) -> bool:
        return self.aligned_x is not None

    @property
    def rest_mapping(self) -> MatrixTransform | None:
        if self.position_x is None:
            return None
        pose = similarity_mapping((self.position_x, self.position_y, self.rotation, self.scale))
        return pose.compose(self.rest_intrinsic) if self.rest_intrinsic is not None else pose

    @property
    def aligned_mapping(self) -> MatrixTransform | WarpTransform | None:
        if self.aligned_x is None:
            return None
        if self.aligned_warp is not None:
            return self.aligned_warp
        pose = similarity_mapping((self.aligned_x, self.aligned_y, self.aligned_rotation, self.aligned_scale))
        return pose.compose(self.aligned_intrinsic) if self.aligned_intrinsic is not None else pose

    @property
    def world_mapping(self) -> MatrixTransform | None:
        if self.world_x is None or self.world_y is None:
            return None
        return similarity_mapping((self.world_x, self.world_y, self.world_rotation, self.world_scale), True)

    @property
    def mask_active(self) -> bool:
        return self.mask_enabled and len(self.mask_polygon) >= 3

    def to_dict(self, crs: str) -> dict[str, Any]:
        values = vars(self).copy()
        values.pop("rest_intrinsic")
        values.pop("aligned_intrinsic")
        values.pop("aligned_warp")
        native_world_mapping = values.pop("native_world_mapping")
        if native_world_mapping is None and self.world_mapping is not None:
            native_world_mapping = ImageMapping(self.world_mapping, "native_crs", crs)
        values["native_world_mapping"] = native_world_mapping.to_dict() if native_world_mapping is not None else None
        mappings = {}
        for name, fields, matrix in (
            ("rest", ("position_x", "position_y", "rotation", "scale"), self.rest_mapping),
            ("aligned", ("aligned_x", "aligned_y", "aligned_rotation", "aligned_scale"), self.aligned_mapping),
            ("world", ("world_x", "world_y", "world_rotation", "world_scale"), self.world_mapping),
        ):
            pose = [values.pop(field) for field in fields]
            record = ImageMapping(
                matrix, "native_crs" if name == "world" else "layout", crs if name == "world" else None,
                source_size=self.source_dimensions,
            ) if matrix is not None else None
            intrinsic = self.rest_intrinsic if name == "rest" else self.aligned_intrinsic if name == "aligned" else None
            intrinsic_record = ImageMapping(intrinsic, "layout", source_size=self.source_dimensions) if intrinsic is not None else None
            mappings[name] = {"pose": pose, "transform": record.to_dict() if record is not None else None,
                              "intrinsic": intrinsic_record.to_dict() if intrinsic_record is not None else None}
        values["mappings"] = mappings
        return json.loads(json.dumps(values))

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], crs: str, *, mapping_format: bool = False,
        allow_layout_matrices: bool = False, allow_native_world_mapping: bool = False,
        allow_layout_warps: bool = False,
    ) -> ImageRecord:
        if not isinstance(data, dict):
            raise ValueError("Saved image data must be an object.")
        if not mapping_format:
            values = dict(data)
            if "mappings" in values:
                return cls.from_dict(
                    values, crs, mapping_format=True, allow_layout_matrices=allow_layout_matrices,
                    allow_native_world_mapping=allow_native_world_mapping, allow_layout_warps=allow_layout_warps,
                )
            if "native_world_mapping" in values:
                raise ValueError("Native world mappings are not enabled for this project version.")
            image = cls(**values)
            image.ensure_native_world_mapping(crs)
            return image
        values = dict(data)
        mappings = values.pop("mappings", None)
        saved_native_world_mapping = values.pop("native_world_mapping", None)
        if allow_native_world_mapping != ("native_world_mapping" in data):
            raise ValueError("Native world mapping availability does not match the project version.")
        if not isinstance(mappings, dict) or set(mappings) != {"rest", "aligned", "world"}:
            raise ValueError("Saved images require rest, aligned, and world mapping states.")
        if "rest_intrinsic" in values or "aligned_intrinsic" in values:
            raise ValueError("Intrinsic mappings must be stored inside their layout records.")
        for name, fields in (
            ("rest", ("position_x", "position_y", "rotation", "scale")),
            ("aligned", ("aligned_x", "aligned_y", "aligned_rotation", "aligned_scale")),
            ("world", ("world_x", "world_y", "world_rotation", "world_scale")),
        ):
            if any(field in values for field in fields):
                raise ValueError("An image cannot contain conflicting legacy and matrix transform fields.")
            slot = mappings[name]
            if not isinstance(slot, dict):
                raise ValueError("Invalid saved image mapping state.")
            pose = slot.get("pose")
            if not isinstance(pose, list) or len(pose) != 4:
                raise ValueError("A compatibility pose requires four parameters.")
            for index, value in enumerate(pose):
                if value is None and (index == 0 or name == "world" and index == 1):
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                    raise ValueError("Saved image pose parameters must be finite numbers.")
            if pose[3] <= 0:
                raise ValueError("Saved image scale must be positive.")
            available = pose[0] is not None and (name != "world" or pose[1] is not None)
            saved_transform = slot.get("transform")
            saved_intrinsic = slot.get("intrinsic")
            if (saved_transform is not None) != available:
                raise ValueError("Image pose availability does not match its mapping.")
            if saved_intrinsic is not None and (not allow_layout_matrices or name == "world" or not available):
                raise ValueError("General image mappings are not enabled for this frame or project version.")
            if saved_transform is not None:
                record = ImageMapping.from_dict(saved_transform)
                expected_frame = "native_crs" if name == "world" else "layout"
                if record.target_frame != expected_frame or name == "world" and record.native_crs != crs:
                    raise ValueError("Image mapping frame or native CRS does not match the project.")
                source_dimensions = values.get("source_dimensions")
                if source_dimensions is not None:
                    if not isinstance(source_dimensions, (list, tuple)) or record.source_size != tuple(source_dimensions):
                        raise ValueError("Image and mapping source dimensions disagree.")
                elif record.source_size is not None:
                    raise ValueError("A mapping cannot introduce source dimensions absent from its image.")
                if isinstance(record.mapping, WarpTransform):
                    if name != "aligned" or not allow_layout_warps or saved_intrinsic is not None:
                        raise ValueError("Aligned TPS mappings are not enabled for this project version or frame.")
                    base = record.mapping.base_mapping
                    if base.model == "similarity":
                        expected_pose = base.to_similarity()
                    else:
                        origin_x, origin_y = base.map_point(0.0, 0.0)
                        a, _b, d, _e = base.jacobian(0.0, 0.0)
                        expected_pose = (origin_x, origin_y, degrees(atan2(d, a)), hypot(a, d))
                    if any(not isclose(actual, wanted, rel_tol=1e-13, abs_tol=1e-12)
                           for actual, wanted in zip(pose, expected_pose)):
                        raise ValueError("Aligned TPS base mapping and compatibility pose disagree.")
                    values["aligned_warp"] = record.mapping
                    expected = None
                else:
                    expected = similarity_mapping(tuple(pose), name == "world")
                if saved_intrinsic is not None:
                    intrinsic_record = ImageMapping.from_dict(saved_intrinsic)
                    if intrinsic_record.target_frame != "layout" or intrinsic_record.source_size != record.source_size:
                        raise ValueError("Intrinsic mapping frame or source dimensions disagree.")
                    if not isinstance(intrinsic_record.mapping, MatrixTransform):
                        raise ValueError("Intrinsic layout records must be matrix mappings.")
                    if not intrinsic_record.mapping.is_affine and intrinsic_record.source_size is None:
                        raise ValueError("Projective image mappings require known source dimensions.")
                    if expected is None:
                        raise ValueError("Aligned TPS mappings cannot also use an intrinsic mapping.")
                    expected = expected.compose(intrinsic_record.mapping)
                    values[f"{name}_intrinsic"] = intrinsic_record.mapping
                elif isinstance(record.mapping, MatrixTransform) and record.mapping.model != "similarity":
                    raise ValueError("General image mappings require an intrinsic layout record.")
                if expected is not None and (
                    not isinstance(record.mapping, MatrixTransform)
                    or any(not isclose(actual, wanted, rel_tol=1e-13, abs_tol=1e-12)
                           for actual, wanted in zip(record.mapping.coefficients, expected.coefficients))
                ):
                    raise ValueError("Image matrix and compatibility pose disagree.")
            values.update(zip(fields, pose))
        image = cls(**values)
        if allow_native_world_mapping:
            record = ImageMapping.from_dict(saved_native_world_mapping) if saved_native_world_mapping is not None else None
            if (image.world_mapping is None) != (record is None):
                raise ValueError("Native world mapping availability does not match its compatibility pose.")
            if record is not None:
                image._validate_native_world_mapping(record)
            image.native_world_mapping = record
        else:
            image.ensure_native_world_mapping(crs)
        return image

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
    geographic_native_x: float | None = None
    geographic_native_y: float | None = None
    geographic_native_crs: str | None = None
    geographic_checkpoint: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.geographic_checkpoint, bool):
            raise ValueError("Geographic checkpoint status must be true or false.")
        native = (self.geographic_native_x, self.geographic_native_y, self.geographic_native_crs)
        if any(value is not None for value in native):
            if not all(value is not None for value in native):
                raise ValueError("Native geographic coordinates require X, Y, and CRS together.")
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in native[:2]) or not all(isfinite(value) for value in native[:2]) or not isinstance(self.geographic_native_crs, str) or not self.geographic_native_crs.strip():
                raise ValueError("Native geographic coordinates must be finite and use a valid CRS definition.")

    def set_geographic_coordinates(self, x: float | None, y: float | None, crs: str) -> None:
        if (x is None) != (y is None):
            raise ValueError("Enter both geographic coordinates, or leave both empty.")
        if x is None:
            self.geographic_x = self.geographic_y = None
            self.geographic_native_x = self.geographic_native_y = None
            self.geographic_native_crs = None
            return
        if not isfinite(x) or not isfinite(y):
            raise ValueError("Geographic coordinates must be finite.")
        if not isinstance(crs, str) or not crs.strip():
            raise ValueError("A project CRS is required for geographic coordinates.")
        self.geographic_x, self.geographic_y = float(x), float(y)
        self.geographic_native_x, self.geographic_native_y = float(x), float(y)
        self.geographic_native_crs = crs

    def ensure_native_geographic_coordinates(self, crs: str) -> None:
        if self.geographic_x is None or self.geographic_y is None:
            self.geographic_native_x = self.geographic_native_y = None
            self.geographic_native_crs = None
        elif self.geographic_native_crs is None:
            self.set_geographic_coordinates(self.geographic_x, self.geographic_y, crs)

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
    affine_min_stretch: float = 0.05
    affine_max_stretch: float = 20.0
    affine_max_condition: float = 100.0
    affine_allow_reflection: bool = False
    projective_min_stretch: float = 0.05
    projective_max_stretch: float = 20.0
    projective_max_condition: float = 100.0
    projective_horizon_margin: float = 0.02
    projective_allow_reflection: bool = False
    warp_smoothing: float = 0.1
    warp_min_relative_area: float = 0.05
    warp_max_relative_stretch: float = 20.0

    def __post_init__(self) -> None:
        if not isfinite(self.tolerance) or self.tolerance <= 0:
            raise ValueError("Solver tolerance must be finite and greater than zero.")
        if not isinstance(self.max_iterations, int) or isinstance(self.max_iterations, bool) or not 1 <= self.max_iterations <= 10000:
            raise ValueError("Maximum solver iterations must be an integer from 1 to 10000.")
        if not isinstance(self.auto_apply, bool):
            raise ValueError("Automatic application must be true or false.")
        for value, choices, label in (
            (self.transform_model, ("translation", "rigid", "similarity", "affine", "projective", "tps"), "Transform model"),
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
        for value, label in (
            (self.affine_min_stretch, "Minimum affine stretch"),
            (self.affine_max_stretch, "Maximum affine stretch"),
            (self.affine_max_condition, "Maximum affine condition"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be finite and greater than zero.")
        if self.affine_max_condition < 1 or self.affine_min_stretch >= self.affine_max_stretch:
            raise ValueError("Affine limits require min stretch < max stretch and condition >= 1.")
        if not isinstance(self.affine_allow_reflection, bool):
            raise ValueError("Affine reflection policy must be true or false.")
        for value, label in (
            (self.projective_min_stretch, "Minimum projective stretch"),
            (self.projective_max_stretch, "Maximum projective stretch"),
            (self.projective_max_condition, "Maximum projective condition"),
            (self.projective_horizon_margin, "Projective horizon margin"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be finite and greater than zero.")
        if self.projective_max_condition < 1 or self.projective_min_stretch >= self.projective_max_stretch or self.projective_horizon_margin >= 1:
            raise ValueError("Projective limits require min stretch < max stretch, condition >= 1, and horizon margin < 1.")
        if not isinstance(self.projective_allow_reflection, bool):
            raise ValueError("Projective reflection policy must be true or false.")
        if isinstance(self.warp_smoothing, bool) or not isinstance(self.warp_smoothing, (int, float)) or not isfinite(self.warp_smoothing) or self.warp_smoothing < 0:
            raise ValueError("TPS smoothing must be finite and non-negative.")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0
            for value in (self.warp_min_relative_area, self.warp_max_relative_stretch)
        ) or self.warp_min_relative_area >= 1:
            raise ValueError("TPS validity limits require relative area in (0, 1) and positive maximum stretch.")
        if self.transform_model == "tps" and self.loss != "linear":
            raise ValueError("TPS fitting currently requires least-squares residual loss.")
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
        point.ensure_native_geographic_coordinates(self.crs)
        self.control_points.append(point)

    def set_point_geographic_coordinates(
        self, point: GroundControlPoint, x: float | None, y: float | None,
    ) -> None:
        if point not in self.control_points:
            raise ValueError("Control point does not belong to this project.")
        point.set_geographic_coordinates(x, y, self.crs)

    def ensure_native_geographic_coordinates(self) -> None:
        for point in self.control_points:
            point.ensure_native_geographic_coordinates(self.crs)

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
            "version": 27,
            "name": self.name,
            "crs": self.crs,
            "images": [image.to_dict(self.crs) for image in self.images],
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
                    "geographic_native_x": (point.geographic_native_x if point.geographic_native_x is not None else point.geographic_x) if point.geographic_x is not None and point.geographic_y is not None else None,
                    "geographic_native_y": (point.geographic_native_y if point.geographic_native_y is not None else point.geographic_y) if point.geographic_x is not None and point.geographic_y is not None else None,
                    "geographic_native_crs": (point.geographic_native_crs or self.crs) if point.geographic_x is not None and point.geographic_y is not None else None,
                    "geographic_checkpoint": point.geographic_checkpoint,
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
        if data.get("version") not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27):
            raise ValueError("This project uses an unsupported file version.")
        project = cls(name=data.get("name", "Untitled project"), crs=data.get("crs", "EPSG:4326"))
        project.images = [ImageRecord.from_dict(
            image, project.crs, mapping_format=data["version"] >= 18,
            allow_layout_matrices=data["version"] >= 19, allow_native_world_mapping=data["version"] >= 23,
            allow_layout_warps=data["version"] >= 27,
        ) for image in data.get("images", [])]
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
                geographic_native_x=(
                    point.get("geographic_native_x") if data["version"] >= 21 else point.get("geographic_x")
                ) if point.get("geographic_x") is not None and point.get("geographic_y") is not None else None,
                geographic_native_y=(
                    point.get("geographic_native_y") if data["version"] >= 21 else point.get("geographic_y")
                ) if point.get("geographic_x") is not None and point.get("geographic_y") is not None else None,
                geographic_native_crs=(
                    point.get("geographic_native_crs") if data["version"] >= 21 else project.crs
                ) if point.get("geographic_x") is not None and point.get("geographic_y") is not None else None,
                geographic_checkpoint=point.get("geographic_checkpoint", False) if data["version"] >= 26 else False,
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
        members = {image.id: image for image in project.images}
        for point in project.control_points:
            for observation in point.observations:
                image = members[observation.image_id]
                for name in ("rest", "aligned"):
                    if getattr(image, f"{name}_intrinsic") is not None:
                        mapping = getattr(image, f"{name}_mapping")
                        mapping.map_point(observation.x, observation.y)
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
                    geographic_native_x=point.geographic_native_x,
                    geographic_native_y=point.geographic_native_y,
                    geographic_native_crs=point.geographic_native_crs,
                    geographic_checkpoint=point.geographic_checkpoint,
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
