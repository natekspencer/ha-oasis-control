"""Oasis device light entity."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityDescription,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.color import (
    brightness_to_value,
    color_rgb_to_hex,
    rgb_hex_to_rgb_list,
    value_to_brightness,
)

from . import OasisDeviceConfigEntry, setup_platform_from_coordinator
from .entity import OasisDeviceEntity
from .pyoasiscontrol import OasisDevice
from .pyoasiscontrol.const import LED_EFFECTS


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: OasisDeviceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oasis device lights using config entry."""

    def make_entities(new_devices: list[OasisDevice]):
        """
        Create OasisDeviceLightEntity instances for each provided Oasis device.

        Parameters:
            new_devices (list[OasisDevice]): Devices to wrap as light entities.

        Returns:
            list[OasisDeviceLightEntity]: A list of light entity instances corresponding to the input devices.
        """
        return [
            OasisDeviceLightEntity(entry.runtime_data, device, DESCRIPTOR)
            for device in new_devices
        ]

    setup_platform_from_coordinator(entry, async_add_entities, make_entities)


DESCRIPTOR = LightEntityDescription(key="led", translation_key="led")


class OasisDeviceLightEntity(OasisDeviceEntity, LightEntity):
    """Oasis device light entity."""

    _attr_supported_features = LightEntityFeature.EFFECT

    @property
    def brightness(self) -> int:
        """
        Get the light's brightness on a 0-255 scale.

        Returns:
            int: Brightness value between 0 and 255.
        """
        scale = (1, self.device.brightness_max)
        return value_to_brightness(scale, self.device.brightness)

    @property
    def color_mode(self) -> ColorMode:
        """Return the color mode of the light."""
        if self.effect in (
            "Rainbow",
            "Glitter",
            "Confetti",
            "BPM",
            "Juggle",
        ):
            return ColorMode.BRIGHTNESS
        return ColorMode.RGB

    @property
    def effect(self) -> str:
        """Return the current effect."""
        return LED_EFFECTS.get(self.device.led_effect)

    @property
    def effect_list(self) -> list[str]:
        """Return the list of supported effects."""
        return list(LED_EFFECTS.values())

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return self.device.brightness > 0

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the rgb color value [int, int, int]."""
        if not self.device.color:
            return None
        return rgb_hex_to_rgb_list(self.device.color.replace("#", ""))

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Flag supported color modes."""
        return {ColorMode.RGB}

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self.device.async_set_led(brightness=0)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """
        Turn the light on and set its LED state.

        Processes optional keyword arguments to compute the device-specific LED
        parameters, then updates the device's LEDs with the resulting brightness, color,
        and effect.

        Parameters:
            kwargs: Optional control parameters recognized by the method:
                ATTR_BRIGHTNESS (int): Brightness in the 0-255 Home Assistant scale. When provided,
                    it is converted and rounded up to the device's brightness scale (1..device.brightness_max).
                    When omitted, uses self.device.brightness_on (last non-zero brightness).
                ATTR_RGB_COLOR (tuple[int, int, int]): RGB tuple (R, G, B). When provided, it is
                    converted to a hex color string prefixed with '#'.
                ATTR_EFFECT (str): Human-readable effect name. When provided, it is mapped to the
                    device's internal effect key; if no mapping exists, `None` is used.

        Side effects:
            Updates the underlying device LED state with the computed `brightness`, `color`, and `led_effect`.
        """
        if brightness := kwargs.get(ATTR_BRIGHTNESS):
            scale = (1, self.device.brightness_max)
            brightness = math.ceil(brightness_to_value(scale, brightness))
        else:
            brightness = self.device.brightness_on

        color = None
        if rgb := kwargs.get(ATTR_RGB_COLOR):
            color = f"#{color_rgb_to_hex(*rgb)}"

        led_effect = None
        if effect := kwargs.get(ATTR_EFFECT):
            led_effect = next((k for k, v in LED_EFFECTS.items() if v == effect), None)

        await self.device.async_set_led(
            brightness=brightness, color=color, led_effect=led_effect
        )
