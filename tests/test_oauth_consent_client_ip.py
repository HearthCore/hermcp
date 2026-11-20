"""#64: the /authorize/consent limiter keys on the resolved client, never a spoofable header."""
import pytest
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

ORIGIN = "https://hermcp.test"
PROXY = "192.0.2.9"                                         # RFC 5737 documentation range


def make_app(home, monkeypatch, **env):
    from hermcp import oauth
    from hermcp.app import create_app
    from hermcp.settings import load_settings
    s = load_settings({"HERMES_HOME": str(home), "HERMCP_PUBLIC_URL": f"{ORIGIN}/mcp",
                       "HERMCP_ALLOWED_HOSTS": "hermcp.test", **env})
    oauth.init_client(s.data)
    seen = []

    async def spy(self, req, limiter, ip):
        seen.append(ip)
        return PlainTextResponse("ok")

    monkeypatch.setattr(oauth.HermcpOAuthProvider, "consent_post", spy)
    return create_app(s), seen


def post(app, peer, xff=None):
    c = TestClient(app.app, base_url=ORIGIN, client=(peer, 4321))
    headers = {"X-Forwarded-For": xff} if xff else {}
    assert c.post("/authorize/consent", data={"rid": "x", "key": "y"}, headers=headers).status_code == 200


def test_untrusted_peer_header_ignored(home, monkeypatch):
    app, seen = make_app(home, monkeypatch, HERMCP_TRUSTED_PROXIES=PROXY)
    post(app, "203.0.113.7", xff="1.2.3.4")
    assert seen == ["203.0.113.7"]


@pytest.mark.parametrize("flag", ["1", ""])
def test_legacy_trust_proxy_does_not_trust_leftmost(home, monkeypatch, flag):
    app, seen = make_app(home, monkeypatch, HERMCP_TRUST_PROXY=flag)
    post(app, "203.0.113.7", xff="9.9.9.9")                 # no trusted proxy configured
    assert seen == ["203.0.113.7"]


def test_trusted_proxy_reads_right_to_left(home, monkeypatch):
    app, seen = make_app(home, monkeypatch, HERMCP_TRUSTED_PROXIES=PROXY)
    post(app, PROXY, xff="6.6.6.6, 198.51.100.4")           # client-forged leftmost, proxy-appended right
    post(app, PROXY, xff="7.7.7.7, 8.8.8.8, 198.51.100.4")  # rotating the forged part changes nothing
    assert seen == ["198.51.100.4", "198.51.100.4"]


def test_trusted_proxy_without_header_uses_peer(home, monkeypatch):
    app, seen = make_app(home, monkeypatch, HERMCP_TRUSTED_PROXIES=PROXY)
    post(app, PROXY)
    assert seen == [PROXY]
