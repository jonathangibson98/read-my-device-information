# Changelog

## 1.2.0
- **Saved as .txt.** Overview and Full are now written as `.txt` files instead of `.md`
  (the content is the same). Data stays `.json`. Older `.md` files are not deleted.
- **Save to your Home Assistant config folder.** New location *Home Assistant config folder
  (/config)* (`/homeassistant` inside the add-on). Pick it, then type any sub-folder to choose the
  exact path. The config mount is now read-write so the add-on can save there.
- **MQTT sensor.** Optional. Publishes the whole Full text as the `content` attribute of
  `sensor.my_device_information_full` (state = time of the last snapshot). Switch it on from the
  add-on page (*MQTT sensor*) or with the new `mqtt_sensor` option. Turning it off removes the sensor
  on the next snapshot.

## 1.1.0
- **Choose your files.** Switch Overview, Full and Data (JSON) on or off, each with its own
  masking (default, standard, strict or none).
- **Second place.** Tick *Also save a copy* on any file to write it to a separate location and
  folder as well. The copy has the same masking as the file.
- **Automatic updates on the web page.** Pick an interval (hourly to weekly) and see when the
  next update is due. Changes apply immediately, no restart. The add-on option is now just the default.
- **History size.** Choose how many dated copies of each file to keep (0 = keep all). The oldest are
  deleted after each run. New add-on option `history_keep` (default 20).
- Create snapshot now saves your selection first, so automatic runs match what you see.

## 1.0.0
- Renamed from "AI Context Export" to **My Device Information**.
- Now a Home Assistant add-on with a sidebar page, plus a Docker / Docker Compose version.
- Choose where files are saved (location, sub-folder, optional dated history), or use Save as…
- New icon, logo, favicon and sidebar icon.
