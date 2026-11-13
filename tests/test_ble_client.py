"""Tests for the Oasis Bluetooth LE client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.oasis_mini.pyoasiscontrol import OasisDevice
from custom_components.oasis_mini.pyoasiscontrol.clients import ble_client
from custom_components.oasis_mini.pyoasiscontrol.clients.ble_client import (
    OasisBleClient,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"

# Returns the notifications the fake device sends in reply to a command
type Responder = Callable[[dict[str, Any]], list[dict[str, Any]]]


@pytest.fixture(autouse=True)
def no_write_spacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't space writes apart in tests."""
    monkeypatch.setattr(ble_client, "WRITE_MIN_INTERVAL", 0)


@pytest.fixture
def device() -> OasisDevice:
    """Return a device."""
    return OasisDevice(model="Oasis Mini", serial_number="OM1")


@pytest.fixture
def responses() -> dict[str, Responder]:
    """Replies the fake device sends for each command; tests add to this."""
    return {
        "GETMAC": lambda _: [{"command": "GETMAC", "mac": "aa:bb:cc:dd:ee:ff"}],
        "GETSOFTWAREVER": lambda _: [{"command": "GETSOFTWAREVER", "version": "2.60"}],
        "WIFISTATUS": lambda _: [{"command": "WIFISTATUS", "status": "1", "mqtt": "1"}],
    }


@pytest.fixture
def client(device: OasisDevice, responses: dict[str, Responder]) -> OasisBleClient:
    """Return a BLE client attached to the device over a fake connection."""
    client = OasisBleClient(ADDRESS, lambda: None)
    client.attach_device(device)
    device.attach_client(client)

    async def _write(_uuid: str, data: bytes, response: bool) -> None:
        payload = json.loads(data)
        if responder := responses.get(payload["command"]):
            for message in responder(payload):
                _notify(client, message)

    client._client = MagicMock(is_connected=True)
    client._client.write_gatt_char = AsyncMock(side_effect=_write)
    return client


def _notify(client: OasisBleClient, message: dict[str, Any]) -> None:
    """Deliver a notification to the client, as the device would."""
    client._handle_notification(MagicMock(), bytearray(json.dumps(message).encode()))


def _written(client: OasisBleClient) -> list[dict[str, Any]]:
    """Return the commands written to the device."""
    assert client._client is not None
    return [
        json.loads(call.args[1]) for call in client._client.write_gatt_char.mock_calls
    ]


def _playlist_push(command: str, track_ids: list[int]) -> list[dict[str, Any]]:
    """Return the notifications the device sends to report its playlist."""
    return [
        {"command": command, "part": str(i), "job": {"idx": str(i), "id": str(t)}}
        for i, t in enumerate(track_ids)
    ] + [{"command": command, "part": "end"}]


def test_status_notification(client: OasisBleClient, device: OasisDevice) -> None:
    """GETSTATUS fields are mapped onto the device."""
    _notify(
        client,
        {
            "command": "GETSTATUS",
            "st": "4",
            "job": "1",
            "cl": "250",
            "sp": "200",
            "lb": "150",
            "lp": "#FFFFFF",
            "rj": "1",
            "ac": "0",
        },
    )

    assert device.status_code == 4
    assert device.playlist_index == 1
    assert device.progress == 250
    assert device.ball_speed == 200
    assert device.brightness == 150
    assert device.color == "#FFFFFF"
    assert device.repeat_playlist is True
    assert device.auto_clean is False


def test_fragmented_notification(client: OasisBleClient, device: OasisDevice) -> None:
    """A notification split across writes is reassembled."""
    data = json.dumps({"command": "LINESTATUS", "cl": "321"}).encode()

    client._handle_notification(MagicMock(), bytearray(data[:10]))
    assert device.progress == 0
    client._handle_notification(MagicMock(), bytearray(data[10:]))

    assert device.progress == 321


