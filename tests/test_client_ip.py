"""Trusted proxies: who the client really is.

The rule under test is the whole point of the setting: without a trusted peer the
forwarding headers are a caller's opinion about itself and must change nothing;
with one, the chain is read right to left so a client cannot prepend whatever it
likes.
"""
from __future__ import annotations

from starlette.requests import Request

from hermcp import client_ip as CI
from hermcp.app import create_app
from hermcp.settings import ConfigError, load_settings


def nets(values):
    return CI.parse_networks(values)


PROXY = nets(["10.1.0.9"])


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def test_parse_ip_accepts_what_proxies_send():
    assert str(CI.parse_ip("10.0.0.5")) == "10.0.0.5"
    assert str(CI.parse_ip("2001:db8::1")) == "2001:db8::1"
    assert str(CI.parse_ip("1.2.3.4:5678")) == "1.2.3.4"
    assert str(CI.parse_ip("[2001:db8::1]:443")) == "2001:db8::1"
    assert str(CI.parse_ip('"10.0.0.5"')) == "10.0.0.5"
    assert str(CI.parse_ip("::ffff:192.168.1.9")) == "192.168.1.9"
    for bad in ("unknown", "_hidden", "", None, "1.2.3", "evil.example"):
        assert CI.parse_ip(bad) is None, bad


def test_networks_accept_single_addresses_and_cidrs():
    assert len(CI.parse_networks(["10.1.0.9"])) == 1
    assert CI.in_networks(CI.parse_ip("10.1.0.9"), nets(["10.1.0.9"]))
    assert not CI.in_networks(CI.parse_ip("10.1.0.10"), nets(["10.1.0.9"]))
    assert CI.in_networks(CI.parse_ip("10.1.0.10"), nets(["10.1.0.0/24"]))
    assert CI.in_networks(CI.parse_ip("2a00::5"), nets(["2a00::/32"]))
    assert not CI.in_networks(CI.parse_ip("10.1.0.10"), nets(["2a00::/32"]))


def test_invalid_entries_are_reported_and_skipped():
    assert CI.invalid_networks(["10.0.0.1", "nope", "10.0.0.0/33"]) == ["nope", "10.0.0.0/33"]
    assert len(CI.parse_networks(["10.0.0.1", "nope"])) == 1
    try:
        load_settings({"HERMES_HOME": "/tmp/x", "HERMCP_TRUSTED_PROXIES": "10.0.0.1, nope"})
    except ConfigError as exc:
        assert "HERMCP_TRUSTED_PROXIES" in str(exc)
    else:                                                     # a local .env may soften strictness
        s = load_settings({"HERMES_HOME": "/tmp/x", "HERMCP_TRUSTED_PROXIES": "nope"}, strict=False)
        assert s.trusted_proxies == []


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------

def test_untrusted_peer_headers_change_nothing():
    info = CI.resolve("203.0.113.9", {"x-forwarded-for": "127.0.0.1",
                                      "x-forwarded-proto": "https",
                                      "x-forwarded-host": "evil.example",
                                      "x-real-ip": "10.0.0.5"})
    assert info.ip == "203.0.113.9"
    assert info.trusted is False and info.via_proxy is False
    assert info.proto == "http"
    assert info.host == ""


def test_untrusted_private_peer_keeps_its_own_address():
    info = CI.resolve("10.1.0.9", {"x-forwarded-for": "8.8.8.8"})   # a private peer, but not our proxy
    assert info.ip == "10.1.0.9" and info.trusted is False


def test_trusted_peer_multi_hop_chain_takes_the_first_untrusted_from_the_right():
    # client pretended 1.2.3.4; the proxy appended the real address
    info = CI.resolve("10.1.0.9", {"x-forwarded-for": "1.2.3.4, 198.51.100.7, 10.1.0.9"}, PROXY)
    assert info.ip == "198.51.100.7"
    assert info.trusted is True and info.via_proxy is True

    # two of our proxies in the chain
    info = CI.resolve("10.1.0.9", {"x-forwarded-for": "203.0.113.9, 10.1.0.9, 10.1.0.9"},
                      nets(["10.1.0.0/24"]))
    assert info.ip == "203.0.113.9"

    # every entry is one of ours: the leftmost is the client
    info = CI.resolve("10.1.0.9", {"x-forwarded-for": "10.1.0.20, 10.1.0.9"}, nets(["10.1.0.0/24"]))
    assert info.ip == "10.1.0.20"


