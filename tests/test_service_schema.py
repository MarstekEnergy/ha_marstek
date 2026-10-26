"""Behavioral tests for the service schemas and mode-config builder.

These tests cover the pure logic that does not depend on a running Home
Assistant instance: schema validation and the translation from a service
payload to an aiomarstek MarstekModeConfig. service_schema.py is loaded
directly from its file so the package __init__ (which imports Home
Assistant) is never executed. Config-flow and live-service behavior still
needs pytest-homeassistant; this file covers the parts that can be reasoned
about without it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import voluptuous as vol

COMPONENT = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "marstek"
    / "service_schema.py"
)

# Load service_schema.py as a standalone module: it depends only on
# voluptuous and aiomarstek, never on the Home Assistant runtime.
_spec = importlib.util.spec_from_file_location("marstek_service_schema", COMPONENT)
assert _spec is not None and _spec.loader is not None
service_schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(service_schema)


def test_dod_schema_accepts_inclusive_bounds() -> None:
    """Discharge depth accepts 30 and 88, the documented bounds."""
    assert service_schema.SET_DOD_SCHEMA({"value": 30})["value"] == 30
    assert service_schema.SET_DOD_SCHEMA({"value": 88})["value"] == 88


def test_dod_schema_rejects_out_of_range() -> None:
    """Values below 30 or above 88 raise before reaching the device."""
    with pytest.raises(vol.Invalid):
        service_schema.SET_DOD_SCHEMA({"value": 29})
    with pytest.raises(vol.Invalid):
        service_schema.SET_DOD_SCHEMA({"value": 89})
    with pytest.raises(vol.Invalid):
        service_schema.SET_DOD_SCHEMA({"value": 0})


def test_dod_schema_rejects_non_integer() -> None:
    """Non-numeric values are rejected cleanly."""
    with pytest.raises(vol.Invalid):
        service_schema.SET_DOD_SCHEMA({"value": "fifty"})


def test_power_version_schema_only_accepts_documented_values() -> None:
    """Only 800/1200/1500/2200/2500 are valid power versions."""
    for version in (800, 1200, 1500, 2200, 2500):
        assert (
            service_schema.SET_POWER_VERSION_SCHEMA({"power_version": version})[
                "power_version"
            ]
            == version
        )

    with pytest.raises(vol.Invalid):
        service_schema.SET_POWER_VERSION_SCHEMA({"power_version": 1000})
    with pytest.raises(vol.Invalid):
        service_schema.SET_POWER_VERSION_SCHEMA({"power_version": 2600})


def test_set_mode_schema_only_accepts_documented_modes() -> None:
    """Unknown operating modes are rejected at the schema level."""
    for mode in ("auto", "ai", "manual", "passive", "ups"):
        assert service_schema.SET_MODE_SCHEMA({"mode": mode})["mode"] == mode
    with pytest.raises(vol.Invalid):
        service_schema.SET_MODE_SCHEMA({"mode": "ecp"})


def test_build_mode_config_auto() -> None:
    """Auto mode emits the documented auto_cfg payload."""
    config = service_schema.build_mode_config({"mode": "auto"})
    assert config.to_payload() == {"mode": "Auto", "auto_cfg": {"enable": 1}}


def test_build_mode_config_passive() -> None:
    """Passive mode reads power and cd_time from config."""
    config = service_schema.build_mode_config(
        {
            "mode": "passive",
            "config": {"power": 100, "cd_time": 300},
        }
    )
    assert config.to_payload() == {
        "mode": "Passive",
        "passive_cfg": {"power": 100, "cd_time": 300},
    }


def test_build_mode_config_passive_wrapped() -> None:
    """Passive config may also be wrapped under passive_cfg."""
    config = service_schema.build_mode_config(
        {
            "mode": "passive",
            "config": {"passive_cfg": {"power": 50, "cd_time": 120}},
        }
    )
    assert config.to_payload() == {
        "mode": "Passive",
        "passive_cfg": {"power": 50, "cd_time": 120},
    }


def test_build_mode_config_passive_without_params() -> None:
    """Passive mode without power/cd_time is rejected, not sent half-empty."""
    with pytest.raises(vol.Invalid):
        service_schema.build_mode_config({"mode": "passive"})


def test_build_mode_config_ups_toggle() -> None:
    """UPS mode reads enable from config, defaulting to enabled."""
    on = service_schema.build_mode_config({"mode": "ups"})
    assert on.to_payload() == {"mode": "UPS", "ups_cfg": {"enable": 1}}

    off = service_schema.build_mode_config({"mode": "ups", "config": {"enable": False}})
    assert off.to_payload() == {"mode": "UPS", "ups_cfg": {"enable": 0}}


def test_build_mode_config_manual_full_schedule() -> None:
    """Manual mode passes the whole slot list through to ES.SetMode."""
    config = service_schema.build_mode_config(
        {
            "mode": "manual",
            "config": {
                "manual_cfg": [
                    {
                        "time_num": 1,
                        "start_time": "00:00",
                        "end_time": "06:00",
                        "week_set": 127,
                        "power": 100,
                        "enable": True,
                    },
                    {
                        "time_num": 2,
                        "start_time": "18:00",
                        "end_time": "23:00",
                        "week_set": 3,
                        "power": 250,
                        "enable": False,
                    },
                ]
            },
        }
    )
    payload = config.to_payload()
    assert payload["mode"] == "Manual"
    assert payload["manual_cfg"] == [
        {
            "time_num": 1,
            "start_time": "00:00",
            "end_time": "06:00",
            "week_set": 127,
            "power": 100,
            "enable": 1,
        },
        {
            "time_num": 2,
            "start_time": "18:00",
            "end_time": "23:00",
            "week_set": 3,
            "power": 250,
            "enable": 0,
        },
    ]


def test_build_mode_config_manual_accepts_single_object() -> None:
    """The API example shows a single slot object; accept it as one slot."""
    config = service_schema.build_mode_config(
        {
            "mode": "manual",
            "config": {
                "manual_cfg": {
                    "time_num": 1,
                    "start_time": "00:00",
                    "end_time": "06:00",
                    "week_set": 127,
                    "power": 100,
                }
            },
        }
    )
    assert config.to_payload()["manual_cfg"] == [
        {
            "time_num": 1,
            "start_time": "00:00",
            "end_time": "06:00",
            "week_set": 127,
            "power": 100,
            "enable": 1,
        }
    ]


def test_build_mode_config_manual_accepts_slots_alias() -> None:
    """Slots can be passed under either manual_cfg or slots."""
    config = service_schema.build_mode_config(
        {
            "mode": "manual",
            "config": {
                "slots": [
                    {
                        "time_num": 1,
                        "start_time": "00:00",
                        "end_time": "06:00",
                        "week_set": 1,
                        "power": 100,
                    }
                ]
            },
        }
    )
    assert config.to_payload()["manual_cfg"] == [
        {
            "time_num": 1,
            "start_time": "00:00",
            "end_time": "06:00",
            "week_set": 1,
            "power": 100,
            "enable": 1,
        }
    ]


def test_build_mode_config_manual_rejects_empty_schedule() -> None:
    """Manual mode without slots is rejected, not sent as an empty schedule."""
    with pytest.raises(vol.Invalid):
        service_schema.build_mode_config({"mode": "manual"})
    with pytest.raises(vol.Invalid):
        service_schema.build_mode_config({"mode": "manual", "config": {}})


def test_build_mode_config_manual_rejects_unsupported_slot_type() -> None:
    """A non-object/non-list slot payload is rejected."""
    with pytest.raises(vol.Invalid):
        service_schema.build_mode_config(
            {"mode": "manual", "config": {"manual_cfg": "08:00-20:00"}}
        )


def test_build_mode_config_manual_rejects_too_many_slots() -> None:
    """More than the documented maximum number of slots is rejected."""
    slots = [
        {
            "time_num": i,
            "start_time": "00:00",
            "end_time": "01:00",
            "week_set": 127,
            "power": 100,
        }
        for i in range(11)
    ]
    with pytest.raises(vol.Invalid):
        service_schema.build_mode_config(
            {"mode": "manual", "config": {"manual_cfg": slots}}
        )


def test_build_mode_config_normalizes_case() -> None:
    """Mode is case-insensitive at the service layer."""
    config = service_schema.build_mode_config({"mode": "AUTO"})
    assert config.to_payload()["mode"] == "Auto"


def test_factory_reset_schema_defaults() -> None:
    """Factory reset defaults to clearing accumulated data."""
    data = service_schema.FACTORY_RESET_SCHEMA({})
    assert data["keep_accumulated_data"] is False
    assert (
        service_schema.FACTORY_RESET_SCHEMA({"keep_accumulated_data": True})[
            "keep_accumulated_data"
        ]
        is True
    )


def test_service_schema_module_has_no_homeassistant_import() -> None:
    """service_schema must stay free of Home Assistant runtime imports."""
    source = COMPONENT.read_text(encoding="utf-8")
    assert "from homeassistant" not in source
    assert "import homeassistant" not in source


def test_strict_int_rejects_non_integer_floats() -> None:
    """A non-integer like 1200.9 is rejected, not truncated to 1200."""
    with pytest.raises(vol.Invalid):
        service_schema.SET_POWER_VERSION_SCHEMA({"power_version": 1200.9})
    with pytest.raises(vol.Invalid):
        service_schema.SET_DOD_SCHEMA({"value": 50.5})
    with pytest.raises(vol.Invalid):
        service_schema.build_mode_config(
            {
                "mode": "passive",
                "config": {"power": 100.9, "cd_time": 300},
            }
        )


def test_strict_int_accepts_integer_valued_floats() -> None:
    """An integer-valued float is accepted and normalized to int."""
    assert (
        service_schema.SET_POWER_VERSION_SCHEMA({"power_version": 1200.0})[
            "power_version"
        ]
        == 1200
    )


def test_build_mode_config_passive_rejects_missing_field() -> None:
    """A passive config missing cd_time is rejected cleanly, not via KeyError."""
    with pytest.raises(vol.Invalid):
        service_schema.build_mode_config({"mode": "passive", "config": {"power": 100}})


def test_build_mode_config_manual_rejects_slot_missing_field() -> None:
    """A manual slot missing a required field is rejected, not via KeyError."""
    with pytest.raises(vol.Invalid):
        service_schema.build_mode_config(
            {
                "mode": "manual",
                "config": {
                    "manual_cfg": [
                        {"time_num": 1, "start_time": "00:00", "end_time": "06:00"}
                    ]
                },
            }
        )


def test_build_manual_schedule_folds_days_to_bitmask() -> None:
    """Weekday names fold into the documented week_set bitmask."""
    slots = service_schema.build_manual_schedule(
        [
            {
                "start_time": "00:00",
                "end_time": "06:00",
                "days": ["mon", "sun"],
                "power": 800,
            }
        ]
    )
    assert slots[0].week_set == 1 | 64  # bit 0 = Monday, bit 6 = Sunday
    assert slots[0].time_num == 0  # auto-assigned from list position


def test_build_manual_schedule_all_week_is_127() -> None:
    """Selecting every weekday yields the documented 0b1111111 = 127."""
    slots = service_schema.build_manual_schedule(
        [
            {
                "start_time": "00:00",
                "end_time": "06:00",
                "days": service_schema.MANUAL_SCHEDULE_WEEKDAYS,
                "power": 1500,
                "manual_set": 2,
            }
        ]
    )
    assert slots[0].week_set == 127
    assert slots[0].manual_set == 2


def test_build_manual_schedule_rejects_too_many_slots() -> None:
    """More than the documented slot maximum is rejected."""
    slots = [
        {
            "start_time": "00:00",
            "end_time": "01:00",
            "days": ["mon"],
            "power": 100,
        }
        for _ in range(11)
    ]
    with pytest.raises(vol.Invalid):
        service_schema.build_manual_schedule(slots)


def test_build_manual_schedule_rejects_bad_weekday() -> None:
    """An unknown weekday name is rejected at the schema level."""
    with pytest.raises(vol.Invalid):
        service_schema.build_manual_schedule(
            [
                {
                    "start_time": "00:00",
                    "end_time": "06:00",
                    "days": ["funday"],
                    "power": 100,
                }
            ]
        )
