"""Oasis device switch entity."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OasisDeviceConfigEntry, setup_described_entities
from .coordinator import OasisDeviceCoordinator
from .entity import OasisDeviceEntity, OasisEntityDescription
from .pyoasiscontrol import OasisDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oasis device switch entities for a config entry."""
    setup_described_entities(
        entry, async_add_entities, OasisDeviceSwitchEntity, DESCRIPTORS
    )


@dataclass(frozen=True, kw_only=True)
class OasisSwitchEntityDescription(OasisEntityDescription, SwitchEntityDescription):
    """Oasis device switch entity description."""

    is_on_fn: Callable[[OasisDeviceCoordinator, OasisDevice], bool]
    set_fn: Callable[[OasisDeviceCoordinator, OasisDevice, bool], Awaitable[None]]


DESCRIPTORS = (
    OasisSwitchEntityDescription(
        key="auto_clean",
        translation_key="auto_clean",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda _, device: device.auto_clean,
        set_fn=lambda _, device, on: device.async_set_auto_clean(on),
    ),
    # Whether Home Assistant connects to the device over Bluetooth; it doesn't
    # change the table's own Bluetooth. The table accepts a single Bluetooth
    # connection, so turning this off frees it for the Oasis app. Created right
    # away: a device only reachable over Bluetooth never initializes while it's
    # off, and must be turnable on
    OasisSwitchEntityDescription(
        key="bluetooth",
        translation_key="bluetooth",
        entity_category=EntityCategory.CONFIG,
        local_only=True,
        requires_initialized=False,
        requires_bluetooth=True,
        is_on_fn=lambda coordinator, device: coordinator.bluetooth_enabled(
            device.serial_number
        ),
        set_fn=lambda coordinator, device, on: coordinator.async_set_bluetooth_enabled(
            device, on
        ),
    ),
)


class OasisDeviceSwitchEntity(OasisDeviceEntity, SwitchEntity):
    """Oasis device switch entity."""

    entity_description: OasisSwitchEntityDescription

    @property
    def is_on(self) -> bool:
        """Return whether the switch is on."""
        return self.entity_description.is_on_fn(self.coordinator, self.device)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.entity_description.set_fn(self.coordinator, self.device, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.entity_description.set_fn(self.coordinator, self.device, False)
        self.async_write_ha_state()
