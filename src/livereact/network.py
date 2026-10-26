"""Network helpers for Pro DJ Link bring-up (docs/04 §7, docs/07).

Pro DJ Link is UDP on a link-local network. The reliable setup is a static
link-local IP on the host NIC (more dependable than waiting on APIPA), with the
firewall allowing the DJ-Link UDP ports. This module only *reports* — it never
changes OS network config; that's a manual step documented in HARDWARE.md.
"""

from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)

DJLINK_PORTS = (50000, 50001, 50002)
LINK_LOCAL_PREFIX = "169.254."
RECOMMENDED_HOST_IP = "169.254.50.50"   # /16


def list_ipv4_addresses() -> list[tuple[str, str]]:
    """Best-effort list of (interface-ish, ipv4) for the host.

    Uses psutil when available (richer), then fcntl (Unix), then hostname fallback.
    """
    try:
        import psutil  # optional

        out: list[tuple[str, str]] = []
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET:
                    out.append((name, a.address))
        return out
    except Exception:  # noqa: BLE001
        pass

    # fcntl-based fallback (macOS / Linux, no extra deps)
    try:
        import fcntl
        import struct

        SIOCGIFCONF = 0xC0106924 if hasattr(socket, "AF_UNIX") else 0x8912  # macOS / Linux
        buf_size = 4096
        buf = bytearray(buf_size)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            fcntl.ioctl(s.fileno(), SIOCGIFCONF, buf)  # fills `buf` in place
        finally:
            s.close()
        out: list[tuple[str, str]] = []
        # Each ifreq is 16 bytes name + 16 bytes sockaddr_in on macOS (total 32),
        # but the struct length varies; iterate until we hit nulls.
        length = struct.unpack_from("I", buf, 0)[0]  # macOS: first 4 bytes = total length
        offset = 4
        while offset < length:
            name_raw = buf[offset : offset + 16]
            name = name_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
            # sockaddr family is at offset+16, addr at offset+20
            family = buf[offset + 17]
            if family == socket.AF_INET:
                ip = socket.inet_ntoa(bytes(buf[offset + 20 : offset + 24]))
                out.append((name, ip))
            # ifreq size: name(16) + sa_len(1) + sa_family(1) + sa_data(14) = 32
            offset += 32
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass

    # Last resort: subprocess ifconfig (macOS/Linux)
    try:
        import re
        import subprocess

        result = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5)
        out = []
        current_if = "unknown"
        for line in result.stdout.splitlines():
            m_if = re.match(r"^(\S+):", line)
            if m_if:
                current_if = m_if.group(1)
            m_ip = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
            if m_ip:
                out.append((current_if, m_ip.group(1)))
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass

    try:
        return [("default", socket.gethostbyname(socket.gethostname()))]
    except OSError:
        return []


def has_link_local(addresses: list[tuple[str, str]]) -> bool:
    return any(ip.startswith(LINK_LOCAL_PREFIX) for _, ip in addresses)


def link_local_routes() -> list[str]:
    """Interfaces that own a 169.254/16 route (macOS/Linux ``netstat -rn``).

    More than one is the known cause of "No route to host" when announcing on Pro
    DJ Link: broadcasts leave via Wi-Fi (en0) instead of the XDJ's cable. Fix:
    ``sudo route delete 169.254.0.0/16 -ifscope en0``. Returns [] where netstat
    is unavailable (Windows) or unparsable.
    """
    try:
        import subprocess

        result = subprocess.run(["netstat", "-rn", "-f", "inet"], capture_output=True, text=True, timeout=5)
    except Exception:  # noqa: BLE001
        return []
    owners: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and (parts[0] == "169.254" or parts[0].startswith("169.254.0.0") or parts[0].startswith("169.254/")):
            iface = parts[3] if len(parts) >= 4 else "?"
            # macOS columns: Destination Gateway Flags Netif Expire; Linux: Destination Gateway Genmask Flags ... Iface
            if iface not in owners:
                owners.append(iface if len(parts) < 8 else parts[-1])
    return owners


def diagnostics() -> str:
    """A human-readable readiness report for the DJ-Link network."""
    addrs = list_ipv4_addresses()
    lines = ["Pro DJ Link network check:"]
    for name, ip in addrs:
        flag = "  <- link-local (good)" if ip.startswith(LINK_LOCAL_PREFIX) else ""
        lines.append(f"  {name}: {ip}{flag}")
    if not has_link_local(addrs):
        lines.append(
            f"  WARNING: No link-local address found. Set the wired NIC to a static "
            f"{RECOMMENDED_HOST_IP}/255.255.0.0 and allow UDP {DJLINK_PORTS} through "
            f"the firewall (see HARDWARE.md)."
        )
    routes = link_local_routes()
    if routes:
        lines.append(f"  169.254/16 routes on: {', '.join(routes)}")
    if len(routes) > 1:
        lines.append(
            "  WARNING: more than one interface holds a 169.254/16 route — Pro DJ Link "
            "announces may leave via the wrong NIC ('No route to host'). Fix: "
            "sudo route delete 169.254.0.0/16 -ifscope en0  (the Wi-Fi one)."
        )
    return "\n".join(lines)
