from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout,
)

from geostitch.icons import make_icon
from geostitch.image_repair import find_replacements
from geostitch.models import ImageRecord


class _SearchSignals(QObject):
    finished = Signal(object, str)


class _SearchJob(QRunnable):
    def __init__(self, images: list[ImageRecord], directory: Path):
        super().__init__()
        self.images = images
        self.directory = directory
        self.cancelled = Event()
        self.signals = _SearchSignals()

    def run(self) -> None:
        try:
            matches = find_replacements(self.images, self.directory, cancelled=self.cancelled.is_set)
            error = ""
        except (OSError, InterruptedError) as exception:
            matches, error = {}, str(exception)
        self.signals.finished.emit(matches, error)


class ImageRepairDialog(QDialog):
    def __init__(self, images: list[ImageRecord], directory: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Repair missing images")
        self.resize(1000, 440)
        self.images = images
        self.directory = directory
        self._job: _SearchJob | None = None
        self._choices: dict[str, QComboBox] = {}
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.search_button = QPushButton(make_icon("open"), "Search folder...", self)
        self.search_button.clicked.connect(self._search_folder)
        toolbar.addWidget(self.search_button)
        self.status = QLabel(f"{len(images)} missing image(s)")
        toolbar.addWidget(self.status, 1)
        layout.addLayout(toolbar)
        self.table = QTableWidget(len(images), 4)
        self.table.setHorizontalHeaderLabels(["Image", "Missing file", "Replacement", "Locate"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        for row, image in enumerate(images):
            self.table.setItem(row, 0, QTableWidgetItem(image.name))
            original = QTableWidgetItem(image.path)
            original.setToolTip(image.path)
            self.table.setItem(row, 1, original)
            choice = QComboBox()
            choice.setMinimumWidth(120)
            choice.addItem("Unresolved", None)
            choice.currentIndexChanged.connect(self._update_apply)
            self.table.setCellWidget(row, 2, choice)
            self._choices[image.id] = choice
            locate = QToolButton()
            locate.setIcon(make_icon("open"))
            locate.setToolTip(f"Locate {image.name}")
            locate.setAccessibleName(f"Locate {image.name}")
            locate.clicked.connect(lambda _checked=False, image_id=image.id: self._locate(image_id))
            self.table.setCellWidget(row, 3, locate)
        layout.addWidget(self.table)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update_apply()

    def replacements(self) -> dict[str, Path]:
        return {
            image_id: Path(choice.currentData()) for image_id, choice in self._choices.items()
            if choice.currentData() is not None
        }

    def _update_apply(self, *_args) -> None:
        if hasattr(self, "buttons"):
            self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(
                self._job is None and bool(self.replacements())
            )
        for choice in self._choices.values():
            choice.setToolTip(str(choice.currentData() or ""))

    def _locate(self, image_id: str) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Locate image", self.directory, "Images (*);;All files (*)")
        if not path:
            return
        self.directory = str(Path(path).parent)
        choice = self._choices[image_id]
        choice.addItem(str(Path(path).resolve()), str(Path(path).resolve()))
        choice.setCurrentIndex(choice.count() - 1)

    def _search_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Search for missing images", self.directory)
        if not directory:
            return
        self.directory = directory
        self._job = _SearchJob(self.images, Path(directory))
        self._job.signals.finished.connect(self._search_finished)
        self.search_button.setEnabled(False)
        self.table.setEnabled(False)
        self.status.setText("Searching...")
        self._update_apply()
        QThreadPool.globalInstance().start(self._job)

    def _search_finished(self, matches: dict[str, list[Path]], error: str) -> None:
        if self._job is None:
            return
        self._job = None
        self.search_button.setEnabled(True)
        self.table.setEnabled(True)
        if error:
            self.status.setText(f"Search failed: {error}")
            self._update_apply()
            return
        ambiguous = 0
        for image in self.images:
            choice = self._choices[image.id]
            previous = choice.currentData()
            choice.blockSignals(True)
            choice.clear()
            paths = matches.get(image.id, [])
            choice.addItem("Choose a match" if len(paths) > 1 else "Unresolved", None)
            for path in paths:
                choice.addItem(str(path), str(path))
            if previous is not None:
                index = choice.findData(previous)
                if index < 0:
                    choice.addItem(previous, previous)
                    index = choice.count() - 1
                choice.setCurrentIndex(index)
            elif len(paths) == 1:
                choice.setCurrentIndex(1)
            if len(paths) > 1:
                ambiguous += 1
            choice.blockSignals(False)
        self.status.setText(f"{len(self.replacements())} ready; {ambiguous} ambiguous")
        self._update_apply()

    def done(self, result: int) -> None:
        if self._job is not None:
            self._job.cancelled.set()
            self._job = None
        super().done(result)