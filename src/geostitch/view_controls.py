from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QWidget

from geostitch.icons import make_icon


class ActiveToolHint(QFrame):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("activeToolHint")
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.escape_key_label = QLabel("Esc")
        self.escape_key_label.setObjectName("activeToolEscapeKey")
        self.cancel_text_label = QLabel("to cancel")
        self.cancel_text_label.setAccessibleName("Press Escape to cancel the active tool")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 5, 9, 5)
        layout.setSpacing(8)
        layout.addWidget(self.message_label, 1)
        layout.addWidget(self.escape_key_label)
        layout.addWidget(self.cancel_text_label)
        self.setStyleSheet(
            "QFrame#activeToolHint { background: rgba(25, 35, 33, 238); border: 1px solid #aebbb5; border-radius: 4px; }"
            "QFrame#activeToolHint QLabel { color: #ffffff; background: transparent; }"
            "QLabel#activeToolEscapeKey { background: #354640; border: 1px solid #82948b; border-radius: 3px; "
            "padding: 2px 5px; font-weight: 600; }"
        )
        self.hide()

    def set_hint(self, message: str) -> None:
        if not message:
            self.hide()
            return
        self.message_label.setText(message)
        self.setMaximumWidth(max(160, self.parentWidget().width() - 16))
        self.adjustSize()
        self.move(8, 8)
        self.show()
        self.raise_()


def overlay_toggle(parent: QWidget, icon: str, tooltip: str, *, checkable: bool = True) -> QToolButton:
    button = QToolButton(parent)
    button.setCheckable(checkable)
    button.setIcon(make_icon(icon))
    button.setIconSize(QSize(22, 22))
    button.setFixedSize(36, 36)
    button.setAccessibleName(tooltip.split("\n", 1)[0])
    button.setToolTip(tooltip)
    button.setStyleSheet(
        "QToolButton { background: palette(button); border: 1px solid palette(mid); border-radius: 4px; padding: 4px; }"
        "QToolButton:hover { border-color: palette(highlight); }"
        "QToolButton:checked { background: palette(highlight); border-color: palette(highlight); }"
    )
    return button