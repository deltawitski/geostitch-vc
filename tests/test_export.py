import json

from PIL import Image
from pyproj import CRS
import pytest

from geostitch.export import WarpGrid, export_project_images, image_affine, plan_export, plan_warp_grid, warp_native_pixels
from geostitch.mapping import ImageMapping
from geostitch.models import Project
from geostitch.transforms import MatrixTransform


def _georeferenced_project(tmp_path):
    source = tmp_path / "scene.png"
    Image.new("RGB", (8, 4), (10, 20, 30)).save(source)
    project = Project(crs="EPSG:32610")
    image = project.add_image(source)
    image.world_x, image.world_y, image.world_rotation, image.world_scale = 500000.0, 4000000.0, 0.0, 2.0
    project.add_image(tmp_path / "unreferenced.png")
    return project, image


def _local_frame_project(source_path):
    project = Project(crs="EPSG:4326")
    image = project.add_image(source_path)
    image.world_x, image.world_y = -123.0, 48.7
    image.source_dimensions = (2, 1)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((10, 0, 500000, 0, -10, 5400000)),
        "native_crs", project.crs, source_size=(2, 1), matrix_crs="EPSG:32610",
    )
    return project, image


def test_affine_flips_pixel_y_to_north_up():
    project = Project()
    image = project.add_image("a.png")
    image.world_x, image.world_y, image.world_rotation, image.world_scale = 10.0, 20.0, 0.0, 2.0

    assert image_affine(image) == (2.0, 0.0, 10.0, 0.0, -2.0, 20.0)


def test_geotiff_export_writes_transformation_and_epsg(tmp_path):
    project, _image = _georeferenced_project(tmp_path)

    result = export_project_images(project, tmp_path / "out", ".tif")

    assert len(result.written) == 1 and not result.failures
    with Image.open(result.written[0]) as exported:
        assert exported.size == (8, 4)
        transformation = exported.tag_v2[34264]
        assert transformation[0] == 2.0 and transformation[3] == 500000.0
        assert transformation[5] == -2.0 and transformation[7] == 4000000.0
        assert 32610 in exported.tag_v2[34735]


def test_native_affine_mapping_exports_exact_geotiff_transform_in_matching_crs(tmp_path):
    project, image = _georeferenced_project(tmp_path)
    matrix = MatrixTransform.from_affine((2.0, 0.5, 100.0, 0.25, -3.0, 200.0))
    image.native_world_mapping = ImageMapping(matrix, "native_crs", project.crs)

    result = export_project_images(project, tmp_path / "affine-out", ".tif")

    assert not result.failures
    with Image.open(result.written[0]) as exported:
        transform = exported.tag_v2[34264]
        assert (transform[0], transform[1], transform[3]) == (2.0, 0.5, 100.0)
        assert (transform[4], transform[5], transform[7]) == (0.25, -3.0, 200.0)


def test_native_affine_export_refuses_to_flatten_across_crs(tmp_path):
    project, image = _georeferenced_project(tmp_path)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((0.2, 0.03, -123, 0.04, -0.2, 45)), "native_crs", "EPSG:4326",
    )

    with pytest.raises(ValueError, match="output pixel resolution"):
        export_project_images(project, tmp_path / "cross-crs-out", ".tif")


def test_projective_native_mapping_requires_inverse_warp_export(tmp_path):
    project, image = _georeferenced_project(tmp_path)
    image.native_world_mapping = ImageMapping(
        MatrixTransform((1, 0, 0, 0, 1, 0, 0.001, 0, 1)), "native_crs", project.crs,
    )

    with pytest.raises(ValueError, match="inverse-warp export"):
        image_affine(image, project.crs)


