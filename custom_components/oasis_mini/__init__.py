"""Support for Oasis devices."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.helpers.entity_registry as er

from .const import DOMAIN
from .coordinator import OasisDeviceCoordinator
from .entity import OasisDeviceEntity
from .helpers import create_client
from .pyoasiscontrol import OasisDevice, UnauthenticatedError

type OasisDeviceConfigEntry = ConfigEntry[OasisDeviceCoordinator]

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.LIGHT,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


def setup_platform_from_coordinator(
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
    make_entities: Callable[[Iterable[OasisDevice]], Iterable[OasisDeviceEntity]],
    update_before_add: bool = False,
) -> None:
    """
    Populate entities for devices managed by the coordinator and add entities for any devices discovered later.

    This registers a listener on the coordinator to detect newly discovered devices by serial number and calls `make_entities` to construct entity objects for those devices, passing them to `async_add_entities`. The initial device set is processed immediately; subsequent discoveries are handled via the coordinator listener.

    Parameters:
        entry: Config entry containing the coordinator in its `runtime_data`.
        async_add_entities: Home Assistant callback to add entities to the platform.
        make_entities: Callable that accepts an iterable of `OasisDevice` objects and returns an iterable of `OasisDeviceEntity` instances to add.
        update_before_add: If true, entities will be updated before being added.
    """
    coordinator = entry.runtime_data
    known_serials: set[str] = set()
    signal = coordinator._device_initialized_signal

    @callback
    def _check_devices() -> None:
        """Add entities for any initialized devices not yet seen."""
        devices = coordinator.data or []
        new_devices: list[OasisDevice] = []

        for device in devices:
            serial = device.serial_number
            if not device.is_initialized or not serial or serial in known_serials:
                continue

            known_serials.add(serial)
            new_devices.append(device)

        if not new_devices:
            return

        if entities := make_entities(new_devices):
            async_add_entities(entities, update_before_add)

    @callback
    def _handle_device_initialized(device: OasisDevice) -> None:
        """
        Dispatcher callback for when a single device becomes initialized.

        Adds entities immediately for that device if we haven't seen it yet.
        """
        serial = device.serial_number
        if not serial or serial in known_serials or not device.is_initialized:
            return

        known_serials.add(serial)

        if entities := make_entities([device]):
            async_add_entities(entities, update_before_add)

    # Initial population from current coordinator data
    _check_devices()

    # Future changes: new devices / account re-sync via coordinator
    entry.async_on_unload(coordinator.async_add_listener(_check_devices))

    # Device-level initialization events via dispatcher
    entry.async_on_unload(
        async_dispatcher_connect(coordinator.hass, signal, _handle_device_initialized)
    )


async def async_setup_entry(hass: HomeAssistant, entry: OasisDeviceConfigEntry) -> bool:
    """
    Initialize Oasis cloud for a config entry, create and refresh the device
    coordinator, register update listeners for discovered devices, forward platform
    setup, and update the entry's metadata as needed.

    Returns:
        True if the config entry was set up successfully.
    """
    cloud_client = create_client(hass, entry.data)
    try:
        user = await cloud_client.async_get_user()
    except UnauthenticatedError as err:
        await cloud_client.async_close()
        raise ConfigEntryAuthFailed(err) from err
    except Exception:
        await cloud_client.async_close()
        raise

    coordinator = OasisDeviceCoordinator(hass, entry, cloud_client)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_close()
        raise

    if entry.unique_id != (user_id := str(user["id"])):
        hass.config_entries.async_update_entry(entry, unique_id=user_id)

    if not coordinator.data:
        _LOGGER.warning("No devices associated with account")

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: OasisDeviceConfigEntry
) -> bool:
    """
    Cleanly unload an Oasis device config entry.

    Unloads all supported platforms and closes the coordinator connections.

    Returns:
        `True` if all platforms were unloaded successfully, `False` otherwise.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    try:
        await entry.runtime_data.async_close()
    except Exception:
        _LOGGER.exception("Error closing Oasis coordinator during unload")
    return unload_ok


async def async_remove_entry(
    hass: HomeAssistant, entry: OasisDeviceConfigEntry
) -> None:
    """
    Perform logout and cleanup for the cloud client associated with the config entry.

    Attempts to call the cloud client's logout method and logs any exception encountered, then ensures the client is closed.
    """
    cloud_client = create_client(hass, entry.data)
    try:
        await cloud_client.async_logout()
    except Exception:
        _LOGGER.exception("Error attempting to logout from the cloud")
    await cloud_client.async_close()


async def async_migrate_entry(
    hass: HomeAssistant, entry: OasisDeviceConfigEntry
) -> bool:
    """
    Migrate an Oasis config entry to the current schema (minor version 3).

    Performs in-place migrations for older entries:
    - Renames select entity unique IDs ending with `-playlist` to `-queue`.
    - When migrating to the auth-required schema, moves relevant options into entry data and clears options.
    - Updates the config entry's data, options, minor_version, title (from CONF_EMAIL or "Oasis Control"), unique_id, and version.

    Parameters:
        entry: The config entry to migrate.

    Returns:
        `True` if migration succeeded, `False` if migration could not be performed (e.g., entry.version is greater than supported).
    """
    _LOGGER.debug(
        "Migrating configuration from version %s.%s", entry.version, entry.minor_version
    )

    if entry.version > 1:
        # This means the user has downgraded from a future version
        return False

    if entry.version == 1:
        new_data = {**entry.data}
        new_options = {**entry.options}

        if entry.minor_version < 2:
            # Need to update previous playlist select entity to queue
            @callback
            def migrate_unique_id(
                entity_entry: er.RegistryEntry,
            ) -> dict[str, Any] | None:
                """
                Update a registry entry's unique_id suffix from "-playlist" to "-queue" when applicable.

                Parameters:
                    entity_entry (er.RegistryEntry): Registry entry to inspect.

                Returns:
                    dict[str, Any] | None: A mapping {"new_unique_id": <new id>} if the entry is in the "select" domain and its unique_id ends with "-playlist"; otherwise `None`.
                """
                if entity_entry.domain == "select" and entity_entry.unique_id.endswith(
                    "-playlist"
                ):
                    unique_id = entity_entry.unique_id.replace("-playlist", "-queue")
                    return {"new_unique_id": unique_id}
                return None

            await er.async_migrate_entries(hass, entry.entry_id, migrate_unique_id)

        if entry.minor_version < 3:
            # Auth is now required, host is dropped
            new_data = {**entry.options}
            new_options = {}

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            options=new_options,
            minor_version=3,
            title=new_data.get(CONF_EMAIL, "Oasis Control"),
            unique_id=None,
            version=1,
        )

    _LOGGER.debug(
        "Migration to configuration version %s.%s successful",
        entry.version,
        entry.minor_version,
    )

    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: OasisDeviceConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """
    Determine whether the config entry is no longer associated with the given device.

    Parameters:
        config_entry (OasisDeviceConfigEntry): The config entry whose runtime data contains device serial numbers.
        device_entry (DeviceEntry): The device registry entry to check for matching identifiers.

    Returns:
        bool: `true` if none of the device's identifiers match serial numbers present in the config entry's runtime data, `false` otherwise.
    """
    current_serials = {d.serial_number for d in (config_entry.runtime_data.data or [])}
    return not any(
        identifier
        for identifier in device_entry.identifiers
        if identifier[0] == DOMAIN and identifier[1] in current_serials
    )
