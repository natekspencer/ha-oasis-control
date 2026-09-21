"""Oasis device update entity."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OasisDeviceConfigEntry, setup_platform_from_coordinator
from .entity import OasisDeviceEntity
from .pyoasiscontrol import OasisDevice
from .pyoasiscontrol.const import STATUS_UPDATING

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=6)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up update entities for Oasis devices from a configuration entry.

    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        entry (OasisDeviceConfigEntry): Config entry containing runtime data used to create device update entities.
        async_add_entities (AddEntitiesCallback): Callback to add created entities to Home Assistant.
    """

    def make_entities(new_devices: list[OasisDevice]):
        """
        Create update entities for the given Oasis devices.

        Parameters:
            new_devices (list[OasisDevice]): Devices to create update entities for.

        Returns:
            list: A list of OasisDeviceUpdateEntity instances corresponding to each device.
        """
        return [
            OasisDeviceUpdateEntity(entry.runtime_data, device, DESCRIPTOR)
            for device in new_devices
        ]

    setup_platform_from_coordinator(entry, async_add_entities, make_entities, True)


DESCRIPTOR = UpdateEntityDescription(
    key="software", device_class=UpdateDeviceClass.FIRMWARE
)


class OasisDeviceUpdateEntity(OasisDeviceEntity, UpdateEntity):
    """Oasis device update entity."""

    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    @property
    def in_progress(self) -> bool | int:
        """Update installation progress."""
        if self.device.status_code == STATUS_UPDATING:
            return self.device.download_progress
        return False

    @property
    def installed_version(self) -> str:
        """Version installed and in use."""
        return self.device.software_version

    @property
    def should_poll(self) -> bool:
        """Set polling to True."""
        return True

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """
        Trigger installation of the latest available update on the device.

        If the latest available version matches the device's currently installed software version, no action is taken. Otherwise an upgrade is started on the device.

        Parameters:
            version (str | None): Ignored by this implementation; the entity uses its known latest version.
            backup (bool): Ignored by this implementation.
            **kwargs: Additional keyword arguments are ignored.
        """
        if self.latest_version == self.device.software_version:
            return
        await self.device.async_upgrade()

    async def async_update(self) -> None:
        """
        Refreshes this entity's latest software metadata.

        Fetches the latest software details from the coordinator's cloud client and updates
        the entity's `latest_version`, `release_summary`, and `release_url` attributes.
        If no software details are returned, the entity's attributes are left unchanged.
        """
        client = self.coordinator.cloud_client
        if not (software := await client.async_get_latest_software_details()):
            _LOGGER.warning("Unable to get latest software details")
            return
        self._attr_latest_version = software["version"]
        self._attr_release_summary = software["description"]
        self._attr_release_url = f"https://app.grounded.so/software/{software['id']}"
