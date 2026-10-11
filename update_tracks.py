"""Script to update track details from Grounded Labs."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from aiohttp import ClientError

from custom_components.oasis_mini.pyoasiscontrol import OasisCloudClient
from custom_components.oasis_mini.pyoasiscontrol.const import TRACKS
from custom_components.oasis_mini.pyoasiscontrol.exceptions import UnauthenticatedError

ACCESS_TOKEN = os.getenv("GROUNDED_TOKEN")


def get_author_name(data: dict) -> str:
    """
    Extracts the author's display name from a nested track data dictionary.

    Parameters:
        data (dict): A mapping representing track/result data. Expected shape is
            {"author": {"user": {"name": ..., "nickname": ...}}}.

    Returns:
        str: The author's `name` if present, otherwise the author's `nickname`, otherwise "Kinetic Oasis".
    """
    author = (data.get("author") or {}).get("user") or {}
    return author.get("name") or author.get("nickname") or "Kinetic Oasis"


def write_tracks(tracks: dict[int, dict[str, Any]]) -> None:
    """Write the track list to `pyoasiscontrol/tracks.json`."""
    with open(
        "custom_components/oasis_mini/pyoasiscontrol/tracks.json", "w", encoding="utf8"
    ) as file:
        json.dump(tracks, file, indent=2, ensure_ascii=False)


class UpdateTracksError(Exception):
    """The track update could not run at all."""


def annotate(level: str, message: str) -> None:
    """Print a message, as a GitHub Actions annotation when running in a workflow."""
    if os.getenv("GITHUB_ACTIONS") == "true":
        print(f"::{level}::{message}")
    else:
        print(f"{level.upper()}: {message}")


async def update_tracks() -> None:
    """
    Fetch tracks from the Grounded Labs cloud, detect new or changed public tracks
    compared to the local TRACKS mapping, augment changed entries with author and
    reduced SVG content, and persist the merged, sorted track list to
    `custom_components/oasis_mini/pyoasiscontrol/tracks.json`.

    A track whose details can't be fetched is skipped with a warning and keeps its
    existing data, so the remaining tracks still update.

    Raises:
        UnauthenticatedError: If the access token is missing, invalid or expired.
        aiohttp.ClientError: If the track list can't be fetched from the cloud.
        UpdateTracksError: If the cloud returns unexpected data, or no changed
            track's details could be fetched.
    """
    if not ACCESS_TOKEN:
        raise UnauthenticatedError("GROUNDED_TOKEN is not set")

    client = OasisCloudClient(access_token=ACCESS_TOKEN)
    try:
        data = await client.async_get_tracks()
        if not isinstance(data, list):
            raise UpdateTracksError(f"Unexpected track list from the cloud: {data!r}")

        updated_tracks: dict[int, dict[str, Any]] = {}
        failed: list[str] = []
        for result in filter(lambda d: d["public"], data):
            if (
                (track_id := result["id"]) not in TRACKS
                or any(
                    result[field] != TRACKS[track_id].get(field)
                    for field in ("name", "image", "png_image")
                )
                or TRACKS[track_id].get("author") != get_author_name(result)
            ):
                print(f"Updating track {track_id}: {result['name']}")
                try:
                    track_info = await client.async_get_track_info(int(track_id))
                except ClientError as ex:
                    track_info = None
                    print(f"  {type(ex).__name__}: {ex}")
                if not track_info:
                    failed.append(f"{track_id} ({result['name']})")
                    continue
                result["author"] = get_author_name(result)
                result["reduced_svg_content_new"] = track_info.get(
                    "reduced_svg_content_new"
                )
                updated_tracks[track_id] = result
    finally:
        await client.async_close()

    if failed and not updated_tracks:
        raise UpdateTracksError(
            f"Could not fetch details for any of the {len(failed)} changed tracks"
        )
    if failed:
        annotate(
            "warning",
            f"Skipped {len(failed)} track(s) whose details could not be fetched; "
            f"they keep their existing data: {', '.join(failed)}",
        )

    if not updated_tracks:
        print("No updated tracks")
        return

    track_ids = {d["id"] for d in data}
    tracks = {k: v for k, v in TRACKS.items() if k in track_ids}
    tracks.update(updated_tracks)
    tracks = dict(sorted(tracks.items(), key=lambda t: t[1]["name"].lower()))

    await asyncio.to_thread(write_tracks, tracks)
    print(f"Updated {len(updated_tracks)} track(s)")


def main() -> int:
    """Run the update and return the process exit code."""
    try:
        asyncio.run(update_tracks())
    except UnauthenticatedError as ex:
        annotate("error", f"Grounded Labs token is missing, invalid or expired: {ex}")
    except (ClientError, TimeoutError) as ex:
        annotate("error", f"Could not reach the Grounded Labs cloud: {ex!r}")
    except UpdateTracksError as ex:
        annotate("error", str(ex))
    else:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
