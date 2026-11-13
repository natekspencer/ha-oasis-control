"""Oasis HTTP client (per-device)."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any, Final

from aiohttp import ClientSession, ClientTimeout

from ..device import OasisDevice
from .transport import OasisClientProtocol

_LOGGER = logging.getLogger(__name__)


def _normalize_mac(mac: str) -> str:
    """Return a MAC address without separators, lowercased, for comparison."""
    return "".join(c for c in mac.lower() if c in "0123456789abcdef")


# The device answers on the LAN in well under a second when HTTP mode is on
REQUEST_TIMEOUT: Final = ClientTimeout(total=5)


class OasisHttpClient(OasisClientProtocol):
    """HTTP-based Oasis transport.

    This client is typically used per-device (per host/IP).
    It implements the OasisClientProtocol so OasisDevice can delegate
    all commands through it.

    Firmware 2.60+ only answers once HTTP mode is enabled from the Oasis Control
    app (tap the serial number on the device page 10 times).
    """

    def __init__(
        self,
        host: str,
        session: ClientSession | None = None,
        on_command: Callable[[], None] | None = None,
    ) -> None:
        """
        Initialize the HTTP client for a specific device host.

        Parameters:
            host (str): Hostname or IP address of the target device (used to build the base HTTP URL).
            session (ClientSession | None): Optional aiohttp ClientSession to reuse for requests. If omitted, a new session will be created and owned by this client.
            on_command (Callable[[], None] | None): Called after each successful command, so the caller can refresh the device state (HTTP has no push updates).
        """
        self.host = host
        self._session: ClientSession | None = session
        self._owns_session: bool = session is None
        self._on_command = on_command
        self._connected = False
        # The last valid status the device reported
        self.last_status: dict[str, Any] | None = None

    @property
    def is_connected(self) -> bool:
        """Return `True` if the device last answered GETSTATUS with a valid status.

        Any other answer (e.g. a router's web page after the IP was reassigned)
        doesn't count, so HTTP is never chosen for something that isn't the device.
        """
        return self._connected

    @property
    def session(self) -> ClientSession:
        """
        Ensure and return a usable aiohttp ClientSession for this client.

        If no session exists or the existing session is closed, a new ClientSession is created and the client records ownership of that session so it can be closed later.

        Returns:
            An active aiohttp ClientSession instance associated with this client.
        """
        if self._session is None or self._session.closed:
            self._session = ClientSession()
            self._owns_session = True
        return self._session

    async def async_close(self) -> None:
        """
        Close the client's owned HTTP session if one exists and is open.

        Does nothing when there is no session, the session is already closed, or the client does not own the session.
        """
        if self._session and not self._session.closed and self._owns_session:
            await self._session.close()

    @property
    def url(self) -> str:
        # These devices are plain HTTP, no TLS
        """
        Base HTTP URL for the target device.

        Returns:
            The device base URL using plain HTTP (no TLS), including a trailing slash (e.g. "http://{host}/").
        """
        return f"http://{self.host}/"

    async def _async_request(self, method: str, url: str, **kwargs: Any) -> Any:
        """
        Perform an HTTP request using the client's session and decode the response.

        Logs the request URL and query parameters. If the response status is 200, returns the response body as a string for `text/plain`, the parsed JSON for `application/json`, or `None` for other content types. On non-200 responses, raises the client response error.

        Returns:
            The response body as `str` for `text/plain`, the parsed JSON value for `application/json`, or `None` for other content types.

        Raises:
            aiohttp.ClientResponseError: If the response status is not 200.
        """
        session = self.session
        _LOGGER.debug(
            "%s %s",
            method,
            session._build_url(url).update_query(  # pylint: disable=protected-access
                kwargs.get("params"),
            ),
        )
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        # Optional requests (e.g. the schedule) don't decide whether the device is
        # reachable when they fail
        optional = kwargs.pop("optional", False)
        try:
            resp = await session.request(method, url, **kwargs)
            resp.raise_for_status()
            if resp.content_type == "text/plain":
                return await resp.text()
            if resp.content_type == "application/json":
                return await resp.json()
            return None
        except Exception:
            if not optional:
                self._connected = False
            raise

    async def _async_get(self, **kwargs: Any) -> str | None:
        """
        Perform a GET request to the client's base URL using the provided request keyword arguments.

        Parameters:
            **kwargs: Additional request keyword arguments forwarded to the underlying request (for example `params`, `headers`, `timeout`).

        Returns:
            `str` response text when the server responds with `text/plain`, `None` otherwise.
        """
        return await self._async_request("GET", self.url, **kwargs)

    async def _async_command(self, **kwargs: Any) -> str | None:
        """
        Execute a device command by issuing a GET request with the provided query parameters and return the parsed response.

        Parameters:
            **kwargs: Mapping of query parameter names to values sent with the GET request.

        Returns:
            str | None: The device response as a string, a parsed JSON value, or None when the response has an unsupported content type.
        """
        result = await self._async_get(**kwargs)
        _LOGGER.debug("Result: %s", result)
        if self._on_command:
            self._on_command()
        return result

    async def async_get_mac_address(self, device: OasisDevice) -> str | None:
        """
        Fetch the device MAC address using the device's HTTP GETMAC endpoint.

        Returns:
            str: The MAC address with surrounding whitespace removed, or `None` if it could not be retrieved.
        """
        try:
            mac = await self._async_get(params={"GETMAC": ""})
            if isinstance(mac, str):
                return mac.strip()
        except Exception:
            _LOGGER.exception(
                "Failed to get MAC address via HTTP for %s", device.serial_number
            )
        return None

    async def async_send_auto_clean_command(
        self,
        device: OasisDevice,
        auto_clean: bool,
    ) -> None:
        """
        Enable or disable the device's auto-clean mode.

        Parameters:
            device (OasisDevice): The target Oasis device to send the command to.
            auto_clean (bool): `True` to enable auto-clean mode, `False` to disable it.
        """
        await self._async_command(
            params={"WRIAUTOCLEAN": 1 if auto_clean else 0},
        )

    async def async_send_ball_speed_command(
        self,
        device: OasisDevice,
        speed: int,
    ) -> None:
        """
        Send a ball speed command to the specified device.

        Parameters:
            device (OasisDevice): Target device for the command.
            speed (int): Speed value to set for the device's ball mechanism.
        """
        await self._async_command(params={"WRIOASISSPEED": speed})

    async def async_send_led_command(
        self,
        device: OasisDevice,
        led_effect: str,
        color: str,
        led_speed: int,
        brightness: int,
    ) -> None:
        """
        Send an LED control command to the device.

        Parameters:
            device (OasisDevice): Target device to receive the command.
            led_effect (str): Effect name or identifier to apply to the LEDs.
            color (str): Color value recognized by the device (e.g., hex code or device color name).
            led_speed (int): Animation speed value; larger values increase animation speed.
            brightness (int): Brightness level for the LEDs.
        """
        payload = f"{led_effect};0;{color};{led_speed};{brightness}"
        await self._async_command(params={"WRILED": payload})

    async def async_send_sleep_command(self, device: OasisDevice) -> None:
        """
        Send a sleep command to the device.

        Requests the device to enter sleep mode.
        """
        await self._async_command(params={"CMDSLEEP": ""})

    async def async_send_move_job_command(
        self,
        device: OasisDevice,
        from_index: int,
        to_index: int,
    ) -> None:
        """
        Move a job in the device's playlist from one index to another.

        Parameters:
            device (OasisDevice): Target device whose job list will be modified.
            from_index (int): Zero-based index of the job to move.
            to_index (int): Zero-based destination index where the job will be placed.
        """
        await self._async_command(params={"MOVEJOB": f"{from_index};{to_index}"})

    async def async_send_change_track_command(
        self,
        device: OasisDevice,
        index: int,
    ) -> None:
        """
        Change the device's current track to the specified track index.

        Parameters:
            index (int): Zero-based index of the track to select.
        """
        await self._async_command(params={"CMDCHANGETRACK": index})

    async def async_send_add_joblist_command(
        self,
        device: OasisDevice,
        tracks: list[int],
    ) -> None:
        # The old code passed the list directly; if the device expects CSV:
        """
        Send an "add joblist" command to the device with a list of track indices.

        The provided track indices are serialized as a comma-separated string and sent to the device using the `ADDJOBLIST` parameter.

        Parameters:
            device (OasisDevice): Target device to receive the command.
            tracks (list[int]): Track indices to add; these are sent as a CSV string (e.g., [1,2,3] -> "1,2,3").
        """
        await self._async_command(params={"ADDJOBLIST": ",".join(map(str, tracks))})

    async def async_send_set_playlist_command(
        self,
        device: OasisDevice,
        playlist: list[int],
    ) -> None:
        """
        Set the device's playlist on the target device and optimistically update the local device state.

        Parameters:
            device (OasisDevice): Target device to receive the playlist command; its state will be updated optimistically.
            playlist (list[int]): Ordered list of track indices to set as the device's playlist.
        """
        await self._async_command(params={"WRIJOBLIST": ",".join(map(str, playlist))})
        # optional: optimistic state update
        device.update_from_status_dict({"playlist": playlist}, source=self)

    async def async_send_set_repeat_playlist_command(
        self,
        device: OasisDevice,
        repeat: bool,
    ) -> None:
        """
        Set the device's playlist repeat flag.

        Parameters:
            repeat (bool): `True` to enable playlist repeat, `False` to disable it.
        """
        await self._async_command(params={"WRIREPEATJOB": 1 if repeat else 0})

    async def async_send_set_autoplay_command(
        self,
        device: OasisDevice,
        option: str,
    ) -> None:
        """
        Set the device's autoplay (wait-after) option.

        Parameters:
            device (OasisDevice): Target device whose autoplay option will be updated.
            option (str): The value for the device's wait-after/autoplay setting as expected by the device firmware.
        """
        await self._async_command(params={"WRIWAITAFTER": option})

    async def async_send_upgrade_command(
        self,
        device: OasisDevice,
        beta: bool,
    ) -> None:
        """
        Send a firmware upgrade command to the specified device.

        Parameters:
            device (OasisDevice): Target device to receive the upgrade command.
            beta (bool): If True, request the beta firmware; if False, request the stable firmware.
        """
        await self._async_command(params={"CMDUPGRADE": 1 if beta else 0})

    async def async_send_play_command(self, device: OasisDevice) -> None:
        """
        Send the play command to the device.
        """
        await self._async_command(params={"CMDPLAY": ""})

    async def async_send_pause_command(self, device: OasisDevice) -> None:
        """
        Send a pause command to the device.
        """
        await self._async_command(params={"CMDPAUSE": ""})

    async def async_send_stop_command(self, device: OasisDevice) -> None:
        """
        Sends the device stop command to halt playback or activity.

        Sends an HTTP command to request the device stop its current operation.
        """
        await self._async_command(params={"CMDSTOP": ""})

    async def async_send_reboot_command(self, device: OasisDevice) -> None:
        """
        Send a reboot command to the device.

        Sends a reboot request to the target device using the CMDBOOT control parameter.
        """
        await self._async_command(params={"CMDBOOT": ""})

    async def async_get_status(self, device: OasisDevice) -> None:
        """
        Retrieve the device status from the device and apply it to the given OasisDevice.

        If the device does not return a status, the device object is not modified.

        Parameters:
            device (OasisDevice): Device instance to update with the fetched status.
        """
        host = self.host
        raw_status = await self._async_get(params={"GETSTATUS": ""})
        if self.host != host:
            # The device moved while this was in flight; the reply is from an
            # address that may now belong to something else
            raise ValueError(f"Device moved from {host} to {self.host}")
        # A non-text reply, or text that isn't a status string, isn't the device
        status = (
            device.parse_status_string(raw_status)
            if isinstance(raw_status, str) and ";" in raw_status
            else None
        )
        if status is None:
            self._connected = False
            raise ValueError(f"Invalid status from {self.host}: {raw_status!r:.100}")

        _LOGGER.debug("Status for %s: %s", device.serial_number, raw_status)
        self._connected = True
        self.last_status = status
        device.update_from_status_dict(status, source=self)

    async def async_get_all(self, device: OasisDevice) -> None:
        """
        Fetch the device status, identity and schedule and apply them to the device.

        The MAC address identifies the device: once known, a different MAC at this
        address means the IP now belongs to another device, so it isn't used. The
        firmware version is fetched if the status string doesn't include it, since
        the device isn't initialized without both. The schedule is optional.

        Raises:
            ValueError: If the address answers as a different device.
        """
        mac = await self._async_get(params={"GETMAC": ""})
        mac = mac.strip() if isinstance(mac, str) else ""
        if (
            mac
            and device.mac_address
            and _normalize_mac(mac) != _normalize_mac(device.mac_address)
        ):
            self._connected = False
            raise ValueError(
                f"{self.host} is now a different device ({mac}, expected "
                f"{device.mac_address})"
            )
        await self.async_get_status(device)
        if mac and not device.mac_address:
            device.update_from_status_dict({"mac_address": mac}, source=self)
        if not device.software_version:
            version = await self._async_get(params={"GETSOFTWAREVER": ""})
            if isinstance(version, str) and version.strip():
                device.update_from_status_dict(
                    {"software_version": version.strip()}, source=self
                )
        try:
            schedule = await self._async_get(params={"GETSCH": ""}, optional=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unable to fetch the schedule from %s: %s", self.host, err)
        else:
            if isinstance(schedule, str):
                device.update_from_status_dict({"schedule": schedule}, source=self)
