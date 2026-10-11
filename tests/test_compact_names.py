from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from geostitch.app import MainWindow
from geostitch.models import GroundControlPoint, PointConstraint, Project


def test_compact_rows_and_details_preserve_context_and_unique_names(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(QSettings(str(tmp_path / "names.ini"), QSettings.Format.IniFormat))
    images = [window.project.add_image(name) for name in ("Sheet A.png", "Sheet B.png")]
    points = []
    for image in images:
        point = GroundControlPoint.create(window.project.unique_control_point_name("P1"))
        point.short_name = "P1"
        point.set_observation(image.id, 10, 20)
        window.project.add_control_point(point)
        points.append(point)
    points[0].label = "Bridge"
    link = PointConstraint.create(points[0].id, points[1].id)
    window.project.add_constraint(link)
    window._refresh_all()
    try:
        row = window.image_list.item(0).child(0)
        assert row.text(0) == "P1 · Bridge"
        assert row.child(0).text(0) == "L1 → P1"
        assert "Sheet A.png / P1" in row.child(0).toolTip(0)
        assert "Sheet B.png / P1" in row.child(0).toolTip(0)
        assert "Type: tie" in row.child(0).toolTip(0)
        window._select_point(points[1].id)
        stored_name = points[1].name
        assert window.name_edit.text() == "P1"
        window.label_edit.setText("Road")
        window.update_selected_point()
        assert points[1].name == stored_name
        assert points[1].display_name == "P1"
        window.name_edit.setText("North gate")
        window.update_selected_point()
        assert points[1].name == points[1].display_name == "North gate"
        assert points[1].short_name is None
        window.undo_stack.undo()
        point = window.project.point_by_id(points[1].id)
        assert point.display_name == "P1"
        assert point.name == stored_name
        window._select_constraint(link.id)
        assert "L1" in window.details_title.text()
        choices = [window.constraint_first_edit.itemText(index) for index in range(window.constraint_first_edit.count())]
        assert len(set(choices)) == 2
        assert any("Sheet A.png" in choice for choice in choices)
        assert any("Sheet B.png" in choice for choice in choices)
        assert window.constraint_second_edit.currentData() == points[1].id
    finally:
        window.close()


def test_legacy_links_and_split_points_receive_saved_link_numbers():
    project = Project()
    images = [project.add_image(name) for name in ("first.png", "second.png")]
    point = GroundControlPoint.create("Existing custom name")
    for image in images:
        point.set_observation(image.id, 10, 20)
    project.add_control_point(point)
    data = project.to_dict()
    data["version"] = 15
    data.pop("next_link_number")
    for image in data["images"]:
        image.pop("native_world_mapping")
    data["control_points"][0].pop("short_name")
    restored = Project.from_dict(data)
    assert restored.control_points[0].name == "Existing custom name"
    assert all(point.short_name is None for point in restored.control_points)
    assert [link.name for link in restored.constraints] == ["L1"]
    assert Project.from_dict(restored.to_dict()).constraints[0].name == "L1"


def test_split_point_migration_preserves_unrelated_link_identity():
    project = Project()
    images = [project.add_image(name) for name in ("first.png", "second.png")]
    endpoints = []
    for index, image in enumerate(images):
        point = GroundControlPoint.create(f"custom-{index}")
        point.set_observation(image.id, 10, 20)
        project.add_control_point(point)
        endpoints.append(point)
    original = PointConstraint.create(endpoints[0].id, endpoints[1].id)
    project.add_constraint(original)
    shared = GroundControlPoint.create("legacy-shared")
    for image in images:
        shared.set_observation(image.id, 30, 40)
    project.add_control_point(shared)
    restored = Project.from_dict(project.to_dict())
    saved = next(link for link in restored.constraints if link.id == original.id)
    assert saved.name == original.name == "L1"
    assert len({link.name for link in restored.constraints}) == 2