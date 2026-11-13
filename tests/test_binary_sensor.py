"""Tests for the Oasis binary sensors."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest

from custom_components.oasis_mini.const import DOMAIN
from custom_components.oasis_mini.coordinator import (
    LIVENESS_INTERVAL,
    PROBE_AFTER,
    STALE_AFTER,
)
from homeassistant.const import STATE_OFF, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .common import MockConfigEntry, async_fire_time_changed
from .conftest import SERIAL_NUMBER


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the binary sensor platform."""
    return [Platform.BINARY_SENSOR]


def responding_state(hass: HomeAssistant) -> str:
    """Return the state of the device's Responding sensor."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{SERIAL_NUMBER}-responding"
    )
    assert entity_id
    state = hass.states.get(entity_id)
    assert state
    return state.state


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    """Move time forward in liveness-check steps."""
    elapsed = timedelta()
    while elapsed < delta:
        freezer.tick(LIVENESS_INTERVAL)
        elapsed += LIVENESS_INTERVAL
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_responding_until_device_goes_quiet(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
) -> None:
    """The sensor turns off once the device hasn't been heard from for a while."""
    device = init_integration.runtime_data.data[0]
    assert responding_state(hass) == STATE_ON

    await advance(hass, freezer, STALE_AFTER)
    assert responding_state(hass) == STATE_OFF

    # Any status from the device, even an unchanged one, means it's responding
    device.update_from_status_dict({"status_code": device.status_code})
    await advance(hass, freezer, LIVENESS_INTERVAL)
    assert responding_state(hass) == STATE_ON


async def test_regular_updates_do_not_change_state(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
) -> None:
    """A device reporting progress while drawing doesn't produce state changes."""
    device = init_integration.runtime_data.data[0]
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{SERIAL_NUMBER}-responding"
    )
    before = hass.states.get(entity_id)

    for progress in range(10):
        device.update_from_status_dict({"progress": progress})
        await advance(hass, freezer, LIVENESS_INTERVAL)

    after = hass.states.get(entity_id)
    assert after.state == STATE_ON
    assert after.last_changed == before.last_changed


@pytest.mark.parametrize("device_status", [{}])
async def test_unreached_device_shows_not_responding(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A device that hasn't answered since startup still gets the sensor, off."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    assert not device.is_initialized
    device.last_updated = None  # The mocked MQTT client reports an empty status

    coordinator._async_check_liveness()
    await hass.async_block_till_done()

    assert responding_state(hass) == STATE_OFF


async def test_quiet_mqtt_device_asked_for_status(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_mqtt_client: MagicMock,
) -> None:
    """An idle device on MQTT, which sends nothing, is asked for its status."""
    device = init_integration.runtime_data.data[0]
    mqtt = mock_mqtt_client
    mqtt.is_connected = True
    mqtt.async_get_status = AsyncMock()
    assert device.client is mqtt

    await advance(hass, freezer, PROBE_AFTER - LIVENESS_INTERVAL)
    mqtt.async_get_status.assert_not_awaited()

    await advance(hass, freezer, LIVENESS_INTERVAL)
    mqtt.async_get_status.assert_awaited_with(device)


async def test_polling_transport_not_asked_for_status(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
) -> None:
    """HTTP and BLE poll on their own, so they aren't sent extra requests."""
    device = init_integration.runtime_data.data[0]
    client = MagicMock()
    client.async_get_status = AsyncMock()
    device.attach_client(client)

    await advance(hass, freezer, STALE_AFTER)

    client.async_get_status.assert_not_awaited()
