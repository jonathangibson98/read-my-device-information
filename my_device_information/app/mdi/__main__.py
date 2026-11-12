"""Entry point:  python -m mdi            (web page + optional schedule)
                python -m mdi once       (create the files once and exit)"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiohttp import web

from . import NAME, VERSION
from .exporter import ExportManager
from .settings import load
from .web import build_app

_LOGGER = logging.getLogger("mdi")


async def _schedule(mgr: ExportManager, hours: int) -> None:
    await asyncio.sleep(30)  # let Home Assistant finish starting
    while True:
        await mgr.scheduled()
        await asyncio.sleep(hours * 3600)


async def _serve(rt, mgr: ExportManager) -> None:
    app = build_app(rt, mgr)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, rt.bind, rt.port).start()
    _LOGGER.info("%s %s is listening on %s:%s (%s mode)", NAME, VERSION, rt.bind, rt.port, rt.mode)
    hours = rt.options["auto_refresh_hours"]
    if hours > 0:
        _LOGGER.info("Refreshing automatically every %s hour(s)", hours)
        asyncio.create_task(_schedule(mgr, hours))
    await asyncio.Event().wait()


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
