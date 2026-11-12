"""Small web page (Home Assistant ingress panel or standalone) and its JSON API."""

from __future__ import annotations

import base64
import hmac
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiohttp import web

from . import NAME, VERSION
from .exporter import ExportManager, NoFilesSelected
from . import mqtt_sensor
from .hass_client import HAError
from .settings import (
    KINDS,
    MAX_AUTO_HOURS,
    MAX_HISTORY_KEEP,
    REDACTION_LEVELS,
    Runtime,
    clean_subfolder,
    normalize_files,
)

_LOGGER = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "static"
INGRESS_PEERS = {"172.30.32.2", "127.0.0.1", "::1"}
CT = {"overview": "text/plain", "full": "text/plain", "json": "application/json"}


def _status(rt: Runtime, mgr: ExportManager) -> dict[str, Any]:
    eff = rt.effective()
    try:
        folder = str(rt.output_dir())
    except (ValueError, OSError):
        folder = None
    o = rt.options
    extra = rt.extra_settings()
    try:
        extra_folder = str(rt.extra_dir()) if extra["location"] else None
    except (ValueError, OSError):
        extra_folder = None
    hours = rt.auto_refresh_hours()
    due = mgr.next_due(hours)
    return {
        "name": NAME,
        "version": VERSION,
        "mode": rt.mode,
        "locations": [
            {"id": l.id, "label": l.label, "path": str(l.path), "writable": l.writable}
            for l in rt.locations.values()
        ],
        "selected": eff,
        "folder": folder,
        "has_saved_choice": bool(rt.selection),
        "options": {
            "redaction": o["redaction"],
            "include_attributes": o["include_attributes"],
            "include_configuration": o["include_configuration"],
            "include_logs": o["include_logs"],
            "auto_refresh_hours": o["auto_refresh_hours"],
            "history_keep": o["history_keep"],
        },
        "files_config": rt.file_settings(),
        "mqtt": {
            "enabled": rt.mqtt_enabled(),
            "default": o["mqtt_sensor"],
            "entity_id": mqtt_sensor.ENTITY_ID,
            "attribute": mqtt_sensor.ATTRIBUTE,
        },
        "extra": {**extra, "folder": extra_folder},
        "auto": {
            "hours": hours,
            "default_hours": o["auto_refresh_hours"],
            "overridden": "auto_refresh_hours" in rt.prefs,
            "next_run": due.isoformat(timespec="seconds") if due else None,
        },
        "config_folder_available": bool(rt.config_dir and rt.config_dir.is_dir()),
        "latest": mgr.latest,
    }


def _json_error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


