"""Main window: shell layout, section routing, unlock logic and the import controller."""
from __future__ import annotations
import uuid
from typing import Optional
from pathlib import Path
from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
                               QStackedWidget, QVBoxLayout, QWidget)
from .backend import AmbiguousMapping, Backend, ColumnMapping, ImportCancelled, ImportResult, SUPPORTED_FILTER
from .elab_controller import ElabController, UserDefaults
from .explorer_controller import ExplorerController
from .capacity_controller import CapacityController
from .dcir_controller import DcirController
from .gitt_ici_controller import GittIciController
from .correction_controller import CorrectionController
from .dqdv_controller import DqdvController
from .comparison_controller import ComparisonController
from .state import SECTION_LABELS, SECTION_ORDER, Dataset, Section, SessionState, Status, fallback_section
from .shutdown import SHUTDOWN_WAIT_MS
from .theme import ThemeManager
from .tokens import SIDE_CARD_W, SIDE_CARD_W_COLLAPSED
from .units_controller import UnitsController
from .mascot.observer import MascotObserver
from .branding import ABOUT_ICON_PX, APP_NAME, about_html, app_icon
from .mascot.placement import BrandSlot
from .mascot.widget import Mascot
from .widgets.advanced_dock import DockHost
from .widgets.busy_overlay import BusyOverlay
from .widgets.data_page import DataPage
from .widgets.elab_dock import ElabSettingsDock
from .widgets.elab_page import ElabPage
from .widgets.export_center import ExportCenterDialog
from .widgets.explorer_dock import PlotSettingsDock
from .widgets.explorer_page import ExplorerPage
from .export_center import ExportCenterController
from .file_dialogs import dataset_folder, open_dataset, remember_import
from .widgets.capacity_dock import CapacitySettingsDock
from .widgets.capacity_page import CapacityPage
from .standby import STANDBY
from .widgets.combos import install_popup_fit
from .widgets.dcir_dock import DcirSettingsDock
from .widgets.dcir_page import DcirPage
from .widgets.gitt_ici_dock import GittIciSettingsDock
from .widgets.gitt_ici_page import GittIciPage
from .widgets.dqdv_dock import DqdvSettingsDock
from .widgets.dqdv_page import DqdvPage
from .widgets.comparison_dock import ComparisonDock
from .widgets.comparison_page import ComparisonPage
from .widgets.map_dialog import MapDialog
from .widgets.nav import NavSidebar
from .widgets.settings_dialog import SettingsDialog
from .widgets.tabs import TabBand
from .settings_profiles import SettingsProfiles, apply_dataset_preferences, snapshot, validate_preferences

NARROW_PX = 1100
DOCK_TITLES = {Section.EXPLORER: "Plot Settings", Section.CAPACITY: "Capacity Settings", Section.DCIR: "DCIR Settings",
               Section.GITT_ICI: "GITT / ICI Settings",
               Section.DQDV: "dQ/dV Settings", Section.COMPARISON: "Comparison Settings"}


class _Signals(QObject):
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)
    ambiguous = Signal(object)
    cancelled = Signal()


class _ImportJob(QRunnable):
    """Runs a backend import off the GUI thread. Never touches widgets; reports through signals only."""

    def __init__(self, fn, signals: _Signals):
        super().__init__()
        self.fn, self.sig = fn, signals

    def run(self) -> None:
        try:
            self.sig.finished.emit(self.fn(self.sig.progress.emit))
        except AmbiguousMapping as e:
            self.sig.ambiguous.emit(e.inspection)
        except ImportCancelled:
            self.sig.cancelled.emit()
        except Exception as e:                      # surfaced to the user, never swallowed
            self.sig.failed.emit(f"{type(e).__name__}: {e}")


class PlaceholderPage(QWidget):
    def __init__(self, section: Section):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        a = QLabel(SECTION_LABELS[section])
        a.setStyleSheet("font-size: 15pt; font-weight: 500;")
        b = QLabel("Section canvas — designed in a later step.")
        b.setProperty("muted", True)
        for w in (a, b):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(w)


