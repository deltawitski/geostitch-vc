from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QToolButton

from geostitch.app import MainWindow


def make_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "image-controls.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(tmp_path / "image.png")
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("red"))
    window._image_pixmaps[image.id] = pixmap
    window._refresh_all()
    window._on_canvas_image_selected(image.id)
    window.show()
    window.image_view_dock.show()
    window.image_view_dock.raise_()
    app.processEvents()
    return app, window, image.id


def test_image_overlay_controls_draw_mode_apply_clear_and_undo(tmp_path):
    app, window, image_id = make_window(tmp_path)
    try:
        buttons = (window.mask_draw_button, window.mask_clear_button, window.mask_mode_button, window.mask_enable_button)
        assert all(isinstance(button, QToolButton) and not button.icon().isNull() for button in buttons)
        assert all(window.image_viewer.viewport().isAncestorOf(button) for button in buttons)
        assert window.mask_mode_actions["inside"].isChecked()
        assert not window.mask_clear_button.isEnabled()
        assert not window.mask_enable_button.isEnabled()
        QTest.mouseClick(window.mask_draw_button, Qt.MouseButton.LeftButton)
        assert window.image_viewer._drawing
        window._on_mask_polygon_drawn([[10, 10], [50, 10], [50, 50], [10, 50]])
        assert not window.mask_draw_button.isChecked()
        assert not window.image_viewer._drawing
        assert window.mask_enable_button.isChecked()
        window.mask_mode_actions["outside"].trigger()
        assert window.project.images[0].mask_mode == "outside"
        assert window.mask_mode_actions["outside"].isChecked()
        assert not window.mask_mode_actions["inside"].isChecked()
        assert "outside" in window.mask_mode_button.toolTip()
        QTest.mouseClick(window.mask_enable_button, Qt.MouseButton.LeftButton)
        assert not window.project.images[0].mask_enabled
        QTest.mouseClick(window.mask_clear_button, Qt.MouseButton.LeftButton)
        assert window.project.images[0].mask_polygon == []
        assert not window.mask_clear_button.isEnabled()
        window.undo_stack.undo()
        assert window.project.images[0].mask_polygon
        assert window.mask_clear_button.isEnabled()
        assert not any(button.text() == "Fit" for button in window.image_view_dock.findChildren(QPushButton))
        window._clear_image_selection()
        window._refresh_details()
        assert all(not button.isEnabled() for button in buttons)
    finally:
        window.close()


def test_image_overlay_controls_stay_fixed_and_fit_shortcut_remains(tmp_path, monkeypatch):
    app, window, image_id = make_window(tmp_path)
    window.image_view_dock.setFloating(True)
    window.image_view_dock.resize(640, 420)
    app.processEvents()
    try:
        view = window.image_viewer
        controls = view.view_controls
        assert controls.x() == view.viewport().width() - controls.width() - 8
        expected = controls.geometry()
        view.scale(2, 2)
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 30)
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 20)
        assert controls.geometry() == expected
        window.image_view_dock.resize(480, 320)
        app.processEvents()
        assert controls.x() == view.viewport().width() - controls.width() - 8
        assert view.viewport().rect().contains(controls.geometry())
        calls = []
        monkeypatch.setattr(window, "_panel_under_focus_or_cursor", lambda: window.image_view_dock)
        monkeypatch.setattr(view, "fit_image", lambda: calls.append(True))
        window.shortcut_actions["fit_images"].trigger()
        assert calls == [True]
        assert window.shortcut_actions["fit_images"].shortcut().toString() == "Ctrl+Shift+F"
    finally:
        window.close()