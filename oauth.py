"""OAuth 2.1 authorization server for Claude.ai custom connectors.

Built on the MCP SDK auth module shipped in the Hermes venv (``mcp.server.auth``): request
parsing/validation for /authorize and /token (PKCE S256, redirect_uri matching, code expiry) is the
SDK's; this module supplies the provider (storage, the paste-key consent page) and the metadata.

Design (docs/oauth.md):
- one static confidential client (client_id + client_secret, shown once, stored salted-hashed),
  ``client_secret_post`` only, DCR and revocation endpoints off;
- exact redirect_uri allowlist (Claude callbacks), PKCE S256 mandatory;
- /authorize shows a page where the user pastes an ``hmcp_`` key; the issued tokens are bound to
  that key and inherit its CURRENT scopes on every request (revoke/rotate/expire the key and the
  tokens die with it);
- opaque ``token_urlsafe(32)`` tokens, stored as SHA-256 hashes, access 1h / refresh 30d rotating
  / code 60s; ``resource`` is bound at issue time and checked on use.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import (AuthorizationCode, AuthorizationParams, AuthorizeError,
                                      RefreshToken, TokenError, construct_redirect_uri)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .keys import KeyStore
from .registry import Principal
from .settings import Settings

SCOPE = "hermes"
CODE_TTL = 60
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600
PENDING_TTL = 600
MAX_CONSENT_TRIES = 5
CLIENT_FILE = "oauth-client.json"
TOKEN_FILE = "oauth-tokens.json"
DEFAULT_CLIENT_ID = "hermcp-claude"


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".oauth-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# -- static client ------------------------------------------------------------------------
def init_client(data_dir: Path, client_id: str = DEFAULT_CLIENT_ID, rotate: bool = False) -> tuple[str, str] | None:
    """Create (or rotate) the static client. Returns (client_id, secret) ONCE; None if one exists."""
    path = Path(data_dir) / CLIENT_FILE
    if path.exists() and not rotate:
        return None
    secret = secrets.token_urlsafe(32)
    salt = secrets.token_hex(16)
    digest = hmac.new(bytes.fromhex(salt), secret.encode(), hashlib.sha256).hexdigest()
    _atomic_write(path, {"client_id": client_id, "salt": salt, "secret_hash": digest, "created": time.time()})
    if rotate:      # a new secret invalidates everything minted for the old one
        (Path(data_dir) / TOKEN_FILE).unlink(missing_ok=True)
    return client_id, secret


def load_client(data_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((Path(data_dir) / CLIENT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class _Authenticator(ClientAuthenticator):
    """/token client auth against the salted hash; client_secret_post only (Basic is rejected)."""

    def __init__(self, provider: "HermcpOAuthProvider"):
        super().__init__(provider)
        self.p = provider

    async def authenticate_request(self, request: Request) -> OAuthClientInformationFull:
        form = await request.form()
        cid, secret = form.get("client_id"), form.get("client_secret")
        rec = self.p.client_rec()
        ok = bool(rec) and isinstance(cid, str) and isinstance(secret, str) and secret
        if ok:
            digest = hmac.new(bytes.fromhex(rec["salt"]), secret.encode(), hashlib.sha256).hexdigest()
            ok = hmac.compare_digest(digest, rec["secret_hash"]) and hmac.compare_digest(cid, rec["client_id"])
        if not ok:
            raise AuthenticationError("invalid client credentials")
        return self.p.client_info(rec)


# -- provider -----------------------------------------------------------------------------
class _Code(AuthorizationCode):
    key_id: str
    key_fp: str


class _Refresh(RefreshToken):
    key_id: str
    key_fp: str
    resource: str | None = None


class HermcpOAuthProvider:
    def __init__(self, settings: Settings, keystore: KeyStore):
        self.s, self.keys = settings, keystore
        self.dir = settings.data
        self._lock = threading.RLock()
        self._codes: dict[str, _Code] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self.resource = settings.public_url or f"{settings.public_origin}/mcp"

    # -- client -------------------------------------------------------------------------
    def client_rec(self) -> dict[str, Any] | None:
        return load_client(self.dir)

    def client_info(self, rec: dict[str, Any]) -> OAuthClientInformationFull:
        return OAuthClientInformationFull(
            client_id=rec["client_id"], client_secret=None,
            redirect_uris=[AnyUrl(u) for u in self.s.oauth_redirect_uris],
            token_endpoint_auth_method="client_secret_post",
            grant_types=["authorization_code", "refresh_token"], response_types=["code"],
            scope=SCOPE, client_name="hermcp connector")

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        rec = self.client_rec()
        if not rec or not hmac.compare_digest(client_id, rec["client_id"]):
            return None
        return self.client_info(rec)

    async def register_client(self, client_info) -> None:   # DCR is off
        raise NotImplementedError("dynamic client registration is disabled")

    # -- token store (hashed at rest) -----------------------------------------------------
    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads((self.dir / TOKEN_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        now = time.time()
        return {kind: {h: r for h, r in raw.get(kind, {}).items() if r["expires_at"] > now}
                for kind in ("access", "refresh")}

    def _save(self, db: dict[str, dict[str, Any]]) -> None:
        _atomic_write(self.dir / TOKEN_FILE, db)

    def _mint(self, db: dict, key_id: str, key_fp: str, resource: str | None) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = time.time()
        base = {"client_id": self.client_rec()["client_id"], "key_id": key_id, "key_fp": key_fp,
                "resource": resource or self.resource, "scope": SCOPE}
        db["access"][_h(access)] = {**base, "expires_at": now + ACCESS_TTL, "refresh": _h(refresh)}
        db["refresh"][_h(refresh)] = {**base, "expires_at": now + REFRESH_TTL}
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL,
                          refresh_token=refresh, scope=SCOPE)

    def _key_ok(self, key_id: str, key_fp: str):
        """The bound key must still exist, be enabled, unexpired and unrotated."""
        try:
            rec = self.keys.get(key_id)
        except Exception:  # noqa: BLE001 - KeyError_ or unreadable store: fail closed
            return None
        if rec.id != key_id or rec.disabled or rec.expired() or not hmac.compare_digest(rec.hash[:16], key_fp):
            return None
        return rec

    # -- SDK provider protocol -------------------------------------------------------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        res = (params.resource or "").rstrip("/")
        if res and res != self.resource.rstrip("/"):
            raise AuthorizeError("invalid_target", "unknown resource")
        rid = secrets.token_urlsafe(24)
        with self._lock:
            now = time.time()
            self._pending = {k: v for k, v in self._pending.items() if v["exp"] > now}
            self._pending[rid] = {"params": params, "client_id": client.client_id, "exp": now + PENDING_TTL,
                                  "tries": 0}
        return f"{self.s.public_origin}/authorize/consent?rid={rid}"

    async def load_authorization_code(self, client, authorization_code: str) -> _Code | None:
        with self._lock:
            return self._codes.get(authorization_code)

    async def exchange_authorization_code(self, client, authorization_code: _Code) -> OAuthToken:
        with self._lock:
            if self._codes.pop(authorization_code.code, None) is None:   # single use
                raise TokenError("invalid_grant", "authorization code already used")
            if self._key_ok(authorization_code.key_id, authorization_code.key_fp) is None:
                raise TokenError("invalid_grant", "the key behind this authorization is no longer valid")
            db = self._load()
            tok = self._mint(db, authorization_code.key_id, authorization_code.key_fp, authorization_code.resource)
            self._save(db)
            return tok

    async def load_refresh_token(self, client, refresh_token: str) -> _Refresh | None:
        with self._lock:
            r = self._load()["refresh"].get(_h(refresh_token))
        if r is None:
            return None
        return _Refresh(token=refresh_token, client_id=r["client_id"], scopes=[SCOPE],
                        expires_at=int(r["expires_at"]), key_id=r["key_id"], key_fp=r["key_fp"],
                        resource=r.get("resource"))

    async def exchange_refresh_token(self, client, refresh_token: _Refresh, scopes: list[str]) -> OAuthToken:
        with self._lock:
            db = self._load()
            old = db["refresh"].pop(_h(refresh_token.token), None)   # rotation: one use only
            if old is None:
                raise TokenError("invalid_grant", "refresh token already used or expired")
            for h in [h for h, a in db["access"].items() if a.get("refresh") == _h(refresh_token.token)]:
                db["access"].pop(h, None)
            if self._key_ok(old["key_id"], old["key_fp"]) is None:
                self._save(db)
                raise TokenError("invalid_grant", "the key behind this authorization is no longer valid")
            tok = self._mint(db, old["key_id"], old["key_fp"], old.get("resource"))
            self._save(db)
            return tok

    async def load_access_token(self, token: str):   # not used: /mcp auth goes through verify()
        return None

    async def revoke_token(self, token) -> None:    # /revoke is not mounted
        return None

    # -- /mcp bearer check (sync, called from HermcpApp.authenticate) -----------------------
    def verify(self, token: str) -> Principal | None:
        with self._lock:
            a = self._load()["access"].get(_h(token))
        if a is None or a.get("resource", "").rstrip("/") != self.resource.rstrip("/"):
            return None
        rec = self._key_ok(a["key_id"], a["key_fp"])
        if rec is None:
            return None
        return Principal(rec.id, f"{rec.name} (oauth)", rec.scopes)

    def revoke_all(self) -> int:
        with self._lock:
            db = self._load()
            n = len(db["access"]) + len(db["refresh"])
            self._save({"access": {}, "refresh": {}})
            return n

    def stats(self) -> dict[str, int]:
        with self._lock:
            db = self._load()
            return {"access": len(db["access"]), "refresh": len(db["refresh"])}

    # -- consent page -------------------------------------------------------------------------
    def _page(self, rid: str, error: str = "", status: int = 200) -> HTMLResponse:
        err = f'<p class="err">{html.escape(error)}</p>' if error else ""
        body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Authorize hermcp</title>
<style>body{{font:16px system-ui;max-width:30rem;margin:12vh auto;padding:0 1rem}}
input{{width:100%;padding:.6rem;margin:.5rem 0;box-sizing:border-box;font:14px monospace}}
button{{padding:.6rem 1.2rem}}.err{{color:#b00020}}</style></head><body>
<h1>Authorize connector</h1>
<p>An MCP client (Claude) asks for access to your Hermes control surface. Paste an
<code>hmcp_…</code> key; the connector gets exactly that key's permissions.</p>{err}
<form method="post" action="/authorize/consent" autocomplete="off">
<input type="hidden" name="rid" value="{html.escape(rid)}">
<input type="password" name="key" placeholder="hmcp_…" required autofocus>
<button type="submit">Authorize</button></form></body></html>"""
        return HTMLResponse(body, status_code=status, headers={
            "Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"})

    def _peek(self, rid: str) -> dict[str, Any] | None:
        with self._lock:
            p = self._pending.get(rid)
            if p and p["exp"] <= time.time():
                self._pending.pop(rid, None)
                return None
            return p

    async def consent_get(self, req: Request) -> Response:
        rid = req.query_params.get("rid", "")
        if not self._peek(rid):
            return HTMLResponse("This authorization request expired. Start again from the client.",
                                status_code=400, headers={"Cache-Control": "no-store"})
        return self._page(rid)

    async def consent_post(self, req: Request, limiter, ip: str) -> Response:
        form = await req.form()
        rid, token = str(form.get("rid", "")), str(form.get("key", "")).strip()
        pend = self._peek(rid)
        if not pend:
            return HTMLResponse("This authorization request expired. Start again from the client.",
                                status_code=400, headers={"Cache-Control": "no-store"})
        if limiter.blocked(ip):
            return self._page(rid, "Too many failed attempts. Wait a minute.", 429)
        rec = self.keys.authenticate(token)
        if rec is None:
            limiter.fail(ip)
            with self._lock:
                pend["tries"] += 1
                if pend["tries"] >= MAX_CONSENT_TRIES:
                    self._pending.pop(rid, None)
            return self._page(rid, "That key is not valid.", 403)
        params: AuthorizationParams = pend["params"]
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._pending.pop(rid, None)
            now = time.time()
            self._codes = {c: v for c, v in self._codes.items() if v.expires_at > now}
            self._codes[code] = _Code(
                code=code, scopes=[SCOPE], expires_at=now + CODE_TTL, client_id=pend["client_id"],
                code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                resource=params.resource or self.resource, key_id=rec.id, key_fp=rec.hash[:16])
        url = construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state,
                                     iss=self.s.public_origin)
        return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})


