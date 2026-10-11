from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor, isfinite
import os
from json import dumps
from pathlib import Path
from shutil import copy2, copyfileobj
from tempfile import TemporaryDirectory
from typing import Callable
from xml.etree import ElementTree

from PIL import Image, ImageChops, ImageDraw, ImageOps, TiffImagePlugin
from pyproj import CRS
from pyproj import Transformer

from geostitch.models import ImageRecord, Project
from geostitch.transforms import MatrixTransform
from geostitch.warp import WarpTransform

Affine = tuple[float, float, float, float, float, float]

FORMATS = {
    "GeoTIFF (.tif)": ".tif",
    "PNG + world file (.png/.pgw)": ".png",
    "JPEG + world file (.jpg/.jgw)": ".jpg",
}
_WORLD_FILE_SUFFIX = {".png": ".pgw", ".jpg": ".jgw"}

_MODEL_TRANSFORMATION = 34264
_GEO_KEY_DIRECTORY = 34735
_TYPE_SHORT = 3
_TYPE_DOUBLE = 12
DEFAULT_MAX_WARP_PIXELS = 20_000_000


@dataclass
class ExportResult:
    written: list[Path]
    failures: list[str]
    cancelled: bool = False


@dataclass
class ExportItem:
    image: ImageRecord
    target: Path
    outputs: tuple[Path, ...]
    warp_grid: WarpGrid | None = None


@dataclass(frozen=True)
class WarpGrid:
    crs: CRS
    bounds: tuple[float, float, float, float]
    resolution: float
    resampling: str = "bilinear"
    max_pixels: int = DEFAULT_MAX_WARP_PIXELS

    def __post_init__(self) -> None:
        if not isinstance(self.crs, CRS):
            object.__setattr__(self, "crs", CRS.from_user_input(self.crs))
        if len(self.bounds) != 4 or not all(isfinite(value) for value in self.bounds):
            raise ValueError("Warp grid bounds must be four finite coordinates.")
        if self.bounds[0] >= self.bounds[2] or self.bounds[1] >= self.bounds[3]:
            raise ValueError("Warp grid bounds must have positive width and height.")
        if not isfinite(self.resolution) or self.resolution <= 0:
            raise ValueError("Warp grid resolution must be finite and greater than zero.")
        width = ceil((self.bounds[2] - self.bounds[0]) / self.resolution)
        height = ceil((self.bounds[3] - self.bounds[1]) / self.resolution)
        object.__setattr__(self, "bounds", (
            self.bounds[0], self.bounds[3] - height * self.resolution,
            self.bounds[0] + width * self.resolution, self.bounds[3],
        ))
        if self.resampling not in ("nearest", "bilinear"):
            raise ValueError("Warp resampling must be nearest or bilinear.")
        if not isinstance(self.max_pixels, int) or isinstance(self.max_pixels, bool) or self.max_pixels <= 0:
            raise ValueError("Warp grid pixel limit must be a positive integer.")
        if self.width * self.height > self.max_pixels:
            raise ValueError(
                f"Warp grid requires {self.width * self.height:,} pixels, exceeding the "
                f"configured limit of {self.max_pixels:,}. Increase output resolution or extent."
            )

    @property
    def width(self) -> int:
        return ceil((self.bounds[2] - self.bounds[0]) / self.resolution)

    @property
    def height(self) -> int:
        return ceil((self.bounds[3] - self.bounds[1]) / self.resolution)

    @property
    def affine(self) -> Affine:
        return self.resolution, 0.0, self.bounds[0], 0.0, -self.resolution, self.bounds[3]


