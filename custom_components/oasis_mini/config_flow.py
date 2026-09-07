"""Config flow for Oasis device integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import ClientConnectorError
from httpx import ConnectError, HTTPStatusError
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_EMAIL, CONF_PASSWORD

from .const import DOMAIN
from .helpers import create_client
from .pyoasiscontrol import UnauthenticatedError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)


class OasisDeviceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Oasis devices."""

    VERSION = 1
    MINOR_VERSION = 3

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """
        Begin the reauthentication flow for an existing config entry.

        Parameters:
            entry_data (Mapping[str, Any]): Data from the existing config entry that triggered the reauthentication flow.

        Returns:
            ConfigFlowResult: Result that presents the reauthentication confirmation dialog to the user.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Present a reauthentication confirmation form to the user.

        If `user_input` is provided it will be used as the form values; otherwise the existing entry's data are used as suggested values.

        Returns:
            ConfigFlowResult: Result of the config flow step that renders the reauthentication form or advances the flow.
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry

        suggested_values = user_input or entry.data
        return await self._async_step(
            "reauth_confirm", STEP_USER_DATA_SCHEMA, user_input, suggested_values
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Handle the initial user configuration step for the Oasis integration.

        Parameters:
            user_input (dict[str, Any] | None): Optional prefilled values (e.g., `email`, `password`) submitted by the user.

        Returns:
            ConfigFlowResult: Result of the "user" step — a form prompting for credentials, an abort, or a created/updated config entry.
        """
        return await self._async_step(
            "user", STEP_USER_DATA_SCHEMA, user_input, user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry

        suggested_values = user_input or entry.data
        return await self._async_step(
            "reconfigure", STEP_USER_DATA_SCHEMA, user_input, suggested_values
        )

    async def _async_step(
        self,
        step_id: str,
        schema: vol.Schema,
        user_input: dict[str, Any] | None = None,
        suggested_values: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """
        Handle a single config flow step: validate input, create or update entries, or render the form.

        If valid credentials are provided, this will create a new config entry (title set to the provided email) or update an existing entry and trigger a reload. The step will abort if the validated account conflicts with an existing entry's unique ID. If no input is provided or validation fails, the flow returns a form populated with the given schema, any suggested values, and validation errors.

        Parameters:
            step_id: Identifier of the flow step to render or process.
            schema: Voluptuous schema used to build the form.
            user_input: Submitted values from the form; when present, used for validation and entry creation/update.
            suggested_values: Values to pre-fill into the form schema when rendering.

        Returns:
            A ConfigFlowResult representing either a created entry, an update-and-reload abort, an abort due to a unique-id conflict, or a form to display with errors and suggested values.
        """
        errors = {}

        if user_input is not None:
            if not (errors := await self.validate_client(user_input)):
                entry_id = self.context.get("entry_id")
                existing_entry = self.hass.config_entries.async_get_entry(entry_id)
                if existing_entry and existing_entry.unique_id:
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                if existing_entry:
                    return self.async_update_reload_and_abort(
                        existing_entry,
                        unique_id=self.unique_id,
                        title=user_input[CONF_EMAIL],
                        data=user_input,
                        reload_even_if_entry_is_unchanged=False,
                    )

                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(
                    title=user_input[CONF_EMAIL], data=user_input
                )

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested_values),
            errors=errors,
        )

    async def validate_client(self, user_input: dict[str, Any]) -> dict[str, str]:
        """
        Validate provided credentials by attempting to authenticate with the Oasis API and retrieve the user's identity.

        Parameters:
            user_input (dict[str, Any]): Mutable credential mapping containing at least `email` and `password`.
                On success, this mapping will be updated with `CONF_ACCESS_TOKEN` (the received access token)
                and the `password` key will be removed.

        Returns:
            dict[str, str]: A mapping of form field names to error keys. Common keys:
                - `"base": "invalid_auth"` when credentials are incorrect or connection refused.
                - `"base": "timeout_connect"` when the authentication request times out.
                - `"base": "unknown"` for unexpected errors.
                - `"base": "<http error text>"` when the server returns an HTTP error.
        """
        errors = {}
        client = create_client(self.hass, user_input)
        try:
            async with asyncio.timeout(10):
                await client.async_login(
                    email=user_input[CONF_EMAIL], password=user_input[CONF_PASSWORD]
                )
                user_input[CONF_ACCESS_TOKEN] = client.access_token
                user = await client.async_get_user()
                await self.async_set_unique_id(str(user["id"]))
                del user_input[CONF_PASSWORD]
            if not self.unique_id:
                errors["base"] = "invalid_auth"
        except UnauthenticatedError:
            errors["base"] = "invalid_auth"
        except asyncio.TimeoutError:
            errors["base"] = "timeout_connect"
        except ConnectError:
            errors["base"] = "invalid_auth"
        except ClientConnectorError:
            errors["base"] = "invalid_auth"
        except HTTPStatusError as err:
            errors["base"] = str(err)
        except Exception:
            _LOGGER.exception("Error while attempting to validate client")
            errors["base"] = "unknown"
        finally:
            await client.async_close()
        return errors
