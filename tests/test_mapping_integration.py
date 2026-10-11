import pytest
from PySide6.QtCore import QPointF, QSize
from PySide6.QtGui import QTransform
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from geostitch.canvas import ImageCanvas
from geostitch.export import image_affine
from geostitch.image_item import SourceImageItem
from geostitch.models import ImageRecord, Project
from geostitch.project_io import load_project, save_project
from geostitch.transforms import MatrixTransform
from test_canvas_incremental import layer
from test_solver_workflow import solver_window


def test_legacy_image_mappings_preserve_unset_states_and_round_trip(tmp_path):
    project = Project()
    image = project.add_image("image.png")
    assert image.rest_mapping is None
    assert image.aligned_mapping is None
    assert image.world_mapping is None
    image.position_x, image.position_y, image.rotation, image.scale = 100, 200, 23, 1.2
    image.aligned_x, image.aligned_y, image.aligned_rotation, image.aligned_scale = 300, 400, -17, 2
    image.world_x, image.world_y, image.world_rotation, image.world_scale = 500000, 5400000, 12, 0.5
    expected = [mapping.map_point(120, 90) for mapping in (image.rest_mapping, image.aligned_mapping, image.world_mapping)]
    path = tmp_path / "mapping-compatibility.gsp"
    save_project(project, path)
    restored = load_project(path).images[0]
    actual = [mapping.map_point(120, 90) for mapping in (restored.rest_mapping, restored.aligned_mapping, restored.world_mapping)]
    assert actual == expected
    old_mapping = image.world_mapping
    image.world_x += 50
    assert image.world_mapping.map_point(120, 90)[0] == pytest.approx(expected[2][0] + 50)
    assert old_mapping.map_point(120, 90) == expected[2]


@pytest.mark.parametrize("rotation, scale", [(0, 1), (37, 2.3), (-120, 0.2)])
def test_canvas_point_positions_and_inverse_match_domain_mapping(rotation, scale):
    app = QApplication.instance() or QApplication([])
    canvas = ImageCanvas()
    source = (20, 30)
    item_data = dict(layer(points=[("point", *source)]), position_x=120, position_y=-80, rotation=rotation, scale=scale)
    mapping = MatrixTransform.from_similarity((120, -80, rotation, scale))
    try:
        canvas.set_layers([item_data])
        item = canvas._layers["first"]
        marker = canvas._point_items[("first", "point")]
        expected = mapping.map_point(*source)
        assert (marker.pos().x(), marker.pos().y()) == pytest.approx(expected)
        local = item.mapFromScene(QPointF(*expected))
        assert (local.x(), local.y()) == pytest.approx(source)
        canvas.set_layers([item_data])
        assert canvas._layers["first"] is item
        assert canvas._point_items[("first", "point")] is marker
    finally:
        canvas.close()
        delete(canvas)


@pytest.mark.parametrize("mapping", [
    MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50)),
    MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1)),
])
def test_qt_source_item_forward_inverse_agrees_with_general_matrix(mapping):
    app = QApplication.instance() or QApplication([])
    preview = layer()["pixmap"]
    item = SourceImageItem(preview, QSize(1000, 800))
    coefficients = mapping.coefficients
    item.setTransform(QTransform(
        coefficients[0], coefficients[3], coefficients[6],
        coefficients[1], coefficients[4], coefficients[7],
        coefficients[2], coefficients[5], coefficients[8],
    ))
    try:
        for source in [(0, 0), (500, 400), (1000, 800)]:
            expected = mapping.map_point(*source)
            actual = item.mapToScene(QPointF(*source))
            assert (actual.x(), actual.y()) == pytest.approx(expected, abs=1e-9)
            local = item.mapFromScene(actual)
            assert (local.x(), local.y()) == pytest.approx(source, abs=1e-9)
        assert not item.sceneBoundingRect().isEmpty()
    finally:
        delete(item)


def test_export_and_fitted_map_point_share_source_pixel_mapping(solver_window):
    app, window, images, alignment, world, errors = solver_window
    image = images[0]
    image.world_x, image.world_y, image.world_rotation, image.world_scale = 500000, 5400000, 23, 2.3
    coefficients = image_affine(image)
    for column, row in [(0, 0), (20, 30), (1000, 800)]:
        expected = image.world_mapping.map_point(column, row)
        assert window._image_world_point(image, column, row) == expected
        exported = (coefficients[0] * column + coefficients[1] * row + coefficients[2],
                    coefficients[3] * column + coefficients[4] * row + coefficients[5])
        assert exported == expected
    assert alignment.mappings[image.id].map_point(20, 30) == pytest.approx(
        MatrixTransform.from_similarity(alignment.transforms[image.id]).map_point(20, 30)
    )
    assert world.mappings[image.id].map_point(20, 30) == pytest.approx(
        MatrixTransform.from_similarity(world.transforms[image.id], pixel_y_down=True).map_point(20, 30)
    )


def test_export_rejects_missing_geographic_mapping():
    with pytest.raises(ValueError, match="geographic mapping"):
        image_affine(ImageRecord("image", "image.png", "image"))


def test_geographic_application_updates_details_count_and_undo_restores_it(solver_window):
    app, window, images, alignment, world, errors = solver_window
    assert "(0 georeferenced)" in window.project_summary_label.text()
    window._apply_georeference_result(world)
    assert "(2 georeferenced)" in window.project_summary_label.text()
    assert len(window.world_map._image_items) == 2
    window.undo_stack.undo()
    assert "(0 georeferenced)" in window.project_summary_label.text()
    assert len(window.world_map._image_items) == 0
    window.undo_stack.redo()
    assert "(2 georeferenced)" in window.project_summary_label.text()
    assert len(window.world_map._image_items) == 2