"""Collect a snapshot of a Home Assistant instance through its APIs.

Produces the same data layout the renderer expects. Every step is guarded, so one
failing call never stops the export; it is listed under "Export notes" instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from . import NAME, VERSION
from .diskscan import parse_log, scan_disk, summarize_views
from .hass_client import HAClient, HAError, WSConn
from .redact import STRICT, Redactor

_LOGGER = logging.getLogger(__name__)

HELPER_DOMAINS = (
    "input_boolean", "input_number", "input_select", "input_text", "input_datetime",
    "input_button", "counter", "timer", "schedule",
)
SKIP_ATTRS = {
    "friendly_name", "unit_of_measurement", "device_class", "entity_picture",
    "entity_picture_local", "icon", "supported_features", "editable", "restored",
    "attribution",
}
PERSON_DOMAINS = ("person", "device_tracker")
COMMON_PRESENCE = {"home", "not_home", "unavailable", "unknown"}


def _trim(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 300 else value[:300] + "…"
    if isinstance(value, dict):
        if depth > 3:
            return "…"
        return {str(k): _trim(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out = [_trim(v, depth + 1) for v in items[:25]]
        if len(items) > 25:
            out.append(f"…(+{len(items) - 25} more)")
        return out
    return value


def _system(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "ha_core_version": cfg.get("version"),
        "location_name": cfg.get("location_name"),
        "time_zone": cfg.get("time_zone"),
        "language": cfg.get("language"),
        "country": cfg.get("country"),
        "currency": cfg.get("currency"),
        "latitude": cfg.get("latitude"),
        "longitude": cfg.get("longitude"),
        "elevation": cfg.get("elevation"),
        "radius_m": cfg.get("radius"),
        "unit_system": cfg.get("unit_system") or {},
        "internal_url": cfg.get("internal_url"),
        "external_url": cfg.get("external_url"),
        "config_dir": cfg.get("config_dir"),
        "safe_mode": cfg.get("safe_mode", False),
        "recovery_mode": cfg.get("recovery_mode", False),
    }


async def _supervisor(client: HAClient, errors: list[str]) -> dict[str, Any] | None:
    if not client.has_supervisor:
        return None
    out: dict[str, Any] = {}
    for key, path in (
        ("info", "/info"), ("host", "/host/info"), ("os", "/os/info"),
        ("core", "/core/info"), ("supervisor", "/supervisor/info"),
    ):
        try:
            out[key] = await client.supervisor(path) or {}
        except HAError as err:
            out[key] = {}
            errors.append(f"supervisor {key}: {err}")
    addons_raw: list[dict[str, Any]] = []
    try:
        data = await client.supervisor("/addons") or {}
        addons_raw = data.get("addons", []) if isinstance(data, dict) else []
    except HAError as err:
        errors.append(f"supervisor addons: {err}")
    addons = [
        {
            "name": a.get("name"),
            "slug": a.get("slug"),
            "version": a.get("version"),
            "state": a.get("state"),
            "update_available": a.get("update_available"),
            "latest_version": a.get("version_latest"),
        }
        for a in addons_raw
    ]
    info, host, os_info = out["info"], out["host"], out["os"]
    sup, core = out["supervisor"], out["core"]
    return {
        "supervisor_version": sup.get("version"),
        "supervisor_channel": sup.get("channel"),
        "supervisor_healthy": sup.get("healthy"),
        "supervisor_supported": sup.get("supported"),
        "core_version": core.get("version"),
        "operating_system": host.get("operating_system"),
        "os_version": os_info.get("version"),
        "board": os_info.get("board"),
        "kernel": host.get("kernel"),
        "hostname": host.get("hostname"),
        "chassis": host.get("chassis"),
        "docker_version": info.get("docker"),
        "arch": info.get("arch"),
        "host_disk_total_gb": host.get("disk_total"),
        "host_disk_used_gb": host.get("disk_used"),
        "host_disk_free_gb": host.get("disk_free"),
        "addons": sorted(addons, key=lambda a: str(a.get("name")).lower()),
    }


def _build_inventory(
    raw: dict[str, Any], opts: dict[str, Any], red: Redactor
) -> dict[str, Any]:
    exclude = {d.strip().lower() for d in str(opts.get("exclude_domains", "")).split(",") if d.strip()}
    include_attrs = bool(opts.get("include_attributes"))
    strict = red.level == STRICT

    floors = {f.get("floor_id"): f.get("name") for f in raw.get("floors", [])}
    labels = {x.get("label_id"): x.get("name") for x in raw.get("labels", [])}
    area_by_id = {a.get("area_id"): a for a in raw.get("areas", [])}

    def area_name(area_id: str | None) -> str | None:
        area = area_by_id.get(area_id) if area_id else None
        return area.get("name") if area else None

    entries = raw.get("config_entries", [])
    domain_by_entry = {e.get("entry_id"): e.get("domain") for e in entries}

    dev_list = sorted(
        raw.get("devices", []),
        key=lambda d: str(d.get("name_by_user") or d.get("name") or "").lower(),
    )
    dev_ref = {d["id"]: f"dev{i}" for i, d in enumerate(dev_list, 1)}
    dev_by_id = {d["id"]: d for d in dev_list}
    dev_count_by_entry: Counter = Counter()
    area_dev: Counter = Counter()
    devices = []
    for d in dev_list:
        for entry_id in d.get("config_entries") or []:
            dev_count_by_entry[entry_id] += 1
        if d.get("area_id"):
            area_dev[d["area_id"]] += 1
        devices.append(
            {
                "ref": dev_ref[d["id"]],
                "name": d.get("name_by_user") or d.get("name"),
                "manufacturer": d.get("manufacturer"),
                "model": d.get("model"),
                "sw_version": d.get("sw_version"),
                "hw_version": d.get("hw_version"),
                "area": area_name(d.get("area_id")),
                "via": dev_ref.get(d.get("via_device_id")),
                "integrations": sorted(
                    {domain_by_entry[e] for e in d.get("config_entries") or [] if e in domain_by_entry}
                ),
                "entry_type": d.get("entry_type"),
                "disabled": bool(d.get("disabled_by")),
            }
        )

    states = {s["entity_id"]: s for s in raw.get("states", [])}
    entities: list[dict[str, Any]] = []
    ent_count_by_entry: Counter = Counter()
    domain_counts: Counter = Counter()
    platform_counts: Counter = Counter()
    area_ent: Counter = Counter()
    unavailable: list[dict[str, Any]] = []
    low_battery: list[dict[str, Any]] = []
    excluded = 0

    def make(entity_id: str, entry: dict[str, Any] | None, state: dict[str, Any] | None) -> None:
        nonlocal excluded
        domain = entity_id.split(".", 1)[0]
        if domain in exclude:
            excluded += 1
            return
        dev = dev_by_id.get(entry.get("device_id")) if entry and entry.get("device_id") else None
        area_id = (entry.get("area_id") if entry else None) or (dev.get("area_id") if dev else None)
        attrs = (state or {}).get("attributes") or {}
        name = (
            attrs.get("friendly_name")
            or (entry and (entry.get("name") or entry.get("original_name")))
            or entity_id
        )
        value = state.get("state") if state else None
        if strict and domain in PERSON_DOMAINS:
            name = red.alias("person", str(name))
            if value not in COMMON_PRESENCE and value is not None:
                value = "<zone>"
        device_class = attrs.get("device_class") or (
            (entry.get("device_class") or entry.get("original_device_class")) if entry else None
        )
        rec: dict[str, Any] = {
            "entity_id": entity_id,
            "name": name,
            "platform": entry.get("platform") if entry else None,
            "device": dev_ref.get(entry.get("device_id")) if entry else None,
            "area": area_name(area_id),
            "state": value,
            "unit": attrs.get("unit_of_measurement"),
            "device_class": device_class,
            "category": (entry.get("entity_category") or None) if entry else None,
            "disabled": bool(entry.get("disabled_by")) if entry else False,
            "hidden": bool(entry.get("hidden_by")) if entry else False,
            "last_changed": state.get("last_changed") if state else None,
        }
        if include_attrs and state:
            extra = {k: _trim(v) for k, v in attrs.items() if k not in SKIP_ATTRS}
            if strict and domain in PERSON_DOMAINS:
                extra = {}
            if extra:
                rec["attributes"] = extra
        entities.append(rec)
        domain_counts[domain] += 1
        if entry:
            platform_counts[entry.get("platform")] += 1
            if entry.get("config_entry_id"):
                ent_count_by_entry[entry["config_entry_id"]] += 1
        if area_id:
            area_ent[area_id] += 1
        if state and state.get("state") == "unavailable":
            unavailable.append(
                {"entity_id": entity_id, "name": name,
                 "platform": entry.get("platform") if entry else None}
            )
        if state and device_class == "battery":
            try:
                level = float(state.get("state"))
            except (TypeError, ValueError):
                level = None
            if level is not None and level <= 20:
                low_battery.append({"entity_id": entity_id, "name": name, "level": level})

    for entry in raw.get("entities", []):
        make(entry["entity_id"], entry, states.pop(entry["entity_id"], None))
    for entity_id, state in states.items():
        make(entity_id, None, state)
    entities.sort(key=lambda e: ((e["area"] or "~").lower(), e["entity_id"]))

    areas = sorted(
        (
            {
                "id": a.get("area_id"),
                "name": a.get("name"),
                "floor": floors.get(a.get("floor_id")),
                "labels": [labels.get(x, x) for x in a.get("labels") or []],
                "devices": area_dev[a.get("area_id")],
                "entities": area_ent[a.get("area_id")],
            }
            for a in area_by_id.values()
        ),
        key=lambda a: str(a["name"]).lower(),
    )

    integrations = [
        {
            "domain": e.get("domain"),
            "title": e.get("title"),
            "state": e.get("state"),
            "source": e.get("source"),
            "disabled": bool(e.get("disabled_by")),
            "reason": e.get("reason"),
            "devices": dev_count_by_entry[e.get("entry_id")],
            "entities": ent_count_by_entry[e.get("entry_id")],
        }
        for e in sorted(entries, key=lambda x: (str(x.get("domain")), str(x.get("title") or "")))
    ]
    entry_domains = {e.get("domain") for e in entries}
    other_components = sorted(
        c for c in raw.get("components", []) if "." not in c and c not in entry_domains
    )
    return {
        "areas": areas,
        "devices": devices,
        "entities": entities,
        "integrations": integrations,
        "other_loaded_components": other_components,
        "domain_counts": dict(domain_counts.most_common()),
        "platform_counts": dict(platform_counts.most_common()),
        "unavailable": sorted(unavailable, key=lambda u: u["entity_id"]),
        "low_battery": sorted(low_battery, key=lambda b: b["level"]),
        "excluded_entities": excluded,
    }


def _from_states(states: list[dict[str, Any]], domain: str) -> list[dict[str, Any]]:
    return [s for s in states if s["entity_id"].startswith(domain + ".")]


def _automation_like(states: list[dict[str, Any]], domain: str) -> list[dict[str, Any]]:
    out = []
    for st in _from_states(states, domain):
        a = st.get("attributes") or {}
        rec = {
            "entity_id": st["entity_id"],
            "name": a.get("friendly_name") or st["entity_id"],
            "state": st.get("state"),
        }
        if domain in ("automation", "script"):
            rec["last_triggered"] = a.get("last_triggered")
            rec["mode"] = a.get("mode")
            rec["running"] = a.get("current")
        out.append(rec)
    return sorted(out, key=lambda r: str(r["name"]).lower())


def _helpers(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for domain in HELPER_DOMAINS:
        for st in _from_states(states, domain):
            out.append(
                {
                    "entity_id": st["entity_id"],
                    "name": (st.get("attributes") or {}).get("friendly_name") or st["entity_id"],
                    "state": st.get("state"),
                }
            )
    return sorted(out, key=lambda r: r["entity_id"])


def _updates(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for st in _from_states(states, "update"):
        if st.get("state") != "on":
            continue
        a = st.get("attributes") or {}
        out.append(
            {
                "entity_id": st["entity_id"],
                "title": a.get("title") or a.get("friendly_name") or st["entity_id"],
                "installed": a.get("installed_version"),
                "latest": a.get("latest_version"),
            }
        )
    return sorted(out, key=lambda r: r["entity_id"])


def _issues(result: Any) -> list[dict[str, Any]]:
    items = result.get("issues", []) if isinstance(result, dict) else []
    out = [
        {
            "domain": i.get("domain"),
            "issue_id": i.get("issue_id"),
            "severity": i.get("severity"),
            "fixable": i.get("is_fixable"),
            "breaks_in_ha_version": i.get("breaks_in_ha_version"),
            "dismissed": bool(i.get("dismissed_version") or i.get("ignored")),
        }
        for i in items
    ]
    return sorted(out, key=lambda r: (str(r["domain"]), str(r["issue_id"])))


async def _lovelace_ws(ws: WSConn) -> dict[str, Any]:
    dashboards: list[dict[str, Any]] = []
    listed = await ws.command("lovelace/dashboards/list") or []
    targets: list[dict[str, Any]] = [
        {"title": "Default dashboard", "url_path": None, "mode": "storage"}
    ] + [
        {"title": d.get("title"), "url_path": d.get("url_path"), "mode": d.get("mode", "storage")}
        for d in listed
    ]
    for meta in targets:
        record = dict(meta)
        try:
            payload = {"url_path": meta["url_path"]} if meta["url_path"] else {}
            config = await ws.command("lovelace/config", timeout=30, **payload)
            record["views"] = summarize_views(config or {})
        except HAError as err:
            record["views"] = None
            record["note"] = (
                "auto-generated dashboard (no stored config)"
                if "config_not_found" in str(err)
                else f"could not read config ({err})"
            )
        dashboards.append(record)
    resources: list[dict[str, Any]] = []
    try:
        resources = [
            {"url": r.get("url"), "type": r.get("type")}
            for r in await ws.command("lovelace/resources") or []
        ]
    except HAError:
        pass
    return {"dashboards": dashboards, "resources": resources}


async def collect(
    client: HAClient,
    opts: dict[str, Any],
    config_dir: Path | None,
    red: Redactor,
) -> dict[str, Any]:
    """Collect everything and return a redacted dict."""
    errors: list[str] = []
    data: dict[str, Any] = {}
    raw: dict[str, Any] = {}
    states: list[dict[str, Any]] = []
    lovelace_done = False

    async with client.websocket() as ws:

        async def step(label: str, command: str, **kw: Any) -> Any:
            try:
                return await ws.command(command, **kw)
            except (HAError, asyncio.TimeoutError) as err:
                errors.append(f"{label}: {err or 'timed out'}")
                return None

        cfg = await step("system", "get_config") or {}
        data["system"] = _system(cfg)
        raw["components"] = cfg.get("components", [])
        states = await step("states", "get_states") or []
        raw["states"] = states
        raw["entities"] = await step("entity registry", "config/entity_registry/list") or []
        raw["devices"] = await step("device registry", "config/device_registry/list") or []
        raw["areas"] = await step("area registry", "config/area_registry/list") or []
        raw["floors"] = await step("floor registry", "config/floor_registry/list") or []
        raw["labels"] = await step("label registry", "config/label_registry/list") or []
        raw["config_entries"] = await step("integrations", "config_entries/get") or []
        data["repairs"] = _issues(await step("repairs", "repairs/list_issues"))
        if opts.get("include_services"):
            services = await step("services", "get_services") or {}
            data["services"] = {d: sorted(s) for d, s in sorted(services.items())}
        try:
            data["lovelace"] = await _lovelace_ws(ws)
            lovelace_done = True
        except (HAError, asyncio.TimeoutError) as err:
            errors.append(f"dashboards over API: {err or 'timed out'}")

    data["supervisor"] = await _supervisor(client, errors)
    data["recorder"] = None
    data["system_health"] = {}

    try:
        data.update(_build_inventory(raw, opts, red))
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("inventory failed", exc_info=True)
        errors.append(f"inventory: {err!r}")

    data["automations"] = _automation_like(states, "automation")
    data["scripts"] = _automation_like(states, "script")
    data["scenes"] = _automation_like(states, "scene")
    data["helpers"] = _helpers(states)
    data["updates_available"] = _updates(states)

    has_config = bool(
        config_dir
        and config_dir.is_dir()
        and ((config_dir / "configuration.yaml").exists() or (config_dir / ".storage").is_dir())
    )
    if has_config:
        disk = await asyncio.to_thread(
            scan_disk, str(config_dir), opts, red, lovelace=not lovelace_done
        )
        errors.extend(disk.pop("errors", []))
        data.update(disk)
    else:
        errors.append(
            "configuration folder not available: YAML files, custom components, HACS list "
            "and disk usage are skipped (mount your Home Assistant config folder to include them)"
        )

    if opts.get("include_logs"):
        log = data.get("log")
        if not log or not log.get("available"):
            data["log"] = await _log_from_api(client, int(opts.get("log_lines", 40)), errors)

    data["counts"] = {
        "entities": len(data.get("entities", [])),
        "devices": len(data.get("devices", [])),
        "areas": len(data.get("areas", [])),
        "integrations": len(data.get("integrations", [])),
        "automations": len(data.get("automations", [])),
        "scripts": len(data.get("scripts", [])),
        "unavailable": len(data.get("unavailable", [])),
    }
    data["meta"] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": f"{NAME} {VERSION}",
        "redaction": red.level,
        "include_attributes": bool(opts.get("include_attributes")),
        "include_configuration": bool(opts.get("include_configuration")),
        "excluded_domains": opts.get("exclude_domains") or None,
        "collection_errors": errors,
    }
    return red.deep(data)


async def _log_from_api(client: HAClient, max_entries: int, errors: list[str]) -> dict[str, Any]:
    """Fallback when home-assistant.log is not on disk: ask Home Assistant for it."""
    attempts = [("Home Assistant API", lambda: client.get_text("/api/error_log"))]
    if client.has_supervisor:
        attempts.append(("Supervisor", lambda: client.supervisor_text("/core/logs")))
    for label, fetch in attempts:
        try:
            raw = await fetch()
        except HAError as err:
            errors.append(f"log via {label}: {err}")
            continue
        tail = raw[-1_500_000:]
        return parse_log(tail, round(len(tail) / 1024), max_entries)
    return {"available": False, "entries": []}
