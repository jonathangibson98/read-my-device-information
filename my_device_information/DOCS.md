# My Device Information

## Use

1. Start the add-on and open **My Device Information** from the sidebar.
2. Press **Create snapshot**.
3. Under **Your files**, use **Copy** on the Overview and paste it into your AI chat. If the AI
   needs more, paste the Full edition or the part you need.

## Where the files are saved

Under **Where the files are saved** pick a location and an optional sub-folder, then press
**Save this choice**. Locations:

| Location | Path | Reach it with |
|---|---|---|
| Share folder | `/share/<sub-folder>` | Samba share "share", File editor, SSH |
| Media folder | `/media/<sub-folder>` | Samba share "media", Media browser |
| This add-on's config folder | `/addon_configs/<id>_my_device_information/<sub-folder>` | SSH, Studio Code Server (with add-on config access) |

Your choice overrides the defaults on the Configuration tab until you press **Back to default**.
**Save as…** next to a file lets you put a copy anywhere on the device you are browsing from.

Files are overwritten on each run. Turn on *Keep a dated copy of every run* to also write
timestamped copies into a `history` folder.

## Options

| Option | What it does |
|---|---|
| Default save location / sub-folder | Used until you pick another place on the web page |
| Keep a dated copy of every run | Adds timestamped copies under `history/` |
| Privacy masking | `none`, `standard` (default) or `strict` |
| Include entity attributes | Bigger full edition with every attribute |
| Include YAML configuration files | Adds `configuration.yaml` and friends. `secrets.yaml` is never read |
| Include recent warnings and errors | Groups the latest problems from the log |
| Maximum log problems to list | 10 to 500 |
| Include the list of available services | Every service per domain |
| Exclude entity domains | For example `camera, media_player` |
| Refresh automatically every N hours | 0 = off. Rewrites the files in the background |

## Privacy

| Level | Masked |
|---|---|
| standard | Passwords, tokens, API keys, webhook IDs, JWTs, long hex/base64 strings, emails, MAC addresses, Nabu Casa / DuckDNS / Tailscale-style hostnames, credentials in URLs, coordinates (rounded to ~10 km), instance ID |
| strict | Everything above plus IP addresses, `.local` hostnames, serial numbers, unique IDs, SSIDs, usernames, person and device-tracker names and locations |
| none | Nothing |

The page is only reachable through Home Assistant's ingress (admins only). Masking is pattern
based, so skim the overview before pasting it anywhere you don't fully trust.

## Troubleshooting

- **"Not writable"** next to a location: restart the add-on after updating it so the folder
  mappings apply.
- **Sections missing:** the "Export notes" list at the bottom of the page and inside the
  Overview names anything that could not be collected.
- **Log section empty:** Home Assistant OS may not keep `home-assistant.log`; the add-on then
  asks Home Assistant for the log, which can be unavailable on some setups.
