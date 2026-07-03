"""Services for advanced Marstek control (Passive + Manual schedules).

Modeled after jaapp/ha-marstek-local-api for sustainable, full-featured control.
Replaces fragile hard-coded power=0 in mode switching.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import time as dt_time
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from typing import TYPE_CHECKING

from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

if TYPE_CHECKING:
    from . import MarstekConfigEntry

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_PASSIVE_MODE = "set_passive_mode"
SERVICE_SET_MANUAL_SCHEDULE = "set_manual_schedule"
SERVICE_SET_MANUAL_SCHEDULES = "set_manual_schedules"
SERVICE_CLEAR_MANUAL_SCHEDULES = "clear_manual_schedules"

SERVICE_SET_PASSIVE_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("power"): vol.All(vol.Coerce(int), vol.Range(min=-10000, max=10000)),
        vol.Required("duration"): vol.All(vol.Coerce(int), vol.Range(min=1, max=86400)),
    }
)

SERVICE_SET_MANUAL_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("time_num"): vol.All(vol.Coerce(int), vol.Range(min=0, max=9)),
        vol.Required("start_time"): cv.time,
        vol.Required("end_time"): cv.time,
        vol.Optional("days", default=["mon", "tue", "wed", "thu", "fri", "sat", "sun"]): vol.All(
            cv.ensure_list, [vol.In(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])]
        ),
        vol.Optional("power", default=0): vol.Coerce(int),
        vol.Optional("enabled", default=True): cv.boolean,
    }
)

SERVICE_SET_MANUAL_SCHEDULES_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("schedules"): [
            vol.Schema(
                {
                    vol.Required("time_num"): vol.All(vol.Coerce(int), vol.Range(min=0, max=9)),
                    vol.Required("start_time"): cv.time,
                    vol.Required("end_time"): cv.time,
                    vol.Optional("days", default=["mon", "tue", "wed", "thu", "fri", "sat", "sun"]): vol.All(
                        cv.ensure_list, [vol.In(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])]
                    ),
                    vol.Optional("power", default=0): vol.Coerce(int),
                    vol.Optional("enabled", default=True): cv.boolean,
                }
            )
        ],
    }
)

SERVICE_CLEAR_MANUAL_SCHEDULES_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
    }
)


def _get_coordinator_for_device(hass: HomeAssistant, device_id: str) -> tuple[MarstekDataUpdateCoordinator, str]:
    """Resolve coordinator and host from a device_id."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if not device:
        raise HomeAssistantError(f"Device {device_id} not found")

    for entry_id in device.config_entries:
        entry: MarstekConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and entry.runtime_data:
            host = entry.data.get("host") or entry.data.get("ip", "")
            return entry.runtime_data.coordinator, host

    raise HomeAssistantError(f"No Marstek coordinator found for device {device_id}")


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register Marstek services."""

    async def _set_passive_mode(call: ServiceCall) -> None:
        device_id = call.data["device_id"]
        power = call.data["power"]
        duration = call.data["duration"]

        coordinator, host = _get_coordinator_for_device(hass, device_id)
        if not host:
            raise HomeAssistantError("Could not determine device host")

        _LOGGER.info("Setting Passive mode power=%s duration=%s for %s", power, duration, host)

        from pymarstek import build_command

        config = {
            "mode": "Passive",
            "passive_cfg": {"power": power, "cd_time": duration},
        }
        command = build_command("ES.SetMode", {"id": 0, "config": config})

        await coordinator.udp_client.pause_polling(host)
        try:
            response = await coordinator.udp_client.send_request(
                command, host, 30000, timeout=5.0, quiet_on_timeout=True
            )
            result = response.get("result", {}) if isinstance(response, dict) else {}
            if result.get("set_result") is False:
                raise HomeAssistantError("Device rejected passive mode")
            # Optimistic
            if coordinator.data:
                updated = dict(coordinator.data)
                updated["device_mode"] = "Passive"
                coordinator.async_set_updated_data(updated)
        finally:
            await coordinator.udp_client.resume_polling(host)

        await coordinator.async_request_refresh()

    async def _set_manual_schedule(call: ServiceCall) -> None:
        device_id = call.data["device_id"]
        time_num = call.data["time_num"]
        start_time: dt_time = call.data["start_time"]
        end_time: dt_time = call.data["end_time"]
        days = call.data["days"]
        power = call.data["power"]
        enabled = call.data["enabled"]

        coordinator, host = _get_coordinator_for_device(hass, device_id)

        week_set = sum({"mon": 1, "tue": 2, "wed": 4, "thu": 8, "fri": 16, "sat": 32, "sun": 64}[d] for d in days)

        manual_cfg = {
            "time_num": time_num,
            "start_time": start_time.strftime("%H:%M"),
            "end_time": end_time.strftime("%H:%M"),
            "week_set": week_set,
            "power": power,
            "enable": 1 if enabled else 0,
        }

        _LOGGER.info("Setting manual schedule slot %s for %s: %s", time_num, host, manual_cfg)

        from pymarstek import build_command

        config = {"mode": "Manual", "manual_cfg": manual_cfg}
        command = build_command("ES.SetMode", {"id": 0, "config": config})

        await coordinator.udp_client.pause_polling(host)
        try:
            response = await coordinator.udp_client.send_request(
                command, host, 30000, timeout=6.0, quiet_on_timeout=True
            )
            result = response.get("result", {}) if isinstance(response, dict) else {}
            if result.get("set_result") is False:
                raise HomeAssistantError(f"Device rejected manual schedule slot {time_num}")

            if coordinator.data:
                updated = dict(coordinator.data)
                updated["device_mode"] = "Manual"
                coordinator.async_set_updated_data(updated)
        finally:
            await coordinator.udp_client.resume_polling(host)

        await coordinator.async_request_refresh()

    async def _set_manual_schedules(call: ServiceCall) -> None:
        device_id = call.data["device_id"]
        schedules = call.data["schedules"]
        coordinator, host = _get_coordinator_for_device(hass, device_id)

        from pymarstek import build_command

        any_success = False
        await coordinator.udp_client.pause_polling(host)
        try:
            for sched in schedules:
                time_num = sched["time_num"]
                start_time: dt_time = sched["start_time"]
                end_time: dt_time = sched["end_time"]
                days = sched.get("days", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
                power = sched.get("power", 0)
                enabled = sched.get("enabled", True)

                week_set = sum({"mon": 1, "tue": 2, "wed": 4, "thu": 8, "fri": 16, "sat": 32, "sun": 64}[d] for d in days)
                manual_cfg = {
                    "time_num": time_num,
                    "start_time": start_time.strftime("%H:%M"),
                    "end_time": end_time.strftime("%H:%M"),
                    "week_set": week_set,
                    "power": power,
                    "enable": 1 if enabled else 0,
                }
                config = {"mode": "Manual", "manual_cfg": manual_cfg}
                command = build_command("ES.SetMode", {"id": 0, "config": config})

                response = await coordinator.udp_client.send_request(
                    command, host, 30000, timeout=6.0, quiet_on_timeout=True
                )
                result = response.get("result", {}) if isinstance(response, dict) else {}
                if result.get("set_result") is not False:
                    any_success = True
                await asyncio.sleep(0.4)

            if any_success and coordinator.data:
                updated = dict(coordinator.data)
                updated["device_mode"] = "Manual"
                coordinator.async_set_updated_data(updated)
        finally:
            await coordinator.udp_client.resume_polling(host)

        if any_success:
            await coordinator.async_request_refresh()

    async def _clear_manual_schedules(call: ServiceCall) -> None:
        device_id = call.data["device_id"]
        coordinator, host = _get_coordinator_for_device(hass, device_id)

        from pymarstek import build_command

        # Clear by setting all slots disabled (or just slot 9 neutral as common pattern)
        await coordinator.udp_client.pause_polling(host)
        try:
            for slot in range(10):
                manual_cfg = {
                    "time_num": slot,
                    "start_time": "00:00",
                    "end_time": "00:00",
                    "week_set": 0,
                    "power": 0,
                    "enable": 0,
                }
                config = {"mode": "Manual", "manual_cfg": manual_cfg}
                command = build_command("ES.SetMode", {"id": 0, "config": config})
                await coordinator.udp_client.send_request(
                    command, host, 30000, timeout=4.0, quiet_on_timeout=True
                )
                await asyncio.sleep(0.3)

            if coordinator.data:
                updated = dict(coordinator.data)
                updated["device_mode"] = "Manual"
                coordinator.async_set_updated_data(updated)
        finally:
            await coordinator.udp_client.resume_polling(host)

        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_SET_PASSIVE_MODE, _set_passive_mode, schema=SERVICE_SET_PASSIVE_MODE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_MANUAL_SCHEDULE, _set_manual_schedule, schema=SERVICE_SET_MANUAL_SCHEDULE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_MANUAL_SCHEDULES, _set_manual_schedules, schema=SERVICE_SET_MANUAL_SCHEDULES_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_MANUAL_SCHEDULES, _clear_manual_schedules, schema=SERVICE_CLEAR_MANUAL_SCHEDULES_SCHEMA
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload services."""
    for service in (
        SERVICE_SET_PASSIVE_MODE,
        SERVICE_SET_MANUAL_SCHEDULE,
        SERVICE_SET_MANUAL_SCHEDULES,
        SERVICE_CLEAR_MANUAL_SCHEDULES,
    ):
        hass.services.async_remove(DOMAIN, service)
