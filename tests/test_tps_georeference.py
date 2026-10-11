import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QSettings, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit
from pyproj import CRS, Transformer

from geostitch.app import MainWindow
from geostitch.export import export_project_images
from geostitch.georeference import _chunk_frames, build_local_metric_frame, simulate_georeference
from geostitch.models import GroundControlPoint, PointConstraint, Project, SolverSettings
from geostitch.project_io import load_project
from geostitch.transforms import MatrixTransform, similarity_mapping
from geostitch.warp import WarpTransform


def local_truth(mapping, x, y):
    base_x, base_y = mapping.map_point(x, y)
    ripple = np.sin(np.pi * x / 120) * np.sin(np.pi * y / 100)
    return base_x + 2.0 * ripple, base_y - 1.5 * ripple


def test_tps_georeference_fits_one_common_warp_for_linked_images_and_holdout():
    project = Project(crs="EPSG:4326")
    first, second, third = [project.add_image(name) for name in ("first.png", "second.png", "checkpoint-only.png")]
    first.source_dimensions = second.source_dimensions = (120, 100)
    third.source_dimensions = (120, 100)
    first.position_x, first.position_y, first.rotation, first.scale = 0.0, 0.0, 0.0, 1.0
    second.position_x, second.position_y, second.rotation, second.scale = 15.0, -10.0, 8.0, 1.05
    frames = {
        first.id: (first.position_x, first.position_y, first.rotation, first.scale),
        second.id: (second.position_x, -second.position_y, -second.rotation, second.scale),
    }
    source_to_group = {
        image_id: similarity_mapping(frame, True) for image_id, frame in frames.items()
    }
    group_to_source = {image_id: mapping.inverse() for image_id, mapping in source_to_group.items()}
    seed_frame, _seed_transform = build_local_metric_frame(project.crs, [(179.5, 60.0)])
    to_geographic = Transformer.from_crs(seed_frame.crs, project.crs, always_xy=True)
    truth = MatrixTransform.from_affine((1.5, 0.1, 0.0, 0.05, 1.2, 0.0))
    group_controls = ((20, 20), (100, 20), (20, 80), (100, 80), (60, 50), (40, 30), (80, 70))
    linked_pairs = []
    for index, (group_x, group_y) in enumerate(group_controls):
        local_x, local_y = local_truth(truth, group_x, group_y)
        target = to_geographic.transform(local_x, local_y, errcheck=True)
        pair = []
        for image in (first, second):
            point = GroundControlPoint.create(f"control-{index}-{image.name}")
            pixel = group_to_source[image.id].map_point(group_x, group_y)
            point.set_observation(image.id, *pixel)
            point.set_geographic_coordinates(*target, project.crs)
            project.add_control_point(point)
            pair.append(point)
        project.add_constraint(PointConstraint.create(pair[0].id, pair[1].id))
        linked_pairs.append((pair, target))

    checkpoint_group = (72.0, 46.0)
    checkpoint_local = local_truth(truth, *checkpoint_group)
    checkpoint_target = to_geographic.transform(*checkpoint_local, errcheck=True)
    first_checkpoint = GroundControlPoint.create("held-out-first")
    first_checkpoint.set_observation(first.id, *group_to_source[first.id].map_point(*checkpoint_group))
    first_checkpoint.set_geographic_coordinates(*checkpoint_target, project.crs)
    first_checkpoint.geographic_checkpoint = True
    project.add_control_point(first_checkpoint)
    third_checkpoint = GroundControlPoint.create("held-out-third")
    third_checkpoint.set_observation(third.id, 10, 10)
    third_checkpoint.set_geographic_coordinates(*checkpoint_target, project.crs)
    third_checkpoint.geographic_checkpoint = True
    project.add_control_point(third_checkpoint)
    project.add_constraint(PointConstraint.create(first_checkpoint.id, third_checkpoint.id))

    chunk_of, solved_frames = _chunk_frames(
        project, excluded_point_ids={first_checkpoint.id, third_checkpoint.id},
    )
    assert chunk_of[first.id] == chunk_of[second.id]
    assert chunk_of[third.id] != chunk_of[first.id]
    for image_id in frames:
        assert solved_frames[image_id] == pytest.approx(frames[image_id])
    settings = SolverSettings(
        transform_model="tps", tolerance=0.1, max_iterations=2000,
        warp_smoothing=0.01, warp_min_relative_area=0.05,
    )

    result = simulate_georeference(project, settings=settings)

    assert result.converged
    assert result.maximum_residual < settings.tolerance
    assert set(result.mapping_records) == {first.id, second.id}
    assert all(isinstance(record.mapping, WarpTransform) for record in result.mapping_records.values())
    for pair, target in linked_pairs:
        for point in pair:
            observation = point.observations[0]
            actual = result.mapping_records[observation.image_id].mapping_chain.map_point(
                observation.x, observation.y,
            )
            assert actual == pytest.approx(target, abs=0.1)

    assert third.id not in result.mapping_records
    assert (first.id, first_checkpoint.id) in result.checkpoint_residuals
    assert (third.id, third_checkpoint.id) in result.unmapped_checkpoints
    assert result.checkpoint_residuals[(first.id, first_checkpoint.id)] < 1.0


