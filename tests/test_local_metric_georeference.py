import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QSettings, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication
from math import hypot

from pyproj import CRS, Transformer
import pytest

from geostitch.app import MainWindow
from geostitch.export import export_project_images
from geostitch.georeference import (
    _measure_metric_frame_domains, build_local_metric_frame, simulate_georeference,
)
from geostitch.mapping import CRSMapping, MappingChain
from geostitch.map_view import WorldMapView
from geostitch.models import GroundControlPoint, Project, SolverSettings
from geostitch.project_io import load_project, save_project
from geostitch.transforms import MatrixTransform


def test_local_metric_distortion_uses_the_source_datum_ellipsoid():
    source_crs = "EPSG:4277"
    coordinates = [(-2.0, 51.5), (-1.8, 51.7), (-2.2, 51.8)]
    frame, to_local = build_local_metric_frame(source_crs, coordinates)
    to_wgs84 = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    longitudes, latitudes = to_wgs84.transform(
        [point[0] for point in coordinates], [point[1] for point in coordinates], errcheck=True,
    )
    local_x, local_y = to_local.transform(
        [point[0] for point in coordinates], [point[1] for point in coordinates], errcheck=True,
    )
    geod = CRS.from_user_input(source_crs).get_geod()
    expected_distortion = 0.0
    for first_index in range(len(coordinates)):
        for second_index in range(first_index + 1, len(coordinates)):
            _azimuth, _back_azimuth, geodesic_distance = geod.inv(
                longitudes[first_index], latitudes[first_index],
                longitudes[second_index], latitudes[second_index],
            )
            planar_distance = hypot(
                local_x[first_index] - local_x[second_index],
                local_y[first_index] - local_y[second_index],
            )
            expected_distortion = max(
                expected_distortion, abs(planar_distance / geodesic_distance - 1.0),
            )

    assert CRS.from_user_input(frame.crs).geodetic_crs.ellipsoid.semi_major_metre == pytest.approx(
        CRS.from_user_input(source_crs).geodetic_crs.ellipsoid.semi_major_metre,
    )
    assert frame.sampled_distance_distortion == pytest.approx(expected_distortion, abs=1e-12)


def test_mapped_domain_distortion_uses_the_project_datum_ellipsoid():
    source_crs = "EPSG:4277"
    frame, _to_local = build_local_metric_frame(
        source_crs, [(-2.0, 51.5), (-1.8, 51.7), (-2.2, 51.8)],
    )
    mapping = MatrixTransform.from_affine((10000, 0, -100000, 0, 10000, -100000))
    updated = _measure_metric_frame_domains(
        {"group": frame}, {"image": mapping}, {"image": "group"},
        {"image": (20, 20)}, source_crs,
    )["group"]
    local_points = [
        mapping.map_point(20 * column / 4, 20 * row / 4)
        for row in range(5) for column in range(5)
    ]
    local_to_project = Transformer.from_crs(frame.crs, source_crs, always_xy=True)
    project_x, project_y = local_to_project.transform(
        [point[0] for point in local_points], [point[1] for point in local_points], errcheck=True,
    )
    to_wgs84 = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    longitudes, latitudes = to_wgs84.transform(project_x, project_y, errcheck=True)
    geod = CRS.from_user_input(source_crs).get_geod()
    expected_distortion = 0.0
    for first_index in range(len(local_points)):
        for second_index in range(first_index + 1, len(local_points)):
            _azimuth, _back_azimuth, geodesic_distance = geod.inv(
                longitudes[first_index], latitudes[first_index],
                longitudes[second_index], latitudes[second_index],
            )
            planar_distance = hypot(
                local_points[first_index][0] - local_points[second_index][0],
                local_points[first_index][1] - local_points[second_index][1],
            )
            expected_distortion = max(
                expected_distortion, abs(planar_distance / geodesic_distance - 1.0),
            )

    assert updated.sampled_domain_distance_distortion == pytest.approx(expected_distortion, abs=1e-12)


