import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from geostitch.app import MainWindow
from geostitch.export import export_project_images
from geostitch.georeference import build_local_metric_frame, simulate_georeference
from geostitch.models import GroundControlPoint, Project, SolverSettings
from geostitch.transforms import MatrixTransform, similarity_mapping


def add_projective_controls(project, image, mapping, source_locations):
    points = []
    for index, (pixel_x, pixel_y) in enumerate(source_locations):
        point = GroundControlPoint.create(f"control-{index}")
        point.set_observation(image.id, pixel_x, pixel_y)
        point.set_geographic_coordinates(*mapping.map_point(pixel_x, -pixel_y), project.crs)
        project.add_control_point(point)
        points.append(point)
    return points


def test_projective_georeference_recovers_homography_over_source_domain():
    project = Project(crs="EPSG:32610")
    image = project.add_image("survey.png")
    image.source_dimensions = (1000, 800)
    group_mapping = MatrixTransform((
        1.2, 0.08, 500000, 0.04, 1.1, 4000000, 0.00000002, -0.00000001, 1,
    ))
    source_locations = ((40, 30), (900, 45), (80, 720), (850, 700), (300, 400), (670, 250))
    points = add_projective_controls(project, image, group_mapping, source_locations)
    settings = SolverSettings(transform_model="projective", tolerance=1e-4, max_iterations=2000)

    result = simulate_georeference(project, settings=settings)

    expected = group_mapping.compose(similarity_mapping((0, 0, 0, 1), True))
    actual = result.mappings[image.id]
    assert result.converged
    assert result.maximum_residual < settings.tolerance
    assert result.settings == settings
    assert actual.model == "projective"
    assert result.mapping_records[image.id].source_size == image.source_dimensions
    for pixel in ((0, 0), (500, 400), (1000, 800)):
        assert actual.map_point(*pixel) == pytest.approx(expected.map_point(*pixel), abs=1e-4)


def test_projective_georeference_fits_geographic_controls_in_local_metric_frame():
    from pyproj import Transformer

    project = Project(crs="EPSG:4326")
    image = project.add_image("dateline-survey.png")
    image.source_dimensions = (120, 100)
    seed_frame, _ = build_local_metric_frame(project.crs, [(179.9995, 60.0)])
    to_geographic = Transformer.from_crs(seed_frame.crs, project.crs, always_xy=True)
    expected = MatrixTransform((1.2, 0.04, 0, 0.03, 1.1, 0, 0.0001, -0.00005, 1))
    locations = ((10, 10), (110, 10), (10, 90), (110, 90), (60, 50), (35, 70))
    geographic_targets = {}
    for index, (pixel_x, pixel_y) in enumerate(locations):
        point = GroundControlPoint.create(f"local-control-{index}")
        point.set_observation(image.id, pixel_x, pixel_y)
        local_x, local_y = expected.map_point(pixel_x, -pixel_y)
        geographic_x, geographic_y = to_geographic.transform(local_x, local_y, errcheck=True)
        geographic_targets[point.id] = (geographic_x, geographic_y)
        point.set_geographic_coordinates(geographic_x, geographic_y, project.crs)
        project.add_control_point(point)
    settings = SolverSettings(transform_model="projective", tolerance=0.05, max_iterations=2000)

    result = simulate_georeference(project, settings=settings)

    assert result.converged
    assert result.maximum_residual < settings.tolerance
    assert result.metric_frames[image.id].maximum_control_radius_m < 500
    assert result.metric_frames[image.id].domain_sample_count == 25
    assert result.metric_frames[image.id].maximum_domain_radius_m is not None
    record = result.mapping_records[image.id]
    assert record.mapping.model == "projective"
    assert record.matrix_crs is not None
    for point in project.control_points:
        observation = point.observations[0]
        actual = record.mapping_chain.map_point(observation.x, observation.y)
        assert actual == pytest.approx(geographic_targets[point.id], abs=1e-7)


def test_projective_georeference_refuses_missing_source_dimensions():
    project = Project(crs="EPSG:32610")
    image = project.add_image("survey.png")
    mapping = MatrixTransform((1, 0.02, 500000, 0.01, -1, 4000000, 0.00001, 0.00002, 1))
    add_projective_controls(project, image, mapping, ((10, 10), (900, 20), (20, 700), (900, 700), (450, 350)))
    settings = SolverSettings(transform_model="projective")

    with pytest.raises(ValueError, match="source dimensions"):
        simulate_georeference(project, settings=settings)


def test_projective_georeference_rejects_horizon_crossing_source_domain():
    project = Project(crs="EPSG:32610")
    image = project.add_image("survey.png")
    image.source_dimensions = (1000, 800)
    mapping = MatrixTransform((1, 0, 500000, 0, 1, 4000000, -0.002, 0, 1))
    source_locations = ((20, 20), (400, 20), (20, 700), (400, 700), (200, 360))
    add_projective_controls(project, image, mapping, source_locations)

    with pytest.raises(ValueError, match="horizon crosses"):
        simulate_georeference(project, settings=SolverSettings(transform_model="projective"))


def test_projective_georeference_applies_saves_and_inverse_warp_exports(tmp_path):
    app = QApplication.instance() or QApplication([])
    source_path = tmp_path / "perspective.png"
    source = Image.new("RGB", (120, 100))
    source.putdata([(column, row, 80) for row in range(100) for column in range(120)])
    source.save(source_path)
    project = Project(crs="EPSG:32610")
    image = project.add_image(source_path)
    image.source_dimensions = (120, 100)
    group_mapping = MatrixTransform((
        1.0, 0.02, 500000, 0.01, 1.0, 4000000, 0.00000002, -0.00000001, 1,
    ))
    source_locations = ((10, 10), (110, 10), (10, 90), (110, 90), (60, 50), (35, 70))
    add_projective_controls(project, image, group_mapping, source_locations)
    project.georeference_settings = SolverSettings(
        transform_model="projective", tolerance=1e-4, max_iterations=2000,
    )
    window = MainWindow(QSettings(str(tmp_path / "projective-georef.ini"), QSettings.Format.IniFormat))
    window.project = project
    pixmap = QPixmap(str(source_path))
    pixmap.fill(QColor("#5078a0"))
    window._image_pixmaps = {image.id: pixmap}
    window._image_sizes = {image.id: pixmap.size()}
    solver_errors = []
    window._show_error = solver_errors.append
    window._refresh_all()
    try:
        assert window.run_georeference(), solver_errors
        solved = window.project.images[0]
        assert solved.native_world_mapping.mapping.model == "projective"
        expected = group_mapping.compose(similarity_mapping((0, 0, 0, 1), True))
        assert solved.native_world_mapping.mapping.coefficients == pytest.approx(expected.coefficients, abs=2e-5)
        window.project_path = tmp_path / "projective-georef.gsp"
        assert window.save_current_project()
        restored = Project.from_dict(window.project.to_dict())
        assert restored.images[0].native_world_mapping == solved.native_world_mapping

        center = expected.map_point(0.5, 0.5)
        exported = export_project_images(
            restored, tmp_path / "projective-export", ".tif", output_crs=project.crs,
            resolution=0.1, resampling="nearest",
            bounds=(center[0] - 0.05, center[1] - 0.05, center[0] + 0.05, center[1] + 0.05),
        )
        assert not exported.failures
        with Image.open(exported.written[0]) as raster:
            assert raster.getpixel((0, 0)) == (0, 0, 80, 255)
    finally:
        window.close()
