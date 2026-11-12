"""Offline tests: no Home Assistant and no aiohttp needed (aiohttp is stubbed)."""
import asyncio, json, os, sys, tempfile, types, unittest
from contextlib import asynccontextmanager
from pathlib import Path

# --- stub aiohttp so the package imports in a bare sandbox --------------------
stub = types.ModuleType("aiohttp")
class _Err(Exception): ...
stub.ClientError = _Err
stub.ClientWebSocketResponse = object
stub.WSMsgType = types.SimpleNamespace(TEXT=1, CLOSE=2, CLOSING=3, CLOSED=4, ERROR=5)
class _TO:
    def __init__(self, **kw): pass
stub.ClientTimeout = _TO
class _Sess:
    def __init__(self, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
stub.ClientSession = _Sess
stub.web = types.SimpleNamespace()
sys.modules["aiohttp"] = stub
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "my_device_information" / "app"))

from mdi import settings, exporter, collector  # noqa: E402
from mdi.redact import Redactor  # noqa: E402
from mdi.render import render  # noqa: E402

STATES = [
    {"entity_id": "light.kitchen", "state": "on", "last_changed": "2026-11-11T10:00:00+00:00",
     "attributes": {"friendly_name": "Kitchen Light", "brightness": 200}},
    {"entity_id": "sensor.door_battery", "state": "12", "last_changed": "x",
     "attributes": {"friendly_name": "Door battery", "device_class": "battery", "unit_of_measurement": "%"}},
    {"entity_id": "sensor.gone", "state": "unavailable", "last_changed": "x", "attributes": {"friendly_name": "Gone"}},
    {"entity_id": "person.alex", "state": "home", "last_changed": "x",
     "attributes": {"friendly_name": "Alex", "latitude": 52.5234, "source": "device_tracker.phone"}},
    {"entity_id": "automation.night", "state": "on", "last_changed": "x",
     "attributes": {"friendly_name": "Night mode", "last_triggered": "2026-11-10T22:00:00+00:00", "mode": "single"}},
    {"entity_id": "update.core", "state": "on", "last_changed": "x",
     "attributes": {"title": "Core", "installed_version": "2026.10", "latest_version": "2026.11"}},
    {"entity_id": "sensor.leak", "state": "ok", "last_changed": "x",
     "attributes": {"api_key": "SECRETSECRET", "note": "mail me at bob@example.com from 192.168.1.50"}},
]
RESP = {
    "get_config": {"version": "2026.11.0", "location_name": "Home", "time_zone": "Europe/Berlin",
                   "latitude": 52.5234, "longitude": 13.4115, "unit_system": {"length": "km"},
                   "components": ["light", "sensor", "hue", "http"], "config_dir": "/config",
                   "internal_url": "http://192.168.1.10:8123"},
    "get_states": STATES,
    "config/entity_registry/list": [
        {"entity_id": "light.kitchen", "platform": "hue", "device_id": "d1", "config_entry_id": "e1",
         "area_id": None, "name": None, "original_name": "Kitchen Light", "disabled_by": None, "hidden_by": None},
        {"entity_id": "sensor.disabled", "platform": "hue", "device_id": "d1", "config_entry_id": "e1",
         "disabled_by": "user", "original_name": "Disabled one"},
    ],
    "config/device_registry/list": [
        {"id": "d1", "name": "Hue Bridge", "name_by_user": None, "manufacturer": "Signify", "model": "BSB002",
         "sw_version": "1.60", "area_id": "kitchen", "config_entries": ["e1"], "via_device_id": None,
         "entry_type": None, "disabled_by": None}],
    "config/area_registry/list": [{"area_id": "kitchen", "name": "Kitchen", "floor_id": "f0", "labels": []}],
    "config/floor_registry/list": [{"floor_id": "f0", "name": "Ground"}],
    "config/label_registry/list": [],
    "config_entries/get": [{"entry_id": "e1", "domain": "hue", "title": "Philips Hue", "state": "loaded",
                            "source": "user", "disabled_by": None, "reason": None}],
    "repairs/list_issues": {"issues": [{"domain": "hue", "issue_id": "old_fw", "severity": "warning",
                                        "is_fixable": False}]},
    "get_services": {"light": {"turn_on": {}, "turn_off": {}}},
    "lovelace/dashboards/list": [],
    "lovelace/config": {"views": [{"title": "Home", "cards": [{"type": "entities"}, {"type": "tile"}]}]},
    "lovelace/resources": [],
}
LOG = ("2026-11-11 10:00:00.123 WARNING (MainThread) [homeassistant.components.hue] Bridge 12 slow\n"
       "2026-11-11 10:05:00.123 WARNING (MainThread) [homeassistant.components.hue] Bridge 99 slow\n")

