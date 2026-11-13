"""Tests for keeping devices usable while the Oasis cloud is unreachable."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError
from freezegun.api import FrozenDateTimeFactory
import pytest

from custom_components.oasis_mini.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .common import MockConfigEntry, async_fire_time_changed
from .conftest import SERIAL_NUMBER

HOST = "10.0.0.5"
CACHED_DEVICE = {
    "serial_number": SERIAL_NUMBER,
    "name": "Oasis",
    "model": {"name": "Oasis Mini"},
    "ip": HOST,
}


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the sensor platform to check entity availability."""
    return [Platform.SENSOR]


@pytest.fixture(autouse=True)
def no_http() -> Any:
    """Don't poll the device over HTTP; these tests are about the cloud."""
    with patch(
        "custom_components.oasis_mini.coordinator.OasisDeviceCoordinator._async_http_poll"
    ):
        yield


def cache_key(entry: MockConfigEntry) -> str:
    """Return the storage key of an entry's cached device list."""
    return f"{DOMAIN}.{entry.entry_id}.devices"


def connection_state(hass: HomeAssistant) -> str:
    """Return the state of the device's connection sensor."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{SERIAL_NUMBER}-connection"
    )
    assert entity_id
    state = hass.states.get(entity_id)
    assert state
    return state.state


async def setup_entry(
    hass: HomeAssistant, entry: MockConfigEntry, platforms: list[Platform]
) -> None:
    """Set up the config entry."""
    entry.add_to_hass(hass)
    with patch("custom_components.oasis_mini.PLATFORMS", platforms):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_device_list_cached(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    mock_cloud_client: MagicMock,
    init_integration: MockConfigEntry,
) -> None:
    """The device list from the cloud is saved for use during an outage."""
    mock_cloud_client.async_get_devices.return_value[0]["ip"] = HOST
    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    await hass.async_stop()

    assert hass_storage[cache_key(init_integration)]["data"] == {
        "devices": [CACHED_DEVICE]
    }


async def test_cloud_outage_keeps_devices(
    hass: HomeAssistant,
    mock_cloud_client: MagicMock,
    mock_mqtt_client: MagicMock,
    init_integration: MockConfigEntry,
) -> None:
    """Losing the cloud keeps devices and their entities available."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    mock_cloud_client.async_get_devices.side_effect = ClientError("outage")
    mock_cloud_client.async_get_playlists.reset_mock()

    for _ in range(4):
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert not coordinator.cloud_available
    assert coordinator.data == [device]
    assert connection_state(hass) != STATE_UNAVAILABLE
    # Cloud-only extras are skipped rather than failing every refresh
    mock_cloud_client.async_get_playlists.assert_not_called()

    mock_cloud_client.async_get_devices.side_effect = None
    mock_mqtt_client.retry_now.reset_mock()
    await coordinator.async_refresh()
    assert coordinator.cloud_available
    # MQTT doesn't wait out its backoff once the cloud answers again
    mock_mqtt_client.retry_now.assert_called_once()


async def test_setup_during_outage_uses_cache(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    mock_config_entry: MockConfigEntry,
    mock_cloud_client: MagicMock,
    mock_mqtt_client: MagicMock,
    platforms: list[Platform],
) -> None:
    """Starting during an outage uses the cached devices and their addresses."""
    hass_storage[cache_key(mock_config_entry)] = {
        "version": 1,
        "key": cache_key(mock_config_entry),
        "data": {"devices": [CACHED_DEVICE]},
    }
    mock_cloud_client.async_get_user.side_effect = ClientError("outage")
    mock_cloud_client.async_get_devices.side_effect = ClientError("outage")

    await setup_entry(hass, mock_config_entry, platforms)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    coordinator = mock_config_entry.runtime_data
    assert [d.serial_number for d in coordinator.data] == [SERIAL_NUMBER]
    assert coordinator.http_clients[SERIAL_NUMBER].host == HOST
    assert connection_state(hass) != STATE_UNAVAILABLE


async def test_setup_during_outage_without_cache_retries(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_cloud_client: MagicMock,
    mock_mqtt_client: MagicMock,
    platforms: list[Platform],
) -> None:
    """Without a cached device list there's nothing to fall back on yet."""
    mock_cloud_client.async_get_user.side_effect = ClientError("outage")

    await setup_entry(hass, mock_config_entry, platforms)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_remove_entry_deletes_cache(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    init_integration: MockConfigEntry,
    mock_cloud_client: MagicMock,
) -> None:
    """Removing the integration deletes its cached device list."""
    mock_cloud_client.async_logout = AsyncMock()
    hass_storage[cache_key(init_integration)] = {
        "version": 1,
        "key": cache_key(init_integration),
        "data": {"devices": [CACHED_DEVICE]},
    }

    await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()

    assert cache_key(init_integration) not in hass_storage


async def test_pending_cache_save_does_not_outlive_removal(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_cloud_client: MagicMock,
) -> None:
    """A cache change still waiting to be saved can't recreate it after removal."""
    mock_cloud_client.async_logout = AsyncMock()
    mock_cloud_client.async_get_devices.return_value[0]["ip"] = "10.0.0.42"
    await init_integration.runtime_data.async_refresh()

    await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert cache_key(init_integration) not in hass_storage
