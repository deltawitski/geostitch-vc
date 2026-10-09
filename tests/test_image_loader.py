import os
import threading
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from geostitch.image_loader import ImageLoader


def wait_for_results(app, results):
    deadline = monotonic() + 5
    while not results and monotonic() < deadline:
        app.processEvents()
    assert results


def test_image_loader_decodes_on_worker_and_delivers_on_ui_thread():
    app = QApplication.instance() or QApplication([])
    ui_thread = threading.get_ident()
    decoded_threads = []
    results = []

    def decode(path):
        decoded_threads.append(threading.get_ident())
        return QImage(10, 20, QImage.Format.Format_RGB32), ""

    loader = ImageLoader(decode)
    loader.loaded.connect(lambda *args: results.append((threading.get_ident(), args)))
    loader.request((1, "image"), "image.png")
    wait_for_results(app, results)
    assert decoded_threads[0] != ui_thread
    assert results[0][0] == ui_thread
    assert results[0][1][0] == (1, "image")
    assert results[0][1][1].size().width() == 10
    assert results[0][1][2] == ""


def test_image_loader_reports_decode_errors():
    app = QApplication.instance() or QApplication([])
    results = []

    def decode(path):
        raise OSError("missing image")

    loader = ImageLoader(decode)
    loader.loaded.connect(lambda *args: results.append(args))
    loader.request((1, "image"), "missing.png")
    wait_for_results(app, results)
    assert results[0][1].isNull()
    assert results[0][2] == "missing image"