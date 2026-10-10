import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog

from geostitch.app import MainWindow
from geostitch.models import Project
from geostitch.project_io import save_project


def make_window(tmp_path, settings=None):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(settings or QSettings(str(tmp_path / "recent.ini"), QSettings.Format.IniFormat))
    return app, window


def test_recent_projects_persist_in_most_recent_order_without_duplicates(tmp_path):
    app, window = make_window(tmp_path)
    first, second = tmp_path / "first.gsp", tmp_path / "second project.gsp"
    for path in (first, second):
        save_project(Project(name=path.stem), path)
    try:
        window._open_project_path(str(first))
        window._open_project_path(str(second))
        window._open_project_path(str(first))
        assert window._recent_project_paths() == [first, second]
        assert [action.data() for action in window.recent_projects_menu.actions() if action.data()] == [str(first), str(second)]
    finally:
        window.close()
    app, restored = make_window(tmp_path)
    try:
        assert restored._recent_project_paths() == [first, second]
    finally:
        restored.close()


def test_recent_history_is_limited_and_clear_does_not_delete_files(tmp_path):
    app, window = make_window(tmp_path)
    paths = [tmp_path / f"project-{index}.gsp" for index in range(12)]
    try:
        for path in paths:
            save_project(Project(), path)
            window._remember_recent_project(path)
        assert window._recent_project_paths() == list(reversed(paths))[:10]
        window._settings.setValue("last_project", str(paths[-1]))
        window.clear_recent_projects()
        assert window._recent_project_paths() == []
        assert window._settings.value("last_project") == str(paths[-1])
        assert all(path.exists() for path in paths)
        assert window.recent_projects_menu.actions()[0].text() == "No recent projects"
    finally:
        window.close()
    app, restored = make_window(tmp_path)
    try:
        assert restored._recent_project_paths() == []
    finally:
        restored.close()


def test_recent_history_seeds_from_legacy_last_project(tmp_path):
    settings = QSettings(str(tmp_path / "legacy.ini"), QSettings.Format.IniFormat)
    path = tmp_path / "last.gsp"
    settings.setValue("last_project", str(path))
    app, window = make_window(tmp_path, settings)
    try:
        assert window._recent_project_paths() == [path]
        assert "[Missing]" in window.recent_projects_menu.actions()[0].text()
    finally:
        window.close()


def test_failed_open_does_not_reorder_history_or_replace_project(tmp_path, monkeypatch):
    app, window = make_window(tmp_path)
    first, missing = tmp_path / "valid.gsp", tmp_path / "missing.gsp"
    save_project(Project(name="Current"), first)
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    try:
        window._remember_recent_project(missing)
        window._open_project_path(str(first))
        action = next(action for action in window.recent_projects_menu.actions() if action.data() == str(missing))
        assert "[Missing]" in action.text()
        action.trigger()
        assert window.project.name == "Current"
        assert window._recent_project_paths() == [first, missing]
        assert len(errors) == 1
    finally:
        window.close()


def test_successful_save_as_is_remembered_with_final_extension(tmp_path, monkeypatch):
    app, window = make_window(tmp_path)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "new project"), ""))
    try:
        assert window.save_current_project(save_as=True)
        assert window._recent_project_paths() == [tmp_path / "new project.gsp"]
        assert window.shortcut_actions["open_project_folder"].isEnabled()
    finally:
        window.close()


def test_duplicate_filenames_are_disambiguated_by_full_path(tmp_path):
    app, window = make_window(tmp_path)
    paths = [tmp_path / directory / "survey & field.gsp" for directory in ("one", "two")]
    try:
        for path in paths:
            save_project(Project(), path)
            window._remember_recent_project(path)
        actions = [action for action in window.recent_projects_menu.actions() if action.data()]
        assert actions[0].text() != actions[1].text()
        assert "&&" in actions[0].text()
        assert [action.toolTip() for action in actions] == [str(path) for path in reversed(paths)]
        actions[1].trigger()
        assert window.project_path == paths[0]
    finally:
        window.close()


def test_project_folder_action_targets_saved_project_and_disables_for_new(tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    app, window = make_window(tmp_path)
    path = tmp_path / "project folder" / "survey.gsp"
    save_project(Project(), path)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    try:
        assert not window.shortcut_actions["open_project_folder"].isEnabled()
        window._open_project_path(str(path))
        assert window.shortcut_actions["open_project_folder"].isEnabled()
        window.shortcut_actions["open_project_folder"].trigger()
        assert opened == [str(path.parent).replace("\\", "/")]
        assert not window._is_project_modified()
        window.new_project()
        assert not window.shortcut_actions["open_project_folder"].isEnabled()
    finally:
        window.close()


def test_unavailable_project_folder_reports_error(tmp_path, monkeypatch):
    app, window = make_window(tmp_path)
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    try:
        window.project_path = tmp_path / "removed folder" / "survey.gsp"
        window.open_project_folder()
        assert len(errors) == 1
        assert "no longer available" in errors[0]
    finally:
        window.close()


def test_history_handles_malformed_values_and_normalized_paths(tmp_path):
    app, window = make_window(tmp_path)
    path = tmp_path / "survey.gsp"
    try:
        window._settings.setValue("recent_projects", ["", 123, "\x00", str(path), str(tmp_path / "subfolder" / ".." / "survey.gsp")])
        assert window._recent_project_paths() == [path]
        window._settings.setValue("recent_projects", str(path))
        assert window._recent_project_paths() == [path]
        window._settings.setValue("recent_projects", 123)
        assert window._recent_project_paths() == []
    finally:
        window.close()


def test_history_menu_updates_missing_status_when_file_moves(tmp_path):
    app, window = make_window(tmp_path)
    path = tmp_path / "survey.gsp"
    save_project(Project(), path)
    try:
        window._open_project_path(str(path))
        assert "[Missing]" not in window.recent_projects_menu.actions()[0].text()
        path.rename(tmp_path / "moved.gsp")
        window.recent_projects_menu.aboutToShow.emit()
        assert "[Missing]" in window.recent_projects_menu.actions()[0].text()
        assert window._recent_project_paths() == [path]
    finally:
        window.close()


def test_open_windows_merge_recent_history_updates(tmp_path):
    app, first = make_window(tmp_path)
    app, second = make_window(tmp_path)
    paths = [tmp_path / "one.gsp", tmp_path / "two.gsp"]
    try:
        first._remember_recent_project(paths[0])
        second._remember_recent_project(paths[1])
        first.recent_projects_menu.aboutToShow.emit()
        assert first._recent_project_paths() == list(reversed(paths))
        assert second._recent_project_paths() == list(reversed(paths))
    finally:
        first.close()
        second.close()


def test_project_folder_launch_failure_is_reported(tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    app, window = make_window(tmp_path)
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: False)
    try:
        window.project_path = tmp_path / "survey.gsp"
        window.open_project_folder()
        assert len(errors) == 1
        assert "Could not open project folder" in errors[0]
    finally:
        window.close()