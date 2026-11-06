"""Tests for the Oasis coordinator."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.oasis_mini.pyoasiscontrol import UnauthenticatedError
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.core import HomeAssistant


async def test_rejected_token_starts_reauth(
    hass: HomeAssistant, init_integration: ConfigEntry, mock_cloud_client: MagicMock
) -> None:
    """Losing cloud access asks the user to log in again."""
    mock_cloud_client.async_get_devices.side_effect = UnauthenticatedError()

    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert list(init_integration.async_get_active_flows(hass, {SOURCE_REAUTH}))
