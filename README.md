# Marstek Home Assistant Integration


The Marstek integration is an official integration component for Home Assistant provided by Marstek, which can be used to monitor and control Marstek devices.

## System Requirements

> Home Assistant version requirements:
>
> - Core version: ^2025.10.0
> - HAOS version: ^15.0
>
> Marstek devices and Home Assistant must be on the same local network
>
> Marstek devices must have OPEN API enabled
>
> **⚠️ Important**: This integration is currently not compatible with Venus E2.0 devices. Using this integration with Venus E2.0 may cause disconnection between the device and CT003.

## Quickstart (no existing repo)

If you have not cloned the repository before, follow these steps directly:

```bash
# 1) Clone the repo (marstek-integration branch)
git clone https://github.com/home-assistant/core.git

cd core

# 2) Create and activate venv (Python 3.13)
python3.13 -m venv venv

source venv/bin/activate    # Windows: venv\Scripts\activate

# 3) Install dependencies
pip install -r requirements.txt -r requirements_test.txt

pip install homeassistant

# 4) Run Home Assistant (uses ./config as your config directory)
mkir config

hass -c config
```

## Installation

### Method 1: Manual Installation (Recommended)

1. **Clone the repository and switch to the marstek-dev branch:**

```bash
git clone https://github.com/MarstekEnergy/ha_marstek.git

cd ha_marstek

git checkout marstek-dev
```

2. **Copy the marstek folder to your Home Assistant components directory:**

```bash
# If using Home Assistant Core (Python virtual environment)
cp -r ./custom_components/marstek /path/to/homeassistant/config/custom_components/marstek

```


## Important Notes

- **Branch**: Make sure you're on the `marstek-dev` branch to get the latest development version.
- **Directory Structure**: The `marstek` folder should be placed directly in the `components` directory, not in a subdirectory.
- **Permissions**: Ensure the files have proper read permissions for the Home Assistant process.

## After Installation

1. Restart Home Assistant
2. Go to **Settings** → **Devices & Services**
3. Click **Add Integration**
4. Search for "Marstek"
5. Follow the configuration flow

## Directory Structure

After installation, your Home Assistant components directory should look like:

```
homeassistant/components/
├── marstek/
│   ├── __init__.py
│   ├── config_flow.py
│   ├── const.py
│   ├── coordinator.py
│   ├── device_action.py
│   ├── manifest.json
│   ├── quality_scale.yaml
│   ├── scanner.py
│   ├── sensor.py
│   ├── strings.json
│   └── translations/
│       └── en.json
└── ... (other components)
```

## Updating the Integration

To update to the latest version:

```bash
# If you kept the cloned repository
cd /path/to/ha_marstek

git pull origin marstek-dev

# Copy the updated files
cp -r ./custom_components/marstek /path/to/homeassistant/config/custom_components/marstek


```



## Frequently Asked Questions

1. **Which devices are supported?**

   Supports Venus A, Venus D, Venus E 3.0 with new firmware versions, as well as other Marstek devices that support OPEN API communication.
   
   **Note**: This integration is currently not compatible with Venus E2.0 devices. Using this integration with Venus E2.0 may cause disconnection between the device and CT003.

2. **Why can't I find my device?**

   - OPEN API is not enabled on the device
   - Ensure Marstek devices and Home Assistant are on the same network segment, and port 30000 is open
   - The integration searches for devices via UDP broadcast. Network fluctuations may affect communication between devices and HA. It is recommended to retry

3. **What is OPEN API?**

   OPEN API is a communication interface provided by Marstek device firmware for querying device status and controlling some commands in a local network environment.