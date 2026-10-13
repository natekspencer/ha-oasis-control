"""Fixtures for Oasis Control tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.oasis_mini.const import DOMAIN
from custom_components.oasis_mini.pyoasiscontrol import OasisDevice
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_EMAIL, Platform
from homeassistant.core import HomeAssistant

SERIAL_NUMBER = "OM123456789"
USER_ID = 1234

# Initial device state reported on connection, as received from MQTT
DEVICE_STATUS: dict[str, Any] = {
    "mac_address": "aa:bb:cc:dd:ee:ff",
    "software_version": "2.60",
    "status_code": 4,
    "playlist": [101, 102],
    "playlist_index": 0,
    "progress": 100,
}

# Cloud track details, keyed by track id
TRACKS: dict[int, dict[str, Any]] = {
    101: {"id": 101, "name": "Track One", "path_draw": "track_one"},
    102: {"id": 102, "name": "Track Two", "path_draw": "track_two"},
    103: {
        "id": 103,
        "name": "Track Three",
        "svg_content": {"decrypted": "M0,0L10,10L20,20L30,30"},
    },
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration in all tests."""


@pytest.fixture
def platforms() -> list[Platform]:
    """Platforms to set up; override in a test module to limit setup."""
    return [Platform.IMAGE]


@pytest.fixture
def device_status() -> dict[str, Any]:
    """Initial device status; override to change the state at setup."""
    return dict(DEVICE_STATUS)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for the integration."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(USER_ID),
        data={CONF_EMAIL: "user@example.com", CONF_ACCESS_TOKEN: "token"},
        version=1,
        minor_version=3,
    )


@pytest.fixture
def mock_cloud_client() -> Generator[MagicMock]:
    """Patch the cloud client with a mock returning a single device."""
    client = MagicMock()
    client.async_get_user = AsyncMock(return_value={"id": USER_ID})
    client.async_get_devices = AsyncMock(
        return_value=[
            {
                "serial_number": SERIAL_NUMBER,
                "name": "Oasis",
                "model": {"name": "Oasis Mini"},
            }
        ]
    )
    client.async_get_playlists = AsyncMock(return_value=[])
    client.async_get_track_info = AsyncMock(side_effect=TRACKS.get)
    client.async_get_mqtt_token = AsyncMock(return_value="mqtt-token")
    client.async_close = AsyncMock()

    with patch("custom_components.oasis_mini.create_client", return_value=client):
        yield client


@pytest.fixture
def mock_mqtt_client(device_status: dict[str, Any]) -> Generator[MagicMock]:
    """Patch the MQTT client; registered devices receive `device_status`."""
    client = MagicMock()
    client.is_running = False
    client.async_close = AsyncMock()
    client.async_get_status = AsyncMock()

    def _register_devices(devices: list[OasisDevice]) -> None:
        for device in devices:
            if not device.client:
                device.attach_client(client)
                device.update_from_status_dict(device_status)

    client.register_devices.side_effect = _register_devices

    with patch(
        "custom_components.oasis_mini.coordinator.OasisMqttClient",
        return_value=client,
    ):
        yield client


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_cloud_client: MagicMock,
    mock_mqtt_client: MagicMock,
    platforms: list[Platform],
) -> MockConfigEntry:
    """Set up the integration with mocked clients."""
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.oasis_mini.PLATFORMS", platforms):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    return mock_config_entry


@pytest.fixture
def device(init_integration: MockConfigEntry) -> OasisDevice:
    """Return the set up Oasis device."""
    return init_integration.runtime_data.data[0]