def test_warp_grid_uses_explicit_resolution_and_enforces_pixel_budget(tmp_path):
    project, image = _georeferenced_project(tmp_path)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((0.00001, 0, -123.0, 0, -0.00001, 45.0)),
        "native_crs", "EPSG:4326",
    )
    image.source_dimensions = (8, 4)

    grid = plan_warp_grid(image, "EPSG:3857", 10.0, max_pixels=2_000_000)

    assert grid.crs == CRS.from_epsg(3857)
    assert grid.width > 0 and grid.height > 0
    assert grid.width * grid.height <= 2_000_000
    assert grid.affine == (10.0, 0.0, grid.bounds[0], 0.0, -10.0, grid.bounds[3])
    with pytest.raises(ValueError, match="exceeding the configured limit"):
        WarpGrid(grid.crs, grid.bounds, 0.01, max_pixels=10)


def test_local_matrix_crs_produces_compact_native_crs_grid(tmp_path):
    project, image = _local_frame_project(tmp_path / "local-frame.png")

    grid = plan_warp_grid(image, "EPSG:4326", 0.0001, max_pixels=100)

    assert grid.width * grid.height <= 100
    assert -124 < grid.bounds[0] < grid.bounds[2] < -122
    assert 48 < grid.bounds[1] < grid.bounds[3] < 50


def test_local_matrix_crs_warps_pixels_and_cutline_to_native_crs(tmp_path):
    from pyproj import Transformer

    source_path = tmp_path / "local-frame.png"
    source = Image.new("RGB", (2, 1))
    source.putdata([(240, 20, 30), (20, 40, 230)])
    source.save(source_path)
    project, image = _local_frame_project(source_path)
    image.mask_polygon = [[0, 0], [2, 0], [2, 1], [0, 1]]
    image.mask_enabled = True
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
        project, tmp_path / "local-frame-export", ".tif", output_crs="EPSG:4326",
        resolution=resolution, resampling="nearest", bounds=bounds,
    )

    assert not result.failures
    with Image.open(result.written[0]) as exported:
        assert exported.size == (2, 1)
        assert exported.getpixel((0, 0)) == (240, 20, 30, 255)
        assert exported.getpixel((1, 0)) == (20, 40, 230, 255)
        transform = exported.tag_v2[34264]
        assert (transform[0], transform[3], transform[5], transform[7]) == pytest.approx(
            (resolution, bounds[0], -resolution, bounds[3]), abs=1e-12,
        )
    cutline = json.loads(
        (tmp_path / "local-frame-export" / "local-frame_georef_cutline.geojson").read_text()
    )
    ring = cutline["features"][0]["geometry"]["coordinates"][0]
    assert max(point[0] for point in ring) - min(point[0] for point in ring) < 0.001
    assert all(-124 < point[0] < -122 and 48 < point[1] < 50 for point in ring)


def test_warp_grid_keeps_antimeridian_footprint_compact(tmp_path):
    from pyproj import Transformer

    project = Project(crs="EPSG:4326")
    image = project.add_image("date-line.png")
    image.source_dimensions = (2, 1)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((1, 0, 179, 0, 1, 0)), "native_crs", project.crs,
        source_size=(2, 1),
    )
    converter = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    resolution = converter.transform(180, 0)[0] - converter.transform(179, 0)[0]

    grid = plan_warp_grid(image, "EPSG:3857", resolution, max_pixels=20)

    assert grid.width <= 4
    assert grid.bounds[0] > 0
    assert grid.bounds[2] > 20_000_000


def test_antimeridian_warp_samples_both_sides_of_unwrapped_grid(tmp_path):
    from pyproj import Transformer

    project = Project(crs="EPSG:4326")
    image = project.add_image("date-line.png")
    image.source_dimensions = (2, 1)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((1, 0, 179, 0, 1, 0)), "native_crs", project.crs,
        source_size=(2, 1),
    )
    converter = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True, force_over=True)
    first_x, center_y = converter.transform(179.5, 0.5)
    second_x, _ = converter.transform(180.5, 0.5)
    resolution = second_x - first_x
    grid = WarpGrid(
        CRS.from_epsg(3857),
        (first_x - resolution / 2, center_y - resolution / 2,
         second_x + resolution / 2, center_y + resolution / 2),
        resolution, "nearest",
    )
    source = Image.new("RGB", (2, 1))
    source.putdata([(240, 20, 30), (20, 40, 230)])

    warped = warp_native_pixels(source, image, grid)

    assert warped.getpixel((0, 0)) == (240, 20, 30, 255)
    assert warped.getpixel((1, 0)) == (20, 40, 230, 255)


