"""Output redaction for tool results. Pure functions: they return copies and never write anything.

Why a second layer: Hermes' own config masker (hermes_cli.config.redact_config_value) only looks at the last
key segment, skips lists and URLs, and shows head+tail characters of what it does mask. Everything a tool
returns goes to a cloud model, so hermcp redacts on its own. This module is the ONE place that decides what is
secret and how it is shown; every tool result, error, audit record and log line goes through it.

Two output styles (same detection, different rendering):
  READ   (`partial=True`)  identifiable, not reconstructable: values shorter than 16 characters become
                           "********"; longer ones keep 4 characters at each end around a fixed
                           "********" (the real length is never revealed): "abcd********wxyz".
  WRITE  (`partial=False`) the literal "[redacted]". Write/admin tools never show any character of a secret,
                           not even edge markers (and report changes as a key diff, see `diff_changes`).
Both are idempotent: masking an already masked value changes nothing.

Three layers, applied in this order to every value:
  1. key names (any depth; list items inherit the parent key): the whole value is replaced.
  2. value shapes (any string): known secret formats (hashes, bearer/basic, vendor prefixes, JWT, PEM, URL
     userinfo, secret query parameters, `name=value` assignments with secret-looking names, CLI flags).
  3. a careful entropy rule: only very long, random-looking strings, and only in secret contexts
     (env/headers/args/... maps, or the argument after a secret-looking CLI flag).

Two key profiles, because "key" is a secret word in config files but an ID in other outputs
(approvals' session_key, kanban idempotency_key):
  STRICT   config-like tools (config_*, models_*, mcp_*, providers_*)
  GENERIC  everything else (sessions, cron, kanban, chat, status, ...)

Deliberately NOT redacted: ids, names, paths, timestamps, URLs without credentials, UUIDs, commit hashes,
numbers/bools, `${VAR}` references, names of env vars (`key_env`, `*_env`). Keys that carry "key" but name an
id (`idempotency_key`, `request_key`, `approval_key`, `session_key`, `client_session`) and their CLI flags
(`--idempotency-key VALUE`, ...) are not secrets either and stay readable.

Containers named env/environment/headers/auth/oauth/... are credential containers: every string below them is
masked unless it is clearly harmless (`${VAR}` reference, URL without credentials, path, number/boolean word,
mime type, env-var NAME, or a value under a harmless key such as client_id/host/port/timeout).

Known gap (documented, not closable without destroying output): an opaque value under a neutral key name
in a neutral context (for example `notes: "<random token>"`) is not recognised. The entropy rule only
covers secret contexts and strings of 32+ characters.
"""
from __future__ import annotations

import math
import re
from typing import Any

MARK = "[redacted]"


class Label(str):
    """A string that is a LABEL (config path, tool-argument name), not a value. It is shown as is, unless it
    carries a secret shape. Tools wrap the labels they put into results (`key`) in this class."""


STARS = "********"
MAX_DEPTH = 30
_MASKED = re.compile(r"^(?:\[redacted\]|\*{8}|.{4}\*{8}.{4})$", re.S)


_SCHEME = re.compile(r"^(bearer|basic|token)\s+(\S.*)$", re.I | re.S)


def mask_value(value: str, partial: bool = False) -> str:
    """Render one secret. partial=False: "[redacted]". partial=True: "********" below 16 characters, else
    first 4 + "********" + last 4 (fixed middle, real length hidden). Idempotent."""
    if _MASKED.match(value):                                 # already masked: the write style drops edge markers
        return value if (partial or value == MARK) else MARK
    if not partial:
        return MARK
    m = _SCHEME.match(value)
    if m:                                                    # "Bearer <token>": keep the scheme, mark the token
        return f"{value[:m.start(2)].rstrip()} {mask_value(m.group(2), True)}"
    return STARS if len(value) < 16 else value[:4] + STARS + value[-4:]

