import json

from geostitch.models import ImageRecord, Project, VectorLayer
from geostitch.vector import read_geojson, reproject


def test_read_geojson_flattens_features(tmp_path):
    path = tmp_path / "shapes.geojson"
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [1, 2]}},
            {"type": "Feature", "properties": {}, "geometry": {"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]}},
            {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}},
        ],
    }))
    geometry, crs = read_geojson(path)
    assert crs == "EPSG:4326"
    assert geometry.points == [(1.0, 2.0)]
    assert len(geometry.lines) == 2
    assert len(geometry.polygons) == 1
    assert geometry.bounds() == (0.0, 0.0, 3.0, 3.0)


def test_reproject_to_web_mercator(tmp_path):
    path = tmp_path / "point.geojson"
    path.write_text(json.dumps({"type": "Point", "coordinates": [180, 0]}))
    geometry, crs = read_geojson(path)
    x, y = reproject(geometry, crs, "EPSG:3857").points[0]
    assert round(x) == 20037508 and abs(y) < 1e-6


def test_vector_layers_round_trip():
    project = Project()
    project.vector_layers.append(VectorLayer(id="v", path="a.geojson", name="a.geojson", visible=False))
    project.images.append(ImageRecord(id="i", path="i.png", name="i.png", map_visible=False))
    restored = Project.from_dict(project.to_dict())
    assert restored.vector_layers == project.vector_layers
    assert restored.images[0].map_visible is False


def test_prepare_vectors_projects_and_computes_bounds():
    from geostitch.vector import VectorGeometry, prepare_vectors

    geometry = VectorGeometry(
        points=[(180.0, 0.0)],
        lines=[[(0.0, 0.0), (10.0, 0.0)], []],
        polygons=[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]],
    )
    seen = []
    prepared = prepare_vectors(geometry, "EPSG:4326", "EPSG:4326", lambda done, total: seen.append((done, total)))
    assert round(prepared.points[0][0]) == 20037508
    assert prepared.lines.count == 1 and prepared.polygons.count == 1
    assert prepared.polygons.bounds[0][1] < 0 and prepared.polygons.bounds[0][3] <= 0
    assert prepared.bounds[0] == 0.0
    assert seen and seen[-1][0] == seen[-1][1]


def test_world_map_draws_only_visible_simplified_geometry():
    import numpy as np
    from PySide6.QtWidgets import QApplication

    from geostitch.map_view import WorldMapView
    from geostitch.vector import VectorGeometry, prepare_vectors

    QApplication.instance() or QApplication([])
    count = 20_000
    angle = np.linspace(0, 2 * np.pi, count)
    ring = list(zip((5 + np.cos(angle)).tolist(), (5 + np.sin(angle)).tolist()))
    far = [(-100.0 + i * 1e-4, 40.0) for i in range(100)]
    data = prepare_vectors(VectorGeometry(lines=[far], polygons=[ring]), "EPSG:4326", "EPSG:3857")
    view = WorldMapView()
    view.resize(400, 300)
    view.set_world_vectors([{"id": "v", "data": data}])
    view.fit_world()
    view._rebuild_vector_items()
    polygon_items = [item for item, _index in view._vector_items if item.brush().style() != 0]
    assert polygon_items
    assert polygon_items[0].path().elementCount() < 2000


def test_layer_order_interleaves_images_and_vectors_and_round_trips():
    project = Project()
    project.images += [ImageRecord(id="i1", path="1.png", name="1"), ImageRecord(id="i2", path="2.png", name="2")]
    project.vector_layers.append(VectorLayer(id="v", path="a.geojson", name="a"))
    assert project.stacked_layer_ids() == ["i1", "i2", "v"]
    project.layer_order = ["i1", "v", "gone", "i2"]
    assert project.stacked_layer_ids() == ["i1", "v", "i2"]
    assert Project.from_dict(project.to_dict()).stacked_layer_ids() == ["i1", "v", "i2"]
