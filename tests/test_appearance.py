import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPixmap
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QStyleFactory

from geostitch.appearance import AppearanceDialog
from geostitch.app import MainWindow
from geostitch.models import GroundControlPoint, PointConstraint, VectorLayer
from geostitch.vector import VectorGeometry, prepare_vectors
from geostitch.icons import DEFAULT_KIND_COLORS, KIND_COLORS, kind_icon, set_symbol_colors


def test_symbol_color_changes_invalidate_cached_icons():
    app = QApplication.instance() or QApplication([])
    before = kind_icon("point").pixmap(8, 16).toImage()
    try:
        set_symbol_colors({"point": "#112233", "image": "invalid"})
        assert KIND_COLORS["image"] == DEFAULT_KIND_COLORS["image"]
        after = kind_icon("point").pixmap(8, 16).toImage()
        assert before != after
        assert after.pixelColor(4, 8) == QColor("#112233")
    finally:
        set_symbol_colors(DEFAULT_KIND_COLORS)


def test_appearance_dialog_color_picker_apply_cancel_and_defaults(monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = AppearanceDialog("Fusion", "dark", DEFAULT_KIND_COLORS)
    values = []
    dialog.applied.connect(lambda *args: values.append(args))
    try:
        assert dialog.style_combo.count() == len(QStyleFactory.keys())
        monkeypatch.setattr("geostitch.appearance.QColorDialog.getColor", lambda *args: QColor("#123456"))
        dialog.color_buttons["point"].click()
        assert dialog.colors["point"] == "#123456"
        assert dialog.color_buttons["point"].toolTip() == "#123456"
        assert values == []
        dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).click()
        assert values[0][2]["point"] == "#123456"
        dialog.restore_defaults()
        assert dialog.colors == DEFAULT_KIND_COLORS
        assert values[0][2]["point"] == "#123456"
        dialog.reject()
        assert len(values) == 1
    finally:
        dialog.close()


def test_dark_map_preference_moves_to_appearance_and_respects_apply_cancel(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings_path = str(tmp_path / "dark-map.ini")
    settings = QSettings(settings_path, QSettings.Format.IniFormat)
    settings.setValue("map/dark", True)
    window = MainWindow(settings)
    try:
        assert window.world_map._dark_mode
        assert not hasattr(window, "map_dark_checkbox")

        def cancel(dialog):
            assert dialog.dark_map_checkbox.isChecked()
            dialog.dark_map_checkbox.setChecked(False)
            dialog.reject()
            return dialog.result()

        monkeypatch.setattr(AppearanceDialog, "exec", cancel)
        window.open_appearance_settings()
        assert window.world_map._dark_mode
        assert settings.value("map/dark", type=bool)

        def apply_then_cancel(dialog):
            assert dialog.dark_map_checkbox.isChecked()
            dialog.dark_map_checkbox.setChecked(False)
            dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).click()
            assert not window.world_map._dark_mode
            dialog.dark_map_checkbox.setChecked(True)
            dialog.reject()
            return dialog.result()

        monkeypatch.setattr(AppearanceDialog, "exec", apply_then_cancel)
        window.open_appearance_settings()
        assert not window.world_map._dark_mode
        assert not settings.value("map/dark", type=bool)
    finally:
        window.close()
    restored = MainWindow(QSettings(settings_path, QSettings.Format.IniFormat))
    try:
        assert not restored.world_map._dark_mode

        def accept(dialog):
            dialog.dark_map_checkbox.setChecked(True)
            dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
            return dialog.result()

        monkeypatch.setattr(AppearanceDialog, "exec", accept)
        restored.open_appearance_settings()
        assert restored.world_map._dark_mode
        assert restored._settings.value("map/dark", type=bool)
    finally:
        restored.close()


def test_appearance_restore_defaults_resets_dark_map_without_applying():
    app = QApplication.instance() or QApplication([])
    dialog = AppearanceDialog("Fusion", "dark", DEFAULT_KIND_COLORS, dark_map=True)
    applied = []
    dialog.applied.connect(lambda *args: applied.append(args))
    try:
        assert dialog.dark_map_checkbox.isChecked()
        dialog.restore_defaults()
        assert not dialog.dark_map_checkbox.isChecked()
        assert applied == []
        dialog.apply()
        assert applied[0][3] is False
    finally:
        dialog.close()


def test_settings_menu_and_appearance_persistence(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings_path = str(tmp_path / "appearance.ini")
    window = MainWindow(QSettings(settings_path, QSettings.Format.IniFormat))
    try:
        settings_menu = next(
            action.menu() for action in window.menuBar().actions() if action.text() == "Settings"
        )
        file_menu = window.menuBar().actions()[0].menu()
        assert window.appearance_action in settings_menu.actions()
        assert window.shortcut_settings_action in settings_menu.actions()
        assert all(action.menu() is None or action.text() != "Settings" for action in file_menu.actions())
        colors = dict(DEFAULT_KIND_COLORS, point="#f02040", image="#2030f0")
        window._apply_appearance("Fusion", "native", colors)
        assert app.styleSheet() == ""
        assert app.style().objectName().casefold() == "fusion"
        assert KIND_COLORS["point"] == "#f02040"
        assert window._settings.value("appearance/colors/image") == "#2030f0"
    finally:
        window.close()
    restored = MainWindow(QSettings(settings_path, QSettings.Format.IniFormat))
    try:
        assert restored._color_scheme == "native"
        assert KIND_COLORS["point"] == "#f02040"
        restored._apply_appearance("Fusion", "dark", DEFAULT_KIND_COLORS)
        assert "QMenuBar::item:selected" in app.styleSheet()
    finally:
        restored.close()
        set_symbol_colors(DEFAULT_KIND_COLORS)


@pytest.mark.parametrize("style", QStyleFactory.keys())
def test_available_qt_styles_apply_and_dialog_fits(tmp_path, style):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "styles.ini"), QSettings.Format.IniFormat))
    dialog = AppearanceDialog(style, "native", KIND_COLORS, window)
    dialog.applied.connect(window._apply_appearance)
    try:
        dialog.apply()
        assert app.style().objectName().casefold() == style.casefold()
        assert app.styleSheet() == ""
        dialog.show()
        app.processEvents()
        assert dialog.rect().contains(dialog.buttons.geometry())
        assert all(button.rect().width() >= button.iconSize().width() for button in dialog.color_buttons.values())
        dialog._accept()
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        dialog.close()
        window._apply_appearance("Fusion", "dark", DEFAULT_KIND_COLORS)
        window.close()


