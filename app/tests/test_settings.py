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


# ---- a pattern must actually be able to name a file ------------------------
#
# Filename patterns are user-editable, and the workbook is the whole point of
# the app, so a pattern that cannot build a filename must be a validation
# error at the moment it is saved - never a 500, and never a scan that
# finishes with no spreadsheet to show for it.


def test_stray_brace_in_a_filename_pattern_is_a_friendly_error(tmp_path):
    path = tmp_path / "settings.json"
    current = save_settings(path, load_settings(path), {})
    before = path.read_text(encoding="utf-8")

    with pytest.raises(SettingsError) as err:
        save_settings(
            path, current,
            {"output": {"filename_patterns": {"master": "Cash {.xlsx"}}},
        )
    assert "filename_patterns.master" in str(err.value)
    assert path.read_text(encoding="utf-8") == before


def test_pattern_that_cannot_build_a_filename_is_rejected(tmp_path):
    path = tmp_path / "settings.json"
    current = load_settings(path)
    for bad in ("Cash {date:z}.xlsx", "Cash {}.xlsx"):
        with pytest.raises(SettingsError) as err:
            save_settings(
                path, current,
                {"output": {"filename_patterns": {"master": bad}}},
            )
        assert "filename_patterns.master" in str(err.value)
    assert not path.exists()          # nothing was written for either attempt


def test_patterns_that_can_build_a_filename_are_accepted():
    doc = json.loads(json.dumps(DEFAULT_SETTINGS))
    for good in ("Cash Bids {date} {time}.xlsx", "{company} Bids {date}.xlsx",
                 "{commodity} {scan_id}.xlsx", "Cash Bids.xlsx"):
        doc["output"]["filename_patterns"]["master"] = good
        validate(doc)                 # must not raise


def test_every_shipped_pattern_still_builds_a_filename():
    from bidboard.report.files import render_filename

    validate(DEFAULT_SETTINGS)
    tokens = {"date": "2026-01-31", "time": "0730", "scan_id": "1",
              "company": "Sample Grain Co", "commodity": "CORN"}
    for name, pattern in DEFAULT_SETTINGS["output"]["filename_patterns"].items():
        assert render_filename(pattern, tokens).lower().endswith(".xlsx"), name


def test_hand_edited_unbuildable_pattern_falls_back_to_defaults(tmp_path):
    """settings.json is the human-editable preference file. An unbuildable
    pattern now makes the file invalid, so the app falls back to the shipped
    defaults - the documented behaviour for any invalid settings file -
    instead of loading a pattern that breaks every later workbook build."""
    path = tmp_path / "settings.json"
    doc = json.loads(json.dumps(DEFAULT_SETTINGS))
    doc["output"]["filename_patterns"]["master"] = "Cash {date:z}.xlsx"
    doc["output"]["charts"] = "bars"
    path.write_text(json.dumps(doc), encoding="utf-8")

    assert load_settings(path) == DEFAULT_SETTINGS


def test_settings_endpoint_answers_a_bad_pattern_with_400(tmp_path):
    """The settings page shows the message inline; before this fix a stray
    brace reached the endpoint as an unhandled ValueError and answered 500."""
    from flask import Flask

    from bidboard.web.settings_api import settings_bp

    app = Flask(__name__)
    app.config["SETTINGS_PATH"] = str(tmp_path / "settings.json")
    app.register_blueprint(settings_bp)
    client = app.test_client()

    for bad in ("Cash {.xlsx", "Cash } Bids.xlsx", "Cash {date:z}.xlsx"):
        resp = client.put(
            "/api/settings",
            json={"output": {"filename_patterns": {"master": bad}}},
        )
        assert resp.status_code == 400, bad
        body = resp.get_json()
        assert body["field"] == "output.filename_patterns.master"
        assert "filename_patterns.master" in body["error"]


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
