import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from geostitch.link_review import LinkReviewPanel
from geostitch.models import GroundControlPoint, PointConstraint, Project

_application = None

@pytest.fixture(scope="module", autouse=True)
def application():
    global _application
    _application = QApplication.instance() or QApplication([])
    yield _application


def make_review():
    application = QApplication.instance() or QApplication([])
    project = Project()
    images = [project.add_image(name) for name in ("first.png", "second.png")]
    points = []
    for index, image in enumerate(images):
        point = GroundControlPoint.create(f"Point-{index}")
        point.set_observation(image.id, 40, 60)
        project.add_control_point(point)
        points.append(point)
    links = [PointConstraint.create(points[0].id, points[1].id, kind=kind) for kind in ("tie", "check")]
    for link in links:
        project.add_constraint(link)
    pixmap = QPixmap(200, 150)
    pixmap.fill()
    panel = LinkReviewPanel()
    panel.set_project(project, {links[0].id: 0.1, links[1].id: 2.0}, True, lambda image: (pixmap, ""))
    return application, panel, project, images, points, links


def test_review_filters_sorts_and_navigates():
    application, panel, project, images, points, links = make_review()
    try:
        assert panel.visible_link_ids() == [links[1].id, links[0].id]
        chosen = []
        panel.link_selected.connect(chosen.append)
        panel._step(1)
        assert chosen == [links[1].id]
        panel._step(1)
        assert chosen[-1] == links[0].id
        panel.filter.setCurrentIndex(1)
        assert panel.visible_link_ids() == [links[1].id]
        panel.search.setText("check")
        assert panel.visible_link_ids() == [links[1].id]
        panel.search.setText("absent")
        assert panel.visible_link_ids() == []
    finally:
        panel.close()
        delete(panel)


def test_review_shows_endpoint_observations_and_outdated_state():
    application, panel, project, images, points, links = make_review()
    try:
        panel.select_link(links[0].id)
        assert [view._image_key for view in panel.views] == [image.id for image in images]
        assert panel.views[0]._point_items[points[0].id].pos().x() == 40
        points[0].observations[0].locked = True
        panel.set_project(project, {links[0].id: 1.2}, False, panel._pixmap)
        assert points[0].id in panel.views[0]._locked_points
        assert "Outdated" in panel.summary.text()
        assert not panel.views[0].view_controls.isVisible()
    finally:
        panel.close()
        delete(panel)


def test_numeric_sort_keeps_unavailable_residuals_last():
    application, panel, project, images, points, links = make_review()
    extra = PointConstraint.create(points[0].id, points[1].id, kind="extra")
    project.add_constraint(extra)
    try:
        panel.set_project(project, {links[0].id: 10, links[1].id: 2}, True, panel._pixmap)
        assert panel.visible_link_ids() == [links[0].id, links[1].id, extra.id]
        panel.filter.setCurrentIndex(2)
        assert panel.visible_link_ids() == [extra.id]
        panel.filter.setCurrentIndex(0)
        points[0].label = "Bridge"
        panel.set_project(project, panel._residuals, False, panel._pixmap)
        panel.search.setText("bridge")
        assert len(panel.visible_link_ids()) == 3
    finally:
        panel.close()
        delete(panel)


def test_mainwindow_review_selection_drag_and_undo(tmp_path):
    from PySide6.QtCore import QSettings, QPointF, QPoint, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from geostitch.app import MainWindow

    application, panel, project, images, points, links = make_review()
    pixmap = QPixmap(200, 150)
    pixmap.fill()
    window = MainWindow(QSettings(str(tmp_path / "review.ini"), QSettings.Format.IniFormat))
    window.project = project
    window._image_pixmaps = {image.id: pixmap for image in images}
    window._mark_project_saved()
    window._refresh_all()
    window.show()
    window.link_review_dock.setFloating(True)
    window.link_review_dock.resize(1000, 650)
    window.link_review_dock.show()
    application.processEvents()
    review = window.link_review
    try:
        review._step(1)
        assert window.selected_constraint_id == review._selected
        window._select_constraint(links[0].id)
        assert review._selected == links[0].id
        view = review.views[0]
        view.fit_image()
        start = view.mapFromScene(QPointF(40, 60))
        end = start + QPoint(30, 20)
        expected = view.mapToScene(end)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        application.sendEvent(view.viewport(), move)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        observation = window.project.point_by_id(points[0].id).observations[0]
        assert observation.x == pytest.approx(expected.x())
        assert observation.y == pytest.approx(expected.y())
        assert window.selected_constraint_id == links[0].id
        assert window.undo_stack.count() == 1
        assert window._is_project_modified()
        window.undo_stack.undo()
        observation = window.project.point_by_id(points[0].id).observations[0]
        assert (observation.x, observation.y) == (40, 60)
        assert not window._is_project_modified()
        window.undo_stack.redo()
        assert window.project.point_by_id(points[0].id).observations[0].x == pytest.approx(expected.x())
        observation = window.project.point_by_id(points[0].id).observations[0]
        observation.locked = True
        window._refresh_alignment_feedback()
        assert points[0].id in view._locked_points
        before = (observation.x, observation.y)
        start = view.mapFromScene(QPointF(*before))
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        end = start + QPoint(20, 10)
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
                   Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        application.sendEvent(view.viewport(), move)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert (observation.x, observation.y) == before
        assert window.undo_stack.count() == 1
    finally:
        panel.close()
        delete(panel)
        window.close()


