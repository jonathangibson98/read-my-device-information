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
from .exporter import KINDS, ExportManager
from .hass_client import HAError
from .settings import REDACTION_LEVELS, Runtime, clean_subfolder

_LOGGER = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "static"
INGRESS_PEERS = {"172.30.32.2", "127.0.0.1", "::1"}
CT = {"overview": "text/markdown", "full": "text/markdown", "json": "application/json"}


def _status(rt: Runtime, mgr: ExportManager) -> dict[str, Any]:
    eff = rt.effective()
    try:
        folder = str(rt.output_dir())
    except (ValueError, OSError):
        folder = None
    o = rt.options
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
    previous = dict(rt.selection)
    rt.save_selection({"location": loc, "subfolder": sub, "keep_history": bool(body.get("keep_history"))})
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


def build_app(rt: Runtime, mgr: ExportManager) -> web.Application:
    app = web.Application(middlewares=[guard], client_max_size=1024**2)
    app["rt"] = rt
    app["mgr"] = mgr
    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/generate", generate)
    app.router.add_get("/api/file/{kind}", file)
    app.router.add_post("/api/location", set_location)
    app.router.add_post("/api/location/reset", reset_location)
    app.router.add_static("/assets/", WEB_DIR / "assets", follow_symlinks=False)
    return app
