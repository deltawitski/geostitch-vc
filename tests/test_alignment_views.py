import json
from unittest.mock import patch

import pytest

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPixmap
from PySide6.QtWidgets import QApplication

from geostitch.app import MainWindow
from geostitch.alignment_feedback import AlignmentFeedback
from geostitch.adjustment import simulate_alignment
from geostitch.alignment_feedback import residual_color
from geostitch.icons import KIND_COLORS
from geostitch.layers import LayerTree
from test_alignment_feedback import make_feedback_project


@pytest.fixture
def solved_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    project, images, points, links, result = make_feedback_project()
    window.project = project
    pixmap = QPixmap(200, 150)
    pixmap.fill(QColor("white"))
    window._image_pixmaps = {image.id: pixmap for image in images}
    window._refresh_all()
    window._apply_alignment_result(result, tolerance=0.5)
    yield app, window, images, points, links, result
    window.close()


def test_layers_show_image_point_and_link_errors(solved_window):
    app, window, images, points, links, result = solved_window
    column = LayerTree.ERROR_COLUMN
    top = window.image_list.item(0)
    point = top.child(0)
    link = point.child(0)
    assert top.text(column) == "0.875"
    assert point.text(column) == "1.200"
    assert link.text(column) == "0.300"
    assert "RMS" in top.toolTip(column)
    assert "scene units" in point.toolTip(column)
    assert point.foreground(column).color() == QColor("#d44f54")
    assert link.foreground(column).color() == QColor("#2f9e68")
    assert window.image_list.item(2).text(column) == "--"
    window.image_list.setCurrentItem(link, column)
    assert window._selected_constraint_ids() == [links[0].id]
    assert window.canvas._selected_constraint_indices == {0}


def test_feedback_refresh_checks_freshness_once(solved_window):
    app, window, images, points, links, result = solved_window
    original = AlignmentFeedback.is_current
    with patch.object(AlignmentFeedback, "is_current", autospec=True, side_effect=original) as checks:
        window._refresh_alignment_feedback()
        assert checks.call_count == 1
    row = window.image_list.item(0).child(0).child(0)
    assert "Current" in row.toolTip(0)


def test_full_refresh_rebuilds_canvas_once(solved_window):
    app, window, images, points, links, result = solved_window
    with patch.object(window.canvas, "set_layers", wraps=window.canvas.set_layers) as rebuilds:
        window._refresh_all()
        assert rebuilds.call_count == 1
    assert len(window.canvas._constraint_items) == len(links)


def test_point_refresh_does_not_repeat_constraint_assignment(solved_window):
    app, window, images, points, links, result = solved_window
    with patch.object(window.canvas, "set_constraints", wraps=window.canvas.set_constraints) as resets:
        window._refresh_points(points[0].id)
        assert resets.call_count == 0
    assert window._selected_point_id() == points[0].id


def test_layers_mark_changed_observations_outdated_and_recover_on_undo(solved_window):
    app, window, images, points, links, result = solved_window
    window._begin_point_observation_drag(images[0].id, points[0].id)
    window._on_point_observation_changed(images[0].id, points[0].id, 21, 30)
    window._finish_project_edit()
    window._refresh_details()
    row = window.image_list.item(0).child(0)
    assert "Outdated" in row.toolTip(LayerTree.ERROR_COLUMN)
    assert row.foreground(LayerTree.ERROR_COLUMN).color() == QColor("#7a848a")
    window.undo_stack.undo()
    row = window.image_list.item(0).child(0)
    assert "Outdated" not in row.toolTip(LayerTree.ERROR_COLUMN)
    assert row.foreground(LayerTree.ERROR_COLUMN).color() == QColor("#d44f54")


def test_canvas_residual_colors_preserve_selection_and_toggle(solved_window):
    app, window, images, points, links, result = solved_window
    assert window.canvas._constraint_items[0].pen().color() == QColor("#2f9e68")
    assert window.canvas._constraint_items[1].pen().color() == QColor("#d44f54")
    window._select_constraint(links[1].id)
    selected = window.canvas._constraint_items[1]
    assert selected.pen().color() == QColor("#d44f54")
    assert selected.pen().widthF() == 4
    assert selected.pen().style() == Qt.PenStyle.SolidLine
    assert "Link residual: 1.200 scene units" in window.alignment_error_label.text()
    assert "Current" in window.alignment_error_label.text()
    assert window.canvas._point_items[(images[0].id, points[0].id)].pen().widthF() == 3
    window.alignment_errors_checkbox.setChecked(False)
    assert window.canvas._constraint_items[1].pen().color() == QColor(KIND_COLORS["link"])
    assert window.canvas._constraint_items[1].pen().widthF() == 4
    window.alignment_errors_checkbox.setChecked(True)
    assert window.canvas._constraint_items[1].pen().color() == QColor("#d44f54")


