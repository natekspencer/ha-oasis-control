"""Oasis device image entity."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from typing import Any

from homeassistant.components.image import Image, ImageEntity, ImageEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util import dt as dt_util

from . import OasisDeviceConfigEntry, setup_platform_from_coordinator
from .coordinator import OasisDeviceCoordinator
from .entity import OasisDeviceEntity
from .pyoasiscontrol import OasisDevice
from .pyoasiscontrol.const import TRACKS
from .pyoasiscontrol.utils import create_svg, get_image_url_from_track

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
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


@dataclass
class OasisDeviceImageExtraStoredData(ExtraStoredData):
    """Track and progress last drawn on the sand, persisted across restarts."""

    track_id: int | None
    progress: int

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the extra stored data."""
        return asdict(self)

    @classmethod
    def from_dict(
        cls, restored: dict[str, Any]
    ) -> OasisDeviceImageExtraStoredData | None:
        """Initialize the extra stored data from a dict, or return None if invalid."""
        try:
            return cls(restored["track_id"], int(restored["progress"]))
        except (KeyError, TypeError, ValueError):
            return None


class OasisDeviceImageEntity(OasisDeviceEntity, ImageEntity, RestoreEntity):
    """
    Oasis device image entity.

    Shows the track that is currently drawn on the sand. When a track finishes, the
    device advances to the next track in the playlist before it starts drawing it, so
    the image only follows the device's current track once that track is actively
    being drawn (playing with progress beyond the first line).
    """

    _track_id: int | None = None
    _track: dict[str, Any] | None = None
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

    @property
    def extra_restore_state_data(self) -> OasisDeviceImageExtraStoredData:
        """Return the track and progress currently shown so they survive a restart."""
        return OasisDeviceImageExtraStoredData(self._track_id, self._progress)

    async def async_added_to_hass(self) -> None:
        """
        Restore the last drawn track and progress unless the device is actively drawing.

        After a restart the device may already point at the next track in the playlist
        while the sand still shows the previously drawn track, so the restored state is
        preferred until the device starts drawing again.
        """
        await super().async_added_to_hass()

        if self._is_drawing():
            return
        if (extra := await self.async_get_last_extra_data()) is None or (
            restored := OasisDeviceImageExtraStoredData.from_dict(extra.as_dict())
        ) is None:
            return
        if (track_id := restored.track_id) is None:
            return
        # Device progress on the same track is more current than what was stored
        if track_id == self.device.track_id and self.device.progress > 0:
            return

        if track_id == self.device.track_id and self.device.track:
            track = self.device.track
        else:
            track = TRACKS.get(track_id)
            try:
                if fetched := await self.coordinator.cloud_client.async_get_track_info(
                    track_id
                ):
                    track = fetched
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Unable to fetch track info for %s", track_id)

        self._set_image(track_id, track, restored.progress)
        if (last_state := await self.async_get_last_state()) and (
            last_updated := dt_util.parse_datetime(last_state.state)
        ):
            self._attr_image_last_updated = last_updated
        self.async_write_ha_state()

    def image(self) -> bytes | None:
        """
        Provide the entity's image bytes, generating and caching an SVG of the displayed track when available.

        If no SVG can be produced, the image URL and last-updated timestamp are set and no bytes are returned. When an SVG is produced, the content type is set to "image/svg+xml" and the SVG bytes are cached for future calls.

        Returns:
            bytes: The image content bytes, or `None` if no image is available yet.
        """
        if not self._cached_image:
            if (svg := create_svg(self._track, self._progress)) is None:
                self._attr_image_url = get_image_url_from_track(
                    self._track, self._progress
                )
                self._attr_image_last_updated = dt_util.now()
                return None
            self._attr_content_type = "image/svg+xml"
            self._cached_image = Image(self.content_type, svg.encode())
        return self._cached_image.content

    def _is_drawing(self) -> bool:
        """Return whether the device is actively drawing its current track."""
        return self.device.status == "playing" and self.device.progress > 0

    def _set_image(
        self, track_id: int | None, track: dict | None, progress: int
    ) -> None:
        """Show the given track at the given progress and invalidate the cached image."""
        self._attr_image_last_updated = self.coordinator.last_updated or dt_util.now()
        self._track_id = track_id
        self._track = track
        self._progress = progress
        self._cached_image = None

        if track and track.get("svg_content"):
            self._attr_image_url = UNDEFINED
        else:
            self._attr_image_url = get_image_url_from_track(track, progress)

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Update the displayed track and progress when the coordinator reports changes.

        The image follows the device's current track only while it is actively being
        drawn, or when nothing has been shown yet. This keeps the finished drawing on
        screen when the device advances to the next track in the playlist without
        drawing it. If more detailed metadata arrives for the displayed track (e.g. from
        the cloud), the image is regenerated with it. When Home Assistant is available,
        propagate the update to the base class handler.
        """
        device = self.device

        if self._track_id is None or self._is_drawing():
            if (
                self._track_id != device.track_id
                or self._progress != device.progress
                or self._track is not device.track
            ):
                self._set_image(device.track_id, device.track, device.progress)
        elif self._track_id == device.track_id and (
            (track := device.track) is not None and track is not self._track
        ):
            self._set_image(self._track_id, track, self._progress)

        if self.hass:
            super()._handle_coordinator_update()
