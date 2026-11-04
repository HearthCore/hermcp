"""Client for Hermes' api_server (chat completions / runs).

The api_server authorizes `/p/<profile>/...` against that profile's own `API_SERVER_KEY`, so the
key is resolved per profile (and re-read at call time; see `profile_keys`). Keys are never returned
to callers or logged, and a 401 names the profile plus the sources that were tried — never a value.
"""
from __future__ import annotations

import json
import http.client
import urllib.error
import urllib.request
from typing import Any

from ..profile_keys import resolve
from ..registry import AdapterError, ToolError
from ..settings import Settings


def api_key(settings: Settings, profile: str = "default") -> str:
    """The api_server key for *profile* (override > profile .env > global). Never logged."""
    return resolve(settings, profile).key


def auth_error(settings: Settings, profile: str, code: int) -> ToolError:
    """401/403 for *profile*: names the profile and the sources tried, without any key value."""
    res = resolve(settings, profile)
    found = (f"resolved from {res.source} (fingerprint {res.fingerprint})" if res.present
             else "no key found for this profile")
    return ToolError("api_auth",
                     f"Hermes api_server rejected the credentials for profile {res.profile!r} "
                     f"(HTTP {code}); {found}. Sources tried in order: {'; '.join(res.tried)}. "
                     "Fix it with `python -m hermcp profile-keys status` and "
                     "`python -m hermcp profile-keys setup`. No key values are logged.")


def request(settings: Settings, method: str, path: str, *, profile: str = "default",
            body: dict | None = None, timeout: float = 30.0, headers: dict[str, str] | None = None) -> Any:
    prefix = "" if profile in ("", "default") else f"/p/{profile}"
    url = f"{settings.hermes_api_url}{prefix}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for hk, hv in (headers or {}).items():
        req.add_header(hk, hv)
    key = api_key(settings, profile)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        if exc.code in (401, 403):
            raise auth_error(settings, profile, exc.code) from None
        raise ToolError("api_error", f"Hermes api_server HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AdapterError(f"Hermes api_server unreachable at {settings.hermes_api_url}: {exc}") from exc
    except ValueError as exc:
        raise AdapterError(f"Hermes api_server returned invalid JSON: {exc}") from exc


def request_status(settings: Settings, method: str, path: str, *, profile: str = "default",
                   body: dict | None = None, timeout: float = 10.0,
                   headers: dict[str, str] | None = None) -> tuple[int | None, Any]:
    """Like `request`, but never raises: returns (http_status, parsed_json_or_None).
    http_status is None when the api_server could not be reached at all (network error/timeout)."""
    prefix = "" if profile in ("", "default") else f"/p/{profile}"
    req = urllib.request.Request(f"{settings.hermes_api_url}{prefix}{path}",
                                 data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Content-Type", "application/json")
    for hk, hv in (headers or {}).items():
        req.add_header(hk, hv)
    key = api_key(settings, profile)
    if key:
        req.add_header("Authorization", f"Bearer {key}")

    def parse(raw: bytes):
        try:
            return json.loads(raw.decode("utf-8", "replace")) if raw.strip() else {}
        except ValueError:
            return None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, parse(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, parse(exc.read()[:20000])
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        return None, None
