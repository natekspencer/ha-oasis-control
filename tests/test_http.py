"""Tests for the local HTTP transport and transport selection."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError
import pytest
from yarl import URL

from custom_components.oasis_mini.const import DOMAIN
from custom_components.oasis_mini.coordinator import OasisDeviceCoordinator
from custom_components.oasis_mini.pyoasiscontrol import OasisBleClient, OasisHttpClient
from homeassistant.config_entries import SOURCE_DHCP
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .common import AiohttpClientMocker, AiohttpClientMockResponse, MockConfigEntry
from .conftest import SERIAL_NUMBER

HOST = "10.0.0.5"
BASE_URL = f"http://{HOST}/"
MAC = "3C:84:27:CC:F0:DC"
# Playing track 101 of [101, 102] at line 50, firmware 3.54
STATUS = "4;0;300;101,102;0;50;21;0;0;100;#d2b48c;0;0;200;1;1;6;1;3.54"
TEXT = {"content-type": "text/plain"}


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the sensor platform for the connection sensor."""
    return [Platform.SENSOR]


@pytest.fixture
def device_status() -> dict[str, Any]:
    """Report nothing over MQTT, like firmware 3.54 with remote access inactive."""
    return {}


@pytest.fixture(autouse=True)
def mock_cloud_ip(mock_cloud_client: MagicMock) -> None:
    """Report the device's LAN IP from the cloud."""
    mock_cloud_client.async_get_devices.return_value[0]["ip"] = HOST


@pytest.fixture(autouse=True)
def no_command_delay() -> Generator[None]:
    """Poll right after a command instead of waiting for the device."""
    with patch(
        "custom_components.oasis_mini.coordinator.HTTP_COMMAND_REFRESH_DELAY", 0
    ):
        yield