class MainWindow(QMainWindow):
    shutdownFinished = Signal()                           # closeEvent is done: the entry point leaves the event loop

    def __init__(self, backend: Backend, theme: ThemeManager, demo: bool = False):
        super().__init__()
        install_popup_fit(QApplication.instance())      # every drop-down list as wide as its longest item
        self.backend, self.theme, self.demo = backend, theme, demo
        self._closing = False
        self.state = SessionState()
        self.user_defaults = UserDefaults()
        self.profiles = SettingsProfiles()
        self.elab = ElabController(self.state, backend, self.user_defaults)
        self.units = UnitsController(self.state, backend)
        self.explorer = ExplorerController(self.state, backend, self.units)
        self.capacity = CapacityController(self.state, backend, self.elab, self.units)
        self.dcir = DcirController(self.state, backend, self.elab, self.units)
        self.gitt_ici = GittIciController(self.state, backend)
        self.dqdv = DqdvController(self.state, backend, self.elab, self.units,
                                   cycles_fn=self._result_cycles, dcir=self.dcir)
        self.correction = CorrectionController(self.state, backend, self.elab, self.capacity, self.dcir, self.units,
                                               self.dqdv)
        self.comparison = ComparisonController(self.state, backend, self.units, self.elab, self.capacity, self.dqdv)
        self.export_center = ExportCenterController(self.state, backend, self.units, self.gitt_ici)
        self.results: dict[str, ImportResult] = {}
        self.current = Section.DATA
        self._last_sections: dict[str, Section] = {}
        self._active_job: dict | None = None
        self._close_after_cancel: set[str] = set()
        self.settings = QSettings("gregorithm", "gui")
        self.setWindowTitle(APP_NAME)
        self.resize(1360, 840)
        self.setMinimumSize(980, 640)
        self._build_actions()
        self._build_menus()
        self._build_layout()
        self.refresh_all()
        self.statusBar().showMessage("Ready" if not demo else "Ready · SYNTHETIC DEMO DATA (not scientific)")

    # --- construction ------------------------------------------------------------------------
    def _build_actions(self) -> None:
        self.act_auto = QAction("Auto Mapping…", self)
        self.act_auto.setShortcut(QKeySequence("Ctrl+O"))
        self.act_auto.setStatusTip("Choose a file and import it now; asks only if columns are ambiguous")
        self.act_auto.triggered.connect(lambda: self.open_file("auto"))
        self.act_manual = QAction("Manual Mapping…", self)
        self.act_manual.setStatusTip("Choose a file, then choose columns and units yourself")
        self.act_manual.triggered.connect(lambda: self.open_file("manual"))
        self.act_settings = QAction("Settings…", self)
        self.act_settings.triggered.connect(self.open_settings)
        self.act_about = QAction(f"About {APP_NAME}", self)
        self.act_about.triggered.connect(self._about)
        self.act_sidebar = QAction("Toggle Sidebar", self)
        self.act_sidebar.triggered.connect(lambda: self.nav.set_collapsed(not self.nav.collapsed, user=True))
        self.addAction(self.act_sidebar)
        self.act_help = QAction(f"{APP_NAME} Help", self)
        self.act_help.setShortcut(QKeySequence("F1"))
        self.act_help.triggered.connect(lambda: self.open_help("guide"))
        self.act_methods = QAction("Scientific Methods", self)
        self.act_methods.triggered.connect(lambda: self.open_help("methods"))
        self.act_quit = QAction("Exit", self)
        self.act_quit.triggered.connect(self.close)

    def open_menu(self, parent=None) -> QMenu:
        """"Open File" menu with the two import modes; shared by File, the tab band "+" and Settings."""
        menu = QMenu("Open File", parent or self)
        menu.addAction(self.act_auto)
        menu.addAction(self.act_manual)
        return menu

    def _build_menus(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("File")
        f.addMenu(self.open_menu())
        f.addSeparator()
        f.addAction(self.act_quit)
        mb.addMenu("Settings").addAction(self.act_settings)
        mb.addMenu("About").addAction(self.act_about)
        h = mb.addMenu("Help")
        h.addAction(self.act_help)
        h.addAction(self.act_methods)
        if self.demo:                                         # visible only with --demo
            d = mb.addMenu("Demo")
            a = QAction("Mark Active Dataset as Elaborated", self)
            a.triggered.connect(self._demo_elaborate)
            d.addAction(a)

    def _build_layout(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        body = QHBoxLayout(root)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        # left column
        self.left = QWidget()
        ll = QVBoxLayout(self.left)
        ll.setContentsMargins(10, 0, 10, 10)
        ll.setSpacing(0)
        self.brand = QLabel(APP_NAME)
        self.brand.setObjectName("Brand")
        self.brand.setFixedHeight(56)
        self.nav = NavSidebar(self.theme)
        ll.addWidget(self.brand)
        # Mascot right of the brand text, as large as the row allows (app.mascot.placement). It only observes the app
        # (app.mascot.observer); with the sidebar collapsed it replaces the brand text, centred (_sync_left).
        self.mascot = Mascot(height=46)
        self.mascot_slot = BrandSlot(self.mascot, self.brand, self.left, max_h=56)
        ll.addWidget(self.nav, 1)
        body.addWidget(self.left)
        # right column
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 10, 10)
        rl.setSpacing(0)
        self.band = TabBand(self.theme)
        rl.addWidget(self.band)
        # Content card + Advanced-settings dock side by side. The dock has a fixed width (DOCK_WIDTH): opening it
        # never squeezes or re-flows its own contents, which are laid out for exactly that width.
        self.split = QWidget()
        split_lay = QHBoxLayout(self.split)
        split_lay.setContentsMargins(0, 0, 0, 0)
        split_lay.setSpacing(10)
        card = QFrame()
        card.setObjectName("ContentCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        cl.addWidget(self.stack)
        self.data_page = DataPage(self.theme, self.act_auto, self.act_manual)
        self.pages: dict[Section, QWidget] = {Section.DATA: self.data_page}
        self.stack.addWidget(self.data_page)
        for s in SECTION_ORDER[1:]:
            if s is Section.ELABORATION:
                self.pages[s] = ElabPage(self.theme, self.elab, self.backend)
            elif s is Section.EXPLORER:
                self.pages[s] = ExplorerPage(self.theme, self.explorer)
            elif s is Section.CAPACITY:
                self.pages[s] = CapacityPage(self.theme, self.capacity)
            elif s is Section.DCIR:
                self.pages[s] = DcirPage(self.theme, self.dcir, self.correction)
            elif s is Section.GITT_ICI:
                self.pages[s] = GittIciPage(self.theme, self.gitt_ici)
            elif s is Section.DQDV:
                self.pages[s] = DqdvPage(self.theme, self.dqdv)
            elif s is Section.COMPARISON:
                self.pages[s] = ComparisonPage(self.theme, self.comparison)
            else:
                self.pages[s] = PlaceholderPage(s)
            self.stack.addWidget(self.pages[s])
        self.elab_page: ElabPage = self.pages[Section.ELABORATION]
        self.explorer_page: ExplorerPage = self.pages[Section.EXPLORER]
        self.capacity_page: CapacityPage = self.pages[Section.CAPACITY]
        self.dcir_page: DcirPage = self.pages[Section.DCIR]
        self.gitt_ici_page: GittIciPage = self.pages[Section.GITT_ICI]
        self.dqdv_page: DqdvPage = self.pages[Section.DQDV]
        self.comparison_page: ComparisonPage = self.pages[Section.COMPARISON]
        self.elab_dock = ElabSettingsDock(self.theme, self.elab)
        self.plot_dock = PlotSettingsDock(self.theme, self.explorer)
        self.cap_dock = CapacitySettingsDock(self.theme, self.capacity)
        self.dcir_dock = DcirSettingsDock(self.theme, self.dcir)
        self.dqdv_dock = DqdvSettingsDock(self.theme, self.dqdv)
        self.comparison_dock = ComparisonDock(self.theme, self.comparison)
        self.gitt_ici_dock = GittIciSettingsDock(self.theme, self.gitt_ici_page.setup.advanced_panel,
                                                 self.gitt_ici_page.results)
        self.dock = DockHost({Section.ELABORATION: self.elab_dock, Section.EXPLORER: self.plot_dock,
                              Section.CAPACITY: self.cap_dock, Section.DCIR: self.dcir_dock,
                              Section.DQDV: self.dqdv_dock, Section.COMPARISON: self.comparison_dock,
                              Section.GITT_ICI: self.gitt_ici_dock})
        self.dock.hide()
        # page and dock of each section, for the standby of the sections not on screen (see _park_views)
        self._views: dict[Section, tuple] = {
            Section.ELABORATION: (self.elab_page, self.elab_dock), Section.EXPLORER: (self.explorer_page, self.plot_dock),
            Section.CAPACITY: (self.capacity_page, self.cap_dock), Section.DCIR: (self.dcir_page, self.dcir_dock),
            Section.GITT_ICI: (self.gitt_ici_page,), Section.DQDV: (self.dqdv_page, self.dqdv_dock)}
        self._view_ds: dict = {}
        self._shown_in_go: Optional[Section] = None
        split_lay.addWidget(card, 1)
        split_lay.addWidget(self.dock, 0)
        rl.addWidget(self.split, 1)
        body.addWidget(right, 1)
        # wiring
        self.nav.sectionRequested.connect(self.go)
        self.nav.exportRequested.connect(self.open_export_center)
        self.nav.collapsedChanged.connect(lambda c: (self.settings.setValue("sidebarCollapsed", c), self._sync_left()))
        self.band.tabSelected.connect(self.select_dataset)
        self.band.tabCloseRequested.connect(self.close_dataset)
        self.band.advancedToggled.connect(self._toggle_dock)
        self.band.set_open_menu(self.open_menu(self.band))
        self.data_page.fileDropped.connect(lambda p: self.start_import(p, "auto"))
        self.data_page.cancelRequested.connect(self._cancel_active)
        self.data_page.remapRequested.connect(self.remap)
        self.data_page.goElaborationRequested.connect(lambda: self.go(Section.ELABORATION))
        self.elab_page.openSettingsRequested.connect(lambda: self.band.set_advanced_checked(True))
        self.elab_page.openExplorerRequested.connect(lambda: self.go(Section.EXPLORER))
        self.elab.runFinished.connect(self._elab_finished)
        self.elab.resultReplaced.connect(self.explorer.result_replaced)
        self.elab.resultReplaced.connect(self.capacity.elaboration_replaced)
        self.elab.resultReplaced.connect(self.dcir.elaboration_replaced)
        self.elab.resultReplaced.connect(self.gitt_ici.elaboration_replaced)
        self.elab.resultReplaced.connect(self.correction.elaboration_replaced)
        self.elab.resultCorrected.connect(self.explorer.result_replaced)
        self.units.changed.connect(self._units_changed)
        self.plot_dock.unitsRequested.connect(lambda: self.open_settings("units"))
        self.cap_dock.unitsRequested.connect(lambda: self.open_settings("units"))
        self.dcir_dock.unitsRequested.connect(lambda: self.open_settings("units"))
        self.dqdv_dock.unitsRequested.connect(lambda: self.open_settings("units"))
        self.comparison_dock.unitsRequested.connect(lambda: self.open_settings("units"))
        for widget in (self.capacity_page, self.cap_dock):
            widget.goElaborationRequested.connect(self._to_elaboration)
        self.elab.exportFinished.connect(lambda _ds, message: self.statusBar().showMessage(message, 8000))
        self.elab.exportError.connect(lambda _ds, message: QMessageBox.warning(self, "Data Elaboration", message))
        self.capacity.exportFinished.connect(lambda _ds, message: self.statusBar().showMessage(message, 8000))
        self.capacity.exportError.connect(lambda _ds, message: QMessageBox.warning(self, "Capacity", message))
        self.dcir.notice.connect(lambda _ds, message: self.statusBar().showMessage(message, 8000))
        self.correction.notice.connect(lambda _ds, message: self.statusBar().showMessage(message, 10000))
        self.dcir_page.openCapacityRequested.connect(lambda: (self.go(Section.CAPACITY), self.band.set_advanced_checked(True)))
        self.dcir.exportFinished.connect(lambda _ds, message: self.statusBar().showMessage(message, 8000))
        self.dcir.exportError.connect(lambda _ds, message: QMessageBox.warning(self, "DCIR", message))
        self.dqdv.exportFinished.connect(lambda _ds, message: self.statusBar().showMessage(message, 8000))
        self.dqdv.exportError.connect(lambda _ds, message: QMessageBox.warning(self, "dQ/dV", message))
        self.gitt_ici.exportFinished.connect(lambda _ds, message: self.statusBar().showMessage(message, 8000))
        self.gitt_ici.exportError.connect(lambda _ds, message: QMessageBox.warning(self, "GITT / ICI", message))
        self.comparison.notice.connect(lambda message: self.statusBar().showMessage(message, 8000))
        self.explorer.savedImage.connect(lambda _ds, path: self.statusBar().showMessage(f"Image saved: {path}", 6000))
        self.explorer.saveFailed.connect(lambda _ds, msg: QMessageBox.warning(
            self, "Save Image", f"Could not save the image.\n{msg}"))
        self.theme.changed.connect(self._sync_left)
        self.busy = BusyOverlay(self)                           # theme re-style: visible progress, no frozen window
        self.theme.applyStarted.connect(lambda: self.busy.start("Applying theme…"))
        self.theme.applyFinished.connect(self.busy.stop)
        self.mascot_observer = MascotObserver(self.mascot, self)
        self.mascot_observer.watch_window(self)
        self.mascot.removeRequested.connect(lambda: self.set_mascot_enabled(False))
        self.mascot_enabled = str(self.settings.value("mascot/enabled", "true")).lower() == "true"
        self.mascot_observer.enabled = self.mascot_enabled
        collapsed = str(self.settings.value("sidebarCollapsed", "false")).lower() == "true"
        self.nav.set_collapsed(collapsed)
        self._sync_left()

    def _result_cycles(self, ds_id: str) -> list[int]:
        ds = self.state.get(ds_id)
        if ds is None or ds.elab.result is None:
            return []
        return self.backend.result_cycles(ds.elab.result.result_id)

    def _about(self) -> None:
        """About dialog: author, build, engine, AI-assistance note and licences, with the large application icon."""
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(about_html())
        box.setIconPixmap(app_icon().pixmap(ABOUT_ICON_PX, ABOUT_ICON_PX))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        self._about_box = box                  # kept for tests; exec() blocks until closed
        box.exec()

    def _sync_left(self) -> None:
        """Brand row of the sidebar column: the product name (APP_NAME) with the mascot beside it; with the sidebar
        collapsed only the mascot, centred , or the letter "S" when the mascot is turned off."""
        c = self.nav.collapsed
        mascot_only = c and self.mascot_enabled
        self.left.setFixedWidth((SIDE_CARD_W_COLLAPSED if c else SIDE_CARD_W) + 20)
        self.brand.setText("" if mascot_only else (APP_NAME[0] if c else APP_NAME))
        self.brand.setAccessibleName(APP_NAME)
        self.brand.setAlignment((Qt.AlignmentFlag.AlignCenter if c else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
        self.brand.setContentsMargins(0 if c else 9, 0, 0, 0)
        self.mascot_slot.centered = mascot_only
        self.mascot_slot.set_enabled(self.mascot_enabled)

    def set_mascot_enabled(self, enabled: bool) -> None:
        """Show or remove the mascot (right-click › Remove Mascot, Settings › Appearance). The choice is stored in
        QSettings ("mascot/enabled"), so it holds for the next launches. A removed mascot is hidden, its timer stops
        and it does not react to anything."""
        self.mascot_enabled = bool(enabled)
        self.settings.setValue("mascot/enabled", self.mascot_enabled)
        self.mascot_observer.enabled = self.mascot_enabled
        self._sync_left()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if hasattr(self, "band"):
            self.band.set_compact(self.width() < NARROW_PX)

    # --- navigation & unlock logic ---------------------------------------------------------------
    def refresh_all(self) -> None:
        avail = self.state.availability()
        self.nav.set_available(avail)
        self.nav.set_export_enabled(self.state.active_id is not None)
        new = fallback_section(self.current, avail)
        if new is not self.current:
            self.statusBar().showMessage(f"{SECTION_LABELS[self.current]} is not available for this dataset; "
                                         f"showing {SECTION_LABELS[new]}.", 6000)
        self._shown_in_go = None
        self.go(new)
        self.band.set_active(None if self.current is Section.COMPARISON else self.state.active_id)
        self.data_page.show_dataset(self.state.active_id)
        self._standby()
        for section in self._views:
            if section is self.current:
                if self._shown_in_go is not section:      # go() above may have loaded it already
                    self._show_views(section)             # the section on screen always shows the active dataset
            else:
                self._park_views(section)
        if self.current is Section.EXPLORER:
            self.explorer_page.activate()
        elif self.current is Section.CAPACITY:
            self.capacity_page.activate()

    def go(self, section: Section) -> None:
        if not self.state.availability().get(section, False):
            return
        if self.current is Section.COMPARISON and section is not Section.COMPARISON:
            self.comparison_page.activate(False)
        self.current = section
        self._standby()
        if section in self._views and any(self._view_ds.get(v) != self.state.active_id
                                          for v in self._views[section]):
            self._show_views(section)                     # a parked section loads the active dataset now
            self._shown_in_go = section
        self.stack.setCurrentWidget(self.pages[section])
        self.nav.set_current(section)
        self.dock.set_section(section, DOCK_TITLES.get(section, "Advanced Settings"))
        if section is Section.COMPARISON:
            self.band.set_active(None)
            self.comparison_page.activate(True)
            if not self.comparison.cs.checked:
                self.band.set_advanced_checked(True)
        else:
            if self.state.active_id:
                self._last_sections[self.state.active_id] = section
            self.band.set_active(self.state.active_id)
        if section is Section.EXPLORER:
            self.explorer_page.activate()
        elif section is Section.CAPACITY:
            self.capacity_page.activate()

    # --- standby of what is not shown ----------------------------------------------------------------------------
    # Only the selected dataset works: the calculations of the others pause (app.standby), and the pages and docks of
    # the sections not on screen are parked (they keep nothing of another dataset and load the active one only when
    # their section is opened). Going back to a dataset or a section can therefore take a moment to load.

    def _standby(self) -> None:
        """Let only the active dataset's calculations run (all of them in Comparison, which overlays datasets)."""
        STANDBY.set_active(self.state.active_id, everyone=self.current is Section.COMPARISON)

    def _show_views(self, section: Section) -> None:
        """Show the active dataset on the page and dock of ``section``."""
        for view in self._views[section]:
            view.show_dataset(self.state.active_id)
            self._view_ds[view] = self.state.active_id

    def _park_views(self, section: Section) -> None:
        """Park the page and dock of a section not on screen when they show another dataset than the active one:
        they are emptied (no drawing, no loading) until the section is opened again."""
        for view in self._views[section]:
            if self._view_ds.get(view, self.state.active_id) not in (None, self.state.active_id):
                view.show_dataset(None)
                self._view_ds[view] = None

    def load_docks(self) -> None:
        """Show the active dataset in every section dock, parked or not: Settings → Advanced edits the active
        dataset through these docks, whatever section is on screen."""
        for views in self._views.values():
            for dock in views[1:]:
                if self._view_ds.get(dock) != self.state.active_id:
                    dock.show_dataset(self.state.active_id)
                    self._view_ds[dock] = self.state.active_id

    def open_export_center(self) -> None:
        """Export button: the Export Center for the active dataset (every section's exports in one window)."""
        ds = self.state.get(self.state.active_id) if self.state.active_id else None
        if ds is None:
            return
        dlg = ExportCenterDialog(self, self.theme, self.export_center, ds.id, ds.name, dataset_folder(ds))
        target: list[str] = []
        dlg.goSectionRequested.connect(target.append)
        dlg.exec()
        if target:
            self.go(Section(target[0]))

    def _to_elaboration(self) -> None:
        """Capacity links return to the existing Elaboration section and dock."""
        dialog = getattr(self, "_settings_dialog", None)
        if dialog is not None:
            dialog.select_page("advanced")
            dialog.advanced_section.setCurrentIndex(dialog.advanced_section.findData(Section.ELABORATION.value))
            return
        self.go(Section.ELABORATION)
        self.band.set_advanced_checked(True)

    def select_dataset(self, ds_id: str) -> None:
        self.state.set_active(ds_id)
        if self.current is Section.COMPARISON:
            self.current = self._last_sections.get(ds_id, Section.DATA)
        self.refresh_all()

    def close_dataset(self, ds_id: str) -> None:
        ds = self.state.get(ds_id)
        if ds and ds.elab.is_running:
            self._close_after_cancel.add(ds_id)
            self.elab.cancel(ds_id)
            self.statusBar().showMessage("Cancelling processing; the dataset closes when it has stopped.", 6000)
            return
        if ds and ds.status is Status.ELABORATED:            # confirm only when work would be lost
            r = QMessageBox.question(self, "Close dataset",
                                     f"Close \u201c{ds.name}\u201d? Its elaboration results will be discarded.",
                                     QMessageBox.StandardButton.Close | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Close:
                return
        if ds and ds.elab.result is not None:
            self.backend.release_result(ds.elab.result.result_id)
        self.explorer.dataset_closed(ds_id)
        self.capacity.dataset_closed(ds_id)
        self.dcir.dataset_closed(ds_id)
        self.gitt_ici.dataset_closed(ds_id)
        self.correction.dataset_closed(ds_id)
        self.dqdv.dataset_closed(ds_id)
        self.comparison.dataset_closed(ds_id)
        self._last_sections.pop(ds_id, None)
        self.state.remove(ds_id)
        self.band.remove_tab(ds_id)
        self.data_page.drop_dataset(ds_id)
        self.results.pop(ds_id, None)
        self.backend.release(ds_id)
        self.refresh_all()

    def bring_to_front(self) -> None:
        """Restore and raise the window: SparkBox was started again while already open (app.single_instance)."""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def open_help(self, doc: str = "guide") -> None:
        """One non-modal Help window (User Guide / Scientific Methods), raised if already open."""
        from .widgets.help_window import HelpWindow
        if getattr(self, "_help", None) is None:
            self._help = HelpWindow(self.theme)
        self._help.show_doc(doc)
        self._help.show()
        self._help.raise_()
        self._help.activateWindow()

    def open_settings(self, page: str = "appearance") -> None:
        if getattr(self, "_settings_dialog", None) is not None:
            self._settings_dialog.select_page(page)
            return
        ds = self.state.active
        dialog = SettingsDialog(self.theme, self.user_defaults, self, units=self.units,
                                ds_id=ds.id if ds else None, ds_name=ds.name if ds else "", page=page,
                                owner=self)
        self._settings_dialog = dialog
        try:
            dialog.exec()
        finally:
            dialog.restore_advanced_panels()
            self._settings_dialog = None
            dialog.deleteLater()

    def capture_preferences(self) -> dict:
        """Save only GUI choices; invalid pending input must not silently become a valid profile."""
        ds = self.state.active
        if ds is not None:
            errors = (ds.elab.errors or ds.capacity.reference_error or ds.dcir.param_errors or
                      ds.dcir.time_error or ds.dcir.current_time_error or ds.explorer.filter_error or
                      ds.capacity.view.filter_error or ds.dqdv.view.shown_error or ds.correction.threshold_error)
            if errors:
                raise ValueError("Fix the highlighted settings before saving a profile.")
        if ds is None:
            error = self.dcir_dock.default_settings_error()
            if error:
                raise ValueError(error)
            data = self.profiles.starting_preferences()
            data.update(theme=self.theme.mode, units=self.units.app_units, elaboration=self.user_defaults.params)
            data["comparison"] = snapshot(Dataset("", "", ""), self.comparison.cs,
                                          self.theme.mode, self.units.app_units)["comparison"]
            return data
        return snapshot(ds, self.comparison.cs, self.theme.mode, self.units.app_units)

    def preferences_blocker(self) -> str:
        """Profiles must not change the inputs of an in-flight operation."""
        for ds in self.state.datasets:
            if (ds.elab.is_running or ds.capacity.is_running or ds.dcir.is_running or ds.dqdv.is_running or
                    ds.correction.is_running or ds.explorer.locked or self.capacity.is_exporting(ds.id) or
                    self.dcir.is_exporting(ds.id) or self.dqdv.is_exporting(ds.id)):
                return "Wait for processing, rendering or export to finish before applying a profile."
        if self._active_job is not None or self.comparison._flat_cancel is not None:
            return "Wait for import or rendering to finish before applying a profile."
        return ""

    def apply_preferences(self, ident: str) -> None:
        blocker = self.preferences_blocker()
        if blocker:
            raise ValueError(blocker)
        data = self.profiles.get(ident)
        validate_preferences(data)
        ds = self.state.active
        if ds is not None:
            if ds.correction.applied is not None or ds.correction.removed_at:
                current = ds.dcir
                target = data["dcir"]
                if (current.params != target["params"] or current.measurement_time_s != target["measurement_time_s"] or
                        current.current_time_s != target["current_time_s"]):
                    raise ValueError("This profile changes DCIR detection or measurement settings. Process the original "
                                     "data again before changing them after Cycle Correction.")
        self.user_defaults.set(data["elaboration"])
        self.theme.set_mode(data["theme"])
        self.units.set_app_units(data["units"])
        if ds is not None:
            self.units.set_dataset_units(ds.id, data["dataset_units"])
            # Unit-change handlers reset axes. Restore the profile's ranges only afterwards,
            # since they are expressed in the profile's own display units.
            apply_dataset_preferences(ds, data)
            for ctl in (self.elab, self.explorer, self.capacity, self.dcir, self.dqdv):
                ctl.changed.emit(ds.id)
            self.explorer.request(ds.id)
        for key, value in data["comparison"].items():
            from copy import deepcopy
            setattr(self.comparison.cs, key, deepcopy(value))
        self.comparison.edit(lambda _cs: None)
        self.profiles.select(ident)
        if ds is None and getattr(self, "_settings_dialog", None) is not None:
            self.dcir_dock.set_preferences_mode(True, self.profiles)
        self.statusBar().showMessage("Profile applied. Run the relevant analysis explicitly to update scientific results.", 8000)

    def _push_units_to_data(self, ds_id: str) -> None:
        units = self.units.units_for(ds_id)
        self.data_page.set_units(ds_id, self.backend.data_columns(ds_id), units.time,
                                 self.units.is_custom(ds_id))

    def _units_changed(self, ds_id: str) -> None:
        self._push_units_to_data(ds_id)
        if hasattr(self.elab_page, "units_changed"):
            self.elab_page.units_changed(ds_id, self.units.is_custom(ds_id))

    def _toggle_dock(self, on: bool) -> None:
        self.dock.setVisible(on)

    def _demo_elaborate(self) -> None:
        if self.state.active:
            self.state.mark_elaborated(self.state.active.id)
            self.refresh_all()

    # --- import controller ---------------------------------------------------------------------------
    def open_file(self, mode: str) -> None:
        path = open_dataset(self, "Open battery dataset", SUPPORTED_FILTER)
        if path:
            self.start_import(path, mode)

    def start_import(self, path: str, mode: str, replace_id: str | None = None) -> None:
        if self._active_job is not None:
            self.statusBar().showMessage("Another import is already running.", 5000)
            return
        remember_import(path)                # dropped and re-mapped files also set the next Open folder
        if mode == "manual":
            try:
                insp = self.backend.inspect_source(path)
            except Exception as e:
                return self._fail(f"{type(e).__name__}: {e}", path, False)
            return self._manual_dialog(path, insp, replace_id)
        target = replace_id or uuid.uuid4().hex
        self._run_job(path, replace_id, target,
                      lambda cb, token: self.backend.import_auto(path, target, cb, token), "Auto Mapping")

    def remap(self, ds_id: str) -> None:
        ds = self.state.get(ds_id)
        if not ds:
            return
        if ds.elab.is_running:
            QMessageBox.information(self, "Re-map Columns", "This dataset is being processed. Cancel or wait for the "
                                    "processing to finish before re-mapping its columns.")
            return
        try:
            insp = self.backend.inspect_source(ds.path)
        except Exception as e:
            return self._fail(f"{type(e).__name__}: {e}", ds.path, False)
        self._manual_dialog(ds.path, insp, ds_id)

    def _manual_dialog(self, path: str, insp, replace_id: str | None) -> None:
        dlg = MapDialog(Path(path).name, insp, self.theme, self)
        if dlg.exec():
            m: ColumnMapping = dlg.mapping()
            target = replace_id or uuid.uuid4().hex
            self._run_job(path, replace_id, target,
                          lambda cb, token: self.backend.import_mapped(path, m, cb, token, target),
                          "Manual Mapping")
        elif not self.state.datasets:
            self.data_page.show_empty()

    def _run_job(self, path: str, replace_id: str | None, target: str, fn, method: str) -> None:
        import gregorithm
        token = gregorithm.CancellationToken()
        job_id = uuid.uuid4().hex
        self._active_job = {"id": job_id, "path": path, "replace": replace_id, "target": target,
                            "token": token, "mode": "auto" if method.startswith("Auto") else "manual"}
        self._set_import_actions(False)
        self.data_page.show_importing(Path(path).name)       # shown for every import
        sig = _Signals()
        self._active_job["signals"] = sig
        sig.progress.connect(lambda p, j=job_id: self._job_progress(j, p))
        sig.finished.connect(lambda result, j=job_id: self._import_done(j, result))
        sig.failed.connect(lambda msg, j=job_id: self._job_failed(j, msg))
        sig.cancelled.connect(lambda j=job_id: self._import_cancelled(j))
        sig.ambiguous.connect(lambda insp, j=job_id: self._on_ambiguous(j, insp))
        QThreadPool.globalInstance().start(_ImportJob(lambda cb: fn(cb, token), sig))

    def _set_import_actions(self, enabled: bool) -> None:
        self.act_auto.setEnabled(enabled)
        self.act_manual.setEnabled(enabled)
        self.band.add_btn.setEnabled(enabled)

    def _cancel_active(self) -> None:
        if self._active_job is not None:
            self._active_job["token"].cancel()
            self.data_page.importing.cancel.setEnabled(False)
            self.statusBar().showMessage("Cancelling import…")

    def _job_progress(self, job_id: str, progress) -> None:
        if self._active_job and self._active_job["id"] == job_id:
            self.data_page.importing.update_progress(progress)

    def _on_ambiguous(self, job_id: str, insp) -> None:
        if not self._active_job or self._active_job["id"] != job_id:
            return
        ctx = self._active_job
        self._active_job = None
        self._set_import_actions(True)
        self._manual_dialog(ctx["path"], insp, ctx.get("replace"))

    def _import_done(self, job_id: str, result: ImportResult) -> None:
        if not self._active_job or self._active_job["id"] != job_id:
            return
        ctx = self._active_job
        self._active_job = None
        self._set_import_actions(True)
        rid = ctx.get("replace")
        if rid and self.state.get(rid):
            ds = self.state.get(rid)
            old_result = ds.elab.result
            self.state.invalidate_elaboration(rid)            # re-import makes elaboration obsolete
            if old_result is not None:
                self.backend.release_result(old_result.result_id)
            self.explorer.result_replaced(rid)
        else:
            ds = Dataset(ctx["target"], Path(ctx["path"]).stem, ctx["path"])
            apply_dataset_preferences(ds, self.profiles.starting_preferences())
            ds.elab.params = self.user_defaults.params
            self.state.add(ds)
            self.band.add_tab(ds.id, ds.name)
        self.results[ds.id] = result
        self.units.dataset_added(ds.id)
        self.data_page.set_dataset_view(ds.id, ds.name, result, self.backend)
        self._push_units_to_data(ds.id)
        self.state.set_active(ds.id)
        self.refresh_all()
        self.statusBar().showMessage(f"Imported {ds.name}", 5000)

    def _elab_finished(self, ds_id: str, outcome: str) -> None:
        ds = self.state.get(ds_id)
        name = ds.name if ds else "dataset"
        message = {"ok": f"Processing finished: {name}",
                   "cancelled": f"Processing cancelled: {name}",
                   "failed": f"Processing failed: {name}"}[outcome]
        self.statusBar().showMessage(message, 6000)
        if ds_id in self._close_after_cancel:
            self._close_after_cancel.discard(ds_id)
            self.close_dataset(ds_id)
        else:
            self.refresh_all()

    def _import_cancelled(self, job_id: str) -> None:
        if not self._active_job or self._active_job["id"] != job_id:
            return
        self._active_job = None
        self._set_import_actions(True)
        self.statusBar().showMessage("Import cancelled", 5000)
        self.data_page.show_dataset(self.state.active_id)

    def _job_failed(self, job_id: str, msg: str) -> None:
        if not self._active_job or self._active_job["id"] != job_id:
            return
        ctx = self._active_job
        self._active_job = None
        self._set_import_actions(True)
        self._fail(msg, ctx.get("path"), ctx.get("mode") == "auto", ctx.get("replace"))

    def _fail(self, msg: str, path: str | None = None, offer_manual: bool | None = None,
              replace_id: str | None = None) -> None:
        """Modal error with what happened and a way forward; Auto failures offer Manual Mapping."""
        path = path or ""
        offer = bool(offer_manual)
        self.data_page.show_dataset(self.state.active_id)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Import Failed")
        box.setText(f"Could not import {Path(path).name or 'the file'}.")
        box.setInformativeText(msg)
        manual = box.addButton("Manual Mapping…", QMessageBox.ButtonRole.AcceptRole) if offer and path else None
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if manual is not None and box.clickedButton() is manual:
            self.start_import(path, "manual", replace_id)

    def closeEvent(self, event) -> None:
        """Closing this window closes the application: the window disappears at once, every other window closes, every
        job of every section is cancelled and the workers get at most SHUTDOWN_WAIT_MS to stop (app.shutdown ends the
        process afterwards, whatever is still running). `shutdownFinished` tells the entry point to leave the event
        loop. Limits: backend files are closed only when no worker can still be using them."""
        if self._closing:
            super().closeEvent(event)
            return
        self._closing = True
        STANDBY.open_all()                                  # paused workers must reach their cancellation checks
        self.hide()
        dialog = getattr(self, "_settings_dialog", None)
        if dialog is not None:
            dialog.reject()
        for widget in QApplication.topLevelWidgets():     # Help, dialogs: would otherwise outlive this window
            if widget is not self and widget.isVisible():
                widget.close()
        if self._active_job is not None:
            self._active_job["token"].cancel()
        for controller in (self.elab, self.explorer, self.capacity, self.dcir, self.correction, self.gitt_ici,
                           self.dqdv):
            controller.cancel_all()
        self.comparison.close()
        pool = QThreadPool.globalInstance()
        pool.clear()                                      # jobs queued but not started never start
        # Do not destroy backend-owned Parquet handles while a cooperative core call is still unwinding.
        if pool.waitForDone(SHUTDOWN_WAIT_MS):
            close_backend = getattr(self.backend, "close", None)
            if close_backend is not None:
                close_backend()
        super().closeEvent(event)
        self.shutdownFinished.emit()
