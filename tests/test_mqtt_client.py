"""Tests for the Oasis MQTT client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiomqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
import pytest

from custom_components.oasis_mini.pyoasiscontrol import OasisDevice, OasisMqttClient

SERIAL = "OM1"


@pytest.fixture
def device() -> OasisDevice:
    """Return a device."""
    return OasisDevice(model="Oasis Mini", serial_number=SERIAL)


@pytest.fixture
def mqtt_client(device: OasisDevice) -> OasisMqttClient:
    """Return an MQTT client with the device registered and a mocked connection."""
    client = OasisMqttClient()
    client.register_device(device)
    client._client = MagicMock()
    client._client.subscribe = AsyncMock()
    client._client.publish = AsyncMock()
    client._connected_event.set()
    return client


def _message(status_name: str, payload: str) -> SimpleNamespace:
    """Return an incoming STATUS message."""
    return SimpleNamespace(
        topic=f"{SERIAL}/STATUS/{status_name}", payload=payload.encode()
    )


@pytest.mark.parametrize("status_name", ["OASIS_SPEED", "OASIS_SPEEED"])
async def test_ball_speed_status(
    mqtt_client: OasisMqttClient, device: OasisDevice, status_name: str
) -> None:
    """Ball speed is read from both the current and legacy (misspelled) topics."""
    await mqtt_client._handle_status_message(_message(status_name, "250"))

    assert device.ball_speed == 250


async def test_user_attached_status_not_warned(
    mqtt_client: OasisMqttClient, caplog: pytest.LogCaptureFixture
) -> None:
    """USERATTACHED is a known status topic and isn't logged as unknown."""
    await mqtt_client._handle_status_message(_message("USERATTACHED", "1"))

    assert "Unknown status" not in caplog.text


async def test_get_all_command(
    mqtt_client: OasisMqttClient, device: OasisDevice
) -> None:
    """GETALL is sent with a trailing `=` like the app."""
    await mqtt_client.async_get_all(device)

    mqtt_client._client.publish.assert_awaited_once_with(
        f"{SERIAL}/COMMAND/CTL", b"GETALL=", qos=1
    )


@pytest.mark.parametrize(
    "result",
    [(1,), [ReasonCode(PacketTypes.SUBACK, identifier=1)]],
    ids=["mqttv311", "mqttv5"],
)
async def test_subscribe_success(
    mqtt_client: OasisMqttClient, result: tuple | list
) -> None:
    """A granted subscription is recorded."""
    mqtt_client._client.subscribe.return_value = result

    await mqtt_client._subscribe_serial(SERIAL)

    assert SERIAL in mqtt_client._subscribed_serials


@pytest.mark.parametrize(
    "result",
    [(0x80,), [ReasonCode(PacketTypes.SUBACK, identifier=0x87)]],
    ids=["mqttv311", "mqttv5"],
)
async def test_subscribe_rejected(
    mqtt_client: OasisMqttClient,
    caplog: pytest.LogCaptureFixture,
    result: tuple | list,
) -> None:
    """A rejected subscription isn't recorded, so it's retried on reconnect."""
    mqtt_client._client.subscribe.return_value = result

    await mqtt_client._subscribe_serial(SERIAL)

    assert SERIAL not in mqtt_client._subscribed_serials
    assert f"Broker rejected subscription to {SERIAL}/STATUS/#" in caplog.text


