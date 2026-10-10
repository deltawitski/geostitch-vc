import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from geostitch.app import MainWindow
from test_layer_filter import make_project


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "filters.ini"), QSettings.Format.IniFormat))
    project, images, points, link = make_project()
    window.project = project
    pixmap = QPixmap(100, 80)
    pixmap.fill()
    window._image_pixmaps = {image.id: pixmap for image in images}
    window._refresh_all()
    window._mark_project_saved()
    yield window, images, points, link
    window.close()


def point_rows(window):
    tree = window.image_list
    return {top.child(index).item_id: top.child(index)
            for row in range(tree.topLevelItemCount()) for top in [tree.topLevelItem(row)]
            for index in range(top.childCount())}


def test_ui_filter_hides_rows_not_canvas_items_or_project_data(window):
    window, images, points, link = window
    before = window.project.to_dict()
    window.layer_point_filter.setCurrentIndex(window.layer_point_filter.findData("unlinked"))
    rows = point_rows(window)
    assert rows[points[0].id].isHidden()
    assert not rows[points[1].id].isHidden()
    assert rows[points[2].id].isHidden()
    assert all(item.isVisible() for item in window.canvas._layers.values())
    assert all(item.isVisible() for item in window.canvas._point_items.values())
    assert window.project.to_dict() == before
    assert not window._is_project_modified()
    assert not window.image_list.dragEnabled()
    window.clear_layer_filters()
    assert all(not item.isHidden() for item in point_rows(window).values())
    assert window.image_list.dragEnabled()


def test_filter_preserves_selected_image_and_point_outside_results(window):
    window, images, points, link = window
    window._on_canvas_image_selected(images[0].id)
    window.selected_point_ids = [points[0].id]
    window._refresh_details()
    window.layer_search.setText("not found")
    assert window._selected_image_ids() == [images[0].id]
    assert window._selected_point_ids() == [points[0].id]
    assert "selected outside filter" in window.layer_filter_summary.text()
    window._refresh_layers(force=True)
    assert window._selected_image_ids() == [images[0].id]
    assert window._selected_point_ids() == [points[0].id]
    window.clear_layer_filters()
    assert window._selected_image_ids() == [images[0].id]


def test_search_shows_hierarchy_and_restores_expansion(window):
    window, images, points, link = window
    top = window.image_list.item(0)
    top.setExpanded(False)
    rows = point_rows(window)
    rows[points[0].id].setExpanded(False)
    window.layer_search.setText("bridge")
    assert top.isExpanded()
    assert not rows[points[0].id].isHidden()
    assert rows[points[1].id].isHidden()
    assert rows[points[0].id].isExpanded()
    window.clear_layer_filters()
    assert not top.isExpanded()
    assert not rows[points[0].id].isExpanded()


def test_coordinate_filter_updates_after_point_edit_and_undo(window):
    window, images, points, link = window
    window.layer_point_filter.setCurrentIndex(window.layer_point_filter.findData("missing_geo"))
    assert not point_rows(window)[points[1].id].isHidden()
    before = window.project.to_dict()
    points[1].geographic_x, points[1].geographic_y = 300, 400
    window._record_project_edit(before, "Assign coordinates")
    window._refresh_points()
    assert point_rows(window)[points[1].id].isHidden()
    assert "1 of 3 points" in window.layer_filter_summary.text()
    window.undo_stack.undo()
    assert not point_rows(window)[points[1].id].isHidden()
    assert "2 of 3 points" in window.layer_filter_summary.text()


def test_image_scope_survives_rebuild_and_removed_image_resets_scope(window, monkeypatch):
    window, images, points, link = window
    combo = window.layer_image_filter
    combo.setCurrentIndex(combo.findData(images[1].id))
    window._refresh_layers(force=True)
    assert combo.currentData() == images[1].id
    assert window.image_list.item(0).isHidden()
    assert not window.image_list.item(1).isHidden()
    window._on_canvas_image_selected(images[1].id)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    assert window.remove_selected_image()
    assert combo.currentData() is None


def test_hidden_link_selection_is_retained_and_counted_once(window):
    window, images, points, link = window
    window._select_constraint(link.id)
    window.layer_point_filter.setCurrentIndex(window.layer_point_filter.findData("unlinked"))
    assert window._selected_constraint_ids() == [link.id]
    assert len(window.image_list.selected_constraint_items()) == 2
    assert "1 selected outside filter" in window.layer_filter_summary.text()
    window._refresh_layers(force=True)
    assert window._selected_constraint_ids() == [link.id]
    window.clear_layer_filters()
    assert "selected outside filter" not in window.layer_filter_summary.text()