def mock_device(aioclient_mock: AiohttpClientMocker, status: str = STATUS) -> None:
    """Answer the device's HTTP API, which takes the command as the query key."""
    replies = {
        "GETSTATUS": status,
        "GETMAC": f"{MAC}\n",
        "GETSCH": '{"actions":[]}',
        "CMDPLAY": "OK",
    }

    async def _reply(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(
            method, url, text=replies[next(iter(url.query))], headers=TEXT
        )

    aioclient_mock.clear_requests()
    aioclient_mock.get(BASE_URL, side_effect=_reply)


def mock_device_offline(aioclient_mock: AiohttpClientMocker) -> None:
    """Make the device's HTTP API unreachable (e.g. HTTP mode is off)."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(BASE_URL, exc=ClientError("unreachable"))


async def poll(hass: HomeAssistant, coordinator: OasisDeviceCoordinator) -> None:
    """Run the device's HTTP poll loop once more."""
    coordinator._http_refresh[SERIAL_NUMBER].set()
    for _ in range(20):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


def connection_state(hass: HomeAssistant) -> str:
    """Return the state of the device's connection sensor."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", "oasis_mini", f"{SERIAL_NUMBER}-connection"
    )
    assert entity_id
    state = hass.states.get(entity_id)
    assert state
    return state.state


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> OasisDeviceCoordinator:
    """Return the coordinator once the first HTTP poll has run."""
    coordinator = entry.runtime_data
    await poll(hass, coordinator)
    return coordinator


async def test_http_preferred_when_available(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    mock_cloud_client: MagicMock,
    mock_mqtt_client: MagicMock,
    platforms: list[Platform],
) -> None:
    """A device answering on the LAN is controlled over HTTP and fully read."""
    mock_device(aioclient_mock)
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.oasis_mini.PLATFORMS", platforms):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    coordinator = await setup(hass, mock_config_entry)

    device = coordinator.data[0]
    assert isinstance(device.client, OasisHttpClient)
    assert device.client.host == HOST
    assert device.mac_address == MAC
    assert device.software_version == "3.54"
    assert device.progress == 50
    assert connection_state(hass) == "local"


async def test_http_unavailable_uses_mqtt(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    mock_mqtt_client: MagicMock,
) -> None:
    """Without HTTP mode the device stays on the cloud MQTT transport."""
    mock_device_offline(aioclient_mock)
    coordinator = await setup(hass, init_integration)

    device = coordinator.data[0]
    assert device.client is mock_mqtt_client
    assert not coordinator.http_clients[SERIAL_NUMBER].is_connected


async def test_http_lost_falls_back_to_mqtt_and_recovers(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    mock_mqtt_client: MagicMock,
) -> None:
    """Losing HTTP falls back to MQTT, and HTTP is used again once it answers."""
    mock_device(aioclient_mock)
    coordinator = await setup(hass, init_integration)
    device = coordinator.data[0]
    assert isinstance(device.client, OasisHttpClient)

    mock_device_offline(aioclient_mock)
    await poll(hass, coordinator)
    assert device.client is mock_mqtt_client

    mock_device(aioclient_mock)
    await poll(hass, coordinator)
    assert isinstance(device.client, OasisHttpClient)


async def test_http_lost_falls_back_to_bluetooth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """Bluetooth is preferred over MQTT when HTTP is lost."""
    mock_device(aioclient_mock)
    coordinator = await setup(hass, init_integration)
    device = coordinator.data[0]

    ble_client = MagicMock(spec=OasisBleClient)
    ble_client.serial_number = SERIAL_NUMBER
    ble_client.is_connected = True
    ble_client.async_get_all = AsyncMock()
    coordinator.ble_clients["AA:BB:CC:DD:EE:FF"] = ble_client

    mock_device_offline(aioclient_mock)
    await poll(hass, coordinator)

    assert device.client is ble_client
    ble_client.async_get_all.assert_awaited_once_with(device)


async def test_http_command_refreshes_status(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """A command over HTTP is followed by a status poll, as HTTP doesn't push."""
    mock_device(aioclient_mock)
    coordinator = await setup(hass, init_integration)
    device = coordinator.data[0]

    mock_device(aioclient_mock, STATUS.replace("4;0;300", "5;0;300", 1))
    await device.async_play()
    for _ in range(20):
        await asyncio.sleep(0)
    await hass.async_block_till_done()

    assert [str(call[1].query_string) for call in aioclient_mock.mock_calls] == [
        "CMDPLAY=",
        "GETSTATUS=",
    ]
    assert device.status_code == 5


NEW_HOST = "10.0.0.9"


async def test_device_reported_ip_updates_host(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
    mock_cloud_client: MagicMock,
) -> None:
    """The device's own IP wins over a stale cloud one until the cloud changes."""
    mock_device_offline(aioclient_mock)
    coordinator = await setup(hass, init_integration)
    device = coordinator.data[0]
    client = coordinator.http_clients[SERIAL_NUMBER]

    device.update_from_status_dict({"wifi_ip": NEW_HOST})
    assert client.host == NEW_HOST

    # The cloud still reports the old address, which mustn't override it
    await coordinator.async_refresh()
    assert client.host == NEW_HOST

    # A changed cloud address is the newest information, so it's used
    mock_cloud_client.async_get_devices.return_value[0]["ip"] = "10.0.0.7"
    await coordinator.async_refresh()
    assert client.host == "10.0.0.7"


async def test_dhcp_updates_host_before_device_reached(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """DHCP finds a registered device's address, even before it has been reached."""
    mock_device_offline(aioclient_mock)
    coordinator = await setup(hass, init_integration)
    assert not coordinator.data[0].is_initialized
    dr.async_get(hass).async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, SERIAL_NUMBER)},
        connections={(dr.CONNECTION_NETWORK_MAC, dr.format_mac(MAC))},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP},
        data=DhcpServiceInfo(
            ip=NEW_HOST, hostname="esp32", macaddress=MAC.replace(":", "").lower()
        ),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert coordinator.http_clients[SERIAL_NUMBER].host == NEW_HOST


async def test_dhcp_for_unknown_mac_is_ignored(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """A DHCP discovery for an unregistered MAC changes nothing."""
    mock_device_offline(aioclient_mock)
    coordinator = await setup(hass, init_integration)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP},
        data=DhcpServiceInfo(ip=NEW_HOST, hostname="other", macaddress="001122334455"),
    )

    assert result["type"] is FlowResultType.ABORT
    assert coordinator.http_clients[SERIAL_NUMBER].host == HOST


async def test_same_address_rechecks_http_right_away(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    init_integration: MockConfigEntry,
) -> None:
    """Hearing from the device again while HTTP is down retries without waiting."""
    mock_device_offline(aioclient_mock)
    coordinator = await setup(hass, init_integration)
    device = coordinator.data[0]
    assert not coordinator._http_refresh[SERIAL_NUMBER].is_set()

    mock_device(aioclient_mock)
    coordinator.async_update_host(device, HOST, "DHCP")
    for _ in range(20):
        await asyncio.sleep(0)
    await hass.async_block_till_done()

    assert isinstance(device.client, OasisHttpClient)
