"""Entry point:  python -m mdi            (web page + optional schedule)
                python -m mdi once       (create the files once and exit)"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime

from aiohttp import web

from . import NAME, VERSION
from .exporter import ExportManager
from .settings import Runtime, load
from .web import build_app

_LOGGER = logging.getLogger("mdi")


POLL_SECONDS = 30


async def _schedule(rt: Runtime, mgr: ExportManager) -> None:
    """Re-reads the interval every few seconds, so changes on the web page apply at once."""
    await asyncio.sleep(30)  # let Home Assistant finish starting
    announced: int | None = None
    while True:
        try:
            hours = rt.auto_refresh_hours()
            if hours != announced:
                _LOGGER.info("Automatic updates: %s", f"every {hours} hour(s)" if hours else "off")
                announced = hours
            if hours > 0:
                due = mgr.next_due(hours)
                if due is not None and datetime.now().astimezone() >= due:
                    await mgr.scheduled()
        except Exception:  # noqa: BLE001  (never let the schedule die)
            _LOGGER.exception("Scheduler error")
        await asyncio.sleep(POLL_SECONDS)


async def _serve(rt: Runtime, mgr: ExportManager) -> None:
    app = build_app(rt, mgr)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, rt.bind, rt.port).start()
    _LOGGER.info("%s %s is listening on %s:%s (%s mode)", NAME, VERSION, rt.bind, rt.port, rt.mode)
    scheduler = asyncio.create_task(_schedule(rt, mgr))  # keep a reference so it is not collected
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.cancel()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rt = load()
    mgr = ExportManager(rt)
    if rt.run_once or "once" in sys.argv[1:]:
        try:
            info = asyncio.run(mgr.generate())
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Export failed: %s", err)
            return 1
        for kind, f in info["files"].items():
            _LOGGER.info("Saved %s (~%s tokens)", f["path"], f"{f['tokens']:,}")
        return 0
    try:
        asyncio.run(_serve(rt, mgr))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
