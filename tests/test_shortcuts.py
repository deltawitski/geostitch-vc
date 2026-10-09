import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QKeySequence, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDockWidget, QMenuBar, QMessageBox, QToolBar

from geostitch.app import MainWindow, ShortcutSettingsDialog
from geostitch.layers import CONSTRAINT_KIND, IMAGE_KIND, VECTOR_KIND, LayerItem, LayerTree
from geostitch.models import GroundControlPoint, PointConstraint, VectorLayer


def test_point_rows_expand_to_incident_constraints_and_preserve_expansion(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "links.ini"), QSettings.Format.IniFormat))
    first_image = window.project.add_image("first.png")
    second_image = window.project.add_image("second.png")
    points = [GroundControlPoint.create(name) for name in ("first", "second", "third", "unlinked")]
    for index, point in enumerate(points):
        point.set_observation(first_image.id if index == 0 else second_image.id, 10, 20)
        window.project.add_control_point(point)
    links = [PointConstraint.create(points[0].id, point.id) for point in points[1:3]]
    for link in links:
        window.project.add_constraint(link)
    window._refresh_all()

    def point_rows():
        tree = window.image_list
        return {tree.topLevelItem(row).child(index).item_id: tree.topLevelItem(row).child(index)
                for row in range(tree.topLevelItemCount()) for index in range(tree.topLevelItem(row).childCount())}

    try:
        rows = point_rows()
        assert [rows[point.id].childCount() for point in points] == [2, 1, 1, 0]
        assert rows[points[0].id].child(0).item_id == links[0].id
        assert "second (second.png)" in rows[points[0].id].child(0).text(0)
        rows[points[0].id].setExpanded(True)
        rows[points[1].id].setExpanded(False)
        points[1].name = "renamed"
        links[0].kind = "updated"
        window._refresh_layers()
        rows = point_rows()
        assert rows[points[0].id].isExpanded()
        assert not rows[points[1].id].isExpanded()
        assert "renamed (second.png) - updated" == rows[points[0].id].child(0).text(0)
        window.project.constraints.remove(links[0])
        window._refresh_layers()
        assert [point_rows()[point.id].childCount() for point in points] == [1, 0, 1, 0]
    finally:
        window.close()


