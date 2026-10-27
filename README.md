# Marstek for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A [Home Assistant](https://www.home-assistant.io) integration for [Marstek Energy](https://de.marstekenergy.com) devices (Venus series), communicating locally over the [Marstek Device Open API](docs/Marstek_Device_Open_API_EN_Rev3.1.2.pdf).

## Supported devices

- Venus E / Venus E 3.0 (VNSE3-0)
- Venus A (VNSA-0)
- Venus D (VNSD-0)

## Features

### Monitoring (sensors)

- Battery level, power, charge/discharge status and remaining/rated capacity
- Device operating mode (Auto / AI / Manual / Passive / UPS)
- Grid input/output power, off-grid power, total power and CT phase powers (A/B/C)
- Battery temperature
- Charge and discharge permission flags; device connectivity status
- WiFi signal strength, SSID and IP address (diagnostic, disabled by default)
- Per-input PV power, voltage, current and state (Venus A/D)
- Lifetime energy counters (PV, grid input/output, CT input/output, load)

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
- Factory reset buttons: one clears accumulated data, one preserves it

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
| `marstek.set_manual_schedule` | Replace the device's Manual-mode time schedule |
| `marstek.factory_reset` | Reset the device to factory settings (optionally keep accumulated data) |

Most actions are also available as regular entities (select, number, switch,
button) so they can be used in automations and dashboards.

### Manual mode

Switching to Manual mode from HA only tells the device to enter Manual mode;
the actual time slots are configured in the **Marstek mobile app**. Picking
Manual in the operating-mode select shows a reminder to set up the slots in
the app, and there are no per-slot entities on the HA device page.

> **Experimental — writing the schedule from HA.** The integration also
> exposes services that write the Manual schedule directly, but the
> [Open API document (Rev3.1.2)](docs/Marstek_Device_Open_API_EN_Rev3.1.2.pdf)
> shows a single slot as an *object* while the firmware's accepted shape
> (object vs array) has not been confirmed on a live device. Prefer the app,
> and treat these services as experimental until verified.

- `marstek.set_manual_schedule` — replace the whole schedule with a friendly
  list of slots (`start_time`, `end_time`, `days` as `mon`..`sun`, `power`;
  optional `enable`, `manual_set`, `time_num` which defaults to the slot's
  position).
- `marstek.set_mode` with `mode: manual` and `config.manual_cfg` — send the
  raw Open API schedule; each slot needs `time_num`, `start_time`,
  `end_time`, `week_set` (bitmask, bit0 = Monday), `power` and `enable`.

```yaml
action: marstek.set_manual_schedule
target:
  entity_id: select.marstek_operating_mode
data:
  slots:
    - start_time: "00:00"
      end_time: "06:00"
      days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
      power: 100
    - start_time: "18:00"
      end_time: "23:00"
      days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
      power: 250
```

> `set_mode` only updates the slots you provide and does **not** clear other
> slots already programmed on the device; `set_manual_schedule` replaces the
> whole schedule. Manual mode supports up to 10 time slots.

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

## Acknowledgements

We would like to thank [@DominusEcho](https://github.com/DominusEcho), [@uid0c0der](https://github.com/uid0c0der) and [@craimasjien](https://github.com/craimasjien) for their contributions to this integration.

## License

MIT — see [LICENSE](LICENSE).
