"""Oasis cloud client."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any
from urllib.parse import urljoin

from aiohttp import ClientResponseError, ClientSession

from ..exceptions import UnauthenticatedError
from ..utils import now

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://app.grounded.so"
PLAYLISTS_REFRESH_LIMITER = timedelta(minutes=5)
SOFTWARE_REFRESH_LIMITER = timedelta(hours=1)


class OasisCloudClient:
    """Cloud client for Oasis.

    Responsibilities:
    - Manage aiohttp session (optionally owned)
    - Manage access token
    - Provide async_* helpers for:
        * login/logout
        * user info
        * devices
        * tracks/playlists
        * latest software metadata
    """

    def __init__(
        self,
        *,
        session: ClientSession | None = None,
        access_token: str | None = None,
    ) -> None:
        """
        Initialize the OasisCloudClient.

        Sets the optional aiohttp session and access token, records whether the client owns the session, and initializes caches and asyncio locks for playlists and software metadata.

        Parameters:
            session (ClientSession | None): Optional aiohttp ClientSession to use. If None, the client will create and own a session later.
            access_token (str | None): Optional initial access token for authenticated requests.
        """
        self._session = session
        self._owns_session = session is None
        self._access_token = access_token

        now_dt = now()

        # playlists cache
        self._playlists_cache: dict[bool, list[dict[str, Any]]] = {False: [], True: []}
        self._playlists_next_refresh = {False: now_dt, True: now_dt}
        self._playlists_lock = asyncio.Lock()

        # software metadata cache
        self._software_details: dict[str, int | str] | None = None
        self._software_next_refresh = now()
        self._software_lock = asyncio.Lock()

    @property
    def playlists(self) -> list[dict]:
        """Return all cached playlists, deduplicated by ID."""
        seen = set()
        merged: list[dict] = []

        for items in self._playlists_cache.values():
            for pl in items:
                if (pid := pl.get("id")) not in seen:
                    seen.add(pid)
                    merged.append(pl)

        return merged

    @property
    def session(self) -> ClientSession:
        """
        Get the active aiohttp ClientSession, creating and owning a new session if none exists or the existing session is closed.

        Returns:
            ClientSession: The active aiohttp ClientSession; a new session is created and marked as owned by this client when necessary.
        """
        if self._session is None or self._session.closed:
            self._session = ClientSession()
            self._owns_session = True
        return self._session

    async def async_close(self) -> None:
        """
        Close the aiohttp ClientSession owned by this client if it exists and is open.

        This should be called during teardown when the client is responsible for the session.
        """
        if self._session and not self._session.closed and self._owns_session:
            await self._session.close()

    @property
    def access_token(self) -> str | None:
        """
        Access token used for authenticated requests or None if not set.

        Returns:
            The current access token string, or `None` if no token is stored.
        """
        return self._access_token

    @access_token.setter
    def access_token(self, value: str | None) -> None:
        """
        Set the access token used for authenticated requests.

        Parameters:
            value (str | None): The bearer token to store; pass None to clear the stored token.
        """
        self._access_token = value

    async def async_login(self, email: str, password: str) -> None:
        """
        Log in to the Oasis cloud and store the received access token on the client.

        Performs an authentication request using the provided credentials and saves the returned access token to self.access_token for use in subsequent authenticated requests.
        """
        response = await self._async_request(
            "POST",
            urljoin(BASE_URL, "api/auth/login"),
            json={"email": email, "password": password},
        )
        token = response.get("access_token") if isinstance(response, dict) else None
        self.access_token = token
        _LOGGER.debug("Cloud login succeeded, token set: %s", bool(token))

    async def async_logout(self) -> None:
        """
        End the current authenticated session with the Oasis cloud.

        Performs a logout request and clears the stored access token on success.
        """
        await self._async_auth_request("GET", "api/auth/logout")
        self.access_token = None

    async def async_get_user(self) -> dict:
        """
        Return information about the currently authenticated user.

        Returns:
            dict: A mapping containing the user's details as returned by the cloud API.

        Raises:
            UnauthenticatedError: If no access token is available or the request is unauthorized.
        """
        return await self._async_auth_request("GET", "api/auth/user")

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """
        Retrieve the current user's devices from the cloud API.

        Returns:
            list[dict[str, Any]]: A list of device objects as returned by the API.
        """
        return await self._async_auth_request("GET", "api/user/devices")

    async def async_get_playlists(
        self, personal_only: bool = False
    ) -> list[dict[str, Any]]:
        """
        Retrieve playlists from the Oasis cloud, optionally limited to the authenticated user's personal playlists.

        The result is cached and will be refreshed according to PLAYLISTS_REFRESH_LIMITER to avoid frequent network requests.

        Parameters:
            personal_only (bool): If True, return only playlists owned by the authenticated user; otherwise return all available playlists.

        Returns:
            list[dict[str, Any]]: A list of playlist objects represented as dictionaries; an empty list if no playlists are available.
        """
        now_dt = now()

        def _is_cache_valid() -> bool:
            """
            Determine whether the playlists cache is still valid.

            Returns:
                `true` if the playlists cache contains data and the next refresh timestamp is later than the current time, `false` otherwise.
            """
            cache = self._playlists_cache[personal_only]
            next_refresh = self._playlists_next_refresh[personal_only]
            return bool(cache) and next_refresh > now_dt

        if _is_cache_valid():
            return self._playlists_cache[personal_only]

        async with self._playlists_lock:
            # Double-check in case another task just refreshed it
            now_dt = now()
            if _is_cache_valid():
                return self._playlists_cache[personal_only]

            params = {"my_playlists": str(personal_only).lower()}
            playlists = await self._async_auth_request(
                "GET", "api/playlist", params=params
            )

            if not isinstance(playlists, list):
                playlists = []

            self._playlists_cache[personal_only] = playlists
            self._playlists_next_refresh[personal_only] = (
                now_dt + PLAYLISTS_REFRESH_LIMITER
            )

            return playlists

    async def async_get_track_info(self, track_id: int) -> dict[str, Any] | None:
        """
        Retrieve information for a single track from the cloud.

        Returns:
            dict: Track detail dictionary. If the track is not found (HTTP 404), returns a dict with keys `id` and `name` where `name` is "Unknown Title (#{id})". Returns `None` on other failures.
        """
        try:
            return await self._async_auth_request("GET", f"api/track/{track_id}")
        except ClientResponseError as err:
            if err.status == 404:
                return {"id": track_id, "name": f"Unknown Title (#{track_id})"}
            raise
        except UnauthenticatedError:
            raise
        except Exception:
            _LOGGER.exception("Error fetching track %s", track_id)
        return None

    async def async_get_tracks(
        self, tracks: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """
        Retrieve track details for the given track IDs, following pagination until all pages are fetched.

        Parameters:
            tracks (list[int] | None): Optional list of track IDs to request. If omitted or None, an empty list is sent to the API.

        Returns:
            list[dict[str, Any]]: A list of track detail dictionaries returned by the cloud, aggregated across all pages (may be empty).
        """
        response = await self._async_auth_request(
            "GET",
            "api/track",
            params={"ids[]": tracks or []},
        )
        if not response:
            return []
        track_details = response.get("data", [])
        while next_page_url := response.get("next_page_url"):
            response = await self._async_auth_request("GET", next_page_url)
            track_details += response.get("data", [])
        return track_details

    async def async_get_latest_software_details(
        self, *, force_refresh: bool = False
    ) -> dict[str, int | str] | None:
        """
        Retrieve the latest software metadata from the cloud, using an internal cache to limit requests.

        Parameters:
            force_refresh (bool): If True, bypass the cache and fetch fresh metadata from the cloud.

        Returns:
            details (dict[str, int | str] | None): A mapping of software metadata keys to integer or string values, or `None` if no metadata is available.
        """
        now_dt = now()

        def _is_cache_valid() -> bool:
            """
            Determine whether the cached software metadata should be used instead of fetching fresh data.

            Returns:
                True if the software cache exists, has not expired, and a force refresh was not requested; False otherwise.
            """
            return (
                not force_refresh
                and self._software_details is not None
                and self._software_next_refresh > now_dt
            )

        if _is_cache_valid():
            return self._software_details

        async with self._software_lock:
            # Double-check in case another task just refreshed it
            now_dt = now()
            if _is_cache_valid():
                return self._software_details

            details = await self._async_auth_request("GET", "api/software/last-version")

            if not isinstance(details, dict):
                details = {}

            self._software_details = details
            self._software_next_refresh = now_dt + SOFTWARE_REFRESH_LIMITER

            return self._software_details

    async def async_get_mqtt_token(self) -> str:
        """Get a token to interface with the MQTT server."""
        response = await self._async_auth_request(
            "POST", urljoin(BASE_URL, "api/v2/auth/mqtt/login")
        )
        token = response.get("token") if isinstance(response, dict) else None
        if not isinstance(token, str) or not token:
            raise UnauthenticatedError("MQTT login did not return a valid token")
        _LOGGER.debug("MQTT login succeeded, token returned: %s", bool(token))
        return token

    async def _async_auth_request(self, method: str, url: str, **kwargs: Any) -> Any:
        """
        Perform a cloud API request using the stored access token.

        If `url` is relative it will be joined with the module `BASE_URL`. The method will
        inject an `Authorization: Bearer <token>` header into the request.

        Parameters:
            method (str): HTTP method (e.g., "GET", "POST").
            url (str): Absolute URL or path relative to `BASE_URL`.
            **kwargs: Passed through to the underlying request helper.

        Returns:
            The parsed response value (JSON object, text, or None) as returned by the underlying request helper.

        Raises:
            UnauthenticatedError: If no access token is set.
        """
        if not self.access_token:
            raise UnauthenticatedError("Unauthenticated")

        headers = kwargs.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {self.access_token}"

        return await self._async_request(
            method,
            url if url.startswith("http") else urljoin(BASE_URL, url),
            headers=headers,
            **kwargs,
        )

    async def _async_request(self, method: str, url: str, **kwargs: Any) -> Any:
        """
        Perform a single HTTP request and return a normalized response value.

        Performs the request using the client's session and:
        - If the response status is 200:
          - returns parsed JSON for `application/json`.
          - returns text for `text/plain`.
          - if `text/html` and the URL targets the cloud base URL and contains a login page, raises UnauthenticatedError.
          - returns `None` for other content types.
        - If the response status is 401, raises UnauthenticatedError.
        - For other non-200 statuses, re-raises the response's HTTP error.

        Parameters:
            method: HTTP method to use (e.g., "GET", "POST").
            url: Request URL or path.
            **kwargs: Passed through to the session request (e.g., `params`, `json`, `headers`).

        Returns:
            The parsed JSON object, response text, or `None` depending on the response content type.

        Raises:
            UnauthenticatedError: when the server indicates the client is unauthenticated (401) or a cloud login page is returned.
            aiohttp.ClientResponseError: for other non-success HTTP statuses raised by `response.raise_for_status()`.
        """
        session = self.session
        _LOGGER.debug(
            "%s %s",
            method,
            session._build_url(url).update_query(  # pylint: disable=protected-access
                kwargs.get("params"),
            ),
        )
        response = await session.request(method, url, **kwargs)

        if response.status == 200:
            if response.content_type == "application/json":
                return await response.json()
            if response.content_type == "text/plain":
                return await response.text()
            if response.content_type == "text/html" and BASE_URL in url:
                text = await response.text()
                if "login-page" in text:
                    raise UnauthenticatedError("Unauthenticated")
            return None

        if response.status == 401:
            raise UnauthenticatedError("Unauthenticated")

        response.raise_for_status()
