"""Tests for the Oasis image entity."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.oasis_mini.image import OasisDeviceImageEntity
from custom_components.oasis_mini.pyoasiscontrol import OasisDevice
from custom_components.oasis_mini.pyoasiscontrol.utils import get_image_url_from_track
from homeassistant.components.image import async_get_image
from homeassistant.components.image.const import DATA_COMPONENT
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .conftest import DEVICE_STATUS, TRACKS

ENTITY_ID = "image.oasis"


def _get_entity(hass: HomeAssistant) -> OasisDeviceImageEntity:
    """Return the image entity object."""
    entity = hass.data[DATA_COMPONENT].get_entity(ENTITY_ID)
    assert isinstance(entity, OasisDeviceImageEntity)
    return entity


def _url(track_id: int, progress: int) -> str | None:
    """Return the expected image URL for a track at a given progress."""
    return get_image_url_from_track(TRACKS[track_id], progress)


async def _update(hass: HomeAssistant, device: OasisDevice, **data: Any) -> None:
    """Apply a status update to the device, as an MQTT message would."""
    device.update_from_status_dict(data)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("init_integration")
async def test_image_follows_track_being_drawn(
    hass: HomeAssistant, device: OasisDevice, freezer: FrozenDateTimeFactory
) -> None:
    """The image updates as the current track is drawn."""
    entity = _get_entity(hass)
    assert entity.image_url == _url(101, 100)
    initial_state = hass.states.get(ENTITY_ID).state

    freezer.tick(timedelta(seconds=10))
    await _update(hass, device, progress=200)

    assert entity.image_url == _url(101, 200)
    assert hass.states.get(ENTITY_ID).state != initial_state


@pytest.mark.usefixtures("init_integration")
async def test_finished_track_kept_until_next_track_draws(
    hass: HomeAssistant, device: OasisDevice, freezer: FrozenDateTimeFactory
) -> None:
    """
    The finished track stays shown after the device advances the playlist.

    Replays the MQTT sequence logged when a track finishes: the device pauses,
    moves to the next track and resets progress before drawing anything.
    """
    entity = _get_entity(hass)
    await _update(hass, device, progress=1666)
    finished_state = hass.states.get(ENTITY_ID).state

    for data in (
        {"status_code": 5},
        {"playlist_index": 1},
        {"progress": 0},
        {"status_code": 4},
    ):
        freezer.tick(timedelta(seconds=1))
        await _update(hass, device, **data)
        assert entity.image_url == _url(101, 1666)
        assert hass.states.get(ENTITY_ID).state == finished_state

    freezer.tick(timedelta(seconds=1))
    await _update(hass, device, progress=12)

    assert entity.image_url == _url(102, 12)
    assert hass.states.get(ENTITY_ID).state != finished_state


@pytest.mark.usefixtures("init_integration")
async def test_autoplay_without_pause_waits_for_drawing(
    hass: HomeAssistant, device: OasisDevice
) -> None:
    """When the device moves on while playing, the image waits for the new track to draw."""
    entity = _get_entity(hass)
    await _update(hass, device, progress=1666)

    await _update(hass, device, playlist_index=1)
    assert device.progress == 0
    assert entity.image_url == _url(101, 1666)

    await _update(hass, device, progress=3)
    assert entity.image_url == _url(102, 3)


@pytest.mark.usefixtures("init_integration")
async def test_track_change_while_stopped_not_shown(
    hass: HomeAssistant, device: OasisDevice
) -> None:
    """Selecting a different track while stopped keeps the current drawing."""
    entity = _get_entity(hass)

    await _update(hass, device, status_code=2)
    await _update(hass, device, playlist_index=1, progress=0)

    assert entity.image_url == _url(101, 100)


@pytest.mark.parametrize(
    "device_status",
    [
        DEVICE_STATUS
        | {
            "playlist": [101, 103],
            "progress": 2,
        }
    ],
)
async def test_svg_image_for_shown_track(
    hass: HomeAssistant, init_integration: MockConfigEntry, device: OasisDevice
) -> None:
    """Tracks with SVG content are rendered as an SVG of the shown track."""
    await _update(hass, device, playlist_index=1)
    await _update(hass, device, progress=3)

    image = await async_get_image(hass, ENTITY_ID)

    assert image.content_type == "image/svg+xml"
    assert image.content.startswith(b"<svg")


@pytest.fixture
def restore_cache(
    hass: HomeAssistant, request: pytest.FixtureRequest
) -> dict[str, Any]:
    """Seed the restore cache with the parametrized extra data."""
    stored: dict[str, Any] = request.param
    mock_restore_cache_with_extra_data(
        hass, ((State(ENTITY_ID, "2026-10-13T15:07:37+00:00"), stored),)
    )
    return stored


@pytest.mark.parametrize(
    "device_status",
    [DEVICE_STATUS | {"status_code": 5, "playlist_index": 1, "progress": 0}],
)
@pytest.mark.parametrize(
    "restore_cache", [{"track_id": 101, "progress": 1666}], indirect=True
)
@pytest.mark.usefixtures("restore_cache", "init_integration")
async def test_restore_finished_track(hass: HomeAssistant) -> None:
    """After a restart, the last drawn track is restored if the device isn't drawing."""
    entity = _get_entity(hass)

    assert entity.image_url == _url(101, 1666)
    assert entity.image_last_updated == dt_util.parse_datetime(
        "2026-10-13T15:07:37+00:00"
    )


@pytest.mark.parametrize(
    "device_status", [DEVICE_STATUS | {"playlist_index": 1, "progress": 50}]
)
@pytest.mark.parametrize(
    "restore_cache", [{"track_id": 101, "progress": 1666}], indirect=True
)
@pytest.mark.usefixtures("restore_cache", "init_integration")
async def test_restore_ignored_while_drawing(hass: HomeAssistant) -> None:
    """After a restart, a device that is drawing takes precedence over restored state."""
    assert _get_entity(hass).image_url == _url(102, 50)


@pytest.mark.parametrize(
    "device_status", [DEVICE_STATUS | {"status_code": 5, "progress": 900}]
)
@pytest.mark.parametrize(
    "restore_cache", [{"track_id": 101, "progress": 400}], indirect=True
)
@pytest.mark.usefixtures("restore_cache", "init_integration")
async def test_restore_prefers_device_progress_on_same_track(
    hass: HomeAssistant,
) -> None:
    """After a restart, newer device progress on the restored track is kept."""
    assert _get_entity(hass).image_url == _url(101, 900)


@pytest.mark.parametrize(
    "device_status",
    [DEVICE_STATUS | {"status_code": 5, "playlist_index": 1, "progress": 0}],
)
@pytest.mark.parametrize(
    "restore_cache", [{"track_id": None, "progress": 0}], indirect=True
)
@pytest.mark.usefixtures("restore_cache", "init_integration")
async def test_restore_without_track(hass: HomeAssistant) -> None:
    """Restored state without a track falls back to the device's current track."""
    assert _get_entity(hass).image_url == _url(102, 0)
