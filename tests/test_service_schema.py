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
    for mode in ("auto", "ups"):
        assert service_schema.SET_MODE_SCHEMA({"mode": mode})["mode"] == mode
    with pytest.raises(vol.Invalid):
        service_schema.SET_MODE_SCHEMA({"mode": "ecp"})


def test_set_mode_schema_rejects_ai() -> None:
    """AI is configured in the Marstek app, so HA never writes it."""
    with pytest.raises(vol.Invalid):
        service_schema.SET_MODE_SCHEMA({"mode": "ai"})


def test_set_mode_schema_rejects_manual() -> None:
    """Manual is not writable until its schedule payload is verified on hardware."""
    with pytest.raises(vol.Invalid):
        service_schema.SET_MODE_SCHEMA({"mode": "manual"})


def test_set_mode_schema_rejects_passive() -> None:
    """Passive is not writable from HA at all."""
    with pytest.raises(vol.Invalid):
        service_schema.SET_MODE_SCHEMA({"mode": "passive"})
    with pytest.raises(vol.Invalid):
        service_schema.SET_MODE_SCHEMA(
            {"mode": "passive", "config": {"power": 100, "cd_time": 300}}
        )


def test_build_mode_config_auto() -> None:
    """Auto mode emits the documented auto_cfg payload."""
    config = service_schema.build_mode_config({"mode": "auto"})
    assert config.to_payload() == {"mode": "Auto", "auto_cfg": {"enable": 1}}


def test_build_mode_config_ups_toggle() -> None:
    """UPS mode reads enable from config, defaulting to enabled."""
    on = service_schema.build_mode_config({"mode": "ups"})
    assert on.to_payload() == {"mode": "UPS", "ups_cfg": {"enable": 1}}

    off = service_schema.build_mode_config({"mode": "ups", "config": {"enable": False}})
    assert off.to_payload() == {"mode": "UPS", "ups_cfg": {"enable": 0}}


def test_build_mode_config_normalizes_case() -> None:
    """Mode is case-insensitive at the service layer."""
    config = service_schema.build_mode_config({"mode": "AUTO"})
    assert config.to_payload()["mode"] == "Auto"


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
                "mode": "ups",
                "config": {"enable": 1.5},
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
