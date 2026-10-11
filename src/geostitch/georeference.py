from __future__ import annotations

from dataclasses import dataclass, replace
from math import atan2, cos, degrees, hypot, isclose, isfinite, radians, sin
from cmath import phase
from typing import Any, Callable
import warnings

from geostitch.models import Project, SolverSettings
from geostitch.mapping import CRSMapping, ImageMapping, MappingChain
from geostitch.transforms import MatrixTransform, similarity_mapping
from geostitch.transforms import apply_similarity_transform as _apply_world_transform
from geostitch.warp import WarpTransform

WorldTransform = tuple[float, float, float, float]
ObservationKey = tuple[str, str]
LOCAL_METRIC_WARNING_RADIUS_M = 100_000.0
LOCAL_METRIC_WARNING_DISTORTION = 0.001


@dataclass(frozen=True)
class GeoreferenceDistanceUnit:
    dimension: str
    conversion_to_si: float | None
    label: str


def georeference_distance_unit(crs: str, settings: SolverSettings | str) -> GeoreferenceDistanceUnit:
    from pyproj import CRS

    coordinate_system = CRS.from_user_input(crs)
    transform_model = settings.transform_model if isinstance(settings, SolverSettings) else settings
    if transform_model in ("affine", "projective", "tps") and coordinate_system.is_geographic:
        return GeoreferenceDistanceUnit("linear", 1.0, "local metres")
    axes = coordinate_system.axis_info[:2]
    dimension = "angular" if coordinate_system.is_geographic else "linear" if coordinate_system.is_projected else "unknown"
    if len(axes) != 2:
        return GeoreferenceDistanceUnit(dimension, None, "CRS units")
    factors = [axis.unit_conversion_factor for axis in axes]
    conversion = None
    if all(isinstance(factor, (int, float)) and isfinite(factor) and factor > 0 for factor in factors):
        conversion = factors[0] if all(
            isclose(factor, factors[0], rel_tol=1e-12, abs_tol=1e-15) for factor in factors
        ) else None
    names = [axis.unit_name.casefold() for axis in axes]
    label = {
        "degree": "degrees", "radian": "radians", "grad": "grads",
        "metre": "metres", "meter": "metres", "foot": "feet",
        "us survey foot": "US survey feet", "international foot": "feet",
        "kilometre": "kilometres", "kilometer": "kilometres",
    }.get(names[0], axes[0].unit_name)
    if names[0] != names[1]:
        label = f"{axes[0].unit_name} / {axes[1].unit_name}"
    return GeoreferenceDistanceUnit(dimension, conversion, label)


def rebase_georeference_settings(
    settings: SolverSettings, source_crs: str, target_crs: str,
) -> SolverSettings | None:
    source_unit = georeference_distance_unit(source_crs, settings)
    target_unit = georeference_distance_unit(target_crs, settings)
    if (
        source_unit.dimension != target_unit.dimension
        or source_unit.conversion_to_si is None
        or target_unit.conversion_to_si is None
    ):
        return None
    factor = source_unit.conversion_to_si / target_unit.conversion_to_si
    if isclose(factor, 1.0, rel_tol=1e-14, abs_tol=1e-15):
        return settings

    def convert(value: float | None) -> float | None:
        return None if value is None else value * factor

    return replace(
        settings,
        tolerance=settings.tolerance * factor,
        loss_scale=settings.loss_scale * factor,
        scale_min=convert(settings.scale_min),
        scale_max=convert(settings.scale_max),
        affine_min_stretch=settings.affine_min_stretch * factor,
        affine_max_stretch=settings.affine_max_stretch * factor,
        projective_min_stretch=settings.projective_min_stretch * factor,
        projective_max_stretch=settings.projective_max_stretch * factor,
    )


@dataclass(frozen=True)
class LocalMetricFrameInfo:
    crs: str
    center_longitude: float
    center_latitude: float
    maximum_control_radius_m: float
    sampled_distance_distortion: float
    maximum_domain_radius_m: float | None = None
    sampled_domain_distance_distortion: float | None = None
    domain_sample_count: int | None = None
    domain_diagnostic_error: str | None = None

    def __post_init__(self) -> None:
        from pyproj import CRS

        if not isinstance(self.crs, str) or not self.crs.strip():
            raise ValueError("A local metric frame requires a CRS definition.")
        frame_crs = CRS.from_user_input(self.crs)
        if not frame_crs.is_projected or not frame_crs.axis_info or any(
            abs(axis.unit_conversion_factor - 1.0) > 1e-12 for axis in frame_crs.axis_info[:2]
        ):
            raise ValueError("Local metric frame coordinates must use projected metre units.")
        if not all(isfinite(value) for value in (
            self.center_longitude, self.center_latitude,
            self.maximum_control_radius_m, self.sampled_distance_distortion,
        )):
            raise ValueError("Local metric frame diagnostics must be finite.")
        if not -180 <= self.center_longitude <= 180 or not -90 <= self.center_latitude <= 90:
            raise ValueError("Local metric frame center must be a valid longitude and latitude.")
        if self.maximum_control_radius_m < 0 or self.sampled_distance_distortion < 0:
            raise ValueError("Local metric frame extent and distortion cannot be negative.")
        if any(value is not None and (not isfinite(value) or value < 0) for value in (
            self.maximum_domain_radius_m, self.sampled_domain_distance_distortion,
        )):
            raise ValueError("Local metric domain extent and distortion must be finite and non-negative.")
        if self.domain_sample_count is not None and (
            isinstance(self.domain_sample_count, bool) or self.domain_sample_count < 0
        ):
            raise ValueError("Local metric domain sample count cannot be negative.")
        if self.domain_diagnostic_error is not None and not isinstance(self.domain_diagnostic_error, str):
            raise ValueError("Local metric domain diagnostic errors must be text.")

    @property
    def needs_extent_warning(self) -> bool:
        return self.maximum_control_radius_m > LOCAL_METRIC_WARNING_RADIUS_M or (
            self.sampled_distance_distortion > LOCAL_METRIC_WARNING_DISTORTION
        ) or (
            self.maximum_domain_radius_m is not None
            and self.maximum_domain_radius_m > LOCAL_METRIC_WARNING_RADIUS_M
        ) or (
            self.sampled_domain_distance_distortion is not None
            and self.sampled_domain_distance_distortion > LOCAL_METRIC_WARNING_DISTORTION
        ) or self.domain_diagnostic_error is not None or self.domain_sample_count == 0


