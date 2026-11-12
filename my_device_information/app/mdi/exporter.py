"""Generate the files, save them where the user chose, and remember the last result."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp

from . import NAME, VERSION
from .collector import collect
from .hass_client import HAClient, HAError
from .redact import Redactor
from .render import estimate_tokens, render
from .settings import KINDS, REDACTION_LEVELS, Runtime

_LOGGER = logging.getLogger(__name__)

RETRY_AFTER_FAILURE = timedelta(minutes=15)
FILE_LABELS = {"overview": "Overview.md", "full": "Full.md", "json": "Data.json"}
CONTENT_TYPES = {"overview": "text/markdown", "full": "text/markdown", "json": "application/json"}


def filename(kind: str, stamp: str | None = None) -> str:
    base = f"{NAME} - {FILE_LABELS[kind]}"
    if stamp is None:
        return base
    stem, _, ext = base.rpartition(".")
    return f"{stem} {stamp}.{ext}"


class NoFilesSelected(ValueError):
    """Every file is switched off, so there is nothing to create."""


def _history_pattern(kind: str) -> re.Pattern[str]:
    stem, _, ext = filename(kind).rpartition(".")
    return re.compile(rf"^{re.escape(stem)} \d{{4}}-\d{{2}}-\d{{2}}_\d{{6}}\.{re.escape(ext)}$")


def prune_history(history_dir: Path, kind: str, keep: int) -> int:
    """Delete the oldest dated copies of one file type, keeping the newest `keep`.

    Only names this add-on wrote (the exact dated pattern) are ever touched.
    Returns how many files were removed. keep <= 0 means keep everything.
    """
    if keep <= 0 or not history_dir.is_dir():
        return 0
    pattern = _history_pattern(kind)
    names = sorted(p.name for p in history_dir.iterdir() if p.is_file() and pattern.match(p.name))
    removed = 0
    for name in names[: max(0, len(names) - keep)]:
        try:
            (history_dir / name).unlink()
            removed += 1
        except OSError:
            _LOGGER.debug("could not remove old history file %s", name, exc_info=True)
    return removed


class ExportManager:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self._lock = asyncio.Lock()
        self._last_failure: datetime | None = None  # set when a run raises, cleared by a success
        self.latest: dict[str, Any] | None = None
        self._meta_file = rt.data_dir / "latest.json"
        self._load_latest()

    # -- persistence of the last result (metadata only, never the content) ------
    def _load_latest(self) -> None:
        try:
            self.latest = json.loads(self._meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.latest = None

    def _save_latest(self) -> None:
        try:
            self.rt.data_dir.mkdir(parents=True, exist_ok=True)
            self._meta_file.write_text(json.dumps(self.latest), encoding="utf-8")
        except OSError:
            _LOGGER.debug("could not store latest.json", exc_info=True)

    # -- generation -----------------------------------------------------------------
    def _plan(self, opts: dict[str, Any], forced: str | None) -> list[dict[str, Any]]:
        """Which files to create, each with its masking level and second-copy flag."""
        files = self.rt.file_settings()
        plan: list[dict[str, Any]] = []
        for kind in KINDS:
            cfg = files[kind]
            if not cfg["enabled"]:
                continue
            level = forced or (opts["redaction"] if cfg["redaction"] == "default" else cfg["redaction"])
            if level not in REDACTION_LEVELS:
                level = "standard"
            plan.append({"kind": kind, "level": level, "copy": cfg["copy"]})
        if not plan:
            raise NoFilesSelected("Pick at least one file to create.")
        return plan

    def _extra_target(self, out_dir: Path, plan: list[dict[str, Any]]) -> tuple[Path | None, list[str]]:
        """Folder for second copies, or None plus a note saying why they are skipped."""
        if not any(p["copy"] for p in plan):
            return None, []
        try:
            extra = self.rt.extra_dir()
        except (ValueError, OSError):
            return None, ["Second copy skipped: that folder is not allowed."]
        if extra is None:
            return None, ["Second copy skipped: no second place is chosen."]
        if extra == out_dir:
            return None, ["Second copy skipped: it is the same folder as the main files."]
        loc = self.rt.locations[self.rt.extra_settings()["location"]]
        if not loc.writable:
            return None, [f"Second copy skipped: \u201c{loc.label}\u201d is not writable."]
        return extra, []

    async def _collect(self, level: str, opts: dict[str, Any]) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=300, connect=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            client = HAClient(
                session,
                rest_base=self.rt.rest_base,
                ws_url=self.rt.ws_url,
                token=self.rt.token,
                supervisor_base=self.rt.supervisor_base,
                supervisor_token=self.rt.supervisor_token,
            )
            return await collect(client, {**opts, "redaction": level}, self.rt.config_dir, Redactor(level))

    async def generate(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            try:
                result = await self._generate(overrides or {})
            except BaseException:
                self._last_failure = datetime.now().astimezone()
                raise
            self._last_failure = None
            return result

    async def _generate(self, overrides: dict[str, Any]) -> dict[str, Any]:
        opts = {**self.rt.options, **overrides}
        out_dir = self.rt.output_dir()  # validated before doing any work
        plan = self._plan(opts, overrides.get("redaction"))
        extra_dir, notes = self._extra_target(out_dir, plan)
        # Masking happens while collecting, so each distinct level needs its own pass.
        datasets: dict[str, dict[str, Any]] = {}
        for level in dict.fromkeys(p["level"] for p in plan):
            datasets[level] = await self._collect(level, opts)
        eff = self.rt.effective()
        files = await asyncio.to_thread(
            self._write, plan, datasets, out_dir, extra_dir, eff["keep_history"], eff["history_keep"]
        )
        first = datasets[plan[0]["level"]]
        errors: list[str] = []
        for data in datasets.values():
            errors.extend((data.get("meta") or {}).get("collection_errors", []))
        levels = {p["level"] for p in plan}
        self.latest = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "folder": str(out_dir),
            "location": eff["location"],
            "subfolder": eff["subfolder"],
            "copy_folder": str(extra_dir) if extra_dir and any(f.get("copy_path") for f in files.values()) else None,
            "files": files,
            "counts": first.get("counts", {}),
            "redaction": levels.pop() if len(levels) == 1 else "mixed",
            "errors": list(dict.fromkeys(errors + notes)),
        }
        self._save_latest()
        return self.latest

    def _write(
        self,
        plan: list[dict[str, Any]],
        datasets: dict[str, dict[str, Any]],
        out_dir: Path,
        extra_dir: Path | None,
        keep_history: bool,
        history_keep: int,
    ) -> dict[str, Any]:
        self._make_private_dir(out_dir)
        if extra_dir is not None:
            self._make_private_dir(extra_dir)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        history_dir = out_dir / "history"
        if keep_history:
            history_dir.mkdir(exist_ok=True)
        result: dict[str, Any] = {}
        for item in plan:
            kind = item["kind"]
            text = render(datasets[item["level"]], kind)
            path = out_dir / filename(kind)
            self._atomic_write(path, text)
            if keep_history:
                self._atomic_write(history_dir / filename(kind, stamp), text)
                prune_history(history_dir, kind, history_keep)
            info: dict[str, Any] = {
                "name": path.name,
                "path": str(path),
                "bytes": len(text.encode("utf-8")),
                "tokens": estimate_tokens(text),
                "redaction": item["level"],
            }
            if item["copy"] and extra_dir is not None:
                copy_path = extra_dir / filename(kind)
                self._atomic_write(copy_path, text)
                info["copy_path"] = str(copy_path)
            result[kind] = info
        return result

    @staticmethod
    def _make_private_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)

    def read(self, kind: str) -> tuple[str, str] | None:
        """Return (filename, text) of one saved edition, or None."""
        if kind not in KINDS or not self.latest:
            return None
        info = (self.latest.get("files") or {}).get(kind)
        if not info:
            return None
        path = Path(info["path"])
        try:
            return info["name"], path.read_text(encoding="utf-8")
        except OSError:
            return None

    # -- schedule ---------------------------------------------------------------------
    def _latest_time(self) -> datetime | None:
        try:
            return datetime.fromisoformat((self.latest or {})["generated_at"])
        except (KeyError, TypeError, ValueError):
            return None

    def next_due(self, hours: int) -> datetime | None:
        """When the next automatic run is due, None when automatic updates are off."""
        if hours <= 0:
            return None
        interval = timedelta(hours=hours)
        done = self._latest_time()
        failed = self._last_failure
        if failed is not None and (done is None or failed > done):
            # The last try failed: retry soon, but do not hammer Home Assistant.
            return failed + min(interval, RETRY_AFTER_FAILURE)
        if done is None:
            return datetime.now().astimezone()
        return done + interval

    async def scheduled(self) -> None:
        if self._lock.locked():
            return  # a run is already in progress
        try:
            await self.generate()
        except NoFilesSelected as err:
            _LOGGER.warning("Scheduled export skipped: %s", err)
        except (HAError, OSError, ValueError) as err:
            _LOGGER.error("Scheduled export failed: %s", err)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Scheduled export failed")
