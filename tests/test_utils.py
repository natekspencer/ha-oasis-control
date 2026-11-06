"""Tests for the Oasis utilities."""

from __future__ import annotations

import pytest

from custom_components.oasis_mini.pyoasiscontrol.utils import get_image_url_from_track

PATH_DRAW = "0-1000/128"


@pytest.mark.parametrize(
    ("file_lines", "progress", "frame"),
    [
        (68850, 68850, 1376),  # Line count is a multiple of 50, so no frame 1377
        (68850, 68849, 1376),
        (20038, 20038, 400),
        (None, 100, 2),
        (-5, 100, 2),  # Invalid line counts are ignored
    ],
)
def test_image_url_frame(file_lines: int | None, progress: int, frame: int) -> None:
    """The frame never goes past the last one the track has."""
    track = {"path_draw": PATH_DRAW, "file_lines": file_lines}

    url = get_image_url_from_track(track, progress)

    assert url == f"https://app.grounded.so/uploads/{PATH_DRAW}/{frame}.webp"
