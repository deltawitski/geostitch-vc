from __future__ import annotations

from dataclasses import dataclass
from json import dumps
from math import cos, radians, sin
from pathlib import Path
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


def export_project_images(
    project: Project,
    directory: str | Path,
    extension: str,
    progress: Callable[[int, int, str], None] | None = None,
) -> ExportResult:
    """Write each georeferenced image to `directory` as `<name>_georef<extension>`."""
    if extension not in FORMATS.values():
        raise ValueError(f"Unsupported export format '{extension}'.")
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    crs = CRS.from_user_input(project.crs)
    result = ExportResult(written=[], failures=[])
    used_names: set[str] = set()
    exportable = [image for image in project.images if image.world_x is not None and image.world_y is not None]
    for index, image in enumerate(exportable):
        if progress is not None:
            progress(index, len(exportable), image.name)
        stem = f"{Path(image.name).stem}_georef"
        candidate, counter = stem, 2
        while candidate.casefold() in used_names:
            candidate = f"{stem}-{counter}"
            counter += 1
        used_names.add(candidate.casefold())
        target = destination / f"{candidate}{extension}"
        try:
            with Image.open(image.path) as source:
                source.load()
                pixels = _exportable_image(ImageOps.exif_transpose(source), extension)
            affine = image_affine(image)
            sidecar_mask = None
            if image.mask_active:
                mask = build_mask(pixels.size, image.mask_polygon, image.mask_mode)
                pixels, sidecar_mask = _apply_mask(pixels, mask, extension)
            if extension == ".tif":
                write_geotiff(target, pixels, affine, crs)
            else:
                write_world_file_image(target, pixels, affine, crs)
            if sidecar_mask is not None:
                sidecar_mask.save(destination / f"{candidate}_mask.png")
            if image.mask_active:
                (destination / f"{candidate}_cutline.geojson").write_text(
                    _cutline_geojson(image, affine, project.crs),
                    encoding="utf-8",
                )
        except (OSError, ValueError) as error:
            result.failures.append(f"{image.name}: {error}")
            continue
        result.written.append(target)
    if progress is not None:
        progress(len(exportable), len(exportable), "")
    return result


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
