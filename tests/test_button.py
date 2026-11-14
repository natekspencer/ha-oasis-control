"""Tests for the Oasis buttons."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.oasis_mini.button import (
    ENABLE_HTTP_DESCRIPTOR,
    OasisDeviceEnableHttpButtonEntity,
)
from custom_components.oasis_mini.pyoasiscontrol import OasisBleClient
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .common import MockConfigEntry
from .conftest import SERIAL_NUMBER

ADDRESS = "AA:AA:AA:AA:AA:AA"


def ble_client(*, connected: bool = True, http_mode: bool | None = True) -> MagicMock:
    """Return a mock BLE client for the test device."""
    client = MagicMock(spec=OasisBleClient)
    client.address = ADDRESS
    client.serial_number = SERIAL_NUMBER
    client.is_connected = connected
    client.async_enable_http_mode = AsyncMock(return_value=http_mode)
    return client


async def test_enable_http_button_needs_bluetooth(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The button is only available while connected over Bluetooth."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    entity = OasisDeviceEnableHttpButtonEntity(
        coordinator, device, ENABLE_HTTP_DESCRIPTOR
    )
    assert not entity.available

    coordinator.ble_clients[ADDRESS] = ble_client()
    assert entity.available

    coordinator.ble_clients[ADDRESS].is_connected = False
    assert not entity.available


async def test_enable_http_mode_rechecks_http(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Enabling HTTP mode checks the local connection right away."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    client = ble_client()
    coordinator.ble_clients[ADDRESS] = client
    refresh = MagicMock()
    coordinator._http_refresh[SERIAL_NUMBER] = refresh

    await OasisDeviceEnableHttpButtonEntity(
        coordinator, device, ENABLE_HTTP_DESCRIPTOR
    ).async_press()

    client.async_enable_http_mode.assert_awaited_once()
    refresh.set.assert_called_once()


@pytest.mark.parametrize(
    ("connected", "http_mode"), [(False, True), (True, False)], ids=["no_ble", "off"]
)
async def test_enable_http_mode_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    connected: bool,
    http_mode: bool | None,
) -> None:
    """Without Bluetooth, or if the table keeps HTTP off, the press fails clearly."""
    coordinator = init_integration.runtime_data
    coordinator.ble_clients[ADDRESS] = ble_client(
        connected=connected, http_mode=http_mode
    )

    with pytest.raises(HomeAssistantError):
        await coordinator.async_enable_http_mode(coordinator.data[0])


async def test_enable_http_button_available_during_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Bluetooth is local, so the button works while the cloud refresh fails."""
    coordinator = init_integration.runtime_data
    entity = OasisDeviceEnableHttpButtonEntity(
        coordinator, coordinator.data[0], ENABLE_HTTP_DESCRIPTOR
    )
    coordinator.ble_clients[ADDRESS] = ble_client()
    coordinator.last_update_success = False

    assert entity.available
