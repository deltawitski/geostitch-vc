import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
import pytest
import rasterio
from pyproj import Transformer

from geostitch.export import export_project_images
from geostitch.mapping import ImageMapping
from geostitch.models import Project
from geostitch.transforms import MatrixTransform


def test_native_affine_geotiff_is_interpreted_correctly_by_rasterio(tmp_path):
    source_path = tmp_path / "source.png"
    source = Image.new("RGB", (4, 3))
    source.putdata([(column * 30, row * 40, 90) for row in range(3) for column in range(4)])
    source.save(source_path)
    project = Project(crs="EPSG:32610")
    image = project.add_image(source_path)
    image.world_x, image.world_y = 500000, 4000000
    image.source_dimensions = (4, 3)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((2, 0.5, 500000, 0.25, -3, 4000000)),
        "native_crs", project.crs, source_size=(4, 3),
    )

    result = export_project_images(project, tmp_path / "native-out", ".tif")

    assert not result.failures
    with rasterio.open(result.written[0]) as dataset:
        assert dataset.crs.to_epsg() == 32610
        assert tuple(dataset.transform)[:6] == pytest.approx((2, 0.5, 500000, 0.25, -3, 4000000))
        assert (dataset.width, dataset.height, dataset.count) == (4, 3, 3)
        assert tuple(dataset.read()[:, 1, 2]) == (60, 40, 90)


def test_cross_crs_warped_geotiff_grid_and_alpha_are_readable_by_rasterio(tmp_path):
    source_path = tmp_path / "geographic.png"
    source = Image.new("RGB", (2, 2))
    source.putdata([(220, 10, 20), (30, 210, 40), (50, 60, 200), (240, 230, 10)])
    source.save(source_path)
    project = Project(crs="EPSG:4326")
    image = project.add_image(source_path)
    image.world_x, image.world_y = 0, 0
    image.source_dimensions = (2, 2)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((1, 0, 0, 0, 1, 0)),
        "native_crs", "EPSG:4326", source_size=(2, 2),
    )
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(0.5, 0.5)
    bounds = (x - 0.5, y - 0.5, x + 0.5, y + 0.5)

    result = export_project_images(
        project, tmp_path / "warped-out", ".tif", output_crs="EPSG:3857",
        resolution=1, resampling="nearest", bounds=bounds,
    )

    assert not result.failures
    with rasterio.open(result.written[0]) as dataset:
        assert dataset.crs.to_epsg() == 3857
        assert tuple(dataset.transform)[:6] == pytest.approx((1, 0, bounds[0], 0, -1, bounds[3]))
        assert (dataset.width, dataset.height, dataset.count) == (1, 1, 4)
        assert tuple(dataset.read()[:, 0, 0]) == (220, 10, 20, 255)
        assert dataset.dataset_mask()[0, 0] == 255


def test_local_matrix_crs_warped_geotiff_is_readable_by_rasterio(tmp_path):
    source_path = tmp_path / "local-frame-source.png"
    source = Image.new("RGB", (2, 1))
    source.putdata([(240, 20, 30), (20, 40, 230)])
    source.save(source_path)
    project = Project(crs="EPSG:4326")
    image = project.add_image(source_path)
    image.world_x, image.world_y = -123, 48.7
    image.source_dimensions = (2, 1)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((10, 0, 500000, 0, -10, 5400000)),
        "native_crs", project.crs, source_size=(2, 1), matrix_crs="EPSG:32610",
    )
    to_geographic = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)
    first = to_geographic.transform(500005, 5399995)
    second = to_geographic.transform(500015, 5399995)
    resolution = second[0] - first[0]
    center_y = (first[1] + second[1]) / 2
    bounds = (
        first[0] - resolution / 2, center_y - resolution / 2,
        second[0] + resolution / 2, center_y + resolution / 2,
    )

    result = export_project_images(
        project, tmp_path / "local-frame-gis", ".tif", output_crs="EPSG:4326",
        resolution=resolution, resampling="nearest", bounds=bounds,
    )

    assert not result.failures
    with rasterio.open(result.written[0]) as dataset:
        assert dataset.crs.to_epsg() == 4326
        assert tuple(dataset.transform)[:6] == pytest.approx(
            (resolution, 0, bounds[0], 0, -resolution, bounds[3]), abs=1e-12,
        )
        assert (dataset.width, dataset.height, dataset.count) == (2, 1, 4)
        assert tuple(dataset.read()[:, 0, 0]) == (240, 20, 30, 255)
        assert tuple(dataset.read()[:, 0, 1]) == (20, 40, 230, 255)


