from PySide6.QtCore import QSize
from PySide6.QtWidgets import QToolButton, QWidget

from geostitch.icons import make_icon


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