def _measure_metric_frame_domains(
    metric_frames: dict[str, LocalMetricFrameInfo],
    mappings: dict[str, MatrixTransform | WarpTransform],
    chunk_of: dict[str, str],
    source_dimensions: dict[str, tuple[int, int]],
    project_crs: str,
) -> dict[str, LocalMetricFrameInfo]:
    """Sample fitted image domains in their local frame; this is a warning heuristic, not a bound."""
    from pyproj import CRS

    geographic = CRSMapping(project_crs, "EPSG:4326")
    geod = CRS.from_user_input(project_crs).get_geod()
    results = {}
    for chunk_id, frame in metric_frames.items():
        local_to_project = CRSMapping(frame.crs, project_crs)
        candidates = []
        missing_dimensions = any(
            owner == chunk_id and image_id not in source_dimensions
            for image_id, owner in chunk_of.items()
        )
        try:
            for image_id, mapping in mappings.items():
                if chunk_of.get(image_id) != chunk_id:
                    continue
                if image_id not in source_dimensions:
                    continue
                width, height = source_dimensions[image_id]
                for row in range(5):
                    for column in range(5):
                        x, y = width * column / 4, height * row / 4
                        candidates.append(mapping.map_point(x, y))
            if len(candidates) > 64:
                candidates = [candidates[round(index * (len(candidates) - 1) / 63)] for index in range(64)]
            if candidates:
                project_coordinates = [local_to_project.map_point(*point) for point in candidates]
                geographic_coordinates = [geographic.map_point(*point) for point in project_coordinates]
                maximum_radius = max(hypot(x, y) for x, y in candidates)
                maximum_distortion = 0.0
                for first_index, first in enumerate(geographic_coordinates):
                    for second_index in range(first_index + 1, len(geographic_coordinates)):
                        _azimuth, _back_azimuth, geodesic_distance = geod.inv(*first, *geographic_coordinates[second_index])
                        if geodesic_distance > 0.01:
                            planar_distance = hypot(
                                candidates[first_index][0] - candidates[second_index][0],
                                candidates[first_index][1] - candidates[second_index][1],
                            )
                            maximum_distortion = max(
                                maximum_distortion,
                                abs(planar_distance / geodesic_distance - 1.0),
                            )
                results[chunk_id] = replace(
                    frame,
                    maximum_domain_radius_m=maximum_radius,
                    sampled_domain_distance_distortion=maximum_distortion,
                    domain_sample_count=len(candidates),
                    domain_diagnostic_error=(
                        "Some images in this connected group have unavailable source dimensions; "
                        "only known image domains were sampled."
                        if missing_dimensions else None
                    ),
                )
            else:
                results[chunk_id] = replace(
                    frame, maximum_domain_radius_m=None,
                    sampled_domain_distance_distortion=None, domain_sample_count=0,
                    domain_diagnostic_error=(
                        "Source dimensions are unavailable for the connected group."
                        if missing_dimensions else None
                    ),
                )
        except (ValueError, RuntimeError) as error:
            results[chunk_id] = replace(
                frame, maximum_domain_radius_m=None,
                sampled_domain_distance_distortion=None, domain_sample_count=len(candidates),
                domain_diagnostic_error=str(error),
            )
    return results


def build_local_metric_frame(
    source_crs: str, coordinates: list[tuple[float, float]],
) -> tuple[LocalMetricFrameInfo, Any]:
    """Build a datum-preserving local AEQD frame and project geographic controls into it."""
    from pyproj import CRS, Transformer

    geographic = CRS.from_user_input(source_crs)
    if not geographic.is_geographic:
        raise ValueError("A local metric frame can only be built from a geographic CRS.")
    if not coordinates or any(not all(isfinite(value) for value in point) for point in coordinates):
        raise ValueError("Local metric fitting needs finite geographic control coordinates.")
    to_geographic = Transformer.from_crs(geographic, CRS.from_epsg(4326), always_xy=True)
    longitudes, latitudes = to_geographic.transform(
        [point[0] for point in coordinates], [point[1] for point in coordinates], errcheck=True,
    )
    if not all(isfinite(value) for value in (*longitudes, *latitudes)):
        raise ValueError("Geographic controls cannot be normalized to longitude and latitude.")
    longitude_x = sum(cos(radians(longitude)) for longitude in longitudes)
    longitude_y = sum(sin(radians(longitude)) for longitude in longitudes)
    if hypot(longitude_x, longitude_y) <= 1e-8 * len(coordinates):
        raise ValueError("Control longitudes do not define a stable local metric frame center.")
    center_longitude = degrees(atan2(longitude_y, longitude_x))
    center_latitude = sum(latitudes) / len(latitudes)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        geodetic_proj4 = geographic.geodetic_crs.to_proj4()
    datum_parameters = [
        token for token in geodetic_proj4.split()
        if not token.startswith(("+proj=", "+units=")) and token != "+type=crs"
    ]
    local_crs = CRS.from_proj4(" ".join((
        "+proj=aeqd", f"+lat_0={center_latitude:.12g}", f"+lon_0={center_longitude:.12g}",
        *datum_parameters, "+units=m", "+type=crs",
    )))
    transformer = Transformer.from_crs(geographic, local_crs, always_xy=True)
    local_x, local_y = transformer.transform(
        [point[0] for point in coordinates], [point[1] for point in coordinates], errcheck=True,
    )
    if not all(isfinite(value) for value in (*local_x, *local_y)):
        raise ValueError("Geographic controls cannot be represented in the local metric frame.")
    maximum_radius = max(hypot(x, y) for x, y in zip(local_x, local_y))
    geod = geographic.get_geod()
    geographic_coordinates = list(zip(longitudes, latitudes))
    sample_count = min(len(geographic_coordinates), 64)
    sample_indices = sorted({
        round(index * (len(geographic_coordinates) - 1) / max(1, sample_count - 1))
        for index in range(sample_count)
    })
    maximum_distortion = 0.0
    for offset, first_index in enumerate(sample_indices):
        for second_index in sample_indices[offset + 1:]:
            first, second = geographic_coordinates[first_index], geographic_coordinates[second_index]
            _azimuth, _back_azimuth, geodesic_distance = geod.inv(*first, *second)
            if geodesic_distance <= 0.01:
                continue
            planar_distance = hypot(
                local_x[first_index] - local_x[second_index],
                local_y[first_index] - local_y[second_index],
            )
            maximum_distortion = max(
                maximum_distortion, abs(planar_distance / geodesic_distance - 1.0),
            )
    return LocalMetricFrameInfo(
        local_crs.to_wkt(), center_longitude, center_latitude,
        maximum_radius, maximum_distortion,
    ), transformer


