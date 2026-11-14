"""Tests for the Oasis device model."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.oasis_mini.pyoasiscontrol import OasisDevice


@pytest.fixture
def oasis_device() -> OasisDevice:
    """Return a device drawing the last track of its playlist."""
    device = OasisDevice(model="Oasis Mini", serial_number="OM1")
    device.update_from_status_dict(
        {
            "status_code": 4,
            "playlist": [101, 102, 103],
            "playlist_index": 2,
            "progress": 1666,
        }
    )
    return device


def test_track_change_resets_progress(oasis_device: OasisDevice) -> None:
    """Progress from the previous track isn't carried over to the next track."""
    oasis_device.update_from_status_dict({"playlist_index": 0})

    assert oasis_device.track_id == 101
    assert oasis_device.progress == 0


def test_track_change_keeps_reported_progress(oasis_device: OasisDevice) -> None:
    """Progress reported alongside a track change (e.g. FULLSTATUS) is kept."""
    oasis_device.update_from_status_dict({"playlist_index": 0, "progress": 25})

    assert oasis_device.track_id == 101
    assert oasis_device.progress == 25


def test_playlist_change_with_same_track_keeps_progress(
    oasis_device: OasisDevice,
) -> None:
    """Changing the playlist without changing the current track keeps progress."""
    oasis_device.update_from_status_dict({"playlist": [101, 102, 103, 104]})

    assert oasis_device.track_id == 103
    assert oasis_device.progress == 1666


def test_playlist_change_with_new_track_resets_progress(
    oasis_device: OasisDevice,
) -> None:
    """Replacing the playlist so the current track changes resets progress."""
    oasis_device.update_from_status_dict({"playlist": [201, 202, 203]})

    assert oasis_device.track_id == 203
    assert oasis_device.progress == 0


def test_parse_status_string() -> None:
    """A FULLSTATUS string is parsed into device fields."""
    device = OasisDevice(model="Oasis Mini", serial_number="OM1")
    raw = "4;0;200;101,102,103;1;1234;0;1;99;150;#FFFFFF;0;0;200;1;1;0;0;2.60"

    status = device.parse_status_string(raw)

    assert status is not None
    assert status["status_code"] == 4
    assert status["playlist"] == [101, 102, 103]
    assert status["playlist_index"] == 1
    assert status["progress"] == 1234
    assert status["brightness"] == 150
    assert status["color"] == "#FFFFFF"
    assert status["software_version"] == "2.60"


def test_status_string_from_other_transport_respects_owner() -> None:
    """A status string from a non-active transport can't overwrite owned fields."""
    device = OasisDevice(model="Oasis Mini", serial_number="OM1")
    owner = MagicMock(status_fields=frozenset({"status_code"}))
    device.attach_client(owner)
    device.update_from_status_dict({"status_code": 4}, source=owner)

    device.update_from_status_string(
        "2;0;300;101,102;0;50;21;0;0;100;#d2b48c;0;0;200;1;1;6;1;3.54",
        source=MagicMock(),
    )

    assert device.status_code == 4
    assert device.ball_speed == 300


async def test_oversized_playlist_rejected_before_stopping() -> None:
    """A playlist too long for the transport is refused without stopping playback."""
    device = OasisDevice(model="Oasis Mini", serial_number="OM1")
    client = MagicMock(max_playlist_size=40)
    client.async_send_stop_command = AsyncMock()
    device.attach_client(client)

    with pytest.raises(ValueError, match="40"):
        await device.async_set_playlist(list(range(1, 42)))

    client.async_send_stop_command.assert_not_awaited()
