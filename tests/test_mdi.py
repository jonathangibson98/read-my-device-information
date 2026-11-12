"""Offline tests: no Home Assistant and no aiohttp needed (aiohttp is stubbed)."""
import asyncio, json, os, sys, tempfile, types, unittest
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path

# --- aiohttp: use the real one when installed (enables the web tests), else stub it -----
try:
    import aiohttp  # noqa: F401
    HAVE_AIOHTTP = True
except ImportError:
    HAVE_AIOHTTP = False
if not HAVE_AIOHTTP:
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

from mdi import VERSION, settings, exporter, collector  # noqa: E402
from mdi.exporter import NoFilesSelected, prune_history  # noqa: E402
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
        self.assertEqual(json.loads(render(data, "json"))["meta"]["generator"], f"My Device Information {VERSION}")

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
            self.assertEqual(names, ["My Device Information - Data.json", "My Device Information - Full.txt",
                                     "My Device Information - Overview.txt", "history"])
            self.assertEqual(len(list((out / "history").iterdir())), 3)
            self.assertEqual(info["folder"], str(out.resolve()))
            self.assertFalse(list((Path(tmp) / "share").iterdir()))  # nothing written to the unchosen place
            self.assertNotIn("SECRETSECRET", (out / "My Device Information - Full.txt").read_text())
            self.assertEqual(os.stat(out / "My Device Information - Overview.txt").st_mode & 0o777, 0o600)
            name, text = mgr.read("overview")
            self.assertTrue(text.startswith("# My Device Information"))
            self.assertIsNone(mgr.read("nope"))
            # latest result survives a restart
            self.assertEqual(exporter.ExportManager(rt).latest["folder"], info["folder"])


def make_rt3(tmp, **opt):
    """Like make_rt, with a third folder so second-place tests have somewhere to go."""
    rt = make_rt(tmp, **opt)
    p = Path(tmp) / "extra"; p.mkdir(exist_ok=True)
    rt.locations["extra"] = settings.Location("extra", "Extra", p)
    return rt

def fresh(tmp, **opt):
    rt = make_rt3(tmp, save_location="share", **opt)
    exporter.HAClient = lambda *a, **k: FakeClient()
    return rt, exporter.ExportManager(rt)

OV, FU, JS = ("My Device Information - Overview.txt", "My Device Information - Full.txt", "My Device Information - Data.json")

