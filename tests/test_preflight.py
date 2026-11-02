"""--preflight (src/livereact/preflight.py), with the hardware faked (2026-11-02, AEPI post-mortem)."""
from __future__ import annotations

import errno

from livereact import preflight as pf

ADDRS = [("en0", "192.168.1.20"), ("en8", "169.254.116.220")]


def _run(**kw):
    base = dict(running=False, bind=lambda p: None, holder=lambda p: "", addresses=ADDRS,
                audio=lambda: ("XDJ-XZ", 43, 0.05), midi=["XDJ-XZ (2)"], dmx=lambda: "/dev/cu.usbserial-EN489520")
    base.update(kw)
    return pf.run("rigs/aepi.json", "en8", **base)


def _by(checks, name):
    return next(c for c in checks if c.name == name)


def test_everything_connected_is_ready():
    checks = _run()
    assert pf.exit_code(checks) == 0
    assert "READY FOR THE SHOW" in pf.format_checks(checks)


def test_rekordbox_holding_the_dj_link_ports_fails_and_says_so():
    checks = _run(bind=lambda p: OSError(errno.EADDRINUSE, "Address already in use"), holder=lambda p: "rekordbox")
    c = _by(checks, "DJ Link ports free")
    assert c.status == pf.FAIL and "rekordbox" in c.detail and "Quit rekordbox" in c.detail
    assert pf.exit_code(checks) == 1


def test_the_named_interface_without_169_254_fails():
    c = _by(_run(addresses=[("en0", "192.168.1.20"), ("en12", "169.254.9.9")]), "DJ network (169.254)")
    assert c.status == pf.FAIL and "en12" in c.detail


def test_audio_not_flowing_fails_and_silence_only_warns():
    assert _by(_run(audio=lambda: ("XDJ-XZ", 2, 0.05)), "XDJ audio").status == pf.FAIL
    assert _by(_run(audio=lambda: ("XDJ-XZ", 43, 0.0)), "XDJ audio").status == pf.WARN


def test_missing_dmx_adapter_fails():
    def gone():
        raise RuntimeError("No Enttec DMX USB Pro found")
    assert _by(_run(dmx=gone), "DMX adapter").status == pf.FAIL


def test_a_running_app_stops_the_hardware_checks():
    checks = _run(running=True)
    assert checks[0].status == pf.FAIL and not any(c.name == "DMX adapter" for c in checks)


def test_rig_lists_every_unit_address_to_check():
    lines = pf.format_checks(_run())
    for addr in ("150", "120", "075", "001", "069"):
        assert f"display reads {addr}" in lines


def test_app_running_reads_the_process_list_and_ignores_itself():
    ps = ("  101 /usr/bin/python -m livereact --preflight\n"
          "  202 .venv313/bin/python -m livereact --hardware --visualize\n"
          "  303 /bin/zsh tools/run_show.sh\n")
    assert pf.app_running(ps, self_pid=101) is True
    assert pf.app_running(ps.replace("202", "999").replace("--hardware", "--preflight"), self_pid=101) is False
    assert pf.app_running("  202 .venv313/bin/python -m livereact --hardware\n", self_pid=202) is False