class FakeWS:
    async def command(self, type_, timeout=90, **kw):
        if type_ not in RESP:
            raise collector.HAError(f"{type_}: unknown_command")
        return RESP[type_]

class FakeClient:
    def __init__(self, supervisor=True): self._sup = supervisor
    @property
    def has_supervisor(self): return self._sup
    @asynccontextmanager
    async def websocket(self): yield FakeWS()
    async def supervisor(self, path):
        data = {"/info": {"docker": "27.0", "arch": "amd64"},
                "/host/info": {"operating_system": "Home Assistant OS 15", "hostname": "ha-box", "kernel": "6.6", "disk_total": 30},
                "/os/info": {"version": "15.0", "board": "generic-x86-64"},
                "/core/info": {"version": "2026.11.0"},
                "/supervisor/info": {"version": "2026.11.1", "healthy": True},
                "/addons": {"addons": [{"name": "My Device Information", "slug": "x", "version": "1.0.0",
                                        "state": "started", "update_available": False}]}}
        return data[path]
    async def supervisor_text(self, path): return LOG
    async def get_text(self, path): return LOG

OPTS = {**settings.DEFAULTS, "include_services": True}

def run(coro): return asyncio.run(coro)

class CollectorTests(unittest.TestCase):
    def test_strict_masks_and_counts(self):
        data = run(collector.collect(FakeClient(), {**OPTS, "redaction": "strict"}, None, Redactor("strict")))
        blob = json.dumps(data)
        self.assertNotIn("SECRETSECRET", blob)
        self.assertNotIn("bob@example.com", blob)
        self.assertNotIn("192.168.1.50", blob)
        self.assertNotIn("Alex", blob)
        self.assertNotIn("52.5234", blob)
        self.assertEqual(data["counts"]["entities"], 8)  # 2 registry + 6 state-only
        self.assertEqual(data["counts"]["areas"], 1)
        self.assertEqual(data["unavailable"][0]["entity_id"], "sensor.gone")
        self.assertEqual(data["low_battery"][0]["level"], 12.0)
        self.assertEqual(data["updates_available"][0]["latest"], "2026.11")
        self.assertEqual(data["repairs"][0]["issue_id"], "old_fw")
        self.assertEqual(data["integrations"][0]["devices"], 1)
        self.assertEqual(data["integrations"][0]["entities"], 2)
        self.assertEqual(data["lovelace"]["dashboards"][0]["views"][0]["cards"], {"entities": 1, "tile": 1})
        self.assertEqual(data["supervisor"]["addons"][0]["name"], "My Device Information")
        self.assertTrue(any("configuration folder not available" in e for e in data["meta"]["collection_errors"]))
        self.assertEqual(data["log"]["unique_problems"], 1)  # numbers are grouped
        self.assertEqual(data["log"]["entries"][0]["count"], 2)
        self.assertEqual(data["services"]["light"], ["turn_off", "turn_on"])

    def test_standard_keeps_names_masks_secret(self):
        data = run(collector.collect(FakeClient(supervisor=False), {**OPTS, "redaction": "standard"}, None, Redactor("standard")))
        blob = json.dumps(data)
        self.assertNotIn("SECRETSECRET", blob)
        self.assertIn("Kitchen Light", blob)
        self.assertIsNone(data["supervisor"])

    def test_one_failing_call_is_reported_not_fatal(self):
        saved = RESP.pop("repairs/list_issues")
        try:
            data = run(collector.collect(FakeClient(), OPTS, None, Redactor("standard")))
        finally:
            RESP["repairs/list_issues"] = saved
        self.assertEqual(data["repairs"], [])
        self.assertTrue(any(e.startswith("repairs:") for e in data["meta"]["collection_errors"]))
        self.assertGreater(data["counts"]["entities"], 0)

    def test_renders_use_new_name(self):
        data = run(collector.collect(FakeClient(), OPTS, None, Redactor("standard")))
        for kind in ("overview", "full"):
            text = render(data, kind)
            self.assertIn("My Device Information", text)
        self.assertIn("kitchen", render(data, "full").lower())
        self.assertEqual(json.loads(render(data, "json"))["meta"]["generator"], "My Device Information 1.0.0")

