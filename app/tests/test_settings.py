"""Settings document: defaults, validation, persistence, override resolution."""
import json

import pytest

from bidboard.settings import (
    DEFAULT_SETTINGS,
    SettingsError,
    load_settings,
    resolve,
    save_settings,
    validate,
)


def test_defaults_are_valid():
    validate(DEFAULT_SETTINGS)


def test_load_missing_file_returns_defaults(tmp_path):
    doc = load_settings(tmp_path / "settings.json")
    assert doc == DEFAULT_SETTINGS
    assert doc is not DEFAULT_SETTINGS  # a copy, not the shared object


def test_save_and_reload_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    current = load_settings(path)
    updated = save_settings(path, current, {"output": {"charts": "bars"}})
    assert updated["output"]["charts"] == "bars"
    # untouched keys survive
    assert updated["output"]["sort_by"] == "delivery_start"

    reloaded = load_settings(path)
    assert reloaded["output"]["charts"] == "bars"


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json at all", encoding="utf-8")
    assert load_settings(path) == DEFAULT_SETTINGS


def test_invalid_patch_rejected_and_file_untouched(tmp_path):
    path = tmp_path / "settings.json"
    current = save_settings(path, load_settings(path), {})
    before = path.read_text(encoding="utf-8")

    with pytest.raises(SettingsError) as err:
        save_settings(path, current, {"output": {"charts": "sparkles"}})
    assert "charts" in str(err.value)
    assert path.read_text(encoding="utf-8") == before


def test_unknown_top_level_key_rejected(tmp_path):
    with pytest.raises(SettingsError):
        save_settings(tmp_path / "s.json", load_settings(tmp_path / "s.json"), {"bogus": 1})


def test_threshold_cross_validation():
    doc = json.loads(json.dumps(DEFAULT_SETTINGS))
    doc["output"]["stale_thresholds"] = {"aging_days": 5, "stale_days": 5, "identical_scans": 3}
    with pytest.raises(SettingsError) as err:
        validate(doc)
    assert "aging_days" in str(err.value)


def test_filename_token_validation():
    doc = json.loads(json.dumps(DEFAULT_SETTINGS))
    doc["output"]["filename_patterns"]["master"] = "Bids {weekday}.xlsx"
    with pytest.raises(SettingsError) as err:
        validate(doc)
    assert "weekday" in str(err.value)

    doc["output"]["filename_patterns"]["master"] = "Bids {date}.csv"
    with pytest.raises(SettingsError):
        validate(doc)


def test_resolve_no_override_returns_globals():
    eff = resolve(DEFAULT_SETTINGS, company_id=7)
    assert eff["sort_by"] == "delivery_start"
    assert eff["commodities"] == []


def test_resolve_override_arrays_replace_and_dicts_merge(tmp_path):
    path = tmp_path / "settings.json"
    current = load_settings(path)
    current = save_settings(
        path,
        current,
        {
            "filters": {"commodities": ["CORN", "SOYBEANS"]},
            "company_overrides": {
                "7": {
                    "commodities": ["CORN"],
                    "sort_by": "cash_desc",
                    "decimals": {"cash": 2},
                }
            },
        },
    )

    eff = resolve(current, company_id=7)
    assert eff["commodities"] == ["CORN"]          # array replaced wholesale
    assert eff["sort_by"] == "cash_desc"           # scalar overridden
    assert eff["decimals"]["cash"] == 2            # deep-merged...
    assert eff["decimals"]["basis"] == 4           # ...others intact

    other = resolve(current, company_id=99)
    assert other["commodities"] == ["CORN", "SOYBEANS"]
    assert other["sort_by"] == "delivery_start"


def test_override_cannot_touch_file_lifecycle_keys(tmp_path):
    path = tmp_path / "settings.json"
    with pytest.raises(SettingsError):
        save_settings(
            path,
            load_settings(path),
            {"company_overrides": {"7": {"filename_patterns": {"master": "x.xlsx"}}}},
        )
