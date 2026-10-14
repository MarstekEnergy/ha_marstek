"""Tests for the Marstek translations."""

import json
from pathlib import Path
import re

import pytest

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


@pytest.mark.parametrize(
    "path", sorted((COMPONENT / "translations").glob("*.json")), ids=lambda p: p.stem
)
def test_translation_matches_english(path: Path) -> None:
    """Every translation has the English keys and placeholders."""
    english = _strings(ENGLISH)
    translated = _strings(json.loads(path.read_text()))
    assert translated.keys() == english.keys()
    for key, text in english.items():
        placeholders = set(re.findall(r"{\w+}", text))
        assert set(re.findall(r"{\w+}", translated[key])) == placeholders, key


def test_english_matches_strings() -> None:
    """translations/en.json holds the same keys as strings.json."""
    strings = json.loads((COMPONENT / "strings.json").read_text())
    assert _strings(strings).keys() == _strings(ENGLISH).keys()
