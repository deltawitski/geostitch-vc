from pathlib import Path
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

from geostitch.image_repair import apply_replacements, find_replacements, missing_images
from geostitch.models import ImageRecord


def test_search_matches_original_filename_and_preserves_ambiguity(tmp_path):
    first = tmp_path / "one" / "map.PNG"
    second = tmp_path / "two" / "map.png"
    for path in (first, second):
        path.parent.mkdir()
        path.touch()
    image = ImageRecord(id="image", name="Custom label", path=str(tmp_path / "missing" / "map.png"))
    matches = find_replacements([image], tmp_path)
    assert set(matches[image.id]) == {first.resolve(), second.resolve()}
    assert image.path == str(tmp_path / "missing" / "map.png")


def test_missing_images_excludes_existing_files(tmp_path):
    existing = tmp_path / "existing.png"
    existing.touch()
    images = [ImageRecord.from_path(existing), ImageRecord.from_path(tmp_path / "missing.png")]
    assert missing_images(images) == [images[1]]


def test_apply_changes_only_path(tmp_path):
    target = tmp_path / "replacement.png"
    target.touch()
    image = ImageRecord(id="image", path="missing.png", name="Original", position_x=125, rotation=20, scale=2,
                        world_x=500, world_y=1000, aligned_x=30, mask_polygon=[[0, 0], [5, 0], [5, 5]])
    before = vars(image).copy()
    assert apply_replacements([image], {image.id: target}) == [image.id]
    assert image.path == str(target.resolve())
    assert {key: value for key, value in vars(image).items() if key != "path"} == {
        key: value for key, value in before.items() if key != "path"
    }


def test_invalid_batch_does_not_partially_change_paths(tmp_path):
    target = tmp_path / "replacement.png"
    target.touch()
    images = [ImageRecord(id="one", path="one.png", name="One"), ImageRecord(id="two", path="two.png", name="Two")]
    with pytest.raises(ValueError, match="does not exist"):
        apply_replacements(images, {"one": target, "two": tmp_path / "absent.png"})
    assert [image.path for image in images] == ["one.png", "two.png"]


def test_unknown_image_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="no longer"):
        apply_replacements([], {"unknown": Path("whatever.png")})


def test_scan_cancellation_leaves_project_unchanged(tmp_path):
    target = tmp_path / "map.png"
    target.touch()
    image = ImageRecord(id="image", name="Map", path="missing/map.png")
    with pytest.raises(InterruptedError):
        find_replacements([image], tmp_path, cancelled=lambda: True)
    assert image.path == "missing/map.png"


def test_repair_undo_redo_preserves_points_and_invalidates_pixels(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from geostitch.app import MainWindow
    from geostitch.models import GroundControlPoint

    application = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "repair.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(tmp_path / "missing.png")
    image.position_x = 200
    image.rotation = 15
    point = GroundControlPoint.create("Control")
    point.set_observation(image.id, 20, 30)
    window.project.add_control_point(point)
    window._mark_project_saved()
    pixels = QImage(60, 40, QImage.Format.Format_RGB32)
    pixels.fill(0)
    replacement = tmp_path / "found.png"
    assert pixels.save(str(replacement))
    queued = []
    monkeypatch.setattr(window._image_loader, "request", lambda key, path: queued.append((key, path)))
    try:
        assert window._repair_image_paths({image.id: replacement})
        assert window._is_project_modified()
        repaired_key = queued[-1][0]
        window._on_image_loaded(repaired_key, pixels, "")
        assert image.id in window._image_pixmaps
        window.undo_stack.undo()
        assert window.project.images[0].path == str(tmp_path / "missing.png")
        assert image.id not in window._image_pixmaps
        assert not window._is_project_modified()
        window._on_image_loaded(repaired_key, pixels, "")
        assert image.id not in window._image_pixmaps
        window.undo_stack.redo()
        restored = window.project.images[0]
        assert restored.path == str(replacement)
        assert restored.id == image.id
        assert restored.position_x == 200
        assert restored.rotation == 15
        observation = window.project.control_points[0].observations[0]
        assert (observation.image_id, observation.x, observation.y) == (image.id, 20, 30)
        assert queued[-1][1] == str(replacement)
    finally:
        window.close()


def test_unreadable_replacement_does_not_change_any_paths(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from geostitch.app import MainWindow

    application = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "invalid.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(tmp_path / "missing.png")
    replacement = tmp_path / "not-an-image.png"
    replacement.write_text("Not image data", encoding="utf-8")
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    try:
        assert not window._repair_image_paths({image.id: replacement})
        assert image.path == str(tmp_path / "missing.png")
        assert window.undo_stack.count() == 0
        assert len(errors) == 1
    finally:
        window.close()


@pytest.mark.parametrize("accepted", [False, True])
def test_repair_dialog_cancel_and_partial_apply(tmp_path, monkeypatch, accepted):
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QDialog
    from geostitch.app import MainWindow
    import geostitch.repair_dialog as repair_module

    application = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "dialog.ini"), QSettings.Format.IniFormat))
    images = [window.project.add_image(tmp_path / name) for name in ("one.png", "two.png")]
    replacement = tmp_path / "found.png"
    pixels = QImage(60, 40, QImage.Format.Format_RGB32)
    pixels.fill(0)
    pixels.save(str(replacement))
    monkeypatch.setattr(window._image_loader, "request", lambda *args: None)

    class RepairPreview:
        def __init__(self, missing, directory, parent):
            assert missing == images

        def exec(self):
            return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

        def replacements(self):
            return {images[0].id: replacement}

    monkeypatch.setattr(repair_module, "ImageRepairDialog", RepairPreview)
    try:
        window.repair_missing_images()
        assert images[0].path == str(replacement if accepted else tmp_path / "one.png")
        assert images[1].path == str(tmp_path / "two.png")
        assert window.undo_stack.count() == int(accepted)
        if accepted:
            assert "1 missing image(s) remain" in window.statusBar().currentMessage()
    finally:
        window.close()