# -- layer 1: key names ----------------------------------------------------------------------------
_STRICT_KEY = re.compile(
    r"(secret|token|passw|passphrase|pwd|(^|[_-])pass($|[_-])|hash|salt|api[_-]?key|apikey|access[_-]?key|"
    r"(^|[_-])key($|[_-])|private|credential|cookie|authorization|bearer|jwt|hmac|signature|dsn|"
    r"auth[_-]?(key|code|header|token))", re.I)
_GENERIC_KEY = re.compile(
    r"(secret|token|passw|passphrase|pwd|(^|[_-])pass($|[_-])|api[_-]?key|apikey|access[_-]?key|private[_-]?key|"
    r"credential|cookie|authorization|bearer|jwt|hmac|signature|dsn)", re.I)
# names that are secret CONTAINERS or weak secrets: a container keeps its shape and is masked leaf by leaf
# unless the leaf is harmless; a scalar is masked unless it is a plain mode word (auth: oauth)
_WEAK_KEY = re.compile(r"^(auth|oauth|authentication)$", re.I)
# credential containers: everything below is masked unless it is clearly harmless (_benign)
_CRED_CONTEXT = re.compile(r"^(env|environment|headers|extra_headers|http_headers|secrets|credentials|auth|oauth|"
                           r"authentication)$", re.I)
_HARMLESS_KEY = re.compile(
    r"(^|[_-])(client[_-]?id|host(name)?|port|region|lang(uage)?|locale|log[_-]?level|timeout|debug|verbose|"
    r"node[_-]?env|tz|timezone|path|home|dir|base[_-]?url|url|uri|endpoint|domain|content[_-]?type|accept|"
    r"user[_-]?agent|redirect[_-]?uri|scope|grant[_-]?type|audience|user|username|login|account)s?$", re.I)
# suffixes that name something (an id, a name, a location), even under a credential word: token-id, key-id, key-name
_LABEL_KEY = re.compile(r"(^|[_-])(id|ids|uuid|name|names|url|uri|path|file|dir|host|port|region|type|mode|method|"
                        r"store|source|backend|provider|format|style|level|fingerprint)$", re.I)
_MIME = re.compile(r"^[a-z]+/[a-z0-9.+*-]+(;\s*[a-z-]+=[\w.-]+)?$", re.I)
_ENV_NAME_VALUE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")     # OPENAI_API_KEY: the NAME of a variable
_WORDS = {"true", "false", "yes", "no", "on", "off", "none", "null", "debug", "info", "warn", "warning", "error",
          "production", "development", "test", "staging", "oauth", "basic", "bearer", "stdio", "sse", "http"}
# names that look secret but are not: env var NAMES, counters, limits, modes
# names that LOOK secret but are not: a compound whose last segment is one of these (token_count, url_file, ...)
_HARMLESS_TAIL = ("env", "env_var", "env_name", "key_env", "count", "counts", "limit", "limits", "budget", "max",
                  "min", "ttl", "timeout", "timeouts", "expires", "expiry", "usage", "used", "url", "urls", "uri",
                  "uris", "endpoint", "endpoints", "file", "files", "path", "paths", "dir", "dirs", "keybind",
                  "keybinding", "keybinds", "hotkey", "hotkeys", "shortcut", "shortcuts", "type", "mode", "method",
                  "name", "names", "length", "size", "len", "status", "state", "label", "labels", "record",
                  "record_key", "recording", "store", "source", "backend", "provider", "format", "style", "level",
                  "id", "ids", "uuid", "fingerprint")
_SAFE_TAIL = re.compile(r"^(?:.+[_-])?(%s)$" % "|".join(_HARMLESS_TAIL), re.I)
_SAFE_EXACT = re.compile(r"^(hotkeys?|keybinds?|shortcuts?)$", re.I)
_NUMERIC_SAFE_KEY = re.compile(r"tokens?", re.I)    # max_tokens: 4096 is a number, not a credential
_REF = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")

# containers whose string members are credentials more often than not (entropy rule context)
_SECRET_CONTEXT = re.compile(r"^(env|environment|headers|extra_headers|args|command|secrets|credentials|auth|"
                             r"params|options|extra_body|query)$", re.I)
