from collections.abc import Callable

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QSplitter, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

from geostitch.image_view import ImageViewer
from geostitch.icons import make_icon
from geostitch.models import Project


class _ResidualItem(QTableWidgetItem):
    def __init__(self, residual: float | None):
        super().__init__(f"{residual:.3f}" if residual is not None else "--")
        self.residual = residual

    def __lt__(self, other) -> bool:
        if isinstance(other, _ResidualItem):
            return (self.residual if self.residual is not None else -1) < (other.residual if other.residual is not None else -1)
        return super().__lt__(other)


class LinkReviewPanel(QWidget):
    link_selected = Signal(str)
    point_observation_interaction_started = Signal(str, str)
    point_observation_changed = Signal(str, str, float, float)
    point_observation_interaction_finished = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project = Project()
        self._images = {}
        self._points = {}
        self._residuals: dict[str, float] = {}
        self._current = False
        self._pixmap: Callable | None = None
        self._image_size: Callable | None = None
        self._selected: str | None = None
        self._displayed: tuple | None = None
        self._table_signature: tuple | None = None
        self._syncing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search links, images, points or labels")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._rebuild_table)
        toolbar.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItems(["All links", "Above threshold", "No residual"])
        self.filter.currentIndexChanged.connect(self._rebuild_table)
        toolbar.addWidget(self.filter)
        self.threshold = QDoubleSpinBox()
        self.threshold.setDecimals(3)
        self.threshold.setRange(0, 1_000_000_000)
        self.threshold.setValue(0.5)
        self.threshold.setSuffix(" scene units")
        self.threshold.setToolTip("Minimum residual for Above threshold")
        self.threshold.valueChanged.connect(self._rebuild_table)
        toolbar.addWidget(self.threshold)
        layout.addLayout(toolbar)
        navigation = QHBoxLayout()
        self.previous = self._button("Previous link", "SP_ArrowBack", lambda: self._step(-1))
        self.next = self._button("Next link", "SP_ArrowForward", lambda: self._step(1))
        self.center = self._button("Center both endpoints", "SP_ArrowUp", self.center_endpoints)
        self.center.setIcon(make_icon("fit"))
        for button in (self.previous, self.next, self.center):
            navigation.addWidget(button)
        self.summary = QLabel("No links")
        navigation.addWidget(self.summary, 1)
        layout.addLayout(navigation)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Link", "First endpoint", "Second endpoint", "Residual"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        self.table.sortItems(3, Qt.SortOrder.DescendingOrder)
        self.table.itemSelectionChanged.connect(self._row_selected)
        split.addWidget(self.table)
        viewers = QSplitter(Qt.Orientation.Horizontal)
        self.views = []
        self.labels = []
        for _side in range(2):
            pane = QWidget()
            pane_layout = QVBoxLayout(pane)
            pane_layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel("No endpoint")
            label.setWordWrap(True)
            pane_layout.addWidget(label)
            view = ImageViewer()
            view.view_controls.hide()
            view._placeholder_label.setText("No endpoint")
            view.point_observation_interaction_started.connect(self.point_observation_interaction_started.emit)
            view.point_observation_changed.connect(self.point_observation_changed.emit)
            view.point_observation_interaction_finished.connect(self.point_observation_interaction_finished.emit)
            pane_layout.addWidget(view, 1)
            viewers.addWidget(pane)
            self.views.append(view)
            self.labels.append(label)
        split.addWidget(viewers)
        split.setSizes([150, 400])
        layout.addWidget(split, 1)
        self._update_navigation()

    def closeEvent(self, event) -> None:
        self.table.blockSignals(True)
        for view in self.views:
            view._finish_point_drag()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        self.table.blockSignals(False)
        super().showEvent(event)

    def _button(self, tooltip: str, icon: str, callback) -> QToolButton:
        from PySide6.QtWidgets import QStyle

        button = QToolButton()
        button.setIcon(self.style().standardIcon(getattr(QStyle.StandardPixmap, icon)))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFixedSize(30, 30)
        button.clicked.connect(callback)
        return button

    def set_project(self, project: Project, residuals: dict[str, float], current: bool, pixmap: Callable,
                    image_size: Callable | None = None) -> None:
        self._project, self._residuals, self._current, self._pixmap = project, residuals, current, pixmap
        self._image_size = image_size
        self._images = {image.id: image for image in project.images}
        self._points = {point.id: point for point in project.control_points}
        signature = tuple(
            (link.id, link.name, link.kind, *self._contexts(link), residuals.get(link.id))
            for link in project.constraints
        ) + (current,)
        if signature != self._table_signature:
            self._table_signature = signature
            self._rebuild_table()
        self._refresh_endpoints()

    def _contexts(self, link) -> tuple[str, str]:
        contexts = []
        for point_id in (link.first_point_id, link.second_point_id):
            point = self._points.get(point_id)
            image = self._images.get(point.image_id) if point else None
            contexts.append(
                f"{image.name if image else 'Missing image'} / {point.display_name} {point.name} {point.label or ''}"
                if point else "Missing point"
            )
        return contexts[0], contexts[1]

    def _rebuild_table(self, *_args) -> None:
        if not hasattr(self, "table"):
            return
        self._syncing = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        query = self.search.text().strip().casefold()
        for link in self._project.constraints:
            contexts = self._contexts(link)
            residual = self._residuals.get(link.id)
            if query and query not in " ".join((link.name, link.kind, *contexts)).casefold():
                continue
            if self.filter.currentIndex() == 1 and (residual is None or residual <= self.threshold.value()):
                continue
            if self.filter.currentIndex() == 2 and residual is not None:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, text in enumerate((link.name, *contexts)):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, link.id)
                item.setToolTip(text)
                self.table.setItem(row, column, item)
            item = _ResidualItem(residual)
            item.setToolTip("No residual" if residual is None else f"{residual:.4f} scene units ({'Current' if self._current else 'Outdated'})")
            if not self._current:
                item.setForeground(QBrush(QColor("#7a848a")))
            self.table.setItem(row, 3, item)
        self.table.setSortingEnabled(True)
        self._syncing = False
        self.select_link(self._selected)
        self.summary.setText(
            f"{self.table.rowCount()} of {len(self._project.constraints)} links | "
            f"{'Current residuals' if self._current else 'Outdated residuals' if self._residuals else 'No residual report'}"
        )
        self._update_navigation()

    def visible_link_ids(self) -> list[str]:
        return [self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(self.table.rowCount())]

    def select_link(self, link_id: str | None) -> None:
        if self._selected != link_id:
            for view in self.views:
                view._finish_point_drag()
        self._selected = link_id
        self._syncing = True
        self.table.clearSelection()
        self.table.setCurrentItem(None)
        for row, candidate in enumerate(self.visible_link_ids()):
            if candidate == link_id:
                self.table.selectRow(row)
                break
        self._syncing = False
        self._refresh_endpoints()
        self._update_navigation()

    def _row_selected(self) -> None:
        if self._syncing or not self.table.selectedItems():
            return
        link_id = self.table.item(self.table.currentRow(), 0).data(Qt.ItemDataRole.UserRole)
        self.select_link(link_id)
        self.link_selected.emit(link_id)

    def _step(self, delta: int) -> None:
        ids = self.visible_link_ids()
        if not ids:
            return
        index = ids.index(self._selected) if self._selected in ids else (-1 if delta > 0 else len(ids))
        index = max(0, min(len(ids) - 1, index + delta))
        self.select_link(ids[index])
        self.link_selected.emit(ids[index])

    def _update_navigation(self) -> None:
        ids = self.visible_link_ids()
        index = ids.index(self._selected) if self._selected in ids else None
        self.previous.setEnabled(bool(ids) and (index is None or index > 0))
        self.next.setEnabled(bool(ids) and (index is None or index < len(ids) - 1))
        self.center.setEnabled(any(link.id == self._selected for link in self._project.constraints))

    def _refresh_endpoints(self) -> None:
        link = next((link for link in self._project.constraints if link.id == self._selected), None)
        points = [self._points.get(point_id) for point_id in (link.first_point_id, link.second_point_id)] if link else [None, None]
        signature = []
        endpoints = []
        for point in points:
            image = self._images.get(point.image_id) if point else None
            observation = point.observations[0] if point and point.observations else None
            pixmap, error = self._pixmap(image) if image and self._pixmap else (None, "")
            size = self._image_size(image, pixmap) if image and self._image_size else None
            endpoints.append((point, image, observation, pixmap, error, size))
            signature.append((point.id if point else None, image.path if image else None,
                              observation.x if observation else None, observation.y if observation else None,
                              observation.locked if observation else None,
                              (image.name, point.display_name) if image and point else None,
                              error,
                              (size.width(), size.height()) if size is not None else None,
                              pixmap.cacheKey() if pixmap is not None else None))
        signature = tuple(signature)
        if signature == self._displayed:
            return
        previous = self._displayed
        self._displayed = signature
        for index, (point, image, observation, pixmap, error, size) in enumerate(endpoints):
            if self.views[index]._dragged_point_id is not None:
                continue
            valid = image is not None and observation is not None
            self.labels[index].setText(f"{image.name} / {point.display_name}" if valid else "Missing endpoint" if link else "No endpoint")
            self.views[index]._placeholder_label.setText(
                f"Image unavailable: {error}" if error else "Loading image..."
                if valid and (pixmap is None or pixmap.isNull()) else "Missing endpoint" if link else "No endpoint"
            )
            self.views[index].set_image(
                image.id if valid else None, pixmap,
                [(point.id, point.display_name, observation.x, observation.y)] if valid else [],
                [], "inside", False, point.id if valid else None,
                {point.id} if valid and observation.locked else set(),
                source_size=size,
            )
            if valid and (previous is None or previous[index][0] != point.id or previous[index][-1] != signature[index][-1]):
                self.views[index].centerOn(QPointF(observation.x, observation.y))

    def center_endpoints(self) -> None:
        for view in self.views:
            for marker in view._point_items.values():
                view.centerOn(marker.pos())

    def clear_images(self) -> None:
        self._displayed = None
        for view in self.views:
            view.set_image(None, None, [], [], "inside", False)