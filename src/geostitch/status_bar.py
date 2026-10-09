from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QProgressBar, QSizePolicy, QStatusBar, QWidget


class ScrollingText(QWidget):
    """Single-line text that, when clipped, periodically scrolls to reveal the rest and back."""

    TICK_MS = 33
    START_PAUSE_MS = 2500
    END_PAUSE_MS = 1500
    SPEED_PIXELS_PER_SECOND = 45.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._offset = 0.0
        self._phase = "idle"
        self._direction = 1
        self._pause_ticks = 0
        self.setMinimumWidth(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self._restart()
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(self.fontMetrics().height() + 4)
        return size

    def _overflow(self) -> int:
        return max(0, self.fontMetrics().horizontalAdvance(self._text) - self.width() + 8)

    def _restart(self) -> None:
        self._offset = 0.0
        self._direction = 1
        self._pause_ticks = self.START_PAUSE_MS // self.TICK_MS
        if self._overflow() > 0:
            self._phase = "pause"
            self._timer.start()
        else:
            self._phase = "idle"
            self._timer.stop()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._restart()

    def _tick(self) -> None:
        overflow = self._overflow()
        if overflow <= 0:
            self._restart()
            return
        if self._phase == "pause":
            self._pause_ticks -= 1
            if self._pause_ticks <= 0:
                self._phase = "scroll"
            return
        self._offset += self._direction * self.SPEED_PIXELS_PER_SECOND * self.TICK_MS / 1000.0
        end = self._direction > 0 and self._offset >= overflow
        start = self._direction < 0 and self._offset <= 0
        if end or start:
            self._offset = float(overflow) if end else 0.0
            self._direction = -self._direction
            self._phase = "pause"
            self._pause_ticks = (self.END_PAUSE_MS if end else self.START_PAUSE_MS) // self.TICK_MS
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        painter.drawText(
            QRect(-int(self._offset), 0, self.fontMetrics().horizontalAdvance(self._text) + 8, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._text,
        )


class ScrollingStatusBar(QStatusBar):
    """Status bar whose message scrolls instead of being clipped by the widgets on its right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._message = ScrollingText(self)
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self.clearMessage)
        self.addPermanentWidget(self._message, 1)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFixedWidth(180)
        self.progress_bar.setMaximumHeight(14)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        self.addPermanentWidget(self.progress_bar)

    def begin_progress(self, maximum: int = 100) -> None:
        """Show the bar; maximum 0 makes it an indeterminate busy indicator."""
        self.progress_bar.setRange(0, maximum)
        self.progress_bar.setValue(0)
        self.progress_bar.show()

    def set_progress(self, value: int, maximum: int | None = None) -> None:
        if maximum is not None:
            self.progress_bar.setMaximum(maximum)
        self.progress_bar.setValue(value)

    def end_progress(self) -> None:
        self.progress_bar.hide()

    def showMessage(self, text: str, timeout: int = 0) -> None:
        self._message.set_text(text)
        if timeout > 0:
            self._clear_timer.start(timeout)
        else:
            self._clear_timer.stop()
        self.messageChanged.emit(text)

    def clearMessage(self) -> None:
        self._message.set_text("")
        self.messageChanged.emit("")

    def currentMessage(self) -> str:
        return self._message.text()
