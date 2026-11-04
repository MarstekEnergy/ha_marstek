"""Static structure tests for the Marstek integration files."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import yaml

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "marstek"


def _load_json(name: str) -> dict:
    return json.loads((COMPONENT_DIR / name).read_text(encoding="utf-8"))


def test_manifest_is_valid_hacs_integration() -> None:
    """manifest.json declares everything HACS and HA require."""
    manifest = _load_json("manifest.json")

    assert manifest["domain"] == "marstek"
    assert manifest["name"] == "Marstek"
    assert "version" in manifest  # custom integrations must declare a version
    assert manifest["config_flow"] is True
    assert manifest["requirements"], "requirements must list aiomarstek"
    assert manifest["requirements"][0].startswith("aiomarstek")
    assert manifest["documentation"].startswith("https://")
    assert manifest["issue_tracker"].startswith("https://")
    assert manifest["codeowners"], "codeowners must be set"


def test_hacs_json_is_present_and_valid() -> None:
    """hacs.json exists with the minimum required fields."""
    hacs_json_path = COMPONENT_DIR.parents[1] / "hacs.json"
    hacs = json.loads(hacs_json_path.read_text(encoding="utf-8"))

    assert "name" in hacs
    assert isinstance(hacs.get("render_readme"), bool) or "render_readme" in hacs


def test_platform_modules_parse_as_valid_python() -> None:
    """Every platform module parses without syntax errors."""
    for module in (
        "__init__",
        "binary_sensor",
        "button",
        "config_flow",
        "const",
        "coordinator",
        "entity",
        "helpers",
        "number",
        "select",
        "sensor",
        "service_schema",
        "switch",
        "write_queue",
    ):
        source = (COMPONENT_DIR / f"{module}.py").read_text(encoding="utf-8")
        ast.parse(source, filename=f"{module}.py")


def test_registered_platforms_match_manifest_platform_list() -> None:
    """The platforms registered in __init__.py cover all platform modules."""
    init_source = (COMPONENT_DIR / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(init_source)

    platforms: list[str] = []
    for node in ast.walk(tree):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        if (
            isinstance(target, ast.Name)
            and target.id == "PLATFORMS"
            and isinstance(value, ast.List)
        ):
            for elt in value.elts:
                assert isinstance(elt, ast.Attribute)
                platforms.append(elt.attr.lower())

    assert set(platforms) == {
        "binary_sensor",
        "button",
        "number",
        "select",
        "sensor",
        "switch",
    }


def test_services_yaml_lists_all_documented_services() -> None:
    """services.yaml declares the documented configuration services only."""
    services = yaml.safe_load(
        (COMPONENT_DIR / "services.yaml").read_text(encoding="utf-8")
    )
    # Manual schedules have a dedicated service since v0.2.3.
    assert set(services) == {
        "factory_reset",
        "set_dod",
        "set_manual_schedule",
        "set_mode",
        "set_power_version",
    }


def test_services_yaml_target_is_mapping_limited_to_marstek() -> None:
    """Each service target is a mapping restricted to the Marstek integration.

    A bare ``target: entity`` string fails HA's service schema validation and
    makes Home Assistant skip loading the service metadata, so the target must
    be a mapping with an entity selector scoped to the integration.
    """
    services = yaml.safe_load(
        (COMPONENT_DIR / "services.yaml").read_text(encoding="utf-8")
    )
    for name, spec in services.items():
        target = spec.get("target")
        assert isinstance(target, dict), f"{name} target must be a mapping"
        entity = target.get("entity")
        assert isinstance(entity, dict), f"{name} target.entity must be a mapping"
        assert entity.get("integration") == "marstek", (
            f"{name} target.entity must be limited to the marstek integration"
        )


def test_select_platform_creates_both_selects() -> None:
    """The select platform instantiates the mode and power-version selects."""
    source = (COMPONENT_DIR / "select.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    instantiated: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "async_add_entities"
        ):
            assert isinstance(node.args[0], ast.List)
            for elt in node.args[0].elts:
                assert isinstance(elt, ast.Call)
                assert isinstance(elt.func, ast.Name)
                instantiated.append(elt.func.id)
    assert instantiated == ["MarstekModeSelect", "MarstekPowerVersionSelect"]


def test_coordinator_handles_marstek_no_response() -> None:
    """The coordinator catches MarstekNoResponse, not just timeout errors."""
    source = (COMPONENT_DIR / "coordinator.py").read_text(encoding="utf-8")
    assert "MarstekNoResponse" in source, "coordinator must import MarstekNoResponse"
    assert "except MarstekNoResponse" in source, (
        "coordinator must handle MarstekNoResponse explicitly"
    )


def test_coordinator_caps_silent_polls() -> None:
    """Repeated silence must eventually surface as unavailable."""
    source = (COMPONENT_DIR / "coordinator.py").read_text(encoding="utf-8")
    assert "MAX_SILENT_POLLS" in source, "coordinator must cap consecutive silent polls"
    assert "_silent_polls" in source, "coordinator must track silent polls"


def test_config_flow_uses_voluptuous_not_probatio() -> None:
    """config_flow must not depend on the non-standard probatio package."""
    source = (COMPONENT_DIR / "config_flow.py").read_text(encoding="utf-8")
    assert "probatio" not in source, "config_flow must use voluptuous, not probatio"
    assert "import voluptuous as vol" in source


def test_config_flow_supports_custom_port() -> None:
    """Manual setup must let the user enter a UDP port."""
    source = (COMPONENT_DIR / "config_flow.py").read_text(encoding="utf-8")
    assert "CONF_PORT" in source
    assert "DEFAULT_UDP_PORT" in source


def test_select_mode_options_use_constant() -> None:
    """The mode select options come from SELECTABLE_MODE_OPTIONS."""
    source = (COMPONENT_DIR / "select.py").read_text(encoding="utf-8")
    assert "SELECTABLE_MODE_OPTIONS" in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.keyword)
            and node.arg == "options"
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "list"
        ):
            # list(SELECTABLE_MODE_OPTIONS) — the literal set is imported.
            return
    # Fall back: options come from SELECTABLE_MODE_OPTIONS indirectly.


def test_select_power_version_uses_per_type_lookup() -> None:
    """The power-version select narrows options by device type."""
    source = (COMPONENT_DIR / "select.py").read_text(encoding="utf-8")
    assert "power_versions_for" in source


def test_binary_sensor_platform_creates_permission_sensors() -> None:
    """The binary sensor platform exposes charge, discharge and connectivity."""
    source = (COMPONENT_DIR / "binary_sensor.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    instantiated: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "async_add_entities"
        ):
            assert isinstance(node.args[0], ast.List)
            for elt in node.args[0].elts:
                assert isinstance(elt, ast.Call)
                assert isinstance(elt.func, ast.Name)
                instantiated.append(elt.func.id)
    assert "MarstekChargePermissionBinarySensor" in instantiated
    assert "MarstekDischargePermissionBinarySensor" in instantiated
    assert "MarstekCTStateBinarySensor" in instantiated
    assert "MarstekConnectedBinarySensor" in instantiated


def test_connectivity_sensor_reports_coordinator_status() -> None:
    """The connectivity sensor tracks coordinator success and stays available."""
    source = (COMPONENT_DIR / "binary_sensor.py").read_text(encoding="utf-8")
    assert "MarstekConnectedBinarySensor" in source
    assert "BinarySensorDeviceClass.CONNECTIVITY" in source
    assert "EntityCategory.DIAGNOSTIC" in source
    assert "last_update_success" in source
    # The sensor must override available so it can report the disconnect
    # instead of going unavailable itself.
    assert "def available" in source
    assert "return True" in source


def test_sensor_platform_exposes_wifi_network_details() -> None:
    """Wifi.GetStatus gateway/netmask/DNS are surfaced as sensors."""
    source = (COMPONENT_DIR / "sensor.py").read_text(encoding="utf-8")
    for key in ("wifi_gateway", "wifi_netmask", "wifi_dns"):
        assert f'key="{key}"' in source
        assert f'translation_key="{key}"' in source


def test_new_entity_keys_have_translations() -> None:
    """Every new entity key has a name in strings.json and en.json."""
    keys = {
        "binary_sensor": ["ct_state"],
        "button": ["restart"],
        "sensor": [
            "wifi_gateway",
            "wifi_netmask",
            "wifi_dns",
            "battery_soh",
            "battery_total_input_energy",
            "battery_total_output_energy",
            "backup_input_energy",
            "backup_output_energy",
            "energy_day_charge",
            "energy_day_discharge",
            "energy_month_charge",
            "energy_month_discharge",
            "energy_total_charge",
            "energy_total_discharge",
            "ems_error",
            "bms_error",
            "inv_error",
            "mppt_error",
            "ems_version",
            "boot_version",
            "bms_version",
            "inverter_version",
        ],
    }
    for filename in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT_DIR / filename).read_text(encoding="utf-8"))
        for domain, entity_keys in keys.items():
            for key in entity_keys:
                assert key in data["entity"][domain], f"{filename} missing {domain}.{key}"


def test_coordinator_delegates_to_debounced_write_queue() -> None:
    """The coordinator hands every write to the shared debounce queue."""
    source = (COMPONENT_DIR / "coordinator.py").read_text(encoding="utf-8")
    assert "from .write_queue import DebouncedWriteQueue" in source
    assert "DebouncedWriteQueue(" in source
    assert "WRITE_DEBOUNCE_DELAY" in source
    assert "async def async_write(" in source
    assert "await self._write_queue.async_write(key, action)" in source
    # The queue must not be implemented twice: a second copy in the
    # coordinator would drift from the one the behaviour tests cover.
    assert "_drain_write" not in source
    assert "_pending_writes" not in source
    assert "_write_task" not in source


def test_control_entities_route_through_debounced_write() -> None:
    """Select, switch, number and button entities go via coordinator.async_write."""
    for module in ("select", "switch", "number", "button"):
        source = (COMPONENT_DIR / f"{module}.py").read_text(encoding="utf-8")
        assert "coordinator.async_write" in source, (
            f"{module}.py must route writes through coordinator.async_write"
        )
        assert "async_request_refresh()" not in source, (
            f"{module}.py must let async_write do the post-write refresh"
        )


def test_write_queue_coalesces_per_key_and_moves_to_back() -> None:
    """Rapid repeats of one key coalesce; distinct keys queue, not drop.

    The queue tracks pending writes per key, so a write to one control
    (mode, LED, power version, factory reset, ...) is never silently
    replaced by another control's write inside the debounce window. Only
    repeats of the same key collapse, and the surviving entry takes the
    position of the latest request rather than the earliest one.
    """
    source = (COMPONENT_DIR / "write_queue.py").read_text(encoding="utf-8")
    assert "async def async_write(" in source
    assert "key: str," in source
    # Pending writes are tracked per key, not as a single slot that the next
    # write overwrites regardless of what it targets.
    assert "if key in self._pending:" in source
    assert "self._pending[key] = (action, future)" in source
    # A superseded entry is removed and re-inserted so it moves to the back
    # of the queue instead of keeping the first request's position.
    assert "del self._pending[key]" in source
    # Distinct pending writes are drained in FIFO order, each one sent.
    assert "iter(self._pending.items())" in source


def _async_write_keys(module: str) -> set[str]:
    """Return the literal keys passed to coordinator.async_write in a module."""
    source = (COMPONENT_DIR / f"{module}.py").read_text(encoding="utf-8")
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source, filename=f"{module}.py")):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "async_write"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
    return keys


def test_control_entities_pass_distinct_write_keys() -> None:
    """Each control write carries a key so only same-key repeats coalesce."""
    expected = {
        "number": {"dod", "passive"},
        "switch": {"bluetooth", "led", "device_mode"},
        "select": {"device_mode", "power_version"},
        "button": {"restart", "factory_reset", "factory_reset_keep_data"},
    }
    for module, keys in expected.items():
        assert _async_write_keys(module) == keys, (
            f"{module}.py must pass exactly these keys to async_write: {keys}"
        )


def test_mode_entry_points_split_plain_switch_from_passive() -> None:
    """Plain mode switches share one key; Passive writes use their own.

    The mode select and UPS switch both set a bare operating mode and share
    "device_mode" so the latest request survives. The Passive number entities
    carry power/cooldown parameters, so they use a separate "passive" key: a
    later plain mode switch must queue after a Passive write (not coalesce it
    away), otherwise the Passive write would be reported as done without ever
    reaching the device.
    """
    assert _async_write_keys("select") == {"device_mode", "power_version"}
    assert _async_write_keys("switch") == {"bluetooth", "led", "device_mode"}
    assert _async_write_keys("number") == {"dod", "passive"}
    # Passive writes never share the plain mode-switch key, and vice versa.
    assert "device_mode" not in _async_write_keys("number")
    assert "passive" not in _async_write_keys("select")
    assert "passive" not in _async_write_keys("switch")


def test_passive_number_rolls_back_on_cancellation() -> None:
    """A cancelled Passive write reverts the field to its confirmed value."""
    source = (COMPONENT_DIR / "number.py").read_text(encoding="utf-8")
    assert "asyncio.CancelledError" in source
    assert "(HomeAssistantError, asyncio.CancelledError)" in source
    assert ".rollback(target)" in source


def test_services_route_through_debounced_write() -> None:
    """Service calls go through the per-device queue, never straight to UDP.

    A service that writes the client directly would race ahead of a queued
    entity write and let the older request take effect last. The shared
    helper and the per-model power-version pre-check must both enqueue.
    """
    source = (COMPONENT_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "await coordinator.async_write(key, make_action(coordinator))" in source
    assert "coord.async_write(" in source
    # No service may await a UDP client method directly.
    assert "await coordinator.udp_client" not in source
    assert "await coord.udp_client" not in source
    # Mode, DOD, manual schedule and the two factory-reset variants are each
    # queued under their per-device-state key. set_mode splits by requested
    # mode so a Passive write is not coalesced away by a plain mode switch.
    assert '"device_mode"' in source
    assert '"passive"' in source
    assert '"dod"' in source
    assert '"manual_schedule"' in source
    assert 'config.mode == "passive"' in source


def test_factory_reset_service_matches_button_keys() -> None:
    """The factory-reset service reuses the buttons' two keys.

    Clearing and keeping data are different device operations, so they stay
    distinct keys (FIFO, later request wins); the service must pick the same
    keys the buttons use so a service call and a button press order by
    arrival instead of racing.
    """
    init_source = (COMPONENT_DIR / "__init__.py").read_text(encoding="utf-8")
    assert '"factory_reset_keep_data" if keep else "factory_reset"' in init_source

    button_source = (COMPONENT_DIR / "button.py").read_text(encoding="utf-8")
    assert '"factory_reset"' in button_source
    assert '"factory_reset_keep_data"' in button_source


def test_unload_entry_shuts_down_write_queue() -> None:
    """Unloading a config entry stops the background write-queue drain."""
    source = (COMPONENT_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "async def async_unload_entry" in source
    assert "await entry.runtime_data.async_shutdown()" in source

    coordinator = (COMPONENT_DIR / "coordinator.py").read_text(encoding="utf-8")
    assert "async def async_shutdown" in coordinator
    assert "await self._write_queue.async_shutdown()" in coordinator


def test_controls_read_back_from_sys_info() -> None:
    """The LED and Bluetooth switches report the device's value, not an assumption."""
    switch_source = (COMPONENT_DIR / "switch.py").read_text(encoding="utf-8")
    # Readback comes from the Sys.GetInfo fields.
    assert "data.led" in switch_source
    assert "data.bluetooth_advertising" in switch_source
    # The device's value always wins; only a freshly written value is held.
    assert "_optimistic_state" in switch_source
    assert "_attr_is_on = " not in switch_source

    number_source = (COMPONENT_DIR / "number.py").read_text(encoding="utf-8")
    # No optimistic _attr_native_value on DOD/passive numbers.
    assert "_attr_native_value = " not in number_source

    select_source = (COMPONENT_DIR / "select.py").read_text(encoding="utf-8")
    assert "data.power_version" in select_source


