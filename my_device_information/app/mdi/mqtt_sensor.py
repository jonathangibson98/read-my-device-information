"""Publish the Full edition as an MQTT sensor through Home Assistant's mqtt.publish service.

The sensor's state is the time of the last snapshot. Everything in Full.txt is stored in one
attribute called ``content``. No MQTT client library or broker password is needed: Home Assistant
does the publishing, so the MQTT integration only has to be set up in Home Assistant.
"""

from __future__ import annotations

import json
from typing import Any

from . import NAME, SLUG, VERSION
from .hass_client import HAClient, HAError

DISCOVERY_PREFIX = "homeassistant"
UNIQUE_ID = f"{SLUG}_full"
CONFIG_TOPIC = f"{DISCOVERY_PREFIX}/sensor/{SLUG}/full/config"
STATE_TOPIC = f"{SLUG}/full/state"
ATTRIBUTES_TOPIC = f"{SLUG}/full/attributes"
# What Home Assistant names the entity: device name + entity name.
ENTITY_ID = "sensor.my_device_information_full"
ATTRIBUTE = "content"


def discovery_payload() -> dict[str, Any]:
    return {
        "name": "Full",
        "unique_id": UNIQUE_ID,
        "state_topic": STATE_TOPIC,
        "json_attributes_topic": ATTRIBUTES_TOPIC,
        "device_class": "timestamp",
        "icon": "mdi:information-box-outline",
        "device": {
            "identifiers": [SLUG],
            "name": NAME,
            "manufacturer": "My Device Information",
            "sw_version": VERSION,
        },
    }


def attributes_payload(text: str, info: dict[str, Any], generated_at: str) -> dict[str, Any]:
    """Attributes of the sensor. `content` is the whole text of Full.txt."""
    return {
        ATTRIBUTE: text,
        "generated_at": generated_at,
        "bytes": info.get("bytes"),
        "tokens": info.get("tokens"),
        "masking": info.get("redaction"),
        "file": info.get("name"),
    }


async def _publish(client: HAClient, topic: str, payload: str, *, retain: bool = True) -> None:
    try:
        await client.call_service("mqtt", "publish", {
            "topic": topic, "payload": payload, "retain": retain, "qos": 0,
        })
    except HAError as err:
        raise HAError(f"{err}. Is the MQTT integration set up in Home Assistant?") from err


async def publish(client: HAClient, text: str, info: dict[str, Any], generated_at: str) -> None:
    """Create or update the sensor. The config is retained, so it survives restarts."""
    await _publish(client, CONFIG_TOPIC, json.dumps(discovery_payload()))
    await _publish(
        client, ATTRIBUTES_TOPIC,
        json.dumps(attributes_payload(text, info, generated_at), ensure_ascii=False),
    )
    await _publish(client, STATE_TOPIC, generated_at)


async def remove(client: HAClient) -> None:
    """Delete the sensor: an empty retained config removes it, empty retained data clears the rest."""
    await _publish(client, CONFIG_TOPIC, "")
    await _publish(client, ATTRIBUTES_TOPIC, "")
    await _publish(client, STATE_TOPIC, "")