def test_constraint_tree_selection_details_delete_and_undo(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "link-selection.ini"), QSettings.Format.IniFormat))
    images = [window.project.add_image(name) for name in ("first.png", "second.png")]
    points = [GroundControlPoint.create(name) for name in ("first", "second")]
    for image, point in zip(images, points):
        point.set_observation(image.id, 10, 20)
        window.project.add_control_point(point)
    link = PointConstraint.create(points[0].id, points[1].id)
    window.project.add_constraint(link)
    window._refresh_all()
    window.show()
    app.processEvents()

    def link_rows():
        return [window.image_list.item(index).child(0).child(0) for index in range(2)]

    try:
        tree = window.image_list
        image_row = tree.item(0)
        image_row.setExpanded(True)
        point_row = image_row.child(0)
        point_row.setExpanded(False)
        before_selection = (window._selected_image_ids(), window._selected_point_ids(), window._selected_constraint_ids())
        point_rect = tree.visualItemRect(point_row)
        arrow = QPoint(point_rect.left() - tree.indentation() // 2, point_rect.center().y())
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=arrow)
        assert point_row.isExpanded()
        assert (window._selected_image_ids(), window._selected_point_ids(), window._selected_constraint_ids()) == before_selection
        row = link_rows()[0]
        assert row.kind == CONSTRAINT_KIND
        assert not row.flags() & Qt.ItemFlag.ItemIsDragEnabled
        assert all(row.icon(column).isNull() for column in (LayerTree.EYE_COLUMN, LayerTree.MAP_COLUMN, LayerTree.LOCK_COLUMN))
        window.image_list.setCurrentItem(row)
        assert window._selected_constraint_ids() == [link.id]
        assert window._selected_point_ids() == []
        assert window._selected_image_ids() == []
        assert window.details_stack.currentIndex() == 3
        assert window.canvas._selected_constraint_indices == {0}
        assert all(item.isSelected() for item in link_rows())
        snapshot = window.project.to_dict()
        for column in (LayerTree.EYE_COLUMN, LayerTree.MAP_COLUMN, LayerTree.LOCK_COLUMN):
            position = tree.visualItemRect(row).center()
            position.setX(tree.header().sectionViewportPosition(column) + tree.columnWidth(column) // 2)
            QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
        assert window.project.to_dict() == snapshot
        assert window._selected_constraint_ids() == [link.id]
        window._on_image_selected()
        assert window._selected_constraint_ids() == [link.id]
        window.constraint_kind_edit.setText("refined")
        window.update_selected_constraint()
        assert all("refined" in item.text(0) for item in link_rows())
        assert window.image_list.currentItem().parent().item_id == points[0].id
        window.delete_selected()
        assert window.project.constraints == []
        assert all(window.image_list.item(index).child(0).childCount() == 0 for index in range(2))
        assert len(window.project.control_points) == 2
        window.undo_stack.undo()
        assert all(window.image_list.item(index).child(0).childCount() == 1 for index in range(2))
        window._select_point(points[0].id)
        assert not any(item.isSelected() for item in link_rows())
    finally:
        window.close()


@pytest.mark.parametrize("modifier", [
    Qt.KeyboardModifier.NoModifier,
    Qt.KeyboardModifier.ControlModifier,
    Qt.KeyboardModifier.ShiftModifier,
])
@pytest.mark.parametrize("column", [LayerTree.EYE_COLUMN, LayerTree.LOCK_COLUMN])
def test_visibility_gesture_preserves_multiselection_and_next_row_click(modifier, column):
    app = QApplication.instance() or QApplication([])
    tree = LayerTree()
    first = LayerItem(IMAGE_KIND, "first", "First")
    target = LayerItem(IMAGE_KIND, "target", "Target")
    vector = LayerItem(VECTOR_KIND, "vector", "Vector")
    tree.addTopLevelItems([first, target, vector])
    first.setSelected(True)
    vector.setSelected(True)
    tree.resize(420, 200)
    tree.show()
    app.processEvents()
    toggles = []
    tree.visibility_toggled.connect(lambda item, column: toggles.append((item.item_id, column)))
    tree.lock_toggled.connect(lambda item: toggles.append((item.item_id, LayerTree.LOCK_COLUMN)))
    position = tree.visualItemRect(target).center()
    position.setX(tree.header().sectionViewportPosition(column) + tree.columnWidth(column) // 2)
    release_position = QPoint(40, position.y())
    try:
        QTest.mousePress(tree.viewport(), Qt.MouseButton.LeftButton, modifier, pos=position)
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(release_position),
            QPointF(tree.viewport().mapToGlobal(release_position)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            modifier,
        )
        app.sendEvent(tree.viewport(), move)
        QTest.mouseRelease(tree.viewport(), Qt.MouseButton.LeftButton, modifier, pos=release_position)

        assert toggles == [("target", column)]
        assert tree.selectedItems() == [first]
        assert tree.selected_vector_items() == [vector]

        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=release_position)
        assert tree.selectedItems() == [target]
        assert tree.selected_vector_items() == []
    finally:
        tree.close()


@pytest.mark.parametrize("target_kind", ["image", "point", "vector"])
@pytest.mark.parametrize("modifier", [
    Qt.KeyboardModifier.NoModifier,
    Qt.KeyboardModifier.ControlModifier,
    Qt.KeyboardModifier.ShiftModifier,
])
def test_layer_lock_click_updates_indicator_without_changing_selection(tmp_path, target_kind, modifier):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "row-lock.ini"), QSettings.Format.IniFormat))
    first = window.project.add_image(tmp_path / "first.png")
    target = window.project.add_image(tmp_path / "target.png")
    point = GroundControlPoint.create("point")
    point.set_observation(target.id, 10, 20)
    window.project.add_control_point(point)
    vector = VectorLayer(id="vector", path=str(tmp_path / "layer.geojson"), name="layer")
    window.project.vector_layers.append(vector)
    window._refresh_all()
    window.show()
    app.processEvents()
    tree = window.image_list

    def target_row():
        rows = {tree.topLevelItem(row).item_id: tree.topLevelItem(row) for row in range(tree.topLevelItemCount())}
        return {"image": rows[target.id], "point": rows[target.id].child(0), "vector": rows[vector.id]}[target_kind]

    window._on_canvas_image_selected(first.id)
    vector_row = next(tree.topLevelItem(row) for row in range(tree.topLevelItemCount()) if tree.topLevelItem(row).item_id == vector.id)
    vector_row.setSelected(True)
    row = target_row()
    if row.parent() is not None:
        row.parent().setExpanded(True)
    before_selection = (window._selected_image_ids(), window._selected_point_ids(), window._selected_vector_ids())
    before_project = window.project.to_dict()
    current_id = tree.currentItem().item_id
    position = tree.visualItemRect(row).center()
    position.setX(tree.header().sectionViewportPosition(LayerTree.LOCK_COLUMN) + tree.columnWidth(LayerTree.LOCK_COLUMN) // 2)
    icon = row.icon(LayerTree.LOCK_COLUMN).pixmap(24, 24).toImage()
    try:
        assert row.icon(LayerTree.LOCK_COLUMN).isNull() == (target_kind == "vector")
        QTest.mousePress(tree.viewport(), Qt.MouseButton.LeftButton, modifier, pos=position)
        moved = position + QPoint(0, 2)
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(moved), QPointF(tree.viewport().mapToGlobal(moved)),
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, modifier)
        app.sendEvent(tree.viewport(), move)
        QTest.mouseRelease(tree.viewport(), Qt.MouseButton.LeftButton, modifier, pos=moved)
        assert (window._selected_image_ids(), window._selected_point_ids(), window._selected_vector_ids()) == before_selection
        assert tree.currentItem().item_id == current_id
        assert not first.rest_locked
        if target_kind == "vector":
            assert window.project.to_dict() == before_project
            return
        assert target.rest_locked == (target_kind == "image")
        assert point.observations[0].locked == (target_kind == "point")
        assert "Locked:" in target_row().toolTip(LayerTree.LOCK_COLUMN)
        assert target_row().icon(LayerTree.LOCK_COLUMN).pixmap(24, 24).toImage() != icon
        window.undo_stack.undo()
        assert "Unlocked:" in target_row().toolTip(LayerTree.LOCK_COLUMN)
        window.undo_stack.redo()
        assert "Locked:" in target_row().toolTip(LayerTree.LOCK_COLUMN)
        row = target_row()
        if row.parent() is not None:
            row.parent().setExpanded(True)
        position.setY(tree.visualItemRect(row).center().y())
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
        assert "Unlocked:" in target_row().toolTip(LayerTree.LOCK_COLUMN)
        assert (window._selected_image_ids(), window._selected_point_ids(), window._selected_vector_ids()) == before_selection
    finally:
        window.close()


