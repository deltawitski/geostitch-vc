from __future__ import annotations

from dataclasses import dataclass
import os
from json import dumps
from math import cos, radians, sin
from pathlib import Path
from shutil import copy2, copyfileobj
from tempfile import TemporaryDirectory
from typing import Callable

from PIL import Image, ImageChops, ImageDraw, ImageOps, TiffImagePlugin
from pyproj import CRS

from geostitch.models import ImageRecord, Project

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


def plan_export(project: Project, directory: str | Path, extension: str) -> list[ExportItem]:
    if extension not in FORMATS.values():
        raise ValueError(f"Unsupported export format '{extension}'.")
    destination = Path(directory)
    crs = CRS.from_user_input(project.crs)
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
        outputs = [target]
        if extension != ".tif":
            outputs.append(target.with_suffix(_WORLD_FILE_SUFFIX[extension]))
        if extension != ".tif" or crs.to_epsg() is None:
            outputs.append(target.with_suffix(".prj"))
        if image.mask_active:
            outputs.extend((destination / f"{candidate}_mask.png", destination / f"{candidate}_cutline.geojson"))
        items.append(ExportItem(image, target, tuple(outputs)))
    sources = {Path(image.path).resolve() for image in project.images}
    for item in items:
        if any(path.resolve() in sources for path in item.outputs):
            raise ValueError(f"Export would overwrite a source image: {item.target}")
    return items


def image_affine(image: ImageRecord) -> Affine:
    """Pixel (col, row) to world: x = a*col + b*row + c, y = d*col + e*row + f, with pixel Y pointing down."""
    angle = radians(image.world_rotation)
    scale = image.world_scale
    return (
        scale * cos(angle),
        scale * sin(angle),
        image.world_x,
        scale * sin(angle),
        -scale * cos(angle),
        image.world_y,
    )


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
) -> ExportResult:
    """Write each georeferenced image to `directory` as `<name>_georef<extension>`."""
    items = plan_export(project, directory, extension)
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    crs = CRS.from_user_input(project.crs)
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
                _export_image(image, staged, extension, crs, project.crs)
                _publish_image(Path(temporary), destination, overwrite)
        except (OSError, ValueError) as error:
            result.failures.append(f"{image.name}: {error}")
            continue
        result.written.append(target)
    if progress is not None:
        progress(len(result.written) + len(result.failures), len(items), "")
    return result


def _export_image(image: ImageRecord, target: Path, extension: str, crs: CRS, crs_name: str) -> None:
    with Image.open(image.path) as source:
        oriented = ImageOps.exif_transpose(source)
        try:
            pixels = _exportable_image(oriented, extension)
        finally:
            oriented.close()
    mask = None
    sidecar_mask = None
    try:
        affine = image_affine(image)
        if image.mask_active:
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
            (target.parent / f"{target.stem}_cutline.geojson").write_text(
                _cutline_geojson(image, affine, crs_name), encoding="utf-8",
            )
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
