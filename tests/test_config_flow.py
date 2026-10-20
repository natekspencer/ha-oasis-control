"""Tests for the Oasis device-approval config flow."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.oasis_mini.const import DOMAIN
from custom_components.oasis_mini.pyoasiscontrol import UnauthenticatedError
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import USER_ID

EMAIL = "user@example.com"
PASSWORD = "hunter2"
TOKEN = "approved-token"
CREDENTIALS = {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}

CHALLENGE = {
    "login_id": "abc123",
    "code": "123456",
    "poll_secret": "s3cr3t",
    "expires_at": "2026-10-20T06:31:11.000000Z",
    "qr_payload": "https://app.grounded.so/app-scheme/?page=login-validation",
}


@pytest.fixture
def mock_flow_client() -> Generator[MagicMock]:
    """Patch the cloud client used by the config flow."""
    client = MagicMock()
    client.access_token = None
    client.async_start_login = AsyncMock(return_value=CHALLENGE)
    client.async_check_login = AsyncMock(return_value={"status": "pending"})
    client.async_get_user = AsyncMock(return_value={"id": USER_ID})
    client.async_close = AsyncMock()

    def _approve(*_args: object) -> dict[str, object]:
        client.access_token = TOKEN
        return {"authenticated": True, "access_token": TOKEN}

    client.approve = _approve

    with patch(
        "custom_components.oasis_mini.config_flow.create_client", return_value=client
    ):
        yield client


async def _start(hass: HomeAssistant) -> str:
    """Begin the user flow and submit credentials; return the flow id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == {"code": CHALLENGE["code"]}
    return result["flow_id"]


async def test_full_flow(hass: HomeAssistant, mock_flow_client: MagicMock) -> None:
    """A new entry is created once the login is approved in the app."""
    flow_id = await _start(hass)

    mock_flow_client.async_check_login.side_effect = mock_flow_client.approve
    result = await hass.config_entries.flow.async_configure(flow_id, {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {CONF_EMAIL: EMAIL, CONF_ACCESS_TOKEN: TOKEN}
    assert result["result"].unique_id == str(USER_ID)
    mock_flow_client.async_start_login.assert_awaited_once_with(EMAIL, PASSWORD)


async def test_pending_then_approved(
    hass: HomeAssistant, mock_flow_client: MagicMock
) -> None:
    """Submitting before approval reshows the form until the login is approved."""
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"] == {"base": "login_pending"}

    mock_flow_client.async_check_login.side_effect = mock_flow_client.approve
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("status", "error"),
    [
        ("expired", "login_expired"),
        ("not_found", "login_expired"),
        ("cancelled", "login_rejected"),
    ],
)
async def test_terminal_status_returns_to_credentials(
    hass: HomeAssistant, mock_flow_client: MagicMock, status: str, error: str
) -> None:
    """A rejected or expired request returns to the credentials step with an error."""
    flow_id = await _start(hass)

    mock_flow_client.async_check_login.return_value = {"status": status}
    result = await hass.config_entries.flow.async_configure(flow_id, {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}


@pytest.mark.parametrize("result_payload", [{"status": "weird"}, {}])
async def test_unexpected_status_shows_unknown(
    hass: HomeAssistant, mock_flow_client: MagicMock, result_payload: dict
) -> None:
    """An unknown or missing status stays on the confirm step as an unknown error."""
    flow_id = await _start(hass)

    mock_flow_client.async_check_login.return_value = result_payload
    result = await hass.config_entries.flow.async_configure(flow_id, {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"] == {"base": "unknown"}


async def test_invalid_credentials(
    hass: HomeAssistant, mock_flow_client: MagicMock
) -> None:
    """Rejected credentials keep the user on the credentials step."""
    mock_flow_client.async_start_login.side_effect = UnauthenticatedError

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_already_configured(
    hass: HomeAssistant, mock_flow_client: MagicMock
) -> None:
    """A second entry for the same account aborts."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=str(USER_ID), data={CONF_EMAIL: EMAIL}
    ).add_to_hass(hass)

    flow_id = await _start(hass)
    mock_flow_client.async_check_login.side_effect = mock_flow_client.approve
    result = await hass.config_entries.flow.async_configure(flow_id, {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_wrong_account(
    hass: HomeAssistant, mock_flow_client: MagicMock
) -> None:
    """Reauthenticating with a different account aborts."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="9999",
        data={CONF_EMAIL: EMAIL, CONF_ACCESS_TOKEN: "old"},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CREDENTIALS
    )
    assert result["step_id"] == "confirm"

    mock_flow_client.async_check_login.side_effect = mock_flow_client.approve
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
