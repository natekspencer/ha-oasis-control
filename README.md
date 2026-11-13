<!-- BEGIN AUTO-GENERATED HEADER -->

[![Release](https://img.shields.io/github/v/release/natekspencer/ha-oasis-control?style=for-the-badge)](https://github.com/natekspencer/ha-oasis-control/releases)
[![HACS Badge](https://img.shields.io/badge/HACS-default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![Buy Me A Coffee/Beer](https://img.shields.io/badge/Buy_Me_A_☕/🍺-F16061?style=for-the-badge&logo=ko-fi&logoColor=white&labelColor=grey)](https://ko-fi.com/natekspencer)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor_💜-6f42c1?style=for-the-badge&logo=github&logoColor=white&labelColor=grey)](https://github.com/sponsors/natekspencer)

![Downloads](https://img.shields.io/github/downloads/natekspencer/ha-oasis-control/total?style=flat-square)
![Latest Downloads](https://img.shields.io/github/downloads/natekspencer/ha-oasis-control/latest/total?style=flat-square)

<!-- END AUTO-GENERATED HEADER -->

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="custom_components/oasis_mini/brand/dark_logo.png">
  <img alt="Oasis Mini logo" src="custom_components/oasis_mini/brand/logo.png">
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

## 📡 Connecting to your Oasis

The integration talks to each Oasis over the best connection available, in this order:

1. **Local network (HTTP)** – fastest and doesn't depend on the cloud. Requires HTTP mode (see below).
2. **Bluetooth** – used when HTTP isn't available and the table is in range of Home Assistant's Bluetooth (on by default).
3. **Cloud (MQTT)** – the last resort; requires **Remote access** to be active on the device.

It switches automatically as connections come and go. The **Connection type** diagnostic sensor on each device shows which one is in use (`Local network`, `Bluetooth`, `Cloud` or `Disconnected`).

The **Connection** diagnostic sensor shows whether the table is reachable and has answered recently. It turns to `Disconnected` as soon as no connection works (when **Connection type** shows `Disconnected`) or when nothing has been heard from the table for 3 minutes, and only changes state when that happens, so it suits alerts and automations.

If the Oasis cloud is down, tables that were set up before keep working over the local network and Bluetooth, including after a Home Assistant restart. Track images and names that come from the cloud may be missing until it's back.

### Enabling HTTP mode

Newer firmware turns off the table's local HTTP interface by default. To turn it on:

1. Make sure your phone has Bluetooth on and is near the table.
2. Open the **Oasis Control** app and go to your device's settings page.
3. Tap the **serial number** 10 times. The app counts down the last few taps, then enables HTTP mode.

Home Assistant finds the table's IP address (from the Oasis cloud, the table itself, or DHCP discovery) and picks up the local connection within a minute. No restart of Home Assistant is needed.

> ⚠️ **HTTP mode turns off whenever the table restarts** (a power cut, a reboot or a firmware update). Turn it back on in the app as above, or press the table's **Enable HTTP mode** button in Home Assistant (under the device's configuration entities). The button is available while Home Assistant is connected to the table over [Bluetooth](#bluetooth), and switches to the local connection right away.

> 💡 Give the table a DHCP reservation on your router so its IP address doesn't change.

### Bluetooth

When the Home Assistant [Bluetooth integration](https://www.home-assistant.io/integrations/bluetooth/) is set up (a Bluetooth proxy works), Home Assistant connects to tables in range over Bluetooth. Over Bluetooth the table pushes playback updates instantly, and Home Assistant asks it for its other settings every 10 seconds.

The table accepts a single Bluetooth connection, so while Home Assistant holds it the Oasis Control app can't connect over Bluetooth. To free it for the app, turn off the table's **Connect over Bluetooth** switch (under the device's configuration entities). This only stops Home Assistant connecting; the table's own Bluetooth stays on. Turning it back on reconnects; neither requires a restart.

### Remote access inactive

If the Oasis Control app shows **Remote access inactive**, the table isn't connected to the Oasis cloud and the cloud connection won't receive any updates. Use HTTP mode or Bluetooth instead, or reconnect the table to the cloud by tapping that message in the app and setting up its Wi‑Fi again.

The **Remote access** diagnostic sensor (disabled by default) shows the same state in Home Assistant: `Active`, `Inactive` or `Disabled`. It's read over Bluetooth, or shown as `Active` while the table is reporting through the cloud, and is unknown otherwise.

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
