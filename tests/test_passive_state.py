"""Tests for the write-only Passive-mode field state tracking.

Loaded directly from ``custom_components/marstek/passive_state.py`` by file
path so the tests run under the CI environment, which does not install
``homeassistant``. They exercise the pending-vs-confirmed value logic the
Passive number entities rely on to avoid rolling a failed/coalesced write back
to an intermediate value that never reached the device.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "marstek"


def _load_field_cls():
    """Import passive_state.py without triggering the marstek package __init__."""
    spec = importlib.util.spec_from_file_location(
        "marstek_passive_state", COMPONENT_DIR / "passive_state.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.PassiveField


PassiveField = _load_field_cls()


def test_start_write_sets_pending_target() -> None:
    """A new target becomes the value to send."""
    field = PassiveField()
    field.start_write(100)
    assert field.value == 100


def test_success_promotes_target_to_confirmed() -> None:
    """A successful write confirms the target and keeps it as the value."""
    field = PassiveField()
    field.start_write(100)
    field.mark_written(100)
    assert field.value == 100


def test_failure_rolls_back_to_last_confirmed() -> None:
    """A failed write reverts to the last device-confirmed value."""
    field = PassiveField()
    field.start_write(100)
    field.mark_written(100)
    field.start_write(200)
    field.rollback(200)
    assert field.value == 100


def test_concurrent_failure_rolls_back_to_confirmed_not_intermediate() -> None:
    """Rapid 100->200 where the surviving write fails must not leave 100.

    The 100 write was coalesced away and never reached the device; when both
    callers report failure the field must revert to the last confirmed value
    (here, nothing), not to the 100 intermediate target.
    """
    field = PassiveField()
    field.start_write(100)
    field.start_write(200)
    field.rollback(200)  # the surviving (200) write failed
    field.rollback(100)  # the coalesced (100) write also reports failure
    assert field.value is None


def test_stale_success_does_not_clobber_newer_confirmation() -> None:
    """A coalesced-away write reporting success must not confirm its value."""
    field = PassiveField()
    field.start_write(100)
    field.start_write(200)
    field.mark_written(200)  # the surviving write succeeded
    field.mark_written(100)  # the coalesced write's stale success
    assert field.value == 200
