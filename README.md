<!-- BEGIN AUTO-GENERATED HEADER -->

[![Release](https://img.shields.io/github/v/release/natekspencer/ha-oasis-control?style=for-the-badge)](https://github.com/natekspencer/ha-oasis-control/releases)
[![HACS Badge](https://img.shields.io/badge/HACS-default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![Buy Me A Coffee/Beer](https://img.shields.io/badge/Buy_Me_A_☕/🍺-F16061?style=for-the-badge&logo=ko-fi&logoColor=white&labelColor=grey)](https://ko-fi.com/natekspencer)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor_💜-6f42c1?style=for-the-badge&logo=github&logoColor=white&labelColor=grey)](https://github.com/sponsors/natekspencer)

![Downloads](https://img.shields.io/github/downloads/natekspencer/ha-oasis-control/total?style=flat-square)
![Latest Downloads](https://img.shields.io/github/downloads/natekspencer/ha-oasis-control/latest/total?style=flat-square)

<!-- END AUTO-GENERATED HEADER -->

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://brands.home-assistant.io/oasis_mini/dark_logo.png">
  <img alt="Oasis Mini logo" src="https://brands.home-assistant.io/oasis_mini/logo.png">
</picture>

# Oasis Control for Home Assistant

Home Assistant integration for Oasis kinetic sand art devices.

<!-- BEGIN AUTO-GENERATED INSTALLATION -->

## ⬇️ Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=natekspencer&repository=ha-oasis-control&category=integration)

This integration is available in the default [HACS](https://hacs.xyz/) repository.

1. Use the **My Home Assistant** badge above, or from within Home Assistant, click on **HACS**
2. Search for `Oasis Control` and click on the appropriate repository
3. Click **DOWNLOAD**
4. Restart Home Assistant

### Manual

If you prefer manual installation:

1. Download or clone this repository
2. Copy the `custom_components/oasis_mini` folder to your Home Assistant `custom_components` directory
3. Restart Home Assistant

> ⚠️ Manual installation will not provide automatic update notifications. HACS installation is recommended unless you have a specific need.

## ➕ Setup

Once installed, you can set up the integration by clicking on the following badge:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=oasis_mini)

Alternatively:

1. Go to [Settings > Devices & services](https://my.home-assistant.io/redirect/integrations/)
2. In the bottom-right corner, select **Add integration**
3. Type `Oasis Control` and select the **Oasis Control** integration
4. Follow the instructions to add the integration to your Home Assistant
<!-- END AUTO-GENERATED INSTALLATION -->

## 🎬 Actions

The media player entity supports various actions, including managing the playlist queue. You can specify a track by its ID or name. If using a track name, it must match an entry in the [tracks list](custom_components/oasis_mini/pyoasiscontrol/tracks.json). To specify multiple tracks, separate them with commas. An example is below:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.oasis_mini
data:
  media_content_id: 63, Turtle
  media_content_type: track
  enqueue: replace
```

---

<!-- BEGIN AUTO-GENERATED FOOTER -->

## ❤️ Support Me

I maintain this Home Assistant integration in my spare time. If you find it useful, consider supporting development:

- 💜 [Sponsor me on GitHub](https://github.com/sponsors/natekspencer)
- ☕ [Buy me a coffee / beer](https://ko-fi.com/natekspencer)
- 💸 [PayPal (direct support)](https://www.paypal.com/paypalme/natekspencer)
- ⭐ [Star this project](https://github.com/natekspencer/ha-oasis-control)
- 📦 If you’d like to support in other ways, such as donating hardware for testing, feel free to [reach out to me](https://github.com/natekspencer)

## 📈 Star History

[![Star History Chart](https://api.star-history.com/chart?repos=natekspencer/ha-oasis-control&type=date&legend=top-left&sealed_token=n9TbiL627kWZF3nZ9mlqtnBMAEJG1cY4JTLrbad6LVY3S7UoBAgjPPro4yMx5DfBlVx6elYEiYr7u2IV17EjJiwp2_STvx3ULL5Tf59qNJHtamOyqZaF6jGTtLHhLHqhTMDKF1Ubq86nb2IaZrTiiK5qVNdMwtfYbJbQMPJUFVVVNwogZ3Ch2CqFtFXI)](https://www.star-history.com/?repos=natekspencer%2Fha-oasis-control)

<!-- END AUTO-GENERATED FOOTER -->
