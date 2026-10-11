import pytest
from PySide6.QtCore import QPointF, QSize

from geostitch.transforms import MatrixTransform
from geostitch.qt_transforms import from_qtransform, to_qtransform
from test_canvas_incremental import canvas, layer


@pytest.mark.parametrize("mapping", [
    MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50)),
    MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1)),
])
def test_canvas_mapping_preserves_markers_inverse_and_item_reuse(canvas, mapping):
    data = dict(layer(points=[("point", 500, 400)]), mapping=mapping, source_size=QSize(1000, 800))
    canvas.set_layers([data])
    item = canvas._layers["first"]
    marker = canvas._point_items[("first", "point")]
    expected = mapping.map_point(500, 400)
    assert (marker.pos().x(), marker.pos().y()) == pytest.approx(expected)
    assert canvas.image_mapping("first") == mapping
    local = item.mapFromScene(QPointF(*expected))
    assert (local.x(), local.y()) == pytest.approx((500, 400))
    canvas.set_layers([data])
    assert canvas._layers["first"] is item
    assert canvas._point_items[("first", "point")] is marker
    assert canvas.image_mapping("first") == mapping
    with pytest.raises(ValueError):
        canvas.image_transform("first")


def test_qt_adapter_preserves_projective_matrix_order():
    mapping = MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1))
    assert from_qtransform(to_qtransform(mapping), mapping.model) == mapping


def test_switching_back_to_legacy_pose_removes_intrinsic_matrix(canvas):
    data = layer(points=[("point", 20, 30)])
    canvas.set_layers([dict(data, mapping=MatrixTransform.from_affine((1, 0.3, 40, 0, 1, 50)))])
    item = canvas._layers["first"]
    canvas.set_layers([dict(data, position_x=100, position_y=200, rotation=90, scale=2)])
    assert canvas._layers["first"] is item
    assert item.transform().isIdentity()
    assert canvas.image_transform("first") == (100, 200, 90, 2)
    assert canvas.image_mapping("first").map_point(20, 30) == pytest.approx((40, 240))


def test_invalid_domain_is_rejected_before_changing_canvas(canvas):
    canvas.set_layers([layer()])
    item = canvas._layers["first"]
    mapping = MatrixTransform((1, 0, 0, 0, 1, 0, -0.002, 0, 1))
    with pytest.raises(ValueError, match="horizon"):
        canvas.set_layers([dict(layer(), mapping=mapping, source_size=QSize(1000, 800))])
    assert canvas._layers["first"] is item
    assert canvas.image_transform("first") == (0, 0, 0, 1)


def test_general_mapping_emits_complete_signal_without_fake_similarity(canvas):
    mapping = MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50))
    canvas.set_layers([dict(layer(), mapping=mapping)])
    matrices, poses = [], []
    canvas.mapping_changed.connect(lambda image_id, value: matrices.append((image_id, value)))
    canvas.transform_changed.connect(lambda *args: poses.append(args))
    canvas._emit_transform("first")
    assert matrices == [("first", mapping)]
    assert poses == []


@pytest.mark.parametrize("kind", ["rotate", "scale"])
@pytest.mark.parametrize("mapping", [
    MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50)),
    MatrixTransform((1.2, 0.15, 10, 0.05, 0.9, 20, 0.0002, -0.0001, 1)),
])
def test_matrix_handles_compose_outer_pose_and_keep_center_fixed(canvas, mapping, kind):
    data = dict(layer(), mapping=mapping, source_size=QSize(1000, 800))
    canvas.set_layers([data])
    canvas.set_selected_images({"first"})
    center = mapping.map_point(500, 400)
    start = QPointF(center[0] + 100, center[1])
    end = QPointF(center[0], center[1] + 100) if kind == "rotate" else QPointF(center[0] + 200, center[1])
    canvas._begin_transform_handle(kind, start)
    canvas._update_transform_handle(end)
    actual = canvas.image_mapping("first")
    pose = MatrixTransform.from_similarity((0, 0, 90 if kind == "rotate" else 0, 2 if kind == "scale" else 1))
    expected = MatrixTransform.from_similarity((*center, 0, 1)).compose(pose).compose(
        MatrixTransform.from_similarity((-center[0], -center[1], 0, 1))
    ).compose(mapping)
    for source in ((0, 0), (500, 400), (1000, 800)):
        assert actual.map_point(*source) == pytest.approx(expected.map_point(*source))
    assert actual.map_point(500, 400) == pytest.approx(center)
    canvas.set_layers([data])
    assert canvas.image_mapping("first") == actual


def test_position_edit_moves_matrix_origin_without_discarding_shear(canvas):
    mapping = MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50))
    canvas.set_layers([dict(layer(), mapping=mapping)])
    canvas.set_image_position("first", 100, 200)
    actual = canvas.image_mapping("first")
    assert actual.map_point(0, 0) == (100, 200)
    assert actual.jacobian(0, 0) == mapping.jacobian(0, 0)
    with pytest.raises(ValueError, match="complete mappings"):
        canvas.set_image_transform("first", rotation=30)


def test_group_drag_refresh_preserves_live_matrix_translations(canvas):
    mapping = MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50))
    data = [dict(layer(), mapping=mapping), dict(layer("second"), mapping=mapping)]
    canvas.set_layers(data)
    canvas.set_selected_images({"first", "second"})
    canvas._dragged_image_id = "first"
    canvas._layers["first"].setPos(30, 40)
    canvas._layers["second"].setPos(30, 40)
    before = {image_id: canvas.image_mapping(image_id) for image_id in ("first", "second")}
    canvas.set_layers(data)
    assert {image_id: canvas.image_mapping(image_id) for image_id in before} == before


def test_matrix_position_edit_respects_lock_and_read_only(canvas):
    mapping = MatrixTransform.from_affine((1.2, 0.3, 40, 0.2, 0.8, 50))
    canvas.set_layers([dict(layer(), mapping=mapping, rest_locked=True)])
    canvas.set_image_position("first", 100, 200)
    assert canvas.image_mapping("first") == mapping
    canvas.set_layers([dict(layer(), mapping=mapping)])
    canvas.set_read_only(True)
    canvas.set_image_position("first", 100, 200)
    assert canvas.image_mapping("first") == mapping


def test_matrix_point_drag_uses_validated_inverse_and_ignores_scene_horizon(canvas):
    mapping = MatrixTransform((1, 0, 0, 0, 1, 0, 0.002, 0, 1))
    canvas.set_layers([dict(layer(points=[("point", 20, 30)]), mapping=mapping)])
    changed = []
    canvas.point_observation_changed.connect(lambda *args: changed.append(args))
    canvas._move_point_marker(("first", "point"), QPointF(*mapping.map_point(40, 50)))
    assert canvas._points[("first", "point")] == pytest.approx((40, 50))
    assert changed[-1][2:] == pytest.approx((40, 50))
    canvas._move_point_marker(("first", "point"), QPointF(500, 0))
    assert len(changed) == 1
    assert canvas._points[("first", "point")] == pytest.approx((40, 50))


def test_similarity_matrix_scalar_edit_keeps_absolute_pose_semantics(canvas):
    mapping = MatrixTransform.from_similarity((100, 200, 30, 2))
    canvas.set_layers([dict(layer(), mapping=mapping)])
    canvas.set_image_transform("first", rotation=45, scale=3)
    assert canvas.image_transform("first") == (100, 200, 45, 3)
    expected = MatrixTransform.from_similarity((100, 200, 45, 3))
    assert canvas.image_mapping("first").coefficients == pytest.approx(expected.coefficients)