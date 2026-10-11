from pathlib import Path
from typing import Callable, Iterable

from geostitch.models import ImageRecord


def missing_images(images: Iterable[ImageRecord]) -> list[ImageRecord]:
    return [image for image in images if not Path(image.path).is_file()]


def find_replacements(
    images: Iterable[ImageRecord], directory: Path, *, cancelled: Callable[[], bool] | None = None,
) -> dict[str, list[Path]]:
    images = list(images)
    names = {Path(image.path).name.casefold() for image in images}
    matches: dict[str, list[Path]] = {name: [] for name in names}
    for path in directory.rglob("*"):
        if cancelled is not None and cancelled():
            raise InterruptedError("Search cancelled")
        if path.name.casefold() in names and path.is_file():
            matches[path.name.casefold()].append(path.resolve())
    return {
        image.id: sorted(matches[Path(image.path).name.casefold()], key=lambda path: str(path).casefold())
        for image in images
    }


def apply_replacements(images: Iterable[ImageRecord], replacements: dict[str, Path]) -> list[str]:
    images_by_id = {image.id: image for image in images}
    resolved = {}
    validated_dimensions = {}
    for image_id, path in replacements.items():
        if image_id not in images_by_id:
            raise ValueError("The image is no longer in the project.")
        path = path.expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Replacement image does not exist: {path}")
        image = images_by_id[image_id]
        projective = any(matrix is not None and not matrix.is_affine for matrix in (image.rest_intrinsic, image.aligned_intrinsic))
        if projective and image.path != str(path):
            from PIL import Image

            try:
                with Image.open(path) as source:
                    width, height = source.size
                    if source.getexif().get(274, 1) in (5, 6, 7, 8):
                        width, height = height, width
            except (OSError, ValueError) as error:
                raise ValueError(f"Cannot validate replacement image geometry: {path}") from error
            if (width, height) != image.source_dimensions:
                raise ValueError("Projective image replacement changes source dimensions; review the mapping geometry before replacing this file.")
            validated_dimensions[image_id] = (width, height)
        resolved[image_id] = path
    changed = []
    for image_id, path in resolved.items():
        image = images_by_id[image_id]
        if image.path != str(path):
            image.path = str(path)
            image.source_dimensions = validated_dimensions.get(image_id)
            changed.append(image_id)
    return changed