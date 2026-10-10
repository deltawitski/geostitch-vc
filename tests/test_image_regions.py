import pytest
from PIL import Image
from PySide6.QtCore import QRect, QSize

from geostitch.image_decode import _read_region_with_pillow, read_region, source_size
from geostitch.app import MainWindow


@pytest.mark.parametrize("orientation", range(1, 9))
def test_region_matches_full_oriented_decode(tmp_path, orientation):
    path = tmp_path / "oriented.jpg"
    with Image.new("RGB", (180, 120)) as source:
        source.putdata([(horizontal % 256, vertical % 256, (horizontal + vertical) % 256)
                        for vertical in range(120) for horizontal in range(180)])
        exif = Image.Exif()
        exif[274] = orientation
        source.save(path, exif=exif, quality=100)
    full, error = MainWindow._read_image_data(path)
    assert not error
    region = QRect(17, 23, 41, 37)
    tile, error = read_region(path, region)
    assert not error
    assert source_size(tile) == full.size()
    assert tile.size() == region.size()
    for horizontal, vertical in [(0, 0), (20, 15), (40, 36)]:
        assert tile.pixelColor(horizontal, vertical) == full.pixelColor(horizontal + region.x(), vertical + region.y())
    fallback = _read_region_with_pillow(path, region)
    assert fallback.size() == region.size()
    for horizontal, vertical in [(0, 0), (20, 15), (40, 36)]:
        first = fallback.pixelColor(horizontal, vertical)
        second = full.pixelColor(horizontal + region.x(), vertical + region.y())
        assert max(abs(first.red() - second.red()), abs(first.green() - second.green()), abs(first.blue() - second.blue())) <= 3


def test_region_clamps_to_source_and_preserves_alpha(tmp_path):
    path = tmp_path / "alpha.png"
    with Image.new("RGBA", (100, 80), (10, 20, 30, 120)) as source:
        source.save(path)
    tile, error = read_region(path, QRect(90, 70, 50, 50))
    assert not error
    assert tile.size() == QSize(10, 10)
    assert source_size(tile) == QSize(100, 80)
    assert tile.pixelColor(5, 5).alpha() == 120


def test_region_outside_source_reports_error(tmp_path):
    path = tmp_path / "image.png"
    with Image.new("RGB", (100, 80)) as source:
        source.save(path)
    tile, error = read_region(path, QRect(200, 200, 10, 10))
    assert tile.isNull()
    assert "outside" in error