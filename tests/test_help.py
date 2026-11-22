"""In-app Help: Markdown preparation, pre-rendered equations and the Help window."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage, QPalette, QTextDocument
from PySide6.QtWidgets import QApplication

from app.fonts import load_fonts
from app.help_docs import DOCS, FORMULA_DIR, equations, formula_key, normalize_tex, prepare_markdown
from app.theme import ThemeManager


class Preparation(unittest.TestCase):
    def test_equation_keys_ignore_whitespace_and_blocks_become_images(self):
        self.assertEqual(formula_key("a  =\n b"), formula_key("a = b"))
        self.assertEqual(normalize_tex(" x \n= 1 "), "x = 1")
        md = "Text\n\n$$\na = b\n$$\n\nMore"
        with tempfile.TemporaryDirectory() as tmp:
            text, missing = prepare_markdown(md, Path(tmp))
            self.assertEqual(missing, [formula_key("a = b")])
            self.assertIn("```\na = b\n```", text)                 # never dropped: LaTeX shown as code
            (Path(tmp) / f"{formula_key('a = b')}.png").write_bytes(b"")
            text, missing = prepare_markdown(md, Path(tmp))
            self.assertEqual(missing, [])
            self.assertIn(f"![equation](formula:{formula_key('a = b')})", text)
            self.assertNotIn("$$", text)

    def test_every_equation_of_both_documents_has_its_image(self):
        """Run tools/build_help_formulas.py after editing a $$ block; the app never renders LaTeX itself."""
        for doc in DOCS:
            text = doc.path.read_text(encoding="utf-8")
            for tex in equations(text):
                self.assertTrue((FORMULA_DIR / f"{formula_key(tex)}.png").is_file(), (doc.filename, tex))
        # 43 + the GITT_X and GITT_C forms of the GITT VA derivation (added 2026-11-15)
        self.assertEqual(len(equations(DOCS[1].path.read_text(encoding="utf-8"))), 45)

    def test_documents_use_the_current_wording_and_defaults(self):
        guide = DOCS[0].path.read_text(encoding="utf-8")
        methods = DOCS[1].path.read_text(encoding="utf-8")
        self.assertIn("## Quick start", guide)
        self.assertIn("The base value is 30 s", guide)
        self.assertIn("30 s by default in the GUI", methods)
        self.assertNotIn("Publishing result", guide)
        self.assertNotIn("Click **+**", guide)


class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_fonts()

    def setUp(self):
        from app.widgets.help_window import HelpWindow
        self.theme = ThemeManager(self.app)
        self.original = self.theme.mode
        self.window = HelpWindow(self.theme)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.theme.set_mode(self.original)

    def test_both_documents_load_with_contents_equations_and_search(self):
        w = self.window
        w.show()
        self.app.processEvents()
        for doc in DOCS:
            w.show_doc(doc.key)
            self.assertEqual(w.missing, [])
            self.assertGreater(w.toc.count(), 10)
            self.assertNotIn("$$", w.page.toPlainText())
        w.show_doc("methods")
        image = w.page.loadResource(QTextDocument.ResourceType.ImageResource.value, QUrl(f"formula:{formula_key(equations(DOCS[1].path.read_text(encoding='utf-8'))[0])}"))
        self.assertIsInstance(image, QImage)
        self.assertGreater(image.devicePixelRatio(), 2)                # drawn at logical size, crisp on HiDPI
        w.search.setText("Savitzky")
        self.assertTrue(w.find())
        self.assertIn("Savitzky", w.page.textCursor().selectedText())
        w.search.setText("zzzz-not-present")
        self.assertFalse(w.find())
        self.assertEqual(w.found.text(), "Not found")
        w.show_doc("guide")
        self.app.processEvents()
        item = w.toc.item(5)
        w._jump(item)
        self.assertGreater(w.page.verticalScrollBar().value(), 0)

    def test_page_stays_white_in_dark_theme(self):
        self.theme.set_mode("dark")
        self.window.show_doc("guide")
        self.window.show()
        self.assertEqual(self.window.page.palette().color(QPalette.ColorRole.Base).name(), "#ffffff")
        self.assertIn("background: #ffffff", self.window.page.styleSheet())


if __name__ == "__main__":
    unittest.main()