@pytest.mark.parametrize("layout", ["rest", "aligned"])
def test_canvas_colors_current_results_in_both_layouts_but_not_outdated(solved_window, layout):
    app, window, images, points, links, result = solved_window
    window._set_canvas_state(layout)
    if layout == "rest":
        assert "Rest layout" in window.alignment_error_label.text()
    assert window.alignment_errors_checkbox.isEnabled()
    assert window.canvas._constraint_items[0].pen().color() == QColor("#2f9e68")
    assert window.canvas._constraint_items[1].pen().color() == QColor("#d44f54")
    window.alignment_errors_checkbox.setChecked(False)
    assert window.canvas._constraint_items[0].pen().color() == QColor(KIND_COLORS["link"])
    window.alignment_errors_checkbox.setChecked(True)
    assert window.canvas._constraint_items[0].pen().color() == QColor("#2f9e68")
    window._begin_point_observation_drag(images[0].id, points[0].id)
    window._on_point_observation_changed(images[0].id, points[0].id, 21, 30)
    window._finish_project_edit()
    assert "Outdated" in window.alignment_error_label.text()
    assert window.canvas._constraint_items[0].pen().color() == QColor(KIND_COLORS["link"])
    window.undo_stack.undo()
    assert "Current" in window.alignment_error_label.text()
    assert window.canvas._constraint_items[0].pen().color() == QColor("#2f9e68")


def test_rest_layout_can_show_unapplied_solve_residuals(solved_window):
    app, window, images, points, links, result = solved_window
    window._set_canvas_state("rest")
    window.undo_stack.undo()
    assert not window._alignment_feedback.matches_layout(window.project)
    assert window.alignment_errors_checkbox.isEnabled()
    assert "Not applied" in window.alignment_error_label.text()
    assert window.canvas._constraint_items[0].pen().color() == QColor("#2f9e68")
    assert window.canvas._constraint_items[1].pen().color() == QColor("#d44f54")


def test_canvas_readout_follows_point_and_image_selection(solved_window):
    app, window, images, points, links, result = solved_window
    window._select_point(points[0].id)
    assert "Point max link: 1.200 scene units" in window.alignment_error_label.text()
    window._on_canvas_image_selected(images[0].id)
    assert "Image RMS: 0.875 scene units" in window.alignment_error_label.text()
    window._on_canvas_image_selected(images[2].id)
    assert "Image RMS: --" in window.alignment_error_label.text()


def test_real_solver_residuals_feed_both_views(solved_window):
    app, window, images, points, links, result = solved_window
    actual = simulate_alignment(window.project, tolerance=0.25)
    window._apply_alignment_result(actual, tolerance=0.25)
    assert window._alignment_feedback.is_current(window.project)
    for index, link in enumerate(links):
        residual = actual.constraint_residuals[link.id]
        row = window.image_list.item(0).child(0).child(index)
        assert row.text(LayerTree.ERROR_COLUMN) == f"{residual:.3f}"
        assert window.canvas._constraint_items[index].pen().color() == QColor(residual_color(residual, 0.25))
    window._select_constraint(links[0].id)
    assert "Target: 0.25" in window.alignment_error_label.text()
    assert "scene units" in window.constraint_info_label.text()


@pytest.mark.parametrize("width", [900, 1400])
def test_canvas_error_readout_fits_available_space(solved_window, width):
    app, window, images, points, links, result = solved_window
    window.resize(width, 800)
    window.show()
    window.canvas_dock.show()
    window.canvas_dock.raise_()
    window._select_constraint(links[1].id)
    app.processEvents()
    label = window.alignment_error_label
    text_bounds = QFontMetrics(label.font()).boundingRect(
        label.contentsRect(), Qt.TextFlag.TextWordWrap, label.text(),
    )
    assert text_bounds.height() <= label.contentsRect().height()
    assert not label.geometry().intersects(window.alignment_errors_checkbox.geometry())
    assert window.image_list.columnWidth(LayerTree.ERROR_COLUMN) >= 80


