"""Oasis devices coordinator."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from functools import partial
import logging
from typing import TYPE_CHECKING

from bleak.exc import BleakError

from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .pyoasiscontrol import (
    OasisCloudClient,
    OasisDevice,
    OasisMqttClient,
    UnauthenticatedError,
)
from .pyoasiscontrol.clients.ble_client import OASIS_SERVICE_UUID, OasisBleClient

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import (
        BluetoothChange,
        BluetoothServiceInfoBleak,
    )

    from . import OasisDeviceConfigEntry

_LOGGER = logging.getLogger(__name__)

# BLE connection retry backoff: starts at BLE_RECONNECT_DELAY seconds and doubles
# up to BLE_RETRY_INTERVAL while Home Assistant can still see the device
BLE_RETRY_INTERVAL = 60
BLE_RECONNECT_DELAY = 5


class OasisDeviceCoordinator(DataUpdateCoordinator[list[OasisDevice]]):
    """Oasis device data update coordinator."""

    attempt: int = 0
    last_updated: datetime | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: OasisDeviceConfigEntry,
        cloud_client: OasisCloudClient,
    ) -> None:
        """
        Create an OasisDeviceCoordinator that manages OasisDevice discovery and updates using cloud and MQTT clients.

        Parameters:
            config_entry (OasisDeviceConfigEntry): The config entry whose runtime data contains device serial numbers.
            cloud_client (OasisCloudClient): Client for communicating with the Oasis cloud API and fetching device data.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=10),
            always_update=False,
        )
        self.cloud_client = cloud_client
        self.mqtt_client = OasisMqttClient()
        config_entry.async_on_unload(
            self.mqtt_client.add_connection_listener(self.async_update_listeners)
        )

        # Bluetooth clients by address, plus probe bookkeeping
        self.ble_clients: dict[str, OasisBleClient] = {}
        self._ble_probing: set[str] = set()
        # Advertisers identified as devices not on this account, by address => serial
        self._ble_ignored: dict[str, str] = {}
        self._ble_reconnect_tasks: dict[str, asyncio.Task[None]] = {}

        # Track which devices are currently considered initialized
        self._initialized_serials: set[str] = set()

    @property
    def _device_initialized_signal(self) -> str:
        """Dispatcher signal name for device initialization events."""
        return f"{DOMAIN}_{self.config_entry.entry_id}_device_initialized"

    def _attach_device_listeners(self, device: OasisDevice) -> None:
        """Attach a listener so we can fire dispatcher events when a device initializes."""

        def _on_device_update() -> None:
            serial = device.serial_number
            if not serial:
                return

            initialized = device.is_initialized
            was_initialized = serial in self._initialized_serials

            if initialized and not was_initialized:
                self._initialized_serials.add(serial)
                _LOGGER.debug("%s ready for setup; dispatching signal", device.name)
                async_dispatcher_send(
                    self.hass, self._device_initialized_signal, device
                )

            elif not initialized and was_initialized:
                self._initialized_serials.remove(serial)
                _LOGGER.debug("Oasis device %s no longer initialized", serial)

            self.last_updated = dt_util.now()
            self.async_update_listeners()

        device.add_update_listener(_on_device_update)

        # Seed the initialized set if the device is already initialized
        if device.is_initialized and device.serial_number:
            self._initialized_serials.add(device.serial_number)

    async def _async_update_data(self) -> list[OasisDevice]:
        """
        Fetch and assemble the current list of OasisDevice objects, reconcile removed
        devices in Home Assistant, register discovered devices with MQTT, and
        best-effort trigger status updates for uninitialized devices.

        Returns:
            A list of OasisDevice instances representing devices currently available for the account.

        Raises:
            ConfigEntryAuthFailed: If the cloud no longer accepts the access token.
            UpdateFailed: If an unexpected error persists past retry limits.
        """
        devices: list[OasisDevice] = []
        self.attempt += 1

        try:
            async with asyncio.timeout(30):
                raw_devices = await self.cloud_client.async_get_devices()

                existing_by_serial = {
                    d.serial_number: d for d in (self.data or []) if d.serial_number
                }

                for raw in raw_devices:
                    if not (serial := raw.get("serial_number")):
                        continue

                    if device := existing_by_serial.get(serial):
                        if name := raw.get("name"):
                            device.name = name
                    else:
                        device = OasisDevice(
                            model=(raw.get("model") or {}).get("name"),
                            serial_number=serial,
                            name=raw.get("name"),
                            cloud=self.cloud_client,
                        )
                        self._attach_device_listeners(device)

                    devices.append(device)

                # Handle devices removed from the account
                new_serials = {d.serial_number for d in devices if d.serial_number}
                removed_serials = set(existing_by_serial) - new_serials

                if removed_serials:
                    device_registry = dr.async_get(self.hass)
                    for serial in removed_serials:
                        self._initialized_serials.discard(serial)
                        _LOGGER.info(
                            "Oasis device %s removed from account; cleaning up in HA",
                            serial,
                        )
                        device_entry = device_registry.async_get_device(
                            identifiers={(DOMAIN, serial)}
                        )
                        if device_entry:
                            device_registry.async_update_device(
                                device_id=device_entry.id,
                                remove_config_entry_id=self.config_entry.entry_id,
                            )

                # If logged in, but no devices on account, return without starting mqtt
                if not devices:
                    _LOGGER.debug("No Oasis devices found for account")
                    if self.mqtt_client.is_running:
                        # Close the mqtt client if it was previously started
                        await self.mqtt_client.async_close()
                    self.attempt = 0
                    if devices != self.data:
                        self.last_updated = dt_util.now()
                    return []

                # Ensure MQTT is running and devices are registered
                if not self.mqtt_client.is_running:
                    username = self.config_entry.data[CONF_EMAIL]
                    self.mqtt_client.start(
                        username, self.cloud_client.async_get_mqtt_token
                    )
                self.mqtt_client.register_devices(devices)

                # Best-effort playlists
                try:
                    await self.cloud_client.async_get_playlists()
                except UnauthenticatedError:
                    raise
                except Exception:
                    _LOGGER.exception("Error fetching playlists from cloud")

                # Best-effort: request status for devices that are not yet initialized
                for device in devices:
                    try:
                        if not device.is_initialized:
                            await device.async_get_status()
                        device.schedule_track_refresh()
                    except Exception:
                        _LOGGER.exception(
                            "Error requesting status for Oasis device %s; "
                            "will retry on future updates",
                            device.serial_number,
                        )

                self.attempt = 0

        except UnauthenticatedError as ex:
            raise ConfigEntryAuthFailed(ex) from ex
        except Exception as ex:
            if self.attempt > 2 or not (devices or self.data):
                raise UpdateFailed(
                    "Unexpected error talking to Oasis devices "
                    f"after {self.attempt} attempts"
                ) from ex

            _LOGGER.warning(
                "Error updating Oasis devices; reusing previous data", exc_info=ex
            )
            return self.data or devices

        if devices != self.data:
            self.last_updated = dt_util.now()

        return devices

    @callback
    def async_setup_bluetooth(self) -> None:
        """Start watching for Oasis devices advertising over Bluetooth."""
        if "bluetooth" not in self.hass.config.components:
            _LOGGER.debug("Bluetooth integration not loaded; using MQTT only")
            return

        # Imported lazily so the integration still loads without bluetooth support
        from homeassistant.components import bluetooth

        self.config_entry.async_on_unload(
            self.async_add_listener(self._async_ble_reprobe_ignored)
        )
        self.config_entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self._async_ble_discovered,
                bluetooth.BluetoothCallbackMatcher(
                    service_uuid=OASIS_SERVICE_UUID, connectable=True
                ),
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )
        for service_info in bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        ):
            if OASIS_SERVICE_UUID in service_info.service_uuids:
                self._async_ble_discovered(
                    service_info, bluetooth.BluetoothChange.ADVERTISEMENT
                )

    @callback
    def _async_ble_discovered(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """Probe a newly seen Oasis BLE advertiser to learn which device it is."""
        address = service_info.address
        if (
            address in self.ble_clients
            or address in self._ble_probing
            or address in self._ble_ignored
        ):
            return
        self._async_start_ble_probe(address)

    @callback
    def _async_ble_reprobe_ignored(self) -> None:
        """Probe ignored BLE devices again once they are added to the account."""
        if not self._ble_ignored:
            return
        serials = {d.serial_number for d in self.data or []}
        for address, serial in list(self._ble_ignored.items()):
            if serial in serials:
                del self._ble_ignored[address]
                self._async_start_ble_probe(address)

    @callback
    def _async_start_ble_probe(self, address: str) -> None:
        """Start identifying a BLE advertiser in the background."""
        self._ble_probing.add(address)
        self.config_entry.async_create_background_task(
            self.hass, self._async_probe_ble(address), f"{DOMAIN} BLE probe {address}"
        )

    async def _async_probe_ble(self, address: str) -> None:
        """Connect to a BLE advertiser, identify it and attach it to its device."""
        from homeassistant.components import bluetooth

        client = OasisBleClient(
            address,
            partial(bluetooth.async_ble_device_from_address, self.hass, address, True),
            on_disconnect=partial(self._async_ble_disconnected, address),
        )

        async def _async_identify() -> None:
            if not await client.async_get_serial_number():
                raise BleakError("No response to GETID")

        try:
            if not await self._async_ble_retry(address, _async_identify):
                await client.async_close()
                return
        finally:
            self._ble_probing.discard(address)
        serial = client.serial_number
        assert serial is not None

        device = next((d for d in self.data or [] if d.serial_number == serial), None)
        if device is None:
            _LOGGER.debug(
                "Oasis BLE device %s (%s) is not on this account; ignoring",
                address,
                serial,
            )
            self._ble_ignored[address] = serial
            await client.async_close()
            return

        _LOGGER.info("Using Bluetooth for Oasis device %s (%s)", serial, address)
        self.ble_clients[address] = client
        device_registry = dr.async_get(self.hass)
        if device_entry := device_registry.async_get_device(
            identifiers={(DOMAIN, serial)}
        ):
            device_registry.async_update_device(
                device_entry.id,
                merge_connections={(dr.CONNECTION_BLUETOOTH, dr.format_mac(address))},
            )
        client.attach_device(device)
        device.attach_client(client)
        try:
            await client.async_get_all(device)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error fetching status over BLE from %s: %s", address, err)

    @callback
    def _async_ble_disconnected(self, address: str) -> None:
        """Fall back to MQTT when a BLE connection drops and start reconnecting.

        Reconnection can't wait for the next advertisement: Home Assistant only
        dispatches advertisements whose content changed, and the device re-advertises
        the same data after a disconnect.
        """
        if not (client := self.ble_clients.get(address)):
            return
        device = next(
            (
                d
                for d in self.data or []
                if client.serial_number and d.serial_number == client.serial_number
            ),
            None,
        )
        if device is None:
            self.ble_clients.pop(address)
            return

        if device.client is client:
            _LOGGER.info(
                "Bluetooth lost for Oasis device %s; falling back to MQTT",
                device.serial_number,
            )
            device.attach_client(self.mqtt_client)

        if address not in self._ble_reconnect_tasks:
            self._ble_reconnect_tasks[address] = (
                self.config_entry.async_create_background_task(
                    self.hass,
                    self._async_ble_reconnect(address, client, device),
                    f"{DOMAIN} BLE reconnect {address}",
                )
            )

    async def _async_ble_reconnect(
        self, address: str, client: OasisBleClient, device: OasisDevice
    ) -> None:
        """Reconnect a dropped BLE client with backoff, then switch back to it."""
        try:
            if not await self._async_ble_retry(
                address, client.async_connect, BLE_RECONNECT_DELAY
            ):
                self.ble_clients.pop(address, None)
                await client.async_close()
                return
        finally:
            self._ble_reconnect_tasks.pop(address, None)

        _LOGGER.info(
            "Bluetooth restored for Oasis device %s (%s)", device.serial_number, address
        )
        device.attach_client(client)
        try:
            await client.async_get_all(device)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error fetching status over BLE from %s: %s", address, err)

    async def _async_ble_retry(
        self,
        address: str,
        attempt: Callable[[], Awaitable[object]],
        delay: float = 0,
    ) -> bool:
        """Run a BLE connection attempt, retrying with backoff while the device is in range.

        Gives up once no Bluetooth scanner has a connectable path to the device.
        Home Assistant drops it from its advertisement history at that point, so
        its next advertisement reaches _async_ble_discovered and probes it again
        (identical advertisements are otherwise never re-dispatched).

        Returns:
            bool: True if the attempt succeeded, False if the device is out of range.
        """
        from homeassistant.components import bluetooth

        while True:
            if delay:
                await asyncio.sleep(delay)
            if not bluetooth.async_address_present(
                self.hass, address, connectable=True
            ):
                _LOGGER.debug(
                    "Oasis BLE device %s no longer seen by any Bluetooth adapter; "
                    "waiting for it to advertise again",
                    address,
                )
                return False
            try:
                await attempt()
            except Exception as err:  # noqa: BLE001
                delay = min(max(delay * 2, BLE_RECONNECT_DELAY), BLE_RETRY_INTERVAL)
                _LOGGER.debug(
                    "Unable to connect to Oasis BLE device %s: %s; retrying in %ss",
                    address,
                    err,
                    delay,
                )
            else:
                return True

    async def async_close(self) -> None:
        """Close client connections."""
        for task in self._ble_reconnect_tasks.values():
            task.cancel()
        self._ble_reconnect_tasks.clear()
        ble_clients = list(self.ble_clients.values())
        self.ble_clients.clear()
        await asyncio.gather(
            self.mqtt_client.async_close(),
            self.cloud_client.async_close(),
            *(client.async_close() for client in ble_clients),
            return_exceptions=True,
        )