# keys that carry "key" but are ids, not secrets (kanban idempotency_key, approvals session_key): never masked.
# Applies to the key-NAME layer and to the CLI-flag layer (--idempotency-key VALUE in argv lists).
_KEY_LIKE_ID = re.compile(r"^(?:idempotency|request|approval|api_?request)[_-]?key$|"
                          r"^(?:session_key|client_session|client_session_key)$", re.I)
_NONSECRET_ID_FLAG = re.compile(r"^--?(?:idempotency|request|approval|api[_-]?request)[_-]?key$|"
                                r"^--?(?:client[_-]?session|session[_-]?key)$", re.I)
_NONSECRET_FLAG_SUFFIX = re.compile(r"-(file|path|dir|url|uri|name|id|env|count|size|length|type|mode)$", re.I)
_SECRET_FLAG = re.compile(r"^--?(?:[a-z0-9]+-)*(token|secret|passw(?:or)?d|passwd|api-?key|apikey|key|auth|"
                          r"bearer|credential)(?:-[a-z0-9]+)*$", re.I)

# -- layer 2: value shapes -------------------------------------------------------------------------
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)
_HASH = re.compile(r"(?i)\b(?:scrypt|pbkdf2(?:[-_:]\w+)*|argon2\w*|bcrypt|sha(?:1|256|512)_crypt)[:$][^\s\"',;]+"
                   r"|\$(?:2[abxy]|argon2\w*|pbkdf2[\w-]*|[156])\$[^\s\"',;]{16,}")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+([A-Za-z0-9._~+/=-]{8,})")
_PREFIXED = re.compile(
    r"(?<![A-Za-z0-9])(?:hmcp_[A-Za-z0-9_-]{6,}|(?:sk|pk|rk)[-_][A-Za-z0-9_-]{16,}|"
    r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|xox[abprs]-[A-Za-z0-9-]{8,}|hf_[A-Za-z0-9]{20,}|"
    r"glpat-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{20,}|ctx7sk-[A-Za-z0-9-]{12,})")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@\"']+:[^/\s@\"']+@")
_QUERY_SECRET = re.compile(r"(?i)([?&;](?:[a-z0-9_.-]*(?:token|secret|passw(?:or)?d|signature|sig|api[_-]?key|"
                           r"apikey|key|auth|code_verifier))=)[^&\s\"'#]+")
_ASSIGN = re.compile(
    r"(?i)((?:[\"']?)\b[A-Za-z0-9_.-]*(?:secret|token|passw(?:or)?d|(?<![a-z])pass(?![a-z])|passphrase|api[_-]?key|apikey|"
    r"private[_-]?key|credential|authorization|cookie|hmac|salt|hash|dsn)[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*[\"']?)"
    r"(?!\[redacted\]|\$\{)([^\s\"',;}\]]{8,})")
_FLAG = re.compile(r"(?i)(?<![\w-])(--?(?:[a-z0-9-]*(?:token|secret|passw(?:or)?d|api[_-]?key|credential)[a-z0-9-]*)"
                   r"(\s+|=))(?!\[redacted\])([^\s\"']+)")


_SYMBOL = re.compile(r"[_+/=@#%^!~:]")
_STRONG_NAME = re.compile(r"secret|token|passw|(?<![a-z])pass(?![a-z])|api|private|credential|authorization|cookie|hmac|"
                          r"salt|dsn|key", re.I)


def _tokenish(v: str, min_len: int = 8) -> bool:
    """Does a free-text VALUE look like a credential? Needs length, no operator/path/URL start, and a digit, a
    symbol, or random camel case. Plain words, commands, paths, URLs and ${VAR} references never qualify."""
    if len(v) < min_len or v[0] in "&|;<>-([{$`'\"/~." or _REF.match(v) or re.match(r"^[a-z][a-z0-9+.-]*://", v, re.I):
        return False
    if v.isdigit():
        return False
    if re.search(r"\d", v) or _SYMBOL.search(v):
        return True
    return bool(re.search(r"[a-z]", v) and re.search(r"[A-Z]", v[1:]))


