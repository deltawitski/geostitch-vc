from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from pyproj import CRS, Transformer

Coordinate = tuple[float, float]


@dataclass
class VectorGeometry:
    points: list[Coordinate] = field(default_factory=list)
    lines: list[list[Coordinate]] = field(default_factory=list)
    polygons: list[list[Coordinate]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.points or self.lines or self.polygons)

    def bounds(self) -> tuple[float, float, float, float] | None:
        coordinates = [*self.points, *(c for line in self.lines for c in line), *(c for ring in self.polygons for c in ring)]
        if not coordinates:
            return None
        xs = [x for x, _y in coordinates]
        ys = [y for _x, y in coordinates]
        return min(xs), min(ys), max(xs), max(ys)


def read_geojson(path: str | Path) -> tuple[VectorGeometry, str]:
    """Parse a GeoJSON file into flat geometry and the CRS it declares (WGS 84 unless a legacy `crs` member says otherwise)."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("A GeoJSON file must contain a JSON object.")
    crs = "EPSG:4326"
    declared = data.get("crs")
    if isinstance(declared, dict) and isinstance(declared.get("properties"), dict):
        name = declared["properties"].get("name")
        if isinstance(name, str) and name:
            crs = name
    geometry = VectorGeometry()
    _collect(data, geometry)
    if geometry.is_empty():
        raise ValueError("The file has no point, line, or polygon geometry.")
    return geometry, crs


def _collect(node: object, out: VectorGeometry) -> None:
    if not isinstance(node, dict):
        return
    kind = node.get("type")
    if kind == "FeatureCollection":
        for feature in node.get("features") or []:
            _collect(feature, out)
    elif kind == "Feature":
        _collect(node.get("geometry"), out)
    elif kind == "GeometryCollection":
        for member in node.get("geometries") or []:
            _collect(member, out)
    else:
        _add_coordinates(kind, node.get("coordinates"), out)


def _position(value: object) -> Coordinate:
    return float(value[0]), float(value[1])


def _add_coordinates(kind: object, coordinates: object, out: VectorGeometry) -> None:
    if not coordinates:
        return
    if kind == "Point":
        out.points.append(_position(coordinates))
    elif kind == "MultiPoint":
        out.points.extend(_position(item) for item in coordinates)
    elif kind == "LineString":
        out.lines.append([_position(item) for item in coordinates])
    elif kind == "MultiLineString":
        out.lines.extend([_position(item) for item in line] for line in coordinates)
    elif kind == "Polygon":
        out.polygons.extend([_position(item) for item in ring] for ring in coordinates)
    elif kind == "MultiPolygon":
        out.polygons.extend([_position(item) for item in ring] for polygon in coordinates for ring in polygon)


def reproject(geometry: VectorGeometry, source_crs: str, target_crs: str) -> VectorGeometry:
    source, target = CRS.from_user_input(source_crs), CRS.from_user_input(target_crs)
    if source == target:
        return geometry
    return transform_geometry(geometry, Transformer.from_crs(source, target, always_xy=True))


def transform_geometry(geometry: VectorGeometry, transformer: Transformer) -> VectorGeometry:
    def convert(coordinates: list[Coordinate]) -> list[Coordinate]:
        return [transformer.transform(x, y) for x, y in coordinates]

    return VectorGeometry(
        points=convert(geometry.points),
        lines=[convert(line) for line in geometry.lines],
        polygons=[convert(ring) for ring in geometry.polygons],
    )


@dataclass
class PartSet:
    """Lines or rings stored as one coordinate array plus per-part offsets and bounding boxes."""

    coords: np.ndarray
    offsets: np.ndarray
    bounds: np.ndarray

    @property
    def count(self) -> int:
        return len(self.offsets) - 1


@dataclass
class PreparedVectors:
    """Vector geometry in map scene coordinates (Web Mercator x, -y), ready for culled drawing."""

    points: np.ndarray
    polygons: PartSet
    lines: PartSet
    bounds: tuple[float, float, float, float] | None


_CHUNK = 100_000


def prepare_vectors(
    geometry: VectorGeometry,
    source_crs: str,
    project_crs: str,
    progress: Callable[[int, int], None] | None = None,
) -> PreparedVectors:
    """Reproject source -> project CRS -> Web Mercator in chunks, reporting (done, total) vertices."""
    source, project, mercator = CRS.from_user_input(source_crs), CRS.from_user_input(project_crs), CRS.from_epsg(3857)
    steps = [
        Transformer.from_crs(a, b, always_xy=True)
        for a, b in ((source, project), (project, mercator))
        if a != b
    ]
    points = _flatten([[point] for point in geometry.points])[0]
    polygons = _flatten(geometry.polygons)
    lines = _flatten(geometry.lines)
    total = max(1, (len(points) + len(polygons[0]) + len(lines[0])) * max(1, len(steps)))
    done = [0]

    def project_chunks(coords: np.ndarray) -> np.ndarray:
        for step in steps:
            out = np.empty_like(coords)
            for start in range(0, len(coords), _CHUNK):
                part = coords[start:start + _CHUNK]
                out[start:start + len(part), 0], out[start:start + len(part), 1] = step.transform(part[:, 0], part[:, 1])
                done[0] += len(part)
                if progress is not None:
                    progress(done[0], total)
            coords = out
        return coords

    points = project_chunks(points)
    points = points[np.isfinite(points).all(axis=1)]
    points[:, 1] *= -1
    polygon_set, line_set = (_part_set(project_chunks(coords), lengths) for coords, lengths in (polygons, lines))
    extents = [np.array([*points.min(axis=0), *points.max(axis=0)])] if len(points) else []
    extents += [
        np.array([parts.bounds[:, 0].min(), parts.bounds[:, 1].min(), parts.bounds[:, 2].max(), parts.bounds[:, 3].max()])
        for parts in (polygon_set, line_set)
        if parts.count
    ]
    bounds = None
    if extents:
        stacked = np.vstack(extents)
        bounds = (stacked[:, 0].min(), stacked[:, 1].min(), stacked[:, 2].max(), stacked[:, 3].max())
    return PreparedVectors(points=points, polygons=polygon_set, lines=line_set, bounds=bounds)


def _flatten(parts: list[list[Coordinate]]) -> tuple[np.ndarray, np.ndarray]:
    lengths = np.fromiter((len(part) for part in parts), dtype=np.int64, count=len(parts))
    kept = [part for part in parts if part]
    if not kept:
        return np.zeros((0, 2)), lengths[:0]
    return np.array([coordinate for part in kept for coordinate in part], dtype=np.float64), lengths[lengths > 0]


def _part_set(coords: np.ndarray, lengths: np.ndarray) -> PartSet:
    """Drop non-finite vertices and empty parts, flip Y into scene space, and compute per-part bounds."""
    coords = coords.copy()
    coords[:, 1] *= -1
    part_index = np.repeat(np.arange(len(lengths)), lengths)
    finite = np.isfinite(coords).all(axis=1)
    coords, part_index = coords[finite], part_index[finite]
    counts = np.bincount(part_index, minlength=len(lengths))
    present = counts > 0
    counts = counts[present]
    offsets = np.concatenate(([0], np.cumsum(counts)))
    if not len(counts):
        return PartSet(coords, offsets, np.zeros((0, 4)))
    starts = offsets[:-1]
    bounds = np.column_stack((
        np.minimum.reduceat(coords[:, 0], starts),
        np.minimum.reduceat(coords[:, 1], starts),
        np.maximum.reduceat(coords[:, 0], starts),
        np.maximum.reduceat(coords[:, 1], starts),
    ))
    return PartSet(coords, offsets, bounds)
