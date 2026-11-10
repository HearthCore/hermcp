"""One place that decides who the client is.

Behind a reverse proxy the TCP peer is always the proxy, so the real client address
only exists in a forwarding header — and only trustworthy if that peer really is a
proxy we configured. This module implements exactly that:

* The peer is **not** in ``HERMCP_TRUSTED_PROXIES``: every forwarding header
  (``X-Forwarded-For``, ``Forwarded``, ``X-Real-IP``, ``X-Forwarded-Proto``,
  ``X-Forwarded-Host``) is **ignored**. Nothing a caller writes about itself can
  change the client address, the scheme or the host. That is the anti-spoofing rule.
* The peer **is** trusted: ``X-Forwarded-For`` is walked from right to left and the
  first address that is not itself a trusted proxy is the client. That is what makes
  a multi-hop chain correct — the left entries are written by the client and cannot
  be believed, the right entries were appended by proxies we trust. RFC 7239
  ``Forwarded`` is used when there is no ``X-Forwarded-For``.
* Unparsable entries (``unknown``, obfuscated ``_hidden``, garbage) are skipped, never
  turned into an address. If every entry is a trusted proxy, the leftmost valid one is
  the client (a chain of proxies only).

``settings.trust_proxy`` (``HERMCP_TRUST_PROXY``) is the old boolean and no longer
widens anything: it is kept for the uvicorn/oauth wiring and documented as deprecated.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

IP_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")
PROTO_HEADER = "x-forwarded-proto"
HOST_HEADER = "x-forwarded-host"
FORWARDING_HEADERS = (PROTO_HEADER, HOST_HEADER, *IP_HEADERS)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def parse_ip(value: Any) -> Optional[Any]:
    """A single address, or None. Accepts ``1.2.3.4``, ``::1``, ``1.2.3.4:5678``, ``[::1]:443``
    and RFC 7239 quoted forms (``for="[2001:db8::1]:4711"``)."""
    if not isinstance(value, str):
        return None
    text = value.strip().strip('"')
    if not text:
        return None
    if text.startswith("[") and "]" in text:
        text = text[1:text.index("]")]
    elif text.count(":") == 1 and "." in text:            # v4 with port
        text = text.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return mapped
    return ip


def parse_networks(values: Iterable[str]) -> List[Any]:
    """Trusted-proxy entries as networks; a bare address becomes a /32 or /128."""
    out: List[Any] = []
    for raw in values or []:
        text = str(raw).strip()
        if not text:
            continue
        try:
            out.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue
    return out


def invalid_networks(values: Iterable[str]) -> List[str]:
    """Entries that are not a valid address or CIDR (for load-time validation)."""
    bad: List[str] = []
    for raw in values or []:
        text = str(raw).strip()
        if not text:
            continue
        try:
            ipaddress.ip_network(text, strict=False)
        except ValueError:
            bad.append(text)
    return bad


def in_networks(ip: Any, networks: Sequence[Any]) -> bool:
    if ip is None:
        return False
    return any(net.version == ip.version and ip in net for net in networks)


# ---------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------

@dataclass
class ClientInfo:
    """Who we are talking to, as far as we are allowed to know."""

    ip: str = ""             # effective client address, "" when unknown
    trusted: bool = False    # the direct peer is a configured trusted proxy
    via_proxy: bool = False  # forwarding headers were used to derive ip/proto/host
    proto: str = "http"      # scheme the client used
    host: str = ""           # host the client asked for
    peer: str = ""           # the direct TCP peer, always as seen

    @property
    def ip_obj(self) -> Optional[Any]:
        return parse_ip(self.ip)


def _header(headers: Any, name: str) -> str:
    try:
        return str(headers.get(name) or "")
    except Exception:  # noqa: BLE001 - a header mapping we do not understand
        return ""


def _xff_chain(value: str) -> List[Any]:
    out: List[Any] = []
    for part in value.split(","):
        ip = parse_ip(part)
        if ip is not None:
            out.append(ip)
    return out


def _forwarded_elements(value: str) -> List[Tuple[Optional[Any], str, str]]:
    """``(for-address, proto, host)`` per RFC 7239 element, left to right."""
    out: List[Tuple[Optional[Any], str, str]] = []
    for element in value.split(","):
        addr, proto, host = None, "", ""
        for pair in element.split(";"):
            key, _, val = pair.partition("=")
            key = key.strip().lower()
            val = val.strip().strip('"')
            if key == "for":
                addr = parse_ip(val)
            elif key == "proto":
                proto = val.lower()
            elif key == "host":
                host = val
        out.append((addr, proto, host))
    return out


def _pick_from_chain(chain: List[Any], networks: Sequence[Any]) -> Optional[Any]:
    """Right to left: the first address that is not one of our own proxies."""
    for ip in reversed(chain):
        if not in_networks(ip, networks):
            return ip
    return chain[0] if chain else None


def _valid_host(value: str) -> str:
    """A host header we are willing to build links from (``host`` or ``host:port``)."""
    text = (value or "").split(",")[0].strip()
    if not text or len(text) > 255:
        return ""
    if "://" in text or "/" in text or " " in text or "@" in text:
        return ""
    base, _, port = text.rpartition(":")
    if base and port.isdigit():
        return text
    return text if ":" not in text else text          # a bare IPv6 literal is fine as-is


def resolve(peer: Optional[str], headers: Any, trusted: Sequence[Any] = (), scheme: str = "http") -> ClientInfo:
    """Decide the effective client from the peer plus the headers it is allowed to have set."""
    peer_obj = parse_ip(peer)
    peer_text = str(peer_obj) if peer_obj is not None else (str(peer) if peer else "")
    info = ClientInfo(ip=peer_text, peer=peer_text, proto=(scheme or "http").lower(),
                      host=_valid_host(_header(headers, "host")), trusted=False, via_proxy=False)
    if peer_obj is None or not in_networks(peer_obj, trusted):
        return info                                     # untrusted or unknown peer: headers ignored

    info.trusted = True
    via_proxy = False

    chain = [ip for ip in _xff_chain(_header(headers, "x-forwarded-for")) if ip is not None]
    proto = _header(headers, PROTO_HEADER).split(",")[0].strip().lower()
    host = _header(headers, HOST_HEADER)
    if not chain:
        elements = _forwarded_elements(_header(headers, "forwarded"))
        chain = [addr for addr, _, _ in elements if addr is not None]
        if not proto:
            proto = next((p for _, p, _ in elements if p), "")
        if not host:
            host = next((h for _, _, h in elements if h), "")
    if not chain:
        # Trusted peer, but it forwarded no address (or only garbage): fall back to
        # X-Real-IP, and otherwise keep the peer itself.
        real = parse_ip(_header(headers, "x-real-ip"))
        chain = [real] if real is not None else []

    if chain:
        chosen = _pick_from_chain(chain, trusted)
        if chosen is not None:
            info.ip = str(chosen)
            via_proxy = True
    if proto in ("http", "https"):
        info.proto = proto
        via_proxy = True
    clean_host = _valid_host(host)
    if clean_host:
        info.host = clean_host
        via_proxy = True
    info.via_proxy = via_proxy
    return info


def request_base(info: ClientInfo, fallback: str = "") -> str:
    """``scheme://host`` as the client sees it (for links, cookie flags, redirects)."""
    host = info.host or ""
    if not host:
        return fallback.rstrip("/")
    return f"{info.proto}://{host}".rstrip("/")
