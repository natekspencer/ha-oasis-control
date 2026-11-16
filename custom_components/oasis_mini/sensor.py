"""Oasis device sensor entity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import OasisDeviceConfigEntry, setup_described_entities
from .coordinator import OasisDeviceCoordinator
from .entity import OasisDeviceEntity, OasisEntityDescription
from .pyoasiscontrol import OasisDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oasis device sensor entities for a config entry."""
    setup_described_entities(
        entry, async_add_entities, OasisDeviceSensorEntity, DESCRIPTORS
    )


@dataclass(frozen=True, kw_only=True)
class OasisSensorEntityDescription(OasisEntityDescription, SensorEntityDescription):
    """Oasis device sensor entity description."""

    value_fn: Callable[[OasisDeviceCoordinator, OasisDevice], StateType]


def remote_access(
    coordinator: OasisDeviceCoordinator, device: OasisDevice
) -> str | None:
    """Return whether the device can be controlled remotely, if known.

    This is "Remote access" in the app: without it, the device can't be reached
    through the cloud broker (MQTT).
    """
    # A device publishing over the cloud broker is evidently connected to it,
    # unless BLE has reported otherwise since
    seen = coordinator.mqtt_last_seen(device)
    reported = device.remote_access_updated
    if seen is not None and (reported is None or seen >= reported):
        return "active"
    # Otherwise BLE's report is used; a quiet broker alone doesn't prove the
    # device is offline, so it's unknown without one
    return device.remote_access


DESCRIPTORS = (
    OasisSensorEntityDescription(
        key="download_progress",
        translation_key="download_progress",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda _, device: device.download_progress,
    ),
    OasisSensorEntityDescription(
        key="drawing_progress",
        translation_key="drawing_progress",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda _, device: device.drawing_progress,
    ),
    OasisSensorEntityDescription(
        key="error",
        translation_key="error",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _, device: device.error,
    ),
    OasisSensorEntityDescription(
        key="led_color_id",
        translation_key="led_color_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _, device: device.led_color_id,
    ),
    OasisSensorEntityDescription(
        key="status",
        translation_key="status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _, device: device.status,
    ),
    OasisSensorEntityDescription(
        key="connection",
        translation_key="connection",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["local", "bluetooth", "cloud", "disconnected"],
        # How the device is reached doesn't depend on the cloud refresh
        local_only=True,
        value_fn=lambda coordinator, device: coordinator.connection_type(device),
    ),
    OasisSensorEntityDescription(
        key="remote_access",
        translation_key="remote_access",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=["active", "inactive"],
        value_fn=remote_access,
    ),
)


class OasisDeviceSensorEntity(OasisDeviceEntity, SensorEntity):
    """Oasis device sensor entity."""

    entity_description: OasisSensorEntityDescription

    @property
    def native_value(self) -> StateType:
        """Return the sensor's value."""
        return self.entity_description.value_fn(self.coordinator, self.device)
