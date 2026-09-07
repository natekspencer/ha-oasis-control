"""Oasis device number entity."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OasisDeviceConfigEntry, setup_platform_from_coordinator
from .entity import OasisDeviceEntity
from .pyoasiscontrol import OasisDevice
from .pyoasiscontrol.device import (
    BALL_SPEED_MAX,
    BALL_SPEED_MIN,
    LED_SPEED_MAX,
    LED_SPEED_MIN,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up number entities for Oasis devices from a configuration entry.

    Creates number entities for each discovered Oasis device and each descriptor in DESCRIPTORS, then registers those entities with the platform coordinator so they are added to Home Assistant.

    Parameters:
        hass (HomeAssistant): Home Assistant core object.
        entry (OasisDeviceConfigEntry): Configuration entry containing runtime data and devices to expose.
        async_add_entities (AddEntitiesCallback): Callback to add created entities to Home Assistant.
    """

    def make_entities(new_devices: list[OasisDevice]):
        """
        Create number entity instances for each provided Oasis device using the module's DESCRIPTORS.

        Parameters:
            new_devices (list[OasisDevice]): Devices to create entities for.

        Returns:
            list[OasisDeviceNumberEntity]: A flat list of number entities (one per descriptor for each device).
        """
        return [
            OasisDeviceNumberEntity(entry.runtime_data, device, descriptor)
            for device in new_devices
            for descriptor in DESCRIPTORS
        ]

    setup_platform_from_coordinator(entry, async_add_entities, make_entities)


DESCRIPTORS = (
    NumberEntityDescription(
        key="ball_speed",
        translation_key="ball_speed",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.SLIDER,
        native_max_value=BALL_SPEED_MAX,
        native_min_value=BALL_SPEED_MIN,
    ),
    NumberEntityDescription(
        key="led_speed",
        translation_key="led_speed",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.SLIDER,
        native_max_value=LED_SPEED_MAX,
        native_min_value=LED_SPEED_MIN,
    ),
)


class OasisDeviceNumberEntity(OasisDeviceEntity, NumberEntity):
    """Oasis device number entity."""

    @property
    def native_value(self) -> float | None:
        """
        Get the current value of the number entity from the underlying device.

        Returns:
            float | None: The current value as a float, or `None` if the device has no value.
        """
        return getattr(self.device, self.entity_description.key)

    async def async_set_native_value(self, value: float) -> None:
        """
        Set the configured numeric value on the underlying Oasis device.

        The provided value is converted to an integer and applied to the device property indicated by this entity's description key: if the key is "ball_speed" the device's ball speed is updated; if the key is "led_speed" the device's LED speed is updated.

        Parameters:
            value (float): New numeric value to apply; will be converted to an integer.
        """
        value = int(value)
        if self.entity_description.key == "ball_speed":
            await self.device.async_set_ball_speed(value)
        elif self.entity_description.key == "led_speed":
            await self.device.async_set_led(led_speed=value)
