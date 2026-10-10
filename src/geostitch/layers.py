from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem

from geostitch.alignment_feedback import residual_color
from geostitch.icons import kind_icon

ID_ROLE = Qt.ItemDataRole.UserRole
KIND_ROLE = Qt.ItemDataRole.UserRole + 1
IMAGE_KIND = "image"
POINT_KIND = "point"
VECTOR_KIND = "vector"
CONSTRAINT_KIND = "link"


class LayerItem(QTreeWidgetItem):
    """Tree row for an image or control point; `data(role)` defaults to the first column."""

    def __init__(self, kind: str, item_id: str, text: str):
        super().__init__([text, "", "", "", ""])
        self.setData(0, ID_ROLE, item_id)
        self.setData(0, KIND_ROLE, kind)
        self.setIcon(0, kind_icon({VECTOR_KIND: "geojson"}.get(kind, kind)))
        if kind in (POINT_KIND, CONSTRAINT_KIND):
            self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        # Rows stay drop-enabled so Qt accepts drops over their middle; `dropEvent` resolves above/below itself.

    def data(self, column, role=None):
        if role is None:
            column, role = 0, column
        return super().data(column, role)

    @property
    def kind(self) -> str:
        return self.data(0, KIND_ROLE)

    @property
    def item_id(self) -> str:
        return self.data(0, ID_ROLE)

    def set_alignment_error(self, value: float | None, tolerance: float, current: bool, description: str) -> None:
        column = LayerTree.ERROR_COLUMN
        self.setTextAlignment(column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setText(column, "--" if value is None else f"{value:.3f}")
        color = QColor(residual_color(value, tolerance) if value is not None and current else "#7a848a")
        self.setForeground(column, QBrush(color))
        if value is None:
            self.setIcon(column, QIcon())
            self.setToolTip(column, "No alignment residual available for this item.")
            return
        swatch = QPixmap(4, 12)
        swatch.fill(color)
        self.setIcon(column, QIcon(swatch))
        state = "Current solve" if current else "Outdated solve: observations, links or rest transforms changed"
        self.setToolTip(column, (
            f"{description}: {value:.6f} scene units\n{state}\n"
            f"Green <= {tolerance:g}; amber <= {tolerance * 2:g}; red > {tolerance * 2:g} scene units.\n"
            "Alignment fit, not independent geographic accuracy."
        ))


class LayerTree(QTreeWidget):
    """Images and GeoJSON layers as drag-reorderable top-level rows (top row is drawn in front), images holding their control points."""

    visibility_toggled = Signal(object, int)
    lock_toggled = Signal(object)
    layers_reordered = Signal(list)
    EYE_COLUMN = 1
    MAP_COLUMN = 2
    LOCK_COLUMN = 3
    ERROR_COLUMN = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._control_click_active = False
        self._filter_active = False
        self.setColumnCount(5)
        self.setHeaderLabels(["Layer", "", "", "", "Error"])
        self.setHeaderHidden(False)
        self.setIndentation(16)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.EYE_COLUMN, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.MAP_COLUMN, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.LOCK_COLUMN, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.ERROR_COLUMN, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(self.EYE_COLUMN, 32)
        self.setColumnWidth(self.MAP_COLUMN, 32)
        self.setColumnWidth(self.LOCK_COLUMN, 32)
        self.setColumnWidth(self.ERROR_COLUMN, 80)
        self.headerItem().setToolTip(self.ERROR_COLUMN, "Alignment residuals in scene units; image RMS, point maximum, link distance.")

    def _image_rows(self) -> list[LayerItem]:
        """Image rows from the bottom layer up, skipping GeoJSON rows."""
        rows = [self.topLevelItem(row) for row in range(self.topLevelItemCount())]
        return [item for item in reversed(rows) if item.kind == IMAGE_KIND]

    def count(self) -> int:
        return len(self._image_rows())

    def item(self, row: int) -> LayerItem | None:
        """Image row in project order (bottom layer first), unlike the on-screen order."""
        rows = self._image_rows()
        return rows[row] if 0 <= row < len(rows) else None

    def selected_rows(self) -> list[LayerItem]:
        return [self.itemFromIndex(index) for index in self.selectionModel().selectedRows(0)]

    def set_filter_active(self, active: bool) -> None:
        self._filter_active = active
        self.setDragEnabled(not active)
        self.setAcceptDrops(not active)

    def selectedItems(self) -> list[LayerItem]:
        """Selected image rows only; use `selected_point_items` for control points."""
        return [item for item in self.selected_rows() if item.kind == IMAGE_KIND]

    def selected_point_items(self) -> list[LayerItem]:
        return [item for item in self.selected_rows() if item.kind == POINT_KIND]

    def selected_vector_items(self) -> list[LayerItem]:
        return [item for item in self.selected_rows() if item.kind == VECTOR_KIND]

    def selected_constraint_items(self) -> list[LayerItem]:
        return [item for item in self.selected_rows() if item.kind == CONSTRAINT_KIND]

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._control_click_active = False
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and index.column() in (self.EYE_COLUMN, self.MAP_COLUMN, self.LOCK_COLUMN):
                self._control_click_active = True
                item = self.itemFromIndex(index)
                if index.column() == self.LOCK_COLUMN:
                    if item.kind in (IMAGE_KIND, POINT_KIND):
                        self.lock_toggled.emit(item)
                else:
                    if item.kind != CONSTRAINT_KIND:
                        self.visibility_toggled.emit(item, index.column())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._control_click_active:
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._control_click_active:
            self._control_click_active = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and index.column() in (self.EYE_COLUMN, self.MAP_COLUMN, self.LOCK_COLUMN):
                self.mousePressEvent(event)
                return
        super().mouseDoubleClickEvent(event)

    def dragMoveEvent(self, event) -> None:
        super().dragMoveEvent(event)
        # Any position over a row is a valid reorder target; `dropEvent` resolves above or below.
        if event.source() is self and not event.isAccepted():
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()

    def dropEvent(self, event) -> None:
        if self._filter_active:
            event.ignore()
            return
        # Reordering is applied by the owner; ignoring the action stops Qt from removing the dragged rows.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        order = [self.topLevelItem(row).item_id for row in range(self.topLevelItemCount())]
        moved = sorted((item.item_id for item in [*self.selectedItems(), *self.selected_vector_items()]), key=order.index)
        if not moved:
            return
        position = event.position().toPoint()
        target = self.itemAt(position)
        if target is None:
            row = len(order)
        else:
            layer = target
            while layer.parent() is not None:
                layer = layer.parent()
            row = order.index(layer.item_id)
            below = target is not layer or position.y() > self.visualItemRect(layer).center().y()
            row += 1 if below else 0
        row -= sum(1 for item_id in order[:row] if item_id in moved)
        remaining = [item_id for item_id in order if item_id not in moved]
        new_order = remaining[:row] + moved + remaining[row:]
        if new_order != order:
            self.layers_reordered.emit(new_order)