# -- metadata + wiring -----------------------------------------------------------------------
def authorization_server_metadata(s: Settings) -> dict[str, Any]:
    o = s.public_origin
    return {
        "issuer": o, "authorization_endpoint": f"{o}/authorize", "token_endpoint": f"{o}/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],   # NOT client_secret_basic
        "scopes_supported": [SCOPE],
        "authorization_response_iss_parameter_supported": True,
    }


def protected_resource_metadata(s: Settings) -> dict[str, Any]:
    o = s.public_origin
    return {"resource": s.public_url or f"{o}/mcp", "authorization_servers": [o],
            "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"]}


def _json(doc: dict[str, Any]) -> Response:
    return JSONResponse(doc, headers={"Cache-Control": "public, max-age=3600",
                                      "Access-Control-Allow-Origin": "*"})


def build_oauth(s: Settings, keystore: KeyStore, limiter=None):
    """-> (extra_auth, extra_routes), or (None, None) when OAuth cannot be served (no public URL
    or no client created yet: run `hermes hermcp oauth init`)."""
    if not s.public_origin or load_client(s.data) is None:
        return None, None
    if limiter is None:
        from .server import FailLimiter
        limiter = FailLimiter(s.auth_fail_limit, s.auth_fail_window)
    p = HermcpOAuthProvider(s, keystore)
    as_doc, pr_doc = authorization_server_metadata(s), protected_resource_metadata(s)

    async def as_meta(_req: Request) -> Response:
        return _json(as_doc)

    async def pr_meta(_req: Request) -> Response:
        return _json(pr_doc)

    async def consent_post(req: Request) -> Response:
        ip = req.client.host if req.client else "?"
        if s.trust_proxy and req.headers.get("x-forwarded-for"):
            ip = req.headers["x-forwarded-for"].split(",")[0].strip()
        return await p.consent_post(req, limiter, ip)

    async def register(_req: Request) -> Response:
        return JSONResponse({"error": "unsupported", "error_description": "dynamic client registration is "
                             "disabled; use the pre-shared client_id/client_secret"}, status_code=400)

    routes = [
        Route("/.well-known/oauth-authorization-server", as_meta, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", pr_meta, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource/mcp", pr_meta, methods=["GET"]),
        Route("/authorize", AuthorizationHandler(p).handle, methods=["GET"]),
        Route("/authorize/consent", p.consent_get, methods=["GET"]),
        Route("/authorize/consent", consent_post, methods=["POST"]),
        Route("/token", TokenHandler(p, _Authenticator(p)).handle, methods=["POST"]),
        Route("/register", register, methods=["POST"]),
    ]
    p.routes = routes   # type: ignore[attr-defined]
    return p.verify, routes