def _world_compatibility_transform(mapping: MatrixTransform | WarpTransform | MappingChain) -> WorldTransform:
    if isinstance(mapping, WarpTransform):
        return _world_compatibility_transform(mapping.base_mapping)
    if isinstance(mapping, MappingChain):
        if any(isinstance(step, WarpTransform) for step in mapping.steps):
            base_chain = MappingChain(tuple(
                step.base_mapping if isinstance(step, WarpTransform) else step
                for step in mapping.steps
            ))
            return _world_compatibility_transform(base_chain)
        if all(isinstance(step, MatrixTransform) for step in mapping.steps):
            return _world_compatibility_transform(mapping.to_matrix())
        origin_x, origin_y = mapping.map_point(0, 0)
        axis_x, axis_y = mapping.map_point(1, 0)
        direction = complex(axis_x - origin_x, axis_y - origin_y)
    elif mapping.is_affine:
        a, _b, origin_x, d, _e, origin_y = mapping.to_affine()
        direction = complex(a, d)
    else:
        origin_x, origin_y = mapping.map_point(0, 0)
        a, _b, d, _e = mapping.jacobian(0, 0)
        direction = complex(a, d)
    return origin_x, origin_y, degrees(phase(direction)), hypot(direction.real, direction.imag)


@dataclass(frozen=True)
class GeoreferenceResult:
    transforms: dict[str, WorldTransform]
    observation_residuals: dict[ObservationKey, float]
    iterations: int
    converged: bool
    crs: str
    optimizer_status: int | None = None
    optimizer_message: str = ""
    max_iterations: int | None = None
    settings: SolverSettings | None = None
    matrix_transforms: dict[str, MatrixTransform] | None = None
    source_dimensions: dict[str, tuple[int, int]] | None = None
    native_mapping_records: dict[str, ImageMapping] | None = None
    metric_frames: dict[str, LocalMetricFrameInfo] | None = None
    checkpoint_residuals: dict[ObservationKey, float] | None = None
    unmapped_checkpoints: tuple[ObservationKey, ...] = ()

    def __post_init__(self) -> None:
        native_records = None
        if self.native_mapping_records is not None:
            native_records = dict(self.native_mapping_records)
            if any(not isinstance(record, ImageMapping) for record in native_records.values()):
                raise ValueError("Solver native mappings require validated image mapping records.")
            if set(native_records) != set(self.transforms):
                raise ValueError("Solver native mappings and compatibility transforms disagree.")
            if any(record.target_frame != "native_crs" or record.native_crs != self.crs
                   for record in native_records.values()):
                raise ValueError("Solver native mappings must target the result CRS.")
            if self.matrix_transforms is None and all(
                isinstance(record.mapping, MatrixTransform) for record in native_records.values()
            ):
                object.__setattr__(self, "matrix_transforms", {
                    image_id: record.mapping for image_id, record in native_records.items()
                })
            object.__setattr__(self, "native_mapping_records", native_records)
            for image_id, record in native_records.items():
                transform = self.transforms.get(image_id)
                if transform is None or not all(isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-12)
                                                for actual, wanted in zip(transform, _world_compatibility_transform(record.mapping_chain))):
                    raise ValueError("Solver compatibility transform and native mapping disagree.")
        if self.metric_frames is not None:
            frames = dict(self.metric_frames)
            if any(not isinstance(frame, LocalMetricFrameInfo) for frame in frames.values()):
                raise ValueError("Solver metric frames require validated local frame metadata.")
            object.__setattr__(self, "metric_frames", frames)
        if self.checkpoint_residuals is not None:
            checkpoint_residuals = dict(self.checkpoint_residuals)
            if any(not isfinite(value) or value < 0 for value in checkpoint_residuals.values()):
                raise ValueError("Checkpoint residuals must be finite non-negative distances.")
            object.__setattr__(self, "checkpoint_residuals", checkpoint_residuals)
        object.__setattr__(self, "unmapped_checkpoints", tuple(self.unmapped_checkpoints))
        if self.matrix_transforms is not None:
            matrices = dict(self.matrix_transforms)
            if any(not isinstance(value, MatrixTransform) for value in matrices.values()):
                raise ValueError("Solver mappings require validated matrix transforms.")
            for image_id, transform in self.transforms.items():
                matrix = matrices.get(image_id)
                record = (native_records or {}).get(image_id)
                if matrix is None:
                    if record is not None and isinstance(record.mapping, WarpTransform):
                        continue
                    raise ValueError("Solver matrix and compatibility transform disagree.")
                if record is not None:
                    if isinstance(record.mapping, WarpTransform):
                        raise ValueError("A nonlinear native mapping cannot also declare matrix coefficients.")
                    if matrix != record.mapping:
                        raise ValueError("Solver matrix and native mapping record disagree.")
                    compatible = all(isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-12)
                                     for actual, wanted in zip(transform, _world_compatibility_transform(record.mapping_chain)))
                elif matrix.model == "similarity":
                    compatible = matrix.coefficients == similarity_mapping(transform, True).coefficients
                elif matrix.model in ("affine", "projective"):
                    compatible = all(isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-12)
                                     for actual, wanted in zip(transform, _world_compatibility_transform(matrix)))
                else:
                    compatible = False
                if not compatible:
                    raise ValueError("Solver matrix and compatibility transform disagree.")
            object.__setattr__(self, "matrix_transforms", matrices)
        if self.source_dimensions is not None:
            dimensions = dict(self.source_dimensions)
            mappings = self.mappings
            for image_id, size in dimensions.items():
                matrix = mappings.get(image_id)
                if matrix is None:
                    raise ValueError("Source dimensions refer to an image absent from the solver result.")
                record = (native_records or {}).get(image_id)
                if record is None:
                    checked = ImageMapping(matrix, "native_crs", self.crs, source_size=size)
                else:
                    if record.source_size not in (None, size):
                        raise ValueError("Solver native mapping and source dimensions disagree.")
                    checked = ImageMapping(
                        record.mapping, record.target_frame, record.native_crs, record.source_frame,
                        size, record.matrix_crs,
                    )
                    native_records[image_id] = checked
                dimensions[image_id] = checked.source_size
            object.__setattr__(self, "source_dimensions", dimensions)
            if native_records is not None:
                object.__setattr__(self, "native_mapping_records", native_records)

    @property
    def maximum_residual(self) -> float:
        return max(self.observation_residuals.values(), default=0.0)

    @property
    def maximum_checkpoint_error(self) -> float:
        return max((self.checkpoint_residuals or {}).values(), default=0.0)

    @property
    def residual_units(self) -> str:
        if self.metric_frames or self.settings is not None and self.settings.transform_model == "tps":
            return "metres in local metric frame"
        return georeference_distance_unit(self.crs, self.settings).label if self.settings else "CRS units"

    @property
    def mappings(self) -> dict[str, MatrixTransform | WarpTransform]:
        if self.matrix_transforms is not None:
            return dict(self.matrix_transforms)
        if self.native_mapping_records is not None:
            return {image_id: record.mapping for image_id, record in self.native_mapping_records.items()}
        return {image_id: similarity_mapping(transform, True) for image_id, transform in self.transforms.items()}

    @property
    def mapping_records(self) -> dict[str, ImageMapping]:
        if self.native_mapping_records is not None:
            return dict(self.native_mapping_records)
        return {
            image_id: ImageMapping(matrix, "native_crs", self.crs, source_size=(self.source_dimensions or {}).get(image_id))
            for image_id, matrix in self.mappings.items()
        }


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
    settings: SolverSettings | None = None,
) -> GeoreferenceResult:
    """Fit each connected group's pixel-to-world transform to its GCP observations."""
    config = settings or SolverSettings(tolerance, max_iterations)
    tolerance, max_iterations = config.tolerance, config.max_iterations
    from pyproj import CRS

    try:
        project_crs = CRS.from_user_input(project.crs)
    except Exception as error:
        raise ValueError("Georeferencing requires a valid project CRS.") from error
    if config.transform_model == "tps" and not project_crs.is_geographic:
        raise ValueError("TPS georeferencing currently requires a geographic project CRS.")
    if config.transform_model in ("affine", "projective"):
        if not project_crs.is_projected and not project_crs.is_geographic:
            raise ValueError("Affine and Projective georeferencing require a projected or geographic CRS.")
    if tolerance <= 0:
        raise ValueError("Tolerance must be greater than zero.")
    if max_iterations < 1:
        raise ValueError("Maximum iterations must be at least one.")

    from scipy.optimize import least_squares

    checkpoint_ids = {
        point.id for point in project.control_points if point.geographic_checkpoint
    }
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
        if not point.geographic_checkpoint
        and point.geographic_x is not None and point.geographic_y is not None
        for observation in point.observations
    ]
    if not matches:
        raise ValueError("No control points have geographic X and Y coordinates and image observations.")

    world_origin_x = sum(match.world_x for match in matches) / len(matches)
    world_origin_y = sum(match.world_y for match in matches) / len(matches)

    # Images linked by constraints form a rigid chunk solved as one element in canvas space.
    chunk_of, frames = _chunk_frames(project, excluded_point_ids=checkpoint_ids)
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
    metric_frames: dict[str, LocalMetricFrameInfo] = {}
    metric_transformers = {}
    matrix_crs_by_chunk: dict[str, str] = {}
    if config.transform_model in ("affine", "projective", "tps") and project_crs.is_geographic:
        chunks = list(dict.fromkeys(match.image_id for match in matches))
        matches_by_chunk: dict[str, list[_ControlMatch]] = {}
        for match in matches:
            matches_by_chunk.setdefault(match.image_id, []).append(match)
        for chunk_id in chunks:
            chunk_matches = matches_by_chunk[chunk_id]
            controls = list(dict.fromkeys((match.world_x, match.world_y) for match in chunk_matches))
            frame_info, transformer = build_local_metric_frame(project.crs, controls)
            metric_frames[chunk_id] = frame_info
            metric_transformers[chunk_id] = transformer
            matrix_crs_by_chunk[chunk_id] = frame_info.crs

        def to_metric_frame(match: _ControlMatch, chunk_id: str) -> _ControlMatch:
            world_x, world_y = metric_transformers[chunk_id].transform(
                match.world_x, match.world_y, errcheck=True,
            )
            if not isfinite(world_x) or not isfinite(world_y):
                raise ValueError("A geographic control cannot be transformed into its local metric frame.")
            return replace(match, world_x=world_x, world_y=world_y)

        original_matches = [to_metric_frame(match, chunk_of[match.image_id]) for match in original_matches]
        matches = [to_metric_frame(match, match.image_id) for match in matches]
    if config.transform_model == "affine":
        result = _simulate_affine_georeference(
            project, original_matches, matches, chunk_of, frames, config, progress,
            matrix_crs_by_chunk, metric_frames,
        )
        return _include_checkpoint_diagnostics(result, project)
    if config.transform_model == "projective":
        result = _simulate_projective_georeference(
            project, original_matches, matches, chunk_of, frames, config, progress,
            matrix_crs_by_chunk, metric_frames,
        )
        return _include_checkpoint_diagnostics(result, project)
    if config.transform_model == "tps":
        result = _simulate_tps_georeference(
            project, original_matches, matches, chunk_of, frames, config, progress,
            matrix_crs_by_chunk, metric_frames,
        )
        return _include_checkpoint_diagnostics(result, project)
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
    scale_min = config.scale_min if config.scale_min is not None else 1e-12
    scale_max = config.scale_max if config.scale_max is not None else float("inf")
    if config.transform_model == "similarity" and scale_min >= scale_max:
        raise ValueError("Minimum geographic scale must be less than maximum scale.")
    for chunk_id in chunk_ids:
        image_matches = matches_by_image[chunk_id]
        current = chunk_initial.get(chunk_id) or _closed_form_fit(image_matches) or (
            world_origin_x,
            world_origin_y,
            0.0,
            1.0,
        )
        if chunk_id not in chunk_initial:
            x, y, rotation, scale = current
            if config.transform_model == "translation":
                rotation, scale = 0.0, 1.0
            elif config.transform_model == "rigid":
                scale = 1.0
            else:
                scale = min(scale_max, max(scale_min, scale))
            offsets = [
                _apply_world_transform(match.pixel_x, match.pixel_y, (0.0, 0.0, rotation, scale))
                for match in image_matches
            ]
            current = (
                sum(match.world_x - offset[0] for match, offset in zip(image_matches, offsets)) / len(image_matches),
                sum(match.world_y - offset[1] for match, offset in zip(image_matches, offsets)) / len(image_matches),
                rotation, scale,
            )
        initial[chunk_id] = current
        unique_pixels = {(round(match.pixel_x, 9), round(match.pixel_y, 9)) for match in image_matches}
        if len(unique_pixels) < 2 or config.transform_model == "translation":
            underconstrained_ids.add(chunk_id)
        variable_ids.append(chunk_id)
        x, y, rotation, scale = current
        if chunk_id in underconstrained_ids:
            values.extend((x - world_origin_x, y - world_origin_y))
            lower_bounds.extend((-float("inf"), -float("inf")))
            upper_bounds.extend((float("inf"), float("inf")))
        elif config.transform_model == "rigid":
            values.extend((x - world_origin_x, y - world_origin_y, radians(rotation)))
            lower_bounds.extend((-float("inf"), -float("inf"), -float("inf")))
            upper_bounds.extend((float("inf"), float("inf"), float("inf")))
        else:
            values.extend((x - world_origin_x, y - world_origin_y, radians(rotation), min(scale_max, max(scale_min, scale))))
            lower_bounds.extend((-float("inf"), -float("inf"), -float("inf"), scale_min))
            upper_bounds.extend((float("inf"), float("inf"), float("inf"), scale_max))

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
            elif config.transform_model == "rigid":
                result[image_id] = (
                    parameters[offset] + world_origin_x, parameters[offset + 1] + world_origin_y,
                    degrees(parameters[offset + 2]), initial[image_id][3],
                )
                offset += 3
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
        method=config.optimizer_method,
        loss=config.loss,
        f_scale=config.loss_scale,
        x_scale="jac" if config.parameter_scaling == "jacobian" else 1.0,
        ftol=config.ftol,
        xtol=config.xtol,
        gtol=config.gtol,
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

    result = GeoreferenceResult(
        transforms=transforms,
        observation_residuals=observation_residuals,
        iterations=optimization.nfev,
        converged=optimization.success and max(observation_residuals.values(), default=0.0) <= tolerance,
        crs=project.crs,
        optimizer_status=int(optimization.status),
        optimizer_message=str(optimization.message),
        max_iterations=max_iterations,
        settings=config,
        source_dimensions={
            image.id: image.source_dimensions for image in project.images
            if image.id in transforms and image.source_dimensions is not None
        } or None,
    )
    return _include_checkpoint_diagnostics(result, project)


