# My Device Information

<img src="my_device_information/logo.png" alt="My Device Information" width="250">

Creates a readable snapshot of your whole Home Assistant setup that you can paste into
Claude, ChatGPT or any other AI, so it knows what you run before it answers.
Nothing is uploaded anywhere. Files are only written to the folder **you** choose.

Two ways to run it, same code, same web page:

| | Home Assistant add-on | Docker / Docker Compose |
|---|---|---|
| For | Home Assistant OS and Supervised | Home Assistant Container/Core, or any machine on your network |
| Connects with | Supervisor (no token needed) | Long-lived access token |
| Saves to | Share, Media or the add-on's config folder | Any folder you mount |

## What is in the snapshot

System, host and every add-on (add-on mode), integrations and custom components, health
(repairs, updates, unavailable entities, low batteries, grouped log errors), areas, devices,
entities, automations, scripts, scenes, helpers, dashboards, and your YAML configuration.
Three editions are written each run:

- **Overview.md**: start here, a few thousand tokens.
- **Full.md**: every device, entity and config file.
- **Data.json**: everything as structured data.

## Choosing where files are saved

- **On the web page:** pick a location and sub-folder under "Where the files are saved", then
  press *Save this choice*. It is remembered across restarts.
- **Save as…** on any file opens your browser's save dialog so you can put a copy anywhere on
  the device you are browsing from.
- **Add-on:** the default location is also a setting on the Configuration tab.
- **Docker:** the folder is whatever you mount to `/output` (`OUTPUT_PATH` in `.env`). Mount
  more folders and list them in `EXTRA_OUTPUT_DIRS` to choose between them on the page.

## Install as a Home Assistant add-on

1. Put this repository on GitHub and edit the two `your-name` URLs, or copy the
   `my_device_information` folder into `/addons/` (use the Samba or SSH add-on) for a local install.
2. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add your repository URL
   (for a local install, choose ⋮ → Check for updates instead).
3. Install **My Device Information**, start it, and open it from the sidebar.

## Install with Docker Compose

```bash
cd docker
cp .env.example .env        # set HA_URL and HA_TOKEN, and OUTPUT_PATH
mkdir -p output             # or the folder you chose; it must be writable by PUID:PGID
docker compose up -d --build
```

Open <http://localhost:8099>. The port is bound to localhost; to use it from other devices
change the port mapping and set `WEB_PASSWORD`.

One-off run without the web page (for cron):

```bash
docker compose run --rm -e RUN_MODE=once my-device-information
```

Create the token in Home Assistant under your profile → **Security → Long-lived access tokens**.
To include YAML files, custom components and disk usage, mount your config folder with
`HA_CONFIG_PATH` (read-only). Without it you still get everything the API exposes.

## Privacy

Masking is on by default (`standard`). `strict` also hides IP addresses, hostnames, serial
numbers and person names; `none` masks nothing. `secrets.yaml` and the `.storage` folder
contents are never copied into the files. Masking is pattern based, so skim the overview before
you paste it anywhere you don't fully trust.

## Differences from the old "AI Context Export" integration

- It no longer runs inside Home Assistant, so the notification, button entity and sensor are gone;
  use the sidebar page instead. For hands-off runs set *Refresh automatically every N hours*
  (add-on) or `AUTO_REFRESH_HOURS` (Docker).
- "System health" and "recorder" details are not available through the API and are left out.
- Python version and platform of the Home Assistant process are not reported.
- Files are named `My Device Information - Overview.md` and so on.

## Layout

```
repository.yaml
my_device_information/    add-on (config.yaml, Dockerfile, icon.png, logo.png, translations, app/)
docker/                   Dockerfile, docker-compose.yml, .env.example
tests/                    offline tests:  python3 tests/test_mdi.py
```