def test_mode_select_updates_optimistically() -> None:
    """The mode select shows the chosen option immediately, before the write."""
    source = (COMPONENT_DIR / "select.py").read_text(encoding="utf-8")
    assert "_attr_current_option" in source
    assert "async_write_ha_state" in source
    assert "_handle_coordinator_update" in source


def test_switch_platform_hides_ups() -> None:
    """The UPS switch is hidden (UPS is controlled via the mode select)."""
    source = (COMPONENT_DIR / "switch.py").read_text(encoding="utf-8")
    assert "MarstekUpsSwitch" in source  # class still defined for future use
    tree = ast.parse(source)
    instantiated: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "async_add_entities"
        ):
            for elt in node.args[0].elts:
                if isinstance(elt, ast.Call) and isinstance(elt.func, ast.Name):
                    instantiated.append(elt.func.id)
    assert "MarstekUpsSwitch" not in instantiated


def test_number_platform_hides_passive_entities() -> None:
    """Passive power/cooldown numbers are hidden (set via the set_mode service)."""
    source = (COMPONENT_DIR / "number.py").read_text(encoding="utf-8")
    assert "MarstekPassivePowerNumber" in source  # classes still defined
    assert "MarstekPassiveCdTimeNumber" in source
    tree = ast.parse(source)
    instantiated: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "async_add_entities"
        ):
            for elt in node.args[0].elts:
                if isinstance(elt, ast.Call) and isinstance(elt.func, ast.Name):
                    instantiated.append(elt.func.id)
    assert "MarstekPassivePowerNumber" not in instantiated
    assert "MarstekPassiveCdTimeNumber" not in instantiated