def _include_checkpoint_diagnostics(result: GeoreferenceResult, project: Project) -> GeoreferenceResult:
    from pyproj import CRS

    records = result.mapping_records
    crs = CRS.from_user_input(result.crs)
    geod = crs.get_geod() if crs.is_geographic else None
    residuals: dict[ObservationKey, float] = {}
    unmapped = []
    for point in project.control_points:
        if not point.geographic_checkpoint:
            continue
        if point.geographic_x is None or point.geographic_y is None:
            continue
        for observation in point.observations:
            key = (observation.image_id, point.id)
            record = records.get(observation.image_id)
            if record is None:
                unmapped.append(key)
                continue
            try:
                predicted = record.mapping_chain.map_point(observation.x, observation.y)
            except (ValueError, OverflowError):
                unmapped.append(key)
                continue
            if geod is not None:
                error = geod.inv(
                    point.geographic_x, point.geographic_y, predicted[0], predicted[1],
                )[2]
            else:
                error = hypot(predicted[0] - point.geographic_x, predicted[1] - point.geographic_y)
            residuals[key] = error
    return replace(
        result, checkpoint_residuals=residuals, unmapped_checkpoints=tuple(unmapped),
    )


def _simulate_tps_georeference(
    project: Project,
    original_matches: list[_ControlMatch],
    frame_matches: list[_ControlMatch],
    chunk_of: dict[str, str],
    frames: dict[str, WorldTransform],
    config: SolverSettings,
    progress: Callable[[float], None] | None,
    matrix_crs_by_chunk: dict[str, str],
    metric_frames: dict[str, LocalMetricFrameInfo],
) -> GeoreferenceResult:
    parent = {point.id: point.id for point in project.control_points}

    def find(point_id: str) -> str:
        if parent[point_id] != point_id:
            parent[point_id] = find(parent[point_id])
        return parent[point_id]

    for constraint in project.constraints:
        first_id, second_id = constraint.first_point_id, constraint.second_point_id
        if first_id in parent and second_id in parent:
            first_root, second_root = find(first_id), find(second_id)
            if first_root != second_root:
                parent[max(first_root, second_root)] = min(first_root, second_root)

    component_observations: dict[tuple[str, str], list[_ControlMatch]] = {}
    for match in frame_matches:
        component_observations.setdefault((match.image_id, find(match.point_id)), []).append(match)
    controls_by_chunk: dict[str, list[_ControlMatch]] = {}
    for (chunk_id, component_id), observations in component_observations.items():
        count = len(observations)
        controls_by_chunk.setdefault(chunk_id, []).append(_ControlMatch(
            chunk_id, component_id,
            sum(match.pixel_x for match in observations) / count,
            sum(match.pixel_y for match in observations) / count,
            sum(match.world_x for match in observations) / count,
            sum(match.world_y for match in observations) / count,
        ))
    if not controls_by_chunk:
        raise ValueError("TPS georeferencing needs observed geographic controls.")

    images = {image.id: image for image in project.images}
    image_ids_by_chunk: dict[str, list[str]] = {}
    for image_id, chunk_id in chunk_of.items():
        if chunk_id in controls_by_chunk:
            image_ids_by_chunk.setdefault(chunk_id, []).append(image_id)
    observations_by_image: dict[str, list[_ControlMatch]] = {}
    for match in original_matches:
        observations_by_image.setdefault(match.image_id, []).append(match)

    warps: dict[str, WarpTransform] = {}
    dimensions: dict[str, tuple[int, int]] = {}
    group_ids = list(controls_by_chunk)
    completed = 0
    for group_index, group_id in enumerate(group_ids):
        controls = controls_by_chunk[group_id]
        group_source = tuple((match.pixel_x, match.pixel_y) for match in controls)
        if len(set(group_source)) < 3:
            raise ValueError("TPS georeferencing needs at least three distinct linked control groups.")
        seed = _closed_form_fit(controls)
        if seed is None:
            target_center = (
                sum(match.world_x for match in controls) / len(controls),
                sum(match.world_y for match in controls) / len(controls),
            )
            seed = (target_center[0], target_center[1], 0.0, 1.0)
        group_base = MatrixTransform.from_similarity(seed)
        group_targets = tuple((match.world_x, match.world_y) for match in controls)

        for image_id in image_ids_by_chunk[group_id]:
            image = images[image_id]
            if image.source_dimensions is None:
                raise ValueError(f"TPS georeferencing requires known source dimensions for '{image.name}'.")
            image_to_group = similarity_mapping(frames[image_id], True)
            group_to_image = image_to_group.inverse()
            image_controls = tuple(group_to_image.map_point(*point) for point in group_source)
            base_mapping = group_base.compose(image_to_group)
            width, height = image.source_dimensions
            support_points = [
                *image_controls,
                (0.0, 0.0), (float(width), 0.0),
                (float(width), float(height)), (0.0, float(height)),
            ]
            support_points.extend(
                (match.pixel_x, -match.pixel_y)
                for match in observations_by_image.get(image_id, ())
            )
            support_points = tuple(dict.fromkeys(support_points))
            warp = WarpTransform.fit(
                base_mapping, image_controls, group_targets,
                smoothing=config.warp_smoothing, support_points=support_points,
            )
            domain_grid = tuple(
                (width * column / 8, height * row / 8)
                for row in range(9) for column in range(9)
            )
            supported_grid = tuple(
                point for point, supported in zip(domain_grid, warp.supports(domain_grid)) if supported
            )
            validation_samples = tuple(dict.fromkeys((*support_points, *supported_grid)))
            warp.validate_domain(
                validation_samples,
                minimum_relative_area=config.warp_min_relative_area,
                maximum_relative_stretch=config.warp_max_relative_stretch,
            )
            warps[image_id] = warp
            dimensions[image_id] = image.source_dimensions
            completed += 1
            if progress is not None:
                progress(min(0.99, completed / max(1, len(chunk_of))))

    records = {
        image_id: ImageMapping(
            warp, "native_crs", project.crs,
            source_size=dimensions[image_id], matrix_crs=matrix_crs_by_chunk.get(chunk_of[image_id]),
        )
        for image_id, warp in warps.items()
    }
    residuals: dict[ObservationKey, float] = {}
    for match in original_matches:
        predicted = warps[match.image_id].map_point(match.pixel_x, -match.pixel_y)
        residual = hypot(
            predicted[0] - match.world_x, predicted[1] - match.world_y,
        )
        key = (match.image_id, match.point_id)
        residuals[key] = max(residuals.get(key, 0.0), residual)
    if metric_frames:
        metric_frames = _measure_metric_frame_domains(
            metric_frames, warps, chunk_of, dimensions, project.crs,
        )
    transforms = {
        image_id: _world_compatibility_transform(record.mapping_chain)
        for image_id, record in records.items()
    }
    return GeoreferenceResult(
        transforms=transforms,
        observation_residuals=residuals,
        iterations=completed,
        converged=bool(residuals and max(residuals.values()) <= config.tolerance),
        crs=project.crs,
        optimizer_status=1,
        optimizer_message="TPS coefficient systems solved directly for each connected image group.",
        max_iterations=config.max_iterations,
        settings=config,
        source_dimensions=dimensions,
        native_mapping_records=records,
        metric_frames=metric_frames or None,
    )