@web.middleware
async def guard(request: web.Request, handler):
    rt: Runtime = request.app["rt"]
    if rt.mode == "addon":
        if request.remote not in INGRESS_PEERS:
            return _json_error("Only reachable through Home Assistant", 403)
    elif rt.web_password:
        header = request.headers.get("Authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                _user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                ok = hmac.compare_digest(pwd.encode(), rt.web_password.encode())
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return web.Response(
                status=401, text="Password required",
                headers={"WWW-Authenticate": f'Basic realm="{NAME}", charset="UTF-8"'},
            )
    if request.method == "POST" and request.headers.get("X-MDI") != "1":
        return _json_error("Missing request header", 403)
    return await handler(request)


async def index(request: web.Request) -> web.Response:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


async def status(request: web.Request) -> web.Response:
    return web.json_response(_status(request.app["rt"], request.app["mgr"]))


async def generate(request: web.Request) -> web.Response:
    rt: Runtime = request.app["rt"]
    mgr: ExportManager = request.app["mgr"]
    overrides: dict[str, Any] = {}
    try:
        body = await request.json() if request.can_read_body else {}
    except ValueError:
        body = {}
    level = (body or {}).get("redaction")
    if level:
        if level not in REDACTION_LEVELS:
            return _json_error("Unknown privacy level", 400)
        overrides["redaction"] = level
    try:
        await mgr.generate(overrides)
    except NoFilesSelected as err:
        return _json_error(str(err), 400)
    except HAError as err:
        return _json_error(str(err), 502)
    except (ValueError, PermissionError, OSError) as err:
        return _json_error(f"Could not save the files: {err}", 500)
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("Export failed")
        return _json_error(f"Export failed: {err!r}", 500)
    return web.json_response(_status(rt, mgr))


async def file(request: web.Request) -> web.Response:
    mgr: ExportManager = request.app["mgr"]
    kind = request.match_info["kind"]
    if kind not in KINDS:
        return _json_error("Unknown file", 404)
    found = mgr.read(kind)
    if found is None:
        return _json_error("Nothing saved yet. Press “Create snapshot” first.", 404)
    name, text = found
    headers = {"Cache-Control": "no-store"}
    if request.query.get("download"):
        headers["Content-Disposition"] = (
            f"attachment; filename=\"{name.replace(chr(34), '')}\"; filename*=UTF-8''{quote(name)}"
        )
    return web.Response(text=text, content_type=CT[kind], charset="utf-8", headers=headers)


async def set_location(request: web.Request) -> web.Response:
    rt: Runtime = request.app["rt"]
    mgr: ExportManager = request.app["mgr"]
    try:
        body = await request.json()
    except ValueError:
        return _json_error("Invalid request", 400)
    loc = str(body.get("location", ""))
    if loc not in rt.locations:
        return _json_error("Unknown location", 400)
    try:
        sub = clean_subfolder(str(body.get("subfolder", "")))
    except ValueError as err:
        return _json_error(str(err), 400)
    if not rt.locations[loc].writable:
        return _json_error(
            f"“{rt.locations[loc].label}” is not writable here. Check the folder mapping.", 400
        )
    try:
        keep = int(body.get("history_keep", rt.effective()["history_keep"]))
    except (TypeError, ValueError):
        keep = -1
    if not 0 <= keep <= MAX_HISTORY_KEEP:
        return _json_error(f"Enter how many dated copies to keep: a number from 0 to {MAX_HISTORY_KEEP}.", 400)
    previous = dict(rt.selection)
    rt.save_selection({
        "location": loc,
        "subfolder": sub,
        "keep_history": bool(body.get("keep_history")),
        "history_keep": keep,
    })
    try:
        rt.output_dir()
    except (ValueError, OSError):
        rt.save_selection(previous)
        return _json_error("That folder is not allowed.", 400)
    return web.json_response(_status(rt, mgr))


async def reset_location(request: web.Request) -> web.Response:
    rt: Runtime = request.app["rt"]
    rt.clear_selection()
    return web.json_response(_status(rt, request.app["mgr"]))


def _validate_prefs(rt: Runtime, prefs: dict[str, Any]) -> None:
    """Cross-checks that need the whole picture. Raises ValueError with a plain message."""
    extra = rt.extra_settings(prefs)
    if extra["location"] and extra["location"] not in rt.locations:
        raise ValueError("Unknown location for the second copy")
    clean_subfolder(extra["subfolder"])
    files = rt.file_settings(prefs)
    if not any(f["enabled"] for f in files.values()):
        raise ValueError("Pick at least one file to create.")
    if rt.mqtt_enabled(prefs) and not files["full"]["enabled"]:
        raise ValueError("The MQTT sensor publishes the Full file. Switch Full on, or turn the sensor off.")
    if any(f["enabled"] and f["copy"] for f in files.values()):
        loc = rt.locations.get(extra["location"])
        if loc is None:
            raise ValueError("Pick a place for the second copy, or untick \u201cAlso save a copy\u201d.")
        if not loc.writable:
            raise ValueError(f"\u201c{loc.label}\u201d is not writable here. Check the folder mapping.")
        try:
            same = rt.extra_dir(prefs) == rt.output_dir()
        except OSError:
            raise ValueError("That folder is not allowed.") from None
        if same:
            raise ValueError("The second copy would go to the same folder as the main files. Pick a different place.")


async def save_settings(request: web.Request) -> web.Response:
    """Which files, masking per file, the second place, and the automatic schedule."""
    rt: Runtime = request.app["rt"]
    mgr: ExportManager = request.app["mgr"]
    try:
        body = await request.json()
    except ValueError:
        return _json_error("Invalid request", 400)
    if not isinstance(body, dict):
        return _json_error("Invalid request", 400)
    prefs = dict(rt.prefs)
    try:
        if "files" in body:
            prefs["files"] = normalize_files(body["files"], rt.file_settings())
        if "extra" in body:
            extra = body["extra"]
            if not isinstance(extra, dict):
                raise ValueError("Invalid request")
            loc = str(extra.get("location") or "")
            if loc and loc not in rt.locations:
                raise ValueError("Unknown location for the second copy")
            prefs["extra"] = {"location": loc, "subfolder": clean_subfolder(str(extra.get("subfolder") or ""))}
        if "mqtt" in body:
            mq = body["mqtt"]
            if not isinstance(mq, dict) or "enabled" not in mq:
                raise ValueError("Invalid request")
            prefs["mqtt"] = {"enabled": bool(mq["enabled"])}
        if "auto_refresh_hours" in body:
            hours = body["auto_refresh_hours"]
            if hours is None:
                prefs.pop("auto_refresh_hours", None)  # back to the add-on / .env default
            else:
                try:
                    hours = int(hours)
                except (TypeError, ValueError):
                    hours = -1
                if isinstance(body["auto_refresh_hours"], bool) or not 0 <= hours <= MAX_AUTO_HOURS:
                    raise ValueError(f"Automatic updates: choose 0 (off) to {MAX_AUTO_HOURS} hours.")
                prefs["auto_refresh_hours"] = hours
        _validate_prefs(rt, prefs)
    except ValueError as err:
        return _json_error(str(err), 400)
    try:
        rt.save_prefs(prefs)
    except OSError as err:
        return _json_error(f"Could not store the settings: {err}", 500)
    return web.json_response(_status(rt, mgr))


def build_app(rt: Runtime, mgr: ExportManager) -> web.Application:
    app = web.Application(middlewares=[guard], client_max_size=1024**2)
    app["rt"] = rt
    app["mgr"] = mgr
    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/generate", generate)
    app.router.add_get("/api/file/{kind}", file)
    app.router.add_post("/api/settings", save_settings)
    app.router.add_post("/api/location", set_location)
    app.router.add_post("/api/location/reset", reset_location)
    app.router.add_static("/assets/", WEB_DIR / "assets", follow_symlinks=False)
    return app
