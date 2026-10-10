from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from geostitch.adjustment import AlignmentResult
from geostitch.app import MainWindow


def test_canvas_overlay_buttons_toggle_layout_and_clipping(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "canvas-controls.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(tmp_path / "image.png")
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("red"))
    window._image_pixmaps[image.id] = pixmap
    image.mask_polygon = [[10, 10], [50, 10], [50, 50], [10, 50]]
    image.mask_enabled = True
    window._refresh_all()
    window.show()
    window.canvas_dock.show()
    window.canvas_dock.raise_()
    app.processEvents()
    try:
        layout = window.alignment_toggle_button
        clipping = window.canvas_clip_button
        errors = window.alignment_errors_button
        assert isinstance(errors, QToolButton) and errors.isCheckable()
        assert not errors.isEnabled()
        assert window.canvas.viewport().isAncestorOf(errors)
        assert all(isinstance(button, QToolButton) and button.isCheckable() for button in (layout, clipping))
        assert window.canvas.viewport().isAncestorOf(layout)
        assert window.canvas.viewport().isAncestorOf(clipping)
        assert not layout.isEnabled()
        assert not clipping.isChecked()
        assert window.canvas._layers[image.id].pixmap().toImage().pixelColor(80, 70).alpha() == 255
        clicked = []
        window.canvas.image_clicked.connect(lambda *args: clicked.append(args))
        QTest.mouseClick(clipping, Qt.MouseButton.LeftButton)
        assert clipping.isChecked()
        assert window.canvas._layers[image.id].pixmap().toImage().pixelColor(80, 70).alpha() == 0
        QTest.mouseClick(clipping, Qt.MouseButton.LeftButton)
        assert window.canvas._layers[image.id].pixmap().toImage().pixelColor(80, 70).alpha() == 255
        result = AlignmentResult({image.id: (20, 30, 0, 1)}, {}, 1, True, (image.id,))
        window._apply_alignment_result(result)
        assert layout.isEnabled() and layout.isChecked()
        QTest.mouseClick(layout, Qt.MouseButton.LeftButton)
        assert window.project.canvas_state == "rest"
        assert not layout.isChecked() and not window.canvas._read_only
        window.toggle_alignment_action.trigger()
        assert layout.isChecked() and window.canvas._read_only
        assert window.toggle_alignment_action.shortcut().toString() == "A"
        assert window.toggle_alignment_action not in window.main_toolbar.actions()
        assert clicked == []
    finally:
        window.close()


def test_canvas_controls_stay_top_right_without_overlapping(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "position.ini"), QSettings.Format.IniFormat))
    window.show()
    window.canvas_dock.setFloating(True)
    window.canvas_dock.resize(640, 420)
    window.canvas_dock.show()
    app.processEvents()
    try:
        view = window.canvas
        controls = view.view_controls
        assert controls.x() == view.viewport().width() - controls.width() - 8
        assert controls.y() == 8
        assert not window.alignment_toggle_button.geometry().intersects(window.canvas_clip_button.geometry())
        errors = window.alignment_errors_button
        assert not errors.geometry().intersects(window.alignment_toggle_button.geometry())
        assert not errors.geometry().intersects(window.canvas_clip_button.geometry())
        assert controls.rect().contains(errors.geometry())
        expected = controls.geometry()
        view.scale(2, 2)
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 30)
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 20)
        assert controls.geometry() == expected
        window.canvas_dock.resize(480, 320)
        app.processEvents()
        assert controls.x() == view.viewport().width() - controls.width() - 8
        assert view.viewport().rect().contains(controls.geometry())
    finally:
        window.close()