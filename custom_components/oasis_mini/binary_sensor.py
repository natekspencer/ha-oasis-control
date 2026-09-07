"""Oasis device binary sensor entity."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OasisDeviceConfigEntry, setup_platform_from_coordinator
from .entity import OasisDeviceEntity
from .pyoasiscontrol import OasisDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up Oasis device binary sensor entities for a config entry.

    Registers a factory that creates an OasisDeviceBinarySensorEntity for each device and descriptor defined in DESCRIPTORS, and forwards those entities to Home Assistant via the provided add-entities callback.

    Parameters:
        entry (OasisDeviceConfigEntry): Configuration entry for the Oasis integration containing runtime data and coordinator used to create entities.
    """

    def make_entities(new_devices: list[OasisDevice]):
        """
        Create binary sensor entity instances for each provided Oasis device using the module's descriptors.

        Parameters:
            new_devices (list[OasisDevice]): Devices to generate entities for.

        Returns:
            list[OasisDeviceBinarySensorEntity]: A list of binary sensor entities pairing each device with every descriptor in DESCRIPTORS.
        """
        return [
            OasisDeviceBinarySensorEntity(entry.runtime_data, device, descriptor)
            for device in new_devices
            for descriptor in DESCRIPTORS
        ]

    setup_platform_from_coordinator(entry, async_add_entities, make_entities)


DESCRIPTORS = (
    BinarySensorEntityDescription(
        key="busy",
        translation_key="busy",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    BinarySensorEntityDescription(
        key="wifi_connected",
        translation_key="wifi_status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


class OasisDeviceBinarySensorEntity(OasisDeviceEntity, BinarySensorEntity):
    """Oasis device binary sensor entity."""

    @property
    def is_on(self) -> bool:
        """
        Indicates whether the binary sensor is currently active.

        Returns:
            bool: True if the sensor is on, False otherwise.
        """
        return getattr(self.device, self.entity_description.key)