def test_invalid_data_dropped(client: OasisBleClient, device: OasisDevice) -> None:
    """Data that isn't the start of a JSON object doesn't block later notifications."""
    client._handle_notification(MagicMock(), bytearray(b"garbage"))
    _notify(client, {"command": "LINESTATUS", "cl": "12"})

    assert device.progress == 12


async def test_get_all(
    client: OasisBleClient, device: OasisDevice, responses: dict[str, Responder]
) -> None:
    """The initial sync reads status and the full playlist."""
    responses["GETSTATUS"] = lambda _: [{"command": "GETSTATUS", "st": "2"}]
    responses["GETPLAYLIST"] = lambda _: _playlist_push("GETPLAYLIST", [101, 102])

    await client.async_get_all(device)

    assert device.status_code == 2
    assert device.playlist == [101, 102]
    assert client.legacy_firmware is False


async def test_legacy_firmware(
    client: OasisBleClient, device: OasisDevice, responses: dict[str, Responder]
) -> None:
    """Firmware without GETSTATUS over BLE only owns identity and Wi-Fi fields."""
    responses["GETSTATUS"] = lambda _: [
        {"command": "UNKNOWN", "received": '{"command":"GETSTATUS"}'}
    ]

    await client.async_get_all(device)

    assert client.legacy_firmware is True
    assert "status_code" not in client.status_fields
    assert "GETPLAYLIST" not in [c["command"] for c in _written(client)]

    # The cloud keeps reporting status for legacy firmware
    device.update_from_status_dict({"status_code": 4}, source=MagicMock())
    assert device.status_code == 4


def test_other_transport_cannot_overwrite_ble_fields(
    client: OasisBleClient, device: OasisDevice
) -> None:
    """While BLE is active, MQTT updates to fields BLE reports are ignored."""
    _notify(client, {"command": "GETSTATUS", "st": "4"})

    device.update_from_status_dict(
        {"status_code": 2, "wifi_ssid": "home"}, source=MagicMock()
    )

    assert device.status_code == 4
    assert device.wifi_ssid == "home"


async def test_set_playlist_waits_for_push(
    client: OasisBleClient, device: OasisDevice, responses: dict[str, Responder]
) -> None:
    """Replacing the playlist completes when the device pushes the new playlist."""
    responses["WRIJOBLIST"] = lambda payload: _playlist_push(
        "GETPLAYLIST_PUSH", [int(t) for t in payload["value"].split(",")]
    )

    async with asyncio.timeout(1):
        await client.async_send_set_playlist_command(device, [201, 202, 203])

    assert _written(client) == [{"command": "WRIJOBLIST", "value": "201,202,203"}]
    assert device.playlist == [201, 202, 203]


