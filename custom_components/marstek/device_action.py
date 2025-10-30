"""Device actions for Marstek.

Expose actions in UI: charge / discharge / stop.

"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .command_builder import CMD_ES_SET_MODE, build_command, get_es_mode
from .const import DEFAULT_UDP_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

#action types
ACTION_CHARGE = "charge"
ACTION_DISCHARGE = "discharge"
ACTION_STOP = "stop"


# Device action configuration validation schema
ACTION_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_DOMAIN): vol.In((DOMAIN,)),
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_TYPE): vol.In((ACTION_CHARGE, ACTION_DISCHARGE, ACTION_STOP)),
        # Frontend sometimes carries entity_id (even though device actions don't need it), make optional for validation
        vol.Optional("entity_id"): cv.entity_id,
    }
)


async def async_get_actions(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """List device actions for a Marstek device."""
    actions: list[dict[str, Any]] = []

    # Only expose actions for this integration
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if not device:
        return actions

    if not any(ident[0] == DOMAIN for ident in device.identifiers):
        return actions

    actions.extend(
        {
            "domain": DOMAIN,
            "type": action,
            "device_id": device_id,
        }
        for action in (ACTION_CHARGE, ACTION_DISCHARGE, ACTION_STOP)
    )

    return actions


async def _get_host_from_device(hass: HomeAssistant, device_id: str) -> str | None:
    """Resolve device IP(host) via device registry and config entries.

    Identifiers are (DOMAIN, ip), so read IP directly; fallback to config entry host.
    """
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if not device:
        return None

    # Prefer IP from identifiers
    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            return identifier

    # Fallback: find associated config entry
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN:
            return entry.data.get("host")

    return None


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: dict[str, Any],
    context: Any,
) -> None:
    """Execute a device action."""
    action_type: str = config.get("type")
    device_id: str = config.get("device_id")

    host = await _get_host_from_device(hass, device_id)
    if not host:
        return

    # Charge/Discharge/Stop: 00:00-23:59, week_set=127
    if action_type == ACTION_CHARGE:
        power = -1300
        enable = 1
    elif action_type == ACTION_DISCHARGE:
        power = 800
        enable = 1
    elif action_type == ACTION_STOP:
        power = 0
        enable = 0
    else:
        return

    payload = {
        "id": 0,
        "config": {
            "mode": "Manual",
            "manual_cfg": {
                "time_num": 0,
                "start_time": "00:00",
                "end_time": "23:59",
                "week_set": 127,
                "power": power,
                "enable": enable,
            },
        },
    }
    command = build_command(CMD_ES_SET_MODE, payload)

    # 发送并验证：指数退避+抖动重试，且通过 ES.GetMode 验证目标状态
    udp = hass.data.get(DOMAIN, {}).get("udp_client")
    if not udp:
        return

    # 尝试参数
    attempts: list[tuple[float, float]] = [
        (2.4, 0.4),
        (3.2, 0.6),
        (4.0, 0.8),
        (5.0, 1.0),
        (6.0, 1.2),
        (7.0, 1.4),
        (8.0, 1.6),
        (9.0, 1.8),
    ]

    # 全过程暂停该设备轮询，避免竞态
    await udp.pause_polling(host)
    try:
        for idx, (timeout_s, backoff_base) in enumerate(attempts, start=1):
            try:
                await udp.send_request(
                    command, host, DEFAULT_UDP_PORT, timeout=timeout_s, quiet_on_timeout=True
                )
            except (TimeoutError, OSError, ValueError) as e:
                # 发送/等待失败也继续做状态验证；若设备已成功接收则可能已生效
                _LOGGER.debug("ES.SetMode attempt %d send error: %s", idx, e)

            # 验证：轮询 ES.GetMode，确认 enable/power 是否符合期望
            try:
                verify_ok = await _verify_es_mode(hass, host, enable, power)
            except (TimeoutError, OSError, ValueError) as ve:
                _LOGGER.debug("Verify ES.GetMode failed at attempt %d: %s", idx, ve)
                verify_ok = False

            if verify_ok:
                _LOGGER.info("ES.SetMode applied after attempt %d (device %s)", idx, host)
                return

            # 未验证通过则退避后重试
            if idx < len(attempts):
                # 指数退避 + 抖动
                jitter = 0.30 * idx
                delay = backoff_base * idx + (jitter)
                _LOGGER.warning(
                    "ES.SetMode not confirmed on attempt %d/%d (device %s), retry in %.2fs",
                    idx, len(attempts), host, delay,
                )
                await asyncio.sleep(delay)
        # 全部尝试后仍未确认
        raise TimeoutError(f"ES.SetMode not confirmed on device {host}")
    finally:
        await udp.resume_polling(host)


async def async_get_action_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, Any]:
    """List action capabilities.

    Note: Must return a voluptuous Schema, not a list.
    Return empty Schema to indicate no extra fields, avoid frontend conversion errors.
    """
    return {"extra_fields": vol.Schema({})}


async def _verify_es_mode(hass: HomeAssistant, host: str, enable: int, power: int) -> bool:
    """通过 ES.GetMode 验证目标状态是否生效.

    规则（经验性）：
    - 模式应为 Manual
    - enable=0 视为停止：允许有少量功率波动（|ongrid_power| < 50W）
    - enable=1 且 power<0 视为充电：ongrid_power 应为负
    - enable=1 且 power>0 视为放电：ongrid_power 应为正
    连续进行短轮询，任何一次命中即可视为成功。
    """
    udp = hass.data.get(DOMAIN, {}).get("udp_client")
    if not udp:
        return False

    attempts = 5
    for _ in range(attempts):
        cmd = get_es_mode(0)
        try:
            resp = await udp.send_request(
                cmd, host, DEFAULT_UDP_PORT, timeout=2.4, quiet_on_timeout=True
            )
        except (TimeoutError, OSError, ValueError):
            await asyncio.sleep(0.4)
            continue

        result = resp.get("result", {}) if isinstance(resp, dict) else {}
        mode = result.get("mode")
        ongrid_power = result.get("ongrid_power")

        if mode == "Manual" and isinstance(ongrid_power, (int, float)):
            if enable == 0:
                if abs(ongrid_power) < 50:
                    return True
            elif enable == 1 and power < 0:
                if ongrid_power < 0:
                    return True
            elif enable == 1 and power > 0:
                if ongrid_power > 0:
                    return True

        await asyncio.sleep(0.5)

    return False


