import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPixmap
from PySide6.QtCore import QSettings, QPointF, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from pyproj import Transformer
import pytest

from geostitch.app import MainWindow
from geostitch.georeference import GeoreferenceResult
from geostitch.map_view import WorldMapView


def test_world_map_places_images_from_geographic_transforms_north_up():
    app = QApplication.instance() or QApplication([])
    map_view = WorldMapView()
    pixmap = QPixmap(100, 60)
    pixmap.fill(QColor("#387765"))
    map_view.set_world_images([{
        "id": "sheet-a",
        "pixmap": pixmap,
        "mercator_corners": [(500_000.0, 5_400_000.0), (500_200.0, 5_400_050.0), (499_970.0, 5_399_880.0)],
    }])

    item = map_view._image_items["sheet-a"]
    assert item.mapToScene(QPointF(0.0, 0.0)) == QPointF(500_000.0, -5_400_000.0)
    assert item.mapToScene(QPointF(100.0, 0.0)) == QPointF(500_200.0, -5_400_050.0)
    assert item.mapToScene(QPointF(0.0, 60.0)) == QPointF(499_970.0, -5_399_880.0)
    assert not map_view._scene.itemsBoundingRect().isEmpty()
    map_view.close()


def test_world_map_omits_images_without_applied_georeferencing():
    app = QApplication.instance() or QApplication([])
    map_view = WorldMapView()
    pixmap = QPixmap(80, 50)
    pixmap.fill(QColor("white"))
    map_view.set_world_images([
        {
            "id": "georeferenced",
            "pixmap": pixmap,
            "mercator_corners": [(120.0, 35.0), (200.0, 35.0), (120.0, -15.0)],
        },
        {
            "id": "canvas-only",
            "pixmap": pixmap,
            "mercator_corners": None,
        },
    ])

    assert set(map_view._image_items) == {"georeferenced"}
    map_view.set_world_images([{
        "id": "canvas-only",
        "pixmap": pixmap,
        "mercator_corners": None,
    }])
    assert not map_view._image_items
    assert map_view._scene.sceneRect().width() > 40_000_000
    map_view.close()


def test_world_map_has_global_osm_extent_and_emits_clicked_coordinates():
    app = QApplication.instance() or QApplication([])
    map_view = WorldMapView()
    map_view.resize(640, 420)
    map_view.show()
    app.processEvents()
    map_view.fit_world()
    app.processEvents()
    map_view.set_pick_mode(True)
    picked = []
    map_view.coordinate_picked.connect(lambda longitude, latitude: picked.append((longitude, latitude)))
    center = map_view.mapFromScene(QPointF(0.0, 0.0))

    QTest.mouseClick(map_view.viewport(), Qt.MouseButton.LeftButton, pos=center)

    assert map_view.sceneRect().width() > 40_000_000
    assert picked
    assert abs(picked[0][0]) < 0.01
    assert abs(picked[0][1]) < 0.01
    map_view.close()


def test_app_map_dock_updates_when_georeference_result_is_applied(tmp_path):
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "world.png"
    pixmap = QPixmap(40, 30)
    pixmap.fill(QColor("#387765"))
    assert pixmap.save(str(image_path))

    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    assert window.pick_gcp_action.isCheckable() is True
    assert window.pick_gcp_action.isVisible()
    assert not window.pick_gcp_action.isEnabled()
    image = window.project.add_image(image_path)
    window._image_pixmaps[image.id] = pixmap
    result = GeoreferenceResult(
        transforms={image.id: (510_000.0, 5_410_000.0, 3.0, 1.5)},
        observation_residuals={(image.id, "gcp-1"): 0.1},
        iterations=6,
        converged=True,
        crs="EPSG:26910",
    )

    window._apply_georeference_result(result)

    map_item = window.world_map._image_items[image.id]
    to_mercator = Transformer.from_crs("EPSG:26910", "EPSG:3857", always_xy=True)
    top_left = to_mercator.transform(510_000.0, 5_410_000.0)
    assert map_item.mapToScene(QPointF(0.0, 0.0)).x() == pytest.approx(top_left[0])
    assert map_item.mapToScene(QPointF(0.0, 0.0)).y() == pytest.approx(-top_left[1])
    assert window.world_map_dock.windowTitle() == "World Map · EPSG:26910"
    window.close()
