"""Scope model: access level, toolsets, profiles, deny_tools, approvals, secrets_write."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ACCESS_LEVELS = {"read": 0, "write": 1, "admin": 2}


class ScopeError(ValueError):
    pass


@dataclass
class Scopes:
    access: str = "read"
    toolsets: list[str] = field(default_factory=lambda: ["*"])
    profiles: list[str] = field(default_factory=lambda: ["*"])
    deny_tools: list[str] = field(default_factory=list)
    approvals: bool = False
    secrets_write: bool = False
    dangerous: bool = False       # exfiltration / irreversible tools: explicit opt-in, never implied by '*'
    inbox_push: bool = False      # may deliver events into the inbox (inbox_push, POST /inbox): explicit opt-in
    approvals_always: bool = False  # may answer an approval with `always` (permanent rule rewrite): explicit opt-in,
                                    # never implied by admin or '*'; additionally needs access=admin

    def __post_init__(self) -> None:
        if self.access not in ACCESS_LEVELS:
            raise ScopeError(f"access must be one of {sorted(ACCESS_LEVELS)}, got {self.access!r}")
        for name in ("toolsets", "profiles", "deny_tools"):
            v = getattr(self, name)
            if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
                raise ScopeError(f"{name} must be a list of non-empty strings")
        if not self.toolsets or not self.profiles:
            raise ScopeError("toolsets and profiles must not be empty (use ['*'] for all)")
        self.approvals = self.approvals is True
        self.secrets_write = self.secrets_write is True
        self.dangerous = self.dangerous is True
        self.inbox_push = self.inbox_push is True
        self.approvals_always = self.approvals_always is True

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Scopes":
        d = d or {}
        known = {"access", "toolsets", "profiles", "deny_tools", "approvals", "secrets_write", "dangerous", "inbox_push",
                 "approvals_always"}
        extra = set(d) - known
        if extra:
            raise ScopeError(f"unknown scope fields: {sorted(extra)}")
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        return {
            "access": self.access, "toolsets": list(self.toolsets), "profiles": list(self.profiles),
            "deny_tools": list(self.deny_tools), "approvals": self.approvals,
            "secrets_write": self.secrets_write, "dangerous": self.dangerous, "inbox_push": self.inbox_push,
            "approvals_always": self.approvals_always,
        }

    # -- checks -------------------------------------------------------------------------
    @property
    def all_profiles(self) -> bool:
        return "*" in self.profiles

    def level(self) -> int:
        return ACCESS_LEVELS[self.access]

    def profile_allowed(self, profile: str) -> bool:
        return self.all_profiles or profile in self.profiles

    @property
    def restricted(self) -> bool:
        """True for every key that is limited to named profiles (pinned to one or to a list)."""
        return not self.all_profiles

    def bound_profile(self) -> str | None:
        """The one profile this key is bound to (None for unrestricted keys and for keys with several profiles)."""
        return self.profiles[0] if (not self.all_profiles and len(self.profiles) == 1) else None

    def default_profile(self) -> str:
        """Profile used when a call omits `profile`: the key's only profile if pinned to one."""
        if not self.all_profiles and len(self.profiles) == 1:
            return self.profiles[0]
        return "default"

    def tool_allowed(self, tool) -> tuple[bool, str]:
        """Static (argument-independent) check. Returns (allowed, reason)."""
        if tool.name in self.deny_tools:
            return False, "tool is denied for this key"
        if ACCESS_LEVELS[tool.access] > self.level():
            return False, f"requires access={tool.access}"
        # The approvals toolset is never implied by '*': needs the explicit flag.
        if (tool.toolset == "approvals" or getattr(tool, "requires_approvals", False)) and not self.approvals:
            return False, "requires approvals scope"
        if tool.toolset != "meta" and tool.toolset not in self.toolsets and "*" not in self.toolsets:
            return False, f"toolset {tool.toolset!r} not in key scope"
        if getattr(tool, "dangerous", False) and not self.dangerous:
            return False, "dangerous tool: key needs the explicit `dangerous` scope"
        if getattr(tool, "requires_inbox_push", False) and not self.inbox_push:
            return False, "requires the explicit `inbox_push` scope"
        if getattr(tool, "secrets", False) and not self.secrets_write:
            return False, "requires secrets_write scope"
        # Profile-restricted keys: a tool that is not profile-aware is only reachable when it is classified as
        # safe for them (see pinning.py); admin tools never are for a key bound to ONE profile.
        if not self.all_profiles:
            from .pinning import bound_verdict
            ok, why = bound_verdict(tool, bound=self.bound_profile() is not None)
            if not ok:
                return False, why
        # Mutating a global (non profile-aware) resource is not allowed for profile-pinned keys.
        if not tool.profile_aware and tool.access != "read" and not self.all_profiles:
            from .pinning import classify
            if classify(tool) not in ("own", "open"):
                return False, "global write not allowed for profile-pinned key"
        return True, ""
