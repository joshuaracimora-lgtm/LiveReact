"""Add-fixture wizard: three ways in, one way out (the channel-test screen).

Pages (roadmap item 23): Search Open Fixture Library (item 25, ui/ofl_worker.py), Upload the
manual (the AI reader, items 26-28, ui/manual_upload.py), Enter by hand (a channel table
producing a ``user`` definition). Pages whose backend is not built yet say so. Every route ends on ui/channel_test.py, where Confirm
saves the definition to the user's library.
"""

from __future__ import annotations

import re

from PySide6 import QtCore, QtWidgets

from .. import userdata
from ..fixtures.definition import CATEGORIES, FUNCTIONS, SCHEMA, DefinitionError, load_definition, validate
from ..fixtures.library import rigs_using, save_user_definition
from .channel_test import ChannelTestPanel
from .manual_upload import UploadManualPage
from .ofl_worker import OflSearchPage

PAGE_START, PAGE_OFL, PAGE_MANUAL, PAGE_HAND, PAGE_TEST = range(5)
NOT_BUILT = "Not available in this version yet — use “Enter by hand” for now."
NEXT_STEP = "Next: open the Rig Config tab, press Add fixture, and pick it in the Type list."
# scenes and engines write the wash role only through isinstance(fx, (Par, WashBar)) (roadmap item 15)
WASH_LIMIT = "Known limit in this version: the show does not light a fixture added here on the wash role."
HEAD_ROLE = "Set its Role to spot: the show does not light a fixture added here on the wash role yet."


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:80]