def _assign_sub(partial: bool):
    def sub(m: re.Match) -> str:
        val = m.group(2)
        if _MASKED.match(val) or STARS in val:               # already masked by an earlier rule
            return m.group(0)
        if not _tokenish(val):                               # "expired", "required", "Schlüssel", 12345678, ...
            return m.group(0)
        name = m.group(1).lower()
        if "hash" in name and not _STRONG_NAME.search(name) and re.fullmatch(r"[0-9a-f]{7,64}", val.lower().rstrip(").,")):
            return m.group(0)                                # commit hash / content hash, not a credential
        return m.group(1) + mask_value(val, partial)
    return sub


def _mv(partial: bool):
    return lambda m: mask_value(m.group(0), partial)


def _bearer_sub(partial: bool):
    def sub(m: re.Match) -> str:
        if not (_tokenish(m.group(2)) or "." in m.group(2) or (len(m.group(2)) >= 20 and not m.group(2).isalpha())):
            return m.group(0)                                # "basic configuration", "bearer tokens"
        return f"{m.group(1)} {mask_value(m.group(2), partial)}"
    return sub


def _prefixed_sub(partial: bool):
    def sub(m: re.Match) -> str:
        t = m.group(0)
        if re.match(r"(?:sk|pk|rk)[-_]", t) and not (re.search(r"\d", t) or re.search(r"[A-Z]", t)):
            return t                                         # identifier such as pk_user_profile_settings
        return mask_value(t, partial)
    return sub


def _flag_sub(partial: bool):
    def sub(m: re.Match) -> str:
        val = m.group(3)
        if _MASKED.match(val) or STARS in val:
            return m.group(0)
        explicit = m.group(2) == "="
        if not (_tokenish(val, 4) if explicit else _tokenish(val, 6)):
            return m.group(0)                                # `-secret-masking-1 && sudo`, `--secret none`
        return m.group(1) + mask_value(val, partial)
    return sub


def redact_text(text: str, partial: bool = False) -> str:
    """Redact secret shapes inside free text (logs, messages, CLI output). partial: see mask_value."""
    if not text or len(text) < 8:
        return text
    t = _PEM.sub(lambda m: MARK if not partial else STARS, text)      # a key block never shows edges
    t = _HASH.sub(_mv(partial), t)
    t = _JWT.sub(_mv(partial), t)
    t = _BEARER.sub(_bearer_sub(partial), t)
    t = _PREFIXED.sub(_prefixed_sub(partial), t)
    t = _URL_USERINFO.sub(lambda m: m.group(1) + mask_value(m.group(0)[len(m.group(1)):-1], partial) + "@", t)
    t = _QUERY_SECRET.sub(lambda m: m.group(1) + mask_value(m.group(0)[len(m.group(1)):], partial), t)
    t = _FLAG.sub(_flag_sub(partial), t)
    t = _ASSIGN.sub(_assign_sub(partial), t)
    return t


# -- layer 3: entropy ------------------------------------------------------------------------------
def _entropy(s: str) -> float:
    n = len(s)
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def looks_random(s: str, min_len: int = 32, min_entropy: float = 4.2) -> bool:
    """Long, whitespace-free, mixed-class, high-entropy string. Paths and URLs without credentials are not."""
    if len(s) < min_len or len(s) > 4096 or re.search(r"\s", s):
        return False
    if "/" in s and s.count("/") >= 2:                       # paths, URLs
        return False
    parts = re.split(r"[._:-]", s)
    if len(parts) >= 3 and max(len(p) for p in parts) <= 16:  # dotted/underscored identifier path, not a blob
        return False
    classes = sum(bool(re.search(p, s)) for p in ("[a-z]", "[A-Z]", "[0-9]"))
    if classes < 3 and not (classes == 2 and re.search(r"[-_+=]", s)):
        return False
    if re.fullmatch(r"[0-9a-fA-F-]{32,40}", s) and ("-" in s or len(s) in (32, 40)):
        return False                                         # UUIDs, md5/sha1: ids, not credentials
    return _entropy(s) >= min_entropy


