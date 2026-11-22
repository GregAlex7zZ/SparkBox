"""Regressions for the 2026-10-26 polish round: amber buttons for actions that start a calculation, smoother text
(FreeType on Windows, slight hinting), Render All Points drawn as a data layer under the chart's own axes and saved at
300 dpi, table titles measured with the font they are drawn with, themed list views and the "DCIR corrected" pill."""
import os
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PySide6.QtCore import QEventLoop, QRectF, QTimer
from PySide6.QtGui import QFont, QImage, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication, QHeaderView, QTableView

import run
from app.backend import FlatStyle
from app.demo_backend import DemoBackend
from app.flat_render import data_layer
from app.fonts import load_fonts
from app.main_window import MainWindow
from app.state import Section
from app.theme import ThemeManager
from app.tokens import DARK, LIGHT
from app.widgets.chart import ChartWidget
from app.widgets.flat_layer import EXPORT_DPI
from app.widgets.wrap_header import install


def pump(ms=30):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(cond, timeout=20.0):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        pump()
    if not cond():
        raise AssertionError("timeout")


def contrast(a: str, b: str) -> float:
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setOrganizationName("gregorithm-test")
        load_fonts()


class TextRendering(Base):
    def test_windows_uses_freetype_unless_a_platform_is_chosen(self):
        env = os.environ.pop("QT_QPA_PLATFORM", None)
        try:
            args = run.qt_args(["run.py", "--demo"])
            if sys.platform == "win32":
                self.assertEqual(args[-2:], ["-platform", "windows:fontengine=freetype"])
            self.assertNotIn("--demo", args)
            self.assertEqual(run.qt_args(["run.py", "-platform", "offscreen"]), ["run.py", "-platform", "offscreen"])
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            self.assertNotIn("windows:fontengine=freetype", run.qt_args(["run.py"]))
        finally:
            if env is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = env

    def test_ui_and_chart_fonts_use_slight_hinting(self):
        theme = ThemeManager(self.app)
        theme.apply(force=True)
        self.assertEqual(self.app.font().hintingPreference(), QFont.HintingPreference.PreferVerticalHinting)
        from app.widgets.chart import _font
        self.assertEqual(_font(11).hintingPreference(), QFont.HintingPreference.PreferVerticalHinting)


class Amber(Base):
    def test_run_tokens_are_readable_in_both_themes(self):
        for t in (LIGHT, DARK):
            self.assertGreaterEqual(contrast(t.run, t.on_run), 4.5, t.name)
            self.assertGreaterEqual(contrast(t.run_hover, t.on_run), 4.5, t.name)

    def test_calculation_buttons_are_amber_and_navigation_is_not(self):
        theme = ThemeManager(self.app)
        theme.apply(force=True)
        self.assertIn('QPushButton[run="true"]', self.app.styleSheet())
        w = MainWindow(DemoBackend(0.0), theme, demo=True)
        try:
            for b in (w.elab_page.process_btn, w.capacity_page.analyze_btn, w.dqdv_page.analyze_btn,
                      w.dqdv_dock.go, w.cap_dock.reanalyze, w.dcir_page.detect_btn, w.dcir_page.publish_btn):
                self.assertTrue(b.property("run"), b.text())
            self.assertFalse(w.dcir_page.next_btn.property("run"))               # navigation stays Ocean
            self.assertEqual(w.dcir_page.next_strip.property("banner"), "next")  # amber accent on the strip
        finally:
            w.close()


