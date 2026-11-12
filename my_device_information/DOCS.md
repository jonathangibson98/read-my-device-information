# My Device Information

## Use

1. Start the add-on and open **My Device Information** from the sidebar.
2. Choose which files to create (see below), then press **Create snapshot**.
3. Under **Your files**, use **Copy** on the Overview and paste it into your AI chat. If the AI
   needs more, paste the Full edition or the part you need.

## Choosing the files and their masking

Under **Create a snapshot** each file has its own card:

- **Tick box:** create this file or skip it. At least one must stay on.
- **Masking:** *Default* (follows the add-on option), *Standard*, *Strict* or *None*. You can,
  for example, keep Overview on *Strict* to paste into a public forum and Data (JSON) on *None*
  for your own tools.
- **Also save a copy in a second place:** also writes that file to a separate location (see
  below). Pick the place and sub-folder in the box that appears. Copies are overwritten each run
  and have no history.

**Create snapshot** saves these choices first, so automatic updates use the same ones.
**Save selection** saves them without running. Each different masking level needs its own pass
over Home Assistant, so mixing levels makes a run take longer.

## Where the files are saved

Under **Where the files are saved** pick a location and an optional sub-folder, then press
**Save this choice**. Locations:

| Location | Path | Reach it with |
|---|---|---|
| Share folder | `/share/<sub-folder>` | Samba share "share", File editor, SSH |
| Media folder | `/media/<sub-folder>` | Samba share "media", Media browser |
| Home Assistant config folder | `/config/<sub-folder>` (File editor) | File editor, Studio Code Server, Samba share "config" |
| This add-on's config folder | `/addon_configs/<id>_my_device_information/<sub-folder>` | SSH, Studio Code Server (with add-on config access) |

Your choice overrides the defaults on the Configuration tab until you press **Back to default**.
**Save as…** next to a file lets you put a copy anywhere on the device you are browsing from.

Files are overwritten on each run. Turn on *Also keep a dated copy of every run* to also write
timestamped copies into a `history` folder, and set **Dated copies to keep for each file**
(default 20, 0 = keep all). After each run the oldest copies beyond that number are deleted, per
file type. Only files with this add-on's dated names are ever removed, and lowering the number
deletes nothing until the next run.

## Automatic updates

Under **Automatic updates** pick how often the files are re-created (off, hourly up to weekly)
and press **Save**. The page shows when the next update is due. It takes effect straight away,
with no restart, and uses the files, masking and places you saved. If a run fails (for example
Home Assistant is restarting) it tries again after 15 minutes at most. **Back to default** returns
to the value on the Configuration tab.

## Options

| Option | What it does |
|---|---|
| Default save location / sub-folder | Used until you pick another place on the web page |
| Keep a dated copy of every run | Adds timestamped copies under `history/` |
| Dated copies to keep | How many dated copies of each file stay in `history/` (0 = all, default 20) |
| Default privacy masking | `none`, `standard` (default) or `strict`. Files set to *Default* on the web page use this |
| Include entity attributes | Bigger full edition with every attribute |
| Include YAML configuration files | Adds `configuration.yaml` and friends. `secrets.yaml` is never read |
| Include recent warnings and errors | Groups the latest problems from the log |
| Maximum log problems to list | 10 to 500 |
| Include the list of available services | Every service per domain |
| Exclude entity domains | For example `camera, media_player` |
| Refresh automatically every N hours | 0 = off. Default for the web page's *Automatic updates* |

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

## MQTT sensor

Turn on **Publish Full as an MQTT sensor** (add-on page, section *MQTT sensor*, or the
`mqtt_sensor` option) and press **Create snapshot**. You get `sensor.my_device_information_full`:

- **State:** the time of the last snapshot.
- **Attribute `content`:** the complete text of the Full file, with the same masking as that file.
- Other attributes: `generated_at`, `bytes`, `tokens`, `masking`, `file`.

Read it in a template with `{{ state_attr('sensor.my_device_information_full', 'content') }}`.

Notes:

- Needs the MQTT integration set up in Home Assistant. The add-on publishes through Home Assistant,
  so it needs no broker password. The sensor is created with MQTT discovery and is retained, so it
  survives restarts.
- The Full file must be switched on. The sensor is refreshed on every snapshot, including automatic ones.
- The attribute can be large. Home Assistant does not store attributes over 16 KB in its history
  database (it logs a warning). To avoid the warning, exclude the sensor from the recorder:

  ```yaml
  recorder:
    exclude:
      entities:
        - sensor.my_device_information_full
  ```

- Check the real entity ID in Developer tools > States if you renamed it.
- Turning the option off removes the sensor from Home Assistant on the next snapshot.