def test_projective_warped_geotiff_grid_is_readable_by_rasterio(tmp_path):
    source_path = tmp_path / "perspective.png"
    source = Image.new("RGB", (2, 2))
    source.putdata([(220, 10, 20), (30, 210, 40), (50, 60, 200), (240, 230, 10)])
    source.save(source_path)
    project = Project(crs="EPSG:32610")
    image = project.add_image(source_path)
    image.world_x, image.world_y = 0, 0
    image.source_dimensions = (2, 2)
    mapping = MatrixTransform((1, 0, 0, 0, -1, 2, 0.01, 0.005, 1))
    image.native_world_mapping = ImageMapping(
        mapping, "native_crs", project.crs, source_size=(2, 2),
    )
    x, y = mapping.map_point(0.5, 0.5)

    result = export_project_images(
        project, tmp_path / "projective-reader", ".tif", resolution=1,
        resampling="nearest", bounds=(x - 0.5, y - 0.5, x + 0.5, y + 0.5),
    )

    assert not result.failures
    with rasterio.open(result.written[0]) as dataset:
        assert dataset.crs.to_epsg() == 32610
        assert (dataset.width, dataset.height, dataset.count) == (1, 1, 4)
        assert tuple(dataset.read()[:, 0, 0]) == (220, 10, 20, 255)


def test_native_affine_png_world_file_is_interpreted_correctly_by_rasterio(tmp_path):
    source_path = tmp_path / "world-source.png"
    Image.new("RGB", (4, 3), (70, 120, 170)).save(source_path)
    project = Project(crs="EPSG:32610")
    image = project.add_image(source_path)
    image.world_x, image.world_y = 500000, 4000000
    image.source_dimensions = (4, 3)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((2, 0.5, 500000, 0.25, -3, 4000000)),
        "native_crs", project.crs, source_size=(4, 3),
    )

    result = export_project_images(project, tmp_path / "native-world", ".png")

    assert not result.failures
    with rasterio.open(result.written[0]) as dataset:
        assert dataset.crs.to_epsg() == 32610
        assert tuple(dataset.transform)[:6] == pytest.approx((2, 0.5, 500000, 0.25, -3, 4000000))
        assert (dataset.width, dataset.height) == (4, 3)


def test_cross_crs_warped_png_world_file_is_interpreted_correctly_by_rasterio(tmp_path):
    source_path = tmp_path / "world-geographic.png"
    source = Image.new("RGB", (2, 2))
    source.putdata([(220, 10, 20), (30, 210, 40), (50, 60, 200), (240, 230, 10)])
    source.save(source_path)
    project = Project(crs="EPSG:4326")
    image = project.add_image(source_path)
    image.world_x, image.world_y = 0, 0
    image.source_dimensions = (2, 2)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((1, 0, 0, 0, 1, 0)),
        "native_crs", "EPSG:4326", source_size=(2, 2),
    )
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(0.5, 0.5)
    bounds = (x - 0.5, y - 0.5, x + 0.5, y + 0.5)

    result = export_project_images(
        project, tmp_path / "world-warp", ".png", output_crs="EPSG:3857",
        resolution=1, resampling="nearest", bounds=bounds,
    )

    assert not result.failures
    with rasterio.open(result.written[0]) as dataset:
        assert dataset.crs.to_epsg() == 3857
        assert tuple(dataset.transform)[:6] == pytest.approx((1, 0, bounds[0], 0, -1, bounds[3]))
        assert (dataset.width, dataset.height, dataset.count) == (1, 1, 4)
        assert tuple(dataset.read()[:, 0, 0]) == (220, 10, 20, 255)


def test_native_affine_jpeg_world_file_is_interpreted_correctly_by_rasterio(tmp_path):
    source_path = tmp_path / "world-source.jpg"
    Image.new("RGB", (32, 24), (90, 130, 170)).save(source_path, quality=100)
    project = Project(crs="EPSG:32610")
    image = project.add_image(source_path)
    image.world_x, image.world_y = 500000, 4000000
    image.source_dimensions = (32, 24)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((2, 0.5, 500000, 0.25, -3, 4000000)),
        "native_crs", project.crs, source_size=(32, 24),
    )

    result = export_project_images(project, tmp_path / "native-jpeg", ".jpg")

    assert not result.failures
    with rasterio.open(result.written[0]) as dataset:
        assert dataset.crs.to_epsg() == 32610
        assert tuple(dataset.transform)[:6] == pytest.approx((2, 0.5, 500000, 0.25, -3, 4000000))
        assert (dataset.width, dataset.height, dataset.count) == (32, 24, 3)
        assert tuple(map(int, dataset.read()[:, 10, 10])) == pytest.approx((90, 130, 170), abs=4)
