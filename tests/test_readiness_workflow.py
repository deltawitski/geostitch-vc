import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from geostitch.app import MainWindow
from geostitch.georeference import GeoreferenceResult
from geostitch.models import GroundControlPoint, PointConstraint
from geostitch.solver_readiness import check_readiness


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "readiness.ini"), QSettings.Format.IniFormat))
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    yield window, errors
    window.close()


def test_blocking_preflight_does_not_run_optimizer_or_change_project(window, monkeypatch):
    window, errors = window
    before = window.project.to_dict()

    def unexpected(*args, **kwargs):
        pytest.fail("Optimizer must not run without cross-image links.")

    monkeypatch.setattr(window, "_compute_solver_result", unexpected)
    assert not window.run_alignment()
    assert window.project.to_dict() == before
    assert window.undo_stack.count() == 0
    assert not window._solver_running
    assert "matching points" in errors[0]
    assert window.shortcut_actions["solver_diagnostics"].isEnabled()


def test_geographic_translation_warning_allows_existing_solver_workflow(window, monkeypatch):
    window, errors = window
    image = window.project.add_image("image.png")
    point = GroundControlPoint.create("Control")
    point.set_observation(image.id, 10, 20)
    point.geographic_x, point.geographic_y = 100, 200
    window.project.add_control_point(point)
    result = GeoreferenceResult({image.id: (90, 220, 0, 1)}, {(image.id, point.id): 0}, 1, True, window.project.crs)
    calls = []

    def solve(*args):
        calls.append(True)
        return result

    monkeypatch.setattr(window, "_compute_solver_result", solve)
    assert window.run_georeference()
    assert calls == [True]
    assert not errors
    assert image.world_x == 90
    assert "readiness warning" in window.statusBar().currentMessage()


def test_combined_missing_geographic_controls_does_not_apply_alignment(window, monkeypatch):
    window, errors = window
    images = [window.project.add_image(name) for name in ("one.png", "two.png")]
    points = []
    for index, image in enumerate(images):
        point = GroundControlPoint.create(str(index))
        point.set_observation(image.id, 10, 20)
        window.project.add_control_point(point)
        points.append(point)
    window.project.add_constraint(PointConstraint.create(points[0].id, points[1].id))
    before = window.project.to_dict()
    monkeypatch.setattr(window, "_compute_solver_result", lambda *args: pytest.fail("Preflight must stop both stages."))
    assert not window.run_combined_solvers()
    assert window.project.to_dict() == before
    assert window.undo_stack.count() == 0
    assert "geographic" in errors[0]


def test_affected_item_selection_identifies_images_points_and_links(window):
    window, errors = window
    images = [window.project.add_image(name) for name in ("one.png", "two.png")]
    pixmap = QPixmap(100, 80)
    pixmap.fill()
    points = []
    for index, image in enumerate(images):
        window._image_pixmaps[image.id] = pixmap
        point = GroundControlPoint.create(str(index))
        point.set_observation(image.id, 10, 20)
        window.project.add_control_point(point)
        points.append(point)
    link = PointConstraint.create(points[0].id, points[1].id)
    window.project.add_constraint(link)
    window._refresh_all()
    window._mark_project_saved()
    issue = next(issue for issue in check_readiness(window.project, ("alignment",)) if issue.code == "translation_only")
    window._select_readiness_items(issue)
    assert set(window._selected_image_ids()) == {image.id for image in images}
    assert set(window._selected_point_ids()) == {point.id for point in points}
    assert window._selected_constraint_ids() == [link.id]
    assert not window._is_project_modified()
    assert window.statusBar().currentMessage() == issue.recommendation


def test_combined_geo_checks_use_fresh_alignment_frames(window, monkeypatch):
    from geostitch.adjustment import AlignmentResult
    import geostitch.app as app_module

    window, errors = window
    images = [window.project.add_image(name) for name in ("one.png", "two.png")]
    for image in images:
        image.position_x = 0
    points = []
    for index, image in enumerate(images):
        point = GroundControlPoint.create(str(index))
        point.set_observation(image.id, 10, 20)
        point.geographic_x, point.geographic_y = 100 + index * 100, 200
        window.project.add_control_point(point)
        points.append(point)
    link = PointConstraint.create(points[0].id, points[1].id)
    window.project.add_constraint(link)
    assert any(issue.code == "translation_only" for issue in check_readiness(window.project, ("georeference",)))
    alignment = AlignmentResult({images[0].id: (0, 0, 0, 1), images[1].id: (100, 0, 0, 1)}, {link.id: 0}, 1, True, (images[0].id,))
    world = GeoreferenceResult({image.id: (100, 200, 0, 1) for image in images}, {}, 1, True, window.project.crs)
    geographic_checks = []
    original = app_module.check_readiness

    def record_check(project, stages=("alignment", "georeference"), image_sizes=None):
        issues = original(project, stages, image_sizes)
        if stages == ("georeference",):
            geographic_checks.append(issues)
        return issues

    monkeypatch.setattr(app_module, "check_readiness", record_check)
    monkeypatch.setattr(window, "_compute_solver_result", lambda stage, *args: alignment if stage == "alignment" else world)
    assert window.run_combined_solvers()
    assert geographic_checks
    assert not any(issue.code == "translation_only" for issue in geographic_checks[-1])
    assert any(issue.code == "minimal_controls" for issue in geographic_checks[-1])
    assert not errors