async def test_command_not_supported(
    client: OasisBleClient,
    device: OasisDevice,
    responses: dict[str, Responder],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An UNKNOWN reply to a command is logged."""
    responses["CMDPLAY"] = lambda _: [{"command": "UNKNOWN", "received": "CMDPLAY"}]

    await client.async_send_play_command(device)

    assert "does not support BLE command CMDPLAY" in caplog.text


def test_empty_playlist_transfer_not_stale(
    client: OasisBleClient, device: OasisDevice
) -> None:
    """A transfer with no entries reports an empty playlist, not the last one."""
    for message in _playlist_push("GETPLAYLIST_PUSH", [101, 102]):
        _notify(client, message)
    assert device.playlist == [101, 102]

    _notify(client, {"command": "GETPLAYLIST_PUSH", "part": "end"})

    assert device.playlist == []


async def test_concurrent_playlist_writes_serialized(
    client: OasisBleClient, device: OasisDevice, responses: dict[str, Responder]
) -> None:
    """A second playlist write waits for the first to complete."""
    pushes: list[list[int]] = []
    responses["WRIJOBLIST"] = lambda payload: (
        pushes.append([int(t) for t in payload["value"].split(",")]) or []
    )

    first = asyncio.create_task(client.async_send_set_playlist_command(device, [1]))
    second = asyncio.create_task(client.async_send_set_playlist_command(device, [2]))
    for _ in range(5):
        await asyncio.sleep(0)
    # Only the first write is sent until the device pushes its playlist back
    assert pushes == [[1]]

    for message in _playlist_push("GETPLAYLIST_PUSH", [1]):
        _notify(client, message)
    await first
    for _ in range(5):
        await asyncio.sleep(0)
    assert pushes == [[1], [2]]
    assert not second.done()

    for message in _playlist_push("GETPLAYLIST_PUSH", [2]):
        _notify(client, message)
    await second
    assert device.playlist == [2]


async def test_disconnect_fails_pending_reply(client: OasisBleClient) -> None:
    """A disconnect while waiting for a reply raises instead of returning nothing."""
    task = asyncio.create_task(client.async_get_status(client._device))
    for _ in range(5):
        await asyncio.sleep(0)

    client._handle_disconnect(MagicMock())

    with pytest.raises(ble_client.BleakError):
        await task


async def test_cancel_while_waiting_for_reply(client: OasisBleClient) -> None:
    """Cancelling a command waiting for its reply cancels it, not a silent None."""
    task = asyncio.create_task(client.async_get_status(client._device))
    for _ in range(5):
        await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_connection_completing_after_close_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection that finishes after async_close is disconnected, not kept."""
    connected = asyncio.Event()
    proceed = asyncio.Event()
    connection = MagicMock(is_connected=True)
    connection.start_notify = AsyncMock()
    connection.disconnect = AsyncMock()

    async def _establish(*_args: object, **_kwargs: object) -> MagicMock:
        connected.set()
        await proceed.wait()
        return connection

    monkeypatch.setattr(ble_client, "establish_connection", _establish)
    client = OasisBleClient(ADDRESS, lambda: MagicMock())

    task = asyncio.create_task(client.async_connect())
    await connected.wait()
    await client.async_close()
    proceed.set()

    with pytest.raises(ble_client.BleakError):
        await task
    connection.disconnect.assert_awaited_once()
    assert not client.is_connected


async def test_queued_connect_after_close_stays_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connect queued behind one in progress doesn't reopen a closed client."""
    started = asyncio.Event()
    proceed = asyncio.Event()
    connections: list[MagicMock] = []

    async def _establish(*_args: object, **_kwargs: object) -> MagicMock:
        connection = MagicMock(is_connected=True)
        connection.start_notify = AsyncMock()
        connection.disconnect = AsyncMock()
        connections.append(connection)
        started.set()
        await proceed.wait()
        return connection

    monkeypatch.setattr(ble_client, "establish_connection", _establish)
    client = OasisBleClient(ADDRESS, lambda: MagicMock())

    first = asyncio.create_task(client.async_connect())
    await started.wait()
    second = asyncio.create_task(client.async_connect())
    await asyncio.sleep(0)
    await client.async_close()
    proceed.set()

    for task in (first, second):
        with pytest.raises(ble_client.BleakError):
            await task
    # Only the first attempt connected, and it was disconnected again
    assert len(connections) == 1
    connections[0].disconnect.assert_awaited_once()
    assert not client.is_connected


async def test_overlapping_requests_get_their_own_replies(
    client: OasisBleClient, device: OasisDevice
) -> None:
    """A second request isn't sent until the first has its reply."""
    first = asyncio.create_task(client.async_get_status(device))
    second = asyncio.create_task(client.async_get_status(device))
    for _ in range(5):
        await asyncio.sleep(0)
    assert [c["command"] for c in _written(client)] == ["GETSTATUS"]

    _notify(client, {"command": "GETSTATUS", "st": "4"})
    await first
    for _ in range(5):
        await asyncio.sleep(0)
    # The first reply didn't also complete the second request
    assert not second.done()
    assert [c["command"] for c in _written(client)] == ["GETSTATUS", "GETSTATUS"]

    _notify(client, {"command": "GETSTATUS", "st": "5"})
    await second
    assert device.status_code == 5
