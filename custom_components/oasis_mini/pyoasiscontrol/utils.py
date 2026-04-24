"""Oasis control utils."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import logging
import math
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

APP_KEY = "5joW8W4Usk4xUXu5bIIgGiHloQmzMZUMgz6NWQnNI04="

BACKGROUND_FILL = ("#CCC9C4", "#28292E")
COLOR_DARK = ("#28292E", "#F4F5F8")
COLOR_LIGHT = ("#FFFFFF", "#222428")
COLOR_LIGHT_SHADE = ("#FFFFFF", "#86888F")
COLOR_MEDIUM_SHADE = ("#E5E2DE", "#86888F")
COLOR_MEDIUM_TINT = ("#B8B8B8", "#FFFFFF")

IMAGE_URL = "https://app.grounded.so/uploads/{image}"
IMAGE_PROGRESS_URL = "https://app.grounded.so/uploads/{path_draw}/{progress}.webp"


def _bit_to_bool(val: str) -> bool:
    """Convert a bit string to bool."""
    return val == "1"


def _parse_int(val: Any | None) -> int:
    """
    Parse a string into an integer, falling back to 0 when conversion fails.

    Parameters:
        val (Any | None): String potentially containing an integer value.

    Returns:
        int: The parsed integer, or 0 if `val` cannot be converted.
    """
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def create_svg(track: dict, progress: int) -> str | None:
    """
    Create an SVG visualization of a track showing progress as a completed path and indicator.

    Builds an SVG representation from the track's "svg_content" and the provided progress value. If progress is supplied, the function will decrypt the stored SVG content (if needed), compute which path segments are complete using the track's optional "reduced_svg_content_new" value or the number of path segments, and render a base arc, completed arc, track, completed track segment, background circle, and a ball indicator positioned at the current progress point. Returns None if input is missing or an error occurs.

    Parameters:
        track (dict): Track data containing at minimum an "svg_content" entry and optionally "reduced_svg_content_new" to indicate total segments.
        progress (int): Current progress expressed as a count relative to the track's total segments.

    Returns:
        str | None: Serialized SVG markup as a UTF-8 string when successful, otherwise `None`.
    """
    if track and (svg_content := track.get("svg_content")):
        try:
            if progress is not None:
                svg_content = decrypt_svg_content(svg_content)
                paths = svg_content.split("L")
                total = track.get("reduced_svg_content_new", 0) or len(paths)
                percent = min((100 * progress) / total, 100)
                progress = math.floor((percent / 100) * (len(paths) - 1))

                svg = Element(
                    "svg",
                    {
                        "title": "OasisStatus",
                        "version": "1.1",
                        "viewBox": "-25 -25 250 250",
                        "xmlns": "http://www.w3.org/2000/svg",
                        "class": "svg-status",
                    },
                )

                style = SubElement(svg, "style")
                style.text = f"""
                    circle.background {{ fill: {BACKGROUND_FILL[0]}; }}
                    circle.ball {{ stroke: {COLOR_DARK[0]}; fill: {COLOR_LIGHT[0]}; }}
                    path.progress_arc {{ stroke: {COLOR_MEDIUM_SHADE[0]}; }}
                    path.progress_arc_complete {{ stroke: {COLOR_DARK[0]}; }}
                    path.track {{ stroke: {COLOR_LIGHT_SHADE[0]}; }}
                    path.track_complete {{ stroke: {COLOR_MEDIUM_TINT[0]}; }}
                    @media (prefers-color-scheme: dark) {{
                        circle.background {{ fill: {BACKGROUND_FILL[1]}; }}
                        circle.ball {{ stroke: {COLOR_DARK[1]}; fill: {COLOR_LIGHT[1]}; }}
                        path.progress_arc {{ stroke: {COLOR_MEDIUM_SHADE[1]}; }}
                        path.progress_arc_complete {{ stroke: {COLOR_DARK[1]}; }}
                        path.track {{ stroke: {COLOR_LIGHT_SHADE[1]}; }}
                        path.track_complete {{ stroke: {COLOR_MEDIUM_TINT[1]}; }}
                    }}""".replace("\n", " ").strip()

                group = SubElement(
                    svg,
                    "g",
                    {"stroke-linecap": "round", "fill": "none", "fill-rule": "evenodd"},
                )

                progress_arc = "M37.85,203.55L32.85,200.38L28.00,196.97L23.32,193.32L18.84,189.45L14.54,185.36L10.45,181.06L6.58,176.58L2.93,171.90L-0.48,167.05L-3.65,162.05L-6.57,156.89L-9.24,151.59L-11.64,146.17L-13.77,140.64L-15.63,135.01L-17.22,129.30L-18.51,123.51L-19.53,117.67L-20.25,111.79L-20.69,105.88L-20.84,99.95L-20.69,94.02L-20.25,88.11L-19.53,82.23L-18.51,76.39L-17.22,70.60L-15.63,64.89L-13.77,59.26L-11.64,53.73L-9.24,48.31L-6.57,43.01L-3.65,37.85L-0.48,32.85L2.93,28.00L6.58,23.32L10.45,18.84L14.54,14.54L18.84,10.45L23.32,6.58L28.00,2.93L32.85,-0.48L37.85,-3.65L43.01,-6.57L48.31,-9.24L53.73,-11.64L59.26,-13.77L64.89,-15.63L70.60,-17.22L76.39,-18.51L82.23,-19.53L88.11,-20.25L94.02,-20.69L99.95,-20.84L105.88,-20.69L111.79,-20.25L117.67,-19.53L123.51,-18.51L129.30,-17.22L135.01,-15.63L140.64,-13.77L146.17,-11.64L151.59,-9.24L156.89,-6.57L162.05,-3.65L167.05,-0.48L171.90,2.93L176.58,6.58L181.06,10.45L185.36,14.54L189.45,18.84L193.32,23.32L196.97,28.00L200.38,32.85L203.55,37.85L206.47,43.01L209.14,48.31L211.54,53.73L213.67,59.26L215.53,64.89L217.12,70.60L218.41,76.39L219.43,82.23L220.15,88.11L220.59,94.02L220.73,99.95L220.59,105.88L220.15,111.79L219.43,117.67L218.41,123.51L217.12,129.30L215.53,135.01L213.67,140.64L211.54,146.17L209.14,151.59L206.47,156.89L203.55,162.05L200.38,167.05L196.97,171.90L193.32,176.58L189.45,181.06L185.36,185.36L181.06,189.45L176.58,193.32L171.90,196.97L167.05,200.38"

                SubElement(
                    group,
                    "path",
                    {
                        "class": "progress_arc",
                        "stroke-width": "2",
                        "d": progress_arc,
                    },
                )

                progress_arc_paths = progress_arc.split("L")
                paths_to_draw = math.floor((percent * len(progress_arc_paths)) / 100)
                SubElement(
                    group,
                    "path",
                    {
                        "class": "progress_arc_complete",
                        "stroke-width": "4",
                        "d": "L".join(progress_arc_paths[:paths_to_draw]),
                    },
                )

                SubElement(
                    group,
                    "circle",
                    {
                        "class": "background",
                        "r": "100",
                        "cx": "100",
                        "cy": "100",
                        "opacity": "0.3",
                    },
                )

                SubElement(
                    group,
                    "path",
                    {
                        "class": "track",
                        "stroke-width": "1.4",
                        "d": svg_content,
                    },
                )

                SubElement(
                    group,
                    "path",
                    {
                        "class": "track_complete",
                        "stroke-width": "1.8",
                        "d": "L".join(paths[:progress]),
                    },
                )

                _cx, _cy = map(float, paths[progress].replace("M", "").split(","))
                SubElement(
                    group,
                    "circle",
                    {
                        "class": "ball",
                        "stroke-width": "1",
                        "cx": f"{_cx:.2f}",
                        "cy": f"{_cy:.2f}",
                        "r": "5",
                    },
                )

                return tostring(svg).decode()
        except Exception:
            _LOGGER.exception("Error creating svg")
    return None


def decrypt_svg_content(svg_content: dict[str, str]):
    """Decrypt SVG content using AES CBC mode."""
    if decrypted := svg_content.get("decrypted"):
        return decrypted

    # decode base64-encoded data
    key = base64.b64decode(APP_KEY)
    iv = base64.b64decode(svg_content["iv"])
    ciphertext = base64.b64decode(svg_content["content"])

    # create the cipher and decrypt the ciphertext
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(ciphertext) + decryptor.finalize()

    # remove PKCS7 padding
    pad_len = decrypted[-1]
    decrypted = decrypted[:-pad_len].decode("utf-8")

    # save decrypted data so we don't have to do this each time
    svg_content["decrypted"] = decrypted

    return decrypted


def get_image_url_from_track(
    track: dict[str, Any] | None, progress: int | None = None
) -> str | None:
    """Get the image URL from a track."""
    if not isinstance(track, dict):
        return None
    if progress is not None and (path_draw := track.get("path_draw")):
        return IMAGE_PROGRESS_URL.format(
            path_draw=path_draw, progress=math.floor(progress / 50)
        )
    return IMAGE_URL.format(image=image) if (image := track.get("image")) else None


def get_track_ids_from_playlist(playlist: dict[str, Any]) -> list[int]:
    """Get a list of track ids from a playlist."""
    return [track["id"] for track in (playlist.get("patterns") or []) if "id" in track]


def now() -> datetime:
    return datetime.now(UTC)
