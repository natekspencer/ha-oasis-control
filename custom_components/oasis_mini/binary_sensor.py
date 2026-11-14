"""Oasis device binary sensor entity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
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
    """Set up Oasis device binary sensor entities for a config entry."""
    setup_described_entities(
        entry, async_add_entities, OasisDeviceBinarySensorEntity, DESCRIPTORS
    )


@dataclass(frozen=True, kw_only=True)
class OasisBinarySensorEntityDescription(
    OasisEntityDescription, BinarySensorEntityDescription
):
    """Oasis device binary sensor entity description."""

    is_on_fn: Callable[[OasisDeviceCoordinator, OasisDevice], bool]


DESCRIPTORS = (
    OasisBinarySensorEntityDescription(
        key="busy",
        translation_key="busy",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        is_on_fn=lambda _, device: device.busy,
    ),
    OasisBinarySensorEntityDescription(
        key="wifi_connected",
        translation_key="wifi_status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        is_on_fn=lambda _, device: device.wifi_connected,
    ),
    # Whether the device is reachable and has been heard from recently, over any
    # connection. Unlike a last-updated timestamp, it only changes state when the
    # device stops or starts responding, not on every status update while it draws.
    # Created right away, so a device that never answers shows as not responding
    OasisBinarySensorEntityDescription(
        key="responding",
        translation_key="responding",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        local_only=True,
        requires_initialized=False,
        is_on_fn=lambda coordinator, device: coordinator.device_connected(device),
    ),
)


class OasisDeviceBinarySensorEntity(OasisDeviceEntity, BinarySensorEntity):
    """Oasis device binary sensor entity."""

    entity_description: OasisBinarySensorEntityDescription

    @property
    def is_on(self) -> bool:
        """Return whether the binary sensor is on."""
        return self.entity_description.is_on_fn(self.coordinator, self.device)
