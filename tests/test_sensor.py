"""Tests for the Oasis sensors."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.oasis_mini.pyoasiscontrol import OasisHttpClient
from custom_components.oasis_mini.sensor import DESCRIPTORS, OasisDeviceSensorEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .common import MockConfigEntry


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the sensor platform."""
    return [Platform.SENSOR]


async def test_connection_type_available_during_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """How the device is reached is local, so it's reported during reauth too."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    http = MagicMock(spec=OasisHttpClient)
    http.is_connected = True
    device.attach_client(http)
    coordinator.last_update_success = False

    entity = OasisDeviceSensorEntity(
        coordinator,
        device,
        next(d for d in DESCRIPTORS if d.key == "connection"),
    )

    assert entity.available
    assert entity.native_value == "local"