@pytest.mark.parametrize("model", ["affine", "projective"])
@pytest.mark.parametrize("mask_mode", ["inside", "outside"])
@pytest.mark.parametrize("project_crs, center, output_crs", [
    ("EPSG:4326", (179.5, 60.0), "EPSG:3857"),
    ("EPSG:4326", (179.5, 60.0), "EPSG:32660"),
    ("EPSG:4277", (-2.0, 51.5), "EPSG:27700"),
])
def test_geographic_fit_predicts_independent_checkpoint_through_map_and_masked_export(
    tmp_path, monkeypatch, model, mask_mode, project_crs, center, output_crs,
):
    source_path = tmp_path / f"{model}-holdout.png"
    source_image = Image.new("RGB", (120, 100), (35, 95, 155))
    for row in range(92, 99):
        for column in range(112, 117):
            source_image.putpixel((column, row), (220, 30, 70))
    source_image.save(source_path)
    project = Project(crs=project_crs)
    image = project.add_image(source_path)
    image.source_dimensions = (120, 100)
    seed_frame, _seed_transform = build_local_metric_frame(project.crs, [center])
    to_geographic = Transformer.from_crs(seed_frame.crs, project.crs, always_xy=True)
    truth = (
        MatrixTransform.from_affine((2.0, 0.1, 0, 0.05, 1.5, 0))
        if model == "affine"
        else MatrixTransform((1.5, 0.1, 0, 0.05, 1.2, 0, 0.00015, -0.0001, 1))
    )
    training_locations = ((10, 10), (110, 10), (10, 90), (110, 90), (60, 50), (40, 30))
    for index, (pixel_x, pixel_y) in enumerate(training_locations):
        point = GroundControlPoint.create(f"training-{index}")
        point.set_observation(image.id, pixel_x, pixel_y)
        local_x, local_y = truth.map_point(pixel_x, -pixel_y)
        point.set_geographic_coordinates(
            *to_geographic.transform(local_x, local_y, errcheck=True), project.crs,
        )
        project.add_control_point(point)

    checkpoint_pixel = (115, 95)
    expected_local = truth.map_point(checkpoint_pixel[0], -checkpoint_pixel[1])
    expected_geographic = to_geographic.transform(*expected_local, errcheck=True)
    settings = SolverSettings(transform_model=model, tolerance=0.1, max_iterations=2000)
    result = simulate_georeference(project, settings=settings)
    record = result.mapping_records[image.id]
    fitted_geographic = record.mapping_chain.map_point(*checkpoint_pixel)
    checkpoint_error = CRS.from_user_input(project.crs).get_geod().inv(
        *expected_geographic, *fitted_geographic,
    )[2]

    assert result.converged
    assert len(result.observation_residuals) == len(training_locations)
    assert checkpoint_error < 1.0

    image.world_x, image.world_y, image.world_rotation, image.world_scale = result.transforms[image.id]
    image.native_world_mapping = record
    image.mask_polygon = [
        [110, 90], [120, 90], [120, 94], [116, 94],
        [116, 96], [120, 96], [120, 100], [110, 100],
    ]
    image.mask_enabled = True
    image.mask_mode = mask_mode
    project_path = tmp_path / f"{model}-holdout.gsp"
    save_project(project, project_path)
    restored = load_project(project_path)
    restored_record = restored.images[0].native_world_mapping
    assert restored_record == record
    assert restored_record.mapping_chain.map_point(*checkpoint_pixel) == pytest.approx(fitted_geographic)

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    to_mercator = Transformer.from_crs(project.crs, "EPSG:3857", always_xy=True)
    output_x, output_y = to_mercator.transform(*fitted_geographic, errcheck=True)
    view = WorldMapView()
    try:
        map_chain = MappingChain((
            *restored_record.mapping_chain.steps, CRSMapping(project.crs, "EPSG:3857"),
        ))
        view.set_world_images([{
            "id": image.id, "pixmap": QPixmap(str(source_path)),
            "source_size": QSize(120, 100), "mapping_chain": map_chain,
        }])
        raster_position = view._image_items[image.id].map_source_to_scene(checkpoint_pixel)
        view.set_control_points([{
            "id": "held-out-checkpoint", "x": output_x, "y": output_y,
            "name": "Held-out checkpoint", "locked": False, "georeferenced": True,
        }])
        marker = view._point_overlay_items[0]
        assert (marker.scenePos().x(), marker.scenePos().y()) == pytest.approx(raster_position, abs=1e-7)
        assert raster_position == pytest.approx((output_x, -output_y), abs=1e-7)
    finally:
        view.close()

    to_output = Transformer.from_crs(project.crs, output_crs, always_xy=True)
    inside_x, inside_y = to_output.transform(*fitted_geographic, errcheck=True)
    inside_export = export_project_images(
        restored, tmp_path / f"{model}-holdout-inside", ".tif",
        output_crs=output_crs, resolution=1, resampling="nearest",
        bounds=(inside_x - 50, inside_y - 50, inside_x + 50, inside_y + 50),
    )
    assert not inside_export.failures
    with Image.open(inside_export.written[0]) as raster:
        transform = raster.tag_v2[34264]
        column = int((inside_x - transform[3]) / transform[0])
        row = int((transform[7] - inside_y) / -transform[5])
        inside_pixel = raster.getpixel((column, row))
    assert inside_pixel[3] == (255 if mask_mode == "inside" else 0)
    if mask_mode == "inside":
        assert inside_pixel == (220, 30, 70, 255)

    outside_pixel = (119, 95)
    outside_geographic = restored_record.mapping_chain.map_point(*outside_pixel)
    outside_x, outside_y = to_output.transform(*outside_geographic, errcheck=True)
    outside_export = export_project_images(
        restored, tmp_path / f"{model}-holdout-outside", ".tif",
        output_crs=output_crs, resolution=1, resampling="nearest",
        bounds=(outside_x - 50, outside_y - 50, outside_x + 50, outside_y + 50),
    )
    assert not outside_export.failures
    with Image.open(outside_export.written[0]) as raster:
        transform = raster.tag_v2[34264]
        column = int((outside_x - transform[3]) / transform[0])
        row = int((transform[7] - outside_y) / -transform[5])
        outside_exported_pixel = raster.getpixel((column, row))
    assert outside_exported_pixel[3] == (0 if mask_mode == "inside" else 255)
    if mask_mode == "outside":
        assert outside_exported_pixel == (35, 95, 155, 255)


