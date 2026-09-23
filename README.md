# Marstek Home Assistant Integration

The Marstek integration is an official Home Assistant integration from Marstek. It communicates with supported Marstek energy storage devices locally over UDP and exposes their status as Home Assistant sensors.

## Requirements

- Home Assistant Core 2025.10.0 or newer.
- Home Assistant OS 15.0 or newer when running Home Assistant OS.
- Home Assistant and the Marstek device must be on the same local network.
- Open API must be enabled on the Marstek device.
- UDP port `30000` must be available between Home Assistant and the device. Discovery uses a local-network UDP broadcast.

> **Important:** Venus E2.0 is not supported. Do not use this integration with Venus E2.0, as it may disconnect the device from CT003.

## Supported Devices

The integration currently supports these device types. A device may report either its model name or one of the protocol identifiers below:

| Device | Reported device type |
| --- | --- |
| Venus A | `VNSA-0`, `VenusA`, `Venus A` |
| Venus D | `VNSD-0`, `VenusD`, `Venus D` |
| Venus E 3.0 | `VNSE3-0`, `VenusE 3.0`, `Venus E 3.0` |

Support depends on the device firmware exposing the Marstek Open API. Other device types are rejected during setup until they are explicitly supported.

## Available Entities

After setup, the integration creates sensors for:

- Battery level, power, and status.
- Device operating mode (`Auto`, `AI`, `Manual`, `Passive`, or `UPS`).
- Each of the four PV inputs: power, voltage, current, and state (`Standby` or `Working`).

The device is polled locally every 30 seconds. No cloud account or external service is required.

## Installation

This repository is intended for manual installation as a Home Assistant custom integration.

1. Clone the repository and switch to the `main` branch:

   ```bash
   git clone https://github.com/MarstekEnergy/ha_marstek.git
   cd ha_marstek
   git checkout main
   ```

2. Copy the integration into Home Assistant's `custom_components` directory. Replace `/path/to/homeassistant/config` with your Home Assistant configuration directory:

   ```bash
   mkdir -p /path/to/homeassistant/config/custom_components
   cp -r custom_components/marstek /path/to/homeassistant/config/custom_components/marstek
   ```

3. Restart Home Assistant.

4. Open **Settings** > **Devices & services**, select **Add integration**, search for **Marstek**, and choose one of the following setup methods:

   - **Search for devices on the local network** to use UDP discovery.
   - **Enter device IP address** for manual setup.

The Marstek device must be powered on, reachable from Home Assistant, and have Open API enabled before setup.

## Updating

Pull the latest version and copy the integration over the installed version:

```bash
cd /path/to/ha_marstek
git pull origin main
cp -r custom_components/marstek /path/to/homeassistant/config/custom_components/marstek
```

Restart Home Assistant after updating.

## Troubleshooting

If no device is discovered:

- Confirm that Open API is enabled on the device.
- Confirm that Home Assistant and the device are on the same network segment.
- Allow UDP traffic on port `30000` in the network and host firewalls.
- Try manual setup with the device's IP address.
- Check the Home Assistant log for connection or unsupported-device errors.

If the device is detected but setup fails, verify that its reported device type is listed above and that the device firmware supports the Open API used by this integration.
