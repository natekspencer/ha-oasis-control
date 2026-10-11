"""Oasis device sensor entity."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
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
    Set up and register sensor entities for each Oasis device in the config entry.

    Creates sensor entities for every Oasis device available on the provided config entry and adds them to Home Assistant via the provided add-entities callback.

    Parameters:
        hass (HomeAssistant): Home Assistant core object.
        entry (OasisDeviceConfigEntry): Configuration entry containing runtime data and devices to expose.
        async_add_entities (AddEntitiesCallback): Callback to add created entities to Home Assistant.
    """

    def make_entities(new_devices: list[OasisDevice]):
        """
        Create sensor entity instances for each Oasis device and each sensor descriptor.

        Parameters:
            new_devices (list[OasisDevice]): Devices to create sensor entities for.

        Returns:
            list[OasisDeviceSensorEntity]: A list containing one sensor entity per combination of device and descriptor from DESCRIPTORS.
        """
        return [
            OasisDeviceSensorEntity(entry.runtime_data, device, descriptor)
            for device in new_devices
            for descriptor in DESCRIPTORS
        ]

    setup_platform_from_coordinator(entry, async_add_entities, make_entities)


DESCRIPTORS = [
    SensorEntityDescription(
        key="download_progress",
        translation_key="download_progress",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="drawing_progress",
        translation_key="drawing_progress",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="last_updated",
        translation_key="last_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
]
DESCRIPTORS.extend(
    SensorEntityDescription(
        key=key,
        translation_key=key,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    )
    for key in ("error", "led_color_id", "status")
)


class OasisDeviceSensorEntity(OasisDeviceEntity, SensorEntity):
    """Oasis device sensor entity."""

    @property
    def native_value(self) -> str | int | float | datetime | None:
        """Provide the current sensor value from the underlying device."""
        return getattr(self.device, self.entity_description.key)
