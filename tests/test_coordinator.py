"""Tests for the Oasis coordinator."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.oasis_mini.pyoasiscontrol import (
    OasisBleClient,
    OasisDevice,
    UnauthenticatedError,
)
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


def _ble_client(**attrs: object) -> MagicMock:
    """Return a mock BLE client for the test device."""
    client = MagicMock(spec=OasisBleClient)
    client.address = "AA:AA:AA:AA:AA:AA"
    client.serial_number = SERIAL_NUMBER
    client.is_connected = True
    client.legacy_firmware = False
    client.async_get_status = AsyncMock()
    for name, value in attrs.items():
        setattr(client, name, value)
    return client


async def test_ble_poll_requests_status_when_active(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A device using BLE is asked for its status, as settings aren't pushed."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    client = _ble_client()
    device.attach_client(client)

    coordinator._async_ble_poll()
    await hass.async_block_till_done()

    client.async_get_status.assert_awaited_once_with(device)


@pytest.mark.parametrize(
    ("attrs", "active"),
    [
        ({"is_connected": False}, True),
        ({"legacy_firmware": True}, True),
        ({}, False),
    ],
    ids=["disconnected", "legacy_firmware", "not_active"],
)
async def test_ble_poll_skipped(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    attrs: dict[str, object],
    active: bool,
) -> None:
    """BLE isn't polled when it can't answer or another transport is in use."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    client = _ble_client(**attrs)
    coordinator.ble_clients[client.address] = client
    if active:
        device.attach_client(client)

    coordinator._async_ble_poll()
    await hass.async_block_till_done()

    client.async_get_status.assert_not_awaited()


