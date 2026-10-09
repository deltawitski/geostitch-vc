from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem

from geostitch.icons import kind_icon

ID_ROLE = Qt.ItemDataRole.UserRole
KIND_ROLE = Qt.ItemDataRole.UserRole + 1
IMAGE_KIND = "image"
POINT_KIND = "point"
VECTOR_KIND = "vector"


class LayerItem(QTreeWidgetItem):
    """Tree row for an image or control point; `data(role)` defaults to the first column."""

    def __init__(self, kind: str, item_id: str, text: str):
        super().__init__([text, "", ""])
        self.setData(0, ID_ROLE, item_id)
        self.setData(0, KIND_ROLE, kind)
        self.setIcon(0, kind_icon({VECTOR_KIND: "geojson"}.get(kind, kind)))
        if kind == POINT_KIND:
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


class LayerTree(QTreeWidget):
    """Images and GeoJSON layers as drag-reorderable top-level rows (top row is drawn in front), images holding their control points."""

    visibility_toggled = Signal(object, int)
    layers_reordered = Signal(list)
    EYE_COLUMN = 1
    MAP_COLUMN = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHeaderHidden(True)
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
        self.setColumnWidth(self.EYE_COLUMN, 32)
        self.setColumnWidth(self.MAP_COLUMN, 32)

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

    def selectedItems(self) -> list[LayerItem]:
        """Selected image rows only; use `selected_point_items` for control points."""
        return [item for item in super().selectedItems() if item.kind == IMAGE_KIND]

    def selected_point_items(self) -> list[LayerItem]:
        return [item for item in super().selectedItems() if item.kind == POINT_KIND]

    def selected_vector_items(self) -> list[LayerItem]:
        return [item for item in super().selectedItems() if item.kind == VECTOR_KIND]

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and index.column() in (self.EYE_COLUMN, self.MAP_COLUMN):
                self.visibility_toggled.emit(self.itemFromIndex(index), index.column())
                event.accept()
                return
        super().mousePressEvent(event)

    def dragMoveEvent(self, event) -> None:
        super().dragMoveEvent(event)
        # Any position over a row is a valid reorder target; `dropEvent` resolves above or below.
        if event.source() is self and not event.isAccepted():
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()

    def dropEvent(self, event) -> None:
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
            layer = target.parent() or target
            row = order.index(layer.item_id)
            below = target is not layer or position.y() > self.visualItemRect(layer).center().y()
            row += 1 if below else 0
        row -= sum(1 for item_id in order[:row] if item_id in moved)
        remaining = [item_id for item_id in order if item_id not in moved]
        new_order = remaining[:row] + moved + remaining[row:]
        if new_order != order:
            self.layers_reordered.emit(new_order)
