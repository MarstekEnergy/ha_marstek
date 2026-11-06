# Marstek for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A [Home Assistant](https://www.home-assistant.io) integration for [Marstek Energy](https://de.marstekenergy.com) devices (Venus series), communicating locally over the [Marstek Device Local API](<docs/Marstek Device Local API_EN (Rev3.1.2).pdf>).

## Supported devices

- Venus E 3.0 (VNSE3-0)
- Venus A (VNSA-0)
- Venus D (VNSD-0)


## Features

### Monitoring (sensors)

- SOC, Battery power, Battery charge/discharge status and remaining/rated capacity, Battery temperature
- Operating mode (Auto/ AI / Manual /  Passive / UPS)
- Grid power, off-grid power and CT phase powers (A/B/C)
- Charge and discharge permission flags; device connectivity status
- WiFi signal strength, SSID and IP address (diagnostic, disabled by default)
- Per-input PV power, voltage, current and state (Venus A/D)
- Lifetime energy counters (PV, grid charge/discharge, load)

### Control

- Switch the operating mode between Auto and UPS.
- Set the discharge depth (DOD), 30–88 %.
- Set the maximum grid charge/discharge power version.
- Bluetooth lock switch.
- Front-panel LED switch.

All communication stays on your local network; no cloud account is required.

## Prerequisites

Home Assistant **2026.10** or newer is required. This is the same minimum
`hacs.json` declares, so HACS refuses to install on an older version. The
integration imports `homeassistant.helpers.target.TargetSelection` (added in
2026.1) and the `binary_sensor` constant module (added in 2026.10); older
releases fail to load the integration at all.

1. Your Marstek device is connected to the same LAN as Home Assistant.
2. The **local API** feature is enabled in the Marstek mobile app.
   The default UDP port is `30000`; if you configured a custom port
   (the API recommends `49152`–`65535`), enter it during manual setup.

## Installation

### HACS (recommended)

1. Open HACS and search for **Marstek** under *Integrations*.
2. Install it and restart Home Assistant.

If **Marstek** does not show up in the HACS search yet (for example on an
older HACS data set), add this repository to HACS as a custom repository
(category: *Integration*), then install **Marstek** from there.

### Manual

Copy `custom_components/marstek` into the `custom_components`
directory of your Home Assistant configuration and restart.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Marstek** and select it.
3. Choose *Search for devices on the local network* to discover devices,
   or *Enter device IP address* if you prefer a static IP.
4. Select the discovered device and finish setup.

The device must expose a stable MAC address. A static IP (or DHCP
reservation) is recommended for long-term reliability.

## Services

Once a device is configured, the following services are available. Services
target entities and act on every device represented in the target, so you can
select multiple entities, areas or devices at once.

| Service | Description |
| --- | --- |
| `marstek.set_mode` | Switch the operating mode (Auto/UPS) |
| `marstek.set_dod` | Set the discharge depth (30–88) |
| `marstek.set_power_version` | Set the max charge/discharge power version |

Most actions are also available as regular entities (select, number, switch)
so they can be used in automations and dashboards.

## Security considerations

The Marstek local API is an unauthenticated local UDP protocol: a command packet
carries only `id`, `method` and `params` — no token, nonce or signature. Until
the device protocol or firmware documentation confirms an authentication
mechanism, assume **any host that can reach the device's UDP port can send it
control commands**, including mode changes and power limits.

- Do not port-forward the device's UDP port (default `30000`) to the internet,
  and do not expose it through a VPN or proxy reachable by untrusted clients.
- Keep devices on an isolated network segment (IoT VLAN/SSID) where only the
  Home Assistant host can reach the API port.
- The client binds `0.0.0.0:30000` to receive replies, so verify the Home
  Assistant host firewall accepts UDP on that port only from the device. The
  client checks that a unicast reply comes from the device it asked, but that
  protects *responses*, not the commands a device will accept.

## Debugging

Enable debug logging for the integration and the client library:

```yaml
logger:
  logs:
    custom_components.marstek: debug
    aiomarstek: debug
```

Debug logging is **not** redacted: it records the device IP and MAC, the setup
unique id, and the full UDP response payloads. Turn it off once troubleshooting
is finished, and remove IP, MAC and SSID values before sharing a log. Use
diagnostics (below) when you need to attach something to a bug report.

### Diagnostics

On the device page (**Settings → Devices & Services → *your Marstek device*
→ Download diagnostics**) you can download a snapshot of the device's config
entry, the cached device info and the last status the coordinator received.
Hardware and network identifiers (IP, MAC, SSID) are redacted automatically,
so the downloaded file is safe to attach to a bug report.

## Known limitations

- Enabling the local API in the Marstek app may disable some built-in app features to avoid command conflicts.
- The operating mode can only be set to Auto or UPS. AI, Manual schedules and Passive mode are configured in the Marstek app and are read-only here.

## Acknowledgements

We would like to thank [@DominusEcho](https://github.com/DominusEcho), [@uid0c0der](https://github.com/uid0c0der) and [@craimasjien](https://github.com/craimasjien) for their contributions to this integration.

## License

MIT — see [LICENSE](LICENSE).