def test_unlinked_filter_tracks_link_creation_and_undo(window):
    from geostitch.models import PointConstraint

    window, images, points, link = window
    window.layer_point_filter.setCurrentIndex(window.layer_point_filter.findData("unlinked"))
    assert not point_rows(window)[points[1].id].isHidden()
    before = window.project.to_dict()
    window.project.add_constraint(PointConstraint.create(points[1].id, points[2].id))
    window._record_project_edit(before, "Link points")
    window._refresh_constraints()
    assert point_rows(window)[points[1].id].isHidden()
    assert "0 of 3 points" in window.layer_filter_summary.text()
    window.undo_stack.undo()
    assert not point_rows(window)[points[1].id].isHidden()
    assert "1 of 3 points" in window.layer_filter_summary.text()


def test_label_rename_updates_active_search(window):
    window, images, points, link = window
    window.layer_search.setText("culvert")
    assert all(row.isHidden() for row in point_rows(window).values())
    before = window.project.to_dict()
    points[1].label = "Culvert"
    window._record_project_edit(before, "Label point")
    window._refresh_points()
    assert not point_rows(window)[points[1].id].isHidden()
    assert "1 of 3 points" in window.layer_filter_summary.text()
    window.undo_stack.undo()
    assert point_rows(window)[points[1].id].isHidden()
    assert "0 of 3 points" in window.layer_filter_summary.text()


def test_filtering_does_not_rebuild_canvas_or_recheck_feedback(window):
    from unittest.mock import patch

    window, images, points, link = window
    with patch.object(window.canvas, "set_layers", wraps=window.canvas.set_layers) as canvas_refreshes:
        window.layer_search.setText("bridge")
        window.layer_image_filter.setCurrentIndex(window.layer_image_filter.findData(images[0].id))
        window.layer_point_filter.setCurrentIndex(window.layer_point_filter.findData("with_geo"))
        assert canvas_refreshes.call_count == 0
    assert not window._is_project_modified()


@pytest.mark.parametrize("width", [300, 440])
def test_layer_filter_controls_fit_compact_dock(window, width):
    window, images, points, link = window
    app = QApplication.instance()
    window.show()
    window.image_dock.setFloating(True)
    window.image_dock.resize(width, 600)
    window.image_dock.show()
    app.processEvents()
    panel = window.image_dock.widget()
    for widget in (window.layer_search, window.layer_filter_reset,
                   window.layer_image_filter, window.layer_point_filter, window.layer_filter_summary):
        bounds = widget.rect().translated(widget.mapTo(panel, widget.rect().topLeft()))
        assert panel.rect().contains(bounds)
    assert window.layer_image_filter.geometry().right() < window.layer_point_filter.geometry().left()


def test_vector_selection_survives_point_only_filter_and_rebuild(window):
    window, images, points, link = window
    tree = window.image_list
    vector = next(tree.topLevelItem(index) for index in range(tree.topLevelItemCount())
                  if tree.topLevelItem(index).item_id == "vector")
    tree.setCurrentItem(vector)
    assert window._selected_vector_ids() == ["vector"]
    window.layer_point_filter.setCurrentIndex(window.layer_point_filter.findData("missing_geo"))
    assert vector.isHidden()
    assert window._selected_vector_ids() == ["vector"]
    window._refresh_layers(force=True)
    assert window._selected_vector_ids() == ["vector"]
    window.clear_layer_filters()
    window.layer_search.setText("roads")
    assert "1 layers" in window.layer_filter_summary.text()
    assert "selected outside filter" not in window.layer_filter_summary.text()


def test_delete_under_filter_removes_selected_point_not_unrelated_matches(window):
    window, images, points, link = window
    window._select_point(points[0].id)
    window.layer_point_filter.setCurrentIndex(window.layer_point_filter.findData("unlinked"))
    assert point_rows(window)[points[0].id].isHidden()
    assert not point_rows(window)[points[1].id].isHidden()
    window.delete_selected()
    assert window.project.point_by_id(points[0].id) is None
    assert window.project.point_by_id(points[1].id) is not None
    assert window.project.point_by_id(points[2].id) is not None
    window.undo_stack.undo()
    assert window.project.point_by_id(points[0].id) is not None
    assert point_rows(window)[points[0].id].isHidden()


def test_map_coordinate_pick_updates_missing_filter_and_keeps_selection(window):
    window, images, points, link = window
    window.layer_point_filter.setCurrentIndex(window.layer_point_filter.findData("missing_geo"))
    window._select_point(points[1].id)
    assert "2 of 3 points" in window.layer_filter_summary.text()
    window._on_world_coordinate_picked(0, 0)
    assert point_rows(window)[points[1].id].isHidden()
    assert window._selected_point_ids() == [points[1].id]
    assert "1 of 3 points" in window.layer_filter_summary.text()
    assert "1 selected outside filter" in window.layer_filter_summary.text()
    window.undo_stack.undo()
    assert not point_rows(window)[points[1].id].isHidden()
    assert "2 of 3 points" in window.layer_filter_summary.text()