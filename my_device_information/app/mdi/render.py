"""Turn the collected data into AI-friendly Markdown. No Home Assistant imports."""

from __future__ import annotations

import json
from typing import Any

MAX_AUTOMATIONS_OVERVIEW = 400
MAX_UNAVAILABLE_OVERVIEW = 120
MAX_ATTR_CHARS = 600


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return ["_none_", ""]
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out.extend("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows)
    out.append("")
    return out


def _flatten(data: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for key, value in data.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, dict):
            rows.extend(_flatten(value, f"{prefix}{key}."))
        elif isinstance(value, (list, tuple)):
            rows.append((f"{prefix}{key}", ", ".join(str(v) for v in value)))
        else:
            rows.append((f"{prefix}{key}", value))
    return rows


def _kv(data: dict[str, Any] | None) -> list[str]:
    rows = _flatten(data or {})
    return _table(["Setting", "Value"], [[k, v] for k, v in rows])


def _entity_line(e: dict[str, Any], with_attrs: bool) -> list[str]:
    name = e.get("name")
    parts = [f"- `{e['entity_id']}`"]
    if name and name != e["entity_id"]:
        parts.append(f'"{name}"')
    state = e.get("state")
    if state is None:
        shown = "(disabled, no state)" if e.get("disabled") else "(no state)"
    else:
        shown = f"{state} {e['unit']}" if e.get("unit") else str(state)
    parts.append(f"= {shown}")
    tags = []
    for key in ("platform", "device_class", "category", "device"):
        if e.get(key):
            tags.append(f"{key}={e[key]}")
    if e.get("hidden"):
        tags.append("hidden")
    if e.get("disabled") and state is not None:
        tags.append("disabled")
    if tags:
        parts.append("[" + ", ".join(tags) + "]")
    lines = [" ".join(parts)]
    if with_attrs and e.get("attributes"):
        blob = json.dumps(e["attributes"], default=str, ensure_ascii=False, separators=(",", ":"))
        if len(blob) > MAX_ATTR_CHARS:
            blob = blob[:MAX_ATTR_CHARS] + "…"
        lines.append(f"    attrs: {blob}")
    return lines


def _header(data: dict[str, Any], full: bool) -> list[str]:
    meta = data.get("meta", {})
    counts = data.get("counts", {})
    sys_info = data.get("system", {})
    lines = [
        f"# My Device Information: Home Assistant snapshot ({'full' if full else 'overview'})",
        "",
        f"- Generated: {meta.get('generated_at')} by {meta.get('generator')}",
        f"- Home Assistant Core: {sys_info.get('ha_core_version')}",
        f"- Redaction level: **{meta.get('redaction')}** (values shown as `<redacted>`, `<ip-1>` etc. were masked on purpose)",
        f"- Size: {counts.get('entities', 0)} entities, {counts.get('devices', 0)} devices, "
        f"{counts.get('areas', 0)} areas, {counts.get('integrations', 0)} integrations, "
        f"{counts.get('automations', 0)} automations, {counts.get('scripts', 0)} scripts",
        "",
        "> **Note to the AI reading this:** this is a point-in-time snapshot of one Home Assistant "
        "installation, exported so you can answer questions about it, debug it, and suggest "
        "automations or fixes. Entity states reflect the moment of export. Do not ask for "
        "masked values; work around them or tell me what you need to know.",
        "",
    ]
    if not full:
        lines += [
            "This is the **overview** edition: it lists integrations, add-ons, areas, problems, "
            "automations and dashboards, but not every entity. Ask for the full edition when you "
            "need entity-level detail.",
            "",
        ]
    return lines


def _system_sections(data: dict[str, Any]) -> list[str]:
    L = ["## System", ""]
    L += _kv(data.get("system"))
    sup = data.get("supervisor")
    if sup:
        addons = sup.get("addons") or []
        L += ["## Host and Supervisor", ""]
        L += _kv({k: v for k, v in sup.items() if k != "addons"})
        L += [f"### Add-ons ({len(addons)})", ""]
        L += _table(
            ["Name", "Version", "State", "Update"],
            [
                [
                    a.get("name"),
                    a.get("version"),
                    a.get("state"),
                    f"-> {a.get('latest_version')}" if a.get("update_available") else "",
                ]
                for a in addons
            ],
        )
    storage = {"disk": data.get("disk"), "recorder": data.get("recorder")}
    L += ["## Storage and recorder", ""]
    L += _kv(storage)
    health = data.get("system_health") or {}
    if health:
        L += ["## System health (as reported by integrations)", ""]
        for domain, info in sorted(health.items()):
            if isinstance(info, dict) and info:
                L += [f"### {domain}", ""]
                L += _kv(info)
    return L


def _integration_sections(data: dict[str, Any]) -> list[str]:
    integrations = data.get("integrations") or []
    L = [f"## Integrations ({len(integrations)} configured)", ""]
    L += _table(
        ["Integration", "Title", "State", "Devices", "Entities", "Notes"],
        [
            [
                i["domain"],
                i.get("title"),
                i.get("state"),
                i.get("devices"),
                i.get("entities"),
                "; ".join(
                    x
                    for x in (
                        "disabled" if i.get("disabled") else "",
                        i.get("reason") or "",
                        f"source={i['source']}" if i.get("source") not in (None, "user") else "",
                    )
                    if x
                ),
            ]
            for i in integrations
        ],
    )
    bad = [i for i in integrations if i.get("state") not in ("loaded", None)]
    if bad:
        L += ["**Integrations not in the `loaded` state:** " + ", ".join(
            f"{i['domain']} ({i['state']})" for i in bad
        ), ""]
    other = data.get("other_loaded_components") or []
    if other:
        L += [
            "### Other loaded components (YAML-configured or built in)",
            "",
            ", ".join(other),
            "",
        ]
    custom = data.get("custom_components") or []
    L += [f"## Custom components ({len(custom)})", ""]
    L += _table(
        ["Domain", "Name", "Version", "Type"],
        [[c.get("domain"), c.get("name"), c.get("version"), c.get("integration_type")] for c in custom],
    )
    hacs = data.get("hacs_repositories") or []
    if hacs:
        L += [f"### HACS installed repositories ({len(hacs)})", ""]
        L += _table(
            ["Repository", "Category", "Version"],
            [[h.get("repository"), h.get("category"), h.get("version")] for h in hacs],
        )
    return L


def _problem_sections(data: dict[str, Any], full: bool) -> list[str]:
    L = ["## Health and problems", ""]
    repairs = data.get("repairs") or []
    L += [f"### Repairs ({len(repairs)})", ""]
    L += _table(
        ["Integration", "Issue", "Severity", "Fixable", "Dismissed"],
        [[r["domain"], r["issue_id"], r.get("severity"), r.get("fixable"), r.get("dismissed")] for r in repairs],
    )
    updates = data.get("updates_available") or []
    L += [f"### Updates available ({len(updates)})", ""]
    L += _table(
        ["Entity", "Title", "Installed", "Latest"],
        [[u["entity_id"], u.get("title"), u.get("installed"), u.get("latest")] for u in updates],
    )
    unavailable = data.get("unavailable") or []
    L += [f"### Unavailable entities ({len(unavailable)})", ""]
    shown = unavailable if full else unavailable[:MAX_UNAVAILABLE_OVERVIEW]
    L += _table(
        ["Entity", "Name", "Integration"],
        [[u["entity_id"], u.get("name"), u.get("platform")] for u in shown],
    )
    if len(shown) < len(unavailable):
        L += [f"_…and {len(unavailable) - len(shown)} more (see full edition)._", ""]
    battery = data.get("low_battery") or []
    if battery:
        L += [f"### Low batteries (<=20%) ({len(battery)})", ""]
        L += _table(
            ["Entity", "Name", "Level %"],
            [[b["entity_id"], b.get("name"), b.get("level")] for b in battery],
        )
    log = data.get("log")
    if log is not None:
        L += ["### Recent warnings and errors from home-assistant.log", ""]
        if not log.get("available"):
            L += ["_log file not found_", ""]
        else:
            L += [
                f"Scanned the last ~{log.get('scanned_kb')} KB; {log.get('unique_problems')} distinct problems. "
                "Most recent first; repeated messages are grouped.",
                "",
            ]
            L += _table(
                ["Last seen", "Level", "Count", "Logger", "Message"],
                [
                    [e["last_seen"], e["level"], e["count"], e["logger"], e["message"]]
                    for e in log.get("entries", [])
                ],
            )
    errs = (data.get("meta") or {}).get("collection_errors") or []
    if errs:
        L += ["### Export notes (parts that could not be collected)", ""]
        L += [f"- {e}" for e in errs] + [""]
    return L


def _area_sections(data: dict[str, Any]) -> list[str]:
    areas = data.get("areas") or []
    L = [f"## Areas ({len(areas)})", ""]
    L += _table(
        ["Area", "Floor", "Devices", "Entities"],
        [[a["name"], a.get("floor"), a.get("devices"), a.get("entities")] for a in areas],
    )
    return L


def _automation_sections(data: dict[str, Any], full: bool) -> list[str]:
    L: list[str] = []
    autos = data.get("automations") or []
    shown = autos if full else autos[:MAX_AUTOMATIONS_OVERVIEW]
    L += [f"## Automations ({len(autos)})", ""]
    L += _table(
        ["Automation", "Entity", "State", "Last triggered", "Mode"],
        [[a["name"], a["entity_id"], a["state"], a.get("last_triggered"), a.get("mode")] for a in shown],
    )
    if len(shown) < len(autos):
        L += [f"_…and {len(autos) - len(shown)} more (see full edition)._", ""]
    scripts = data.get("scripts") or []
    L += [f"## Scripts ({len(scripts)})", ""]
    L += _table(
        ["Script", "Entity", "State", "Last triggered"],
        [[s["name"], s["entity_id"], s["state"], s.get("last_triggered")] for s in scripts],
    )
    scenes = data.get("scenes") or []
    L += [f"## Scenes ({len(scenes)})", ""]
    L += _table(["Scene", "Entity"], [[s["name"], s["entity_id"]] for s in scenes])
    helpers = data.get("helpers") or []
    L += [f"## Helpers ({len(helpers)})", ""]
    if full:
        L += _table(
            ["Helper", "Entity", "State"],
            [[h["name"], h["entity_id"], h["state"]] for h in helpers],
        )
    else:
        counts: dict[str, int] = {}
        for h in helpers:
            domain = h["entity_id"].split(".", 1)[0]
            counts[domain] = counts.get(domain, 0) + 1
        L += [", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "_none_", ""]
    return L


def _dashboard_sections(data: dict[str, Any]) -> list[str]:
    lovelace = data.get("lovelace") or {}
    dashboards = lovelace.get("dashboards") or []
    L = [f"## Dashboards ({len(dashboards)})", ""]
    for d in dashboards:
        label = d.get("title") or d.get("url_path") or "dashboard"
        L.append(f"- **{label}** (url: `{d.get('url_path') or 'lovelace'}`, mode: {d.get('mode')})")
        if d.get("views") is None:
            L.append(f"  - {d.get('note')}")
        for v in d.get("views") or []:
            cards = ", ".join(f"{k}×{n}" for k, n in (v.get("cards") or {}).items()) or "no cards"
            L.append(
                f"  - view \"{v.get('title') or v.get('path')}\" ({v.get('type')}): {cards}"
                + (f"; {v['badges']} badges" if v.get("badges") else "")
            )
    L.append("")
    resources = lovelace.get("resources") or []
    if resources:
        L += [f"### Custom dashboard resources ({len(resources)})", ""]
        L += [f"- `{r.get('url')}` ({r.get('type')})" for r in resources] + [""]
    return L


def _domain_sections(data: dict[str, Any]) -> list[str]:
    counts = data.get("domain_counts") or {}
    L = ["## Entities per domain", ""]
    L += _table(["Domain", "Entities"], [[k, v] for k, v in counts.items()])
    excluded = data.get("excluded_entities")
    if excluded:
        L += [f"_{excluded} entities were excluded by the domain filter._", ""]
    return L


def _device_section(data: dict[str, Any]) -> list[str]:
    devices = data.get("devices") or []
    L = [f"## Devices ({len(devices)})", ""]
    L += _table(
        ["Ref", "Name", "Manufacturer", "Model", "Firmware", "Area", "Integration", "Via"],
        [
            [
                d["ref"],
                d.get("name"),
                d.get("manufacturer"),
                d.get("model"),
                d.get("sw_version"),
                d.get("area"),
                ", ".join(d.get("integrations") or []),
                d.get("via"),
            ]
            for d in devices
        ],
    )
    return L


def _entity_sections(data: dict[str, Any]) -> list[str]:
    with_attrs = bool((data.get("meta") or {}).get("include_attributes"))
    L = ["## Entities by area", ""]
    L.append(
        "Format: `entity_id` \"name\" = state unit [platform = integration that owns it, device_class, category, device = ref from the Devices table]"
        + (". `attrs:` lines carry the entity's extra attributes." if with_attrs else ".")
    )
    L.append("")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for e in data.get("entities") or []:
        grouped.setdefault(e.get("area") or "(no area)", []).append(e)
    order = sorted(grouped, key=lambda a: (a == "(no area)", a.lower()))
    for area in order:
        items = grouped[area]
        L += [f"### {area} ({len(items)})", ""]
        for e in items:
            L += _entity_line(e, with_attrs)
        L.append("")
    return L


def _services_section(data: dict[str, Any]) -> list[str]:
    services = data.get("services")
    if not services:
        return []
    L = ["## Available services", ""]
    L += [f"- `{domain}`: {', '.join(names)}" for domain, names in services.items()]
    L.append("")
    return L


def _config_section(data: dict[str, Any]) -> list[str]:
    cfg = data.get("config_files")
    if not cfg:
        return []
    L = ["## Configuration files", "", "Secrets are masked; `!secret` references are kept as-is.", ""]
    listing = data.get("config_dir_listing") or []
    if listing:
        L += ["Top-level contents of the config folder: " + ", ".join(f"`{n}`" for n in listing), ""]
    for extra, label in (("blueprints", "Blueprints"), ("esphome_files", "ESPHome configs")):
        names = data.get(extra) or []
        if names:
            L += [f"{label}: " + ", ".join(f"`{n}`" for n in names), ""]
    for name, text in (cfg.get("files") or {}).items():
        L += [f"### {name}", "", "```yaml", text.rstrip(), "```", ""]
    if cfg.get("truncated"):
        L += ["_Truncated for size: " + ", ".join(cfg["truncated"]) + "_", ""]
    if cfg.get("skipped"):
        L += ["_Skipped (size cap): " + ", ".join(cfg["skipped"]) + "_", ""]
    return L


def render(data: dict[str, Any], kind: str) -> str:
    """Render one edition: 'overview', 'full' or 'json'."""
    if kind == "json":
        return json.dumps(data, indent=1, default=str, ensure_ascii=False)
    full = kind == "full"
    L: list[str] = _header(data, full)
    L += _system_sections(data)
    L += _integration_sections(data)
    L += _problem_sections(data, full)
    L += _area_sections(data)
    L += _domain_sections(data)
    L += _automation_sections(data, full)
    L += _dashboard_sections(data)
    if full:
        L += _device_section(data)
        L += _entity_sections(data)
        L += _services_section(data)
        L += _config_section(data)
    return "\n".join(L).rstrip() + "\n"
