from PIL import Image

from geostitch.export import export_project_images, image_affine
from geostitch.models import Project


def _georeferenced_project(tmp_path):
    source = tmp_path / "scene.png"
    Image.new("RGB", (8, 4), (10, 20, 30)).save(source)
    project = Project(crs="EPSG:32610")
    image = project.add_image(source)
    image.world_x, image.world_y, image.world_rotation, image.world_scale = 500000.0, 4000000.0, 0.0, 2.0
    project.add_image(tmp_path / "unreferenced.png")
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