class RenderAllPoints(Base):
    def test_data_layer_is_transparent_plot_area_at_300_dpi(self):
        style = FlatStyle(400, 200, 1.0, "x", "y", None, "#006D85", "#B05A31", False, "lines")
        image, painter, plot, width, height = data_layer(style)
        painter.end()
        self.assertEqual(plot, QRectF(0, 0, 400, 200))
        self.assertEqual((width, height), (1250, 625))                        # 300 / 96 device px per logical px
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

    def test_layer_sits_on_the_shown_ranges_and_hides_the_sampled_curves(self):
        chart = ChartWidget()
        chart.resize(600, 400)
        chart.show()
        x = np.linspace(0.0, 10.0, 50)
        chart.set_style("time_s", ("Time [s]", "Voltage [V]", None), ("#006D85", "#B05A31"), True, "lines")
        chart.set_view(SimpleNamespace(y1=SimpleNamespace(x=x, y=3 + x / 10, source_row=np.arange(50)), y2=None,
                                       x_range=(0.0, 10.0), y1_range=(3.0, 4.0), y2_range=None))
        pump(50)
        w, h = chart.plot_size()
        image, painter, _plot, *_ = data_layer(FlatStyle(w, h, 1.0, "", "", None, "#000000", "#000000", False, "lines"))
        painter.end()
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        chart.show_flat(bytes(data))
        pump(80)                                   # pyqtgraph applies the view transform lazily
        item = chart.flat.item.mapRectToScene(chart.flat.item.boundingRect())
        target = chart.vb.mapRectToScene(chart.vb.rect())
        for a, b in ((item.left(), target.left()), (item.top(), target.top()), (item.width(), target.width()),
                     (item.height(), target.height())):
            self.assertAlmostEqual(a, b, delta=1.0)
        self.assertEqual(chart.c1.opacity(), 0.0)
        self.assertFalse(chart.interactive)
        saved = QImage.fromData(chart.export_png(), "PNG")
        self.assertGreaterEqual(saved.width(), round(chart.pw.width() * EXPORT_DPI / 96) - 1)
        self.assertGreaterEqual(round(saved.dotsPerMeterX() * 0.0254), EXPORT_DPI)
        chart.show_flat(None)
        self.assertFalse(chart.flat.visible)
        self.assertEqual(chart.c1.opacity(), 1.0)
        self.assertTrue(chart.interactive)
        chart.close()


class HeaderTitles(Base):
    def test_resize_to_contents_titles_are_never_cut(self):
        theme = ThemeManager(self.app)
        theme.apply(force=True)
        heads = ["Pulse", "Source", "Cycle", "Time [s]", "I0 [µA]", "SOC [%]", "Resistance [Ω]", "Requested time [s]"]
        m = QStandardItemModel(1, len(heads))
        m.setHorizontalHeaderLabels(heads)
        for c in range(len(heads)):
            m.setItem(0, c, QStandardItem("1"))
        table = QTableView()
        table.setModel(m)
        header = install(table)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.resize(1400, 200)
        table.show()
        pump(60)
        for s in range(len(heads)):
            self.assertFalse(any("…" in line for line in header.lines(s)), (heads[s], header.lines(s)))
        table.close()


class PillsAndLists(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.theme = ThemeManager(cls.app)
        cls.theme.apply(force=True)
        cls.w = MainWindow(DemoBackend(0.0), cls.theme, demo=True)
        cls.w.show()
        cls.w.start_import("a_cell.csv", "auto")
        wait_for(lambda: cls.w._active_job is None)
        cls.ds = cls.w.state.datasets[0]
        cls.w.elab.process(cls.ds.id)
        wait_for(lambda: cls.ds.elab.has_result)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()
        pump()

    def test_list_views_follow_the_theme(self):
        qss = self.app.styleSheet()
        self.assertIn("QListView::item:selected { background: %s" % self.theme.tokens.tint, qss)

    def test_explorer_shows_dcir_corrected_instead_of_current_result(self):
        self.w.go(Section.EXPLORER)
        pump(60)
        self.assertEqual(self.w.explorer_page.result_pill.text.text(), "Current result")
        original = self.ds.elab.result
        self.ds.elab.result = replace(original, correction="dcir")
        try:
            self.w.explorer_page.refresh()
            self.assertEqual(self.w.explorer_page.result_pill.text.text(), "DCIR corrected")
        finally:
            self.ds.elab.result = original
            self.w.explorer_page.refresh()


if __name__ == "__main__":
    unittest.main()
