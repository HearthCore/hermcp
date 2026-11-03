"""Client for Hermes' api_server (chat completions / runs). The API key is never returned or logged."""
from __future__ import annotations

import json
import http.client
import urllib.error
import urllib.request
from typing import Any

from ..registry import AdapterError, ToolError
from ..settings import Settings, _read_dotenv, hermes_home


def api_key(settings: Settings) -> str:
    if settings.hermes_api_key:
        return settings.hermes_api_key
    env = _read_dotenv(hermes_home() / ".env")
    return env.get("API_SERVER_KEY", "") or env.get("HERMES_API_KEY", "")


def request(settings: Settings, method: str, path: str, *, profile: str = "default",
            body: dict | None = None, timeout: float = 30.0, headers: dict[str, str] | None = None) -> Any:
    prefix = "" if profile in ("", "default") else f"/p/{profile}"
    url = f"{settings.hermes_api_url}{prefix}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for hk, hv in (headers or {}).items():
        req.add_header(hk, hv)
    key = api_key(settings)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        if exc.code in (401, 403):
            raise ToolError("api_auth", f"Hermes api_server rejected the credentials (HTTP {exc.code}); "
                                        "set HERMES_API_KEY for hermcp") from None
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
    key = api_key(settings)
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
