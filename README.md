# Marstek for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A [Home Assistant](https://www.home-assistant.io) integration for [Marstek](https://www.marstekenergy.com/) energy storage devices (Venus series), communicating locally over the [Marstek Device Open API](docs/Marstek_Device_Open_API_EN_Rev3.1.2.pdf).

## Supported devices

- Venus E / Venus E 3.0 (VNSE3-0)
- Venus A (VNSA-0)
- Venus D (VNSD-0)

> **Venus C** and **Venus E mini** are not yet supported. Their feature sets
> differ (the mini has no PV inputs and fewer manual time slots; the C series
> power versions are unconfirmed), so they are excluded until verified on real
> hardware. If your device reports one of these types it will be rejected
> during setup.

## Features

### Monitoring (sensors)

- Battery level, power and charge/discharge status
- Device operating mode (Auto / AI / Manual / Passive / UPS)
- Grid input/output power and CT phase powers (A/B/C)
- Battery temperature and remaining capacity
- WiFi signal strength
- Per-input PV power, voltage, current and state (Venus A/D)
- Lifetime energy counters (PV, grid input/output, load)

### Control

- Switch between operating modes. The select entity offers Auto, AI, Manual
  and UPS. Manual switches the device into Manual mode; configure the time
  slots in the Marstek app (HA shows a reminder). Passive (with power/countdown)
  is set via the `set_mode` service.
- Discharge depth (DOD) number entity (30–88 %)
- Max charge/discharge power version select entity, options narrowed to
  the versions documented for your device model
- Bluetooth advertising lock switch
- Front-panel LED switch
- Factory reset button

All communication stays on your local network; no cloud account is required.

## Prerequisites

1. Your Marstek device is connected to the same LAN as Home Assistant.
2. The **Open API** feature is enabled in the Marstek mobile app.
   The default UDP port is `30000`; if you configured a custom port
   (the API recommends `49152`–`65535`), enter it during manual setup.

## Installation

### HACS (recommended)

1. Add this repository to HACS as a custom repository
   (category: *Integration*).
2. Install **Marstek** from HACS.
3. Restart Home Assistant.

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
| `marstek.set_mode` | Switch the operating mode (Auto/AI/Manual/Passive/UPS) |
| `marstek.set_dod` | Set the discharge depth (30–88) |
| `marstek.set_power_version` | Set the max charge/discharge power version |
| `marstek.factory_reset` | Reset the device to factory settings |

Most actions are also available as regular entities (select, number, switch,
button) so they can be used in automations and dashboards.

### Manual mode time slots

> **Experimental.** The [Open API document (Rev3.1.2)](docs/Marstek_Device_Open_API_EN_Rev3.1.2.pdf) shows a Manual time slot
> as an *object*, while the firmware's accepted shape (object vs array) has not
> been confirmed on a live device. This integration accepts either form but
> treats Manual scheduling as experimental until verified.

The Open API takes the whole Manual schedule in a single `ES.SetMode` payload,
so there is no per-slot service. To set a Manual schedule, call
`marstek.set_mode` with `mode: manual` and at least one slot under
`config.manual_cfg`. Each slot has `time_num`, `start_time`, `end_time`,
`week_set` (bitmask, bit0 = Monday), `power` (watts) and `enable` (true/false).

```yaml
action: marstek.set_mode
target:
  entity_id: select.marstek_operating_mode
data:
  mode: manual
  config:
    manual_cfg:
      - time_num: 1
        start_time: "00:00"
        end_time: "06:00"
        week_set: 127
        power: 100
        enable: true
      - time_num: 2
        start_time: "18:00"
        end_time: "23:00"
        week_set: 127
        power: 250
        enable: true
```

> **Slots are updated, not replaced.** A Manual schedule write only sets the
> slots you provide (`time_num`, `enable`); it does **not** clear slots already
> programmed on the device for other `time_num` values, which keep running. To
> replace the whole schedule, explicitly set every slot you want.

## Security considerations

The Marstek Open API is an unauthenticated local UDP protocol: a command packet
carries only `id`, `method` and `params` — no token, nonce or signature. Until
the device protocol or firmware documentation confirms an authentication
mechanism, assume **any host that can reach the device's UDP port can send it
control commands**, including mode changes, power limits and factory reset.

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

- Enabling the Open API in the Marstek app may disable some built-in app features to avoid command conflicts.

- **Factory reset is never retried.** If the device does not answer, the button
  or service reports a failure even when the reset actually ran and the device
  was already rebooting. Treat a failed reset as "outcome unknown" and check the
  device before pressing again — a second attempt resets it again.

- Manual mode supports up to 10 time slots and is experimental (see above).

## License

MIT — see [LICENSE](LICENSE).