def plan_export(
    project: Project, directory: str | Path, extension: str, *,
    output_crs: CRS | str | None = None, resolution: float | None = None,
    resampling: str = "bilinear", max_pixels: int = DEFAULT_MAX_WARP_PIXELS,
    bounds: tuple[float, float, float, float] | None = None,
) -> list[ExportItem]:
    if extension not in FORMATS.values():
        raise ValueError(f"Unsupported export format '{extension}'.")
    destination = Path(directory)
    crs = CRS.from_user_input(output_crs or project.crs)
    active_crs = CRS.from_user_input(project.crs)
    used_names: set[str] = set()
    items = []
    for image in project.images:
        if image.world_x is None or image.world_y is None:
            continue
        stem = f"{Path(image.name).stem}_georef"
        candidate, counter = stem, 2
        while candidate.casefold() in used_names:
            candidate = f"{stem}-{counter}"
            counter += 1
        used_names.add(candidate.casefold())
        target = destination / f"{candidate}{extension}"
        warp_grid = None
        native = image.native_world_mapping
        if native is None and crs != active_crs:
            raise ValueError(
                f"Image '{image.name}' has no native CRS mapping; it cannot be exported to a different CRS safely."
            )
        if native is not None and (
            CRS.from_user_input(native.native_crs) != crs
            or not isinstance(native.mapping, MatrixTransform)
            or native.mapping.model == "projective"
            or native.matrix_crs is not None
        ):
            if resolution is None:
                raise ValueError("Choose an output pixel resolution for warped raster export.")
            warp_grid = plan_warp_grid(
                image, crs, resolution, resampling=resampling, max_pixels=max_pixels, bounds=bounds,
            )
        outputs = [target]
        if extension != ".tif":
            outputs.append(target.with_suffix(_WORLD_FILE_SUFFIX[extension]))
            outputs.append(target.with_suffix(target.suffix + ".aux.xml"))
        if extension != ".tif" or crs.to_epsg() is None:
            outputs.append(target.with_suffix(".prj"))
        if image.mask_active:
            outputs.extend((destination / f"{candidate}_mask.png", destination / f"{candidate}_cutline.geojson"))
        items.append(ExportItem(image, target, tuple(outputs), warp_grid))
    sources = {Path(image.path).resolve() for image in project.images}
    for item in items:
        if any(path.resolve() in sources for path in item.outputs):
            raise ValueError(f"Export would overwrite a source image: {item.target}")
    return items


def image_affine(image: ImageRecord, output_crs: CRS | str | None = None) -> Affine:
    """Pixel (col, row) to world: x = a*col + b*row + c, y = d*col + e*row + f, with pixel Y pointing down."""
    native_mapping = image.native_world_mapping
    if native_mapping is not None and isinstance(native_mapping.mapping, WarpTransform):
        raise ValueError("TPS native world mappings require inverse-warp export.")
    if native_mapping is not None and native_mapping.matrix_crs is not None:
        raise ValueError("Native mappings with a CRS conversion require inverse-warp export.")
    if native_mapping is not None and output_crs is not None:
        target_crs = CRS.from_user_input(output_crs)
        native_crs = CRS.from_user_input(native_mapping.native_crs)
        if native_crs == target_crs:
            if not native_mapping.mapping.is_affine:
                raise ValueError("Projective native world mappings require inverse-warp export.")
            return native_mapping.mapping.to_affine()
        if native_mapping.mapping.model != "similarity":
            raise ValueError(
                "Affine and projective native world mappings require reprojection-aware warped export "
                "when the output CRS differs from their native CRS."
            )
    mapping = image.world_mapping
    if mapping is None:
        raise ValueError("The image does not have a geographic mapping.")
    return mapping.to_affine()


def plan_warp_grid(
    image: ImageRecord,
    output_crs: CRS | str,
    resolution: float,
    *,
    resampling: str = "bilinear",
    max_pixels: int = DEFAULT_MAX_WARP_PIXELS,
    bounds: tuple[float, float, float, float] | None = None,
) -> WarpGrid:
    if not isfinite(resolution) or resolution <= 0:
        raise ValueError("Output resolution must be finite and greater than zero.")
    native = image.native_world_mapping
    if native is None:
        raise ValueError("Warped raster export requires a native image mapping.")
    source_size = image.source_dimensions or native.source_size
    if source_size is None:
        raise ValueError("Warped raster export requires known source image dimensions.")
    if isinstance(native.mapping, MatrixTransform):
        native.mapping.domain_bounds(*source_size)
    else:
        width, height = source_size
        boundary = ((0, 0), (width, 0), (width, height), (0, height))
        if not all(native.mapping.supports(boundary)):
            raise ValueError("Warped raster export requires TPS support across the complete image domain.")
    target = CRS.from_user_input(output_crs)
    if bounds is None:
        converter = Transformer.from_crs(native.native_crs, target, always_xy=True, force_over=True)
        width, height = source_size
        boundary = []
        for index in range(65):
            fraction = index / 64
            boundary.extend(((width * fraction, 0), (width * fraction, height),
                             (0, height * fraction), (width, height * fraction)))
        native_points = [native.mapping_chain.map_point(x, y) for x, y in boundary]
        output_x, output_y = converter.transform(
            [point[0] for point in native_points], [point[1] for point in native_points], errcheck=True,
        )
        if not all(isfinite(value) for value in (*output_x, *output_y)):
            raise ValueError("Image footprint cannot be transformed into the requested output CRS.")
        margin = float(resolution) * 0.5
        min_x = floor((min(output_x) - margin) / resolution) * resolution
        min_y = floor((min(output_y) - margin) / resolution) * resolution
        max_x = ceil((max(output_x) + margin) / resolution) * resolution
        max_y = ceil((max(output_y) + margin) / resolution) * resolution
        bounds = min_x, min_y, max_x, max_y
    return WarpGrid(target, bounds, float(resolution), resampling, max_pixels)


