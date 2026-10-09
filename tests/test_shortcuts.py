import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QDockWidget, QMenuBar, QToolBar

from geostitch.app import MainWindow, ShortcutSettingsDialog


def test_custom_shortcuts_persist_between_window_instances(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings_path = tmp_path / "shortcuts.ini"
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)

    first_window = MainWindow(settings)
    assert first_window._apply_shortcuts({"fit_images": QKeySequence("F6")})
    first_window.close()

    restored_settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    second_window = MainWindow(restored_settings)
    assert second_window.shortcut_actions["fit_images"].shortcut().toString() == "F6"
    second_window.close()


def test_conflicting_shortcuts_are_rejected_without_changing_actions(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "shortcuts.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    original_fit = window.shortcut_actions["fit_images"].shortcut()
    original_arrange = window.shortcut_actions["arrange_images"].shortcut()

    applied = window._apply_shortcuts({
        "fit_images": QKeySequence("F6"),
        "arrange_images": QKeySequence("F6"),
    })

    assert not applied
    assert window.shortcut_actions["fit_images"].shortcut() == original_fit
    assert window.shortcut_actions["arrange_images"].shortcut() == original_arrange
    window.close()


def test_shortcut_dialog_restores_default_sequences(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "shortcuts.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    dialog = ShortcutSettingsDialog(window.shortcut_actions, window.SHORTCUTS, window)
    dialog.editors["fit_images"].setKeySequence(QKeySequence("F6"))

    dialog.restore_defaults()

    assert dialog.editors["fit_images"].keySequence().toString() == "Ctrl+Shift+F"
    window.close()


def test_main_commands_are_grouped_in_category_menus(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "shortcuts.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    menus = {
        action.text().replace("&", ""): action.menu()
        for action in window.menuBar().actions()
    }

    assert set(menus) == {"File", "Edit", "Image", "View", "Control Points", "Constraints", "Settings"}
    assert window.shortcut_actions["undo"] in menus["Edit"].actions()
    assert window.shortcut_actions["redo"] in menus["Edit"].actions()
    assert window.shortcut_actions["new_project"] in menus["File"].actions()
    assert window.shortcut_actions["import_images"] in menus["File"].actions()
    assert window.shortcut_actions["arrange_images"] in menus["Image"].actions()
    assert window.shortcut_actions["simulate_alignment"] in menus["Image"].actions()
    assert window.shortcut_actions["georeference"] in menus["Image"].actions()
    assert window.shortcut_actions["fit_images"] in menus["View"].actions()
    assert window.add_points_action in menus["Control Points"].actions()
    assert window.link_points_action in menus["Constraints"].actions()
    assert window.shortcut_settings_action in menus["Settings"].actions()
    window.close()


def test_toolbar_exposes_common_actions_and_menus_have_visible_hover_highlight(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "shortcuts.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    stylesheet = app.styleSheet()

    toolbar_actions = window.findChild(QToolBar, "mainToolbar").actions()
    for key in ("add_points", "link_points", "simulate_alignment", "georeference"):
        assert window.shortcut_actions[key] in toolbar_actions
    assert "QMenuBar::item:selected" in stylesheet
    assert "QMenu::item:selected" in stylesheet
    assert window.shortcut_actions["save_project"].shortcut().toString() == "Ctrl+S"
    assert window.shortcut_actions["save_project"] in window.menuBar().actions()[0].menu().actions()
    window.close()


def test_native_window_frame_and_standard_menu_bar_are_preserved(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "titlebar.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    window.show()
    app.processEvents()

    assert not (window.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert isinstance(window.menuBar(), QMenuBar)
    assert window.menuWidget() is window.menuBar()
    assert window.menuBar().actions()[0].text() == "File"
    assert window.menuBar().actions()[-1].text() == "Settings"
    window.close()


def test_panel_docks_are_movable_hideable_and_listed_in_view_menu(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings)
    window.show()
    app.processEvents()

    assert window.isDockNestingEnabled()

    for dock in (window.image_dock, window.canvas_dock, window.details_dock, window.world_map_dock):
        assert dock.features() & QDockWidget.DockWidgetFeature.DockWidgetMovable
        assert dock.features() & QDockWidget.DockWidgetFeature.DockWidgetFloatable
        assert dock.features() & QDockWidget.DockWidgetFeature.DockWidgetClosable
        view_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "View")
        assert dock.toggleViewAction() in view_menu.actions()

    window.canvas_dock.hide()
    window.close()
    app.processEvents()

    restored_window = MainWindow(QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat))
    restored_window.show()
    app.processEvents()
    assert not restored_window.canvas_dock.isVisible()
    assert restored_window.image_dock.isVisible()
    assert restored_window.details_dock.isVisible()

    restored_window.reset_panel_layout()
    assert restored_window.canvas_dock.isVisible()
    assert restored_window.world_map_dock.isVisible()
    assert restored_window.image_dock.isVisible()
    assert restored_window.details_dock.isVisible()
    restored_window.close()


def test_nested_dock_split_state_restores(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings_path = tmp_path / "nested-layout.ini"
    first_window = MainWindow(QSettings(str(settings_path), QSettings.Format.IniFormat))
    first_window.resizeDocks(
        [first_window.image_dock, first_window.details_dock],
        [180, 360],
        Qt.Orientation.Vertical,
    )
    first_window.splitDockWidget(
        first_window.image_dock,
        first_window.details_dock,
        Qt.Orientation.Vertical,
    )
    first_window.splitDockWidget(
        first_window.image_dock,
        first_window.canvas_dock,
        Qt.Orientation.Horizontal,
    )
    first_window.show()
    app.processEvents()
    image_rect = first_window.image_dock.geometry()
    canvas_rect = first_window.canvas_dock.geometry()
    points_rect = first_window.details_dock.geometry()
    assert canvas_rect.x() > image_rect.x()
    assert canvas_rect.y() == image_rect.y()
    assert points_rect.y() > image_rect.y()
    first_window.close()

    restored = MainWindow(QSettings(str(settings_path), QSettings.Format.IniFormat))
    restored.show()
    app.processEvents()

    assert restored.isDockNestingEnabled()
    assert restored.dockWidgetArea(restored.image_dock) != Qt.DockWidgetArea.NoDockWidgetArea
    assert restored.dockWidgetArea(restored.details_dock) != Qt.DockWidgetArea.NoDockWidgetArea
    image_rect = restored.image_dock.geometry()
    canvas_rect = restored.canvas_dock.geometry()
    points_rect = restored.details_dock.geometry()
    assert canvas_rect.x() > image_rect.x()
    assert canvas_rect.y() == image_rect.y()
    assert points_rect.y() > image_rect.y()
    restored.close()

def test_hints_follow_selection_and_disable_invalid_actions(tmp_path):
    QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "hints.ini"), QSettings.Format.IniFormat))

    assert "Import images" in window.hint_label.text()
    assert not window.shortcut_actions["add_points"].isEnabled()
    assert not window.shortcut_actions["georeference"].isEnabled()

    window.project.add_image(str(tmp_path / "a.png"))
    window._refresh_images()

    assert window.shortcut_actions["add_points"].isEnabled()
    assert "Add points" in window.hint_label.text()
    window.close()
