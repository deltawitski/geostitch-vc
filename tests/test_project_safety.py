import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

import geostitch.app as app_module
from geostitch.app import MainWindow
from geostitch.models import Project
from geostitch.project_io import load_project, load_recovery, save_project, save_recovery


@pytest.fixture
def window(tmp_path):
    application = QApplication.instance() or QApplication([])
    main_window = MainWindow(QSettings(str(tmp_path / "safety.ini"), QSettings.Format.IniFormat))
    yield main_window
    main_window._mark_project_saved()
    main_window.close()


def edit_project(window, name="Edited"):
    before = window.project.to_dict()
    window.project.name = name
    window._record_project_edit(before, "Rename project")


def test_modified_state_tracks_save_undo_and_redo(window, tmp_path):
    assert not window._is_project_modified()
    edit_project(window)
    assert window._is_project_modified()
    assert window.windowTitle().endswith(" *")
    window.undo_stack.undo()
    assert not window._is_project_modified()
    assert not window.windowTitle().endswith(" *")
    window.undo_stack.redo()
    window.project_path = tmp_path / "saved.gsp"
    assert window.save_current_project()
    assert not window._is_project_modified()
    assert window.undo_stack.isClean()
    window.undo_stack.undo()
    assert window._is_project_modified()
    window.undo_stack.redo()
    assert not window._is_project_modified()


def test_navigation_alone_does_not_modify_document(window):
    window.project.views = {"canvas": {"scale": 2}}
    window.project.canvas_state = "aligned"
    assert not window._is_project_modified()


@pytest.mark.parametrize("operation", ["new", "open", "close"])
def test_cancel_preserves_project_and_undo_history(window, tmp_path, monkeypatch, operation):
    edit_project(window)
    original = window.project
    path = tmp_path / "other.gsp"
    save_project(Project(name="Other"), path)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    if operation == "new":
        window.new_project()
    elif operation == "open":
        window._open_project_path(str(path))
    else:
        window.show()
        assert not window.close()
        assert window.isVisible()
    assert window.project is original
    assert window.undo_stack.count() == 1


def test_save_before_new_preserves_edits_on_disk(window, tmp_path, monkeypatch):
    edit_project(window)
    path = tmp_path / "saved.gsp"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    window.new_project()
    assert load_project(path).name == "Edited"
    assert window.project.name != "Edited"
    assert not window._is_project_modified()


def test_cancelled_save_blocks_project_replacement(window, monkeypatch):
    edit_project(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    window.new_project()
    assert window.project.name == "Edited"
    assert window._is_project_modified()


def test_failed_save_as_keeps_original_path_and_modified_state(window, tmp_path, monkeypatch):
    original_path = tmp_path / "original.gsp"
    window.project_path = original_path
    edit_project(window)
    errors = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "new.gsp"), ""))
    monkeypatch.setattr(window, "_show_error", errors.append)

    def fail_save(*args, **kwargs):
        raise OSError("Disk full")

    monkeypatch.setattr(app_module, "save_project", fail_save)
    assert not window.save_current_project(save_as=True)
    assert window.project_path == original_path
    assert window._is_project_modified()
    assert len(errors) == 1
    assert window._recent_project_paths() == []


def test_discard_opens_requested_project(window, tmp_path):
    edit_project(window)
    path = tmp_path / "other.gsp"
    save_project(Project(name="Other"), path)
    window._open_project_path(str(path))
    assert window.project.name == "Other"
    assert not window._is_project_modified()
    assert not window.undo_stack.canUndo()


def test_autosave_is_separate_and_cleared_after_explicit_save(window, tmp_path):
    source = tmp_path / "source.gsp"
    window.project_path = source
    assert window.save_current_project()
    edit_project(window)
    window._autosave_recovery()
    recovered, original_path = load_recovery(window._recovery_path)
    assert recovered.name == "Edited"
    assert original_path == source.resolve()
    assert load_project(source).name != "Edited"
    assert window._is_project_modified()
    assert window.save_current_project()
    assert not window._recovery_path.exists()