def test_appearance_menu_opens_dialog_and_cancel_does_not_save(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "cancel.ini"), QSettings.Format.IniFormat))
    dialogs = []

    def cancel(dialog):
        dialogs.append(dialog.windowTitle())
        dialog.colors["point"] = "#123456"
        dialog.reject()
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(AppearanceDialog, "exec", cancel)
    try:
        window.appearance_action.trigger()
        assert dialogs == ["Appearance"]
        assert KIND_COLORS == DEFAULT_KIND_COLORS
        assert not window._settings.contains("appearance/colors/point")
    finally:
        window.close()


def test_invalid_saved_appearance_falls_back_to_defaults(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "invalid.ini"), QSettings.Format.IniFormat)
    settings.setValue("appearance/style", "UnavailableStyle")
    settings.setValue("appearance/scheme", "UnknownScheme")
    settings.setValue("appearance/colors/point", "invalid")
    window = MainWindow(settings)
    try:
        assert window._qt_style == "Fusion"
        assert window._color_scheme == "dark"
        assert KIND_COLORS == DEFAULT_KIND_COLORS
    finally:
        window.close()


def test_appearance_colors_update_all_views_without_changing_project_or_selection(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "colors.ini"), QSettings.Format.IniFormat))
    images = [window.project.add_image(name) for name in ("first.png", "second.png")]
    points = [GroundControlPoint.create(name) for name in ("first", "second")]
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    for index, (image, point) in enumerate(zip(images, points)):
        image.position_x = index * 150
        window._image_pixmaps[image.id] = pixmap
        point.set_observation(image.id, 20, 30)
        point.geographic_x, point.geographic_y = 0, 0
        window.project.add_control_point(point)
    link = PointConstraint.create(points[0].id, points[1].id)
    window.project.add_constraint(link)
    vector = VectorLayer("vector", "vector.geojson", "vector")
    window.project.vector_layers.append(vector)
    window._vector_cache[window._vector_key(vector)] = prepare_vectors(
        VectorGeometry(points=[(0, 0)]), "EPSG:3857", "EPSG:3857",
    )
    window._refresh_all()
    window.map_points_button.setChecked(True)
    window._on_canvas_area_selected([images[0].id], [(images[1].id, points[1].id)], [0], False)
    snapshot = window.project.to_dict()
    selection = (window._selected_image_ids(), window._selected_point_ids(), window._selected_constraint_ids())
    undo_count = window.undo_stack.count()
    colors = {"image": "#1122ee", "point": "#ee2244", "link": "#ffaa22", "geojson": "#22bbcc"}
    try:
        window._apply_appearance("Fusion", "native", colors)
        assert window.project.to_dict() == snapshot
        assert (window._selected_image_ids(), window._selected_point_ids(), window._selected_constraint_ids()) == selection
        assert window.undo_stack.count() == undo_count
        for row in range(window.image_list.topLevelItemCount()):
            item = window.image_list.topLevelItem(row)
            symbol = "geojson" if item.kind == "vector" else "image"
            assert item.icon(0).pixmap(8, 16).toImage().pixelColor(4, 8) == QColor(colors[symbol])
        assert window.canvas._point_items[(images[0].id, points[0].id)].pen().color() == QColor("#ffffff")
        assert window.canvas._constraint_items[0].pen().color() == QColor(colors["link"])
        assert window.canvas._selection_outlines[0].pen().color() == QColor(colors["image"])
        assert all(marker.brush().color() == QColor(colors["point"]) for marker in window.world_map._point_overlay_items)
        assert all(marker.pen().color() == QColor("#ffffff") for marker in window.world_map._point_overlay_items)
        assert window.world_map._constraint_overlay_items
        assert window.world_map._constraint_overlay_items[-1].pen().color() == QColor(colors["link"])
        assert window.world_map._selected_marker is not None
        assert window.world_map._selected_marker.brush().color() == QColor(colors["point"])
        assert window.world_map._vector_items
        assert all(item.pen().color() == QColor(colors["geojson"]) for item, index in window.world_map._vector_items)
        window._select_point(points[0].id)
        preview_marker = next(item for item in window.image_viewer.scene().items() if item.data(0) == points[0].id)
        assert preview_marker.brush().color() == QColor(colors["point"])
        assert preview_marker.pen().color() == QColor("#ffffff")
    finally:
        window._apply_appearance("Fusion", "dark", DEFAULT_KIND_COLORS)
        window.close()