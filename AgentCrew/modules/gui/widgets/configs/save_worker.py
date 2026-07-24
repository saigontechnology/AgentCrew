from collections.abc import Callable

from PySide6.QtCore import QThread, Signal


class SaveWorker(QThread):
    """
    Worker thread for handling save operations to prevent UI freezing.
    """

    finished = Signal()
    error = Signal(str)
    progress = Signal(str)

    def __init__(self, save_function: Callable, *args, **kwargs):
        super().__init__()
        self.save_function = save_function
        self.args = args
        self.kwargs = kwargs

    def run(self):
        """Execute the save operation in a separate thread."""
        try:
            self.save_function(*self.args, **self.kwargs)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
