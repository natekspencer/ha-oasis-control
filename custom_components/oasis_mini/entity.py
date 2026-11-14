"""Oasis device entity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    CONNECTION_NETWORK_MAC,
    format_mac,
)
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OasisDeviceCoordinator
from .pyoasiscontrol import OasisDevice


@dataclass(frozen=True, kw_only=True)
class OasisEntityDescription(EntityDescription):
    """Description fields shared by Oasis entities of every platform.

    Attributes:
        local_only: Doesn't depend on the cloud, so stays available while the
            device is on the account even if the cloud refresh fails (e.g. during
            reauth).
        available_fn: Further condition for the entity to be available.
        requires_initialized: Only created once the device is fully identified.
            Entities that control or report how the device is reached are created
            right away, so a device that can't be reached still has them.
        requires_bluetooth: Only created when Home Assistant's Bluetooth
            integration is loaded.
    """

    local_only: bool = False
    available_fn: Callable[[OasisDeviceCoordinator, OasisDevice], bool] | None = None
    requires_initialized: bool = True
    requires_bluetooth: bool = False


class OasisDeviceEntity(CoordinatorEntity[OasisDeviceCoordinator]):
    """Base class for Oasis device entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OasisDeviceCoordinator,
        device: OasisDevice,
        description: EntityDescription,
    ) -> None:
        """
        Initialize an entity representing an Oasis device.

        Sets the entity's unique_id from the device serial number and the provided description key, stores the given device on the entity, and constructs DeviceInfo containing identifiers, name, manufacturer, model, software version, and a network MAC connection if the device exposes a MAC address.

        Parameters:
            coordinator: The coordinator responsible for updating the device state.
            device: OasisDevice instance providing metadata and identifiers (serial_number, mac_address, name, manufacturer, model, software_version).
            description: EntityDescription used to derive the entity key for the unique_id.
        """
        super().__init__(coordinator)
        self.device = device
        self.entity_description = description

        serial_number = device.serial_number
        self._attr_unique_id = f"{serial_number}-{description.key}"

        connections = set()
        if mac_address := device.mac_address:
            connections.add((CONNECTION_NETWORK_MAC, format_mac(mac_address)))
        connections.update(
            (CONNECTION_BLUETOOTH, format_mac(address))
            for address, client in coordinator.ble_clients.items()
            if client.serial_number == serial_number
        )

        self._attr_device_info = DeviceInfo(
            connections=connections,
            identifiers={(DOMAIN, serial_number)},
            name=device.name,
            manufacturer=device.manufacturer,
            model=device.model,
            serial_number=serial_number,
            sw_version=device.software_version,
        )

    @property
    def available(self) -> bool:
        """Return whether the entity is available, per its description."""
        description = self.entity_description
        if getattr(description, "local_only", False):
            available = any(d is self.device for d in self.coordinator.data or [])
        else:
            available = super().available
        if available and (fn := getattr(description, "available_fn", None)):
            available = fn(self.coordinator, self.device)
        return available
