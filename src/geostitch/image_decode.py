from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader

PREVIEW_MAX_EDGE = 2048
_SOURCE_WIDTH = "geostitch.source_width"
_SOURCE_HEIGHT = "geostitch.source_height"


def source_size(image: QImage) -> QSize:
    try:
        width, height = int(image.text(_SOURCE_WIDTH)), int(image.text(_SOURCE_HEIGHT))
        if width > 0 and height > 0:
            return QSize(width, height)
    except ValueError:
        pass
    return image.size()


def _with_source_size(image: QImage, size: QSize) -> QImage:
    image.setText(_SOURCE_WIDTH, str(size.width()))
    image.setText(_SOURCE_HEIGHT, str(size.height()))
    return image


def read_preview(path: str | Path, max_edge: int = PREVIEW_MAX_EDGE) -> tuple[QImage, str]:
    if max_edge <= 0:
        raise ValueError("Preview size must be positive.")
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    reader.setDecideFormatFromContent(True)
    original = reader.size()
    rotated = bool(reader.transformation().value & QImageIOHandler.Transformation.TransformationRotate90.value)
    oriented_size = QSize(original.height(), original.width()) if rotated else original
    if original.isValid() and max(original.width(), original.height()) > max_edge:
        reader.setScaledSize(original.scaled(max_edge, max_edge, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if not image.isNull():
        decoded_size = image.size()
        if max(image.width(), image.height()) > max_edge:
            image = image.scaled(max_edge, max_edge, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return _with_source_size(image, oriented_size if oriented_size.isValid() else decoded_size), ""
    qt_error = reader.errorString()
    try:
        return _read_preview_with_pillow(path, max_edge), ""
    except Exception as error:
        return QImage(), f"{qt_error}; Pillow fallback: {error}"


def _read_preview_with_pillow(path: str | Path, max_edge: int) -> QImage:
    from PIL import Image, ImageOps

    with Image.open(path) as source:
        width, height = source.size
        orientation = source.getexif().get(274, 1)
        original = QSize(height, width) if orientation in (5, 6, 7, 8) else QSize(width, height)
        source.draft("RGB", (max_edge, max_edge))
        source.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        oriented = ImageOps.exif_transpose(source)
        try:
            has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
            with oriented.convert("RGBA" if has_alpha else "RGB") as converted:
                image_format = QImage.Format.Format_RGBA8888 if has_alpha else QImage.Format.Format_RGB888
                image = QImage(converted.tobytes(), converted.width, converted.height,
                               converted.width * (4 if has_alpha else 3), image_format).copy()
        finally:
            oriented.close()
    return _with_source_size(image, original)


def _raw_region(region: QRect, size: QSize, orientation: int) -> tuple[QRect, QRect, QSize]:
    width, height = size.width(), size.height()
    oriented = QSize(height, width) if orientation in (5, 6, 7, 8) else size
    region = region.intersected(QRect(0, 0, oriented.width(), oriented.height()))
    if region.isEmpty():
        raise ValueError("The requested region is outside the source image.")

    def inverse(horizontal: int, vertical: int) -> tuple[int, int]:
        if orientation == 2:
            return width - horizontal, vertical
        if orientation == 3:
            return width - horizontal, height - vertical
        if orientation == 4:
            return horizontal, height - vertical
        if orientation == 5:
            return vertical, horizontal
        if orientation == 6:
            return vertical, height - horizontal
        if orientation == 7:
            return width - vertical, height - horizontal
        if orientation == 8:
            return width - vertical, horizontal
        return horizontal, vertical

    corners = [inverse(horizontal, vertical)
               for horizontal in (region.x(), region.x() + region.width())
               for vertical in (region.y(), region.y() + region.height())]
    left = min(point[0] for point in corners)
    top = min(point[1] for point in corners)
    right = max(point[0] for point in corners)
    bottom = max(point[1] for point in corners)
    return QRect(left, top, right - left, bottom - top), region, oriented


def read_region(path: str | Path, region: QRect) -> tuple[QImage, str]:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    reader.setDecideFormatFromContent(True)
    try:
        orientation = {0: 1, 1: 2, 2: 4, 3: 3, 4: 6, 5: 7, 6: 5, 7: 8}[reader.transformation().value]
        raw, clipped, original = _raw_region(region, reader.size(), orientation)
        reader.setClipRect(raw)
        image = reader.read()
        if not image.isNull() and image.size() == clipped.size():
            return _with_source_size(image, original), ""
        qt_error = reader.errorString()
    except (KeyError, ValueError) as error:
        qt_error = str(error)
    try:
        return _read_region_with_pillow(path, region), ""
    except Exception as error:
        return QImage(), f"{qt_error}; Pillow fallback: {error}"


def _read_region_with_pillow(path: str | Path, region: QRect) -> QImage:
    from PIL import Image

    with Image.open(path) as source:
        orientation = source.getexif().get(274, 1)
        raw, clipped, original = _raw_region(region, QSize(*source.size), orientation)
        transforms = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                      4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
                      6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
                      8: Image.Transpose.ROTATE_90}
        with source.crop((raw.x(), raw.y(), raw.x() + raw.width(), raw.y() + raw.height())) as cropped:
            oriented = cropped.transpose(transforms[orientation]) if orientation in transforms else cropped.copy()
            try:
                has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
                with oriented.convert("RGBA" if has_alpha else "RGB") as converted:
                    image_format = QImage.Format.Format_RGBA8888 if has_alpha else QImage.Format.Format_RGB888
                    image = QImage(converted.tobytes(), converted.width, converted.height,
                                   converted.width * (4 if has_alpha else 3), image_format).copy()
            finally:
                oriented.close()
    return _with_source_size(image, original)