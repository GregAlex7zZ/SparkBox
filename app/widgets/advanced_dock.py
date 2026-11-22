"""Right dock, closed by default, contextual per section.

The dock has one fixed width (DOCK_WIDTH) for every section: opening it never resizes or re-flows its contents, so
each section dock is laid out for exactly that width (vertical scrolling only, never horizontal)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QStackedWidget, QVBoxLayout

DOCK_WIDTH = 360          # px, outer width of the dock card; every section dock is designed for it


class AdvancedDock(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("DockCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        self.title = QLabel("Advanced Settings")
        self.title.setStyleSheet("font-size: 11.25pt; font-weight: 500;")
        self.body = QLabel("No advanced settings in this section yet.")
        self.body.setProperty("muted", True)
        self.body.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.body)
        lay.addStretch(1)

    def set_title(self, text: str) -> None:
        self.title.setText(text)


class DockHost(QStackedWidget):
    """The right dock is contextual per section: Data Elaboration shows its own settings, other sections the generic
    placeholder until their step is designed."""

    def __init__(self, docks: dict):
        super().__init__()
        self.setObjectName("DockHost")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(DOCK_WIDTH)
        self.generic = AdvancedDock()
        self.docks = dict(docks)
        self.addWidget(self.generic)
        for dock in self.docks.values():
            self.addWidget(dock)

    def set_section(self, section, title: str) -> None:
        self.generic.set_title(title)
        widget = self.docks.get(section, self.generic)
        if self.indexOf(widget) >= 0:  # a modal Settings dialog can temporarily host the same panels
            self.setCurrentWidget(widget)

