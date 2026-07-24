from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class LoadingOverlay(QWidget):
    """
    A semi-transparent overlay widget with a loading spinner and message.
    Can be shown over any parent widget to indicate loading state.
    """

    finished = Signal()

    def __init__(self, parent=None, message="Loading..."):
        super().__init__(parent)
        self.message = message
        self.angle = 0

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.label = QLabel(self.message)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("""
            QLabel {
                color: white;
                background-color: rgba(0, 0, 0, 200);
                padding: 20px 30px;
                border-radius: 10px;
                font-size: 14px;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.label)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)

        self.hide()

    def showEvent(self, event):
        """Start animation when shown."""
        super().showEvent(event)
        self.timer.start(50)  # Update every 50ms

    def hideEvent(self, event):
        """Stop animation when hidden."""
        super().hideEvent(event)
        self.timer.stop()
        self.finished.emit()

    def rotate(self):
        """Rotate the spinner."""
        self.angle = (self.angle + 10) % 360
        self.update()

    def paintEvent(self, event):
        """Paint the semi-transparent background and spinner."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        center_x = self.width() // 2
        center_y = self.height() // 2 - 40  # Above the label
        radius = 20

        painter.setPen(Qt.PenStyle.NoPen)

        for i in range(8):
            angle_offset = i * 45
            alpha = int(255 * (i + 1) / 8)
            color = QColor(255, 255, 255, alpha)
            painter.setBrush(color)

            angle_rad = (self.angle + angle_offset) * 3.14159 / 180
            x = center_x + radius * 0.7 * (i / 8) * painter.fontMetrics().height() / 20
            y = center_y + radius * 0.7 * (i / 8) * painter.fontMetrics().height() / 20

            import math

            x = center_x + int(radius * math.cos(angle_rad))
            y = center_y + int(radius * math.sin(angle_rad))

            dot_size = 6
            painter.drawEllipse(
                x - dot_size // 2, y - dot_size // 2, dot_size, dot_size
            )

    def set_message(self, message: str):
        """Update the loading message."""
        self.message = message
        self.label.setText(message)

    def show_loading(self):
        """Show the loading overlay."""
        if self.parent():
            # Resize to cover parent
            self.resize(self.parent().size())  # type: ignore
            self.raise_()
        self.show()

    def hide_loading(self):
        """Hide the loading overlay."""
        self.hide()

    def resizeEvent(self, event):
        """Keep overlay covering parent when resized."""
        super().resizeEvent(event)
        if self.parent() and self.isVisible():
            self.resize(self.parent().size())  # type: ignore