def test_antimeridian_warped_cutline_uses_continuous_output_coordinates():
    from geostitch.export import _warped_cutline_geojson

    project = Project(crs="EPSG:4326")
    image = project.add_image("date-line.png")
    image.source_dimensions = (2, 1)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((1, 0, 179, 0, 1, 0)), "native_crs", project.crs,
        source_size=(2, 1),
    )
    image.mask_polygon = [[0, 0], [2, 0], [2, 1], [0, 1]]

    cutline = json.loads(_warped_cutline_geojson(image, "EPSG:3857"))
    ring = cutline["features"][0]["geometry"]["coordinates"][0]
    x_values = [point[0] for point in ring]

    assert max(x_values) - min(x_values) < 250_000
    assert min(x_values) > 19_000_000


def test_cross_crs_inverse_sampler_maps_grid_pixel_centers_back_to_native_image():
    from pyproj import Transformer

    project = Project(crs="EPSG:4326")
    image = project.add_image("colors.png")
    image.source_dimensions = (2, 2)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((1, 0, 0, 0, 1, 0)), "native_crs", "EPSG:4326",
    )
    longitude, latitude = 0.5, 0.5
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(longitude, latitude)
    grid = WarpGrid(CRS.from_epsg(3857), (x - 0.5, y - 0.5, x + 0.5, y + 0.5), 1.0, "nearest")
    source = Image.new("RGB", (2, 2))
    source.putdata([(220, 10, 20), (30, 210, 40), (50, 60, 200), (240, 230, 10)])

    warped = warp_native_pixels(source, image, grid)

    assert warped.size == (1, 1)
    assert warped.getpixel((0, 0)) == (220, 10, 20, 255)


def test_cross_crs_inverse_sampler_bilinearly_interpolates_source_pixel_centers():
    from pyproj import Transformer

    project = Project(crs="EPSG:4326")
    image = project.add_image("colors.png")
    image.source_dimensions = (2, 2)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((1, 0, 0, 0, 1, 0)), "native_crs", "EPSG:4326",
    )
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(1, 1)
    grid = WarpGrid(CRS.from_epsg(3857), (x - 0.5, y - 0.5, x + 0.5, y + 0.5), 1.0, "bilinear")
    source = Image.new("RGB", (2, 2))
    source.putdata([(0, 0, 0), (100, 100, 100), (200, 200, 200), (255, 255, 255)])

    warped = warp_native_pixels(source, image, grid)

    assert warped.getpixel((0, 0)) == (138, 138, 138, 255)


def test_cross_crs_bilinear_resampling_avoids_transparent_color_fringe():
    project = Project(crs="EPSG:32610")
    image = project.add_image("alpha-edge.png")
    image.source_dimensions = (2, 1)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((1, 0, 0, 0, 1, 0)), "native_crs", project.crs,
        source_size=(2, 1),
    )
    source = Image.new("RGBA", (2, 1))
    source.putdata([(255, 0, 0, 0), (0, 0, 255, 255)])
    grid = WarpGrid(CRS.from_epsg(32610), (0.5, -0.5, 1.5, 0.5), 1.0, "bilinear")

    warped = warp_native_pixels(source, image, grid)

    red, green, blue, alpha = warped.getpixel((0, 0))
    assert alpha == 127 or alpha == 128
    assert red <= 1 and green == 0 and blue >= 254


