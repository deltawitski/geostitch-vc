import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication
import pytest

from geostitch.app import MainWindow
from geostitch.export import export_project_images
from geostitch.georeference import build_local_metric_frame, simulate_georeference, _chunk_frames
from geostitch.models import GroundControlPoint, PointConstraint, Project, SolverSettings
from geostitch.transforms import MatrixTransform, similarity_mapping


def test_affine_georeference_fits_linked_group_and_composes_image_frames():
    project = Project(crs="EPSG:32610")
    first, second = [project.add_image(name) for name in ("first.png", "second.png")]
    first.position_x, first.position_y, first.rotation, first.scale = 0, 0, 0, 1
    second.position_x, second.position_y, second.rotation, second.scale = 15, -30, 10, 1.1
    first.source_dimensions = second.source_dimensions = (1200, 900)
    image_points = {first.id: [], second.id: []}
    layout_locations = ((250, 200), (750, 220), (300, 650), (700, 620), (500, 430), (550, 270))
    for index, _location in enumerate(layout_locations):
        pair = []
        for image, suffix in ((first, "first"), (second, "second")):
            point = GroundControlPoint.create(f"{suffix}-{index}")
            point.set_observation(image.id, 0, 0)
            project.add_control_point(point)
            pair.append(point)
            image_points[image.id].append(point)
        project.add_constraint(PointConstraint.create(pair[0].id, pair[1].id))

    chunk_of, frames = _chunk_frames(project)
    assert chunk_of[first.id] == chunk_of[second.id]
    group_mapping = MatrixTransform.from_affine((2.0, 0.3, 500000, 0.2, 3.0, 4000000))
    for index, layout_location in enumerate(layout_locations):
        target = group_mapping.map_point(*layout_location)
        for image in (first, second):
            point = image_points[image.id][index]
            point.set_geographic_coordinates(*target, project.crs)
            observation = point.observations[0]
            observation.x, observation.y = similarity_mapping(frames[image.id], True).inverse().map_point(*layout_location)

    settings = SolverSettings(transform_model="affine", tolerance=1e-5, max_iterations=1000)
    result = simulate_georeference(project, settings=settings)

    assert result.converged
    assert result.settings == settings
    assert result.maximum_residual < settings.tolerance
    for image in (first, second):
        expected = group_mapping.compose(similarity_mapping(frames[image.id], True))
        actual = result.mappings[image.id]
        assert actual.model == "affine"
        assert actual.coefficients == pytest.approx(expected.coefficients, abs=1e-7)
        assert result.mapping_records[image.id].native_crs == project.crs
        for source in ((0, 0), (600, 450), (1200, 900)):
            assert actual.map_point(*source) == pytest.approx(expected.map_point(*source), abs=1e-6)


def test_affine_georeference_applies_saves_exports_and_undoes(tmp_path):
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "survey.png"
    Image.new("RGB", (200, 160), (20, 80, 140)).save(image_path)
    project = Project(crs="EPSG:32610")
    image = project.add_image(image_path)
    image.source_dimensions = (200, 160)
    group_mapping = MatrixTransform.from_affine((2.0, 0.3, 500000, 0.2, 3.0, 4000000))
    source_to_group = similarity_mapping((0, 0, 0, 1), True)
    expected = group_mapping.compose(source_to_group)
    locations = ((10, 10), (190, 15), (20, 150), (180, 145), (90, 80), (120, 40))
    for index, (pixel_x, pixel_y) in enumerate(locations):
        point = GroundControlPoint.create(f"control-{index}")
        target = group_mapping.map_point(pixel_x, -pixel_y)
        point.set_geographic_coordinates(*target, project.crs)
        point.set_observation(image.id, pixel_x, pixel_y)
        project.add_control_point(point)
    project.georeference_settings = SolverSettings(
        transform_model="affine", tolerance=1e-5, max_iterations=1000,
    )
    window = MainWindow(QSettings(str(tmp_path / "affine-georeference.ini"), QSettings.Format.IniFormat))
    window.project = project
    pixmap = QPixmap(str(image_path))
    pixmap.fill(QColor("#14508c"))
    window._image_pixmaps = {image.id: pixmap}
    window._image_sizes = {image.id: pixmap.size()}
    window.project_path = tmp_path / "affine-georeference.gsp"
    window._refresh_all()
    try:
        before_count = window.undo_stack.count()
        assert window.run_georeference()
        solved = window.project.images[0]
        assert solved.native_world_mapping.mapping.model == "affine"
        assert solved.native_world_mapping.mapping.coefficients == pytest.approx(expected.coefficients, abs=1e-7)
        assert window.undo_stack.count() == before_count + 1
        assert window.save_current_project()

        restored = Project.from_dict(window.project.to_dict())
        assert restored.images[0].native_world_mapping == solved.native_world_mapping
        exported = export_project_images(restored, tmp_path / "affine-export", ".tif")
        assert not exported.failures
        with Image.open(exported.written[0]) as raster:
            transform = raster.tag_v2[34264]
            assert (transform[0], transform[1], transform[3]) == pytest.approx((2.0, -0.3, 500000.0))
            assert (transform[4], transform[5], transform[7]) == pytest.approx((0.2, -3.0, 4000000.0))

        window.undo_stack.undo()
        assert window.project.images[0].native_world_mapping is None
        window.undo_stack.redo()
        assert window.project.images[0].native_world_mapping.mapping.coefficients == pytest.approx(
            expected.coefficients, abs=1e-7,
        )
    finally:
        window.close()


