"""Tests for the Oasis device model."""

from __future__ import annotations

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
