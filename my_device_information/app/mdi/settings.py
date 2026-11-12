"""Runtime settings. Works as a Home Assistant add-on or as a plain Docker container."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import SLUG

DEFAULTS: dict[str, Any] = {
    "save_location": "",
    "save_subfolder": SLUG,
    "keep_history": False,
    "history_keep": 20,
    "redaction": "standard",
    "include_attributes": True,
    "include_configuration": True,
    "include_logs": True,
    "log_lines": 40,
    "include_services": False,
    "exclude_domains": "",
    "auto_refresh_hours": 0,
}
REDACTION_LEVELS = ("none", "standard", "strict")
KINDS = ("overview", "full", "json")
FILE_LEVEL_CHOICES = ("default",) + REDACTION_LEVELS  # "default" = follow the global option
MAX_HISTORY_KEEP = 1000
MAX_AUTO_HOURS = 168
_SUBFOLDER_PART = re.compile(r"^[\w][\w .\-]*$")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def clamp_int(value: Any, lo: int, hi: int, fallback: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return fallback


def _coerce(options: dict[str, Any]) -> dict[str, Any]:
    out = {**DEFAULTS, **{k: v for k, v in options.items() if k in DEFAULTS and v is not None}}
    for key in ("keep_history", "include_attributes", "include_configuration",
                "include_logs", "include_services"):
        out[key] = _as_bool(out[key])
    for key, lo, hi in (
        ("log_lines", 10, 500),
        ("auto_refresh_hours", 0, MAX_AUTO_HOURS),
        ("history_keep", 0, MAX_HISTORY_KEEP),
    ):
        out[key] = clamp_int(out[key], lo, hi, DEFAULTS[key])
    if out["redaction"] not in REDACTION_LEVELS:
        out["redaction"] = "standard"
    out["exclude_domains"] = str(out["exclude_domains"] or "")
    out["save_subfolder"] = str(out["save_subfolder"] or "")
    out["save_location"] = str(out["save_location"] or "")
    return out


def clean_subfolder(value: str) -> str:
    """Return a safe relative sub-folder ('' means the root of the location)."""
    value = (value or "").strip().replace("\\", "/").strip("/")
    if not value:
        return ""
    parts = [p.strip() for p in value.split("/") if p.strip()]
    for part in parts:
        if part in (".", "..") or not _SUBFOLDER_PART.match(part):
            raise ValueError(
                "Folder names may only contain letters, numbers, spaces, dots, dashes and "
                "underscores, and cannot use '..'."
            )
    return "/".join(parts)


def normalize_files(raw: Any, current: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge a (possibly partial) per-file settings request into the current settings."""
    if not isinstance(raw, dict):
        raise ValueError("Invalid file settings")
    out = {kind: dict(cfg) for kind, cfg in current.items()}
    for kind, cfg in raw.items():
        if kind not in KINDS:
            raise ValueError(f"Unknown file \u201c{kind}\u201d")
        if not isinstance(cfg, dict):
            raise ValueError("Invalid file settings")
        if "enabled" in cfg:
            out[kind]["enabled"] = _as_bool(cfg["enabled"])
        if "copy" in cfg:
            out[kind]["copy"] = _as_bool(cfg["copy"])
        if "redaction" in cfg:
            if cfg["redaction"] not in FILE_LEVEL_CHOICES:
                raise ValueError("Unknown privacy level")
            out[kind]["redaction"] = cfg["redaction"]
    return out


@dataclass
class Location:
    id: str
    label: str
    path: Path

    @property
    def writable(self) -> bool:
        return self.path.is_dir() and os.access(self.path, os.W_OK)


