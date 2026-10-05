"""Oasis device select entity."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OasisDeviceConfigEntry, setup_platform_from_coordinator
from .coordinator import OasisDeviceCoordinator
from .entity import OasisDeviceEntity
from .pyoasiscontrol import OasisDevice
from .pyoasiscontrol.const import AUTOPLAY_MAP, TRACKS

AUTOPLAY_MAP_LIST = list(AUTOPLAY_MAP)


def playlists_update_handler(entity: OasisDeviceSelectEntity) -> None:
    """
    Update the playlists select options and current option from the device's cloud playlists.

    Iterates the device's cloud playlists to build a display list of playlist names (appending " (N)" for duplicate names)
    and sets the entity's options to that list. If the device's current playlist matches a playlist's pattern IDs,
    sets the entity's current option to that playlist's display name; otherwise leaves it None.

    Parameters:
        entity (OasisDeviceSelectEntity): The select entity to update.
    """
    # pylint: disable=protected-access
    device = entity.device
    counts = defaultdict(int)
    options = []
    current_option: str | None = None
    for playlist in device._cloud.playlists:
        name = playlist["name"]
        counts[name] += 1
        if counts[name] > 1:
            name = f"{name} ({counts[name]})"
        options.append(name)
        if device.playlist == [pattern["id"] for pattern in playlist["patterns"]]:
            current_option = name
    entity._attr_options = options
    entity._attr_current_option = current_option


def queue_update_handler(entity: OasisDeviceSelectEntity) -> None:
    """
    Update the select options and current selection for the device's playback queue.

    Populate the entity's options from the device's current playlist and playlist details, disambiguating duplicate track names by appending a counter (e.g., "Title (2)"). Set the entity's current option to the track at device.playlist_index (or None if the queue is empty).

    Parameters:
        entity (OasisDeviceSelectEntity): The select entity whose options and current option will be updated.
    """
    # pylint: disable=protected-access
    device = entity.device
    counts = defaultdict(int)
    options = []
    for track in device.playlist:
        name = device.playlist_details.get(track, {}).get(
            "name",
            TRACKS.get(track, {"id": track, "name": f"Unknown Title (#{track})"}).get(
                "name",
                device.track["name"]
                if device.track and device.track["id"] == track
                else str(track),
            ),
        )
        counts[name] += 1
        if counts[name] > 1:
            name = f"{name} ({counts[name]})"
        options.append(name)
    entity._attr_options = options
    index = min(device.playlist_index, len(options) - 1)
    entity._attr_current_option = options[index] if options else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Set up select entities for each Oasis device from a config entry.

    Creates OasisDeviceSelectEntity instances for every device and descriptor and registers them with Home Assistant via the platform setup.

    Parameters:
        hass (HomeAssistant): Home Assistant core object.
        entry (OasisDeviceConfigEntry): Configuration entry containing runtime data and devices to expose.
        async_add_entities (AddEntitiesCallback): Callback to add created entities to Home Assistant.
    """

    def make_entities(new_devices: list[OasisDevice]):
        """
        Create select entity instances for each provided Oasis device.

        Parameters:
            new_devices (list[OasisDevice]): Devices to create select entities for.

        Returns:
            list[OasisDeviceSelectEntity]: A flat list of OasisDeviceSelectEntity objects created for every combination of device and descriptor.
        """
        return [
            OasisDeviceSelectEntity(entry.runtime_data, device, descriptor)
            for device in new_devices
            for descriptor in DESCRIPTORS
        ]

    setup_platform_from_coordinator(entry, async_add_entities, make_entities)


@dataclass(frozen=True, kw_only=True)
class OasisDeviceSelectEntityDescription(SelectEntityDescription):
    """Oasis device select entity description."""

    current_value: Callable[[OasisDevice], Any]
    select_fn: Callable[[OasisDevice, int], Awaitable[None]]
    update_handler: Callable[[OasisDeviceSelectEntity], None] | None = None


DESCRIPTORS = (
    OasisDeviceSelectEntityDescription(
        key="autoplay",
        translation_key="autoplay",
        entity_category=EntityCategory.CONFIG,
        options=AUTOPLAY_MAP_LIST,
        current_value=lambda device: str(device.autoplay),
        select_fn=lambda device, index: device.async_set_autoplay(
            AUTOPLAY_MAP_LIST[index]
        ),
    ),
    OasisDeviceSelectEntityDescription(
        key="playlists",
        translation_key="playlist",
        current_value=lambda device: (device._cloud.playlists, device.playlist.copy()),
        select_fn=lambda device, index: device.async_set_playlist(
            [pattern["id"] for pattern in device._cloud.playlists[index]["patterns"]]
        ),
        update_handler=playlists_update_handler,
    ),
    OasisDeviceSelectEntityDescription(
        key="queue",
        translation_key="queue",
        current_value=lambda device: (device.playlist.copy(), device.playlist_index),
        select_fn=lambda device, index: device.async_change_track(index),
        update_handler=queue_update_handler,
    ),
)


class OasisDeviceSelectEntity(OasisDeviceEntity, SelectEntity):
    """Oasis device select entity."""

    entity_description: OasisDeviceSelectEntityDescription
    _current_value: Any | None = None

    def __init__(
        self,
        coordinator: OasisDeviceCoordinator,
        device: OasisDevice,
        description: EntityDescription,
    ) -> None:
        """
        Initialize the Oasis device select entity and perform an initial coordinator update.

        Parameters:
            coordinator (OasisDeviceCoordinator): Coordinator that manages device updates.
            device (OasisDevice): The Oasis device this entity represents.
            description (EntityDescription): Metadata describing this select entity.
        """
        super().__init__(coordinator, device, description)
        self._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """
        Select and apply the option identified by its display string.

        Parameters:
            option (str): The display string of the option to select; the option's index in the current options list is used to apply the selection.
        """
        await self.entity_description.select_fn(self.device, self.options.index(option))

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Update the entity's cached value and current option when coordinator data changes.

        If the derived current value differs from the stored value, update the stored value.
        If the entity description provides an update_handler, call it with this entity; otherwise,
        set the entity's current option to the string form of the device attribute named by the
        description's key. If Home Assistant is available on the entity, delegate to the base
        class's _handle_coordinator_update to propagate the state change.
        """
        new_value = self.entity_description.current_value(self.device)
        if self._current_value == new_value:
            return
        self._current_value = new_value
        if update_handler := self.entity_description.update_handler:
            update_handler(self)
        else:
            self._attr_current_option = str(
                getattr(self.device, self.entity_description.key)
            )
        if self.hass:
            return super()._handle_coordinator_update()