@pytest.mark.parametrize("model", ["affine", "projective"])
def test_geographic_matrix_georeference_applies_saves_and_warp_exports(tmp_path, model):
    app = QApplication.instance() or QApplication([])
    source_path = tmp_path / f"{model}-geographic.png"
    Image.new("RGB", (120, 100), (35, 95, 155)).save(source_path)
    project = Project(crs="EPSG:4326")
    image = project.add_image(source_path)
    image.source_dimensions = (120, 100)
    project.georeference_settings = SolverSettings(
        transform_model=model, tolerance=0.1, max_iterations=2000,
    )
    frame, _to_local = build_local_metric_frame(project.crs, [(179.9995, 60.0)])
    to_geographic = Transformer.from_crs(frame.crs, project.crs, always_xy=True)
    if model == "affine":
        truth = MatrixTransform.from_affine((2.0, 0.1, 0, 0.05, 1.5, 0))
    else:
        truth = MatrixTransform((
            1.5, 0.1, 0, 0.05, 1.2, 0, 0.00015, -0.0001, 1,
        ))
    controls = ((10, 10), (110, 10), (10, 90), (110, 90), (60, 50), (40, 30))
    for index, (pixel_x, pixel_y) in enumerate(controls):
        point = GroundControlPoint.create(f"{model}-control-{index}")
        point.set_observation(image.id, pixel_x, pixel_y)
        local_x, local_y = truth.map_point(pixel_x, -pixel_y)
        point.set_geographic_coordinates(
            *to_geographic.transform(local_x, local_y, errcheck=True), project.crs,
        )
        project.add_control_point(point)

    window = MainWindow(QSettings(str(tmp_path / f"{model}-metric.ini"), QSettings.Format.IniFormat))
    window.project = project
    window.project_path = tmp_path / f"{model}-metric.gsp"
    pixmap = QPixmap(str(source_path))
    pixmap.fill(QColor("#235f9b"))
    window._image_pixmaps = {image.id: pixmap}
    window._image_sizes = {image.id: pixmap.size()}
    errors = []
    window._show_error = errors.append
    window._refresh_all()
    try:
        assert window.run_georeference(), errors
        solved = window.project.images[0]
        record = solved.native_world_mapping
        assert record.mapping.model == model
        assert record.native_crs == project.crs
        assert record.matrix_crs is not None
        assert window._georeference_result.residual_units == "metres in local metric frame"
        for point in window.project.control_points:
            observation = point.observations[0]
            mapped = record.mapping_chain.map_point(observation.x, observation.y)
            assert mapped == pytest.approx((point.geographic_x, point.geographic_y), abs=2e-6)
        assert window.save_current_project()
        restored = Project.from_dict(window.project.to_dict())
        assert restored.images[0].native_world_mapping == record

        longitude, latitude = record.mapping_chain.map_point(60, 50)
        resolution = 1e-5
        bounds = (
            longitude - resolution / 2, latitude - resolution / 2,
            longitude + resolution / 2, latitude + resolution / 2,
        )
        exported = export_project_images(
            restored, tmp_path / f"{model}-metric-export", ".tif",
            output_crs="EPSG:4326", resolution=resolution, resampling="nearest", bounds=bounds,
        )
        assert not exported.failures
        with Image.open(exported.written[0]) as raster:
            assert raster.size in ((1, 1), (2, 2))
            assert all(pixel == (35, 95, 155, 255) for pixel in raster.get_flattened_data())

        window.undo_stack.undo()
        assert window.project.images[0].native_world_mapping is None
        window.undo_stack.redo()
        assert window.project.images[0].native_world_mapping == record
    finally:
        window.close()