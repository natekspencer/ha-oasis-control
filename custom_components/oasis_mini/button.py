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

from . import OasisDeviceConfigEntry, setup_described_entities
from .coordinator import OasisDeviceCoordinator
from .entity import OasisDeviceEntity, OasisEntityDescription
from .helpers import add_and_play_track
from .pyoasiscontrol import OasisDevice
from .pyoasiscontrol.const import TRACKS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oasis device button entities for a config entry."""
    setup_described_entities(
        entry, async_add_entities, OasisDeviceButtonEntity, DESCRIPTORS
    )


async def play_random_track(
    coordinator: OasisDeviceCoordinator, device: OasisDevice
) -> None:
    """
    Play a random track on the given Oasis device.

    Raises:
        HomeAssistantError: If adding the selected track to the device's queue times out.
    """
    track = random.choice(list(TRACKS))
    try:
        await add_and_play_track(device, track)
    except TimeoutError as err:
        raise HomeAssistantError("Timeout adding track to queue") from err


@dataclass(frozen=True, kw_only=True)
class OasisButtonEntityDescription(OasisEntityDescription, ButtonEntityDescription):
    """Oasis device button entity description."""

    press_fn: Callable[[OasisDeviceCoordinator, OasisDevice], Awaitable[None]]


DESCRIPTORS = (
    OasisButtonEntityDescription(
        key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda _, device: device.async_reboot(),
    ),
    OasisButtonEntityDescription(
        key="random_track",
        translation_key="random_track",
        press_fn=play_random_track,
    ),
    OasisButtonEntityDescription(
        key="sleep",
        translation_key="sleep",
        press_fn=lambda _, device: device.async_sleep(),
    ),
    # Turns on the device's local HTTP API over Bluetooth. HTTP mode turns off
    # whenever the table restarts, and can only be turned on over Bluetooth, so
    # the button is only available while that's connected
    OasisButtonEntityDescription(
        key="enable_http_mode",
        translation_key="enable_http_mode",
        entity_category=EntityCategory.CONFIG,
        local_only=True,
        requires_bluetooth=True,
        available_fn=lambda coordinator, device: coordinator.bluetooth_connected(
            device
        ),
        press_fn=lambda coordinator, device: coordinator.async_enable_http_mode(device),
    ),
)


class OasisDeviceButtonEntity(OasisDeviceEntity, ButtonEntity):
    """Oasis device button entity."""

    entity_description: OasisButtonEntityDescription

    async def async_press(self) -> None:
        """Run the button's action on the device."""
        await self.entity_description.press_fn(self.coordinator, self.device)
