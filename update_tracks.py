"""Script to update track details from Grounded Labs."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from custom_components.oasis_mini.pyoasiscontrol import OasisCloudClient
from custom_components.oasis_mini.pyoasiscontrol.const import TRACKS

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


async def update_tracks() -> None:
    """
    Fetch tracks from the Grounded Labs cloud, detect new or changed public tracks
    compared to the local TRACKS mapping, augment changed entries with author and
    reduced SVG content, and persist the merged, sorted track list to
    `custom_components/oasis_mini/pyoasiscontrol/tracks.json`.

    Side effects:
    - May print error or status messages to stdout.
    - Writes the updated tracks JSON file.
    - Ensures the OasisCloudClient session is closed and returns early on errors or
      unexpected data.
    """
    client = OasisCloudClient(access_token=ACCESS_TOKEN)
    try:
        try:
            data = await client.async_get_tracks()
        except Exception as ex:  # noqa: BLE001 - report any API failure and exit
            print(type(ex).__name__, ex)
            return

        if not isinstance(data, list):
            print("Unexpected result:", data)
            return

        updated_tracks: dict[int, dict[str, Any]] = {}
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
                track_info = await client.async_get_track_info(int(track_id))
                if not track_info:
                    print("No track info")
                    break
                result["author"] = get_author_name(result)
                result["reduced_svg_content_new"] = track_info.get(
                    "reduced_svg_content_new"
                )
                updated_tracks[track_id] = result
    finally:
        await client.async_close()

    if not updated_tracks:
        print("No updated tracks")
        return

    track_ids = {d["id"] for d in data}
    tracks = {k: v for k, v in TRACKS.items() if k in track_ids}
    tracks.update(updated_tracks)
    tracks = dict(sorted(tracks.items(), key=lambda t: t[1]["name"].lower()))

    await asyncio.to_thread(write_tracks, tracks)


if __name__ == "__main__":
    asyncio.run(update_tracks())