def test_recovery_restores_original_path_and_remains_modified(window, tmp_path):
    source = tmp_path / "source.gsp"
    save_project(Project(name="Saved"), source)
    recovery = window._recovery_directory / "crashed.gsp"
    save_recovery(Project(name="Recovered"), recovery, source)
    window._restore_recovery(recovery)
    assert window.project.name == "Recovered"
    assert window.project_path == source
    assert window._is_project_modified()
    assert window.windowTitle().endswith(" *")
    assert load_project(source).name == "Saved"
    assert window._recovery_path.exists()
    assert not recovery.exists()
    assert window._settings.value("last_project") == str(source)


def test_untitled_recovery_requires_save_as(window):
    recovery = window._recovery_directory / "untitled.gsp"
    save_recovery(Project(name="Recovered unsaved work"), recovery, None)
    window._restore_recovery(recovery)
    assert window.project_path is None
    assert window.project.name == "Recovered unsaved work"
    assert window._is_project_modified()


def test_startup_offers_recovery_for_matching_source_only(window, tmp_path, monkeypatch):
    source = tmp_path / "source.gsp"
    other = tmp_path / "other.gsp"
    save_project(Project(name="Saved"), source)
    save_recovery(Project(name="Other recovery"), window._recovery_directory / "other.gsp", other)
    save_recovery(Project(name="Matching recovery"), window._recovery_directory / "matching.gsp", source)
    prompts = []

    def restore_prompt(parent, title, text, *args):
        prompts.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", restore_prompt)
    window._open_project_path(str(source))
    assert len(prompts) == 1
    assert "Matching recovery" in prompts[0]
    assert window.project.name == "Matching recovery"


def test_autosave_skips_active_solver_and_partial_edits(window):
    edit_project(window)
    window._solver_running = True
    window._autosave_recovery()
    assert not window._recovery_path.exists()
    window._solver_running = False
    window._edit_depth = 1
    window._autosave_recovery()
    assert not window._recovery_path.exists()
    window._edit_depth = 0


def test_corrupt_original_does_not_prevent_recovery(window, tmp_path, monkeypatch):
    source = tmp_path / "corrupt.gsp"
    source.write_text("not valid JSON", encoding="utf-8")
    recovery = window._recovery_directory / "valid.gsp"
    save_recovery(Project(name="Recovered"), recovery, source)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window._open_project_path(str(source), quiet=True)
    assert window.project.name == "Recovered"
    assert window.project_path == source
    assert window._is_project_modified()
    assert source.read_text(encoding="utf-8") == "not valid JSON"


def test_open_dialog_accepts_previous_save_backup(window, tmp_path, monkeypatch):
    backup = tmp_path / "survey.gsp.bak"
    save_project(Project(name="Previous save"), backup)

    def choose_backup(parent, title, directory, filters):
        assert "Project backup (*.gsp.bak)" in filters
        return str(backup), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose_backup)
    window.open_project()
    assert window.project.name == "Previous save"
    assert window.project_path == backup


@pytest.mark.parametrize("decision", ["cancel", "cancel_save", "save"])
def test_recent_project_activation_preserves_unsaved_protection(window, tmp_path, monkeypatch, decision):
    recent = tmp_path / "recent.gsp"
    saved = tmp_path / "current.gsp"
    save_project(Project(name="Recent"), recent)
    window._remember_recent_project(recent)
    edit_project(window)
    original = window.project
    monkeypatch.setattr(QMessageBox, "question", lambda *args: (
        QMessageBox.StandardButton.Cancel if decision == "cancel" else QMessageBox.StandardButton.Save
    ))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("" if decision == "cancel_save" else str(saved), ""))
    action = next(action for action in window.recent_projects_menu.actions() if action.data() == str(recent))
    action.trigger()
    if decision == "save":
        assert window.project.name == "Recent"
        assert load_project(saved).name == "Edited"
        assert window._recent_project_paths() == [recent, saved]
    else:
        assert window.project is original
        assert window._is_project_modified()
        assert window.undo_stack.count() == 1
        assert window._recent_project_paths() == [recent]