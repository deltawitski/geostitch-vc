from PIL import Image
from PySide6.QtCore import QSize

from geostitch.image_decode import _read_preview_with_pillow, read_preview, source_size


def test_preview_bounds_preserve_original_dimensions(tmp_path):
    path = tmp_path / "large.png"
    Image.new("RGB", (5001, 3001), (10, 20, 30)).save(path)
    preview, error = read_preview(path, 512)
    assert error == ""
    assert max(preview.width(), preview.height()) <= 512
    assert source_size(preview) == QSize(5001, 3001)
    assert preview.pixelColor(10, 10).red() == 10


def test_preview_orientation_uses_oriented_source_size(tmp_path):
    path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (900, 600), (10, 20, 30)).save(path, exif=exif)
    preview, error = read_preview(path, 300)
    assert error == ""
    assert source_size(preview) == QSize(600, 900)
    assert preview.height() > preview.width()
    assert max(preview.width(), preview.height()) <= 300


def test_pillow_preview_preserves_alpha_and_source_dimensions(tmp_path):
    path = tmp_path / "transparent.png"
    Image.new("RGBA", (1200, 800), (10, 20, 30, 100)).save(path)
    preview = _read_preview_with_pillow(path, 200)
    assert source_size(preview) == QSize(1200, 800)
    assert max(preview.width(), preview.height()) <= 200
    assert preview.pixelColor(10, 10).alpha() == 100


def test_pillow_preview_handles_exif_rotation(tmp_path):
    path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 8
    Image.new("RGB", (1200, 800)).save(path, exif=exif)
    preview = _read_preview_with_pillow(path, 200)
    assert source_size(preview) == QSize(800, 1200)
    assert preview.height() > preview.width()


def test_codec_without_header_size_preserves_decoded_source_size(monkeypatch):
    import geostitch.image_decode as decode_module
    from PySide6.QtGui import QImage, QImageIOHandler

    class UnknownSizeReader:
        def __init__(self, path):
            pass

        def setAutoTransform(self, enabled):
            pass

        def setDecideFormatFromContent(self, enabled):
            pass

        def size(self):
            return QSize()

        def transformation(self):
            return QImageIOHandler.Transformation.TransformationNone

        def read(self):
            return QImage(1000, 600, QImage.Format.Format_RGB32)

    monkeypatch.setattr(decode_module, "QImageReader", UnknownSizeReader)
    preview, error = read_preview("unknown-format", 100)
    assert error == ""
    assert preview.width() == 100
    assert source_size(preview) == QSize(1000, 600)