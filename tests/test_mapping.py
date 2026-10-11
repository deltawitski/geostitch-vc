import json
from types import SimpleNamespace

import pytest
from pyproj import Transformer

from geostitch.mapping import CRSMapping, ImageMapping, MappingChain, _crs_operation_info
from geostitch.transforms import MatrixTransform
from geostitch.warp import WarpTransform


@pytest.mark.parametrize("crs, pose", [
    ("EPSG:26910", (500000, 5400000, 15, 20)),
    ("EPSG:4326", (10, 60, 0, 0.001)),
    ("EPSG:3857", (1000000, 5000000, -18, 2)),
])
def test_nonlinear_chain_matches_direct_crs_evaluation_across_image(crs, pose):
    image = MatrixTransform.from_similarity(pose, pixel_y_down=True)
    chain = MappingChain((image, CRSMapping(crs, "EPSG:3857")))
    transformer = Transformer.from_crs(crs, "EPSG:3857", always_xy=True)
    inverse = chain.inverse()
    restored = MappingChain.from_dict(json.loads(json.dumps(chain.to_dict())))
    for column in range(0, 5001, 1000):
        for row in range(0, 4001, 1000):
            expected = transformer.transform(*image.map_point(column, row), errcheck=True)
            assert chain.map_point(column, row) == pytest.approx(expected, abs=1e-8)
            assert restored.map_point(column, row) == pytest.approx(expected, abs=1e-8)
            assert inverse.map_point(*expected) == pytest.approx((column, row), abs=1e-5)
    with pytest.raises(ValueError, match="approximation"):
        chain.to_matrix()


def test_matrix_chain_preserves_composition_order_and_can_collapse_exactly():
    first = MatrixTransform.from_affine((2, 0.3, 100, 0.2, 1.5, 200))
    second = MatrixTransform((1, 0, 0, 0, 1, 0, 0.001, 0, 1))
    inner, outer = MappingChain((first,)), MappingChain((second,))
    chain = outer.compose(inner)
    expected = second.map_point(*first.map_point(40, 50))
    assert chain.map_point(40, 50) == pytest.approx(expected)
    assert chain.to_matrix().map_point(40, 50) == pytest.approx(expected)
    assert chain.inverse().map_point(*expected) == pytest.approx((40, 50))


def test_tps_mapping_chain_inverse_and_image_mapping_round_trip():
    controls = ((0, 0), (1, 0), (0, 1), (1, 1))
    targets = tuple((x + 0.1 * x * y, y - 0.05 * x * y) for x, y in controls)
    warp = WarpTransform.fit(MatrixTransform.identity(), controls, targets, smoothing=0.01)
    chain = MappingChain((warp,))
    point = (0.3, 0.4)
    mapped = chain.map_point(*point)
    restored_chain = MappingChain.from_dict(json.loads(json.dumps(chain.to_dict())))

    assert restored_chain.map_point(*point) == pytest.approx(mapped)
    assert chain.inverse().map_point(*mapped) == pytest.approx(point, abs=1e-7)
    with pytest.raises(ValueError, match="nonlinear mapping"):
        chain.to_matrix()

    record = ImageMapping(
        warp, "native_crs", "EPSG:4326", source_size=(1, 1), matrix_crs="EPSG:32610",
    )
    saved_record = record.to_dict()
    assert saved_record["version"] == 3
    restored_record = ImageMapping.from_dict(json.loads(json.dumps(saved_record)))
    assert restored_record == record
    assert restored_record.mapping_chain.map_point(*point) == pytest.approx(
        record.mapping_chain.map_point(*point),
    )


def test_invalid_crs_coordinates_are_reported_not_rendered_as_finite_positions():
    chain = MappingChain((CRSMapping("EPSG:4326", "EPSG:3857"),))
    points = chain.map_points([(0, 0), (0, 100), (float("nan"), 0)])
    assert points == ((0, 0), None, None)
    with pytest.raises(ValueError):
        chain.map_point(0, 100)


