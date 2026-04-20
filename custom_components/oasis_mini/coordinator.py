"""Oasis devices coordinator."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .pyoasiscontrol import OasisCloudClient, OasisDevice, OasisMqttClient

if TYPE_CHECKING:
    from . import OasisDeviceConfigEntry

_LOGGER = logging.getLogger(__name__)


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
                    mqtt_token = await self.cloud_client.async_get_mqtt_token()
                    self.mqtt_client.start(username, mqtt_token)
                self.mqtt_client.register_devices(devices)

                # Best-effort playlists
                try:
                    await self.cloud_client.async_get_playlists()
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

    async def async_close(self) -> None:
        """Close client connections."""
        await asyncio.gather(
            self.mqtt_client.async_close(),
            self.cloud_client.async_close(),
            return_exceptions=True,
        )
