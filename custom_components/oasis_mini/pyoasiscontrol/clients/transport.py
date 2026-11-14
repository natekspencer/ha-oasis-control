from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..device import OasisDevice


@runtime_checkable
class OasisClientProtocol(Protocol):
    """Transport/client interface for an Oasis device.

    Concrete implementations:
    - MQTT client (remote connection)
    - HTTP client (direct LAN)
    - BLE client (direct Bluetooth)
    """

    async def async_get_mac_address(self, device: OasisDevice) -> str | None:
        """
        Retrieve the MAC address of the specified Oasis device.

        Parameters:
            device (OasisDevice): The target device to query.

        Returns:
            str | None: The device's MAC address as a string, or `None` if the MAC address is unavailable.
        """

    async def async_send_auto_clean_command(
        self, device: OasisDevice, auto_clean: bool
    ) -> None:
        """
        Enable or disable the device's auto-clean mode.

        Parameters:
            device (OasisDevice): The target Oasis device to send the command to.
            auto_clean (bool): `True` to enable auto-clean mode, `False` to disable it.
        """

    async def async_send_ball_speed_command(
        self,
        device: OasisDevice,
        speed: int,
    ) -> None:
        """
        Set the device's ball speed to the specified value.

        Parameters:
            device (OasisDevice): Target Oasis device to send the command to.
            speed (int): Desired ball speed value for the device.
        """

    async def async_send_led_command(
        self,
        device: OasisDevice,
        led_effect: str,
        color: str,
        led_speed: int,
        brightness: int,
    ) -> None:
        """
        Configure the device's LED effect, color, speed, and brightness.

        Parameters:
            device (OasisDevice): Target Oasis device to receive the LED command.
            led_effect (str): Name or identifier of the LED effect to apply.
            color (str): Color for the LED effect (format depends on implementation, e.g., hex code or color name).
            led_speed (int): Effect speed; larger values increase the animation speed.
            brightness (int): Brightness level as a percentage from 0 to 100.
        """

    async def async_send_sleep_command(self, device: OasisDevice) -> None:
        """
        Put the specified Oasis device into sleep mode.

        Parameters:
            device (OasisDevice): The target Oasis device to send the sleep command to.
        """

    async def async_send_move_job_command(
        self,
        device: OasisDevice,
        from_index: int,
        to_index: int,
    ) -> None:
        """
        Move a job within the device's job list from one index to another.

        Parameters:
            device (OasisDevice): Target Oasis device.
            from_index (int): Source index of the job in the device's job list.
            to_index (int): Destination index to move the job to.
        """

    async def async_send_change_track_command(
        self,
        device: OasisDevice,
        index: int,
    ) -> None:
        """
        Change the device's current track to the specified track index.

        Parameters:
            device (OasisDevice): The target Oasis device to receive the command.
            index (int): The index of the track to select on the device.
        """

    async def async_send_add_joblist_command(
        self,
        device: OasisDevice,
        tracks: list[int],
    ) -> None:
        """
        Add the given sequence of track indices to the device's job list.

        Parameters:
                device (OasisDevice): Target Oasis device to receive the new jobs.
                tracks (list[int]): Ordered list of track indices to append to the device's job list.
        """

    async def async_send_set_playlist_command(
        self,
        device: OasisDevice,
        playlist: list[int],
    ) -> None:
        """
        Set the device's current playlist to the provided sequence of track indices.

        Parameters:
            device (OasisDevice): The target Oasis device to receive the playlist.
            playlist (list[int]): Sequence of track indices in the desired playback order.
        """

    async def async_send_set_repeat_playlist_command(
        self,
        device: OasisDevice,
        repeat: bool,
    ) -> None:
        """
        Set whether the device should repeat the current playlist.

        Parameters:
            repeat (bool): True to enable repeating the current playlist, False to disable it.
        """

    async def async_send_set_autoplay_command(
        self,
        device: OasisDevice,
        option: str,
    ) -> None:
        """
        Send a command to configure the device's autoplay behavior.

        Parameters:
            device (OasisDevice): Target Oasis device to receive the command.
            option (str): Autoplay option to set (e.g., "on", "off", "shuffle", or other device-supported mode).
        """

    async def async_send_upgrade_command(
        self,
        device: OasisDevice,
        beta: bool,
    ) -> None:
        """
        Initiates a firmware upgrade on the given Oasis device.

        If `beta` is True, requests the device to use the beta upgrade channel; otherwise requests the stable channel.

        Parameters:
            device (OasisDevice): Target device to upgrade.
            beta (bool): Whether to use the beta upgrade channel (`True`) or the stable channel (`False`).
        """

    async def async_send_play_command(self, device: OasisDevice) -> None:
        """
        Send a play command to the specified Oasis device.

        Parameters:
                device (OasisDevice): The target device to instruct to start playback.
        """

    async def async_send_pause_command(self, device: OasisDevice) -> None:
        """
        Pause playback on the specified Oasis device.

        This sends a pause command to the device so it stops current playback.
        """

    async def async_send_stop_command(self, device: OasisDevice) -> None:
        """
        Send a stop command to the specified Oasis device to halt playback.

        Parameters:
            device (OasisDevice): The target Oasis device to receive the stop command.
        """

    async def async_send_reboot_command(self, device: OasisDevice) -> None:
        """
        Send a reboot command to the specified Oasis device.

        Parameters:
            device (OasisDevice): The target Oasis device to reboot.
        """

    async def async_get_all(self, device: OasisDevice) -> None:
        """
        Fetch comprehensive device data for the specified Oasis device.

        This method triggers retrieval of all relevant information (configuration, status, and runtime data) for the given device so the client's representation of that device can be refreshed.

        Parameters:
            device (OasisDevice): Target device whose data should be fetched and refreshed.
        """

    async def async_get_status(self, device: OasisDevice) -> None:
        """
        Retrieve the current runtime status for the specified Oasis device.

        Implementations should query the device for its current state (for example: playback, LED settings, job/track lists, and connectivity) and update any client-side representation or caches as needed.

        Parameters:
            device (OasisDevice): The target device to query.
        """