@dataclass
class Runtime:
    mode: str  # "addon" or "docker"
    options: dict[str, Any]
    data_dir: Path
    locations: dict[str, Location]
    rest_base: str
    ws_url: str
    token: str
    supervisor_base: str | None
    supervisor_token: str | None
    config_dir: Path | None
    port: int
    bind: str
    web_password: str
    run_once: bool
    selection: dict[str, Any] = field(default_factory=dict)
    prefs: dict[str, Any] = field(default_factory=dict)

    # -- saved choice made in the web page ------------------------------------
    @property
    def _selection_file(self) -> Path:
        return self.data_dir / "selection.json"

    def load_selection(self) -> None:
        try:
            self.selection = json.loads(self._selection_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.selection = {}

    def save_selection(self, selection: dict[str, Any]) -> None:
        self.selection = selection
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._selection_file.write_text(json.dumps(selection), encoding="utf-8")
        except OSError:
            pass

    def clear_selection(self) -> None:
        self.selection = {}
        try:
            self._selection_file.unlink()
        except OSError:
            pass

    # -- other web settings: which files, masking per file, second place, schedule ----
    @property
    def _prefs_file(self) -> Path:
        return self.data_dir / "prefs.json"

    def load_prefs(self) -> None:
        try:
            data = json.loads(self._prefs_file.read_text(encoding="utf-8"))
            self.prefs = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.prefs = {}

    def save_prefs(self, prefs: dict[str, Any]) -> None:
        """Write to disk first; raises OSError so the page can say the save did not stick."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._prefs_file.with_name("prefs.json.tmp")
        tmp.write_text(json.dumps(prefs), encoding="utf-8")
        tmp.replace(self._prefs_file)
        self.prefs = prefs

    def file_settings(self, prefs: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
        """Per file: enabled, masking ('default' follows the global option) and second copy."""
        saved = (self.prefs if prefs is None else prefs).get("files")
        saved = saved if isinstance(saved, dict) else {}
        out: dict[str, dict[str, Any]] = {}
        for kind in KINDS:
            cfg = saved.get(kind) if isinstance(saved.get(kind), dict) else {}
            level = cfg.get("redaction", "default")
            out[kind] = {
                "enabled": _as_bool(cfg.get("enabled", True)),
                "redaction": level if level in FILE_LEVEL_CHOICES else "default",
                "copy": _as_bool(cfg.get("copy", False)),
            }
        return out

    def extra_settings(self, prefs: dict[str, Any] | None = None) -> dict[str, str]:
        """The optional second place that gets a copy of the files that ask for one."""
        saved = (self.prefs if prefs is None else prefs).get("extra")
        saved = saved if isinstance(saved, dict) else {}
        return {
            "location": str(saved.get("location") or ""),
            "subfolder": str(saved.get("subfolder", DEFAULTS["save_subfolder"]) or ""),
        }

    def extra_dir(self, prefs: dict[str, Any] | None = None) -> Path | None:
        """Resolved second folder, None if no place is chosen. Raises ValueError if unsafe."""
        extra = self.extra_settings(prefs)
        loc = self.locations.get(extra["location"])
        if loc is None:
            return None
        sub = clean_subfolder(extra["subfolder"])
        target = (loc.path / sub) if sub else loc.path
        resolved = target.resolve()
        resolved.relative_to(loc.path.resolve())  # raises ValueError if it escapes
        return resolved

    def auto_refresh_hours(self) -> int:
        """Live schedule: the web page's choice wins over the add-on / .env default."""
        fallback = self.options["auto_refresh_hours"]
        value = self.prefs.get("auto_refresh_hours")
        if value is None:
            return fallback
        return clamp_int(value, 0, MAX_AUTO_HOURS, fallback)

    # -- where files go ----------------------------------------------------------
    def effective(self) -> dict[str, Any]:
        """Location id, sub-folder and history flag currently in force."""
        loc = self.selection.get("location") or self.options["save_location"]
        if loc not in self.locations:
            loc = next(iter(self.locations))
        sub = self.selection.get("subfolder", self.options["save_subfolder"])
        try:
            sub = clean_subfolder(sub)
        except ValueError:
            sub = clean_subfolder(DEFAULTS["save_subfolder"])
        hist = self.selection.get("keep_history", self.options["keep_history"])
        keep = clamp_int(
            self.selection.get("history_keep", self.options["history_keep"]),
            0, MAX_HISTORY_KEEP, DEFAULTS["history_keep"],
        )
        return {"location": loc, "subfolder": sub, "keep_history": bool(hist), "history_keep": keep}

    def output_dir(self) -> Path:
        eff = self.effective()
        base = self.locations[eff["location"]].path
        target = (base / eff["subfolder"]) if eff["subfolder"] else base
        resolved = target.resolve()
        resolved.relative_to(base.resolve())  # raises ValueError if it escapes
        return resolved


def _parse_extra_dirs(raw: str) -> dict[str, Location]:
    """EXTRA_OUTPUT_DIRS='NAS=/mnt/nas,Backups=/backups'"""
    out: dict[str, Location] = {}
    for item in filter(None, (x.strip() for x in raw.split(","))):
        label, _, path = item.partition("=")
        if not path:
            label, path = Path(item).name or item, item
        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "extra"
        out[key] = Location(key, label.strip(), Path(path.strip()))
    return out


def load() -> Runtime:
    mode = os.environ.get("MDI_MODE", "docker").lower()
    if mode == "addon":
        try:
            raw = json.loads(Path("/data/options.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        options = _coerce(raw)
        token = os.environ.get("SUPERVISOR_TOKEN", "")
        locations = {
            "share": Location("share", "Share folder (/share)", Path("/share")),
            "media": Location("media", "Media folder (/media)", Path("/media")),
            "addon_config": Location(
                "addon_config", "This add-on's config folder", Path("/config")
            ),
            # Home Assistant's own config folder (what the File editor calls /config).
            # Inside an add-on it is mounted at /homeassistant.
            "homeassistant": Location(
                "homeassistant", "Home Assistant config folder (/config)", Path("/homeassistant")
            ),
        }
        cfg = Path("/homeassistant")
        rt = Runtime(
            mode="addon",
            options=options,
            data_dir=Path("/data"),
            locations=locations,
            rest_base="http://supervisor/core",
            ws_url="ws://supervisor/core/websocket",
            token=token,
            supervisor_base="http://supervisor",
            supervisor_token=token,
            config_dir=cfg if cfg.is_dir() else None,
            port=8099,
            bind="0.0.0.0",
            web_password="",
            run_once=False,
        )
    else:
        options = _coerce({k: os.environ.get(k.upper()) for k in DEFAULTS})
        url = os.environ.get("HA_URL", "http://homeassistant.local:8123").rstrip("/")
        ws = re.sub(r"^http", "ws", url) + "/api/websocket"
        output = Path(os.environ.get("OUTPUT_DIR", "/output"))
        locations = {"output": Location("output", f"Output folder ({output})", output)}
        locations.update(_parse_extra_dirs(os.environ.get("EXTRA_OUTPUT_DIRS", "")))
        cfg = Path(os.environ.get("HA_CONFIG_DIR", "/config"))
        if cfg.is_dir():
            # Only works if the config folder is mounted read-write in docker-compose.
            locations.setdefault(
                "homeassistant",
                Location("homeassistant", f"Home Assistant config folder ({cfg})", cfg),
            )
        rt = Runtime(
            mode="docker",
            options=options,
            data_dir=Path(os.environ.get("DATA_DIR", "/data")),
            locations=locations,
            rest_base=url,
            ws_url=ws,
            token=os.environ.get("HA_TOKEN", ""),
            supervisor_base=None,
            supervisor_token=None,
            config_dir=cfg,
            port=int(os.environ.get("PORT", "8099")),
            bind=os.environ.get("BIND", "0.0.0.0"),
            web_password=os.environ.get("WEB_PASSWORD", ""),
            run_once=os.environ.get("RUN_MODE", "server").lower() == "once",
        )
    rt.load_selection()
    rt.load_prefs()
    return rt
