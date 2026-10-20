"""Opt-in phone remote: serve on the LAN behind a random access token.

Off by default (the server binds 127.0.0.1). With --lan it binds 0.0.0.0, but
only answers private-network clients, and every non-loopback request must carry
the token (in the ?t= URL the QR code encodes, then in a cookie).
Loopback requests must name the local host, which blocks DNS-rebinding pages.
"""
from __future__ import annotations

import hmac
import ipaddress
import secrets
import socket
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

COOKIE = "l120t"


def lan_ip() -> str:
    """This Mac's address on the local network (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Remote:
    def __init__(self, enabled: bool, port: int):
        self.enabled = enabled
        self.port = port
        self.token = secrets.token_urlsafe(18) if enabled else ""
        self.ip = lan_ip() if enabled else "127.0.0.1"

    @property
    def url(self) -> str | None:
        return f"http://{self.ip}:{self.port}/?t={self.token}" if self.enabled else None

    def qr_svg(self) -> str | None:
        if not self.enabled:
            return None
        import segno
        return segno.make(self.url, error="m").svg_inline(scale=6, border=2, dark="#000", light="#fff")

    def print_banner(self):
        if not self.enabled:
            return
        import segno
        print(f"\nPhone remote (same Wi-Fi only): {self.url}\nScan this with the phone camera:\n", flush=True)
        segno.make(self.url, error="m").terminal(compact=True)

    def _local_hosts(self) -> set[str]:
        hosts = {"localhost", "127.0.0.1", "[::1]"}
        if self.enabled:
            hosts.add(self.ip)
        return {f"{h}:{self.port}" for h in hosts} | hosts

    def check(self, client_ip: str, host: str, path: str, cookie_header: str | None) -> tuple[bool, bool]:
        """(allowed, set_cookie). set_cookie: the request carried a valid ?t= token."""
        ip = ipaddress.ip_address(client_ip.split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if ip.is_loopback:
            return (host or "").lower() in self._local_hosts(), False
        if not self.enabled or not (ip.is_private or ip.is_link_local):
            return False, False
        q = parse_qs(urlparse(path).query).get("t", [""])[0]
        if q and hmac.compare_digest(q, self.token):
            return True, True
        c = SimpleCookie()
        try:
            c.load(cookie_header or "")
        except Exception:
            pass
        tok = c[COOKIE].value if COOKIE in c else ""
        return bool(tok) and hmac.compare_digest(tok, self.token), False

    def cookie(self) -> str:
        return f"{COOKIE}={self.token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"
