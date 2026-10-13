"""Tests for the Oasis MQTT client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