def _simulate_affine_georeference(
    project: Project,
    original_matches: list[_ControlMatch],
    frame_matches: list[_ControlMatch],
    chunk_of: dict[str, str],
    frames: dict[str, WorldTransform],
    config: SolverSettings,
    progress: Callable[[float], None] | None,
    matrix_crs_by_chunk: dict[str, str],
    metric_frames: dict[str, LocalMetricFrameInfo],
) -> GeoreferenceResult:
    import numpy as np
    from scipy.optimize import least_squares

    matches_by_chunk: dict[str, list[_ControlMatch]] = {}
    for match in frame_matches:
        matches_by_chunk.setdefault(match.image_id, []).append(match)
    chunks = list(dict.fromkeys(matches_by_chunk))
    if not chunks:
        raise ValueError("No control points have geographic X and Y coordinates and image observations.")

    group_mappings = {}
    residuals: dict[ObservationKey, float] = {}
    total_evaluations = 0
    optimizer_statuses = []
    optimizer_success = True
    optimizer_messages = []
    for chunk_index, chunk_id in enumerate(chunks):
        chunk_matches = matches_by_chunk[chunk_id]
        source = np.asarray([(match.pixel_x, match.pixel_y) for match in chunk_matches], dtype=float)
        target = np.asarray([(match.world_x, match.world_y) for match in chunk_matches], dtype=float)
        source_center = source.mean(axis=0)
        target_center = target.mean(axis=0)
        source_spread = max(float(np.sqrt(np.mean(np.sum((source - source_center) ** 2, axis=1) / 2))), 1e-12)
        target_spread = max(float(np.sqrt(np.mean(np.sum((target - target_center) ** 2, axis=1) / 2))), 1e-12)
        normalized_source = (source - source_center) / source_spread
        normalized_target = (target - target_center) / target_spread
        design = np.column_stack((normalized_source, np.ones(len(normalized_source))))
        target_design = np.column_stack((normalized_target, np.ones(len(normalized_target))))
        if np.linalg.matrix_rank(design) < 3 or np.linalg.matrix_rank(target_design) < 3:
            raise ValueError("Affine georeferencing needs three non-collinear controls in each linked image group.")
        seed = np.linalg.lstsq(design, normalized_target, rcond=None)[0]
        initial = seed.T.reshape(-1)
        evaluations = [0]

        def residual_vector(parameters):
            if progress is not None:
                evaluations[0] += 1
                progress(min(0.99, (chunk_index + evaluations[0] / (config.max_iterations * 7)) / len(chunks)))
            predicted = np.column_stack((
                parameters[0] * normalized_source[:, 0] + parameters[1] * normalized_source[:, 1] + parameters[2],
                parameters[3] * normalized_source[:, 0] + parameters[4] * normalized_source[:, 1] + parameters[5],
            ))
            return (predicted - normalized_target).ravel()

        optimization = least_squares(
            residual_vector, initial, max_nfev=config.max_iterations,
            method=config.optimizer_method, loss=config.loss,
            f_scale=config.loss_scale / target_spread,
            x_scale="jac" if config.parameter_scaling == "jacobian" else 1.0,
            ftol=config.ftol, xtol=config.xtol, gtol=config.gtol,
        )
        total_evaluations += int(optimization.nfev)
        optimizer_statuses.append(int(optimization.status))
        optimizer_success &= bool(optimization.success)
        optimizer_messages.append(str(optimization.message))
        first, second, third, fourth, fifth, sixth = optimization.x
        a, b = target_spread * first / source_spread, target_spread * second / source_spread
        d, e = target_spread * fourth / source_spread, target_spread * fifth / source_spread
        c = target_center[0] + target_spread * third - a * source_center[0] - b * source_center[1]
        f = target_center[1] + target_spread * sixth - d * source_center[0] - e * source_center[1]
        group_mapping = MatrixTransform.from_affine((a, b, c, d, e, f))
        singular_values = np.linalg.svd(np.asarray(((a, b), (d, e))), compute_uv=False)
        minimum_stretch, maximum_stretch = float(singular_values[-1]), float(singular_values[0])
        if maximum_stretch > config.affine_max_stretch or minimum_stretch < config.affine_min_stretch:
            raise ValueError(f"Affine georeference fit for group '{chunk_id}' exceeds the configured stretch limits.")
        if maximum_stretch / minimum_stretch > config.affine_max_condition:
            raise ValueError(f"Affine georeference fit for group '{chunk_id}' exceeds the configured condition limit.")
        if group_mapping.determinant < 0 and not config.affine_allow_reflection:
            raise ValueError(f"Affine georeference fit for group '{chunk_id}' reflects the group.")
        group_mappings[chunk_id] = group_mapping

    image_records = {image.id: image for image in project.images}
    mappings = {
        image_id: group_mappings[chunk_id].compose(similarity_mapping(frames[image_id], True))
        for image_id, chunk_id in chunk_of.items() if chunk_id in group_mappings
    }
    source_dimensions = {
        image_id: image_records[image_id].source_dimensions for image_id in mappings
        if image_records[image_id].source_dimensions is not None
    } or None
    native_records = {
        image_id: ImageMapping(
            mapping, "native_crs", project.crs,
            source_size=(source_dimensions or {}).get(image_id),
            matrix_crs=matrix_crs_by_chunk.get(chunk_of[image_id]),
        )
        for image_id, mapping in mappings.items()
    }
    transforms = {
        image_id: _world_compatibility_transform(record.mapping_chain)
        for image_id, record in native_records.items()
    }
    if metric_frames:
        metric_frames = _measure_metric_frame_domains(
            metric_frames, mappings, chunk_of, source_dimensions or {}, project.crs,
        )
    for match in original_matches:
        chunk_id = chunk_of[match.image_id]
        group_pixel = _apply_world_transform(match.pixel_x, match.pixel_y, frames[match.image_id])
        predicted = group_mappings[chunk_id].map_point(*group_pixel)
        residuals[(match.image_id, match.point_id)] = hypot(
            predicted[0] - match.world_x, predicted[1] - match.world_y,
        )
    optimizer_status = -1 if any(status < 0 for status in optimizer_statuses) else (
        0 if any(status == 0 for status in optimizer_statuses) else optimizer_statuses[-1]
    )
    return GeoreferenceResult(
        transforms=transforms, observation_residuals=residuals,
        iterations=total_evaluations,
        converged=bool(optimizer_success and residuals and max(residuals.values()) <= config.tolerance),
        crs=project.crs, optimizer_status=optimizer_status,
        optimizer_message="; ".join(optimizer_messages), max_iterations=config.max_iterations,
        settings=config, matrix_transforms=mappings, source_dimensions=source_dimensions,
        native_mapping_records=native_records, metric_frames=metric_frames or None,
    )


