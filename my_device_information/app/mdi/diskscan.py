"""Blocking disk collectors. Run these in an executor. No Home Assistant imports."""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from .redact import Redactor

MAX_FILE_CHARS = 150_000
MAX_TOTAL_CHARS = 600_000
MAX_FILES = 60
MAX_LOG_BYTES = 1_500_000

BASE_FILES = [
    "configuration.yaml",
    "automations.yaml",
    "scripts.yaml",
    "scenes.yaml",
    "customize.yaml",
    "groups.yaml",
    "ui-lovelace.yaml",
]
BLOCKED_NAMES = {"secrets.yaml", "secrets.yml"}
BLOCKED_PARTS = {".storage", ".git", ".cloud"}
INCLUDE_RE = re.compile(
    r"!include(?:_dir_(?:merge_)?(?:named|list))?\s+['\"]?([^\s'\"#]+)"
)
LOG_RE = re.compile(
    r"^(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[.,]\d+)?)\s+(WARNING|ERROR|CRITICAL)\s+"
    r"\(([^)]*)\)\s+\[([^\]]*)\]\s+(.*)$"
)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _safe(root: Path, candidate: Path) -> Path | None:
    """Return a resolved path only if it is inside root and not sensitive."""
    try:
        resolved = candidate.resolve()
        rel = resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if resolved.name.lower() in BLOCKED_NAMES:
        return None
    if any(part in BLOCKED_PARTS for part in rel.parts):
        return None
    return resolved


def _config_files(root: Path, red: Redactor) -> dict[str, Any]:
    files: dict[str, str] = {}
    truncated: list[str] = []
    skipped: list[str] = []
    seen: set[Path] = set()
    total = 0
    queue: list[Path] = [root / name for name in BASE_FILES]

    while queue:
        candidate = queue.pop(0)
        path = _safe(root, candidate)
        if path is None or path in seen:
            continue
        seen.add(path)
        if path.is_dir():
            children = sorted(
                p for p in path.rglob("*") if p.suffix.lower() in (".yaml", ".yml")
            )
            queue.extend(children[:30])
            continue
        if not path.is_file():
            continue
        rel = str(path.relative_to(root.resolve()))
        if len(files) >= MAX_FILES or total >= MAX_TOTAL_CHARS:
            skipped.append(rel)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in INCLUDE_RE.finditer(text):
            queue.append(path.parent / match.group(1))
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS]
            truncated.append(rel)
        total += len(text)
        files[rel] = red.yaml_text(text)
    return {"files": files, "truncated": truncated, "skipped": skipped}


def _custom_components(root: Path) -> list[dict[str, Any]]:
    result = []
    for manifest in sorted((root / "custom_components").glob("*/manifest.json")):
        data = _load_json(manifest) or {}
        result.append(
            {
                "domain": data.get("domain") or manifest.parent.name,
                "name": data.get("name"),
                "version": data.get("version"),
                "integration_type": data.get("integration_type"),
                "iot_class": data.get("iot_class"),
                "documentation": data.get("documentation"),
            }
        )
    return result


def _hacs(root: Path) -> list[dict[str, Any]]:
    doc = _load_json(root / ".storage" / "hacs.repositories")
    data = (doc or {}).get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        return []
    repos = []
    for repo in data.values():
        if not isinstance(repo, dict) or not repo.get("installed"):
            continue
        repos.append(
            {
                "repository": repo.get("full_name"),
                "category": repo.get("category"),
                "version": repo.get("installed_version") or repo.get("version_installed"),
            }
        )
    return sorted(repos, key=lambda r: str(r.get("repository")))[:200]


def _count_cards(card: Any, counter: Counter, depth: int = 0) -> None:
    if not isinstance(card, dict) or depth > 10:
        return
    counter[str(card.get("type", "?"))] += 1
    for child in card.get("cards") or []:
        _count_cards(child, counter, depth + 1)
    _count_cards(card.get("card"), counter, depth + 1)


