from PIL import Image
import pytest

from geostitch.export import export_project_images, image_affine, plan_export
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
    assert {path.suffix for path in items[0].outputs} == {".jpg", ".jgw", ".prj", ".png", ".geojson"}


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