def test_layer_lock_indicators_follow_shortcut_and_point_checkbox(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "lock-indicators.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(tmp_path / "image.png")
    point = GroundControlPoint.create("point")
    point.set_observation(image.id, 10, 20)
    window.project.add_control_point(point)
    window._refresh_all()
    try:
        window._on_canvas_image_selected(image.id)
        window.shortcut_actions["toggle_lock"].trigger()
        assert "Locked:" in window.image_list.item(0).toolTip(LayerTree.LOCK_COLUMN)
        window.shortcut_actions["toggle_lock"].trigger()
        assert "Unlocked:" in window.image_list.item(0).toolTip(LayerTree.LOCK_COLUMN)
        window._select_point(point.id)
        window.lock_observation_checkbox.setChecked(True)
        assert "Locked:" in window.image_list.item(0).child(0).toolTip(LayerTree.LOCK_COLUMN)
        window.shortcut_actions["toggle_lock"].trigger()
        assert "Unlocked:" in window.image_list.item(0).child(0).toolTip(LayerTree.LOCK_COLUMN)
    finally:
        window.close()


@pytest.mark.parametrize("selection", ["none", "image", "point", "vector"])
@pytest.mark.parametrize("target_kind,column", [
    ("image", LayerTree.EYE_COLUMN),
    ("image", LayerTree.MAP_COLUMN),
    ("point", LayerTree.EYE_COLUMN),
    ("point", LayerTree.MAP_COLUMN),
    ("vector", LayerTree.MAP_COLUMN),
])
def test_layer_visibility_click_preserves_selection(tmp_path, selection, target_kind, column):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "visibility.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(tmp_path / "selected.png")
    target = window.project.add_image(tmp_path / "target.png")
    point = GroundControlPoint.create("point")
    point.set_observation(image.id, 10.0, 20.0)
    window.project.add_control_point(point)
    target_point = GroundControlPoint.create("target-point")
    target_point.set_observation(target.id, 10.0, 20.0)
    window.project.add_control_point(target_point)
    vector = VectorLayer(id="vector", path=str(tmp_path / "layer.geojson"), name="layer")
    window.project.vector_layers.append(vector)
    target_vector = VectorLayer(id="target-vector", path=str(tmp_path / "target.geojson"), name="target-layer")
    window.project.vector_layers.append(target_vector)
    window._refresh_all()
    window.show()
    app.processEvents()
    tree = window.image_list
    tree.clearSelection()
    rows = {tree.topLevelItem(row).item_id: tree.topLevelItem(row) for row in range(tree.topLevelItemCount())}
    if selection != "none":
        selected = {"image": rows[image.id], "point": rows[image.id].child(0), "vector": rows[vector.id]}[selection]
        tree.setCurrentItem(selected)
    before = (window._selected_image_ids(), window._selected_point_ids(), window._selected_vector_ids())
    current_id = tree.currentItem().item_id if tree.currentItem() is not None else None
    rows[target.id].setExpanded(True)
    target_item = {"image": rows[target.id], "point": rows[target.id].child(0), "vector": rows[target_vector.id]}[target_kind]
    target = {"image": target, "point": target_point, "vector": target_vector}[target_kind]
    rect = tree.visualItemRect(target_item)
    position = rect.center()
    position.setX(tree.header().sectionViewportPosition(column) + tree.columnWidth(column) // 2)
    attribute = "map_visible" if column == LayerTree.MAP_COLUMN and target_kind != "vector" else "visible"
    visible = getattr(target, attribute)
    try:
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
        app.processEvents()

        assert getattr(target, attribute) is not visible
        assert (window._selected_image_ids(), window._selected_point_ids(), window._selected_vector_ids()) == before
        assert (tree.currentItem().item_id if tree.currentItem() is not None else None) == current_id
        QTest.mousePress(tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
        moved_position = position + QPoint(0, 2)
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(moved_position),
            QPointF(tree.viewport().mapToGlobal(moved_position)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        app.sendEvent(tree.viewport(), move)
        QTest.mouseRelease(tree.viewport(), Qt.MouseButton.LeftButton, pos=moved_position)
        app.processEvents()
        assert (window._selected_image_ids(), window._selected_point_ids(), window._selected_vector_ids()) == before
        window.canvas.setFocus()
        app.processEvents()
        tree.setFocus()
        app.processEvents()
        assert (window._selected_image_ids(), window._selected_point_ids(), window._selected_vector_ids()) == before
        QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
        app.processEvents()
        assert (window._selected_image_ids(), window._selected_point_ids(), window._selected_vector_ids()) == before
        window.undo_stack.undo()
        assert (window._selected_image_ids(), window._selected_point_ids(), window._selected_vector_ids()) == before
        window.undo_stack.redo()
        assert (window._selected_image_ids(), window._selected_point_ids(), window._selected_vector_ids()) == before
    finally:
        window.close()


@pytest.mark.parametrize("command", ["delete_selected", "remove_selected_image"])
@pytest.mark.parametrize("confirm_vectors,select_image", [(True, False), (False, False), (True, True)])
def test_deleting_selected_geojson_does_not_delete_unselected_image(
    tmp_path, monkeypatch, command, confirm_vectors, select_image
):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "delete.ini"), QSettings.Format.IniFormat))
    image = window.project.add_image(tmp_path / "image.png")
    selected_image = window.project.add_image(tmp_path / "selected.png") if select_image else None
    vector = VectorLayer(id="vector", path=str(tmp_path / "layer.geojson"), name="layer")
    window.project.vector_layers.append(vector)
    window._refresh_all()
    window.image_list.clearSelection()
    vector_item = next(
        window.image_list.topLevelItem(row)
        for row in range(window.image_list.topLevelItemCount())
        if window.image_list.topLevelItem(row).item_id == vector.id
    )
    window.image_list.setCurrentItem(vector_item)
    if selected_image is not None:
        image_item = next(
            window.image_list.item(row)
            for row in range(window.image_list.count())
            if window.image_list.item(row).item_id == selected_image.id
        )
        image_item.setSelected(True)
    assert window._selected_vector_ids() == [vector.id]
    assert window._selected_image_ids() == ([selected_image.id] if selected_image is not None else [])
    prompts = []

    def confirm(parent, title, text):
        prompts.append(title)
        return QMessageBox.StandardButton.Yes if confirm_vectors else QMessageBox.StandardButton.No

    monkeypatch.setattr("geostitch.app.QMessageBox.question", confirm)
    try:
        getattr(window, command)()

        assert prompts == ["Remove GeoJSON layer"] + (["Remove image"] if select_image else [])
        assert window.project.images == [image]
        assert window.project.vector_layers == ([] if confirm_vectors else [vector])
    finally:
        window.close()


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
