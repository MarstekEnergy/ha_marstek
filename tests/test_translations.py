"""Tests for the Marstek translations."""

import json
from pathlib import Path

COMPONENT = Path(__file__).parent.parent / "custom_components" / "marstek"
ENGLISH = json.loads((COMPONENT / "translations" / "en.json").read_text())


def _strings(data: dict, path: str = "") -> dict[str, str]:
    """Flatten a translation file to {dotted key: text}."""
    flat: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flat |= _strings(value, f"{path}{key}.")
        else:
            flat[f"{path}{key}"] = value
    return flat


def test_english_matches_strings() -> None:
    """translations/en.json holds the same keys as strings.json."""
    strings = json.loads((COMPONENT / "strings.json").read_text())
    assert _strings(strings).keys() == _strings(ENGLISH).keys()