def warp_native_pixels(pixels: Image.Image, image: ImageRecord, grid: WarpGrid) -> Image.Image:
    import numpy as np

    native = image.native_world_mapping
    if native is None:
        raise ValueError("Warped raster export requires a native image mapping.")
    to_native = Transformer.from_crs(grid.crs, native.native_crs, always_xy=True, force_over=True)
    to_matrix_crs = (
        Transformer.from_crs(native.matrix_crs, native.native_crs, always_xy=True, force_over=True)
        if native.matrix_crs is not None else None
    )
    if to_matrix_crs is not None:
        from pyproj.enums import TransformDirection

    inverse = native.mapping.inverse() if isinstance(native.mapping, MatrixTransform) else None
    if inverse is not None:
        ia, ib, ic, id_, ie, iff, ig, ih, ii = inverse.coefficients
    source = np.asarray(pixels.convert("RGBA"), dtype=np.uint8)
    source_height, source_width = source.shape[:2]
    output = np.zeros((grid.height, grid.width, 4), dtype=np.uint8)
    for row_start in range(0, grid.height, 256):
        row_end = min(grid.height, row_start + 256)
        y = grid.bounds[3] - (np.arange(row_start, row_end, dtype=np.float64) + 0.5) * grid.resolution
        for column_start in range(0, grid.width, 512):
            column_end = min(grid.width, column_start + 512)
            x = grid.bounds[0] + (np.arange(column_start, column_end, dtype=np.float64) + 0.5) * grid.resolution
            target_x, target_y = np.meshgrid(x, y)
            native_x, native_y = to_native.transform(target_x, target_y, errcheck=False)
            if to_matrix_crs is not None:
                matrix_x, matrix_y = to_matrix_crs.transform(
                    native_x, native_y, direction=TransformDirection.INVERSE, errcheck=False,
                )
            else:
                matrix_x, matrix_y = native_x, native_y
            finite_target = np.isfinite(matrix_x) & np.isfinite(matrix_y)
            source_x = np.zeros_like(matrix_x)
            source_y = np.zeros_like(matrix_y)
            warp_inverse_valid = np.zeros(matrix_x.shape, dtype=bool)
            if isinstance(native.mapping, WarpTransform):
                flat_valid = np.flatnonzero(finite_target.ravel())
                flat_x, flat_y = source_x.ravel(), source_y.ravel()
                flat_warp_valid = warp_inverse_valid.ravel()
                for inverse_start in range(0, len(flat_valid), 2048):
                    indices = flat_valid[inverse_start:inverse_start + 2048]
                    target_points = np.column_stack((matrix_x.ravel()[indices], matrix_y.ravel()[indices]))
                    inverse_points = native.mapping.inverse_points(
                        target_points, tolerance=1e-5, invalid_as_nan=True,
                    )
                    point_valid = np.isfinite(inverse_points).all(axis=1)
                    flat_x[indices[point_valid]] = inverse_points[point_valid, 0]
                    flat_y[indices[point_valid]] = inverse_points[point_valid, 1]
                    flat_warp_valid[indices[point_valid]] = True
                valid = finite_target & warp_inverse_valid
            else:
                denominator = ig * matrix_x + ih * matrix_y + ii
                denominator_scale = np.abs(ig * matrix_x) + np.abs(ih * matrix_y) + abs(ii)
                valid = finite_target & (np.abs(denominator) > 1e-12 * denominator_scale)
                safe_denominator = np.where(valid, denominator, 1.0)
                source_x = (ia * matrix_x + ib * matrix_y + ic) / safe_denominator
                source_y = (id_ * matrix_x + ie * matrix_y + iff) / safe_denominator
            valid &= np.isfinite(source_x) & np.isfinite(source_y) & (source_x >= 0) & (source_x < source_width) & (source_y >= 0) & (source_y < source_height)
            safe_x = np.where(valid, source_x, 0.0)
            safe_y = np.where(valid, source_y, 0.0)
            tile = output[row_start:row_end, column_start:column_end]
            if grid.resampling == "nearest":
                columns = np.clip(np.floor(safe_x).astype(np.int64), 0, source_width - 1)
                rows = np.clip(np.floor(safe_y).astype(np.int64), 0, source_height - 1)
                tile[valid] = source[rows[valid], columns[valid]]
                continue
            center_x, center_y = safe_x - 0.5, safe_y - 0.5
            left = np.floor(center_x).astype(np.int64)
            top = np.floor(center_y).astype(np.int64)
            fraction_x, fraction_y = center_x - left, center_y - top
            left0, left1 = np.clip(left, 0, source_width - 1), np.clip(left + 1, 0, source_width - 1)
            top0, top1 = np.clip(top, 0, source_height - 1), np.clip(top + 1, 0, source_height - 1)
            samples = np.zeros((*source_x.shape, 4), dtype=np.float32)
            for source_rows, source_columns, weight in (
                (top0, left0, (1 - fraction_x) * (1 - fraction_y)),
                (top0, left1, fraction_x * (1 - fraction_y)),
                (top1, left0, (1 - fraction_x) * fraction_y),
                (top1, left1, fraction_x * fraction_y),
            ):
                corner = source[source_rows, source_columns].astype(np.float32)
                corner[:, :, :3] *= corner[:, :, 3:4] / 255.0
                samples += corner * weight[:, :, None].astype(np.float32)
            alpha = samples[:, :, 3]
            nontransparent = valid & (alpha > 0)
            samples[:, :, :3][nontransparent] *= (255.0 / alpha[nontransparent])[:, None]
            tile[valid] = np.clip(samples[valid], 0, 255).astype(np.uint8)
    return Image.fromarray(output, "RGBA")


