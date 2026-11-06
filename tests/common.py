"""Test helpers from whichever Home Assistant test package is installed."""

try:
    from pytest_homeassistant.common import (
        MockConfigEntry,
        mock_restore_cache_with_extra_data,
    )
except ImportError:  # Home Assistant before 2026.11
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        mock_restore_cache_with_extra_data,
    )

__all__ = ["MockConfigEntry", "mock_restore_cache_with_extra_data"]
