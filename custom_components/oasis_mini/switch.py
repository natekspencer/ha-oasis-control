"""Oasis device switch entity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
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
    Set up Oasis device switch entities for a config entry.

    Creates an OasisDeviceSwitchEntity for each OasisDevice associated with the given config entry (one entity per descriptor in DESCRIPTORS) and registers them with Home Assistant via the coordinator helper.

    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        entry (OasisDeviceConfigEntry): Config entry containing runtime data used to create device update entities.
        async_add_entities (AddEntitiesCallback): Callback to add created entities to Home Assistant.
    """

    def make_entities(new_devices: list[OasisDevice]):
        """
        Create OasisDeviceSwitchEntity instances for each device and descriptor.

        Parameters:
            new_devices (list[OasisDevice]): Devices to wrap as switch entities.

        Returns:
            list[OasisDeviceSwitchEntity]: A list containing one switch entity per device per descriptor from DESCRIPTORS.
        """
        return [
            OasisDeviceSwitchEntity(entry.runtime_data, device, descriptor)
            for device in new_devices
            for descriptor in DESCRIPTORS
        ]

    setup_platform_from_coordinator(entry, async_add_entities, make_entities)


DESCRIPTORS = (
    SwitchEntityDescription(
        key="auto_clean",
        translation_key="auto_clean",
        entity_category=EntityCategory.CONFIG,
    ),
)


class OasisDeviceSwitchEntity(OasisDeviceEntity, SwitchEntity):
    """Oasis device switch entity."""

    @property
    def is_on(self) -> bool:
        """
        Determine whether the switch entity is currently on.

        Returns:
            bool: `True` if the underlying device attribute named by this entity's description key is truthy, `False` otherwise.
        """
        return bool(getattr(self.device, self.entity_description.key))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """
        Disable the device's automatic cleaning mode.

        Sets the device's auto_clean setting to off.
        """
        await self.device.async_set_auto_clean(False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """
        Enable the device's auto-clean feature.
        """
        await self.device.async_set_auto_clean(True)