def _random_anywhere(s: str) -> bool:
    """Any context: only unmistakable base64-like blobs (3 character classes, 40+ chars, high entropy)."""
    return looks_random(s, 40, 4.5) and bool(re.search(r"[a-z]", s) and re.search(r"[A-Z]", s) and re.search(r"[0-9]", s))


# -- structured -----------------------------------------------------------------------------------
def secret_key(key: Any, *, strict: bool) -> bool:
    if not isinstance(key, str) or not key:
        return False
    k = key.rsplit(".", 1)[-1]
    if _KEY_LIKE_ID.match(k):
        return False
    rx = _STRICT_KEY if strict else _GENERIC_KEY
    if _SAFE_EXACT.match(k) or _WEAK_KEY.match(k):
        return False
    if _SAFE_TAIL.match(k):
        return False
    return bool(rx.search(k))


_URL_KEY = re.compile(r"(^|[_-])(url|uri|endpoint|base_url|webhook)s?$", re.I)
_URL_QUERY = re.compile(r"^([a-z][a-z0-9+.-]*://[^?#\s]*)[?#].*$", re.I | re.S)


def _scrub_url_extras(url: str, partial: bool) -> str:
    """Query parameter values and fragment of a URL field are masked when they look like credentials
    (page=2, sort=asc stay)."""
    m = re.match(r"^([^?#]*)(?:\?([^#]*))?(?:#(.*))?$", url, re.S)
    base, query, frag = m.group(1), m.group(2), m.group(3)
    parts = []
    for pair in (query or "").split("&") if query else []:
        k, sep, v = pair.partition("=")
        parts.append(f"{k}{sep}{mask_value(v, partial)}" if sep and (_tokenish(v, 6) or looks_random(v, 20, 3.8)) else pair)
    out = base + ("?" + "&".join(parts) if query is not None else "")
    if frag is not None:
        out += "#" + (mask_value(frag, partial) if _tokenish(frag, 6) or looks_random(frag, 20, 3.8) else frag)
    return out


def _benign(key: str, value: str) -> bool:
    """Inside a credential container: is this string clearly NOT a secret? (conservative: doubt means mask)"""
    v = value.strip()
    if not v or len(v) > 300:
        return not v
    if v.lower() in _WORDS or re.fullmatch(r"-?\d+(\.\d+)?", v) or _ENV_NAME_VALUE.match(v) or _MIME.match(v):
        return True
    if len(v) < 16 and not re.search(r"[$+/=@#%^!~:{}\[\]\\]", v) and not re.search(r"[A-Z]{2,}", v):
        if not re.search(r"\d", v) or len(re.findall(r"[A-Za-z]", v)) >= 2:
            return True                                      # short plain word/id: "subwave-admin", "F2", "prod"
    if redact_text(v) != v:                                  # carries a secret shape
        return False
    if re.match(r"^[a-z][a-z0-9+.-]*://[^\s@]*$", v, re.I) and "@" not in v.split("://", 1)[1].split("/", 1)[0]:
        return not looks_random(v.rsplit("/", 1)[-1])        # URL without credentials
    if re.match(r"^(/|~/|\./|\.\./)[^\s]*$", v) and not _random_anywhere(v.rsplit("/", 1)[-1]):
        return True                                          # absolute / relative path
    if _HARMLESS_KEY.search(key) and not looks_random(v):
        return True                                          # client_id, host, port, region, ...
    if _LABEL_KEY.search(key) and not looks_random(v) and not redact_text(v) != v:
        return True                                          # token-id, key-name, url, path: a name or a location
    return False


