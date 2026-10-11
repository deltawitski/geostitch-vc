from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import isfinite
from typing import Iterable
import warnings

from geostitch.transforms import Coordinate, MatrixTransform
from geostitch.warp import InverseWarpTransform, WarpTransform


@dataclass(frozen=True)
class ImageMapping:
    mapping: MatrixTransform | WarpTransform
    target_frame: str
    native_crs: str | None = None
    source_frame: str = "source_pixels"
    source_size: tuple[int, int] | None = None
    matrix_crs: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mapping, (MatrixTransform, WarpTransform)):
            raise ValueError("An image mapping requires a validated matrix or warp transform.")
        if self.source_frame != "source_pixels" or self.target_frame not in ("layout", "native_crs"):
            raise ValueError("Unsupported image coordinate frame.")
        if self.target_frame == "native_crs":
            if not isinstance(self.native_crs, str) or not self.native_crs.strip():
                raise ValueError("A geographic mapping requires its native CRS.")
            CRSMapping(self.native_crs, self.native_crs)
            if self.matrix_crs is not None:
                CRSMapping(self.matrix_crs, self.native_crs)
        elif self.native_crs is not None:
            raise ValueError("A layout mapping cannot declare a geographic CRS.")
        elif self.matrix_crs is not None:
            raise ValueError("A layout mapping cannot declare a matrix CRS.")
        if self.source_size is not None:
            if len(self.source_size) != 2 or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in self.source_size
            ):
                raise ValueError("Source dimensions must be two positive integers.")
            object.__setattr__(self, "source_size", tuple(self.source_size))
            if isinstance(self.mapping, MatrixTransform):
                self.mapping.domain_bounds(*self.source_size)
            elif not all(self.mapping.supports((
                (0.0, 0.0), (float(self.source_size[0]), 0.0),
                (float(self.source_size[0]), float(self.source_size[1])),
                (0.0, float(self.source_size[1])),
            ))):
                raise ValueError("TPS support must cover the complete source image domain.")
        elif isinstance(self.mapping, WarpTransform):
            raise ValueError("TPS image mappings require known source dimensions.")

    def to_dict(self) -> dict:
        data = {
            "version": 3 if isinstance(self.mapping, WarpTransform) else 2 if self.matrix_crs is not None else 1,
            "source_frame": self.source_frame, "target_frame": self.target_frame,
            "native_crs": self.native_crs,
            "source_size": list(self.source_size) if self.source_size is not None else None,
            "mapping": self.mapping.to_dict(),
        }
        if self.matrix_crs is not None:
            data["matrix_crs"] = self.matrix_crs
        return data

    @property
    def mapping_chain(self) -> MappingChain:
        steps: tuple[MappingStep, ...] = (self.mapping,)
        if self.matrix_crs is not None:
            steps += (CRSMapping(self.matrix_crs, self.native_crs),)
        return MappingChain(steps)

    @classmethod
    def from_dict(cls, data: dict) -> ImageMapping:
        if not isinstance(data, dict) or data.get("version") not in (1, 2, 3):
            raise ValueError("Unsupported image mapping format.")
        matrix_crs = data.get("matrix_crs")
        if (data["version"] == 1 and matrix_crs is not None) or (
            data["version"] == 2 and (not isinstance(matrix_crs, str) or not matrix_crs.strip())
        ) or (data["version"] == 3 and matrix_crs is not None and (
            not isinstance(matrix_crs, str) or not matrix_crs.strip()
        )):
            raise ValueError("Invalid matrix coordinate CRS.")
        source_size = data.get("source_size")
        if source_size is not None and not isinstance(source_size, (list, tuple)):
            raise ValueError("Invalid source dimensions.")
        mapping = (
            WarpTransform.from_dict(data.get("mapping")) if data["version"] == 3
            else MatrixTransform.from_dict(data.get("mapping"))
        )
        return cls(
            mapping, data.get("target_frame"),
            data.get("native_crs"), data.get("source_frame"),
            tuple(source_size) if source_size is not None else None, matrix_crs,
        )


@lru_cache(maxsize=128)
def _crs_transformer(source_crs: str, target_crs: str):
    from pyproj import Transformer

    return Transformer.from_crs(source_crs, target_crs, always_xy=True)


@dataclass(frozen=True)
class CRSOperationInfo:
    description: str
    accuracy_meters: float | None
    area_of_use: str | None
    selected_grids: tuple[str, ...]
    unavailable_grids: tuple[str, ...]
    best_available: bool
    ballpark: bool

    def summary(self, source_crs: str, target_crs: str) -> str:
        lines = [f"CRS operation ({source_crs} to {target_crs}): {self.description or 'Unavailable'}"]
        accuracy = "unknown" if self.accuracy_meters is None else f"{self.accuracy_meters:g} m"
        lines.append(f"Reported operation accuracy: {accuracy}.")
        if self.area_of_use:
            lines.append(f"Area of use: {self.area_of_use}")
        if self.selected_grids:
            lines.append(f"Selected operation grids: {', '.join(self.selected_grids)}")
        if self.unavailable_grids:
            lines.append(
                "More accurate candidate operations require unavailable grids: "
                + ", ".join(self.unavailable_grids)
            )
        if self.ballpark:
            lines.append("The selected operation is a ballpark transformation.")
        elif not self.best_available:
            lines.append("The best available operation could not be selected.")
        return "\n".join(lines)


def _grid_names(operations, *, unavailable_only: bool) -> tuple[str, ...]:
    names = []
    for operation in operations:
        for grid in getattr(operation, "grids", ()):
            if not unavailable_only or not grid.available:
                names.append(grid.short_name or grid.url or "unnamed grid")
        names.extend(_grid_names(getattr(operation, "operations", ()), unavailable_only=unavailable_only))
    return tuple(dict.fromkeys(names))


