"""ui/health.py -- the console's plain-language fault banner (2026-11-02, 2026-10-31 show post-mortem)."""
from __future__ import annotations

import errno
from types import SimpleNamespace

from livereact.ui.health import GRACE_S, health_warnings


def _app(demo=False, open_=True, failed=None, started=0.0, pdl_last=None):
    pdl = SimpleNamespace(name="prodj-link", last_packet_time=pdl_last)
    return SimpleNamespace(config=SimpleNamespace(demo=demo), writer=SimpleNamespace(is_open=open_),
                           failed_inputs=failed or {}, started_at=started, sources=[pdl])


def _snap(pdl=True, audio=True):
    return SimpleNamespace(pdl_fresh=pdl, audio_fresh=audio)


def test_all_good_and_demo_mode_say_nothing():
    assert health_warnings(_app(), _snap(), 60.0) == []
    assert health_warnings(_app(demo=True, open_=False), _snap(False, False), 60.0) == []


def test_rekordbox_holding_the_ports_names_the_fix():
    w = health_warnings(_app(failed={"prodj-link": OSError(errno.EADDRINUSE, "Address already in use")}),
                        _snap(pdl=False), 60.0)
    assert len(w) == 1 and "DJ LINK BLOCKED" in w[0] and "rekordbox" in w[0]


def test_dead_audio_and_lost_dmx_are_both_reported_worst_first():
    w = health_warnings(_app(open_=False), _snap(audio=False), 60.0)
    assert w[0].startswith("DMX ADAPTER LOST") and w[1].startswith("NO AUDIO")


def test_signals_get_a_grace_period_after_start():
    assert health_warnings(_app(started=0.0), _snap(False, False), GRACE_S - 1) == []
    assert len(health_warnings(_app(started=0.0), _snap(False, False), GRACE_S + 1)) == 2


def test_a_paused_deck_is_not_a_dead_network():
    # beats stop when nothing plays, keepalives keep coming: no warning (2026-11-02 stress test)
    assert health_warnings(_app(pdl_last=59.0), _snap(pdl=False), 60.0) == []
    w = health_warnings(_app(pdl_last=50.0), _snap(pdl=False), 60.0)
    assert len(w) == 1 and w[0].startswith("DJ LINK SILENT")


def test_no_dj_link_input_means_no_dj_link_warning():
    app = _app()
    app.sources = []
    assert health_warnings(app, _snap(pdl=False), 60.0) == []