class HandEntryPage(QtWidgets.QWidget):
    """Manufacturer / model / category / mode name, and one row per channel (function, default)."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        v = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.e_manufacturer = QtWidgets.QLineEdit()
        self.e_model = QtWidgets.QLineEdit()
        self.cmb_category = QtWidgets.QComboBox()
        self.cmb_category.addItems(list(CATEGORIES))
        self.e_mode = QtWidgets.QLineEdit()
        self.e_mode.setPlaceholderText("e.g. 7ch")
        form.addRow("Manufacturer", self.e_manufacturer)
        form.addRow("Model", self.e_model)
        form.addRow("Category", self.cmb_category)
        form.addRow("DMX mode name", self.e_mode)
        v.addLayout(form)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Channel", "Function", "Default"])
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        self.b_add = QtWidgets.QPushButton("Add channel")
        self.b_remove = QtWidgets.QPushButton("Remove last")
        row.addWidget(self.b_add)
        row.addWidget(self.b_remove)
        row.addStretch(1)
        v.addLayout(row)
        self.l_errors = QtWidgets.QLabel("")
        self.l_errors.setWordWrap(True)
        self.l_errors.setStyleSheet("color:#c33;")
        v.addWidget(self.l_errors)
        self.b_add.clicked.connect(lambda: self.add_channel())
        self.b_remove.clicked.connect(self.remove_last)

    def add_channel(self, function: str = "dimmer", default: int = 0) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        num = QtWidgets.QTableWidgetItem(str(r + 1))
        num.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(r, 0, num)
        fn = QtWidgets.QComboBox()
        fn.addItems(list(FUNCTIONS))
        fn.setCurrentText(function)
        self.table.setCellWidget(r, 1, fn)
        dv = QtWidgets.QSpinBox()
        dv.setRange(0, 255)
        dv.setValue(default)
        self.table.setCellWidget(r, 2, dv)

    def remove_last(self) -> None:
        if self.table.rowCount():
            self.table.removeRow(self.table.rowCount() - 1)

    def definition_dict(self) -> dict:
        functions = [self.table.cellWidget(r, 1).currentText() for r in range(self.table.rowCount())]
        channels = []
        for r, f in enumerate(functions):
            ch = {"offset": r, "function": f, "default": self.table.cellWidget(r, 2).value()}
            if f.endswith("_fine"):
                coarse = f[: -len("_fine")]
                if coarse in functions:
                    ch["fine_of"] = functions.index(coarse)
            channels.append(ch)
        manufacturer, model = self.e_manufacturer.text().strip(), self.e_model.text().strip()
        return {
            "schema": SCHEMA, "id": slug(f"{manufacturer} {model}"),
            "manufacturer": manufacturer, "model": model, "category": self.cmb_category.currentText(),
            "source": {"kind": "user", "confirmed": False},
            "modes": [{"name": self.e_mode.text().strip() or f"{len(channels)}ch", "channels": channels}],
        }

    def build(self):
        """The entered definition, or None with the problems shown on the page."""
        raw = self.definition_dict()
        errs = []
        if not raw["manufacturer"] or not raw["model"]:
            errs.append("enter the manufacturer and the model")
        elif not raw["id"]:
            errs.append("the manufacturer or the model needs at least one letter or digit")
        channels = raw["modes"][0]["channels"]
        if not channels:
            errs.append("add at least one channel")
        elif raw["category"] == "moving_head" and not {"pan", "tilt"} <= {c["function"] for c in channels}:
            errs.append("a moving head needs pan and tilt channels")
        for ch in channels:
            if ch["function"].endswith("_fine") and "fine_of" not in ch:
                fine = ch["function"].replace("_", " ")
                errs.append(f"{fine} needs a {fine[:-len(' fine')]} channel in the list")
        d = None
        if not errs:
            try:
                d = load_definition(raw)
                errs = validate(d)
            except DefinitionError as exc:
                errs = [str(exc)]
        self.l_errors.setText("; ".join(errs))
        return None if errs else d


def _default_ofl_service():
    from ..fixtures.ofl import OflService, default_cache_dir
    return OflService(default_cache_dir())


class FixtureWizard(QtWidgets.QDialog):
    added = QtCore.Signal(object)   # the confirmed, saved FixtureDefinition

    def __init__(self, parent: QtWidgets.QWidget | None = None, save=None,
                 ofl_service_factory=None, threaded: bool = True, manual_page_kwargs: dict | None = None,
                 laser_armed=None, confirm=None) -> None:
        """``save``: what Confirm writes through (default: the user library).
        ``confirm(title, text) -> bool``: the replace-a-saved-fixture box (default: a QMessageBox).
        ``ofl_service_factory() -> OflService`` (default: the real one, cached in the user folder).
        ``manual_page_kwargs``: passed to UploadManualPage (tests inject a fake reader / key).
        ``laser_armed()``: the console's arm switch, the channel-test screen's second laser key."""
        super().__init__(parent)
        self.setWindowTitle("Add a fixture")
        self._save = save
        self._laser_armed = laser_armed
        self._confirm = confirm if confirm is not None else self._ask
        self.test_panel: ChannelTestPanel | None = None
        v = QtWidgets.QVBoxLayout(self)
        self.stack = QtWidgets.QStackedWidget()
        v.addWidget(self.stack, 1)

        start = QtWidgets.QWidget()
        sl = QtWidgets.QVBoxLayout(start)
        sl.addWidget(QtWidgets.QLabel("How do you want to add the fixture?"))
        self.b_ofl = QtWidgets.QPushButton("Search Open Fixture Library")
        self.b_manual = QtWidgets.QPushButton("Upload the fixture's manual (PDF)")
        self.b_hand = QtWidgets.QPushButton("Enter the channels by hand")
        for b in (self.b_ofl, self.b_manual, self.b_hand):
            sl.addWidget(b)
        sl.addStretch(1)
        self.stack.addWidget(start)
        self.ofl_page = OflSearchPage(ofl_service_factory or _default_ofl_service, threaded=threaded)
        self.ofl_page.imported.connect(self.open_channel_test)
        self.stack.addWidget(self.ofl_page)
        self.manual_page = UploadManualPage(save=save, threaded=threaded, **(manual_page_kwargs or {}))
        self.manual_page.drafted.connect(self.open_channel_test)
        self.stack.addWidget(self.manual_page)
        self.hand = HandEntryPage()
        self.stack.addWidget(self.hand)
        self.test_holder = QtWidgets.QWidget()
        QtWidgets.QVBoxLayout(self.test_holder)
        self.stack.addWidget(self.test_holder)

        nav = QtWidgets.QHBoxLayout()
        self.b_back = QtWidgets.QPushButton("Back")
        self.b_next = QtWidgets.QPushButton("Test channels")
        nav.addWidget(self.b_back)
        nav.addStretch(1)
        nav.addWidget(self.b_next)
        v.addLayout(nav)

        self.b_ofl.clicked.connect(lambda: self.go(PAGE_OFL))
        self.b_manual.clicked.connect(lambda: self.go(PAGE_MANUAL))
        self.b_hand.clicked.connect(lambda: self.go(PAGE_HAND))
        self.b_back.clicked.connect(lambda: self.go(PAGE_START))
        self.b_next.clicked.connect(self.to_channel_test)
        self.go(PAGE_START)

    @staticmethod
    def _placeholder(title: str) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(w)
        head = QtWidgets.QLabel(title)
        head.setObjectName("status")
        msg = QtWidgets.QLabel(NOT_BUILT)
        msg.setWordWrap(True)
        lv.addWidget(head)
        lv.addWidget(msg)
        lv.addStretch(1)
        return w

    def _ask(self, title: str, text: str) -> bool:
        box = QtWidgets.QMessageBox
        return box.question(self, title, text, box.StandardButton.Yes | box.StandardButton.No,
                            box.StandardButton.No) == box.StandardButton.Yes

    def _save_checked(self, d):
        """Save the definition this wizard built, asking first if that would change a saved fixture of the
        same id: a different channel layout under an old id moves every rig that uses it. The manual
        reader's own draft, or the same channels confirmed again, change nothing and are not asked about."""
        path = userdata.paths(create=False).fixtures / f"{d.id}.json"
        if path.is_file():
            try:
                old = load_definition(path).to_dict()["modes"]
            except (DefinitionError, OSError, ValueError):
                old = None
            if old != d.to_dict()["modes"]:
                text = f"You already have a saved fixture {d.id!r} with different channels. Replace it?"
                used = rigs_using(d.id)
                if used:
                    text += "\n\nThese rigs use it and will change: " + ", ".join(used) + "."
                if not self._confirm("Replace fixture", text):
                    raise ValueError(f"you kept the saved {d.id!r}; change the model name to save this as a new one")
        return (self._save or save_user_definition)(d)

    def _confirmed(self, d) -> None:
        limit = {"moving_head": HEAD_ROLE, "laser": ""}.get(d.category, WASH_LIMIT)
        self.test_panel.status.setText(" ".join(t for t in (self.test_panel.status.text(), NEXT_STEP, limit) if t))
        self.added.emit(d)

    def go(self, page: int) -> None:
        if page == PAGE_OFL and self.ofl_page.service is None:
            self.ofl_page.load()
        if page == PAGE_MANUAL:
            self.manual_page.refresh()
        self.stack.setCurrentIndex(page)
        self.b_back.setVisible(page != PAGE_START)
        self.b_next.setVisible(page == PAGE_HAND)

    def to_channel_test(self) -> ChannelTestPanel | None:
        d = self.hand.build()
        if d is None:
            return None
        return self.open_channel_test(d)

    def open_channel_test(self, definition) -> ChannelTestPanel:
        """The last page for every route."""
        lay = self.test_holder.layout()
        if self.test_panel is not None:
            lay.removeWidget(self.test_panel)
            self.test_panel.deleteLater()
        self.test_panel = ChannelTestPanel(definition, save=self._save_checked, laser_armed=self._laser_armed)
        self.test_panel.confirmed.connect(self._confirmed)
        lay.addWidget(self.test_panel)
        self.go(PAGE_TEST)
        return self.test_panel