async def test_ble_poll_does_not_overlap(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A slow BLE poll isn't repeated until it has finished."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    reply = asyncio.Event()
    client = _ble_client()

    async def _wait_for_reply(_device: object) -> None:
        await reply.wait()

    client.async_get_status.side_effect = _wait_for_reply
    device.attach_client(client)

    coordinator._async_ble_poll()
    await asyncio.sleep(0)
    coordinator._async_ble_poll()
    reply.set()
    await hass.async_block_till_done()
    coordinator._async_ble_poll()
    await hass.async_block_till_done()

    assert client.async_get_status.await_count == 2


async def test_removed_device_releases_bluetooth(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_cloud_client: MagicMock,
) -> None:
    """A device removed from the account has its BLE connection closed."""
    coordinator = init_integration.runtime_data
    client = _ble_client()
    client.async_close = AsyncMock()
    coordinator.ble_clients[client.address] = client
    other = _ble_client(address="BB:BB:BB:BB:BB:BB", serial_number="OM-OTHER")
    other.async_close = AsyncMock()
    coordinator.ble_clients[other.address] = other

    mock_cloud_client.async_get_devices.return_value = []
    await coordinator.async_refresh()

    client.async_close.assert_awaited_once()
    assert coordinator.ble_clients == {other.address: other}
    other.async_close.assert_not_awaited()
    assert coordinator._ble_ignored == {client.address: SERIAL_NUMBER}


async def test_failed_refresh_stops_polling_discarded_devices(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_cloud_client: MagicMock,
    mock_mqtt_client: MagicMock,
) -> None:
    """Devices from a refresh that fails aren't left polling over HTTP."""
    coordinator = init_integration.runtime_data
    mock_cloud_client.async_get_devices.return_value = [
        *mock_cloud_client.async_get_devices.return_value,
        {"serial_number": "OM-NEW", "ip": "10.0.0.8", "model": {"name": "Oasis"}},
    ]
    mock_mqtt_client.register_devices.side_effect = RuntimeError("boom")

    with patch.object(coordinator, "_async_http_poll"):
        await coordinator.async_refresh()

    assert [d.serial_number for d in coordinator.data] == [SERIAL_NUMBER]
    assert "OM-NEW" not in coordinator.http_clients
    # Registered with MQTT before the failure, so it's unregistered again
    (unregistered,) = mock_mqtt_client.unregister_device.call_args.args
    assert unregistered.serial_number == "OM-NEW"


async def test_cancelled_ble_probe_closes_client(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Cancelling a probe before the device is identified disconnects it."""
    coordinator = init_integration.runtime_data
    client = _ble_client(serial_number=None)
    client.async_close = AsyncMock()

    with (
        patch(
            "custom_components.oasis_mini.coordinator.OasisBleClient",
            return_value=client,
        ),
        patch.object(
            coordinator,
            "_async_ble_retry",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch("homeassistant.components.bluetooth.async_ble_device_from_address"),
        pytest.raises(asyncio.CancelledError),
    ):
        await coordinator._async_probe_ble(client.address)

    client.async_close.assert_awaited_once()
    assert client.address not in coordinator.ble_clients


async def test_failed_refresh_keeps_published_new_device(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_cloud_client: MagicMock,
    mock_mqtt_client: MagicMock,
) -> None:
    """A new device whose entities were created mid-refresh survives a failure."""
    coordinator = init_integration.runtime_data
    mock_cloud_client.async_get_devices.return_value = [
        *mock_cloud_client.async_get_devices.return_value,
        {"serial_number": "OM-NEW", "ip": "10.0.0.8", "model": {"name": "Oasis"}},
    ]

    def _initialize_then_fail(devices: list[OasisDevice]) -> None:
        # The new device initializes (and is published) before the refresh fails
        for device in devices:
            if device.serial_number == "OM-NEW":
                device.update_from_status_dict(
                    {"mac_address": "11:22:33:44:55:66", "software_version": "3.54"}
                )
        raise RuntimeError("boom")

    mock_mqtt_client.register_devices.side_effect = _initialize_then_fail

    with patch.object(coordinator, "_async_http_poll"):
        await coordinator.async_refresh()

    assert [d.serial_number for d in coordinator.data] == [SERIAL_NUMBER, "OM-NEW"]
    assert "OM-NEW" in coordinator.http_clients


@pytest.mark.parametrize(("legacy", "refreshed"), [(True, True), (False, False)])
async def test_ble_sync_refreshes_legacy_firmware_over_mqtt(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_mqtt_client: MagicMock,
    legacy: bool,
    refreshed: bool,
) -> None:
    """Legacy firmware found by a BLE sync gets its status from MQTT right away."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    client = _ble_client()
    client.legacy_firmware = False

    async def _get_all(_device: OasisDevice) -> None:
        # Detected from the BLE replies, after MQTT may already have connected
        client.legacy_firmware = legacy

    client.async_get_all = AsyncMock(side_effect=_get_all)
    mock_mqtt_client.async_get_all = AsyncMock()

    await coordinator._async_ble_sync(client, device)

    assert mock_mqtt_client.async_get_all.await_count == int(refreshed)


@pytest.mark.parametrize("device_status", [{}])
async def test_ble_poll_retries_sync_until_initialized(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A missed identity reply is retried by the poll, without reconnecting."""
    coordinator = init_integration.runtime_data
    device = coordinator.data[0]
    client = _ble_client()
    client.async_connect = AsyncMock()

    async def _get_all(_device: OasisDevice) -> None:
        device.update_from_status_dict(
            {"mac_address": "aa:bb:cc:dd:ee:ff", "software_version": "3.54"}
        )

    client.async_get_all = AsyncMock(side_effect=_get_all)
    device.attach_client(client)
    assert not device.is_initialized

    coordinator._async_ble_poll()
    await hass.async_block_till_done()
    assert device.is_initialized
    client.async_get_status.assert_not_awaited()

    # Once initialized, the poll is back to plain status requests
    coordinator._async_ble_poll()
    await hass.async_block_till_done()
    client.async_get_status.assert_awaited_once_with(device)
    assert client.async_get_all.await_count == 1
    client.async_connect.assert_not_awaited()


async def test_probe_completing_during_shutdown_not_kept(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A probe that identifies its device after shutdown began disconnects it."""
    coordinator = init_integration.runtime_data
    client = _ble_client(serial_number=SERIAL_NUMBER)
    client.async_close = AsyncMock()
    client.async_get_serial_number = AsyncMock(return_value=SERIAL_NUMBER)
    await coordinator.async_close()

    with (
        patch(
            "custom_components.oasis_mini.coordinator.OasisBleClient",
            return_value=client,
        ),
        patch.object(coordinator, "_async_ble_retry", AsyncMock(return_value=True)),
        patch("homeassistant.components.bluetooth.async_ble_device_from_address"),
    ):
        await coordinator._async_probe_ble(client.address)

    client.async_close.assert_awaited_once()
    assert coordinator.ble_clients == {}
