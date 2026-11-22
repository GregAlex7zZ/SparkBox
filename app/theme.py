"""Theme manager (System / Light / Dark) and the single application stylesheet."""
from __future__ import annotations
from dataclasses import replace
from string import Template
from PySide6.QtCore import QEventLoop, QObject, QSettings, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget
from .chevrons import chevron_file
from .fonts import UI_FONT
from .tokens import DARK, LIGHT, Tokens

MODES = ("system", "light", "dark")


def _luminance(c: QColor) -> float:
    """WCAG 2 relative luminance of an sRGB colour."""
    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.redF()) + 0.7152 * ch(c.greenF()) + 0.0722 * ch(c.blueF())


def contrast(a: str, b: str) -> float:
    """WCAG 2 contrast ratio of two colours (1 to 21)."""
    la, lb = sorted((_luminance(QColor(a)), _luminance(QColor(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _mix(a: str, b: str, t: float) -> str:
    """`a` blended over `b` with weight t (0..1)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() * t + cb.red() * (1 - t)), round(ca.green() * t + cb.green() * (1 - t)),
                  round(ca.blue() * t + cb.blue() * (1 - t))).name().upper()


def accent_tokens(base: Tokens, accent: str) -> Tokens:
    """System mode: `base` (Light or Dark) with the operating-system accent as the primary colour.

    The accent is darkened (Light) or lightened (Dark) only as far as needed for 4.5:1 contrast against the card, so
    it stays readable as text and as a fill; `on_pri` is black or white, whichever contrasts more; `tint` is the
    accent lightly blended into the card. Charts, status colours and the amber of the compute buttons are unchanged.
    Limits: a nearly grey accent gives a grey primary; explicit Light and Dark use their own (orange) primary.
    """
    c = QColor(accent)
    if not c.isValid():
        return base
    toward = QColor("#000000") if base.name == "light" else QColor("#FFFFFF")
    pri = c.name().upper()
    for step in range(1, 21):
        if contrast(pri, base.card) >= 4.5:
            break
        pri = _mix(toward.name(), c.name(), step * 0.05)
    on_pri = "#FFFFFF" if contrast("#FFFFFF", pri) >= contrast("#101418", pri) else "#101418"
    tint = _mix(pri, base.card, 0.12 if base.name == "light" else 0.22)
    return replace(base, pri=pri, on_pri=on_pri, tint=tint)

QSS = Template(r"""
* { font-family: "Inter", "Segoe UI", "Noto Sans", sans-serif; font-size: 9.75pt; }
QMainWindow, QDialog, #Root { background: $bg; }
QWidget { color: $text; }
QLabel { background: transparent; }
QMenuBar { background: $bg; border-bottom: 1px solid $border; padding: 0 6px; }
QMenuBar::item { padding: 4px 10px; border-radius: 6px; background: transparent; }
QMenuBar::item:selected { background: $alt; }
QMenu { background: $card; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 8px 18px; border-radius: 6px; }
QMenu::item:selected { background: $tint; color: $text; }
QMenu::item:disabled { color: $dis; }
QMenu::separator { height: 1px; background: $border; margin: 4px 6px; }
QStatusBar { background: $bg; border-top: 1px solid $border; color: $muted; font-size: 9pt; }
QStatusBar::item { border: 0; }
QToolTip { background: $card; color: $text; border: 1px solid $ctrl; padding: 4px 8px; }

#Brand { color: $pri; font-size: 18pt; font-weight: 700; }
#SideCard, #ContentCard { background: $card; border: 1px solid $border; border-radius: 14px; }
#DockHost { background: $card; border: 1px solid $border; border-radius: 14px; }
#DockHost #DockCard { background: transparent; border: 0; border-radius: 14px; }
#DockHost QScrollArea, #DockHost QWidget { background: transparent; }
/* ...except combo-box drop-down lists, which are separate popup windows: transparent there renders black. */
#DockHost QComboBox QAbstractItemView, #DockHost QComboBox QAbstractItemView QWidget { background: $card; }
#DockHost QPushButton[goElab="true"] { background: $pri; }
#DockHost QPushButton[goElab="true"]:disabled, #DockHost QPushButton[go="true"]:disabled { background: $alt; }
#DockHost QPushButton[go="true"] { background: $tint; }
QSplitter::handle { background: transparent; }

QPushButton[nav="true"] { text-align: left; padding: 0 12px; border-radius: 10px; border: 1px solid transparent;
  background: $alt; color: $text; }
QPushButton[nav="true"][collapsed="true"] { text-align: center; padding: 0; }
QPushButton[nav="true"]:hover:enabled:!checked { border-color: $ctrl; }
QPushButton[nav="true"]:focus { border-color: $pri; }
QPushButton[nav="true"]:checked { background: $pri; border-color: $pri; color: $on_pri; font-weight: 500; }
QPushButton[nav="true"]:disabled { background: transparent; color: $dis; border-color: transparent; }
QPushButton#NavExport:enabled { border-color: $ctrl; }
#QuietButton { border: 0; border-radius: 8px; background: transparent; }
#QuietButton:hover { background: $alt; }

#TabBox { background: $card; border: 1.5px solid $border; border-radius: 10px; }
#TabBox[active="true"] { background: $tint; border-color: $pri; }
#TabLabel { border: 0; background: transparent; text-align: left; padding: 0; color: $muted; }
#TabBox[active="true"] #TabLabel { color: $text; font-weight: 500; }
#TabClose { border: 0; border-radius: 6px; background: transparent; }
#TabClose:hover { background: $alt; }
#TabStrip, QScrollArea#TabScroll { background: transparent; border: 0; }
#ChevButton { border: 1px solid $border; border-radius: 8px; background: $card; }
#ChevButton:hover:enabled { border-color: $ctrl; }
#ChevButton:disabled { background: $bg; }
#MapGrid { background: $card; border: 0; }
#AddTab { border: 1px solid $border; border-radius: 8px; background: $card; }
#AddTab:hover:enabled { border-color: $ctrl; background: $alt; }
#AddTab:focus { border-color: $pri; }
#AddTab::menu-indicator { image: none; width: 0; }
#Sep { background: $border; }
#AdvButton { padding: 0 12px; border: 1px solid $ctrl; border-radius: 8px; background: $card; color: $text; }
#AdvButton:checked { background: $tint; border-color: $pri; }
#AdvButton:hover { background: $alt; }

#DropZone { border: 1.5px dashed $ctrl; border-radius: 14px; background: $bg; }
#DropZone[drag="true"] { border-color: $pri; background: $tint; }
#SplitMain { height: 40px; padding: 0 20px; border: 0; border-radius: 10px; background: $pri; color: $on_pri;
  font-size: 10.5pt; font-weight: 500; }
#SplitArrow { height: 40px; width: 38px; border: 0; border-left: 1px solid $on_pri; background: $pri; border-radius: 10px; }
QPushButton[ghost="true"], QPushButton[go="true"] { height: 30px; padding: 0 12px; border: 1px solid $ctrl; border-radius: 8px;
  background: $card; color: $text; }
QPushButton[ghost="true"]:hover { background: $alt; }
QPushButton[ghost="true"]:checked { background: $tint; border-color: $pri; }
QPushButton[go="true"] { border: 0; background: $tint; color: $pri; font-weight: 500; padding: 0 14px; }
QPushButton[goElab="true"] { height: 30px; padding: 0 14px; border: 0; border-radius: 8px; background: $pri;
  color: $on_pri; font-weight: 500; }
QPushButton[goElab="true"]:hover { background: $pri; }
QPushButton[goElab="true"]:disabled { background: $alt; color: $dis; }
#Step { height: 30px; padding: 0 14px; border: 1px solid $ctrl; border-radius: 15px; background: $card; color: $text;
  font-size: 10pt; font-weight: 500; }
#Step:hover { background: $tint; border-color: $pri; color: $pri; }
#Step[current="true"] { background: $pri; border-color: $pri; color: $on_pri; }
#Step:disabled { background: $bg; border-color: $border; color: $dis; }
#StepNum { min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; border-radius: 11px;
  background: $tint; color: $pri; font-weight: 500; qproperty-alignment: AlignCenter; }
#Segmented[stepper="true"] QPushButton { height: 26px; padding: 0 14px; font-size: 10pt; font-weight: 500; color: $text; }
#Segmented[stepper="true"] QPushButton:hover:!checked { background: $tint; color: $pri; }
#Segmented[stepper="true"][changed="true"] { border: 1px solid $pri; }
#Segmented[stepper="true"][changed="true"] QPushButton:checked { background: $pri; color: $on_pri; }
#Segmented[stepper="true"] QPushButton:disabled { color: $dis; }
QPushButton[primary="true"] { height: 36px; padding: 0 22px; border: 0; border-radius: 10px; background: $pri;
  color: $on_pri; font-size: 10.5pt; font-weight: 500; }
QPushButton[primary="true"]:disabled { background: $alt; color: $dis; }
QPushButton[secondary="true"] { height: 36px; padding: 0 18px; border: 1px solid $ctrl; border-radius: 10px;
  background: $card; color: $text; font-size: 10.5pt; }
QLineEdit { height: 30px; padding: 0 10px; border: 1px solid $ctrl; border-radius: 8px; background: $card; }
QLineEdit:focus { border-color: $pri; }
/* Spin boxes (dQ/dV and Comparison line width / opacity) look like line edits, with themed chevron steppers. */
QAbstractSpinBox { height: 30px; padding: 0 4px 0 10px; border: 1px solid $ctrl; border-radius: 8px; background: $card;
  color: $text; selection-background-color: $tint; selection-color: $text; }
QAbstractSpinBox:focus { border-color: $pri; }
QAbstractSpinBox:disabled { color: $dis; border-color: $border; background: $bg; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { subcontrol-origin: border; width: 20px; border: 0;
  background: transparent; margin-right: 3px; }
QAbstractSpinBox::up-button { subcontrol-position: top right; margin-top: 2px; }
QAbstractSpinBox::down-button { subcontrol-position: bottom right; margin-bottom: 2px; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover { background: $alt; border-radius: 4px; }
QAbstractSpinBox::up-arrow { image: url("$chev_up_small"); width: 12px; height: 12px; }
QAbstractSpinBox::down-arrow { image: url("$chev_down_small"); width: 12px; height: 12px; }
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off { image: url("$chev_up_small_dis"); }
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off { image: url("$chev_down_small_dis"); }
QComboBox { height: 32px; min-height: 32px; padding: 0 8px 0 10px; border: 1px solid $ctrl; border-radius: 8px;
  background: $card; color: $text; }
QComboBox[changed="true"] { border: 1px solid $pri; }
QComboBox:hover:enabled { background: $alt; }
QComboBox:focus, QComboBox:on { border: 2px solid $pri; padding: 0 7px 0 9px; }
QComboBox:disabled { color: $dis; border-color: $border; background: $bg; }
QComboBox::drop-down { width: 24px; border: 0; background: transparent; subcontrol-origin: padding;
  subcontrol-position: center right; }
QComboBox::down-arrow { image: url("$chev_down"); width: 16px; height: 16px; }
QComboBox::down-arrow:on { image: url("$chev_up_pri"); }
QComboBox::down-arrow:focus { image: url("$chev_down_pri"); }
QComboBox::down-arrow:disabled { image: url("$chev_down_dis"); }
QComboBox QAbstractItemView { background: $card; border: 1px solid $border; border-radius: 10px; padding: 4px;
  outline: 0; selection-background-color: $tint; selection-color: $text; }
QComboBox QAbstractItemView::item { min-height: 32px; padding: 0 10px; border-radius: 6px; }
QComboBox QAbstractItemView::item:selected { background: $tint; color: $text; }
QLabel[chip="true"] { padding: 4px 10px; border: 1px solid $border; border-radius: 8px; background: $alt; font-size: 9pt; }
QLabel[chip="true"][required="true"] { background: $tint; border-color: $pri; color: $pri; font-weight: 500; }
QLabel[muted="true"] { color: $muted; }
QLabel[small="true"] { font-size: 9pt; }
QLabel[paneTitle="true"] { font-size: 13pt; font-weight: 600; }
QLabel[cardTitle="true"] { font-weight: 600; }
QPushButton#ProfileCard { text-align: left; padding: 0; border: 1px solid $border; border-radius: 10px; background: $alt; }
QPushButton#ProfileCard:hover:!checked { border-color: $ctrl; }
QPushButton#ProfileCard:checked { background: $tint; border-color: $pri; }
QPushButton#ProfileCard:focus { border: 2px solid $pri; }
#SettingsNav { background: transparent; padding: 0; }
#SettingsNav::item { min-height: 34px; padding: 0 10px; }
#ProfileList { border: 1px solid $border; border-radius: 10px; padding: 4px; }
#ProfileList::item { min-height: 32px; padding: 0 10px; }
#HowTo { background: $card; border: 1px solid $border; border-radius: 10px; }
QLabel#StepNum { background: $tint; color: $pri; border-radius: 10px; font-weight: 600; font-size: 9pt; }
QLabel[ok="true"] { color: $success; }
QFrame[banner="true"] { background: $tint; border-radius: 10px; }
QFrame[panel="true"] { border: 1px solid $border; border-radius: 10px; background: $card; }
QProgressBar { background: $alt; border: 0; border-radius: 4px; max-height: 8px; min-height: 8px; }
QProgressBar::chunk { background: $pri; border-radius: 4px; }
QTableView { background: $card; border: 1px solid $border; border-radius: 10px; gridline-color: transparent;
  selection-background-color: $tint; selection-color: $text; alternate-background-color: $card; }
QTableView::item { border-top: 1px solid $border; padding: 0 16px; }
QHeaderView::section { background: $alt; color: $muted; border: 0; padding: 0 16px; font-size: 9pt; }
QTreeView { background: $card; color: $text; border: 1px solid $border; border-radius: 10px; outline: 0;
  selection-background-color: $tint; selection-color: $text; alternate-background-color: $card; }
QTreeView::item { min-height: 26px; }
QTreeView::item:selected { background: $tint; color: $text; }
QTextEdit { background: $card; color: $text; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QTabBar::tab { background: transparent; color: $muted; border: 0; border-bottom: 2px solid transparent;
  padding: 6px 14px; }
QTabBar::tab:selected { color: $pri; border-bottom-color: $pri; font-weight: 500; }
QTabBar::tab:hover:!selected { color: $text; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $ctrl; border-radius: 3px; min-height: 28px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $ctrl; border-radius: 3px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QListView { background: $card; border: 0; outline: 0; padding: 2px; }
QListView::item { border-radius: 8px; margin: 1px 0; color: $text; border: 1px solid transparent; }
QListView::item:hover:!selected { background: $alt; }
QListView::item:selected { background: $tint; color: $text; border: 1px solid $pri; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1.5px solid $ctrl; border-radius: 4px; background: $card; }
QCheckBox::indicator:hover:enabled { border-color: $pri; }
QCheckBox::indicator:checked { background: $pri; border-color: $pri; image: url("$check_on"); }
QCheckBox::indicator:disabled { background: $bg; border-color: $border; }
QCheckBox::indicator:checked:disabled { background: $dis; border-color: $dis; }

#Segmented { border: 1px solid $ctrl; border-radius: 8px; background: $card; }
#Segmented QPushButton { border: 0; border-radius: 6px; background: transparent; color: $muted; padding: 0 14px;
  height: 28px; font-weight: 400; }
#Segmented[small="true"] QPushButton { height: 24px; padding: 0 12px; font-size: 9pt; }
#Segmented[changed="true"] { border-color: $pri; }
#Segmented QPushButton:checked { background: $tint; color: $pri; font-weight: 500; }
#Segmented QPushButton:hover:!checked { background: $alt; }
#Segmented QPushButton:focus { border: 1px solid $pri; }
QFrame[pill] { border-radius: 13px; min-height: 26px; max-height: 26px; }
QFrame[pill] QLabel { font-size: 9pt; font-weight: 500; }
QFrame[pill="n"] { background: $alt; }
QFrame[pill="n"] QLabel { color: $muted; }
QFrame[pill="p"] { background: $tint; }
QFrame[pill="p"] QLabel { color: $pri; }
QFrame[pill="ok"] { background: $ok_bg; }
QFrame[pill="ok"] QLabel { color: $success; }
QFrame[pill="warn"] { background: $warn_bg; }
QFrame[pill="warn"] QLabel { color: $warn; }
QFrame[pill="err"] { background: $err_bg; }
QFrame[pill="err"] QLabel { color: $err; }
QLabel[tag="true"] { background: $warn_bg; color: $warn; font-size: 8.25pt; font-weight: 500; padding: 0 7px;
  border-radius: 9px; min-height: 18px; max-height: 18px; }
QLabel[pending="true"] { background: $warn_bg; color: $warn; font-size: 9pt; font-weight: 500; padding: 0 9px;
  border-radius: 11px; min-height: 22px; max-height: 22px; }
QFrame[banner="n"], QFrame[banner="warn"], QFrame[banner="err"] { border-radius: 10px; border-left: 3px solid $muted; }
QFrame[banner="n"] { background: $alt; }
QFrame[banner="warn"] { background: $warn_bg; border-left-color: $warn; }
QFrame[banner="err"] { background: $err_bg; border-left-color: $err; }
QLabel[hintErr="true"] { color: $err; font-size: 8.25pt; font-weight: 500; }
QLineEdit[error="true"] { border: 1px solid $err; }
QLineEdit[changed="true"] { border: 1px solid $pri; }
QPushButton[link="true"] { border: 0; background: transparent; color: $pri; font-weight: 500; text-decoration: underline;
  padding: 0 0 0 6px; }
QPushButton[quiet="true"] { border: 0; background: transparent; color: $muted; padding: 0 6px; height: 24px; }
QPushButton[quiet="true"]:hover { color: $text; }
QPushButton[secondary="true"]:disabled { color: $dis; border-color: $border; }
QPushButton[ghost="true"]:disabled { color: $dis; border-color: $border; }
QToolButton[info="true"] { border: 0; border-radius: 10px; background: transparent; }
QToolButton[info="true"]:hover { background: $alt; }
#ChartTool { border: 0; border-radius: 6px; background: transparent; }
#ChartTool:hover:enabled { background: $alt; }
#ChartTool:checked { background: $tint; }
#ChartTool:focus { border: 1px solid $pri; }
QPushButton[go="true"]:disabled { background: $alt; color: $dis; }
QPushButton[ghost="true"]:checked:disabled { background: $tint; color: $pri; border-color: $pri; }
#GroupHead { border: 0; background: transparent; text-align: left; font-weight: 500; height: 28px; padding: 0; }
#Popover { background: $card; border: 1px solid $ctrl; border-radius: 10px; }
#Rule { background: $border; max-height: 1px; min-height: 1px; }
#BigNumber { font-size: 33pt; font-weight: 500; }
QTabWidget::pane { border: 0; }
/* Amber = starts a calculation (button property run=true). Last, so it wins over primary / goElab / #DockHost. */
QPushButton[run="true"], #DockHost QPushButton[run="true"] { border: 0; background: $run; color: $on_run; }
QPushButton[run="true"]:hover, #DockHost QPushButton[run="true"]:hover { background: $run_hover; }
QPushButton[run="true"]:focus { border: 2px solid $text; }
QPushButton[run="true"]:disabled, #DockHost QPushButton[run="true"]:disabled { background: $alt; color: $dis; }
QFrame[banner="next"] { background: $alt; border-radius: 10px; border-left: 3px solid $run; }
""")


def repolish(w: QWidget) -> None:
    for x in [w] + w.findChildren(QWidget):
        x.style().unpolish(x)
        x.style().polish(x)
        x.update()


def set_prop(w: QWidget, name: str, value) -> None:
    """Set a dynamic property used by the stylesheet and re-polish ``w`` and its children so the style follows.

    Re-polishing a widget tree is expensive (style unpolish + polish of every descendant), and pages call this on
    every refresh, which runs on each progress tick of a calculation. When the property already holds ``value`` the
    stylesheet result cannot change, so nothing is done. Theme changes re-apply the whole stylesheet separately and do
    not rely on this call."""
    if w.property(name) == value:
        return
    w.setProperty(name, value)
    repolish(w)


class ThemeManager(QObject):
    """Resolves the appearance mode to design tokens and applies the single application stylesheet.

    Re-styling every live widget is the slowest thing the GUI does (all pages are built up front, about 2,000 widgets,
    1-3 s). So `apply()` skips the work when the resolved tokens did not change, and otherwise runs in stages: it emits
    `applyStarted` (the main window shows a "Applying theme…" overlay), lets the event loop paint it, re-styles, and
    pumps the event loop between the remaining stages so Windows never marks the window as "Not responding". User input
    is excluded while it runs. `applyFinished` always follows `applyStarted`. Limits: the single `setStyleSheet` call
    itself cannot be split, so the overlay's progress bar pauses during it.
    """
    changed = Signal()
    applyStarted = Signal()
    applyFinished = Signal()

    def __init__(self, app: QApplication):
        super().__init__()
        self._app = app
        self._settings = QSettings("gregorithm", "gui")
        mode = str(self._settings.value("appearance", "system"))
        self.mode = mode if mode in MODES else "system"         # default System: follows light/dark
        self.tokens: Tokens = LIGHT
        self._applied: Tokens | None = None                     # tokens of the stylesheet currently installed
        self._applying = False
        self._pending = False
        hints = app.styleHints()
        if hasattr(hints, "colorSchemeChanged"):                 # Qt >= 6.5
            hints.colorSchemeChanged.connect(self._on_color_scheme_changed)
        if hasattr(app, "paletteChanged"):                       # the user changed the Windows accent colour
            app.paletteChanged.connect(lambda _p: self._on_color_scheme_changed(None))

    @Slot(Qt.ColorScheme)
    def _on_color_scheme_changed(self, _scheme: Qt.ColorScheme) -> None:
        if self.mode == "system":
            self.apply()

    def set_mode(self, mode: str) -> None:
        """Store the mode and re-style the app; a no-op when the resolved colours stay the same."""
        if mode in MODES:
            self.mode = mode
            self._settings.setValue("appearance", mode)
            self.apply()

    def _resolve(self) -> Tokens:
        if self.mode == "light":
            return LIGHT
        if self.mode == "dark":
            return DARK
        hints = self._app.styleHints()
        if hasattr(hints, "colorScheme"):
            base = DARK if hints.colorScheme() == Qt.ColorScheme.Dark else LIGHT
        else:
            base = DARK if self._app.palette().window().color().lightness() < 128 else LIGHT
        return accent_tokens(base, self.system_accent())

    def system_accent(self) -> str:
        """The operating-system accent colour (Windows: Settings > Personalization > Colors) as #RRGGBB."""
        pal = QApplication.palette()
        role = getattr(QPalette.ColorRole, "Accent", QPalette.ColorRole.Highlight)   # Accent: Qt >= 6.6
        return pal.color(role).name()

    def _pump(self) -> None:
        """Let Qt paint and answer the OS (no user input) between the stages of a re-style."""
        self._app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def apply(self, force: bool = False) -> None:
        """Apply the stylesheet of the resolved tokens (synchronous; see the class notes)."""
        if self._applying:                                       # re-entered from _pump: run once more afterwards
            self._pending = True
            return
        tokens = self._resolve()
        if not force and tokens == self._applied:
            return
        self._applying = True
        self.applyStarted.emit()
        busy = self._applied is not None                         # first styling at start-up: nothing to show yet
        if busy:
            self._app.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._pump()
        try:
            self.tokens = tokens
            vals = dict(tokens.__dict__)
            vals.update(chev_down=chevron_file("down", tokens.muted), chev_down_pri=chevron_file("down", tokens.pri),
                        chev_up_pri=chevron_file("up", tokens.pri), chev_down_dis=chevron_file("down", tokens.dis),
                        check_on=chevron_file("check", tokens.on_pri, 14),
                        chev_up_small=chevron_file("up", tokens.muted, 12), chev_down_small=chevron_file("down", tokens.muted, 12),
                        chev_up_small_dis=chevron_file("up", tokens.dis, 12),
                        chev_down_small_dis=chevron_file("down", tokens.dis, 12))
            # A concrete application font also protects Qt teardown from inherited
            # fonts with an invalid (-1) point size (BUG-001).
            font = QFont(UI_FONT)
            font.setPointSizeF(9.75)
            font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)   # smooth, crisp baselines
            self._app.setFont(font)
            self._app.setStyleSheet(QSS.substitute(vals))
            self._applied = tokens
            if busy:
                self._pump()
            # Some Qt style-sheet pixel declarations otherwise leave the effective
            # QFont point size at -1.  Convert their already-resolved pixel metric
            # to a real point size (96 DPI) instead of allowing an invalid font to
            # reach widget destruction.
            self._normalize_widget_fonts()
            self.changed.emit()
            # Theme listeners may rebuild dynamic rows (for example Comparison
            # dataset entries). Normalize those newly-created widgets as well.
            self._normalize_widget_fonts()
            if busy:
                self._pump()
        finally:
            if busy:
                self._app.restoreOverrideCursor()
            self._applying = False
            self.applyFinished.emit()
        if self._pending:
            self._pending = False
            self.apply()

    def _normalize_widget_fonts(self) -> None:
        for widget in self._app.allWidgets():
            resolved = widget.font()
            if resolved.pointSizeF() <= 0:
                px = resolved.pixelSize()
                resolved.setPointSizeF((px if px > 0 else 13) * 0.75)
                widget.setFont(resolved)