def _scalar(value: Any, key: str, strict: bool, in_secret_ctx: bool, partial: bool = False, cred_ctx: bool = False) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if secret_key(key, strict=strict) and not _NUMERIC_SAFE_KEY.search(key) and abs(value) >= 10_000_000:
            return mask_value(str(value), partial)           # a number that long under a secret name
        return value
    if not isinstance(value, str):
        return value
    if value in ("", MARK, "[set]") or _REF.match(value):
        return value                                         # already-masked marker and ${VAR} references
    if isinstance(value, Label):
        return value if redact_text(value) == value else mask_value(str(value), partial)
    if _MASKED.match(value):
        return value if partial else MARK                    # write style: edge markers of an earlier pass go too
    if secret_key(key, strict=strict):
        return mask_value(value, partial)
    if _WEAK_KEY.match(key.rsplit(".", 1)[-1] if key else ""):
        return value if re.fullmatch(r"[a-z_-]{1,16}", value) else mask_value(value, partial)
    out = redact_text(value, partial)
    if _URL_KEY.search(key) and _URL_QUERY.match(out):
        out = _scrub_url_extras(out, partial)                # a URL field may carry a key in a parameter it names oddly
    if out != value:
        return out
    if cred_ctx and not _benign(key, value):
        return mask_value(value, partial)
    if in_secret_ctx and looks_random(value):
        return mask_value(value, partial)
    if _random_anywhere(value):
        return mask_value(value, partial)
    return out


def looks_secret(key: str, value: Any, *, strict: bool = True) -> bool:
    """Would this key/value pair be treated as a secret by NAME or by SHAPE (no env-context guessing)?
    Used to refuse writing it literally where a ${VAR} reference is required (mcp_add)."""
    if not isinstance(value, str) or not value or _REF.match(value):
        return False
    return (secret_key(key, strict=strict) or bool(_WEAK_KEY.match(key) and not re.fullmatch(r"[a-z_-]{1,16}", value))
            or redact_text(value) != value or _random_anywhere(value))


_LABEL = re.compile(r"^[A-Za-z0-9_.\-\[\]:/]{1,200}$")


def echo_labels(args: dict[str, Any] | None) -> dict[str, str]:
    """Arguments of a call that may be echoed back verbatim. The caller typed them, so repeating them reveals
    nothing new; masking them only hides WHICH value a result is about (config_get(key="kanban") answering
    {"key": "[redacted]"}). A string argument qualifies only if it looks like a plain label (name, dotted path,
    id) and no secret shape matches it; the VALUES next to a secret-named label are still masked by `redact`."""
    out: dict[str, str] = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and _LABEL.match(v) and redact_text(v) == v and not looks_random(v):
            out[str(k)] = v
    return out


def redact(value: Any, key: str = "", *, strict: bool = False, partial: bool = False, _ctx: bool = False,
           _cred: bool = False, _depth: int = 0, echo: dict[str, str] | None = None) -> Any:
    """Redacted deep copy of `value`; `key` is the name it sits under. `partial` selects the READ style (edge
    markers), default is the WRITE style ([redacted]). `echo` = call arguments that may be repeated verbatim
    (see echo_labels): a field of the same name holding exactly that string is a label."""
    if _depth > MAX_DEPTH:
        return MARK
    kw = dict(strict=strict, partial=partial, echo=echo)
    if isinstance(value, dict):
        # {"key": "providers.x.api_key", "before": ..., "after": ..., "value": ...}: the name decides
        pair = isinstance(value.get("key"), str) and secret_key(value["key"], strict=True)
        out: dict[Any, Any] = {}
        for k, v in value.items():
            if echo and isinstance(k, str) and isinstance(v, str) and echo.get(k) == v:
                out[k] = v                                    # the caller's own argument, echoed as a label
            elif pair and k in ("value", "before", "after") and v not in (None, "", False, True):
                out[k] = _redact_container_under_secret(v, _depth + 1, partial) if isinstance(v, (dict, list, tuple)) \
                    else mask_value(str(v), partial)
            elif secret_key(k, strict=strict) and isinstance(v, (dict, list, tuple)):
                out[k] = _redact_container_under_secret(v, _depth + 1, partial)
            else:
                ks = str(k)
                ctx = _ctx or bool(_SECRET_CONTEXT.match(ks))
                cred = _cred or bool(_CRED_CONTEXT.match(ks))
                out[k] = redact(v, ks, _ctx=ctx, _cred=cred, _depth=_depth + 1, **kw)
        return out
    if isinstance(value, (list, tuple)):
        res: list[Any] = []
        prev_flag = False
        for item in value:
            if isinstance(item, str) and prev_flag and not item.startswith(("-", "/", "~", "./")) and not _REF.match(item):
                res.append(mask_value(item, partial))         # argument after --token / --api-key ...
            else:
                res.append(redact(item, key, _ctx=_ctx, _cred=_cred, _depth=_depth + 1, **kw))
            prev_flag = (isinstance(item, str) and bool(_SECRET_FLAG.match(item)) and "=" not in item
                         and not _NONSECRET_FLAG_SUFFIX.search(item) and not _NONSECRET_ID_FLAG.match(item))
        return type(value)(res) if isinstance(value, tuple) else res
    return _scalar(value, key, strict, _ctx, partial, _cred)


