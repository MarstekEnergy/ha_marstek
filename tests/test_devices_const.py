"""Tests for the device constants: supported types and per-model power versions.

const.py imports only aiomarstek and the standard library, so it is loaded
directly from its file without triggering the package __init__ (which pulls
in Home Assistant).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

CONST = (
    Path(__file__).resolve().parents[1] / "custom_components" / "marstek" / "const.py"
)

_spec = importlib.util.spec_from_file_location("marstek_const", CONST)
assert _spec is not None and _spec.loader is not None
const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(const)


def test_venusc_and_mini_are_not_supported() -> None:
    """Venus C and Venus E mini are excluded until confirmed on real hardware."""
    for excluded in (
        "VenusC",
        "Venus C",
        "VNSEM-0",
        "VenusEm",
        "Venus E mini",
        "VenusEmini",
    ):
        assert excluded not in const.SUPPORTED_DEVICE_TYPES, (
            f"{excluded} must not be in the supported set yet"
        )
    # The supported set still covers Venus E / A / D.
    assert "VNSE3-0" in const.SUPPORTED_DEVICE_TYPES
    assert "VNSA-0" in const.SUPPORTED_DEVICE_TYPES


def test_power_versions_for_venus_a() -> None:
    """Venus A exposes the documented 800/1200/1500 W versions."""
    versions = const.power_versions_for("VNSA-0")
    assert versions == (800, 1200, 1500)
    assert const.power_versions_for("VenusA") == (800, 1200, 1500)


def test_power_versions_for_venus_d() -> None:
    """Venus D exposes the documented 800/2200 W versions."""
    assert const.power_versions_for("VNSD-0") == (800, 2200)


def test_power_versions_for_venus_e() -> None:
    """Venus E exposes the documented 800/2500 W versions."""
    assert const.power_versions_for("VNSE3-0") == (800, 2500)


def test_power_versions_for_unknown_model_falls_back() -> None:
    """Unknown models get the full option set rather than an empty tuple."""
    versions = const.power_versions_for("UnknownModel")
    assert versions == const.POWER_VERSION_OPTIONS


def test_selectable_modes_exclude_passive() -> None:
    """The operating-mode select offers Auto/AI/Manual/UPS; Passive is service-only."""
    assert set(const.SELECTABLE_MODE_OPTIONS) == {"auto", "ai", "manual", "ups"}
    assert "passive" not in const.SELECTABLE_MODE_OPTIONS


def test_port_constant_exists() -> None:
    """The per-device port config key is defined."""
    assert const.CONF_PORT == "port"
