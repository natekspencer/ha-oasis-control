"""Oasis device image entity."""

from __future__ import annotations

from homeassistant.components.image import Image, ImageEntity, ImageEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util import dt as dt_util

from . import OasisDeviceConfigEntry, setup_platform_from_coordinator
from .coordinator import OasisDeviceCoordinator
from .entity import OasisDeviceEntity
from .pyoasiscontrol import OasisDevice
from .pyoasiscontrol.utils import get_image_url_from_track


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up image entities for Oasis devices from a config entry.

    Creates an OasisDeviceImageEntity for each device in the entry's runtime data and registers them with Home Assistant.

    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        entry (OasisDeviceConfigEntry): Config entry containing runtime data and device registrations.
        async_add_entities (AddEntitiesCallback): Callback to add created entities to Home Assistant.
    """

    def make_entities(new_devices: list[OasisDevice]):
        """
        Create an Image entity for each OasisDevice using the enclosing config entry's runtime data.

        Parameters:
            new_devices (list[OasisDevice]): Devices to create image entities for.

        Returns:
            list[OasisDeviceImageEntity]: A list of image entity instances, one per device.
        """
        return [
            OasisDeviceImageEntity(entry.runtime_data, device, IMAGE)
            for device in new_devices
        ]

    setup_platform_from_coordinator(entry, async_add_entities, make_entities)


IMAGE = ImageEntityDescription(key="image", name=None)


class OasisDeviceImageEntity(OasisDeviceEntity, ImageEntity):
    """Oasis device image entity."""

    _track_id: int | None = None
    _progress: int = 0

    def __init__(
        self,
        coordinator: OasisDeviceCoordinator,
        device: OasisDevice,
        description: ImageEntityDescription,
    ) -> None:
        """
        Create an Oasis device image entity tied to a coordinator and a specific device.

        Initializes the entity with the provided coordinator, device, and image description and synchronizes its initial state from the coordinator.

        Parameters:
            coordinator (OasisDeviceCoordinator): Coordinator providing updates and Home Assistant context.
            device (OasisDevice): The Oasis device this entity represents.
            description (ImageEntityDescription): Metadata describing the image entity.
        """
        super().__init__(coordinator, device, description)
        ImageEntity.__init__(self, coordinator.hass)
        self._handle_coordinator_update()

    def image(self) -> bytes | None:
        """
        Provide the entity's image bytes, generating and caching an SVG from the device when available.

        If the device cannot produce an SVG, the entity's image URL and last-updated timestamp are set and no bytes are returned. When an SVG is produced, the content type is set to "image/svg+xml" and the SVG bytes are cached for future calls.

        Returns:
            bytes: The image content bytes, or `None` if no image is available yet.
        """
        if not self._cached_image:
            if (svg := self.device.create_svg()) is None:
                self._attr_image_url = get_image_url_from_track(
                    self.device.track, self.device.progress
                )
                self._attr_image_last_updated = dt_util.now()
                return None
            self._attr_content_type = "image/svg+xml"
            self._cached_image = Image(self.content_type, svg.encode())
        return self._cached_image.content

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Update image metadata and cached image when the coordinator reports changes to
        the device's track or progress.

        If the device's track_id or progress changed and updates are allowed (the device
        is playing or there is no cached image), update image last-updated timestamp,
        record the new track_id and progress, clear the cached image to force
        regeneration, and set the image URL to UNDEFINED when the track contains inline
        SVG content or to the progress-aware URL derived from the track (via
        get_image_url_from_track) otherwise. When Home Assistant is available, propagate
         the update to the base class handler.
        """
        device = self.device

        track_changed = self._track_id != device.track_id
        progress_changed = self._progress != device.progress
        allow_update = device.status == "playing" or self._cached_image is None

        if (track_changed or progress_changed) and allow_update:
            self._attr_image_last_updated = self.coordinator.last_updated
            self._track_id = device.track_id
            self._progress = device.progress
            self._cached_image = None

            if device.track and device.track.get("svg_content"):
                self._attr_image_url = UNDEFINED
            else:
                self._attr_image_url = get_image_url_from_track(
                    device.track, device.progress
                )

        if self.hass:
            super()._handle_coordinator_update()
