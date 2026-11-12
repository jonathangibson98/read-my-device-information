"""Talk to Home Assistant (WebSocket + REST) and, in the add-on, to the Supervisor."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import aiohttp


class HAError(Exception):
    """Something went wrong talking to Home Assistant."""


class WSConn:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws
        self._id = 0

    async def command(self, type_: str, timeout: float = 90, **payload: Any) -> Any:
        self._id += 1
        mid = self._id
        await self._ws.send_json({"id": mid, "type": type_, **payload})
        async with asyncio.timeout(timeout):
            while True:
                msg = await self._ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("id") == mid and data.get("type") == "result":
                        if data.get("success"):
                            return data.get("result")
                        err = data.get("error") or {}
                        raise HAError(f"{type_}: {err.get('code')} {err.get('message')}")
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    raise HAError("connection closed while waiting for " + type_)


class HAClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        rest_base: str,
        ws_url: str,
        token: str,
        supervisor_base: str | None = None,
        supervisor_token: str | None = None,
    ) -> None:
        self._session = session
        self.rest_base = rest_base
        self.ws_url = ws_url
        self.token = token
        self.supervisor_base = supervisor_base
        self.supervisor_token = supervisor_token

    @property
    def has_supervisor(self) -> bool:
        return bool(self.supervisor_base and self.supervisor_token)

    @asynccontextmanager
    async def websocket(self) -> AsyncIterator[WSConn]:
        if not self.token:
            raise HAError(
                "No access token. Set HA_TOKEN to a long-lived access token "
                "(Profile → Security → Long-lived access tokens)."
            )
        try:
            async with self._session.ws_connect(
                self.ws_url, max_msg_size=0, heartbeat=30
            ) as ws:
                first = await ws.receive_json()
                if first.get("type") != "auth_required":
                    raise HAError(f"Unexpected handshake: {first.get('type')}")
                await ws.send_json({"type": "auth", "access_token": self.token})
                reply = await ws.receive_json()
                if reply.get("type") != "auth_ok":
                    raise HAError("Home Assistant rejected the access token.")
                yield WSConn(ws)
        except aiohttp.ClientError as err:
            raise HAError(f"Cannot reach Home Assistant at {self.ws_url}: {err}") from err

    async def get_text(self, path: str) -> str:
        """GET a Home Assistant REST path such as /api/error_log."""
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with self._session.get(self.rest_base + path, headers=headers) as resp:
                if resp.status != 200:
                    raise HAError(f"GET {path} returned HTTP {resp.status}")
                return await resp.text()
        except aiohttp.ClientError as err:
            raise HAError(f"GET {path}: {err}") from err

    async def call_service(self, domain: str, service: str, data: dict[str, Any]) -> None:
        """POST /api/services/<domain>/<service>."""
        if not self.token:
            raise HAError("No access token.")
        headers = {"Authorization": f"Bearer {self.token}"}
        path = f"/api/services/{domain}/{service}"
        try:
            async with self._session.post(self.rest_base + path, json=data, headers=headers) as resp:
                if resp.status not in (200, 201):
                    raise HAError(f"{domain}.{service} returned HTTP {resp.status}")
        except aiohttp.ClientError as err:
            raise HAError(f"{domain}.{service}: {err}") from err

    async def supervisor(self, path: str) -> Any:
        """GET a Supervisor API path; returns its 'data' payload."""
        if not self.has_supervisor:
            raise HAError("Supervisor not available")
        headers = {"Authorization": f"Bearer {self.supervisor_token}"}
        try:
            async with self._session.get(self.supervisor_base + path, headers=headers) as resp:
                if resp.status != 200:
                    raise HAError(f"Supervisor {path} returned HTTP {resp.status}")
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise HAError(f"Supervisor {path}: {err}") from err
        return body.get("data") if isinstance(body, dict) else body

    async def supervisor_text(self, path: str) -> str:
        headers = {"Authorization": f"Bearer {self.supervisor_token}"}
        try:
            async with self._session.get(self.supervisor_base + path, headers=headers) as resp:
                if resp.status != 200:
                    raise HAError(f"Supervisor {path} returned HTTP {resp.status}")
                return await resp.text()
        except aiohttp.ClientError as err:
            raise HAError(f"Supervisor {path}: {err}") from err
