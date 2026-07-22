"""The settings document - every user preference in one human-readable
settings.json. Validated against a JSON schema on every save; invalid
patches are rejected with field-level messages the UI can show inline.

Relational/growing data lives in SQLite; preferences live here. Never mix.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema

SORT_CHOICES = ["delivery_start", "cash_desc", "commodity", "location"]
GROUP_CHOICES = ["commodity", "location", "company", "none"]
CHART_CHOICES = ["none", "bars", "lines", "both"]
MODE_CHOICES = ["master", "per_company", "per_commodity"]

SETTINGS_JSON_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "BidBoardSettings",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "version": {"type": "integer"},
        "output": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workbook_modes": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"enum": MODE_CHOICES},
                },
                "master": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "sheet_per": {"enum": ["company", "company_location"]},
                        "include_all_bids_sheet": {"type": "boolean"},
                    },
                },
                "include_health_sheet": {"type": "boolean"},
                "max_data_sheets_per_workbook": {
                    "type": "integer", "minimum": 1, "maximum": 100
                },
                "charts": {"enum": CHART_CHOICES},
                "chart_history_trend": {"type": "boolean"},
                "sort_by": {"enum": SORT_CHOICES},
                "group_by": {"enum": GROUP_CHOICES},
                "horizon_months": {"type": "integer", "minimum": 0, "maximum": 36},
                "decimals": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "cash": {"type": "integer", "minimum": 2, "maximum": 4},
                        "basis": {"type": "integer", "minimum": 2, "maximum": 4},
                        "futures": {"type": "integer", "minimum": 2, "maximum": 4},
                    },
                },
                "columns": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "include_change": {"type": "boolean"},
                        "include_futures": {"type": "boolean"},
                        "include_delivery_dates": {"type": "boolean"},
                        "include_flags": {"type": "boolean"},
                        "highlight_best_bids": {"type": "boolean"},
                    },
                },
                "hyperlinks": {"type": "boolean"},
                "overlap_policy": {"enum": ["show_both", "dedupe_with_note"]},
                "stale_thresholds": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "aging_days": {"type": "integer", "minimum": 1},
                        "stale_days": {"type": "integer", "minimum": 2},
                        "identical_scans": {"type": "integer", "minimum": 2},
                    },
                },
                "filename_patterns": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "master": {"type": "string"},
                        "per_company": {"type": "string"},
                        "per_commodity": {"type": "string"},
                    },
                },
                "archive": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "keep_latest": {"type": "integer", "minimum": 1},
                    },
                },
                "auto_build_after_scan": {"type": "boolean"},
            },
        },
        "filters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "commodities": {"type": "array", "items": {"type": "string"}},
                "locations": {"type": "array", "items": {"type": "string"}},
            },
        },
        "company_overrides": {
            "type": "object",
            "additionalProperties": {"$ref": "#/definitions/CompanyOverride"},
        },
        "schedule": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "enabled": {"type": "boolean"},
                "day_of_week": {
                    "enum": ["daily", "mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                },
                "hour": {"type": "integer", "minimum": 0, "maximum": 23},
                "minute": {"type": "integer", "minimum": 0, "maximum": 59},
            },
        },
        "advanced": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "politeness_min_s": {"type": "number", "minimum": 0.5},
                "politeness_max_s": {"type": "number", "minimum": 1.0},
                "page_timeout_s": {"type": "integer", "minimum": 5, "maximum": 120},
                "respect_robots": {"type": "boolean"},
                "resolve_verify": {"type": "boolean"},
            },
        },
    },
    "definitions": {
        "CompanyOverride": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "commodities": {"type": "array", "items": {"type": "string"}},
                "locations": {"type": "array", "items": {"type": "string"}},
                "sort_by": {"enum": SORT_CHOICES},
                "group_by": {"enum": GROUP_CHOICES},
                "horizon_months": {"type": "integer", "minimum": 0, "maximum": 36},
                "charts": {"enum": CHART_CHOICES},
                "decimals": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "cash": {"type": "integer", "minimum": 2, "maximum": 4},
                        "basis": {"type": "integer", "minimum": 2, "maximum": 4},
                        "futures": {"type": "integer", "minimum": 2, "maximum": 4},
                    },
                },
                "columns": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "include_change": {"type": "boolean"},
                        "include_futures": {"type": "boolean"},
                        "include_delivery_dates": {"type": "boolean"},
                        "include_flags": {"type": "boolean"},
                        "highlight_best_bids": {"type": "boolean"},
                    },
                },
            },
        }
    },
}

DEFAULT_SETTINGS: dict = {
    "version": 1,
    "output": {
        "workbook_modes": ["master"],
        "master": {"sheet_per": "company", "include_all_bids_sheet": True},
        "include_health_sheet": True,
        "max_data_sheets_per_workbook": 24,
        "charts": "none",
        "chart_history_trend": False,
        "sort_by": "delivery_start",
        "group_by": "commodity",
        "horizon_months": 12,
        "decimals": {"cash": 4, "basis": 4, "futures": 4},
        "columns": {
            "include_change": True,
            "include_futures": True,
            "include_delivery_dates": True,
            "include_flags": True,
            "highlight_best_bids": True,
        },
        "hyperlinks": True,
        "overlap_policy": "show_both",
        "stale_thresholds": {
            "aging_days": 2,
            "stale_days": 5,
            "identical_scans": 3,
        },
        "filename_patterns": {
            "master": "Cash Bids {date} {time}.xlsx",
            "per_company": "{company} Bids {date}.xlsx",
            "per_commodity": "{commodity} Bids {date}.xlsx",
        },
        "archive": {"enabled": True, "keep_latest": 10},
        "auto_build_after_scan": True,
    },
    "filters": {"commodities": [], "locations": []},
    "company_overrides": {},
    "schedule": {"enabled": False, "day_of_week": "daily", "hour": 7, "minute": 30},
    "advanced": {
        "politeness_min_s": 2.0,
        "politeness_max_s": 5.0,
        "page_timeout_s": 30,
        # Crawl rules are honoured by default. Some small elevator sites
        # ship a blanket "Disallow: /" that also blocks the search engines
        # they clearly want indexing them; an operator who has confirmed
        # they are allowed to fetch a given site can turn this off in
        # Advanced settings, and owns that decision.
        "respect_robots": True,
        "resolve_verify": True,
    },
}

# Filename tokens the patterns may use; anything else is a save-time error.
VALID_FILENAME_TOKENS = {"date", "time", "company", "commodity", "scan_id"}

# Per-company override keys that may NOT be overridden (file lifecycle
# stays globally coherent).
_OVERRIDE_MERGE_DEEP = {"decimals", "columns"}


class SettingsError(ValueError):
    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(f"{field}: {message}")


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _check_filename_tokens(doc: dict) -> None:
    import string

    patterns = doc.get("output", {}).get("filename_patterns", {})
    fmt = string.Formatter()
    for name, pattern in patterns.items():
        for _, field_name, _, _ in fmt.parse(pattern):
            if field_name and field_name not in VALID_FILENAME_TOKENS:
                raise SettingsError(
                    f"output.filename_patterns.{name}",
                    f"unknown token {{{field_name}}} - valid tokens: "
                    + ", ".join(sorted(VALID_FILENAME_TOKENS)),
                )
        if not pattern.lower().endswith(".xlsx"):
            raise SettingsError(
                f"output.filename_patterns.{name}", "must end with .xlsx"
            )


def validate(doc: dict) -> None:
    """Raise SettingsError on the first problem found."""
    try:
        jsonschema.validate(doc, SETTINGS_JSON_SCHEMA)
    except jsonschema.ValidationError as e:
        path = ".".join(str(p) for p in e.absolute_path) or "(document)"
        raise SettingsError(path, e.message) from e
    adv = doc.get("advanced", {})
    if adv.get("politeness_min_s", 0) > adv.get("politeness_max_s", 99):
        raise SettingsError(
            "advanced.politeness_min_s", "must not exceed politeness_max_s"
        )
    th = doc.get("output", {}).get("stale_thresholds", {})
    if th.get("aging_days", 0) >= th.get("stale_days", 99):
        raise SettingsError(
            "output.stale_thresholds.aging_days", "must be less than stale_days"
        )
    _check_filename_tokens(doc)


def load_settings(path: Path | str) -> dict:
    """Load settings.json, layering it over defaults so new keys added in
    later versions appear automatically. Unknown/invalid files fall back to
    defaults rather than crashing the app."""
    p = Path(path)
    if not p.exists():
        return copy.deepcopy(DEFAULT_SETTINGS)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        merged = _deep_merge(DEFAULT_SETTINGS, raw)
        validate(merged)
        return merged
    except (OSError, ValueError, SettingsError):
        return copy.deepcopy(DEFAULT_SETTINGS)


def save_settings(path: Path | str, current: dict, patch: dict) -> dict:
    """Merge patch into current, validate, persist atomically. Returns the
    new document. Raises SettingsError without touching the file when the
    patch is invalid."""
    merged = _deep_merge(current, patch)
    validate(merged)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    tmp.replace(p)
    return merged


def resolve(settings: dict, company_id: int | str | None) -> dict:
    """Effective OUTPUT settings for one company: global output + filters,
    with the company's override keys applied. Arrays replace wholesale;
    decimals/columns merge per subkey. File-lifecycle keys are global-only
    by schema construction (they don't exist in CompanyOverride)."""
    eff = copy.deepcopy(settings["output"])
    eff["commodities"] = list(settings["filters"].get("commodities", []))
    eff["locations"] = list(settings["filters"].get("locations", []))
    if company_id is None:
        return eff
    override = settings.get("company_overrides", {}).get(str(company_id))
    if not override:
        return eff
    for key, value in override.items():
        if key in _OVERRIDE_MERGE_DEEP:
            eff[key] = _deep_merge(eff.get(key, {}), value)
        else:
            eff[key] = copy.deepcopy(value)
    return eff