def test_crs_operation_info_reports_accuracy_selected_grids_and_unavailable_candidates(monkeypatch):
    available_grid = SimpleNamespace(short_name="selected-grid.tif", url="https://example.test/selected", available=True)
    missing_grid = SimpleNamespace(short_name="required-grid.tif", url="https://example.test/required", available=False)
    selected_operation = SimpleNamespace(grids=[available_grid], operations=[])
    unavailable_operation = SimpleNamespace(grids=[missing_grid], operations=[])
    transformer = SimpleNamespace(
        description="High accuracy grid operation", name="grid operation", accuracy=0.25,
        area_of_use=SimpleNamespace(name="Test region"), operations=[selected_operation],
    )
    group_transformer = SimpleNamespace(
        description="A different operation", accuracy=3.0, area_of_use=None, operations=[],
    )
    group = SimpleNamespace(
        transformers=[group_transformer], unavailable_operations=[unavailable_operation], best_available=False,
    )
    monkeypatch.setattr("pyproj.transformer.TransformerGroup", lambda *args, **kwargs: group)
    monkeypatch.setattr("geostitch.mapping._crs_transformer", lambda *args: transformer)

    info = _crs_operation_info("TEST:SOURCE", "TEST:TARGET")
    summary = info.summary("TEST:SOURCE", "TEST:TARGET")

    assert info.accuracy_meters == 0.25
    assert info.area_of_use == "Test region"
    assert info.selected_grids == ("selected-grid.tif",)
    assert info.unavailable_grids == ("required-grid.tif",)
    assert "More accurate candidate operations require unavailable grids: required-grid.tif" in summary


@pytest.mark.parametrize("source_crs, target_crs, coordinate", [
    ("EPSG:4277", "EPSG:4326", (-2.0, 51.5)),
    ("EPSG:4269", "EPSG:3857", (-123.0, 60.0)),
])
def test_actual_datum_operation_metadata_matches_pyproj_selection(source_crs, target_crs, coordinate):
    mapping = CRSMapping(source_crs, target_crs)
    selected = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    expected = selected.transform(*coordinate, errcheck=True)
    mapped = mapping.map_point(*coordinate)
    info = mapping.operation_info
    summary = info.summary(source_crs, target_crs)

    assert mapped == pytest.approx(expected, abs=1e-8)
    assert info.description == (selected.description or selected.name)
    expected_accuracy = selected.accuracy if selected.accuracy is not None and selected.accuracy >= 0 else None
    assert info.accuracy_meters == expected_accuracy
    expected_area = selected.area_of_use.name if selected.area_of_use is not None else None
    assert info.area_of_use == expected_area
    assert "Reported operation accuracy:" in summary
    if expected_accuracy is None:
        assert "Reported operation accuracy: unknown." in summary
    if expected_area:
        assert f"Area of use: {expected_area}" in summary
    for grid in (*info.selected_grids, *info.unavailable_grids):
        assert grid in summary
    if info.ballpark:
        assert "ballpark transformation" in summary
    elif not info.best_available:
        assert "best available operation could not be selected" in summary
    assert mapping.map_point(*coordinate) == pytest.approx(mapped, abs=1e-8)


@pytest.mark.parametrize("data", [
    {}, {"version": 2, "steps": []}, {"version": 1, "steps": None},
    {"version": 1, "steps": [{"kind": "unknown"}]},
    {"version": 1, "steps": [{"kind": "matrix", "mapping": {}}]},
    {"version": 1, "steps": [{"kind": "crs", "mapping": {"version": 1}}]},
    {"version": 1, "steps": [{}] * 33},
])
def test_invalid_saved_chains_are_rejected(data):
    with pytest.raises(ValueError):
        MappingChain.from_dict(data)


def test_identity_chain_validates_inputs_and_is_immutable():
    steps = [MatrixTransform.identity()]
    chain = MappingChain(steps)
    steps.clear()
    assert len(chain.steps) == 1
    assert MappingChain(()).map_point(10, 20) == (10, 20)
    with pytest.raises(ValueError):
        MappingChain(()).map_point(float("inf"), 0)