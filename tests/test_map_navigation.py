from PySide6.QtCore import QBuffer, QIODevice, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import pytest

from geostitch.map_view import WorldMapView

UPDATE_VISIBLE_TILES = WorldMapView._update_visible_tiles


def scroll(view, delta=120, position=None):
    position = position or QPointF(view.viewport().rect().center())
    event = QWheelEvent(position, position, QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    view.wheelEvent(event)


def test_wheel_zoom_animates_and_preserves_cursor_anchor(map_view):
    app, view = map_view
    view.restore_view_state({"center": [0, 0], "scale": 2 ** 8 / 156543.03392804097})
    position = QPointF(180, 140)
    before = view.mapToScene(position.toPoint())
    scale = view.transform().m11()
    scroll(view, position=position)
    assert view.transform().m11() == pytest.approx(scale)
    view._zoom_animation.setCurrentTime(view.ZOOM_ANIMATION_MS // 2)
    assert scale < view.transform().m11() < scale * 1.2
    view._zoom_animation.setCurrentTime(view.ZOOM_ANIMATION_MS)
    assert view.transform().m11() == pytest.approx(scale * 1.2)
    anchor = view.mapFromScene(before)
    assert abs(anchor.x() - position.x()) <= 2
    assert abs(anchor.y() - position.y()) <= 2


def test_wheel_burst_uses_fractional_input_and_defers_tile_updates(map_view, monkeypatch):
    app, view = map_view
    scale = view.transform().m11()
    updates = []
    monkeypatch.setattr(view, "_update_visible_tiles", lambda: updates.append(True))
    scroll(view, 60)
    scroll(view, 60)
    view._zoom_animation.setCurrentTime(view.ZOOM_ANIMATION_MS // 2)
    assert updates == []
    view._zoom_animation.setCurrentTime(view.ZOOM_ANIMATION_MS)
    assert view.transform().m11() == pytest.approx(scale * 1.2)
    assert updates == [True]


def test_reversing_zoom_during_animation_updates_target(map_view):
    app, view = map_view
    scale = view.transform().m11()
    scroll(view, 120)
    view._zoom_animation.setCurrentTime(view.ZOOM_ANIMATION_MS // 2)
    scroll(view, -120)
    view._zoom_animation.setCurrentTime(view.ZOOM_ANIMATION_MS)
    assert view.transform().m11() == pytest.approx(scale)


def test_zoom_animation_finishes_through_qt_event_loop(map_view):
    app, view = map_view
    scale = view.transform().m11()
    scroll(view)
    QTest.qWait(view.ZOOM_ANIMATION_MS + 80)
    assert view._zoom_target is None
    assert view.transform().m11() == pytest.approx(scale * 1.2)


def test_middle_button_pan_stops_zoom_without_later_movement(map_view):
    app, view = map_view
    scroll(view)
    view._zoom_animation.setCurrentTime(view.ZOOM_ANIMATION_MS // 2)
    position = view.viewport().rect().center()
    QTest.mousePress(view.viewport(), Qt.MouseButton.MiddleButton, pos=position)
    expected = view.view_state()
    view._zoom_animation.setCurrentTime(view.ZOOM_ANIMATION_MS)
    assert view.view_state() == expected
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.MiddleButton, pos=position)


def test_stationary_pan_interrupt_schedules_tile_refresh(map_view):
    app, view = map_view
    scroll(view)
    view._zoom_animation.setCurrentTime(view.ZOOM_ANIMATION_MS // 2)
    QTest.mousePress(view.viewport(), Qt.MouseButton.MiddleButton)
    assert view._tile_update_timer.isActive()
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.MiddleButton)


def test_closing_map_settles_fades_and_only_caches_late_replies(map_view):
    app, view = map_view
    key = (view._zoom, 0, 0)
    view._on_tile_loaded(TileReply(key))
    item = view._tile_items[key]
    assert item.opacity() == 0
    view.close()
    assert item.opacity() == 1
    assert not view._fades
    late_key = (view._zoom, 1, 0)
    view._on_tile_loaded(TileReply(late_key))
    assert late_key in view._tile_cache
    assert late_key not in view._tile_items
    assert not view._fade_timer.isActive()
    view.show()
    assert view._tile_update_timer.isActive()


@pytest.mark.parametrize("operation", ["fit", "restore"])
def test_explicit_view_change_cancels_pending_zoom(map_view, operation):
    app, view = map_view
    scroll(view)
    if operation == "fit":
        view.fit_world()
    else:
        view.restore_view_state({"center": [0, 0], "scale": 0.001})
    expected = view.view_state()
    view._zoom_animation.setCurrentTime(view.ZOOM_ANIMATION_MS)
    assert view.view_state() == expected


def test_cached_tiles_reappear_without_requests_or_fading(map_view, monkeypatch):
    app, view = map_view
    view.restore_view_state({"center": [0, 0], "scale": 2 ** 8 / 156543.03392804097})
    requests = []

    class Reply:
        def setProperty(self, name, value):
            requests.append(value)

    monkeypatch.setattr(view._manager, "get", lambda request: Reply())
    UPDATE_VISIBLE_TILES(view)
    needed = set(view._needed_tiles)
    for key in needed:
        view._on_tile_loaded(TileReply(key))
    for key in list(view._tile_items):
        view._remove_tile(key)
    requests.clear()
    UPDATE_VISIBLE_TILES(view)
    assert requests == []
    assert needed <= view._tile_items.keys()
    assert all(view._tile_items[key].opacity() == 1 for key in needed)


def test_decoded_tile_cache_is_bounded(map_view):
    app, view = map_view
    view.MAX_CACHED_TILES = 2
    keys = [(view._zoom + 1, index, 0) for index in range(3)]
    for key in keys:
        view._on_tile_loaded(TileReply(key))
    assert len(view._tile_cache) == 2
    assert keys[0] not in view._tile_cache

class TileReply:
    class NetworkError:
        NoError = 0

    def __init__(self, key, color="white"):
        self.key = key
        pixmap = QPixmap(256, 256)
        pixmap.fill(QColor(color))
        self.buffer = QBuffer()
        self.buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert pixmap.save(self.buffer, "PNG")

    def property(self, name):
        return self.key

    def readAll(self):
        return self.buffer.data()

    def error(self):
        return self.NetworkError.NoError

    def deleteLater(self):
        pass


@pytest.fixture
def map_view(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorldMapView, "_update_visible_tiles", lambda self: None)
    view = WorldMapView()
    view.resize(640, 420)
    view.show()
    app.processEvents()
    yield app, view
    view.close()


def test_zoom_out_tiles_fade_above_higher_resolution_backdrop(map_view):
    app, view = map_view
    view._zoom = 9
    old_key = (9, 0, 0)
    view._on_tile_loaded(TileReply(old_key, "red"))
    old = view._tile_items[old_key]
    view._fades.clear()
    old.setOpacity(1)
    view._zoom = 8
    key = (8, 0, 0)
    view._needed_tiles = {key}
    view._on_tile_loaded(TileReply(key, "blue"))
    current = view._tile_items[key]
    assert current.zValue() > old.zValue()
    assert current.opacity() == 0
    view._prune_stale_tiles()
    assert old_key in view._tile_items
    view._fades.clear()
    current.setOpacity(1)
    view._prune_stale_tiles()
    assert old_key not in view._tile_items


def test_zoom_out_fade_visually_blends_over_old_tiles(map_view):
    app, view = map_view
    view._zoom = 9
    view._on_tile_loaded(TileReply((9, 256, 256), "red"))
    old = view._tile_items[(9, 256, 256)]
    old.setOpacity(1)
    view._fades.clear()
    view._zoom = 8
    key = (8, 128, 128)
    view._needed_tiles = {key}
    view._on_tile_loaded(TileReply(key, "blue"))
    current = view._tile_items[key]
    source = QRectF(0, 0, view.WORLD_ORIGIN * 2 / (1 << 9), view.WORLD_ORIGIN * 2 / (1 << 9))

    def render_center():
        image = QImage(64, 64, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        view._scene.render(painter, QRectF(0, 0, 64, 64), source)
        painter.end()
        return image.pixelColor(32, 32)

    assert render_center() == QColor("red")
    current.setOpacity(0.5)
    mixed = render_center()
    assert 110 <= mixed.red() <= 145
    assert 110 <= mixed.blue() <= 145
    current.setOpacity(1)
    view._fades.clear()
    view._prune_stale_tiles()
    assert render_center() == QColor("blue")


def test_dark_mode_change_clears_fades_for_removed_tiles(map_view):
    app, view = map_view
    view._on_tile_loaded(TileReply((view._zoom, 0, 0)))
    assert view._fades
    view.set_dark_mode(True)
    assert not view._fades
    view._step_fades()


def test_late_tile_reply_is_cached_for_revisiting_zoom_level(map_view):
    app, view = map_view
    late_key = (view._zoom + 1, 0, 0)
    view._on_tile_loaded(TileReply(late_key))
    assert late_key not in view._tile_items
    assert late_key in view._tile_cache