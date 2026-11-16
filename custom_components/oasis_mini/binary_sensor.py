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

    is_on_fn: Callable[[OasisDeviceCoordinator, OasisDevice], bool | None]


def remote_access(
    coordinator: OasisDeviceCoordinator, device: OasisDevice
) -> bool | None:
    """Return whether the device can be controlled remotely, if known.

    This is "Remote access" in the app: without it, the device can't be reached
    through the cloud broker (MQTT).
    """
    # A device publishing over the cloud broker is evidently connected to it,
    # unless BLE has reported otherwise since
    seen = coordinator.mqtt_last_seen(device)
    reported = device.remote_access_updated
    if seen is not None and (reported is None or seen >= reported):
        return True
    # Otherwise BLE's report is used; a quiet broker alone doesn't prove the
    # device is offline, so it's unknown without one
    if device.remote_access is None:
        return None
    return device.remote_access == "active"


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
    OasisBinarySensorEntityDescription(
        key="remote_access",
        translation_key="remote_access",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        is_on_fn=remote_access,
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
    def is_on(self) -> bool | None:
        """Return whether the binary sensor is on."""
        return self.entity_description.is_on_fn(self.coordinator, self.device)
