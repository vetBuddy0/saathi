"""contracts/: every sample document is valid against its JSON Schema, and
the schemas are the ones the rules and the other units expect.

Why this exists: the device (Python), the caregiver app (TypeScript) and
the Cloud Functions all read the same files in contracts/. The rules suite
there (Node, against the Firestore emulator) writes each sample through
the rules; this test proves the same samples match the schemas, so a
schema, a sample and a rule cannot drift apart without a red test on
whichever side moved.

Why a tiny validator instead of `jsonschema`: `jsonschema` is not a
dependency of this project (it is only in uv.lock transitively, and not
installed in the venv), and adding one for eight flat documents isn't
worth it. Lost: the real library. To keep the small one honest,
`check_schema` walks every schema in full and refuses any keyword, type or
format it doesn't implement. A schema that starts using `oneOf` or `$ref`
fails here loudly instead of being silently half-checked.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"
NAMES = ["device", "turn", "event", "message", "reply", "reminder", "pairing", "user"]

# Keywords whose meaning the validator implements, and annotations it may ignore.
_CHECKED = {
    "type", "properties", "required", "additionalProperties", "enum", "pattern",
    "minLength", "maxLength", "items", "minItems", "maxItems", "uniqueItems", "format",
}
_ANNOTATIONS = {"$schema", "$id", "title", "description"}
_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
}


def _is_datetime(value: str) -> bool:
    # RFC 3339 date-time: a date, a "T", a time, and an explicit offset or Z.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})", value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def check_schema(schema: dict, path: str = "$") -> None:
    """Raise if any part of `schema` -- reached by data or not -- uses
    something `validate` doesn't implement."""
    unknown = set(schema) - _CHECKED - _ANNOTATIONS
    if unknown:
        raise ValueError(f"{path}: schema keyword(s) this validator doesn't implement: {sorted(unknown)}")
    if "type" in schema and schema["type"] not in _TYPES:
        raise ValueError(f"{path}: type {schema['type']!r} not implemented")
    if "format" in schema and schema["format"] != "date-time":
        raise ValueError(f"{path}: format {schema['format']!r} not implemented")
    for key, sub in schema.get("properties", {}).items():
        check_schema(sub, f"{path}.{key}")
    if "items" in schema:
        check_schema(schema["items"], f"{path}[]")


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Errors for `value` against the subset of draft 2020-12 named above.
    Call `check_schema` on the schema first."""
    errors: list[str] = []
    if "type" in schema:
        # Only object/array/string/boolean exist here; bool-vs-int can't arise.
        if not isinstance(value, _TYPES[schema["type"]]):
            return [f"{path}: expected {schema['type']}, got {type(value).__name__}"]
    # Same type as well as equal: in Python True == 1.
    if "enum" in schema and not any(type(v) is type(value) and v == value for v in schema["enum"]):
        errors.append(f"{path}: {value!r} not one of {schema['enum']}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} doesn't match {schema['pattern']}")
        if schema.get("format") == "date-time" and not _is_datetime(value):
            errors.append(f"{path}: {value!r} is not an RFC 3339 date-time")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            errors.append(f"{path}: items are not unique")
        for i, item in enumerate(value):
            errors += validate(item, schema.get("items", {}), f"{path}[{i}]")

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required {key!r}")
        for key, item in value.items():
            if key in props:
                errors += validate(item, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: {key!r} is not in the contract")
    return errors


def _load(kind: str, name: str) -> dict:
    suffix = ".schema.json" if kind == "schemas" else ".json"
    return json.loads((CONTRACTS / kind / f"{name}{suffix}").read_text())


def test_there_is_exactly_one_schema_and_one_sample_per_shape():
    assert sorted(p.name.removesuffix(".schema.json") for p in (CONTRACTS / "schemas").glob("*.json")) == sorted(NAMES)
    assert sorted(p.stem for p in (CONTRACTS / "samples").glob("*.json")) == sorted(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_sample_is_valid_against_its_schema(name):
    schema = _load("schemas", name)
    check_schema(schema)
    assert validate(_load("samples", name), schema) == []


@pytest.mark.parametrize("name", NAMES)
def test_schema_is_draft_2020_12_closed_object(name):
    schema = _load("schemas", name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) <= set(schema["properties"])


# The shared contract, field by field: (required, optional). Other units
# code against exactly these names, so a rename shows up here first.
CONTRACT_FIELDS = {
    "device": ({"name", "createdAt", "caregivers", "deviceUid"}, {"pairingCode"}),
    "turn": ({"ts", "heard", "said", "language"}, {"clipPath"}),
    "event": ({"ts", "kind", "text"}, {"reminderId"}),
    "message": ({"from", "fromUid", "text", "createdAt"}, {"spokenAt"}),
    "reply": ({"ts", "text"}, set()),
    "reminder": ({"text", "time", "repeat", "active", "createdBy", "updatedAt"}, {"date"}),
    "pairing": ({"deviceId", "expiresAt"}, {"claimedBy"}),
    "user": ({"name", "fcmTokens"}, set()),
}


@pytest.mark.parametrize("name", NAMES)
def test_schema_fields_are_the_shared_contract(name):
    schema = _load("schemas", name)
    required, optional = CONTRACT_FIELDS[name]
    assert set(schema["required"]) == required
    assert set(schema["properties"]) == required | optional


def test_enums_are_the_shared_contract():
    assert _load("schemas", "event")["properties"]["kind"]["enum"] == [
        "fired", "acked", "missed", "timer_done", "message_spoken",
    ]
    assert _load("schemas", "reminder")["properties"]["repeat"]["enum"] == [
        "none", "daily", "weekdays", "weekly",
    ]


# --- the validator itself: it must say no, not just yes ----------------------


def _reminder(**changes):
    doc = _load("samples", "reminder")
    doc.update(changes)
    return doc


@pytest.mark.parametrize(
    "doc, why",
    [
        (_reminder(time="25:00"), "doesn't match"),
        (_reminder(time="8:00"), "doesn't match"),
        (_reminder(date="2026-13-01"), "doesn't match"),
        (_reminder(repeat="monthly"), "not one of"),
        (_reminder(active="yes"), "expected boolean"),
        (_reminder(updatedAt="yesterday"), "date-time"),
        (_reminder(updatedAt="2026-10-29T10:05:00"), "date-time"),  # no offset
        (_reminder(text=""), "shorter than"),
        (_reminder(extra=1), "not in the contract"),
    ],
)
def test_validator_rejects_a_reminder_outside_the_contract(doc, why):
    errors = validate(doc, _load("schemas", "reminder"))
    assert any(why in e for e in errors), errors


def test_validator_rejects_missing_fields_and_bad_items():
    device = _load("samples", "device")
    del device["deviceUid"]
    device["caregivers"] = ["a", "a"]
    device["pairingCode"] = "12345"
    errors = validate(device, _load("schemas", "device"))
    assert any("missing required 'deviceUid'" in e for e in errors)
    assert any("not unique" in e for e in errors)
    assert any("pairingCode" in e for e in errors)


@pytest.mark.parametrize(
    "schema, why",
    [
        ({"type": "object", "oneOf": []}, "oneOf"),
        # Deep in an optional property no sample uses: still refused.
        ({"type": "object", "properties": {"x": {"type": "string", "$ref": "#/a"}}}, "ref"),
        ({"type": "array", "items": {"anyOf": []}}, "anyOf"),
        ({"type": "integer"}, "integer"),
        ({"type": "string", "format": "email"}, "email"),
    ],
)
def test_validator_refuses_what_it_does_not_implement(schema, why):
    with pytest.raises(ValueError, match=why):
        check_schema(schema)


def test_enum_does_not_confuse_true_with_1():
    assert validate(True, {"enum": [0, 1]}) != []
    assert validate(1, {"enum": [0, 1]}) == []
