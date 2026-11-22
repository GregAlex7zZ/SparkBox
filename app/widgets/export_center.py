"""Export Center window, opened by the Export button at the bottom of the section list.

It lists every table export of the active dataset, grouped by section (app.export_center.export_items). Exports whose
section has a result have a tick box; the others are locked, say which section must be run first, and clicking them
repeats that explanation in a banner with a button that opens the section. The ticked exports are written as separate
files into one chosen folder, in CSV or Parquet, on a worker thread (ExportCenterController); the window then lists
the files written and any export that failed.

Limits: one dataset at a time (the active one); table exports only (chart images are saved from each section).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QRadioButton, QScrollArea, QVBoxLayout, QWidget)

from ..export_center import ExportCenterController, ExportItem
from ..file_dialogs import EXPORT_KEY, export_start_folder, remember
from ..icons import pixmap
from ..theme import ThemeManager, set_prop

SECTION_KEYS = {"Data Elaboration": "elaboration", "Capacity": "capacity", "DCIR": "dcir", "dQ/dV": "dqdv",
                "GITT / ICI": "gitt_ici"}


def _lab(text: str = "", muted: bool = False, size: Optional[int] = None, weight: Optional[int] = None,
         wrap: bool = False) -> QLabel:
    """Themed label (size in px, converted to pt as in the other pages)."""
    label = QLabel(text)
    if muted:
        label.setProperty("muted", True)
    css = (f"font-size: {size * .75:g}pt;" if size else "") + (f"font-weight: {weight};" if weight else "")
    if css:
        label.setStyleSheet(css)
    label.setWordWrap(wrap)
    return label


def _btn(text: str, kind: str) -> QPushButton:
    """Push button styled by the QSS property ``kind`` (ghost, goElab, quiet, link)."""
    button = QPushButton(text)
    button.setProperty(kind, True)
    button.setFixedHeight(30)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class ExportRow(QFrame):
    """One export: tick box, title and the file name it writes; or, when unavailable, a locked row with the reason.
    A click anywhere on the row toggles the tick box, or (locked) emits ``lockedClicked``."""
    lockedClicked = Signal(object)          # ExportItem

    def __init__(self, theme: ThemeManager, item: ExportItem, file_hint: str):
        super().__init__()
        self.item = item
        self.setProperty("panel", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)
        self.box = QCheckBox()
        self.box.setAccessibleName(f"{item.section}: {item.title}")
        col = QVBoxLayout()
        col.setSpacing(1)
        title = _lab(item.title, size=13, weight=500 if item.available else None)
        hint = _lab(file_hint if item.available else f"Not available · {item.reason}", muted=True, size=11, wrap=True)
        col.addWidget(title)
        col.addWidget(hint)
        icon = QLabel()
        t = theme.tokens
        icon.setPixmap(pixmap("circle-check" if item.available else "circle-dashed",
                              t.success if item.available else t.muted, 16))
        if not item.available:
            self.box.setEnabled(False)
            # Clicks pass through the disabled box to the row, which explains what to run first.
            self.box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            title.setProperty("muted", True)
            self.setToolTip(item.reason)
        else:
            self.box.setChecked(True)
        lay.addWidget(self.box)
        lay.addLayout(col, 1)
        lay.addWidget(icon)

    def mousePressEvent(self, event) -> None:
        """Toggle an available export; explain a locked one."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self.item.available:
                self.box.toggle()
            else:
                self.lockedClicked.emit(self.item)
            event.accept()
            return
        super().mousePressEvent(event)


