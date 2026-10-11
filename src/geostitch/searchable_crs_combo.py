from __future__ import annotations

from PySide6.QtCore import QPoint, QRegularExpression, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QLineEdit,
    QListView,
    QVBoxLayout,
)


class SearchableCRSComboBox(QComboBox):
    """Editable CRS selector with a wide, filterable popup list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(16)
        self.lineEdit().setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._popup_selection_active = False
        self._popup_width = 620
        self._popup_height = 420

        self._popup = QFrame(self.window(), Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._popup.setObjectName("crsPickerPopup")
        self._popup.setFrameShape(QFrame.Shape.StyledPanel)
        popup_layout = QVBoxLayout(self._popup)
        popup_layout.setContentsMargins(8, 8, 8, 8)
        popup_layout.setSpacing(6)

        self.search_edit = QLineEdit(self._popup)
        self.search_edit.setObjectName("crsSearchField")
        self.search_edit.setPlaceholderText("Search CRS name or code")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("Search coordinate systems")
        popup_layout.addWidget(self.search_edit)

        self.results_view = QListView(self._popup)
        self.results_view.setObjectName("crsResultsList")
        self.results_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results_view.setUniformItemSizes(True)
        self._proxy = QSortFilterProxyModel(self._popup)
        self._proxy.setSourceModel(self.model())
        self._proxy.setFilterKeyColumn(0)
        self._proxy.setFilterRole(Qt.ItemDataRole.DisplayRole)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.results_view.setModel(self._proxy)
        popup_layout.addWidget(self.results_view, 1)

        self.search_edit.textChanged.connect(self._filter_results)
        self.search_edit.returnPressed.connect(self._activate_current_result)
        self.results_view.clicked.connect(self._activate_result)
        self.results_view.activated.connect(self._activate_result)

    def set_compact_text(self, text: str) -> None:
        if self.currentText() != text:
            self.setEditText(text)
        self.lineEdit().deselect()
        self.lineEdit().setCursorPosition(0)

    @property
    def popup_selection_active(self) -> bool:
        return self._popup_selection_active

    def showPopup(self) -> None:
        self.search_edit.clear()
        self._filter_results("")
        current = self.model().index(self.currentIndex(), 0) if self.currentIndex() >= 0 else None
        if current is not None:
            self.results_view.setCurrentIndex(self._proxy.mapFromSource(current))
        else:
            self.results_view.setCurrentIndex(self._proxy.index(0, 0))

        screen = self.screen()
        available = screen.availableGeometry() if screen is not None else self.geometry()
        target_width = min(max(self.width(), self._popup_width), available.width() - 16)
        target_height = min(self._popup_height, available.height() - 16)
        below = self.mapToGlobal(QPoint(0, self.height()))
        above = self.mapToGlobal(QPoint(0, 0))
        x = min(max(below.x(), available.left() + 8), available.right() - target_width - 7)
        y = below.y()
        if y + target_height > available.bottom() and above.y() - target_height >= available.top():
            y = above.y() - target_height
        self._popup.setGeometry(x, y, target_width, target_height)
        self._popup.show()
        self.search_edit.setFocus(Qt.FocusReason.PopupFocusReason)

    def hidePopup(self) -> None:
        self._popup.hide()

    def _filter_results(self, text: str) -> None:
        expression = QRegularExpression.escape(text.strip())
        self._proxy.setFilterRegularExpression(expression)
        if self._proxy.rowCount() > 0:
            self.results_view.setCurrentIndex(self._proxy.index(0, 0))
        else:
            self.results_view.setCurrentIndex(self._proxy.index(-1, 0))

    def _activate_current_result(self) -> None:
        index = self.results_view.currentIndex()
        if not index.isValid() and self._proxy.rowCount() > 0:
            index = self._proxy.index(0, 0)
        if index.isValid():
            self._activate_result(index)

    def _activate_result(self, proxy_index) -> None:
        if not proxy_index.isValid():
            return
        source_index = self._proxy.mapToSource(proxy_index)
        source_row = source_index.row()
        self._popup_selection_active = True
        try:
            self.setCurrentIndex(source_row)
            self.hidePopup()
            self.set_compact_text(str(self.itemData(source_row)))
            self.activated.emit(source_row)
        finally:
            self._popup_selection_active = False
