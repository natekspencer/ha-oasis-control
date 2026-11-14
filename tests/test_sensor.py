"""Tests for the Oasis sensors."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.oasis_mini.const import DOMAIN
from custom_components.oasis_mini.coordinator import STALE_AFTER
from custom_components.oasis_mini.pyoasiscontrol import OasisBleClient, OasisMqttClient
from custom_components.oasis_mini.sensor import (
    REMOTE_ACCESS_DESCRIPTOR,
    OasisDeviceRemoteAccessSensorEntity,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util

from .common import MockConfigEntry
from .conftest import SERIAL_NUMBER


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the sensor platform."""
    return [Platform.SENSOR]


async def test_remote_access_disabled_by_default(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The remote access sensor is a diagnostic entity that starts disabled."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{SERIAL_NUMBER}-remote_access"
    )
    assert entity_id
    entry = er.async_get(hass).async_get(entity_id)
    assert entry
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category == "diagnostic"


@pytest.mark.parametrize(
    ("seen_ago", "reported", "reported_ago", "expected"),
    [
        # Publishing over MQTT shows it's active
        (timedelta(seconds=5), None, None, "active"),
        # ...even if BLE reported it inactive before that
        (timedelta(seconds=5), "inactive", timedelta(seconds=30), "active"),
        # A newer BLE report wins over an earlier MQTT message
        (timedelta(seconds=30), "inactive", timedelta(seconds=5), "inactive"),
        (timedelta(seconds=30), "disabled", timedelta(seconds=5), "disabled"),
        # Without MQTT, BLE's report is used, or it's unknown
        (None, "inactive", timedelta(seconds=5), "inactive"),
        (None, None, None, None),
        # An MQTT sighting older than the stale limit no longer counts
        (STALE_AFTER + timedelta(seconds=1), None, None, None),
    ],
)
async def test_remote_access_value(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_mqtt_client: MagicMock,
    seen_ago: timedelta | None,
    reported: str | None,
    reported_ago: timedelta | None,
    expected: str | None,
) -> None:
    """The newest of an MQTT message and BLE's report decides the state."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    now = dt_util.utcnow()
    mock_mqtt_client.last_seen.return_value = now - seen_ago if seen_ago else None
    device.remote_access = reported
    device.remote_access_updated = now - reported_ago if reported_ago else None

    entity = OasisDeviceRemoteAccessSensorEntity(
        coordinator, device, REMOTE_ACCESS_DESCRIPTOR
    )

    assert entity.native_value == expected


async def test_ble_report_overrides_mqtt_with_broker_connected(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """BLE reporting lost access overrides an earlier MQTT message, broker still up."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    mqtt = OasisMqttClient()
    mqtt.register_device(device)
    mqtt._connected_event.set()
    coordinator.mqtt_client = mqtt
    entity = OasisDeviceRemoteAccessSensorEntity(
        coordinator, device, REMOTE_ACCESS_DESCRIPTOR
    )

    await mqtt._handle_status_message(
        SimpleNamespace(topic=f"{SERIAL_NUMBER}/STATUS/OASIS_STATUS", payload=b"4")
    )
    assert entity.native_value == "active"

    ble = OasisBleClient("AA:AA:AA:AA:AA:AA", lambda: None)
    ble.attach_device(device)
    ble._process_message({"command": "WIFISTATUS_PUSH", "status": "1", "mqtt": "0"})

    assert mqtt.is_connected
    assert entity.native_value == "inactive"
