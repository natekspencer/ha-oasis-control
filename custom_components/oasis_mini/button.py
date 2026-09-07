"""Oasis device button entity."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import random

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OasisDeviceConfigEntry, setup_platform_from_coordinator
from .entity import OasisDeviceEntity
from .helpers import add_and_play_track
from .pyoasiscontrol import OasisDevice
from .pyoasiscontrol.const import TRACKS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Create and add button entities for each Oasis device defined in the config entry.

    Parameters:
        entry (OasisDeviceConfigEntry): Config entry containing runtime data and registered Oasis devices.
        async_add_entities (AddEntitiesCallback): Callback used to register the created entities with Home Assistant.
    """

    def make_entities(new_devices: list[OasisDevice]):
        """
        Create button entities for each provided Oasis device using the module descriptors.

        Parameters:
            new_devices (list[OasisDevice]): Devices to create button entities for.

        Returns:
            list[OasisDeviceButtonEntity]: Button entity instances created for each device and each descriptor in DESCRIPTORS.
        """
        return [
            OasisDeviceButtonEntity(entry.runtime_data, device, descriptor)
            for device in new_devices
            for descriptor in DESCRIPTORS
        ]

    setup_platform_from_coordinator(entry, async_add_entities, make_entities)


async def play_random_track(device: OasisDevice) -> None:
    """
    Play a random track on the given Oasis device.

    Selects a track at random from the available TRACKS and attempts to add it to the device's queue and play it. Raises HomeAssistantError if adding the track times out.

    Parameters:
        device: The Oasis device on which to play the track.

    Raises:
        HomeAssistantError: If adding the selected track to the device's queue times out.
    """
    track = random.choice(list(TRACKS))
    try:
        await add_and_play_track(device, track)
    except TimeoutError as err:
        raise HomeAssistantError("Timeout adding track to queue") from err


@dataclass(frozen=True, kw_only=True)
class OasisDeviceButtonEntityDescription(ButtonEntityDescription):
    """Oasis device button entity description."""

    press_fn: Callable[[OasisDevice], Awaitable[None]]


DESCRIPTORS = (
    OasisDeviceButtonEntityDescription(
        key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda device: device.async_reboot(),
    ),
    OasisDeviceButtonEntityDescription(
        key="random_track",
        translation_key="random_track",
        press_fn=play_random_track,
    ),
    OasisDeviceButtonEntityDescription(
        key="sleep",
        translation_key="sleep",
        press_fn=lambda device: device.async_sleep(),
    ),
)


class OasisDeviceButtonEntity(OasisDeviceEntity, ButtonEntity):
    """Oasis device button entity."""

    entity_description: OasisDeviceButtonEntityDescription

    async def async_press(self) -> None:
        """
        Trigger the button's configured action on the associated device.

        Calls the entity description's `press_fn` with the device to perform the button's effect.
        """
        await self.entity_description.press_fn(self.device)
