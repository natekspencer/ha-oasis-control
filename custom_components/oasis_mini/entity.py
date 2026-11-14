"""Oasis device entity."""

from __future__ import annotations

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