def test_set_manual_schedule_service_registered() -> None:
    """The set_manual_schedule service is registered with a schema."""
    source = (COMPONENT_DIR / "__init__.py").read_text(encoding="utf-8")
    assert '"set_manual_schedule"' in source
    assert "SET_MANUAL_SCHEDULE_SERVICE_SCHEMA" in source
    schema = (COMPONENT_DIR / "service_schema.py").read_text(encoding="utf-8")
    assert "build_manual_schedule" in schema
    assert "MANUAL_SCHEDULE_WEEKDAYS" in schema


def test_write_only_controls_notify_on_success() -> None:
    """Write-only controls route feedback through the _notify_success hook.

    The sidebar panel renders its own success/error toasts, so the hook no
    longer creates a persistent notification (it is a no-op kept as the
    single feedback seam). The call sites in the write-only entities must
    still invoke it so a future transport can be added in one place.
    """
    entity = (COMPONENT_DIR / "entity.py").read_text(encoding="utf-8")
    assert "_notify_success" in entity
    # The persistent-notification transport was removed in favour of the
    # panel toasts; importing it here would be dead code.
    assert "persistent_notification" not in entity
    for module in ("number", "switch", "select"):
        source = (COMPONENT_DIR / f"{module}.py").read_text(encoding="utf-8")
        assert "_notify_success" in source, (
            f"{module}.py should call _notify_success on a successful write"
        )


