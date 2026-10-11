from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from geostitch.models import Project
from geostitch.solver_readiness import ReadinessIssue
from geostitch.solver_messages import readiness_copy


class ReadinessDialog(QDialog):
    affected_selected = Signal(object)

    def __init__(self, project: Project, issues: list[ReadinessIssue], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Solver diagnostics")
        self.resize(900, 520)
        self.issues = issues
        self._image_names = {image.id: image.name for image in project.images}
        self._point_names = {point.id: point.display_name for point in project.control_points}
        self._link_names = {link.id: link.name for link in project.constraints}
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.stage_filter = QComboBox()
        self.stage_filter.addItems(["Both stages", "Alignment", "Georeference"])
        self.stage_filter.currentIndexChanged.connect(self._refresh)
        toolbar.addWidget(self.stage_filter)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        toolbar.addWidget(self.summary, 1)
        layout.addLayout(toolbar)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Severity", "Stage", "Finding"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._show_details)
        layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(160)
        layout.addWidget(self.details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.select_button = QPushButton("Select affected items")
        buttons.addButton(self.select_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.select_button.clicked.connect(self._select_affected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self, *_args) -> None:
        stage = {1: "alignment", 2: "georeference"}.get(self.stage_filter.currentIndex())
        issues = [issue for issue in self.issues if stage is None or issue.stage == stage]
        issues.sort(key=lambda issue: issue.severity != "Error")
        self.table.blockSignals(True)
        self.table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            finding, _next_step = readiness_copy(issue)
            for column, text in enumerate((issue.severity, issue.stage.title(), finding)):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                item.setData(Qt.ItemDataRole.UserRole, issue)
                self.table.setItem(row, column, item)
        self.table.blockSignals(False)
        errors = sum(issue.severity == "Error" for issue in issues)
        warnings = sum(issue.severity == "Warning" for issue in issues)
        self.summary.setText(
            f"{errors} blocking issue(s) need fixing; {warnings} warning(s) may affect the result"
            if issues else "Ready to solve"
        )
        if issues:
            self.table.selectRow(0)
        self._show_details()

    def _selected_issue(self) -> ReadinessIssue | None:
        row = self.table.currentRow()
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 and self.table.item(row, 0) else None

    def _show_details(self) -> None:
        issue = self._selected_issue()
        self.select_button.setEnabled(bool(issue and (issue.image_ids or issue.point_ids or issue.constraint_ids)))
        if issue is None:
            self.details.clear()
            return
        _finding, next_step = readiness_copy(issue)
        lines = [f"Next step: {next_step}"]
        for title, identifiers, names in (("Images", issue.image_ids, self._image_names),
                                          ("Points", issue.point_ids, self._point_names),
                                          ("Links", issue.constraint_ids, self._link_names)):
            if identifiers:
                lines.extend(("", f"{title}: {', '.join(names.get(identifier, identifier) for identifier in identifiers)}"))
        self.details.setPlainText("\n".join(lines))

    def _select_affected(self) -> None:
        issue = self._selected_issue()
        if issue is not None:
            self.affected_selected.emit(issue)
            self.accept()