def test_cross_crs_geotiff_export_writes_requested_grid_and_warped_pixels(tmp_path):
    from pyproj import Transformer

    source_path = tmp_path / "geographic.png"
    source = Image.new("RGB", (2, 2))
    source.putdata([(220, 10, 20), (30, 210, 40), (50, 60, 200), (240, 230, 10)])
    source.save(source_path)
    project = Project(crs="EPSG:4326")
    image = project.add_image(source_path)
    image.world_x, image.world_y = 0, 0
    image.source_dimensions = (2, 2)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((1, 0, 0, 0, 1, 0)), "native_crs", "EPSG:4326", source_size=(2, 2),
    )
    target_x, target_y = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform(0.5, 0.5)
    bounds = (target_x - 0.5, target_y - 0.5, target_x + 0.5, target_y + 0.5)

    result = export_project_images(
        project, tmp_path / "warped-out", ".tif", output_crs="EPSG:3857",
        resolution=1.0, resampling="nearest", bounds=bounds,
    )

    assert not result.failures
    with Image.open(result.written[0]) as exported:
        assert exported.size == (1, 1)
        assert exported.getpixel((0, 0)) == (220, 10, 20, 255)
        transform = exported.tag_v2[34264]
        assert (transform[0], transform[3], transform[5], transform[7]) == pytest.approx(
            (1.0, bounds[0], -1.0, bounds[3]),
        )
        assert 3857 in exported.tag_v2[34735]


def test_projective_native_geotiff_inverse_warps_on_an_explicit_grid(tmp_path):
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
    center_x, center_y = mapping.map_point(0.5, 0.5)

    result = export_project_images(
        project, tmp_path / "projective-warp", ".tif", resolution=1,
        resampling="nearest", bounds=(center_x - 0.5, center_y - 0.5, center_x + 0.5, center_y + 0.5),
    )

    assert not result.failures
    with Image.open(result.written[0]) as exported:
        assert exported.size == (1, 1)
        assert exported.getpixel((0, 0)) == (220, 10, 20, 255)


def test_cross_crs_warp_preserves_mask_alpha_and_reprojects_cutline(tmp_path):
    from pyproj import Transformer

    source_path = tmp_path / "masked-geographic.png"
    source = Image.new("RGB", (2, 2), (80, 120, 160))
    source.save(source_path)
    project = Project(crs="EPSG:4326")
    image = project.add_image(source_path)
    image.world_x, image.world_y = 0, 0
    image.source_dimensions = (2, 2)
    image.native_world_mapping = ImageMapping(
        MatrixTransform.from_affine((1, 0, 0, 0, 1, 0)), "native_crs", "EPSG:4326", source_size=(2, 2),
    )
    image.mask_polygon = [[0, 0], [0.9, 0], [0.9, 2], [0, 2]]
    image.mask_enabled = True
    image.mask_mode = "inside"
    converter = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    left_x, center_y = converter.transform(0.5, 0.5)
    right_x, _ = converter.transform(1.5, 0.5)
    resolution = right_x - left_x
    bounds = (left_x - resolution / 2, center_y - resolution / 2,
              left_x + resolution * 1.5, center_y + resolution / 2)

    result = export_project_images(
        project, tmp_path / "masked-warp", ".tif", output_crs="EPSG:3857",
        resolution=resolution, resampling="nearest", bounds=bounds,
    )

    assert not result.failures
    with Image.open(result.written[0]) as exported:
        assert exported.size == (2, 1)
        assert exported.getpixel((0, 0))[3] == 255
        assert exported.getpixel((1, 0))[3] == 0
    cutline = json.loads((tmp_path / "masked-warp" / "masked-geographic_georef_cutline.geojson").read_text())
    assert cutline["crs_name"] == "EPSG:3857"
    expected_cutline_x = converter.transform(0.9, 1)[0]
    ring = cutline["features"][0]["geometry"]["coordinates"][0]
    assert any(coordinate[0] == pytest.approx(expected_cutline_x) for coordinate in ring)


