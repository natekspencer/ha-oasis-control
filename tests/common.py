"""Test helpers from whichever Home Assistant test package is installed."""

try:
    from pytest_homeassistant.common import (
        MockConfigEntry,
        async_fire_time_changed,
        mock_restore_cache_with_extra_data,
    )
    from pytest_homeassistant.test_util.aiohttp import (
        AiohttpClientMocker,
        AiohttpClientMockResponse,
    )
except ImportError:  # Home Assistant before 2026.11
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_fire_time_changed,
        mock_restore_cache_with_extra_data,
    )
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
        AiohttpClientMockResponse,
    )

__all__ = [
    "AiohttpClientMockResponse",
    "AiohttpClientMocker",
    "MockConfigEntry",
    "async_fire_time_changed",
    "mock_restore_cache_with_extra_data",
]
