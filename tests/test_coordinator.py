"""Tests for the Oasis coordinator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.oasis_mini.pyoasiscontrol import UnauthenticatedError
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.core import HomeAssistant

from .common import MockConfigEntry
from .conftest import SERIAL_NUMBER


async def test_rejected_token_starts_reauth(
    hass: HomeAssistant, init_integration: ConfigEntry, mock_cloud_client: MagicMock
) -> None:
    """Losing cloud access asks the user to log in again."""
    mock_cloud_client.async_get_devices.side_effect = UnauthenticatedError()

    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert list(init_integration.async_get_active_flows(hass, {SOURCE_REAUTH}))


async def test_rejected_playlist_request_starts_reauth(
    hass: HomeAssistant, init_integration: ConfigEntry, mock_cloud_client: MagicMock
) -> None:
    """A rejected playlist request also asks the user to log in again."""
    mock_cloud_client.async_get_playlists.side_effect = UnauthenticatedError()

    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert list(init_integration.async_get_active_flows(hass, {SOURCE_REAUTH}))


async def test_ignored_ble_device_reprobed_when_on_account(
    init_integration: MockConfigEntry,
) -> None:
    """A BLE advertiser ignored as not on the account is probed again once it is."""
    coordinator = init_integration.runtime_data
    coordinator._ble_ignored = {
        "AA:AA:AA:AA:AA:AA": SERIAL_NUMBER,
        "BB:BB:BB:BB:BB:BB": "OM-OTHER-ACCOUNT",
    }

    with patch.object(coordinator, "_async_start_ble_probe") as mock_probe:
        coordinator._async_ble_reprobe_ignored()

    mock_probe.assert_called_once_with("AA:AA:AA:AA:AA:AA")
    assert coordinator._ble_ignored == {"BB:BB:BB:BB:BB:BB": "OM-OTHER-ACCOUNT"}