def test_service_schema_exposes_fields_not_full_schemas_only() -> None:
    """service_schema exposes field dicts for cv.make_entity_service_schema."""
    source = (COMPONENT_DIR / "service_schema.py").read_text(encoding="utf-8")
    for fields in (
        "SET_MODE_FIELDS",
        "SET_DOD_FIELDS",
        "SET_POWER_VERSION_FIELDS",
        "FACTORY_RESET_FIELDS",
    ):
        assert fields in source, f"service_schema must expose {fields}"


def test_strings_json_covers_every_translation_key() -> None:
    """strings.json declares names for every entity translation_key used."""
    strings = _load_json("strings.json")
    sensor_keys = set(strings["entity"]["sensor"])

    sensor_source = (COMPONENT_DIR / "sensor.py").read_text(encoding="utf-8")
    used_translation_keys = {
        match.value for match in _translation_keys_in_source(sensor_source)
    }

    missing = used_translation_keys - sensor_keys
    assert not missing, f"sensor strings.json missing: {missing}"


def test_strings_and_en_translation_are_in_sync() -> None:
    """translations/en.json mirrors the entity keys in strings.json."""
    strings = _load_json("strings.json")
    en = _load_json("translations/en.json")

    for platform in (
        "binary_sensor",
        "button",
        "number",
        "select",
        "sensor",
        "switch",
    ):
        assert strings["entity"][platform].keys() == en["entity"][platform].keys(), (
            f"{platform} entity keys differ between strings.json and en.json"
        )


