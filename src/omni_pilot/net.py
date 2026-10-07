"""Connect over IPv4 before IPv6.

Some networks hand out IPv6 addresses but drop IPv6 traffic. iOS lists a
host's IPv6 addresses first, so every connection waited out the connect
timeout on each of them before trying IPv4 (BAR-78) — Gemini's host alone has
eight. requests has no setting for address order, so urllib3's connection
function is replaced with one that tries IPv4 first and falls back to IPv6,
which keeps IPv6-only networks working.
"""
from __future__ import annotations

import socket

import urllib3.util.connection as urllib3_connection

_urllib3_create_connection = urllib3_connection.create_connection


def prefer_ipv4(addrinfos: list[tuple]) -> list[tuple]:
    """getaddrinfo results with IPv4 first, each family keeping its order."""
    return sorted(addrinfos, key=lambda info: info[0] != socket.AF_INET)


def create_connection_ipv4_first(address: tuple[str, int], *args, **kwargs) -> socket.socket:
    """urllib3's create_connection, trying the host's addresses IPv4 first.

    Each address is handed to urllib3 as a literal, so its timeout and socket
    options apply unchanged; TLS still verifies against the hostname.
    """
    host, port = address
    try:
        addrinfos = socket.getaddrinfo(
            host.strip("[]"), port, urllib3_connection.allowed_gai_family(), socket.SOCK_STREAM
        )
    except OSError:
        # Let urllib3 resolve again and raise its usual error.
        return _urllib3_create_connection(address, *args, **kwargs)
    if not addrinfos:
        return _urllib3_create_connection(address, *args, **kwargs)

    error: OSError | None = None
    for *_, sockaddr in prefer_ipv4(addrinfos):
        try:
            return _urllib3_create_connection((sockaddr[0], port), *args, **kwargs)
        except OSError as e:
            error = e
    raise error


def prefer_ipv4_connections() -> None:
    """Make every requests/urllib3 connection in this process try IPv4 first."""
    urllib3_connection.create_connection = create_connection_ipv4_first