@pytest.mark.parametrize("layout", ["rest", "aligned"])
@pytest.mark.parametrize("outdated", [False, True])
def test_reopening_project_restores_examined_errors(solved_window, tmp_path, monkeypatch, layout, outdated):
    app, window, images, points, links, result = solved_window
    window._set_canvas_state(layout)
    if outdated:
        window._begin_point_observation_drag(images[0].id, points[0].id)
        window._on_point_observation_changed(images[0].id, points[0].id, 21, 30)
        window._finish_project_edit()
    window.project_path = tmp_path / "examined.gsp"
    window.save_current_project()
    expected = window._alignment_feedback.to_dict()

    def unexpected_solve(*args, **kwargs):
        pytest.fail("Reopening a saved residual report must not run the solver.")

    monkeypatch.setattr("geostitch.app.simulate_alignment", unexpected_solve)
    reopened = MainWindow(QSettings(str(tmp_path / "reopened.ini"), QSettings.Format.IniFormat))
    try:
        reopened._open_project_path(str(window.project_path))
        assert reopened._alignment_feedback is not None
        assert reopened._alignment_feedback.to_dict() == expected
        assert reopened._alignment_feedback.is_current(reopened.project) is not outdated
        assert reopened._alignment_result == result
        assert reopened.project.canvas_state == layout
        row = reopened.image_list.item(0).child(0)
        assert row.text(LayerTree.ERROR_COLUMN) == "1.200"
        assert ("Outdated" in row.toolTip(LayerTree.ERROR_COLUMN)) is outdated
        assert reopened.alignment_errors_checkbox.isEnabled() is not outdated
        reopened._image_pixmaps = dict(window._image_pixmaps)
        reopened._refresh_canvas()
        color = KIND_COLORS["link"] if outdated else "#d44f54"
        assert reopened.canvas._constraint_items[1].pen().color() == QColor(color)
        reopened._select_constraint(links[1].id)
        assert "Link residual: 1.200 scene units" in reopened.alignment_error_label.text()
    finally:
        reopened.close()


def test_reopening_project_restores_unapplied_report(solved_window, tmp_path):
    app, window, images, points, links, result = solved_window
    window._set_canvas_state("rest")
    window.undo_stack.undo()
    window.project_path = tmp_path / "unapplied.gsp"
    window.save_current_project()
    reopened = MainWindow(QSettings(str(tmp_path / "unapplied.ini"), QSettings.Format.IniFormat))
    try:
        reopened._open_project_path(str(window.project_path))
        assert reopened._alignment_feedback is not None
        assert reopened._alignment_feedback.is_current(reopened.project)
        assert not reopened._alignment_feedback.matches_layout(reopened.project)
        assert "Not applied" in reopened.alignment_error_label.text()
        assert reopened.alignment_errors_checkbox.isEnabled()
    finally:
        reopened.close()


def test_startup_reopen_restores_saved_feedback(solved_window, tmp_path):
    app, window, images, points, links, result = solved_window
    window.project_path = tmp_path / "startup-feedback.gsp"
    window.save_current_project()
    settings = QSettings(str(tmp_path / "startup.ini"), QSettings.Format.IniFormat)
    settings.setValue("last_project", str(window.project_path))
    reopened = MainWindow(settings)
    try:
        reopened._reopen_startup_project()
        assert reopened._alignment_result == result
        assert reopened._alignment_feedback.is_current(reopened.project)
        assert reopened.image_list.item(0).child(0).text(LayerTree.ERROR_COLUMN) == "1.200"
        assert reopened.alignment_errors_checkbox.isEnabled()
    finally:
        reopened.close()


def test_opening_older_project_clears_previous_feedback(solved_window, tmp_path):
    app, window, images, points, links, result = solved_window
    data = window.project.to_dict()
    data["version"] = 13
    data.pop("alignment_feedback")
    path = tmp_path / "older-project.gsp"
    path.write_text(json.dumps(data), encoding="utf-8")
    window._open_project_path(str(path))
    assert window._alignment_feedback is None
    assert window._alignment_result is None
    assert window.image_list.item(0).child(0).text(LayerTree.ERROR_COLUMN) == "--"
    assert "unavailable" in window.alignment_error_label.text()
    assert not window.alignment_errors_checkbox.isEnabled()