def test_cross_crs_export_requires_native_crs_provenance(tmp_path):
    project, _image = _georeferenced_project(tmp_path)

    with pytest.raises(ValueError, match="has no native CRS mapping"):
        plan_export(project, tmp_path / "missing-native", ".tif", output_crs="EPSG:3857", resolution=10)


def test_world_file_export_references_pixel_center(tmp_path):
    project, _image = _georeferenced_project(tmp_path)

    result = export_project_images(project, tmp_path / "out", ".png")

    world_file = result.written[0].with_suffix(".pgw")
    values = [float(line) for line in world_file.read_text().split()]
    assert values == [2.0, 0.0, 0.0, -2.0, 500001.0, 3999999.0]
    assert result.written[0].with_suffix(".prj").exists()


def _masked_project(tmp_path, mode="inside"):
    project, image = _georeferenced_project(tmp_path)
    image.mask_polygon = [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0]]
    image.mask_mode = mode
    image.mask_enabled = True
    return project


def test_masked_export_writes_alpha_and_cutline(tmp_path):
    project = _masked_project(tmp_path)

    result = export_project_images(project, tmp_path / "out", ".tif")

    with Image.open(result.written[0]) as exported:
        assert exported.mode == "RGBA"
        assert exported.getpixel((1, 1))[3] == 255
        assert exported.getpixel((6, 1))[3] == 0
    cutline = (tmp_path / "out" / "scene_georef_cutline.geojson").read_text()
    assert "500000.0" in cutline and '"keep": "inside"' in cutline


def test_outside_mode_inverts_alpha(tmp_path):
    project = _masked_project(tmp_path, mode="outside")

    result = export_project_images(project, tmp_path / "out", ".png")

    with Image.open(result.written[0]) as exported:
        assert exported.getpixel((1, 1))[3] == 0
        assert exported.getpixel((6, 1))[3] == 255


def test_jpeg_export_writes_mask_sidecar(tmp_path):
    project = _masked_project(tmp_path)

    result = export_project_images(project, tmp_path / "out", ".jpg")

    assert result.written[0].suffix == ".jpg"
    with Image.open(tmp_path / "out" / "scene_georef_mask.png") as mask:
        assert mask.getpixel((1, 1)) == 255 and mask.getpixel((6, 1)) == 0


def test_export_reports_progress_per_image(tmp_path):
    project, _image = _georeferenced_project(tmp_path)
    calls = []

    export_project_images(project, tmp_path / "out", ".png", progress=lambda done, total, name: calls.append((done, total, name)))

    assert calls == [(0, 1, "scene.png"), (1, 1, "")]


def test_mask_alpha_keeps_colour_under_clipped_pixels(tmp_path):
    project = _masked_project(tmp_path)

    result = export_project_images(project, tmp_path / "out", ".tif")

    with Image.open(result.written[0]) as exported:
        assert exported.getpixel((6, 1)) == (10, 20, 30, 0)


def test_existing_alpha_is_preserved_and_mask_goes_to_sidecar(tmp_path):
    project = _masked_project(tmp_path)
    source = project.images[0].path
    Image.new("RGBA", (8, 4), (10, 20, 30, 128)).save(source)

    result = export_project_images(project, tmp_path / "out", ".tif")

    with Image.open(result.written[0]) as exported:
        assert exported.getpixel((6, 1)) == (10, 20, 30, 128)
    with Image.open(tmp_path / "out" / "scene_georef_mask.png") as mask:
        assert mask.getpixel((1, 1)) == 255 and mask.getpixel((6, 1)) == 0


