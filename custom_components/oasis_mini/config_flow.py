"""Config flow for Oasis device integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import ClientConnectorError, ClientResponseError
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.selector import (
    QrCodeSelector,
    QrCodeSelectorConfig,
    QrErrorCorrectionLevel,
)

from .const import DOMAIN
from .helpers import create_client
from .pyoasiscontrol import OasisCloudClient, UnauthenticatedError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)

# Steps that collect credentials; each matches an async_step_<id> entry point
CREDENTIALS_STEPS = ("user", "reauth_confirm", "reconfigure")


class OasisDeviceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Oasis devices.

    The cloud only accepts password logins from its official app, so sign-in uses
    a device-approval flow: credentials start an approval request that the user
    confirms in the Oasis Control app (by scanning a QR code or entering a code),
    after which the flow polls for the issued access token.
    """

    VERSION = 1
    MINOR_VERSION = 3

    _email: str | None = None
    _challenge: dict[str, Any] | None = None
    _credentials_step: str = "user"

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Begin the reauthentication flow for an existing config entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials to reauthenticate an existing entry."""
        return await self._async_credentials_step("reauth_confirm", user_input)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials for a new entry."""
        return await self._async_credentials_step("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials to reconfigure an existing entry."""
        return await self._async_credentials_step("reconfigure", user_input)

    async def _async_credentials_step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """
        Show the credentials form, or start a device-approval login.

        On submit, the credentials begin an approval request; on success the flow
        advances to the confirmation step where the user approves it in the app.
        """
        if user_input is None:
            return self._show_credentials_form(step_id)

        self._credentials_step = step_id
        errors = await self._async_start_login(user_input)
        if errors:
            return self._show_credentials_form(step_id, user_input, errors)

        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Wait for the login to be approved in the app, then finish.

        The form shows a QR code and a short code to confirm in the Oasis Control
        app. Submitting polls once: if approved the entry is created or updated, if
        still pending the form is shown again, and if the request was rejected or
        expired the flow returns to the credentials step.
        """
        assert self._challenge is not None
        if user_input is None:
            return self._show_confirm_form()

        client = create_client(self.hass, {})
        try:
            async with asyncio.timeout(10):
                result = await client.async_check_login(
                    self._challenge["login_id"], self._challenge["poll_secret"]
                )
        except (TimeoutError, ClientConnectorError):
            await client.async_close()
            return self._show_confirm_form({"base": "cannot_connect"})
        except Exception:
            await client.async_close()
            _LOGGER.exception("Error while checking Oasis login approval")
            return self._show_confirm_form({"base": "unknown"})

        # Finish outside the broad except so HA's abort signals propagate
        if result.get("authenticated") and client.access_token:
            try:
                return await self._async_finish_login(client)
            finally:
                await client.async_close()

        await client.async_close()
        status = result.get("status")
        if status == "pending":
            return self._show_confirm_form({"base": "login_pending"})

        if status in ("expired", "not_found"):
            error = "login_expired"
        elif status == "cancelled":
            error = "login_rejected"
        else:
            # Log only the keys so the token/secret are never recorded
            _LOGGER.error(
                "Unexpected Oasis login check response (keys: %s)", sorted(result)
            )
            return self._show_confirm_form({"base": "unknown"})

        return self._show_credentials_form(
            self._credentials_step, {CONF_EMAIL: self._email}, {"base": error}
        )

    async def _async_start_login(self, user_input: dict[str, Any]) -> dict[str, str]:
        """
        Start a device-approval login and store the returned challenge.

        Returns:
            dict[str, str]: Form errors keyed by field; empty when the approval
            request was started successfully.
        """
        self._email = user_input[CONF_EMAIL]
        client = create_client(self.hass, {})
        try:
            async with asyncio.timeout(10):
                self._challenge = await client.async_start_login(
                    self._email, user_input[CONF_PASSWORD]
                )
        except UnauthenticatedError:
            return {"base": "invalid_auth"}
        except TimeoutError:
            return {"base": "timeout_connect"}
        except ClientConnectorError:
            return {"base": "cannot_connect"}
        except ClientResponseError as err:
            _LOGGER.error("Oasis login request rejected: %s", err)
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Error while starting Oasis login")
            return {"base": "unknown"}
        finally:
            await client.async_close()
        return {}

    async def _async_finish_login(self, client: OasisCloudClient) -> ConfigFlowResult:
        """Create or update the config entry once a login has been approved."""
        try:
            async with asyncio.timeout(10):
                user = await client.async_get_user()
            user_id = str(user["id"])
        except Exception:
            _LOGGER.exception("Error while fetching the Oasis user")
            return self._show_confirm_form({"base": "unknown"})

        # Set the unique id outside the broad except so HA's abort signals
        # (e.g. another flow already in progress) propagate unchanged
        await self.async_set_unique_id(user_id)

        data = {CONF_EMAIL: self._email, CONF_ACCESS_TOKEN: client.access_token}

        entry_id = self.context.get("entry_id")
        existing_entry = self.hass.config_entries.async_get_entry(entry_id)
        if existing_entry and existing_entry.unique_id:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
        if existing_entry:
            return self.async_update_reload_and_abort(
                existing_entry,
                unique_id=self.unique_id,
                title=self._email,
                data=data,
                reload_even_if_entry_is_unchanged=False,
            )

        self._abort_if_unique_id_configured(updates=data)
        return self.async_create_entry(title=self._email, data=data)

    def _show_credentials_form(
        self,
        step_id: str,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Render a credentials form, prefilling the email where known."""
        suggested_values = user_input
        if suggested_values is None and step_id != "user":
            entry = self.hass.config_entries.async_get_entry(
                self.context.get("entry_id")
            )
            suggested_values = dict(entry.data) if entry else None

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, suggested_values
            ),
            errors=errors or {},
        )

    def _show_confirm_form(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Render the approval form with a QR code and the confirmation code."""
        assert self._challenge is not None
        data_schema = vol.Schema(
            {
                vol.Optional("qr_code"): QrCodeSelector(
                    config=QrCodeSelectorConfig(
                        data=self._challenge["qr_payload"],
                        scale=6,
                        error_correction_level=QrErrorCorrectionLevel.QUARTILE,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="confirm",
            data_schema=data_schema,
            errors=errors or {},
            description_placeholders={"code": self._challenge["code"]},
        )
