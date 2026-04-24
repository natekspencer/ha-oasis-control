"""Oasis MQTT client (multi-device)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import ssl
from typing import Any, Awaitable, Callable, Final, Iterable

import aiomqtt

from ..device import OasisDevice
from ..exceptions import UnauthenticatedError
from ..utils import _bit_to_bool, _parse_int
from .transport import OasisClientProtocol

_LOGGER = logging.getLogger(__name__)

# mqtt connection parameters
HOST: Final = "mqtt.grounded.so"
PORT: Final = 8084
PATH: Final = "mqtt"
RECONNECT_INTERVAL: Final = 4

# Command queue behaviour
MAX_PENDING_COMMANDS: Final = 10


class OasisMqttClient(OasisClientProtocol):
    """MQTT-based Oasis transport using WSS.

    Responsibilities:
    - Maintain a single MQTT connection to:
        wss://mqtt.grounded.so:8084/mqtt
    - Subscribe only to <serial>/STATUS/# for devices it knows about.
    - Publish commands to <serial>/COMMAND/CTL
    - Map MQTT payloads to OasisDevice.update_from_status_dict()
    """

    def __init__(self) -> None:
        # MQTT connection state
        """
        Initialize internal state for the MQTT transport client.

        Sets up connection state, per-device registries and events, subscription bookkeeping, and a bounded pending command queue capped by MAX_PENDING_COMMANDS.

        Attributes:
            _client: Active aiomqtt client or None.
            _loop_task: Background MQTT loop task or None.
            _connected_at: Timestamp of last successful connection or None.
            _connected_event: Event signaled when a connection is established.
            _stop_event: Event signaled to request the loop to stop.
            _devices: Mapping of device serial to OasisDevice instances.
            _initialized_events: Per-serial events signaled on receiving the full device initialization.
            _mac_events: Per-serial events signaled when a device MAC address is received.
            _subscribed_serials: Set of serials currently subscribed to STATUS topics.
            _subscription_lock: Lock protecting subscribe/unsubscribe operations.
            _command_queue: Bounded queue of pending (serial, payload) commands.
        """
        self._client: aiomqtt.Client | None = None
        self._loop_task: asyncio.Task | None = None
        self._connected_at: datetime | None = None

        self._connected_event: asyncio.Event = asyncio.Event()
        self._stop_event: asyncio.Event = asyncio.Event()

        # Known devices by serial
        self._devices: dict[str, OasisDevice] = {}

        # Per-device events
        self._initialized_events: dict[str, asyncio.Event] = {}
        self._mac_events: dict[str, asyncio.Event] = {}

        # Subscription bookkeeping
        self._subscribed_serials: set[str] = set()
        self._subscription_lock = asyncio.Lock()

        # Pending command queue: (serial, payload)
        self._command_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(
            maxsize=MAX_PENDING_COMMANDS
        )

    @property
    def is_running(self) -> bool:
        """Return `True` if the MQTT loop has been started and is not stopped."""
        return (
            self._loop_task is not None
            and not self._loop_task.done()
            and not self._stop_event.is_set()
        )

    @property
    def is_connected(self) -> bool:
        """Return `True` if the MQTT client is currently connected."""
        return self._connected_event.is_set()

    def register_device(self, device: OasisDevice) -> None:
        """
        Register an OasisDevice so MQTT messages for its serial are routed to that device.

        Ensures the device has a serial_number (raises ValueError if not), stores the device in the client's registry, creates per-device asyncio.Events for device initialization and MAC-address arrival, attaches this client to the device if it has no client, and schedules a subscription for the device's STATUS topics if the MQTT client is currently connected.

        Parameters:
            device (OasisDevice): The device instance to register.

        Raises:
            ValueError: If `device.serial_number` is not set.
        """
        if not device.serial_number:
            raise ValueError("Device must have serial_number set before registration")

        serial = device.serial_number
        self._devices[serial] = device

        # Ensure we have per-device events
        self._initialized_events.setdefault(serial, asyncio.Event())
        self._mac_events.setdefault(serial, asyncio.Event())

        # Attach ourselves as the client if the device doesn't already have one
        if not device.client:
            device.attach_client(self)

        # If we're already connected, subscribe to this device's topics
        if self._client is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._subscribe_serial(serial))
            except RuntimeError:
                # No running loop (unlikely in HA), so just log
                _LOGGER.debug(
                    "Could not schedule subscription for %s (no running loop)", serial
                )

    def register_devices(self, devices: Iterable[OasisDevice]) -> None:
        """
        Register multiple OasisDevice instances with the client.

        Parameters:
            devices (Iterable[OasisDevice]): Iterable of devices to register.
        """
        for device in devices:
            self.register_device(device)

    def unregister_device(self, device: OasisDevice) -> None:
        """
        Unregisters a device from MQTT routing and cleans up related per-device state.

        Removes the device's registration, initialization and MAC events. If there is an active MQTT client and the device's serial is currently subscribed, schedules an asynchronous unsubscription task. If the device has no serial_number, the call is a no-op.

        Parameters:
            device (OasisDevice): The device to unregister; must have `serial_number` set.
        """
        serial = device.serial_number
        if not serial:
            return

        self._devices.pop(serial, None)
        self._initialized_events.pop(serial, None)
        self._mac_events.pop(serial, None)

        # If connected and we were subscribed, unsubscribe
        if self._client is not None and serial in self._subscribed_serials:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._unsubscribe_serial(serial))
            except RuntimeError:
                _LOGGER.debug(
                    "Could not schedule unsubscription for %s (no running loop)",
                    serial,
                )

    async def _subscribe_serial(self, serial: str) -> None:
        """
        Subscribe to the device's STATUS topic pattern and mark the device as subscribed.

        Subscribes to "<serial>/STATUS/#" with QoS 1 and records the subscription; does nothing if the MQTT client is not connected or the serial is already subscribed.
        """
        if not self._client:
            return

        async with self._subscription_lock:
            if not self._client or serial in self._subscribed_serials:
                return

            topic = f"{serial}/STATUS/#"
            await self._client.subscribe([(topic, 1)])
            self._subscribed_serials.add(serial)
            _LOGGER.info("Subscribed to %s", topic)

    async def _unsubscribe_serial(self, serial: str) -> None:
        """
        Unsubscribe from the device's STATUS topic and update subscription state.

        If there is no active MQTT client or the serial is not currently subscribed, this is a no-op.
        Parameters:
            serial (str): Device serial used to build the topic "<serial>/STATUS/#".
        """
        if not self._client:
            return

        async with self._subscription_lock:
            if not self._client or serial not in self._subscribed_serials:
                return

            topic = f"{serial}/STATUS/#"
            await self._client.unsubscribe(topic)
            self._subscribed_serials.discard(serial)
            _LOGGER.info("Unsubscribed from %s", topic)

    async def _resubscribe_all(self) -> None:
        """Resubscribe to all known devices after (re)connect."""
        self._subscribed_serials.clear()
        for serial, device in self._devices.items():
            await self._subscribe_serial(serial)
            if not device.is_sleeping:
                await self.async_get_all(device)

    def start(
        self, username: str, get_token_method: Callable[[], Awaitable[str]]
    ) -> None:
        """Start MQTT connection loop."""
        if self._loop_task is None or self._loop_task.done():
            self._stop_event.clear()
            loop = asyncio.get_running_loop()
            self._loop_task = loop.create_task(
                self._mqtt_loop(username, get_token_method)
            )

    async def async_close(self) -> None:
        """Close connection loop and MQTT client."""
        await self.stop()

    async def stop(self) -> None:
        """
        Stop the MQTT client and clean up resources.

        Signals the background MQTT loop to stop, cancels the loop task,
        disconnects the MQTT client if connected, and drops any pending commands.
        """
        _LOGGER.debug("MQTT stop() called - beginning shutdown sequence")
        self._stop_event.set()

        if self._loop_task:
            _LOGGER.debug(
                "Cancelling MQTT background task (task=%s, done=%s)",
                self._loop_task,
                self._loop_task.done(),
            )
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            _LOGGER.debug("MQTT background task cancelled")

        if self._client:
            _LOGGER.debug("Disconnecting MQTT client from broker")
            try:
                await self._client.disconnect()
                _LOGGER.debug("MQTT client disconnected")
            except Exception:
                _LOGGER.exception("Error disconnecting MQTT client")
            finally:
                self._client = None

        # Drop queued commands
        if not self._command_queue.empty():
            _LOGGER.debug("Dropping queued commands")
            dropped = 0
            while not self._command_queue.empty():
                try:
                    self._command_queue.get_nowait()
                    self._command_queue.task_done()
                    dropped += 1
                except asyncio.QueueEmpty:
                    break
            _LOGGER.debug("MQTT dropped %s queued command(s)", dropped)

        _LOGGER.debug("MQTT shutdown sequence complete")

    async def wait_until_ready(
        self, device: OasisDevice, timeout: float = 10.0, request_status: bool = True
    ) -> bool:
        """
        Block until the MQTT client is connected and the device has emitted at least one STATUS message.

        If `request_status` is True, a status request is sent after the client is connected to prompt the device to report its state.

        Parameters:
            device (OasisDevice): The device to wait for; must have `serial_number` set.
            timeout (float): Maximum seconds to wait for connection and for the device to be initialized.
            request_status (bool): If True, issue a status refresh after connection to encourage a STATUS update.

        Returns:
            bool: `True` if the device was initialized within the timeout, `False` otherwise.

        Raises:
            RuntimeError: If the provided device does not have a `serial_number`.
        """
        serial = device.serial_number
        if not serial:
            raise RuntimeError("Device has no serial_number set")

        is_initialized_event = self._initialized_events.setdefault(
            serial, asyncio.Event()
        )

        # Wait for MQTT connection
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            _LOGGER.debug(
                "Timeout (%.1fs) waiting for MQTT connection (device %s)",
                timeout,
                serial,
            )
            return False

        # Optionally request a status refresh
        if request_status:
            try:
                await self.async_get_status(device)
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Could not request status for %s (not fully connected yet?)",
                    serial,
                )

        # Wait for initialization
        try:
            await asyncio.wait_for(is_initialized_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            _LOGGER.debug(
                "Timeout (%.1fs) waiting for initialization from %s",
                timeout,
                serial,
            )
            return False
        else:
            return True

    async def async_get_mac_address(self, device: OasisDevice) -> str | None:
        """
        Request a device's MAC address via an MQTT STATUS refresh and return it if available.

        If the device already has a MAC address, it is returned immediately. Otherwise the function requests a status update (which causes the device to publish MAC_ADDRESS) and waits up to 3 seconds for the MAC to arrive.

        Parameters:
            device (OasisDevice): The device whose MAC address will be requested.

        Returns:
            str | None: The device MAC address if obtained, `None` if the wait timed out and no MAC was received.

        Raises:
            RuntimeError: If the provided device has no serial_number set.
        """
        # If already known on the device, return it
        if device.mac_address:
            return device.mac_address

        serial = device.serial_number
        if not serial:
            raise RuntimeError("Device has no serial_number set")

        mac_event = self._mac_events.setdefault(serial, asyncio.Event())
        mac_event.clear()

        # Ask device to refresh status (including MAC_ADDRESS)
        await self.async_get_status(device)

        try:
            await asyncio.wait_for(mac_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            _LOGGER.debug("Timed out waiting for MAC_ADDRESS for %s", serial)

        return device.mac_address

    async def async_send_auto_clean_command(
        self, device: OasisDevice, auto_clean: bool
    ) -> None:
        """
        Set the device's automatic cleaning mode.

        Parameters:
            device (OasisDevice): Target Oasis device to send the command to.
            auto_clean (bool): True to enable automatic cleaning, False to disable.
        """
        payload = f"WRIAUTOCLEAN={1 if auto_clean else 0}"
        await self._publish_command(device, payload)

    async def async_send_ball_speed_command(
        self,
        device: OasisDevice,
        speed: int,
    ) -> None:
        """
        Set the device's ball speed.

        Parameters:
            device (OasisDevice): Target device.
            speed (int): Speed value to apply.
        """
        payload = f"WRIOASISSPEED={speed}"
        await self._publish_command(device, payload)

    async def async_send_led_command(
        self,
        device: OasisDevice,
        led_effect: str,
        color: str,
        led_speed: int,
        brightness: int,
    ) -> None:
        """
        Send an LED configuration command to the device.

        If `brightness` is greater than zero, the device is woken before sending the command.

        Parameters:
            device (OasisDevice): Target device (must have a serial number).
            led_effect (str): LED effect identifier to apply.
            color (str): Color value for the LED effect (format expected by device).
            led_speed (int): Speed/tempo for the LED effect.
            brightness (int): Brightness level to set; also used to determine wake behavior.
        """
        payload = f"WRILED={led_effect};0;{color};{led_speed};{brightness}"
        await self._publish_command(device, payload, bool(brightness))

    async def async_send_sleep_command(self, device: OasisDevice) -> None:
        """
        Send the sleep command to the specified Oasis device.

        Parameters:
            device (OasisDevice): Target device; must have a valid serial_number. If the MQTT client is not connected, the command may be queued for delivery when a connection is available.
        """
        await self._publish_command(device, "CMDSLEEP")

    async def async_send_move_job_command(
        self,
        device: OasisDevice,
        from_index: int,
        to_index: int,
    ) -> None:
        """
        Move a job in the device's playlist from one index to another.

        Parameters:
            device (OasisDevice): Target device to receive the command.
            from_index (int): Source index of the job in the playlist.
            to_index (int): Destination index where the job should be placed.
        """
        payload = f"MOVEJOB={from_index};{to_index}"
        await self._publish_command(device, payload)

    async def async_send_change_track_command(
        self,
        device: OasisDevice,
        index: int,
    ) -> None:
        """
        Change the device's current track to the specified track index.

        Parameters:
            device (OasisDevice): Target Oasis device.
            index (int): Track index to switch to (zero-based).
        """
        payload = f"CMDCHANGETRACK={index}"
        await self._publish_command(device, payload)

    async def async_send_add_joblist_command(
        self,
        device: OasisDevice,
        tracks: list[int],
    ) -> None:
        """
        Send an ADDJOBLIST command to add multiple tracks to the device's job list.

        Parameters:
            device (OasisDevice): Target device to receive the command.
            tracks (list[int]): List of track indices to add; elements will be joined as a comma-separated list in the command payload.
        """
        track_str = ",".join(map(str, tracks))
        payload = f"ADDJOBLIST={track_str}"
        await self._publish_command(device, payload)

    async def async_send_set_playlist_command(
        self,
        device: OasisDevice,
        playlist: list[int],
    ) -> None:
        """
        Set the device's playlist to the specified ordered list of track indices.

        Parameters:
            device (OasisDevice): Target Oasis device to receive the playlist command.
            playlist (list[int]): Ordered list of track indices to apply as the device's playlist.
        """
        track_str = ",".join(map(str, playlist))
        payload = f"WRIJOBLIST={track_str}"
        await self._publish_command(device, payload)

    async def async_send_set_repeat_playlist_command(
        self,
        device: OasisDevice,
        repeat: bool,
    ) -> None:
        """
        Send a command to enable or disable repeating the device's playlist.

        Parameters:
            device (OasisDevice): Target device; must have a serial number.
            repeat (bool): True to enable playlist repeat, False to disable it.
        """
        payload = f"WRIREPEATJOB={1 if repeat else 0}"
        await self._publish_command(device, payload)

    async def async_send_set_autoplay_command(
        self,
        device: OasisDevice,
        option: str,
    ) -> None:
        """
        Set the device's wait-after-job / autoplay option.

        Publishes a "WRIWAITAFTER=<option>" command for the specified device to configure how long the device waits after a job or to adjust autoplay behavior.

        Parameters:
            device (OasisDevice): Target device (must have a serial_number).
            option (str): Value accepted by the device firmware for the wait-after-job/autoplay setting (typically a numeric string or predefined option token).
        """
        payload = f"WRIWAITAFTER={option}"
        await self._publish_command(device, payload)

    async def async_send_upgrade_command(
        self,
        device: OasisDevice,
        beta: bool,
    ) -> None:
        """
        Request a firmware upgrade for the given device.

        Sends an upgrade command to the device and selects the beta channel when requested.

        Parameters:
            device (OasisDevice): Target device.
            beta (bool): If `True`, request a beta firmware upgrade; if `False`, request the stable firmware.
        """
        payload = f"CMDUPGRADE={1 if beta else 0}"
        await self._publish_command(device, payload)

    async def async_send_play_command(self, device: OasisDevice) -> None:
        """
        Send a "play" command to the given device and wake it if the device is sleeping.
        """
        await self._publish_command(device, "CMDPLAY", True)

    async def async_send_pause_command(self, device: OasisDevice) -> None:
        """
        Sends a pause command to the specified Oasis device.

        Publishes the "CMDPAUSE" command to the device's command topic.
        """
        await self._publish_command(device, "CMDPAUSE")

    async def async_send_stop_command(self, device: OasisDevice) -> None:
        """
        Send the "stop" command to the given Oasis device.

        Parameters:
            device (OasisDevice): Target device to receive the stop command; must be registered with a valid serial number.
        """
        await self._publish_command(device, "CMDSTOP")

    async def async_send_reboot_command(self, device: OasisDevice) -> None:
        """
        Send a reboot command to the specified Oasis device.

        Parameters:
            device (OasisDevice): Target device to receive the reboot command; must have a valid serial_number.
        """
        await self._publish_command(device, "CMDBOOT")

    async def async_get_all(self, device: OasisDevice) -> None:
        """Request FULLSTATUS + SCHEDULE (compact snapshot)."""
        await self._publish_command(device, "GETALL")

    async def async_get_status(self, device: OasisDevice) -> None:
        """
        Request the device to publish its current STATUS topics.
        """
        await self._publish_command(device, "GETSTATUS")

    async def _enqueue_command(self, serial: str, payload: str) -> None:
        """Queue a command to be sent when connected.

        If the queue is full, drop the oldest command to make room.
        """
        if self._command_queue.full():
            try:
                dropped = self._command_queue.get_nowait()
                self._command_queue.task_done()
                _LOGGER.debug(
                    "Command queue full, dropping oldest command: %s", dropped
                )
            except asyncio.QueueEmpty:
                # race: became empty between full() and get_nowait()
                pass

        await self._command_queue.put((serial, payload))
        _LOGGER.debug("Queued command for %s: %s", serial, payload)

    async def _flush_pending_commands(self) -> None:
        """
        Flush queued commands by publishing them to each device's COMMAND/CTL topic.

        This consumes all entries from the internal command queue, skipping entries for devices that are no longer registered, publishing each payload to "<serial>/COMMAND/CTL" with QoS 1, and marking queue tasks done. If a publish fails, the failed command is re-queued and flushing stops so remaining queued commands will be retried on the next reconnect.
        """
        if not self._client:
            return

        while not self._command_queue.empty():
            if not self._client:
                break

            try:
                serial, payload = self._command_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            try:
                # Skip commands for unknown devices
                if serial not in self._devices:
                    _LOGGER.debug(
                        "Skipping queued command for unknown device %s: %s",
                        serial,
                        payload,
                    )
                    continue

                topic = f"{serial}/COMMAND/CTL"
                _LOGGER.debug("Flushing queued MQTT command %s => %s", topic, payload)
                await self._client.publish(topic, payload.encode(), qos=1)
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to flush queued command for %s, re-queuing", serial
                )
                # Put it back; we'll try again on next reconnect
                await self._enqueue_command(serial, payload)
                break
            finally:
                # Ensure we always balance the get(), even on cancellation
                self._command_queue.task_done()

    async def _publish_command(
        self, device: OasisDevice, payload: str, wake: bool = False
    ) -> None:
        """
        Publish a command payload to the device's MQTT COMMAND topic, queueing it if the client is not connected.

        If `wake` is True and the device reports it is sleeping, requests a full status refresh before publishing. If the MQTT client is not connected or publish fails, the command is enqueued for later delivery.

        Parameters:
            device (OasisDevice): Target device; must have a valid `serial_number`.
            payload (str): Command payload to send to the device.
            wake (bool): If True, refresh the device state when the device is sleeping before sending the command.

        Raises:
            RuntimeError: If the provided device has no serial number set.
        """
        serial = device.serial_number
        if not serial:
            raise RuntimeError("Device has no serial number set")

        if wake and device.is_sleeping:
            await self.async_get_all(device)

        # If not connected, just queue the command
        if not self._client or not self._connected_event.is_set():
            _LOGGER.debug(
                "MQTT not connected, queueing command for %s: %s", serial, payload
            )
            await self._enqueue_command(serial, payload)
            return

        topic = f"{serial}/COMMAND/CTL"
        try:
            _LOGGER.debug("MQTT publish %s => %s", topic, payload)
            await self._client.publish(topic, payload.encode(), qos=1)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "MQTT publish failed, queueing command for %s: %s", serial, payload
            )
            await self._enqueue_command(serial, payload)

    async def _mqtt_loop(
        self, username: str, get_token_method: Callable[[], Awaitable[str]]
    ) -> None:
        """
        Run the MQTT WebSocket connection loop that maintains connection, subscriptions,
        and message handling.

        This background coroutine establishes a persistent WSS MQTT connection to the
        configured broker, sets connection state on successful connect, resubscribes to
        known device STATUS topics, flushes any queued outbound commands, and dispatches
        incoming MQTT messages to the status handler. On disconnect or error it clears
        connection state and subscription tracking, and retries connecting after the
        configured backoff interval until the client is stopped.
        """
        loop = asyncio.get_running_loop()
        tls_context = await loop.run_in_executor(None, ssl.create_default_context)

        while not self._stop_event.is_set():
            try:
                _LOGGER.info("Connecting MQTT WSS to wss://%s:%s/%s", HOST, PORT, PATH)

                async with aiomqtt.Client(
                    hostname=HOST,
                    port=PORT,
                    transport="websockets",
                    tls_context=tls_context,
                    username=username,
                    password=await get_token_method(),
                    keepalive=30,
                    websocket_path=f"/{PATH}",
                ) as client:
                    self._client = client
                    self._connected_event.set()
                    self._connected_at = datetime.now(UTC)
                    _LOGGER.info("Connected to MQTT broker")

                    # Subscribe only to STATUS topics for known devices
                    await self._resubscribe_all()

                    # Flush any queued commands now that we're connected
                    await self._flush_pending_commands()

                    async for msg in client.messages:
                        if self._stop_event.is_set():
                            break
                        await self._handle_status_message(msg)

            except asyncio.CancelledError:
                break
            except UnauthenticatedError as err:
                _LOGGER.warning("MQTT authentication failed: %s", err)
                break
            except Exception as ex:  # noqa: BLE001
                _LOGGER.warning("MQTT connection error: %s", ex)

            finally:
                if self._connected_event.is_set():
                    self._connected_event.clear()
                    if self._connected_at:
                        _LOGGER.info(
                            "MQTT was connected for %s",
                            datetime.now(UTC) - self._connected_at,
                        )
                    self._connected_at = None
                self._client = None
                self._subscribed_serials.clear()

            if not self._stop_event.is_set():
                _LOGGER.info(
                    "Disconnected from broker, retrying in %.1fs", RECONNECT_INTERVAL
                )
                await asyncio.sleep(RECONNECT_INTERVAL)

    async def _handle_status_message(self, msg: aiomqtt.Message) -> None:
        """
        Map an incoming MQTT STATUS message to an OasisDevice state update.

        Expects msg.topic in the form "<serial>/STATUS/<STATUS_NAME>" and decodes msg.payload as text.
        If the topic corresponds to a registered device, extracts the relevant status field and calls
        the device's update_from_status_dict with a mapping of the parsed values. For the "MAC_ADDRESS"
        status, sets the per-device MAC event to signal arrival of the MAC address. Always sets the
        per-device initialization event once the appropriate messages are processed for that serial.

        Parameters:
            msg (aiomqtt.Message): Incoming MQTT message; topic identifies device serial and status.
        """
        topic_str = str(msg.topic) if msg.topic is not None else ""
        payload = msg.payload.decode(errors="replace")

        parts = topic_str.split("/")
        # Expect: "<serial>/STATUS/<STATUS_NAME>"
        if len(parts) < 3:
            return

        serial, _, status_name = parts[:3]

        device = self._devices.get(serial)
        if not device:
            _LOGGER.debug("Received MQTT for unknown device %s: %s", serial, topic_str)
            return

        data: dict[str, Any] = {}

        try:
            if status_name == "OASIS_STATUS":
                data["status_code"] = int(payload)
            elif status_name == "OASIS_ERROR":
                data["error"] = int(payload)
            elif status_name == "OASIS_SPEEED":
                data["ball_speed"] = int(payload)
            elif status_name == "JOBLIST":
                data["playlist"] = [
                    track_id
                    for track_str in payload.split(",")
                    if (track_id := _parse_int(track_str))
                ]
            elif status_name == "CURRENTJOB":
                data["playlist_index"] = int(payload)
            elif status_name == "CURRENTLINE":
                data["progress"] = int(payload)
            elif status_name == "LED_EFFECT":
                data["led_effect"] = payload
            elif status_name == "LED_EFFECT_COLOR":
                data["led_color_id"] = payload
            elif status_name == "LED_SPEED":
                data["led_speed"] = int(payload)
            elif status_name == "LED_BRIGHTNESS":
                data["brightness"] = int(payload)
            elif status_name == "LED_MAX":
                data["brightness_max"] = int(payload)
            elif status_name == "LED_EFFECT_PARAM":
                data["color"] = payload if payload.startswith("#") else None
            elif status_name == "SYSTEM_BUSY":
                data["busy"] = payload in ("1", "true", "True")
            elif status_name == "DOWNLOAD_PROGRESS":
                data["download_progress"] = int(payload)
            elif status_name == "REPEAT_JOB":
                data["repeat_playlist"] = payload in ("1", "true", "True")
            elif status_name == "WAIT_AFTER_JOB":
                data["autoplay"] = _parse_int(payload)
            elif status_name == "AUTO_CLEAN":
                data["auto_clean"] = payload in ("1", "true", "True")
            elif status_name == "SOFTWARE_VER":
                data["software_version"] = payload
            elif status_name == "MAC_ADDRESS":
                data["mac_address"] = payload
                mac_event = self._mac_events.setdefault(serial, asyncio.Event())
                mac_event.set()
            elif status_name == "WIFI_SSID":
                data["wifi_ssid"] = payload
            elif status_name == "WIFI_IP":
                data["wifi_ip"] = payload
            elif status_name == "WIFI_PDNS":
                data["wifi_pdns"] = payload
            elif status_name == "WIFI_SDNS":
                data["wifi_sdns"] = payload
            elif status_name == "WIFI_GATE":
                data["wifi_gate"] = payload
            elif status_name == "WIFI_SUB":
                data["wifi_sub"] = payload
            elif status_name == "WIFI_STATUS":
                data["wifi_connected"] = _bit_to_bool(payload)
            elif status_name == "SCHEDULE":
                data["schedule"] = payload
            elif status_name == "ENVIRONMENT":
                data["environment"] = payload
            elif status_name == "FULLSTATUS":
                if parsed := device.parse_status_string(payload):
                    data = parsed
            else:
                _LOGGER.warning(
                    "Unknown status received for %s: %s=%s",
                    serial,
                    status_name,
                    payload,
                )
        except Exception:
            _LOGGER.exception(
                "Error parsing MQTT payload for %s %s: %r", serial, status_name, payload
            )
            return

        if data:
            device.update_from_status_dict(data)

        is_initialized_event = self._initialized_events.setdefault(
            serial, asyncio.Event()
        )
        if not is_initialized_event.is_set() and device.is_initialized:
            is_initialized_event.set()
