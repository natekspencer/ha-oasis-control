"""Oasis device media player entity."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaPlayerEnqueue,
    MediaPlayerEntity,
    MediaPlayerEntityDescription,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OasisDeviceConfigEntry, setup_platform_from_coordinator
from .browse_media import (
    MEDIA_TYPE_OASIS_PLAYLIST,
    MEDIA_TYPE_OASIS_PLAYLISTS,
    MEDIA_TYPE_OASIS_ROOT,
    MEDIA_TYPE_OASIS_TRACK,
    MEDIA_TYPE_OASIS_TRACKS,
    async_search_media,
    build_playlist_item,
    build_playlists_root,
    build_root_response,
    build_track_item,
    build_tracks_root,
)
from .const import DOMAIN
from .entity import OasisDeviceEntity
from .helpers import get_track_id
from .pyoasiscontrol import OasisDevice
from .pyoasiscontrol.const import (
    STATUS_CENTERING,
    STATUS_DOWNLOADING,
    STATUS_ERROR,
    STATUS_LIVE,
    STATUS_PAUSED,
    STATUS_PLAYING,
    STATUS_STOPPED,
    STATUS_UPDATING,
)
from .pyoasiscontrol.utils import get_track_ids_from_playlist

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oasis device media_players using config entry."""

    def make_entities(new_devices: list[OasisDevice]):
        """
        Create media player entities for the given Oasis devices.

        Parameters:
            new_devices (list[OasisDevice]): Devices to wrap as media player entities.

        Returns:
            list[OasisDeviceMediaPlayerEntity]: Media player entities corresponding to each device.
        """
        return [
            OasisDeviceMediaPlayerEntity(entry.runtime_data, device, DESCRIPTOR)
            for device in new_devices
        ]

    setup_platform_from_coordinator(entry, async_add_entities, make_entities)


DESCRIPTOR = MediaPlayerEntityDescription(key="oasis_mini", name=None)


