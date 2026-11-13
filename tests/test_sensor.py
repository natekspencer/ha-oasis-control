"""Tests for the Oasis sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.oasis_mini.const import DOMAIN
from custom_components.oasis_mini.sensor import (
    REMOTE_ACCESS_DESCRIPTOR,
    OasisDeviceRemoteAccessSensorEntity,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

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
    ("publishing", "reported", "expected"),
    [
        (True, None, "active"),
        (True, "inactive", "active"),
        (False, "inactive", "inactive"),
        (False, "disabled", "disabled"),
        (False, None, None),
    ],
)
async def test_remote_access_value(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_mqtt_client: MagicMock,
    publishing: bool,
    reported: str | None,
    expected: str | None,
) -> None:
    """Publishing over MQTT shows it's active; otherwise BLE's report is used."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    mock_mqtt_client.is_device_reachable.return_value = publishing
    device.remote_access = reported

    entity = OasisDeviceRemoteAccessSensorEntity(
        coordinator, device, REMOTE_ACCESS_DESCRIPTOR
    )

    assert entity.native_value == expected
