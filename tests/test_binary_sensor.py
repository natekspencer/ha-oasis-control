"""Tests for the Oasis binary sensors."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest

from custom_components.oasis_mini.binary_sensor import (
    DESCRIPTORS,
    OasisDeviceBinarySensorEntity,
)
from custom_components.oasis_mini.const import DOMAIN
from custom_components.oasis_mini.coordinator import (
    LIVENESS_INTERVAL,
    PROBE_AFTER,
    STALE_AFTER,
)
from custom_components.oasis_mini.pyoasiscontrol import OasisBleClient, OasisMqttClient
from homeassistant.const import STATE_OFF, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util

from .common import MockConfigEntry, async_fire_time_changed
from .conftest import SERIAL_NUMBER

REMOTE_ACCESS = next(d for d in DESCRIPTORS if d.key == "remote_access")


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the binary sensor platform."""
    return [Platform.BINARY_SENSOR]


@pytest.fixture(autouse=True)
def mqtt_publishing(mock_mqtt_client: MagicMock) -> None:
    """Report the device over MQTT whenever its status was last updated."""
    mock_mqtt_client.last_seen.side_effect = lambda device: device.last_updated


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


async def test_disconnected_as_soon_as_no_transport_works(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_mqtt_client: MagicMock,
) -> None:
    """Losing every connection turns the sensor off right away, not after 3 minutes."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    assert responding_state(hass) == STATE_ON
    assert coordinator.connection_type(device) == "cloud"

    # The device was just heard from, but nothing reaches it any more
    mock_mqtt_client.last_seen.side_effect = None
    mock_mqtt_client.last_seen.return_value = None
    coordinator._async_check_liveness()
    await hass.async_block_till_done()

    assert coordinator.connection_type(device) == "disconnected"
    assert responding_state(hass) == STATE_OFF


async def test_connection_available_while_refresh_fails(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Reachability is local, so the sensor keeps reporting during reauth."""
    coordinator = init_integration.runtime_data
    coordinator.last_update_success = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert responding_state(hass) == STATE_ON


async def test_remote_access_disabled_by_default(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The remote access binary sensor is a diagnostic entity that starts disabled."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{SERIAL_NUMBER}-remote_access"
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
        (timedelta(seconds=5), None, None, True),
        # ...even if BLE reported it inactive before that
        (timedelta(seconds=5), "inactive", timedelta(seconds=30), True),
        # A newer BLE report wins over an earlier MQTT message
        (timedelta(seconds=30), "inactive", timedelta(seconds=5), False),
        # Without MQTT, BLE's report is used, or it's unknown
        (None, "inactive", timedelta(seconds=5), False),
        (None, "active", timedelta(seconds=5), True),
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
    expected: bool | None,
) -> None:
    """The newest of an MQTT message and BLE's report decides the state."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    now = dt_util.utcnow()
    mock_mqtt_client.last_seen.side_effect = None
    mock_mqtt_client.last_seen.return_value = now - seen_ago if seen_ago else None
    device.remote_access = reported
    device.remote_access_updated = now - reported_ago if reported_ago else None

    entity = OasisDeviceBinarySensorEntity(coordinator, device, REMOTE_ACCESS)

    assert entity.is_on is expected


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
    entity = OasisDeviceBinarySensorEntity(coordinator, device, REMOTE_ACCESS)

    await mqtt._handle_status_message(
        SimpleNamespace(topic=f"{SERIAL_NUMBER}/STATUS/OASIS_STATUS", payload=b"4")
    )
    assert entity.is_on is True

    ble = OasisBleClient("AA:AA:AA:AA:AA:AA", lambda: None)
    ble.attach_device(device)
    ble._process_message({"command": "WIFISTATUS_PUSH", "status": "1", "mqtt": "0"})

    assert mqtt.is_connected
    assert entity.is_on is False