def test_tps_georeference_applies_saves_maps_undoes_and_warp_exports(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source_path = tmp_path / "tps-survey.png"
    Image.new("RGB", (100, 80), (35, 95, 155)).save(source_path)
    project = Project(crs="EPSG:4326")
    image = project.add_image(source_path)
    image.source_dimensions = (100, 80)
    image.mask_polygon = [[55, 35], [70, 35], [70, 50], [55, 50]]
    image.mask_enabled = True
    project.georeference_settings = SolverSettings(
        transform_model="tps", tolerance=0.1, max_iterations=1000,
        warp_smoothing=0.01, warp_min_relative_area=0.05,
        warp_max_relative_stretch=2.0,
    )
    metric_frame, _ = build_local_metric_frame(project.crs, [(179.5, 60.0)])
    to_geographic = Transformer.from_crs(metric_frame.crs, project.crs, always_xy=True)
    axis_x, axis_y = np.linspace(0, 100, 5), np.linspace(0, 80, 5)
    checkpoint_point = None
    for row, pixel_y in enumerate(axis_y):
        for column, pixel_x in enumerate(axis_x):
            point = GroundControlPoint.create(f"control-{row}-{column}")
            point.set_observation(image.id, float(pixel_x), float(pixel_y))
            local_x = 2.0 * pixel_x - 0.1 * pixel_y
            local_y = 0.05 * pixel_x - 1.5 * pixel_y
            ripple = np.sin(np.pi * pixel_x / 100) * np.sin(np.pi * pixel_y / 80)
            checkpoint_offset = 5.0 if row == 2 and column == 2 else 0.0
            point.set_geographic_coordinates(
                *to_geographic.transform(local_x + 2 * ripple + checkpoint_offset, local_y - 1.5 * ripple, errcheck=True),
                project.crs,
            )
            project.add_control_point(point)
            if checkpoint_offset:
                checkpoint_point = point

    window = MainWindow(QSettings(str(tmp_path / "tps-workflow.ini"), QSettings.Format.IniFormat))
    window.project = project
    window.project_path = tmp_path / "tps-workflow.gsp"
    pixmap = QPixmap(str(source_path))
    pixmap.fill(QColor("#235f9b"))
    window._image_pixmaps = {image.id: pixmap}
    window._image_sizes = {image.id: QSize(100, 80)}
    window._refresh_all()
    errors = []
    window._show_error = errors.append
    try:
        assert checkpoint_point is not None
        window._select_point(checkpoint_point.id)
        window.geographic_checkpoint_checkbox.setChecked(True)
        window.update_selected_point()
        assert window.project.point_by_id(checkpoint_point.id).geographic_checkpoint
        assert window.run_georeference(), errors
        result = window._georeference_result
        checkpoint_key = (image.id, checkpoint_point.id)
        assert checkpoint_key not in result.observation_residuals
        assert len(result.observation_residuals) == 24
        assert len(result.checkpoint_residuals) == 1
        assert result.checkpoint_residuals[checkpoint_key] == pytest.approx(5.0, abs=0.25)
        review = window._solver_review_lines("georeference", result)
        assert any("Independent checkpoints: 1 mapped" in line for line in review)
        summary = window._solver_summary("georeference", result, "Applied")
        assert "Independent checkpoints: 1 mapped" in summary
        reports = []

        def capture_report(dialog):
            reports.append(dialog.findChild(QPlainTextEdit).toPlainText())
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(QDialog, "exec", capture_report)
        window.show_solver_report("georeference")
        assert len(reports) == 1
        assert "Residuals by image / control point" in reports[0]
        assert "Independent checkpoint errors (excluded from fit)" in reports[0]
        assert f"{checkpoint_point.display_name}" in reports[0]
        solved = window.project.images[0]
        record = solved.native_world_mapping
        assert isinstance(record.mapping, WarpTransform)
        assert record.source_size == (100, 80)
        termination = window._solver_termination_lines("georeference", window._georeference_result)
        assert any("direct regularized TPS coefficient solve" in line for line in termination)
        assert any("not a global injectivity guarantee" in line for line in termination)
        test_pixel = (62.0, 43.0)
        geographic = record.mapping_chain.map_point(*test_pixel)
        expected_map = Transformer.from_crs(
            project.crs, "EPSG:3857", always_xy=True,
        ).transform(*geographic, errcheck=True)
        map_item = window.world_map._image_items[image.id]
        assert map_item.map_source_to_scene(test_pixel) == pytest.approx(
            (expected_map[0], -expected_map[1]), abs=1e-7,
        )
        assert window.save_current_project()
        restored = load_project(window.project_path)
        restored_record = restored.images[0].native_world_mapping
        assert restored.point_by_id(checkpoint_point.id).geographic_checkpoint
        assert restored_record == record
        assert restored_record.mapping_chain.map_point(*test_pixel) == pytest.approx(geographic)

        before_crs_switch = window.project.to_dict()
        window.crs_edit.setText("EPSG:4277")
        window._update_crs()
        assert window.project.crs == "EPSG:4277"
        assert window.project.point_by_id(checkpoint_point.id).geographic_checkpoint
        refit_result = window._georeference_result
        assert (image.id, checkpoint_point.id) in refit_result.checkpoint_residuals
        assert 4.0 < refit_result.checkpoint_residuals[(image.id, checkpoint_point.id)] < 6.0
        after_crs_switch = window.project.to_dict()
        window.undo_stack.undo()
        assert window.project.to_dict() == before_crs_switch
        assert window.project.point_by_id(checkpoint_point.id).geographic_checkpoint
        window.undo_stack.redo()
        assert window.project.to_dict() == after_crs_switch
        assert window.project.point_by_id(checkpoint_point.id).geographic_checkpoint

        window.undo_stack.undo()
        window.undo_stack.undo()
        assert window.project.images[0].native_world_mapping is None
        window.undo_stack.redo()
        window.undo_stack.redo()
        assert window.project.images[0].native_world_mapping == record

        output_xy = Transformer.from_crs(
            "EPSG:4326", "EPSG:32610", always_xy=True,
        ).transform(*geographic, errcheck=True)
        exported = export_project_images(
            restored, tmp_path / "tps-export", ".tif", output_crs="EPSG:32610",
            resolution=10, resampling="nearest",
            bounds=(output_xy[0] - 5, output_xy[1] - 5, output_xy[0] + 5, output_xy[1] + 5),
        )
        assert not exported.failures
        with Image.open(exported.written[0]) as raster:
            assert raster.size == (1, 1)
            assert raster.getpixel((0, 0)) == (35, 95, 155, 255)
        cutline_path = tmp_path / "tps-export" / "tps-survey_georef_cutline.geojson"
        assert cutline_path.is_file()
        assert '"keep": "inside"' in cutline_path.read_text(encoding="utf-8")
        assert errors == []
    finally:
        window.close()