def test_export_protects_existing_sidecars_without_confirmation(tmp_path):
    project, image = _georeferenced_project(tmp_path)
    destination = tmp_path / "out"
    destination.mkdir()
    sidecar = destination / "scene_georef.pgw"
    sidecar.write_text("Existing metadata", encoding="utf-8")
    result = export_project_images(project, destination, ".png")
    assert not result.written and len(result.failures) == 1
    assert sidecar.read_text() == "Existing metadata"
    assert not (destination / "scene_georef.png").exists()
    result = export_project_images(project, destination, ".png", overwrite=True)
    assert len(result.written) == 1
    assert sidecar.read_text() != "Existing metadata"


def test_export_cancels_between_images_and_keeps_completed_outputs(tmp_path):
    project, image = _georeferenced_project(tmp_path)
    source = tmp_path / "second.png"
    Image.new("RGB", (8, 4)).save(source)
    second = project.add_image(source)
    second.name = image.name
    second.world_x, second.world_y = image.world_x, image.world_y
    stop = []

    def progress(done, total, name):
        if done == 1:
            stop.append(True)

    result = export_project_images(project, tmp_path / "out", ".png", progress, cancelled=lambda: bool(stop))
    assert result.cancelled
    assert len(result.written) == 1
    assert result.written[0].exists()
    assert not (tmp_path / "out" / "scene_georef-2.png").exists()


def test_failed_encoding_leaves_existing_outputs_unchanged(tmp_path, monkeypatch):
    import geostitch.export as export_module

    project, image = _georeferenced_project(tmp_path)
    destination = tmp_path / "out"
    destination.mkdir()
    target = destination / "scene_georef.png"
    target.write_text("Existing image", encoding="utf-8")

    def fail_write(path, *args):
        path.write_text("Partial encoding", encoding="utf-8")
        raise OSError("Encoding failed")

    monkeypatch.setattr(export_module, "write_world_file_image", fail_write)
    result = export_project_images(project, destination, ".png", overwrite=True)
    assert len(result.failures) == 1
    assert target.read_text() == "Existing image"
    assert not list(destination.glob(".geostitch-export-*"))


def test_plan_reserves_duplicate_names_and_mask_sidecars(tmp_path):
    project = _masked_project(tmp_path)
    second = project.add_image(tmp_path / "second.png")
    second.name = project.images[0].name
    second.world_x, second.world_y = 100, 200
    items = plan_export(project, tmp_path / "out", ".jpg")
    assert [item.target.name for item in items] == ["scene_georef.jpg", "scene_georef-2.jpg"]
    assert {path.suffix for path in items[0].outputs} == {".jpg", ".jgw", ".prj", ".xml", ".png", ".geojson"}
    assert any(path.name == "scene_georef.jpg.aux.xml" for path in items[0].outputs)


def test_plan_refuses_overwriting_source_image(tmp_path):
    project, image = _georeferenced_project(tmp_path)
    image.path = str(tmp_path / "scene_georef.png")
    with pytest.raises(ValueError, match="source image"):
        plan_export(project, tmp_path, ".png")


def test_late_sidecar_conflict_rolls_back_new_outputs(tmp_path, monkeypatch):
    import geostitch.export as export_module

    project, image = _georeferenced_project(tmp_path)
    destination = tmp_path / "out"
    original_link = export_module.os.link
    calls = []

    def late_conflict(source, target):
        calls.append(target)
        if len(calls) == 2:
            target.write_text("Created by another process", encoding="utf-8")
        original_link(source, target)

    monkeypatch.setattr(export_module.os, "link", late_conflict)
    result = export_project_images(project, destination, ".png")
    assert not result.written
    assert len(result.failures) == 1
    assert not calls[0].exists()
    assert calls[1].read_text() == "Created by another process"


def test_export_supports_filesystems_without_hardlinks(tmp_path, monkeypatch):
    import geostitch.export as export_module

    project, image = _georeferenced_project(tmp_path)

    def unsupported_link(*args):
        raise OSError("Hardlinks unavailable")

    monkeypatch.setattr(export_module.os, "link", unsupported_link)
    result = export_project_images(project, tmp_path / "out", ".png")
    assert not result.failures and len(result.written) == 1
    with Image.open(result.written[0]) as pixels:
        assert pixels.size == (8, 4)


