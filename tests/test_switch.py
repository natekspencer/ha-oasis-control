"""Tests for the Oasis switches."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.oasis_mini.const import CONF_BLUETOOTH_DISABLED
from custom_components.oasis_mini.coordinator import OasisDeviceCoordinator
from custom_components.oasis_mini.pyoasiscontrol import OasisBleClient
from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .common import MockConfigEntry
from .conftest import SERIAL_NUMBER

ADDRESS = "AA:AA:AA:AA:AA:AA"


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the switch platform."""
    return [Platform.SWITCH]


@pytest.fixture(autouse=True)
def mock_bluetooth() -> Generator[MagicMock]:
    """Pretend the Bluetooth integration is loaded, without scanning."""

    def _setup(self: OasisDeviceCoordinator) -> None:
        self.bluetooth_available = True

    with patch.object(
        OasisDeviceCoordinator, "async_setup_bluetooth", _setup
    ) as mock_setup:
        yield mock_setup


def bluetooth_entity_id(hass: HomeAssistant) -> str:
    """Return the entity id of the device's Bluetooth switch."""
    entity_id = er.async_get(hass).async_get_entity_id(
        SWITCH_DOMAIN, "oasis_mini", f"{SERIAL_NUMBER}-bluetooth"
    )
    assert entity_id
    return entity_id


def connected_ble_client() -> MagicMock:
    """Return a mock BLE client connected to the test device."""
    client = MagicMock(spec=OasisBleClient)
    client.address = ADDRESS
    client.serial_number = SERIAL_NUMBER
    client.is_connected = True
    client.async_close = AsyncMock()
    return client


async def test_bluetooth_switch_on_by_default(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Bluetooth is used unless turned off."""
    state = hass.states.get(bluetooth_entity_id(hass))
    assert state
    assert state.state == STATE_ON


@pytest.mark.parametrize("device_status", [{}])
async def test_bluetooth_switch_added_before_initialized(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The switch exists for a device that hasn't been reached yet."""
    assert not init_integration.runtime_data.data[0].is_initialized
    state = hass.states.get(bluetooth_entity_id(hass))
    assert state
    assert state.state == STATE_ON
    # Other switches wait until the device is identified
    assert not er.async_get(hass).async_get_entity_id(
        SWITCH_DOMAIN, "oasis_mini", f"{SERIAL_NUMBER}-auto_clean"
    )


@pytest.mark.parametrize("entry_options", [{CONF_BLUETOOTH_DISABLED: [SERIAL_NUMBER]}])
async def test_bluetooth_switch_restored_off(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A switch turned off stays off across restarts."""
    state = hass.states.get(bluetooth_entity_id(hass))
    assert state
    assert state.state == STATE_OFF


async def test_turn_off_bluetooth_releases_connection(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_mqtt_client: MagicMock,
) -> None:
    """Turning Bluetooth off disconnects and falls back without a reload."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    client = connected_ble_client()
    coordinator.ble_clients[ADDRESS] = client
    device.attach_client(client)

    with patch.object(hass.config_entries, "async_reload") as mock_reload:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: bluetooth_entity_id(hass)},
            blocking=True,
        )

    mock_reload.assert_not_called()
    client.async_close.assert_awaited_once()
    assert ADDRESS not in coordinator.ble_clients
    assert device.client is mock_mqtt_client
    assert init_integration.options[CONF_BLUETOOTH_DISABLED] == [SERIAL_NUMBER]
    assert hass.states.get(bluetooth_entity_id(hass)).state == STATE_OFF


@pytest.mark.parametrize("entry_options", [{CONF_BLUETOOTH_DISABLED: [SERIAL_NUMBER]}])
async def test_turn_on_bluetooth_reconnects(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Turning Bluetooth back on probes the device's advertiser again."""
    coordinator = init_integration.runtime_data
    coordinator._ble_disabled[ADDRESS] = SERIAL_NUMBER

    with patch.object(coordinator, "_async_start_ble_probe") as mock_probe:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: bluetooth_entity_id(hass)},
            blocking=True,
        )

    mock_probe.assert_called_once_with(ADDRESS)
    assert init_integration.options[CONF_BLUETOOTH_DISABLED] == []
    assert hass.states.get(bluetooth_entity_id(hass)).state == STATE_ON


async def test_disabled_device_not_kept_connected(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A probed device whose switch is off is disconnected and not reconnected."""
    coordinator = init_integration.runtime_data
    coordinator.hass.config_entries.async_update_entry(
        init_integration, options={CONF_BLUETOOTH_DISABLED: [SERIAL_NUMBER]}
    )
    client = connected_ble_client()
    client.async_get_serial_number = AsyncMock(return_value=SERIAL_NUMBER)

    with (
        patch(
            "custom_components.oasis_mini.coordinator.OasisBleClient",
            return_value=client,
        ),
        patch.object(coordinator, "_async_ble_retry", AsyncMock(return_value=True)),
        patch("homeassistant.components.bluetooth.async_ble_device_from_address"),
    ):
        await coordinator._async_probe_ble(ADDRESS)

    client.async_close.assert_awaited_once()
    assert ADDRESS not in coordinator.ble_clients
    assert coordinator._ble_disabled == {ADDRESS: SERIAL_NUMBER}


async def test_bluetooth_switch_available_while_refresh_fails(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_cloud_client: MagicMock,
) -> None:
    """Bluetooth is local, so its switch works while the cloud refresh fails."""
    coordinator = init_integration.runtime_data
    coordinator.last_update_success = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    state = hass.states.get(bluetooth_entity_id(hass))
    assert state
    assert state.state == STATE_ON

    with patch.object(coordinator, "async_set_bluetooth_enabled") as mock_set:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: bluetooth_entity_id(hass)},
            blocking=True,
        )
    mock_set.assert_awaited_once()
