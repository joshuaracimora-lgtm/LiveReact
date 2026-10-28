"""Roadmap item 28: manual reader worker (cancellable, cached by PDF sha256), review (an unconfirmed
manual_ai draft + what to double-check), and the wizard's upload page. A fake reader stands in
for Claude; nothing is sent anywhere."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from livereact.fixtures.manual_reader import client as mc  # noqa: E402
from livereact.fixtures.manual_reader import review, worker  # noqa: E402

DATA = Path(__file__).resolve().parent / "data" / "manual_reader"
COVER = "Thank you for purchasing this product.\nPlease read this manual carefully before use."
SAFETY = "Safety instructions\nKeep away from water. Do not look into the beam.\nDisconnect power before cleaning."
CHART = ("DMX channel chart (7CH mode)\nCH1 Dimmer 000-255\nCH2 Red 000-255\nCH3 Green 000-255\n"
         "CH4 Blue 000-255\nCH5 Strobe 000-009 off, 010-255 slow to fast\nCH6 Macro 000-255\nCH7 Speed 000-255")


def make_pdf(path, pages):
    from test_app_manual_extract import make_pdf as mk
    mk(path, pages)


class FakeReader:
    def __init__(self):
        raw = json.loads((DATA / "response_par.json").read_text(encoding="utf-8"))
        self.answer = json.loads(raw["content"][0]["text"])
        self.reads, self.estimates = [], []

    def read(self, pdf_bytes):
        self.reads.append(pdf_bytes)
        return mc.ReaderResult(self.answer, 2310, 812, mc.MODEL)

    def estimate(self, pdf_bytes):
        self.estimates.append(pdf_bytes)
        return mc.CostEstimate(2310, 0.09, 0.33)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEREACT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def manual(tmp_path):
    p = tmp_path / "manual.pdf"
    make_pdf(p, [COVER, SAFETY, CHART])
    return p


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# --- pipeline / cache / cancel ---

def test_reads_only_the_trimmed_pages_and_caches_by_sha(manual, tmp_path):
    from pypdf import PdfReader
    import io
    r = FakeReader()
    res = worker.read_manual(manual, r, tmp_path / "m")
    assert not res.from_cache and res.pages.best == [3]
    assert len(PdfReader(io.BytesIO(r.reads[0])).pages) == 1          # only the chart page was sent
    assert (tmp_path / "m" / res.pages.sha256 / worker.ANSWER_FILE).is_file()
    again = worker.read_manual(manual, r, tmp_path / "m")
    assert again.from_cache and len(r.reads) == 1                       # never paid for twice
    assert again.data == res.data


def test_cancel_before_the_paid_call(manual, tmp_path):
    import threading
    ev = threading.Event()
    ev.set()
    r = FakeReader()
    with pytest.raises(worker.Cancelled):
        worker.read_manual(manual, r, tmp_path / "m", cancel=ev)
    assert r.reads == []


def test_a_pdf_with_no_chart_is_not_sent(tmp_path):
    p = tmp_path / "plain.pdf"
    make_pdf(p, [COVER, SAFETY])
    r = FakeReader()
    with pytest.raises(ValueError, match="by hand"):
        worker.read_manual(p, r, tmp_path / "m")
    assert r.reads == []


def test_worker_thread_emits_done(qapp, manual, tmp_path):
    from PySide6 import QtCore
    got, failed = [], []
    w = worker.ManualReaderWorker(manual, FakeReader(), tmp_path / "m")
    w.done.connect(got.append)
    w.failed.connect(failed.append)
    w.start()
    assert w.wait(10000)
    for _ in range(50):
        QtCore.QCoreApplication.processEvents()
        if got or failed:
            break
    assert got and not failed and got[0].data["model"] == "Par 7"


def test_worker_reports_reader_errors(qapp, manual, tmp_path):
    class Broken(FakeReader):
        def read(self, pdf_bytes):
            raise mc.ReaderOffline("offline")
    w = worker.ManualReaderWorker(manual, Broken(), tmp_path / "m")
    failed = []
    w.failed.connect(failed.append)
    w.run()                                    # inline: same code path, no thread
    assert failed == ["offline"]


# --- review ---

def test_draft_is_unconfirmed_manual_ai_with_doubts_on_original_pages(manual, tmp_path):
    res = worker.read_manual(manual, FakeReader(), tmp_path / "m")
    d = review.draft(res)
    assert d.source.kind == "manual_ai" and not d.source.confirmed and d.source.pdf_sha256 == res.pages.sha256
    doubts = review.doubts(d)
    assert {x.offset for x in doubts} == {5, 6}                         # confidence 0.4 and 0.3
    assert all(x.source_page == 3 for x in doubts)                     # trimmed page 1/2 -> manual page 3
    from livereact.fixtures import library
    review.save_draft(d)
    assert library.resolve(d.id)[0].source.confirmed is False


def test_save_draft_refuses_a_confirmed_definition(manual, tmp_path):
    import dataclasses
    d = review.draft(worker.read_manual(manual, FakeReader(), tmp_path / "m"))
    with pytest.raises(ValueError):
        review.save_draft(dataclasses.replace(d, source=dataclasses.replace(d.source, confirmed=True)))


# --- upload page ---

def _page(manual=None, key="sk-test", online=True, reader=None, **kw):
    from livereact.ui.manual_upload import UploadManualPage
    from livereact.settings import Settings
    r = reader or FakeReader()
    p = UploadManualPage(reader_factory=lambda: r, key_provider=lambda: key, online=lambda: online,
                         settings=Settings.load(), pick_pdf=lambda: str(manual) if manual else "",
                         threaded=False, **kw)
    p.refresh()
    return p, r


def test_greyed_out_without_a_key_or_offline(qapp):
    p, _ = _page(key=None)
    assert "API key" in p.l_avail.text() and not p.b_pick.isEnabled() and not p.cb_consent.isEnabled()
    p, _ = _page(online=False)
    assert "Offline" in p.l_avail.text() and not p.b_pick.isEnabled()


def test_never_checks_the_key_until_shown(qapp):
    from livereact.ui.manual_upload import UploadManualPage
    asked = []
    UploadManualPage(key_provider=lambda: asked.append(1) or None, threaded=False)
    assert asked == []


def test_consent_is_needed_once_and_remembered(qapp, manual):
    from livereact.settings import Settings
    from livereact.ui.manual_upload import CONSENT_KEY
    p, r = _page(manual)
    p.b_pick.click()
    assert not p.b_read.isEnabled()
    p.read()
    assert r.reads == []                                              # no consent, nothing sent
    p.cb_consent.setChecked(True)
    assert Settings.load().extra[CONSENT_KEY] is True
    p2, _ = _page(manual)
    assert p2.cb_consent.isChecked()


def test_pick_shows_pages_and_a_cost_before_sending(qapp, manual):
    p, r = _page(manual)
    p.b_pick.click()
    assert "[3]" in p.l_cost.text() and "$" in p.l_cost.text()
    assert len(r.estimates) == 1 and r.reads == []


def test_read_saves_a_draft_and_emits_it(qapp, manual):
    from livereact.fixtures import library
    p, r = _page(manual)
    got = []
    p.drafted.connect(got.append)
    p.cb_consent.setChecked(True)
    p.b_pick.click()
    p.b_read.click()
    assert got and got[0].source.kind == "manual_ai" and not got[0].source.confirmed
    assert library.resolve(got[0].id)[0].model == "Par 7"
    assert "2 value(s) to double-check" in p.l_status.text()
    p.b_pick.click()
    assert "no charge" in p.l_cost.text()


def test_reader_failure_is_shown(qapp, manual):
    class Broken(FakeReader):
        def read(self, pdf_bytes):
            raise mc.ReaderRateLimited("Anthropic is rate-limiting this key. Try again in a minute.")
    p, _ = _page(manual, reader=Broken())
    p.cb_consent.setChecked(True)
    p.b_pick.click()
    p.b_read.click()
    assert "rate-limiting" in p.l_status.text()


# --- the wizard route ---

def test_wizard_manual_route_ends_on_the_channel_test_with_flags(qapp, manual):
    from livereact.settings import Settings
    from livereact.ui import fixture_wizard as fw
    r = FakeReader()
    w = fw.FixtureWizard(threaded=False, manual_page_kwargs={
        "reader_factory": lambda: r, "key_provider": lambda: "sk-test", "online": lambda: True,
        "settings": Settings.load(), "pick_pdf": lambda: str(manual)})
    w.b_manual.click()
    page = w.manual_page
    assert page.b_pick.isEnabled()
    page.cb_consent.setChecked(True)
    page.b_pick.click()
    page.b_read.click()
    assert w.stack.currentIndex() == fw.PAGE_TEST
    panel = w.test_panel
    assert panel.definition.source.kind == "manual_ai"
    assert panel.flagged == [5, 6]