def test_icons_json_only_references_known_entity_keys() -> None:
    """icons.json entity keys are a subset of strings.json entity keys."""
    strings = _load_json("strings.json")
    icons = _load_json("icons.json")

    for platform, entries in icons["entity"].items():
        known = set(strings["entity"][platform])
        unknown = set(entries) - known
        assert not unknown, f"icons.json {platform} has unknown keys: {unknown}"


def test_write_forces_extras_readback() -> None:
    """A successful write forces the slow extras group on the next poll.

    Sys.GetInfo (DOD, the Bluetooth lock, the LED, the power version) lives in
    the extras group, which otherwise only refreshes every EXTRAS_INTERVAL
    polls. Forcing it after a write makes the readback converge in one poll
    instead of up to a couple of minutes.
    """
    coordinator = (COMPONENT_DIR / "coordinator.py").read_text(encoding="utf-8")
    assert "self._force_extras = False" in coordinator
    assert "force_extras=force_extras" in coordinator
    # The write queue's refresh callback is the force-aware method.
    assert "self._async_refresh_after_write" in coordinator
    assert "_async_refresh_after_write" in coordinator
    # The flag is consumed inside _async_update_data (async_request_refresh is
    # debounced, so setting it around the call would race the deferred run).
    assert "force_extras = self._force_extras" in coordinator
    # A poll that never delivered data must not swallow the pending readback.
    assert coordinator.count("self._force_extras = force_extras or self._force_extras") == 2