class PerFileTests(unittest.TestCase):
    def test_only_chosen_files_each_with_its_own_masking(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, mgr = fresh(tmp)
            rt.save_prefs({"files": {"overview": {"redaction": "strict"}, "full": {"enabled": False},
                                     "json": {"redaction": "none"}}})
            info = run(mgr.generate())
            self.assertEqual(set(info["files"]), {"overview", "json"})
            self.assertEqual(info["files"]["overview"]["redaction"], "strict")
            self.assertEqual(info["files"]["json"]["redaction"], "none")
            self.assertEqual(info["redaction"], "mixed")
            out = rt.output_dir()
            self.assertFalse((out / FU).exists())
            self.assertIn("Redaction level: **strict**", (out / OV).read_text())
            self.assertNotIn("SECRETSECRET", (out / OV).read_text())
            data = json.loads((out / JS).read_text())
            self.assertEqual(data["meta"]["redaction"], "none")
            self.assertIn("SECRETSECRET", json.dumps(data))  # none really masks nothing
            self.assertIsNone(mgr.read("full"))

    def test_default_follows_the_global_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, mgr = fresh(tmp, redaction="strict")
            info = run(mgr.generate())
            self.assertEqual({f["redaction"] for f in info["files"].values()}, {"strict"})
            self.assertEqual(info["redaction"], "strict")

    def test_api_override_beats_per_file_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, mgr = fresh(tmp)
            rt.save_prefs({"files": {"json": {"redaction": "none"}}})
            info = run(mgr.generate({"redaction": "strict"}))
            self.assertEqual({f["redaction"] for f in info["files"].values()}, {"strict"})

    def test_nothing_selected_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, mgr = fresh(tmp)
            rt.save_prefs({"files": {k: {"enabled": False} for k in settings.KINDS}})
            with self.assertRaises(NoFilesSelected):
                run(mgr.generate())
            self.assertEqual(list(rt.output_dir().parent.iterdir()) if rt.output_dir().parent.exists() else [], [])

class SecondPlaceTests(unittest.TestCase):
    def test_copy_goes_only_where_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, mgr = fresh(tmp)
            rt.save_prefs({"files": {"overview": {"copy": True}},
                           "extra": {"location": "extra", "subfolder": "copies"}})
            info = run(mgr.generate())
            copies = Path(tmp) / "extra" / "copies"
            self.assertEqual([p.name for p in copies.iterdir()], [OV])
            self.assertEqual(info["files"]["overview"]["copy_path"], str(copies.resolve() / OV))
            self.assertNotIn("copy_path", info["files"]["full"])
            self.assertEqual(info["copy_folder"], str(copies.resolve()))
            self.assertEqual((copies / OV).read_text(), (rt.output_dir() / OV).read_text())
            self.assertEqual(os.stat(copies / OV).st_mode & 0o777, 0o600)
            self.assertTrue((rt.output_dir() / FU).exists())  # main place still gets everything

    def test_second_copy_carries_the_files_own_masking(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, mgr = fresh(tmp)
            rt.save_prefs({"files": {"overview": {"redaction": "strict", "copy": True}},
                           "extra": {"location": "extra", "subfolder": ""}})
            run(mgr.generate())
            self.assertIn("Redaction level: **strict**", (Path(tmp) / "extra" / OV).read_text())

    def test_same_folder_is_skipped_with_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, mgr = fresh(tmp, save_subfolder="mdi")
            rt.save_prefs({"files": {"overview": {"copy": True}},
                           "extra": {"location": "share", "subfolder": "mdi"}})
            info = run(mgr.generate())
            self.assertNotIn("copy_path", info["files"]["overview"])
            self.assertTrue(any("same folder" in e for e in info["errors"]))

    def test_no_place_chosen_is_skipped_with_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, mgr = fresh(tmp)
            rt.save_prefs({"files": {"json": {"copy": True}}})
            info = run(mgr.generate())
            self.assertTrue(any("no second place" in e for e in info["errors"]))

    def test_unsafe_subfolder_never_escapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, _ = fresh(tmp)
            rt.save_prefs({"extra": {"location": "extra", "subfolder": "../x"}})
            with self.assertRaises(ValueError):
                rt.extra_dir()

def history_names(rt, kind_label):
    return sorted(p.name for p in (rt.output_dir() / "history").iterdir() if kind_label in p.name)

class HistoryTests(unittest.TestCase):
    def _seed(self, rt, label, days):
        h = rt.output_dir() / "history"; h.mkdir(parents=True, exist_ok=True)
        ext = "json" if label == "Data" else "txt"
        for d in days:
            (h / f"My Device Information - {label} 2026-01-{d:02d}_000000.{ext}").write_text("old")
        return h

    def test_keeps_only_the_newest_n(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, mgr = fresh(tmp)
            rt.save_selection({"location": "share", "subfolder": "mdi", "keep_history": True, "history_keep": 3})
            h = self._seed(rt, "Overview", range(1, 7))
            self._seed(rt, "Full", range(1, 7))
            (h / "notes.txt").write_text("mine")
            (h / "My Device Information - Overview backup.txt").write_text("mine too")
            run(mgr.generate())
            ov = [n for n in history_names(rt, "Overview") if "backup" not in n]
            self.assertEqual(len(ov), 3)
            self.assertEqual(ov[:2], ["My Device Information - Overview 2026-01-05_000000.txt",
                                      "My Device Information - Overview 2026-01-06_000000.txt"])
            self.assertEqual(len(history_names(rt, "Full")), 3)       # each file type is counted separately
            self.assertEqual(len(history_names(rt, "Data")), 1)
            self.assertTrue((h / "notes.txt").exists())                # never touches what it did not write
            self.assertTrue((h / "My Device Information - Overview backup.txt").exists())

    def test_zero_keeps_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, mgr = fresh(tmp)
            rt.save_selection({"location": "share", "subfolder": "mdi", "keep_history": True, "history_keep": 0})
            self._seed(rt, "Overview", range(1, 9))
            run(mgr.generate())
            self.assertEqual(len(history_names(rt, "Overview")), 9)

    def test_lowering_the_number_deletes_nothing_until_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, _ = fresh(tmp)
            h = self._seed(rt, "Overview", range(1, 6))
            rt.save_selection({"location": "share", "subfolder": "mdi", "keep_history": True, "history_keep": 1})
            self.assertEqual(len(list(h.iterdir())), 5)

    def test_prune_function_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Path(tmp)
            for d in range(1, 5):
                (h / f"My Device Information - Data 2026-02-{d:02d}_101010.json").write_text("x")
            self.assertEqual(prune_history(h, "json", 0), 0)
            self.assertEqual(prune_history(h, "json", 2), 2)
            self.assertEqual(prune_history(h / "missing", "json", 2), 0)

    def test_history_off_writes_no_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt, mgr = fresh(tmp)
            run(mgr.generate())
            self.assertFalse((rt.output_dir() / "history").exists())

class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _, self.mgr = fresh(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def now(self): return datetime.now().astimezone()

    def test_off_means_no_due_time(self):
        self.assertIsNone(self.mgr.next_due(0))

    def test_never_run_is_due_now(self):
        self.assertLessEqual(self.mgr.next_due(6), self.now())

    def test_due_one_interval_after_the_last_snapshot(self):
        last = self.now() - timedelta(hours=1)
        self.mgr.latest = {"generated_at": last.isoformat(timespec="seconds")}
        due = self.mgr.next_due(6)
        self.assertAlmostEqual((due - last).total_seconds(), 6 * 3600, delta=2)
        self.mgr.latest = {"generated_at": (self.now() - timedelta(hours=7)).isoformat(timespec="seconds")}
        self.assertLess(self.mgr.next_due(6), self.now())   # overdue after a restart

    def test_a_failed_run_retries_in_minutes_not_hours_or_seconds(self):
        self.mgr._last_failure = self.now()
        d = (self.mgr.next_due(168) - self.now()).total_seconds()
        self.assertTrue(14 * 60 < d <= 15 * 60)
        d = (self.mgr.next_due(0) is None)
        self.assertTrue(d)

    def test_regression_a_real_success_is_not_mistaken_for_a_failure(self):
        # generated_at is stored in whole seconds; comparing it with a finer timestamp once made
        # every successful run look failed, which re-ran the export every 15 minutes.
        run(self.mgr.generate())
        due_in = (self.mgr.next_due(6) - self.now()).total_seconds()
        self.assertGreater(due_in, 6 * 3600 - 30)

    def test_real_failure_retries_soon_then_recovers(self):
        real = exporter.collect
        async def boom(*a, **k): raise collector.HAError("Home Assistant is restarting")
        exporter.collect = boom
        try:
            with self.assertRaises(collector.HAError):
                run(self.mgr.generate())
            self.assertTrue(14 * 60 < (self.mgr.next_due(24) - self.now()).total_seconds() <= 15 * 60)
            run(self.mgr.scheduled())                      # the scheduler path swallows and logs it
        finally:
            exporter.collect = real
        run(self.mgr.generate())                           # next success clears the failure state
        self.assertGreater((self.mgr.next_due(24) - self.now()).total_seconds(), 23 * 3600)

    def test_scheduled_skips_while_a_run_is_in_progress(self):
        async def go():
            await self.mgr._lock.acquire()
            try:
                await self.mgr.scheduled()          # must return at once, not queue a second run
            finally:
                self.mgr._lock.release()
        run(go())
        self.assertIsNone(self.mgr.latest)

class PrefsTests(unittest.TestCase):
    def test_prefs_survive_a_restart_and_reset_is_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = make_rt3(tmp)
            rt.save_prefs({"files": {"full": {"enabled": False, "redaction": "strict"}}, "auto_refresh_hours": 12})
            rt.save_selection({"location": "media", "subfolder": "a", "keep_history": True, "history_keep": 7})
            rt2 = make_rt3(tmp); rt2.load_selection(); rt2.load_prefs()
            self.assertFalse(rt2.file_settings()["full"]["enabled"])
            self.assertEqual(rt2.file_settings()["full"]["redaction"], "strict")
            self.assertTrue(rt2.file_settings()["overview"]["enabled"])
            self.assertEqual(rt2.auto_refresh_hours(), 12)
            self.assertEqual(rt2.effective()["history_keep"], 7)
            rt2.clear_selection()                   # "Back to default" on the location card ...
            self.assertEqual(rt2.effective()["history_keep"], 20)
            self.assertEqual(rt2.auto_refresh_hours(), 12)   # ... leaves the other settings alone
            self.assertFalse(rt2.file_settings()["full"]["enabled"])

    def test_auto_hours_web_value_wins_but_falls_back_to_the_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = make_rt3(tmp, auto_refresh_hours=3)
            self.assertEqual(rt.auto_refresh_hours(), 3)
            rt.save_prefs({"auto_refresh_hours": 0})
            self.assertEqual(rt.auto_refresh_hours(), 0)     # explicitly off beats an add-on default of 3
            rt.save_prefs({})
            self.assertEqual(rt.auto_refresh_hours(), 3)

    def test_normalize_files(self):
        cur = make_rt3(tempfile.gettempdir()).file_settings()
        out = settings.normalize_files({"json": {"redaction": "none", "enabled": "false"}}, cur)
        self.assertEqual(out["json"], {"enabled": False, "redaction": "none", "copy": False})
        self.assertEqual(out["full"], cur["full"])
        for bad in ({"nope": {}}, {"full": {"redaction": "extreme"}}, {"full": 3}, ["x"]):
            with self.assertRaises(ValueError, msg=str(bad)): settings.normalize_files(bad, cur)

    def test_corrupt_prefs_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = make_rt3(tmp); rt.data_dir.mkdir(); (rt.data_dir / "prefs.json").write_text("{not json")
            rt.load_prefs()
            self.assertTrue(all(f["enabled"] for f in rt.file_settings().values()))
            (rt.data_dir / "prefs.json").write_text("[1, 2]")
            rt.load_prefs(); self.assertEqual(rt.prefs, {})

    def test_history_keep_is_clamped(self):
        self.assertEqual(settings._coerce({"history_keep": 99999})["history_keep"], 1000)
        self.assertEqual(settings._coerce({"history_keep": -4})["history_keep"], 0)
        self.assertEqual(settings._coerce({"history_keep": "abc"})["history_keep"], 20)
        self.assertEqual(settings._coerce({})["history_keep"], 20)


@unittest.skipUnless(HAVE_AIOHTTP, "needs aiohttp installed")
class WebTests(unittest.IsolatedAsyncioTestCase):
    """Real HTTP round trips through the aiohttp app (Home Assistant itself is faked)."""
    H = {"X-MDI": "1"}

    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        from mdi.web import build_app
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, self.mgr = fresh(self.tmp.name)
        self.client = TestClient(TestServer(build_app(self.rt, self.mgr)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close(); self.tmp.cleanup()

    async def post(self, path, body, headers=None):
        r = await self.client.post(path, json=body, headers=self.H if headers is None else headers)
        return r.status, await r.json()

    async def status(self):
        return await (await self.client.get("/api/status")).json()

    async def test_status_exposes_the_new_settings(self):
        s = await self.status()
        self.assertEqual(set(s["files_config"]), {"overview", "full", "json"})
        self.assertTrue(all(c == {"enabled": True, "redaction": "default", "copy": False} for c in s["files_config"].values()))
        self.assertEqual(s["selected"]["history_keep"], 20)
        self.assertEqual(s["auto"], {"hours": 0, "default_hours": 0, "overridden": False, "next_run": None})
        self.assertEqual(s["extra"]["location"], "")

    async def test_post_needs_the_header(self):
        st, body = await self.post("/api/settings", {"auto_refresh_hours": 6}, headers={})
        self.assertEqual(st, 403)

    async def test_save_files_and_they_stick(self):
        st, s = await self.post("/api/settings", {"files": {"full": {"enabled": False}, "json": {"redaction": "none"}}})
        self.assertEqual(st, 200)
        self.assertFalse(s["files_config"]["full"]["enabled"])
        self.assertEqual(s["files_config"]["json"]["redaction"], "none")
        self.assertEqual(s["files_config"]["overview"]["redaction"], "default")   # untouched
        again = settings.Runtime.__new__(settings.Runtime)                         # survives a restart
        rt2 = make_rt3(self.tmp.name); rt2.load_prefs()
        self.assertFalse(rt2.file_settings()["full"]["enabled"])

    async def test_validation_messages(self):
        cases = [
            ({"files": {"full": {"redaction": "extreme"}}}, "privacy level"),
            ({"files": {"nope": {"enabled": True}}}, "Unknown file"),
            ({"files": {k: {"enabled": False} for k in settings.KINDS}}, "at least one file"),
            ({"files": {"json": {"copy": True}}}, "Pick a place"),
            ({"files": {"json": {"copy": True}}, "extra": {"location": "nowhere"}}, "Unknown location"),
            ({"files": {"json": {"copy": True}}, "extra": {"location": "extra", "subfolder": "../x"}}, "Folder names"),
            ({"files": {"json": {"copy": True}}, "extra": {"location": "share", "subfolder": "my_device_information"}}, "same folder"),
            ({"auto_refresh_hours": 999}, "0 (off) to 168"),
            ({"auto_refresh_hours": "soon"}, "0 (off) to 168"),
            ({"auto_refresh_hours": True}, "0 (off) to 168"),
            ({"auto_refresh_hours": -1}, "0 (off) to 168"),
        ]
        for body, expect in cases:
            st, resp = await self.post("/api/settings", body)
            self.assertEqual(st, 400, msg=str(body)); self.assertIn(expect, resp["error"], msg=str(body))
        self.assertEqual(self.rt.prefs, {})        # nothing half-saved by any failed request
        st, _ = await self.post("/api/settings", ["not", "an", "object"]); self.assertEqual(st, 400)

    async def test_unwritable_second_place_is_refused(self):
        self.rt.locations["extra"].path = Path(self.tmp.name) / "does-not-exist"
        st, resp = await self.post("/api/settings", {"files": {"json": {"copy": True}}, "extra": {"location": "extra", "subfolder": ""}})
        self.assertEqual(st, 400); self.assertIn("not writable", resp["error"])

    async def test_auto_update_set_report_and_reset(self):
        st, s = await self.post("/api/settings", {"auto_refresh_hours": 6})
        self.assertEqual((st, s["auto"]["hours"], s["auto"]["overridden"]), (200, 6, True))
        due = datetime.fromisoformat(s["auto"]["next_run"])
        self.assertLessEqual(due, datetime.now().astimezone() + timedelta(seconds=2))   # never run -> due immediately
        self.assertEqual(self.rt.auto_refresh_hours(), 6)         # the scheduler reads this live, no restart
        st, s = await self.post("/api/settings", {"auto_refresh_hours": 0})
        self.assertEqual((s["auto"]["hours"], s["auto"]["next_run"], s["auto"]["overridden"]), (0, None, True))
        st, s = await self.post("/api/settings", {"auto_refresh_hours": None})
        self.assertFalse(s["auto"]["overridden"])

    async def test_history_size_on_the_location_card(self):
        base = {"location": "share", "subfolder": "mdi", "keep_history": True}
        for bad in ("abc", "", -1, 1001, None):
            st, resp = await self.post("/api/location", {**base, "history_keep": bad})
            self.assertEqual(st, 400, msg=repr(bad)); self.assertIn("0 to 1000", resp["error"])
        st, s = await self.post("/api/location", {**base, "history_keep": "5"})
        self.assertEqual((st, s["selected"]["history_keep"], s["selected"]["keep_history"]), (200, 5, True))
        st, s = await self.post("/api/location", base)                 # an older page that omits it keeps the value
        self.assertEqual(s["selected"]["history_keep"], 5)
        st, s = await self.post("/api/location/reset", {})
        self.assertEqual(s["selected"]["history_keep"], 20)

    async def test_save_then_generate_end_to_end(self):
        await self.post("/api/settings", {
            "files": {"overview": {"redaction": "strict", "copy": True}, "full": {"enabled": False}, "json": {"redaction": "none"}},
            "extra": {"location": "extra", "subfolder": "send_me"}})
        st, s = await self.post("/api/generate", {})
        self.assertEqual(st, 200)
        files = s["latest"]["files"]
        self.assertEqual(set(files), {"overview", "json"})
        self.assertEqual((files["overview"]["redaction"], files["json"]["redaction"]), ("strict", "none"))
        self.assertTrue(Path(files["overview"]["copy_path"]).exists())
        self.assertNotIn("copy_path", files["json"])
        self.assertEqual(s["latest"]["redaction"], "mixed")
        r = await self.client.get("/api/file/overview")
        self.assertIn("Redaction level: **strict**", await r.text())
        self.assertEqual((await self.client.get("/api/file/full")).status, 404)   # switched off -> not offered
        # the run counts as the last snapshot, so the schedule is now a full interval away
        st, s = await self.post("/api/settings", {"auto_refresh_hours": 6})
        self.assertGreater(datetime.fromisoformat(s["auto"]["next_run"]), datetime.now().astimezone() + timedelta(hours=5, minutes=55))

    async def test_generate_with_everything_off_is_a_clear_400(self):
        self.rt.prefs = {"files": {k: {"enabled": False} for k in settings.KINDS}}   # e.g. edited by hand
        st, resp = await self.post("/api/generate", {})
        self.assertEqual(st, 400); self.assertIn("at least one file", resp["error"])

    async def test_generate_keeps_the_old_one_shot_masking_override(self):
        st, s = await self.post("/api/generate", {"redaction": "strict"})
        self.assertEqual((st, s["latest"]["redaction"]), (200, "strict"))
        st, resp = await self.post("/api/generate", {"redaction": "extreme"})
        self.assertEqual(st, 400)

    async def test_page_serves_and_has_the_new_controls(self):
        html = await (await self.client.get("/")).text()
        for needle in ('id="kinds"', 'id="histkeep"', 'id="auto"', 'id="xloc"', 'id="saveauto"', 'id="savefiles"'):
            self.assertIn(needle, html)

class DockerEnvTests(unittest.TestCase):
    def test_env_loading(self):
        env = {"MDI_MODE": "docker", "HA_URL": "https://ha.example:8123/", "HA_TOKEN": "tok",
               "OUTPUT_DIR": "/tmp", "EXTRA_OUTPUT_DIRS": "NAS Backup=/tmp", "REDACTION": "strict",
               "KEEP_HISTORY": "true", "HISTORY_KEEP": "5", "LOG_LINES": "9999", "SAVE_SUBFOLDER": "", "RUN_MODE": "once",
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
        self.assertEqual(rt.options["history_keep"], 5)
        self.assertEqual(rt.options["save_subfolder"], "")
        self.assertTrue(rt.run_once)

if __name__ == "__main__":
    unittest.main(verbosity=2)
