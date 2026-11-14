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
            list[SensorEntity]: One sensor entity per device and descriptor in DESCRIPTORS, plus a connection sensor per device.
        """
        entities: list[SensorEntity] = [
            OasisDeviceSensorEntity(entry.runtime_data, device, descriptor)
            for device in new_devices
            for descriptor in DESCRIPTORS
        ]
        entities.extend(
            OasisDeviceConnectionSensorEntity(
                entry.runtime_data, device, CONNECTION_DESCRIPTOR
            )
            for device in new_devices
        )
        entities.extend(
            OasisDeviceRemoteAccessSensorEntity(
                entry.runtime_data, device, REMOTE_ACCESS_DESCRIPTOR
            )
            for device in new_devices
        )
        return entities

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


CONNECTION_DESCRIPTOR = SensorEntityDescription(
    key="connection",
    translation_key="connection",
    device_class=SensorDeviceClass.ENUM,
    entity_category=EntityCategory.DIAGNOSTIC,
    options=["local", "bluetooth", "cloud", "disconnected"],
)


class OasisDeviceSensorEntity(OasisDeviceEntity, SensorEntity):
    """Oasis device sensor entity."""

    @property
    def native_value(self) -> str | int | float | datetime | None:
        """Provide the current sensor value from the underlying device."""
        return getattr(self.device, self.entity_description.key)


class OasisDeviceConnectionSensorEntity(OasisDeviceEntity, SensorEntity):
    """Oasis device connection sensor entity."""

    @property
    def native_value(self) -> str:
        """Return how Home Assistant is currently reaching the device."""
        return self.coordinator.connection_type(self.device)


REMOTE_ACCESS_DESCRIPTOR = SensorEntityDescription(
    key="remote_access",
    translation_key="remote_access",
    device_class=SensorDeviceClass.ENUM,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    options=["active", "inactive", "disabled"],
)


class OasisDeviceRemoteAccessSensorEntity(OasisDeviceEntity, SensorEntity):
    """Whether the device can be controlled remotely, "Remote access" in the app.

    Without it, the device is still online (it can download patterns, for
    example), but can't be reached through the cloud broker (MQTT). "disabled"
    means Grounded has turned it off for the device.
    """

    @property
    def native_value(self) -> str | None:
        """Return the device's remote access state, if known."""
        # A device publishing over the cloud broker is evidently connected to it,
        # unless BLE has reported otherwise since
        seen = self.coordinator.mqtt_last_seen(self.device)
        reported = self.device.remote_access_updated
        if seen is not None and (reported is None or seen >= reported):
            return "active"
        # Otherwise BLE's report is used; a quiet broker alone doesn't prove the
        # device is offline, so it's unknown without one
        return self.device.remote_access
