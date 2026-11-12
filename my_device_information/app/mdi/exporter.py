"""Generate the files, save them where the user chose, and remember the last result."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp

from . import NAME, VERSION
from .collector import collect
from .hass_client import HAClient, HAError
from .redact import Redactor
from .render import estimate_tokens, render
from .settings import Runtime

_LOGGER = logging.getLogger(__name__)

KINDS = ("overview", "full", "json")
FILE_LABELS = {"overview": "Overview.md", "full": "Full.md", "json": "Data.json"}
CONTENT_TYPES = {"overview": "text/markdown", "full": "text/markdown", "json": "application/json"}


def filename(kind: str, stamp: str | None = None) -> str:
    base = f"{NAME} - {FILE_LABELS[kind]}"
    if stamp is None:
        return base
    stem, _, ext = base.rpartition(".")
    return f"{stem} {stamp}.{ext}"


class ExportManager:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self._lock = asyncio.Lock()
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
    async def generate(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            opts = {**self.rt.options, **(overrides or {})}
            out_dir = self.rt.output_dir()  # validated before doing any work
            red = Redactor(opts["redaction"])
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
                data = await collect(client, opts, self.rt.config_dir, red)
            eff = self.rt.effective()
            files = await asyncio.to_thread(self._write, data, out_dir, eff["keep_history"])
            self.latest = {
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "folder": str(out_dir),
                "location": eff["location"],
                "subfolder": eff["subfolder"],
                "files": files,
                "counts": data.get("counts", {}),
                "redaction": data.get("meta", {}).get("redaction"),
                "errors": data.get("meta", {}).get("collection_errors", []),
            }
            self._save_latest()
            return self.latest

    def _write(self, data: dict[str, Any], out_dir: Path, keep_history: bool) -> dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(out_dir, 0o700)
        except OSError:
            pass
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        history_dir = out_dir / "history"
        if keep_history:
            history_dir.mkdir(exist_ok=True)
        result: dict[str, Any] = {}
        for kind in KINDS:
            text = render(data, kind)
            path = out_dir / filename(kind)
            self._atomic_write(path, text)
            if keep_history:
                self._atomic_write(history_dir / filename(kind, stamp), text)
            result[kind] = {
                "name": path.name,
                "path": str(path),
                "bytes": len(text.encode("utf-8")),
                "tokens": estimate_tokens(text),
            }
        return result

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

    async def scheduled(self) -> None:
        try:
            await self.generate()
        except (HAError, OSError, ValueError) as err:
            _LOGGER.error("Scheduled export failed: %s", err)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Scheduled export failed")