async def test_subscribe_timeout_warns(
    mqtt_client: OasisMqttClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A subscription that fails is a warning, and still restarts the connection."""
    mqtt_client._client.subscribe.side_effect = aiomqtt.MqttError("Operation timed out")

    with pytest.raises(aiomqtt.MqttError):
        await mqtt_client._subscribe_serial(SERIAL)

    assert SERIAL not in mqtt_client._subscribed_serials
    assert [
        record.levelno
        for record in caplog.records
        if record.getMessage()
        == f"Failed to subscribe to {SERIAL}/STATUS/#: Operation timed out"
    ] == [logging.WARNING]


async def test_disconnect_logs_reason(
    device: OasisDevice, caplog: pytest.LogCaptureFixture
) -> None:
    """The underlying reason is logged, not aiomqtt's generic wrapper message."""
    client = OasisMqttClient()
    client.register_device(device)

    async def _messages() -> AsyncIterator[aiomqtt.Message]:
        client._stop_event.set()
        raise aiomqtt.MqttError("Disconnected during message iteration") from (
            aiomqtt.MqttError("The connection was lost.")
        )
        yield

    connection = MagicMock(subscribe=AsyncMock(), publish=AsyncMock())
    connection.messages = _messages()
    mqtt = MagicMock()
    mqtt.return_value.__aenter__ = AsyncMock(return_value=connection)
    mqtt.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("aiomqtt.Client", mqtt),
        caplog.at_level(logging.INFO),
    ):
        await client._mqtt_loop("user", AsyncMock(return_value="token"))

    assert [
        record.levelno
        for record in caplog.records
        if record.getMessage() == "MQTT connection error: The connection was lost."
    ] == [logging.INFO]


async def test_unexpected_error_warns(
    device: OasisDevice, caplog: pytest.LogCaptureFixture
) -> None:
    """An error that isn't a dropped connection is a warning, even when connected."""
    client = OasisMqttClient()
    client.register_device(device)

    async def _messages() -> AsyncIterator[aiomqtt.Message]:
        client._stop_event.set()
        raise IndexError("list index out of range")
        yield

    connection = MagicMock(subscribe=AsyncMock(), publish=AsyncMock())
    connection.messages = _messages()
    mqtt = MagicMock()
    mqtt.return_value.__aenter__ = AsyncMock(return_value=connection)
    mqtt.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("aiomqtt.Client", mqtt):
        await client._mqtt_loop("user", AsyncMock(return_value="token"))

    assert [
        record.levelno
        for record in caplog.records
        if record.getMessage() == "MQTT connection error: list index out of range"
    ] == [logging.WARNING]


async def test_failed_connection_warns(
    device: OasisDevice, caplog: pytest.LogCaptureFixture
) -> None:
    """Failing to connect at all is logged as a warning."""
    client = OasisMqttClient()
    client.register_device(device)

    def _fail(*_: object) -> None:
        client._stop_event.set()
        raise aiomqtt.MqttError("Connection refused")

    mqtt = MagicMock()
    mqtt.return_value.__aenter__ = AsyncMock(side_effect=_fail)

    with patch("aiomqtt.Client", mqtt):
        await client._mqtt_loop("user", AsyncMock(return_value="token"))

    assert [
        record.levelno
        for record in caplog.records
        if record.getMessage() == "MQTT connection error: Connection refused"
    ] == [logging.WARNING]


async def test_retry_now_skips_backoff(
    device: OasisDevice, caplog: pytest.LogCaptureFixture
) -> None:
    """Failures back off and warn once; retry_now cuts the wait short."""
    client = OasisMqttClient()
    client.register_device(device)
    attempts = 0

    def _fail(*_: object) -> None:
        nonlocal attempts
        attempts += 1
        raise aiomqtt.MqttError("Connection refused")

    async def _wait_for_attempt(count: int) -> None:
        async with asyncio.timeout(2):
            while attempts < count:
                await asyncio.sleep(0.01)

    mqtt = MagicMock()
    mqtt.return_value.__aenter__ = AsyncMock(side_effect=_fail)

    with patch("aiomqtt.Client", mqtt), caplog.at_level(logging.INFO):
        client.start("user", AsyncMock(return_value="token"))
        try:
            await _wait_for_attempt(1)

            # Waiting out the 4s backoff, so nothing happens on its own
            await asyncio.sleep(0.1)
            assert attempts == 1

            client.retry_now()
            await _wait_for_attempt(2)
        finally:
            await client.async_close()

    errors = [
        record.levelno
        for record in caplog.records
        if record.getMessage() == "MQTT connection error: Connection refused"
    ]
    assert errors == [logging.WARNING, logging.INFO]
    # retry_now doesn't reset the backoff: the next wait is still doubled
    assert [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Disconnected from broker")
    ] == [
        "Disconnected from broker, retrying in 4.0s",
        "Disconnected from broker, retrying in 8.0s",
    ]
