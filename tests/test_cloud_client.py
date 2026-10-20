"""Tests for the Oasis cloud client login helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.oasis_mini.pyoasiscontrol import (
    OasisCloudClient,
    UnauthenticatedError,
)

FULL_CHALLENGE = {
    "login_id": "abc123",
    "code": "123456",
    "poll_secret": "s3cr3t",
    "expires_at": "2026-10-20T06:31:11.000000Z",
    "qr_payload": "https://app.grounded.so/app-scheme/?page=login-validation",
}


async def test_start_login_returns_challenge() -> None:
    """A complete challenge is returned unchanged."""
    client = OasisCloudClient()
    client._async_request = AsyncMock(return_value=FULL_CHALLENGE)

    assert await client.async_start_login("user@example.com", "pw") == FULL_CHALLENGE


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"poll_secret": "s3cr3t"},
        {k: v for k, v in FULL_CHALLENGE.items() if k != "qr_payload"},
        {k: v for k, v in FULL_CHALLENGE.items() if k != "login_id"},
        {**FULL_CHALLENGE, "code": ""},
    ],
)
async def test_start_login_rejects_incomplete_challenge(response: object) -> None:
    """A response missing any field the flow uses is treated as a failed login."""
    client = OasisCloudClient()
    client._async_request = AsyncMock(return_value=response)

    with pytest.raises(UnauthenticatedError):
        await client.async_start_login("user@example.com", "pw")


async def test_check_login_stores_token_when_approved() -> None:
    """An approved check stores the issued access token on the client."""
    client = OasisCloudClient()
    client._async_request = AsyncMock()  # unused; guard against real requests
    client.session.request = AsyncMock(  # type: ignore[attr-defined]
        return_value=_json_response({"authenticated": True, "access_token": "tok"})
    )

    result = await client.async_check_login("abc123", "s3cr3t")

    assert result["authenticated"] is True
    assert client.access_token == "tok"
    await client.async_close()


def _json_response(payload: dict) -> AsyncMock:
    """Return a fake aiohttp response carrying a JSON body."""
    response = AsyncMock()
    response.content_type = "application/json"
    response.json = AsyncMock(return_value=payload)
    return response
