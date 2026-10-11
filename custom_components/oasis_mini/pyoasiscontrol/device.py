"""Oasis device."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any, Final

from .const import (
    ERROR_CODE_MAP,
    LED_EFFECTS,
    STATUS_CODE_MAP,
    STATUS_ERROR,
    STATUS_PLAYING,
    STATUS_SLEEPING,
    TRACKS,
)
from .utils import (
    _bit_to_bool,
    _parse_int,
    create_svg,
    decrypt_svg_content,
    get_image_url_from_track,
    now,
)

if TYPE_CHECKING:  # avoid runtime circular imports
    from .clients import OasisCloudClient
    from .clients.transport import OasisClientProtocol

_LOGGER = logging.getLogger(__name__)

BALL_SPEED_MAX: Final = 400
BALL_SPEED_MIN: Final = 100
BRIGHTNESS_DEFAULT: Final = 100
LED_SPEED_MAX: Final = 90
LED_SPEED_MIN: Final = -90

_STATE_FIELDS = (
    "auto_clean",
    "autoplay",
    "ball_speed",
    "brightness",
    "busy",
    "color",
    "download_progress",
    "error",
    "led_effect",
    "led_speed",
    "mac_address",
    "playlist",
    "playlist_index",
    "progress",
    "repeat_playlist",
    "serial_number",
    "software_version",
    "status_code",
)


class OasisDevice:
    """Oasis device model + behavior.

    Transport-agnostic; all I/O is delegated to an attached
    OasisClientProtocol (MQTT, HTTP, etc.) via `attach_client`.
    """

    manufacturer: Final = "Kinetic Oasis"

    def __init__(
        self,
        *,
        model: str | None = None,
        serial_number: str | None = None,
        name: str | None = None,
        ssid: str | None = None,
        ip_address: str | None = None,
        cloud: OasisCloudClient | None = None,
        client: OasisClientProtocol | None = None,
    ) -> None:
        """
        Initialize an OasisDevice with identification, network, transport references, and default state fields.

        Parameters:
            model (str | None): Device model identifier.
            serial_number (str | None): Device serial number.
            name (str | None): Human-readable device name; if omitted, defaults to "<model> <serial_number>".
            ssid (str | None): Last-known Wi-Fi SSID for the device.
            ip_address (str | None): Last-known IP address for the device.
            cloud (OasisCloudClient | None): Optional cloud client used to fetch track metadata and remote data.
            client (OasisClientProtocol | None): Optional transport client used to send commands to the device.

        Notes:
            - Creates an internal listener list for state-change callbacks.
            - Initializes status fields (brightness, playlist, playback state, networking, etc.) with sensible defaults.
            - Initializes a track metadata cache and a placeholder for a background refresh task.
        """
        self._cloud = cloud
        self._client = client
        self._listeners: list[Callable[[], None]] = []

        # Details
        self.model = model
        self.serial_number = serial_number
        self.name = name if name else f"{model} {serial_number}"
        self.ssid = ssid
        self.ip_address = ip_address

        # Status
        self.auto_clean: bool = False
        self.autoplay: int = 0
        self.ball_speed: int = BALL_SPEED_MIN
        self._brightness: int = 0
        self.brightness_max: int = 200
        self.brightness_on: int = BRIGHTNESS_DEFAULT
        self.busy: bool = False
        self.color: str | None = None
        self.download_progress: int = 0
        self.error: int = 0
        self.led_color_id: str = "0"
        self.led_effect: str = "0"
        self.led_speed: int = 0
        self.mac_address: str | None = None
        self.playlist: list[int] = []
        self.playlist_index: int = 0
        self.progress: int = 0
        self.repeat_playlist: bool = False
        self.software_version: str | None = None
        self.status_code: int = 0
        self.wifi_connected: bool = False
        self.wifi_ip: str | None = None
        self.wifi_ssid: str | None = None
        self.wifi_pdns: str | None = None
        self.wifi_sdns: str | None = None
        self.wifi_gate: str | None = None
        self.wifi_sub: str | None = None
        self.environment: str | None = None
        self.schedule: Any | None = None

        # Track metadata cache
        self._track: dict | None = None
        self._track_task: asyncio.Task | None = None

        # Diagnostic metadata
        self.last_updated: datetime | None = None

    @property
    def brightness(self) -> int:
        """
        Current display brightness adjusted for the device sleep state.

        Returns:
            int: 0 when the device is sleeping, otherwise the stored brightness value.
        """
        return 0 if self.is_sleeping else self._brightness

    @brightness.setter
    def brightness(self, value: int) -> None:
        """
        Set the device brightness and update brightness_on when non-zero.

        Parameters:
            value (int): Brightness level to apply; if non-zero, also stored in `brightness_on`.
        """
        self._brightness = value
        if value:
            self.brightness_on = value

    @property
    def is_initialized(self) -> bool:
        """Return `True` if the device is fully identified."""
        return bool(self.serial_number and self.mac_address and self.software_version)

    @property
    def is_sleeping(self) -> bool:
        """
        Indicates whether the device is currently in the sleeping status.

        Returns:
            `true` if the device is sleeping, `false` otherwise.
        """
        return self.status_code == STATUS_SLEEPING

    def attach_client(self, client: OasisClientProtocol) -> None:
        """Attach a transport client (MQTT, HTTP, etc.) to this device."""
        self._client = client

    @property
    def client(self) -> OasisClientProtocol | None:
        """
        Get the attached transport client, or `None` if no client is attached.

        Returns:
            The attached transport client, or `None` if not attached.
        """
        return self._client

    def _require_client(self) -> OasisClientProtocol:
        """
        Get the attached transport client for this device.

        Returns:
            OasisClientProtocol: The attached transport client.

        Raises:
            RuntimeError: If no client/transport is attached to the device.
        """
        if self._client is None:
            raise RuntimeError(
                f"No client/transport attached for device {self.serial_number!r}"
            )
        return self._client

    def _update_field(self, name: str, value: Any) -> bool:
        """
        Update an attribute on the device if the new value differs from the current value.

        Sets the instance attribute named `name` to `value` and logs a debug message when a change occurs.

        Parameters:
            name (str): The attribute name to update.
            value (Any): The new value to assign to the attribute.

        Returns:
            bool: True if the attribute was changed, False otherwise.
        """
        old = getattr(self, name, None)
        if old != value:
            _LOGGER.debug(
                "%s %s changed: '%s' -> '%s'",
                self.serial_number,
                name.replace("_", " ").capitalize(),
                old,
                value,
            )
            setattr(self, name, value)
            return True
        return False

    def update_from_status_dict(self, data: dict[str, Any]) -> None:
        """
        Update the device's attributes from a status dictionary.

        Expects a mapping of attribute names to values; known attributes are applied to the device,
        unknown keys are logged and ignored. If `playlist` or `playlist_index` change, a track
        refresh is scheduled. If any attribute changed, registered update listeners are notified.
        """
        changed = False
        playlist_or_index_changed = False

        for key, value in data.items():
            if hasattr(self, key):
                if self._update_field(key, value):
                    changed = True
                    if key in ("playlist", "playlist_index"):
                        playlist_or_index_changed = True
            else:
                _LOGGER.warning("Unknown field: %s=%s", key, value)

        if playlist_or_index_changed:
            self.schedule_track_refresh()

        if changed:
            self._notify_listeners()

        self.last_updated = now()

    def parse_status_string(self, raw_status: str) -> dict[str, Any] | None:
        """
        Parse a semicolon-separated device status string into a structured state dictionary.

        Expects a semicolon-separated string containing at least 18 fields (device status format returned by the device: e.g., HTTP GETSTATUS or MQTT FULLSTATUS). Returns None for empty input or if the string cannot be parsed into the expected fields.

        Parameters:
            raw_status (str): Semicolon-separated status string from the device.

        Returns:
            dict[str, Any] | None: A dictionary with these keys on success:
            - `status_code`, `error`, `ball_speed`, `playlist` (list[int]), `playlist_index`,
              `progress`, `led_effect`, `led_color_id`, `led_speed`, `brightness`, `color`,
              `busy`, `download_progress`, `brightness_max`, `wifi_connected`, `repeat_playlist`,
              `autoplay`, `auto_clean`
            - `software_version` (str) is included if an additional trailing field is present.
            Returns `None` if the input is empty or parsing fails.
        """
        if not raw_status:
            return None

        values = raw_status.split(";")

        # We rely on indices 0..17 existing (18 fields)
        if (n := len(values)) < 18:
            _LOGGER.warning(
                "Unexpected status format for %s: %s", self.serial_number, values
            )
            return None

        playlist = [
            track_id
            for track_str in values[3].split(",")
            if (track_id := _parse_int(track_str))
        ]

        try:
            status: dict[str, Any] = {
                "status_code": _parse_int(values[0]),
                "error": _parse_int(values[1]),
                "ball_speed": _parse_int(values[2]),
                "playlist": playlist,
                "playlist_index": min(_parse_int(values[4]), len(playlist)),
                "progress": _parse_int(values[5]),
                "led_effect": values[6],
                "led_color_id": values[7],
                "led_speed": _parse_int(values[8]),
                "brightness": _parse_int(values[9]),
                "color": values[10] if "#" in values[10] else None,
                "busy": _bit_to_bool(values[11]),
                "download_progress": _parse_int(values[12]),
                "brightness_max": _parse_int(values[13]),
                "wifi_connected": _bit_to_bool(values[14]),
                "repeat_playlist": _bit_to_bool(values[15]),
                "autoplay": _parse_int(values[16]),
                "auto_clean": _bit_to_bool(values[17]),
            }

            # Optional trailing field(s)
            if n > 18:
                status["software_version"] = values[18]

        except Exception:
            _LOGGER.exception(
                "Error parsing status string for %s: %r", self.serial_number, raw_status
            )
            return None

        return status

    def update_from_status_string(self, raw_status: str) -> None:
        """
        Parse a semicolon-separated device status string and apply the resulting fields to the device state.

        If the string cannot be parsed into a valid status dictionary, no state is changed.

        Parameters:
            raw_status (str): Raw status payload received from the device (semicolon-separated fields).
        """
        if status := self.parse_status_string(raw_status):
            self.update_from_status_dict(status)

    def as_dict(self) -> dict[str, Any]:
        """
        Return a mapping of the device's core state fields to their current values.

        Returns:
            dict[str, Any]: A dictionary whose keys are the core state field names (as defined in _STATE_FIELDS)
            and whose values are the current values for those fields.
        """
        return {field: getattr(self, field) for field in _STATE_FIELDS}

    @property
    def error_message(self) -> str | None:
        """
        Get the human-readable error message for the current device error code.

        Returns:
            str: The mapped error message when the device status indicates an error (status code 9); `None` otherwise.
        """
        if self.status_code == STATUS_ERROR:
            return ERROR_CODE_MAP.get(self.error, f"Unknown ({self.error})")
        return None

    @property
    def status(self) -> str:
        """
        Get a human-readable status description for the current status_code.

        Returns:
            str: Human-readable status corresponding to the device's status_code, or "Unknown (<code>)" when the code is not recognized.
        """
        return STATUS_CODE_MAP.get(self.status_code, f"Unknown ({self.status_code})")

    @property
    def track(self) -> dict | None:
        """
        Return the cached track metadata when it corresponds to the current track, otherwise retrieve the built-in track metadata.

        Returns:
            dict | None: The track metadata dictionary for the current `track_id`, or `None` if no matching track is available.
        """
        if (track := self._track) and track["id"] == self.track_id:
            return track
        return TRACKS.get(self.track_id)

    @property
    def track_id(self) -> int | None:
        """
        Determine the current track id from the active playlist.

        If the playlist index is beyond the end of the playlist, the first track id is returned.

        Returns:
            int | None: The current track id, or `None` if there is no playlist.
        """
        if not self.playlist:
            return None
        i = self.playlist_index
        return self.playlist[0] if i >= len(self.playlist) else self.playlist[i]

    @property
    def track_image_url(self) -> str | None:
        """
        Get the full HTTPS URL for the current track's image if available.

        Returns:
            str: Full URL to the track image or `None` if no image is available.
        """
        return get_image_url_from_track(self.track)

    @property
    def track_name(self) -> str | None:
        """
        Get the current track's display name.

        If the current track has no explicit name, returns "Unknown Title (#{track_id})". If there is no current track, returns None.

        Returns:
            str | None: The track name, or `None` if no current track is available.
        """
        if track := self.track:
            return track.get("name", f"Unknown Title (#{self.track_id})")
        return None

    @property
    def drawing_progress(self) -> float | None:
        """
        Compute drawing progress percentage for the current track.

        If the current track or its SVG content is unavailable, returns None.

        Returns:
            progress_percent (float | None): Percentage of the drawing completed (0-100), clamped to 100; `None` if no track or SVG content is available.
        """
        if not (track := self.track):
            return None
        total = _parse_int(
            track.get("file_lines") or track.get("reduced_svg_content_new")
        )
        if not total and (svg_content := track.get("svg_content")):
            try:
                svg_content = decrypt_svg_content(svg_content)
            except Exception:
                _LOGGER.exception(
                    "Error decrypting SVG content for track %s", track.get("id")
                )
                return None
            total = len(svg_content.split("L"))
        if not total:
            return None
        percent = (100 * self.progress) / total
        return min(percent, 100)

    @property
    def playlist_details(self) -> dict[int, dict[str, str]]:
        """
        Build a mapping of track IDs in the current playlist to their detail dictionaries, preferring the device's cached/current track data and falling back to built-in TRACKS.

        Returns:
            dict[int, dict[str, str]]: A mapping from track ID to a details dictionary (contains at least a `'name'` key). If track metadata is available from the device cache or built-in TRACKS it is used; otherwise a fallback `{"name": "Unknown Title (#<id>)"}` is provided.
        """
        base = dict(TRACKS)
        if (current_id := self.track_id) is not None and self.track:
            base[current_id] = self.track
        return {
            track_id: base.get(track_id, {"name": f"Unknown Title (#{track_id})"})
            for track_id in self.playlist
        }

    def create_svg(self) -> str | None:
        """
        Generate an SVG representing the current track at the device's drawing progress.

        Returns:
            svg (str | None): SVG content for the current track reflecting current progress, or None if track data is unavailable.
        """
        return create_svg(self.track, self.progress)

    def add_update_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """
        Register a callback to be invoked whenever the device state changes.

        Parameters:
            listener (Callable[[], None]): A zero-argument callback that will be called on state updates.

        Returns:
            Callable[[], None]: An unsubscribe function that removes the registered listener; calling the unsubscribe function multiple times is safe.
        """
        self._listeners.append(listener)

        def _unsub() -> None:
            """
            Remove the previously registered listener from the device's listener list if it is present.

            This function silently does nothing if the listener is not found.
            """
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsub

    def _notify_listeners(self) -> None:
        """
        Invoke all registered update listeners in registration order.

        Each listener is called synchronously; exceptions raised by a listener are caught and logged so other listeners still run.
        """
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("Error in update listener")

    async def async_get_status(self) -> None:
        """Request that the device update its current status."""
        client = self._require_client()
        await client.async_get_status(self)

    async def async_get_mac_address(self) -> str | None:
        """
        Get the device MAC address, requesting it from the attached transport client if not already known.

        Returns:
            mac (str | None): The device MAC address if available, otherwise `None`.
        """
        if self.mac_address:
            return self.mac_address

        client = self._require_client()
        mac = await client.async_get_mac_address(self)
        if mac:
            self._update_field("mac_address", mac)
        return mac

    async def async_set_auto_clean(self, auto_clean: bool) -> None:
        """
        Set whether the device performs automatic cleaning.

        Parameters:
            auto_clean (bool): `True` to enable automatic cleaning, `False` to disable it.
        """
        client = self._require_client()
        await client.async_send_auto_clean_command(self, auto_clean)

    async def async_set_ball_speed(self, speed: int) -> None:
        """
        Set the device's ball speed.

        Parameters:
            speed (int): Desired ball speed in the allowed range (BALL_SPEED_MIN to BALL_SPEED_MAX, inclusive).

        Raises:
            ValueError: If `speed` is outside the allowed range.
        """
        if not BALL_SPEED_MIN <= speed <= BALL_SPEED_MAX:
            raise ValueError("Invalid speed specified")
        client = self._require_client()
        await client.async_send_ball_speed_command(self, speed)

    async def async_set_led(
        self,
        *,
        led_effect: str | None = None,
        color: str | None = None,
        led_speed: int | None = None,
        brightness: int | None = None,
    ) -> None:
        """
        Set the device LED effect, color, speed, and brightness.

        Parameters:
            led_effect (str | None): LED effect name; if None, the device's current effect is used. Must be one of the supported LED effects.
            color (str | None): Hex color string (e.g. "#rrggbb"); if None, the device's current color is used or `#ffffff` if unset.
            led_speed (int | None): LED animation speed; if None, the device's current speed is used. Must be within the allowed LED speed range.
            brightness (int | None): Brightness level; if None, the device's current brightness is used. Must be between 0 and the device's `brightness_max`.

        Raises:
            ValueError: If `led_effect` is not supported, or `led_speed` or `brightness` are outside their valid ranges.
            RuntimeError: If no transport client is attached to the device.
        """
        if led_effect is None:
            led_effect = self.led_effect
        if color is None:
            color = self.color or "#ffffff"
        if led_speed is None:
            led_speed = self.led_speed
        if brightness is None:
            brightness = self.brightness

        if led_effect not in LED_EFFECTS:
            raise ValueError("Invalid led effect specified")
        if not LED_SPEED_MIN <= led_speed <= LED_SPEED_MAX:
            raise ValueError("Invalid led speed specified")
        if not 0 <= brightness <= self.brightness_max:
            raise ValueError("Invalid brightness specified")

        client = self._require_client()
        await client.async_send_led_command(
            self, led_effect, color, led_speed, brightness
        )

    async def async_sleep(self) -> None:
        """
        Put the device into sleep mode.

        Sends a sleep command to the attached transport client.

        Raises:
            RuntimeError: If no client is attached.
        """
        client = self._require_client()
        await client.async_send_sleep_command(self)

    async def async_move_track(self, from_index: int, to_index: int) -> None:
        """
        Move a track within the device's playlist from one index to another.

        Parameters:
            from_index (int): Index of the track to move within the current playlist.
            to_index (int): Destination index where the track should be placed.
        """
        client = self._require_client()
        await client.async_send_move_job_command(self, from_index, to_index)

    async def async_change_track(self, index: int) -> None:
        """
        Change the device's current track to the track at the given playlist index.

        Parameters:
            index (int): Zero-based index of the track in the device's current playlist.
        """
        client = self._require_client()
        await client.async_send_change_track_command(self, index)

    async def async_clear_playlist(self) -> None:
        """Clear the playlist."""
        await self.async_set_playlist([])

    async def async_add_track_to_playlist(self, track: int | Iterable[int]) -> None:
        """
        Add one or more tracks to the device's playlist via the attached client.

        Parameters:
            track (int | Iterable[int]): A single track id or an iterable of track ids to append to the playlist.

        Raises:
            RuntimeError: If no transport client is attached to the device.
        """
        if isinstance(track, int):
            tracks = [track]
        else:
            tracks = list(track)
        client = self._require_client()
        await client.async_send_add_joblist_command(self, tracks)

    async def async_set_playlist(
        self, playlist: int | Iterable[int], *, start_playing: bool | None = None
    ) -> None:
        """
        Set the device's playlist to the provided track or tracks.

        Accepts a single track ID or an iterable of track IDs, stops the device,
        replaces the playlist, and resumes playback based on the `start_playing`
        parameter or, if unspecified, the device's prior playing state.

        Parameters:
            playlist (int | Iterable[int]):
                A single track ID or an iterable of track IDs to set as the new playlist.
            start_playing (bool | None, keyword-only):
                Whether to start playback after updating the playlist. If None (default),
                playback will resume only if the device was previously playing and the
                new playlist is non-empty.
        """
        playlist = [playlist] if isinstance(playlist, int) else list(playlist)
        if start_playing is None:
            start_playing = self.status_code == STATUS_PLAYING

        client = self._require_client()
        await client.async_send_stop_command(self)  # needed before replacing playlist
        await client.async_send_set_playlist_command(self, playlist)
        if start_playing and playlist:
            await client.async_send_play_command(self)

    async def async_set_repeat_playlist(self, repeat: bool) -> None:
        """
        Set whether the device's playlist should repeat.

        Parameters:
            repeat (bool): True to enable repeating the playlist, False to disable it.
        """
        client = self._require_client()
        await client.async_send_set_repeat_playlist_command(self, repeat)

    async def async_set_autoplay(self, option: bool | int | str) -> None:
        """
        Set the device's autoplay / wait-after option.

        Parameters:
            option (bool | int | str): Desired autoplay/wait-after value. If a `bool` is provided, `True` is converted to `"0"` and `False` to `"1"`. Integer or string values are sent as their string representation.
        """
        if isinstance(option, bool):
            option = 0 if option else 1
        client = self._require_client()
        await client.async_send_set_autoplay_command(self, str(option))

    async def async_upgrade(self, beta: bool = False) -> None:
        """
        Initiates a firmware upgrade on the device.

        Parameters:
            beta (bool): If True, request a beta (pre-release) firmware; otherwise request the stable firmware.
        """
        client = self._require_client()
        await client.async_send_upgrade_command(self, beta)

    async def async_play(self) -> None:
        """
        Send a play command to the device via the attached transport client.

        Raises:
            RuntimeError: If no transport client is attached.
        """
        client = self._require_client()
        await client.async_send_play_command(self)

    async def async_pause(self) -> None:
        """
        Pause playback on the device.

        Raises:
            RuntimeError: If no transport client is attached.
        """
        client = self._require_client()
        await client.async_send_pause_command(self)

    async def async_stop(self) -> None:
        """
        Stop playback on the device by sending a stop command through the attached transport client.

        Raises:
            RuntimeError: if no transport client is attached to the device.
        """
        client = self._require_client()
        await client.async_send_stop_command(self)

    async def async_reboot(self) -> None:
        """
        Reboots the device using the attached transport client.

        Requests the attached client to send a reboot command to the device.

        Raises:
            RuntimeError: If no transport client is attached.
        """
        client = self._require_client()
        await client.async_send_reboot_command(self)

    def schedule_track_refresh(self) -> None:
        """
        Schedule a background refresh of the current track metadata when the device's track may have changed.

        Does nothing if no cloud client is attached or if there is no running event loop. If a previous refresh task is still pending, it is cancelled before a new background task is scheduled.
        """
        if not self._cloud:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _LOGGER.debug("No running loop; cannot schedule track refresh")
            return

        if self._track_task and not self._track_task.done():
            self._track_task.cancel()

        self._track_task = loop.create_task(self._async_refresh_current_track())

    async def _async_refresh_current_track(self) -> None:
        """
        Refresh cached information for the current track by fetching details from the attached cloud client and notify listeners when updated.

        If no cloud client is attached, no current track exists, or the cached track already matches the current track id, the method returns without change. On successful fetch, updates the device's track cache and invokes registered update listeners.
        """
        if not self._cloud:
            return

        if (track_id := self.track_id) is None:
            self._track = None
            return

        if self._track and self._track.get("id") == track_id:
            return

        try:
            track = await self._cloud.async_get_track_info(track_id)
        except Exception:
            _LOGGER.exception("Error fetching track info for %s", track_id)
            return

        if not track:
            return

        self._track = track
        self._notify_listeners()