class OasisDeviceMediaPlayerEntity(OasisDeviceEntity, MediaPlayerEntity):
    """Oasis device media player entity."""

    _attr_media_content_type = MediaType.IMAGE
    _attr_media_image_remotely_accessible = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.MEDIA_ENQUEUE
        | MediaPlayerEntityFeature.CLEAR_PLAYLIST
        | MediaPlayerEntityFeature.REPEAT_SET
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.SEARCH_MEDIA
    )

    @property
    def media_duration(self) -> int | None:
        """Duration of current playing media in seconds."""
        if (track := self.device.track) and "reduced_svg_content_new" in track:
            return track["reduced_svg_content_new"]
        return None

    @property
    def media_image_url(self) -> str | None:
        """
        URL of the image representing the currently playing media.

        Returns:
            The image URL as a string, or `None` if no image is available.
        """
        return self.device.track_image_url

    @property
    def media_position(self) -> int:
        """
        Playback position of the current media in seconds.

        Returns:
            int: Position in seconds of the currently playing media.
        """
        return self.device.progress

    @property
    def media_position_updated_at(self) -> datetime | None:
        """When was the position of the current playing media valid."""
        return self.coordinator.last_updated

    @property
    def media_title(self) -> str | None:
        """
        Provide the title of the currently playing track.

        Returns:
            str | None: The track title, or None if no title is available.
        """
        return self.device.track_name

    @property
    def repeat(self) -> RepeatMode:
        """
        Get the current repeat mode for the device.

        Returns:
            `RepeatMode.ALL` if the device is configured to repeat the playlist, `RepeatMode.OFF` otherwise.
        """
        return RepeatMode.ALL if self.device.repeat_playlist else RepeatMode.OFF

    @property
    def state(self) -> MediaPlayerState:
        """State of the player."""
        status_code = self.device.status_code
        if self.device.error or status_code in (STATUS_ERROR, STATUS_UPDATING):
            return MediaPlayerState.OFF
        if status_code == STATUS_STOPPED:
            return MediaPlayerState.IDLE
        if status_code in (STATUS_CENTERING, STATUS_DOWNLOADING):
            return MediaPlayerState.BUFFERING
        if status_code == STATUS_PLAYING:
            return MediaPlayerState.PLAYING
        if status_code == STATUS_PAUSED:
            return MediaPlayerState.PAUSED
        if status_code == STATUS_LIVE:
            return MediaPlayerState.ON
        return MediaPlayerState.IDLE

    def abort_if_busy(self) -> None:
        """Abort if the device is currently busy."""
        if self.device.busy:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_busy",
                translation_placeholders={"name": self._friendly_name_internal()},
            )

    async def async_media_pause(self) -> None:
        """
        Pause playback on the device.

        Raises:
            ServiceValidationError: If the device is busy and cannot accept commands.
        """
        self.abort_if_busy()
        await self.device.async_pause()

    async def async_media_play(self) -> None:
        """
        Start playback on the device.

        Raises:
            ServiceValidationError: If the device is currently busy.
        """
        self.abort_if_busy()
        await self.device.async_play()

    async def async_media_stop(self) -> None:
        """
        Stop playback on the Oasis device.

        Raises:
            ServiceValidationError: If the device is currently busy.
        """
        self.abort_if_busy()
        await self.device.async_stop()

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """
        Set the device playlist repeat behavior.

        Enables or disables looping of the playlist according to the provided RepeatMode:
        - RepeatMode.OFF disables playlist repeat.
        - RepeatMode.ALL enables playlist repeat for the entire playlist.
        - RepeatMode.ONE enables single-track repeat, except when the device is currently repeating the entire playlist; in that case the playlist repeat is disabled to preserve single-track semantics.

        Parameters:
            repeat (RepeatMode): The desired repeat mode to apply to the device playlist.
        """
        await self.device.async_set_repeat_playlist(
            repeat != RepeatMode.OFF
            and not (repeat == RepeatMode.ONE and self.repeat == RepeatMode.ALL)
        )

    async def async_media_previous_track(self) -> None:
        """
        Move playback to the previous track in the device's playlist, wrapping to the last track when currently at the first.

        Raises:
            ServiceValidationError: If the device is busy.
        """
        self.abort_if_busy()
        if (index := self.device.playlist_index - 1) < 0:
            index = len(self.device.playlist) - 1
        await self.device.async_change_track(index)

    async def async_media_next_track(self) -> None:
        """
        Advance the device to the next track in its playlist, wrapping to the first track when at the end.

        Raises:
            ServiceValidationError: if the device is busy.
        """
        self.abort_if_busy()
        if (index := self.device.playlist_index + 1) >= len(self.device.playlist):
            index = 0
        await self.device.async_change_track(index)

    async def async_play_media(
        self,
        media_type: MediaType | str,
        media_id: str,
        enqueue: MediaPlayerEnqueue | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Play or enqueue one or more Oasis tracks on the device.

        Validates the media type and parses one or more track identifiers from
        `media_id`, then updates the device playlist according to `enqueue`. Depending
        on the enqueue mode the method can replace the playlist, append tracks, move
        appended tracks to the next play position, and optionally start playback.

        Parameters:
            media_type (MediaType | str): The media type being requested.
            media_id (str): A comma-separated string of track identifiers.
            enqueue (MediaPlayerEnqueue | None): How to insert the tracks into the playlist; if omitted defaults to PLAY.

        Raises:
            ServiceValidationError: If the device is busy or if `media_id` does not contain any valid media identifiers.
        """
        self.abort_if_busy()

        track_ids: list[int] = []

        # Entire playlist from browse
        if media_type == MEDIA_TYPE_OASIS_PLAYLIST:
            try:
                playlist_id = int(media_id)
            except (TypeError, ValueError) as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_media",
                    translation_placeholders={"media": f"playlist {media_id}"},
                ) from err

            playlists = await self.coordinator.cloud_client.async_get_playlists()
            playlist = next((p for p in playlists if p.get("id") == playlist_id), None)
            if not playlist:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_media",
                    translation_placeholders={"media": f"playlist {playlist_id}"},
                )

            track_ids = get_track_ids_from_playlist(playlist)

            if not track_ids:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_media",
                    translation_placeholders={
                        "media": f"playlist {playlist_id} is empty"
                    },
                )

        elif media_type == MEDIA_TYPE_OASIS_TRACK:
            try:
                track_id = int(media_id)
            except (TypeError, ValueError) as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_media",
                    translation_placeholders={"media": f"track {media_id}"},
                ) from err

            track_ids = [track_id]

        else:
            track_ids = list(filter(None, map(get_track_id, media_id.split(","))))
            if not track_ids:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_media",
                    translation_placeholders={"media": media_id},
                )

        device = self.device
        enqueue = MediaPlayerEnqueue.PLAY if not enqueue else enqueue

        if enqueue == MediaPlayerEnqueue.ADD:
            await device.async_add_track_to_playlist(track_ids)
            return

        if enqueue == MediaPlayerEnqueue.REPLACE:
            await device.async_set_playlist(track_ids, start_playing=True)
            return

        insert_at = (device.playlist_index or 0) + 1
        original_len = len(device.playlist)
        await device.async_add_track_to_playlist(track_ids)

        # Move each newly-added track into the desired position
        for offset, _track_id in enumerate(track_ids):
            from_index = original_len + offset  # position at end after append
            to_index = insert_at + offset  # target position in playlist
            if from_index > to_index:
                await device.async_move_track(from_index, to_index)

        if enqueue == MediaPlayerEnqueue.PLAY or (
            enqueue == MediaPlayerEnqueue.NEXT and device.status_code != STATUS_PLAYING
        ):
            await device.async_change_track(min(insert_at, original_len))
            await device.async_play()

    async def async_clear_playlist(self) -> None:
        """
        Clear the device's playlist.

        Raises:
            ServiceValidationError: If the device is busy and cannot accept commands.
        """
        self.abort_if_busy()
        await self.device.async_clear_playlist()

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """
        Provide a browse tree for Oasis playlists and tracks.

        Root (`None` or oasis_root):
          - Playlists folder
          - Tracks folder
        """
        # Root
        if media_content_id in (None, "", "oasis_root") or media_content_type in (
            None,
            MEDIA_TYPE_OASIS_ROOT,
        ):
            return await build_root_response()

        # Playlists folder
        if (
            media_content_type == MEDIA_TYPE_OASIS_PLAYLISTS
            or media_content_id == "playlists_root"
        ):
            return await build_playlists_root(self.coordinator.cloud_client)

        # Single playlist
        if media_content_type == MEDIA_TYPE_OASIS_PLAYLIST:
            try:
                playlist_id = int(media_content_id)
            except (TypeError, ValueError) as err:
                raise BrowseError(f"Invalid playlist id: {media_content_id}") from err

            return await build_playlist_item(self.coordinator.cloud_client, playlist_id)

        # Tracks folder
        if (
            media_content_type == MEDIA_TYPE_OASIS_TRACKS
            or media_content_id == "tracks_root"
        ):
            return build_tracks_root()

        # Single track
        if media_content_type == MEDIA_TYPE_OASIS_TRACK:
            try:
                track_id = int(media_content_id)
            except (TypeError, ValueError) as err:
                raise BrowseError(f"Invalid track id: {media_content_id}") from err

            return build_track_item(track_id)

        raise BrowseError(
            f"Unsupported media_content_type/id: {media_content_type}/{media_content_id}"
        )

    async def async_search_media(
        self,
        query: SearchMediaQuery,
    ) -> SearchMedia:
        """
        Search tracks and/or playlists and return a BrowseMedia tree of matches.

        - If media_type == MEDIA_TYPE_OASIS_TRACK:   search tracks only
        - If media_type == MEDIA_TYPE_OASIS_PLAYLIST: search playlists only
        - Otherwise: search both tracks and playlists
        """
        return await async_search_media(self.coordinator.cloud_client, query)