class ExportCenterDialog(QDialog):
    """Export window for one dataset. ``folder`` is the default destination (the folder of the dataset's original
    file). ``goSectionRequested(key)`` asks the main window to open a section (SECTION_KEYS values) after the dialog
    closes."""
    goSectionRequested = Signal(str)

    def __init__(self, parent, theme: ThemeManager, ctl: ExportCenterController, ds_id: str, ds_name: str,
                 folder: Optional[str] = None):
        super().__init__(parent)
        self.theme, self.ctl, self.ds_id, self.ds_name = theme, ctl, ds_id, ds_name
        self.setWindowTitle("Export")
        self.setMinimumSize(560, 600)
        self._folder = export_start_folder(folder)      # the original file's folder, else the last export folder
        self._locked: Optional[ExportItem] = None
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(_lab("Export", size=18, weight=500))
        v.addWidget(_lab(f"{ds_name} · every export is written as a separate file, with all rows and all columns, "
                         "in the units shown (Settings › Units). Existing files are never overwritten: a free name "
                         "such as “… (2)” is used instead.", muted=True, wrap=True))
        # locked-export explanation
        self.notice = QFrame()
        set_prop(self.notice, "banner", "warn")
        nl = QHBoxLayout(self.notice)
        nl.setContentsMargins(12, 8, 12, 8)
        self.notice_icon = QLabel()
        self.notice_text = _lab(wrap=True)
        self.notice_go = _btn("Open section", "ghost")
        self.notice_go.clicked.connect(self._go_locked)
        nl.addWidget(self.notice_icon, 0, Qt.AlignmentFlag.AlignTop)
        nl.addWidget(self.notice_text, 1)
        nl.addWidget(self.notice_go)
        self.notice.hide()
        v.addWidget(self.notice)
        # rows
        self.rows: list[ExportRow] = []
        host = QWidget()
        host.setObjectName("ExportList")
        self.list_lay = QVBoxLayout(host)
        self.list_lay.setContentsMargins(0, 0, 4, 0)
        self.list_lay.setSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea, #qt_scrollarea_viewport, #ExportList { background: transparent; }")
        scroll.setWidget(host)
        v.addWidget(scroll, 1)
        sel = QHBoxLayout()
        self.all_btn, self.none_btn = _btn("Select all", "quiet"), _btn("Select none", "quiet")
        self.all_btn.clicked.connect(lambda: self._select(True))
        self.none_btn.clicked.connect(lambda: self._select(False))
        sel.addWidget(self.all_btn)
        sel.addWidget(self.none_btn)
        sel.addStretch(1)
        v.addLayout(sel)
        # format + folder
        f = QHBoxLayout()
        f.addWidget(_lab("Format"))
        self.csv, self.parquet = QRadioButton("CSV"), QRadioButton("Parquet")
        self.csv.setChecked(True)
        self.csv.toggled.connect(self._fill)
        f.addWidget(self.csv)
        f.addWidget(self.parquet)
        f.addStretch(1)
        v.addLayout(f)
        d = QHBoxLayout()
        d.addWidget(_lab("Folder"))
        self.folder_lab = _lab(muted=True)
        self.folder_lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        choose = _btn("Choose…", "ghost")
        choose.clicked.connect(self._choose_folder)
        d.addWidget(self.folder_lab, 1)
        d.addWidget(choose)
        v.addLayout(d)
        self.status = _lab(muted=True, wrap=True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.hide()
        v.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        self.close_btn, self.export_btn = _btn("Close", "ghost"), _btn("Export", "goElab")
        self.close_btn.clicked.connect(self._close_or_cancel)
        self.export_btn.clicked.connect(self._export)
        row.addWidget(self.close_btn)
        row.addWidget(self.export_btn)
        v.addLayout(row)
        ctl.progress.connect(self._progress)
        ctl.finished.connect(self._finished)
        self._fill()

    # ---- content ---------------------------------------------------------------------------------------------
    def _ext(self) -> str:
        return ".csv" if self.csv.isChecked() else ".parquet"

    def _fill(self) -> None:
        """(Re)build the rows from the current availability, keeping the user's ticks."""
        ticked = {r.item.key: r.box.isChecked() for r in self.rows}
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.rows = []
        section = None
        for item in self.ctl.items(self.ds_id):
            if item.section != section:
                section = item.section
                head = _lab(section, muted=True, size=11, weight=600)
                self.list_lay.addWidget(head)
            hint = f"{self.ds_name} - {item.suffix}{self._ext()}"
            if item.key == "gitt_ici":
                hint = f"{self.ds_name} - {item.suffix} - charge / discharge{self._ext()}"
            row = ExportRow(self.theme, item, hint)
            if item.available and item.key in ticked:
                row.box.setChecked(ticked[item.key])
            row.box.toggled.connect(self._sync)
            row.lockedClicked.connect(self._explain)
            self.rows.append(row)
            self.list_lay.addWidget(row)
        self.list_lay.addStretch(1)
        self.folder_lab.setText(self._folder)
        self._sync()

    def _chosen(self) -> list[str]:
        return [r.item.key for r in self.rows if r.item.available and r.box.isChecked()]

    def _sync(self) -> None:
        """Export button text and state from the ticks; nothing can be ticked while exporting."""
        n = len(self._chosen())
        running = self.ctl.running
        self.export_btn.setText(f"Export {n} file{'s' if n != 1 else ''}" if n else "Export")
        self.export_btn.setEnabled(bool(n) and not running)
        self.export_btn.setToolTip("" if n else "Tick at least one available export.")
        any_available = any(r.item.available for r in self.rows)
        self.all_btn.setEnabled(any_available and not running)
        self.none_btn.setEnabled(any_available and not running)
        for r in self.rows:
            r.setEnabled(not running)
        self.close_btn.setText("Cancel Export" if running else "Close")

    def _select(self, on: bool) -> None:
        for r in self.rows:
            if r.item.available:
                r.box.setChecked(on)

    def _explain(self, item: ExportItem) -> None:
        """A locked export was clicked: say plainly that its section must be run first."""
        self._locked = item
        self.notice_icon.setPixmap(pixmap("circle-alert", self.theme.tokens.warn, 18))
        self.notice_text.setText(f"<b>{item.title}</b> cannot be exported yet: {item.section} has no result for "
                                 f"this dataset. {item.reason}")
        target = "Data Elaboration" if item.reason.startswith("Run Data Elaboration") else item.section
        self.notice_go.setText(f"Go to {target}")
        self.notice_go.setProperty("target", SECTION_KEYS[target])
        self.notice.show()

    def _go_locked(self) -> None:
        key = self.notice_go.property("target")
        if key:
            self.goSectionRequested.emit(str(key))
            self.reject()

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Export folder", self._folder)
        if folder:
            self._folder = folder
            self.folder_lab.setText(folder)

    # ---- running ---------------------------------------------------------------------------------------------
    def _export(self) -> None:
        folder = Path(self._folder)
        if not folder.is_dir():
            self.status.setText("The export folder does not exist any more. Choose another folder.")
            self.status.show()
            return
        if self.ctl.run(self.ds_id, self._chosen(), "csv" if self.csv.isChecked() else "parquet", str(folder)):
            remember(EXPORT_KEY, str(folder / "export"))
            self.notice.hide()
            self.status.setText("Starting export…")
            self.status.show()
        else:
            self.status.setText("Nothing could be exported: the chosen results are no longer available.")
            self.status.show()
            self._fill()
        self._sync()

    def _progress(self, text: str) -> None:
        self.status.setText(text)
        self.status.show()

    def _finished(self, outcomes: list) -> None:
        """List what was written, what failed and what was cancelled."""
        lines = []
        for o in outcomes:
            if o.error:
                lines.append(f"✗ {o.title}: {o.error}")
            elif o.cancelled:
                lines.append(f"– {o.title}: cancelled, nothing written")
            else:
                lines.append(f"✓ {o.title}: " + ", ".join(Path(p).name for p in o.paths))
        ok = sum(1 for o in outcomes if not o.error and not o.cancelled)
        head = f"Exported {ok} of {len(outcomes)} to {self._folder}"
        self.status.setText(head + "\n" + "\n".join(lines))
        self.status.show()
        self._sync()

    def _close_or_cancel(self) -> None:
        if self.ctl.running:
            self.ctl.cancel()
            self.status.setText("Cancelling… the file being written is removed.")
        else:
            self.accept()

    def reject(self) -> None:
        """Esc / window close: never leave an export running without a window to report it."""
        if self.ctl.running:
            self.ctl.cancel()
            return
        super().reject()