@lru_cache(maxsize=128)
def _crs_operation_info(source_crs: str, target_crs: str) -> CRSOperationInfo:
    from pyproj.transformer import TransformerGroup

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Best transformation is not available.*")
            group = TransformerGroup(source_crs, target_crs, always_xy=True)
        selected = _crs_transformer(source_crs, target_crs)
    except Exception as error:
        return CRSOperationInfo(f"Metadata unavailable: {error}", None, None, (), (), False, False)
    description = selected.description or selected.name
    accuracy = selected.accuracy
    area = selected.area_of_use
    operations = selected.operations
    return CRSOperationInfo(
        description,
        accuracy if accuracy is not None and accuracy >= 0 else None,
        area.name if area is not None else None,
        _grid_names(operations, unavailable_only=False),
        _grid_names(group.unavailable_operations, unavailable_only=True),
        bool(group.best_available and group.transformers),
        "ballpark" in description.casefold(),
    )


@dataclass(frozen=True)
class CRSMapping:
    source_crs: str
    target_crs: str
    inverse_direction: bool = False

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (self.source_crs, self.target_crs)):
            raise ValueError("A CRS mapping requires source and target CRS definitions.")
        if not isinstance(self.inverse_direction, bool):
            raise ValueError("CRS mapping direction must be a boolean.")
        try:
            _crs_transformer(self.source_crs, self.target_crs)
        except Exception as error:
            raise ValueError(f"Invalid CRS mapping: {error}") from error

    def map_point(self, x: float, y: float) -> Coordinate:
        if not isfinite(x) or not isfinite(y):
            raise ValueError("Point coordinates must be finite.")
        try:
            from pyproj.enums import TransformDirection

            direction = TransformDirection.INVERSE if self.inverse_direction else TransformDirection.FORWARD
            mapped = _crs_transformer(self.source_crs, self.target_crs).transform(x, y, direction=direction, errcheck=True)
        except Exception as error:
            raise ValueError(f"Point cannot be transformed between these CRSs: {error}") from error
        if not all(isfinite(value) for value in mapped):
            raise ValueError("CRS transformation produced invalid coordinates.")
        return mapped

    @property
    def operation_info(self) -> CRSOperationInfo:
        return _crs_operation_info(self.source_crs, self.target_crs)

    def inverse(self) -> CRSMapping:
        return CRSMapping(self.source_crs, self.target_crs, not self.inverse_direction)

    def to_dict(self) -> dict:
        return {"version": 1, "source_crs": self.source_crs, "target_crs": self.target_crs,
            "inverse_direction": self.inverse_direction}

    @classmethod
    def from_dict(cls, data: dict) -> CRSMapping:
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Unsupported CRS mapping format.")
        return cls(data.get("source_crs"), data.get("target_crs"), data.get("inverse_direction", False))


MappingStep = MatrixTransform | CRSMapping | WarpTransform | InverseWarpTransform


@dataclass(frozen=True)
class MappingChain:
    steps: tuple[MappingStep, ...]

    def __post_init__(self) -> None:
        if len(self.steps) > 32 or any(
            not isinstance(step, (MatrixTransform, CRSMapping, WarpTransform, InverseWarpTransform))
            for step in self.steps
        ):
            raise ValueError("A mapping chain supports at most 32 validated mapping steps.")
        object.__setattr__(self, "steps", tuple(self.steps))

    def map_point(self, x: float, y: float) -> Coordinate:
        if not isfinite(x) or not isfinite(y):
            raise ValueError("Point coordinates must be finite.")
        for step in self.steps:
            x, y = step.map_point(x, y)
        return x, y

    def map_points(self, points: Iterable[Coordinate]) -> tuple[Coordinate | None, ...]:
        mapped: list[Coordinate | None] = []
        for x, y in points:
            try:
                mapped.append(self.map_point(x, y))
            except ValueError:
                mapped.append(None)
        return tuple(mapped)

    def inverse(self) -> MappingChain:
        return MappingChain(tuple(step.inverse() for step in reversed(self.steps)))

    def compose(self, inner: MappingChain) -> MappingChain:
        return MappingChain((*inner.steps, *self.steps))

    def to_matrix(self) -> MatrixTransform:
        matrix = MatrixTransform.identity()
        for step in self.steps:
            if not isinstance(step, MatrixTransform):
                raise ValueError("A nonlinear mapping chain cannot be reduced to a matrix without approximation.")
            matrix = step.compose(matrix)
        return matrix

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "steps": [
                {
                    "kind": (
                        "matrix" if isinstance(step, MatrixTransform) else
                        "crs" if isinstance(step, CRSMapping) else
                        "warp" if isinstance(step, WarpTransform) else "inverse_warp"
                    ),
                    "mapping": step.to_dict(),
                }
                for step in self.steps
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> MappingChain:
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Unsupported mapping chain format.")
        saved_steps = data.get("steps")
        if not isinstance(saved_steps, list) or len(saved_steps) > 32:
            raise ValueError("Invalid saved mapping steps.")
        steps: list[MappingStep] = []
        for saved in saved_steps:
            if not isinstance(saved, dict) or saved.get("kind") not in (
                "matrix", "crs", "warp", "inverse_warp",
            ):
                raise ValueError("Unsupported mapping chain step.")
            mapping = saved.get("mapping")
            kind = saved["kind"]
            if kind == "matrix":
                steps.append(MatrixTransform.from_dict(mapping))
            elif kind == "crs":
                steps.append(CRSMapping.from_dict(mapping))
            elif kind == "warp":
                steps.append(WarpTransform.from_dict(mapping))
            else:
                steps.append(InverseWarpTransform.from_dict(mapping))
        return cls(tuple(steps))