def _simulate_projective_georeference(
    project: Project,
    original_matches: list[_ControlMatch],
    frame_matches: list[_ControlMatch],
    chunk_of: dict[str, str],
    frames: dict[str, WorldTransform],
    config: SolverSettings,
    progress: Callable[[float], None] | None,
    matrix_crs_by_chunk: dict[str, str],
    metric_frames: dict[str, LocalMetricFrameInfo],
) -> GeoreferenceResult:
    import numpy as np
    from scipy.optimize import least_squares

    from geostitch.adjustment import _normalization_matrix, _normalized_dlt, _projective_control_rank

    matches_by_chunk: dict[str, list[_ControlMatch]] = {}
    for match in frame_matches:
        matches_by_chunk.setdefault(match.image_id, []).append(match)
    chunks = list(dict.fromkeys(matches_by_chunk))
    if not chunks:
        raise ValueError("No control points have geographic X and Y coordinates and image observations.")
    image_records = {image.id: image for image in project.images}
    chunk_members = {
        chunk_id: [image_id for image_id, owner in chunk_of.items() if owner == chunk_id]
        for chunk_id in chunks
    }
    for chunk_id, members in chunk_members.items():
        for image_id in members:
            if image_records[image_id].source_dimensions is None:
                raise ValueError(f"Projective georeferencing requires known source dimensions for '{image_records[image_id].name}'.")

    group_mappings = {}
    residuals: dict[ObservationKey, float] = {}
    total_evaluations = 0
    optimizer_statuses = []
    optimizer_success = True
    optimizer_messages = []
    for chunk_index, chunk_id in enumerate(chunks):
        chunk_matches = matches_by_chunk[chunk_id]
        source_points = [(match.pixel_x, match.pixel_y) for match in chunk_matches]
        target_points = [(match.world_x, match.world_y) for match in chunk_matches]
        if _projective_control_rank(source_points, target_points) < 8:
            raise ValueError("Projective georeferencing needs four controls in general position in each linked image group.")
        source_normalization = _normalization_matrix(source_points)
        target_normalization = _normalization_matrix(target_points)
        if source_normalization is None or target_normalization is None:
            raise ValueError("Projective georeferencing controls cannot be normalized.")
        normalized_source, source_matrix = source_normalization
        normalized_target, target_matrix = target_normalization
        seed, rank = _normalized_dlt(source_points, target_points)
        if seed is None or rank < 8:
            raise ValueError("Projective georeferencing could not construct a full-rank homography seed.")
        normalized_h = target_matrix @ np.asarray(seed.coefficients).reshape((3, 3)) @ np.linalg.inv(source_matrix)
        divisor = normalized_h[2, 2]
        if abs(divisor) <= 1e-12 * max(abs(value) for value in normalized_h.flat):
            raise ValueError("Projective georeferencing seed has invalid homogeneous scale.")
        normalized_h /= divisor
        initial = [float(normalized_h[row, column]) for row in range(3) for column in range(3) if (row, column) != (2, 2)]
        target_center = np.asarray(target_points, dtype=float).mean(axis=0)
        target_spread = max(float(np.sqrt(np.mean(np.sum((np.asarray(target_points) - target_center) ** 2, axis=1) / 2))), 1.0)
        evaluations = [0]

        def residual_vector(parameters):
            if progress is not None:
                evaluations[0] += 1
                progress(min(0.99, (chunk_index + evaluations[0] / (config.max_iterations * 9)) / len(chunks)))
            h = (*parameters[:6], parameters[6], parameters[7], 1.0)
            predicted = []
            for x, y in normalized_source:
                denominator = h[6] * x + h[7] * y + 1.0
                denominator_scale = abs(h[6] * x) + abs(h[7] * y) + 1.0
                if abs(denominator) < config.projective_horizon_margin * denominator_scale:
                    return [1e6] * (2 * len(chunk_matches))
                predicted.extend(((h[0] * x + h[1] * y + h[2]) / denominator,
                                  (h[3] * x + h[4] * y + h[5]) / denominator))
            return (np.asarray(predicted).reshape((-1, 2)) - normalized_target).ravel()

        optimization = least_squares(
            residual_vector, initial, max_nfev=config.max_iterations,
            method=config.optimizer_method, loss=config.loss,
            f_scale=config.loss_scale / target_spread,
            x_scale="jac" if config.parameter_scaling == "jacobian" else 1.0,
            ftol=config.ftol, xtol=config.xtol, gtol=config.gtol,
        )
        total_evaluations += int(optimization.nfev)
        optimizer_statuses.append(int(optimization.status))
        optimizer_success &= bool(optimization.success)
        optimizer_messages.append(str(optimization.message))
        fitted_h = np.asarray((*optimization.x[:6], optimization.x[6], optimization.x[7], 1.0)).reshape((3, 3))
        world_h = np.linalg.inv(target_matrix) @ fitted_h @ source_matrix
        divisor = world_h[2, 2]
        if abs(divisor) <= 1e-12 * max(abs(value) for value in world_h.flat):
            raise ValueError(f"Projective georeference fit for group '{chunk_id}' has invalid homogeneous scale.")
        world_h /= divisor
        group_mapping = MatrixTransform(tuple(float(value) for value in world_h.flat), "projective")
        group_mappings[chunk_id] = group_mapping

    mappings = {
        image_id: group_mappings[chunk_id].compose(similarity_mapping(frames[image_id], True))
        for image_id, chunk_id in chunk_of.items() if chunk_id in group_mappings
    }
    for image_id, mapping in mappings.items():
        width, height = image_records[image_id].source_dimensions
        mapping.domain_bounds(width, height)
        for row in range(7):
            for column in range(7):
                x, y = width * column / 6, height * row / 6
                first, second, third, fourth, fifth, sixth, seventh, eighth, ninth = mapping.coefficients
                denominator = seventh * x + eighth * y + ninth
                denominator_scale = abs(seventh * x) + abs(eighth * y) + abs(ninth)
                if abs(denominator) < config.projective_horizon_margin * denominator_scale:
                    raise ValueError(f"Projective georeference fit for '{image_records[image_id].name}' approaches a horizon inside the image.")
                a, b, c, d = mapping.jacobian(x, y)
                trace = a * a + b * b + c * c + d * d
                determinant = a * d - b * c
                discriminant = max(0.0, trace * trace - 4 * determinant * determinant) ** 0.5
                maximum_stretch = max(0.0, (trace + discriminant) / 2) ** 0.5
                minimum_stretch = max(0.0, (trace - discriminant) / 2) ** 0.5
                if determinant > 0 and not config.projective_allow_reflection:
                    raise ValueError(f"Projective georeference fit for '{image_records[image_id].name}' reflects the image.")
                if minimum_stretch < config.projective_min_stretch or maximum_stretch > config.projective_max_stretch:
                    raise ValueError(f"Projective georeference fit for '{image_records[image_id].name}' exceeds the configured stretch limits.")
                if minimum_stretch <= 0 or maximum_stretch / minimum_stretch > config.projective_max_condition:
                    raise ValueError(f"Projective georeference fit for '{image_records[image_id].name}' exceeds the configured condition limit.")

    source_dimensions = {
        image_id: image_records[image_id].source_dimensions for image_id in mappings
    } or None
    native_records = {
        image_id: ImageMapping(
            mapping, "native_crs", project.crs,
            source_size=(source_dimensions or {}).get(image_id),
            matrix_crs=matrix_crs_by_chunk.get(chunk_of[image_id]),
        )
        for image_id, mapping in mappings.items()
    }
    transforms = {
        image_id: _world_compatibility_transform(record.mapping_chain)
        for image_id, record in native_records.items()
    }
    if metric_frames:
        metric_frames = _measure_metric_frame_domains(
            metric_frames, mappings, chunk_of, source_dimensions or {}, project.crs,
        )
    for match in original_matches:
        chunk_id = chunk_of[match.image_id]
        group_pixel = _apply_world_transform(match.pixel_x, match.pixel_y, frames[match.image_id])
        predicted = group_mappings[chunk_id].map_point(*group_pixel)
        residuals[(match.image_id, match.point_id)] = hypot(
            predicted[0] - match.world_x, predicted[1] - match.world_y,
        )
    optimizer_status = -1 if any(status < 0 for status in optimizer_statuses) else (
        0 if any(status == 0 for status in optimizer_statuses) else optimizer_statuses[-1]
    )
    return GeoreferenceResult(
        transforms=transforms, observation_residuals=residuals,
        iterations=total_evaluations,
        converged=bool(optimizer_success and residuals and max(residuals.values()) <= config.tolerance),
        crs=project.crs, optimizer_status=optimizer_status,
        optimizer_message="; ".join(optimizer_messages), max_iterations=config.max_iterations,
        settings=config, matrix_transforms=mappings, source_dimensions=source_dimensions,
        native_mapping_records=native_records, metric_frames=metric_frames or None,
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


def _chunk_frames(
    project: Project, *, excluded_point_ids: set[str] | None = None,
) -> tuple[dict[str, str], dict[str, WorldTransform]]:
    """Map each image to its constraint-connected chunk id and its canvas transform (identity if unconstrained)."""
    points = {point.id: point for point in project.control_points}
    excluded = excluded_point_ids or set()
    adjacency: dict[str, set[str]] = {}
    for constraint in project.constraints:
        if constraint.first_point_id in excluded or constraint.second_point_id in excluded:
            continue
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
            intrinsic = image.aligned_intrinsic if image.has_alignment else image.rest_intrinsic
            if intrinsic is not None:
                raise ValueError("The current geographic group solver cannot consume intrinsic layout mappings.")
            frames[image_id] = (
                image.aligned_x if image.has_alignment else (image.position_x if image.position_x is not None else 0.0),
                -(image.aligned_y if image.has_alignment else image.position_y),
                -(image.aligned_rotation if image.has_alignment else image.rotation),
                image.aligned_scale if image.has_alignment else image.scale,
            )
    return chunk_of, frames