def test_trusted_peer_ipv6_chain():
    trusted = nets(["fd00::9"])
    info = CI.resolve("fd00::9", {"x-forwarded-for": "2001:db8::1, fd00::9"}, trusted)
    assert info.ip == "2001:db8::1"
    info = CI.resolve("fd00::9", {"forwarded": "for=\"[2001:db8::2]:4711\""}, trusted)
    assert info.ip == "2001:db8::2"


def test_invalid_entries_are_skipped_not_believed():
    info = CI.resolve("10.1.0.9", {"x-forwarded-for": "garbage, unknown, 203.0.113.4"}, PROXY)
    assert info.ip == "203.0.113.4"
    info = CI.resolve("10.1.0.9", {"x-forwarded-for": "garbage, unknown"}, PROXY)
    assert info.ip == "10.1.0.9"          # nothing usable: stay with the peer


def test_forwarded_header_is_used_when_there_is_no_xff():
    info = CI.resolve("10.1.0.9",
                      {"forwarded": 'for=203.0.113.9;proto=https;host=ledger.example.dev, for=10.1.0.9'},
                      PROXY)
    assert info.ip == "203.0.113.9"
    assert info.proto == "https"
    assert info.host == "ledger.example.dev"
    assert CI.request_base(info) == "https://ledger.example.dev"


def test_proto_and_host_only_from_a_trusted_peer():
    info = CI.resolve("10.1.0.9", {"x-forwarded-proto": "https", "x-forwarded-host": "ledger.example.dev"}, PROXY)
    assert (info.proto, info.host) == ("https", "ledger.example.dev")
    assert CI.request_base(info) == "https://ledger.example.dev"

    bad = CI.resolve("10.1.0.9", {"x-forwarded-proto": "javascript", "x-forwarded-host": "a/b"}, PROXY)
    assert bad.proto == "http" and bad.host == ""

    untrusted = CI.resolve("203.0.113.9", {"x-forwarded-proto": "https", "x-forwarded-host": "ledger.example.dev"})
    assert untrusted.proto == "http" and untrusted.host == ""


def test_default_is_no_trusted_proxy():
    s = load_settings({"HERMES_HOME": "/tmp/x"})
    assert s.trusted_proxies == []
    assert CI.resolve("10.1.0.9", {"x-forwarded-for": "8.8.8.8"}, CI.parse_networks(s.trusted_proxies)).ip == "10.1.0.9"


# ---------------------------------------------------------------------------
# wiring inside the app (rate limits, ledger)
# ---------------------------------------------------------------------------

def _request(peer, headers):
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": "/ledger", "raw_path": b"/ledger", "root_path": "",
        "query_string": b"", "server": ("127.0.0.1", 9230), "client": (peer, 50000),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def test_app_client_ip_uses_the_central_resolution(home):
    app = create_app(load_settings({"HERMES_HOME": str(home), "HERMCP_TRUSTED_PROXIES": "10.1.0.9"}))
    assert app.client_ip(_request("10.1.0.9", {"x-forwarded-for": "203.0.113.9"})) == "203.0.113.9"
    assert app.client_ip(_request("10.1.0.9", {"x-forwarded-for": "garbage"})) == "10.1.0.9"
    assert app.client_ip(_request("203.0.113.9", {"x-forwarded-for": "10.0.0.5"})) == "203.0.113.9"


def test_rate_limit_counts_the_effective_client_not_the_proxy(home):
    app = create_app(load_settings({"HERMES_HOME": str(home), "HERMCP_TRUSTED_PROXIES": "10.1.0.9"}))
    limiter = app.limiter
    ip_a = app.client_ip(_request("10.1.0.9", {"x-forwarded-for": "203.0.113.1"}))
    ip_b = app.client_ip(_request("10.1.0.9", {"x-forwarded-for": "203.0.113.2"}))
    assert ip_a != ip_b
    for _ in range(limiter.limit):
        limiter.fail(ip_a)
    assert limiter.blocked(ip_a) and not limiter.blocked(ip_b)