def _redact_container_under_secret(value: Any, depth: int, partial: bool = False) -> Any:
    """Everything below a secret-named key (dict/list) is redacted leaf by leaf; shape stays visible."""
    if depth > MAX_DEPTH:
        return MARK
    if isinstance(value, dict):
        return {k: _redact_container_under_secret(v, depth + 1, partial) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_container_under_secret(v, depth + 1, partial) for v in value]
    if value is None or isinstance(value, (bool, int, float)) or value == "" or (isinstance(value, str) and _REF.match(value)):
        return value
    return mask_value(str(value), partial)


# -- write responses: a diff instead of before/after ----------------------------------------------
SECRET_UNCHANGED = "(secret, unverändert)"
SECRET_CHANGED = "(secret geändert)"


def _flatten(value: Any, path: str = "", out: dict[str, Any] | None = None, depth: int = 0) -> dict[str, Any]:
    out = {} if out is None else out
    if isinstance(value, dict) and value and depth < MAX_DEPTH:
        for k, v in value.items():
            _flatten(v, f"{path}.{k}" if path else str(k), out, depth + 1)
    elif isinstance(value, (list, tuple)) and value and depth < MAX_DEPTH:
        for i, v in enumerate(value):
            _flatten(v, f"{path}[{i}]", out, depth + 1)
    else:
        out[path] = value
    return out


def _nest(path: str, value: Any) -> dict[str, Any]:
    out: Any = value
    for part in reversed(path.split(".")):
        out = {part: out}
    return out