def make_rt(tmp, **opt):
    base = Path(tmp)
    locs = {"share": settings.Location("share", "Share", base / "share"),
            "media": settings.Location("media", "Media", base / "media")}
    for l in locs.values(): l.path.mkdir(exist_ok=True)
    return settings.Runtime(mode="addon", options=settings._coerce(opt), data_dir=base / "data", locations=locs,
                            rest_base="", ws_url="", token="t", supervisor_base=None, supervisor_token=None,
                            config_dir=None, port=1, bind="x", web_password="", run_once=False)

class LocationTests(unittest.TestCase):
    def test_clean_subfolder(self):
        self.assertEqual(settings.clean_subfolder(" My Folder/sub-1\\x "), "My Folder/sub-1/x")
        self.assertEqual(settings.clean_subfolder(""), "")
        for bad in ("../x", "a/../b", "/etc/../../x", "a/.", "bad;name", "a/$(x)"):
            with self.assertRaises(ValueError, msg=bad): settings.clean_subfolder(bad)

    def test_choose_location_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = make_rt(tmp, save_location="share", save_subfolder="mdi")
            self.assertEqual(rt.output_dir(), (Path(tmp) / "share" / "mdi").resolve())
            rt.save_selection({"location": "media", "subfolder": "snaps/a", "keep_history": True})
            rt2 = make_rt(tmp, save_location="share")  # same data dir -> remembers choice
            rt2.load_selection()
            self.assertEqual(rt2.output_dir(), (Path(tmp) / "media" / "snaps" / "a").resolve())
            rt2.clear_selection()
            self.assertEqual(rt2.effective()["location"], "share")

    def test_symlink_escape_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = make_rt(tmp, save_subfolder="link")
            outside = Path(tmp) / "outside"; outside.mkdir()
            (rt.locations["share"].path / "link").symlink_to(outside)
            with self.assertRaises(ValueError): rt.output_dir()

class ExportTests(unittest.TestCase):
    def test_generate_writes_chosen_folder_with_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = make_rt(tmp, save_location="share")
            rt.save_selection({"location": "media", "subfolder": "My Snapshots", "keep_history": True})
            exporter.HAClient = lambda *a, **k: FakeClient()
            mgr = exporter.ExportManager(rt)
            info = run(mgr.generate({"redaction": "strict"}))
            out = Path(tmp) / "media" / "My Snapshots"
            names = sorted(p.name for p in out.iterdir())
            self.assertEqual(names, ["My Device Information - Data.json", "My Device Information - Full.md",
                                     "My Device Information - Overview.md", "history"])
            self.assertEqual(len(list((out / "history").iterdir())), 3)
            self.assertEqual(info["folder"], str(out.resolve()))
            self.assertFalse(list((Path(tmp) / "share").iterdir()))  # nothing written to the unchosen place
            self.assertNotIn("SECRETSECRET", (out / "My Device Information - Full.md").read_text())
            self.assertEqual(os.stat(out / "My Device Information - Overview.md").st_mode & 0o777, 0o600)
            name, text = mgr.read("overview")
            self.assertTrue(text.startswith("# My Device Information"))
            self.assertIsNone(mgr.read("nope"))
            # latest result survives a restart
            self.assertEqual(exporter.ExportManager(rt).latest["folder"], info["folder"])

class DockerEnvTests(unittest.TestCase):
    def test_env_loading(self):
        env = {"MDI_MODE": "docker", "HA_URL": "https://ha.example:8123/", "HA_TOKEN": "tok",
               "OUTPUT_DIR": "/tmp", "EXTRA_OUTPUT_DIRS": "NAS Backup=/tmp", "REDACTION": "strict",
               "KEEP_HISTORY": "true", "LOG_LINES": "9999", "SAVE_SUBFOLDER": "", "RUN_MODE": "once",
               "DATA_DIR": tempfile.gettempdir()}
        old = dict(os.environ)
        try:
            os.environ.update(env)
            rt = settings.load()
        finally:
            os.environ.clear(); os.environ.update(old)
        self.assertEqual(rt.ws_url, "wss://ha.example:8123/api/websocket")
        self.assertEqual(rt.rest_base, "https://ha.example:8123")
        self.assertEqual(list(rt.locations), ["output", "nas_backup"])
        self.assertEqual(rt.options["redaction"], "strict")
        self.assertTrue(rt.options["keep_history"])
        self.assertEqual(rt.options["log_lines"], 500)
        self.assertEqual(rt.options["save_subfolder"], "")
        self.assertTrue(rt.run_once)

if __name__ == "__main__":
    unittest.main(verbosity=2)
