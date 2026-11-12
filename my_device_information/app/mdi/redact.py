"""Redaction helpers. No Home Assistant imports, so this is easy to test."""

from __future__ import annotations

import re
from typing import Any

NONE = "none"
STANDARD = "standard"
STRICT = "strict"
REDACTED = "<redacted>"

# --- key-name based -------------------------------------------------------
_SENSITIVE_SUB = re.compile(
    r"(password|passwd|passphrase|secret|token|api[_\-]?key|apikey|access[_\-]?key|"
    r"private[_\-]?key|credential|webhook|bearer|authorization|cookie|"
    r"encryption[_\-]?key|instance[_\-]?id|fingerprint)",
    re.I,
)
_SENSITIVE_WORD = re.compile(
    r"(?:^|[_\-.])(pass|pin|otp|psk|auth|license)(?:$|[_\-.])", re.I
)
_COORD_KEY = re.compile(r"(?:^|[_\-.])(latitude|longitude|lat|lon|lng)(?:$|[_\-.])", re.I)
_STRICT_KEY = re.compile(
    r"(?:^|[_\-.])(unique_id|serial(?:_number)?|mac(?:_address)?|bssid|ssid|imei|"
    r"ip(?:_address)?|host(?:name)?|uuid|email|username)(?:$|[_\-.])",
    re.I,
)

# --- text based -----------------------------------------------------------
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_CRED = re.compile(r"(\b[a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@", re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")
_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_LONG_TOKEN = re.compile(
    r"\b(?=[A-Za-z0-9_\-]*[A-Z])(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{40,}\b"
)
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_REMOTE_HOST = re.compile(
    r"\b[\w\-]+(?:\.[\w\-]+)*\.(?:ui\.nabu\.casa|nabu\.casa|duckdns\.org|ts\.net|"
    r"myfritz\.net|dyndns\.org|synology\.me)\b",
    re.I,
)
_WEBHOOK_PATH = re.compile(r"(/api/webhook/)[^\s\"'/]+")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(
    r"\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b|"
    r"\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4})*::[0-9a-fA-F:]*"
)
_LOCAL_HOST = re.compile(r"\b[\w\-]+\.(?:local|lan|home|internal)\b", re.I)

# --- yaml line based ------------------------------------------------------
_YAML_SECRET = re.compile(
    r"^(\s*-?\s*[\w\-]*(?:pass(?:word|wd)|secret|token|api_?key|access_key|private_key|"
    r"client_id|credential|webhook_id|encryption_key|psk)[\w\-]*\s*:\s*)(?!!secret)(?=\S)(.+)$",
    re.I,
)
_YAML_COORD = re.compile(r"^(\s*(?:latitude|longitude)\s*:\s*)(?=\S)(.+)$", re.I)


def _empty(value: Any) -> bool:
    return value is None or value is False or (isinstance(value, str) and value == "")


class Redactor:
    """Mask secrets and personal data in strings and nested structures."""

    def __init__(self, level: str = STANDARD) -> None:
        self.level = level if level in (NONE, STANDARD, STRICT) else STANDARD
        self._aliases: dict[str, dict[str, str]] = {}

    @property
    def active(self) -> bool:
        return self.level != NONE

    def alias(self, kind: str, raw: str) -> str:
        """Return a stable pseudonym such as <ip-3> for the same raw value."""
        mapping = self._aliases.setdefault(kind, {})
        if raw not in mapping:
            mapping[raw] = f"<{kind}-{len(mapping) + 1}>"
        return mapping[raw]

    def _rep(self, kind: str):
        def _inner(match: re.Match[str]) -> str:
            if self.level == STRICT:
                return self.alias(kind, match.group(0))
            return f"<{kind}>"

        return _inner

    def _ip4(self, match: re.Match[str]) -> str:
        value = match.group(0)
        if value in ("127.0.0.1", "0.0.0.0"):
            return value
        if any(int(part) > 255 for part in value.split(".")):
            return value
        return self.alias("ip", value)

    def text(self, value: Any) -> Any:
        """Redact one string."""
        if self.level == NONE or not isinstance(value, str) or not value:
            return value
        s = _URL_CRED.sub(r"\1<redacted>@", value)
        s = _JWT.sub("<jwt>", s)
        s = _WEBHOOK_PATH.sub(r"\1<redacted>", s)
        s = _HEX.sub("<hex-redacted>", s)
        s = _LONG_TOKEN.sub("<token-redacted>", s)
        s = _MAC.sub(self._rep("mac"), s)
        s = _REMOTE_HOST.sub(self._rep("remote-host"), s)
        s = _EMAIL.sub(self._rep("email"), s)
        if self.level == STRICT:
            s = _IPV4.sub(self._ip4, s)
            s = _IPV6.sub(self._rep("ip6"), s)
            s = _LOCAL_HOST.sub(self._rep("host"), s)
        return s

    def yaml_text(self, value: str) -> str:
        """Redact the text of a YAML file line by line."""
        if self.level == NONE:
            return value
        out: list[str] = []
        for line in value.splitlines():
            match = _YAML_SECRET.match(line) or _YAML_COORD.match(line)
            if match:
                line = f"{match.group(1)}{REDACTED}"
            out.append(self.text(line))
        return "\n".join(out)

    def deep(self, obj: Any, key: str = "") -> Any:
        """Redact a nested structure, using key names as hints."""
        if self.level == NONE:
            return obj
        if key:
            if _SENSITIVE_SUB.search(key) or _SENSITIVE_WORD.search(key):
                return obj if _empty(obj) else REDACTED
            if (
                _COORD_KEY.search(key)
                and isinstance(obj, (int, float))
                and not isinstance(obj, bool)
            ):
                return round(obj, 1) if self.level == STANDARD else REDACTED
            if self.level == STRICT and _STRICT_KEY.search(key):
                return obj if _empty(obj) else REDACTED
        if isinstance(obj, dict):
            return {k: self.deep(v, str(k)) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [self.deep(v, key) for v in obj]
        if isinstance(obj, str):
            return self.text(obj)
        return obj
