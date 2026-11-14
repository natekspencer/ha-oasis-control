"""Oasis Bluetooth LE client (per-device)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import time
from typing import Any, Final

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from ..device import OasisDevice
from ..exceptions import CommandRejectedError
from ..utils import _bit_to_bool, _parse_int, now
from .transport import OasisClientProtocol

_LOGGER = logging.getLogger(__name__)

# GATT layout (single read/write/notify characteristic carrying JSON)
OASIS_SERVICE_UUID: Final = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
OASIS_CHARACTERISTIC_UUID: Final = "beb5483e-36e1-4688-b7f5-ea07361b26a8"

# The app spaces writes at least this far apart
WRITE_MIN_INTERVAL: Final = 1.0
COMMAND_TIMEOUT: Final = 3.0
PLAYLIST_TIMEOUT: Final = 10.0
# The app waits this long for the playlist to sync back after writing it
PLAYLIST_WRITE_TIMEOUT: Final = 20.0
# The app's playlist limit; WRIJOBLIST is sent as a single write
MAX_PLAYLIST_SIZE: Final = 40

# Commands whose notifications carry device status fields
_STATUS_COMMANDS: Final = ("GETSTATUS", "LINESTATUS", "DOWNLOADSTATUS")
# Commands later steps depend on, so firmware not supporting them is an error
_REQUIRED_COMMANDS: Final = frozenset({"CMDSTOP"})


def _parse_color(value: str) -> str | None:
    """Return the LED effect parameter if it is a hex color (matches MQTT LED_EFFECT_PARAM)."""
    return value if value.startswith("#") else None


# GETSTATUS short keys => (device field, parser)
_GETSTATUS_FIELDS: Final[dict[str, tuple[str, Callable[[Any], Any]]]] = {
    "err": ("error", _parse_int),
    "sp": ("ball_speed", _parse_int),
    "le": ("led_effect", str),
    "lc": ("led_color_id", str),
    "ls": ("led_speed", _parse_int),
    "lb": ("brightness", _parse_int),
    "lm": ("brightness_max", _parse_int),
    "lp": ("color", _parse_color),
    "rj": ("repeat_playlist", _bit_to_bool),
    "wa": ("autoplay", _parse_int),
    "ac": ("auto_clean", _bit_to_bool),
    "bz": ("busy", _bit_to_bool),
}

# Device fields reported by every firmware (identity and Wi-Fi state)
_BASE_FIELDS: Final = frozenset(
    {
        "mac_address",
        "software_version",
        "wifi_ip",
        "wifi_connected",
        "remote_access",
        "remote_access_updated",
    }
)
# Device fields reported by firmware that supports GETSTATUS/GETPLAYLIST over BLE
_STATUS_FIELDS: Final = _BASE_FIELDS | frozenset(
    {"status_code", "playlist_index", "progress", "download_progress", "playlist"}
    | {field for field, _ in _GETSTATUS_FIELDS.values()}
)


# WIFISTATUS "mqtt" values => the app's remote access states
_REMOTE_ACCESS: Final = {"0": "inactive", "1": "active", "2": "disabled"}


def _has_value(value: Any) -> bool:
    """Return True if a notification field is present and non-empty."""
    return value is not None and value != ""


class OasisBleClient(OasisClientProtocol):
    """Bluetooth LE Oasis transport.

    One client per physical device. Commands are JSON objects written to a single
    characteristic; the device answers (and pushes status) as JSON notifications on
    the same characteristic.

    Note: the device accepts a single BLE connection, so while this client is
    connected the mobile app cannot use Bluetooth.
    """

    # Whether a full sync (identity, status and playlist) has completed over the
    # current connection
    synced: bool = False

    def __init__(
        self,
        address: str,
        ble_device_callback: Callable[[], BLEDevice | None],
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        """
        Initialize the BLE client for a specific device address.

        Parameters:
            address (str): Bluetooth address of the device.
            ble_device_callback (Callable[[], BLEDevice | None]): Returns the most recent BLEDevice for the address (so connections can use the best adapter/proxy).
            on_disconnect (Callable[[], None] | None): Optional callback invoked when the connection drops unexpectedly.
        """
        self.address = address
        self._ble_device_callback = ble_device_callback
        self._on_disconnect = on_disconnect

        self._client: BleakClient | None = None
        self._device: OasisDevice | None = None
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._last_write = 0.0
        self._closing = False
        # Set by async_close; a closed client never connects again (the coordinator
        # creates a new client instead), even for attempts already queued
        self._closed = False

        self._waiters: dict[str, list[asyncio.Future[dict[str, Any]]]] = {}
        self._rx_buffer = ""
        self._playlist: dict[int, int] = {}
        # Completed by the playlist transfer a sync is waiting for (see
        # _async_sync_playlist); failed on disconnect
        self._playlist_waiter: asyncio.Future[list[int]] | None = None
        self._playlist_expected: list[int] | None = None
        # One playlist transfer at a time, so they can't take each other's completion
        self._playlist_lock = asyncio.Lock()
        self._legacy_warned = False

        self.serial_number: str | None = None
        self.legacy_firmware = False
        self.mqtt_connected: bool | None = None
        self.internet_connected: bool | None = None
        # Whether the device's local HTTP API is on, as last reported; it turns off
        # whenever the device restarts
        self.http_mode: bool | None = None

    @property
    def is_connected(self) -> bool:
        """Return True if the BLE link is up."""
        return self._client is not None and self._client.is_connected

    @property
    def max_playlist_size(self) -> int:
        """Largest playlist that can be written over BLE."""
        return MAX_PLAYLIST_SIZE

    @property
    def status_fields(self) -> frozenset[str]:
        """Device fields this client keeps current while it is the active transport."""
        return _BASE_FIELDS if self.legacy_firmware else _STATUS_FIELDS

    def attach_device(self, device: OasisDevice) -> None:
        """Associate the OasisDevice whose state this client updates."""
        self._device = device

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #

    async def async_connect(self) -> None:
        """
        Connect to the device and enable notifications, if not already connected.

        Raises:
            BleakError: If the device cannot be found or the connection fails.
        """
        if self.is_connected:
            return
        if self._closed:
            raise BleakError(f"Oasis device {self.address} was closed")

        async with self._connect_lock:
            if self.is_connected:
                return
            if self._closed:
                raise BleakError(f"Oasis device {self.address} was closed")

            if not (ble_device := self._ble_device_callback()):
                raise BleakError(f"Oasis device {self.address} not found")

            self._closing = False
            _LOGGER.debug("Connecting to Oasis BLE device %s", self.address)
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.serial_number or self.address,
                disconnected_callback=self._handle_disconnect,
                ble_device_callback=self._ble_device_callback,
            )
            try:
                await client.start_notify(
                    OASIS_CHARACTERISTIC_UUID, self._handle_notification
                )
                if self._closed:
                    raise BleakError(f"Oasis device {self.address} was closed")
            except BaseException:
                # Includes cancellation, so a half-made connection is never left up
                self._closing = True
                await client.disconnect()
                raise

            self._client = client
            self._rx_buffer = ""
            _LOGGER.info("Connected to Oasis BLE device %s", self.address)

    async def async_close(self) -> None:
        """Disconnect from the device."""
        self._closing = True
        self._closed = True
        client, self._client = self._client, None
        self._fail_waiters(BleakError(f"Oasis device {self.address} was closed"))
        if client is not None and client.is_connected:
            try:
                await client.disconnect()
            except BleakError as err:
                _LOGGER.debug("Error disconnecting from %s: %s", self.address, err)

    def _handle_disconnect(self, _client: BleakClient) -> None:
        """Handle an unexpected disconnect from the device."""
        self._client = None
        self.synced = False
        self._fail_waiters(BleakError(f"Oasis device {self.address} disconnected"))
        if self._closing:
            return
        _LOGGER.info("Disconnected from Oasis BLE device %s", self.address)
        if self._on_disconnect:
            self._on_disconnect()

    def _fail_waiters(self, err: BleakError) -> None:
        """Fail all pending response waiters, and any playlist sync, with the error."""
        for futures in self._waiters.values():
            for future in futures:
                if not future.done():
                    future.set_exception(err)
        self._waiters.clear()
        if self._playlist_waiter is not None and not self._playlist_waiter.done():
            self._playlist_waiter.set_exception(err)

    # ------------------------------------------------------------------ #
    # Low-level I/O
    # ------------------------------------------------------------------ #

    async def _async_send(
        self,
        payload: dict[str, Any],
        response: str | None = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> dict[str, Any] | None:
        """
        Write a JSON command and optionally wait for the matching notification.

        Parameters:
            payload (dict[str, Any]): Command object; must include a "command" key.
            response (str | None): Notification command name to wait for, or None to not wait.
            timeout (float): Seconds to wait for the response notification.

        Returns:
            dict[str, Any] | None: The response notification, or None if not waited for or timed out.

        Raises:
            BleakError: If the device cannot be reached.
        """
        await self.async_connect()
        data = json.dumps(payload, separators=(",", ":")).encode()

        # One request at a time, from write to reply: a reply resolves every waiter
        # for its command, so overlapping requests would take each other's replies
        async with self._write_lock:
            if self._client is None:
                raise BleakError(f"Oasis device {self.address} disconnected")
            future: asyncio.Future[dict[str, Any]] | None = None
            try:
                if (
                    delay := WRITE_MIN_INTERVAL - (time.monotonic() - self._last_write)
                ) > 0:
                    await asyncio.sleep(delay)
                    if self._client is None:
                        raise BleakError(f"Oasis device {self.address} disconnected")
                # Registered only now, just before the write, so a late reply to an
                # earlier command arriving during the pacing wait can't complete it
                if response:
                    future = asyncio.get_running_loop().create_future()
                    self._waiters.setdefault(response, []).append(future)
                _LOGGER.debug("BLE write %s => %s", self.address, data.decode())
                await self._client.write_gatt_char(
                    OASIS_CHARACTERISTIC_UUID, data, response=True
                )
                self._last_write = time.monotonic()

                if future is None:
                    return None
                try:
                    async with asyncio.timeout(timeout):
                        return await future
                except TimeoutError:
                    _LOGGER.debug(
                        "No BLE response for %s from %s", response, self.address
                    )
                    return None
            finally:
                if future is not None:
                    if future in self._waiters.get(response, ()):
                        self._waiters[response].remove(future)
                    # Don't leave an error set by _fail_waiters unretrieved when the
                    # write itself failed first
                    if future.done():
                        if not future.cancelled():
                            future.exception()
                    else:
                        future.cancel()

    async def _async_command(self, command: str, **fields: str) -> None:
        """
        Send a command and wait for the device's acknowledgement.

        A missing acknowledgement is logged rather than raised, matching the app's
        behaviour, but an explicit rejection raises so dependent commands (e.g.
        replacing the playlist after stopping) don't run.

        Raises:
            CommandRejectedError: If the device rejects the command.
        """
        reply = await self._async_send({"command": command, **fields}, command)
        if reply is None:
            return
        if reply.get("command") == "UNKNOWN":
            message = (
                f"Oasis {self.serial_number or self.address} firmware does not "
                f"support BLE command {command}"
            )
            if command in _REQUIRED_COMMANDS:
                raise CommandRejectedError(message)
            _LOGGER.warning(message)
        elif _has_value(status := reply.get("status")) and str(status) != "1":
            raise CommandRejectedError(
                f"Oasis {self.serial_number or self.address} rejected BLE command "
                f"{command}: {reply}"
            )

    def _handle_notification(
        self, _char: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Decode a JSON notification (reassembling fragments) and dispatch it."""
        self._rx_buffer += data.decode(errors="replace")
        try:
            message = json.loads(self._rx_buffer)
        except json.JSONDecodeError:
            # Wait for the rest of a fragmented message, but don't grow unbounded
            if (
                not self._rx_buffer.lstrip().startswith("{")
                or len(self._rx_buffer) > 8192
            ):
                _LOGGER.debug(
                    "Dropping BLE data from %s: %r", self.address, self._rx_buffer
                )
                self._rx_buffer = ""
            return
        self._rx_buffer = ""

        if not isinstance(message, dict):
            return
        _LOGGER.debug("BLE received %s <= %s", self.address, message)

        command = message.get("command")
        key = command
        if command == "UNKNOWN":
            key = self._extract_received_command(message.get("received"))
            if key in ("GETSTATUS", "GETPLAYLIST"):
                self.legacy_firmware = True
            # Firmware without GETPLAYLIST over BLE answers it with UNKNOWN (the app
            # treats it as legacy too), so a sync waiting for the playlist is done
            if (
                key == "GETPLAYLIST"
                and (waiter := self._playlist_waiter) is not None
                and not waiter.done()
                and self._playlist_expected is None
            ):
                waiter.set_result([])

        try:
            self._process_message(message)
        except Exception:
            _LOGGER.exception(
                "Error handling BLE message from %s: %s", self.address, message
            )

        for future in self._waiters.pop(key, []) if key else []:
            if not future.done():
                future.set_result(message)

    @staticmethod
    def _extract_received_command(received: Any) -> str | None:
        """Extract the original command name from an UNKNOWN response's `received` field."""
        if isinstance(received, dict):
            return received.get("command")
        if not isinstance(received, str):
            return None
        received = received.strip()
        if received.startswith("{"):
            try:
                return json.loads(received).get("command")
            except (json.JSONDecodeError, AttributeError):
                return None
        return received

    def _process_message(self, msg: dict[str, Any]) -> None:
        """Map a notification onto device state."""
        command = msg.get("command")
        data: dict[str, Any] = {}

        if serial := msg.get("oasisid"):
            self.serial_number = serial
        if mac := msg.get("mac"):
            data["mac_address"] = mac
        if version := msg.get("version"):
            data["software_version"] = version
        if ip := msg.get("ip"):
            data["wifi_ip"] = ip
        if _has_value(progress := msg.get("progress")):
            data["download_progress"] = _parse_int(progress)
        if _has_value(http_mode := msg.get("http_mode")):
            self.http_mode = _bit_to_bool(str(http_mode))

        if command in ("WIFISTATUS", "WIFISTATUS_PUSH") and _has_value(
            msg.get("status")
        ):
            data["wifi_connected"] = _bit_to_bool(str(msg["status"]))
            if _has_value(msg.get("mqtt")):
                if remote_access := _REMOTE_ACCESS.get(str(msg["mqtt"])):
                    data["remote_access"] = remote_access
                    data["remote_access_updated"] = now()
                mqtt_connected = _bit_to_bool(str(msg["mqtt"]))
                if mqtt_connected != self.mqtt_connected:
                    _LOGGER.info(
                        "Oasis %s reports cloud MQTT %s",
                        self.serial_number or self.address,
                        "connected" if mqtt_connected else "disconnected",
                    )
                self.mqtt_connected = mqtt_connected
            if _has_value(msg.get("internet")):
                self.internet_connected = _bit_to_bool(str(msg["internet"]))

        if command in _STATUS_COMMANDS:
            if _has_value(msg.get("st")):
                data["status_code"] = _parse_int(msg["st"])
            if _has_value(msg.get("job")):
                data["playlist_index"] = _parse_int(msg["job"])
            if _has_value(msg.get("cl")):
                data["progress"] = _parse_int(msg["cl"])

        if command == "GETSTATUS":
            for key, (field, parser) in _GETSTATUS_FIELDS.items():
                if _has_value(value := msg.get(key)):
                    data[field] = parser(str(value))

        if command == "DOWNLOADSTATUS" and _has_value(msg.get("pct")):
            data["download_progress"] = _parse_int(msg["pct"])

        if command in ("GETPLAYLIST", "GETPLAYLIST_PUSH"):
            job = msg.get("job")
            part = str(msg.get("part", ""))
            if part == "0" or (isinstance(job, dict) and str(job.get("idx")) == "0"):
                self._playlist = {}
            if isinstance(job, dict) and _has_value(job.get("idx")):
                self._playlist[_parse_int(job["idx"])] = _parse_int(job.get("id"))
            if part == "end":
                received, self._playlist = self._playlist, {}
                # Like the app, only a complete transfer counts: entries are indexed
                # from 0 without gaps, and the end message gives their count. An
                # incomplete one is dropped, so a sync waiting for it times out and
                # is retried
                count = (
                    _parse_int(msg.get("count"))
                    if _has_value(msg.get("count"))
                    else None
                )
                if sorted(received) != list(range(len(received))) or (
                    count is not None and count != len(received)
                ):
                    _LOGGER.debug(
                        "Dropping incomplete playlist from %s: %s (count %s)",
                        self.address,
                        received,
                        count,
                    )
                    return
                playlist = [
                    track_id for _, track_id in sorted(received.items()) if track_id
                ]
                data["playlist"] = playlist
                # A sync for a written playlist only completes once the device
                # reports that playlist, not an earlier one still in flight
                if (
                    (waiter := self._playlist_waiter) is not None
                    and not waiter.done()
                    and self._playlist_expected in (None, playlist)
                ):
                    waiter.set_result(playlist)

        if data and self._device is not None:
            self._device.update_from_status_dict(data, source=self)

    # ------------------------------------------------------------------ #
    # Identification / status
    # ------------------------------------------------------------------ #

    async def async_get_serial_number(self) -> str | None:
        """
        Connect if needed and ask the device for its serial number.

        Returns:
            str | None: The device serial number, or None if it did not respond.
        """
        if not self.serial_number:
            await self._async_send({"command": "GETID"}, "GETID")
        return self.serial_number

    async def async_get_mac_address(self, device: OasisDevice) -> str | None:
        """Request the device's MAC address over BLE."""
        if device.mac_address:
            return device.mac_address
        await self._async_send({"command": "GETMAC"}, "GETMAC")
        return device.mac_address

    async def async_get_status(self, device: OasisDevice) -> None:
        """Request the device's runtime status over BLE."""
        await self._async_send({"command": "GETSTATUS"}, "GETSTATUS")

    async def async_get_all(self, device: OasisDevice) -> None:
        """
        Request identity, Wi-Fi/cloud state, runtime status and the current playlist.

        Mirrors the app's BLE initialization sequence. Legacy firmware that does not
        support GETSTATUS/GETPLAYLIST over BLE only reports identity and Wi-Fi state.
        """
        # Cleared first, so a sync that fails partway is retried even if an earlier
        # one had completed
        self.synced = False
        if not device.mac_address:
            await self._async_send({"command": "GETMAC"}, "GETMAC")
        await self._async_send({"command": "GETSOFTWAREVER"}, "GETSOFTWAREVER")
        await self._async_send({"command": "WIFISTATUS"}, "WIFISTATUS")
        await self._async_send({"command": "GETSTATUS"}, "GETSTATUS")
        if not self.legacy_firmware:
            await self._async_sync_playlist(
                {"command": "GETPLAYLIST", "part": "0"}, PLAYLIST_TIMEOUT
            )
        if self.legacy_firmware and not self._legacy_warned:
            self._legacy_warned = True
            _LOGGER.warning(
                "Oasis %s firmware does not report status over BLE",
                device.serial_number,
            )
        # Missed identity replies time out quietly, so only an identified device
        # counts as synced; a failed playlist transfer raises before this
        self.synced = device.is_initialized

    async def async_enable_http_mode(self) -> bool | None:
        """
        Turn on the device's local HTTP API, as the Oasis Control app does.

        HTTP mode turns off whenever the device restarts. Like the app, the device
        is asked for its Wi-Fi status a few times afterwards to confirm it's on.

        Returns:
            bool | None: Whether the device reports HTTP mode on since the request,
            or None if it didn't report it (e.g. its firmware doesn't).
        """
        # Only a report after the request counts, not one from before it
        self.http_mode = None
        await self._async_send({"command": "ENABLEHTTP"})
        for _ in range(3):
            await self._async_send({"command": "WIFISTATUS"}, "WIFISTATUS")
            if self.http_mode:
                break
        return self.http_mode

    async def _async_sync_playlist(
        self,
        payload: dict[str, Any],
        timeout: float,
        expected: list[int] | None = None,
    ) -> None:
        """Send a command and wait for the device to push its full playlist back.

        With `expected`, only a transfer of that playlist completes the sync, so a
        push of the previous playlist arriving meanwhile isn't taken as the result.

        Raises:
            TimeoutError: If the playlist isn't pushed back in time.
            BleakError: If the device disconnects first.
        """
        async with self._playlist_lock:
            self._playlist = {}
            waiter = asyncio.get_running_loop().create_future()
            self._playlist_waiter = waiter
            self._playlist_expected = expected
            try:
                await self._async_send(payload)
                async with asyncio.timeout(timeout):
                    await waiter
            except TimeoutError:
                _LOGGER.debug("Timed out syncing playlist from %s", self.address)
                raise
            finally:
                self._playlist_waiter = None
                self._playlist_expected = None
                if waiter.done() and not waiter.cancelled():
                    waiter.exception()
                else:
                    waiter.cancel()

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #

    async def _async_wake(self, device: OasisDevice) -> None:
        """Refresh status first if the device is sleeping (as the MQTT client does)."""
        if device.is_sleeping:
            await self.async_get_status(device)

    async def async_send_auto_clean_command(
        self, device: OasisDevice, auto_clean: bool
    ) -> None:
        """Enable or disable auto-clean."""
        await self._async_command("WRIAUTOCLEAN", value=str(int(auto_clean)))

    async def async_send_ball_speed_command(
        self, device: OasisDevice, speed: int
    ) -> None:
        """Set the ball speed."""
        await self._async_command("WRISPEED", speed=str(speed))

    async def async_send_led_command(
        self,
        device: OasisDevice,
        led_effect: str,
        color: str,
        led_speed: int,
        brightness: int,
    ) -> None:
        """Set LED effect, color, speed and brightness."""
        if brightness:
            await self._async_wake(device)
        param = f"{led_effect};0;{color};{led_speed};{brightness}"
        await self._async_command("WRILED", param=param)

    async def async_send_sleep_command(self, device: OasisDevice) -> None:
        """Put the device to sleep."""
        await self._async_command("CMDSLEEP")

    async def async_send_move_job_command(
        self, device: OasisDevice, from_index: int, to_index: int
    ) -> None:
        """Move a playlist entry."""
        await self._async_command("MOVEJOB", value=f"{from_index};{to_index}")

    async def async_send_change_track_command(
        self, device: OasisDevice, index: int
    ) -> None:
        """Jump to a playlist index."""
        await self._async_command("CMDCHANGETRACK", track=str(index))

    async def async_send_add_joblist_command(
        self, device: OasisDevice, tracks: list[int]
    ) -> None:
        """Append tracks to the playlist."""
        await self._async_command("ADDJOBLIST", value=",".join(map(str, tracks)))

    async def async_send_set_playlist_command(
        self, device: OasisDevice, playlist: list[int]
    ) -> None:
        """
        Replace the playlist.

        The device doesn't acknowledge WRIJOBLIST; like the app, wait for it to push
        the new playlist back instead.
        """
        payload = {"command": "WRIJOBLIST", "value": ",".join(map(str, playlist))}
        if self.legacy_firmware:
            await self._async_send(payload)
        else:
            await self._async_sync_playlist(
                payload,
                PLAYLIST_WRITE_TIMEOUT,
                expected=[track_id for track_id in playlist if track_id],
            )

    async def async_send_set_repeat_playlist_command(
        self, device: OasisDevice, repeat: bool
    ) -> None:
        """Enable or disable playlist repeat."""
        await self._async_command("WRIREPEATJOB", value=str(int(repeat)))

    async def async_send_set_autoplay_command(
        self, device: OasisDevice, option: str
    ) -> None:
        """Set the wait-after-job (autoplay) option."""
        await self._async_command("WRIWAITAFTER", value=option)

    async def async_send_upgrade_command(self, device: OasisDevice, beta: bool) -> None:
        """Start a firmware upgrade."""
        await self._async_command("CMDUPGRADE", type=str(int(beta)))

    async def async_send_play_command(self, device: OasisDevice) -> None:
        """Start playback."""
        await self._async_wake(device)
        await self._async_command("CMDPLAY")

    async def async_send_pause_command(self, device: OasisDevice) -> None:
        """Pause playback."""
        await self._async_command("CMDPAUSE")

    async def async_send_stop_command(self, device: OasisDevice) -> None:
        """Stop playback."""
        await self._async_command("CMDSTOP")

    async def async_send_reboot_command(self, device: OasisDevice) -> None:
        """Reboot the device."""
        await self._async_command("CMDBOOT")
