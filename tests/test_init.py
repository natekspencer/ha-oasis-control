"""Tests for setting up the Oasis integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from custom_components.oasis_mini.const import DOMAIN
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .common import MockConfigEntry
from .conftest import SERIAL_NUMBER


async def test_retired_last_updated_sensor_removed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_cloud_client: MagicMock,
    mock_mqtt_client: MagicMock,
) -> None:
    """The retired Last updated sensor is removed from the registry on setup."""
    mock_config_entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    retired = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{SERIAL_NUMBER}-last_updated",
        config_entry=mock_config_entry,
    )
    kept = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{SERIAL_NUMBER}-drawing_progress",
        config_entry=mock_config_entry,
    )

    with patch("custom_components.oasis_mini.PLATFORMS", [Platform.SENSOR]):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert entity_registry.async_get(retired.entity_id) is None
    assert entity_registry.async_get(kept.entity_id) is not None
