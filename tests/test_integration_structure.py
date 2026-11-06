"""Static data checks for the integration's metadata files.

Only *data* is asserted here: ``manifest.json``, ``hacs.json``,
``services.yaml``, ``strings.json``, ``translations/en.json`` and
``icons.json``. Behaviour (what the platforms create, which write key a
control uses, how the coordinator or the queue reacts) is covered by
``test_platforms_behaviour.py`` (platforms and the coordinator),
``test_services.py``, ``test_write_queue*.py`` and ``test_select_mode_sync.py``.
Grepping the source for a line of code was the old approach and broke on every
refactor without proving anything, so those assertions are gone.
"""

from __future__ import annotations

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
    hacs = json.loads(
        (COMPONENT_DIR.parents[1] / "hacs.json").read_text(encoding="utf-8")
    )

    assert "name" in hacs
    assert "render_readme" in hacs


def test_services_yaml_lists_all_documented_services() -> None:
    """services.yaml declares the documented configuration services only."""
    services = yaml.safe_load(
        (COMPONENT_DIR / "services.yaml").read_text(encoding="utf-8")
    )
    assert set(services) == {
        "set_dod",
        "set_mode",
        "set_power_version",
    }


def test_services_yaml_mode_options_are_the_writable_modes() -> None:
    """The set_mode selector offers only the modes the integration writes."""
    services = yaml.safe_load(
        (COMPONENT_DIR / "services.yaml").read_text(encoding="utf-8")
    )

    options = services["set_mode"]["fields"]["mode"]["selector"]["select"]["options"]
    assert options == ["auto", "ups"]


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


def test_strings_and_en_translation_are_in_sync() -> None:
    """translations/en.json mirrors strings.json key for key."""
    strings = _load_json("strings.json")
    en = _load_json("translations/en.json")

    for platform in (
        "binary_sensor",
        "number",
        "select",
        "sensor",
        "switch",
    ):
        assert strings["entity"][platform].keys() == en["entity"][platform].keys(), (
            f"{platform} entity keys differ between strings.json and en.json"
        )
    assert strings["services"].keys() == en["services"].keys()
    assert strings.get("exceptions", {}).keys() == en.get("exceptions", {}).keys()


def test_icons_json_only_references_known_entity_keys() -> None:
    """icons.json entity keys are a subset of strings.json entity keys."""
    strings = _load_json("strings.json")
    icons = _load_json("icons.json")

    for platform, entries in icons["entity"].items():
        known = set(strings["entity"][platform])
        unknown = set(entries) - known
        assert not unknown, f"icons.json {platform} has unknown keys: {unknown}"
