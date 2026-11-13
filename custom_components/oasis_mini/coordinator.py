"""Oasis devices coordinator."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import datetime, timedelta
from functools import partial
import logging
from typing import TYPE_CHECKING, Any

from bleak.exc import BleakError

from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .const import CONF_BLUETOOTH_DISABLED, DOMAIN
from .pyoasiscontrol import (
    OASIS_SERVICE_UUID,
    OasisBleClient,
    OasisClientProtocol,
    OasisCloudClient,
    OasisDevice,
    OasisHttpClient,
    OasisMqttClient,
    UnauthenticatedError,
)

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import (
        BluetoothChange,
        BluetoothServiceInfoBleak,
    )

    from . import OasisDeviceConfigEntry

_LOGGER = logging.getLogger(__name__)

# Device list cached from the cloud, so a cloud outage doesn't stop local control
STORAGE_VERSION = 1


def device_cache_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store holding a config entry's cached device list."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.devices")


# BLE connection retry backoff: starts at BLE_RECONNECT_DELAY seconds and doubles
# up to BLE_RETRY_INTERVAL while Home Assistant can still see the device
BLE_RETRY_INTERVAL = 60
BLE_RECONNECT_DELAY = 5
# The device only pushes playback state over BLE (status, track, progress,
# playlist), so settings such as LEDs and ball speed are polled
BLE_POLL_INTERVAL = timedelta(seconds=10)

# Local HTTP polling: HTTP has no push updates, so the device is polled while it
# answers, and retried less often while it doesn't (e.g. HTTP mode is off)
HTTP_POLL_INTERVAL = 10
HTTP_RETRY_INTERVAL = 60
# Pause after a command before polling, so the reply reflects it
HTTP_COMMAND_REFRESH_DELAY = 0.5


class OasisDeviceCoordinator(DataUpdateCoordinator[list[OasisDevice]]):
    """Oasis device data update coordinator."""

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
        self.cloud_available = True
        self._store = device_cache_store(hass, config_entry.entry_id)
        self._device_cache: list[dict[str, Any]] | None = None
        self.mqtt_client = OasisMqttClient()
        config_entry.async_on_unload(
            self.mqtt_client.add_connection_listener(self.async_update_listeners)
        )

        # Bluetooth clients by address, plus probe bookkeeping
        self.ble_clients: dict[str, OasisBleClient] = {}
        self._ble_probing: set[str] = set()
        # Advertisers identified as devices not on this account, by address => serial
        self._ble_ignored: dict[str, str] = {}
        # Advertisers identified as devices whose Bluetooth switch is off
        self._ble_disabled: dict[str, str] = {}
        self.bluetooth_available = False
        self._ble_reconnect_tasks: dict[str, asyncio.Task[None]] = {}
        self._ble_polling: set[str] = set()

        # Last IP address the cloud reported for each device, by serial number
        self._cloud_ips: dict[str, str] = {}

        # Local HTTP clients and their polling tasks, by serial number
        self.http_clients: dict[str, OasisHttpClient] = {}
        self._http_tasks: dict[str, asyncio.Task[None]] = {}
        self._http_refresh: dict[str, asyncio.Event] = {}

        # Track which devices are currently considered initialized
        self._initialized_serials: set[str] = set()

    @property
    def _device_initialized_signal(self) -> str:
        """Dispatcher signal name for device initialization events."""
        return f"{DOMAIN}_{self.config_entry.entry_id}_device_initialized"

    def _attach_device_listeners(self, device: OasisDevice) -> None:
        """Attach a listener so we can fire dispatcher events when a device initializes."""

        last_wifi_ip = device.wifi_ip

        def _on_device_update() -> None:
            nonlocal last_wifi_ip
            serial = device.serial_number
            if not serial:
                return

            if device.wifi_ip and device.wifi_ip != last_wifi_ip:
                last_wifi_ip = device.wifi_ip
                self.async_update_host(device, device.wifi_ip, "device")

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

        When the cloud can't be reached, the device list cached from the last
        successful refresh is used instead, so devices stay controllable over local
        HTTP and Bluetooth during a cloud outage.

        Raises:
            ConfigEntryAuthFailed: If the cloud no longer accepts the access token.
            UpdateFailed: If the cloud can't be reached and no device list is cached.
        """
        devices: list[OasisDevice] = []

        try:
            raw_devices = await self._async_fetch_devices()
            async with asyncio.timeout(30):
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
                    if (ip := raw.get("ip")) and self._cloud_ips.get(serial) != ip:
                        self._cloud_ips[serial] = ip
                        self.async_update_host(device, ip, "cloud")

                    devices.append(device)

                # Handle devices removed from the account
                new_serials = {d.serial_number for d in devices if d.serial_number}
                removed_serials = set(existing_by_serial) - new_serials

                if removed_serials:
                    device_registry = dr.async_get(self.hass)
                    for serial in removed_serials:
                        self._initialized_serials.discard(serial)
                        self._cloud_ips.pop(serial, None)
                        self._async_stop_http(serial)
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

                # Prefer the local HTTP API wherever the cloud knows the device's IP
                for device in devices:
                    self._async_setup_http(device)

                # Best-effort playlists
                if self.cloud_available:
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

        except UnauthenticatedError as ex:
            raise ConfigEntryAuthFailed(ex) from ex
        except UpdateFailed:
            raise
        except Exception as ex:
            if not self.data:
                raise UpdateFailed("Unexpected error updating Oasis devices") from ex
            _LOGGER.warning(
                "Error updating Oasis devices; reusing previous data", exc_info=ex
            )
            return self.data

        if devices != self.data:
            self.last_updated = dt_util.now()

        return devices

    async def async_load_device_cache(self) -> None:
        """Load the device list cached from the last successful cloud refresh."""
        if (data := await self._store.async_load()) is not None:
            self._device_cache = data.get("devices", [])

    @property
    def has_device_cache(self) -> bool:
        """Return whether a device list from an earlier cloud refresh is available."""
        return self._device_cache is not None

    async def _async_fetch_devices(self) -> list[dict[str, Any]]:
        """Fetch the account's devices from the cloud, falling back to the cache.

        Raises:
            UnauthenticatedError: If the cloud rejects the access token.
            UpdateFailed: If the cloud can't be reached and nothing is cached.
        """
        try:
            async with asyncio.timeout(30):
                raw_devices = await self.cloud_client.async_get_devices()
        except UnauthenticatedError:
            raise
        except Exception as err:
            if self._device_cache is None:
                raise UpdateFailed(f"Unable to reach the Oasis cloud: {err}") from err
            if self.cloud_available:
                _LOGGER.warning(
                    "Unable to reach the Oasis cloud (%s); using the cached device "
                    "list and local connections until it's back",
                    err or type(err).__name__,
                )
                self.cloud_available = False
            return self._device_cache

        if not self.cloud_available:
            _LOGGER.info("Oasis cloud reachable again")
            self.cloud_available = True
        # The cloud answers, so don't leave MQTT waiting out a long backoff
        self.mqtt_client.retry_now()

        cache = [
            {
                "serial_number": raw["serial_number"],
                "name": raw.get("name"),
                "model": {"name": (raw.get("model") or {}).get("name")},
                "ip": raw.get("ip"),
            }
            for raw in raw_devices
            if raw.get("serial_number")
        ]
        if cache != self._device_cache:
            self._device_cache = cache
            self._store.async_delay_save(lambda: {"devices": cache}, 1)
        return raw_devices

    def bluetooth_enabled(self, serial: str | None) -> bool:
        """Return whether Bluetooth may be used for a device (its switch is on)."""
        return serial not in self.config_entry.options.get(CONF_BLUETOOTH_DISABLED, [])

    async def async_set_bluetooth_enabled(
        self, device: OasisDevice, enabled: bool
    ) -> None:
        """Turn Bluetooth on or off for a device without reloading the entry.

        Turning it off releases the device's Bluetooth connection (so the Oasis app
        can use it) and switches to the next transport; turning it on connects again.
        """
        serial = device.serial_number
        if not serial or self.bluetooth_enabled(serial) == enabled:
            return
        disabled = set(self.config_entry.options.get(CONF_BLUETOOTH_DISABLED, []))
        if enabled:
            disabled.discard(serial)
        else:
            disabled.add(serial)
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={
                **self.config_entry.options,
                CONF_BLUETOOTH_DISABLED: sorted(disabled),
            },
        )

        if enabled:
            _LOGGER.info("Bluetooth turned on for Oasis device %s", serial)
            for address, disabled_serial in list(self._ble_disabled.items()):
                if disabled_serial == serial:
                    del self._ble_disabled[address]
                    self._async_start_ble_probe(address)
            return

        _LOGGER.info("Bluetooth turned off for Oasis device %s", serial)
        closing: list[OasisBleClient] = []
        for address, client in list(self.ble_clients.items()):
            if client.serial_number != serial:
                continue
            del self.ble_clients[address]
            self._ble_disabled[address] = serial
            if task := self._ble_reconnect_tasks.pop(address, None):
                task.cancel()
            closing.append(client)
        self._async_select_client(device)
        for client in closing:
            await client.async_close()

    def _ble_client_for(self, serial: str | None) -> OasisBleClient | None:
        """Return the Bluetooth client identified as the given device, if any."""
        return next(
            (c for c in self.ble_clients.values() if c.serial_number == serial), None
        )

    @callback
    def _async_select_client(self, device: OasisDevice) -> OasisClientProtocol:
        """Attach the best available transport to a device.

        Local HTTP is preferred, then Bluetooth, then the cloud MQTT broker, which is
        always attached as the last resort since it buffers commands until connected.
        """
        client: OasisClientProtocol
        if (http := self.http_clients.get(device.serial_number or "")) and (
            http.is_connected
        ):
            client, name = http, "local HTTP"
        elif (ble := self._ble_client_for(device.serial_number)) and ble.is_connected:
            client, name = ble, "Bluetooth"
        else:
            client, name = self.mqtt_client, "cloud MQTT"
        if device.client is not client:
            _LOGGER.info("Using %s for Oasis device %s", name, device.serial_number)
            device.attach_client(client)
        return client

    @callback
    def async_update_host(self, device: OasisDevice, host: str, source: str) -> None:
        """Use a newly reported IP address for a device's local HTTP API.

        Addresses come from the cloud, the device itself (over BLE or MQTT) and DHCP
        discovery; each source only reports when its address changes, so the most
        recent change wins. Hearing about the current address again (e.g. a DHCP
        renewal) rechecks HTTP right away if the device isn't answering on it.
        """
        serial = device.serial_number
        if host == device.ip_address:
            if (
                (client := self.http_clients.get(serial or ""))
                and not client.is_connected
                and (refresh := self._http_refresh.get(serial or ""))
            ):
                refresh.set()
            return
        _LOGGER.debug(
            "Oasis device %s reported at %s by %s (was %s)",
            serial,
            host,
            source,
            device.ip_address,
        )
        device.ip_address = host
        self._async_setup_http(device)

    @callback
    def _async_setup_http(self, device: OasisDevice) -> None:
        """Create or update the device's local HTTP client and start polling it."""
        if not (serial := device.serial_number) or not (host := device.ip_address):
            return
        if client := self.http_clients.get(serial):
            if client.host != host:
                _LOGGER.debug("Oasis device %s moved to %s", serial, host)
                client.host = host
                self._http_refresh[serial].set()
            return

        refresh = self._http_refresh[serial] = asyncio.Event()
        client = self.http_clients[serial] = OasisHttpClient(
            host, async_get_clientsession(self.hass), on_command=refresh.set
        )
        self._http_tasks[serial] = self.config_entry.async_create_background_task(
            self.hass,
            self._async_http_poll(device, client, refresh),
            f"{DOMAIN} HTTP poll {serial}",
        )

    @callback
    def _async_stop_http(self, serial: str) -> None:
        """Stop polling a device over HTTP and forget its client."""
        if task := self._http_tasks.pop(serial, None):
            task.cancel()
        self.http_clients.pop(serial, None)
        self._http_refresh.pop(serial, None)

    async def _async_http_poll(
        self, device: OasisDevice, client: OasisHttpClient, refresh: asyncio.Event
    ) -> None:
        """Poll a device's local HTTP API and switch transports as it comes and goes."""
        was_connected = False
        logged_unavailable = False
        while True:
            refresh.clear()
            try:
                if was_connected:
                    await client.async_get_status(device)
                else:
                    await client.async_get_all(device)
            except Exception as err:  # noqa: BLE001
                if was_connected:
                    _LOGGER.info(
                        "Local HTTP lost for Oasis device %s: %s",
                        device.serial_number,
                        err or type(err).__name__,
                    )
                elif not logged_unavailable:
                    _LOGGER.debug(
                        "Local HTTP unavailable for Oasis device %s at %s (%s); "
                        "enable HTTP mode in the Oasis Control app to use it",
                        device.serial_number,
                        client.host,
                        err or type(err).__name__,
                    )
                logged_unavailable = True
                if was_connected:
                    was_connected = False
                    fallback = self._async_select_client(device)
                    with contextlib.suppress(Exception):
                        await fallback.async_get_all(device)
                delay: float = HTTP_RETRY_INTERVAL
            else:
                was_connected = True
                logged_unavailable = False
                self._async_select_client(device)
                delay = HTTP_POLL_INTERVAL

            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(delay):
                    await refresh.wait()
            if refresh.is_set():
                await asyncio.sleep(HTTP_COMMAND_REFRESH_DELAY)

    @callback
    def async_setup_bluetooth(self) -> None:
        """Start watching for Oasis devices advertising over Bluetooth."""
        if "bluetooth" not in self.hass.config.components:
            _LOGGER.debug("Bluetooth integration not loaded; using MQTT only")
            return

        # Imported lazily so the integration still loads without bluetooth support
        from homeassistant.components import bluetooth

        self.bluetooth_available = True
        self.config_entry.async_on_unload(
            self.async_add_listener(self._async_ble_reprobe_ignored)
        )
        self.config_entry.async_on_unload(
            async_track_time_interval(
                self.hass,
                self._async_ble_poll,
                BLE_POLL_INTERVAL,
                name=f"{DOMAIN} BLE status poll",
            )
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
    def _async_ble_poll(self, _now: datetime | None = None) -> None:
        """Request status over BLE from devices that are using it as their transport.

        Skips clients that are disconnected (the reconnect task owns reconnecting),
        on firmware that doesn't support GETSTATUS over BLE, or still waiting on the
        previous poll.
        """
        for device in self.data or []:
            client = device.client
            if (
                not isinstance(client, OasisBleClient)
                or not client.is_connected
                or client.legacy_firmware
                or client.address in self._ble_polling
            ):
                continue
            self._ble_polling.add(client.address)
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_ble_poll_device(client, device),
                f"{DOMAIN} BLE status poll {client.address}",
            )

    async def _async_ble_poll_device(
        self, client: OasisBleClient, device: OasisDevice
    ) -> None:
        """Request a device's status over BLE, logging rather than raising errors."""
        try:
            await client.async_get_status(device)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Error polling status over BLE from %s: %s", client.address, err
            )
        finally:
            self._ble_polling.discard(client.address)

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
            or address in self._ble_disabled
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

        if not self.bluetooth_enabled(serial):
            _LOGGER.debug(
                "Bluetooth is turned off for Oasis device %s (%s); disconnecting",
                serial,
                address,
            )
            self._ble_disabled[address] = serial
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
        self._async_select_client(device)
        try:
            await client.async_get_all(device)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error fetching status over BLE from %s: %s", address, err)

    @callback
    def _async_ble_disconnected(self, address: str) -> None:
        """Fall back to the next transport when a BLE connection drops and reconnect.

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
            _LOGGER.info("Bluetooth lost for Oasis device %s", device.serial_number)
        self._async_select_client(device)

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
        """Reconnect a dropped BLE client with backoff, then use it if it's preferred."""
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
        self._async_select_client(device)
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
        for serial in list(self._http_tasks):
            self._async_stop_http(serial)
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