def test_failed_overwrite_publication_restores_previous_outputs(tmp_path, monkeypatch):
    import geostitch.export as export_module

    project, image = _georeferenced_project(tmp_path)
    destination = tmp_path / "out"
    destination.mkdir()
    target = destination / "scene_georef.png"
    sidecar = destination / "scene_georef.pgw"
    target.write_bytes(b"Previous image")
    sidecar.write_bytes(b"Previous metadata")
    replace = export_module.os.replace
    calls = []

    def fail_second_publish(source, output):
        if source.parent.name.startswith(".geostitch-export-"):
            calls.append(output)
            if len(calls) == 2:
                raise OSError("Failed publishing sidecar")
        replace(source, output)

    monkeypatch.setattr(export_module.os, "replace", fail_second_publish)
    result = export_project_images(project, destination, ".png", overwrite=True)
    assert not result.written and len(result.failures) == 1
    assert target.read_bytes() == b"Previous image"
    assert sidecar.read_bytes() == b"Previous metadata"
    assert not list(destination.glob(".geostitch-export-*"))


def test_failed_rollback_retains_backup_outside_staging(tmp_path, monkeypatch):
    import geostitch.export as export_module

    project, image = _georeferenced_project(tmp_path)
    destination = tmp_path / "out"
    destination.mkdir()
    target = destination / "scene_georef.png"
    target.write_bytes(b"Previous image")
    target.with_suffix(".pgw").write_bytes(b"Previous metadata")
    target.with_suffix(".prj").write_bytes(b"Previous CRS")
    replace = export_module.os.replace
    calls = []

    def fail_publication_and_rollback(source, output):
        calls.append(output)
        if len(calls) > 1:
            raise OSError("Cannot replace output")
        replace(source, output)

    monkeypatch.setattr(export_module.os, "replace", fail_publication_and_rollback)
    result = export_project_images(project, destination, ".png", overwrite=True)
    assert len(result.failures) == 1
    backups = list(destination.glob("*.backup"))
    assert len(backups) == 1
    assert backups[0].read_bytes() in {b"Previous image", b"Previous metadata", b"Previous CRS"}
    assert str(backups[0]) in result.failures[0]


def test_exclusive_copy_failure_cleans_partial_file(tmp_path, monkeypatch):
    import geostitch.export as export_module

    project, image = _georeferenced_project(tmp_path)

    def unsupported_link(*args):
        raise OSError("Hardlinks unavailable")

    def fail_copy(source, output):
        output.write(b"Partial data")
        raise OSError("Disk full")

    monkeypatch.setattr(export_module.os, "link", unsupported_link)
    monkeypatch.setattr(export_module, "copyfileobj", fail_copy)
    result = export_project_images(project, tmp_path / "out", ".png")
    assert len(result.failures) == 1
    assert not (tmp_path / "out" / "scene_georef.png").exists()


def test_rollback_reports_new_output_that_cannot_be_removed(tmp_path, monkeypatch):
    import geostitch.export as export_module
    from pathlib import Path

    project, image = _georeferenced_project(tmp_path)
    link = export_module.os.link
    unlink = Path.unlink
    published = []

    def fail_second_publish(source, target):
        if published:
            raise FileExistsError("Late output conflict")
        link(source, target)
        published.append(target)

    def fail_remove_published(path, *args, **kwargs):
        if path in published:
            raise OSError("File locked")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(export_module.os, "link", fail_second_publish)
    monkeypatch.setattr(Path, "unlink", fail_remove_published)
    result = export_project_images(project, tmp_path / "out", ".png")
    assert not result.written and len(result.failures) == 1
    assert "Incomplete outputs could not be removed" in result.failures[0]
    assert str(published[0]) in result.failures[0]