def diff_changes(path: str, before: Any, after: Any, *, strict: bool = True, limit: int = 60) -> dict[str, Any]:
    """Key diff of two values living at config path `path` (dotted). Secret leaves never carry a value:
    "(secret geändert)" when added/removed/changed, "(secret, unverändert)" when the addressed leaf stays.
    Which leaf is secret is decided by `redact` itself (one definition): a leaf is secret iff the write-style
    redaction of the surrounding tree changes it. Returns {"changes": [...], "secrets": [raw secret strings]}
    (the raw list is for callers that must scrub free text; it must never be put into a response)."""
    tb = _nest(path, before) if before is not None else {}       # None = absent
    ta = _nest(path, after) if after is not None else {}
    fb, fa = (_flatten(tb) if tb else {}), (_flatten(ta) if ta else {})
    frb = _flatten(redact(tb, strict=strict)) if tb else {}
    fra = _flatten(redact(ta, strict=strict)) if ta else {}
    raw_secrets: list[str] = []
    changes: list[dict[str, Any]] = []

    def _children(flat: dict[str, Any], p: str) -> bool:
        return any(k.startswith(p + ".") or k.startswith(p + "[") for k in flat)

    for p in dict.fromkeys([*fb, *fa]):
        in_b, in_a = p in fb, p in fa
        vb, va = fb.get(p), fa.get(p)
        if in_b and in_a and vb == va and p != path:
            continue                                       # unchanged leaves are not part of a diff
        empty_b = in_b and isinstance(vb, (dict, list)) and not vb
        empty_a = in_a and isinstance(va, (dict, list)) and not va
        if (empty_b and _children(fa, p)) or (empty_a and _children(fb, p)) or (empty_b and empty_a):
            continue                                       # an emptied/filled container is told by its leaves
        sec_b = in_b and frb.get(p) != vb
        sec_a = in_a and fra.get(p) != va
        for v, s_ in ((vb, sec_b), (va, sec_a)):
            if s_ and isinstance(v, str) and len(v) >= 4:
                raw_secrets.append(v)
        entry: dict[str, Any] = {"path": p}
        if in_b and in_a:
            entry["change"] = "unchanged" if vb == va else "changed"
            if sec_b or sec_a:
                entry["value"] = SECRET_UNCHANGED if vb == va else SECRET_CHANGED
            elif vb == va:
                entry["value"] = vb
            else:
                entry["before"], entry["after"] = vb, va
        elif in_b:
            entry["change"] = "removed"
            entry["before"] = SECRET_CHANGED if sec_b else vb
        else:
            entry["change"] = "added"
            entry["after"] = SECRET_CHANGED if sec_a else va
        changes.append(entry)
    res: dict[str, Any] = {"changes": changes[:limit], "secrets": raw_secrets}
    if len(changes) > limit:
        res["truncated"] = len(changes) - limit
    return res


def scrub(text: str, secrets: list[str]) -> str:
    """Remove known raw secret values (and their redacted shapes) from free text of a write response."""
    t = text or ""
    for s in sorted(set(secrets), key=len, reverse=True):
        t = t.replace(s, MARK)
    return redact_text(t)


# -- which profile for which tool -------------------------------------------------------------------
STRICT_TOOLSETS = {"config", "models", "mcp", "providers"}
NO_REDACT_TOOLS = {"hermes_search_tools", "hermes_describe_tool"}   # schemas/descriptions only, no data
# names/handles only (a list called "secrets" holds variable NAMES, so the key-name rule would blank all of it):
# shapes inside strings are still masked, key names are not used
NAMES_ONLY_TOOLS = {"secret_set", "secret_list", "secret_delete", "vault_list"}
# admin-access tools that only READ (audit log, key list): read style
READ_STYLE_ADMIN = {"audit_tail", "keys_list"}


def mode_for(toolset: str, name: str, access: str = "read") -> tuple[str, str] | None:
    """(profile, style) for a tool: profile 'strict'|'generic'|'names', style 'read'|'write'; None = do not touch.
    Write and admin tools get the write style (no secret characters at all), except admin tools that only read."""
    if name in NO_REDACT_TOOLS:
        return None
    style = "write" if access in ("write", "admin") and name not in READ_STYLE_ADMIN else "read"
    if name in NAMES_ONLY_TOOLS:
        return ("names", style)
    return ("strict" if toolset in STRICT_TOOLSETS else "generic", style)


def redact_shapes(value: Any, partial: bool = False, _depth: int = 0) -> Any:
    """Shape-level masking only (no key names): for tools that return variable NAMES, never values."""
    if _depth > MAX_DEPTH:
        return MARK
    if isinstance(value, dict):
        return {k: redact_shapes(v, partial, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_shapes(v, partial, _depth + 1) for v in value]
    if isinstance(value, str) and not _REF.match(value):
        return redact_text(value, partial)
    return value


def redact_result(toolset: str, name: str, result: Any, args: dict[str, Any] | None = None, access: str = "read") -> Any:
    mode = mode_for(toolset, name, access)
    if mode is None:
        return result
    if mode[0] == "names":
        return redact_shapes(result, partial=(mode[1] == "read"))
    return redact(result, "", strict=(mode[0] == "strict"), partial=(mode[1] == "read"), echo=echo_labels(args))