def test_review_preserves_zoom_and_skips_unchanged_scene_rebuilds():
    from unittest.mock import patch

    application, panel, project, images, points, links = make_review()
    try:
        panel.resize(1000, 600)
        panel.show()
        application.processEvents()
        panel.select_link(links[0].id)
        panel.views[0].scale(2, 2)
        panel.views[1].scale(0.5, 0.5)
        scales = [view.transform().m11() for view in panel.views]
        with patch.object(panel.views[0], "set_image", wraps=panel.views[0].set_image) as rebuilds:
            panel.set_project(project, panel._residuals, True, panel._pixmap)
            panel.select_link(links[0].id)
            assert rebuilds.call_count == 0
        panel.select_link(links[1].id)
        assert [view.transform().m11() for view in panel.views] == scales
        project.constraints.clear()
        panel.set_project(project, {}, False, panel._pixmap)
        assert not any(view.has_image() for view in panel.views)
        assert not panel.next.isEnabled()
    finally:
        panel.close()
        delete(panel)


def test_missing_endpoint_image_shows_loading_then_error():
    application, panel, project, images, points, links = make_review()
    try:
        panel.set_project(project, {}, False, lambda image: (None, ""))
        panel.select_link(links[0].id)
        assert not panel.views[0].has_image()
        assert panel.views[0]._placeholder_label.text() == "Loading image..."
        panel.set_project(project, {}, False, lambda image: (None, "File missing"))
        assert "File missing" in panel.views[0]._placeholder_label.text()
    finally:
        panel.close()
        delete(panel)


def test_review_edit_marks_residuals_outdated_and_undo_restores_current(tmp_path):
    from PySide6.QtCore import QSettings
    from geostitch.app import MainWindow
    from geostitch.alignment_feedback import AlignmentFeedback
    from test_alignment_feedback import make_feedback_project

    application = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "feedback.ini"), QSettings.Format.IniFormat))
    project, images, points, links, result = make_feedback_project()
    project.alignment_feedback = AlignmentFeedback.capture(project, result, 0.5)
    window.project = project
    pixmap = QPixmap(200, 150)
    pixmap.fill()
    window._image_pixmaps = {image.id: pixmap for image in images}
    window._refresh_all()
    window.show()
    window.link_review_dock.show()
    window.link_review_dock.raise_()
    application.processEvents()
    try:
        window._select_constraint(links[0].id)
        assert "Current" in window.link_review.summary.text()
        observation = points[0].observations[0]
        window.link_review.point_observation_interaction_started.emit(observation.image_id, points[0].id)
        window.link_review.point_observation_changed.emit(observation.image_id, points[0].id, observation.x + 1, observation.y)
        window.link_review.point_observation_interaction_finished.emit(observation.image_id, points[0].id)
        assert "Outdated" in window.link_review.summary.text()
        assert window.selected_constraint_id == links[0].id
        window.undo_stack.undo()
        assert "Current" in window.link_review.summary.text()
        window._solver_running = True
        window._update_hints()
        assert not window.link_review.isEnabled()
        window._solver_running = False
    finally:
        window.close()


@pytest.mark.parametrize("width", [650, 1100])
def test_review_layout_fits_and_has_no_overlapping_viewports(width):
    application, panel, project, images, points, links = make_review()
    try:
        panel.resize(width, 650)
        panel.show()
        panel.select_link(links[0].id)
        application.processEvents()
        assert panel.width() == width
        for widget in (panel.search, panel.filter, panel.threshold):
            assert panel.rect().contains(widget.geometry())
        assert panel.search.geometry().right() < panel.filter.geometry().left()
        assert panel.filter.geometry().right() < panel.threshold.geometry().left()
        assert all(view.viewport().width() > 200 and view.viewport().height() > 200 for view in panel.views)
    finally:
        panel.close()
        delete(panel)