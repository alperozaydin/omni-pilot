"""tests/test_net.py"""
from __future__ import annotations

import socket

import pytest
import urllib3.util.connection as urllib3_connection

from omni_pilot import net

V6_A = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0))
V6_B = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::2", 443, 0, 0))
V4_A = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))
V4_B = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.2", 443))


def test_prefer_ipv4_moves_ipv4_ahead_keeping_each_familys_order():
    assert net.prefer_ipv4([V6_A, V4_A, V6_B, V4_B]) == [V4_A, V4_B, V6_A, V6_B]


@pytest.fixture
def resolves_to(mocker):
    """Make the host resolve to the given addresses; return the mock standing in for urllib3's connect."""
    def setup(*addrinfos):
        mocker.patch("omni_pilot.net.socket.getaddrinfo", return_value=list(addrinfos))
        return mocker.patch("omni_pilot.net._urllib3_create_connection")
    return setup


def test_connects_over_ipv4_before_trying_ipv6(resolves_to):
    # iOS lists IPv6 first; on a network that drops IPv6 every connection
    # waited out the connect timeout on each IPv6 address (BAR-78).
    connect = resolves_to(V6_A, V6_B, V4_A)

    sock = net.create_connection_ipv4_first(("example.com", 443), 5)

    assert sock is connect.return_value
    connect.assert_called_once_with(("192.0.2.1", 443), 5)


def test_falls_back_to_ipv6_when_no_ipv4_address_connects(resolves_to):
    # An IPv6-only network must keep working.
    connect = resolves_to(V6_A, V4_A)
    connect.side_effect = [OSError("unreachable"), "ipv6-socket"]

    assert net.create_connection_ipv4_first(("example.com", 443), 5) == "ipv6-socket"
    assert [c.args[0] for c in connect.call_args_list] == [("192.0.2.1", 443), ("2001:db8::1", 443)]


def test_raises_the_last_error_when_no_address_connects(resolves_to):
    connect = resolves_to(V4_A, V6_A)
    connect.side_effect = [OSError("first"), TimeoutError("last")]

    with pytest.raises(TimeoutError, match="last"):
        net.create_connection_ipv4_first(("example.com", 443), 5)


def test_leaves_resolution_failures_to_urllib3(mocker):
    # urllib3 turns a failed lookup into its own NameResolutionError; keep that.
    mocker.patch("omni_pilot.net.socket.getaddrinfo", side_effect=socket.gaierror("no such host"))
    connect = mocker.patch("omni_pilot.net._urllib3_create_connection")

    net.create_connection_ipv4_first(("nowhere.invalid", 443), 5, source_address=None)

    connect.assert_called_once_with(("nowhere.invalid", 443), 5, source_address=None)


def test_install_makes_urllib3_connect_ipv4_first(monkeypatch):
    monkeypatch.setattr(urllib3_connection, "create_connection", urllib3_connection.create_connection)

    net.prefer_ipv4_connections()

    assert urllib3_connection.create_connection is net.create_connection_ipv4_first