def summarize_views(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarise the views and card types of one dashboard config."""
    views = []
    for view in config.get("views") or []:
        counter: Counter = Counter()
        for card in view.get("cards") or []:
            _count_cards(card, counter)
        for section in view.get("sections") or []:
            for card in (section or {}).get("cards") or []:
                _count_cards(card, counter)
        views.append(
            {
                "title": view.get("title"),
                "path": view.get("path"),
                "type": view.get("type") or "masonry",
                "cards": dict(counter.most_common()),
                "badges": len(view.get("badges") or []),
            }
        )
    return views


def _lovelace(root: Path) -> dict[str, Any]:
    storage = root / ".storage"
    index = _load_json(storage / "lovelace_dashboards")
    items = ((index or {}).get("data") or {}).get("items") or []
    entries: list[tuple[str, dict[str, Any]]] = [
        ("lovelace", {"title": "Default dashboard", "url_path": None, "mode": "storage"})
    ]
    for item in items:
        entries.append(
            (
                f"lovelace.{item.get('id')}",
                {
                    "title": item.get("title"),
                    "url_path": item.get("url_path"),
                    "mode": item.get("mode", "storage"),
                },
            )
        )
    dashboards = []
    for filename, meta in entries:
        doc = _load_json(storage / filename)
        config = ((doc or {}).get("data") or {}).get("config") if doc else None
        record = dict(meta)
        if config is None:
            record["views"] = None
            record["note"] = (
                "auto-generated or YAML mode (no stored config)"
                if filename == "lovelace" or meta.get("mode") == "yaml"
                else "no stored config found"
            )
            dashboards.append(record)
            continue
        record["views"] = summarize_views(config)
        dashboards.append(record)

    resources_doc = _load_json(storage / "lovelace_resources")
    resources = [
        {"url": r.get("url"), "type": r.get("type")}
        for r in ((resources_doc or {}).get("data") or {}).get("items") or []
    ]
    return {"dashboards": dashboards, "resources": resources}


def parse_log(raw: str, scanned_kb: int, max_entries: int) -> dict[str, Any]:
    """Group WARNING/ERROR/CRITICAL lines of a Home Assistant log."""
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for line in raw.splitlines():
        match = LOG_RE.match(line)
        if not match:
            continue
        ts, level, _thread, logger, message = match.groups()
        key = (level, logger, re.sub(r"\d+", "N", message)[:200])
        entry = groups.get(key)
        if entry is None:
            groups[key] = {
                "level": level,
                "logger": logger,
                "count": 1,
                "last_seen": ts,
                "message": message[:300],
            }
        else:
            entry["count"] += 1
            entry["last_seen"] = ts
            entry["message"] = message[:300]
    entries = sorted(groups.values(), key=lambda e: e["last_seen"], reverse=True)
    return {
        "available": True,
        "scanned_kb": scanned_kb,
        "unique_problems": len(groups),
        "entries": entries[:max_entries],
    }


def _read_log(root: Path, max_entries: int) -> dict[str, Any]:
    path = root / "home-assistant.log"
    if not path.is_file():
        return {"available": False, "entries": []}
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - MAX_LOG_BYTES))
        raw = handle.read().decode("utf-8", "replace")
    return parse_log(raw, round(min(size, MAX_LOG_BYTES) / 1024), max_entries)


def _disk(root: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(root)
    db_bytes = sum(p.stat().st_size for p in root.glob("home-assistant_v2.db*") if p.is_file())
    gb = 1024**3
    return {
        "total_gb": round(usage.total / gb, 1),
        "used_gb": round(usage.used / gb, 1),
        "free_gb": round(usage.free / gb, 1),
        "recorder_db_mb": round(db_bytes / 1024**2, 1) if db_bytes else None,
    }


def _listing(root: Path) -> list[str]:
    names = []
    for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if p.name in BLOCKED_NAMES:
            names.append(f"{p.name} (present, never read)")
        else:
            names.append(p.name + ("/" if p.is_dir() else ""))
    return names


def _names(root: Path, pattern: str, limit: int = 100) -> list[str]:
    out = []
    for p in sorted(root.glob(pattern)):
        safe = _safe(root, p)
        if safe is not None and safe.is_file():
            out.append(str(safe.relative_to(root.resolve())))
    return out[:limit]


def scan_disk(
    config_dir: str, opts: dict[str, Any], red: Redactor, *, lovelace: bool = True
) -> dict[str, Any]:
    """Collect everything that needs the filesystem."""
    root = Path(config_dir)
    result: dict[str, Any] = {"errors": []}

    def run(name: str, func, *args) -> None:
        try:
            result[name] = func(*args)
        except Exception as err:  # noqa: BLE001 - keep going, report it
            result["errors"].append(f"{name}: {err!r}")

    run("disk", _disk, root)
    run("config_dir_listing", _listing, root)
    run("custom_components", _custom_components, root)
    run("hacs_repositories", _hacs, root)
    if lovelace:
        run("lovelace", _lovelace, root)
    run("blueprints", _names, root, "blueprints/*/*/*.y*ml")
    run("esphome_files", _names, root, "esphome/*.y*ml")
    if opts.get("include_configuration"):
        run("config_files", _config_files, root, red)
    if opts.get("include_logs"):
        run("log", _read_log, root, int(opts.get("log_lines", 40)))
    return result