def test_affine_georeference_rejects_collinear_target_controls():
    project = Project(crs="EPSG:32610")
    image = project.add_image("one.png")
    project.georeference_settings = SolverSettings(transform_model="affine")
    for index, (pixel_x, pixel_y) in enumerate(((0, 0), (20, 0), (0, 20), (20, 20))):
        point = GroundControlPoint.create(f"control-{index}")
        point.set_observation(image.id, pixel_x, pixel_y)
        point.set_geographic_coordinates(500000 + 2 * pixel_x, 4000000 + 3 * pixel_x, project.crs)
        project.add_control_point(point)

    with pytest.raises(ValueError, match="non-collinear"):
        simulate_georeference(project, settings=project.georeference_settings)


def test_affine_georeference_fits_geographic_controls_in_local_metric_frame():
    from pyproj import Transformer

    project = Project(crs="EPSG:4326")
    image = project.add_image("one.png")
    image.source_dimensions = (120, 100)
    project.georeference_settings = SolverSettings(transform_model="affine")
    seed_frame, _ = build_local_metric_frame(project.crs, [(179.9995, 60.0)])
    to_geographic = Transformer.from_crs(seed_frame.crs, project.crs, always_xy=True)
    expected = MatrixTransform.from_affine((2.0, 0.1, 0, 0.05, 1.5, 0))
    locations = ((10, 10), (110, 10), (10, 90), (110, 90), (60, 50), (40, 30))
    geographic_targets = {}
    for index, (pixel_x, pixel_y) in enumerate(locations):
        point = GroundControlPoint.create(f"control-{index}")
        point.set_observation(image.id, pixel_x, pixel_y)
        local_x, local_y = expected.map_point(pixel_x, -pixel_y)
        geographic_x, geographic_y = to_geographic.transform(local_x, local_y, errcheck=True)
        geographic_targets[point.id] = (geographic_x, geographic_y)
        point.set_geographic_coordinates(geographic_x, geographic_y, project.crs)
        project.add_control_point(point)

    result = simulate_georeference(project, settings=project.georeference_settings)

    assert result.converged
    assert result.maximum_residual < project.georeference_settings.tolerance
    assert result.metric_frames[image.id].maximum_control_radius_m < 500
    record = result.mapping_records[image.id]
    assert record.native_crs == project.crs
    assert record.matrix_crs is not None
    assert record.mapping.model == "affine"
    for point in project.control_points:
        observation = point.observations[0]
        actual = record.mapping_chain.map_point(observation.x, observation.y)
        assert actual == pytest.approx(geographic_targets[point.id], abs=1e-7)


def test_geographic_affine_reports_distortion_over_mapped_image_domain():
    from pyproj import Transformer

    project = Project(crs="EPSG:4326")
    image = project.add_image("wide-domain.png")
    image.source_dimensions = (10000, 8000)
    project.georeference_settings = SolverSettings(
        transform_model="affine", tolerance=0.1, affine_max_stretch=21.0,
    )
    frame, _to_local = build_local_metric_frame(project.crs, [(179.9, 45.0)])
    to_geographic = Transformer.from_crs(frame.crs, project.crs, always_xy=True)
    controls = ((0, 0), (10, 0), (0, 10), (10, 10), (5, 5))
    for index, (pixel_x, pixel_y) in enumerate(controls):
        point = GroundControlPoint.create(f"clustered-{index}")
        point.set_observation(image.id, pixel_x, pixel_y)
        point.set_geographic_coordinates(
            *to_geographic.transform(pixel_x * 20, -pixel_y * 20, errcheck=True), project.crs,
        )
        project.add_control_point(point)

    result = simulate_georeference(project, settings=project.georeference_settings)

    diagnostics = result.metric_frames[image.id]
    assert diagnostics.domain_sample_count == 25
    assert diagnostics.maximum_domain_radius_m > 100_000
    assert diagnostics.maximum_domain_radius_m > diagnostics.maximum_control_radius_m * 10
    assert diagnostics.sampled_domain_distance_distortion > diagnostics.sampled_distance_distortion
    assert diagnostics.needs_extent_warning
    assert result.converged