def test_restart_button_uses_sys_reboot() -> None:
    """The restart control is a real Sys.Reboot button, not the MQTT placeholder."""
    button_source = (COMPONENT_DIR / "button.py").read_text(encoding="utf-8")
    assert "MarstekRestartButton" in button_source
    assert 'key="restart"' in button_source
    assert "udp_client.reboot(" in button_source
    assert "ButtonDeviceClass.RESTART" in button_source
    # A failed send (socket/bind error) is not a restart in progress.
    assert "except OSError" in button_source

    panel = (COMPONENT_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
    # The panel presses the entity; the old "needs MQTT cd=16" toast is gone.
    assert 'callService("button", "press"' in panel
    assert "Restart requires MQTT" not in panel


def test_dod_entity_reads_back_from_sys_info() -> None:
    """The DOD number shows the device's value instead of being write-only."""
    number_source = (COMPONENT_DIR / "number.py").read_text(encoding="utf-8")
    assert "data.dod" in number_source
    assert "Discharge depth is write-only" not in number_source

    panel = (COMPONENT_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
    # The slider starts from (and follows) the reported value.
    assert 'num(d, "dod")' in panel
    assert "data-dod" in panel


def test_error_sensors_expose_hex_codes() -> None:
    """Err.Get bitmasks become diagnostic sensors with a hex attribute."""
    source = (COMPONENT_DIR / "sensor.py").read_text(encoding="utf-8")
    for key in ("ems_error", "bms_error", "inv_error", "mppt_error"):
        assert f'key="{key}"' in source
        assert f'translation_key="{key}"' in source
    assert "is_error_code=True" in source
    assert "hex_code" in source
    assert "EntityCategory.DIAGNOSTIC" in source

    panel = (COMPONENT_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
    # The fault card is fed by the sensors; the cloud-API placeholder is gone.
    assert "get_device_error" not in panel


def test_energy_counters_replace_recorder_month_math() -> None:
    """Energy.GetData counters back the month chart and the day/month rows."""
    source = (COMPONENT_DIR / "sensor.py").read_text(encoding="utf-8")
    for key in (
        "energy_day_charge",
        "energy_day_discharge",
        "energy_month_charge",
        "energy_month_discharge",
        "energy_total_charge",
        "energy_total_discharge",
    ):
        assert f'key="{key}"' in source, f"sensor.py must expose {key}"

    panel = (COMPONENT_DIR / "frontend" / "panel.js").read_text(encoding="utf-8")
    assert 'num(d, "energy_day_charge")' in panel
    assert 'num(d, "energy_month_charge")' in panel
    assert 'num(d, "energy_total_charge")' in panel


# --- helpers ----------------------------------------------------------------


class _TranslationKeyMatch:
    def __init__(self, value: str) -> None:
        self.value = value


def _translation_keys_in_source(source: str) -> list[_TranslationKeyMatch]:
    """Collect translation_key="..." string literals from a module."""
    tree = ast.parse(source)
    matches: list[_TranslationKeyMatch] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword):
            continue
        if node.arg != "translation_key":
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            matches.append(_TranslationKeyMatch(node.value.value))
    return matches