def _publish_exclusive(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError:
        created = False
        try:
            with destination.open("xb") as output:
                created = True
                with source.open("rb") as input_file:
                    copyfileobj(input_file, output)
        except OSError:
            if created:
                destination.unlink(missing_ok=True)
            raise


def _publish_image(staging: Path, destination: Path, overwrite: bool) -> None:
    files = list(staging.iterdir())
    originals = {}
    published = []
    retained = set()
    try:
        if overwrite:
            for source in files:
                target = destination / source.name
                if target.exists():
                    if not target.is_file():
                        raise IsADirectoryError(f"Output is not a file: {target}")
                    backup = destination / f".{target.name}.{staging.name}.backup"
                    originals[target] = backup
                    copy2(target, backup)
        for source in files:
            target = destination / source.name
            if overwrite:
                os.replace(source, target)
            else:
                _publish_exclusive(source, target)
            published.append(target)
    except OSError as error:
        recovery_files = []
        incomplete_files = []
        for target in reversed(published):
            try:
                if target in originals:
                    os.replace(originals[target], target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                if target in originals:
                    retained.add(originals[target])
                    recovery_files.append(str(originals[target]))
                else:
                    incomplete_files.append(str(target))
        if recovery_files or incomplete_files:
            details = str(error)
            if recovery_files:
                details += f". Previous outputs preserved at: {', '.join(recovery_files)}"
            if incomplete_files:
                details += f". Incomplete outputs could not be removed: {', '.join(incomplete_files)}"
            raise OSError(details) from error
        raise
    finally:
        for backup in originals.values():
            if backup not in retained:
                backup.unlink(missing_ok=True)


def export_project_images(
    project: Project,
    directory: str | Path,
    extension: str,
    progress: Callable[[int, int, str], None] | None = None,
    *,
    cancelled: Callable[[], bool] | None = None,
    overwrite: bool = False,
    output_crs: CRS | str | None = None,
    resolution: float | None = None,
    resampling: str = "bilinear",
    max_pixels: int = DEFAULT_MAX_WARP_PIXELS,
    bounds: tuple[float, float, float, float] | None = None,
) -> ExportResult:
    """Write each georeferenced image to `directory` as `<name>_georef<extension>`."""
    items = plan_export(
        project, directory, extension, output_crs=output_crs, resolution=resolution,
        resampling=resampling, max_pixels=max_pixels, bounds=bounds,
    )
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    crs = CRS.from_user_input(output_crs or project.crs)
    result = ExportResult(written=[], failures=[])
    for index, item in enumerate(items):
        if cancelled is not None and cancelled():
            result.cancelled = True
            break
        image, target = item.image, item.target
        if progress is not None:
            progress(index, len(items), image.name)
        if cancelled is not None and cancelled():
            result.cancelled = True
            break
        try:
            conflicts = [path for path in item.outputs if path.exists()]
            if conflicts and not overwrite:
                raise FileExistsError(f"Output already exists: {conflicts[0]}")
            with TemporaryDirectory(prefix=".geostitch-export-", dir=destination) as temporary:
                staged = Path(temporary) / target.name
                _export_image(image, staged, extension, crs, crs.to_string(), item.warp_grid)
                _publish_image(Path(temporary), destination, overwrite)
        except (OSError, ValueError) as error:
            result.failures.append(f"{image.name}: {error}")
            continue
        result.written.append(target)
    if progress is not None:
        progress(len(result.written) + len(result.failures), len(items), "")
    return result


def _export_image(
    image: ImageRecord, target: Path, extension: str, crs: CRS, crs_name: str,
    warp_grid: WarpGrid | None = None,
) -> None:
    with Image.open(image.path) as source:
        oriented = ImageOps.exif_transpose(source)
        try:
            pixels = _exportable_image(oriented, extension)
        finally:
            oriented.close()
    mask = None
    sidecar_mask = None
    try:
        if warp_grid is not None:
            if extension == ".jpg":
                raise ValueError("Cross-CRS warped export needs alpha/nodata support; choose GeoTIFF or PNG instead of JPEG.")
            if image.mask_active:
                mask = build_mask(pixels.size, image.mask_polygon, image.mask_mode)
                rgba = pixels.convert("RGBA")
                alpha = rgba.getchannel("A")
                from PIL import ImageChops

                alpha = ImageChops.multiply(alpha, mask)
                rgba.putalpha(alpha)
                pixels.close()
                alpha.close()
                pixels = rgba
            warped = warp_native_pixels(pixels, image, warp_grid)
            pixels.close()
            pixels = warped
            affine = warp_grid.affine
        else:
            affine = image_affine(image, crs)
        if image.mask_active and warp_grid is None:
            mask = build_mask(pixels.size, image.mask_polygon, image.mask_mode)
            masked, sidecar_mask = _apply_mask(pixels, mask, extension)
            if masked is not pixels:
                pixels.close()
                pixels = masked
        if extension == ".tif":
            write_geotiff(target, pixels, affine, crs)
        else:
            write_world_file_image(target, pixels, affine, crs)
        if sidecar_mask is not None:
            sidecar_mask.save(target.parent / f"{target.stem}_mask.png")
        if image.mask_active:
            cutline = _warped_cutline_geojson(image, crs_name) if warp_grid is not None else _cutline_geojson(image, affine, crs_name)
            (target.parent / f"{target.stem}_cutline.geojson").write_text(cutline, encoding="utf-8")
    finally:
        pixels.close()
        if mask is not None:
            mask.close()


def build_mask(size: tuple[int, int], polygon: list[list[float]], mode: str) -> Image.Image:
    """8-bit mask, 255 where the image stays visible."""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon([tuple(point) for point in polygon], fill=255)
    return ImageOps.invert(mask) if mode == "outside" else mask


def _apply_mask(pixels: Image.Image, mask: Image.Image, extension: str) -> tuple[Image.Image, Image.Image | None]:
    """Keep colour data intact: clip goes into a new alpha band, or a sidecar when alpha can't be added or is taken."""
    if extension == ".jpg" or pixels.mode in ("I;16", "RGBA", "LA"):
        return pixels, mask
    pixels = pixels.convert("LA" if pixels.mode == "L" else "RGBA")
    pixels.putalpha(mask)
    return pixels, None


def _cutline_geojson(image: ImageRecord, affine: Affine, crs: str) -> str:
    a, b, c, d, e, f = affine
    ring = [[a * x + b * y + c, d * x + e * y + f] for x, y in image.mask_polygon]
    ring.append(ring[0])
    return dumps({
        "type": "FeatureCollection",
        "crs_name": crs,
        "features": [{
            "type": "Feature",
            "properties": {"image": image.name, "keep": image.mask_mode},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }],
    }, indent=2)


def _warped_cutline_geojson(image: ImageRecord, target_crs: str) -> str:
    from pyproj import Transformer

    native = image.native_world_mapping
    if native is None:
        raise ValueError("A native world mapping is required to transform the export cutline.")
    transformer = Transformer.from_crs(native.native_crs, target_crs, always_xy=True, force_over=True)
    polygon = []
    for start, end in zip(image.mask_polygon, image.mask_polygon[1:] + image.mask_polygon[:1]):
        for index in range(16):
            fraction = index / 16
            source_x = start[0] + (end[0] - start[0]) * fraction
            source_y = start[1] + (end[1] - start[1]) * fraction
            native_x, native_y = native.mapping_chain.map_point(source_x, source_y)
            polygon.append(transformer.transform(native_x, native_y, errcheck=True))
    ring = [list(point) for point in polygon]
    ring.append(ring[0])
    return dumps({
        "type": "FeatureCollection",
        "crs_name": target_crs,
        "features": [{
            "type": "Feature",
            "properties": {"image": image.name, "keep": image.mask_mode},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }],
    }, indent=2)


def _exportable_image(source: Image.Image, extension: str) -> Image.Image:
    if extension == ".jpg":
        return source.convert("RGB")
    if source.mode in ("L", "RGB", "RGBA", "I;16"):
        return source.copy()
    return source.convert("RGBA" if "A" in source.getbands() or source.mode == "P" else "RGB")


def write_geotiff(path: Path, pixels: Image.Image, affine: Affine, crs: CRS) -> None:
    a, b, c, d, e, f = affine
    directory = TiffImagePlugin.ImageFileDirectory_v2()
    directory[_MODEL_TRANSFORMATION] = (a, b, 0.0, c, d, e, 0.0, f, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    directory.tagtype[_MODEL_TRANSFORMATION] = _TYPE_DOUBLE
    keys = _geo_keys(crs)
    directory[_GEO_KEY_DIRECTORY] = (1, 1, 0, len(keys)) + tuple(value for key in keys for value in key)
    directory.tagtype[_GEO_KEY_DIRECTORY] = _TYPE_SHORT
    pixels.save(path, format="TIFF", tiffinfo=directory, compression="tiff_lzw")
    if crs.to_epsg() is None:
        path.with_suffix(".prj").write_text(crs.to_wkt(), encoding="utf-8")


def _geo_keys(crs: CRS) -> list[tuple[int, int, int, int]]:
    """GeoKey entries as (key id, tag location, count, value)."""
    epsg = crs.to_epsg()
    geographic = crs.is_geographic
    keys = [
        (1024, 0, 1, 2 if geographic else 1),  # GTModelTypeGeoKey
        (1025, 0, 1, 1),  # GTRasterTypeGeoKey: PixelIsArea
    ]
    code = epsg if epsg is not None else 32767
    keys.append((2048 if geographic else 3072, 0, 1, code))
    return keys


def write_world_file_image(path: Path, pixels: Image.Image, affine: Affine, crs: CRS) -> None:
    a, b, c, d, e, f = affine
    pixels.save(path)
    # World files reference the center of the upper-left pixel.
    center_x = c + 0.5 * a + 0.5 * b
    center_y = f + 0.5 * d + 0.5 * e
    lines = (a, d, b, e, center_x, center_y)
    path.with_suffix(_WORLD_FILE_SUFFIX[path.suffix.lower()]).write_text(
        "\n".join(f"{value:.12f}" for value in lines) + "\n",
        encoding="utf-8",
    )
    path.with_suffix(".prj").write_text(crs.to_wkt(), encoding="utf-8")
    a, b, c, d, e, f = affine
    auxiliary = ElementTree.Element("PAMDataset")
    ElementTree.SubElement(
        auxiliary, "SRS", {"dataAxisToSRSAxisMapping": "1,2"},
    ).text = crs.to_wkt(version="WKT1_GDAL")
    ElementTree.SubElement(auxiliary, "GeoTransform").text = "  " + ", ".join(
        f"{value: .16e}" for value in (c, a, b, f, d, e)
    )
    metadata = ElementTree.SubElement(auxiliary, "Metadata", {"domain": "IMAGE_STRUCTURE"})
    ElementTree.SubElement(metadata, "MDI", {"key": "INTERLEAVE"}).text = (
        "PIXEL" if pixels.mode in ("RGB", "RGBA") else "BAND"
    )
    path.with_suffix(path.suffix + ".aux.xml").write_bytes(
        ElementTree.tostring(auxiliary, encoding="utf-8"),
    )
